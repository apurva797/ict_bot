"""Institutional-grade backtesting engine for the Arjuna algorithmic trading platform.

Simulates next-bar-open fills, realistic fees, slippage, ATR risk management,
conservative same-bar stop-loss execution, continuous equity marking, daily calendar
accounting, and out-of-sample testing. Strictly prevents lookahead bias.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from config import (
    ATR_PERIOD,
    ATR_SL_MULTIPLIER,
    DEFAULT_RR,
    MAX_DAILY_LOSS_R,
    MAX_TRADES_PER_DAY,
    MIN_RR,
)
from demo_data import MarketDataError, _validate_ohlcv_frame, fetch_historical_market_data
from demo_safety import assert_demo_mode, validate_risk_controls
from engine.daily_returns import compute_daily_returns_table
from engine.risk import (
    calculate_atr,
    calculate_atr_levels,
    calculate_position_size,
    validate_trade,
)

WARMUP_CANDLES = 100
DEFAULT_FEE_RATE = 0.0004       # 0.04%
DEFAULT_SLIPPAGE_RATE = 0.0001  # 0.01%
DEFAULT_COOLDOWN_MINUTES = 30
DEFAULT_INITIAL_CAPITAL = 10_000.0


# Standard column schema for trade logs
TRADE_COLUMNS = [
    "trade_id", "opened_at", "closed_at", "symbol", "strategy", "side",
    "entry_price", "stop_loss", "take_profit", "exit_price",
    "quantity", "notional", "risk_amount", "planned_rr", "actual_rr",
    "exit_reason", "result", "gross_pnl", "fees", "slippage_cost",
    "net_pnl", "r_multiple", "duration_minutes",
]


class InstitutionalBacktester:
    """Institutional-grade backtesting engine supporting single and multi-strategy execution."""

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        starting_capital: float = DEFAULT_INITIAL_CAPITAL,
        risk_fraction: float = 0.01,
        min_rr: float = MIN_RR,
        target_rr: float = DEFAULT_RR,
        fee_rate: float = DEFAULT_FEE_RATE,
        slippage_rate: float = DEFAULT_SLIPPAGE_RATE,
        cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
        max_concurrent_trades: int = 1,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
        max_daily_loss_r: float = MAX_DAILY_LOSS_R,
        warmup_candles: int = WARMUP_CANDLES,
    ):
        assert_demo_mode()
        validate_risk_controls(risk_fraction, target_rr, 1.0)
        if starting_capital <= 0 or not math.isfinite(float(starting_capital)):
            raise ValueError("Initial capital must be a positive finite amount.")
        if min_rr < MIN_RR:
            raise ValueError(f"Minimum R:R cannot be below {MIN_RR:.1f}R.")
        if target_rr < min_rr:
            raise ValueError("Target R:R must be at least the selected minimum R:R.")
        if not (0 <= fee_rate < 0.5):
            raise ValueError("Fee rate must be a reasonable non-negative rate.")
        if not (0 <= slippage_rate < 0.5):
            raise ValueError("Slippage rate must be a reasonable non-negative rate.")

        self.symbol = symbol
        self.timeframe = timeframe
        self.starting_capital = float(starting_capital)
        self.risk_fraction = float(risk_fraction)
        self.min_rr = float(min_rr)
        self.target_rr = float(target_rr)
        self.fee_rate = float(fee_rate)
        self.slippage_rate = float(slippage_rate)
        self.cooldown_minutes = int(cooldown_minutes)
        self.max_concurrent_trades = int(max_concurrent_trades)
        self.max_trades_per_day = int(max_trades_per_day)
        self.max_daily_loss_r = float(max_daily_loss_r)
        self.warmup_candles = int(warmup_candles)

    def run(
        self,
        frame: pd.DataFrame,
        strategy_name: str = "ARJUNA",
        parameters: Optional[Dict[str, Any]] = None,
        strategy_selection: Optional[Sequence[str]] = None,
        out_of_sample_ratio: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute a backtest on validated historical candles.

        Parameters
        ----------
        frame : pd.DataFrame
            OHLCV DataFrame indexed by UTC datetime.
        strategy_name : str
            Strategy to execute: 'ARJUNA' (ICT), 'MULTI_STRATEGY', 'QUANT_TREND',
            'QUANT_MEAN_REVERSION', or any plugin registered in platform_strategies.
        parameters : dict, optional
            Strategy-specific parameters (e.g. EMA periods, RSI thresholds).
        strategy_selection : sequence of str, optional
            For MULTI_STRATEGY, which strategy names to include in consensus.
        out_of_sample_ratio : float, optional
            If provided (e.g. 0.3 for 30% test period), computes separate IS and OOS metrics.

        Returns
        -------
        dict containing 'metrics', 'daily_table', 'monthly_table', 'trades',
        'equity_curve', 'blocked_signals', 'out_of_sample', and data diagnostics.
        """
        frame = _validate_ohlcv_frame(frame)
        if len(frame) < self.warmup_candles + 2:
            raise MarketDataError(
                f"Insufficient historical data: received {len(frame)} candles; need at least {self.warmup_candles + 2}."
            )

        signal_provider = self._create_signal_provider(strategy_name, parameters, strategy_selection, full_frame=frame)

        balance = self.starting_capital
        cash = self.starting_capital
        open_positions: List[Dict[str, Any]] = []
        trades: List[Dict[str, Any]] = []
        blocked: List[Dict[str, Any]] = []
        equity_records: List[Dict[str, Any]] = []

        last_entry_time: Optional[pd.Timestamp] = None
        daily_entries: Dict[str, int] = defaultdict(int)
        daily_r: Dict[str, float] = defaultdict(float)
        peak_marked_equity = balance
        max_drawdown = 0.0

        all_candles_list = [
            [int(ts.timestamp() * 1000), float(row.open), float(row.high),
             float(row.low), float(row.close), float(row.volume)]
            for ts, row in frame.iterrows()
        ]

        # Candle-by-candle simulation strictly from warmup_candles to end
        # The signal is evaluated on candles[:fill_idx] (representing closed bar fill_idx - 1)
        # The fill occurs at frame.iloc[fill_idx].open (T+1 open)
        for fill_idx in range(self.warmup_candles, len(frame)):
            current_bar = frame.iloc[fill_idx]
            timestamp = frame.index[fill_idx]
            current_open = float(current_bar.open)
            current_high = float(current_bar.high)
            current_low = float(current_bar.low)
            current_close = float(current_bar.close)

            # 1. Evaluate signal on closed candle prefix up to fill_idx (exclusive of fill candle)
            signal_candles = all_candles_list[:fill_idx]
            sig_side, sig_reason = signal_provider(signal_candles, frame.iloc[:fill_idx])

            # 2. Opposite confirmed signal closes existing position at current_open
            for position in list(open_positions):
                if sig_side in {"LONG", "SHORT"} and sig_side != position["side"]:
                    self._close_position(
                        position=position,
                        closed_at=timestamp,
                        raw_exit=current_open,
                        reason="Opposing confirmed signal",
                        trades=trades,
                    )
                    cash += position["last_closed_pnl"]
                    balance = cash
                    self._record_daily_pnl(trades[-1], daily_r)
                    open_positions.remove(position)

            # 3. Process open positions against current candle boundaries
            for position in list(open_positions):
                exit_result = self._check_bar_exit(position, current_open, current_high, current_low)
                if exit_result is not None:
                    raw_exit_price, exit_reason = exit_result
                    self._close_position(
                        position=position,
                        closed_at=timestamp,
                        raw_exit=raw_exit_price,
                        reason=exit_reason,
                        trades=trades,
                    )
                    cash += position["last_closed_pnl"]
                    balance = cash
                    self._record_daily_pnl(trades[-1], daily_r)
                    open_positions.remove(position)

            # 4. Check for new position entry if a confirmed signal exists
            if sig_side in {"LONG", "SHORT"}:
                day_key = str(timestamp.date())
                blocked_reason = None

                if len(open_positions) >= self.max_concurrent_trades:
                    blocked_reason = "Maximum concurrent trades reached."
                elif last_entry_time is not None and (timestamp - last_entry_time).total_seconds() < self.cooldown_minutes * 60:
                    blocked_reason = f"{self.cooldown_minutes}-minute cooldown active."
                elif daily_entries[day_key] >= self.max_trades_per_day:
                    blocked_reason = f"Maximum daily trades ({self.max_trades_per_day}) reached."
                elif daily_r[day_key] <= -abs(self.max_daily_loss_r):
                    blocked_reason = f"Maximum daily loss ({self.max_daily_loss_r}R) reached."

                if not blocked_reason:
                    try:
                        pos = self._create_position(
                            prefix_candles=signal_candles,
                            raw_entry=current_open,
                            side=sig_side,
                            timestamp=timestamp,
                            balance=balance,
                            open_positions=open_positions,
                            strategy_name=strategy_name,
                            reason=sig_reason,
                        )
                        if pos is None:
                            blocked_reason = "Risk, R:R distance, or notional check failed."
                        else:
                            open_positions.append(pos)
                            last_entry_time = timestamp
                            daily_entries[day_key] += 1

                            # Newly opened position could also hit stop or target in its entry candle
                            same_bar_exit = self._check_bar_exit(pos, current_open, current_high, current_low)
                            if same_bar_exit is not None:
                                raw_exit_price, exit_reason = same_bar_exit
                                self._close_position(
                                    position=pos,
                                    closed_at=timestamp,
                                    raw_exit=raw_exit_price,
                                    reason=exit_reason,
                                    trades=trades,
                                )
                                cash += pos["last_closed_pnl"]
                                balance = cash
                                self._record_daily_pnl(trades[-1], daily_r)
                                open_positions.remove(pos)
                    except Exception as exc:
                        blocked_reason = f"Execution error: {exc}"

                if blocked_reason:
                    blocked.append({
                        "timestamp": timestamp,
                        "symbol": self.symbol,
                        "side": sig_side,
                        "strategy": strategy_name,
                        "reason": blocked_reason,
                    })

            # 5. Continuous marked equity at bar close
            marked_equity = cash
            for pos in open_positions:
                marked_equity += self._unrealized_pnl(pos, current_close)
            peak_marked_equity = max(peak_marked_equity, marked_equity)
            dd = (peak_marked_equity - marked_equity) / peak_marked_equity if peak_marked_equity > 0 else 0.0
            max_drawdown = max(max_drawdown, dd)

            equity_records.append({
                "time": timestamp,
                "equity": marked_equity,
                "cash": cash,
                "open_positions": len(open_positions),
                "drawdown_pct": dd * 100.0,
            })

        # 6. Close any outstanding positions at final candle close
        final_timestamp = frame.index[-1]
        final_close = float(frame.close.iloc[-1])
        for position in list(open_positions):
            self._close_position(
                position=position,
                closed_at=final_timestamp,
                raw_exit=final_close,
                reason="End of backtest period",
                trades=trades,
            )
            cash += position["last_closed_pnl"]
            self._record_daily_pnl(trades[-1], daily_r)
            open_positions.remove(position)

        if equity_records:
            equity_records[-1]["equity"] = cash
            equity_records[-1]["cash"] = cash
            equity_records[-1]["open_positions"] = 0
            final_peak = max(r["equity"] for r in equity_records)
            equity_records[-1]["drawdown_pct"] = ((final_peak - cash) / final_peak * 100.0) if final_peak > 0 else 0.0

        equity_df = pd.DataFrame(equity_records).set_index("time") if equity_records else pd.DataFrame(
            columns=["equity", "cash", "open_positions", "drawdown_pct"], index=pd.DatetimeIndex([], name="time")
        )
        trades_df = pd.DataFrame(trades, columns=TRADE_COLUMNS) if trades else pd.DataFrame(columns=TRADE_COLUMNS)
        blocked_df = pd.DataFrame(blocked) if blocked else pd.DataFrame(columns=["timestamp", "symbol", "side", "strategy", "reason"])

        # 7. Compute daily returns table and performance metrics
        daily_df, monthly_df, full_metrics = compute_daily_returns_table(
            equity_curve=equity_df,
            trades=trades_df,
            starting_capital=self.starting_capital,
            start_date=frame.index[0],
            end_date=frame.index[-1],
        )

        # Additional diagnostics
        full_metrics["Symbol"] = self.symbol
        full_metrics["Timeframe"] = self.timeframe
        full_metrics["Strategy"] = strategy_name
        full_metrics["Candles processed"] = len(frame)
        full_metrics["Start date"] = str(frame.index[0])
        full_metrics["End date"] = str(frame.index[-1])
        full_metrics["Blocked signals count"] = len(blocked_df)
        full_metrics["Executed trades count"] = len(trades_df)

        # 8. Out-of-sample partition if requested
        oos_results = None
        if out_of_sample_ratio is not None and 0.0 < out_of_sample_ratio < 1.0:
            oos_results = self._evaluate_oos_split(
                frame=frame,
                trades_df=trades_df,
                equity_df=equity_df,
                daily_df=daily_df,
                ratio=out_of_sample_ratio,
            )

        return {
            "metrics": full_metrics,
            "daily_table": daily_df,
            "monthly_table": monthly_df,
            "trades": trades_df,
            "equity": equity_df,
            "blocked_signals": blocked_df,
            "out_of_sample": oos_results,
            "strategy": strategy_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start": frame.index[0],
            "end": frame.index[-1],
        }

    def _create_position(
        self,
        prefix_candles: List[List[float]],
        raw_entry: float,
        side: str,
        timestamp: pd.Timestamp,
        balance: float,
        open_positions: List[Dict[str, Any]],
        strategy_name: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        """Calculate position sizing and ATR risk levels."""
        atr = calculate_atr(prefix_candles, ATR_PERIOD)
        if atr is None or atr <= 0:
            return None

        # Apply slippage on entry
        entry_price = raw_entry * (1.0 + self.slippage_rate if side == "LONG" else 1.0 - self.slippage_rate)
        levels = calculate_atr_levels(entry_price, atr, side, sl_multiplier=ATR_SL_MULTIPLIER, rr=self.target_rr)
        if not levels:
            return None

        valid, actual_rr, _ = validate_trade(entry_price, levels["stop"], levels["target"], side, min_rr=self.min_rr)
        if not valid:
            return None

        risk_budget = balance * self.risk_fraction
        stop_dist = abs(entry_price - levels["stop"])
        if stop_dist <= 0:
            return None

        # Position size bounded by risk budget and 1x maximum leverage
        raw_quantity = risk_budget / stop_dist
        max_notional_qty = balance / entry_price
        quantity = min(raw_quantity, max_notional_qty)

        if quantity <= 0:
            return None

        # Ensure total active notional does not exceed balance
        current_open_notional = sum(p["entry_price"] * p["quantity"] for p in open_positions)
        if (current_open_notional + entry_price * quantity) > (balance * 1.000001):
            return None

        entry_fee = entry_price * quantity * self.fee_rate
        entry_slippage_cost = abs(entry_price - raw_entry) * quantity

        return {
            "trade_id": len(open_positions) + 1,
            "opened_at": timestamp,
            "symbol": self.symbol,
            "strategy": strategy_name,
            "side": side,
            "raw_entry": raw_entry,
            "entry_price": entry_price,
            "stop_loss": levels["stop"],
            "take_profit": levels["target"],
            "planned_rr": self.target_rr,
            "actual_rr": actual_rr,
            "quantity": quantity,
            "notional": entry_price * quantity,
            "risk_amount": min(risk_budget, stop_dist * quantity),
            "entry_fee": entry_fee,
            "entry_slippage_cost": entry_slippage_cost,
            "entry_reason": reason,
        }

    def _check_bar_exit(
        self,
        position: Dict[str, Any],
        bar_open: float,
        bar_high: float,
        bar_low: float,
    ) -> Optional[Tuple[float, str]]:
        """Conservative bar exit evaluation.

        If both Stop Loss and Take Profit levels are touched within the same candle,
        Stop Loss is conservatively assumed to have triggered first.
        """
        side = position["side"]
        stop = position["stop_loss"]
        target = position["take_profit"]

        if side == "LONG":
            stop_hit = bar_low <= stop
            target_hit = bar_high >= target
            if stop_hit and target_hit:
                # Conservative rule: stop-loss executed first
                raw_exit = bar_open if bar_open <= stop else stop
                return raw_exit, "Stop loss (Conservative same-bar fill)"
            elif stop_hit:
                raw_exit = bar_open if bar_open <= stop else stop
                return raw_exit, "Stop loss"
            elif target_hit:
                raw_exit = bar_open if bar_open >= target else target
                return raw_exit, "Take profit"
        else:  # SHORT
            stop_hit = bar_high >= stop
            target_hit = bar_low <= target
            if stop_hit and target_hit:
                # Conservative rule: stop-loss executed first
                raw_exit = bar_open if bar_open >= stop else stop
                return raw_exit, "Stop loss (Conservative same-bar fill)"
            elif stop_hit:
                raw_exit = bar_open if bar_open >= stop else stop
                return raw_exit, "Stop loss"
            elif target_hit:
                raw_exit = bar_open if bar_open <= target else target
                return raw_exit, "Take profit"

        return None

    def _close_position(
        self,
        position: Dict[str, Any],
        closed_at: pd.Timestamp,
        raw_exit: float,
        reason: str,
        trades: List[Dict[str, Any]],
    ) -> None:
        """Close an active position, deduct fees and slippage, and append to trades."""
        side = position["side"]
        exit_price = raw_exit * (1.0 - self.slippage_rate if side == "LONG" else 1.0 + self.slippage_rate)
        quantity = position["quantity"]

        gross_pnl = (exit_price - position["entry_price"]) * quantity * (1.0 if side == "LONG" else -1.0)
        exit_fee = exit_price * quantity * self.fee_rate
        total_fees = position["entry_fee"] + exit_fee
        exit_slippage_cost = abs(exit_price - raw_exit) * quantity
        total_slippage = position["entry_slippage_cost"] + exit_slippage_cost
        net_pnl = gross_pnl - total_fees

        duration_min = max(0.0, (closed_at - position["opened_at"]).total_seconds() / 60.0)
        risk_amt = position["risk_amount"]
        r_mult = (net_pnl / risk_amt) if risk_amt > 0 else 0.0

        position["last_closed_pnl"] = net_pnl

        trades.append({
            "trade_id": len(trades) + 1,
            "opened_at": position["opened_at"],
            "closed_at": closed_at,
            "symbol": position["symbol"],
            "strategy": position["strategy"],
            "side": side,
            "entry_price": round(position["entry_price"], 4),
            "stop_loss": round(position["stop_loss"], 4),
            "take_profit": round(position["take_profit"], 4),
            "exit_price": round(exit_price, 4),
            "quantity": round(quantity, 6),
            "notional": round(position["notional"], 2),
            "risk_amount": round(risk_amt, 2),
            "planned_rr": round(position["planned_rr"], 2),
            "actual_rr": round(position["actual_rr"], 2),
            "exit_reason": reason,
            "result": "Win" if net_pnl > 0 else "Loss" if net_pnl < 0 else "Flat",
            "gross_pnl": round(gross_pnl, 2),
            "fees": round(total_fees, 2),
            "slippage_cost": round(total_slippage, 2),
            "net_pnl": round(net_pnl, 2),
            "r_multiple": round(r_mult, 3),
            "duration_minutes": round(duration_min, 1),
        })

    def _unrealized_pnl(self, position: Dict[str, Any], current_close: float) -> float:
        """Calculate marked unrealized P&L net of estimated exit fees."""
        side = position["side"]
        qty = position["quantity"]
        gross = (current_close - position["entry_price"]) * qty * (1.0 if side == "LONG" else -1.0)
        est_exit_fee = current_close * qty * self.fee_rate
        return gross - position["entry_fee"] - est_exit_fee

    def _record_daily_pnl(self, trade: Dict[str, Any], daily_r: Dict[str, float]) -> None:
        day_str = str(pd.Timestamp(trade["closed_at"]).date())
        daily_r[day_str] += float(trade.get("r_multiple", 0.0))

    def _create_signal_provider(
        self,
        strategy_name: str,
        parameters: Optional[Dict[str, Any]],
        strategy_selection: Optional[Sequence[str]],
        full_frame: Optional[pd.DataFrame] = None,
    ) -> Callable[[List[List[float]], pd.DataFrame], Tuple[Optional[str], str]]:
        """Return a callable (candles_list, sub_frame) -> (side, reason) without lookahead."""
        strategy_name_upper = strategy_name.upper().replace("-", "_").replace(" ", "_")

        if strategy_name_upper in {"ARJUNA", "ICT"}:
            from strategies.ict import ict_signal
            def arjuna_signals(candles: List[List[float]], sub_frame: pd.DataFrame) -> Tuple[Optional[str], str]:
                if len(candles) < WARMUP_CANDLES:
                    return None, "Warmup"
                res = ict_signal(candles)
                if isinstance(res, dict) and res.get("side") in {"LONG", "SHORT"}:
                    return res["side"], res.get("reason", "ARJUNA institutional signal")
                return None, "Neutral"
            return arjuna_signals

        elif strategy_name_upper == "MULTI_STRATEGY":
            from bot import analyze_multi_strategy_candles, multi_strategy_names
            available_names = set(multi_strategy_names())
            selected = tuple(strategy_selection) if strategy_selection else tuple(available_names)

            def multi_signals(candles: List[List[float]], sub_frame: pd.DataFrame) -> Tuple[Optional[str], str]:
                if len(candles) < WARMUP_CANDLES:
                    return None, "Warmup"
                res = analyze_multi_strategy_candles(candles, strategy_selection=selected, historical=True)
                final = res.get("final_signal", {})
                if final.get("confirmation_passed") and final.get("side") in {"LONG", "SHORT"}:
                    return final["side"], final.get("reason", "Multi-strategy consensus")
                return None, "No confirmation"
            return multi_signals

        # Try to resolve through strategy plugin registry
        from platform_strategies import strategy_registry
        plugin_id = strategy_name.lower().replace(" ", "_")
        plugin = None
        for p in strategy_registry.list():
            if p.id.lower() == plugin_id or p.name.upper() == strategy_name_upper:
                plugin = strategy_registry.get(p.id)
                break

        if plugin is not None:
            full_series = plugin.signal_series(full_frame, parameters) if full_frame is not None else None
            def plugin_signals(candles: List[List[float]], sub_frame: pd.DataFrame) -> Tuple[Optional[str], str]:
                if len(sub_frame) < plugin.metadata.min_candles:
                    return None, "Warmup"
                if full_series is not None:
                    last_sig = full_series.iloc[len(sub_frame) - 1]
                else:
                    sig_series = plugin.signal_series(sub_frame, parameters)
                    last_sig = sig_series.iloc[-1]
                if last_sig in {"BUY", "LONG"}:
                    return "LONG", f"{plugin.metadata.name} buy rule"
                elif last_sig in {"SELL", "SHORT"}:
                    return "SHORT", f"{plugin.metadata.name} sell rule"
                return None, "Neutral"
            return plugin_signals

        # Fallback to single strategies in bot.MULTI_STRATEGY_FUNCTIONS
        from bot import MULTI_STRATEGY_FUNCTIONS, safe_strategy_call
        fn_map = {name: fn_name for name, fn_name in MULTI_STRATEGY_FUNCTIONS}
        if strategy_name_upper in fn_map:
            import bot as bot_module
            fn = getattr(bot_module, fn_map[strategy_name_upper], None)
            if fn is not None:
                def single_bot_signal(candles: List[List[float]], sub_frame: pd.DataFrame) -> Tuple[Optional[str], str]:
                    if len(candles) < 35:
                        return None, "Warmup"
                    out = safe_strategy_call(strategy_name_upper, fn, candles)
                    if isinstance(out, dict) and out.get("side") in {"LONG", "SHORT"}:
                        score = float(out.get("score", 0.0))
                        if score >= 50.0:
                            return out["side"], out.get("reason", f"{strategy_name_upper} signal")
                    return None, "Neutral"
                return single_bot_signal

        raise ValueError(f"Unknown strategy: '{strategy_name}'.")

    def _evaluate_oos_split(
        self,
        frame: pd.DataFrame,
        trades_df: pd.DataFrame,
        equity_df: pd.DataFrame,
        daily_df: pd.DataFrame,
        ratio: float,
    ) -> Dict[str, Any]:
        """Compute In-Sample vs Out-of-Sample metrics chronologically."""
        total_bars = len(frame)
        split_idx = int(total_bars * ratio)
        split_time = frame.index[split_idx]

        is_daily = daily_df.loc[daily_df.index < str(split_time.date())]
        oos_daily = daily_df.loc[daily_df.index >= str(split_time.date())]

        is_trades = trades_df.loc[trades_df["closed_at"] < split_time] if not trades_df.empty else trades_df
        oos_trades = trades_df.loc[trades_df["closed_at"] >= split_time] if not trades_df.empty else trades_df

        def _sub_metrics(sub_daily: pd.DataFrame, sub_trades: pd.DataFrame) -> Dict[str, Any]:
            if sub_daily.empty:
                return {}
            ret_series = sub_daily["Daily Return %"] / 100.0
            pnl_series = sub_daily["Daily P&L"]
            tot_pnl = float(pnl_series.sum())
            start_eq = float(sub_daily["Starting Equity"].iloc[0])
            end_eq = float(sub_daily["Ending Equity"].iloc[-1])
            tot_ret = ((end_eq / start_eq) - 1.0) * 100.0 if start_eq > 0 else 0.0
            n_t = len(sub_trades)
            wins = int((sub_trades["net_pnl"] > 0).sum()) if n_t else 0
            sharpe = float((ret_series.mean() / ret_series.std(ddof=1)) * math.sqrt(365.25)) if len(ret_series) > 1 and ret_series.std(ddof=1) > 0 else None
            return {
                "Days": len(sub_daily),
                "Trades": n_t,
                "Net P&L": round(tot_pnl, 2),
                "Return %": round(tot_ret, 2),
                "Win Rate %": round((wins / n_t * 100.0) if n_t else 0.0, 1),
                "Sharpe": round(sharpe, 2) if sharpe is not None else None,
                "Max DD %": round(float(sub_daily["Drawdown %"].max()), 2),
                "Avg Daily Return %": round(float(ret_series.mean() * 100.0), 4),
            }

        return {
            "split_timestamp": str(split_time),
            "split_ratio": ratio,
            "in_sample": _sub_metrics(is_daily, is_trades),
            "out_of_sample": _sub_metrics(oos_daily, oos_trades),
        }


def run_one_year_backtest(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    strategy_name: str = "ARJUNA",
    starting_capital: float = DEFAULT_INITIAL_CAPITAL,
    risk_fraction: float = 0.01,
    fee_rate: float = DEFAULT_FEE_RATE,
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE,
    out_of_sample_split: float = 0.70,
    historical_candles: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Execute a genuine one-year historical backtest using real market data.

    If historical_candles is not provided, loads 365 calendar days from the
    primary public market data provider.
    """
    if historical_candles is None:
        now = pd.Timestamp.now(tz="UTC")
        one_year_ago = now - pd.Timedelta(days=365)
        data_result = fetch_historical_market_data(symbol, timeframe, one_year_ago, now)
        candles = data_result.frame
        source_name = data_result.source
    else:
        candles = _validate_ohlcv_frame(historical_candles)
        source_name = "Supplied historical DataFrame"

    backtester = InstitutionalBacktester(
        symbol=symbol,
        timeframe=timeframe,
        starting_capital=starting_capital,
        risk_fraction=risk_fraction,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
    )

    result = backtester.run(
        frame=candles,
        strategy_name=strategy_name,
        out_of_sample_ratio=out_of_sample_split,
    )
    result["data_source"] = source_name
    return result
