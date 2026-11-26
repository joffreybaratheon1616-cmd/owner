# language: Python, file: features_upgrade.py, target: Pyrogram bot upgrade modules
# Independent modules: Merger, Force-Join, Channel/Group Audit. No existing handlers removed.

import os
import re
import json
import time
import shutil
import zipfile
import hashlib
import logging
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from pyrogram import filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelPrivate

# Bind into existing app + helpers after import in handlers
app = None
is_admin = None
load_meta = None
save_meta = None
safe_edit = None
ROOT_DIR = "accstore_data"
PACKS_DIR = os.path.join(ROOT_DIR, "packs")

# ---------------------------------------------------------------------------
# Per-user temporary storage (isolated)
# ---------------------------------------------------------------------------
_MERGER_STATE: Dict[int, dict] = {}
_FORCE_JOIN_PROMPT: Dict[int, str] = {}  # admin_id -> "channel" | "group"
_AUDIT_STATE: Dict[int, dict] = {}

SUPPORTED_MERGER_EXT = {".zip", ".session", ".txt", ".json", ".pdf"}


def _user_merger_dir(user_id: int) -> str:
    path = os.path.join(ROOT_DIR, "merger_tmp", str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _clear_merger(user_id: int):
    state = _MERGER_STATE.pop(user_id, None)
    path = _user_merger_dir(user_id)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    return state


def _inventory_counts(user_id: int) -> Tuple[int, int, int]:
    """Return (collected, available_session_like, other)."""
    state = _MERGER_STATE.get(user_id) or {}
    files = state.get("files") or []
    collected = len(files)
    available = sum(
        1
        for f in files
        if f.get("ext") in (".session", ".json", ".txt") or f.get("kind") == "session_zip"
    )
    other = collected - available
    return collected, available, other


def _classify_file(path: str, original_name: str) -> dict:
    ext = os.path.splitext(original_name)[1].lower()
    size = os.path.getsize(path) if os.path.exists(path) else 0
    kind = "other"
    if ext == ".session":
        kind = "session"
    elif ext == ".json":
        kind = "json"
    elif ext == ".txt":
        kind = "txt"
    elif ext == ".pdf":
        kind = "pdf"
    elif ext == ".zip":
        # inventory only — do not extract credentials
        kind = "session_zip"
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()
                if any(n.lower().endswith(".session") for n in names):
                    kind = "session_zip"
        except Exception:
            kind = "zip_other"
    return {
        "path": path,
        "name": original_name,
        "ext": ext,
        "size": size,
        "kind": kind,
        "sha16": hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
        if os.path.exists(path)
        else "",
    }


def merger_interface_text(user_id: int) -> str:
    collected, available, other = _inventory_counts(user_id)
    state = _MERGER_STATE.get(user_id) or {}
    files = state.get("files") or []
    lines = [
        "🧬 **MERGE MODE — SESSION BACKUP CENTER**",
        "═══════════════════════════════",
        f"📦 Collected: `{collected}`",
        f"🟢 Available: `{available}`",
        f"📦 Other: `{other}`",
        "───────────────────────────────",
        "",
        "**How it works**",
        "Upload your own session backup files.",
        "Add multiple supported files.",
        "The bot maintains a temporary inventory.",
        "Display detected formats and duplicate filenames.",
        "Allow the user to review the inventory.",
        "Generate a downloadable backup archive.",
        "",
        "**Supported:** `.zip` `.session` `.txt` `.json` `.pdf`",
        "",
        "⚠️ Credentials stay owner-controlled. Inventory only — no key extraction or redistribution.",
    ]
    if files:
        lines.append("")
        lines.append("**Inventory**")
        seen_names = {}
        for i, f in enumerate(files, 1):
            dup = ""
            if f["name"] in seen_names:
                dup = " ⚠️ duplicate name"
            seen_names[f["name"]] = True
            lines.append(
                f"`{i}.` `{f['name']}` · {f['kind']} · {f['size']} B · `{f['sha16']}`{dup}"
            )
    return "\n".join(lines)


def merger_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Generate Backup ZIP 📦", callback_data="merger|generate")],
            [
                InlineKeyboardButton("Abort ❌", callback_data="merger|abort"),
                InlineKeyboardButton("Back", callback_data="merger|back"),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Force-Join settings (admin)
# ---------------------------------------------------------------------------
def _get_force_join() -> dict:
    meta = load_meta()
    fj = meta.get("force_join") or {}
    return {
        "enabled": bool(fj.get("enabled", False)),
        "channel": fj.get("channel"),  # @username or -100id
        "channel_link": fj.get("channel_link"),
        "group": fj.get("group"),
        "group_link": fj.get("group_link"),
    }


def _save_force_join(data: dict):
    meta = load_meta()
    meta["force_join"] = data
    save_meta(meta)


def force_join_text() -> str:
    fj = _get_force_join()
    status = "ON 🟢" if fj["enabled"] else "OFF 🔴"
    ch = "Configured ✅" if fj.get("channel") else "Not configured"
    gr = "Configured ✅" if fj.get("group") else "Not configured"
    ch_val = fj.get("channel") or "—"
    gr_val = fj.get("group") or "—"
    return (
        "🚀 **FORCE JOIN SETTINGS**\n"
        "═══════════════════════════════\n"
        f"Current status: **{status}**\n"
        f"Channel: {ch} (`{ch_val}`)\n"
        f"Group: {gr} (`{gr_val}`)\n"
    )


def force_join_keyboard() -> InlineKeyboardMarkup:
    fj = _get_force_join()
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Enable Force Join 🟢", callback_data="fjoin|enable"),
                InlineKeyboardButton("Disable Force Join 🔴", callback_data="fjoin|disable"),
            ],
            [
                InlineKeyboardButton("Add Channel ➕", callback_data="fjoin|add_ch"),
                InlineKeyboardButton("Remove Channel ➖", callback_data="fjoin|rm_ch"),
            ],
            [
                InlineKeyboardButton("Add Group ➕", callback_data="fjoin|add_gr"),
                InlineKeyboardButton("Remove Group ➖", callback_data="fjoin|rm_gr"),
            ],
            [InlineKeyboardButton("Back", callback_data="admin_back")],
        ]
    )


