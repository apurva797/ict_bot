"""Central configuration. Risk, provider, and environment values are read here
once instead of being scattered through strategy, risk, and execution code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else default


@dataclass(frozen=True)
class MarketConfig:
    symbols: tuple = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
    default_symbol: str = "BTC/USDT"
    timeframes: tuple = ("5m", "15m", "30m", "1h", "4h")
    default_timeframe: str = "5m"
    min_candles: int = 100
    default_candles: int = 500
    max_candles: int = 1000
    # A series is stale when its last candle is older than this multiple of the
    # candle interval plus a small grace period.
    stale_interval_multiple: float = 3.0
    stale_grace_seconds: float = 90.0


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.01
    # Enforced floor. New setups target ``default_rr`` (2R) and must clear the floor.
    min_rr: float = 1.5
    default_rr: float = 2.0
    max_leverage: float = 1.0
    cooldown_minutes: int = 30
    max_open_positions: int = 1
    max_trades_per_day: int = 3
    max_daily_loss_r: float = 2.0
    max_portfolio_risk_r: float = 3.0
    fee_rate: float = 0.0004
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5
    starting_capital: float = 10_000.0


@dataclass(frozen=True)
class DisplayConfig:
    """Presentation-only settings; never used to make a trading decision."""

    currency: str = "USD"
    locale: str = "en-US"
    timezone: str = "UTC"
    theme: str = "auto"
    price_precision: int = 2
    quantity_precision: int = 6


@dataclass(frozen=True)
class Settings:
    environment: str = "PAPER"
    market: MarketConfig = field(default_factory=MarketConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    news_filter_enabled: bool = True
    news_blackout: bool = False
    # Time-of-day kill zones are informational only and never gate a paper entry.
    kill_zone_enabled: bool = False
    london_hours: tuple = (7, 10)
    new_york_hours: tuple = (12, 15)

    @property
    def is_paper_only(self) -> bool:
        return self.environment.upper() != "LIVE"


def load_settings() -> Settings:
    """Build settings from the environment, keeping conservative defaults."""
    market = MarketConfig(
        default_symbol=_env_str("PLATFORM_SYMBOL", "BTC/USDT"),
        default_timeframe=_env_str("PLATFORM_TIMEFRAME", "5m"),
        min_candles=_env_int("PLATFORM_MIN_CANDLES", 100),
        default_candles=_env_int("PLATFORM_CANDLES", 500),
    )
    risk = RiskConfig(
        risk_per_trade=_env_float("RISK_PER_TRADE", 0.01),
        min_rr=_env_float("MIN_RR", 1.5),
        default_rr=_env_float("DEFAULT_RR", 2.0),
        max_leverage=_env_float("MAX_LEVERAGE", 1.0),
        cooldown_minutes=_env_int("COOLDOWN_MINUTES", 30),
        max_open_positions=_env_int("MAX_OPEN_POSITIONS", 1),
        max_trades_per_day=_env_int("MAX_TRADES_PER_DAY", 3),
        max_daily_loss_r=_env_float("MAX_DAILY_LOSS_R", 2.0),
        starting_capital=_env_float("STARTING_CAPITAL", 10_000.0),
    )
    display = DisplayConfig(
        currency=_env_str("DISPLAY_CURRENCY", "USD"),
        locale=_env_str("DISPLAY_LOCALE", "en-US"),
        timezone=_env_str("DISPLAY_TIMEZONE", "UTC"),
        theme=_env_str("DISPLAY_THEME", "auto"),
    )
    return Settings(
        environment=_env_str("TRADING_ENVIRONMENT", "PAPER").upper(),
        market=market,
        risk=risk,
        display=display,
        news_filter_enabled=_env_bool("NEWS_FILTER_ENABLED", True),
        news_blackout=_env_bool("NEWS_BLACKOUT", False),
        kill_zone_enabled=_env_bool("KILL_ZONE_ENABLED", False),
    )
