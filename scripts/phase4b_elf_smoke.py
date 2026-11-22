#!/usr/bin/env python3
"""Phase 4B RAM-Only (/tmp_bt/) Hardware ELF Smoke Runner for ILCE-7RM4A Ver. 1.12.

Guardrails:
- Strictly RAM-only (/tmp_bt/ tmpfs); zero touches to /etc/ld.so.preload, /dev/nflasha*, or SD cards.
- Verifies SHA-256 on camera via /bin/busybox sha256sum before chmod/execution.
- Non-echo marker pattern (echo "__""MARKER") + line-anchored regex matching.
- Never runs watch, never passes --request to film_ui_stop_cli, cleans up and unmounts /tmp_bt.
"""

import base64
import gzip
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

_REPO = Path(__file__).resolve().parents[1]
ROOT = _REPO if (_REPO / "dist_a7r4a").exists() else _REPO.parent
DIST = ROOT / "dist_a7r4a"
PMCA_DIR = ROOT / "Sony-PMCA-RE"
LOG_DIR = ROOT / "probe_logs" / "phase4"
JPEG_SRC = ROOT / "third_party" / "jpeg-9f"

sys.path.insert(0, str(PMCA_DIR))
from pmca.commands.usb import senserShellCommand
from pmca.usb.driver.generic import GenericUsbException

JPEG_TOOL_C = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <jpeglib.h>

static int make_jpg(const char *path, int w, int h) {
    FILE *fp = fopen(path, "wb");
    if (!fp) return 2;
    struct jpeg_compress_struct cinfo;
    struct jpeg_error_mgr jerr;
    cinfo.err = jpeg_std_error(&jerr);
    jpeg_create_compress(&cinfo);
    jpeg_stdio_dest(&cinfo, fp);
    cinfo.image_width = (JDIMENSION)w;
    cinfo.image_height = (JDIMENSION)h;
    cinfo.input_components = 3;
    cinfo.in_color_space = JCS_RGB;
    jpeg_set_defaults(&cinfo);
    jpeg_set_quality(&cinfo, 90, TRUE);
    jpeg_start_compress(&cinfo, TRUE);

    const unsigned char exif[] = "Exif\0\0MM\0*\0\0\0\x08\0\0\0\0\0\0";
    const unsigned char com[] = "A7R4A_TEST_COMMENT";
    jpeg_write_marker(&cinfo, JPEG_APP0 + 1, exif, sizeof(exif) - 1);
    jpeg_write_marker(&cinfo, JPEG_COM, com, sizeof(com) - 1);

    unsigned char *row = (unsigned char *)malloc((size_t)w * 3);
    if (!row) return 3;
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            int hi = (x >= w / 4 && x < w / 2 && y >= h / 4 && y < h / 2);
            row[x * 3 + 0] = hi ? 250 : (unsigned char)((x * 2) & 0xff);
            row[x * 3 + 1] = hi ? 248 : (unsigned char)((y * 2) & 0xff);
            row[x * 3 + 2] = hi ? 245 : 96;
        }
        JSAMPROW ptr = row;
        jpeg_write_scanlines(&cinfo, &ptr, 1);
    }
    free(row);
    jpeg_finish_compress(&cinfo);
    jpeg_destroy_compress(&cinfo);
    fclose(fp);
    return 0;
}

static int inspect_jpg(const char *path) {
    FILE *fp = fopen(path, "rb");
    if (!fp) return 2;
    struct jpeg_decompress_struct cinfo;
    struct jpeg_error_mgr jerr;
    cinfo.err = jpeg_std_error(&jerr);
    jpeg_create_decompress(&cinfo);
    jpeg_stdio_src(&cinfo, fp);
    jpeg_save_markers(&cinfo, JPEG_COM, 0xffff);
    jpeg_save_markers(&cinfo, JPEG_APP0 + 1, 0xffff);
    if (jpeg_read_header(&cinfo, TRUE) != JPEG_HEADER_OK) return 3;
    int has_app1 = 0, has_com = 0;
    for (jpeg_saved_marker_ptr m = cinfo.marker_list; m; m = m->next) {
        if (m->marker == JPEG_APP0 + 1 && m->data_length >= 4 && memcmp(m->data, "Exif", 4) == 0)
            has_app1 = 1;
        if (m->marker == JPEG_COM && m->data_length == 18 && memcmp(m->data, "A7R4A_TEST_COMMENT", 18) == 0)
            has_com = 1;
    }
    cinfo.out_color_space = JCS_RGB;
    jpeg_start_decompress(&cinfo);
    unsigned char *row = (unsigned char *)malloc(cinfo.output_width * 3);
    unsigned long sum = 0;
    while (cinfo.output_scanline < cinfo.output_height) {
        JSAMPROW ptr = row;
        jpeg_read_scanlines(&cinfo, &ptr, 1);
        for (JDIMENSION i = 0; i < cinfo.output_width * 3; ++i) sum += row[i];
    }
    free(row);
    printf("JPG_INSPECT w=%u h=%u app1=%d com=%d sum=%lu\n",
           (unsigned)cinfo.output_width, (unsigned)cinfo.output_height, has_app1, has_com, sum);
    jpeg_finish_decompress(&cinfo);
    jpeg_destroy_decompress(&cinfo);
    fclose(fp);
    return (has_app1 && has_com) ? 0 : 4;
}

