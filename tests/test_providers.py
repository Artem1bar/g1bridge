import asyncio
from datetime import datetime, timedelta

from g1bridge.pipboy import HomeData
from g1bridge.providers import (
    calendar_provider,
    central_hub_provider,
    merge_all,
    refresh_loop,
    weather_provider,
    weather_words,
)

NOW = datetime(2026, 9, 3, 23, 14)


def test_weather_words():
    assert weather_words(17.6, 0) == "18C clear"
    assert weather_words(3.2, 73) == "3C snow"
    assert weather_words(20.0, 42) == "20C code 42"


def test_weather_provider_fills_the_row_and_survives_failures():
    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        return {"current": {"temperature_2m": 18.4, "weather_code": 2}}

    provider = weather_provider(41.9, -87.6, fetch=fetch)
    data = asyncio.run(provider(HomeData()))
    assert data.weather == "18C partly cloudy"
    assert "latitude=41.9" in calls[0] and "longitude=-87.6" in calls[0]

    def broken(url: str) -> dict:
        raise OSError("no network")

    same = asyncio.run(weather_provider(0, 0, fetch=broken)(data))
    assert same == data  # old row kept


def test_calendar_provider_picks_the_next_event():
    def events(start, end):
        return [
            (NOW + timedelta(hours=9, minutes=46), "Standup"),
            (NOW + timedelta(hours=2), "Call Sam"),
            (NOW - timedelta(hours=1), "Already happened"),
        ]

    provider = calendar_provider(events, now=lambda: NOW)
    data = asyncio.run(provider(HomeData()))
    assert data.next_event == "01:14 Call Sam"
    assert data.next_event_at == NOW + timedelta(hours=2)

    empty = asyncio.run(calendar_provider(lambda s, e: [], now=lambda: NOW)(data))
    assert empty.next_event is None and empty.next_event_at is None


def test_merge_all_chains_providers_and_refresh_loop_writes():
    async def a(data):
        return data.with_battery("left", 10)

    async def b(data):
        return data.with_battery("right", 20)

    merged = merge_all([a, b])
    state = {"data": HomeData()}

    async def go():
        task = asyncio.create_task(
            refresh_loop(
                merged, lambda: state["data"], lambda d: state.update(data=d), 0.01
            )
        )
        await asyncio.sleep(0.03)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert (state["data"].battery_left, state["data"].battery_right) == (10, 20)


def test_central_hub_provider_builds_rows_from_the_real_shapes():
    replies = {
        "/api/lsu/overview": {
            "success": True,
            "data": {
                "dueThisWeek": 6,
                "overdueCount": 1,
                "next": {"startsAt": 1788535800000, "courseCode": "CMST 2064"},
            },
        },
        "/api/deadlines?derived=1": {
            "success": True,
            "data": [
                {
                    "title": "HIST 1005: DQ 1 - Stearns and Bentley",
                    "dueAt": 1788557400000,
                },
                {"title": "Old", "dueAt": 1000},
            ],
        },
        "/api/outbound/leads?limit=1": {
            "success": True,
            "data": {
                "total": 33343,
                "facets": {"statuses": [{"value": "new", "count": 33341}]},
            },
        },
        "/api/ops/runs": {
            "success": True,
            "data": [
                {"status": "failed", "startedAt": "2026-09-03T20:00:00.000Z"},
                {"status": "failed", "startedAt": "2026-08-01T20:00:00.000Z"},
                {"status": "ok", "startedAt": "2026-09-03T21:00:00.000Z"},
            ],
        },
    }
    seen: list[str] = []

    def fetch(url: str) -> dict:
        seen.append(url)
        return replies[url.replace("http://hub.test", "")]

    now = datetime(2026, 9, 3, 23, 14)
    provider = central_hub_provider("http://hub.test", fetch=fetch, now=lambda: now)
    data = asyncio.run(provider(HomeData()))
    assert (data.tasks_due, data.overdue, data.leads_new) == (6, 1, 33341)
    assert data.next_event.endswith("CMST 2064") and data.next_event_at > now
    day = datetime.fromtimestamp(1788557400).strftime("%a")
    assert data.deadline.startswith(day) and "DQ 1 - Stearns" in data.deadline
    assert len(data.deadline) <= 22
    assert data.ops_failed == 1  # the August failure is outside 24 h
    assert not any("queue" in url for url in seen)  # never the lead-claiming endpoint


def test_central_hub_provider_keeps_old_rows_when_the_service_is_down():
    def down(url: str) -> dict:
        raise OSError("connection refused")

    before = HomeData(tasks_due=4, leads_new=9)
    after = asyncio.run(central_hub_provider("http://hub.test", fetch=down)(before))
    assert after == before


def test_central_hub_keeps_the_earlier_of_calendar_and_class():
    now = datetime(2026, 9, 3, 23, 14)
    soon = now + timedelta(minutes=16)
    calendar_first = HomeData(next_event="23:30 Call", next_event_at=soon)
    later_ms = int((now + timedelta(hours=2)).timestamp() * 1000)

    def fetch(url: str) -> dict:
        if "lsu" in url:
            return {
                "success": True,
                "data": {"next": {"startsAt": later_ms, "courseCode": "LATN 1001"}},
            }
        raise OSError("skip")

    provider = central_hub_provider("http://hub.test", fetch=fetch, now=lambda: now)
    data = asyncio.run(provider(calendar_first))
    assert data.next_event == "23:30 Call"
