#!/bin/bash
# ILCE-7RM4A (Ver. 1.12) Standalone Cold-Boot Installer / Uninstaller
#
# Uses Sony's Backup-gated Ir 0a0_geo_res hook (zero /etc/ld.so.preload writes):
#   - /tmp/gpslogdiag.so         (freestanding zero-deps constructor, SCHED_OTHER/FD reset)
#   - /etc/a7r4a_film/boot.sh    (settles past ssboot SD init, unpacks RAM runtime, arms UI & daemon)
#   - /etc/a7r4a_film/MANIFEST   (exact SHA-256 manifest)
#   - /etc/a7r4a_film/*.gz       (compressed A7R4A runtime staged to /tmp_bt RAM on wakeup)
#   - Backup 0x00a10011=01, 0x00a1000b=0000 (armed) / 00, 0200 (stock)
#
# Escape hatches:
#   - Touch /tmp/disable_hook OR /etc/a7r4a_film/DISABLE to bypass boot hook.
#   - Run `./autoboot_flash.sh uninstall` to revert Backup & remove all /tmp + /etc files.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -n "${A7R4A_DIST_DIR:-}" ]; then
    DIST_DIR="$A7R4A_DIST_DIR"
elif [ -d "$REPO_DIR/dist_a7r4a" ]; then
    DIST_DIR="$REPO_DIR/dist_a7r4a"
else
    DIST_DIR="$REPO_DIR/../dist_a7r4a"
fi
if [ -z "${PMCA_DIR:-}" ]; then
    if [ -x "$REPO_DIR/Sony-PMCA-RE/.venv/bin/python" ]; then
        PMCA_DIR="$REPO_DIR/Sony-PMCA-RE"
    elif [ -x "$REPO_DIR/../Sony-PMCA-RE/.venv/bin/python" ]; then
        PMCA_DIR="$REPO_DIR/../Sony-PMCA-RE"
    else
        PMCA_DIR="$REPO_DIR/tools/Sony-PMCA-RE"
    fi
fi
PMCA_PY="${PMCA_PY:-$PMCA_DIR/.venv/bin/python}"
LOG_DIR="${A7R4A_PHASE6_LOGS:-$REPO_DIR/probe_logs/phase6}"
BOOT_SETTLE_S="${A7R4A_BOOT_SETTLE_S:-20}"
BOOT_STAGE="${A7R4A_BOOT_STAGE:-full}"

DRY_RUN=0
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        *) ARGS+=("$arg") ;;
    esac
done
SUBCMD="${ARGS[0]:-}"

