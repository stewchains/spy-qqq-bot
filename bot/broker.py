"""Thin wrapper around alpaca-py so the rest of the bot doesn't care about SDK details."""
import logging
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import (OptionLatestQuoteRequest, OptionSnapshotRequest, StockBarsRequest,
                                  StockLatestTradeRequest)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (AssetStatus, ContractType, OrderClass, OrderSide, QueryOrderStatus,
                                  TimeInForce)
from alpaca.trading.requests import (GetOptionContractsRequest, GetOrdersRequest, LimitOrderRequest,
                                     MarketOrderRequest, StopLossRequest, TakeProfitRequest)

log = logging.getLogger("broker")


class Broker:
    def __init__(self, key: str, secret: str, cfg: dict):
        self.cfg = cfg
        self.paper = cfg["mode"]["paper"]
        self.dry = cfg["mode"].get("dry_run", False)
        self.trading = TradingClient(key, secret, paper=self.paper)
        self.stocks = StockHistoricalDataClient(key, secret)
        self.options = OptionHistoricalDataClient(key, secret)
        self.stock_feed = DataFeed.SIP if cfg["data"]["stock_feed"].lower() == "sip" else DataFeed.IEX
        self.opt_feed = OptionsFeed.OPRA if cfg["data"]["options_feed"].lower() == "opra" else OptionsFeed.INDICATIVE

    # ------------------------------------------------------------------ account / clock
    def account(self):
        a = self.trading.get_account()
        return {"equity": float(a.equity), "buying_power": float(a.buying_power),
                "last_equity": float(a.last_equity) if getattr(a, "last_equity", None) else None,
                "options_level": getattr(a, "options_trading_level", None),
                "blocked": a.trading_blocked, "pdt": a.pattern_day_trader}

    def clock(self):
        return self.trading.get_clock()

    # ------------------------------------------------------------------ market data
    def bars(self, symbol: str, minutes: int, days: int = 5) -> pd.DataFrame:
        tf = TimeFrame(minutes, TimeFrameUnit.Minute) if minutes < 1440 else TimeFrame.Day
        start = datetime.now(timezone.utc) - timedelta(days=days)
        df = self.stocks.get_stock_bars(StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start,
                                                         feed=self.stock_feed)).df
        if df.empty:
            return df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        if minutes < 1440:
            df = df.tz_convert("America/New_York").between_time("09:30", "15:59")
            # drop the candle that's still forming
            if len(df) and df.index[-1] + timedelta(minutes=minutes) > datetime.now(timezone.utc):
                df = df.iloc[:-1]
        return df

    def daily(self, symbol: str, days: int = 200) -> pd.DataFrame:
        return self.bars(symbol, 1440, days)

    def last_price(self, symbol: str) -> float:
        r = self.stocks.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self.stock_feed))
        return float(r[symbol].price)

    def option_candidates(self, underlying: str, direction: str, spot: float) -> list[dict]:
        o = self.cfg["options"]
        today = datetime.now(ZoneInfo("America/New_York")).date()
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying], status=AssetStatus.ACTIVE,
            type=ContractType.CALL if direction == "long" else ContractType.PUT,
            expiration_date_gte=today + timedelta(days=o["min_dte"]),
            expiration_date_lte=today + timedelta(days=o["max_dte"] + 3),  # +3 covers weekends
            strike_price_gte=str(round(spot * 0.97, 2)), strike_price_lte=str(round(spot * 1.03, 2)),
            limit=1000)
        contracts = self.trading.get_option_contracts(req).option_contracts or []
        base = {}
        for c in contracts:
            exp = c.expiration_date if isinstance(c.expiration_date, date) else date.fromisoformat(str(c.expiration_date))
            base[c.symbol] = {"symbol": c.symbol, "type": "call" if "call" in str(c.type).lower() else "put",
                              "strike": float(c.strike_price), "expiration": exp, "dte": (exp - today).days,
                              "open_interest": float(c.open_interest) if c.open_interest else None}
        syms = list(base)
        for i in range(0, len(syms), 100):
            snaps = self.options.get_option_snapshot(OptionSnapshotRequest(symbol_or_symbols=syms[i:i + 100],
                                                                           feed=self.opt_feed))
            for s, snap in snaps.items():
                q = snap.latest_quote
                base[s].update(bid=float(q.bid_price) if q else 0.0, ask=float(q.ask_price) if q else 0.0,
                               delta=float(snap.greeks.delta) if snap.greeks and snap.greeks.delta is not None else None,
                               iv=snap.implied_volatility)
        return list(base.values())

    def option_quote(self, symbol: str):
        q = self.options.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=symbol, feed=self.opt_feed))[symbol]
        return float(q.bid_price), float(q.ask_price)

    # ------------------------------------------------------------------ orders
    def _wait_fill(self, order_id, seconds: int):
        end = time.time() + seconds
        o = self.trading.get_order_by_id(order_id)
        while time.time() < end and str(o.status).split(".")[-1].lower() not in ("filled", "canceled", "rejected", "expired"):
            time.sleep(2)
            o = self.trading.get_order_by_id(order_id)
        return o

    def buy_option(self, symbol: str, qty: int, retries: int):
        """Limit order at the mid, re-priced toward the ask if it doesn't fill. Returns (filled_qty, avg_price)."""
        if self.dry:
            bid, ask = self.option_quote(symbol)
            log.info("[DRY RUN] would buy %s x%d @ %.2f", symbol, qty, (bid + ask) / 2)
            return 0, 0.0
        filled, cost = 0, 0.0
        for attempt in range(retries + 1):
            bid, ask = self.option_quote(symbol)
            if ask <= 0:
                break
            mid = (bid + ask) / 2
            px = round(min(ask, mid + (ask - mid) * attempt / max(retries, 1)), 2)
            o = self.trading.submit_order(LimitOrderRequest(symbol=symbol, qty=qty - filled, side=OrderSide.BUY,
                                                            time_in_force=TimeInForce.DAY, limit_price=px))
            o = self._wait_fill(o.id, 15)
            fq = int(float(o.filled_qty or 0))
            if fq:
                cost += fq * float(o.filled_avg_price)
                filled += fq
            if filled >= qty:
                break
            try:
                self.trading.cancel_order_by_id(o.id)
                time.sleep(1)
                o2 = self.trading.get_order_by_id(o.id)  # catch a fill that raced the cancel
                extra = int(float(o2.filled_qty or 0)) - fq
                if extra > 0:
                    cost += extra * float(o2.filled_avg_price); filled += extra
            except APIError:
                pass
        return filled, (cost / filled if filled else 0.0)

    def sell_option(self, symbol: str, qty: int):
        """Exit: limit at mid, then at bid, then market. Returns avg fill price."""
        if self.dry:
            return 0.0
        filled, proceeds = 0, 0.0
        for step in ("mid", "bid", "market"):
            remaining = qty - filled
            if remaining <= 0:
                break
            if step == "market":
                o = self.trading.submit_order(MarketOrderRequest(symbol=symbol, qty=remaining, side=OrderSide.SELL,
                                                                 time_in_force=TimeInForce.DAY))
                o = self._wait_fill(o.id, 20)
            else:
                bid, ask = self.option_quote(symbol)
                px = round((bid + ask) / 2 if step == "mid" else bid, 2)
                if px <= 0:
                    continue
                o = self.trading.submit_order(LimitOrderRequest(symbol=symbol, qty=remaining, side=OrderSide.SELL,
                                                                time_in_force=TimeInForce.DAY, limit_price=px))
                o = self._wait_fill(o.id, 10)
                if str(o.status).split(".")[-1].lower() != "filled":
                    try:
                        self.trading.cancel_order_by_id(o.id); time.sleep(1)
                        o = self.trading.get_order_by_id(o.id)
                    except APIError:
                        pass
            fq = int(float(o.filled_qty or 0))
            if fq:
                proceeds += fq * float(o.filled_avg_price); filled += fq
        return proceeds / filled if filled else 0.0

    def bracket_shares(self, symbol: str, qty: int, direction: str, stop: float, target: float):
        if self.dry:
            log.info("[DRY RUN] would %s %d %s stop %.2f target %.2f", direction, qty, symbol, stop, target)
            return None
        o = self.trading.submit_order(MarketOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY if direction == "long" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY, order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(target, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop, 2))))
        o = self._wait_fill(o.id, 20)
        return float(o.filled_avg_price) if o.filled_avg_price else None

    def close_shares(self, symbol: str):
        """Cancel the bracket legs, then flatten. Returns avg exit price if known."""
        if self.dry:
            return 0.0
        for o in self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])):
            try:
                self.trading.cancel_order_by_id(o.id)
            except APIError:
                pass
        time.sleep(1)
        try:
            o = self.trading.close_position(symbol)
            o = self._wait_fill(o.id, 20)
            return float(o.filled_avg_price) if o.filled_avg_price else 0.0
        except APIError as e:
            log.info("close %s: %s", symbol, e)
            return self.last_exit_price(symbol)

    def last_exit_price(self, symbol: str) -> float:
        """Most recent filled order price for a symbol (used when the broker closed it, e.g. a bracket stop)."""
        orders = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[symbol],
                                                          limit=10, nested=True))
        fills = [o for o in orders if o.filled_at and o.filled_avg_price]
        for o in list(orders):
            fills += [l for l in (getattr(o, "legs", None) or []) if l.filled_at and l.filled_avg_price]
        if not fills:
            return 0.0
        return float(max(fills, key=lambda o: o.filled_at).filled_avg_price)

    def positions(self) -> dict:
        return {p.symbol: p for p in self.trading.get_all_positions()}
