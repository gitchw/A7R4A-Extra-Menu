#define _GNU_SOURCE
#include "a7r4a_film_luts.h"
#include <errno.h>
#include <fcntl.h>
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <jpeglib.h>

#define MAX_DIM 16384u
#define HALATION_RAD 3
#define HALATION_WIN (HALATION_RAD * 2 + 1)
#define RGB_RING_SLOTS 32u
#define Q_RING_SLOTS 16u
#define A7LT_HEADER_BYTES 16u
#define A7LT_MIN_SIZE 2u
#define A7LT_MAX_SIZE 65u

struct a7lt_lut {
    unsigned size;
    uint8_t *data; /* size * size * size * 3 bytes */
};

struct film_runtime {
    int preset;
    int grain_amp;
    int mono;
    uint32_t rng;
    int bell_scaled[256];
    uint8_t lut[3][256];
    const struct a7lt_lut *custom_lut;
};

struct safe_jpeg_err {
    struct jpeg_error_mgr pub;
    jmp_buf jb;
};

static void on_jpeg_error(j_common_ptr cinfo) {
    struct safe_jpeg_err *err = (struct safe_jpeg_err *)cinfo->err;
    longjmp(err->jb, 1);
}

static uint8_t clamp_u8(int v) {
    if (v <= 0)
        return 0;
    if (v >= 255)
        return 255;
    return (uint8_t)v;
}

static int parse_preset(const char *s) {
    if (!s)
        return -1;
    for (int i = 0; i < FILM_PRESET_COUNT; ++i) {
        if (strcmp(s, FILM_PRESET_NAMES[i]) == 0)
            return i;
    }
    if (strcmp(s, "fuji_classic_chrome") == 0)
        return FILM_CLASSIC_CHROME;
    return -1;
}

static int parse_digit_range(const char *s, int lo, int hi) {
    if (!s || s[0] < '0' || s[0] > '9' || s[1] != '\0')
        return -1;
    int v = s[0] - '0';
    return (v >= lo && v <= hi) ? v : -1;
}

static int open_ro_nofollow(const char *path, unsigned long *bytes_out) {
    int fd = open(path, O_RDONLY | O_NOFOLLOW);
    if (fd < 0)
        return -1;
    off_t sz = lseek(fd, 0, SEEK_END);
    if (sz < 0 || (uint64_t)sz > 0xffffffffu) {
        close(fd);
        return -1;
    }
    if (lseek(fd, 0, SEEK_SET) != 0) {
        close(fd);
        return -1;
    }
    if (bytes_out)
        *bytes_out = (unsigned long)sz;
    return fd;
}

static uint32_t read_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint16_t read_le16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static int load_a7lt(const char *path, struct a7lt_lut *out) {
    memset(out, 0, sizeof(*out));
    unsigned long fbytes = 0;
    int fd = open_ro_nofollow(path, &fbytes);
    if (fd < 0 || fbytes < A7LT_HEADER_BYTES || fbytes > (1u << 20)) {
        if (fd >= 0)
            close(fd);
        return -1;
    }
    uint8_t hdr[A7LT_HEADER_BYTES];
    size_t got = 0;
    while (got < A7LT_HEADER_BYTES) {
        ssize_t n = read(fd, hdr + got, A7LT_HEADER_BYTES - got);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0) {
            close(fd);
            return -1;
        }
        got += (size_t)n;
    }
    if (memcmp(hdr, "A7LT", 4) != 0) {
        close(fd);
        return -1;
    }
    uint16_t ver = read_le16(hdr + 4);
    uint16_t size = read_le16(hdr + 6);
    uint32_t flags = read_le32(hdr + 8);
    uint32_t dbytes = read_le32(hdr + 12);
    if (ver != 1u || flags != 0u || size < A7LT_MIN_SIZE || size > A7LT_MAX_SIZE) {
        close(fd);
        return -1;
    }
    uint32_t expected = (uint32_t)size * (uint32_t)size * (uint32_t)size * 3u;
    if (dbytes != expected || fbytes != A7LT_HEADER_BYTES + (unsigned long)expected) {
        close(fd);
        return -1;
    }
    uint8_t *buf = (uint8_t *)malloc(expected);
    if (!buf) {
        close(fd);
        return -1;
    }
    size_t pos = 0;
    while (pos < expected) {
        ssize_t n = read(fd, buf + pos, expected - pos);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0) {
            free(buf);
            close(fd);
            return -1;
        }
        pos += (size_t)n;
    }
    close(fd);
    out->size = size;
    out->data = buf;
    return 0;
}

