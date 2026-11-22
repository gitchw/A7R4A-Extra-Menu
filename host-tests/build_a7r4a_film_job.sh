#!/bin/sh
# Build only: cross-compile streaming 61MP JPEG worker for ILCE-7RM4A (Ver. 1.12).
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:?output directory}
LIBS=${2:?original camera libc directory}
LIBJPEG=${3:-${A7R4A_LIBJPEG_DIR:-$ROOT/../third_party/libjpeg-arm}}
mkdir -p "$OUT"
test -f "$LIBS/libc-2.11.2.so" || { echo "Missing $LIBS/libc-2.11.2.so" >&2; exit 2; }
test -f "$LIBS/ld-linux.so.3" || { echo "Missing $LIBS/ld-linux.so.3" >&2; exit 2; }
test -f "$LIBJPEG/lib/libjpeg.a" && test -f "$LIBJPEG/include/jpeglib.h" || {
    echo "Missing static libjpeg at $LIBJPEG" >&2; exit 2
}

CC=arm-linux-gnueabihf-gcc
COMMON="-march=armv7-a+fp -mfpu=vfpv3-d16 -mfloat-abi=hard -marm -O3 -Wall -Wextra -Werror -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0"
$CC $COMMON -std=c11 -fPIE -I"$LIBJPEG/include" -c "$ROOT/camera/a7r4a_film_job.c" -o "$OUT/a7r4a-film-job.o"
$CC $COMMON -c "$ROOT/camera/film_settings_start.S" -o "$OUT/start.o"
$CC -mfloat-abi=hard -nostdlib -pie -Wl,-e,_start,-z,now,--dynamic-linker=/lib/ld-linux.so.3,-rpath-link,"$LIBS" \
 "$OUT/start.o" "$OUT/a7r4a-film-job.o" "$LIBJPEG/lib/libjpeg.a" -Wl,--no-as-needed "$LIBS/libc-2.11.2.so" -lgcc \
 -o "$OUT/a7r4a-film-job.elf"

arm-linux-gnueabihf-readelf -lWdVs "$OUT/a7r4a-film-job.elf" > "$OUT/elf-compatibility.txt"
python3 - "$OUT/elf-compatibility.txt" <<'PY'
from pathlib import Path
import re, sys

s = Path(sys.argv[1]).read_text()
versions = set(re.findall(r'Name: GLIBC_([0-9.]+)', s))
assert 'Requesting program interpreter: /lib/ld-linux.so.3' in s
assert '[libc.so.6]' in s and versions
assert all(tuple(map(int, v.split('.'))) <= (2, 11, 2) for v in versions), versions
assert not re.search(r'\bUND\b.*\b(stat|fstat|lstat)(?:64)?@', s), 'Unexpected stat-family import'
print('BUILD_ONLY: streaming 61MP film worker; camera libc', sorted(versions))
PY
sha256sum "$OUT/a7r4a-film-job.elf"
