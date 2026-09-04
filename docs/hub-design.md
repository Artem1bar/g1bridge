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

## Gesture map and why

| Mode | Single tap | Long-press left | Triple tap | Double tap |
|---|---|---|---|---|
| Menu | move cursor (right = down, left = up, wraps) | open the selected agent | — | leave the hub |
| Agent | page the answer (right = next, left = back) | new question | back to the menu | leave the hub |

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

## Where voice plugs in

`Action.NEW_PROMPT` (long-press inside an agent) currently shows a "type your
question" page. The voice path replaces that: enable the mic (`protocol.mic_control`),
collect `MIC_DATA` events until `AI_STOP`, decode LC3, transcribe, and feed the
text into `HubSession._ask` exactly as a typed line does today.
