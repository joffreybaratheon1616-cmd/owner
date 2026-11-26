# Railway deployment notes

## Required variables

Keep the existing application variables already used by this project (`API_ID`, `API_HASH`, `BOT_TOKEN`, and any database/storage variables you use). Do not put credentials in source code.

## Optional Telegram proxy

The bot connects directly by default. If the hosting provider cannot complete Telegram MTProto authentication, configure a proxy that supports Telegram traffic using Railway Variables:

- `TELEGRAM_PROXY_TYPE`: `socks5`, `socks4`, or `http`
- `TELEGRAM_PROXY_HOST`: proxy hostname or IP
- `TELEGRAM_PROXY_PORT`: proxy port
- `TELEGRAM_PROXY_USER`: optional username
- `TELEGRAM_PROXY_PASS`: optional password

The equivalent `BOT_PROXY_TYPE`, `BOT_PROXY_HOST`, `BOT_PROXY_PORT`, `BOT_PROXY_USER`, and `BOT_PROXY_PASS` names are accepted as fallbacks. `PROXY_*` remains reserved for the IMAP proxy pool and is deliberately not reused for Telegram.

Do not enter placeholder/example proxy details. Use a working proxy from a provider you trust. If the proxy is not configured, the bot retains its previous direct-connection default.

## Deployment

- Build command: `pip install -r requirements.txt` (Railway Railpack detects this automatically).
- Start command: `python main.py`
- Railway injects `PORT`; the health server binds to it.
- Use a Railway Volume mounted at the project path if `accstore_data` must survive redeployments.
- Ensure only one replica uses the same SQLite session/database files.

The process exits with status 1 after exhausting Telegram startup attempts, allowing Railway restart policies to detect a failed start. The Telegram proxy must be reachable from the deployed service; code changes alone cannot supply proxy connectivity.
