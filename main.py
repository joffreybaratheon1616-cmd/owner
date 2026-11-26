#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║         Private — Session Management Platform         ║
╚═══════════════════════════════════════════════════════════════╝
"""

import asyncio
import os
import sqlite3

# Optimize file descriptor limit for 500+ concurrent user sessions
try:
    import resource
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(65536, _hard), _hard))
except Exception:
    pass

import config
import session_core
import operations
import handlers

# Re-export everything from major modules for 100% backward compatibility
from config import *
from session_core import *
from operations import *
from handlers import *

for _m in (config, session_core, operations, handlers):
    for _k, _v in vars(_m).items():
        if not _k.startswith('__'):
            globals()[_k] = _v

async def handle_health_check(_reader, writer):
    try:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def start_web_server():
    """Start Railway-compatible HTTP health endpoint without killing other processes."""
    raw_port = os.environ.get("PORT", os.environ.get("SERVER_PORT", "8080"))
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid PORT value: {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"PORT must be between 1 and 65535, got {port}")
    server = await asyncio.start_server(
        handle_health_check, "0.0.0.0", port, reuse_address=True
    )
    print(f"🌐 Web health server started on 0.0.0.0:{port}")
    return server

async def _start_telegram_client(max_attempts=10):
    """Start Pyrogram with bounded retries and clear failure reporting."""
    delay = 3
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"🚀 Calling app.start()... (attempt {attempt}/{max_attempts})")
            await asyncio.wait_for(app.start(), timeout=90)
            print("✅ Telegram client started successfully")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            print(
                f"⏳ Telegram startup failed ({attempt}/{max_attempts}): "
                f"{type(exc).__name__}: {exc}"
            )
            # A timeout can leave a partially initialized client. Best-effort
            # cleanup prevents the next attempt from inheriting stale state.
            try:
                if getattr(app, "is_connected", False) or getattr(app, "is_initialized", False):
                    await asyncio.wait_for(app.stop(), timeout=20)
            except Exception as cleanup_error:
                print(f"⚠️ Startup cleanup warning: {cleanup_error}")
            if attempt < max_attempts:
                await asyncio.sleep(min(delay, 30))
                delay = min(delay * 2, 30)
    raise RuntimeError(
        f"Could not connect to Telegram after {max_attempts} attempts. "
        "If this host blocks Telegram, configure TELEGRAM_PROXY_TYPE, "
        "TELEGRAM_PROXY_HOST and TELEGRAM_PROXY_PORT in Railway Variables. "
        f"Last error: {type(last_error).__name__}: {last_error}"
    ) from last_error

async def main_runner():
    app.loop = asyncio.get_running_loop()
    health_server = None
    watchdog_task = None
    try:
        health_server = await start_web_server()
        await _start_telegram_client()

        if ADMIN_IDS:
            for admin_id in ADMIN_IDS:
                try:
                    await app.send_message(
                        admin_id,
                        f"🟢 **Bot Online**\n\n{PLATFORM_NAME} has been successfully started/restarted and is now active.",
                    )
                except Exception as exc:
                    print(f"⚠️ Failed to send startup message to {admin_id}: {exc}")

        async def connection_watchdog():
            """Check Telegram health and restart the complete Pyrogram client if needed."""
            while True:
                await asyncio.sleep(30)
                try:
                    if not getattr(app, "is_connected", False):
                        print("⚠️ [Watchdog] Telegram client disconnected; restarting client...")
                        try:
                            await asyncio.wait_for(app.restart(), timeout=90)
                            print("✅ [Watchdog] Telegram client restarted")
                        except Exception as exc:
                            print(f"❌ [Watchdog] Client restart failed: {type(exc).__name__}: {exc}")
                    else:
                        await asyncio.wait_for(app.get_me(), timeout=20)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"⚠️ [Watchdog] Health check failed: {type(exc).__name__}: {exc}")

        watchdog_task = asyncio.create_task(connection_watchdog(), name="telegram-watchdog")
        import pyrogram
        print("💤 Entering pyrogram.idle()...")
        await pyrogram.idle()
    finally:
        if watchdog_task:
            watchdog_task.cancel()
            await asyncio.gather(watchdog_task, return_exceptions=True)
        try:
            if getattr(app, "is_connected", False):
                await app.stop()
        except Exception as exc:
            print(f"⚠️ Error while stopping Telegram client: {exc}")
        if health_server:
            health_server.close()
            await health_server.wait_closed()


if __name__ == "__main__":
    ensure_storage()
    try:
        _meta_startup = load_meta()
        GLOBAL_ANNOUNCEMENT = _meta_startup.get("announcement", "")
        MAINTENANCE_MODE = _meta_startup.get("maintenance", False)
        WHITELIST_MODE = _meta_startup.get("whitelist", False)
        for _adm in _meta_startup.get("admins", []):
            if _adm not in ADMIN_IDS:
                ADMIN_IDS.append(_adm)
    except Exception as exc:
        print(f"⚠️ Could not load startup metadata: {type(exc).__name__}: {exc}")

    print(f"💎 {PLATFORM_NAME} is starting...")
    try:
        asyncio.run(main_runner())
    except KeyboardInterrupt:
        print("🛑 Shutdown requested")
    except Exception as exc:
        import traceback
        print(f"❌ Fatal startup/runtime error: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise SystemExit(1)
