#!/usr/bin/env python3
"""Live 61MP (9504x6336) camera-CPU & memory benchmark in RAM-only /tmp_bt/ (32m tmpfs).

Safety invariants:
- Strictly RAM-only (/tmp_bt/ tmpfs size=32m); unmounts /tmp_bt cleanly in finally.
- Zero writes to /etc/ld.so.preload, /dev/nflasha*, or SD cards.
- Separates writeFile USB bulk transfer into its own Senser session so setTerminalEnable
  is never re-toggled after writeFile; verifies exact SHA-256 on camera before execution.
"""

import hashlib
import io
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
            row[x * 3 + 0] = hi ? 250 : 120;
            row[x * 3 + 1] = hi ? 248 : 115;
            row[x * 3 + 2] = hi ? 245 : 110;
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
    unsigned long long sum = 0;
    while (cinfo.output_scanline < cinfo.output_height) {
        JSAMPROW ptr = row;
        jpeg_read_scanlines(&cinfo, &ptr, 1);
        for (JDIMENSION i = 0; i < cinfo.output_width * 3; ++i) sum += row[i];
    }
    free(row);
    printf("JPG_INSPECT w=%u h=%u app1=%d com=%d sum=%llu\n",
           (unsigned)cinfo.output_width, (unsigned)cinfo.output_height, has_app1, has_com, sum);
    jpeg_finish_decompress(&cinfo);
    jpeg_destroy_decompress(&cinfo);
    fclose(fp);
    return (has_app1 && has_com) ? 0 : 4;
}

