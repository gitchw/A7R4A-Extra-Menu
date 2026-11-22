#define _GNU_SOURCE
#include "film_settings_source.h"
#include "film_settings_maps.h"
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/syscall.h>
#endif
#ifdef A7R4A_DYNAMIC_IDENTITY
#include "a7r4a_target.h"
#endif

int film_settings_now(uint32_t *out) {
    struct timespec t;
#ifdef __linux__
    if (syscall(SYS_clock_gettime, CLOCK_MONOTONIC, &t)) return -1;
#else
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return -1;
#endif
    *out = (uint32_t)((uint64_t)t.tv_sec * 1000u + (unsigned)t.tv_nsec / 1000000u);
    return 0;
}

int film_file_matches(const char *path, uint32_t bytes, uint32_t expected) {
    unsigned char data[4096];
    uint32_t hash = 2166136261u, total = 0;
    ssize_t n;
    /* Avoid modern fstat symbols absent from camera glibc2.11. Exact bounded
     * read+EOF enforces the pinned length; nonblocking also rejects FIFO data. */
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return 0;
    while ((n = read(fd, data, sizeof(data))) > 0) {
        if (total > bytes || (unsigned)n > bytes - total) { close(fd); return 0; }
        for (ssize_t i = 0; i < n; i++) hash = (hash ^ data[i]) * 16777619u;
        total += (unsigned)n;
    }
    close(fd);
    return n == 0 && total == bytes && hash == expected;
}

static int proc_path(char *out, unsigned capacity, const char *root, const char *name) {
    int n = snprintf(out, capacity, "%s/%s", root, name);
    return n > 0 && (unsigned)n < capacity;
}

#ifdef A7R4A_DYNAMIC_IDENTITY
static int find_proc_im(char *out, unsigned capacity) {
    DIR *dir = opendir("/proc");
    if (!dir) return 0;
    struct dirent *entry;
    long found = -1;
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
        if (ok) { found = pid; ++matches; }
    }
    closedir(dir);
    if (matches != 1u || found <= 0) return 0;
    int n = snprintf(out, capacity, "/proc/%ld", found);
    return n > 0 && (unsigned)n < capacity;
}

static int map_rx_base(const char *line, const char *exact_path, uint32_t *base_out, unsigned *count) {
    unsigned lo = 0, hi = 0, offset = 0;
    char perms[5] = {0};
    int at = 0;
    if (sscanf(line, "%x-%x %4s %x %*s %*s %n", &lo, &hi, perms, &offset, &at) != 4 || at <= 0)
        return 0;
    size_t len = strcspn(line + at, "\r\n");
    if (!offset && !strcmp(perms, "r-xp") && len == strlen(exact_path) &&
        !memcmp(line + at, exact_path, len)) {
        *base_out = lo;
        ++(*count);
        return 1;
    }
    return 0;
}
#endif

int film_settings_source_get(struct film_settings *out, const struct film_settings_source *s) {
    char path[512], line[512], proc_root[64];
    if (!out || !s || !s->proc || !s->module ||
        !film_file_matches(s->module, s->bytes, s->fnv)) return -17;
#ifdef A7R4A_DYNAMIC_IDENTITY
    if (!strcmp(s->proc, "/proc/auto")) {
        if (!find_proc_im(proc_root, sizeof(proc_root))) return -10;
    } else
#endif
    {
        int pn = snprintf(proc_root, sizeof(proc_root), "%s", s->proc);
        if (pn <= 0 || (unsigned)pn >= sizeof(proc_root)) return -10;
    }
    if (!proc_path(path, sizeof(path), proc_root, "comm")) return -10;
    FILE *f = fopen(path, "r");
    if (!f) return -10;
    int ok = fgets(line, sizeof(line), f) && !strcmp(line, "im.elf\n");
    fclose(f);
    if (!ok) return -11;
    if (!proc_path(path, sizeof(path), proc_root, "maps")) return -12;
    f = fopen(path, "r");
    if (!f) return -12;
    struct film_settings_map map = {0};
#ifdef A7R4A_DYNAMIC_IDENTITY
    uint32_t libobj_base = 0, view2_base = 0;
    unsigned libobj_n = 0, view2_n = 0;
#endif
    while (fgets(line, sizeof(line), f)) {
        film_settings_map_line(&map, line, s->module, s->rva);
#ifdef A7R4A_DYNAMIC_IDENTITY
        map_rx_base(line, A7R4A_LIBOBJ_PATH, &libobj_base, &libobj_n);
        map_rx_base(line, A7R4A_VIEW2_PATH, &view2_base, &view2_n);
#endif
    }
    ok = !ferror(f); fclose(f);
    if (!ok) return -12;
    if (!map.image_count) return 10;
    if (map.image_count != 1 || map.state_count != 1) return -13;
#ifdef A7R4A_DYNAMIC_IDENTITY
    if (libobj_n != 1u || view2_n != 1u || !libobj_base || !view2_base) return -13;
#endif
    if (!proc_path(path, sizeof(path), proc_root, "mem")) return -14;
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -14;
    unsigned char first[FILM_UI_SNAPSHOT_BYTES], second[FILM_UI_SNAPSHOT_BYTES];
    ok = pread(fd, first, sizeof(first), map.state) == sizeof(first) &&
         pread(fd, second, sizeof(second), map.state) == sizeof(second);
#ifdef A7R4A_DYNAMIC_IDENTITY
    uint32_t expected_system = 0, trace_hdr[12] = {0};
    if (ok) {
        ok = pread(fd, &expected_system, 4, (off_t)(libobj_base + A7R4A_SYSTEM_PTR_RVA)) == 4 &&
             pread(fd, trace_hdr, sizeof(trace_hdr), (off_t)(view2_base + A7R4A_STATE_RVA)) == sizeof(trace_hdr);
    }
#endif
    close(fd);
    if (!ok) return -15;
    uint32_t now;
    if (film_settings_now(&now)) return -16;
#ifdef A7R4A_DYNAMIC_IDENTITY
    /* trace_hdr[0..7] = trace meta, trace_hdr[8..10] = events[0].{sequence,tid,system}. */
    if (!expected_system || trace_hdr[0] != A7R4A_TRACE_MAGIC || trace_hdr[1] != 1u ||
        trace_hdr[8] != 1u || !trace_hdr[9] || trace_hdr[10] != expected_system)
        return -1;
    return film_settings_snapshot_get_for(out, first, second, sizeof(first), now,
                                          expected_system, trace_hdr[9]);
#else
    return film_settings_snapshot_get(out, first, second, sizeof(first), now);
#endif
}
