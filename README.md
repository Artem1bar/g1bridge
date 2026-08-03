# g1bridge

Talk to a Claude agent through your **Even Realities G1** smart glasses — no phone app in the loop.

Your Mac connects to both arms of the G1 over Bluetooth LE, and a Claude agent (running through the local `claude` CLI, billed to your existing Claude subscription — no API key) answers onto the heads-up display. TouchBar taps page through long answers.

```
┌────────────┐   BLE (two arms)   ┌────────────┐   claude-agent-sdk   ┌──────────────┐
│ G1 glasses │◄──────────────────►│  your Mac  │◄────────────────────►│  claude CLI  │
│ HUD + taps │                    │  g1bridge  │                      │ (Max/Pro sub)│
└────────────┘                    └────────────┘                      └──────────────┘
```

## Status

| Piece | State |
|---|---|
| Protocol framing (0x4E text, 0xF5 events, heartbeat, mic control) | Unit-tested against the official EvenDemoApp protocol doc + community captures |
| Claude agent layer (multi-turn, subscription-billed, optional WebSearch) | **Verified live** — real answer round-tripped through the local `claude` CLI |
| BLE connect / display / tap paging | Written, **needs first run against real glasses** (see smoke test below) |
| Voice input (glasses mic → STT → Claude) | Not built yet — next milestone |

## Prerequisites

- macOS with Bluetooth, [uv](https://docs.astral.sh/uv/), and the `claude` CLI logged in (`claude` works in your terminal)
- G1 glasses **disconnected from the phone app** — each arm is a BLE peripheral that accepts one central, so turn off your phone's Bluetooth (or forget the glasses in the Even app) while using the bridge

```bash
uv sync
```

## Hardware smoke test (first run)

1. Phone Bluetooth off. Glasses on your head (or out of the case, awake).
2. `uv run g1 scan` — should find `..._L_...` and `..._R_...` and save their addresses to `~/.g1bridge.json`.
3. `uv run g1 hello` — test text should appear on the HUD. Tap the right temple for next page, left for back.
4. `uv run g1 events` — tap the TouchBars, take the glasses on/off, and watch the parsed events. If any show as `unknown`, note the raw hex — that's protocol knowledge we can add.
5. `uv run g1 chat` — type a question in the terminal; the answer pages onto the glasses.

Useful flags: `g1 -v ...` (debug logging), `g1 chat --chars 44 --lines 5` (display tuning), `g1 chat --no-web` (disable web search), `g1 chat --model claude-haiku-4-5` (faster/cheaper answers).

### Things to confirm on hardware (expected rough edges)

- **Line width**: default is 40 chars/line for the proportional 21px font — if lines wrap short or overflow, tune `--chars`.
- **`g1 clear`**: sends a community-observed exit command (`0x18`) that is *not* in the official protocol doc. If it does nothing, that's expected — double-tapping a TouchBar always exits.
- **Page-status nibbles**: first page is sent as "Even AI displaying", taps re-send pages in "manual mode" per the official doc; if paging misbehaves, capture `g1 -v chat` logs.

## Layout

- [protocol.py](src/g1bridge/protocol.py) — pure frame builders + notification parser (the only file that knows byte values)
- [paginate.py](src/g1bridge/paginate.py) — text wrapping into 5-line HUD pages
- [ble.py](src/g1bridge/ble.py) — dual-arm connections, heartbeat, event dispatch (bleak)
- [hud.py](src/g1bridge/hud.py) — paged text sessions with TouchBar paging
- [agent.py](src/g1bridge/agent.py) — Claude Agent SDK wrapper with a HUD-tuned system prompt
- [cli.py](src/g1bridge/cli.py) — `g1 scan | hello | events | chat | clear`

## Testing

`uv run pytest` — pure logic (framing, parsing, pagination) is fully unit-tested. The BLE transport and HUD session are hardware I/O and exempt from unit coverage in this prototype; they're verified on-device via `g1 events` / `g1 hello`.

## Protocol references

- [even-realities/EvenDemoApp](https://github.com/even-realities/EvenDemoApp) — official demo app; its README documents the BLE protocol (BSD-2-Clause). Primary source.
- [emingenc/even_glasses](https://github.com/emingenc/even_glasses), [binarythinktank/eveng1_python_sdk](https://github.com/binarythinktank/eveng1_python_sdk) — community implementations used as factual references for byte constants (no code reused; even_glasses is GPLv3).

## Roadmap

1. **Voice**: long-press the left temple → glasses mic streams LC3 audio → decode → STT → Claude → HUD (the stock Even AI flow, but with our agent).
2. **Tools that make it useful**: calendar, reminders, home control, notes with memory.
3. **Daily-driver platform decision**: keep the Mac bridge for hacking, then either a MentraOS app or a custom companion app (Flutter, based on the official demo) so it works away from the desk.
