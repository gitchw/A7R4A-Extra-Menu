/* Minimal fail-safe cold-boot ld.so.preload constructor for ILCE-7RM4A (1.12).
 *
 * Safety design:
 *   - Built freestanding (-nostdlib) on ARM using raw Linux EABI syscalls so it
 *     has zero libc/TLS/errno initialization ordering dependencies when loaded
 *     via /etc/ld.so.preload before other DSOs initialize.
 *   - Immediately exits for every process except /usr/bin/im.elf (checked via
 *     both /proc/self/comm and readlink(/proc/self/exe)).
 *   - Honors escape hatches (/tmp/disable_hook and /etc/a7r4a_film/DISABLE).
 *   - Guards single-invocation per boot with O_CREAT|O_EXCL on /tmp_bt/.a7r4a-boot.lock
 *     (or EROFS fallback when /tmp_bt is not yet tmpfs-mounted).
 *   - Double-forks and reaps the intermediate child synchronously in im.elf so
 *     im.elf sees zero SIGCHLD/zombie and continues normal startup unhindered.
 */
#if defined(A7R4A_PRELOAD_HOST_TEST) && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L
#endif
#include <stdint.h>

#ifndef A7R4A_BOOT_COMM_PATH
#define A7R4A_BOOT_COMM_PATH "/proc/self/comm"
#endif
#ifndef A7R4A_BOOT_EXE_PATH
#define A7R4A_BOOT_EXE_PATH "/proc/self/exe"
#endif
#ifndef A7R4A_BOOT_TARGET_EXE
#define A7R4A_BOOT_TARGET_EXE "/usr/bin/im.elf"
#endif
#ifndef A7R4A_BOOT_DISABLE_1
#define A7R4A_BOOT_DISABLE_1 "/tmp/disable_hook"
#endif
#ifndef A7R4A_BOOT_DISABLE_2
#define A7R4A_BOOT_DISABLE_2 "/etc/a7r4a_film/DISABLE"
#endif
#ifndef A7R4A_BOOT_LOCK
#define A7R4A_BOOT_LOCK "/tmp_bt/.a7r4a-boot.lock"
#endif
#ifndef A7R4A_BOOT_SH_LOCK
#define A7R4A_BOOT_SH_LOCK "/tmp_bt/.a7r4a-boot-sh.lock"
#endif
#ifndef A7R4A_BOOT_SCRIPT
#define A7R4A_BOOT_SCRIPT "/etc/a7r4a_film/boot.sh"
#endif
#ifndef A7R4A_BOOT_SHELL
#define A7R4A_BOOT_SHELL "/bin/sh"
#endif

