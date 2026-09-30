# ============================================================
# MARKET CONFIGURATION
# ============================================================

MARKET = "CRYPTO"

SYMBOL = "BTC/USDT"

HTF_TIMEFRAME = "1h"
LTF_TIMEFRAME = "5m"

HTF_LIMIT = 300
LTF_LIMIT = 500


# ============================================================
# TRADE / RISK SETTINGS
# ============================================================

PAPER_TRADING = True

MIN_RR = 2.0

RISK_PER_TRADE = 0.01
MAX_LEVERAGE = 1.0

MAX_TRADES_PER_DAY = 3
MAX_DAILY_LOSS_R = 2.0


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

ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 1.5


# ============================================================
# ICT
# ============================================================

ICT_ENABLED = True

DISPLACEMENT_MULTIPLIER = 1.5

SIGNAL_COOLDOWN_MINUTES = 30


# ============================================================
# KILL ZONES - UTC
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

NEWS_FILTER_ENABLED = True

# Manually set True when a major event is approaching.
NEWS_BLACKOUT = False


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