static void free_a7lt(struct a7lt_lut *lut) {
    free(lut->data);
    lut->data = NULL;
    lut->size = 0;
}

static uint8_t interp17(const uint8_t tbl[17], int x) {
    int s = x << 4;
    if (s >= 4080)
        return tbl[16];
    int idx = s / 255;
    int frac = s % 255;
    int v = ((255 - frac) * (int)tbl[idx] + frac * (int)tbl[idx + 1] + 127) / 255;
    return (uint8_t)v;
}

static void film_init(struct film_runtime *rt, int preset, int grain, const struct a7lt_lut *custom) {
    memset(rt, 0, sizeof(*rt));
    rt->preset = preset;
    rt->rng = 0xa6400f17u;
    rt->custom_lut = custom;
    if (grain == 1)
        rt->grain_amp = 12;
    else if (grain == 2)
        rt->grain_amp = 22;
    else if (grain == 3)
        rt->grain_amp = 38;
    else
        rt->grain_amp = 0;

    for (int i = 0; i < 256; ++i)
        rt->bell_scaled[i] = rt->grain_amp * (int)FILM_GRAIN_WEIGHT256[i];

    if (custom)
        return;

    rt->mono = (preset == FILM_TRI_X_400 || preset == FILM_FUJI_ACROS || preset == FILM_ILFORD_HP5);

    int cidx = -1;
    if (preset >= FILM_CLASSIC_CHROME && preset <= FILM_POLAROID_PX680)
        cidx = FILM_CURVE_MAP[preset - FILM_CLASSIC_CHROME];

    for (int i = 0; i < 256; ++i) {
        if (cidx >= 0) {
            rt->lut[0][i] = interp17(FILM_CURVES17[cidx][0], i);
            rt->lut[1][i] = interp17(FILM_CURVES17[cidx][1], i);
            rt->lut[2][i] = interp17(FILM_CURVES17[cidx][2], i);
        } else if (preset == FILM_FUJI_ACROS) {
            uint8_t v = interp17(FILM_ACROS_CURVE17, i);
            rt->lut[0][i] = v;
            rt->lut[1][i] = v;
            rt->lut[2][i] = v;
        } else if (preset == FILM_PORTRA_400) {
            rt->lut[0][i] = FILM_COLOR_TONE256[clamp_u8(((276 * i) >> 8) + 6)];
            rt->lut[1][i] = FILM_COLOR_TONE256[clamp_u8(((261 * i) >> 8) + 2)];
            rt->lut[2][i] = FILM_COLOR_TONE256[clamp_u8(((241 * i) >> 8) + 4)];
        } else if (preset == FILM_CINESTILL_800T) {
            rt->lut[0][i] = FILM_COLOR_TONE256[clamp_u8((243 * i) >> 8)];
            rt->lut[1][i] = FILM_COLOR_TONE256[clamp_u8((251 * i) >> 8)];
            rt->lut[2][i] = FILM_COLOR_TONE256[clamp_u8(((286 * i) >> 8) + 5)];
        } else if (preset == FILM_FUJI_PROVIA) {
            int base = FILM_MONO_TONE256[i];
            rt->lut[0][i] = clamp_u8((246 * base) >> 8);
            rt->lut[1][i] = clamp_u8((271 * base) >> 8);
            rt->lut[2][i] = clamp_u8((276 * base) >> 8);
        } else if (preset == FILM_KODAK_GOLD) {
            rt->lut[0][i] = FILM_COLOR_TONE256[clamp_u8(((294 * i) >> 8) + 8)];
            rt->lut[1][i] = FILM_COLOR_TONE256[clamp_u8(((266 * i) >> 8) + 4)];
            rt->lut[2][i] = FILM_COLOR_TONE256[clamp_u8((225 * i) >> 8)];
        } else if (preset == FILM_KODAK_EKTAR_100) {
            rt->lut[0][i] = FILM_COLOR_TONE256[clamp_u8(((290 * i) >> 8) + 6)];
            rt->lut[1][i] = FILM_COLOR_TONE256[clamp_u8(((256 * i) >> 8) + 2)];
            rt->lut[2][i] = FILM_COLOR_TONE256[clamp_u8(((274 * i) >> 8) - 2)];
        } else if (preset == FILM_ILFORD_HP5) {
            int c = 128 + (((i - 128) * 304) >> 8);
            int v = (int)FILM_MONO_TONE256[clamp_u8(c)];
            rt->lut[0][i] = clamp_u8(v);
            rt->lut[1][i] = clamp_u8(v);
            rt->lut[2][i] = clamp_u8(v);
        } else {
            /* FILM_TRI_X_400 */
            uint8_t v = FILM_MONO_TONE256[i];
            rt->lut[0][i] = v;
            rt->lut[1][i] = v;
            rt->lut[2][i] = v;
        }
    }
}

