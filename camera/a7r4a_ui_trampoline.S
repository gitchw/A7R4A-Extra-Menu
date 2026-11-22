#ifndef A7R4A_UI_MODULE_PATH
#define A7R4A_UI_MODULE_PATH "/tmp_bt/a7r4a-ui-probe.so"
#endif

.syntax unified
.thumb

.section .text.entry,"ax",%progbits
.balign 4
.global ui_trampoline_entry
.thumb_func
ui_trampoline_entry:
    b.w ui_release_loader

.section .a7r4a_tail,"ax",%progbits
.balign 4
.global mt_original_release
.hidden mt_original_release
.thumb_func
mt_original_release:
    push.w {r4,r5,r6,r7,r8,r9,r10,lr}
    sub sp, #0x20
    mov.w r8, #0x14
    movs r2, #1
    ldr.w pc, a7r4a_hook_resume

.balign 4
.global a7r4a_runtime_ptr
.hidden a7r4a_runtime_ptr
.thumb_func
a7r4a_runtime_ptr:
    adr.w r0, a7r4a_runtime_table
    bx lr

.balign 4
.global a7r4a_module_path_ptr
.hidden a7r4a_module_path_ptr
.thumb_func
a7r4a_module_path_ptr:
    adr.w r0, a7r4a_module_path_str
    bx lr

.balign 4
.global a7r4a_attach_sym_ptr
.hidden a7r4a_attach_sym_ptr
.thumb_func
a7r4a_attach_sym_ptr:
    adr.w r0, a7r4a_attach_sym_str
    bx lr

a7r4a_module_path_str:
    .asciz A7R4A_UI_MODULE_PATH
a7r4a_attach_sym_str:
    .asciz "a7r4a_ui_attach_v1"

.balign 4
a7r4a_runtime_table:
    .word 0x41375234 /* magic "A7R4" */
    .word 0          /* state_va */
    .word 0          /* dlopen_va */
    .word 0          /* dlsym_va */
    .word 0          /* system_ptr_va */
a7r4a_hook_resume:
    .word 0          /* hook_resume_va */

.section .note.GNU-stack,"",%progbits
