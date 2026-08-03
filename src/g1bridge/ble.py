"""BLE transport for the G1: per-arm connections, heartbeat, event dispatch."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from . import protocol
from .protocol import G1Event, ScreenStatus, parse_notification

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".g1bridge.json"
HEARTBEAT_INTERVAL_S = 8.0
INTER_ARM_GAP_S = 0.1

# The arms doze aggressively when the glasses aren't worn; the first connect
# after idling routinely exceeds bleak's 10s default, so: retry, generously.
FIND_TIMEOUT_S = 8.0
CONNECT_TIMEOUT_S = 20.0
CONNECT_ATTEMPTS = 4
RETRY_PAUSE_S = 2.0

WAKE_HINT = (
    "Wake the glasses: put them on (or tap a TouchBar), or open and close the "
    "charging-case lid. Also make sure the phone app is fully disconnected "
    "(phone Bluetooth off) and no other g1 command is running."
)


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("could not read %s; ignoring it", path)
        return {}


def save_config(config: dict, path: Path = CONFIG_PATH) -> None:
    path.write_text(json.dumps(config, indent=2) + "\n")
    logger.info("saved glasses addresses to %s", path)


@dataclass(frozen=True)
class ArmSighting:
    """One arm as seen during a diagnostic scan."""

    side: str
    address: str
    name: str
    rssi: int
    connectable: bool | None  # None = flag missing from the advertisement
    manufacturer_hex: str


class GlassArm:
    """One temple of the glasses — a single BLE peripheral with a UART service."""

    def __init__(
        self,
        side: str,
        address: str,
        name: str = "",
        on_event: Callable[[G1Event], None] | None = None,
    ):
        self.side = side
        self.address = address
        self.name = name or f"G1 {side}"
        self._on_event = on_event
        self._client: BleakClient | None = None
        self._tx = None
        self._write_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self, attempts: int = CONNECT_ATTEMPTS) -> None:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await self._connect_once(attempt, attempts)
                return
            except (BleakError, RuntimeError, OSError, TimeoutError) as exc:
                last_error = exc
                reason = str(exc) or type(exc).__name__
                logger.warning(
                    "%s: attempt %d/%d failed (%s)%s",
                    self.name,
                    attempt,
                    attempts,
                    reason,
                    " — retrying" if attempt < attempts else "",
                )
                if attempt < attempts:
                    await asyncio.sleep(RETRY_PAUSE_S)
        raise RuntimeError(
            f"{self.name}: could not connect after {attempts} attempts. {WAKE_HINT}"
        ) from last_error

    async def _connect_once(self, attempt: int, attempts: int) -> None:
        logger.info(
            "connecting %s (attempt %d/%d, up to %.0fs)...",
            self.name,
            attempt,
            attempts,
            FIND_TIMEOUT_S + CONNECT_TIMEOUT_S,
        )
        device = await BleakScanner.find_device_by_address(
            self.address, timeout=FIND_TIMEOUT_S
        )
        if device is None:
            raise RuntimeError("not advertising — probably asleep")
        client = BleakClient(
            device,
            disconnected_callback=self._handle_disconnect,
            timeout=CONNECT_TIMEOUT_S,
        )
        await client.connect()
        try:
            service = client.services.get_service(protocol.UART_SERVICE_UUID)
            if service is None:
                raise RuntimeError("UART service not found")
            tx = service.get_characteristic(protocol.UART_TX_CHAR_UUID)
            rx = service.get_characteristic(protocol.UART_RX_CHAR_UUID)
            if tx is None or rx is None:
                raise RuntimeError("UART characteristics not found")
            await client.start_notify(rx, self._handle_notify)
        except BaseException:
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise
        self._client = client
        self._tx = tx
        logger.info("connected %s (%s)", self.name, self.address)

    async def disconnect(self) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.disconnect()

    async def write(self, frame: bytes) -> None:
        if self._client is None or not self._client.is_connected:
            raise RuntimeError(f"{self.name}: not connected")
        async with self._write_lock:
            await self._client.write_gatt_char(self._tx, frame, response=True)

    def _handle_notify(self, _characteristic, data: bytearray) -> None:
        event = parse_notification(self.side, bytes(data))
        logger.debug("%s notify: %s %s", self.side, event.kind.value, event.raw.hex())
        if self._on_event is not None:
            self._on_event(event)

    def _handle_disconnect(self, _client) -> None:
        logger.warning("%s disconnected", self.name)


class G1Glasses:
    """Both arms plus the heartbeat loop and a parsed-event stream."""

    def __init__(
        self,
        left_address: str,
        right_address: str,
        left_name: str = "",
        right_name: str = "",
    ):
        self.left = GlassArm("left", left_address, left_name, self._dispatch)
        self.right = GlassArm("right", right_address, right_name, self._dispatch)
        self.events: asyncio.Queue[G1Event] = asyncio.Queue()
        self._listeners: list[Callable[[G1Event], None]] = []
        self._heartbeat_task: asyncio.Task | None = None
        self._seq = 0

    @staticmethod
    async def scan(timeout: float = 10.0) -> dict[str, tuple[str, str]]:
        """Find G1 arms nearby; returns {"left": (address, name), "right": (...)}."""
        found: dict[str, tuple[str, str]] = {}
        for device in await BleakScanner.discover(timeout=timeout):
            name = device.name or ""
            if "G1" not in name:
                continue
            if "_L_" in name and "left" not in found:
                found["left"] = (device.address, name)
            elif "_R_" in name and "right" not in found:
                found["right"] = (device.address, name)
        return found

    @staticmethod
    async def scan_adv(timeout: float = 10.0) -> dict[str, "ArmSighting"]:
        """Like scan(), but with diagnostics: RSSI, connectable flag, mfg data."""
        found: dict[str, ArmSighting] = {}
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
        for device, adv in discovered.values():
            name = device.name or ""
            if "G1" not in name:
                continue
            side = "left" if "_L_" in name else "right" if "_R_" in name else None
            if side is None or side in found:
                continue
            connectable: bool | None = None
            try:
                # CoreBluetooth raw advertisement dict rides in platform_data[1].
                raw = adv.platform_data[1]
                value = raw["kCBAdvDataIsConnectable"]
                if value is not None:
                    connectable = bool(int(value))
            except (IndexError, KeyError, TypeError, ValueError):
                pass
            manufacturer_hex = ",".join(
                f"{company:04x}:{payload.hex()}"
                for company, payload in adv.manufacturer_data.items()
            )
            found[side] = ArmSighting(
                side=side,
                address=device.address,
                name=name,
                rssi=adv.rssi,
                connectable=connectable,
                manufacturer_hex=manufacturer_hex,
            )
        return found

    @classmethod
    async def from_config(cls, scan_timeout: float = 10.0) -> "G1Glasses":
        """Connect using saved addresses, scanning (and saving) on first use."""
        config = load_config()
        if "left" not in config or "right" not in config:
            logger.info("no saved addresses; scanning for glasses...")
            found = await cls.scan(scan_timeout)
            missing = {"left", "right"} - found.keys()
            if missing:
                raise RuntimeError(
                    f"could not find the {' and '.join(sorted(missing))} arm(s). "
                    "Make sure the glasses are on, out of the case, and NOT connected "
                    "to the phone app (turning off the phone's Bluetooth is simplest)."
                )
            config = {
                side: {"address": address, "name": name}
                for side, (address, name) in found.items()
            }
            save_config(config)
        glasses = cls(
            config["left"]["address"],
            config["right"]["address"],
            config["left"].get("name", ""),
            config["right"].get("name", ""),
        )
        await glasses.connect()
        return glasses

    async def connect(self) -> None:
        # The official demo's ordering rule: talk to the left arm first.
        await self.left.connect()
        await self.right.connect()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def disconnect(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        await asyncio.gather(
            self.left.disconnect(), self.right.disconnect(), return_exceptions=True
        )

    def add_listener(self, listener: Callable[[G1Event], None]) -> None:
        self._listeners.append(listener)

    def _dispatch(self, event: G1Event) -> None:
        self.events.put_nowait(event)
        for listener in self._listeners:
            listener(event)

    async def send_both(self, frame: bytes, gap_s: float = INTER_ARM_GAP_S) -> None:
        """Send to the left arm first, then the right after a short gap."""
        await self.left.write(frame)
        await asyncio.sleep(gap_s)
        await self.right.write(frame)

    async def send_text_page(
        self,
        text: str,
        *,
        page: int = 1,
        total_pages: int = 1,
        status: ScreenStatus = ScreenStatus.AI_COMPLETE,
    ) -> None:
        self._seq = (self._seq + 1) & 0xFF
        frame = protocol.text_page(
            text, page=page, total_pages=total_pages, status=status, seq=self._seq
        )
        await self.send_both(frame, gap_s=0.15)

    async def exit_to_dashboard(self) -> None:
        """Experimental — see protocol.exit_to_dashboard()."""
        await self.send_both(protocol.exit_to_dashboard())

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                self._seq = (self._seq + 1) & 0xFF
                await self.send_both(protocol.heartbeat(self._seq), gap_s=0.02)
            except Exception as exc:  # keep beating through transient BLE errors
                logger.warning("heartbeat failed: %s", exc)
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
