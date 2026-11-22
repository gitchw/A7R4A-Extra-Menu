/* ASLR-safe, exact-version, reversible RAM-only trace/loader guard for
 * Sony ILCE-7RM4A (Ver. 1.12). Resolves im.elf PID and module bases from
 * /proc/<pid>/maps at runtime and patches the payload's 24-byte runtime table.
 */
#define _DEFAULT_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <unistd.h>
#include "a7r4a_target.h"
#include "native_freeze.h"

#define DEFAULT_REQ_PATH "/tmp_bt/a7r4a-ui-load.req"
#define RUNTIME_TABLE_BYTES 24u
#define A7R4A_SCRATCH_MAX \
    (A7R4A_CODE_MAX > A7R4A_STATE_ZERO_BYTES ? A7R4A_CODE_MAX : A7R4A_STATE_ZERO_BYTES)

_Static_assert(A7R4A_SCRATCH_MAX >= A7R4A_CODE_MAX, "scratch >= CODE_MAX");
_Static_assert(A7R4A_SCRATCH_MAX >= A7R4A_STATE_ZERO_BYTES, "scratch >= STATE_ZERO_BYTES");

static const unsigned char original[12] = {
    0x2d, 0xe9, 0xf0, 0x47, 0x88, 0xb0, 0x4f, 0xf0, 0x14, 0x08, 0x01, 0x22
};
static unsigned char payload[A7R4A_CODE_MAX];
static unsigned char zeros[A7R4A_SCRATCH_MAX];
static unsigned char buffer[A7R4A_SCRATCH_MAX];

struct resolved_maps {
    uint32_t libobj_base;
    uint32_t view2_base;
    uint32_t libimm_base;
    uint32_t libdl_base;
    uint32_t hook_va;
    uint32_t hook_resume_va;
    uint32_t code_va;
    uint32_t state_va;
    uint32_t dlopen_va;
    uint32_t dlsym_va;
    uint32_t system_ptr_va;
};

