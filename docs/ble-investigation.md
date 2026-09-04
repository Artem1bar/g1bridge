# Why the glasses won't connect

Living notes on what was, until 2026-09-03, the one thing blocking this project.

## Resolved: first connection, 2026-09-03

`g1 probe` connected to **both arms** (UART service found, notifications on)
on the third run of the evening. The three runs, same Mac, same code, minutes
apart:

| Run | RSSI L / R | Connect |
|---|---|---|
| 1 | -85 / -90 dBm (far, or in the closed case) | timeout ×2 per arm |
| 2 | -38 / -37 dBm (on the desk next to the Mac) | timeout ×2 per arm |
| 3 | -62 / -60 dBm (posture changed — see below) | **connected in ~3 s per arm** |

Run 2 rules out signal strength as the cause; run 3 shows the Mac-side stack
(bleak 3.0.2, CoreBluetooth, no bonding) is perfectly capable. What changed
between runs 2 and 3 was the physical state of the glasses, not the code. The
working hypothesis: an arm that is not worn and not charging advertises as
connectable but sleeps through most connection requests; worn (wear sensor) or
in the open case (charging) it listens after every advertisement. Whoever runs
this next: note the posture in `~/g1-probe.log`, the log doesn't capture it.

Practical rule until proven otherwise: **wear the glasses, or open the case,
then connect.** The cached addresses in `~/.g1bridge.json` from July were still
valid, so macOS did not rotate them across this period.

**Second finding, same evening: after that first connect, macOS kept the arms.**
Both arms appeared in the menu-bar Bluetooth list, and the next `g1 hello` runs
failed with "not advertising" on every attempt: a peripheral the system is
holding stops advertising, so a scan can never find it again. This is not a
fault — it is how CoreBluetooth shares one link between the system and apps —
but it means a scan-first connect is wrong on macOS after day one. Fix (in
`GlassArm._find_device`): ask CoreBluetooth for the arm by identifier
(`retrievePeripheralsWithIdentifiers:`), wrap that handle as a bleak device,
and connect through it; fall back to a scan only for arms the Mac has never
seen. This is also how the iOS drivers reconnect, so it is the right default,
not a workaround.

**First text on the HUD, 19:21.** `g1 hello` through the remembered handle
connected both arms and the right display rendered the greeting. The left
display showed the firmware's "Even AI is listening" screen instead. Two
deviations from the official protocol were in play and are now fixed as the
default: (1) we sent to the right arm after a fixed 150 ms instead of after the
left arm's acknowledgment (`G1Glasses.send_acked`); (2) the page carried the
"Even AI complete" status, which belongs to the voice-reply flow, rather than
plain "Text Show" (`HudText(ai_mode=False)` is now the default; `--ai` restores
the old behaviour for comparison). With both fixes in place (19:26) `g1 hello` rendered on **both displays**.
Which of the two mattered is untested; `g1 hello --ai` isolates the status
byte if anyone cares. `g1 -v hello` logs every notification byte.

Everything below is kept as the record of what was ruled out.

## Symptom (historical)

`g1 scan` finds both arms instantly and saves their addresses. Every attempt to
*connect* times out: CoreBluetooth's `connectPeripheral` never calls back, four
attempts of 20s each, on both arms. Nothing has ever rendered on the HUD.

Observed state during a failing run: left arm advertising, RSSI −66 dBm (about a
metre away), `kCBAdvDataIsConnectable` present in the advertisement — value never
printed, which is the gap `g1 probe` now closes.

## Ruled out

