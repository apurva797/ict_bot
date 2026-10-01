# This module is the backwards-compatible facade over platform_core.settings.
# Every value below is read from the single settings source, so risk, market,
# and environment configuration can no longer drift between modules.

from platform_core.settings import load_settings


_SETTINGS = load_settings()


# ============================================================
# MARKET CONFIGURATION
# ============================================================

MARKET = "CRYPTO"

SYMBOL = "BTC/USDT"
HTF_TIMEFRAME = "1h"
LTF_TIMEFRAME = _SETTINGS.market.default_timeframe

HTF_LIMIT = 300
LTF_LIMIT = _SETTINGS.market.default_candles
LTF_LIMIT = 500


# ============================================================
# TRADE / RISK SETTINGS
# ============================================================

PAPER_TRADING = _SETTINGS.environment != "LIVE"

MIN_RR = _SETTINGS.risk.min_rr
DEFAULT_RR = _SETTINGS.risk.default_rr

RISK_PER_TRADE = _SETTINGS.risk.risk_per_trade
MAX_LEVERAGE = _SETTINGS.risk.max_leverage

MAX_TRADES_PER_DAY = _SETTINGS.risk.max_trades_per_day
MAX_DAILY_LOSS_R = _SETTINGS.risk.max_daily_loss_r
COOLDOWN_MINUTES = _SETTINGS.risk.cooldown_minutes
MAX_OPEN_POSITIONS = _SETTINGS.risk.max_open_positions


# ============================================================
# CONFIRMATION ENGINE
# ============================================================

# Normal condition = 1 point
NORMAL_CONDITION_POINTS = 1

# Heavy condition = 2 points
HEAVY_CONDITION_POINTS = 2

# Minimum normal-equivalent confirmation
MIN_CONFIRMATION_POINTS = 4

# Minimum number of independent heavy conditions
MIN_HEAVY_CONDITIONS = 2


# ============================================================
# KAMA / EFFICIENCY RATIO
# ============================================================

KAMA_PERIOD = 10
KAMA_FAST = 2
KAMA_SLOW = 30


# ============================================================
# DONCHIAN
# ============================================================

DONCHIAN_PERIOD = 20


# ============================================================
# ATR / VOLATILITY
# ============================================================

ATR_PERIOD = _SETTINGS.risk.atr_period
ATR_SL_MULTIPLIER = _SETTINGS.risk.atr_sl_multiplier


# ============================================================
# ICT
# ============================================================

ICT_ENABLED = True

DISPLACEMENT_MULTIPLIER = 1.5

SIGNAL_COOLDOWN_MINUTES = _SETTINGS.risk.cooldown_minutes


# ============================================================
# KILL ZONES - UTC (informational only, never a paper entry gate)
# ============================================================

# London
LONDON_START = 7
LONDON_END = 10

# New York
NEW_YORK_START = 12
NEW_YORK_END = 15

# Asian range
ASIAN_START = 0
ASIAN_END = 5


# ============================================================
# NEWS PROTECTION
# ============================================================

NEWS_FILTER_ENABLED = _SETTINGS.news_filter_enabled

# Manually set True when a major event is approaching.
NEWS_BLACKOUT = _SETTINGS.news_blackout


# ============================================================
# ADVANCED RISK SYSTEM
# ============================================================

OPTIMAL_F_ENABLED = True
OPTIMAL_F_FRACTION = 0.25

GASP_ENABLED = True

VOLATILITY_STABILIZATION_ENABLED = True

TARGET_PORTFOLIO_VOLATILITY = 0.15


# ============================================================
# EXECUTION
# ============================================================

IOC_ENABLED = True
TWAP_ENABLED = True

# TWAP will only be used when order size justifies it.
TWAP_MIN_ORDER_SIZE = 0.10


# ============================================================
# PERSISTENCE
# ============================================================

REDIS_ENABLED = False
POSTGRES_ENABLED = False

CRASH_RECOVERY_ENABLED = True

