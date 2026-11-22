#!/bin/sh
# Build only: never installs, starts a monitor, or dispatches a camera job.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:?output directory}
LIBS=${2:?original camera libc directory}
SCRIPT=${3:?final pinned camera RAM job script}
SETTINGS=${4:?exact camera settings CLI ELF}
MONITOR=${5:-$LIBS/camera-mpr_monio.elf}
ACTIVE=${FILM_CAPTURE_ACTIVE_MS:-600000}
PERSISTENT=${FILM_CAPTURE_PERSISTENT:-0}
JOB_TIMEOUT=${FILM_CAPTURE_JOB_TIMEOUT_MS:-600000}
BASELINE_TIMEOUT=${FILM_CAPTURE_BASELINE_TIMEOUT_MS:-600000}
mkdir -p "$OUT"
python3 - "$MONITOR" "$SCRIPT" "$SETTINGS" "$OUT" "$ACTIVE" "$PERSISTENT" "$JOB_TIMEOUT" "$BASELINE_TIMEOUT" <<'PY'
from pathlib import Path
import hashlib,json,re,sys
monitor,script,settings,out=map(Path,sys.argv[1:5])
active,persistent,job_timeout,baseline_timeout=sys.argv[5:]
assert re.fullmatch(r'[1-9][0-9]{0,7}',active) and 1000<=int(active)<=86400000,'active_ms range'
assert persistent in ('0','1'),'persistent must be 0 or 1'
assert re.fullmatch(r'[1-9][0-9]{0,7}',job_timeout) and 1000<=int(job_timeout)<=86400000,'job timeout range'
assert re.fullmatch(r'[1-9][0-9]{0,7}',baseline_timeout) and 1000<=int(baseline_timeout)<=86400000,'baseline timeout range'
data=monitor.read_bytes()
assert hashlib.sha256(data).hexdigest()=='0d6e63cfb9ba9db035e7a1113eb2179037a41ef9226293771253298738743817','MPR monitor pin'
def fnv(data):
    h=2166136261
    for b in data:h=((h^b)*16777619)&0xffffffff
    return h
(out/'capture_probe_build.h').write_text(
 '#define CAPTURE_MONITOR "/usr/bin/mpr_monio.elf"\n'
 '#define CAPTURE_LOCK "/tmp_bt/a6400-capture-v1.lock"\n'
 '#define CAPTURE_STOP_FILE "/tmp_bt/a6400-film-capture.stop"\n'
 f'#define CAPTURE_MONITOR_BYTES {len(data)}u\n'
 f'#define CAPTURE_MONITOR_FNV 0x{fnv(data):08x}u\n')
text=[];pins={}
for macro,path,runtime in (
    ('SCRIPT',script,'/tmp_bt/a6400-film-ram-job.sh'),
    ('SETTINGS',settings,'/tmp_bt/a6400-film-menu-job.elf')):
    data=path.read_bytes()
    if macro=='SCRIPT':
        assert data.startswith(b'#!/bin/sh\n') and not re.search(rb'__[A-Z_]+__',data),'final RAM script placeholders'
    else:assert data.startswith(b'\x7fELF'),'settings must be target ELF'
    text += [f'#define FILM_CAPTURE_{macro} "{runtime}"\n',
             f'#define FILM_CAPTURE_{macro}_BYTES {len(data)}u\n',
             f'#define FILM_CAPTURE_{macro}_FNV 0x{fnv(data):08x}u\n']
    pins[macro.lower()]={'source':str(path.resolve()),'runtime':runtime,'bytes':len(data),
                         'fnv':f'{fnv(data):08x}','sha256':hashlib.sha256(data).hexdigest()}
for macro,runtime in (
    ('MANIFEST','/tmp_bt/a6400-film-capture.manifest'),
    ('INFLIGHT','/tmp_bt/a6400-film-capture.inflight'),
    ('JOB_LOG','/tmp_bt/a6400-film-capture.job.log'),
    ('RUNTIME_LOG','/tmp_bt/a6400-film-capture.runtime.log'),
    ('JOB_LOCK','/tmp_bt/a6400-film-job.lock')):
    text += [f'#define FILM_CAPTURE_{macro} "{runtime}"\n']
text += [f'#define FILM_CAPTURE_ACTIVE_MS {active}u\n',
         f'#define FILM_CAPTURE_PERSISTENT {persistent}\n',
         f'#define FILM_CAPTURE_JOB_TIMEOUT_MS {job_timeout}u\n',
         f'#define FILM_CAPTURE_BASELINE_TIMEOUT_MS {baseline_timeout}u\n']