usage() {
    cat <<EOF
Usage:
  $0 [--dry-run] {status|install|uninstall}

Subcommands:
  status     Inspect Backup (0x00a10011/0x00a1000b), /tmp/gpslogdiag.so, /etc/a7r4a_film, flash free space, and boot/daemon logs
  install    Preflight free flash (>=1024 KiB), stage /tmp/gpslogdiag.so & /etc/a7r4a_film/* (SHA-256 verified), arm Backup trigger
  uninstall  Revert Backup (00 / 0200), remove /tmp/gpslogdiag.so, /etc/a7r4a-boot.so, /etc/ld.so.preload, /etc/a7r4a_film, /tmp/disable_hook
EOF
}

if [ $# -lt 1 ] || [ "${#ARGS[@]}" -ne 1 ]; then
    usage
    exit 2
fi

case "$SUBCMD" in
    status|install|uninstall) ;;
    *) usage; exit 2 ;;
esac

case "$BOOT_SETTLE_S" in
    ''|*[!0-9]*) echo "FATAL: A7R4A_BOOT_SETTLE_S must be an integer 0..120 (got $BOOT_SETTLE_S)" >&2; exit 2 ;;
esac
if [ "$BOOT_SETTLE_S" -gt 120 ]; then
    echo "FATAL: A7R4A_BOOT_SETTLE_S must be <= 120 (got $BOOT_SETTLE_S)" >&2
    exit 2
fi
case "$BOOT_STAGE" in
    stage1|stage2|full) ;;
    *) echo "FATAL: A7R4A_BOOT_STAGE must be stage1, stage2, or full (got $BOOT_STAGE)" >&2; exit 2 ;;
esac

verify_dist() {
    [ -d "$DIST_DIR" ] || { echo "FATAL: missing $DIST_DIR" >&2; exit 2; }
    [ -f "$DIST_DIR/SHA256SUMS" ] || { echo "FATAL: missing $DIST_DIR/SHA256SUMS" >&2; exit 2; }
    local required=(
        a7r4a-boot.so
        a7r4a-flash-free.elf
        a7r4a-ui-load-guard.elf
        ui/a7r4a-ui-check.req
        ui/a7r4a-ui-install.req
        ui/a7r4a-ui-remove.req
        a6400-film-ui-v3.so
        a6400-film-menu-job.elf
        a6400-film-ui-stop.elf
        a6400-film-native-command.elf
        a7r4a-film-job.elf
        film_ram_job.sh
        a6400-film-capture-daemon.elf
        a7r4a-remote-shoot.elf
    )
    (
        cd "$DIST_DIR"
        for f in "${required[@]}"; do
            [ -f "$f" ] || { echo "FATAL: missing $DIST_DIR/$f" >&2; exit 2; }
            grep -q "  $f\$" SHA256SUMS || { echo "FATAL: $f missing from SHA256SUMS" >&2; exit 2; }
        done
        if command -v sha256sum >/dev/null 2>&1; then
            sha256sum -c SHA256SUMS >/dev/null
        else
            shasum -a 256 -c SHA256SUMS >/dev/null
        fi
    )
    echo "DIST_OK boot+runtime artifacts verified against SHA256SUMS"
}

run_pmca_python() {
    if [ "${A7R4A_USE_SUDO:-0}" = 1 ]; then
        sudo -n "$PMCA_PY" "$@"
    else
        "$PMCA_PY" "$@"
    fi
}

unmount_sony_msc_host() {
    if [ "$(uname -s)" = "Darwin" ]; then
        if ioreg -p IOService -l -w 0 2>/dev/null | grep -q "Sony"; then
            local devs
            devs=$(diskutil list external 2>/dev/null | awk '/^\/dev\/disk[0-9]+/ { print $1 }')
            for d in $devs; do
                if diskutil info "$d" 2>/dev/null | grep -qiE "Sony|ILCE|DSC|PMHOME"; then
                    diskutil unmountDisk force "$d" >/dev/null 2>&1 || true
                fi
            done
        fi
    fi
}

if [ "$DRY_RUN" = 1 ]; then
    case "$SUBCMD" in
        status)
            cat <<EOF
=== DRY-RUN: autoboot status (gpslogdiag + Backup) ===
  1. Read Backup 0x00a10011 & 0x00a1000b (terminal OFF)
  2. Mount /tmp_bt tmpfs if unmounted, stage .ff.elf -> check /etc free space
  3. Inspect /tmp/gpslogdiag.so, /etc/a7r4a-boot.so, /etc/ld.so.preload, /etc/a7r4a_film, /tmp/disable_hook
  4. Inspect im.elf maps, capture daemon PID, /tmp_bt/a7r4a-boot.log, daemon.stdout.log, runtime.log
EOF
            ;;
        install)
            verify_dist
            cat <<EOF
=== DRY-RUN: autoboot install (ILCE-7RM4A 1.12 /tmp/gpslogdiag.so + Backup) ===
  1. Verify no foreign /etc/ld.so.preload; remove legacy /etc/ld.so.preload & /etc/a7r4a-boot.so
  2. Ensure /tmp_bt tmpfs, check /etc free space >= 1024 KiB via a7r4a-flash-free.elf
  3. Stage a7r4a-boot.so -> /tmp/gpslogdiag.so + /etc/a7r4a_film/{boot.sh,MANIFEST,12x .gz} (60-char b64, SHA-256 verified)
  4. Run sh -n /etc/a7r4a_film/boot.sh, remove /tmp/disable_hook & stale traces, sync
  5. Set terminal OFF, write Backup 0x00a10011=01 & 0x00a1000b=0000, saveBackup(0x0a1) + saveBackup(0), readback verify
  6. Safe settle in boot.sh: A7R4A_BOOT_SETTLE_S=$BOOT_SETTLE_S A7R4A_BOOT_STAGE=$BOOT_STAGE + thread-count stability
EOF
            ;;
        uninstall)
            cat <<EOF
=== DRY-RUN: autoboot uninstall ===
  1. Terminal OFF: revert Backup 0x00a10011=00 & 0x00a1000b=0200, saveBackup(0x0a1) + saveBackup(0), readback verify
  2. Terminal ON: rm -rf /etc/ld.so.preload /tmp/gpslogdiag.so /etc/a7r4a-boot.so /etc/a7r4a_film /tmp/disable_hook && sync
  3. Verify /bin/busybox & /bin/sh execute cleanly, terminal OFF
EOF
            ;;
    esac
    exit 0
fi

[ -x "$PMCA_PY" ] || { echo "FATAL: missing PMCA python $PMCA_PY" >&2; exit 2; }
mkdir -p "$LOG_DIR"
unmount_sony_msc_host

case "$SUBCMD" in
    status)
        verify_dist
        run_pmca_python - "$PMCA_DIR" "$DIST_DIR" "$LOG_DIR" <<'PY'
from pathlib import Path
import base64, gzip, hashlib, re, sys, time
import subprocess
pmca_dir, dist, logs = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, pmca_dir)
from pmca.commands.usb import senserShellCommand

from pmca.usb.driver.generic import GenericUsbException

def unmount_sony_msc_py():
    out = subprocess.check_output(["ioreg", "-p", "IOService", "-l", "-w", "0"], text=True, errors="replace")
    if "Sony" in out:
        disks = subprocess.check_output(["diskutil", "list", "external"], text=True, errors="replace")
        for m in re.finditer(r"(/dev/disk\d+)", disks):
            info = subprocess.check_output(["diskutil", "info", m.group(1)], text=True, errors="replace")
            if re.search(r"(?i)Sony|ILCE|DSC|PMHOME", info):
                subprocess.run(["diskutil", "unmountDisk", "force", m.group(1)], check=False)

def run_with_retry(fn):
    for attempt in range(2):
        state = {"ok": False}
        try:
            senserShellCommand(complete=lambda cam: fn(cam, state))
            if state["ok"]:
                return
            if attempt == 1:
                raise SystemExit("CAMERA_USB_SESSION_INCOMPLETE")
        except GenericUsbException:
            if attempt == 1:
                raise
        time.sleep(2.5)
        unmount_sony_msc_py()

def drain_pending(cam, seconds=0.2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if not d:
            time.sleep(0.02)

def drain(cam, marker, t=30.0):
    deadline = time.monotonic() + t; chunks = []
    pat = re.compile(rf"(?m)^{re.escape(marker)}\r?$")
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            chunks.append(d.decode("latin1", "replace"))
            if pat.search("".join(chunks)): break
        else: time.sleep(0.05)
    return "".join(chunks)

def send(cam, cmd):
    for line in cmd.splitlines(True):
        cam.dev.writeTerminal(line.encode("latin1")); time.sleep(0.03)

def body(cam, state):
    out_parts = []
    try:
        cam.readHasp()
        b11 = cam.readBackup(0x00a10011)
        b0b = cam.readBackup(0x00a1000b)
        armed = (b11 != b"\x00") and ((int.from_bytes(b0b, "little") & 2) == 0)
        b_line = f"=== BACKUP_TRIGGER ===\n0x00a10011={b11.hex()} 0x00a1000b={b0b.hex()} armed={int(armed)}\n"
        out_parts.append(b_line)

        cam.setTerminalEnable(False); cam.setTerminalEnable(True)
        time.sleep(1.0); drain_pending(cam)
        cam.dev.writeTerminal(b"\x03\nEOF_B64\n")
        time.sleep(0.2); drain_pending(cam)
        send(cam, "echo __SHELL_READY__\n")
        drain(cam, "__SHELL_READY__", 5.0)

        raw = (dist / "a7r4a-flash-free.elf").read_bytes()
        exp = hashlib.sha256(raw).hexdigest()
        gz = gzip.compress(raw, compresslevel=9)
        b64 = base64.b64encode(gz).decode("ascii")
        send(cam, "if ! /bin/busybox grep -Fq ' /tmp_bt tmpfs ' /proc/mounts 2>/dev/null; then /bin/busybox mount -t tmpfs -o size=16m,mode=755 tmpfs /tmp_bt; fi\n")
        drain_pending(cam, 0.2)
        send(cam, "cat <<'EOF_B64' | /bin/busybox base64 -d | /bin/busybox gzip -dc > /tmp_bt/.ff.elf\n")
        b64_lines = [b64[i:i+60] for i in range(0, len(b64), 60)]
        for i, ln in enumerate(b64_lines):
            cam.dev.writeTerminal((ln + "\n").encode("latin1")); time.sleep(0.018)
            if (i + 1) % 8 == 0:
                drain_pending(cam, 0.05)
        drain_pending(cam)
        send(cam, "EOF_B64\nchmod 755 /tmp_bt/.ff.elf\necho __FF_UP__\n")
        drain(cam, "__FF_UP__", 30.0)
        cmd = "\n".join([
            "echo === FILES ===",
            "ls -la /tmp/gpslogdiag.so /etc/a7r4a-boot.so /etc/ld.so.preload /etc/a7r4a_film /tmp/disable_hook 2>&1 || true",
            "echo === IM_MAPS ===",
            "IMP=$(/bin/busybox pidof im.elf 2>/dev/null || true); echo IMP=$IMP",
            "if [ -n \"$IMP\" ]; then /bin/busybox grep -E 'a7r4a|gpslogdiag' /proc/$IMP/maps 2>&1 || true; fi",
            "echo === DAEMON ===",
            "/bin/busybox pidof a6400-film-capture-daemon.elf 2>&1 || echo NO_DAEMON",
            "echo === FLASH_FREE ===",
            f"set -- $(/bin/busybox sha256sum /tmp_bt/.ff.elf 2>/dev/null || echo x); if [ \"$1\" = '{exp}' ]; then /tmp_bt/.ff.elf /etc; else echo FF_SHA_MISMATCH=$1; fi",
            "rm -f /tmp_bt/.ff.elf",
            "echo === BOOT_LOG ===",
            "cat /tmp_bt/a7r4a-boot.log 2>/dev/null || echo NO_BOOT_LOG",
            "echo === DAEMON_STDOUT ===",
            "cat /tmp_bt/daemon.stdout.log 2>/dev/null || echo NO_DAEMON_STDOUT",
            "echo === RUNTIME_LOG ===",
            "cat /tmp_bt/a6400-film-capture.runtime.log 2>/dev/null || echo NO_RUNTIME_LOG",
            "echo __AB_STATUS_DONE__"
        ]) + "\n"
        send(cam, cmd)
        out_parts.append(drain(cam, "__AB_STATUS_DONE__", 30.0))
        full = "".join(out_parts)
        (logs / "autoboot-status.log").write_text(full)
        print(full)
        state["ok"] = True
    finally:
        try:
            cam.setTerminalEnable(False)
        except Exception:
            pass

run_with_retry(body)
PY
        ;;
    install)
        verify_dist
        run_pmca_python - "$PMCA_DIR" "$DIST_DIR" "$LOG_DIR" "$BOOT_SETTLE_S" "$BOOT_STAGE" <<'PY'
from pathlib import Path
import base64, gzip, hashlib, re, subprocess, sys, tempfile, time
pmca_dir, dist, logs, settle_s, boot_stage = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5]
sys.path.insert(0, pmca_dir)
from pmca.commands.usb import senserShellCommand

RUNTIME_FILES = [
    ("a7r4a-ui-load-guard.elf",      dist / "a7r4a-ui-load-guard.elf",      "755"),
    ("a7r4a-ui-check.req",           dist / "ui/a7r4a-ui-check.req",         "644"),
    ("a7r4a-ui-install.req",         dist / "ui/a7r4a-ui-install.req",       "644"),
    ("a7r4a-ui-remove.req",          dist / "ui/a7r4a-ui-remove.req",        "644"),
    ("a7r4a-film-ui-clite.so",       dist / "a6400-film-ui-v3.so",           "755"),
    ("a6400-film-menu-job.elf",      dist / "a6400-film-menu-job.elf",       "755"),
    ("a6400-film-ui-stop.elf",       dist / "a6400-film-ui-stop.elf",        "755"),
    ("a6400-film-native-command.elf",dist / "a6400-film-native-command.elf", "755"),
    ("a7r4a-film-job.elf",           dist / "a7r4a-film-job.elf",            "755"),
    ("a6400-film-ram-job.sh",        dist / "film_ram_job.sh",               "755"),
    ("a6400-film-capture-daemon.elf",dist / "a6400-film-capture-daemon.elf", "755"),
    ("a7r4a-remote-shoot.elf",       dist / "a7r4a-remote-shoot.elf",        "755"),
]

BOOT_SH = """#!/bin/sh
set -uf
umask 077
export PATH=/usr/bin:/bin:/sbin:/usr/sbin
BB=/bin/busybox
RAM=/tmp_bt
ETC_DIR=/etc/a7r4a_film
SETTLE_S=@@SETTLE_S@@
STAGE=@@BOOT_STAGE@@

if test -e /tmp/disable_hook || test -e "$ETC_DIR/DISABLE"; then
    exit 0
fi
if ! "$BB" grep -Fq ' /tmp_bt tmpfs ' /proc/mounts 2>/dev/null; then
    "$BB" mount -t tmpfs -o size=16m,mode=755 tmpfs "$RAM" || exit 1
fi
"$BB" mkdir "$RAM/.a7r4a-boot-sh.lock" 2>/dev/null || exit 0
exec > "$RAM/a7r4a-boot.log" 2>&1
read -r boot_up _ < /proc/uptime 2>/dev/null || boot_up=0
boot_up_int=${boot_up%%.*}
case "$boot_up_int" in ''|*[!0-9]*) boot_up_int=0 ;; esac
set +f
fds=""
for fd_entry in /proc/self/fd/*; do
    test -e "$fd_entry" && fds="$fds ${fd_entry##*/}"