static void trilinear_a7lt(const struct a7lt_lut *lut, int r, int g, int b,
                           int *out_r, int *out_g, int *out_b) {
    unsigned n = lut->size;
    unsigned nm1 = n - 1u;
    unsigned sr = (unsigned)r * nm1;
    unsigned sg = (unsigned)g * nm1;
    unsigned sb = (unsigned)b * nm1;

    unsigned ir0 = sr / 255u;
    unsigned ig0 = sg / 255u;
    unsigned ib0 = sb / 255u;
    unsigned ir1 = ir0 < nm1 ? ir0 + 1u : nm1;
    unsigned ig1 = ig0 < nm1 ? ig0 + 1u : nm1;
    unsigned ib1 = ib0 < nm1 ? ib0 + 1u : nm1;

    unsigned fr = (ir0 < nm1) ? (((sr % 255u) * 256u + 127u) / 255u) : 0u;
    unsigned fg = (ig0 < nm1) ? (((sg % 255u) * 256u + 127u) / 255u) : 0u;
    unsigned fb = (ib0 < nm1) ? (((sb % 255u) * 256u + 127u) / 255u) : 0u;

    const uint8_t *d = lut->data;
    size_t idx000 = ((size_t)(ib0 * n + ig0) * n + ir0) * 3u;
    size_t idx001 = ((size_t)(ib0 * n + ig0) * n + ir1) * 3u;
    size_t idx010 = ((size_t)(ib0 * n + ig1) * n + ir0) * 3u;
    size_t idx011 = ((size_t)(ib0 * n + ig1) * n + ir1) * 3u;
    size_t idx100 = ((size_t)(ib1 * n + ig0) * n + ir0) * 3u;
    size_t idx101 = ((size_t)(ib1 * n + ig0) * n + ir1) * 3u;
    size_t idx110 = ((size_t)(ib1 * n + ig1) * n + ir0) * 3u;
    size_t idx111 = ((size_t)(ib1 * n + ig1) * n + ir1) * 3u;

    int res[3];
    for (int c = 0; c < 3; ++c) {
        unsigned c00 = (d[idx000 + c] * (256u - fr) + d[idx001 + c] * fr + 128u) >> 8;
        unsigned c01 = (d[idx010 + c] * (256u - fr) + d[idx011 + c] * fr + 128u) >> 8;
        unsigned c10 = (d[idx100 + c] * (256u - fr) + d[idx101 + c] * fr + 128u) >> 8;
        unsigned c11 = (d[idx110 + c] * (256u - fr) + d[idx111 + c] * fr + 128u) >> 8;
        unsigned c0 = (c00 * (256u - fg) + c01 * fg + 128u) >> 8;
        unsigned c1 = (c10 * (256u - fg) + c11 * fg + 128u) >> 8;
        res[c] = (int)((c0 * (256u - fb) + c1 * fb + 128u) >> 8);
    }
    *out_r = res[0];
    *out_g = res[1];
    *out_b = res[2];
}

