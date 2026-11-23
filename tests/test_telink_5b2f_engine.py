"""Telink 248A:5B2F shared-receiver offline registration regressions."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
sys.path.insert(0, str(ENGINE))

from catalog import validate_catalog, verification_for
from device_pack import BRAND_PROVIDERS, parse_pack
from known_ids import build_index
import server

VID, PID = 0x248A, 0x5B2F
NAME = "2.4G 无线鼠标（248A:5B2F）"


class Telink5b2fBridgeTests(unittest.TestCase):
    def test_registration_catalog_and_offline_index_are_exact(self):
        self.assertIn(("telink_5b2f", "Telink5b2fProvider"), server.PROVIDERS)
        self.assertEqual(server.PROVIDER_VIDS["telink_5b2f"], (VID,))
        self.assertIn("telink_5b2f", BRAND_PROVIDERS)
        self.assertIn("telink_5b2f", server.NO_CHARGING_EVIDENCE)
        self.assertIn("telink_5b2f", server.READER_DIAGNOSTICS)
        disabled = parse_pack(json.dumps({
            "format": "myfinder-device-pack", "loader": 1, "packVersion": 1,
            "keyId": "fixture", "createdAt": "2026-11-04T10:00:00Z",
            "entries": [], "disabled": [{"provider": "telink_5b2f", "vendorId": "248A",
                                          "productId": "5B2F", "reason": "fixture"}],
        }))
        self.assertTrue(disabled.loaded, disabled.rejected)
        self.assertEqual(len(disabled.disabled), 1)
        self.assertEqual(verification_for("telink_5b2f")[0], "C")

        index = build_index()
        self.assertNotIn("telink_5b2f", index["unparsed"])
        self.assertNotIn("telink_5b2f", index["partial"])
        self.assertEqual(index["vendors"]["telink_5b2f"], [VID])
        rows = [item for item in index["devices"] if item["provider"] == "telink_5b2f"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["vendorId"], row["productId"], row["name"], row["kind"]),
                         (VID, PID, NAME, "mouse"))
        # Shared receiver: the offline index must flag model identification,
        # as it does for the other shared-receiver passive reader.
        self.assertIs(row.get("requiresModelMatch"), True)

    def test_generated_catalog_stays_synchronized(self):
        self.assertEqual(validate_catalog()["errors"], [])


if __name__ == "__main__":
    unittest.main()