static uint32_t little32(const unsigned char *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void put_little32(unsigned char *p, uint32_t v) {
    p[0] = (unsigned char)(v & 0xffu);
    p[1] = (unsigned char)((v >> 8) & 0xffu);
    p[2] = (unsigned char)((v >> 16) & 0xffu);
    p[3] = (unsigned char)((v >> 24) & 0xffu);
}

static int file_fnv_matches(const char *path, uint32_t bytes, uint32_t expected) {
    unsigned char block[4096];
    uint32_t hash = 2166136261u, total = 0;
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return 0;
    ssize_t n;
    while ((n = read(fd, block, sizeof(block))) > 0) {
        if (total > bytes || (unsigned)n > bytes - total) {
            close(fd);
            return 0;
        }
        for (ssize_t i = 0; i < n; i++)
            hash = (hash ^ block[i]) * 16777619u;
        total += (unsigned)n;
    }
    close(fd);
    return n == 0 && total == bytes && hash == expected;
}

static pid_t find_im_pid(void) {
    DIR *dir = opendir("/proc");
    if (!dir) return -1;
    struct dirent *entry;
    pid_t found = -1;
    unsigned matches = 0;
    while ((entry = readdir(dir))) {
        char *end = NULL;
        long pid = strtol(entry->d_name, &end, 10);
        if (!*entry->d_name || *end || pid <= 0) continue;
        char path[64], comm[32] = {0};
        snprintf(path, sizeof(path), "/proc/%ld/comm", pid);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        int ok = fgets(comm, sizeof(comm), f) && !strcmp(comm, A7R4A_PROC_COMM "\n");
        fclose(f);
        if (ok) {
            found = (pid_t)pid;
            matches++;
        }
    }
    closedir(dir);
    return matches == 1u ? found : -1;
}

static int readmem(int fd, uint32_t a, void *b, unsigned n) {
    return pread(fd, b, n, a) == (ssize_t)n ? 0 : -1;
}

static int poke(pid_t pid, uint32_t a, const void *b, unsigned n) {
    if ((a | n) & 3u) return -1;
    for (unsigned i = 0; i < n; i += 4) {
        uint32_t word;
        memcpy(&word, (const unsigned char *)b + i, 4);
        if (ptrace(PTRACE_POKETEXT, pid, (void *)(uintptr_t)(a + i), (void *)(uintptr_t)word))
            return -1;
    }
    return 0;
}

static int resolve_maps(pid_t pid, struct resolved_maps *out) {
    char path[64], line[512];
    snprintf(path, sizeof(path), "/proc/%ld/maps", (long)pid);
    FILE *f = fopen(path, "r");
    if (!f) return 0;

    memset(out, 0, sizeof(*out));
    int have_libobj_rx = 0, have_view2_rx = 0, have_libimm_rx = 0, have_libdl_rx = 0;
    int hook_ok = 0, code_ok = 0, state_ok = 0, sysptr_ok = 0;
    uint32_t libobj_rw_hi = 0, view2_rw_hi = 0;
    int prev_was_libobj_rw = 0, prev_was_view2_rw = 0;

    while (fgets(line, sizeof(line), f)) {
        unsigned lo = 0, hi = 0, offset = 0;
        char perms[5] = {0}, dev[16] = {0}, inode[24] = {0};
        int at = 0;
        if (sscanf(line, "%x-%x %4s %x %15s %23s %n",
                   &lo, &hi, perms, &offset, dev, inode, &at) != 6 || at <= 0 || lo >= hi)
            continue;
        size_t len = strcspn(line + at, "\r\n");
        const char *pathname = line + at;
        int is_anon = (!len && !offset && !strcmp(dev, "00:00") && !strcmp(inode, "0"));

        int is_libobj = (len == strlen(A7R4A_LIBOBJ_PATH) && !memcmp(pathname, A7R4A_LIBOBJ_PATH, len));
        int is_view2 = (len == strlen(A7R4A_VIEW2_PATH) && !memcmp(pathname, A7R4A_VIEW2_PATH, len));
        int is_libimm = (len == strlen(A7R4A_LIBIMM_PATH) && !memcmp(pathname, A7R4A_LIBIMM_PATH, len));
        int is_libdl = (len == strlen(A7R4A_LIBDL_PATH) && !memcmp(pathname, A7R4A_LIBDL_PATH, len));

        if (!strcmp(perms, "r-xp") && offset == 0) {
            if (is_libobj) {
                have_libobj_rx++;
                out->libobj_base = lo;
                out->hook_va = lo + A7R4A_HOOK_RVA;
                out->hook_resume_va = lo + A7R4A_HOOK_RESUME_RVA;
                out->system_ptr_va = lo + A7R4A_SYSTEM_PTR_RVA;
                if (out->hook_va >= lo && out->hook_va + 12u <= hi) hook_ok = 1;
            } else if (is_view2) {
                have_view2_rx++;
                out->view2_base = lo;
                out->state_va = lo + A7R4A_STATE_RVA;
            } else if (is_libimm) {
                have_libimm_rx++;
                out->libimm_base = lo;
                out->code_va = lo + A7R4A_CODE_RVA;
                if (out->code_va >= lo && out->code_va + A7R4A_CODE_MAX <= hi) code_ok = 1;
            } else if (is_libdl) {
                have_libdl_rx++;
                out->libdl_base = lo;
                out->dlopen_va = lo + A7R4A_DLOPEN_RVA;
                out->dlsym_va = lo + A7R4A_DLSYM_RVA;
            }
        }

        if (!strcmp(perms, "rw-p")) {
            if (have_libobj_rx == 1 &&
                (is_libobj || (is_anon && prev_was_libobj_rw && lo == libobj_rw_hi))) {
                if (out->system_ptr_va >= lo && out->system_ptr_va + 4u <= hi) sysptr_ok = 1;
                libobj_rw_hi = hi;
                prev_was_libobj_rw = 1;
            } else {
                prev_was_libobj_rw = 0;
            }
            if (have_view2_rx == 1 &&
                (is_view2 || (is_anon && prev_was_view2_rw && lo == view2_rw_hi))) {
                if (out->state_va >= lo && out->state_va + A7R4A_STATE_ZERO_BYTES <= hi) state_ok = 1;
                view2_rw_hi = hi;
                prev_was_view2_rw = 1;
            } else {
                prev_was_view2_rw = 0;
            }
        } else {
            prev_was_libobj_rw = 0;
            prev_was_view2_rw = 0;
        }
    }
    fclose(f);
    return have_libobj_rx == 1 && have_view2_rx == 1 &&
           have_libimm_rx == 1 && have_libdl_rx == 1 &&
           hook_ok && code_ok && state_ok && sysptr_ok;
}

int main(int argc, char **argv) {
    (void)setvbuf(stdout, NULL, _IONBF, 0);
    const char *req_path = DEFAULT_REQ_PATH;
    int want_op = -1;
    if (argc == 2) {
        if (!strcmp(argv[1], "check")) want_op = 0;
        else if (!strcmp(argv[1], "install")) want_op = 1;
        else if (!strcmp(argv[1], "remove")) want_op = 2;
        else req_path = argv[1];
    } else if (argc == 3) {
        if (!strcmp(argv[1], "check")) want_op = 0;
        else if (!strcmp(argv[1], "install")) want_op = 1;
        else if (!strcmp(argv[1], "remove")) want_op = 2;
        else { printf("GUARD_FAIL arg\n"); return 2; }
        req_path = argv[2];
    } else if (argc > 3 || argc < 1) {
        printf("GUARD_FAIL argc=%d\n", argc);
        return 2;
    }

    uint32_t h[8];
    FILE *f = fopen(req_path, "rb");
    if (!f && argc == 2 && want_op >= 0) {
        static const char *const fallback_reqs[3] = {
            "/tmp_bt/a7r4a-check.req",
            "/tmp_bt/a7r4a-install.req",
            "/tmp_bt/a7r4a-remove.req"
        };
        f = fopen(fallback_reqs[want_op], "rb");
    }
    if (!f) { printf("GUARD_FAIL open req=%s\n", req_path); return 2; }
    if (fread(h, 1, sizeof(h), f) != sizeof(h) ||
        h[0] != A7R4A_REQ_MAGIC || h[1] != A7R4A_REQ_VERSION || h[2] > 2u ||
        (want_op >= 0 && h[2] != (uint32_t)want_op) ||
        h[3] < RUNTIME_TABLE_BYTES || h[3] > sizeof(payload) || (h[3] & 3u) ||
        h[4] != 1u || h[6] != A7R4A_STATE_SIZE || h[7] != A7R4A_HOOK_RVA ||
        fread(payload, 1, h[3], f) != h[3] || fgetc(f) != EOF) {
        fclose(f);
        printf("GUARD_FAIL req_header\n");
        return 3;
    }
    fclose(f);

    uint32_t sum = 2166136261u;
    for (unsigned i = 0; i < h[3]; i++) sum = (sum ^ payload[i]) * 16777619u;
    if (sum != h[5]) { printf("GUARD_FAIL payload_fnv\n"); return 4; }

    unsigned char *rt = payload + h[3] - RUNTIME_TABLE_BYTES;
    if (little32(rt) != A7R4A_PAYLOAD_TAIL_MAGIC) { printf("GUARD_FAIL tail_magic\n"); return 4; }
    for (unsigned i = 4; i < RUNTIME_TABLE_BYTES; i += 4) {
        if (little32(rt + i) != 0u) { printf("GUARD_FAIL tail_table\n"); return 4; }
    }

    if (!file_fnv_matches(A7R4A_LIBOBJ_PATH, A7R4A_LIBOBJ_BYTES, A7R4A_LIBOBJ_FNV) ||
        !file_fnv_matches(A7R4A_VIEW2_PATH, A7R4A_VIEW2_BYTES, A7R4A_VIEW2_FNV) ||
        !file_fnv_matches(A7R4A_LIBIMM_PATH, A7R4A_LIBIMM_BYTES, A7R4A_LIBIMM_FNV) ||
        !file_fnv_matches(A7R4A_LIBDL_PATH, A7R4A_LIBDL_BYTES, A7R4A_LIBDL_FNV)) {
        printf("GUARD_FAIL target_fnv\n");
        return 5;
    }

    pid_t im_pid = find_im_pid();
    if (im_pid <= 0) { printf("GUARD_FAIL im_pid\n"); return 5; }

    struct resolved_maps maps;
    if (!resolve_maps(im_pid, &maps)) { printf("GUARD_FAIL maps pid=%ld\n", (long)im_pid); return 5; }

    put_little32(rt + 4, maps.state_va);
    put_little32(rt + 8, maps.dlopen_va);
    put_little32(rt + 12, maps.dlsym_va);
    put_little32(rt + 16, maps.system_ptr_va);
    put_little32(rt + 20, maps.hook_resume_va);

    uint32_t code_entry = maps.code_va | 1u;
    unsigned char branch[12] = {0xdf, 0xf8, 0, 0xf0, 0, 0, 0, 0, 0, 0xbf, 0, 0xbf};
    memcpy(branch + 4, &code_entry, 4);

    unsigned long long t0 = 0, t1 = 0, t2 = 0, t3 = 0, t4 = 0;
    int result = -1, fd = -1, changed = 0;
    for (unsigned attempt = 0; attempt < 6u; attempt++) {
        errno = 0;
        t0 = freeze_ms();
        result = freeze_process_pid(im_pid);
        t1 = freeze_ms();
        if (!result) { errno = 0; break; }
        if (result != -95 && result != -97 && result != -98) break;
        usleep(500000);
    }
    if (result) goto end;

    char mem_path[64];
    snprintf(mem_path, sizeof(mem_path), "/proc/%ld/mem", (long)im_pid);
    fd = open(mem_path, h[2] ? O_RDWR : O_RDONLY);
    if (fd < 0) { result = -10; goto done; }

    struct resolved_maps maps_check;
    if (!resolve_maps(im_pid, &maps_check) ||
        memcmp(&maps, &maps_check, sizeof(maps))) {
        result = -11;
        goto done;
    }

    uint32_t expected_system = 0;
    if (readmem(fd, maps.system_ptr_va, &expected_system, 4) || !expected_system) {
        result = -11;
        goto done;
    }

    if (readmem(fd, maps.hook_va, buffer, 12) ||
        memcmp(buffer, h[2] == 2u ? branch : original, 12)) {
        result = -12;
        goto done;
    }

    for (unsigned i = 0; i < freeze_count; i++) {
        uint32_t regs[18];
        if (ptrace(PTRACE_GETREGS, freeze_tids[i], 0, regs)) { result = -13; goto done; }
        uint32_t pc = regs[15] & ~1u;
        if ((maps.hook_va <= pc && pc < maps.hook_va + 12u) ||
            (maps.code_va <= pc && pc < maps.code_va + h[3])) {
            result = -14;
            goto done;
        }
    }
    t2 = freeze_ms();

    if (h[2] == 2u) {
        uint32_t state[8];
        if (readmem(fd, maps.state_va, state, sizeof(state)) ||
            state[0] != A7R4A_TRACE_MAGIC || state[1] != 1u ||
            state[5] != 24u || state[7] != expected_system) {
            result = -15;
            goto done;
        }
        uint32_t disabled = 0;
        if (pwrite(fd, &disabled, 4, maps.state_va + 8u) != 4) { result = -16; goto done; }
        if (state[4]) { result = -17; goto done; }
        if (readmem(fd, maps.code_va, buffer, h[3]) || memcmp(buffer, payload, h[3])) {
            result = -18;
            goto done;
        }
        if (poke(im_pid, maps.hook_va, original, 12) ||
            poke(im_pid, maps.code_va, zeros, h[3]) ||
            pwrite(fd, zeros, A7R4A_STATE_SIZE, maps.state_va) != A7R4A_STATE_SIZE) {
            result = -19;
            goto done;
        }
        t3 = freeze_ms();
        if (readmem(fd, maps.hook_va, buffer, 12) || memcmp(buffer, original, 12) ||
            readmem(fd, maps.code_va, buffer, h[3]) || memcmp(buffer, zeros, h[3]) ||
            readmem(fd, maps.state_va, buffer, A7R4A_STATE_SIZE) || memcmp(buffer, zeros, A7R4A_STATE_SIZE)) {
            result = -20;
            goto done;
        }
        printf("REMOVED pid=%ld hook=%08x code=%08x state=%08x restored=1\n",
               (long)im_pid, maps.hook_va, maps.code_va, maps.state_va);
    } else {
        if (readmem(fd, maps.code_va, buffer, A7R4A_CODE_MAX) ||
            memcmp(buffer, zeros, A7R4A_CODE_MAX) ||
            readmem(fd, maps.state_va, buffer, A7R4A_STATE_ZERO_BYTES) ||
            memcmp(buffer, zeros, A7R4A_STATE_ZERO_BYTES)) {
            result = -21;
            goto done;
        }
        printf("CHECK op=%u pid=%ld entry=%08x hook=%08x state=%08x sys=%08x code_bytes=%u zero_pad=1 pc_clear=1\n",
               h[2], (long)im_pid, code_entry, maps.hook_va, maps.state_va, expected_system, h[3]);
        if (h[2] == 1u) {
            uint32_t state[8] = {A7R4A_TRACE_MAGIC, 1u, 1u, 0u, 0u, 24u, 0u, expected_system};
            changed = 1;
            if (pwrite(fd, state, sizeof(state), maps.state_va) != sizeof(state) ||
                poke(im_pid, maps.code_va, payload, h[3]) ||
                poke(im_pid, maps.hook_va, branch, 12)) {
                result = -22;
                goto rollback;
            }
            t3 = freeze_ms();
            if (readmem(fd, maps.hook_va, buffer, 12) || memcmp(buffer, branch, 12) ||
                readmem(fd, maps.code_va, buffer, h[3]) || memcmp(buffer, payload, h[3]) ||
                readmem(fd, maps.state_va, buffer, sizeof(state)) || memcmp(buffer, state, sizeof(state))) {
                result = -23;
                goto rollback;
            }
            printf("INSTALLED pid=%ld code=%08x hook=%08x state=%08x sys=%08x\n",
                   (long)im_pid, maps.code_va, maps.hook_va, maps.state_va, expected_system);
        }
    }
    goto done;

rollback:
    if (changed) {
        int ok = !poke(im_pid, maps.hook_va, original, 12) &&
                 !poke(im_pid, maps.code_va, zeros, h[3]) &&
                 pwrite(fd, zeros, A7R4A_STATE_SIZE, maps.state_va) == A7R4A_STATE_SIZE;
        printf("ROLLBACK result=%d\n", ok ? 0 : -1);
        if (!ok) result = -99;
    }

done:
    t4 = freeze_ms();
    if (fd >= 0) close(fd);
    if (freeze_resume()) result = -99;

end:
    printf("PHASE freeze_ms=%llu preflight_ms=%llu write_ms=%llu verify_ms=%llu resume_ms=%llu total_ms=%llu\n",
           t1 - t0, t2 ? t2 - t1 : 0, t3 ? t3 - t2 : 0,
           t4 && t3 ? t4 - t3 : 0, t4 ? freeze_ms() - t4 : 0, freeze_ms() - t0);
    printf("END result=%d op=%u errno=%d\n", result, h[2], errno);
    fflush(stdout);
    return result ? 6 : 0;
}
