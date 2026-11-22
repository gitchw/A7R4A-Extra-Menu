/* Bounded ptrace capture barrier for target CPU threads, not for DMA. */
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/syscall.h>
#ifdef __linux__
#include <sched.h>
#endif
static pid_t freeze_tids[512];
static unsigned freeze_count,freeze_attached;
/* Successful detach is recorded independently: a partial resume must not
 * detach successful entries twice or discard failed entries on a new freeze. */
static unsigned char freeze_owned[512],freeze_stopped[512];
static unsigned char freeze_stop_signals[512];
static unsigned long long freeze_begin;
#ifdef __linux__
static int freeze_prev_policy=-1;
static struct sched_param freeze_prev_param;
static void freeze_boost_rt(void) {
    freeze_prev_policy=sched_getscheduler(0);
    if(freeze_prev_policy>=0)(void)sched_getparam(0,&freeze_prev_param);
    struct sched_param sp={.sched_priority=50};
    if(sched_setscheduler(0,SCHED_FIFO,&sp)<0)freeze_prev_policy=-1;
}
static void freeze_restore_rt(void) {
    if(freeze_prev_policy>=0) {
        (void)sched_setscheduler(0,freeze_prev_policy,&freeze_prev_param);
        freeze_prev_policy=-1;
    }
}
#else
static void freeze_boost_rt(void) {}
static void freeze_restore_rt(void) {}
#endif
static unsigned long long freeze_ms(void) {
    struct timespec t;if(syscall(SYS_clock_gettime,CLOCK_MONOTONIC,&t))return 0;
    return (unsigned long long)t.tv_sec*1000+t.tv_nsec/1000000;
}
static int freeze_enumerate_pid(pid_t pid,pid_t *out,unsigned *count) {
    char path[64];
    int nlen=snprintf(path,sizeof(path),"/proc/%ld/task",(long)pid);
    if(pid<=0 || nlen<=0 || (unsigned)nlen>=sizeof(path))return -90;
    DIR *dir=opendir(path);struct dirent *entry;unsigned n=0;
    if(!dir)return -90;
    while((entry=readdir(dir))) {
        char *end;long id=strtol(entry->d_name,&end,10);
        if(!*entry->d_name || *end || id<=0)continue;
        if(n>=512){closedir(dir);return -91;}out[n++]=(pid_t)id;
    }
    closedir(dir);*count=n;return 0;
}
/* Wait for PTRACE_ATTACH's SIGSTOP; forward unrelated signals via PTRACE_CONT
 * so SIGSTOP cannot remain pending after PTRACE_DETACH and freeze im.elf. */
static int freeze_wait_sigstop(pid_t tid,unsigned char *stop_sig,unsigned char *owned,
                               unsigned long long timeout_ms) {
    unsigned long long start=freeze_ms();
    int status;
    if(!start)return -1;
    for(;;) {
        pid_t got=waitpid(tid,&status,__WALL|WNOHANG);
        if(got==tid && WIFSTOPPED(status)) {
            int sig=WSTOPSIG(status);
            if(sig==SIGSTOP) {
                *stop_sig=(unsigned char)sig;
                return 0;
            }
            if(ptrace(PTRACE_CONT,tid,0,(void *)(unsigned long)sig))return -1;
            continue;
        }
        if(got==tid && (WIFEXITED(status)||WIFSIGNALED(status))) {
            *owned=0;
            return -1;
        }
        if(got<0 && errno!=EINTR)return -1;
        if(freeze_ms()-start>timeout_ms)return -1;
        usleep(1000);
    }
}
static int freeze_resume(void) {
    int result=0;unsigned resumed=0;
    for(unsigned i=freeze_attached;i;i--) {
        unsigned n=i-1;
        if(!freeze_owned[n])continue;
        if(!freeze_stopped[n]) {
            if(!freeze_wait_sigstop(freeze_tids[n],&freeze_stop_signals[n],&freeze_owned[n],200))
                freeze_stopped[n]=1;
            else if(!freeze_owned[n])continue;
            else {result=-92;continue;}
        }
        if(ptrace(PTRACE_DETACH,freeze_tids[n],0,0))result=-92;
        else {freeze_owned[n]=freeze_stopped[n]=0;resumed++;}
    }
    freeze_restore_rt();
    printf("FREEZE threads=%u attached=%u resumed=%u result=%d elapsed_ms=%llu\n",
           freeze_count,freeze_attached,resumed,result,freeze_ms()-freeze_begin);
    return result;
}
static int freeze_process_pid(pid_t pid) {
    pid_t check[512];unsigned confirmed=0;int result;
    for(unsigned i=0;i<freeze_attached;i++)if(freeze_owned[i])return -100;
    freeze_count=freeze_attached=0;freeze_begin=freeze_ms();
    if(!freeze_begin)return -93;
    result=freeze_enumerate_pid(pid,freeze_tids,&freeze_count);
    if(result || !freeze_count)return result?result:-94;
    /* Run above Sony SCHED_RR threads during attach/detach so woken RT threads
     * cannot preempt PTRACE_DETACH on PREEMPT-RT kernels (rt106+). */
    freeze_boost_rt();
    for(unsigned i=0;i<freeze_count;i++) {
        if(ptrace(PTRACE_ATTACH,freeze_tids[i],0,0)){result=-95;goto fail;}
        freeze_owned[i]=1;freeze_stopped[i]=freeze_stop_signals[i]=0;
        freeze_attached++;
    }
    for(unsigned i=0;i<freeze_count;i++) {
        unsigned long long elapsed=freeze_ms()-freeze_begin;
        if(elapsed>2000 ||
           freeze_wait_sigstop(freeze_tids[i],&freeze_stop_signals[i],&freeze_owned[i],2000-elapsed)) {
            result=-96;goto fail;
        }
        freeze_stopped[i]=1;
    }
    result=freeze_enumerate_pid(pid,check,&confirmed);
    if(result || confirmed!=freeze_count){result=-97;goto fail;}
    for(unsigned i=0;i<confirmed;i++) {
        unsigned j;for(j=0;j<freeze_count;j++)if(check[i]==freeze_tids[j])break;
        if(j==freeze_count){result=-98;goto fail;}
    }
    return 0;
fail:
    if(freeze_resume())return -99;
    return result;
}
static inline int freeze_process(void) {
    return freeze_process_pid(149);
}
/* Rebuild rather than trust an old barrier after partial resume. This remains
 * a target CPU barrier, not owner discovery, VFS or USB/DMA quiescence. */
static inline int freeze_recover_process_pid(pid_t pid) {
    if(freeze_resume())return -99;
    return freeze_process_pid(pid);
}
static inline int freeze_recover_process(void) {
    return freeze_recover_process_pid(149);
}
