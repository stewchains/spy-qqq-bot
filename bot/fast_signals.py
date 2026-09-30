"""Vectorized version of strategy.evaluate() for backtests and the strategy search.

Computes the signal for every candle at once (thousands of times faster than calling evaluate()
bar by bar). tests/test_logic.py checks it produces the SAME trades as the live signal code.
"""
import numpy as np
import pandas as pd

from .strategy import daily_dates

ET = "America/New_York"


def daily_trend_per_bar(df: pd.DataFrame, daily: pd.DataFrame) -> np.ndarray:
    """+1 bull / -1 bear / 0 neutral for each intraday candle, using daily closes through yesterday
    plus the candle's close as today's live price (same as strategy.daily_with_live + daily_trend)."""
    out = np.zeros(len(df), dtype=int)
    if daily is None or daily.empty:
        return out
    ddates = daily_dates(daily)
    closes = daily["close"].to_numpy(float)
    e20 = pd.Series(closes).ewm(span=20, adjust=False).mean().to_numpy()
    e50 = pd.Series(closes).ewm(span=50, adjust=False).mean().to_numpy()
    a20, a50 = 2 / 21, 2 / 51
    bar_dates = np.array(df.index.tz_convert(ET).date)
    price = df["close"].to_numpy(float)
    for d in np.unique(bar_dates):
        n_prior = int(np.searchsorted(ddates, d, side="left"))  # daily rows strictly before d
        if n_prior + 1 < 50:
            continue
        m = bar_dates == d
        p = price[m]
        l20 = a20 * p + (1 - a20) * e20[n_prior - 1]
        l50 = a50 * p + (1 - a50) * e50[n_prior - 1]
        out[m] = np.where((p > l20) & (l20 > l50), 1, np.where((p < l20) & (l20 < l50), -1, 0))
    return out


def compute(df: pd.DataFrame, daily: pd.DataFrame, cfg: dict, news_score: float = 0.0, trend=None):
    """df must already have indicators (indicators.add_all). Returns (direction, score, stop_dist):
    direction +1 long / -1 short / 0 none, per candle."""
    s = cfg["strategy"]
    n = len(df)
    c, vwap = df["close"].to_numpy(), df["vwap"].to_numpy()
    e9, e21 = df["ema9"].to_numpy(), df["ema21"].to_numpy()
    mh = df["macd_hist"].to_numpy()
    rsi, adx, atr = df["rsi"].to_numpy(), df["adx"].to_numpy(), df["atr"].to_numpy()
    vol, vavg = df["volume"].to_numpy(), df["vol_avg"].to_numpy()
    if trend is None:
        trend = daily_trend_per_bar(df, daily)
    block = cfg.get("news", {}).get("block_score", -0.5)

    de, dv = e9 - e21, c - vwap
    prev_de, prev_dv = np.r_[np.nan, de[:-1]], np.r_[np.nan, dv[:-1]]
    prev_mh = np.r_[np.nan, mh[:-1]]

    def fresh(up: bool):
        if up:
            x = ((prev_de <= 0) & (de > 0)) | ((prev_dv <= 0) & (dv > 0))
        else:
            x = ((prev_de >= 0) & (de < 0)) | ((prev_dv >= 0) & (dv < 0))
        x = x.astype(int)
        # a cross on this candle or either of the 2 before it (evaluate() looks at the last 3 transitions)
        return (x + np.r_[0, x[:-1]] + np.r_[0, 0, x[:-2]]) > 0

    stretched = np.abs(c - e21) > 1.5 * atr
    vol_ok = np.nan_to_num(vavg) > 0
    res = {}
    for side, sign in (("long", 1), ("short", -1)):
        lo, hi = s["rsi_long_range"] if side == "long" else s["rsi_short_range"]
        score = ((sign * dv > 0).astype(int) + (sign * de > 0) +
                 ((sign * mh > 0) & (sign * (mh - prev_mh) > 0)) +
                 ((rsi >= lo) & (rsi <= hi)) + (adx >= s["min_adx"]) + (trend == sign) +
                 (vol_ok & (vol > np.nan_to_num(vavg))))
        ok = fresh(side == "long") & (score >= s["min_score"]) & ~stretched
        if s.get("require_daily_trend", True):
            ok &= trend != -sign
        if side == "short" and not s.get("allow_shorts", True):
            ok &= False
        if (side == "long" and news_score < block) or (side == "short" and news_score > -block):
            ok &= False
        res[side] = (ok, score)
    lok, lsc = res["long"]
    sok, ssc = res["short"]
    direction = np.where(lok & (~sok | (lsc >= ssc)), 1, np.where(sok, -1, 0))
    direction[:30] = 0  # evaluate() needs 30 candles of history
    score = np.where(direction == 1, lsc, np.where(direction == -1, ssc, 0))
    return direction, score, s["atr_stop_mult"] * atr
