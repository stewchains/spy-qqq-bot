"""News monitor.

- Pulls headlines from Alpaca's news feed (Benzinga) for SPY, QQQ and the mega-caps that move them,
  plus the general market stream.
- Scores market mood from -1 (very bearish) to +1 (very bullish), weighting recent headlines more.
- Flags "shock" headlines (Fed, CPI, jobs, tariffs, war, halts, downgrades...) and pauses new entries.
- Optional: asks Claude to read the headlines for a smarter score (set news.use_claude: true).
"""
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("news")

WATCH = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO"]

POS = {
    "beat": 1, "beats": 1, "surge": 1, "surges": 1, "soar": 1, "soars": 1, "rally": 1, "rallies": 1,
    "record high": 1, "all-time high": 1, "upgrade": 0.8, "upgrades": 0.8, "raises guidance": 1,
    "strong": 0.6, "growth": 0.5, "gain": 0.6, "gains": 0.6, "jumps": 0.8, "rises": 0.5, "rebound": 0.7,
    "cools": 0.6, "easing": 0.6, "rate cut": 0.8, "cuts rates": 0.8, "soft landing": 0.8, "deal": 0.4,
    "ceasefire": 0.8, "stimulus": 0.7, "better-than-expected": 1, "above estimates": 0.9, "bullish": 0.8,
    "optimism": 0.6, "buyback": 0.5, "approval": 0.4, "tops estimates": 0.9, "higher": 0.3,
}
NEG = {
    "miss": 1, "misses": 1, "plunge": 1, "plunges": 1, "tumble": 1, "tumbles": 1, "sell-off": 1,
    "selloff": 1, "crash": 1, "downgrade": 0.8, "downgrades": 0.8, "cuts guidance": 1, "weak": 0.6,
    "slump": 0.8, "falls": 0.5, "drops": 0.6, "slides": 0.6, "recession": 1, "layoffs": 0.6,
    "hot inflation": 1, "inflation accelerates": 1, "rate hike": 0.9, "hawkish": 0.8, "tariff": 0.7,
    "tariffs": 0.7, "sanctions": 0.6, "war": 0.9, "attack": 0.9, "invasion": 1, "default": 1,
    "shutdown": 0.6, "probe": 0.5, "lawsuit": 0.4, "antitrust": 0.5, "worse-than-expected": 1,
    "below estimates": 0.9, "bearish": 0.8, "fears": 0.6, "warning": 0.5, "halted": 0.8, "lower": 0.3,
    "bankruptcy": 1, "volatility spikes": 0.8, "vix jumps": 0.8,
}
SHOCK = [
    r"\bfed\b", r"\bfomc\b", r"powell", r"rate (hike|cut|decision)", r"\bcpi\b", r"\bppi\b",
    r"inflation (data|report)", r"jobs report", r"payrolls", r"unemployment rate", r"tariff",
    r"\bwar\b", r"missile", r"invasion", r"attack", r"sanction", r"circuit breaker", r"trading halt",
    r"halted", r"credit rating", r"downgrade.*(u\.s\.|united states|treasur)", r"default",
    r"emergency", r"bank failure", r"flash crash", r"government shutdown",
]
_shock_re = re.compile("|".join(SHOCK), re.I)


_POS_RE = [(re.compile(r"\b" + re.escape(k) + r"\b", re.I), w) for k, w in POS.items()]
_NEG_RE = [(re.compile(r"\b" + re.escape(k) + r"\b", re.I), w) for k, w in NEG.items()]


def score_text(text: str) -> float:
    s = sum(w for r, w in _POS_RE if r.search(text)) - sum(w for r, w in _NEG_RE if r.search(text))
    return max(-1.0, min(1.0, s / 2))


def is_shock(text: str) -> bool:
    return bool(_shock_re.search(text))


def _extract(news_set):
    """alpaca-py returns a NewsSet; be tolerant of version differences."""
    items = getattr(news_set, "news", None)
    if items is None:
        data = getattr(news_set, "data", news_set)
        items = data.get("news", []) if isinstance(data, dict) else []
    out = []
    for n in items:
        g = (lambda k: n.get(k)) if isinstance(n, dict) else (lambda k: getattr(n, k, None))
        created = g("created_at")
        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        out.append({"id": g("id"), "headline": g("headline") or "", "summary": g("summary") or "",
                    "symbols": g("symbols") or [], "created_at": created, "source": g("source")})
    return out


