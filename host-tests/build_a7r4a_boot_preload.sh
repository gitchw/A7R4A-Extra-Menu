#!/bin/sh
# Build freestanding ARMv7 ld.so.preload constructor and read-only statvfs probe.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:?output directory}
LIBS=${2:-${A7R4A_LIBS_DIR:-$ROOT/../libs_112}}
TOOLCHAIN_BIN=${A7R4A_TOOLCHAIN_BIN:-$ROOT/../toolchain/bin}
if [ -d "$TOOLCHAIN_BIN" ]; then
    PATH="$TOOLCHAIN_BIN:$PATH"
    export PATH
fi

mkdir -p "$OUT"
CC=arm-linux-gnueabihf-gcc
$CC -march=armv7-a+fp -mfpu=vfpv3-d16 -mfloat-abi=hard -marm -Os \
    -Wall -Wextra -Werror -fPIC -ffreestanding -fno-builtin \
    -fomit-frame-pointer -fno-stack-protector -fno-unwind-tables \
    -fno-asynchronous-unwind-tables -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 \
    -nostdlib -shared -Wl,-z,now,--build-id=none \
    "$ROOT/camera/a7r4a_boot_preload.c" \
    -o "$OUT/a7r4a-boot.so"

arm-linux-gnueabihf-readelf -lWdVs "$OUT/a7r4a-boot.so" > "$OUT/a7r4a-boot-elf.txt"
python3 - "$OUT/a7r4a-boot-elf.txt" <<'PY'
from pathlib import Path
import re, sys

text = Path(sys.argv[1]).read_text()
assert 'INIT_ARRAY' in text, 'missing DT_INIT_ARRAY constructor table'
assert not re.search(r'\(NEEDED\)', text), f'unexpected DT_NEEDED dependency:\n{text}'
assert not re.search(r'^\s*[1-9]\d*:.*\bUND\b', text, re.M), f'unexpected undefined symbol:\n{text}'
print('BOOT_PRELOAD_ABI_OK freestanding zero-deps DT_INIT_ARRAY')
PY

COMMON_PIE="-march=armv7-a+fp -mfpu=vfpv3-d16 -mfloat-abi=hard -marm -Os -Wall -Wextra -Werror -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0"
$CC $COMMON_PIE -std=c11 -fPIE -c "$ROOT/camera/a7r4a_flash_free.c" -o "$OUT/flash-free.o"
$CC $COMMON_PIE -c "$ROOT/camera/film_settings_start.S" -o "$OUT/start.o"
$CC -mfloat-abi=hard -nostdlib -pie -Wl,-e,_start,-z,now,--dynamic-linker=/lib/ld-linux.so.3,-rpath-link,"$LIBS" \
    "$OUT/start.o" "$OUT/flash-free.o" -Wl,--no-as-needed "$LIBS/libc-2.11.2.so" -lgcc \
    -o "$OUT/a7r4a-flash-free.elf"
arm-linux-gnueabihf-readelf -lWdVs "$OUT/a7r4a-flash-free.elf" > "$OUT/flash-free-elf.txt"
python3 - "$OUT/flash-free-elf.txt" <<'PY'
from pathlib import Path
import re, sys

s = Path(sys.argv[1]).read_text()
v = set(re.findall(r'Name: GLIBC_([0-9.]+)', s))
assert 'Requesting program interpreter: /lib/ld-linux.so.3' in s
assert '[libc.so.6]' in s and v and all(tuple(map(int, x.split('.'))) <= (2, 11, 2) for x in v), v
print('FLASH_FREE_ABI_OK glibc', sorted(v))
PY
sha256sum "$OUT/a7r4a-boot.so" "$OUT/a7r4a-flash-free.elf"
