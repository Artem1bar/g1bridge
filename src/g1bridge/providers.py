"""Data feeds for the Pip-Boy home screen.

Each provider is an async callable `HomeData -> HomeData` that returns a copy
with its rows filled in (or unchanged when its source is unavailable). The
refresh loop runs them every few minutes and pushes the merged result into
the session. Sources so far: glasses battery (from BLE events, in the
session), weather (Open-Meteo, keyless), calendar (macOS EventKit).
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Iterable

from .pipboy import HomeData

logger = logging.getLogger(__name__)

Provider = Callable[[HomeData], Awaitable[HomeData]]

REFRESH_INTERVAL_S = 300.0
HTTP_TIMEOUT_S = 8.0
OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,weather_code&timezone=auto"
)
# WMO weather interpretation codes, shortened for a 40-column line.
WMO_WORDS = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    61: "rain",
    63: "rain",
    65: "heavy rain",
    71: "snow",
    73: "snow",
    75: "heavy snow",
    80: "showers",
    81: "showers",
    82: "heavy showers",
    95: "thunder",
    96: "hail",
    99: "hail",
}


def weather_words(temperature_c: float, code: int) -> str:
    return f"{round(temperature_c)}C {WMO_WORDS.get(code, 'code ' + str(code))}"


def fetch_json(url: str, timeout: float = HTTP_TIMEOUT_S) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.load(response)


def weather_provider(
    lat: float, lon: float, fetch: Callable[[str], dict] = fetch_json
) -> Provider:
    """Current temperature and sky from Open-Meteo; no key, no account."""

    async def refresh(data: HomeData) -> HomeData:
        url = OPEN_METEO_URL.format(lat=lat, lon=lon)
        try:
            payload = await asyncio.get_running_loop().run_in_executor(None, fetch, url)
            current = payload["current"]
            words = weather_words(
                current["temperature_2m"], int(current["weather_code"])
            )
        except Exception as exc:  # network down, schema change: keep the old row
            logger.warning("weather unavailable: %s", exc)
            return data
        return replace(data, weather=words)

    return refresh


def calendar_provider(
    events: Callable[[datetime, datetime], Iterable[tuple[datetime, str]]],
    horizon: timedelta = timedelta(hours=18),
    now: Callable[[], datetime] = datetime.now,
) -> Provider:
    """Next event from any (start, title) source, e.g. `eventkit_events`."""

    async def refresh(data: HomeData) -> HomeData:
        start = now()
        try:
            upcoming = sorted(
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: list(events(start, start + horizon))
                )
            )
        except Exception as exc:
            logger.warning("calendar unavailable: %s", exc)
            return data
        upcoming = [(when, title) for when, title in upcoming if when >= start]
        if not upcoming:
            return replace(data, next_event=None, next_event_at=None)
        when, title = upcoming[0]
        return replace(data, next_event=f"{when:%H:%M} {title}", next_event_at=when)

    return refresh


def eventkit_events(start: datetime, end: datetime) -> list[tuple[datetime, str]]:
    """macOS Calendar via EventKit. Asks for permission on first use (a dialog
    in the terminal's app context); returns [] if denied or unavailable."""
    try:
        import EventKit  # pyobjc-framework-EventKit
        from Foundation import NSDate
    except ImportError:
        logger.info("EventKit not available; no calendar row")
        return []
    store = EventKit.EKEventStore.alloc().init()
    granted = {"ok": False}

    def handler(ok, _error):
        granted["ok"] = bool(ok)

    # Full access request (macOS 14+); falls back to the older API name.
    request = getattr(store, "requestFullAccessToEventsWithCompletion_", None)
    if request is None:
        request = lambda h: store.requestAccessToEntityType_completion_(0, h)  # noqa: E731
    import threading

    finished = threading.Event()

    def sync_handler(ok, error):
        handler(ok, error)
        finished.set()

    request(sync_handler)
    finished.wait(30)
    if not granted["ok"]:
        logger.info("calendar access not granted; no calendar row")
        return []
    to_ns = lambda d: NSDate.dateWithTimeIntervalSince1970_(d.timestamp())  # noqa: E731
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
        to_ns(start), to_ns(end), None
    )
    found = []
    for event in store.eventsMatchingPredicate_(predicate) or []:
        if event.isAllDay():
            continue
        when = datetime.fromtimestamp(event.startDate().timeIntervalSince1970())
        found.append((when, str(event.title() or "(untitled)")))
    return found


