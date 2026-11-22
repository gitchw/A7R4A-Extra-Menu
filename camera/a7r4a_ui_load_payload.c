/* Position-independent one-shot loader for ILCE-7RM4A (Ver. 1.12).
 * Guard patches the 24-byte trailing runtime table before PTRACE_POKETEXT;
 * no fixed virtual addresses are compiled into this payload.
 */
#include <stdint.h>
#include "a7r4a_target.h"

typedef uint32_t u32;
typedef uint8_t u8;

struct a7r4a_payload_runtime {
    u32 magic;
    u32 state_va;
    u32 dlopen_va;
    u32 dlsym_va;
    u32 system_ptr_va;
    u32 hook_resume_va;
};

struct event {
    u32 sequence,tid,system,before_xy,mouse_button,after_xy,raw_xy,buttons;
    u32 module_handle,attach_result,native_result,transport_tag;
};
struct trace {
    u32 magic,version,enabled,count,inflight,capacity,error,expected_system;
    struct event events[24];
};

extern u32 mt_original_release(void *,void *) __attribute__((visibility("hidden")));
extern const struct a7r4a_payload_runtime *a7r4a_runtime_ptr(void) __attribute__((visibility("hidden")));
extern const char *a7r4a_module_path_ptr(void) __attribute__((visibility("hidden")));
extern const char *a7r4a_attach_sym_ptr(void) __attribute__((visibility("hidden")));

typedef void *(*open_fn)(const char *,int);
typedef void *(*symbol_fn)(void *,const char *);
typedef int (*attach_fn)(void *,u32);

static u32 current_tid(void) {
    register u32 r0 __asm__("r0")=0;
    register u32 r7 __asm__("r7")=224;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory","cc");
    return r0;
}

u32 ui_release_loader(void *system,void *message) {
    const struct a7r4a_payload_runtime *rt=a7r4a_runtime_ptr();
    if(rt->magic!=A7R4A_PAYLOAD_TAIL_MAGIC || !rt->state_va ||
       !rt->system_ptr_va || !rt->dlopen_va || !rt->dlsym_va)
        return mt_original_release(system,message);
    volatile struct trace *s=(volatile struct trace *)(uintptr_t)rt->state_va;
    if(s->magic!=A7R4A_TRACE_MAGIC || s->version!=1u)
        return mt_original_release(system,message);
    __sync_fetch_and_add(&s->inflight,1);
    u32 expected_sys=*(const volatile u32 *)(uintptr_t)rt->system_ptr_va;
    u32 mouse=4,before=0,tag=0;
    if(expected_sys && (u32)(uintptr_t)system==expected_sys && message) {
        mouse=((const u8 *)message)[20];
        tag=*(const u32 *)((const u8 *)message+16);
        if(mouse<4) before=*(const u32 *)((const u8 *)system+mouse*20+0x4a8);
    }
    u32 result=mt_original_release(system,message);
    if(mouse<4 && expected_sys && (u32)(uintptr_t)system==expected_sys &&
       __sync_bool_compare_and_swap(&s->enabled,1,0)) {
        volatile struct event *e=&s->events[0];
        e->tid=current_tid();
        e->system=(u32)(uintptr_t)system;
        e->before_xy=before;
        e->mouse_button=mouse;
        e->transport_tag=tag;
        e->native_result=result;
        const u8 *p=(const u8 *)system+mouse*20;
        e->after_xy=*(const u32 *)(p+0x4a8);
        e->raw_xy=*(const u32 *)(p+0x4ac);
        e->buttons=*(const u32 *)(p+0x4b0);
        open_fn load=(open_fn)(uintptr_t)rt->dlopen_va;
        symbol_fn symbol=(symbol_fn)(uintptr_t)rt->dlsym_va;
        void *module=load(a7r4a_module_path_ptr(),2);
        e->module_handle=(u32)(uintptr_t)module;
        if(!module) s->error=2;
        else {
            attach_fn attach=(attach_fn)symbol(module,a7r4a_attach_sym_ptr());
            if(!attach) s->error=3;
            else {
                int attached=attach(system,e->tid);
                e->attach_result=(u32)attached;
                if(attached) s->error=4;
            }
        }
        __sync_synchronize();
        e->sequence=1;
        s->count=1;
    }
    __sync_fetch_and_sub(&s->inflight,1);
    return result;
}