static void film_grade_row(struct film_runtime *rt, const uint8_t *src_rgb,
                           uint8_t *dst_rgb, unsigned w) {
    int cidx = -1;
    if (!rt->custom_lut && rt->preset >= FILM_CLASSIC_CHROME && rt->preset <= FILM_POLAROID_PX680)
        cidx = FILM_CURVE_MAP[rt->preset - FILM_CLASSIC_CHROME];

    for (unsigned x = 0; x < w; ++x) {
        int r = src_rgb[x * 3u + 0u];
        int g = src_rgb[x * 3u + 1u];
        int b = src_rgb[x * 3u + 2u];
        int luma = (77 * r + 150 * g + 29 * b) >> 8;
        int nr, ng, nb;

        if (rt->custom_lut) {
            trilinear_a7lt(rt->custom_lut, r, g, b, &nr, &ng, &nb);
        } else if (rt->preset == FILM_TRI_X_400 || rt->preset == FILM_ILFORD_HP5) {
            int v = rt->lut[0][luma];
            nr = v;
            ng = v;
            nb = v;
        } else if (rt->preset == FILM_FUJI_ACROS) {
            int idx = clamp_u8((79 * r + 97 * g + 80 * b) >> 8);
            int v = rt->lut[0][idx];
            nr = v;
            ng = v;
            nb = v;
        } else if (cidx >= 0) {
            const struct film_matrix_spec *ms = &FILM_MATRICES[cidx];
            int mr = clamp_u8((ms->m[0] * r + ms->m[1] * g + ms->m[2] * b + ms->bias[0] + 128) >> 8);
            int mg = clamp_u8((ms->m[3] * r + ms->m[4] * g + ms->m[5] * b + ms->bias[1] + 128) >> 8);
            int mb = clamp_u8((ms->m[6] * r + ms->m[7] * g + ms->m[8] * b + ms->bias[2] + 128) >> 8);
            nr = rt->lut[0][mr];
            ng = rt->lut[1][mg];
            nb = rt->lut[2][mb];
        } else {
            nr = rt->lut[0][r];
            ng = rt->lut[1][g];
            nb = rt->lut[2][b];
            if (rt->preset == FILM_FUJI_PROVIA || rt->preset == FILM_KODAK_EKTAR_100) {
                int y2 = (77 * nr + 150 * ng + 29 * nb) >> 8;
                int sat = (rt->preset == FILM_FUJI_PROVIA) ? 307 : 302;
                nr = y2 + ((sat * (nr - y2)) >> 8);
                ng = y2 + ((sat * (ng - y2)) >> 8);
                nb = y2 + ((sat * (nb - y2)) >> 8);
            } else if (rt->preset == FILM_CINESTILL_800T && luma > 195) {
                nr += ((luma - 195) * 160) >> 8;
            }
        }

        if (rt->grain_amp > 0) {
            uint32_t x32 = rt->rng;
            x32 ^= x32 << 13;
            x32 ^= x32 >> 17;
            x32 ^= x32 << 5;
            rt->rng = x32;
            int n = (((int)(x32 & 0xffu) - 128) * rt->bell_scaled[luma]) >> 15;
            nr += n;
            ng += n;
            nb += n;
        }

        dst_rgb[x * 3u + 0u] = clamp_u8(nr);
        dst_rgb[x * 3u + 1u] = clamp_u8(ng);
        dst_rgb[x * 3u + 2u] = clamp_u8(nb);
    }
}

static void hbox7_row(const uint8_t *src, uint8_t *dst, unsigned w) {
    if (w <= HALATION_WIN) {
        for (unsigned x = 0; x < w; ++x) {
            unsigned x0 = x >= HALATION_RAD ? x - HALATION_RAD : 0u;
            unsigned x1 = (x + HALATION_RAD < w) ? (x + HALATION_RAD) : (w - 1u);
            unsigned sum = 0;
            for (unsigned k = x0; k <= x1; ++k)
                sum += src[k];
            dst[x] = (uint8_t)(sum / (x1 - x0 + 1u));
        }
        return;
    }
    unsigned sum = 0;
    for (unsigned k = 0; k <= (unsigned)HALATION_RAD; ++k)
        sum += src[k];
    for (unsigned x = 0; x < (unsigned)HALATION_RAD; ++x) {
        dst[x] = (uint8_t)(sum / (x + (unsigned)HALATION_RAD + 1u));
        sum += src[x + (unsigned)HALATION_RAD + 1u];
    }
    /* Now sum holds [0 .. 2*HALATION_RAD] for x = HALATION_RAD. */
    for (unsigned x = (unsigned)HALATION_RAD; x + (unsigned)HALATION_RAD + 1u < w; ++x) {
        dst[x] = (uint8_t)(sum / HALATION_WIN);
        sum = sum + src[x + (unsigned)HALATION_RAD + 1u] - src[x - (unsigned)HALATION_RAD];
    }
    for (unsigned x = w - (unsigned)HALATION_RAD - 1u; x < w; ++x) {
        unsigned cnt = w - (x - (unsigned)HALATION_RAD);
        dst[x] = (uint8_t)(sum / cnt);
        sum -= src[x - (unsigned)HALATION_RAD];
    }
}

