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
| BLE connect | **Works** (2026-09-03) — both arms connect in ~3 s when the glasses are worn or in the open case; asleep on the desk they ignore connection requests ([investigation](docs/ble-investigation.md)) |
| HUD display | **Works on both arms** (2026-09-03) — after switching to the official left-ack-then-right handshake and plain Text Show status |
| Tap paging / gesture events | Next up — `g1 events` not yet run |
| Agent hub (menu of Claude agents on the HUD, TouchBar navigation) | **Built** — state machine + terminal simulator, 55 unit tests; runs end-to-end with `g1 hub --sim`; not yet seen on hardware |
| Voice input (glasses mic → LC3 decode → whisper.cpp → Claude → HUD) | **Works end to end on the glasses** (2026-09-03 22:47): hold the left temple, "What is the capital of France?" → "Paris." on the HUD; 5.4 s capture, 0.8 s transcription, offline, no key |

## Prerequisites

- macOS with Bluetooth, [uv](https://docs.astral.sh/uv/), and the `claude` CLI logged in (`claude` works in your terminal)
- G1 glasses **disconnected from the phone app** — each arm is a BLE peripheral that accepts one central, so turn off your phone's Bluetooth (or forget the glasses in the Even app) while using the bridge

```bash
uv sync
```

## Hardware smoke test (first run)

If a connection times out, the glasses are almost certainly dozing: put them
on, or open the charging case, and try again. After the first successful
connect the arms show up in the Mac's Bluetooth menu and stay attached to the
system; that's expected, and the bridge reconnects through that handle without
scanning. The history of both findings is in
[docs/ble-investigation.md](docs/ble-investigation.md).

1. Phone Bluetooth off. Glasses **on your head** (or in the open case).
2. `uv run g1 scan` — should find `..._L_...` and `..._R_...` and save their addresses to `~/.g1bridge.json`.
   Then `uv run g1 probe` — reports Bluetooth authorization, whether macOS is already holding an arm, whether each arm advertises as connectable, and whether it accepts a link.
3. `uv run g1 hello` — test text should appear on the HUD. Tap the right temple for next page, left for back.
4. `uv run g1 events` — tap the TouchBars, take the glasses on/off, and watch the parsed events. If any show as `unknown`, note the raw hex — that's protocol knowledge we can add.
5. `uv run g1 chat` — type a question in the terminal; the answer pages onto the glasses.

Useful flags: `g1 -v ...` (debug logging), `g1 chat --chars 44 --lines 5` (display tuning), `g1 chat --no-web` (disable web search), `g1 chat --model claude-haiku-4-5` (faster/cheaper answers).

### Things to confirm on hardware (expected rough edges)

- **Line width**: default is 40 chars/line for the proportional 21px font — if lines wrap short or overflow, tune `--chars`.
- **`g1 clear`**: sends a community-observed exit command (`0x18`) that is *not* in the official protocol doc. If it does nothing, that's expected — double-tapping a TouchBar always exits.
- **Page-status nibbles**: first page is sent as "Even AI displaying", taps re-send pages in "manual mode" per the official doc; if paging misbehaves, capture `g1 -v chat` logs.

## The hub

`g1 hub` puts Claude agents behind the glasses' own hold-to-talk gesture.
At rest the glasses stay on their normal dashboard. Hold the left temple, ask,
let go: the answer arrives as an Even AI result, pages with the temple taps,
and a double tap dismisses it. Say an agent's name first to switch ("research
what is LC3", "translate good morning", "draft a text to Sam"), and "back" or
"home" as words. Each agent has its own role prompt, tool access and multi-turn
memory. Speech recognition runs on the Mac, offline, with no API key.

`--home` shows our own clock page and agent list instead (below); it is kept
for experiments, because on hardware the firmware keeps every TouchBar gesture
to itself while app content is on screen, so nothing but voice works there.

```
+----------------------------------------+      +----------------------------------------+
|19:27  Thu 3 Sep                        |      |CLAUDE HUB                           2/5|
|Claude Hub                              |  tap |  Ask       quick answers, web on       |
|hold left temple: talk to Ask           | ---> |> Research  digs in, cites sources      |
|tap: 5 agents                           |      |  Translate any language <-> English    |
|                                        |      |  Explain   a term or idea, simply      |
+----------------------------------------+      +----------------------------------------+
```

| Where | Long-press left | Right / left tap | Double tap |
|---|---|---|---|
| At rest (glasses' dashboard) | ask the current agent | firmware's own | firmware's own |
| Answer on screen | new question, or "send" if the release wasn't reported | next / previous page (to confirm) | dismiss, back to rest |

Measured on hardware (2026-09-03): the long-press arrives as its own event and
the right arm streams microphone audio by itself; the release is reported only
if we never sent the documented mic-on command, so that command is off by
default (`--mic-cmd` restores it). A double tap is the firmware's "exit app"
and it tells us so. A triple tap toggles the firmware's silent mode. While our
own page is on screen the firmware forwards no taps and no long-press, which
is why the hub rests on the firmware's dashboard.

Until voice lands, questions are typed in the terminal; the answer pages onto
the HUD. Typing an agent's number or name in the menu opens it too.

**No glasses handy?** `uv run g1 hub --sim` renders every HUD page in the
terminal and accepts gesture words instead of taps: `r` / `l` single tap,
`rrr` / `lll` triple tap, `rr` / `ll` double tap, `hold` long-press. Anything
else you type is a question for the open agent. The whole hub, agents included,
runs this way today.

**Your own agents:** create `~/.g1bridge-agents.toml` (or pass `--agents`):

```toml
[[agent]]
id = "recipes"
name = "Recipes"                 # menu label, max 12 chars
blurb = "what to cook tonight"   # menu hint, max 26 chars
system_prompt = "Suggest one dish from the ingredients the wearer names."
web = false                      # WebSearch/WebFetch allowed? default true
# model = "claude-haiku-4-5"     # optional per-agent model
```

An entry whose `id` matches a built-in agent (`ask`, `research`, `translate`,
`explain`, `draft`) replaces it; new ids are appended to the menu. Bad entries
fail with a message naming the field before anything connects.

## Layout

- [protocol.py](src/g1bridge/protocol.py) — pure frame builders + notification parser (the only file that knows byte values)
- [paginate.py](src/g1bridge/paginate.py) — text wrapping into 5-line HUD pages
- [ble.py](src/g1bridge/ble.py) — dual-arm connections, heartbeat, event dispatch (bleak)
- [hud.py](src/g1bridge/hud.py) — paged text sessions with TouchBar paging
- [agent.py](src/g1bridge/agent.py) — Claude Agent SDK wrapper with a HUD-tuned system prompt
- [agents.py](src/g1bridge/agents.py) — agent registry: built-in agents + TOML overrides, validated
- [hub.py](src/g1bridge/hub.py) — pure hub state machine (menu ↔ agent) and menu rendering
- [session.py](src/g1bridge/session.py) — `HubSession`: display events + terminal lines → state machine → agents
- [display.py](src/g1bridge/display.py) — the `Display` protocol both transports satisfy
- [sim.py](src/g1bridge/sim.py) — terminal simulator: ASCII HUD frames, typed gestures
- [cli.py](src/g1bridge/cli.py) — `g1 scan | probe | hello | events | chat | hub | clear`

## Testing

`uv run pytest` — pure logic (framing, parsing, pagination, hub state machine, agent registry) is fully unit-tested, and `HubSession` is exercised end-to-end on the simulator with a stub agent. The BLE transport is hardware I/O and exempt from unit coverage in this prototype; it's verified on-device via `g1 events` / `g1 hello`.

## Protocol references

- [even-realities/EvenDemoApp](https://github.com/even-realities/EvenDemoApp) — official demo app; its README documents the BLE protocol (BSD-2-Clause). Primary source.
- [emingenc/even_glasses](https://github.com/emingenc/even_glasses), [binarythinktank/eveng1_python_sdk](https://github.com/binarythinktank/eveng1_python_sdk) — community implementations used as factual references for byte constants (no code reused; even_glasses is GPLv3).

## Roadmap

1. **Hub on real glasses**: connection works as of 2026-09-03; next confirm text renders (`g1 hello`), taps arrive (`g1 events`), then the gesture map and line widths under `g1 hub`.
2. **Voice on the glasses end-to-end**: the pipeline (LC3 → whisper.cpp small.en, `lc3py` + `pywhispercpp`) is built and validated offline with `g1 transcribe`; confirm the hold-to-talk round trip on the HUD and tune the silence gate on more recordings.
3. **Tools that make agents useful**: calendar, reminders, home control, notes with memory — added per agent in `~/.g1bridge-agents.toml`.
4. **Daily-driver platform decision**: keep the Mac bridge for hacking, then either a MentraOS app or a custom companion app so it works away from the desk. The hub and agent layers are transport-agnostic (`Display` protocol), so a phone-side transport slots in without rewriting them.