done
set -f
echo "BOOT_START uptime=$boot_up settle_s=$SETTLE_S"
echo "BOOT_SELF_STAT $(cat /proc/self/stat 2>/dev/null || echo unknown)"
echo "BOOT_SELF_FDS$fds"

if test -e "$RAM/a7r4a-film.DISABLE" || test -e "$RAM/a6400-film-capture.stop"; then
    echo "BOOT_DISABLED"
    exit 0
fi
if "$BB" grep -Eq '^/dev/mmc' /proc/mounts; then
    echo "BOOT_ABORT_MMC_MOUNTED"
    exit 1
fi

im_pid=
i=0
while test "$i" -lt 30; do
    if test -e /tmp/disable_hook || test -e "$ETC_DIR/DISABLE"; then
        echo "BOOT_DISABLED_WAIT"
        exit 0
    fi
    pids=$("$BB" pidof im.elf 2>/dev/null || true)
    set -- $pids
    if test "$#" = 1 && test -r "/proc/$1/maps"; then
        if "$BB" grep -Fq '/usr/lib/libObj.so' "/proc/$1/maps" &&
           "$BB" grep -Fq '/usr/lib/viewUnified2.so' "/proc/$1/maps" &&
           "$BB" grep -Fq '/usr/lib/libInfraMediaMount.so' "/proc/$1/maps"; then
            im_pid=$1
            break
        fi
    fi
    i=$((i+1))
    "$BB" sleep 1
