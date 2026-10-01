"""Performance report:
    python report.py                    # 0DTE bot (trades.csv)
    python report.py trades_swing.csv   # 30-45 day bot"""
import sys
from pathlib import Path

import pandas as pd

f = Path(__file__).parent / (sys.argv[1] if len(sys.argv) > 1 else "trades.csv")
if not f.exists():
    raise SystemExit("No trades yet.")
t = pd.read_csv(f, parse_dates=["opened", "closed"])
t["day"] = t["closed"].astype(str).str[:10]


def summary(df, label):
    if df.empty:
        return
    wins, losses = df[df.pnl > 0], df[df.pnl <= 0]
    pf = wins.pnl.sum() / abs(losses.pnl.sum()) if len(losses) and losses.pnl.sum() else float("inf")
    eq = df.pnl.cumsum()
    print(f"\n== {label} ==")
    print(f"trades {len(df)} | win rate {len(wins) / len(df):.0%} | total P&L ${df.pnl.sum():,.2f} | "
          f"profit factor {pf:.2f}")
    print(f"avg win ${wins.pnl.mean() if len(wins) else 0:,.2f} | avg loss ${losses.pnl.mean() if len(losses) else 0:,.2f} | "
          f"worst drawdown ${(eq - eq.cummax()).min():,.2f}")


summary(t, "ALL")
for k, g in t.groupby("kind"):
    summary(g, k)
for k, g in t.groupby("underlying"):
    summary(g, k)
print("\nBy day:")
print(t.groupby("day").pnl.agg(["count", "sum"]).rename(columns={"count": "trades", "sum": "P&L"}).tail(20))
print("\nExit reasons:")
print(t.why_out.str.split("(").str[0].str.strip().value_counts())