def join_required_keyboard(fj: dict) -> InlineKeyboardMarkup:
    rows = []
    if fj.get("channel") and fj.get("channel_link"):
        rows.append([InlineKeyboardButton("Join Channel 📢", url=fj["channel_link"])])
    elif fj.get("channel"):
        # fallback deep link style
        ch = str(fj["channel"])
        if ch.startswith("@"):
            rows.append([InlineKeyboardButton("Join Channel 📢", url=f"https://t.me/{ch[1:]}")])
    if fj.get("group") and fj.get("group_link"):
        rows.append([InlineKeyboardButton("Join Group 👥", url=fj["group_link"])])
    elif fj.get("group"):
        gr = str(fj["group"])
        if gr.startswith("@"):
            rows.append([InlineKeyboardButton("Join Group 👥", url=f"https://t.me/{gr[1:]}")])
    rows.append([InlineKeyboardButton("I Have Joined ✅", callback_data="fjoin|check")])
    return InlineKeyboardMarkup(rows)


async def check_membership(user_id: int) -> Tuple[bool, List[str]]:
    """Returns (ok, missing_labels)."""
    fj = _get_force_join()
    if not fj["enabled"]:
        return True, []
    missing = []
    if fj.get("channel"):
        try:
            m = await app.get_chat_member(fj["channel"], user_id)
            if m.status in ("left", "kicked"):
                missing.append("Channel")
        except UserNotParticipant:
            missing.append("Channel")
        except Exception as e:
            logging.warning(f"[force_join] channel check: {e}")
            missing.append("Channel")
    if fj.get("group"):
        try:
            m = await app.get_chat_member(fj["group"], user_id)
            if m.status in ("left", "kicked"):
                missing.append("Group")
        except UserNotParticipant:
            missing.append("Group")
        except Exception as e:
            logging.warning(f"[force_join] group check: {e}")
            missing.append("Group")
    return (len(missing) == 0), missing


# ---------------------------------------------------------------------------
# Channels & Groups audit
# ---------------------------------------------------------------------------
def audit_menu_text() -> str:
    return (
        "📡 **CHANNEL & GROUP AUDIT**\n"
        "═══════════════════════════════\n"
        "Select an operation:\n"
    )


def audit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Audit Channels 📢", callback_data="audit|channels")],
            [InlineKeyboardButton("Audit Groups 👥", callback_data="audit|groups")],
            [InlineKeyboardButton("Audit All 📡", callback_data="audit|all")],
            [InlineKeyboardButton("Back", callback_data="start_menu")],
        ]
    )


