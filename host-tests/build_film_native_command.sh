#!/bin/sh
# Build only: never connects to a camera, mounts scratch or issues a command.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:?output directory}
LIBS=${2:?original camera libc directory}
MONITOR=${3:-$LIBS/camera-mwf_monio.elf}
BOOT_STAGE=${FILM_NATIVE_BOOT_STAGE:-0}
mkdir -p "$OUT"
python3 - "$MONITOR" "$OUT" "$BOOT_STAGE" <<'PY'
from pathlib import Path
import hashlib,sys
monitor,out=map(Path,sys.argv[1:3])
stage=sys.argv[3]
assert stage in ('0','1'),'FILM_NATIVE_BOOT_STAGE must be 0 or 1'
expected='f2b25289d7d490f56993269cbf4891aa1db91451a67d0e8984c60aa67c25f1ef'
if not monitor.is_file():
    raise SystemExit('Pinned camera-mwf_monio.elf missing; supply exact original monitor locally')
data=monitor.read_bytes()
assert hashlib.sha256(data).hexdigest()==expected,'native monitor SHA-256 mismatch'
h=2166136261
for b in data:h=((h^b)*16777619)&0xffffffff
input_path='/tmp_bt/a6400-film-boot.copy' if stage=='1' else '/tmp_bt/a6400-film-job/input.jpg'
(out/'film_native_command_build.h').write_text(
 '#define FILM_NATIVE_MONITOR "/usr/bin/mwf_monio.elf"\n'
 f'#define FILM_NATIVE_INPUT "{input_path}"\n'
 '#define FILM_NATIVE_OUTPUT "/tmp_bt/a6400-film-job/output.jpg"\n'
 f'#define FILM_NATIVE_MONITOR_BYTES {len(data)}u\n'
 f'#define FILM_NATIVE_MONITOR_FNV 0x{h:08x}u\n'+
 ('#define FILM_NATIVE_BOOT_STAGE_PATH "A:/A6400/RUNTIME/BOOT0014.TAR"\n' if stage=='1' else ''))
print('PINNED_MONITOR',monitor,expected)
PY
CC=arm-linux-gnueabihf-gcc
COMMON="-march=armv7-a+fp -mfpu=vfpv3-d16 -mfloat-abi=hard -marm -Os -Wall -Wextra -Werror -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -DFILM_NATIVE_STARTUP_MS=600u -DFILM_NATIVE_QUIET_MS=120u -DFILM_NATIVE_STARTUP_MAX_MS=4000u"
$CC $COMMON -std=c11 -fPIE -I"$OUT" -c "$ROOT/camera/film_native_command.c" -o "$OUT/native-command.o"
$CC $COMMON -c "$ROOT/camera/film_settings_start.S" -o "$OUT/start.o"
$CC -mfloat-abi=hard -nostdlib -pie -Wl,-e,_start,-z,now,--dynamic-linker=/lib/ld-linux.so.3,-rpath-link,"$LIBS" \
 "$OUT/start.o" "$OUT/native-command.o" -Wl,--no-as-needed "$LIBS/libc-2.11.2.so" -lgcc \
 -o "$OUT/a6400-film-native-command.elf"
arm-linux-gnueabihf-readelf -lWdVs "$OUT/a6400-film-native-command.elf" > "$OUT/elf-compatibility.txt"
python3 - "$OUT/elf-compatibility.txt" <<'PY'
from pathlib import Path
import re,sys
s=Path(sys.argv[1]).read_text();v=set(re.findall(r'Name: GLIBC_([0-9.]+)',s))
assert 'Requesting program interpreter: /lib/ld-linux.so.3' in s
assert '[libc.so.6]' in s and v and all(tuple(map(int,x.split('.')))<=(2,11,2) for x in v),v
print('BUILD_ONLY: one bounded native command; camera libc',sorted(v))
PY
sha256sum "$OUT/a6400-film-native-command.elf"
