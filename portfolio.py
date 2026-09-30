"""Portfolio snapshots derived only from the active session's paper accounts."""


def portfolio_snapshot(accounts):
    account_list = list(accounts)
    balance = sum(float(account.get("balance", 0.0)) for account in account_list)
    starting = sum(float(account.get("starting_capital", 0.0)) for account in account_list)
    unrealized = 0.0
    positions = 0
    trades = []
    for account in account_list:
        position = account.get("position")
        mark = account.get("last_mark")
        if position is not None and mark is not None:
            direction = 1 if position["side"] == "LONG" else -1
            unrealized += (float(mark) - float(position["entry"])) * float(position["quantity"]) * direction
            positions += 1
        trades.extend(account.get("trades", []))
    equity = balance + unrealized
    return {
        "starting_capital": starting,
        "balance": balance,
        "realized_pnl": balance - starting,
        "unrealized_pnl": unrealized,
        "equity": equity,
        "positions": positions,
        "closed_trades": len(trades),
        "win_rate_pct": 100.0 * sum(float(t.get("net_pnl", 0.0)) > 0 for t in trades) / len(trades) if trades else None,
        "trade_journal": trades,
    }
