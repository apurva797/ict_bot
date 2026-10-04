"""Screen modules.

Each screen owns its own layout and is responsible for every state it can be
in: loading, empty, populated, and failure. Screens read data through
:mod:`ui.state` and :mod:`ui.marketdata`, and call the engines directly for any
action that changes paper state.
"""

from __future__ import annotations