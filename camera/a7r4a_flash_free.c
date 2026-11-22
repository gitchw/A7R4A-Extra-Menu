/* Read-only filesystem free-space reporter for ILCE-7RM4A preflight checks. */
#include <stdio.h>
#include <sys/statvfs.h>

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    const char *path = argc > 1 ? argv[1] : "/etc";
    struct statvfs st;
    if (statvfs(path, &st) != 0) {
        perror("statvfs");
        return 1;
    }
    unsigned long long bsize = st.f_frsize ? (unsigned long long)st.f_frsize
                                           : (unsigned long long)st.f_bsize;
    unsigned long long total_kb = ((unsigned long long)st.f_blocks * bsize) / 1024ULL;
    unsigned long long free_kb = ((unsigned long long)st.f_bavail * bsize) / 1024ULL;
    printf("FLASH_STAT path=%s total_kb=%llu free_kb=%llu\n", path, total_kb, free_kb);
    fflush(stdout);
    return 0;
}