HUB_URL = "http://127.0.0.1:3100"
# Read-only endpoints only. Never /api/outbound/queue: reading it claims a lead.
HUB_LSU = "/api/lsu/overview"
HUB_DEADLINES = "/api/deadlines?derived=1"
HUB_LEADS = "/api/outbound/leads?limit=1"
HUB_RUNS = "/api/ops/runs"


def central_hub_provider(
    base_url: str = HUB_URL,
    fetch: Callable[[str], dict] = fetch_json,
    now: Callable[[], datetime] = datetime.now,
) -> Provider:
    """Rows from the wearer's own central.hub (Business Command Hub) service.

    Next class (LSU timetable), coursework due this week and overdue, the next
    hard deadline, never-called outbound leads, failed agent runs in 24 h.
    Every call is optional: a missing piece leaves the previous value alone.
    """

    async def get(path: str) -> dict | list | None:
        try:
            payload = await asyncio.get_running_loop().run_in_executor(
                None, fetch, base_url + path
            )
        except Exception as exc:
            logger.warning("central.hub %s unavailable: %s", path, exc)
            return None
        if not isinstance(payload, dict) or not payload.get("success"):
            logger.warning("central.hub %s: unexpected reply", path)
            return None
        return payload.get("data")

    async def refresh(data: HomeData) -> HomeData:
        moment = now()
        lsu = await get(HUB_LSU)
        if isinstance(lsu, dict):
            data = replace(
                data,
                tasks_due=_int(lsu.get("dueThisWeek")),
                overdue=_int(lsu.get("overdueCount")),
            )
            nxt = lsu.get("next") or {}
            starts = _epoch_ms(nxt.get("startsAt"))
            code = nxt.get("courseCode")
            if starts and code and starts >= moment:
                if data.next_event_at is None or starts < data.next_event_at:
                    data = replace(
                        data, next_event=f"{starts:%H:%M} {code}", next_event_at=starts
                    )
        deadlines = await get(HUB_DEADLINES)
        if isinstance(deadlines, list):
            dated = [(_epoch_ms(d.get("dueAt")), d) for d in deadlines]
            future = sorted(
                ((due, d) for due, d in dated if due and due >= moment),
                key=lambda pair: pair[0],
            )
            if future:
                due, first = future[0]
                title = str(first.get("title") or "")
                data = replace(data, deadline=f"{due:%a} {_squeeze(title, 18)}")
        leads = await get(HUB_LEADS)
        if isinstance(leads, dict):
            statuses = {
                s.get("value"): _int(s.get("count"))
                for s in (leads.get("facets") or {}).get("statuses") or []
            }
            if "new" in statuses:
                data = replace(data, leads_new=statuses["new"])
        runs = await get(HUB_RUNS)
        if isinstance(runs, list):
            since = moment - timedelta(hours=24)
            failed = sum(
                1
                for r in runs
                if r.get("status") == "failed"
                and (_iso(r.get("startedAt")) or since) >= since
            )
            data = replace(data, ops_failed=failed)
        return data

    return refresh


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _epoch_ms(value) -> datetime | None:
    number = _int(value)
    return datetime.fromtimestamp(number / 1000) if number else None


def _iso(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None)


def _squeeze(text: str, width: int) -> str:
    """Drop a 'COURSE 1005: ' prefix and trailing words until it fits."""
    if ":" in text:
        text = text.split(":", 1)[1].strip() or text
    return text if len(text) <= width else text[: width - 1].rstrip() + "~"


def merge_all(providers: Iterable[Provider]) -> Provider:
    """Run providers in order, each seeing the previous one's result."""

    async def refresh(data: HomeData) -> HomeData:
        for provider in providers:
            data = await provider(data)
        return data

    return refresh


async def refresh_loop(
    provider: Provider,
    read: Callable[[], HomeData],
    write: Callable[[HomeData], None],
    interval_s: float = REFRESH_INTERVAL_S,
) -> None:
    """Refresh immediately, then every `interval_s`; never raises."""
    while True:
        try:
            write(await provider(read()))
        except Exception:
            logger.exception("home refresh failed")
        await asyncio.sleep(interval_s)
