# Hub design

How `g1 hub` is put together, and where the next pieces plug in. Written 2026-09-03.

## Goal

A launcher on the HUD, in the spirit of Even's own Hub, whose "apps" are Claude
agents: pick one with the temple TouchBars, talk to it, page its answer, go back.
Each agent has a role prompt, its own tool access, and its own multi-turn memory
for the session.

## Layers

```
 TouchBar events ─┐                               ┌─ GlassesAgent (claude-agent-sdk)
 terminal lines ──┼─► HubSession ─► hub.step() ──►│   one live session per agent,
 voice (later) ───┘     (session.py)  (pure)      └─ opened on first use (AgentPool)
                            │
                            ▼
                       Display protocol  ──►  G1Glasses (BLE)  |  SimGlasses (terminal)
```

- **hub.py** is pure: `HubState` is a frozen dataclass; `step(state, event)`
  returns `(new_state, Action)`. Every gesture decision lives here and is unit
  tested without I/O. `render_menu` is the only place that knows the menu layout.
- **session.py** is the only stateful orchestrator. One asyncio queue carries
  display events and terminal lines; the loop applies `step`, then performs the
  `Action` (show menu, open agent, page, ask, exit). Gesture words typed in
  simulator mode are handled inline rather than round-tripped through the
  display, so scripted tests are deterministic.
- **agents.py** owns what the menu lists. Built-ins are code; overrides are TOML
  with fail-fast validation, because a 12-char name and a 26-char blurb are hard
  limits of a 40-column, 5-line screen.
- **display.py** is the seam. Anything with `send_text_page`, `add_listener` and
  `exit_to_dashboard` is a display: the BLE glasses today, the terminal
  simulator, and later a phone-side transport (MentraOS MiniApp, companion app).

## Rest flow (what actually works on the G1)

Evening of 2026-09-03, five hub runs on hardware: whenever a page of ours was
on the display, the firmware forwarded no single taps and no long-press; the
one long-press that did arrive came right after the wearer looked up and the
firmware opened its own dashboard. With nothing of ours on screen (`g1 events`)
the long-press, its release, the double tap and the triple tap all arrived.
So the hub's default is `rest=True`: draw nothing, wait for `AI_START`, collect
the mic stream the firmware starts by itself, transcribe on release (or on a
second press, or after 20 s), show the answer as an Even AI result, and treat
the double tap as "dismiss" rather than "leave". Agent choice is spoken: agent
names double as verbs ("translate …", "research …", "draft …").

The documented mic-on command (`0x0E 0x01`) is opt-in (`--mic-cmd`): after we
sent it the firmware stopped reporting the release, and it streams without it.

## Gesture map and why (home flow, `--home`, for experiments)

| Mode | Single tap | Long-press left | Double tap |
|---|---|---|---|
| Home | open the agent list | talk to the first agent | leave the hub |
| Menu | move cursor (right = down, left = up, wraps) | open the selected agent | leave the hub |
| Agent | page the answer (right = next, left = back) | new question | leave the hub |

What the glasses actually send (measured with `g1 events`, 2026-09-03, nothing
of ours on screen): long-press = `F5 17` / `F5 18` on the left arm, and the
right arm starts streaming mic packets on its own; double tap = `F5 00` on
**both** arms, followed by the firmware's own `dashboard_open`; triple tap =
`F5 04` / `F5 05`, the silent-mode toggle, so it is not a hub gesture; single
taps: nothing at all. Also seen: head up/down (`F5 02` / `F5 03`) right before
the firmware's dashboard events, and a per-arm battery percent (`F5 0A nn`).

Home is the dashboard: clock and date (redrawn on the minute), title, and the
two gestures that matter. It is where the hub rests, so the wearer's habit from
the stock firmware (hold the left temple to talk) keeps working unchanged.

Single taps follow the stock Even AI paging convention so paging feels native.
Long-press left is the firmware's own "start Even AI" gesture (`AI_START`), so
it is the natural "talk to this agent" trigger and becomes the voice entry
point. Double tap is "exit app" in the official TouchBar guide and in the
EvenDemoApp, and MentraOS notes it clears the G1 screen on its own, so the hub
never relies on it for anything but leaving. Triple tap is what the community
`openg1-sdk` launcher uses to switch apps, so it is "back" here. The map is
confined to `_step_menu` / `_step_agent` in hub.py; if hardware shows the
firmware swallowing a gesture, that is the only place to change.

## Open questions for the first hardware run

- **Triple tap doubles as the firmware's silent-mode toggle** (`protocol.py`
  maps `F5 04` / `F5 05` to "silent mode on/off"). If a triple tap blanks the
  display or mutes notifications as a side effect, "back to menu" needs another
  gesture; until then the typed (later spoken) words `back` / `menu` are the
  safe way home. Double tap: assume the firmware exits on its own; the hub only
  needs to notice.
- Does the firmware forward long-presses to us while our text is on screen, or
  does it start its own Even AI flow first?
- Does `ScreenStatus.TEXT_SHOW` for the menu page behave differently from the
  Even-AI statuses used for answers (both are sent through `send_text_page`)?
- Do leading spaces render in the proportional font, so the `> ` cursor column
  lines up? If not, mark rows differently in `render_menu`.
- Real chars-per-line at font 21; `--chars` tunes it.

## Voice

Long-press start (`AI_START`) opens the mic (`Display.set_mic(True)`; the
firmware streams anyway, the command is per spec) and starts a `voice.Capture`;
every `MIC_DATA` packet is appended; long-press end (`AI_STOP`) closes the mic
and hands the raw LC3 bytes to the configured `Transcriber`. The default is
`stt.WhisperTranscriber`: `audio.decode_lc3` (Google liblc3 via `lc3py`,
16 kHz / 10 ms / 20-byte frames, ten per packet) → `trim_silence` (threshold
measured from a real G1 recording) → whisper.cpp `small.en` on Metal
(`pywhispercpp`, ~0.4 s per utterance, model cached under
`~/Library/Application Support/pywhispercpp/models`) → non-speech labels
dropped → `HubSession._ask`, exactly as a typed line. `g1 transcribe FILE`
runs the same path over a recording from `g1 events --record` as an offline
bench; `--stt none` keeps the typed fallback.