class NewsMonitor:
    def __init__(self, cfg: dict, api_key: str, secret: str):
        self.cfg = cfg["news"]
        self.enabled = self.cfg.get("enabled", True)
        self.client = None
        if self.enabled:
            from alpaca.data.historical.news import NewsClient
            self.client = NewsClient(api_key, secret)
        self.items: dict = {}
        self.shock_until: datetime | None = None
        self.shock_headline = ""
        self.score = 0.0
        self.claude_summary = ""
        self._last_poll = datetime.min.replace(tzinfo=timezone.utc)
        self._claude_seen: set = set()

    # ------------------------------------------------------------------ fetching
    def poll(self, now: datetime | None = None, force=False):
        if not self.enabled:
            return
        now = now or datetime.now(timezone.utc)
        if not force and (now - self._last_poll).total_seconds() < self.cfg["poll_seconds"]:
            return
        self._last_poll = now
        from alpaca.data.requests import NewsRequest
        start = now - timedelta(minutes=self.cfg["lookback_minutes"])
        fresh = []
        for syms in (",".join(WATCH), None):
            try:
                kw = dict(start=start, limit=50, include_content=False)
                if syms:
                    kw["symbols"] = syms
                fresh += _extract(self.client.get_news(NewsRequest(**kw)))
            except Exception as e:  # noqa: BLE001
                log.warning("news fetch failed: %s", e)
        self.ingest(fresh, now)

    def ingest(self, items, now: datetime):
        for it in items:
            key = it["id"] or it["headline"]
            if key in self.items or not it["created_at"]:
                continue
            text = f'{it["headline"]}. {it["summary"]}'
            it["score"] = score_text(text)
            it["shock"] = is_shock(it["headline"])
            it["weight"] = 1.5 if any(s in ("SPY", "QQQ") for s in it["symbols"]) or not it["symbols"] else 1.0
            self.items[key] = it
            age_min = (now - it["created_at"]).total_seconds() / 60
            if it["shock"] and age_min <= self.cfg["shock_pause_minutes"]:
                until = it["created_at"] + timedelta(minutes=self.cfg["shock_pause_minutes"])
                if not self.shock_until or until > self.shock_until:
                    self.shock_until, self.shock_headline = until, it["headline"]
                    log.warning("NEWS SHOCK -> pausing entries until %s: %s", until.strftime("%H:%M UTC"), it["headline"])
            log.info("news %+.2f %s| %s", it["score"], "SHOCK " if it["shock"] else "", it["headline"][:140])
        cutoff = now - timedelta(minutes=self.cfg["lookback_minutes"])
        self.items = {k: v for k, v in self.items.items() if v["created_at"] >= cutoff}
        self.score = self._aggregate(now)
        if self.cfg.get("use_claude") and os.getenv("ANTHROPIC_API_KEY"):
            self._claude_read(now)

    def _aggregate(self, now) -> float:
        num = den = 0.0
        for it in self.items.values():
            age = (now - it["created_at"]).total_seconds() / 60
            w = it["weight"] * math.exp(-age / 45)
            num += w * it["score"]
            den += w
        return round(num / den, 3) if den else 0.0

    # ------------------------------------------------------------------ Claude (optional)
    def _claude_read(self, now):
        keys = set(self.items)
        if not keys or keys <= self._claude_seen:
            return
        self._claude_seen = keys
        heads = sorted(self.items.values(), key=lambda x: x["created_at"], reverse=True)[:40]
        lines = "\n".join(f'- [{h["created_at"].strftime("%H:%M")} UTC] {h["headline"]}' for h in heads)
        prompt = (
            "You are a cautious market news analyst. Given these recent headlines, rate the likely "
            "effect on SPY and QQQ over the next 1-2 hours. Reply with ONLY JSON: "
            '{"score": <-1.0 to 1.0>, "shock": <true if a surprise high-impact event just hit>, '
            '"summary": "<one sentence>"}\n\nHeadlines:\n' + lines)
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages", timeout=30,
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": self.cfg.get("claude_model"), "max_tokens": 200,
                      "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            text = r.json()["content"][0]["text"]
            data = json.loads(text[text.find("{"): text.rfind("}") + 1])
            llm = max(-1.0, min(1.0, float(data["score"])))
            self.score = round(0.7 * llm + 0.3 * self.score, 3)
            self.claude_summary = data.get("summary", "")
            if data.get("shock"):
                self.shock_until = now + timedelta(minutes=self.cfg["shock_pause_minutes"])
                self.shock_headline = "Claude flagged: " + self.claude_summary
            log.info("Claude news read %+.2f: %s", llm, self.claude_summary)
        except Exception as e:  # noqa: BLE001
            log.warning("Claude news read failed (using keyword score): %s", e)

    # ------------------------------------------------------------------ queries
    def paused(self, now: datetime) -> bool:
        return bool(self.shock_until and now < self.shock_until)
