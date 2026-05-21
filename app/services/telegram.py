"""Telegram notification helpers for live trading events."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any
from urllib import parse, request

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _fmt_money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}${value:,.2f}"


def _fmt_price(value: float) -> str:
    return f"${value:,.4f}" if value < 1000 else f"${value:,.2f}"


def _fmt_shares(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _fmt_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _fmt_time(value: datetime | str | None) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, str):
        return value
    dt = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def build_positions_summary(positions: list[dict[str, Any]] | None) -> str:
    positions = positions or []
    open_positions = [p for p in positions if float(p.get("shares", 0) or 0) > 0]
    if not open_positions:
        return "Open positions: none"

    total_market_value = sum(float(p.get("market_value", 0.0) or 0.0) for p in open_positions)
    total_unrealized = sum(float(p.get("unrealized_pnl", 0.0) or 0.0) for p in open_positions)

    lines = [
        f"Open positions ({len(open_positions)}) • Value {_fmt_money(total_market_value)} • U-PnL {_fmt_money(total_unrealized)}"
    ]

    for position in sorted(open_positions, key=lambda item: str(item.get("symbol", ""))):
        symbol = str(position.get("symbol", "?")).upper()
        shares = float(position.get("shares", 0.0) or 0.0)
        avg_price = float(position.get("avg_price", 0.0) or 0.0)
        last_price = float(position.get("last_price", 0.0) or 0.0)
        unrealized_pnl = float(position.get("unrealized_pnl", 0.0) or 0.0)
        lines.append(
            f"• {symbol}: {_fmt_shares(shares)} sh @ {_fmt_price(avg_price)} | "
            f"last {_fmt_price(last_price)} | U-PnL {_fmt_money(unrealized_pnl)}"
        )

    return "\n".join(lines)


def build_trade_notification_text(
    *,
    session_name: str,
    symbol: str,
    strategy_name: str | None,
    side: str,
    order_type: str,
    shares: float,
    price: float,
    notional: float,
    cash_remaining: float | None,
    executed_at: datetime | str | None,
    positions: list[dict[str, Any]] | None,
    pnl: float | None = None,
    pnl_pct: float | None = None,
    delayed: bool = False,
    ibkr_order_id: int | None = None,
) -> str:
    side_normalized = side.lower()
    header = "🟢 BUY executed" if side_normalized == "buy" else "🔴 SELL executed"

    lines = [
        header,
        f"Session: {session_name}",
        f"Symbol: {symbol.upper()}",
    ]
    if strategy_name:
        lines.append(f"Strategy: {strategy_name}")
    lines.extend([
        f"Order: {order_type}",
        f"Fill: {_fmt_shares(shares)} sh @ {_fmt_price(price)}",
        f"Notional: {_fmt_money(notional)}",
    ])

    if pnl is not None:
        pnl_line = f"Realized P&L: {_fmt_money(pnl)}"
        if pnl_pct is not None:
            pnl_line += f" ({_fmt_pct(pnl_pct)})"
        lines.append(pnl_line)

    if cash_remaining is not None:
        lines.append(f"Cash remaining: {_fmt_money(cash_remaining)}")

    if delayed:
        lines.append("Execution mode: delayed-data paper fill")
    elif ibkr_order_id is not None:
        lines.append(f"IBKR order id: {ibkr_order_id}")

    lines.append(f"Time: {_fmt_time(executed_at)}")
    lines.append("")
    lines.append(build_positions_summary(positions))
    return "\n".join(lines)


class TelegramNotifier:
    """Send best-effort Telegram messages without interrupting trading."""

    def __init__(
        self,
        *,
        bot_token: str | None = None,
        chat_id: str | None = None,
        enabled: bool | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled
        self._timeout_seconds = timeout_seconds

    @property
    def bot_token(self) -> str:
        return (self._bot_token if self._bot_token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()

    @property
    def chat_id(self) -> str:
        return (self._chat_id if self._chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")).strip()

    @property
    def enabled(self) -> bool:
        explicit = self._enabled if self._enabled is not None else _env_flag("TELEGRAM_NOTIFICATIONS_ENABLED", True)
        return bool(explicit and self.bot_token and self.chat_id)

    async def send_trade_notification(self, **kwargs: Any) -> bool:
        if not self.enabled:
            return False
        text = build_trade_notification_text(**kwargs)
        return await self.send_message(text)

    async def send_message(self, text: str) -> bool:
        if not self.enabled:
            return False

        try:
            await asyncio.to_thread(self._send_message_sync, text)
            return True
        except Exception:
            logger.warning("Failed to send Telegram notification", exc_info=True)
            return False

    def _send_message_sync(self, text: str) -> None:
        payload = parse.urlencode({
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with request.urlopen(req, timeout=self._timeout_seconds) as response:  # noqa: S310
            body = response.read().decode("utf-8")
        data = json.loads(body)
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
