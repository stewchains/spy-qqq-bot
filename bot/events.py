"""Economic-calendar blackout: no new trades around CPI, jobs reports, Fed decisions, etc."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ET = ZoneInfo("America/New_York")


class EventCalendar:
    def __init__(self, path: str, before: int, after: int):
        self.before, self.after = before, after
        self.events = []
        p = Path(path)
        if p.exists():
            for e in yaml.safe_load(p.read_text()) or []:
                d = e["date"] if not isinstance(e["date"], str) else datetime.fromisoformat(e["date"]).date()
                hh, mm = map(int, str(e.get("time", "08:30")).split(":"))
                when = datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)
                self.events.append((when, e.get("name", "event"),
                                    e.get("minutes_before", before), e.get("minutes_after", after)))

    def blocking(self, now: datetime):
        """Return the event name if `now` is inside an event's no-trade window, else None."""
        for when, name, b, a in self.events:
            if when - timedelta(minutes=b) <= now <= when + timedelta(minutes=a):
                return name
        return None

    def today(self, now: datetime):
        d = now.astimezone(ET).date()
        return [f"{w.strftime('%H:%M ET')} {n}" for w, n, _, _ in self.events if w.date() == d]
