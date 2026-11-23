"""Telink 248A:5B2F shared-receiver battery candidate (feature report 0C).

This receiver ID is shared by unrelated mice (DELUX M800 Pro, Lofree Touch,
Lenovo ERAZER G501 and others), so the display name stays neutral and no
brand or model is claimed. Only the descriptor-proven FF00:0001 feature
collection on interface 1 is used, and only the status query documented for
this receiver family by BatteryHub (krisgfx9/Wireless-Device-Battery-Monitor,
src/drivers/misc_mice.cpp PollDeluxM800, hardware-tested on an M800 Pro
receiver) is sent: SET_FEATURE 0C [01 20 00 <seq>], then GET_FEATURE 0C.
The reply's state byte is validated against its documented values and
charging follows it (1 means charging, 2 means full). The percentage was
validated on a real G501 on 2026-11-23, matching the official ERAZER driver
exactly; the charging semantics still await a plug-in test on that hardware.
The wired 248A:5B2E identity is outside this candidate.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None

from battery_hid import BatteryHidProvider
from providers.base import DeviceStatus
from providers import hidlist

VID = 0x248A
PID = 0x5B2F
COLLECTION = (0xFF00, 0x0001)
INTERFACE = 1
REPORT_ID = 0x0C
MIN_REPORT_LENGTH = 33
MIN_REPLY_LENGTH = 20
QUERY = (0x01, 0x20)
STATES = (0, 1, 2)  # documented: discharging, charging, full
REPLY_WAIT_SECONDS = 0.020

PIDS = {PID: "2.4G 无线鼠标（248A:5B2F）"}


def make_request(sequence: int, length: int) -> List[int]:
    """Build only the documented status query at the proven feature length."""
    frame = [REPORT_ID, QUERY[0], QUERY[1], 0, sequence & 0xFF]
    frame += [0] * (length - len(frame))
    return frame


def _header_valid(frame) -> bool:
    return (len(frame) >= MIN_REPLY_LENGTH and frame[0] == REPORT_ID
            and frame[2] == QUERY[1])


def parse_reply(reply) -> Optional[Tuple[int, bool, str]]:
    """Return (percent, charging, detail), rejecting unproven/invalid frames."""
    if not isinstance(reply, (bytes, bytearray, list, tuple)):
        return None
    frame = list(reply)
    if any(type(value) is not int or not 0 <= value <= 255 for value in frame):
        return None
    if not _header_valid(frame):
        return None
    state = frame[19]
    if state not in STATES:
        return None
    level = frame[18]
    if level > 100:
        return None
    # Byte 6 zero with a zero percentage is the receiver reporting no mouse.
    if frame[6] == 0 and level == 0:
        return None
    if state == 2:
        level = 100
    if not 1 <= level <= 100:
        return None
    # Charging follows the documented state byte (1 charging; 0 and the
    # full state 2 are not charging). Percent is validated on G501 hardware;
    # the charging semantics still await a plug-in test there.
    return level, state == 1, ""


def _feature_length(info) -> Optional[int]:
    """Descriptor proof for feature report 0C, following the dareu pattern."""
    maximum = info.get("feature_report_length")
    sizes = info.get("feature_report_sizes")
    if (type(maximum) is not int or not 0 <= maximum <= 4096
            or not isinstance(sizes, dict) or not sizes
            or any(type(n) is not int or not 2 <= n <= maximum for n in sizes.values())
            or max(sizes.values()) != maximum):
        return None
    length = sizes.get(str(REPORT_ID))
    if type(length) is not int or length < MIN_REPORT_LENGTH:
        return None
    return length


class Telink5b2fProvider(BatteryHidProvider):
    name = "telink_5b2f"
    vid = VID
    pids = PIDS
    collection = COLLECTION
    charging_known = True
    unconfirmed = False
    verification_level = "C"
    kind = "mouse"

    def __init__(self):
        super().__init__()
        self._sequence = 0

    def _pick(self, infos):
        # Feature-only route: the inherited _pick proves input/output maps,
        # which this collection is not required to have. Gate locally instead.
        exact = [info for info in infos
                 if (info.get("vendor_id"), info.get("product_id")) == (VID, PID)]
        eligible = [info for info in exact
                    if (info.get("usage_page"), info.get("usage")) == COLLECTION]
        if len(eligible) != 1 or not eligible[0].get("path"):
            self._diag.append("missing or ambiguous eligible collection: nothing sent")
            return None
        info = eligible[0]
        physical = info.get("physical_id", "")
        if not isinstance(physical, str) or not physical.strip() or physical.casefold().startswith("collection:"):
            self._diag.append("physical parent unresolved; nothing sent")
            return None
        if type(info.get("interface_number")) is not int or info["interface_number"] != INTERFACE:
            self._diag.append("interface unproven: nothing sent")
            return None
        length = _feature_length(info)
        if length is None:
            self._diag.append("feature-0C length unproven or incompatible: nothing sent")
            return None
        return dict(info, telink_feature_length=length)

    def device_name(self, info):
        return PIDS[info["product_id"]]

    def parse_reply(self, reply):
        return parse_reply(reply)

    def poll(self):
        self._diag = []
        if hid is None:
            return []
        try:
            inventory = hidlist.enumerate(VID)
        except OSError:
            self._diag.append("enumeration failed: nothing sent")
            return []
        info = self._pick(inventory)
        if info is None:
            return []
        # Avoid inherited fallback or secondary-collection selection: only this
        # descriptor-proven exact collection reaches open_path/send_feature_report.
        return self._poll_one(info, info["telink_feature_length"])

    def _exchange(self, dev, length):
        self._sequence = (self._sequence + 1) & 0xFF
        request = make_request(self._sequence, length)
        if dev.send_feature_report(request) != len(request):
            self._diag.append("short/failed battery feature request; no retry")
            return None
        time.sleep(REPLY_WAIT_SECONDS)  # Upstream exchange delay before GET_FEATURE.
        reply = dev.get_feature_report(REPORT_ID, length)
        return list(reply) if isinstance(reply, (bytes, bytearray, list, tuple)) else None

    def _query(self, dev, length):
        reply = self._exchange(dev, length)
        reading = parse_reply(reply)
        if reading is not None:
            return reading
        # On the receiver a header-valid reply with byte 6 zero can precede
        # the mouse answering; upstream retries once with a fresh sequence.
        if reply is not None and _header_valid(reply) and reply[6] == 0:
            reading = parse_reply(self._exchange(dev, length))
            if reading is not None:
                return reading
        self._diag.append("no valid battery reply; connection unknown")
        return None

    def _poll_one(self, info, length):
        dev = None
        try:
            dev = hid.device()
            dev.open_path(info["path"])
            reading = self._query(dev, length)
            if reading is None:
                if not self._diag:
                    self._diag = ["no valid battery reply; connection unknown"]
                return []
            level, charging, approx = reading
            physical = info.get("physical_id", "")
            return [DeviceStatus(
                f"{self.name}:{physical or repr(info['path'])}", self.device_name(info),
                level, charging, True, self.name, approx=approx, kind=self.kind,
                physical_id=physical, connection_known=True, charging_known=True,
                verification_level=self.verification_level, unconfirmed=False)]
        except (OSError, ValueError):
            self._diag.append("battery I/O failed; connection unknown")
            return []
        finally:
            if dev is not None:
                try:
                    dev.close()
                except OSError:
                    self._diag.append("HID handle close failed")

    def diagnostics(self):
        return list(self._diag)
