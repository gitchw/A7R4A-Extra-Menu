#!/usr/bin/env python3
"""Pack a 32-byte MTHR header + payload.bin into a .req request file."""
import argparse
from pathlib import Path
import struct
import sys

REQ_MAGIC = 0x4D544852  # "MTHR"
OPS = {"check": 0, "install": 1, "remove": 2}


def fnv1a_32(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def pack_req(payload: bytes, op: int, version: int, entry: int, state_size: int,
             hook: int, max_code_bytes: int) -> bytes:
    if not payload or len(payload) > max_code_bytes or (len(payload) % 4) != 0:
        raise ValueError(
            f"payload length {len(payload)} must be non-zero, 4-byte aligned, <= {max_code_bytes}"
        )
    if op not in (0, 1, 2):
        raise ValueError(f"unsupported op {op}")
    if (entry & 1) == 0:
        raise ValueError(f"entry 0x{entry:x} must have Thumb bit 0 set")
    if version == 2 and (entry < 1 or entry >= len(payload)):
        raise ValueError(f"version 2 relative entry 0x{entry:x} out of payload range")
    header = struct.pack(
        "<8I",
        REQ_MAGIC,
        version,
        op,
        len(payload),
        entry,
        fnv1a_32(payload),
        state_size,
        hook,
    )
    return header + payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pack MTHR .req file from payload.bin")
    ap.add_argument("--payload", required=True, type=Path)
    ap.add_argument("--op", required=True, choices=sorted(OPS))
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--version", type=int, default=2)
    ap.add_argument("--entry", type=lambda x: int(x, 0), default=1)
    ap.add_argument("--state-size", type=int, default=1184)
    ap.add_argument("--hook", type=lambda x: int(x, 0), default=0x00625EE8)
    ap.add_argument("--max-code-bytes", type=int, default=1700)
    args = ap.parse_args(argv)

    data = args.payload.read_bytes()
    req = pack_req(
        data,
        OPS[args.op],
        args.version,
        args.entry,
        args.state_size,
        args.hook,
        args.max_code_bytes,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(req)
    print(f"PACKED_REQ out={args.out} op={args.op} bytes={len(req)} payload={len(data)} fnv=0x{fnv1a_32(data):08x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
