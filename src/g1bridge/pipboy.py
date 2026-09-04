"""Fallout-flavoured home screen for a 5 x 40 monochrome green display.

The G1's lens is literally a green phosphor look, so the Pip-Boy idiom fits:
uppercase labels, brackets, bar gauges, one status line per row. Pure
rendering over an immutable `HomeData`; data providers live elsewhere.

    CLAUDE-TEC        THU 03 SEP  23:14
    BATT L[####..]58%  R[###...]46%
    NEXT  09:00 Standup (in 9h40)
    WX    18C clear     TASKS 3 due
    [HOLD L] ASK   [TAP] APPS   [2x] EXIT
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from .paginate import DEFAULT_CHARS_PER_LINE, DEFAULT_LINES_PER_PAGE

TITLE = "CLAUDE-TEC"
GAUGE_CELLS = 6
FILLED = "#"
EMPTY = "."


@dataclass(frozen=True)
class HomeData:
    """Everything the home screen may show; None means "unknown, leave it out"."""

    battery_left: int | None = None  # percent
    battery_right: int | None = None
    next_event: str | None = None  # "09:00 Standup"
    next_event_at: datetime | None = None
    weather: str | None = None  # "18C clear"
    tasks_due: int | None = None  # coursework / tasks due this week
    overdue: int | None = None
    deadline: str | None = None  # "Fri DQ 1 Stearns" (next hard due date)
    leads_new: int | None = None  # outbound leads never called
    ops_failed: int | None = None  # failed agent runs, last 24 h
    extra_rows: tuple[str, ...] = field(default_factory=tuple)  # formatted lines
    agent: str = "ASK"

    def with_battery(self, side: str, percent: int) -> "HomeData":
        key = "battery_left" if side == "left" else "battery_right"
        return replace(self, **{key: percent})


def gauge(percent: int | None, cells: int = GAUGE_CELLS) -> str:
    """[####..] style bar; unknown values render as [??????]."""
    if percent is None:
        return "[" + "?" * cells + "]"
    clamped = max(0, min(100, percent))
    filled = round(clamped / 100 * cells)
    return "[" + FILLED * filled + EMPTY * (cells - filled) + "]"


def countdown(now: datetime, when: datetime) -> str:
    """'in 9h40', 'in 25m', 'now', or 'ago' for a past event."""
    minutes = int((when - now).total_seconds() // 60)
    if minutes < -1:
        return "ago"
    if minutes < 1:
        return "now"
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"in {hours}h{mins:02d}"
    return f"in {mins}m"


def _fit(left: str, right: str, width: int) -> str:
    """Left text, right text pushed to the right edge, truncated to fit."""
    gap = width - len(left) - len(right)
    if gap < 1:
        return (left + " " + right)[:width]
    return left + " " * gap + right


def render_pipboy(
    data: HomeData,
    now: datetime,
    *,
    lines_per_page: int = DEFAULT_LINES_PER_PAGE,
    max_chars: int = DEFAULT_CHARS_PER_LINE,
    agents: int = 0,
) -> str:
    header = _fit(TITLE, f"{now:%a %d %b}  {now:%H:%M}".upper(), max_chars)
    battery = (
        f"BATT L{gauge(data.battery_left)}{_pct(data.battery_left)}"
        f"  R{gauge(data.battery_right)}{_pct(data.battery_right)}"
    )
    if data.next_event:
        when = f" ({countdown(now, data.next_event_at)})" if data.next_event_at else ""
        next_row = f"NEXT  {data.next_event}{when}"
    else:
        next_row = "NEXT  nothing scheduled"
    status_row = status_strip(data, max_chars) or f"AGENT {data.agent.upper()}"
    apps = f"[TAP] {agents} APPS" if agents else "[TAP] APPS"
    hints = f"[HOLD L] ASK   {apps}   [2x] EXIT"
    rows = [header, battery, next_row, *data.extra_rows, status_row, hints]
    # Keep the header and the hints; squeeze the middle if there are too many rows.
    if len(rows) > lines_per_page:
        rows = rows[: lines_per_page - 1] + [hints]
    return "\n".join(row[:max_chars].rstrip() for row in rows)


def status_strip(data: HomeData, width: int) -> str:
    """The one-line status readout: the most urgent bits first, as many as fit."""
    bits: list[str] = []
    if data.overdue:
        bits.append(f"OVERDUE {data.overdue}")
    if data.deadline:
        bits.append(f"DUE {data.deadline}")
    elif data.tasks_due is not None:
        bits.append(f"DUE {data.tasks_due}/wk")
    if data.ops_failed:
        bits.append(f"OPS {data.ops_failed} FAILED")
    if data.leads_new is not None:
        bits.append(f"LEADS {_short(data.leads_new)} new")
    if data.weather:
        bits.append(f"WX {data.weather}")
    line = ""
    for bit in bits:
        candidate = f"{line}  {bit}" if line else bit
        if len(candidate) > width:
            break
        line = candidate
    return line


def _short(count: int) -> str:
    return f"{count / 1000:.0f}k" if count >= 10_000 else str(count)


def _pct(percent: int | None) -> str:
    return "--%" if percent is None else f"{percent:>2d}%"