done
if test -z "$im_pid"; then
    echo "BOOT_ABORT_IM_NOT_READY"
    exit 1
fi
if "$BB" grep -Fq 'a7r4a-film-ui-clite.so' "/proc/$im_pid/maps"; then
    echo "BOOT_ABORT_ALREADY_LOADED pid=$im_pid"
    exit 0
fi

while read -r name mode exp_sha; do
    test -n "$name" || continue
    "$BB" gzip -dc "$ETC_DIR/$name.gz" > "$RAM/$name" || { echo "BOOT_GUNZIP_FAIL $name"; exit 1; }
    "$BB" chmod "$mode" "$RAM/$name" || { echo "BOOT_CHMOD_FAIL $name"; exit 1; }
    got_line=$("$BB" sha256sum "$RAM/$name") || { echo "BOOT_SHA_FAIL $name"; exit 1; }
    test "${got_line%% *}" = "$exp_sha" || { echo "BOOT_SHA_MISMATCH $name"; "$BB" rm -f "$RAM/$name"; exit 1; }
done < "$ETC_DIR/MANIFEST"

# Settle past ssboot UHS-II negotiation & ExFAT FsysMount/XAVC DB load before PTRACE_ATTACH.
prev_tc=0
stable=0
j=0
max_wait=$((SETTLE_S + 25))
settled=0
cur_up=0
elapsed=0
tc=0
while test "$j" -lt "$max_wait"; do
    if test -e /tmp/disable_hook || test -e "$ETC_DIR/DISABLE"; then
        echo "BOOT_DISABLED_SETTLE"
        exit 0
    fi
    read -r cur_up _ < /proc/uptime 2>/dev/null || cur_up=0
    up_int=${cur_up%%.*}
    case "$up_int" in ''|*[!0-9]*) up_int=0 ;; esac
    if test "$boot_up_int" -gt 0 && test "$up_int" -ge "$boot_up_int"; then
        elapsed=$((up_int - boot_up_int))
    else
        elapsed=$j
    fi
    tc_line=$("$BB" grep '^Threads:' "/proc/$im_pid/status" 2>/dev/null || true)
    set -- $tc_line
    tc=${2:-0}
    case "$tc" in ''|*[!0-9]*) tc=0 ;; esac
    if test "$elapsed" -ge "$SETTLE_S" && test "$j" -ge "$SETTLE_S" && test "$tc" -gt 100; then
        if test "$tc" = "$prev_tc"; then
            stable=$((stable+1))
            if test "$stable" -ge 2; then
                settled=1
                echo "BOOT_SETTLED uptime=$cur_up elapsed=$elapsed wait_s=$j threads=$tc"
                break
            fi
        else
            stable=0
        fi
    fi
    prev_tc=$tc
    j=$((j+1))
    "$BB" sleep 1