static void vbox7_row(uint8_t *const ring[Q_RING_SLOTS], uint8_t *dst,
                      unsigned qy, unsigned mw, unsigned mh) {
    unsigned y0 = qy >= HALATION_RAD ? qy - HALATION_RAD : 0u;
    unsigned y1 = (qy + HALATION_RAD < mh) ? (qy + HALATION_RAD) : (mh - 1u);
    unsigned count = y1 - y0 + 1u;
    for (unsigned x = 0; x < mw; ++x) {
        unsigned sum = 0;
        for (unsigned k = y0; k <= y1; ++k)
            sum += ring[k % Q_RING_SLOTS][x];
        dst[x] = (uint8_t)(sum / count);
    }
}

static void apply_halation_row(uint8_t *rgb, const uint8_t *mask_row,
                               unsigned w, unsigned mw, int is_cinestill) {
    int gain = is_cinestill ? 150 : 95;
    for (unsigned x = 0; x < w; ++x) {
        unsigned qx = x >> 2;
        if (qx >= mw)
            qx = mw - 1u;
        int m = mask_row[qx];
        if (m > 2) {
            int hv = (m * gain) >> 7;
            rgb[x * 3u + 0u] = clamp_u8((int)rgb[x * 3u + 0u] + hv);
            rgb[x * 3u + 1u] = clamp_u8((int)rgb[x * 3u + 1u] + ((90 * hv) >> 8));
        }
    }
}

static int cmd_list(void) {
    printf("FILM_READY");
    for (int i = 0; i < FILM_PRESET_COUNT; ++i)
        printf(" %s", FILM_PRESET_NAMES[i]);
    printf("\n");
    return 0;
}

static int cmd_size(const char *path) {
    unsigned long bytes = 0;
    int fd = open_ro_nofollow(path, &bytes);
    if (fd < 0) {
        fprintf(stderr, "FILM_FAIL stage=size errno=%d\n", errno);
        return 2;
    }
    close(fd);
    printf("%lu\n", bytes);
    return 0;
}

static int cmd_verify(const char *in_path) {
    unsigned long bytes = 0;
    int fd = open_ro_nofollow(in_path, &bytes);
    if (fd < 0 || bytes == 0) {
        if (fd >= 0)
            close(fd);
        fprintf(stderr, "FILM_FAIL stage=verify_open errno=%d\n", errno);
        return 3;
    }
    FILE *fp = fdopen(fd, "rb");
    if (!fp) {
        close(fd);
        fprintf(stderr, "FILM_FAIL stage=verify_fdopen\n");
        return 3;
    }

    struct jpeg_decompress_struct cinfo;
    struct safe_jpeg_err jerr;
    uint8_t * volatile row_buf = NULL;
    cinfo.err = jpeg_std_error(&jerr.pub);
    jerr.pub.error_exit = on_jpeg_error;

    if (setjmp(jerr.jb)) {
        jpeg_destroy_decompress(&cinfo);
        free((void *)row_buf);
        fclose(fp);
        fprintf(stderr, "FILM_FAIL stage=verify_decode\n");
        return 4;
    }

    jpeg_create_decompress(&cinfo);
    jpeg_stdio_src(&cinfo, fp);
    if (jpeg_read_header(&cinfo, TRUE) != JPEG_HEADER_OK) {
        jpeg_destroy_decompress(&cinfo);
        fclose(fp);
        fprintf(stderr, "FILM_FAIL stage=verify_header\n");
        return 4;
    }
    cinfo.out_color_space = JCS_RGB;
    jpeg_start_decompress(&cinfo);

    if (cinfo.output_width == 0 || cinfo.output_height == 0 ||
        cinfo.output_width > MAX_DIM || cinfo.output_height > MAX_DIM ||
        cinfo.output_components != 3) {
        jpeg_destroy_decompress(&cinfo);
        fclose(fp);
        fprintf(stderr, "FILM_FAIL stage=verify_dims\n");
        return 4;
    }

    size_t stride = (size_t)cinfo.output_width * 3u;
    row_buf = (uint8_t *)malloc(stride);
    if (!row_buf) {
        jpeg_destroy_decompress(&cinfo);
        fclose(fp);
        fprintf(stderr, "FILM_FAIL stage=verify_oom\n");
        return 5;
    }

    while (cinfo.output_scanline < cinfo.output_height) {
        JSAMPROW ptr = row_buf;
        if (jpeg_read_scanlines(&cinfo, &ptr, 1) != 1)
            longjmp(jerr.jb, 1);
    }

    unsigned w = cinfo.output_width;
    unsigned h = cinfo.output_height;
    jpeg_finish_decompress(&cinfo);
    jpeg_destroy_decompress(&cinfo);
    free(row_buf);
    fclose(fp);
    printf("FILM_DECODED %ux%u %lu\n", w, h, bytes);
    return 0;
}

