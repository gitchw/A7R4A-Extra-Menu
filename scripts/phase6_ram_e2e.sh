#!/bin/bash
# ILCE-7RM4A (Ver. 1.12) Phase 6: 100% RAM-Only (/tmp_bt/) End-to-End Live Test
#
# Safety contract (enforced on host + camera):
#   - Strictly RAM-only under /tmp_bt/: NEVER touches /etc/ld.so.preload,
#     /usr/lib/, /nflasha*, or any flash partition; NEVER executes A6400 ELFs.
#   - Always refuses if /etc/ld.so.preload exists or /dev/mmc* is Linux-mounted.
#   - Every staged file is SHA-256 verified before execution.
#
# Subcommands:
#   ./phase6_ram_e2e.sh [--dry-run] check         # Refresh stage1-check.ok receipt
#   ./phase6_ram_e2e.sh [--dry-run] start         # Stage V3 UI + capture daemon + LUT worker + remote-shoot to /tmp_bt/ & arm RAM hook + daemon
#   ./phase6_ram_e2e.sh [--dry-run] status        # Read live menu snapshot, daemon pid/manifest, & UI trace from /tmp_bt/
#   ./phase6_ram_e2e.sh [--dry-run] remote-shoot [args...] # Run /tmp_bt/a7r4a-remote-shoot.elf (default: shutter)
#   ./phase6_ram_e2e.sh [--dry-run] collect       # Stop daemon + pull logs + stop UI + remove hook + clean /tmp_bt/
#   ./phase6_ram_e2e.sh all-dry-run               # Dry-run check -> start -> status -> remote-shoot -> collect
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
REMOTE_DIR="/tmp_bt"

DRY_RUN=0
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        *) ARGS+=("$arg") ;;
    esac
done
SUBCMD="${ARGS[0]:-}"
EXTRA_ARGS=("${ARGS[@]:1}")

usage() {
    cat <<EOF
Usage:
  $0 [--dry-run] {check|start|status|remote-shoot [args...]|collect|all-dry-run}

Strict RAM-only E2E workflow on ILCE-7RM4A (Ver. 1.12):
  1. $0 check          # Verify load-guard + target hook + write stage1-check.ok
  2. $0 start          # Stage full V3 CLite UI + daemon + authentic-LUT worker -> /tmp_bt/, start daemon & hook
  3. Unplug USB, press C1+C2 (or tap/release touchscreen) to pick LUT/Grain/Halation, shoot photos onto SD card
  4. Replug USB -> $0 status   (optional check)
  5. Replug USB -> $0 collect  (stops daemon/UI, pulls runtime.log/manifest/job.log, restores original hook bytes, cleans /tmp_bt/)
EOF
}

