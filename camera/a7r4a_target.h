#ifndef A7R4A_TARGET_H
#define A7R4A_TARGET_H

/* Verified offline targets for Sony ILCE-7RM4A (Ver. 1.12, CXD90045).
 * Never reuse A6400 absolute addresses; resolve module bases dynamically from
 * /proc/<pid>/maps at runtime.
 */

#define A7R4A_TARGET_MODEL "ILCE-7RM4A"
#define A7R4A_TARGET_FW    "1.12"
#define A7R4A_PROC_COMM    "im.elf"

#define A7R4A_LIBOBJ_PATH  "/usr/lib/libObj.so"
#define A7R4A_VIEW2_PATH   "/usr/lib/viewUnified2.so"
#define A7R4A_LIBIMM_PATH  "/usr/lib/libInfraMediaMount.so"
#define A7R4A_LIBDL_PATH   "/lib/libdl-2.11.2.so"

/* File size and 32-bit FNV-1a pins from pulled ILCE-7RM4A Ver. 1.12 dumps. */
#define A7R4A_LIBOBJ_BYTES 21170860u
#define A7R4A_LIBOBJ_FNV   0xbbb8d6e4u
#define A7R4A_VIEW2_BYTES  12216472u
#define A7R4A_VIEW2_FNV    0xb580c34eu
#define A7R4A_LIBIMM_BYTES 36416u
#define A7R4A_LIBIMM_FNV   0x9958109du
#define A7R4A_LIBDL_BYTES  13976u
#define A7R4A_LIBDL_FNV    0xafcc7332u

/* Trampoline hook inside libObj.so (WidgetSystem::onMouseRelease). */
#define A7R4A_HOOK_RVA         0x00625ee8u
#define A7R4A_HOOK_RESUME_RVA  0x00625ef5u

/* Unused zero-filled r-xp page tail inside libInfraMediaMount.so (PT_LOAD[2]
 * starts cleanly on the next 0x8000-aligned page) and anonymous post-memsz .bss
 * page tail inside viewUnified2.so. */
#define A7R4A_CODE_RVA         0x0000795cu
#define A7R4A_CODE_MAX         1700u
#define A7R4A_STATE_RVA        0x00c3691cu
#define A7R4A_STATE_ZERO_BYTES 1764u
#define A7R4A_STATE_SIZE       1184u
#define A7R4A_TRACE_OFFSET     0x50u

/* libdl-2.11.2.so Thumb entry RVAs. */
#define A7R4A_DLOPEN_RVA       0x00000bb1u
#define A7R4A_DLSYM_RVA        0x00000c49u

/* libObj.so .bss singleton pointers and Widget vtable RVAs. */
#define A7R4A_SYSTEM_PTR_RVA   0x0143baf4u
#define A7R4A_INFRA_PTR_RVA    0x014714fcu
#define A7R4A_WIDGET_VPTR_RVA  0x0138fb40u

/* libObj.so Thumb C++ function RVAs (18 UI methods + probe/input helpers). */
#define A7R4A_RVA_WIDGET_CTOR           0x00621749u
#define A7R4A_RVA_PANEL_CTOR            0x00164f95u
#define A7R4A_RVA_LABEL_CTOR            0x00116a2du
#define A7R4A_RVA_WIDGET_SET_LAYER_ID   0x006216a9u
#define A7R4A_RVA_WIDGET_SET_LOCAL_POS  0x006215f9u
#define A7R4A_RVA_PANEL_SET_SIZE        0x001653d7u
#define A7R4A_RVA_LABEL_SET_SIZE        0x0011706du
#define A7R4A_RVA_LABEL_SET_TEXT        0x001165bbu
#define A7R4A_RVA_LABEL_SET_FONT        0x0011669bu
#define A7R4A_RVA_PANEL_SET_COLOR       0x00164ecdu
#define A7R4A_RVA_LAYER_SET_ACTIVE      0x006232cbu
#define A7R4A_RVA_LAYER_SET_CLEAR       0x006232f5u
#define A7R4A_RVA_WIDGET_SET_ANIMATE    0x0063aaf1u
#define A7R4A_RVA_LAYER_MOVE_LAST       0x006354d1u
#define A7R4A_RVA_ID_GEN_ID             0x0064b011u
#define A7R4A_RVA_CREATE_LAYER          0x006250e5u
#define A7R4A_RVA_GET_MOUSE_POS         0x006251b5u
#define A7R4A_RVA_GET_MOUSE_STATE       0x006251e1u