async def _audit_dialogs(user_client, mode: str) -> List[str]:
    """mode: channels | groups | all. Uses a connected client of the operator's session if available.
    Falls back to bot's own dialogs (bot can only see what it is member of).
    Creation date is never invented.
    """
    results = []
    try:
        async for dialog in user_client.get_dialogs():
            chat = dialog.chat
            chat_type = str(getattr(chat, "type", "")).lower()
            is_channel = "channel" in chat_type
            is_group = "group" in chat_type or "supergroup" in chat_type
            if mode == "channels" and not is_channel:
                continue
            if mode == "groups" and not is_group:
                continue
            if mode == "all" and not (is_channel or is_group):
                continue

            name = getattr(chat, "title", None) or getattr(chat, "first_name", "") or "—"
            username = f"@{chat.username}" if getattr(chat, "username", None) else "—"
            chat_id = chat.id
            public = "Public" if getattr(chat, "username", None) else "Private"
            members = getattr(chat, "members_count", None)
            members_s = f"{members:,}" if isinstance(members, int) else "N/A"

            # Ownership / admin from permissions when present
            ownership = "Unknown"
            try:
                # bot/client may have is_creator on dialog or permissions
                if getattr(dialog, "is_creator", False):
                    ownership = "Owner"
                else:
                    ownership = "Administrator / Member"
            except Exception:
                pass

            creation = "Not available (Telegram API does not expose exact creation timestamps)"

            kind = "Channel" if is_channel else "Group"
            block = (
                f"📡 **{kind.upper()} AUDIT**\n"
                f"Name: {name}\n"
                f"Username: {username}\n"
                f"Chat ID: `{chat_id}`\n"
                f"Type: {public} {kind}\n"
                f"Members: {members_s}\n"
                f"Ownership: {ownership}\n"
                f"Creation date: {creation}\n"
            )
            results.append(block)
    except Exception as e:
        results.append(f"⚠️ Audit error: `{type(e).__name__}: {e}`")
    return results


