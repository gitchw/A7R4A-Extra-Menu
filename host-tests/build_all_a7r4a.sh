#!/bin/sh
# Build all ILCE-7RM4A (Ver. 1.12) Phase 4 deliverables into <out_dir>.
# Build-only: never connects to a camera or stages onto hardware.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:?output directory}
LIBS=${2:-${A7R4A_LIBS_DIR:-$ROOT/../libs_112}}
LIBJPEG=${3:-${A7R4A_LIBJPEG_DIR:-$ROOT/../third_party/libjpeg-arm}}
TOOLCHAIN_BIN=${A7R4A_TOOLCHAIN_BIN:-$ROOT/../toolchain/bin}
if [ -d "$TOOLCHAIN_BIN" ]; then
 PATH="$TOOLCHAIN_BIN:$PATH"
 export PATH
fi

UI_RUNTIME_PATH=${UI_MODULE_PATH:-/tmp_bt/a7r4a-film-ui-clite.so}
UI_INSTANCE_SUFFIX=${A6400_UI_INSTANCE_SUFFIX:-_CLITE}
TRIAL_MS=${A6400_TRIAL_MS:-0}
DISPLAY_MS=${A6400_DISPLAY_MS:-20000}
COMBO_DIAGNOSTIC=${A6400_COMBO_DIAGNOSTIC:-1}
SETTINGS_DIAGNOSTIC=${A6400_SETTINGS_DIAGNOSTIC:-1}
PERSISTENT=${FILM_SETTINGS_PERSISTENT:-1}
CAPTURE_PERSISTENT=${FILM_CAPTURE_PERSISTENT:-1}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/a7r4a-build-all.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$OUT" "$OUT/ui"

# 1. Phase 4 ASLR-safe loader guard, read-only UI probe SO, payload.bin, and .req files
sh "$ROOT/host-tests/build_a7r4a_ui_loader.sh" "$WORK/loader" "$LIBS"

# 2. Persistent UI overlay SO + UI payload.bin + UI .req files
A6400_TRIAL_MS="$TRIAL_MS" A6400_DISPLAY_MS="$DISPLAY_MS" \
 A6400_COMBO_DIAGNOSTIC="$COMBO_DIAGNOSTIC" A6400_SETTINGS_DIAGNOSTIC="$SETTINGS_DIAGNOSTIC" \
 A6400_UI_INSTANCE_SUFFIX="$UI_INSTANCE_SUFFIX" UI_MODULE_PATH="$UI_RUNTIME_PATH" \
 sh "$ROOT/host-tests/build_ui_trial3.sh" "$WORK/ui" "$LIBS"
for op in check install remove; do
 python3 "$ROOT/tools/pack_req.py" \
  --payload "$WORK/ui/payload.bin" --op "$op" \
  --version 2 --entry 1 --state-size 1184 --hook 0x00625ee8 --max-code-bytes 1700 \
  --out "$WORK/ui/a7r4a-ui-$op.req"
done

# 3. Menu snapshot reader CLI & UI stop CLI pinned to exact UI SO
FILM_SETTINGS_PERSISTENT="$PERSISTENT" UI_MODULE_PATH="$UI_RUNTIME_PATH" \
 sh "$ROOT/host-tests/build_film_menu_job_cli.sh" "$WORK/menu" "$LIBS" "$WORK/ui"
UI_MODULE_PATH="$UI_RUNTIME_PATH" \
 sh "$ROOT/host-tests/build_film_ui_stop_cli.sh" "$WORK/stop" "$LIBS" "$WORK/ui"

# 4. Bounded capture observer & native MWF command client
sh "$ROOT/host-tests/build_native_capture_probe.sh" "$WORK/probe" "$LIBS"
sh "$ROOT/host-tests/build_film_native_command.sh" "$WORK/native" "$LIBS"

# 5. Streaming 61MP JPEG film worker & clean-room remote shoot CLI
sh "$ROOT/host-tests/build_a7r4a_film_job.sh" "$WORK/worker" "$LIBS" "$LIBJPEG"
sh "$ROOT/host-tests/build_a7r4a_remote_shoot.sh" "$WORK/remote" "$LIBS"
sh "$ROOT/host-tests/build_a7r4a_boot_preload.sh" "$WORK/boot" "$LIBS"

# 6. Pin SHA-256s into film_ram_job.sh and build film_capture_daemon.elf
python3 - "$ROOT/camera/film_ram_job.sh" \
 "$WORK/native/a6400-film-native-command.elf" \
 "$WORK/worker/a7r4a-film-job.elf" \
 "$WORK/menu/a6400-film-menu-job.elf" \
 "$WORK/film_ram_job.sh" <<'PY'
