#!/bin/sh
# Build only: cross-compile remote shutter / astro interval / focus bracket CLI
# for ILCE-7RM4A (Ver. 1.12). Never connects to hardware.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:?output directory}
LIBS=${2:?original camera libc directory}
mkdir -p "$OUT"
test -f "$LIBS/libc-2.11.2.so" || { echo "Missing $LIBS/libc-2.11.2.so" >&2; exit 2; }
test -f "$LIBS/ld-linux.so.3" || { echo "Missing $LIBS/ld-linux.so.3" >&2; exit 2; }

CC=arm-linux-gnueabihf-gcc
COMMON="-march=armv7-a+fp -mfpu=vfpv3-d16 -mfloat-abi=hard -marm -Os -Wall -Wextra -Werror -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0"
$CC $COMMON -std=c11 -fPIE -DA7R4A_FIXED_SNDCMD=1 -c "$ROOT/camera/a7r4a_remote_shoot.c" -o "$OUT/a7r4a-remote-shoot.o"
$CC $COMMON -c "$ROOT/camera/film_settings_start.S" -o "$OUT/start.o"
$CC -mfloat-abi=hard -nostdlib -pie -Wl,-e,_start,-z,now,--dynamic-linker=/lib/ld-linux.so.3,-rpath-link,"$LIBS" \
 "$OUT/start.o" "$OUT/a7r4a-remote-shoot.o" -Wl,--no-as-needed "$LIBS/libc-2.11.2.so" -lgcc \
 -o "$OUT/a7r4a-remote-shoot.elf"

arm-linux-gnueabihf-readelf -lWdVs "$OUT/a7r4a-remote-shoot.elf" > "$OUT/elf-compatibility.txt"
python3 - "$OUT/elf-compatibility.txt" <<'PY'
from pathlib import Path
import re, sys

s = Path(sys.argv[1]).read_text()
versions = set(re.findall(r'Name: GLIBC_([0-9.]+)', s))
assert 'Requesting program interpreter: /lib/ld-linux.so.3' in s
assert '[libc.so.6]' in s and versions
assert all(tuple(map(int, v.split('.'))) <= (2, 11, 2) for v in versions), versions
assert not re.search(r'\bUND\b.*\b(stat|fstat|lstat)(?:64)?@', s), 'Unexpected stat-family import'
print('BUILD_ONLY: remote shoot CLI; camera libc', sorted(versions))
PY
sha256sum "$OUT/a7r4a-remote-shoot.elf"
