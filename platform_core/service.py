"""Application service layer.

This is the boundary a future HTTP API, Flutter app, or React Native client
consumes. It contains no Streamlit imports, so the trading engine stays
independent from the UI.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from platform_core import indicators as indicator_lib
from platform_core.analytics import compute_analytics, equity_curve
from platform_core.errors import PlatformError
from platform_core.execution import PaperExecutionEngine
from platform_core.ict import analyze_ict
from platform_core.market_data import MarketDataService, MarketDataSnapshot
from platform_core.risk import RiskEngine
from platform_core.settings import Settings, load_settings
from platform_core.status import Status


LOGGER = logging.getLogger("platform_core.service")


def _default_providers():
    from platform_core.market_data import default_service
    return default_service().providers


class TradingService:
    """Coordinates market data, strategies, risk, and paper execution."""

    def __init__(self, settings: Settings | None = None,
                 market_data: MarketDataService | None = None):
        self.settings = settings or load_settings()
        self.market_data = market_data or MarketDataService(
            providers=_default_providers(), config=self.settings.market
        )
        self.risk_engine = RiskEngine(self.settings.risk)
        self.execution = PaperExecutionEngine(self.risk_engine, self.settings.risk)

    def get_snapshot(self, symbol: str | None = None, timeframe: str | None = None,
                     limit: int | None = None) -> MarketDataSnapshot:
        return self.market_data.get_snapshot(
            symbol or self.settings.market.default_symbol,
            timeframe or self.settings.market.default_timeframe,
            limit or self.settings.market.default_candles,
        )

    def markets(self) -> list[dict[str, Any]]:
        """Watchlist rows; unavailable symbols are reported, never hidden."""
        rows: list[dict[str, Any]] = []
        for symbol in self.settings.market.symbols:
            try:
                snapshot = self.get_snapshot(symbol, self.settings.market.default_timeframe, 150)
            except PlatformError as exc:
                rows.append({
                    "symbol": symbol,
                    "status": exc.status.value,
                    "message": exc.message,
                })
                continue
            frame = snapshot.frame
            rows.append({
                "symbol": symbol,
                "timeframe": snapshot.timeframe,
                "price": float(frame["close"].iloc[-1]),
                "change_pct": self.market_data.price_change_pct(snapshot),
                "volume": float(frame["volume"].iloc[-1]),
                "high": float(frame["high"].iloc[-1]),
                "low": float(frame["low"].iloc[-1]),
                "health": snapshot.health.value,
                "is_live": snapshot.is_live,
                "source": snapshot.source,
                "status": Status.OK.value,
            })
        return rows

    def ict_analysis(self, frame: pd.DataFrame,
                     htf_frame: pd.DataFrame | None = None) -> dict[str, Any]:
        analysis = analyze_ict(frame, htf_frame, rr=self.settings.risk.default_rr,
                               atr_period=self.settings.risk.atr_period,
                               atr_multiplier=self.settings.risk.atr_sl_multiplier)
        return analysis.to_dict()

    def strategy_status(self, frame: pd.DataFrame | None) -> dict[str, Any]:
        """ACTIVE / MONITORING / NO_SETUP / DATA_UNAVAILABLE / ERROR, never a guess."""
        if frame is None or frame.empty:
            return {"state": "DATA_UNAVAILABLE", "message": "No market data is loaded."}
        try:
            analysis = self.ict_analysis(frame)
        except PlatformError as exc:
            state = {
                Status.INSUFFICIENT_DATA: "INSUFFICIENT_DATA",
                Status.DATA_UNAVAILABLE: "DATA_UNAVAILABLE",
                Status.STRATEGY_ERROR: "ERROR",
            }.get(exc.status, "ERROR")
            return {"state": state, "message": exc.message}
        return {
            "state": "ACTIVE" if analysis["has_setup"] else "MONITORING",
            "side": analysis["side"],
            "score": analysis["score"],
            "reason": analysis["reason"],
            "stages": analysis["stages"],
        }

    def analytics(self, account: dict) -> dict[str, Any]:
        trades = list(account.get("trades", []))
        return {
            "environment": self.settings.environment,
            **compute_analytics(trades),
            "equity_curve": equity_curve(trades, float(account.get("starting_capital", 0.0))),
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "environment": self.settings.environment,
            "paper_only": self.settings.is_paper_only,
            "live_orders_enabled": False,
            "symbols": list(self.settings.market.symbols),
            "timeframes": list(self.settings.market.timeframes),
            "risk": {
                "risk_per_trade": self.settings.risk.risk_per_trade,
                "min_rr": self.settings.risk.min_rr,
                "max_leverage": self.settings.risk.max_leverage,
                "cooldown_minutes": self.settings.risk.cooldown_minutes,
                "max_trades_per_day": self.settings.risk.max_trades_per_day,
            },
            "indicators": [item["id"] for item in indicator_lib.available_indicators()],
        }

    def catalogue(self) -> dict[str, Any]:
        return {"indicators": indicator_lib.available_indicators()}