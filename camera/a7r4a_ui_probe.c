/* Read-only, non-persistent on-device UI/input probe for ILCE-7RM4A (1.12).
 * Invoked once on natural UI release by a7r4a_ui_load_payload.c.
 * Verifies all libObj.so RVAs, queries live GraphicsEngine framebuffer size
 * (640x480 vs 1024x768), Widget vptr, mouse state, and InfraInput key states,
 * and logs strictly to /tmp_bt/a7r4a-ui-probe.log (zero SD/UI/hook mutations).
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "a7r4a_target.h"

typedef void *(*get_instance_fn)(void);
typedef void (*get_position_fn)(const void *, unsigned char, short *, short *);
typedef int (*get_button_fn)(const void *, unsigned char, unsigned char);
typedef void *(*gfxeng_fn)(void);
typedef uint16_t (*dim_fn)(const void *);
typedef int (*phy_key_fn)(void *, int, void *);
typedef void (*ctor_fn)(void *);

static uintptr_t find_libobj_base(void) {
    FILE *f = fopen("/proc/self/maps", "r");
    if (!f) return 0;
    char line[512];
    uintptr_t base = 0;
    unsigned count = 0;
    while (fgets(line, sizeof(line), f)) {
        unsigned lo = 0, hi = 0, offset = 0;
        char perms[5] = {0};
        int at = 0;
        if (sscanf(line, "%x-%x %4s %x %*s %*s %n", &lo, &hi, perms, &offset, &at) != 4 || at <= 0)
            continue;
        size_t len = strcspn(line + at, "\r\n");
        if (!offset && !strcmp(perms, "r-xp") &&
            len == strlen(A7R4A_LIBOBJ_PATH) && !memcmp(line + at, A7R4A_LIBOBJ_PATH, len)) {
            base = (uintptr_t)lo;
            count++;
        }
    }
    fclose(f);
    return count == 1u ? base : 0;
}

static int check_sym(FILE *log, void *scope, uintptr_t base, const char *name, uint32_t expected_rva, void **out) {
    void *sym = dlsym(scope, name);
    uintptr_t want = base + expected_rva;
    int ok = ((uintptr_t)sym == want);
    fprintf(log, "SYM %s got=%p want=%p match=%d\n", name, sym, (void *)want, ok);
    if (out) *out = sym;
    return ok;
}

static int is_watch_phy(int phy) {
    static const int watch[] = {
        A7R4A_PHY_MENU, A7R4A_PHY_C1, A7R4A_PHY_C2, A7R4A_PHY_C3,
        A7R4A_PHY_ENTER, A7R4A_PHY_MOVIE, A7R4A_PHY_LOCK, A7R4A_PHY_PLAY,
        A7R4A_PHY_DELETE, A7R4A_PHY_DISP, A7R4A_PHY_FN, A7R4A_PHY_AEL,
        A7R4A_PHY_AFMF, A7R4A_PHY_AFON, A7R4A_PHY_CAP, A7R4A_PHY_FOCUS_HOLD,
        A7R4A_PHY_DPAD_UP, A7R4A_PHY_DPAD_LEFT, A7R4A_PHY_DPAD_DOWN, A7R4A_PHY_DPAD_RIGHT,
        A7R4A_PHY_DPAD_5WAY, A7R4A_PHY_JOYSTICK, A7R4A_PHY_KEY_F7, A7R4A_PHY_KEY_F8,
        0x10000013, 0x10000016, 0x10000017, 0x1000001b
    };
    for (unsigned i = 0; i < sizeof(watch) / sizeof(*watch); i++)
        if (watch[i] == phy) return 1;
    return 0;
}

static int a7r4a_phy_state_probe(const unsigned char *infra, int phy) {
    if (!infra) return -1;
    const unsigned char *n = NULL;
    memcpy(&n, infra + A7R4A_INFRA_NODES_OFF, sizeof(n));
    int seen = 0, st = 0;
    for (unsigned h = 0; n && h < 64u; h++) {
        if ((uintptr_t)n < 0x10000u || ((uintptr_t)n & 3u)) return -1;
        int cnt = 0;
        const unsigned char *r = NULL;
        memcpy(&cnt, n + A7R4A_NODE_CNT_OFF, sizeof(cnt));
        memcpy(&r, n + A7R4A_NODE_RECS_OFF, sizeof(r));
        if (cnt > 0 && cnt <= 256 && (uintptr_t)r >= 0x10000u && !((uintptr_t)r & 3u)) {
            for (int i = 0; i < cnt; i++, r += A7R4A_REC_STRIDE) {
                int rphy = 0;
                memcpy(&rphy, r + A7R4A_REC_PHY_OFF, sizeof(rphy));
                if (rphy == phy) {
                    int s = 0;
                    memcpy(&s, r + A7R4A_REC_STATE_OFF, sizeof(s));
                    seen = 1;
                    if (s > 0 && s < 0x7ffffffd) st = s;
                }
            }
        }
        const unsigned char *next = NULL;
        memcpy(&next, n + A7R4A_NODE_NEXT_OFF, sizeof(next));
        n = next;
    }
    return seen ? st : -1;
}

int a7r4a_ui_attach_v1(void *system, unsigned tid) {
    FILE *log = fopen("/tmp_bt/a7r4a-ui-probe.log", "a");
    if (!log) return -1;

    long actual_tid = syscall(SYS_gettid);
    uintptr_t base = find_libobj_base();
    fprintf(log, "A7R4A_PROBE_BEGIN tid=%u actual=%ld system=%p libObj_base=%p\n",
            tid, actual_tid, system, (void *)base);

    if (!base || tid != (unsigned)actual_tid) {
        fprintf(log, "A7R4A_PROBE_BAD_CONTEXT\n");
        fclose(log);
        return -2;
    }

    void *expected_system = *(void *const *)(base + A7R4A_SYSTEM_PTR_RVA);
    if (!system || system != expected_system) {
        fprintf(log, "A7R4A_PROBE_SYSTEM_MISMATCH got=%p expected=%p\n", system, expected_system);
        fclose(log);
        return -3;
    }

    void *obj_handle = dlopen(A7R4A_LIBOBJ_PATH, RTLD_LAZY | RTLD_NOLOAD);
    void *sym_scope = obj_handle ? obj_handle : RTLD_DEFAULT;
    fprintf(log, "A7R4A_PROBE_LIBOBJ_DLOPEN noload=%p\n", obj_handle);

    int all_ok = 1;
    void *fn_sys = NULL, *fn_pos = NULL, *fn_btn = NULL;
    void *fn_gfx = NULL, *fn_fbw = NULL, *fn_fbh = NULL;
    void *fn_infra_inst = NULL, *fn_infra_key = NULL, *fn_wgt_ctor = NULL;
    void *fn_on_anim = NULL;

    all_ok &= check_sym(log, sym_scope, base, "_ZN2ux6wgtsys12WidgetSystem15sys_getInstanceEv", A7R4A_RVA_SYS_GET_INSTANCE, &fn_sys);
    all_ok &= check_sym(log, sym_scope, base, "_ZNK2ux6wgtsys12WidgetSystem24getMousePosInFramebufferEhRsS2_", A7R4A_RVA_GET_MOUSE_POS, &fn_pos);
    all_ok &= check_sym(log, sym_scope, base, "_ZNK2ux6wgtsys12WidgetSystem13getMouseStateEhh", A7R4A_RVA_GET_MOUSE_STATE, &fn_btn);
    all_ok &= check_sym(log, sym_scope, base, "_ZN8UXEngine6gfxengEv", A7R4A_RVA_UX_GFXENG, &fn_gfx);
    all_ok &= check_sym(log, sym_scope, base, "_ZNK2ux6gfxeng14GraphicsEngine19getFramebufferWidthEv", A7R4A_RVA_FB_WIDTH, &fn_fbw);
    all_ok &= check_sym(log, sym_scope, base, "_ZNK2ux6gfxeng14GraphicsEngine20getFramebufferHeightEv", A7R4A_RVA_FB_HEIGHT, &fn_fbh);
    all_ok &= check_sym(log, sym_scope, base, "_ZN10InfraInput11GetInstanceEv", A7R4A_RVA_INFRA_GET_INSTANCE, &fn_infra_inst);
    all_ok &= check_sym(log, sym_scope, base, "_ZN10InfraInput15GetPhyKeyStatusEiP21_InfraInputPhyKeyData", A7R4A_RVA_INFRA_GET_PHY_KEY, &fn_infra_key);
    all_ok &= check_sym(log, sym_scope, base, "_ZN2ux6wgtsys6WidgetC1Ev", A7R4A_RVA_WIDGET_CTOR, &fn_wgt_ctor);
    all_ok &= check_sym(log, sym_scope, base, "_ZN2ux6wgtsys10WidgetBase9onAnimateEv", A7R4A_RVA_ON_ANIMATE, &fn_on_anim);

    static const struct { const char *name; uint32_t rva; } ui_syms[] = {
        {"_ZN9GEN_PanelC1Ev", A7R4A_RVA_PANEL_CTOR},
        {"_ZN9GEN_LabelC1Ev", A7R4A_RVA_LABEL_CTOR},
        {"_ZN2ux6wgtsys6Widget10setLayerIDERKNS_4core2IDE", A7R4A_RVA_WIDGET_SET_LAYER_ID},
        {"_ZN2ux6wgtsys6Widget11setLocalPosERKNS_4core7Vector2E", A7R4A_RVA_WIDGET_SET_LOCAL_POS},
        {"_ZN9GEN_Panel7setSizeEff", A7R4A_RVA_PANEL_SET_SIZE},
        {"_ZN9GEN_Label7setSizeEff", A7R4A_RVA_LABEL_SET_SIZE},
        {"_ZN9GEN_Label13setTextDirectEPKc", A7R4A_RVA_LABEL_SET_TEXT},
        {"_ZN9GEN_Label7setFontEj", A7R4A_RVA_LABEL_SET_FONT},
        {"_ZN9GEN_Panel14setColorDirectERKN6stella6CColorE", A7R4A_RVA_PANEL_SET_COLOR},
        {"_ZN2ux6wgtsys5Layer9setActiveEb", A7R4A_RVA_LAYER_SET_ACTIVE},
        {"_ZN2ux6wgtsys5Layer8setClearEb", A7R4A_RVA_LAYER_SET_CLEAR},
        {"_ZN2ux6wgtsys10WidgetBase10setAnimateEb", A7R4A_RVA_WIDGET_SET_ANIMATE},
        {"_ZN2ux6wgtsys9LayerBase8moveLastEv", A7R4A_RVA_LAYER_MOVE_LAST},
        {"_ZN2ux4core2ID5genIDEPKc", A7R4A_RVA_ID_GEN_ID},
        {"_ZN2ux6wgtsys12WidgetSystem11createLayerERKNS_4core2IDE", A7R4A_RVA_CREATE_LAYER},
    };
    for (unsigned i = 0; i < sizeof(ui_syms) / sizeof(*ui_syms); i++)
        all_ok &= check_sym(log, sym_scope, base, ui_syms[i].name, ui_syms[i].rva, NULL);

    if (!all_ok) {
        fprintf(log, "A7R4A_PROBE_SYM_MISMATCH\n");
        if (obj_handle) dlclose(obj_handle);
        fclose(log);
        return -4;
    }

    const uintptr_t *wgt_vptr = (const uintptr_t *)(base + A7R4A_WIDGET_VPTR_RVA);
    uintptr_t wgt_slot32 = wgt_vptr[32];
    fprintf(log, "A7R4A_WIDGET vptr=%p slot32=%p on_animate=%p match=%d\n",
            (const void *)wgt_vptr, (const void *)wgt_slot32, fn_on_anim,
            wgt_slot32 == (uintptr_t)fn_on_anim);

    void *sys_check = ((get_instance_fn)fn_sys)();
    short mx = -1, my = -1;
    ((get_position_fn)fn_pos)(system, 0, &mx, &my);
    int mstate = ((get_button_fn)fn_btn)(system, 0, 0);

    void *gfx = ((gfxeng_fn)fn_gfx)();
    unsigned fbw = gfx ? ((dim_fn)fn_fbw)(gfx) : 0u;
    unsigned fbh = gfx ? ((dim_fn)fn_fbh)(gfx) : 0u;
    fprintf(log, "A7R4A_GFX sys_ok=%d gfx=%p fb=%ux%u mouse_xy=%d,%d mouse_state=%d\n",
            sys_check == system, gfx, fbw, fbh, mx, my, mstate);

    void *infra = ((get_instance_fn)fn_infra_inst)();
    void *infra_slot = *(void *const *)(base + A7R4A_INFRA_PTR_RVA);
    fprintf(log, "A7R4A_INFRA instance=%p slot=%p match=%d\n",
            infra, infra_slot, infra == infra_slot);
    if (infra) {
        const unsigned char *table = NULL, *physt = NULL;
        uint32_t count = 0;
        memcpy(&table, (const unsigned char *)infra + A7R4A_INFRA_PHYTAB_OFF, sizeof(table));
        memcpy(&count, (const unsigned char *)infra + A7R4A_INFRA_PHYCNT_OFF, sizeof(count));
        memcpy(&physt, (const unsigned char *)infra + A7R4A_INFRA_PHYST_OFF, sizeof(physt));
        uintptr_t taddr = (uintptr_t)table, saddr = (uintptr_t)physt;
        fprintf(log, "A7R4A_KEYTAB ptr=%p count=%u physt=%p\n",
                (const void *)table, (unsigned)count, (const void *)physt);
        if (taddr >= 0x10000u && !(taddr & 3u) && count > 0u && count <= 2048u) {
            int have_physt = (saddr >= 0x10000u && !(saddr & 3u));
            unsigned nonwatch_logged = 0;
            for (unsigned i = 0; i < (unsigned)count; i++) {
                const unsigned char *e = table + i * A7R4A_PHYTAB_STRIDE;
                int kid = 0;
                uint32_t flags = 0, qid = 0, stval = 0x7fffffffu;
                memcpy(&kid, e + A7R4A_PHYTAB_ID_OFF, sizeof(kid));
                memcpy(&flags, e + A7R4A_PHYTAB_FLAGS_OFF, sizeof(flags));
                memcpy(&qid, e + A7R4A_PHYTAB_QID_OFF, sizeof(qid));
                if (have_physt) {
                    memcpy(&stval, physt + i * A7R4A_PHYST_STRIDE + A7R4A_PHYST_STATE_OFF, sizeof(stval));
                }
                if (is_watch_phy(kid)) {
                    fprintf(log, "PHYST idx=%u id=0x%x flags=0x%x qid=0x%x state=0x%x\n",
                            i, (unsigned)kid, (unsigned)flags, (unsigned)qid, (unsigned)stval);
                } else if (have_physt && stval != 0x7fffffffu && nonwatch_logged < 64u) {
                    fprintf(log, "PHYST_ACT idx=%u id=0x%x flags=0x%x qid=0x%x state=0x%x\n",
                            i, (unsigned)kid, (unsigned)flags, (unsigned)qid, (unsigned)stval);
                    nonwatch_logged++;
                }
            }
        }

        const unsigned char *knode = NULL;
        memcpy(&knode, (const unsigned char *)infra + A7R4A_INFRA_NODES_OFF, sizeof(knode));
        unsigned act_logged = 0;
        for (unsigned nidx = 0;
             knode && (uintptr_t)knode >= 0x10000u && !((uintptr_t)knode & 3u) && nidx < 64u;
             nidx++) {
            const unsigned char *next = NULL;
            int cb_id = 0, kcnt = 0;
            unsigned char nflag = 0;
            const unsigned char *recs = NULL;
            memcpy(&next, knode + A7R4A_NODE_NEXT_OFF, sizeof(next));
            memcpy(&cb_id, knode + A7R4A_NODE_CBID_OFF, sizeof(cb_id));
            memcpy(&nflag, knode + A7R4A_NODE_FLAG_OFF, sizeof(nflag));
            memcpy(&kcnt, knode + A7R4A_NODE_CNT_OFF, sizeof(kcnt));
            memcpy(&recs, knode + A7R4A_NODE_RECS_OFF, sizeof(recs));
            fprintf(log, "A7R4A_KEYSET idx=%u node=%p cb_id=%d flag=%u count=%d recs=%p\n",
                    nidx, (const void *)knode, cb_id, (unsigned)nflag, kcnt, (const void *)recs);
            if ((uintptr_t)recs >= 0x10000u && !((uintptr_t)recs & 3u) && kcnt > 0 && kcnt <= 1024) {
                for (int j = 0; j < kcnt; j++) {
                    const unsigned char *rec = recs + (unsigned)j * A7R4A_REC_STRIDE;
                    int rphy = 0, rev = 0, rst = 0, ridx = 0;
                    unsigned char b65 = rec[65];
                    memcpy(&rphy, rec + A7R4A_REC_PHY_OFF, sizeof(rphy));
                    memcpy(&rev, rec + A7R4A_REC_EV_OFF, sizeof(rev));
                    memcpy(&rst, rec + A7R4A_REC_STATE_OFF, sizeof(rst));
                    memcpy(&ridx, rec + A7R4A_REC_PHYIDX_OFF, sizeof(ridx));
                    if (is_watch_phy(rphy)) {
                        fprintf(log, "KEYSET_REC cb_id=%d idx=%d phy=0x%x ev=0x%x state=%d phy_idx=%d b65=%u\n",
                                cb_id, j, (unsigned)rphy, (unsigned)rev, rst, ridx, (unsigned)b65);
                    } else if (rst != 0 && (uint32_t)rst < 0x7ffffffdu && act_logged < 64u) {
                        fprintf(log, "KEYSET_ACT cb_id=%d idx=%d phy=0x%x ev=0x%x state=%d phy_idx=%d b65=%u\n",
                                cb_id, j, (unsigned)rphy, (unsigned)rev, rst, ridx, (unsigned)b65);
                        act_logged++;
                    }
                }
            }
            knode = next;
        }

        static const int probe_watch[] = {
            A7R4A_PHY_MENU, A7R4A_PHY_C1, A7R4A_PHY_C2, A7R4A_PHY_C3,
            A7R4A_PHY_ENTER, A7R4A_PHY_MOVIE, A7R4A_PHY_LOCK, A7R4A_PHY_PLAY,
            A7R4A_PHY_DELETE, A7R4A_PHY_DISP, A7R4A_PHY_FN, A7R4A_PHY_AEL,
            A7R4A_PHY_AFMF, A7R4A_PHY_AFON, A7R4A_PHY_CAP, A7R4A_PHY_FOCUS_HOLD,
            A7R4A_PHY_DPAD_UP, A7R4A_PHY_DPAD_LEFT, A7R4A_PHY_DPAD_DOWN, A7R4A_PHY_DPAD_RIGHT,
            A7R4A_PHY_DPAD_5WAY, A7R4A_PHY_JOYSTICK, A7R4A_PHY_KEY_F7, A7R4A_PHY_KEY_F8
        };
        for (unsigned i = 0; i < sizeof(probe_watch) / sizeof(*probe_watch); i++) {
            int st = a7r4a_phy_state_probe((const unsigned char *)infra, probe_watch[i]);
            fprintf(log, "WATCH phy=0x%x state=%d\n", (unsigned)probe_watch[i], st);
        }
    }

    char path[80], comm[80] = {0};
    snprintf(path, sizeof(path), "/proc/self/task/%u/comm", tid);
    FILE *cf = fopen(path, "r");
    if (cf) {
        if (!fgets(comm, sizeof(comm), cf)) comm[0] = 0;
        fclose(cf);
    }
    fprintf(log, "A7R4A_PROBE_OK thread=%s", comm);
    if (!comm[0]) fputc('\n', log);
    if (obj_handle) dlclose(obj_handle);
    fclose(log);
    return 0;
}
