from datetime import datetime, timezone
import csv
import os
from demo_safety import assert_demo_mode, validate_risk_controls


class PaperTrader:

    def __init__(
        self,
        starting_balance=10000.0,
        risk_amount=100.0,
        fee_rate=0.0004,
        slippage_rate=0.0001,
        slippage=None,
    ):
        self.starting_balance = starting_balance
        self.balance = starting_balance

        self.default_risk_amount = risk_amount

        self.fee_rate = fee_rate

        # Backward compatibility
        if slippage is not None:
            slippage_rate = slippage

        self.slippage_rate = slippage_rate

        self.position = None
        self.history = []

        self.daily_r = 0.0
        self.daily_trade_count = 0

        self.current_day = datetime.now(
            timezone.utc
        ).date()

    # ========================================================
    # DAILY RESET
    # ========================================================

    def reset_daily_if_needed(self):

        today = datetime.now(
            timezone.utc
        ).date()

        if today != self.current_day:

            self.current_day = today
            self.daily_r = 0.0
            self.daily_trade_count = 0

    # ========================================================
    # ENTRY SLIPPAGE
    # ========================================================

    def apply_entry_slippage(self, price, side):

        if side == "LONG":
            return price * (
                1 + self.slippage_rate
            )

        if side == "SHORT":
            return price * (
                1 - self.slippage_rate
            )

        return price

    # ========================================================
    # EXIT SLIPPAGE
    # ========================================================

    def apply_exit_slippage(self, price, side):

        if side == "LONG":
            return price * (
                1 - self.slippage_rate
            )

        if side == "SHORT":
            return price * (
                1 + self.slippage_rate
            )

        return price

    # ========================================================
    # OPEN POSITION
    # ========================================================

    def open_position(
        self,
        side,
        entry,
        stop,
        target,
        score=0,
        risk_amount=None,
        quantity=None,
    ):

        assert_demo_mode()

        self.reset_daily_if_needed()

        if self.position is not None:
            return False

        if side not in ("LONG", "SHORT"):
            return False

        if entry <= 0:
            return False

        if stop <= 0:
            return False

        if target <= 0:
            return False

        if side == "LONG":
            risk_distance = entry - stop
            reward_distance = target - entry
        else:
            risk_distance = stop - entry
            reward_distance = entry - target
        if risk_distance <= 0 or reward_distance / risk_distance < 2.0:
            return False

        if risk_amount is None:
            risk_amount = self.default_risk_amount

        # ----------------------------------------------------
        # QUANTITY
        # ----------------------------------------------------

        if quantity is None:

            risk_distance = abs(
                entry - stop
            )

            if risk_distance <= 0:
                return False

            quantity = (
                risk_amount
                / risk_distance
            )

        if quantity <= 0:
            return False

        if self.balance <= 0:
            return False
        if risk_amount > self.balance * 0.01 + 1e-9:
            return False
        actual_risk = risk_distance * quantity
        if actual_risk > self.balance * 0.01 + 1e-9:
            return False
        risk_amount = actual_risk

        # ----------------------------------------------------
        # ACTUAL ENTRY
        # ----------------------------------------------------

        actual_entry = self.apply_entry_slippage(
            entry,
            side
        )

        notional = actual_entry * quantity

        if notional > self.balance + 1e-9:
            return False

        try:
            validate_risk_controls(
                risk_amount / self.balance,
                reward_distance / risk_distance,
                notional / self.balance,
            )
        except ValueError:
            return False

        entry_fee = notional * self.fee_rate

        # ----------------------------------------------------
        # POSITION
        # ----------------------------------------------------

        self.position = {

            "side": side,
            "signal_entry": entry,
            "entry": actual_entry,
            "stop": stop,
            "target": target,
            "quantity": quantity,
            "notional": notional,
            "risk_amount": risk_amount,
            "score": score,
            "entry_fee": entry_fee,

            "opened_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        self.daily_trade_count += 1

        return True

    # ========================================================
    # CLOSE POSITION
    # ========================================================

    def close_position(
        self,
        exit_price,
        reason="MANUAL",
    ):

        assert_demo_mode()

        self.reset_daily_if_needed()

        if self.position is None:
            return None

        position = self.position

        side = position["side"]
        entry = position["entry"]
        quantity = position["quantity"]
        stop = position["stop"]
        risk_amount = position["risk_amount"]

        # ----------------------------------------------------
        # ACTUAL EXIT
        # ----------------------------------------------------

        actual_exit = self.apply_exit_slippage(
            exit_price,
            side
        )

        # ----------------------------------------------------
        # GROSS P&L
        # ----------------------------------------------------

        if side == "LONG":

            gross_pnl = (
                actual_exit - entry
            ) * quantity

            risk_distance = (
                entry - stop
            )

        elif side == "SHORT":

            gross_pnl = (
                entry - actual_exit
            ) * quantity

            risk_distance = (
                stop - entry
            )

        else:

            gross_pnl = 0.0
            risk_distance = 0.0

        # ----------------------------------------------------
        # EXIT FEE
        # ----------------------------------------------------

        exit_notional = actual_exit * quantity

        exit_fee = (
            exit_notional
            * self.fee_rate
        )

        total_fees = (
            position["entry_fee"]
            + exit_fee
        )

        # ----------------------------------------------------
        # NET P&L
        # ----------------------------------------------------

        net_pnl = (
            gross_pnl
            - total_fees
        )

        # ----------------------------------------------------
        # R MULTIPLE
        # ----------------------------------------------------

        if (
            risk_distance > 0
            and quantity > 0
        ):

            gross_r = (
                gross_pnl
                / (
                    risk_distance
                    * quantity
                )
            )

        else:

            gross_r = 0.0

        if risk_amount > 0:

            net_r = (
                net_pnl
                / risk_amount
            )

        else:

            net_r = 0.0

        # ----------------------------------------------------
        # UPDATE BALANCE
        # ----------------------------------------------------

        self.balance += net_pnl

        self.daily_r += net_r

        # ----------------------------------------------------
        # TRADE RECORD
        # ----------------------------------------------------

        trade = {

            "side": side,
            "entry": entry,
            "exit": actual_exit,
            "stop": stop,
            "target": position["target"],
            "quantity": quantity,
            "notional": position["notional"],
            "risk_amount": risk_amount,
            "score": position["score"],
            "gross_pnl": gross_pnl,
            "fees": total_fees,
            "net_pnl": net_pnl,
            "gross_r": gross_r,
            "r_multiple": net_r,
            "reason": reason,

            "opened_at": position[
                "opened_at"
            ],

            "closed_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        self.history.append(trade)

        # ----------------------------------------------------
        # SAVE CSV
        # ----------------------------------------------------

        self.save_trade_to_csv(trade)

        # ----------------------------------------------------
        # CLEAR POSITION
        # ----------------------------------------------------

        self.position = None

        return trade

    # ========================================================
    # SAVE TRADE
    # ========================================================

    def save_trade_to_csv(self, trade):

        file_path = "trade_history.csv"

        fields = [
            "side",
            "entry",
            "exit",
            "stop",
            "target",
            "quantity",
            "notional",
            "risk_amount",
            "score",
            "gross_pnl",
            "fees",
            "net_pnl",
            "gross_r",
            "r_multiple",
            "reason",
            "opened_at",
            "closed_at",
        ]

        file_exists = os.path.exists(file_path)

        with open(
            file_path,
            "a",
            newline="",
            encoding="utf-8"
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=fields
            )

            if not file_exists:
                writer.writeheader()

            writer.writerow(trade)

    # ========================================================
    # STATISTICS
    # ========================================================

    def statistics(self):

        self.reset_daily_if_needed()

        total_trades = len(self.history)

        wins = sum(
            1
            for trade in self.history
            if trade["net_pnl"] > 0
        )

        losses = sum(
            1
            for trade in self.history
            if trade["net_pnl"] < 0
        )

        total_fees = sum(
            trade["fees"]
            for trade in self.history
        )

        win_rate = (

            wins
            / total_trades
            * 100

            if total_trades > 0

            else 0.0
        )

        return {

            "starting_balance":
                self.starting_balance,

            "balance":
                self.balance,

            "total_trades":
                total_trades,

            "wins":
                wins,

            "losses":
                losses,

            "win_rate":
                win_rate,

            "daily_r":
                self.daily_r,

            "daily_trade_count":
                self.daily_trade_count,

            "total_fees":
                total_fees,
        }

    # ========================================================
    # PRINT STATISTICS
    # ========================================================

    def print_statistics(self):

        stats = self.statistics()

        print(
            "\n================ PAPER STATISTICS ================"
        )

        print(
            f"Starting Balance : "
            f"${stats['starting_balance']:.2f}"
        )

        print(
            f"Current Balance  : "
            f"${stats['balance']:.2f}"
        )

        print(
            f"Total Trades     : "
            f"{stats['total_trades']}"
        )

        print(
            f"Wins             : "
            f"{stats['wins']}"
        )

        print(
            f"Losses           : "
            f"{stats['losses']}"
        )

        print(
            f"Win Rate         : "
            f"{stats['win_rate']:.2f}%"
        )

        print(
            f"Daily R          : "
            f"{stats['daily_r']:.3f}"
        )

        print(
            f"Daily Trades     : "
            f"{stats['daily_trade_count']}"
        )

        print(
            f"Total Fees       : "
            f"${stats['total_fees']:.2f}"
        )

        print(
            "=================================================="
        )