assert_ram_path() {
    local remote_path=$1
    case "$remote_path" in
        /tmp_bt/[A-Za-z0-9._-]*)
            case "$remote_path" in
                *..*|*/tmp_bt/*/*)
                    echo "FATAL: unsafe nested or traversal remote path: $remote_path" >&2
                    exit 2
                    ;;
            esac
            ;;
        *)
            echo "FATAL: refusing non-/tmp_bt/ remote path: $remote_path" >&2
            exit 2
            ;;
    esac
}

verify_dist_artifacts() {
    python3 - "$DIST_DIR" "$REPO_DIR/camera/a7r4a_target.h" <<'PY'
from pathlib import Path
import hashlib
import re
import struct
import sys

dist = Path(sys.argv[1])
target_h = Path(sys.argv[2])

defs = {}
if target_h.is_file():
    for m in re.finditer(r"#define\s+(A7R4A_[A-Z0-9_]+)\s+(0x[0-9a-fA-F]+|[0-9]+)u?", target_h.read_text()):
        defs[m.group(1)] = int(m.group(2), 0)

A7R4A_REQ_MAGIC = defs.get("A7R4A_REQ_MAGIC", 0x5248544D)
A7R4A_REQ_VERSION = defs.get("A7R4A_REQ_VERSION", 2)
A7R4A_STATE_SIZE = defs.get("A7R4A_STATE_SIZE", 1184)
A7R4A_HOOK_RVA = defs.get("A7R4A_HOOK_RVA", 0x00625EE8)
A7R4A_CODE_MAX = defs.get("A7R4A_CODE_MAX", 1700)

def fnv1a32(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h

sha_file = dist / "SHA256SUMS"
if not sha_file.is_file():
    raise SystemExit(f"MISSING_SHA256SUMS: {sha_file}")
for line in sha_file.read_text().splitlines():
    if not line.strip():
        continue
    want, rel = line.split(None, 1)
    p = dist / rel
    if not p.is_file():
        raise SystemExit(f"MISSING_DIST_FILE: {p}")
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    if got != want:
        raise SystemExit(f"SHA256SUMS_MISMATCH for {rel}: got={got} want={want}")

required_elf_or_so = [
    "a7r4a-ui-load-guard.elf",
    "a6400-film-ui-v3.so",
    "a6400-film-menu-job.elf",
    "a6400-film-ui-stop.elf",
    "a6400-film-native-command.elf",
    "a7r4a-film-job.elf",
    "a6400-film-capture-daemon.elf",
    "a7r4a-remote-shoot.elf",
]
for name in required_elf_or_so:
    raw = (dist / name).read_bytes()
    if len(raw) < 52 or raw[:4] != b"\x7fELF" or raw[4] != 1 or raw[5] != 1:
        raise SystemExit(f"BAD_ELF_HEADER: {dist / name}")

for name, op in (
    ("a7r4a-check.req", 0),
    ("ui/a7r4a-ui-check.req", 0),
    ("ui/a7r4a-ui-install.req", 1),
    ("ui/a7r4a-ui-remove.req", 2),
):
    raw = (dist / name).read_bytes()
    if len(raw) < 32:
        raise SystemExit(f"SHORT_REQ: {name}")
    h = struct.unpack("<8I", raw[:32])
    payload = raw[32:]
    if h[0] != A7R4A_REQ_MAGIC or h[1] != A7R4A_REQ_VERSION or h[2] != op:
        raise SystemExit(f"BAD_REQ_HEADER: {name} {h[:3]}")
    if h[3] != len(payload) or h[3] < 24 or h[3] > A7R4A_CODE_MAX or (h[3] & 3):
        raise SystemExit(f"BAD_REQ_LEN: {name} {len(payload)}")
    if h[4] != 1 or h[6] != A7R4A_STATE_SIZE or h[7] != A7R4A_HOOK_RVA or fnv1a32(payload) != h[5]:
        raise SystemExit(f"BAD_REQ_ABI: {name}")

ram_job = (dist / "film_ram_job.sh").read_text()
if "__NATIVE_SHA__" in ram_job or "__WORKER_SHA__" in ram_job or "__SETTINGS_SHA__" in ram_job:
    raise SystemExit("UNPINNED_PLACEHOLDERS_IN_FILM_RAM_JOB_SH")

print("DIST_OK", len(required_elf_or_so), "ELFs/SOs + 4 REQs + film_ram_job.sh verified against SHA256SUMS")
PY
}

unmount_sony_msc_if_present() {
    python3 - <<'PY'
import re, subprocess, time
out = subprocess.check_output(["ioreg", "-p", "IOService", "-l", "-w", "0"], text=True, errors="replace")
if "Sony" in out and ("PMCA" in out or "Mass Storage" in out or "DSC" in out or "ILCE" in out):
    disks = subprocess.check_output(["diskutil", "list", "external"], text=True, errors="replace")
    for m in re.finditer(r"(/dev/disk\d+)", disks):
        dev = m.group(1)
        info = subprocess.check_output(["diskutil", "info", dev], text=True, errors="replace")
        if "Sony" in info or "ILCE" in info or "DSC" in info:
            subprocess.run(["diskutil", "unmountDisk", "force", dev], check=False)
            time.sleep(1.0)
PY
}

run_pmca_python() {
    if [ ! -x "$PMCA_PY" ]; then
        echo "FATAL: missing Sony-PMCA-RE python at $PMCA_PY" >&2
        exit 2
    fi
    unmount_sony_msc_if_present
    mkdir -p "$LOG_DIR"
    if [ "${A7R4A_USE_SUDO:-0}" = "1" ] && [ "$(id -u)" -ne 0 ]; then
        sudo -n "$PMCA_PY" "$@"
    else
        "$PMCA_PY" "$@"
    fi
}

pinned_ui_runtime_path() {
    local ui_path
    ui_path=$(python3 - "$DIST_DIR/pinned-ui.json" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
data = json.loads(p.read_text()) if p.is_file() else {}
print(data.get("path", "/tmp_bt/a7r4a-film-ui-clite.so"))
PY
)
    assert_ram_path "$ui_path"
    printf '%s\n' "$ui_path"
}

cmd_check() {
    verify_dist_artifacts
    local ui_runtime ui_basename
    ui_runtime=$(pinned_ui_runtime_path)
    ui_basename=$(basename "$ui_runtime")
    if [ "$DRY_RUN" = 1 ]; then
        cat <<EOF
=== DRY-RUN: phase6 check ===
  1. check /proc/\$(pidof im.elf)/maps has no stale $ui_basename (glibc 2.11.2 dlopen guard)
  2. verify ! -e /etc/ld.so.preload && ! grep -Eq '^/dev/mmc' /proc/mounts
  3. mount -t tmpfs -o size=16m tmpfs $REMOTE_DIR (if not mounted)
  4. stream + sha256sum: a7r4a-ui-load-guard.elf, a7r4a-check.req -> $REMOTE_DIR/
  5. $REMOTE_DIR/a7r4a-ui-load-guard.elf check $REMOTE_DIR/a7r4a-check.req
  6. clean $REMOTE_DIR/a7r4a-ui-load-guard.elf $REMOTE_DIR/a7r4a-check.req + write $LOG_DIR/stage1-check.ok
EOF
        return 0
    fi
    run_pmca_python - "$PMCA_DIR" "$DIST_DIR" "$LOG_DIR" "$ui_basename" <<'PY'
from pathlib import Path
import base64, gzip, hashlib, re, subprocess, sys, time

pmca_dir, dist_dir, log_dir, ui_basename = sys.argv[1:5]
dist, logs = Path(dist_dir), Path(log_dir)
guard_bytes = (dist / "a7r4a-ui-load-guard.elf").read_bytes()
check_bytes = (dist / "a7r4a-check.req").read_bytes()

sys.path.insert(0, pmca_dir)
from pmca.commands.usb import senserShellCommand
from pmca.usb.driver.generic import GenericUsbException

def unmount_sony_msc_py():
    out = subprocess.check_output(["ioreg", "-p", "IOService", "-l", "-w", "0"], text=True, errors="replace")
    if "Sony" in out:
        disks = subprocess.check_output(["diskutil", "list", "external"], text=True, errors="replace")
        for m in re.finditer(r"(/dev/disk\d+)", disks):
            info = subprocess.check_output(["diskutil", "info", m.group(1)], text=True, errors="replace")
            if "Sony" in info or "ILCE" in info or "DSC" in info:
                subprocess.run(["diskutil", "unmountDisk", "force", m.group(1)], check=False)

def drain_pending(cam, seconds=0.2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            d = cam.dev.readTerminal()
            if not d:
                time.sleep(0.02)
        except GenericUsbException:
            break

def drain_terminal(cam, marker, timeout_sec=30.0):
    pat = re.compile(rf"(?m)^{re.escape(marker)}\r?$")
    deadline = time.monotonic() + timeout_sec
    chunks = []
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            chunks.append(d.decode("latin1", "replace"))
            if pat.search("".join(chunks)):
                return "".join(chunks)
        else:
            time.sleep(0.05)
    return "".join(chunks)

def send_lines(cam, script, delay=0.05):
    for line in script.splitlines(True):
        cam.dev.writeTerminal(line.encode("latin1"))
        time.sleep(delay)

def stream_file(cam, tag, remote, raw):
    digest = hashlib.sha256(raw).hexdigest()
    gz = gzip.compress(raw, compresslevel=9)
    lines = base64.encodebytes(gz).decode("ascii").splitlines()
    drain_pending(cam)
    send_lines(cam, f"/bin/busybox base64 -d <<'__B64EOF__' | /bin/busybox gunzip -c > {remote}\n", 0.04)
    for i, line in enumerate(lines):
        cam.dev.writeTerminal((line + "\n").encode("ascii"))
        time.sleep(0.015)
        if (i + 1) % 8 == 0:
            drain_pending(cam, 0.05)
    drain_pending(cam)
    send_lines(cam, f"__B64EOF__\nset -- $(/bin/busybox sha256sum {remote}) && test \"$1\" = \"{digest}\" && echo \"__\"HASH_OK_{tag}__ || echo \"__\"HASH_BAD_{tag}__\n", 0.04)
    out = drain_terminal(cam, f"__HASH_OK_{tag}__", 15.0)
    if f"__HASH_OK_{tag}__" not in out:
        raise SystemExit(f"SHA256_MISMATCH {remote}:\n{out}")

def body(cam):
    cam.readHasp(); cam.setTerminalEnable(False); cam.setTerminalEnable(True)
    time.sleep(1.0); drain_pending(cam)
    mounted_by_us = False
    try:
        send_lines(cam,
            "stale_so=0\n"
            "for p in $(/bin/busybox pidof im.elf); do\n"
            f"  if grep -Fq '{ui_basename}' \"/proc/$p/maps\" 2>/dev/null; then stale_so=1; fi\n"
            "done\n"
            "if [ \"$stale_so\" = 1 ]; then\n"
            "  echo 'STALE_DLOPEN_IN_IM_ELF: unplug USB, pull battery 5s (cold boot), reinsert/power ON, replug USB'\n"
            "  echo __PRE_RC__=92\n"
            "elif test -e /etc/ld.so.preload || test -L /etc/ld.so.preload; then echo __PRE_RC__=90\n"
            "elif grep -Eq '^/dev/mmc' /proc/mounts; then echo __PRE_RC__=91\n"
            "elif grep -q ' /tmp_bt tmpfs ' /proc/mounts; then echo ALREADY_MOUNTED; echo __PRE_RC__=0\n"
            "else mount -t tmpfs -o size=16m tmpfs /tmp_bt && echo MOUNTED_BY_US && echo __PRE_RC__=0; fi\n"
            "echo __PREP_DONE__\n"
        )
        mout = drain_terminal(cam, "__PREP_DONE__", 15.0)
        if not re.search(r"(?m)^__PRE_RC__=0\r?$", mout):
            (logs / "phase6-check.log").write_text(mout)
            print(mout)
            if "STALE_DLOPEN_IN_IM_ELF" in mout:
                print("\n[重要提示] 相机内存 (im.elf) 仍残留旧版动态库：A7R4A 在插着 USB 或仅拨开关关机时会走 ssboot 休眠，不会真正释放内存！\n"
                      "请按以下 4 步冷启动一次：1) 拔掉 USB 线  2) 关机并取出电池 5 秒  3) 装回电池开机  4) 插回 USB 重试。\n")
            raise SystemExit("PHASE6_CHECK_PRECHECK_FAILED")
        mounted_by_us = "MOUNTED_BY_US" in mout
        stream_file(cam, "GUARD", "/tmp_bt/a7r4a-ui-load-guard.elf", guard_bytes)
        stream_file(cam, "CHK", "/tmp_bt/a7r4a-check.req", check_bytes)
        send_lines(cam,
            "chmod 755 /tmp_bt/a7r4a-ui-load-guard.elf && chmod 644 /tmp_bt/a7r4a-check.req\n"
            "/tmp_bt/a7r4a-ui-load-guard.elf check /tmp_bt/a7r4a-check.req\n"
            "echo __CHECK_RC__=$?\n"
            "rm -f /tmp_bt/a7r4a-ui-load-guard.elf /tmp_bt/a7r4a-check.req\n"
            + ("umount /tmp_bt 2>/dev/null || true\n" if mounted_by_us else "")
            + "echo __CHECK_DONE__\n"
        )
        out = drain_terminal(cam, "__CHECK_DONE__", 45.0)
    finally:
        try: cam.setTerminalEnable(False)
        except Exception: pass
    (logs / "phase6-check.log").write_text(out)
    print(out)
    if not re.search(r"(?m)^__CHECK_RC__=0\r?$", out):
        raise SystemExit("PHASE6_CHECK_FAILED")
    (logs / "stage1-check.ok").write_text(
        f"guard_sha256={hashlib.sha256(guard_bytes).hexdigest()}\n"
        f"check_sha256={hashlib.sha256(check_bytes).hexdigest()}\n"
    )
    print("PHASE6_CHECK_OK -> wrote", logs / "stage1-check.ok")

for attempt in range(2):
    try:
        senserShellCommand(complete=body); sys.exit(0)
    except GenericUsbException:
        if attempt == 1: raise
        time.sleep(2.5); unmount_sony_msc_py()
PY
}

cmd_start() {
    verify_dist_artifacts
    local ui_runtime ui_basename
    ui_runtime=$(pinned_ui_runtime_path)
    ui_basename=$(basename "$ui_runtime")
    local staged_files=(
        "a7r4a-ui-load-guard.elf:/tmp_bt/a7r4a-ui-load-guard.elf:755"
        "ui/a7r4a-ui-check.req:/tmp_bt/a7r4a-ui-check.req:644"
        "ui/a7r4a-ui-install.req:/tmp_bt/a7r4a-ui-install.req:644"
        "ui/a7r4a-ui-remove.req:/tmp_bt/a7r4a-ui-remove.req:644"
        "a6400-film-ui-v3.so:${ui_runtime}:755"
        "a6400-film-menu-job.elf:/tmp_bt/a6400-film-menu-job.elf:755"
        "a6400-film-ui-stop.elf:/tmp_bt/a6400-film-ui-stop.elf:755"
        "a6400-film-native-command.elf:/tmp_bt/a6400-film-native-command.elf:755"
        "a7r4a-film-job.elf:/tmp_bt/a7r4a-film-job.elf:755"
        "film_ram_job.sh:/tmp_bt/a6400-film-ram-job.sh:755"
        "a6400-film-capture-daemon.elf:/tmp_bt/a6400-film-capture-daemon.elf:755"
        "a7r4a-remote-shoot.elf:/tmp_bt/a7r4a-remote-shoot.elf:755"
    )
    for spec in "${staged_files[@]}"; do
        local rest="${spec#*:}"; local rpath="${rest%:*}"
        assert_ram_path "$rpath"
    done

    if [ "$DRY_RUN" = 1 ]; then
        cat <<EOF
=== DRY-RUN: phase6 start (100% RAM-only /tmp_bt/) ===
[precondition]
  Requires $LOG_DIR/stage1-check.ok matching current a7r4a-ui-load-guard.elf + a7r4a-check.req SHA-256
  Aborts with STALE_DLOPEN_IN_IM_ELF if im.elf maps already contain $ui_basename
  Refuses if /etc/ld.so.preload exists or /dev/mmc* is mounted in Linux /proc/mounts
[stage + sha256 verify -> /tmp_bt/]
EOF
        for spec in "${staged_files[@]}"; do
            src="${spec%%:*}"; rest="${spec#*:}"; rpath="${rest%:*}"; mode="${rest##*:}"
            printf "  %-32s -> %-42s (chmod %s)\n" "$src" "$rpath" "$mode"
        done
        cat <<EOF
[arm in RAM]
  /tmp_bt/a7r4a-ui-load-guard.elf check /tmp_bt/a7r4a-ui-check.req
  /tmp_bt/a7r4a-ui-load-guard.elf install /tmp_bt/a7r4a-ui-install.req
  /tmp_bt/a6400-film-capture-daemon.elf watch > /tmp_bt/daemon.stdout.log 2>&1 < /dev/null &
  Wait up to 15s for FILM_CAPTURE_READY + FILM_CAPTURE_BASELINE in /tmp_bt/a6400-film-capture.runtime.log
EOF
        return 0
    fi

    local stage1_ok="$LOG_DIR/stage1-check.ok"
    if [ ! -f "$stage1_ok" ]; then
        echo "FATAL: run '$0 check' first to verify stage1-check.ok against current guard SHA-256" >&2
        exit 3
    fi

    run_pmca_python - "$PMCA_DIR" "$DIST_DIR" "$LOG_DIR" "$ui_runtime" "$ui_basename" <<'PY'
from pathlib import Path
import base64, gzip, hashlib, re, subprocess, sys, time

pmca_dir, dist_dir, log_dir, ui_runtime, ui_basename = sys.argv[1:6]
dist, logs = Path(dist_dir), Path(log_dir)

guard_bytes = (dist / "a7r4a-ui-load-guard.elf").read_bytes()
check_bytes = (dist / "a7r4a-check.req").read_bytes()
expected_receipt = (
    f"guard_sha256={hashlib.sha256(guard_bytes).hexdigest()}\n"
    f"check_sha256={hashlib.sha256(check_bytes).hexdigest()}\n"
)
if (logs / "stage1-check.ok").read_text() != expected_receipt:
    raise SystemExit("PHASE6_START_REFUSED: stage1-check.ok mismatch; run './phase6_ram_e2e.sh check' first")

stage_specs = [
    ("GUARD", "a7r4a-ui-load-guard.elf", "/tmp_bt/a7r4a-ui-load-guard.elf", "755"),
    ("UCHK", "ui/a7r4a-ui-check.req", "/tmp_bt/a7r4a-ui-check.req", "644"),
    ("UINST", "ui/a7r4a-ui-install.req", "/tmp_bt/a7r4a-ui-install.req", "644"),
    ("UREM", "ui/a7r4a-ui-remove.req", "/tmp_bt/a7r4a-ui-remove.req", "644"),
    ("UISO", "a6400-film-ui-v3.so", ui_runtime, "755"),
    ("MENU", "a6400-film-menu-job.elf", "/tmp_bt/a6400-film-menu-job.elf", "755"),
    ("STOP", "a6400-film-ui-stop.elf", "/tmp_bt/a6400-film-ui-stop.elf", "755"),
    ("NAT", "a6400-film-native-command.elf", "/tmp_bt/a6400-film-native-command.elf", "755"),
    ("WRK", "a7r4a-film-job.elf", "/tmp_bt/a7r4a-film-job.elf", "755"),
    ("RAMSH", "film_ram_job.sh", "/tmp_bt/a6400-film-ram-job.sh", "755"),
    ("DMN", "a6400-film-capture-daemon.elf", "/tmp_bt/a6400-film-capture-daemon.elf", "755"),
    ("RMT", "a7r4a-remote-shoot.elf", "/tmp_bt/a7r4a-remote-shoot.elf", "755"),
]

sys.path.insert(0, pmca_dir)
from pmca.commands.usb import senserShellCommand
from pmca.usb.driver.generic import GenericUsbException

def unmount_sony_msc_py():
    out = subprocess.check_output(["ioreg", "-p", "IOService", "-l", "-w", "0"], text=True, errors="replace")
    if "Sony" in out:
        disks = subprocess.check_output(["diskutil", "list", "external"], text=True, errors="replace")
        for m in re.finditer(r"(/dev/disk\d+)", disks):
            info = subprocess.check_output(["diskutil", "info", m.group(1)], text=True, errors="replace")
            if "Sony" in info or "ILCE" in info or "DSC" in info:
                subprocess.run(["diskutil", "unmountDisk", "force", m.group(1)], check=False)

def drain_pending(cam, seconds=0.2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            d = cam.dev.readTerminal()
            if not d:
                time.sleep(0.02)
        except GenericUsbException:
            break

def drain_terminal(cam, marker, timeout_sec=30.0):
    pat = re.compile(rf"(?m)^{re.escape(marker)}\r?$")
    deadline = time.monotonic() + timeout_sec
    chunks = []
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            chunks.append(d.decode("latin1", "replace"))
            if pat.search("".join(chunks)):
                return "".join(chunks)
        else:
            time.sleep(0.05)
    return "".join(chunks)

def send_lines(cam, script, delay=0.05):
    for line in script.splitlines(True):
        cam.dev.writeTerminal(line.encode("latin1"))
        time.sleep(delay)

def stream_file(cam, tag, remote, raw):
    digest = hashlib.sha256(raw).hexdigest()
    gz = gzip.compress(raw, compresslevel=9)
    lines = base64.encodebytes(gz).decode("ascii").splitlines()
    print(f"Staging {remote} ({len(raw)} B -> {len(gz)} B gz, {len(lines)} lines)...")
    drain_pending(cam)
    send_lines(cam, f"/bin/busybox base64 -d <<'__B64EOF__' | /bin/busybox gunzip -c > {remote}\n", 0.04)
    for i, line in enumerate(lines):
        cam.dev.writeTerminal((line + "\n").encode("ascii"))
        time.sleep(0.015)
        if (i + 1) % 8 == 0:
            drain_pending(cam, 0.05)
    drain_pending(cam)
    send_lines(cam, f"__B64EOF__\nset -- $(/bin/busybox sha256sum {remote}) && test \"$1\" = \"{digest}\" && echo \"__\"HASH_OK_{tag}__ || echo \"__\"HASH_BAD_{tag}__\n", 0.04)
    out = drain_terminal(cam, f"__HASH_OK_{tag}__", 20.0)
    if f"__HASH_OK_{tag}__" not in out:
        raise SystemExit(f"SHA256_MISMATCH {remote}:\n{out}")

def body(cam):
    cam.readHasp(); cam.setTerminalEnable(False); cam.setTerminalEnable(True)
    time.sleep(1.0); drain_pending(cam)
    armed = False
    try:
        send_lines(cam,
            "stale_so=0\n"
            "for p in $(/bin/busybox pidof im.elf); do\n"
            f"  if grep -Fq '{ui_basename}' \"/proc/$p/maps\" 2>/dev/null; then stale_so=1; fi\n"
            "done\n"
            "if [ \"$stale_so\" = 1 ]; then\n"
            "  echo 'STALE_DLOPEN_IN_IM_ELF: unplug USB, pull battery 5s (cold boot), reinsert/power ON, replug USB'\n"
            "  echo __PRE_RC__=92\n"
            "elif test -e /etc/ld.so.preload || test -L /etc/ld.so.preload; then echo __PRE_RC__=90\n"
            "elif grep -Eq '^/dev/mmc' /proc/mounts; then echo __PRE_RC__=91\n"
            "elif grep -q ' /tmp_bt tmpfs ' /proc/mounts; then echo __PRE_RC__=0\n"
            "else mount -t tmpfs -o size=16m tmpfs /tmp_bt && echo __PRE_RC__=0; fi\n"
            "echo __PREP_DONE__\n"
        )
        pout = drain_terminal(cam, "__PREP_DONE__", 15.0)
        if not re.search(r"(?m)^__PRE_RC__=0\r?$", pout):
            (logs / "phase6-start.log").write_text(pout)
            raise SystemExit(f"PHASE6_PRECHECK_FAILED:\n{pout}")

        for tag, rel, rpath, _ in stage_specs:
            stream_file(cam, tag, rpath, (dist / rel).read_bytes())

        start_lines = [
            f"chmod {m} {r}" for _, _, r, m in stage_specs
        ] + [
            "rm -f /tmp_bt/a7r4a-ui-probe.log /tmp_bt/a6400-film-capture.stop",
            "rm -f /tmp_bt/daemon.stdout.log /tmp_bt/a6400-film-capture.runtime.log",
            "rm -f /tmp_bt/a6400-film-capture.manifest /tmp_bt/a6400-film-capture.job.log",
            "rm -f /tmp_bt/a6400-film-capture.inflight /tmp_bt/a6400-capture-v1.lock",
            "rm -f /tmp_bt/a6400-film-job.lock",
            "/tmp_bt/a7r4a-ui-load-guard.elf check /tmp_bt/a7r4a-ui-check.req",
            "rc=$?; echo __CHECK_RC__=$rc",
            "if [ \"$rc\" = 0 ]; then",
            "  /tmp_bt/a7r4a-ui-load-guard.elf install /tmp_bt/a7r4a-ui-install.req",
            "  echo __INST_RC__=$?",
            "fi",
            "echo __HOOK_DONE__",
        ]
        send_lines(cam, "\n".join(start_lines) + "\n")
        hout = drain_terminal(cam, "__HOOK_DONE__", 90.0)
        if not (re.search(r"(?m)^__INST_RC__=0\r?$", hout) and "INSTALLED " in hout):
            raise SystemExit(f"PHASE6_HOOK_INSTALL_FAILED:\n{hout}")

        dmn_cmd = "\n".join([
            "/tmp_bt/a6400-film-capture-daemon.elf watch >/tmp_bt/daemon.stdout.log 2>&1 &",
            "d_pid=$!; echo __DMN_PID__=$d_pid",
            "i=0; while [ $i -lt 10 ]; do",
            "  if grep -qE 'FILM_CAPTURE_BASELINE |FILM_CAPTURE_BASELINE_MEDIA_WAIT ' /tmp_bt/a6400-film-capture.runtime.log 2>/dev/null; then break; fi",
            "  if ! kill -0 $d_pid 2>/dev/null; then break; fi",
            "  i=$((i+1)); /bin/busybox sleep 1",
            "done",
            "cat /tmp_bt/daemon.stdout.log 2>/dev/null || true",
            "cat /tmp_bt/a6400-film-capture.runtime.log 2>/dev/null || true",
            "if kill -0 $d_pid 2>/dev/null; then echo __DMN_ALIVE__=1; else echo __DMN_ALIVE__=0; fi",
            "echo __DMN_DONE__",
        ]) + "\n"
        send_lines(cam, dmn_cmd)
        dout = drain_terminal(cam, "__DMN_DONE__", 45.0)
        (logs / "phase6-start.log").write_text(hout + "\n--- DAEMON ---\n" + dout)
        print(hout + "\n" + dout)
        if not re.search(r"(?m)^__DMN_ALIVE__=1\r?$", dout):
            raise SystemExit("DAEMON_EXITED_EARLY; check phase6-start.log")
        armed = True
    finally:
        if not armed:
            try:
                abort_cmd = (
                    ": > /tmp_bt/a6400-film-capture.stop\n"
                    "/tmp_bt/a7r4a-ui-load-guard.elf remove /tmp_bt/a7r4a-ui-remove.req >/dev/null 2>&1 || true\n"
                    "echo __ABORT_DONE__\n"
                )
                send_lines(cam, abort_cmd)
                drain_terminal(cam, "__ABORT_DONE__", 10.0)
            except Exception:
                pass
        try: cam.setTerminalEnable(False)
        except Exception: pass

    print(
        "\n=== PHASE6_RAM_E2E_LIVE (ARMED IN RAM ONLY) ===\n"
        "Next user steps:\n"
        "  1. Unplug the USB cable (keep camera powered ON, do NOT power cycle).\n"
        "  2. First tap/release touchscreen ONCE (loads UI into im.elf), THEN hold C1+C2 or MENU+C1 for 0.6s to open the Film Simulation overlay.\n"
        "  3. Select a film preset + Grain (0..3) + Halation (0|1) and turn ON.\n"
        "  4. Take 1 or more real photos onto the SD card; wait ~5-10s for processing to complete.\n"
        "  5. Replug USB cable and run:\n"
        "       ./phase6_ram_e2e.sh status    # inspect manifest / applied jobs\n"
        "       ./phase6_ram_e2e.sh collect   # stop daemon/UI cleanly, pull logs, restore original hook\n"
    )

for attempt in range(2):
    try:
        senserShellCommand(complete=body); sys.exit(0)
    except GenericUsbException:
        if attempt == 1: raise
        time.sleep(2.5); unmount_sony_msc_py()
PY
}

cmd_status() {
    if [ "$DRY_RUN" = 1 ]; then
        cat <<EOF
=== DRY-RUN: phase6 status ===
  Inspect /tmp_bt/a6400-film-menu-job.elf, /tmp_bt/a6400-film-ui-stop.elf --check / --dump-trace,
  /tmp_bt/a6400-film-capture.manifest, /tmp_bt/a6400-film-capture.runtime.log, /tmp_bt/a6400-film-capture.job.log
EOF
        return 0
    fi
    run_pmca_python - "$PMCA_DIR" "$LOG_DIR" <<'PY'
from pathlib import Path
import re, sys, time
pmca_dir, logs = sys.argv[1], Path(sys.argv[2])
sys.path.insert(0, pmca_dir)
from pmca.commands.usb import senserShellCommand

def drain(cam, marker, t=20.0):
    deadline = time.monotonic() + t; chunks = []
    pat = re.compile(rf"(?m)^{re.escape(marker)}\r?$")
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            chunks.append(d.decode("latin1", "replace"))
            if pat.search("".join(chunks)): break
        else: time.sleep(0.05)
    return "".join(chunks)

def body(cam):
    cam.readHasp(); cam.setTerminalEnable(False); cam.setTerminalEnable(True); time.sleep(0.5)
    cmd = (
        "echo === MENU ===; /tmp_bt/a6400-film-menu-job.elf 2>&1 || true\n"
        "echo === UI_STOP ===; /tmp_bt/a6400-film-ui-stop.elf --check 2>&1 || true\n"
        "echo === UI_TRACE ===; /tmp_bt/a6400-film-ui-stop.elf --dump-trace 2>&1 || true\n"
        "echo === MANIFEST ===; cat /tmp_bt/a6400-film-capture.manifest 2>&1 || true\n"
        "echo === RUNTIME_LOG ===; cat /tmp_bt/a6400-film-capture.runtime.log 2>&1 || true\n"
        "echo === JOB_LOG ===; cat /tmp_bt/a6400-film-capture.job.log 2>&1 || true\n"
        "echo __STATUS_DONE__\n"
    )
    for line in cmd.splitlines(True):
        cam.dev.writeTerminal(line.encode("latin1")); time.sleep(0.04)
    out = drain(cam, "__STATUS_DONE__", 30.0)
    cam.setTerminalEnable(False)
    (logs / "phase6-status.log").write_text(out)
    print(out)

senserShellCommand(complete=body)
PY
}

cmd_remote_shoot() {
    local rargs=("${EXTRA_ARGS[@]}")
    if [ ${#rargs[@]} -eq 0 ]; then
        rargs=("shutter")
    fi
    if [ "$DRY_RUN" = 1 ]; then
        printf "=== DRY-RUN: phase6 remote-shoot ===\n  %s %s\n" "/tmp_bt/a7r4a-remote-shoot.elf" "${rargs[*]}"
        return 0
    fi
    for tok in "${rargs[@]}"; do
        case "$tok" in
            *[!A-Za-z0-9_.:=-]*) echo "FATAL: unsafe remote-shoot arg: $tok" >&2; exit 2 ;;
        esac
    done
    run_pmca_python - "$PMCA_DIR" "${rargs[*]}" <<'PY'
import re, sys, time
pmca_dir, argline = sys.argv[1:3]
sys.path.insert(0, pmca_dir)
from pmca.commands.usb import senserShellCommand

def body(cam):
    cam.readHasp(); cam.setTerminalEnable(False); cam.setTerminalEnable(True); time.sleep(0.5)
    cmd = f"/tmp_bt/a7r4a-remote-shoot.elf {argline}; echo __RS_RC__=$?; echo __RS_DONE__\n"
    cam.dev.writeTerminal(cmd.encode("latin1"))
    deadline = time.monotonic() + 30.0; chunks = []
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            chunks.append(d.decode("latin1", "replace"))
            if re.search(r"(?m)^__RS_DONE__\r?$", "".join(chunks)): break
        else: time.sleep(0.05)
    cam.setTerminalEnable(False)
    print("".join(chunks))

senserShellCommand(complete=body)
PY
}

cmd_collect() {
    local ui_runtime
    ui_runtime=$(pinned_ui_runtime_path)
    if [ "$DRY_RUN" = 1 ]; then
        cat <<EOF
=== DRY-RUN: phase6 collect ===
  1. : > /tmp_bt/a6400-film-capture.stop
  2. wait boundedly (up to 130s) for /tmp_bt/a6400-capture-v1.lock & /tmp_bt/a6400-film-job.lock exit (never force-kill in-flight 61MP render)
  3. pull /tmp_bt/a6400-film-capture.{manifest,runtime.log,job.log} + daemon.stdout.log + menu snapshot + --dump-trace
  4. /tmp_bt/a6400-film-ui-stop.elf --request
  5. /tmp_bt/a7r4a-ui-load-guard.elf remove /tmp_bt/a7r4a-ui-remove.req && check /tmp_bt/a7r4a-ui-check.req
  6. if no lock/inflight remains: rm staged files in /tmp_bt/ (incl $ui_runtime) & umount /tmp_bt
EOF
        return 0
    fi
    run_pmca_python - "$PMCA_DIR" "$LOG_DIR" "$ui_runtime" <<'PY'
from pathlib import Path
import re, subprocess, sys, time

pmca_dir, logs, ui_runtime = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, pmca_dir)
from pmca.commands.usb import senserShellCommand
from pmca.usb.driver.generic import GenericUsbException

def unmount_sony_msc_py():
    out = subprocess.check_output(["ioreg", "-p", "IOService", "-l", "-w", "0"], text=True, errors="replace")
    if "Sony" in out:
        disks = subprocess.check_output(["diskutil", "list", "external"], text=True, errors="replace")
        for m in re.finditer(r"(/dev/disk\d+)", disks):
            info = subprocess.check_output(["diskutil", "info", m.group(1)], text=True, errors="replace")
            if "Sony" in info or "ILCE" in info or "DSC" in info:
                subprocess.run(["diskutil", "unmountDisk", "force", m.group(1)], check=False)

def drain(cam, marker, timeout_sec=60.0):
    pat = re.compile(rf"(?m)^{re.escape(marker)}\r?$")
    deadline = time.monotonic() + timeout_sec
    chunks = []
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            chunks.append(d.decode("latin1", "replace"))
            if pat.search("".join(chunks)):
                return "".join(chunks)
        else:
            time.sleep(0.05)
    return "".join(chunks)

def send_lines(cam, script, delay=0.05):
    for line in script.splitlines(True):
        cam.dev.writeTerminal(line.encode("latin1")); time.sleep(delay)

def body(cam):
    cam.readHasp(); cam.setTerminalEnable(False); cam.setTerminalEnable(True); time.sleep(1.0)
    out_parts = []
    step1 = "\n".join([
        "if ! grep -q ' /tmp_bt tmpfs ' /proc/mounts; then echo CAMERA_REBOOTED_RAM_CLEARED; echo __COLLECT_DONE__; echo __COLLECT_STEP1__; else",
        "  : > /tmp_bt/a6400-film-capture.stop",
        "  i=0; while [ $i -lt 130 ]; do",
        "    if [ ! -e /tmp_bt/a6400-film-job.lock ] && [ ! -e /tmp_bt/a6400-film-capture.inflight ]; then",
        "      if [ ! -e /tmp_bt/a6400-capture-v1.lock ]; then break; fi",
        "      d_pid=$(/bin/busybox sed -n 's/^pid=//p' /tmp_bt/a6400-capture-v1.lock 2>/dev/null || true)",
        "      if ! /bin/busybox pidof a6400-film-capture-daemon.elf >/dev/null 2>&1 && { [ -z \"$d_pid\" ] || ! kill -0 \"$d_pid\" 2>/dev/null; }; then break; fi",
        "    fi",
        "    i=$((i+1)); /bin/busybox sleep 1",
        "  done",
        "  echo __COLLECT_STEP1__",
        "fi",
    ]) + "\n"
    send_lines(cam, step1)
    p1 = drain(cam, "__COLLECT_STEP1__", 140.0)
    out_parts.append(p1)
    if not re.search(r"(?m)^__COLLECT_DONE__\r?$", p1):
        step2 = "\n".join([
            "echo === MENU ===; /tmp_bt/a6400-film-menu-job.elf 2>&1 || true",
            "echo === UI_STOP ===; /tmp_bt/a6400-film-ui-stop.elf --dump-trace 2>&1 || true",
            "/tmp_bt/a6400-film-ui-stop.elf --request 2>&1 || true",
            "echo === MANIFEST ===; cat /tmp_bt/a6400-film-capture.manifest 2>&1 || true",
            "echo === RUNTIME_LOG ===; cat /tmp_bt/a6400-film-capture.runtime.log 2>&1 || true",
            "echo === JOB_LOG ===; cat /tmp_bt/a6400-film-capture.job.log 2>&1 || true",
            "echo __COLLECT_STEP2__",
        ]) + "\n"
        send_lines(cam, step2)
        p2 = drain(cam, "__COLLECT_STEP2__", 30.0)
        out_parts.append(p2)

        step3 = "\n".join([
            "echo === HOOK_REMOVE ===",
            "/tmp_bt/a7r4a-ui-load-guard.elf remove /tmp_bt/a7r4a-ui-remove.req; r_rc=$?",
            "if [ \"$r_rc\" = 2 ]; then /tmp_bt/a7r4a-ui-load-guard.elf /tmp_bt/a7r4a-ui-remove.req; r_rc=$?; fi",
            "echo __REMOVE_RC__=$r_rc",
            "/tmp_bt/a7r4a-ui-load-guard.elf check /tmp_bt/a7r4a-ui-check.req; echo __POST_CHECK_RC__=$?",
            "echo __COLLECT_STEP3__",
        ]) + "\n"
        send_lines(cam, step3)
        p3 = drain(cam, "__COLLECT_STEP3__", 30.0)
        out_parts.append(p3)

        step4 = "\n".join([
            "busy=0",
            "for lk in /tmp_bt/a6400-film-job.lock /tmp_bt/a6400-film-capture.inflight; do if [ -e \"$lk\" ]; then busy=1; fi; done",
            "if [ -e /tmp_bt/a6400-capture-v1.lock ]; then d_pid=$(/bin/busybox sed -n 's/^pid=//p' /tmp_bt/a6400-capture-v1.lock 2>/dev/null || true); if /bin/busybox pidof a6400-film-capture-daemon.elf >/dev/null 2>&1 || { [ -n \"$d_pid\" ] && kill -0 \"$d_pid\" 2>/dev/null; }; then busy=1; else echo STALE_CAPTURE_LOCK_CLEANED; rm -f /tmp_bt/a6400-capture-v1.lock; fi; fi",
            "if [ \"$busy\" = 0 ]; then rm -f /tmp_bt/a7r4a-ui-load-guard.elf /tmp_bt/a7r4a-ui-check.req /tmp_bt/a7r4a-ui-install.req /tmp_bt/a7r4a-ui-remove.req " + ui_runtime + " /tmp_bt/a6400-film-ui-v3.so /tmp_bt/a6400-film-menu-job.elf /tmp_bt/a6400-film-ui-stop.elf /tmp_bt/a6400-film-native-command.elf /tmp_bt/a7r4a-film-job.elf /tmp_bt/a6400-film-ram-job.sh /tmp_bt/a6400-film-capture-daemon.elf /tmp_bt/a7r4a-remote-shoot.elf /tmp_bt/a6400-film-capture.stop /tmp_bt/a7r4a-ui-probe.log /tmp_bt/daemon.stdout.log /tmp_bt/a6400-film-capture.manifest /tmp_bt/a6400-film-capture.runtime.log /tmp_bt/a6400-film-capture.job.log; if umount /tmp_bt 2>/dev/null; then echo UMOUNT_OK; else echo UMOUNT_BUSY_DLOPEN_HELD; fi; else echo RETAINED_LOCK_OR_INFLIGHT; fi",
            "echo __COLLECT_DONE__",
        ]) + "\n"
        send_lines(cam, step4)
        p4 = drain(cam, "__COLLECT_DONE__", 30.0)
        out_parts.append(p4)

    out = "".join(out_parts)
    (logs / "phase6-collect.log").write_text(out)
    print(out)
    try:
        cam.setTerminalEnable(False)
    except GenericUsbException as exc:
        print(f"WARN: setTerminalEnable(False) failed: {exc}", file=sys.stderr)

for attempt in range(2):
    try:
        senserShellCommand(complete=body); sys.exit(0)
    except GenericUsbException:
        if attempt == 1: raise
        time.sleep(2.5); unmount_sony_msc_py()
PY
}

case "$SUBCMD" in
    check) cmd_check ;;
    start) cmd_start ;;
    status) cmd_status ;;
    remote-shoot) cmd_remote_shoot ;;
    collect) cmd_collect ;;
    all-dry-run)
        DRY_RUN=1
        cmd_check
        cmd_start
        cmd_status
        EXTRA_ARGS=("--dry-run" "shutter")
        cmd_remote_shoot
        cmd_collect
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
