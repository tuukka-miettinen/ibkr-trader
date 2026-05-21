# Trader

Real-time candlestick chart platform with live IBKR market data, event overlays, and a Docker-based IB Gateway.

## Stack

| Service | Role | Port |
|---|---|---|
| `frontend` | React + Vite + TradingView charts | `80` |
| `backend` | FastAPI WebSocket + REST | `8000` |
| `ib-gateway` | IB Gateway (paper/live) | `4003` live / `4004` paper |
| `novnc` | Web-based VNC viewer for gateway UI | `6080` |

## Quick start

```powershell
# 1. Copy the env template and fill in your IBKR credentials
cp .env.example .env

# 2. Edit .env — minimum required values:
#    TWS_USERID, TWS_PASSWORD, VNC_SERVER_PASSWORD

# 3. Start the IBKR stack once
docker compose -f docker-compose.ibkr.yml up -d

# 4. Start or rebuild the app stack
docker compose up -d --build

# 5. Open the trading app
start http://localhost

# 6. Open the IB Gateway UI (to confirm login, enable API, etc.)
start http://localhost:6080/vnc.html
```

Rebuilding the app stack with `docker compose up -d --build` no longer restarts `ib-gateway`, so your IBKR login session stays intact unless you explicitly restart the IBKR stack.

## noVNC — IB Gateway UI access

The `novnc` service at **http://localhost:6080/vnc.html** lets you inspect and control the IB Gateway graphical interface directly from your browser.

### Setup

Set `VNC_SERVER_PASSWORD` in `.env` to any non-empty value. Without it the gateway container keeps VNC disabled and noVNC will show a connection error.

```
VNC_SERVER_PASSWORD=yourpassword
```

### Connecting

1. Navigate to `http://localhost:6080/vnc.html`
2. Click **Connect**
3. Enter your `VNC_SERVER_PASSWORD` when prompted
4. The IB Gateway desktop appears in the browser

### Common tasks via VNC

| Task | Where in gateway UI |
|---|---|
| Enable API access | File → Global Configuration → API → Settings → "Enable ActiveX and Socket Clients" |
| Check login status | Top bar shows account name and connection indicator |
| Dismiss 2FA / session dialogs | Any modal visible in the VNC view |
| Set paper trading mode | File → Global Configuration → API → Settings → "Master API client ID" |

### Healthcheck

Docker checks `http://localhost:6080/vnc.html` every 15 seconds. Run `docker compose ps` to see the health status.

## Environment variables

See [.env.example](.env.example) for the full list with descriptions.

Key variables:

| Variable | Default | Description |
|---|---|---|
| `MARKET_DATA_PROVIDER` | `ibkr` | `ibkr` or `mock` |
| `TWS_USERID` | — | IBKR username |
| `TWS_PASSWORD` | — | IBKR password |
| `VNC_SERVER_PASSWORD` | — | VNC password (blank = VNC disabled) |
| `IBKR_TRADING_MODE` | `paper` | `paper` or `live` |
| `READ_ONLY_API` | `yes` | `yes` = no order execution |
| `TELEGRAM_NOTIFICATIONS_ENABLED` | `true` | Enable Telegram trade notifications |
| `TELEGRAM_BOT_TOKEN` | — | Bot token used for notifications |
| `TELEGRAM_CHAT_ID` | — | Telegram group/chat ID to receive notifications |

## Telegram trade notifications

Live paper-trading sessions can send a Telegram message immediately after each executed **buy** and **sell**.
Each message also includes a short snapshot of the session's currently open positions.

Configure these in `.env`:

```env
TELEGRAM_NOTIFICATIONS_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:your_bot_token
TELEGRAM_CHAT_ID=-1001234567890
```

Notes:
- Add the bot to your target Telegram group.
- Use the group's chat ID for `TELEGRAM_CHAT_ID`.
- If `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` is blank, notifications stay disabled.