done
if test "$settled" = 0; then
    echo "BOOT_SETTLE_TIMEOUT uptime=$cur_up elapsed=$elapsed wait_s=$j threads=$tc"
fi

if test "$STAGE" = stage1; then
    echo "BOOT_ISOLATION_STAGE1_OK pid=$im_pid"
    exit 0
fi

"$RAM/a7r4a-ui-load-guard.elf" install "$RAM/a7r4a-ui-install.req" || { echo "BOOT_GUARD_INSTALL_FAIL"; exit 1; }
read -r arm_up _ < /proc/uptime 2>/dev/null || arm_up=unknown
echo "BOOT_UI_ARMED pid=$im_pid uptime=$arm_up"
if test "$STAGE" = stage2; then
    echo "BOOT_ISOLATION_STAGE2_OK pid=$im_pid"
    exit 0
fi
"$BB" sleep 1
exec "$RAM/a6400-film-capture-daemon.elf" watch > "$RAM/daemon.stdout.log" 2>&1
""".replace("@@SETTLE_S@@", settle_s).replace("@@BOOT_STAGE@@", boot_stage)

with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as tf:
    tf.write(BOOT_SH)
    tmp_boot = Path(tf.name)
try:
    subprocess.run(["sh", "-n", str(tmp_boot)], check=True)
finally:
    tmp_boot.unlink(missing_ok=True)

manifest_lines = []
upload_items = []
for name, src, mode in RUNTIME_FILES:
    raw = src.read_bytes()
    exp_raw = hashlib.sha256(raw).hexdigest()
    gz = gzip.compress(raw, compresslevel=9)
    exp_gz = hashlib.sha256(gz).hexdigest()
    manifest_lines.append(f"{name} {mode} {exp_raw}\n")
    upload_items.append((f"/etc/a7r4a_film/{name}.gz", gz, exp_gz))

boot_sh_bytes = BOOT_SH.encode("utf-8")
manifest_bytes = "".join(manifest_lines).encode("utf-8")
upload_items.append(("/etc/a7r4a_film/boot.sh", boot_sh_bytes, hashlib.sha256(boot_sh_bytes).hexdigest()))
upload_items.append(("/etc/a7r4a_film/MANIFEST", manifest_bytes, hashlib.sha256(manifest_bytes).hexdigest()))

boot_so_bytes = (dist / "a7r4a-boot.so").read_bytes()
boot_so_sha = hashlib.sha256(boot_so_bytes).hexdigest()
ff_bytes = (dist / "a7r4a-flash-free.elf").read_bytes()
ff_sha = hashlib.sha256(ff_bytes).hexdigest()

from pmca.usb.driver.generic import GenericUsbException

def unmount_sony_msc_py():
    out = subprocess.check_output(["ioreg", "-p", "IOService", "-l", "-w", "0"], text=True, errors="replace")
    if "Sony" in out:
        disks = subprocess.check_output(["diskutil", "list", "external"], text=True, errors="replace")
        for m in re.finditer(r"(/dev/disk\d+)", disks):
            info = subprocess.check_output(["diskutil", "info", m.group(1)], text=True, errors="replace")
            if re.search(r"(?i)Sony|ILCE|DSC|PMHOME", info):
                subprocess.run(["diskutil", "unmountDisk", "force", m.group(1)], check=False)

def run_with_retry(fn):
    for attempt in range(2):
        state = {"ok": False}
        try:
            senserShellCommand(complete=lambda cam: fn(cam, state))
            if state["ok"]:
                return
            if attempt == 1:
                raise SystemExit("CAMERA_USB_SESSION_INCOMPLETE")
        except GenericUsbException:
            if attempt == 1:
                raise
        time.sleep(2.5)
        unmount_sony_msc_py()

def drain_pending(cam, seconds=0.2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if not d:
            time.sleep(0.02)

def drain(cam, marker, t=30.0):
    deadline = time.monotonic() + t; chunks = []
    pat = re.compile(rf"(?m)^{re.escape(marker)}\r?$")
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            chunks.append(d.decode("latin1", "replace"))
            if pat.search("".join(chunks)): break
        else: time.sleep(0.05)
    return "".join(chunks)

def send(cam, cmd):
    for line in cmd.splitlines(True):
        cam.dev.writeTerminal(line.encode("latin1")); time.sleep(0.04)

_STREAM_SEQ = 0
def stream_raw(cam, data: bytes, dst: str, exp_sha: str, executable: bool):
    global _STREAM_SEQ
    _STREAM_SEQ += 1
    tag = f"F{_STREAM_SEQ}"
    send(cam, f"set -- $(/bin/busybox sha256sum {dst} 2>/dev/null || echo x) && test \"$1\" = \"{exp_sha}\" && echo \"__\"EXIST_{tag}__ || echo \"__\"NEED_{tag}__\n")
    deadline = time.monotonic() + 10.0
    buf = []
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            buf.append(d.decode("latin1", "replace"))
            joined = "".join(buf)
            if f"__EXIST_{tag}__" in joined or f"__NEED_{tag}__" in joined:
                break
        else:
            time.sleep(0.03)
    if f"__EXIST_{tag}__" in "".join(buf):
        print(f"Verified existing {dst} (sha256 OK, skipped)")
        return

    gz = gzip.compress(data, compresslevel=9)
    b64 = base64.b64encode(gz).decode("ascii")
    lines = [b64[i:i+60] for i in range(0, len(b64), 60)]
    print(f"Staging {dst} ({len(data)} B -> {len(gz)} B gz, {len(lines)} lines)...")
    drain_pending(cam)
    tmp_dst = "/tmp_bt/.stage_tmp"
    send(cam, f"/bin/busybox base64 -d <<'__B64EOF__' | /bin/busybox gunzip -c > {tmp_dst}\n")
    for i, ln in enumerate(lines):
        cam.dev.writeTerminal((ln + "\n").encode("ascii"))
        time.sleep(0.018)
        if (i + 1) % 8 == 0:
            drain_pending(cam, 0.05)
    drain_pending(cam)
    send(cam, "__B64EOF__\n")
    send(cam, f"cp {tmp_dst} {dst} && rm -f {tmp_dst}\n")
    if executable:
        send(cam, f"chmod 755 {dst} >/dev/null 2>&1 || true\n")
    else:
        send(cam, f"chmod 644 {dst} >/dev/null 2>&1 || true\n")
    send(cam, f"set -- $(/bin/busybox sha256sum {dst}) && test \"$1\" = \"{exp_sha}\" && echo \"__\"HASH_OK_{tag}__ || echo \"__\"HASH_BAD_{tag}__\n")
    deadline = time.monotonic() + 30.0
    chunks = []
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            chunks.append(d.decode("latin1", "replace"))
            joined = "".join(chunks)
            if f"__HASH_OK_{tag}__" in joined or f"__HASH_BAD_{tag}__" in joined:
                break
        else:
            time.sleep(0.05)
    out = "".join(chunks)
    if f"__HASH_OK_{tag}__" not in out:
        raise SystemExit(f"SHA256_MISMATCH {dst}: {out}")

def body(cam, state):
    try:
        cam.readHasp(); cam.setTerminalEnable(False); cam.setTerminalEnable(True)
        time.sleep(1.0); drain_pending(cam)
        cam.dev.writeTerminal(b"\x03\n__B64EOF__\n")
        time.sleep(0.2); drain_pending(cam)
        send(cam, "echo __SHELL_READY__\n")
        drain(cam, "__SHELL_READY__", 5.0)

        # 1. Preflight check: refuse foreign /etc/ld.so.preload, remove legacy /etc preload, mount /tmp_bt
        pre = "\n".join([
            "if test -L /etc/ld.so.preload; then echo PRELOAD_SYMLINK; echo __PRE_RC__=90;",
            "elif test -e /etc/ld.so.preload; then",
            "  cur=$(cat /etc/ld.so.preload 2>/dev/null || true);",
            "  if [ \"$cur\" = '/etc/a7r4a-boot.so' ]; then rm -f /etc/ld.so.preload && sync; echo LEGACY_PRELOAD_REMOVED; echo __PRE_RC__=0;",
            "  else echo FOREIGN_PRELOAD=$cur; echo __PRE_RC__=91; fi;",
            "else echo NO_PRELOAD; echo __PRE_RC__=0; fi",
            "if ! /bin/busybox grep -Fq ' /tmp_bt tmpfs ' /proc/mounts 2>/dev/null; then /bin/busybox mount -t tmpfs -o size=16m,mode=755 tmpfs /tmp_bt; fi",
            "echo __PRE_DONE__"
        ]) + "\n"
        send(cam, pre)
        pout = drain(cam, "__PRE_DONE__", 20.0)
        print(pout)
        if not re.search(r"(?m)^__PRE_RC__=0\r?$", pout):
            raise SystemExit("PREFLIGHT_PRELOAD_ABORT")

        # 2. Check /etc flash free space >= 1024 KiB via RAM-staged flash-free helper
        stream_raw(cam, ff_bytes, "/tmp_bt/.ff.elf", ff_sha, True)
        send(cam, "/tmp_bt/.ff.elf /etc 2>&1 || true\nrm -f /tmp_bt/.ff.elf\necho __FF_DONE__\n")
        ff_out = drain(cam, "__FF_DONE__", 20.0)
        print(ff_out)
        m = re.search(r"(?m)^FLASH_STAT path=/etc total_kb=(\d+) free_kb=(\d+)\r?$", ff_out)
        if not m or int(m.group(2)) < 1024:
            raise SystemExit(f"INSUFFICIENT_FLASH_FREE_KB: {ff_out}")

        # 3. Stage /tmp/gpslogdiag.so + /etc/a7r4a_film/{boot.sh,MANIFEST,12x .gz}
        send(cam, "rm -f /etc/ld.so.preload /etc/a7r4a-boot.so /etc/a7r4a_film/TRACE /etc/a7r4a_film/seen.log /etc/a7r4a_film/DISABLE /tmp/disable_hook; mkdir -p /etc/a7r4a_film; sync; echo __DIR_DONE__\n")
        drain(cam, "__DIR_DONE__", 15.0)
        stream_raw(cam, boot_so_bytes, "/tmp/gpslogdiag.so", boot_so_sha, True)
        for dst, data, exp in upload_items:
            stream_raw(cam, data, dst, exp, dst.endswith(".sh"))

        vt_cmd = "\n".join([
            "sync",
            "/bin/sh -n /etc/a7r4a_film/boot.sh && echo __BOOT_SH_SYNTAX_OK__",
            "ls -la /tmp/gpslogdiag.so /etc/a7r4a_film/boot.sh /etc/a7r4a_film/MANIFEST",
            "/bin/busybox echo __BB_OK__",
            "echo __VFAT_TEST_DONE__"
        ]) + "\n"
        send(cam, vt_cmd)
        vt_out = drain(cam, "__VFAT_TEST_DONE__", 25.0)
        print(vt_out)
        if "__BOOT_SH_SYNTAX_OK__" not in vt_out or "__BB_OK__" not in vt_out:
            send(cam, "rm -rf /tmp/gpslogdiag.so /etc/a7r4a_film && sync; echo __RB__\n")
            drain(cam, "__RB__", 15.0)
            raise SystemExit(f"VFAT_STAGE_VERIFY_FAILED_ROLLED_BACK:\n{vt_out}")

        # 4. Terminal OFF, then arm Backup 0x00a10011=01 & 0x00a1000b=0000 and persist
        cam.setTerminalEnable(False)
        time.sleep(0.3)

        b11 = cam.readBackup(0x00a10011)
        b0b = cam.readBackup(0x00a1000b)
        used_id1 = False
        try:
            try:
                cam.writeBackup(0x00a10011, b"\x01")
                cam.writeBackup(0x00a1000b, b"\x00\x00")
            except Exception:
                cam.setBackupId1(True)
                used_id1 = True
                cam.writeBackup(0x00a10011, b"\x01")
                cam.writeBackup(0x00a1000b, b"\x00\x00")
            try:
                cam.saveBackup(0x0a1)
            except Exception as se:
                print(f"saveBackup(0x0a1) note: {se}")
            cam.saveBackup(0)
        finally:
            if used_id1:
                try:
                    cam.setBackupId1(False)
                except Exception:
                    pass
        a11 = cam.readBackup(0x00a10011)
        a0b = cam.readBackup(0x00a1000b)
        b_summary = f"BACKUP BEFORE 0x00a10011={b11.hex()} 0x00a1000b={b0b.hex()} -> AFTER 0x00a10011={a11.hex()} 0x00a1000b={a0b.hex()}"
        print(b_summary)
        if a11 != b"\x01" or a0b != b"\x00\x00":
            raise SystemExit(f"BACKUP_ARM_VERIFY_FAILED: {b_summary}")

        (logs / "autoboot-install.log").write_text(pout + "\n" + ff_out + "\n" + vt_out + "\n" + b_summary + "\n")
        print("\n=== AUTOBOOT_GPSLOGDIAG_INSTALLED_OK ===")
        print(f"Trigger: /tmp/gpslogdiag.so (Ir 0a0_geo_res) + Backup 0x00a10011=01 0x00a1000b=0000 (settle={settle_s}s)")
        print("Next: Unplug USB, turn OFF -> pull/reinsert battery (or power cycle) cleanly:")
        print(f"  1. Wait ~{int(settle_s)+3}s after powering ON (check /tmp_bt/a7r4a-boot.log).")
        print("  2. Tap touchscreen ONCE (attaches UI), then hold C1+C2 (or MENU+C1) ~0.8s to open Film Menu!")
        state["ok"] = True
    finally:
        try:
            cam.setTerminalEnable(False)
        except Exception:
            pass

run_with_retry(body)
PY
        ;;
    uninstall)
        run_pmca_python - "$PMCA_DIR" "$LOG_DIR" <<'PY'
from pathlib import Path
import re, subprocess, sys, time
pmca_dir, logs = sys.argv[1], Path(sys.argv[2])
sys.path.insert(0, pmca_dir)
from pmca.commands.usb import senserShellCommand
from pmca.usb.driver.generic import GenericUsbException

def unmount_sony_msc_py():
    out = subprocess.check_output(["ioreg", "-p", "IOService", "-l", "-w", "0"], text=True, errors="replace")
    if "Sony" in out:
        disks = subprocess.check_output(["diskutil", "list", "external"], text=True, errors="replace")
        for m in re.finditer(r"(/dev/disk\d+)", disks):
            info = subprocess.check_output(["diskutil", "info", m.group(1)], text=True, errors="replace")
            if re.search(r"(?i)Sony|ILCE|DSC|PMHOME", info):
                subprocess.run(["diskutil", "unmountDisk", "force", m.group(1)], check=False)

def run_with_retry(fn):
    for attempt in range(2):
        state = {"ok": False}
        try:
            senserShellCommand(complete=lambda cam: fn(cam, state))
            if state["ok"]:
                return
            if attempt == 1:
                raise SystemExit("CAMERA_USB_SESSION_INCOMPLETE")
        except GenericUsbException:
            if attempt == 1:
                raise
        time.sleep(2.5)
        unmount_sony_msc_py()

def drain(cam, marker, t=25.0):
    deadline = time.monotonic() + t; chunks = []
    pat = re.compile(rf"(?m)^{re.escape(marker)}\r?$")
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            chunks.append(d.decode("latin1", "replace"))
            if pat.search("".join(chunks)): break
        else: time.sleep(0.05)
    return "".join(chunks)

def send(cam, cmd):
    for line in cmd.splitlines(True):
        cam.dev.writeTerminal(line.encode("latin1")); time.sleep(0.03)

def body(cam, state):
    try:
        cam.readHasp()
        # 1. Revert Backup FIRST with terminal OFF so next boot is stock & unblocked
        used_id1 = False
        try:
            try:
                cam.writeBackup(0x00a10011, b"\x00")
                cam.writeBackup(0x00a1000b, b"\x02\x00")
            except Exception:
                cam.setBackupId1(True)
                used_id1 = True
                cam.writeBackup(0x00a10011, b"\x00")
                cam.writeBackup(0x00a1000b, b"\x02\x00")
            try:
                cam.saveBackup(0x0a1)
            except Exception as se:
                print(f"saveBackup(0x0a1) note: {se}")
            cam.saveBackup(0)
        finally:
            if used_id1:
                try:
                    cam.setBackupId1(False)
                except Exception:
                    pass
        a11 = cam.readBackup(0x00a10011)
        a0b = cam.readBackup(0x00a1000b)
        b_msg = f"BACKUP_REVERTED 0x00a10011={a11.hex()} 0x00a1000b={a0b.hex()}\n"
        print(b_msg, end="")
        if a11 != b"\x00" or a0b != b"\x02\x00":
            raise SystemExit(f"BACKUP_REVERT_VERIFY_FAILED: {b_msg}")

        cam.setTerminalEnable(False); cam.setTerminalEnable(True); time.sleep(0.5)
        cmds = "\n".join([
            "rm -f /etc/ld.so.preload && sync",
            "/bin/busybox echo __POST_RM_BB_OK__",
            "/bin/sh -c 'echo __POST_RM_SH_OK__'",
            "rm -rf /tmp/gpslogdiag.so /etc/a7r4a-boot.so /etc/a7r4a_film /tmp/disable_hook && sync",
            "ls -la /tmp/gpslogdiag.so /etc/a7r4a-boot.so /etc/ld.so.preload /etc/a7r4a_film /tmp/disable_hook 2>&1 || true",
            "echo __UNINSTALL_DONE__"
        ]) + "\n"
        send(cam, cmds)
        out = drain(cam, "__UNINSTALL_DONE__", 25.0)
        (logs / "autoboot-uninstall.log").write_text(b_msg + out)
        print(out)
        state["ok"] = True
    finally:
        try:
            cam.setTerminalEnable(False)
        except Exception:
            pass

run_with_retry(body)
PY
        ;;
esac
