"""Host unit tests for tools/pack_req.py and A7R4A MTHR v2 header invariants."""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
import pack_req


class PackReqTests(unittest.TestCase):
    def test_pack_v2_ops_and_header(self):
        payload = b"\x01\x02\x03\x04" * 8
        for name, op in (("check", 0), ("install", 1), ("remove", 2)):
            with tempfile.TemporaryDirectory() as td:
                p_in = Path(td) / "payload.bin"
                p_out = Path(td) / f"{name}.req"
                p_in.write_bytes(payload)
                res = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools/pack_req.py"),
                        "--payload",
                        str(p_in),
                        "--op",
                        name,
                        "--out",
                        str(p_out),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertIn(f"op={name}", res.stdout)
                raw = p_out.read_bytes()
                self.assertEqual(len(raw), 32 + len(payload))
                h = struct.unpack("<8I", raw[:32])
                self.assertEqual(h[0], 0x4D544852)
                self.assertEqual(h[1], 2)
                self.assertEqual(h[2], op)
                self.assertEqual(h[3], len(payload))
                self.assertEqual(h[4], 1)
                self.assertEqual(h[5], pack_req.fnv1a_32(payload))
                self.assertEqual(h[6], 1184)
                self.assertEqual(h[7], 0x00625EE8)
                self.assertEqual(raw[32:], payload)

    def test_rejects_unaligned_oversize_or_non_thumb_entry(self):
        with self.assertRaises(ValueError):
            pack_req.pack_req(b"\x00\x00\x00", 0, 2, 1, 1184, 0x00625EE8, 1700)
        with self.assertRaises(ValueError):
            pack_req.pack_req(b"\x00" * 1704, 0, 2, 1, 1184, 0x00625EE8, 1700)
        with self.assertRaises(ValueError):
            pack_req.pack_req(b"\x00" * 16, 0, 2, 0, 1184, 0x00625EE8, 1700)


if __name__ == "__main__":
    unittest.main()
