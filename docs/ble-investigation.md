# Why the glasses won't connect

Living notes on the one thing blocking this project. Last updated 2026-08-03.

## Symptom

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

## Next hardware session — the ladder

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