import os
ui_dir = os.environ.get('FILM_UI_DIR', '')
if ui_dir:
    syms = (Path(ui_dir) / 'symbols.txt').read_text()
    tm = sorted(set(re.findall(r'^\s*\d+:\s+([0-9a-f]+)\s+\d+\s+OBJECT\s+GLOBAL\s+DEFAULT\s+\d+\s+a6400_ui_toast_v1$', syms, re.M)))
    assert len(tm) == 1, f'expected unique a6400_ui_toast_v1 symbol, got {tm}'
    ui_mod = os.environ.get('UI_MODULE_PATH', '/tmp_bt/a7r4a-film-ui-clite.so')
    assert ui_mod.startswith('/') and '"' not in ui_mod and '\n' not in ui_mod, f'invalid UI_MODULE_PATH: {ui_mod!r}'
    text += [
        f'#define FILM_DAEMON_UI_MODULE "{ui_mod}"\n',
        f'#define FILM_DAEMON_TOAST_RVA 0x{int(tm[0], 16):x}u\n',
    ]
    hm = sorted(set(re.findall(r'^\s*\d+:\s+([0-9a-f]+)\s+\d+\s+OBJECT\s+GLOBAL\s+DEFAULT\s+\d+\s+a6400_ui_hires_v1$', syms, re.M)))
    sm = sorted(set(re.findall(r'^\s*\d+:\s+([0-9a-f]+)\s+\d+\s+OBJECT\s+GLOBAL\s+DEFAULT\s+\d+\s+a6400_ui_status_v1$', syms, re.M)))
    if len(hm) == 1:
        text += [f'#define FILM_DAEMON_HIRES_RVA 0x{int(hm[0], 16):x}u\n']
    if len(sm) == 1:
        text += [f'#define FILM_DAEMON_STATUS_RVA 0x{int(sm[0], 16):x}u\n']
if persistent=='1':
    text += ['#define MAX_FILES 1024u\n',
             '#define CAPTURE_WAIT_MS 4000u\n',
             '#define CAPTURE_BASELINE_INTERVAL_MS 750u\n',
             '#define CAPTURE_STARTUP_MS 300u\n',
             '#define CAPTURE_QUIET_MS 80u\n',
             '#define CAPTURE_STARTUP_MAX_MS 4000u\n',
             '#define CAPTURE_INTERVAL_MS 100u\n',
             '#define CAPTURE_STABLE_MS 800u\n',
             '#define CAPTURE_COMMAND_MS 12000u\n',
             '#define SCAN_MS 45000u\n',
             '#define FILM_CAPTURE_LATEST_DIR_ONLY 1\n',
             '#define FILM_CAPTURE_PROC_TRIGGER 1\n',
             '#define FILM_CAPTURE_PROC_TICK_MS 100u\n',
             '#define FILM_CAPTURE_PROC_QUIET_TICKS 2u\n',
             '#define FILM_CAPTURE_IDLE_HEARTBEAT_MS 8000u\n']
(out/'film_capture_daemon_build.h').write_text(''.join(text))
pins.update(active_ms=int(active),persistent=bool(int(persistent)),job_timeout_ms=int(job_timeout),
            baseline_timeout_ms=int(baseline_timeout),
            monitor_sha256='0d6e63cfb9ba9db035e7a1113eb2179037a41ef9226293771253298738743817',
            runtime='/tmp_bt/a6400-film-capture-daemon.elf',arguments=['watch'],
            runtime_log='/tmp_bt/a6400-film-capture.runtime.log',
            policy='stable metadata heuristic; exact live menu gate; serialized native jobs; uncertain stops dispatch')
(out/'pinned-inputs.json').write_text(json.dumps(pins,indent=2)+'\n')
print('PINNED_INPUTS',json.dumps(pins,sort_keys=True))
PY
CC=arm-linux-gnueabihf-gcc
COMMON="-march=armv7-a+fp -mfpu=vfpv3-d16 -mfloat-abi=hard -marm -Os -Wall -Wextra -Werror -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0"
$CC $COMMON -std=c11 -fPIE -I"$OUT" -c "$ROOT/camera/film_capture_daemon.c" -o "$OUT/capture-daemon.o"
$CC $COMMON -std=c11 -fPIE -I"$ROOT/camera" -c "$ROOT/camera/film_runtime_log.c" -o "$OUT/runtime-log.o"
$CC $COMMON -c "$ROOT/camera/film_settings_start.S" -o "$OUT/start.o"
$CC -mfloat-abi=hard -nostdlib -pie -Wl,-e,_start,-z,now,--dynamic-linker=/lib/ld-linux.so.3,-rpath-link,"$LIBS" \
 "$OUT/start.o" "$OUT/capture-daemon.o" "$OUT/runtime-log.o" -Wl,--no-as-needed "$LIBS/libc-2.11.2.so" -lgcc \
 -o "$OUT/a6400-film-capture-daemon.elf"
arm-linux-gnueabihf-readelf -lWdVs "$OUT/a6400-film-capture-daemon.elf" > "$OUT/elf-compatibility.txt"
python3 - "$OUT/elf-compatibility.txt" <<'PY'
from pathlib import Path
import re,sys
s=Path(sys.argv[1]).read_text();versions=set(re.findall(r'Name: GLIBC_([0-9.]+)',s))
assert 'Requesting program interpreter: /lib/ld-linux.so.3' in s
assert '[libc.so.6]' in s and versions
assert all(tuple(map(int,v.split('.')))<=(2,11,2) for v in versions),versions
print('BUILD_ONLY: serialized menu-controlled capture daemon; camera libc',sorted(versions))
PY
python3 - "$OUT" "$ROOT/camera/film_capture_daemon.c" "$ROOT/camera/native_capture_probe.c" <<'PY'
from pathlib import Path
import hashlib,json,sys
out,source,scanner=map(Path,sys.argv[1:])
path=out/'pinned-inputs.json';pins=json.loads(path.read_text())
data=(out/'a6400-film-capture-daemon.elf').read_bytes()
pins.update(daemon_sha256=hashlib.sha256(data).hexdigest(),daemon_bytes=len(data),
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            scanner_source_sha256=hashlib.sha256(scanner.read_bytes()).hexdigest())
path.write_text(json.dumps(pins,indent=2)+'\n')
PY
sha256sum "$OUT/a6400-film-capture-daemon.elf"