int main(int argc, char **argv) {
    if (argc == 3 && strcmp(argv[1], "make61") == 0) return make_jpg(argv[2], 9504, 6336);
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

def run_senser_session(cb):
    unmount_sony_msc_py()
    called = False
    def wrapped(cam):
        nonlocal called
        called = True
        cb(cam)
    for attempt in range(3):
        try:
            senserShellCommand(complete=wrapped)
            if called:
                return
            print("Mode switch poll exceeded 5s; retrying in 2.5s...")
        except GenericUsbException as exc:
            if attempt == 2:
                raise
            print(f"Senser reconnect ({exc.__class__.__name__}); waiting 2.5s...")
        time.sleep(2.5)
        unmount_sony_msc_py()
    raise SystemExit("CAMERA_NOT_CONNECTED")

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
        smoke_in = work / "smoke61_in.jpg"
        subprocess.run([str(jpeg_tool), "make61", str(smoke_in)], check=True)
        in_inspect = subprocess.check_output([str(jpeg_tool), "inspect", str(smoke_in)], text=True).strip()
        in_bytes = smoke_in.read_bytes()
        in_sha = hashlib.sha256(in_bytes).hexdigest()
        assert len(in_bytes) < 2 * 1024 * 1024, f"smoke61_in too large: {len(in_bytes)}"
        (LOG_DIR / "smoke61_in.jpg").write_bytes(in_bytes)
        print(f"Host smoke61_in.jpg: {len(in_bytes)} B sha256={in_sha} {in_inspect}")

        job_bytes = (DIST / "a7r4a-film-job.elf").read_bytes()
        job_sha = hashlib.sha256(job_bytes).hexdigest()
        logs = []

        # Pass 1: Safety check + mount 32m tmpfs at /tmp_bt
        def pass1_mount(cam):
            cam.readHasp()
            cam.setTerminalEnable(False)
            cam.setTerminalEnable(True)
            time.sleep(1.0)
            drain_pending(cam)
            try:
                out = run_cmd_block(
                    cam,
                    "PASS1",
                    'if test -e /etc/ld.so.preload || test -L /etc/ld.so.preload; then echo PRELOAD_PRESENT; exit 90; fi\n'
                    'if grep -Eq "^/dev/mmc" /proc/mounts; then echo LINUX_SD_MOUNTED; exit 91; fi\n'
                    'if grep -q " /tmp_bt tmpfs " /proc/mounts; then umount /tmp_bt; fi\n'
                    'mount -t tmpfs -o size=32m tmpfs /tmp_bt\n'
                    'grep " /tmp_bt " /proc/mounts\n'
                    'cat /proc/meminfo',
                    15.0,
                )
                logs.append(out)
                assert re.search(r"(?m)^tmpfs /tmp_bt tmpfs .*size=32768k", out), out
                print("Pass 1 OK: 32m tmpfs mounted at /tmp_bt")
            finally:
                cam.setTerminalEnable(False)

        # Pass 2: Bulk writeFile ONLY (8192-byte chunks, no terminal toggle!)
        CHUNK = 8192
        job_parts = [(f"/tmp_bt/.j{i:03d}", job_bytes[off:off + CHUNK]) for i, off in enumerate(range(0, len(job_bytes), CHUNK))]
        in_parts = [(f"/tmp_bt/.i{i:03d}", in_bytes[off:off + CHUNK]) for i, off in enumerate(range(0, len(in_bytes), CHUNK))]

        def pass2_push(cam):
            cam.readHasp()
            t0 = time.monotonic()
            for p, chunk in job_parts + in_parts:
                assert re.fullmatch(r"/tmp_bt/[A-Za-z0-9._-]+", p)
                cam.writeFile(p, chunk)
            print(f"Pass 2 OK: pushed {len(job_parts)}+{len(in_parts)} chunks ({len(job_bytes)+len(in_bytes)} B) in {time.monotonic()-t0:.2f}s")

        # Pass 3: Reassemble, SHA verify, meminfo before, timed --verify + portra_400, meminfo after, then pull smoke61_out.jpg via readFile
        pulled_out = bytearray()
        def pass3_bench(cam):
            cam.readHasp()
            cam.setTerminalEnable(False)
            cam.setTerminalEnable(True)
            time.sleep(1.0)
            drain_pending(cam)
            try:
                out_prep = run_cmd_block(
                    cam,
                    "PASS3_PREP",
                    "/bin/busybox cat /tmp_bt/.j??? > /tmp_bt/a7r4a-film-job.elf && rm -f /tmp_bt/.j???\n"
                    "/bin/busybox cat /tmp_bt/.i??? > /tmp_bt/smoke61_in.jpg && rm -f /tmp_bt/.i???\n"
                    f'set -- $(/bin/busybox sha256sum /tmp_bt/a7r4a-film-job.elf) && test "$1" = "{job_sha}" && echo "__""JOB_SHA_OK__"\n'
                    f'set -- $(/bin/busybox sha256sum /tmp_bt/smoke61_in.jpg) && test "$1" = "{in_sha}" && echo "__""IN_SHA_OK__"\n'
                    "chmod 755 /tmp_bt/a7r4a-film-job.elf && chmod 644 /tmp_bt/smoke61_in.jpg && ls -la /tmp_bt",
                    30.0,
                )
                logs.append(out_prep)
                assert re.search(r"(?m)^__JOB_SHA_OK__\r?$", out_prep)
                assert re.search(r"(?m)^__IN_SHA_OK__\r?$", out_prep)

                out_bench = run_cmd_block(
                    cam,
                    "PASS3_BENCH",
                    'echo "__""MEMINFO_BEFORE__"; grep -E "^(MemTotal|MemFree|Buffers|Cached|Shmem|Active|Inactive):" /proc/meminfo\n'
                    'read u0 _ < /proc/uptime; /tmp_bt/a7r4a-film-job.elf --verify /tmp_bt/smoke61_in.jpg; rc_vin=$?; read u1 _ < /proc/uptime\n'
                    'echo "VERIFY_IN rc=$rc_vin t0=$u0 t1=$u1"\n'
                    'read u2 _ < /proc/uptime; /tmp_bt/a7r4a-film-job.elf portra_400 /tmp_bt/smoke61_in.jpg /tmp_bt/smoke61_out.jpg 2 1; rc_ren=$?; read u3 _ < /proc/uptime\n'
                    'echo "RENDER rc=$rc_ren t2=$u2 t3=$u3"\n'
                    '/tmp_bt/a7r4a-film-job.elf --verify /tmp_bt/smoke61_out.jpg; echo "VERIFY_OUT rc=$?"\n'
                    'echo "__""MEMINFO_AFTER__"; grep -E "^(MemTotal|MemFree|Buffers|Cached|Shmem|Active|Inactive):" /proc/meminfo\n'
                    'ls -la /tmp_bt',
                    240.0,
                )
                logs.append(out_bench)
                print(out_bench)
            finally:
                cam.setTerminalEnable(False)

            print("Pulling /tmp_bt/smoke61_out.jpg via cam.readFile...")
            t_pull = time.monotonic()
            buf = io.BytesIO()
            cam.readFile("/tmp_bt/smoke61_out.jpg", buf)
            pulled_out.extend(buf.getvalue())
            print(f"Pulled smoke61_out.jpg ({len(pulled_out)} B) in {time.monotonic()-t_pull:.2f}s")

        # Pass 4: Cleanup /tmp_bt and unmount tmpfs
        def pass4_cleanup(cam):
            cam.readHasp()
            cam.setTerminalEnable(False)
            cam.setTerminalEnable(True)
            time.sleep(1.0)
            drain_pending(cam)
            try:
                out_clean = run_cmd_block(
                    cam,
                    "PASS4",
                    "rm -f /tmp_bt/a7r4a-film-job.elf /tmp_bt/smoke61_in.jpg /tmp_bt/smoke61_out.jpg /tmp_bt/.j* /tmp_bt/.i*\n"
                    "ls -la /tmp_bt\n"
                    'if grep -q " /tmp_bt tmpfs " /proc/mounts; then umount /tmp_bt; echo "UMOUNT_RC=$?"; fi\n'
                    'grep -E "/dev/mmc|/tmp_bt" /proc/mounts || echo MOUNTS_CLEAN\n'
                    'ls -la /etc/ld.so.preload 2>&1 || true\n'
                    'grep -l "^im\\.elf$" /proc/[0-9]*/comm\n'
                    'cat /proc/meminfo | grep -E "^(MemTotal|MemFree|Cached|Shmem):"',
                    15.0,
                )
                logs.append(out_clean)
                print(out_clean)
            finally:
                cam.setTerminalEnable(False)

        run_senser_session(pass1_mount)
        try:
            run_senser_session(pass2_push)
            run_senser_session(pass3_bench)
        finally:
            run_senser_session(pass4_cleanup)

        full_log = "\n".join(logs)
        (LOG_DIR / "phase4b_61mp_bench.log").write_text(full_log)
        out_jpg_path = LOG_DIR / "smoke61_out.jpg"
        out_jpg_path.write_bytes(bytes(pulled_out))
        out_inspect = subprocess.check_output([str(jpeg_tool), "inspect", str(out_jpg_path)], text=True).strip()
        out_sha = hashlib.sha256(bytes(pulled_out)).hexdigest()
        print(f"HOST_VERIFIED smoke61_out.jpg bytes={len(pulled_out)} sha256={out_sha} {out_inspect}")

        assert "FILM_DECODED 9504x6336 " in full_log
        assert "FILM_DONE " in full_log
        assert f"w=9504 h=6336 app1=1 com=1 " in out_inspect
        assert "UMOUNT_RC=0" in full_log and "MOUNTS_CLEAN" in full_log
        print("ALL_61MP_BENCH_PASSED")

if __name__ == "__main__":
    main()
