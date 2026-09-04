"""The `g1` command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import AsyncIterator

from .agent import GlassesAgent
from .agents import AGENTS_PATH, load_agents
from .ble import G1Glasses, save_config, signal_hint
from .display import Display
from .hud import HudText
from .micstream import MicStats, append_payloads
from .paginate import DEFAULT_CHARS_PER_LINE, DEFAULT_LINES_PER_PAGE
from .protocol import EventKind, G1Event
from .session import HubSession
from .sim import SimGlasses
from .stt import DEFAULT_MODEL, WhisperTranscriber

logger = logging.getLogger(__name__)

HELLO_TEXT = "Hello from Claude! Your G1 bridge is alive and talking to both arms."


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="g1", description="Even Realities G1 <-> Claude agent bridge"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="find the glasses and save their addresses")
    sub.add_parser("probe", help="diagnose: test advertising + connection for each arm")
    events = sub.add_parser(
        "events", help="stream parsed TouchBar/wear events (Ctrl+C to stop)"
    )
    events.add_argument(
        "--record",
        type=Path,
        default=None,
        help="append raw microphone (LC3) payloads to this file",
    )
    sub.add_parser("clear", help="exit to the dashboard (experimental command)")

    hello = sub.add_parser("hello", help="connect and put test text on the HUD")
    hello.add_argument("text", nargs="?", default=HELLO_TEXT)

    chat = sub.add_parser("chat", help="terminal chat; answers page onto the glasses")

    hub = sub.add_parser(
        "hub", help="agent hub on the HUD: pick a Claude agent with the TouchBars"
    )
    hub.add_argument(
        "--sim", action="store_true", help="no glasses: render the HUD in the terminal"
    )
    hub.add_argument(
        "--trace",
        action="store_true",
        help="print every gesture event the glasses send",
    )
    hub.add_argument(
        "--home",
        action="store_true",
        help="show our own home page instead of resting on the glasses' dashboard "
        "(gestures are dead while app content is on screen)",
    )
    hub.add_argument(
        "--text-show",
        action="store_true",
        help="send answer pages as Text Show instead of Even AI statuses",
    )
    hub.add_argument(
        "--mic-cmd",
        action="store_true",
        help="send the documented mic-on command on long-press (the glasses "
        "stream by themselves; after this command they stop reporting the release)",
    )
    hub.add_argument(
        "--record-dir",
        type=Path,
        default=None,
        help="save every voice capture as <dir>/<HHMMSS>.lc3 for offline analysis",
    )
    hub.add_argument(
        "--stt",
        choices=("whisper", "none"),
        default="whisper",
        help="speech-to-text for the long-press (default: whisper.cpp, offline)",
    )
    hub.add_argument(
        "--stt-model",
        default=DEFAULT_MODEL,
        help=f"whisper.cpp model name (default: {DEFAULT_MODEL})",
    )

    transcribe = sub.add_parser(
        "transcribe", help="run the voice pipeline on a file saved by `events --record`"
    )
    transcribe.add_argument("file", type=Path)
    transcribe.add_argument("--stt-model", default=DEFAULT_MODEL)
    hub.add_argument(
        "--agents",
        type=Path,
        default=AGENTS_PATH,
        help=f"agent definitions, TOML (default: {AGENTS_PATH})",
    )

    for command in (chat, hub):
        command.add_argument(
            "--model", default=None, help="model override for the agents"
        )
        command.add_argument(
            "--no-web", action="store_true", help="disable WebSearch/WebFetch"
        )
    for command in (hello, chat):
        command.add_argument(
            "--ai",
            action="store_true",
            help="send pages with Even AI statuses instead of plain Text Show",
        )
    for command in (hello, chat, hub):
        command.add_argument(
            "--chars", type=int, default=DEFAULT_CHARS_PER_LINE, help="chars per line"
        )
        command.add_argument(
            "--lines", type=int, default=DEFAULT_LINES_PER_PAGE, help="lines per page"
        )

    args = parser.parse_args()
    # Diagnostics get piped through `tee`; keep prints in order with the log lines.
    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print()
    except (RuntimeError, ValueError) as exc:
        if args.verbose:
            raise
        raise SystemExit(f"error: {exc}") from exc


async def _run(args: argparse.Namespace) -> None:
    if args.command == "scan":
        await _scan()
        return
    if args.command == "probe":
        await _probe()
        return
    if args.command == "transcribe":
        _transcribe_file(args)
        return
    if args.command == "hub":
        agents = load_agents(args.agents)  # fail on bad TOML before the slow connect
        if args.sim:
            sim = SimGlasses(max_chars=args.chars, lines_per_page=args.lines)
            await _hub(sim, agents, args)
            return

    print("Connecting to the glasses (can take ~20s per arm if they were dozing)...")
    glasses = await G1Glasses.from_config()
    print("Connected to both arms.")
    try:
        if args.command == "hello":
            await _hello(glasses, args)
        elif args.command == "events":
            await _events(glasses, args.record)
        elif args.command == "clear":
            await glasses.exit_to_dashboard()
            print(
                "Sent the experimental exit command. Did the HUD return to the dashboard?"
            )
        elif args.command == "chat":
            await _chat(glasses, args)
        elif args.command == "hub":
            await _hub(glasses, agents, args)
    finally:
        await glasses.disconnect()


async def _scan() -> None:
    print("Scanning for G1 arms (10s)... make sure the phone app is disconnected.")
    found = await G1Glasses.scan()
    if not found:
        print("Nothing found. Glasses on, out of the case, phone Bluetooth off?")
        return
    for side, (address, name) in sorted(found.items()):
        print(f"  {side:>5}: {name}  [{address}]")
    if {"left", "right"} <= found.keys():
        save_config(
            {
                side: {"address": address, "name": name}
                for side, (address, name) in found.items()
            }
        )
        print("Saved. You can now run `g1 hello` or `g1 chat`.")
    else:
        print("Found only one arm — not saving. Both arms must be discoverable.")


async def _probe_system_view() -> None:
    """Report what CoreBluetooth itself says before we blame the glasses."""
    from . import macos, protocol
    from .ble import load_config

    print("\nProbe 1/3 — asking macOS about Bluetooth and the saved arms...")
    print(
        "  (if the probe stops here, macOS is holding a Bluetooth permission prompt\n"
        "   for this app — approve it, or grant it in System Settings > Privacy &\n"
        "   Security > Bluetooth, then rerun)"
    )
    config = load_config()
    identifiers = [
        config[side]["address"] for side in ("left", "right") if side in config
    ]
    view = await macos.inspect(identifiers, protocol.UART_SERVICE_UUID)
    print(f"  authorization: {view.authorization}")
    print(f"  power:         {view.power}")
    if view.error:
        print(f"  ! {view.error}")
    for label, peripherals in (
        ("held by macOS now", view.system_connected),
        ("saved arms", view.known),
    ):
        if not peripherals:
            print(f"  {label}: none")
            continue
        print(f"  {label}:")
        for peripheral in peripherals:
            print(f"    {peripheral.name}: {peripheral.state}")
    if view.system_connected:
        print(
            "  ^ macOS is already holding an arm. Connecting to a freshly scanned\n"
            "    peripheral can stall in that state — note this in your report."
        )


async def _probe() -> None:
    """Test each arm independently: advertising mode, connectable flag, UART."""
    from importlib.metadata import version

    from .ble import GlassArm  # local import to keep module load light

    print(f"g1 probe (bleak {version('bleak')})")
    await _probe_system_view()
    print("\nProbe 2/3 — scanning 10s for advertising arms...")
    seen = await G1Glasses.scan_adv()
    for side in ("left", "right"):
        sighting = seen.get(side)
        if sighting is None:
            print(
                f"  {side:>5}: not seen (asleep, out of range, or held by another device)"
            )
            continue
        if sighting.connectable is True:
            mode = "CONNECTABLE"
        elif sighting.connectable is False:
            mode = "NON-CONNECTABLE (beacon only!)"
        else:
            mode = "connectable-flag missing"
        print(f"  {side:>5}: advertising, {mode}, rssi={sighting.rssi}dBm")
        if sighting.manufacturer_hex:
            print(f"         mfg-data: {sighting.manufacturer_hex}")
        if (hint := signal_hint(sighting.rssi)) is not None:
            print(f"         ! {hint}")
    if not seen:
        print("Neither arm is advertising. Wake the glasses and rerun.")
        return

    if all(s.connectable is False for s in seen.values()):
        print(
            "\nBoth arms are broadcasting NON-connectable advertisements — in this state\n"
            "they ignore every connection request, which matches the timeouts exactly.\n"
            "Put the glasses in the charging case with the LID OPEN (the posture the\n"
            "official app uses for pairing) and run `g1 probe` again."
        )
        return

    print("Probe 3/3 — trying to connect to each arm independently (right first)...")
    results: dict[str, str] = {}
    for side in ("right", "left"):
        sighting = seen.get(side)
        if sighting is None:
            results[side] = "skipped (not advertising)"
            continue
        arm = GlassArm(side, sighting.address, sighting.name)
        try:
            await arm.connect(attempts=2)
            results[side] = "CONNECTED — UART service found, notifications on"
            await asyncio.sleep(1.0)
            await arm.disconnect()
        except RuntimeError as exc:
            results[side] = f"FAILED — {exc}"
    print("\nProbe results:")
    for side in ("left", "right"):
        print(f"  {side:>5}: {results.get(side, 'skipped')}")
    failures = [r for r in results.values() if r.startswith("FAILED")]
    if failures and len(failures) == len(
        [r for r in results.values() if not r.startswith("skipped")]
    ):
        print(
            "\nArms advertise as connectable but still refuse links. Next experiments:\n"
            "  1. Rerun `g1 probe` with the glasses in the OPEN charging case.\n"
            "  2. Report this output back so we can try a bleak downgrade or LightBlue test."
        )


async def _hello(glasses: G1Glasses, args: argparse.Namespace) -> None:
    hud = HudText(
        glasses, max_chars=args.chars, lines_per_page=args.lines, ai_mode=args.ai
    )
    pages = await hud.show(args.text)
    print(f"Displayed {pages} page(s).", end=" ")
    if pages > 1:
        print("Tap the right temple for next page, left for back.", end=" ")
    print("Ctrl+C to exit.")
    await asyncio.Event().wait()


async def _events(glasses: G1Glasses, record: Path | None) -> None:
    print(
        "Listening for events (tap the TouchBars, take the glasses on/off)... Ctrl+C to stop."
    )
    if record is not None:
        print(f"Recording microphone payloads to {record}")
    stats = MicStats()
    pending: list[G1Event] = []
    while True:
        try:
            event = await asyncio.wait_for(glasses.events.get(), timeout=1.0)
        except (TimeoutError, asyncio.TimeoutError):
            stats, pending = _flush_mic(stats, pending, record)
            continue
        if event.kind is EventKind.MIC_DATA:
            stats = stats.add(event)
            pending.append(event)
            continue
        stats, pending = _flush_mic(stats, pending, record)
        print(f"  [{event.side:>5}] {event.kind.value:<16} raw={event.raw.hex()}")


def _flush_mic(
    stats: MicStats, pending: list[G1Event], record: Path | None
) -> tuple[MicStats, list[G1Event]]:
    """Print one summary line for the mic packets seen so far and save them."""
    if not pending:
        return stats, pending
    line = stats.summary("right")
    if record is not None:
        written = append_payloads(record, pending)
        line += f" -> {written} B saved"
    print(line)
    return MicStats(), []


async def _chat(glasses: G1Glasses, args: argparse.Namespace) -> None:
    hud = HudText(
        glasses, max_chars=args.chars, lines_per_page=args.lines, ai_mode=args.ai
    )
    print("Chat ready — answers page onto the glasses. Ctrl+D or /quit to exit.")
    async with GlassesAgent(model=args.model, web_search=not args.no_web) as agent:
        loop = asyncio.get_running_loop()
        while True:
            try:
                prompt = await loop.run_in_executor(None, lambda: input("you> "))
            except EOFError:
                print()
                break
            prompt = prompt.strip()
            if not prompt:
                continue
            if prompt in {"/quit", "/exit"}:
                break
            await hud.show("Thinking...")
            answer = await agent.ask(prompt)
            print(f"claude> {answer}\n")
            pages = await hud.show(answer)
            if pages > 1:
                print(
                    f"({pages} pages on the HUD — tap right temple for next, left for back)"
                )


async def _hub(display: Display, agents, args: argparse.Namespace) -> None:
    where = "simulated HUD below" if args.sim else "the glasses"
    flow = "our home page" if args.home else "resting on the glasses' dashboard"
    print(
        f"Hub with {len(agents)} agents on {where}, {flow}. Ctrl+D or /quit to leave."
    )
    if args.agents.exists():
        print(f"(agents merged from {args.agents})")

    def factory(spec):
        return GlassesAgent.for_spec(spec, model=args.model, web_search=not args.no_web)

    transcriber = None
    if args.stt == "whisper" and not args.sim:
        transcriber = WhisperTranscriber(args.stt_model)
        print(
            f"Loading speech model {args.stt_model} (first run downloads it and "
            "compiles Metal shaders; ~20s)..."
        )
        await asyncio.get_running_loop().run_in_executor(None, transcriber.warm)
        print("Speech model ready. Hold the left temple to talk.")

    session = HubSession(
        display,
        agents,
        agent_factory=factory,
        max_chars=args.chars,
        lines_per_page=args.lines,
        sim_gestures=args.sim,
        ai_mode=not args.text_show,
        trace=args.trace,
        transcriber=transcriber,
        rest=not args.home,
        mic_cmd=args.mic_cmd,
        record_dir=args.record_dir,
    )
    await session.run(stdin_lines())


def _transcribe_file(args: argparse.Namespace) -> None:
    """Decode + transcribe a recording; the offline test bench for the voice path."""
    import time

    from .audio import decode_lc3, rms
    from .stt import transcribe_pcm

    payloads = args.file.read_bytes()
    pcm = decode_lc3(payloads)
    print(
        f"{args.file}: {len(payloads)} B -> {pcm.size} samples "
        f"({pcm.size / 16000:.1f}s), rms={rms(pcm):.4f}"
    )
    transcriber = WhisperTranscriber(args.stt_model)
    started = time.perf_counter()
    transcriber.warm()
    print(f"model {args.stt_model} loaded in {time.perf_counter() - started:.1f}s")
    started = time.perf_counter()
    text = transcribe_pcm(transcriber._get_model(), pcm, max_seconds=None)
    print(f"transcribed in {time.perf_counter() - started:.1f}s:")
    print(f"  {text or '(nothing recognised)'}")


async def stdin_lines(prompt: str = "you> ") -> AsyncIterator[str]:
    """Terminal lines as an async stream, read on a daemon thread so a hub exit
    triggered from the glasses does not wait for one more Enter."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def reader() -> None:
        try:
            while True:
                try:
                    line = input(prompt)
                except EOFError:
                    return
                loop.call_soon_threadsafe(queue.put_nowait, line)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=reader, name="stdin-reader", daemon=True).start()
    while (line := await queue.get()) is not None:
        yield line