# ---------------------------------------------------------------------------
# Register handlers (called once from handlers after app is ready)
# ---------------------------------------------------------------------------
def register_upgrade_handlers(_app, _is_admin, _load_meta, _save_meta, _safe_edit, root_dir=None):
    global app, is_admin, load_meta, save_meta, safe_edit, ROOT_DIR, PACKS_DIR
    app = _app
    is_admin = _is_admin
    load_meta = _load_meta
    save_meta = _save_meta
    safe_edit = _safe_edit
    if root_dir:
        ROOT_DIR = root_dir
        PACKS_DIR = os.path.join(ROOT_DIR, "packs")

    # ---- Merger entry ----
    @app.on_callback_query(filters.regex(r"^merger\|"))
    async def merger_callbacks(_, call: CallbackQuery):
        await call.answer()
        uid = call.from_user.id
        action = call.data.split("|", 1)[1]

        if action == "open":
            if uid not in _MERGER_STATE:
                _MERGER_STATE[uid] = {"files": [], "started": time.time()}
            text = merger_interface_text(uid)
            return await safe_edit(call.message, text, reply_markup=merger_keyboard())

        if action == "abort":
            _clear_merger(uid)
            await safe_edit(
                call.message,
                "❌ Operation cancelled successfully.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Back", callback_data="start_menu")]]
                ),
            )
            return

        if action == "back":
            # leave inventory intact until abort; just return to start
            from session_core import start_menu_text, start_keyboard

            return await safe_edit(
                call.message,
                start_menu_text(),
                reply_markup=start_keyboard(uid),
            )

        if action == "generate":
            state = _MERGER_STATE.get(uid)
            if not state or not state.get("files"):
                return await call.answer("Inventory empty — upload files first.", show_alert=True)
            out_dir = _user_merger_dir(uid)
            out_zip = os.path.join(out_dir, f"backup_{uid}_{int(time.time())}.zip")
            try:
                with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in state["files"]:
                        if os.path.exists(f["path"]):
                            zf.write(f["path"], arcname=f["name"])
                await call.message.reply_document(
                    out_zip,
                    caption=f"📦 Backup archive · `{len(state['files'])}` file(s)\nOwner-controlled only.",
                )
                # cleanup after successful send
                _clear_merger(uid)
                await safe_edit(
                    call.message,
                    "✅ Backup generated and sent. Temporary inventory cleared.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("Back", callback_data="start_menu")]]
                    ),
                )
            except Exception as e:
                await call.message.reply_text(f"❌ Backup failed: `{e}`")
            return

    # ---- Force Join admin ----
    @app.on_callback_query(filters.regex(r"^fjoin\|"))
    async def fjoin_callbacks(_, call: CallbackQuery):
        uid = call.from_user.id
        action = call.data.split("|", 1)[1]

        if action == "open":
            if not is_admin(uid):
                return await call.answer("Access Denied", show_alert=True)
            return await safe_edit(
                call.message, force_join_text(), reply_markup=force_join_keyboard()
            )

        if not is_admin(uid) and action not in ("check",):
            return await call.answer("Access Denied", show_alert=True)

        await call.answer()
        fj = _get_force_join()

        if action == "enable":
            fj["enabled"] = True
            _save_force_join(fj)
            return await safe_edit(
                call.message, force_join_text() + "\n✅ Force join enabled.", reply_markup=force_join_keyboard()
            )
        if action == "disable":
            fj["enabled"] = False
            _save_force_join(fj)
            return await safe_edit(
                call.message, force_join_text() + "\n✅ Force join disabled.", reply_markup=force_join_keyboard()
            )
        if action == "rm_ch":
            fj["channel"] = None
            fj["channel_link"] = None
            _save_force_join(fj)
            return await safe_edit(
                call.message, force_join_text() + "\n✅ Channel removed.", reply_markup=force_join_keyboard()
            )
        if action == "rm_gr":
            fj["group"] = None
            fj["group_link"] = None
            _save_force_join(fj)
            return await safe_edit(
                call.message, force_join_text() + "\n✅ Group removed.", reply_markup=force_join_keyboard()
            )
        if action == "add_ch":
            _FORCE_JOIN_PROMPT[uid] = "channel"
            return await safe_edit(
                call.message,
                "➕ **Add Channel**\n\nSend the channel link (`https://t.me/...`) or `@username`.\nBot must be admin in the channel with invite permission preferred.\n\nSend /cancel to abort.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="fjoin|open")]]
                ),
            )
        if action == "add_gr":
            _FORCE_JOIN_PROMPT[uid] = "group"
            return await safe_edit(
                call.message,
                "➕ **Add Group**\n\nSend the group link or `@username`.\nBot must be a member (admin preferred).\n\nSend /cancel to abort.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="fjoin|open")]]
                ),
            )
        if action == "check":
            ok, missing = await check_membership(uid)
            if ok:
                from session_core import start_menu_text, start_keyboard

                return await safe_edit(
                    call.message,
                    start_menu_text(),
                    reply_markup=start_keyboard(uid),
                )
            miss = ", ".join(missing) if missing else "required communities"
            return await call.answer(f"Still missing: {miss}", show_alert=True)

    @app.on_message(filters.text & filters.private & filters.regex(r"^(?!/)"))
    async def fjoin_prompt_text(_, message: Message):
        uid = message.from_user.id if message.from_user else None
        if not uid or uid not in _FORCE_JOIN_PROMPT:
            return
        if not is_admin(uid):
            _FORCE_JOIN_PROMPT.pop(uid, None)
            return
        kind = _FORCE_JOIN_PROMPT.pop(uid)
        raw = (message.text or "").strip()
        # normalize
        link = raw
        username = None
        m = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)", raw)
        if m:
            username = m.group(1)
            link = f"https://t.me/{username}"
        elif raw.startswith("@"):
            username = raw[1:]
            link = f"https://t.me/{username}"
        elif raw.lstrip("-").isdigit():
            username = int(raw)
            link = None
        else:
            return await message.reply_text("❌ Could not parse link or @username. Try again or /cancel.")

        peer = f"@{username}" if isinstance(username, str) else username
        # validate bot can see the chat
        try:
            chat = await app.get_chat(peer)
            peer_id = chat.id
            peer_ref = f"@{chat.username}" if chat.username else peer_id
            if not link and chat.username:
                link = f"https://t.me/{chat.username}"
        except Exception as e:
            return await message.reply_text(
                f"❌ Bot cannot access that chat: `{e}`\nAdd the bot first, then retry."
            )

        fj = _get_force_join()
        if kind == "channel":
            fj["channel"] = peer_ref
            fj["channel_link"] = link
        else:
            fj["group"] = peer_ref
            fj["group_link"] = link
        _save_force_join(fj)
        await message.reply_text(
            force_join_text() + f"\n✅ {kind.title()} saved: `{peer_ref}`",
            reply_markup=force_join_keyboard(),
        )

    # ---- Audit ----
    @app.on_callback_query(filters.regex(r"^audit\|"))
    async def audit_callbacks(_, call: CallbackQuery):
        await call.answer()
        uid = call.from_user.id
        action = call.data.split("|", 1)[1]

        if action == "open":
            return await safe_edit(
                call.message, audit_menu_text(), reply_markup=audit_keyboard()
            )

        mode = action  # channels | groups | all
        status = await call.message.reply_text("📡 Scanning accessible chats (bot membership)...")
        # Use bot client — only chats the bot itself is in
        blocks = await _audit_dialogs(app, mode)
        if not blocks:
            await status.edit_text(
                "📡 No matching channels/groups found for this bot account.\n"
                "Note: audit reflects chats the **bot** can see. For account-owned audits, open a session pack and use community tools.",
                reply_markup=audit_keyboard(),
            )
            return
        # send in chunks
        chunk = ""
        for b in blocks:
            if len(chunk) + len(b) > 3500:
                await status.reply_text(chunk)
                chunk = b + "\n\n"
            else:
                chunk += b + "\n\n"
        if chunk:
            await status.edit_text(chunk[:4000], reply_markup=audit_keyboard())


def validate_login_code(otp_text: str) -> str:
    """Accept exactly 5 or 6 digit codes; preserve leading zeros."""
    digits = re.sub(r"\D", "", otp_text or "")
    if len(digits) not in (5, 6):
        raise ValueError(
            "Login code must be exactly 5 or 6 digits. "
            "Telegram uses 5-digit or 6-digit codes depending on region/app."
        )
    return digits
