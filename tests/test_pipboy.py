from datetime import datetime, timedelta

from g1bridge.pipboy import HomeData, countdown, gauge, render_pipboy

NOW = datetime(2026, 9, 3, 23, 14)


def test_gauge_scales_and_marks_unknown():
    assert gauge(0) == "[......]"
    assert gauge(50) == "[###...]"
    assert gauge(100) == "[######]"
    assert gauge(250) == "[######]"  # clamped
    assert gauge(None) == "[??????]"


def test_countdown_wording():
    assert countdown(NOW, NOW + timedelta(hours=9, minutes=40)) == "in 9h40"
    assert countdown(NOW, NOW + timedelta(minutes=25)) == "in 25m"
    assert countdown(NOW, NOW + timedelta(seconds=30)) == "now"
    assert countdown(NOW, NOW - timedelta(minutes=10)) == "ago"


def test_render_fits_five_by_forty_and_reads_like_a_pipboy():
    data = HomeData(
        battery_left=58,
        battery_right=46,
        next_event="09:00 Standup",
        next_event_at=NOW + timedelta(hours=9, minutes=46),
        weather="18C clear",
        tasks_due=3,
    )
    text = render_pipboy(data, NOW, agents=5)
    lines = text.split("\n")
    assert len(lines) == 5 and all(len(line) <= 40 for line in lines)
    assert lines[0].startswith("CLAUDE-TEC") and lines[0].endswith("THU 03 SEP  23:14")
    assert lines[1] == "BATT L[###...]58%  R[###...]46%"
    assert lines[2] == "NEXT  09:00 Standup (in 9h46)"
    assert lines[3] == "DUE 3/wk  WX 18C clear"
    assert lines[4] == "[HOLD L] ASK   [TAP] 5 APPS   [2x] EXIT"


def test_render_with_nothing_known_still_fills_the_screen():
    text = render_pipboy(HomeData(), NOW)
    lines = text.split("\n")
    assert lines[1] == "BATT L[??????]--%  R[??????]--%"
    assert lines[2] == "NEXT  nothing scheduled"
    assert lines[3] == "AGENT ASK"
    assert lines[4].startswith("[HOLD L] ASK   [TAP] APPS")


def test_extra_rows_never_push_the_hints_off_screen():
    data = HomeData(extra_rows=("LEADS 4 new", "PIPE $12k", "MAIL 7 unread"))
    lines = render_pipboy(data, NOW).split("\n")
    assert len(lines) == 5
    assert lines[-1].startswith("[HOLD L] ASK")
    assert "LEADS 4 new" in lines[3]


def test_with_battery_is_immutable():
    empty = HomeData()
    left = empty.with_battery("left", 33)
    assert empty.battery_left is None and left.battery_left == 33
    assert left.with_battery("right", 36).battery_right == 36


def test_status_strip_orders_by_urgency_and_fits():
    from g1bridge.pipboy import status_strip

    data = HomeData(
        overdue=1,
        deadline="Fri DQ 1 - Stearns",
        ops_failed=2,
        leads_new=33341,
        weather="18C clear",
    )
    strip = status_strip(data, 40)
    assert strip.startswith("OVERDUE 1  DUE Fri DQ 1 - Stearns")
    assert len(strip) <= 40
    strip2 = status_strip(HomeData(weather="18C clear", tasks_due=6), 40)
    assert strip2 == "DUE 6/wk  WX 18C clear"
    assert status_strip(HomeData(), 40) == ""