int main(int argc, char **argv) {
    if (argc == 4 && strcmp(argv[1], "make") == 0) return make_jpg(argv[2], 128, 96);
    if (argc == 3 && strcmp(argv[1], "inspect") == 0) return inspect_jpg(argv[2]);
    return 1;
}
"""

def build_host_jpeg_helper(work_dir: Path) -> Path:
    cfiles = [
        "jaricom.c", "jcapimin.c", "jcapistd.c", "jcarith.c", "jccoefct.c", "jccolor.c",
        "jcdctmgr.c", "jchuff.c", "jcinit.c", "jcmainct.c", "jcmarker.c", "jcmaster.c",
        "jcomapi.c", "jcparam.c", "jcprepct.c", "jcsample.c", "jctrans.c", "jdapimin.c",
        "jdapistd.c", "jdarith.c", "jdatadst.c", "jdatasrc.c", "jdcoefct.c", "jdcolor.c",
        "jddctmgr.c", "jdhuff.c", "jdinput.c", "jdmainct.c", "jdmarker.c", "jdmaster.c",
        "jdmerge.c", "jdpostct.c", "jdsample.c", "jdtrans.c", "jerror.c", "jfdctflt.c",
        "jfdctfst.c", "jfdctint.c", "jidctflt.c", "jidctfst.c", "jidctint.c", "jquant1.c",
        "jquant2.c", "jutils.c", "jmemmgr.c", "jmemnobs.c",
    ]
    objs = []
    for cf in cfiles:
        obj = work_dir / (cf + ".o")
        subprocess.run(["clang", "-O2", "-I", str(JPEG_SRC), "-c", str(JPEG_SRC / cf), "-o", str(obj)], check=True)
        objs.append(str(obj))
    tool_c = work_dir / "jpeg_tool.c"
    tool_c.write_text(JPEG_TOOL_C)
    tool_bin = work_dir / "jpeg_tool"
    subprocess.run(["clang", "-O2", "-I", str(JPEG_SRC), str(tool_c), *objs, "-o", str(tool_bin)], check=True)
    return tool_bin

def unmount_sony_msc_py():
    out = subprocess.check_output(["ioreg", "-p", "IOService", "-l", "-w", "0"], text=True, errors="replace")
    in_sony = False; sony_depth = 0; last_node_depth = 0
    for line in out.splitlines():
        depth = len(line) - len(line.lstrip(" |"))
        if "+-o " in line:
            if in_sony and depth <= sony_depth:
                in_sony = False
            last_node_depth = depth
        if not in_sony and ('"USB Product Name" = "ILCE-7RM4A"' in line or '"kUSBProductString" = "ILCE-7RM4A"' in line):
            in_sony = True; sony_depth = last_node_depth; continue
        if in_sony:
            m = re.search(r'"BSD Name" = "(disk[0-9]+)"', line)
            if m:
                dev = f"/dev/{m.group(1)}"
                info = subprocess.run(["diskutil", "info", dev], capture_output=True, text=True).stdout
                if re.search(r"Device Location:\s+External", info) and re.search(r"Device / Media Name:.*(DSC|Mass-Storage)", info):
                    print(f"Releasing Sony MSC disk {dev}...")
                    subprocess.run(["diskutil", "unmountDisk", "force", dev], check=False)

def drain_pending(cam):
    while True:
        try:
            d = cam.dev.driver.read(0x40, ep=1, timeout=5)
            if not d:
                break
        except GenericUsbException:
            break

def drain_terminal(cam, marker: str, timeout_sec: float = 30.0) -> str:
    pat = re.compile(rf"(?m)^{re.escape(marker)}\r?$")
    deadline = time.monotonic() + timeout_sec
    chunks = []
    while time.monotonic() < deadline:
        d = cam.dev.readTerminal()
        if d:
            chunks.append(d.decode("latin1", "replace"))
            joined = "".join(chunks)
            if pat.search(joined):
                return joined
        else:
            time.sleep(0.05)
    return "".join(chunks)

def send_shell_lines(cam, script: str, line_delay: float = 0.04):
    for line in script.splitlines(True):
        cam.dev.writeTerminal(line.encode("latin1"))
        time.sleep(line_delay)

def stream_ram_file(cam, tag: str, remote_path: str, raw: bytes, line_delay: float = 0.01):
    assert re.fullmatch(r"/tmp_bt/[A-Za-z0-9._-]+", remote_path), f"Unsafe remote path: {remote_path}"
    digest = hashlib.sha256(raw).hexdigest()
    gz = gzip.compress(raw, compresslevel=9)
    b64_lines = base64.encodebytes(gz).decode("ascii").splitlines()
    print(f"Streaming {remote_path} ({len(raw)} B -> {len(gz)} B gz, {len(b64_lines)} b64 lines)...")
    drain_pending(cam)
    send_shell_lines(cam, f"/bin/busybox base64 -d <<'__B64EOF__' | /bin/busybox gunzip -c > {remote_path}\n", 0.04)
    for idx, line in enumerate(b64_lines):
        cam.dev.writeTerminal((line + "\n").encode("ascii"))
        time.sleep(line_delay)
        if (idx + 1) % 8 == 0:
            drain_pending(cam)
    drain_pending(cam)
    send_shell_lines(
        cam,
        "__B64EOF__\n"
        f'set -- $(/bin/busybox sha256sum {remote_path}) && test "$1" = "{digest}" && '
        f'echo "__""HASH_OK_{tag}__" || echo "__""HASH_BAD_{tag}__"\n',
        0.04,
    )
    out = drain_terminal(cam, f"__HASH_OK_{tag}__", timeout_sec=20.0)
    if not re.search(rf"(?m)^__HASH_OK_{re.escape(tag)}__\r?$", out):
        raise SystemExit(f"SHA256_MISMATCH for {remote_path}:\n{out}")

def run_cmd_block(cam, tag: str, cmd: str, timeout_sec: float = 30.0) -> str:
    drain_pending(cam)
    send_shell_lines(cam, cmd + f'\necho "__""{tag}_DONE__"\n', 0.04)
    out = drain_terminal(cam, f"__{tag}_DONE__", timeout_sec=timeout_sec)
    if not re.search(rf"(?m)^__{re.escape(tag)}_DONE__\r?$", out):
        raise SystemExit(f"TIMEOUT waiting for __{tag}_DONE__:\n{out}")
    return out

def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        jpeg_tool = build_host_jpeg_helper(work)
        smoke_in = work / "smoke_in.jpg"
        subprocess.run([str(jpeg_tool), "make", str(smoke_in), "unused"], check=True)
        in_inspect = subprocess.check_output([str(jpeg_tool), "inspect", str(smoke_in)], text=True).strip()
        smoke_in_bytes = smoke_in.read_bytes()
        smoke_in_sha = hashlib.sha256(smoke_in_bytes).hexdigest()
        (LOG_DIR / "smoke_in.jpg").write_bytes(smoke_in_bytes)
        print(f"Host smoke_in.jpg: {len(smoke_in_bytes)} B sha256={smoke_in_sha} {in_inspect}")

        files = {
            "job": (DIST / "a7r4a-film-job.elf").read_bytes(),
            "ui_so": (DIST / "a6400-film-ui-v3.so").read_bytes(),
            "menu": (DIST / "a6400-film-menu-job.elf").read_bytes(),
            "stop": (DIST / "a6400-film-ui-stop.elf").read_bytes(),
            "cap_probe": (DIST / "a6400-capture-probe-v4.elf").read_bytes(),
            "guard": (DIST / "a7r4a-ui-load-guard.elf").read_bytes(),
            "ui_probe": (DIST / "a7r4a-ui-probe.so").read_bytes(),
            "check_req": (DIST / "a7r4a-check.req").read_bytes(),
            "inst_req": (DIST / "a7r4a-install.req").read_bytes(),
            "rem_req": (DIST / "a7r4a-remove.req").read_bytes(),
        }
        transcript = []

        def callback(cam):
            cam.readHasp()
            cam.setTerminalEnable(False)
            cam.setTerminalEnable(True)
            time.sleep(1.0)
            drain_pending(cam)
            try:
                prep = run_cmd_block(
                    cam,
                    "PREP",
                    'if test -e /etc/ld.so.preload || test -L /etc/ld.so.preload; then echo PRELOAD_PRESENT; exit 90; fi\n'
                    'if grep -Eq "^/dev/mmc" /proc/mounts; then echo LINUX_SD_MOUNTED; exit 91; fi\n'
                    'if ! grep -q " /tmp_bt tmpfs " /proc/mounts; then mount -t tmpfs -o size=16m tmpfs /tmp_bt; fi\n'
                    'grep " /tmp_bt " /proc/mounts',
                    15.0,
                )
                transcript.append(prep)
                if not re.search(r"(?m)^tmpfs /tmp_bt tmpfs ", prep):
                    raise SystemExit(f"TMPFS_MOUNT_FAILED:\n{prep}")

                # --- Item 1: a7r4a-film-job.elf ---
                stream_ram_file(cam, "FILMJOB", "/tmp_bt/a7r4a-film-job.elf", files["job"])
                stream_ram_file(cam, "SMOKEIN", "/tmp_bt/smoke_in.jpg", smoke_in_bytes)
                out1 = run_cmd_block(
                    cam,
                    "ITEM1",
                    "chmod 755 /tmp_bt/a7r4a-film-job.elf && chmod 644 /tmp_bt/smoke_in.jpg && rm -f /tmp_bt/smoke_out.jpg\n"
                    '/tmp_bt/a7r4a-film-job.elf --size /tmp_bt/smoke_in.jpg; echo "__""ITEM1_SIZE_RC__=$?"\n'
                    '/tmp_bt/a7r4a-film-job.elf --verify /tmp_bt/smoke_in.jpg; echo "__""ITEM1_VIN_RC__=$?"\n'
                    '/tmp_bt/a7r4a-film-job.elf portra_400 /tmp_bt/smoke_in.jpg /tmp_bt/smoke_out.jpg 2 1; echo "__""ITEM1_RENDER_RC__=$?"\n'
                    '/tmp_bt/a7r4a-film-job.elf --verify /tmp_bt/smoke_out.jpg; echo "__""ITEM1_VOUT_RC__=$?"\n'
                    '/bin/busybox sha256sum /tmp_bt/smoke_in.jpg\n'
                    'echo "__""OUTJPG_B64_BEGIN__"\n'
                    '/bin/busybox base64 /tmp_bt/smoke_out.jpg\n'
                    'echo "__""OUTJPG_B64_END__"\n'
                    'rm -f /tmp_bt/a7r4a-film-job.elf /tmp_bt/smoke_in.jpg /tmp_bt/smoke_out.jpg',
                    40.0,
                )
                transcript.append(out1)
                print(out1)

                # --- Items 2 & 3: a6400-film-menu-job.elf & a6400-film-ui-stop.elf --check ---
                stream_ram_file(cam, "UISO", "/tmp_bt/a6400-film-ui-v3.so", files["ui_so"])
                stream_ram_file(cam, "MENU", "/tmp_bt/a6400-film-menu-job.elf", files["menu"])
                stream_ram_file(cam, "STOP", "/tmp_bt/a6400-film-ui-stop.elf", files["stop"])
                out23 = run_cmd_block(
                    cam,
                    "ITEM23",
                    "chmod 755 /tmp_bt/a6400-film-ui-v3.so /tmp_bt/a6400-film-menu-job.elf /tmp_bt/a6400-film-ui-stop.elf\n"
                    '/tmp_bt/a6400-film-menu-job.elf; echo "__""ITEM2_RC__=$?"\n'
                    '/tmp_bt/a6400-film-ui-stop.elf --check; echo "__""ITEM3_RC__=$?"\n'
                    'rm -f /tmp_bt/a6400-film-ui-v3.so /tmp_bt/a6400-film-menu-job.elf /tmp_bt/a6400-film-ui-stop.elf',
                    20.0,
                )
                transcript.append(out23)
                print(out23)

                # --- Item 4: a6400-capture-probe-v4.elf scan ---
                stream_ram_file(cam, "CAPPROBE", "/tmp_bt/a6400-capture-probe-v4.elf", files["cap_probe"])
                out4 = run_cmd_block(
                    cam,
                    "ITEM4",
                    "chmod 755 /tmp_bt/a6400-capture-probe-v4.elf && test ! -e /tmp_bt/a6400-capture-v1.lock\n"
                    '/tmp_bt/a6400-capture-probe-v4.elf scan; echo "__""ITEM4_RC__=$?"\n'
                    'if test -e /tmp_bt/a6400-capture-v1.lock; then echo LOCK_LEAKED; else echo LOCK_CLEAN; fi\n'
                    'ps | grep -E "[m]pr_monio|[c]apture-probe" || echo NO_STRAY_PROCS\n'
                    'rm -f /tmp_bt/a6400-capture-probe-v4.elf',
                    30.0,
                )
                transcript.append(out4)
                print(out4)

                # --- Item 5: a7r4a-ui-load-guard.elf install -> sleep 1 -> remove -> check ---
                stream_ram_file(cam, "GUARD", "/tmp_bt/a7r4a-ui-load-guard.elf", files["guard"])
                stream_ram_file(cam, "UIPROBE", "/tmp_bt/a7r4a-ui-probe.so", files["ui_probe"])
                stream_ram_file(cam, "CHKREQ", "/tmp_bt/a7r4a-check.req", files["check_req"])
                stream_ram_file(cam, "INSTREQ", "/tmp_bt/a7r4a-install.req", files["inst_req"])
                stream_ram_file(cam, "REMREQ", "/tmp_bt/a7r4a-remove.req", files["rem_req"])
                out5 = run_cmd_block(
                    cam,
                    "ITEM5",
                    "chmod 755 /tmp_bt/a7r4a-ui-load-guard.elf /tmp_bt/a7r4a-ui-probe.so && "
                    "chmod 644 /tmp_bt/a7r4a-check.req /tmp_bt/a7r4a-install.req /tmp_bt/a7r4a-remove.req && "
                    "rm -f /tmp_bt/a7r4a-ui-probe.log\n"
                    '/tmp_bt/a7r4a-ui-load-guard.elf install /tmp_bt/a7r4a-install.req; echo "__""ITEM5_INST_RC__=$?"\n'
                    "sleep 1\n"
                    '/tmp_bt/a7r4a-ui-load-guard.elf remove /tmp_bt/a7r4a-remove.req; rrc=$?; '
                    'if [ "$rrc" != 0 ]; then sleep 1; /tmp_bt/a7r4a-ui-load-guard.elf remove /tmp_bt/a7r4a-remove.req; rrc=$?; fi; '
                    'echo "__""ITEM5_REM_RC__=$rrc"\n'
                    'if [ -f /tmp_bt/a7r4a-ui-probe.log ]; then echo "__""UIPROBE_LOG__"; cat /tmp_bt/a7r4a-ui-probe.log; fi\n'
                    '/tmp_bt/a7r4a-ui-load-guard.elf check /tmp_bt/a7r4a-check.req; echo "__""ITEM5_POSTCHK_RC__=$?"',
                    30.0,
                )
                transcript.append(out5)
                print(out5)

                # --- Item 6: Cleanup & Post-check ---
                out6 = run_cmd_block(
                    cam,
                    "ITEM6",
                    "rm -f /tmp_bt/a7r4a-ui-load-guard.elf /tmp_bt/a7r4a-ui-probe.so "
                    "/tmp_bt/a7r4a-check.req /tmp_bt/a7r4a-install.req /tmp_bt/a7r4a-remove.req /tmp_bt/a7r4a-ui-probe.log\n"
                    'ls -la /tmp_bt\n'
                    'if grep -q " /tmp_bt tmpfs " /proc/mounts; then umount /tmp_bt; echo "UMOUNT_RC=$?"; fi\n'
                    'grep -E "/dev/mmc|/tmp_bt" /proc/mounts || echo MOUNTS_CLEAN\n'
                    'ls -la /etc/ld.so.preload 2>&1 || true\n'
                    'grep -l "^im\\.elf$" /proc/[0-9]*/comm\n'
                    'ps | grep -E "[m]pr_monio|[i]m\\.elf"',
                    15.0,
                )
                transcript.append(out6)
                print(out6)
            finally:
                try:
                    send_shell_lines(
                        cam,
                        "rm -f /tmp_bt/a7r4a-film-job.elf /tmp_bt/smoke_in.jpg /tmp_bt/smoke_out.jpg "
                        "/tmp_bt/a6400-film-ui-v3.so /tmp_bt/a6400-film-menu-job.elf /tmp_bt/a6400-film-ui-stop.elf "
                        "/tmp_bt/a6400-capture-probe-v4.elf /tmp_bt/a7r4a-ui-load-guard.elf /tmp_bt/a7r4a-ui-probe.so "
                        "/tmp_bt/a7r4a-check.req /tmp_bt/a7r4a-install.req /tmp_bt/a7r4a-remove.req /tmp_bt/a7r4a-ui-probe.log\n"
                        'if grep -q " /tmp_bt tmpfs " /proc/mounts; then umount /tmp_bt; fi\n',
                        0.03,
                    )
                    time.sleep(0.3)
                except Exception:
                    pass
                try:
                    cam.setTerminalEnable(False)
                except Exception:
                    pass

        unmount_sony_msc_py()
        called = False
        def wrapped(cam):
            nonlocal called
            called = True
            callback(cam)
        for attempt in range(2):
            try:
                senserShellCommand(complete=wrapped)
                if called:
                    break
            except GenericUsbException as exc:
                if attempt == 1:
                    raise
                print(f"Retrying USB Senser ({exc.__class__.__name__})...")
            time.sleep(2.5)
            unmount_sony_msc_py()
        if not called:
            raise SystemExit("CAMERA_NOT_CONNECTED")

        full_text = "\n".join(transcript)
        (LOG_DIR / "phase4b_smoke.log").write_text(full_text)

        m_b64 = re.search(
            r"(?m)^(?:/usr/share/app # )?/bin/busybox base64 /tmp_bt/smoke_out\.jpg\r?\n(.*?)^(?:/usr/share/app # )?echo \"__\"\"OUTJPG_B64_END__\"",
            full_text,
            re.S,
        )
        assert m_b64, "Missing smoke_out.jpg base64 block"
        b64_lines = [l.strip() for l in m_b64.group(1).splitlines() if re.fullmatch(r"[A-Za-z0-9+/=]+", l.strip())]
        smoke_out_bytes = base64.b64decode("".join(b64_lines))
        smoke_out_path = LOG_DIR / "smoke_out.jpg"
        smoke_out_path.write_bytes(smoke_out_bytes)
        out_inspect = subprocess.check_output([str(jpeg_tool), "inspect", str(smoke_out_path)], text=True).strip()
        print(f"HOST_VERIFIED smoke_out.jpg bytes={len(smoke_out_bytes)} {out_inspect}")

        assert re.search(r"(?m)^__ITEM1_SIZE_RC__=0\r?$", full_text)
        assert re.search(r"(?m)^__ITEM1_VIN_RC__=0\r?$", full_text)
        assert re.search(r"(?m)^__ITEM1_RENDER_RC__=0\r?$", full_text)
        assert re.search(r"(?m)^__ITEM1_VOUT_RC__=0\r?$", full_text)
        assert smoke_in_sha in full_text
        assert re.search(r"(?m)^__ITEM2_RC__=11\r?$", full_text) and "FILM_UNAVAILABLE source=10" in full_text
        assert re.search(r"(?m)^__ITEM3_RC__=11\r?$", full_text) and "FILM_UI_STOP_UNAVAILABLE reason=maps" in full_text
        assert re.search(r"(?m)^__ITEM4_RC__=1\r?$", full_text) and "LOCK_CLEAN" in full_text
        assert re.search(r"(?m)^__ITEM5_INST_RC__=0\r?$", full_text) and "INSTALLED " in full_text
        assert re.search(r"(?m)^__ITEM5_REM_RC__=0\r?$", full_text) and "restored=1" in full_text
        assert re.search(r"(?m)^__ITEM5_POSTCHK_RC__=0\r?$", full_text) and "zero_pad=1 pc_clear=1" in full_text
        assert "UMOUNT_RC=0" in full_text and "MOUNTS_CLEAN" in full_text
        print("ALL_PHASE4B_SMOKE_ASSERTIONS_PASSED")

if __name__ == "__main__":
    main()