static int cmd_render(int preset, const struct a7lt_lut *custom_lut,
                      const char *in_path, const char *out_path,
                      int grain, int halation, int scale) {
    if (strcmp(in_path, out_path) == 0) {
        fprintf(stderr, "FILM_FAIL stage=render_args\n");
        return 2;
    }

    unsigned long in_bytes = 0;
    int in_fd = open_ro_nofollow(in_path, &in_bytes);
    if (in_fd < 0 || in_bytes == 0) {
        if (in_fd >= 0)
            close(in_fd);
        fprintf(stderr, "FILM_FAIL stage=render_open_in errno=%d\n", errno);
        return 3;
    }
    FILE *in_fp = fdopen(in_fd, "rb");
    if (!in_fp) {
        close(in_fd);
        fprintf(stderr, "FILM_FAIL stage=render_fdopen_in\n");
        return 3;
    }

    int out_fd = open(out_path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (out_fd < 0) {
        fclose(in_fp);
        fprintf(stderr, "FILM_FAIL stage=render_open_out errno=%d\n", errno);
        return 3;
    }
    FILE *out_fp = fdopen(out_fd, "wb");
    if (!out_fp) {
        close(out_fd);
        unlink(out_path);
        fclose(in_fp);
        fprintf(stderr, "FILM_FAIL stage=render_fdopen_out\n");
        return 3;
    }

    struct jpeg_decompress_struct dinfo;
    struct jpeg_compress_struct cinfo;
    struct safe_jpeg_err djerr, cjerr;
    uint8_t * volatile src_row = NULL;
    uint8_t * volatile out_row = NULL;
    uint8_t *rgb_ring[RGB_RING_SLOTS] = {0};
    uint8_t *mh0_ring[Q_RING_SLOTS] = {0};
    uint8_t *bh1_ring[Q_RING_SLOTS] = {0};
    uint8_t * volatile q_scratch = NULL;
    uint8_t * volatile b2_row = NULL;

    dinfo.err = jpeg_std_error(&djerr.pub);
    djerr.pub.error_exit = on_jpeg_error;
    cinfo.err = jpeg_std_error(&cjerr.pub);
    cjerr.pub.error_exit = on_jpeg_error;

    if (setjmp(djerr.jb) || setjmp(cjerr.jb)) {
        jpeg_destroy_compress(&cinfo);
        jpeg_destroy_decompress(&dinfo);
        for (unsigned i = 0; i < RGB_RING_SLOTS; ++i)
            free(rgb_ring[i]);
        for (unsigned i = 0; i < Q_RING_SLOTS; ++i) {
            free(mh0_ring[i]);
            free(bh1_ring[i]);
        }
        free((void *)q_scratch);
        free((void *)b2_row);
        free((void *)src_row);
        free((void *)out_row);
        fclose(out_fp);
        unlink(out_path);
        fclose(in_fp);
        fprintf(stderr, "FILM_FAIL stage=render_jpeg\n");
        return 4;
    }

    jpeg_create_decompress(&dinfo);
    jpeg_create_compress(&cinfo);
    jpeg_stdio_src(&dinfo, in_fp);

    jpeg_save_markers(&dinfo, JPEG_COM, 0xffffu);
    for (int m = JPEG_APP0 + 1; m <= JPEG_APP0 + 15; ++m)
        jpeg_save_markers(&dinfo, m, 0xffffu);

    if (jpeg_read_header(&dinfo, TRUE) != JPEG_HEADER_OK)
        longjmp(djerr.jb, 1);

    dinfo.out_color_space = JCS_RGB;
    if (scale == 2 || scale == 4) {
        dinfo.scale_num = 1;
        dinfo.scale_denom = (unsigned int)scale;
    }
    jpeg_start_decompress(&dinfo);

    unsigned w = dinfo.output_width;
    unsigned h = dinfo.output_height;
    if (w == 0 || h == 0 || w > MAX_DIM || h > MAX_DIM || dinfo.output_components != 3)
        longjmp(djerr.jb, 1);

    size_t stride = (size_t)w * 3u;
    src_row = (uint8_t *)malloc(stride);
    if (!src_row)
        longjmp(djerr.jb, 1);

    int use_halation = (halation && w >= 32u && h >= 32u);
    unsigned mw = w >> 2;
    unsigned mh = h >> 2;

    if (!use_halation) {
        out_row = (uint8_t *)malloc(stride);
        if (!out_row)
            longjmp(djerr.jb, 1);
    } else {
        for (unsigned i = 0; i < RGB_RING_SLOTS; ++i) {
            rgb_ring[i] = (uint8_t *)malloc(stride);
            if (!rgb_ring[i])
                longjmp(djerr.jb, 1);
        }
        for (unsigned i = 0; i < Q_RING_SLOTS; ++i) {
            mh0_ring[i] = (uint8_t *)malloc(mw);
            bh1_ring[i] = (uint8_t *)malloc(mw);
            if (!mh0_ring[i] || !bh1_ring[i])
                longjmp(djerr.jb, 1);
        }
        q_scratch = (uint8_t *)malloc(mw);
        b2_row = (uint8_t *)malloc(mw);
        if (!q_scratch || !b2_row)
            longjmp(djerr.jb, 1);
    }

    jpeg_stdio_dest(&cinfo, out_fp);
    cinfo.image_width = w;
    cinfo.image_height = h;
    cinfo.input_components = 3;
    cinfo.in_color_space = JCS_RGB;
    jpeg_set_defaults(&cinfo);
    int out_q = (scale == 1 && (uint64_t)w * (uint64_t)h > 25000000ULL) ? 85 : 92;
    jpeg_set_quality(&cinfo, out_q, TRUE);
    jpeg_start_compress(&cinfo, TRUE);

    for (jpeg_saved_marker_ptr mk = dinfo.marker_list; mk; mk = mk->next)
        jpeg_write_marker(&cinfo, mk->marker, mk->data, mk->data_length);

    struct film_runtime rt;
    film_init(&rt, preset, grain, custom_lut);

    if (!use_halation) {
        for (unsigned y = 0; y < h; ++y) {
            JSAMPROW rptr = src_row;
            if (jpeg_read_scanlines(&dinfo, &rptr, 1) != 1)
                longjmp(djerr.jb, 1);
            film_grade_row(&rt, src_row, out_row, w);
            JSAMPROW wptr = out_row;
            if (jpeg_write_scanlines(&cinfo, &wptr, 1) != 1)
                longjmp(cjerr.jb, 1);
        }
    } else {
        unsigned y_decoded = 0;
        unsigned q_m0_done = 0;
        unsigned q_b1_done = 0;
        unsigned last_qy = 0xffffffffu;
        int is_cinestill = (!custom_lut && preset == FILM_CINESTILL_800T);

        for (unsigned y = 0; y < h; ++y) {
            unsigned qy = y >> 2;
            if (qy >= mh)
                qy = mh - 1u;

            unsigned q_bh1_need = (qy + HALATION_RAD < mh) ? (qy + HALATION_RAD) : (mh - 1u);
            unsigned q_m0_need = (q_bh1_need + HALATION_RAD < mh) ? (q_bh1_need + HALATION_RAD) : (mh - 1u);

            while (q_m0_done <= q_m0_need) {
                unsigned target_y = q_m0_done * 4u;
                while (y_decoded <= target_y) {
                    JSAMPROW rptr = src_row;
                    if (jpeg_read_scanlines(&dinfo, &rptr, 1) != 1)
                        longjmp(djerr.jb, 1);
                    film_grade_row(&rt, src_row, rgb_ring[y_decoded % RGB_RING_SLOTS], w);
                    ++y_decoded;
                }
                const uint8_t *grow = rgb_ring[target_y % RGB_RING_SLOTS];
                for (unsigned qx = 0; qx < mw; ++qx) {
                    const uint8_t *p = grow + (size_t)qx * 12u;
                    int luma = (77 * p[0] + 150 * p[1] + 29 * p[2]) >> 8;
                    if (luma >= 196) {
                        int m = (luma - 195) * 4;
                        q_scratch[qx] = m > 255 ? 255 : (uint8_t)m;
                    } else {
                        q_scratch[qx] = 0;
                    }
                }
                hbox7_row(q_scratch, mh0_ring[q_m0_done % Q_RING_SLOTS], mw);
                ++q_m0_done;
            }

            while (q_b1_done <= q_bh1_need) {
                vbox7_row(mh0_ring, q_scratch, q_b1_done, mw, mh);
                hbox7_row(q_scratch, bh1_ring[q_b1_done % Q_RING_SLOTS], mw);
                ++q_b1_done;
            }

            while (y_decoded <= y) {
                JSAMPROW rptr = src_row;
                if (jpeg_read_scanlines(&dinfo, &rptr, 1) != 1)
                    longjmp(djerr.jb, 1);
                film_grade_row(&rt, src_row, rgb_ring[y_decoded % RGB_RING_SLOTS], w);
                ++y_decoded;
            }

            if (qy != last_qy) {
                vbox7_row(bh1_ring, b2_row, qy, mw, mh);
                last_qy = qy;
            }

            uint8_t *cur = rgb_ring[y % RGB_RING_SLOTS];
            apply_halation_row(cur, b2_row, w, mw, is_cinestill);
            JSAMPROW wptr = cur;
            if (jpeg_write_scanlines(&cinfo, &wptr, 1) != 1)
                longjmp(cjerr.jb, 1);
        }
    }

    jpeg_finish_compress(&cinfo);
    jpeg_finish_decompress(&dinfo);
    jpeg_destroy_compress(&cinfo);
    jpeg_destroy_decompress(&dinfo);

    for (unsigned i = 0; i < RGB_RING_SLOTS; ++i)
        free(rgb_ring[i]);
    for (unsigned i = 0; i < Q_RING_SLOTS; ++i) {
        free(mh0_ring[i]);
        free(bh1_ring[i]);
    }
    free(q_scratch);
    free(b2_row);
    free(src_row);
    free(out_row);

    if (fflush(out_fp) != 0) {
        fclose(out_fp);
        unlink(out_path);
        fclose(in_fp);
        fprintf(stderr, "FILM_FAIL stage=render_flush\n");
        return 6;
    }
    long out_bytes = ftell(out_fp);
    if (fclose(out_fp) != 0 || out_bytes <= 0) {
        unlink(out_path);
        fclose(in_fp);
        fprintf(stderr, "FILM_FAIL stage=render_close\n");
        return 6;
    }
    fclose(in_fp);
    printf("FILM_DONE %ld\n", out_bytes);
    return 0;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc == 2 && strcmp(argv[1], "--list") == 0)
        return cmd_list();
    if (argc == 3 && strcmp(argv[1], "--verify") == 0)
        return cmd_verify(argv[2]);
    if (argc == 3 && strcmp(argv[1], "--size") == 0)
        return cmd_size(argv[2]);

    int scale = 1;
    int argi = 1;
    if (argc >= 3 && strcmp(argv[1], "--scale") == 0) {
        if (strcmp(argv[2], "1") == 0) scale = 1;
        else if (strcmp(argv[2], "2") == 0) scale = 2;
        else if (strcmp(argv[2], "4") == 0) scale = 4;
        else {
            fprintf(stderr, "FILM_FAIL stage=args\n");
            return 2;
        }
        argi = 3;
    }
    int rem = argc - argi;
    if (rem >= 4 && strcmp(argv[argi], "--lut") == 0) {
        if (rem != 4 && rem != 6) {
            fprintf(stderr, "FILM_FAIL stage=usage\n");
            return 2;
        }
        int grain = 0, halation = 0;
        if (rem == 6) {
            grain = parse_digit_range(argv[argi + 4], 0, 3);
            halation = parse_digit_range(argv[argi + 5], 0, 1);
            if (grain < 0 || halation < 0) {
                fprintf(stderr, "FILM_FAIL stage=args\n");
                return 2;
            }
        }
        struct a7lt_lut lut;
        if (load_a7lt(argv[argi + 1], &lut) != 0) {
            fprintf(stderr, "FILM_FAIL stage=lut_load\n");
            return 3;
        }
        int rc = cmd_render(-1, &lut, argv[argi + 2], argv[argi + 3], grain, halation, scale);
        free_a7lt(&lut);
        return rc;
    }

    if (rem == 3 || rem == 5) {
        int pidx = parse_preset(argv[argi]);
        int grain = 0, halation = 0;
        if (rem == 5) {
            grain = parse_digit_range(argv[argi + 3], 0, 3);
            halation = parse_digit_range(argv[argi + 4], 0, 1);
        }
        if (pidx < 0 || grain < 0 || halation < 0) {
            fprintf(stderr, "FILM_FAIL stage=args\n");
            return 2;
        }
        return cmd_render(pidx, NULL, argv[argi + 1], argv[argi + 2], grain, halation, scale);
    }
    fprintf(stderr, "FILM_FAIL stage=usage\n");
    return 2;
}
