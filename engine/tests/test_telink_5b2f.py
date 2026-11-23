"""Hardware-free Telink 248A:5B2F candidate gates and frame tests.

Runs without hidapi or hardware, using the common protocol-test stub.
Frames follow BatteryHub's hardware-tested M800 Pro exchange on this
shared receiver; the percentage was verified on a real G501 (2026-11-23),
while the charging semantics still await a plug-in test there.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _hidstub  # noqa: F401
from providers import telink_5b2f


def info(**changes):
    row = dict(vendor_id=0x248A, product_id=0x5B2F, usage_page=0xFF00,
               usage=1, path=b"mock-telink", physical_id="usb:mock-unit",
               interface_number=1,
               feature_report_length=33, feature_report_sizes={"12": 33})
    row.update(changes)
    return row


def reply(level=63, state=0, connected=1, **changes):
    frame = [0] * 33
    frame[0] = 0x0C
    frame[1] = 0x01
    frame[2] = 0x20
    frame[6] = connected
    frame[8:12] = b"M802"
    frame[18] = level
    frame[19] = state
    for key, value in changes.items():
        frame[int(key)] = value
    return frame


class Telink5b2fTests(unittest.TestCase):
    def poll(self, inventory=None, frames=(), send_result=None):
        provider = telink_5b2f.Telink5b2fProvider()
        backend = MagicMock()
        dev = backend.device.return_value
        dev.send_feature_report.side_effect = (
            lambda request: len(request) if send_result is None else send_result)
        dev.get_feature_report.side_effect = list(frames) + [[]]
        with patch.object(telink_5b2f, "hid", backend), patch.object(
                telink_5b2f.hidlist, "enumerate",
                return_value=inventory if inventory is not None else [info()]) as enum:
            result = provider.poll()
        enum.assert_called_once_with(0x248A)
        return provider, backend, dev, result

    def test_request_is_only_documented_status_query(self):
        expected = [0x0C, 0x01, 0x20, 0, 7] + [0] * 28
        self.assertEqual(telink_5b2f.make_request(7, 33), expected)
        longer = telink_5b2f.make_request(0x1FF, 40)
        self.assertEqual(len(longer), 40)
        self.assertEqual(longer[4], 0xFF)
        one = telink_5b2f.make_request(1, 33)
        one[5] = 0x80
        self.assertEqual(telink_5b2f.make_request(1, 33)[5], 0)

    def test_exact_identity_usage_interface_and_proof_gates(self):
        bad = [[], [info(vendor_id=0x3554)], [info(product_id=0x5B2E)],
               [info(usage_page=0xFF01)], [info(usage=2)],
               [info(), info(path=b"ambiguous")], [info(path=b"")],
               [info(physical_id="collection:unresolved")],
               [info(physical_id=None)], [info(physical_id="")],
               [info(interface_number=0)], [info(interface_number=2)],
               [info(interface_number=None)], [info(interface_number=True)],
               [info(interface_number="1")],
               [info(feature_report_length=None)], [info(feature_report_length=32)],
               [info(feature_report_length="33")], [info(feature_report_length=True)],
               [info(feature_report_sizes=None)], [info(feature_report_sizes={})],
               [info(feature_report_sizes={"12": 32})],
               [info(feature_report_sizes={"12": "33"})],
               [info(feature_report_sizes={12: 33})],
               [info(feature_report_sizes={"12": 33, "13": 34})],
               [info(feature_report_sizes={"12": 33}, feature_report_length=34)]]
        for inventory in bad:
            with self.subTest(inventory=inventory):
                provider, backend, dev, result = self.poll(inventory)
                self.assertEqual(result, [])
                backend.device.assert_not_called()
                dev.open_path.assert_not_called()
                dev.send_feature_report.assert_not_called()
                self.assertTrue(provider.diagnostics())

    def test_unrelated_inventory_has_no_fallback(self):
        provider, backend, dev, result = self.poll([
            info(vendor_id=0x1234, path=b"other-vendor"),
            info(product_id=0x5B2E, path=b"cable"),
            info(usage_page=0xFF01, path=b"sibling"), info()], [reply()])
        self.assertEqual(len(result), 1)
        dev.open_path.assert_called_once_with(b"mock-telink")
        self.assertEqual(result[0].name, "2.4G 无线鼠标（248A:5B2F）")

    def test_charging_follows_documented_state_byte(self):
        # State byte: 0 discharging, 1 charging, 2 full (level forced to 100).
        cases = ((0, 63, False), (1, 63, True), (2, 100, False))
        for state, level, charging in cases:
            with self.subTest(state=state):
                self.assertEqual(telink_5b2f.parse_reply(reply(state=state)),
                                 (level, charging, ""))
                provider, backend, dev, result = self.poll(frames=[reply(state=state)])
                self.assertEqual(len(result), 1)
                status = result[0]
                self.assertEqual(status.level, level)
                self.assertEqual(status.source, "telink_5b2f")
                self.assertEqual(status.kind, "mouse")
                self.assertTrue(status.online)
                self.assertTrue(status.connection_known)
                self.assertIs(status.charging, charging)
                self.assertIs(status.charging_known, True)
                self.assertFalse(status.unconfirmed)
                self.assertEqual(status.verification_level, "C")
                dev.send_feature_report.assert_called_once()
                request = dev.send_feature_report.call_args.args[0]
                self.assertEqual(request, telink_5b2f.make_request(1, 33))
                dev.get_feature_report.assert_called_once_with(0x0C, 33)
                dev.close.assert_called_once_with()

    def test_full_state_reports_100(self):
        self.assertEqual(telink_5b2f.parse_reply(reply(level=84, state=2)), (100, False, ""))
        provider, backend, dev, result = self.poll(frames=[reply(level=84, state=2)])
        self.assertEqual(result[0].level, 100)

    def test_invalid_replies_return_no_status(self):
        bad = [reply(**{"0": 0x0B}), reply(**{"2": 0x21}), reply(state=3),
               reply(state=255), reply(level=101), reply()[:19], reply()[:10],
               [0] * 33, reply(level=0), reply(connected=0, level=0, state=1)]
        for frame in bad:
            with self.subTest(frame=frame):
                self.assertIsNone(telink_5b2f.parse_reply(frame))
        # A disconnected reply (byte 6 zero, zero level) is retried once with
        # a fresh sequence, then gives up without a status.
        provider, backend, dev, result = self.poll(
            frames=[reply(connected=0, level=0), reply(connected=0, level=0)])
        self.assertEqual(result, [])
        self.assertEqual(dev.send_feature_report.call_count, 2)
        seqs = [call.args[0][4] for call in dev.send_feature_report.call_args_list]
        self.assertEqual(seqs, [1, 2])
        self.assertEqual(provider.diagnostics(), ["no valid battery reply; connection unknown"])

    def test_retry_recovers_when_mouse_answers_second_query(self):
        provider, backend, dev, result = self.poll(
            frames=[reply(connected=0, level=0), reply(level=41)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].level, 41)
        self.assertEqual(dev.send_feature_report.call_count, 2)

    def test_garbage_reply_is_not_retried(self):
        provider, backend, dev, result = self.poll(frames=[[0] * 33])
        self.assertEqual(result, [])
        dev.send_feature_report.assert_called_once()
        self.assertEqual(provider.diagnostics(), ["no valid battery reply; connection unknown"])

    def test_short_send_never_retries_or_reads(self):
        provider, backend, dev, result = self.poll(send_result=32)
        self.assertEqual(result, [])
        dev.send_feature_report.assert_called_once()
        dev.get_feature_report.assert_not_called()
        dev.close.assert_called_once_with()
        self.assertIn("short/failed battery feature request; no retry", provider.diagnostics())

    def test_sequence_increments_across_polls(self):
        provider = telink_5b2f.Telink5b2fProvider()
        backend = MagicMock()
        dev = backend.device.return_value
        dev.send_feature_report.side_effect = lambda request: len(request)
        dev.get_feature_report.side_effect = [reply(), reply()]
        with patch.object(telink_5b2f, "hid", backend), patch.object(
                telink_5b2f.hidlist, "enumerate", return_value=[info()]):
            self.assertEqual(provider.poll()[0].level, 63)
            self.assertEqual(provider.poll()[0].level, 63)
        seqs = [call.args[0][4] for call in dev.send_feature_report.call_args_list]
        self.assertEqual(seqs, [1, 2])

    def test_io_failures_and_diagnostics_are_fixed(self):
        for method in ("open_path", "send_feature_report", "get_feature_report"):
            provider = telink_5b2f.Telink5b2fProvider()
            backend = MagicMock()
            dev = backend.device.return_value
            dev.send_feature_report.side_effect = lambda request: len(request)
            getattr(dev, method).side_effect = OSError("private mock-telink path")
            with patch.object(telink_5b2f, "hid", backend), patch.object(
                    telink_5b2f.hidlist, "enumerate", return_value=[info()]):
                self.assertEqual(provider.poll(), [])
            self.assertEqual(provider.diagnostics(), ["battery I/O failed; connection unknown"])
            dev.close.assert_called_once_with()
        provider = telink_5b2f.Telink5b2fProvider()
        with patch.object(telink_5b2f.hidlist, "enumerate", side_effect=OSError("private path")):
            self.assertEqual(provider.poll(), [])
        self.assertEqual(provider.diagnostics(), ["enumeration failed: nothing sent"])


    def test_real_hardware_shaped_inventory_without_feature_proof(self):
        # What topology.discover actually yields for this receiver when its
        # scoped proof block has not run: usage/interface/physical only, no
        # feature_report_length or feature_report_sizes keys at all. The gate
        # must still fail closed, send nothing, and name the exact reason.
        shaped = dict(vendor_id=0x248A, product_id=0x5B2F, usage_page=0xFF00,
                      usage=1, path=b"\\\\?\\hid#vid_248a&pid_5b2f&mi_01&col04#mock",
                      physical_id="pnp:usb\\vid_248a&pid_5b2f\\mock-unit",
                      interface_number=1, release_number=0x0121,
                      input_report_length=0, output_report_length=0,
                      product_string="Wireless-Receiver",
                      manufacturer_string="", serial_number="")
        self.assertNotIn("feature_report_sizes", shaped)
        provider, backend, dev, result = self.poll([shaped])
        self.assertEqual(result, [])
        backend.device.assert_not_called()
        dev.open_path.assert_not_called()
        dev.send_feature_report.assert_not_called()
        self.assertEqual(provider.diagnostics(),
                         ["feature-0C length unproven or incompatible: nothing sent"])

    def test_topology_proof_is_scoped_to_exact_receiver_collection(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "topology.py")
                         .read_text(encoding="utf-8"))
        blocks = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and {0x248A, 0x5B2F, 0xFF00, 0x0001} <= {
                child.value for child in ast.walk(node.test)
                if isinstance(child, ast.Constant) and type(child.value) is int}
            and "byte_report_sizes(hid, data, caps, 2)" in ast.unparse(node)
        ]
        self.assertTrue(blocks, "topology must prove feature sizes for 248A:5B2F FF00:0001")
        block = min(blocks, key=lambda node: node.end_lineno - node.lineno)
        text = ast.unparse(block)
        self.assertIn("feature_report_length=caps.feature", text)
        self.assertIn("feature_report_sizes=byte_report_sizes(hid, data, caps, 2)", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
