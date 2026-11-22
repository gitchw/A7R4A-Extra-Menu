import hashlib
import os
from pathlib import Path
import resource
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
import cube_to_a7r4a_lut  # noqa: E402
WORKSPACE = ROOT.parent
JPEG_SRC = Path(os.environ.get("A7R4A_JPEG_SRC", str(WORKSPACE / "third_party/jpeg-9f")))
JPEG_ARM = Path(os.environ.get("A7R4A_LIBJPEG_ARM", str(WORKSPACE / "third_party/libjpeg-arm")))
LIBS_112 = Path(os.environ.get("A7R4A_LIBS_112", str(WORKSPACE / "libs_112")))
TOOLCHAIN_BIN = Path(os.environ.get("A7R4A_TOOLCHAIN_BIN", str(WORKSPACE / "toolchain/bin")))

LIBJPEG_CFILES = [
    "jaricom.c", "jcapimin.c", "jcapistd.c", "jcarith.c", "jccoefct.c", "jccolor.c",
    "jcdctmgr.c", "jchuff.c", "jcinit.c", "jcmainct.c", "jcmarker.c", "jcmaster.c",
    "jcomapi.c", "jcparam.c", "jcprepct.c", "jcsample.c", "jctrans.c", "jdapimin.c",
    "jdapistd.c", "jdarith.c", "jdatadst.c", "jdatasrc.c", "jdcoefct.c", "jdcolor.c",
    "jddctmgr.c", "jdhuff.c", "jdinput.c", "jdmainct.c", "jdmarker.c", "jdmaster.c",
    "jdmerge.c", "jdpostct.c", "jdsample.c", "jdtrans.c", "jerror.c", "jfdctflt.c",
    "jfdctfst.c", "jfdctint.c", "jidctflt.c", "jidctfst.c", "jidctint.c", "jquant1.c",
    "jquant2.c", "jutils.c", "jmemmgr.c", "jmemnobs.c",
]

HELPER_C = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <jpeglib.h>