#define A7R4A_RVA_SYS_GET_INSTANCE      0x00624ecdu
#define A7R4A_RVA_ON_ANIMATE            0x0063a793u
#define A7R4A_RVA_UX_GFXENG             0x0061eed5u
#define A7R4A_RVA_FB_WIDTH              0x0062019du
#define A7R4A_RVA_FB_HEIGHT             0x006201a9u
#define A7R4A_RVA_INFRA_GET_INSTANCE    0x008cec31u
#define A7R4A_RVA_INFRA_GET_PHY_KEY     0x008ce32du

/* InfraInput in-process RAM key/event tables (zero IPC / zero semaphore). */
#define A7R4A_INFRA_PHYTAB_OFF          0x18u
#define A7R4A_INFRA_PHYCNT_OFF          0x1cu
#define A7R4A_INFRA_PHYST_OFF           0x24u
#define A7R4A_INFRA_NODES_OFF           0x9cu
#define A7R4A_PHYTAB_STRIDE             48u
#define A7R4A_PHYTAB_ID_OFF             8u
#define A7R4A_PHYTAB_FLAGS_OFF          32u
#define A7R4A_PHYTAB_QID_OFF            36u
#define A7R4A_PHYST_STRIDE              40u
#define A7R4A_PHYST_STATE_OFF           8u
#define A7R4A_NODE_NEXT_OFF             0u
#define A7R4A_NODE_CBID_OFF             20u
#define A7R4A_NODE_FLAG_OFF             36u
#define A7R4A_NODE_CNT_OFF              40u
#define A7R4A_NODE_RECS_OFF             44u
#define A7R4A_REC_STRIDE                68u
#define A7R4A_REC_PHY_OFF               0u
#define A7R4A_REC_EV_OFF                4u
#define A7R4A_REC_STATE_OFF             20u
#define A7R4A_REC_PHYIDX_OFF            36u

/* Verified ILCE-7RM4A physical key IDs (ViewConfig::getConfigInputTable). */
#define A7R4A_PHY_MENU                  0x1ab
#define A7R4A_PHY_C1                    0x66
#define A7R4A_PHY_C2                    0x70e7
#define A7R4A_PHY_C3                    0x70ec
#define A7R4A_PHY_ENTER                 0x1ac
#define A7R4A_PHY_MOVIE                 0x1f0
#define A7R4A_PHY_LOCK                  0x1f1
#define A7R4A_PHY_PLAY                  0x1f7
#define A7R4A_PHY_DELETE                0x1f8
#define A7R4A_PHY_DISP                  0x1f9
#define A7R4A_PHY_FN                    0x7095
#define A7R4A_PHY_AEL                   0x7090
#define A7R4A_PHY_AFMF                  0x7091
#define A7R4A_PHY_AFON                  0x0f000004
#define A7R4A_PHY_CAP                   0xa7
#define A7R4A_PHY_FOCUS_HOLD            0x70f4
#define A7R4A_PHY_DPAD_UP               0x3
#define A7R4A_PHY_DPAD_LEFT             0x15
#define A7R4A_PHY_DPAD_DOWN             0x30
#define A7R4A_PHY_DPAD_RIGHT            0x1af
#define A7R4A_PHY_DPAD_5WAY             0x70c3
#define A7R4A_PHY_JOYSTICK              0x70f5
#define A7R4A_PHY_KEY_F7                0x70f7
#define A7R4A_PHY_KEY_F8                0x70f8

/* .req header and runtime patch-table constants. */
#define A7R4A_REQ_MAGIC                 0x4d544852u /* "MTHR" */
#define A7R4A_REQ_VERSION               2u
#define A7R4A_TRACE_MAGIC               0x4d544c47u /* "MTLG" */
#define A7R4A_PAYLOAD_TAIL_MAGIC        0x41375234u /* "A7R4" */

#endif