| Theory | Verdict |
|---|---|
| Arms asleep | No — they advertise strongly throughout |
| bleak's 10s default timeout, no retries | Fixed (4 × 20s + advertising pre-check); made no difference |
| Arms bond-locked to the Even phone app | Weak — [Gadgetbridge](https://gadgetbridge.org/gadgets/others/even_realities/) does initial pairing without the vendor app |
| Mac Bluetooth off | No — controller reports `State: On` |
| A stale bleak release | No — 3.0.2 (2026-05-02) is the latest published |

The last experiment (probe worn vs. probe in the open case) never produced data:
`g1 probe` crashed on its first line before printing anything. Fixed 2026-08-03.

## What the ecosystem says (researched 2026-08-03)

- **Nobody has documented a G1 connected from macOS.** Every working stack is
  Android/iOS ([fahrplan](https://github.com/meyskens/fahrplan) is Flutter,
  MentraOS is a phone app, Even's own demo is Flutter) or Windows
  ([EvenComfort](https://github.com/hqrrr/EvenComfort) drives the glasses from a
  Windows PC through `even_glasses`).
- **The community treats explicit BLE pairing as a required step** —
  [eveng1_python_sdk](https://github.com/binarythinktank/eveng1_python_sdk)'s
  `PairingManager` connects, calls `client.pair()`, disconnects "to finalize
  pairing", and has recovery code for Windows pairing error 19. bleak does not
  implement `pair()` on macOS: CoreBluetooth only bonds implicitly, when a
  characteristic demands encryption. **If the G1 requires a bond before it will
  accept a GATT connection, macOS has no API to initiate one** — which would
  explain a hang that no amount of retrying fixes.
- bleak 3.0.0 (2026-03) was a rewrite; 3.0.2 is current. A downgrade to 2.1.1 is
  a legitimate control experiment, not a fix.
- No bleak issues report a macOS 26 / Darwin 25 BLE regression.
- Heartbeat interval: the community protocol notes say the G1 drops a link after
  32s of silence, so 28–30s is their interval. Ours is 8s — safely inside.

## What changed in the picture (researched 2026-09-03)

- **Bonding is probably not the blocker.** MentraOS's iOS driver connects to a
  G1 with plain CoreBluetooth `connect(peripheral, options: nil)`, matching arms
  by the `_L_` / `_R_` name and discovering the Nordic UART service — no bond,
  because iOS has no API for one. Only its Android driver calls `createBond()`.
  So Apple's stack does reach the G1 without an explicit pairing step.
- **The field fix for "connects but GATT unreachable" is posture + exclusivity:**
  the `even_glasses` maintainer's answer to the same symptom (issue #9, July 2026)
  was "make sure it is in pair mode, in cover, and disconnected from other
  devices", and the reporter confirmed it worked. Official support adds: tap the
  left TouchBar five times to reboot an arm, and unpair/re-pair.
- **macOS addresses are not stable.** `openg1-sdk` (the only G1 Python stack
  still updated in 2026) notes that macOS "hands back opaque addresses that
  change between reboots", so it only ever matches by name. Our cached
  `~/.g1bridge.json` UUIDs date from July; `g1 probe` scans fresh, `g1 hello`
  should too — treat any saved address as a hint, never as truth.
- **No macOS G1 connect-timeout report exists anywhere** (bleak issues, GitHub
  code search). We are still the first documented attempt.
- **Even Hub stays G2-only** (support article updated 2026-08-18; SDK 0.0.14),
  so the Mac bridge / MentraOS choice below is still the whole decision space.
- **MentraOS moved SDKs**: `@mentra/sdk` is deprecated; the current one is
  `@mentra/miniapp` (phone-side JS + WebView, `session.transcription`,
  `session.display.render`, `session.auth.fetch()` to our own backend). Its
  Android G1 driver has double-tap handling commented out as broken, so a
  tap-driven menu on G1 via Mentra is unproven.

## Next hardware session — the ladder (superseded 2026-09-03: connection works; next is `g1 hello` → HUD render)

Before step one: forget the glasses in the Even app (or keep phone Bluetooth
off), reboot each arm (tap the left TouchBar five times), put them in the open
case.

Run each step, stop when something changes, keep the output.

```bash
uv run g1 probe 2>&1 | tee -a ~/g1-probe.log   # wearing the glasses
uv run g1 probe 2>&1 | tee -a ~/g1-probe.log   # in the case, lid open
```

`Probe 1/3` reports the Bluetooth authorization of the terminal app and whether
macOS is already holding an arm. `Probe 2/3` prints, per arm, `CONNECTABLE` vs
`NON-CONNECTABLE (beacon only!)`. `Probe 3/3` connects to each arm separately,
right first — the reverse of the order that has been failing.

Then branch on what the two runs show:

- **Non-connectable in both postures** → the arms only listen for their bonded
  phone. The Mac-side BLE path is a dead end without a bond; go to Pivots.
- **Connectable, but bleak still times out** → control test with LightBlue (free,
  Mac App Store): connect to `Even G1_24_L_8EDD5D` by hand.
  - LightBlue connects → the problem is bleak/pyobjc, not the glasses. Try
    `uv add 'bleak==2.1.1'`, then connecting through a peripheral retrieved with
    `retrievePeripheralsWithIdentifiers:` instead of a scanned one.
  - LightBlue also fails → the problem is this Mac + these glasses. Try another
    central (Linux box or Raspberry Pi, where BlueZ *can* bond explicitly)
    before spending more on macOS.
- **Connect succeeds but the HUD stays blank** → a good problem: the transport
  works and the text frame is wrong. `g1 -v hello`, capture the notification
  bytes, compare against `protocol.py`.

## Pivots, if the Mac-side BLE path stays dead

1. **MentraOS** ([repo](https://github.com/Mentra-Community/MentraOS), MIT) —
   supports G1, replaces the companion app, has a TypeScript MiniApp SDK with
   transcription and display APIs. The agent moves into a MiniApp; the phone does
   the BLE. Fastest route to a daily driver.
2. **Linux central** — a Raspberry Pi running this same package, since BlueZ
   exposes explicit pairing. Keeps all the protocol work; changes only the host.
3. **Android + Gadgetbridge** as a reference implementation to read for the
   handshake we might be missing.