#if defined(__arm__) && !defined(A7R4A_PRELOAD_HOST_TEST)
static inline long sc0(long nr) {
    register long r0 __asm__("r0");
    register long r7 __asm__("r7") = nr;
    __asm__ volatile("svc #0" : "=r"(r0) : "r"(r7) : "memory", "cc");
    return r0;
}
static inline long sc1(long nr, long a0) {
    register long r0 __asm__("r0") = a0;
    register long r7 __asm__("r7") = nr;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r7) : "memory", "cc");
    return r0;
}
static inline long sc2(long nr, long a0, long a1) {
    register long r0 __asm__("r0") = a0;
    register long r1 __asm__("r1") = a1;
    register long r7 __asm__("r7") = nr;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r7) : "memory", "cc");
    return r0;
}
static inline long sc3(long nr, long a0, long a1, long a2) {
    register long r0 __asm__("r0") = a0;
    register long r1 __asm__("r1") = a1;
    register long r2 __asm__("r2") = a2;
    register long r7 __asm__("r7") = nr;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory", "cc");
    return r0;
}
static inline long sc4(long nr, long a0, long a1, long a2, long a3) {
    register long r0 __asm__("r0") = a0;
    register long r1 __asm__("r1") = a1;
    register long r2 __asm__("r2") = a2;
    register long r3 __asm__("r3") = a3;
    register long r7 __asm__("r7") = nr;
    __asm__ volatile("svc #0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r7) : "memory", "cc");
    return r0;
}

#define sys_exit(c)        ((void)sc1(1, (long)(c)))
#define sys_fork()         sc0(2)
#define sys_read(f, b, n)  sc3(3, (long)(f), (long)(b), (long)(n))
#define sys_open(p, f, m)  sc3(5, (long)(p), (long)(f), (long)(m))
#define sys_close(f)       sc1(6, (long)(f))
#define sys_execve(p,a,e)  sc3(11, (long)(p), (long)(a), (long)(e))
#define sys_dup2(o, n)     sc2(63, (long)(o), (long)(n))
#define sys_setsid()       sc0(66)
#define sys_readlink(p,b,n) sc3(85, (long)(p), (long)(b), (long)(n))
#define sys_setpriority(w, who, pr) sc3(97, (long)(w), (long)(who), (long)(pr))
#define sys_wait4(p,s,o,r) sc4(114, (long)(p), (long)(s), (long)(o), (long)(r))
#define sys_sched_setscheduler(p, pol, par) sc3(156, (long)(p), (long)(pol), (long)(par))
#define sys_rt_sigprocmask(h, s, o, sz) sc4(175, (long)(h), (long)(s), (long)(o), (long)(sz))

#define SYS_O_RDONLY   0
#define SYS_O_WRONLY   1
#define SYS_O_RDWR     2
#define SYS_O_CREAT    0100
#define SYS_O_EXCL     0200
#define SYS_O_NONBLOCK 04000
#define SYS_EINTR      4
#define SYS_EEXIST     17
#define SYS_EROFS      30

#else
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#ifdef __linux__
#include <sched.h>
#endif

#define sys_exit(c)        _exit(c)
#define sys_fork()         ((long)fork())
#define sys_read(f, b, n)  ((long)read((f), (b), (size_t)(n)))
static long sys_open(const char *p, int f, int m) {
    int r = open(p, f, m);
    return r < 0 ? -(long)errno : (long)r;
}
#define sys_close(f)       ((void)close(f))
#define sys_execve(p,a,e)  ((long)execve((p), (char *const *)(a), (char *const *)(e)))
#define sys_dup2(o, n)     ((long)dup2((o), (n)))
#define sys_setsid()       ((long)setsid())
#define sys_readlink(p,b,n) ((long)readlink((p), (b), (size_t)(n)))
static long sys_wait4(long p, int *s, int o, void *r) {
    (void)r;
    pid_t w = waitpid((pid_t)p, s, o);
    return w < 0 ? -(long)errno : (long)w;
}

#define SYS_O_RDONLY   O_RDONLY
#define SYS_O_WRONLY   O_WRONLY
#define SYS_O_RDWR     O_RDWR
#define SYS_O_CREAT    O_CREAT
#define SYS_O_EXCL     O_EXCL
#define SYS_O_NONBLOCK O_NONBLOCK
#define SYS_EINTR      EINTR
#define SYS_EEXIST     EEXIST
#define SYS_EROFS      EROFS
#endif

static int eq_mem(const char *a, const char *b, unsigned n) {
    for (unsigned i = 0; i < n; ++i) {
        if (a[i] != b[i]) return 0;
    }
    return 1;
}

static unsigned c_len(const char *s) {
    unsigned n = 0;
    while (s[n]) ++n;
    return n;
}

static int file_exists(const char *path) {
    long fd = sys_open(path, SYS_O_RDONLY | SYS_O_NONBLOCK, 0);
    if (fd >= 0) {
        sys_close((int)fd);
        return 1;
    }
    return 0;
}

static int is_target_im(void) {
    char comm[16];
    long fd = sys_open(A7R4A_BOOT_COMM_PATH, SYS_O_RDONLY, 0);
    if (fd < 0) return 0;
    long n = sys_read((int)fd, comm, sizeof(comm));
    sys_close((int)fd);
    if (n != 7 || !eq_mem(comm, "im.elf\n", 7)) return 0;

    char exe[128];
    long rn = sys_readlink(A7R4A_BOOT_EXE_PATH, exe, sizeof(exe) - 1);
    unsigned tlen = c_len(A7R4A_BOOT_TARGET_EXE);
    if (rn != (long)tlen || !eq_mem(exe, A7R4A_BOOT_TARGET_EXE, tlen)) return 0;
    return 1;
}

static void reset_child_context(void) {
#if defined(__arm__) && !defined(A7R4A_PRELOAD_HOST_TEST)
    struct {
        int sched_priority;
    } sp;
    sp.sched_priority = 0;
    (void)sys_sched_setscheduler(0, 0 /* SCHED_OTHER */, &sp);
    (void)sys_setpriority(0 /* PRIO_PROCESS */, 0, 0);

    uint32_t sigmask[2];
    sigmask[0] = 0xffffffffu;
    sigmask[1] = 0xffffffffu;
    (void)sys_rt_sigprocmask(1 /* SIG_UNBLOCK */, sigmask, (void *)0, 8);
#else
#ifdef __linux__
    struct sched_param sp;
    sp.sched_priority = 0;
    (void)sched_setscheduler(0, SCHED_OTHER, &sp);
#endif
    (void)setpriority(PRIO_PROCESS, 0, 0);
    sigset_t all;
    sigfillset(&all);
    (void)sigprocmask(SIG_UNBLOCK, &all, (void *)0);
#endif
}

__attribute__((constructor, visibility("default")))
void a7r4a_boot_preload_init(void) {
    static volatile int seen_in_proc = 0;
    if (seen_in_proc) return;
    seen_in_proc = 1;

    if (!is_target_im()) return;
    if (file_exists(A7R4A_BOOT_DISABLE_1) || file_exists(A7R4A_BOOT_DISABLE_2)) return;
    if (!file_exists(A7R4A_BOOT_SCRIPT)) return;
    if (file_exists(A7R4A_BOOT_SH_LOCK)) return;

    long lfd = sys_open(A7R4A_BOOT_LOCK, SYS_O_WRONLY | SYS_O_CREAT | SYS_O_EXCL, 0600);
    if (lfd >= 0) {
        sys_close((int)lfd);
    } else if (lfd != -SYS_EROFS) {
        return;
    }

    long pid1 = sys_fork();
    if (pid1 < 0) return;
    if (pid1 > 0) {
        int status = 0;
        while (sys_wait4(pid1, &status, 0, (void *)0) == -SYS_EINTR) {}
        return;
    }

    (void)sys_setsid();
    long pid2 = sys_fork();
    if (pid2 != 0) sys_exit(0);

    /* Reset RT scheduler/priority, signal mask, and inherited im.elf FDs
     * inside the detached grandchild before execve(/bin/sh). */
    reset_child_context();

    long nfd = sys_open("/dev/null", SYS_O_RDWR, 0);
    if (nfd >= 0) {
        if (nfd != 0) (void)sys_dup2((int)nfd, 0);
        if (nfd != 1) (void)sys_dup2((int)nfd, 1);
        if (nfd != 2) (void)sys_dup2((int)nfd, 2);
        if (nfd > 2) sys_close((int)nfd);
    }
    for (int fd = 3; fd < 1024; ++fd) {
        sys_close(fd);
    }

    const char *argv[3];
    argv[0] = A7R4A_BOOT_SHELL;
    argv[1] = A7R4A_BOOT_SCRIPT;
    argv[2] = (const char *)0;
    const char *envp[2];
    envp[0] = "PATH=/usr/bin:/bin:/sbin:/usr/sbin";
    envp[1] = (const char *)0;
    (void)sys_execve(A7R4A_BOOT_SHELL, argv, envp);
    sys_exit(127);
}
