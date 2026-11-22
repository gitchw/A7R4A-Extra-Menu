#!/usr/bin/env python3
"""Convert standard .cube 3D LUT files into compact binary .a7lt tables.

Binary .a7lt layout (little-endian, 16-byte header + N^3 * 3 RGB uint8 payload):
  magic[4]   = b"A7LT"
  version    = uint16 (1)
  size       = uint16 (N in [2..65])
  flags      = uint32 (0)
  data_bytes = uint32 (N * N * N * 3)
  payload    = uint8[N * N * N * 3] in standard .cube order (B major, G middle, R fastest)
"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys

A7LT_MAGIC = b"A7LT"
A7LT_VERSION = 1
A7LT_MIN_SIZE = 2
A7LT_MAX_SIZE = 65
A7LT_HEADER_FMT = "<4sHHII"
A7LT_HEADER_BYTES = struct.calcsize(A7LT_HEADER_FMT)


def _parse_vec3(parts: list[str], keyword: str) -> tuple[float, float, float]:
    if len(parts) != 4:
        raise ValueError(f"{keyword} expects 3 float values")
    vals = tuple(float(x) for x in parts[1:4])
    return vals[0], vals[1], vals[2]


def parse_cube_text(text: str) -> tuple[int, list[tuple[int, int, int]]]:
    """Parse a .cube 3D LUT text stream into (size, [(r_u8, g_u8, b_u8), ...])."""
    lut_size: int | None = None
    domain_min = (0.0, 0.0, 0.0)
    domain_max = (1.0, 1.0, 1.0)
    samples: list[tuple[float, float, float]] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        head = parts[0]
        if head in ("TITLE", "LUT_IN_VIDEO_RANGE", "LUT_OUT_VIDEO_RANGE"):
            continue
        if head == "LUT_1D_SIZE" or head == "LUT_1D_INPUT_RANGE":
            raise ValueError(f"Line {lineno}: 1D LUTs ({head}) are not supported")
        if head == "LUT_3D_SIZE":
            if len(parts) != 2:
                raise ValueError(f"Line {lineno}: LUT_3D_SIZE expects 1 integer")
            lut_size = int(parts[1])
            if not (A7LT_MIN_SIZE <= lut_size <= A7LT_MAX_SIZE):
                raise ValueError(
                    f"Line {lineno}: LUT_3D_SIZE {lut_size} outside [{A7LT_MIN_SIZE}, {A7LT_MAX_SIZE}]"
                )
            continue
        if head == "DOMAIN_MIN":
            domain_min = _parse_vec3(parts, "DOMAIN_MIN")
            continue
        if head == "DOMAIN_MAX":
            domain_max = _parse_vec3(parts, "DOMAIN_MAX")
            continue
        if len(parts) == 3:
            try:
                r, g, b = float(parts[0]), float(parts[1]), float(parts[2])
            except ValueError as exc:
                raise ValueError(f"Line {lineno}: invalid RGB triple {line!r}") from exc
            samples.append((r, g, b))
            continue
        raise ValueError(f"Line {lineno}: unrecognized directive {line!r}")

    if lut_size is None:
        raise ValueError("Missing LUT_3D_SIZE directive")
    expected = lut_size ** 3
    if len(samples) != expected:
        raise ValueError(f"Expected {expected} LUT samples for size {lut_size}, got {len(samples)}")

    spans = tuple(domain_max[c] - domain_min[c] for c in range(3))
    if any(s <= 0.0 for s in spans):
        raise ValueError(f"Invalid domain bounds min={domain_min} max={domain_max}")

    quantized: list[tuple[int, int, int]] = []
    for r, g, b in samples:
        rgb = []
        for idx, v in enumerate((r, g, b)):
            norm = (v - domain_min[idx]) / spans[idx]
            if norm < 0.0:
                norm = 0.0
            elif norm > 1.0:
                norm = 1.0
            rgb.append(int(round(norm * 255.0)))
        quantized.append((rgb[0], rgb[1], rgb[2]))
    return lut_size, quantized


def encode_a7lt(size: int, samples: list[tuple[int, int, int]]) -> bytes:
    """Serialize quantized 3D LUT samples into .a7lt binary bytes."""
    if not (A7LT_MIN_SIZE <= size <= A7LT_MAX_SIZE):
        raise ValueError(f"LUT size {size} outside [{A7LT_MIN_SIZE}, {A7LT_MAX_SIZE}]")
    expected = size ** 3
    if len(samples) != expected:
        raise ValueError(f"Expected {expected} samples, got {len(samples)}")
    payload = bytearray(expected * 3)
    pos = 0
    for r, g, b in samples:
        payload[pos] = r & 0xFF
        payload[pos + 1] = g & 0xFF
        payload[pos + 2] = b & 0xFF
        pos += 3
    header = struct.pack(A7LT_HEADER_FMT, A7LT_MAGIC, A7LT_VERSION, size, 0, len(payload))
    return header + bytes(payload)


def verify_a7lt(blob: bytes) -> tuple[int, int]:
    """Validate an .a7lt binary blob and return (size, payload_bytes)."""
    if len(blob) < A7LT_HEADER_BYTES:
        raise ValueError("Truncated .a7lt header")
    magic, version, size, flags, data_bytes = struct.unpack_from(A7LT_HEADER_FMT, blob, 0)
    if magic != A7LT_MAGIC:
        raise ValueError(f"Bad .a7lt magic: {magic!r}")
    if version != A7LT_VERSION:
        raise ValueError(f"Unsupported .a7lt version: {version}")
    if flags != 0:
        raise ValueError(f"Unsupported .a7lt flags: {flags:#x}")
    if not (A7LT_MIN_SIZE <= size <= A7LT_MAX_SIZE):
        raise ValueError(f"Invalid .a7lt size: {size}")
    expected_bytes = (size ** 3) * 3
    if data_bytes != expected_bytes or len(blob) != A7LT_HEADER_BYTES + expected_bytes:
        raise ValueError(
            f"Size mismatch: header data_bytes={data_bytes}, file_bytes={len(blob)}, expected_payload={expected_bytes}"
        )
    return size, data_bytes


def convert_cube_file(src: Path, dst: Path) -> tuple[int, int]:
    """Read a .cube file and write a validated .a7lt file."""
    size, samples = parse_cube_text(src.read_text(encoding="utf-8"))
    blob = encode_a7lt(size, samples)
    verify_a7lt(blob)
    dst.write_bytes(blob)
    return size, len(blob)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input .cube file (or .a7lt with --verify)")
    parser.add_argument("output", type=Path, nargs="?", help="Output .a7lt file")
    parser.add_argument("--verify", action="store_true", help="Verify an existing .a7lt file")
    args = parser.parse_args(argv)

    if args.verify:
        size, data_bytes = verify_a7lt(args.input.read_bytes())
        print(f"A7LT_OK size={size} payload_bytes={data_bytes}")
        return 0

    if args.output is None:
        parser.error("output path is required unless --verify is passed")

    size, total_bytes = convert_cube_file(args.input, args.output)
    print(f"A7LT_WROTE size={size} total_bytes={total_bytes} out={args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
