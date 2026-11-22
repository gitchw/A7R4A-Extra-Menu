/* Clean-room remote shutter, astro intervalometer, and focus-bracketing CLI
 * for ILCE-7RM4A (Ver. 1.12). Uses verified camera OSAL InfraInput test queue
 * 0x007A018C and remote key IDs (S1=0x1A00000D, S2=0x1A00000E,
 * FOCUS_DRIVE=0x1A000025) via /usr/bin/sndcmd.elf.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define A7R4A_SNDCMD_DEFAULT "/usr/bin/sndcmd.elf"
#define A7R4A_QUEUE_STR "0x007A018C"
#define A7R4A_KEY_S1 0x1A00000Du
#define A7R4A_KEY_S2 0x1A00000Eu
#define A7R4A_KEY_FOCUS_DRIVE 0x1A000025u

#ifndef A7R4A_FIXED_SNDCMD
#define A7R4A_FIXED_SNDCMD 0
#endif

static volatile sig_atomic_t g_stop_sig = 0;
static volatile sig_atomic_t g_s1_down = 0;
static volatile sig_atomic_t g_s2_down = 0;

static void on_stop_signal(int sig) {
    if (!g_stop_sig)
        g_stop_sig = sig;
}

static int install_signal_handlers(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_stop_signal;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGINT, &sa, NULL) != 0)
        return -1;
    if (sigaction(SIGTERM, &sa, NULL) != 0)
        return -1;
    if (sigaction(SIGHUP, &sa, NULL) != 0)
        return -1;
    return 0;
}

static int mono_ms(uint64_t *out) {
    struct timespec ts;
#ifdef __APPLE__
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return -1;
#else
    if (syscall(SYS_clock_gettime, CLOCK_MONOTONIC, &ts) != 0)
        return -1;
#endif
    *out = (uint64_t)ts.tv_sec * 1000u + (uint64_t)(ts.tv_nsec / 1000000u);
    return 0;
}

static int sleep_interruptible_ms(long ms, int dry_run) {
    if (dry_run || ms <= 0)
        return g_stop_sig ? -1 : 0;
    uint64_t start = 0, now = 0;
    if (mono_ms(&start) != 0)
        return -1;
    for (;;) {
        if (g_stop_sig)
            return -1;
        if (mono_ms(&now) != 0)
            return -1;
        uint64_t elapsed = now - start;
        if (elapsed >= (uint64_t)ms)
            return 0;
        uint64_t rem = (uint64_t)ms - elapsed;
        int chunk = rem > 25u ? 25 : (int)rem;
        int rc = poll(NULL, 0, chunk);
        if (rc < 0 && errno != EINTR)
            return -1;
    }
}

static const char *resolve_sndcmd_path(void) {
#if A7R4A_FIXED_SNDCMD
    return A7R4A_SNDCMD_DEFAULT;
#else
    const char *env = getenv("A7R4A_SNDCMD_BIN");
    if (env && env[0] != '\0')
        return env;
    return A7R4A_SNDCMD_DEFAULT;
#endif
}

static int send_key_event(const char *sndcmd, int dry_run, uint32_t key, int press, uint32_t ex_info) {
    char key_arg[24];
    char press_arg[16];
    char ex_arg[24];
    snprintf(key_arg, sizeof(key_arg), "d:0x%08X", key);
    snprintf(press_arg, sizeof(press_arg), "d:%d", press ? 1 : 0);
    if (ex_info == 0u)
        snprintf(ex_arg, sizeof(ex_arg), "d:0");
    else
        snprintf(ex_arg, sizeof(ex_arg), "d:0x%x", ex_info);

    if (dry_run) {
        printf("DRY_RUN %s --sync %s d:2 %s d:0 %s %s\n",
               sndcmd, A7R4A_QUEUE_STR, key_arg, press_arg, ex_arg);
        return 0;
    }

    pid_t pid = fork();
    if (pid < 0)
        return -1;
    if (pid == 0) {
        char *const argv[] = {
            (char *)sndcmd,
            (char *)"--sync",
            (char *)A7R4A_QUEUE_STR,
            (char *)"d:2",
            key_arg,
            (char *)"d:0",
            press_arg,
            ex_arg,
            NULL
        };
        execv(sndcmd, argv);
        _exit(127);
    }

    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR)
            return -1;
    }
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
        return -1;
    return 0;
}

static int release_held_shutter(const char *sndcmd, int dry_run) {
    int rc = 0;
    int s2_rel = 0;
    int s1_rel = 0;
    if (g_s2_down) {
        if (send_key_event(sndcmd, dry_run, A7R4A_KEY_S2, 0, 0u) != 0)
            rc = -1;
        g_s2_down = 0;
        s2_rel = 1;
    }
    if (g_s1_down) {
        if (send_key_event(sndcmd, dry_run, A7R4A_KEY_S1, 0, 0u) != 0)
            rc = -1;
        g_s1_down = 0;
        s1_rel = 1;
    }
    if (g_stop_sig)
        fprintf(stderr, "REMOTE_ABORT sig=%d s2_released=%d s1_released=%d\n",
                (int)g_stop_sig, s2_rel, s1_rel);
    return rc;
}

static int trigger_shutter_once(const char *sndcmd, int dry_run, long s1_hold_ms, long bulb_ms) {
    long s2_hold_ms = (bulb_ms > 0) ? bulb_ms : 150;

    if (send_key_event(sndcmd, dry_run, A7R4A_KEY_S1, 1, 0u) != 0)
        return -1;
    g_s1_down = 1;

    if (sleep_interruptible_ms(s1_hold_ms, dry_run) != 0) {
        release_held_shutter(sndcmd, dry_run);
        return -1;
    }

    if (send_key_event(sndcmd, dry_run, A7R4A_KEY_S2, 1, 0u) != 0) {
        release_held_shutter(sndcmd, dry_run);
        return -1;
    }
    g_s2_down = 1;

    int wait_rc = sleep_interruptible_ms(s2_hold_ms, dry_run);
    if (release_held_shutter(sndcmd, dry_run) != 0)
        return -1;
    if (wait_rc != 0)
        return -1;
    return 0;
}

static int parse_long_range(const char *s, long lo, long hi, long *out) {
    if (!s || s[0] == '\0')
        return -1;
    errno = 0;
    char *end = NULL;
    long v = strtol(s, &end, 10);
    if (errno != 0 || !end || *end != '\0' || v < lo || v > hi)
        return -1;
    *out = v;
    return 0;
}

static void print_usage(void) {
    fprintf(stderr,
            "Usage:\n"
            "  a7r4a-remote-shoot shutter [--bulb-ms N] [--s1-hold-ms M] [--dry-run]\n"
            "  a7r4a-remote-shoot interval --count N --interval-ms M [--bulb-ms B] [--s1-hold-ms H] [--dry-run]\n"
            "  a7r4a-remote-shoot focus-bracket --count N --step {-7..-1|1..7} [--settle-ms S] [--s1-hold-ms H] [--bulb-ms B] [--dry-run]\n");
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc < 2) {
        print_usage();
        return 2;
    }
    if (install_signal_handlers() != 0) {
        fprintf(stderr, "REMOTE_FAIL stage=signals\n");
        return 1;
    }

    const char *mode = argv[1];
    const char *sndcmd = resolve_sndcmd_path();
    long count = -1;
    long interval_ms = -1;
    long bulb_ms = 0;
    long s1_hold_ms = 100;
    long step = 0;
    long settle_ms = 300;
    int step_set = 0;
    int dry_run = 0;

    for (int i = 2; i < argc; ++i) {
        if (strcmp(argv[i], "--dry-run") == 0) {
            dry_run = 1;
        } else if (strcmp(argv[i], "--bulb-ms") == 0 && i + 1 < argc) {
            if (parse_long_range(argv[++i], 0, 3600000, &bulb_ms) != 0) {
                fprintf(stderr, "REMOTE_FAIL stage=arg_bulb_ms\n");
                return 2;
            }
        } else if (strcmp(argv[i], "--s1-hold-ms") == 0 && i + 1 < argc) {
            if (parse_long_range(argv[++i], 10, 5000, &s1_hold_ms) != 0) {
                fprintf(stderr, "REMOTE_FAIL stage=arg_s1_hold_ms\n");
                return 2;
            }
        } else if (strcmp(argv[i], "--count") == 0 && i + 1 < argc) {
            if (parse_long_range(argv[++i], 1, 9999, &count) != 0) {
                fprintf(stderr, "REMOTE_FAIL stage=arg_count\n");
                return 2;
            }
        } else if (strcmp(argv[i], "--interval-ms") == 0 && i + 1 < argc) {
            if (parse_long_range(argv[++i], 100, 86400000, &interval_ms) != 0) {
                fprintf(stderr, "REMOTE_FAIL stage=arg_interval_ms\n");
                return 2;
            }
        } else if (strcmp(argv[i], "--step") == 0 && i + 1 < argc) {
            if (parse_long_range(argv[++i], -7, 7, &step) != 0 || step == 0) {
                fprintf(stderr, "REMOTE_FAIL stage=arg_step\n");
                return 2;
            }
            step_set = 1;
        } else if (strcmp(argv[i], "--settle-ms") == 0 && i + 1 < argc) {
            if (parse_long_range(argv[++i], 50, 30000, &settle_ms) != 0) {
                fprintf(stderr, "REMOTE_FAIL stage=arg_settle_ms\n");
                return 2;
            }
        } else {
            fprintf(stderr, "REMOTE_FAIL stage=unknown_arg arg=%s\n", argv[i]);
            return 2;
        }
    }

    if (strcmp(mode, "shutter") == 0) {
        if (count != -1 || interval_ms != -1 || step_set) {
            fprintf(stderr, "REMOTE_FAIL stage=shutter_args\n");
            return 2;
        }
        if (trigger_shutter_once(sndcmd, dry_run, s1_hold_ms, bulb_ms) != 0) {
            if (g_stop_sig)
                return 128 + (int)g_stop_sig;
            fprintf(stderr, "REMOTE_FAIL stage=shutter_exec\n");
            return 3;
        }
        printf("REMOTE_SHUTTER_DONE bulb_ms=%ld s1_hold_ms=%ld dry_run=%d\n",
               bulb_ms, s1_hold_ms, dry_run);
        return 0;
    }

    if (strcmp(mode, "interval") == 0) {
        if (count < 1 || interval_ms < 100 || step_set) {
            fprintf(stderr, "REMOTE_FAIL stage=interval_args\n");
            return 2;
        }
        for (long i = 0; i < count; ++i) {
            uint64_t t0 = 0, t1 = 0;
            if (!dry_run && mono_ms(&t0) != 0) {
                fprintf(stderr, "REMOTE_FAIL stage=clock\n");
                return 3;
            }
            if (trigger_shutter_once(sndcmd, dry_run, s1_hold_ms, bulb_ms) != 0) {
                if (g_stop_sig)
                    return 128 + (int)g_stop_sig;
                fprintf(stderr, "REMOTE_FAIL stage=interval_shot index=%ld\n", i);
                return 3;
            }
            printf("REMOTE_SHOT index=%ld total=%ld\n", i + 1, count);
            if (i + 1 < count) {
                long wait_ms = interval_ms;
                if (!dry_run && mono_ms(&t1) == 0) {
                    uint64_t used = t1 - t0;
                    wait_ms = (used < (uint64_t)interval_ms) ? (long)((uint64_t)interval_ms - used) : 0;
                }
                if (sleep_interruptible_ms(wait_ms, dry_run) != 0)
                    return g_stop_sig ? (128 + (int)g_stop_sig) : 3;
            }
        }
        printf("REMOTE_INTERVAL_DONE count=%ld interval_ms=%ld bulb_ms=%ld dry_run=%d\n",
               count, interval_ms, bulb_ms, dry_run);
        return 0;
    }

    if (strcmp(mode, "focus-bracket") == 0) {
        if (count < 1 || count > 999 || !step_set || interval_ms != -1) {
            fprintf(stderr, "REMOTE_FAIL stage=focus_bracket_args\n");
            return 2;
        }
        uint32_t step_ex = (uint32_t)(int32_t)step;
        for (long i = 0; i < count; ++i) {
            if (trigger_shutter_once(sndcmd, dry_run, s1_hold_ms, bulb_ms) != 0) {
                if (g_stop_sig)
                    return 128 + (int)g_stop_sig;
                fprintf(stderr, "REMOTE_FAIL stage=focus_shot index=%ld\n", i);
                return 3;
            }
            printf("REMOTE_SHOT index=%ld total=%ld\n", i + 1, count);
            if (i + 1 < count) {
                if (send_key_event(sndcmd, dry_run, A7R4A_KEY_FOCUS_DRIVE, 1, step_ex) != 0) {
                    fprintf(stderr, "REMOTE_FAIL stage=focus_drive_press index=%ld\n", i);
                    return 3;
                }
                if (sleep_interruptible_ms(40, dry_run) != 0) {
                    send_key_event(sndcmd, dry_run, A7R4A_KEY_FOCUS_DRIVE, 0, 0u);
                    return g_stop_sig ? (128 + (int)g_stop_sig) : 3;
                }
                if (send_key_event(sndcmd, dry_run, A7R4A_KEY_FOCUS_DRIVE, 0, 0u) != 0) {
                    fprintf(stderr, "REMOTE_FAIL stage=focus_drive_release index=%ld\n", i);
                    return 3;
                }
                if (sleep_interruptible_ms(settle_ms, dry_run) != 0)
                    return g_stop_sig ? (128 + (int)g_stop_sig) : 3;
            }
        }
        printf("REMOTE_FOCUS_BRACKET_DONE count=%ld step=%ld settle_ms=%ld dry_run=%d\n",
               count, step, settle_ms, dry_run);
        return 0;
    }

    print_usage();
    return 2;
}
