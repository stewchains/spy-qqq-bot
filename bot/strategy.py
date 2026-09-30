"""Signal engine: turns candles (+ news mood) into long / short / no-trade decisions.

Logic in plain English
----------------------
1. Daily trend (big picture): price vs its 20- and 50-day averages -> bull / bear / neutral.
2. On 5-minute candles, a LONG needs a *fresh trigger* in the last 3 candles
   (9 EMA crossed above 21 EMA, or price reclaimed VWAP), then scores 1 point each for:
     price above VWAP | 9 EMA above 21 EMA | MACD histogram positive and rising |
     RSI in the healthy band | ADX shows a real trend | daily trend agrees | volume above average
   SHORT (buy puts / short shares) is the mirror image.
3. Enter only if score >= min_score, the move isn't already stretched (> 1.5 ATR from the 21 EMA),
   and the news mood isn't strongly against the trade.
4. Stop = entry -/+ 1.5 x ATR. Target = 2 x that distance.
"""
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .indicators import add_all, ema

ET = "America/New_York"


@dataclass
class Signal:
    symbol: str
    direction: Optional[str]          # "long", "short", or None
    score: int = 0
    reasons: list = field(default_factory=list)
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    atr: float = 0.0

    def __str__(self):
        if not self.direction:
            return f"{self.symbol}: no trade ({'; '.join(self.reasons) or 'no setup'})"
        return (f"{self.symbol}: {self.direction.upper()} score={self.score} entry={self.entry:.2f} "
                f"stop={self.stop:.2f} target={self.target:.2f} | {', '.join(self.reasons)}")


def daily_trend(daily: pd.DataFrame) -> str:
    if daily is None or len(daily) < 50:
        return "neutral"
    c = daily["close"]
    e20, e50 = ema(c, 20).iloc[-1], ema(c, 50).iloc[-1]
    last = c.iloc[-1]
    if last > e20 > e50:
        return "bull"
    if last < e20 < e50:
        return "bear"
    return "neutral"


def _fresh_trigger(df: pd.DataFrame, side: str, lookback: int = 3) -> Optional[str]:
    w = df.iloc[-(lookback + 1):]
    diff_ema = (w["ema9"] - w["ema21"]).values
    diff_vwap = (w["close"] - w["vwap"]).values
    for i in range(1, len(w)):
        if side == "long":
            if diff_ema[i - 1] <= 0 < diff_ema[i]:
                return "9/21 EMA bull cross"
            if diff_vwap[i - 1] <= 0 < diff_vwap[i]:
                return "VWAP reclaim"
        else:
            if diff_ema[i - 1] >= 0 > diff_ema[i]:
                return "9/21 EMA bear cross"
            if diff_vwap[i - 1] >= 0 > diff_vwap[i]:
                return "VWAP loss"
    return None


def evaluate(symbol: str, intraday: pd.DataFrame, daily: pd.DataFrame, cfg: dict,
             news_score: float = 0.0, prepared: bool = False) -> Signal:
    s = cfg["strategy"]
    if intraday is None or len(intraday) < 30:
        return Signal(symbol, None, reasons=["not enough candles yet"])
    df = intraday if prepared else add_all(intraday)
    bar, prev = df.iloc[-1], df.iloc[-2]
    trend = daily_trend(daily)
    block = cfg.get("news", {}).get("block_score", -0.5)

    best = Signal(symbol, None, reasons=["no fresh trigger"])
    cands = []
    for side in ("long", "short"):
        trig = _fresh_trigger(df, side)
        if not trig:
            continue
        if s.get("require_daily_trend", True):
            if side == "long" and trend == "bear":
                continue
            if side == "short" and trend == "bull":
                continue
        sign = 1 if side == "long" else -1
        reasons, score = [trig], 0
        if sign * (bar.close - bar.vwap) > 0:
            score += 1; reasons.append("VWAP side")
        if sign * (bar.ema9 - bar.ema21) > 0:
            score += 1; reasons.append("EMA stack")
        if sign * bar.macd_hist > 0 and sign * (bar.macd_hist - prev.macd_hist) > 0:
            score += 1; reasons.append("MACD momentum")
        lo, hi = s["rsi_long_range"] if side == "long" else s["rsi_short_range"]
        if lo <= bar.rsi <= hi:
            score += 1; reasons.append(f"RSI {bar.rsi:.0f}")
        if bar.adx >= s["min_adx"]:
            score += 1; reasons.append(f"ADX {bar.adx:.0f}")
        if (side == "long" and trend == "bull") or (side == "short" and trend == "bear"):
            score += 1; reasons.append(f"daily {trend}")
        if bar.vol_avg and bar.volume > bar.vol_avg:
            score += 1; reasons.append("volume")

        stretched = abs(bar.close - bar.ema21) > 1.5 * bar.atr
        news_against = (side == "long" and news_score < block) or (side == "short" and news_score > -block)

        if score < s["min_score"]:
            cand = Signal(symbol, None, score, [f"{side} score {score} < {s['min_score']}"])
        elif stretched:
            cand = Signal(symbol, None, score, [f"{side} setup but price stretched from 21 EMA"])
        elif news_against:
            cand = Signal(symbol, None, score, [f"{side} blocked by news mood {news_score:+.2f}"])
        else:
            risk = s["atr_stop_mult"] * bar.atr
            stop = bar.close - sign * risk
            target = bar.close + sign * risk * s["reward_risk"]
            cand = Signal(symbol, side, score, reasons, float(bar.close), float(stop),
                          float(target), float(bar.atr))
        cands.append(cand)
    live = [c for c in cands if c.direction]
    if live:
        return max(live, key=lambda c: c.score)
    return max(cands, key=lambda c: c.score) if cands else best


def daily_with_live(daily: pd.DataFrame, price: float, today) -> pd.DataFrame:
    """Daily candles through yesterday + today's live price as the latest close."""
    if daily is None or daily.empty:
        return daily
    idx = daily.index.tz_convert(ET) if daily.index.tz is not None else daily.index
    d = daily[idx.date < today]
    row = pd.DataFrame({"open": [price], "high": [price], "low": [price], "close": [price], "volume": [0.0]},
                       index=[d.index[-1] + pd.Timedelta(days=1)] if len(d) else [pd.Timestamp.now(tz="UTC")])
    return pd.concat([d, row])
