"""Shared IBKR connection — single IB() instance + single executor thread.

Both IBKRMarketDataProvider and IBKRTradingClient use this module so that
only ONE connection to TWS / IB Gateway exists at any time.  This prevents
the "Trading TWS session is connected from a different IP address" error
that occurs when two IB() instances connect from different threads/IPs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from ib_insync import IB

logger = logging.getLogger(__name__)

T = TypeVar("T")

_lock = threading.Lock()
_ib: IB | None = None
_executor: ThreadPoolExecutor | None = None
_executor_thread_id: int | None = None
_connected = False
_market_data_mode = "realtime"


def _get_config() -> tuple[str, int, int]:
    host = os.environ.get("IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("IBKR_PORT", "7497"))
    client_id = int(os.environ.get("IBKR_CLIENT_ID", "101"))
    return host, port, client_id


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _ensure_thread_loop() -> None:
    """Guarantee the calling thread owns a fresh, non-running asyncio event loop."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop is None or loop.is_running():
        asyncio.set_event_loop(asyncio.new_event_loop())


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ibkr-shared")
    return _executor


def get_ib() -> IB:
    """Return the shared IB instance (created lazily, NOT connected)."""
    global _ib
    if _ib is None:
        _ib = IB()
    return _ib


def is_connected() -> bool:
    return _connected and _ib is not None and _ib.isConnected()


def run_on_ib_thread(fn: Callable[[], T]) -> T:
    """Run *fn* on the shared IB executor thread, blocking until done."""
    global _executor_thread_id
    if threading.get_ident() == _executor_thread_id:
        return fn()
    executor = _get_executor()
    future = executor.submit(_run_with_loop, fn)
    return future.result()


def _run_with_loop(fn: Callable[[], T]) -> T:
    global _executor_thread_id
    _executor_thread_id = threading.get_ident()
    _ensure_thread_loop()
    return fn()


def ensure_connected() -> None:
    """Connect the shared IB instance if not already connected.

    Must be called from the IB executor thread (via run_on_ib_thread).
    """
    global _connected
    ib = get_ib()
    if ib.isConnected():
        _connected = True
        return

    host, port, client_id = _get_config()
    if not _port_open(host, port):
        from app.providers.base import MarketDataError
        raise MarketDataError(
            f"Cannot reach IBKR at {host}:{port}. "
            "Start TWS / IB Gateway and enable API access."
        )

    ib.connect(host, port, clientId=client_id, readonly=False, timeout=10)
    _connected = True
    logger.info(
        "IBKR shared connection established (client_id=%s, host=%s, port=%s)",
        client_id, host, port,
    )


def set_market_data_mode(mode: str) -> None:
    """Set IBKR market data mode for the shared connection.

    Must be called from the IB executor thread (via run_on_ib_thread).
    """
    global _market_data_mode
    ensure_connected()
    normalized = mode.strip().lower()
    if normalized not in {"realtime", "delayed"}:
        raise ValueError(f"Unsupported market data mode: {mode}")
    market_data_type = 1 if normalized == "realtime" else 3
    get_ib().reqMarketDataType(market_data_type)
    _market_data_mode = normalized
    logger.info("IBKR market data mode set to %s (type=%s)", normalized, market_data_type)


def get_market_data_mode() -> str:
    return _market_data_mode


def disconnect() -> None:
    """Disconnect the shared IB instance."""
    global _connected
    ib = get_ib()
    if ib.isConnected():
        ib.disconnect()
    _connected = False
    logger.info("IBKR shared connection disconnected")


def shutdown() -> None:
    """Fully tear down the shared IB resources for process shutdown."""
    global _ib, _executor, _executor_thread_id, _connected, _market_data_mode

    if _executor is not None:
        try:
            run_on_ib_thread(disconnect)
        except Exception:
            logger.exception("Error disconnecting shared IBKR connection during shutdown")
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
    else:
        try:
            disconnect()
        except Exception:
            logger.exception("Error disconnecting shared IBKR connection during shutdown")

    _executor_thread_id = None
    _connected = False
    _market_data_mode = "realtime"
    _ib = None
