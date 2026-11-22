#!/bin/sh
# Build only: ASLR-safe ILCE-7RM4A (Ver. 1.12) Thumb-2 payload, guard, probe SO,
# and MTHR v2 .req files. Never installs or contacts a camera.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:?output directory}
LIBS=${2:?pulled ILCE-7RM4A 1.12 glibc directory}
MODULE_PATH=${A7R4A_UI_MODULE_PATH:-/tmp_bt/a7r4a-ui-probe.so}
mkdir -p "$OUT"
CC=arm-linux-gnueabihf-gcc
COMMON="-march=armv7-a+fp -mfpu=vfpv3-d16 -mfloat-abi=hard -Os -Wall -Wextra -Werror -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0"

$CC $COMMON -mthumb -nostdlib -static -fPIC -ffreestanding -fno-builtin -fomit-frame-pointer \
 "-DA7R4A_UI_MODULE_PATH=\"$MODULE_PATH\"" \
 -I"$ROOT/camera" \
 -Wl,-T,"$ROOT/camera/a7r4a_ui_payload.ld",-e,ui_trampoline_entry,--build-id=none \
 "$ROOT/camera/a7r4a_ui_trampoline.S" "$ROOT/camera/a7r4a_ui_load_payload.c" \
 -o "$OUT/a7r4a-payload.elf"
arm-linux-gnueabihf-objcopy -O binary -j .text "$OUT/a7r4a-payload.elf" "$OUT/a7r4a-payload.bin"
arm-linux-gnueabihf-readelf -rWS "$OUT/a7r4a-payload.elf" > "$OUT/payload-elf.txt"

python3 - "$OUT/payload-elf.txt" "$OUT/a7r4a-payload.bin" <<'PY'
from pathlib import Path
import re, sys
info = Path(sys.argv[1]).read_text()
payload = Path(sys.argv[2]).read_bytes()
assert 'There are no relocations in this file.' in info, info
assert not re.search(r'\s\.(data|bss|got)\s', info), info
assert 24 <= len(payload) <= 1700 and len(payload) % 4 == 0, len(payload)
assert payload[-24:-20] == b'4R7A', payload[-24:].hex()
assert payload[-20:] == b'\x00' * 20, payload[-20:].hex()
print(f'PAYLOAD_OK bytes={len(payload)} (<=1700) zero_relocs=1')
PY

$CC $COMMON -marm -fPIC -std=c11 -I"$ROOT/camera" -c "$ROOT/camera/a7r4a_ui_probe.c" -o "$OUT/probe.o"
$CC -mfloat-abi=hard -nostdlib -shared -Wl,--no-undefined -Wl,-rpath-link,"$LIBS" \
 "$OUT/probe.o" -Wl,--no-as-needed "$LIBS/libdl-2.11.2.so" "$LIBS/libc-2.11.2.so" -lgcc \
 -o "$OUT/a7r4a-ui-probe.so"

$CC $COMMON -marm -fPIE -std=c11 -I"$ROOT/camera" -c "$ROOT/camera/a7r4a_ui_load_guard.c" -o "$OUT/guard.o"
$CC $COMMON -marm -c "$ROOT/camera/film_settings_start.S" -o "$OUT/start.o"
$CC -mfloat-abi=hard -nostdlib -pie -Wl,-e,_start,-z,now,--dynamic-linker=/lib/ld-linux.so.3,-rpath-link,"$LIBS" \
 "$OUT/start.o" "$OUT/guard.o" -Wl,--no-as-needed "$LIBS/libc-2.11.2.so" -lgcc \
 -o "$OUT/a7r4a-ui-load-guard.elf"

for bin in a7r4a-ui-probe.so a7r4a-ui-load-guard.elf; do
 arm-linux-gnueabihf-readelf -lWdVs "$OUT/$bin" > "$OUT/$bin.readelf.txt"
done

python3 - "$OUT" <<'PY'
from pathlib import Path
import re, sys
out = Path(sys.argv[1])
for name, need_dl in (('a7r4a-ui-probe.so', True), ('a7r4a-ui-load-guard.elf', False)):
    s = (out / f'{name}.readelf.txt').read_text()
    versions = set(re.findall(r'Name: GLIBC_([0-9.]+)', s))
    assert versions and all(tuple(map(int, v.split('.'))) <= (2, 11, 2) for v in versions), (name, versions)
    assert '[libc.so.6]' in s, name
    if need_dl:
        assert '[libdl.so.2]' in s, name
    else:
        assert 'Requesting program interpreter: /lib/ld-linux.so.3' in s, name
    print(f'ABI_OK {name} glibc={sorted(versions)}')
PY

for op in check install remove; do
 python3 "$ROOT/tools/pack_req.py" \
  --payload "$OUT/a7r4a-payload.bin" --op "$op" \
  --version 2 --entry 1 --state-size 1184 --hook 0x00625ee8 --max-code-bytes 1700 \
  --out "$OUT/a7r4a-$op.req"
done

sha256sum "$OUT/a7r4a-payload.bin" "$OUT/a7r4a-ui-probe.so" "$OUT/a7r4a-ui-load-guard.elf" \
 "$OUT/a7r4a-check.req" "$OUT/a7r4a-install.req" "$OUT/a7r4a-remove.req"