static int make_jpg(const char *path, int w, int h, int r0, int g0, int b0) {
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
            /* Include bright highlight patch so halation activates cleanly. */
            int hi = (x >= w / 4 && x < w / 2 && y >= h / 4 && y < h / 2);
            row[x * 3 + 0] = hi ? 250 : (unsigned char)r0;
            row[x * 3 + 1] = hi ? 248 : (unsigned char)g0;
            row[x * 3 + 2] = hi ? 245 : (unsigned char)b0;
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
    unsigned w = cinfo.output_width, h = cinfo.output_height;
    unsigned char *row = (unsigned char *)malloc((size_t)w * 3);
    int sample_r = 0, sample_g = 0, sample_b = 0;
    for (unsigned y = 0; y < h; ++y) {
        JSAMPROW ptr = row;
        jpeg_read_scanlines(&cinfo, &ptr, 1);
        if (y == 2) {
            sample_r = row[6];
            sample_g = row[7];
            sample_b = row[8];
        }
    }
    free(row);
    jpeg_finish_decompress(&cinfo);
    jpeg_destroy_decompress(&cinfo);
    fclose(fp);
    printf("%ux%u app1=%d com=%d rgb=%d,%d,%d\n", w, h, has_app1, has_com, sample_r, sample_g, sample_b);
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 7 && strcmp(argv[1], "make") == 0)
        return make_jpg(argv[2], atoi(argv[3]), atoi(argv[4]), atoi(argv[5]), atoi(argv[6]), 128);
    if (argc == 8 && strcmp(argv[1], "make_rgb") == 0)
        return make_jpg(argv[2], atoi(argv[3]), atoi(argv[4]), atoi(argv[5]), atoi(argv[6]), atoi(argv[7]));
    if (argc == 3 && strcmp(argv[1], "inspect") == 0)
        return inspect_jpg(argv[2]);
    return 1;
}
"""


def _build_host_tools(tmp: Path):
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        raise unittest.SkipTest("No host C compiler found")
    inc_flags = []
    lib_flags = []
    if (JPEG_SRC / "jcapimin.c").is_file() and JPEG_ARM.joinpath("include/jconfig.h").is_file():
        jdir = tmp / "host_jpeg"
        jdir.mkdir()
        shutil.copy2(JPEG_ARM / "include/jconfig.h", jdir / "jconfig.h")
        objs = []
        for c in LIBJPEG_CFILES:
            obj = jdir / (c[:-2] + ".o")
            subprocess.run(
                [cc, "-O2", "-I", str(jdir), "-I", str(JPEG_SRC), "-c", str(JPEG_SRC / c), "-o", str(obj)],
                check=True,
            )
            objs.append(str(obj))
        liba = jdir / "libjpeg.a"
        ar = shutil.which("ar") or "ar"
        subprocess.run([ar, "rcs", str(liba)] + objs, check=True)
        inc_flags = ["-I", str(jdir), "-I", str(JPEG_SRC)]
        lib_flags = [str(liba)]
    else:
        raise unittest.SkipTest("Host libjpeg source/config unavailable")

    worker = tmp / "a7r4a-film-job"
    subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-Werror", "-std=c11"]
        + inc_flags
        + [str(ROOT / "camera/a7r4a_film_job.c"), "-o", str(worker)]
        + lib_flags,
        check=True,
    )
    helper_c = tmp / "helper.c"
    helper_c.write_text(HELPER_C)
    helper = tmp / "helper"
    subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra"] + inc_flags + [str(helper_c), "-o", str(helper)] + lib_flags,
        check=True,
    )
    return worker, helper


class TestA7R4AFilmJob(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_obj = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp_obj.name)
        cls.worker, cls.helper = _build_host_tools(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls._tmp_obj.cleanup()

    def _run(self, args, check=True):
        return subprocess.run([str(self.worker)] + args, text=True, capture_output=True, check=check)

    def _inspect(self, path: Path) -> str:
        return subprocess.check_output([str(self.helper), "inspect", str(path)], text=True).strip()

    def test_size_and_verify_contracts(self):
        jpg = self.tmp / "small.jpg"
        subprocess.run([str(self.helper), "make_rgb", str(jpg), "64", "48", "128", "128", "128"], check=True)
        sz = jpg.stat().st_size

        r_size = self._run(["--size", str(jpg)])
        self.assertEqual(r_size.stdout.strip(), str(sz))

        r_ver = self._run(["--verify", str(jpg)])
        self.assertEqual(r_ver.stdout.strip(), f"FILM_DECODED 64x48 {sz}")

        sym = self.tmp / "sym.jpg"
        sym.symlink_to(jpg)
        self.assertNotEqual(self._run(["--size", str(sym)], check=False).returncode, 0)
        self.assertNotEqual(self._run(["--verify", str(sym)], check=False).returncode, 0)

        bad = self.tmp / "bad.jpg"
        bad.write_bytes(b"not a jpeg")
        self.assertNotEqual(self._run(["--verify", str(bad)], check=False).returncode, 0)

    def test_list_and_presets_markers_and_guards(self):
        r_list = self._run(["--list"])
        expected_presets = [
            "portra_400", "cinestill_800t", "fuji_provia", "kodak_gold", "tri_x_400",
            "classic_chrome", "agfa_ultra_100", "polaroid_669", "fuji_acros", "polaroid_px680",
            "kodak_ektar_100", "ilford_hp5",
        ]
        self.assertEqual(r_list.stdout.strip(), "FILM_READY " + " ".join(expected_presets))

        jpg = self.tmp / "neutral.jpg"
        subprocess.run([str(self.helper), "make_rgb", str(jpg), "64", "48", "128", "128", "128"], check=True)
        in_sha = hashlib.sha256(jpg.read_bytes()).hexdigest()

        rgb_by_preset = {}
        for idx, preset in enumerate(expected_presets):
            out = self.tmp / f"out_{preset}.jpg"
            res = self._run([preset, str(jpg), str(out), str(idx % 4), str(idx % 2)])
            self.assertTrue(res.stdout.startswith("FILM_DONE "), res.stdout)
            out_bytes = int(res.stdout.strip().split()[1])
            self.assertEqual(out_bytes, out.stat().st_size)

            info = self._inspect(out)
            self.assertIn("64x48 app1=1 com=1 ", info)
            rgb = tuple(int(v) for v in info.split("rgb=")[1].split(","))
            rgb_by_preset[preset] = rgb

        # Test fuji_classic_chrome alias + 4-arg invocation.
        out_alias = self.tmp / "out_fuji_cc.jpg"
        self._run(["fuji_classic_chrome", str(jpg), str(out_alias)])
        out_cc_0 = self.tmp / "out_cc_0.jpg"
        self._run(["classic_chrome", str(jpg), str(out_cc_0), "0", "0"])
        self.assertEqual(self._inspect(out_alias), self._inspect(out_cc_0))

        self.assertEqual(hashlib.sha256(jpg.read_bytes()).hexdigest(), in_sha)
        # Verify monochrome stocks (Tri-X, Acros, HP5) stay neutral (without colored halation glow).
        for mono_name in ("tri_x_400", "fuji_acros", "ilford_hp5"):
            out_m = self.tmp / f"out_{mono_name}_clean.jpg"
            self._run([mono_name, str(jpg), str(out_m), "0", "0"])
            minfo = self._inspect(out_m)
            rm, gm, bm = (int(v) for v in minfo.split("rgb=")[1].split(","))
            self.assertLessEqual(max(abs(rm - gm), abs(gm - bm)), 3, mono_name)
        self.assertGreater(rgb_by_preset["kodak_gold"][0], rgb_by_preset["kodak_gold"][2])
        self.assertGreater(rgb_by_preset["cinestill_800t"][2], rgb_by_preset["cinestill_800t"][0])
        self.assertNotEqual(rgb_by_preset["classic_chrome"], rgb_by_preset["agfa_ultra_100"])
        self.assertNotEqual(rgb_by_preset["polaroid_669"], rgb_by_preset["polaroid_px680"])

        # Reject existing output (O_EXCL), same path, bad args, and symlink output.
        existing = self.tmp / "out_portra_400.jpg"
        self.assertNotEqual(self._run(["portra_400", str(jpg), str(existing), "0", "0"], check=False).returncode, 0)
        self.assertNotEqual(self._run(["portra_400", str(jpg), str(jpg), "0", "0"], check=False).returncode, 0)
        self.assertNotEqual(self._run(["unknown_film", str(jpg), str(self.tmp / "x.jpg"), "0", "0"], check=False).returncode, 0)
        self.assertNotEqual(self._run(["portra_400", str(jpg), str(self.tmp / "y.jpg"), "4", "0"], check=False).returncode, 0)
        self.assertNotEqual(self._run(["portra_400", str(jpg), str(self.tmp / "z.jpg"), "0", "2"], check=False).returncode, 0)

    def test_cube_converter_and_custom_a7lt_lut(self):
        # Generate a 17x17x17 warm-shift .cube file in temp dir and convert to .a7lt.
        n = 17
        lines = ["TITLE \"Test Warm Shift\"", f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
        for b in range(n):
            for g in range(n):
                for r in range(n):
                    rf = min(1.0, (r / (n - 1)) * 1.10 + 0.05)
                    gf = g / (n - 1)
                    bf = max(0.0, (b / (n - 1)) * 0.80)
                    lines.append(f"{rf:.6f} {gf:.6f} {bf:.6f}")
        cube_path = self.tmp / "warm.cube"
        cube_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        a7lt_path = self.tmp / "warm.a7lt"
        size, total_bytes = cube_to_a7r4a_lut.convert_cube_file(cube_path, a7lt_path)
        self.assertEqual(size, 17)
        self.assertEqual(total_bytes, 16 + 17 * 17 * 17 * 3)
        self.assertLess(a7lt_path.stat().st_size, 1024 * 1024)

        # CLI --verify on .a7lt works; malformed 1D LUT fails cleanly.
        vr = subprocess.run(
            [sys.executable, str(ROOT / "tools/cube_to_a7r4a_lut.py"), "--verify", str(a7lt_path)],
            text=True, capture_output=True, check=True,
        )
        self.assertIn("A7LT_OK size=17", vr.stdout)
        with self.assertRaises(ValueError):
            cube_to_a7r4a_lut.parse_cube_text("LUT_1D_SIZE 16\n0.0 0.0 0.0\n")

        jpg = self.tmp / "lut_src.jpg"
        subprocess.run([str(self.helper), "make_rgb", str(jpg), "64", "48", "128", "128", "128"], check=True)
        out_lut = self.tmp / "lut_out.jpg"
        res = self._run(["--lut", str(a7lt_path), str(jpg), str(out_lut), "0", "0"])
        self.assertTrue(res.stdout.startswith("FILM_DONE "), res.stdout)
        info = self._inspect(out_lut)
        r, g, b = (int(v) for v in info.split("rgb=")[1].split(","))
        self.assertGreater(r, g)
        self.assertGreater(g, b)

        # Corrupted .a7lt rejected.
        bad_lut = self.tmp / "bad.a7lt"
        bad_lut.write_bytes(b"NOPE" + b"\x00" * 32)
        self.assertNotEqual(
            self._run(["--lut", str(bad_lut), str(jpg), str(self.tmp / "bad_lut_out.jpg")], check=False).returncode,
            0,
        )

    def test_61mp_streaming_rss_and_markers(self):
        big_in = self.tmp / "a7r4a_61mp.jpg"
        big_out = self.tmp / "a7r4a_61mp_out.jpg"
        subprocess.run([str(self.helper), "make_rgb", str(big_in), "9504", "6336", "120", "115", "110"], check=True)
        in_sha = hashlib.sha256(big_in.read_bytes()).hexdigest()

        v_res = self._run(["--verify", str(big_in)])
        self.assertEqual(v_res.stdout.strip(), f"FILM_DECODED 9504x6336 {big_in.stat().st_size}")

        # Measure peak RSS of a single 61MP render with grain=2 and halation=1.
        py_probe = (
            "import resource, subprocess, sys; "
            "r = subprocess.run(sys.argv[1:], capture_output=True, text=True, check=True); "
            "print(r.stdout.strip()); "
            "print('MAXRSS', resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", py_probe, str(self.worker), "portra_400", str(big_in), str(big_out), "2", "1"],
            text=True,
            capture_output=True,
            check=True,
        )
        lines = proc.stdout.strip().splitlines()
        self.assertTrue(lines[0].startswith("FILM_DONE "), lines)
        maxrss = int(lines[1].split()[1])
        # macOS reports ru_maxrss in bytes; Linux reports KiB.
        maxrss_bytes = maxrss if sys.platform == "darwin" else maxrss * 1024
        self.assertLess(maxrss_bytes, 16 * 1024 * 1024, f"Peak RSS too high: {maxrss_bytes} B")

        self.assertEqual(hashlib.sha256(big_in.read_bytes()).hexdigest(), in_sha)
        info = self._inspect(big_out)
        self.assertTrue(info.startswith("9504x6336 app1=1 com=1 "), info)

    def test_scale_4_fast_mode_and_halation_off(self):
        fast_in = self.tmp / "scale_in.jpg"
        fast_out = self.tmp / "scale_out.jpg"
        subprocess.run([str(self.helper), "make_rgb", str(fast_in), "640", "480", "128", "124", "120"], check=True)
        res = self._run(["--scale", "4", "portra_400", str(fast_in), str(fast_out), "2", "0"])
        self.assertTrue(res.stdout.startswith("FILM_DONE "), res.stdout)
        info = self._inspect(fast_out)
        self.assertTrue(info.startswith("160x120 app1=1 com=1 "), info)
        self.assertNotEqual(self._run(["--scale", "3", "portra_400", str(fast_in), str(self.tmp / "bad_scale.jpg"), "0", "0"], check=False).returncode, 0)

    def test_arm_cross_build_if_toolchain_present(self):
        gcc = TOOLCHAIN_BIN / "arm-linux-gnueabihf-gcc"
        if not (gcc.is_file() and LIBS_112.is_dir() and JPEG_ARM.is_dir()):
            self.skipTest("ARM cross-toolchain or libs_112 not present")
        out_dir = self.tmp / "arm_build"
        env = dict(os.environ, PATH=f"{TOOLCHAIN_BIN}:{os.environ.get('PATH', '')}")
        res = subprocess.run(
            [
                "sh",
                str(ROOT / "host-tests/build_a7r4a_film_job.sh"),
                str(out_dir),
                str(LIBS_112),
                str(JPEG_ARM),
            ],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("BUILD_ONLY: streaming 61MP film worker", res.stdout)
        elf = out_dir / "a7r4a-film-job.elf"
        self.assertTrue(elf.is_file())
        self.assertTrue(elf.read_bytes().startswith(b"\x7fELF"))


if __name__ == "__main__":
    unittest.main()
