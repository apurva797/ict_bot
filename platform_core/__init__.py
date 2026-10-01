"""UI-independent trading platform core.

The Streamlit front end is only one consumer of this package. Every module here
is importable without Streamlit so the same code can back a future HTTP API or a
Flutter/React Native client.

Pipeline:
    MarketDataService -> DataValidation -> Strategy -> Signal -> RiskEngine
    -> PaperExecution -> Portfolio/Analytics
"""

from platform_core.status import DataHealth, ExecutionEnv, Outcome, Status
from platform_core.errors import PlatformError

__all__ = [
    "DataHealth",
    "ExecutionEnv",
    "Outcome",
    "PlatformError",
    "Status",
]