from pathlib import Path
import hashlib, re, sys
tpl, native, worker, settings, out = map(Path, sys.argv[1:6])
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
text = tpl.read_text()
text = text.replace('__NATIVE_SHA__', sha(native))
text = text.replace('__WORKER_SHA__', sha(worker))
text = text.replace('__SETTINGS_SHA__', sha(settings))
assert not re.search(r'__[A-Z_]+__', text), 'unfilled placeholder in film_ram_job.sh'
out.write_text(text)
out.chmod(0o755)
PY
sh -n "$WORK/film_ram_job.sh"

FILM_CAPTURE_PERSISTENT="$CAPTURE_PERSISTENT" FILM_UI_DIR="$WORK/ui" UI_MODULE_PATH="$UI_RUNTIME_PATH" \
 sh "$ROOT/host-tests/build_film_capture_daemon.sh" \
 "$WORK/daemon" "$LIBS" "$WORK/film_ram_job.sh" "$WORK/menu/a6400-film-menu-job.elf"

# 7. Copy clean deliverables to OUT
cp "$WORK/loader/a7r4a-ui-load-guard.elf" "$OUT/a7r4a-ui-load-guard.elf"
cp "$WORK/loader/a7r4a-ui-probe.so" "$OUT/a7r4a-ui-probe.so"
cp "$WORK/loader/a7r4a-payload.bin" "$OUT/a7r4a-payload.bin"
cp "$WORK/loader/a7r4a-check.req" "$OUT/a7r4a-check.req"
cp "$WORK/loader/a7r4a-install.req" "$OUT/a7r4a-install.req"
cp "$WORK/loader/a7r4a-remove.req" "$OUT/a7r4a-remove.req"

cp "$WORK/ui/a6400-film-ui-v3.so" "$OUT/a6400-film-ui-v3.so"
cp "$WORK/ui/payload.bin" "$OUT/ui/payload.bin"
cp "$WORK/ui/a7r4a-ui-check.req" "$OUT/ui/a7r4a-ui-check.req"
cp "$WORK/ui/a7r4a-ui-install.req" "$OUT/ui/a7r4a-ui-install.req"
cp "$WORK/ui/a7r4a-ui-remove.req" "$OUT/ui/a7r4a-ui-remove.req"

cp "$WORK/menu/a6400-film-menu-job.elf" "$OUT/a6400-film-menu-job.elf"
cp "$WORK/menu/pinned-ui.json" "$OUT/pinned-ui.json"
cp "$WORK/stop/a6400-film-ui-stop.elf" "$OUT/a6400-film-ui-stop.elf"
cp "$WORK/stop/pinned-ui-stop.json" "$OUT/pinned-ui-stop.json"

cp "$WORK/probe/a6400-capture-probe-v4.elf" "$OUT/a6400-capture-probe-v4.elf"
cp "$WORK/native/a6400-film-native-command.elf" "$OUT/a6400-film-native-command.elf"
cp "$WORK/worker/a7r4a-film-job.elf" "$OUT/a7r4a-film-job.elf"
cp "$WORK/remote/a7r4a-remote-shoot.elf" "$OUT/a7r4a-remote-shoot.elf"
cp "$WORK/boot/a7r4a-boot.so" "$OUT/a7r4a-boot.so"
cp "$WORK/boot/a7r4a-flash-free.elf" "$OUT/a7r4a-flash-free.elf"
cp "$WORK/film_ram_job.sh" "$OUT/film_ram_job.sh"
cp "$WORK/daemon/a6400-film-capture-daemon.elf" "$OUT/a6400-film-capture-daemon.elf"
cp "$WORK/daemon/pinned-inputs.json" "$OUT/pinned-inputs.json"

chmod 755 "$OUT"/*.elf "$OUT"/*.so "$OUT/film_ram_job.sh"
chmod 644 "$OUT"/*.req "$OUT/ui"/*.req "$OUT"/*.json "$OUT/a7r4a-payload.bin" "$OUT/ui/payload.bin"

(
 cd "$OUT"
 sha256sum \
  a7r4a-ui-load-guard.elf \
  a7r4a-ui-probe.so \
  a7r4a-payload.bin \
  a7r4a-check.req \
  a7r4a-install.req \
  a7r4a-remove.req \
  a6400-film-ui-v3.so \
  ui/payload.bin \
  ui/a7r4a-ui-check.req \
  ui/a7r4a-ui-install.req \
  ui/a7r4a-ui-remove.req \
  a6400-film-menu-job.elf \
  a6400-film-ui-stop.elf \
  a6400-capture-probe-v4.elf \
  a6400-film-native-command.elf \
  a7r4a-film-job.elf \
  a7r4a-remote-shoot.elf \
  a7r4a-boot.so \
  a7r4a-flash-free.elf \
  film_ram_job.sh \
  a6400-film-capture-daemon.elf > SHA256SUMS
)
cat "$OUT/SHA256SUMS"
echo "A7R4A_BUILD_ALL_OK out=$OUT"
