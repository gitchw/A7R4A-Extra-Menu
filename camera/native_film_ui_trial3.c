/* Exact-version RAM trial for ILCE-7RM4A (Ver. 1.12). Invoked on the natural
 * Appframework thread. All drawing uses owned Sony widgets/layer. No framebuffer
 * writes, input consumption, firmware/configuration patch or independent SD mount.
 */
#define _GNU_SOURCE
#include "a7r4a_target.h"
#include "film_native_view.h"
#include <dlfcn.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include "ui_trial_trace.h"
#ifdef A6400_INPUT_DIAGNOSTIC
#include "ui_input_journal.h"
static volatile struct ui_input_journal a6400_ui_input_journal_v1;
#endif

#if defined(A6400_UI_TOUCH_CLOSE_DIAGNOSTIC) && !defined(A6400_UI_RENDER_DIAGNOSTIC)
#error "touch-close diagnostic requires the bounded render diagnostic"
#endif

#if defined(A6400_UI_COMBO_DIAGNOSTIC) && defined(A6400_UI_RENDER_DIAGNOSTIC)
#error "combo and automatic-render diagnostics are mutually exclusive"
#endif

#if defined(A6400_UI_SETTINGS_DIAGNOSTIC) && \
    (A6400_UI_SETTINGS_DIAGNOSTIC != 1 || !defined(A6400_UI_COMBO_DIAGNOSTIC))
#error "settings diagnostic requires the combo diagnostic"
#endif

#ifndef A6400_UI_TRIAL_MS
# ifdef A6400_UI_COMBO_DIAGNOSTIC
#  define A6400_UI_TRIAL_MS 180000u
# else
#  define A6400_UI_TRIAL_MS 30000u
# endif
#endif
#if A6400_UI_TRIAL_MS != 0 && (A6400_UI_TRIAL_MS < 30000 || A6400_UI_TRIAL_MS > 600000)
#error "A6400_UI_TRIAL_MS must be 0 (persistent) or between 30000 and 600000 milliseconds"
#endif
#define TRIAL_MS A6400_UI_TRIAL_MS
#ifndef A6400_UI_INSTANCE_SUFFIX
#define A6400_UI_INSTANCE_SUFFIX ""
#endif
#ifndef A6400_UI_DISPLAY_MS
#define A6400_UI_DISPLAY_MS 5000u
#endif
#if A6400_UI_DISPLAY_MS < 1 || A6400_UI_DISPLAY_MS > 20000 || (TRIAL_MS != 0 && A6400_UI_DISPLAY_MS >= TRIAL_MS)
#error "A6400_UI_DISPLAY_MS must be between 1 and 20000 milliseconds"
#endif

typedef void (*ctor_fn)(void *);
typedef void (*unary_fn)(void *);
typedef void (*bool_fn)(void *,int);
typedef void (*layer_id_fn)(void *,const uint32_t *);
/* The firmware is base AAPCS soft-float. Compiler/system headers are hard-float:
 * annotate the only native calls with float arguments explicitly. */
typedef void (*size_fn)(void *,float,float) __attribute__((pcs("aapcs")));
typedef void (*position_fn)(void *,const float *);
typedef void (*text_fn)(void *,const char *);
typedef void (*color_fn)(void *,const unsigned char *);
typedef uint32_t (*id_fn)(const char *);
typedef void *(*create_layer_fn)(void *,const uint32_t *);
typedef void (*mouse_position_fn)(const void *,unsigned char,short *,short *);
typedef int (*mouse_state_fn)(const void *,unsigned char,unsigned char);
typedef void (*font_fn)(void *,uint32_t);
typedef void *(*infra_inst_fn)(void);

/* Kept separate from `ui` to preserve the pinned 1120-byte snapshot ABI. */
static uintptr_t g_libobj_base,g_view2_base;
static uint32_t g_pid;
static volatile struct ui_trial_trace *g_trace;
static void *volatile *g_key_instance_slot;

static struct {
    struct film_native_view view;
    void *system,*layer,*ticker,*background,*panels[6],*labels[8],*keys;
    void *ticker_layer;
    uint32_t ticker_layer_id;
    uint32_t layer_id,tid,started,last_tick,ticks;
    uint32_t opened_ms;
    int ever_opened;
#ifdef A6400_UI_TOUCH_CLOSE_DIAGNOSTIC
    int last_touch;
#endif
    int last_menu,last_zoom,last_other;
    uintptr_t vtable[42];
    int attached,running;
    ctor_fn widget_ctor,panel_ctor,label_ctor;
    layer_id_fn set_layer;
    position_fn set_position;
    size_fn panel_size,label_size;
    text_fn set_text;
    color_fn set_color;
    bool_fn layer_active,layer_clear,set_animate;
    unary_fn move_last;
    id_fn make_id;
    create_layer_fn create_layer;
    mouse_position_fn mouse_position;
    mouse_state_fn mouse_state;
    void *key_reserved;
    font_fn set_font;
    char texts[8][80];
} ui;

#if !defined(A6400_UI_TOUCH_CLOSE_DIAGNOSTIC)
_Static_assert(sizeof(ui)==1120u,"ui snapshot ABI size");
_Static_assert(__builtin_offsetof(__typeof__(ui),system)==0x68u,"ui.system offset");
_Static_assert(__builtin_offsetof(__typeof__(ui),tid)==0xc0u,"ui.tid offset");
_Static_assert(__builtin_offsetof(__typeof__(ui),started)==0xc4u,"ui.started offset");
_Static_assert(__builtin_offsetof(__typeof__(ui),last_tick)==0xc8u,"ui.last_tick offset");
_Static_assert(__builtin_offsetof(__typeof__(ui),attached)==0x18cu,"ui.attached offset");
_Static_assert(__builtin_offsetof(__typeof__(ui),running)==0x190u,"ui.running offset");
#endif

/* Owned control word, separate from the pinned 1120-byte UI snapshot ABI.
 * A matching external manager may write 1; only the natural UI callback
 * stops owned layers and publishes 2. Retained widgets keep this SO loaded. */
volatile unsigned a6400_ui_stop_requested_v1;
__attribute__((visibility("default")))
volatile uint32_t a6400_ui_hires_v1 = 1u;

/* Owned corner-toast ABI, separate from the 1120-byte UI snapshot ABI. */
struct a6400_ui_toast_v1 {
    uint32_t magic;
    uint32_t seq;
    uint32_t kind;
    uint32_t duration_ms;
    char text[64];
};
__attribute__((visibility("default")))
volatile struct a6400_ui_toast_v1 a6400_ui_toast_v1 = { 0x54534f54u, 0u, 0u, 0u, "" };

struct a6400_ui_status_v1 {
    uint32_t magic;       /* 0x53544156u 'STAV' */
    uint32_t seq;
    uint32_t applied;
    uint32_t scanned;
    uint32_t max_files;
    uint32_t custom_charo;
    uint32_t active_jobs;
};
__attribute__((visibility("default")))
volatile struct a6400_ui_status_v1 a6400_ui_status_v1 = { 0x53544156u, 0u, 0u, 0u, 1024u, 0u, 0u };

static struct {
    void *bg,*accent,*label;
    uint32_t seq,until_ms;
    int visible;
    char text[64];
} ui_toast;

/* Decorative theme panels kept outside `ui` so the 1120-byte snapshot ABI stays exact. */
static struct {
    void *card_border,*card_panel,*header_accent,*header_divider;
    void *row_accents[6];
    void *page_panel,*page_label;
    uint32_t status_seq;
} ui_theme;

static int resolve_self_bases(void) {
    int fd=open("/proc/self/maps",O_RDONLY);
    if(fd<0)return -1;
    char buf[4096],line[512];
    unsigned line_used=0;
    uintptr_t libobj=0,view2=0;
    unsigned libobj_n=0,view2_n=0;
    int overflow=0;
    ssize_t n;
    while((n=read(fd,buf,sizeof(buf)))>0) {
        for(ssize_t i=0;i<n;i++) {
            char c=buf[i];
            if(c=='\n') {
                line[line_used]=0;
                if(!overflow) {
                    unsigned lo=0,hi=0,offset=0;char perms[5]={0};int at=0;
                    if(sscanf(line,"%x-%x %4s %x %*s %*s %n",&lo,&hi,perms,&offset,&at)==4 && at>0) {
                        size_t len=strlen(line+at);
                        if(len && line[at+len-1]=='\r')--len;
                        if(!offset && !strcmp(perms,"r-xp")) {
                            if(len==strlen(A7R4A_LIBOBJ_PATH) && !memcmp(line+at,A7R4A_LIBOBJ_PATH,len)) {
                                libobj=(uintptr_t)lo;++libobj_n;
                            } else if(len==strlen(A7R4A_VIEW2_PATH) && !memcmp(line+at,A7R4A_VIEW2_PATH,len)) {
                                view2=(uintptr_t)lo;++view2_n;
                            }
                        }
                    }
                }
                line_used=0;overflow=0;
            } else if(line_used+1<sizeof(line)) {
                line[line_used++]=c;
            } else {
                overflow=1;
            }
        }
    }
    close(fd);
    if(n<0 || libobj_n!=1u || view2_n!=1u || !libobj || !view2)return -1;
    g_libobj_base=libobj;
    g_view2_base=view2;
    g_pid=(uint32_t)getpid();
    g_trace=(volatile struct ui_trial_trace *)(view2+A7R4A_STATE_RVA+A7R4A_TRACE_OFFSET);
    g_key_instance_slot=(void *volatile *)(libobj+A7R4A_INFRA_PTR_RVA);
    return 0;
}

static uint32_t now_ms(void) {
    struct timespec t;
    if(syscall(SYS_clock_gettime,CLOCK_MONOTONIC,&t))return 0;
    return (uint32_t)((uint64_t)t.tv_sec*1000u+(unsigned)t.tv_nsec/1000000u);
}
static void input_snapshot(unsigned stage) {
#ifdef A6400_INPUT_DIAGNOSTIC
    void *owned[UI_INPUT_OWNED]={0};
    owned[0]=ui.background;
    for(unsigned i=0;i<6;i++)owned[i+1]=ui.panels[i];
    for(unsigned i=0;i<8;i++)owned[i+7]=ui.labels[i];
    owned[15]=ui.ticker;
    if(ui.layer)owned[16]=*(void **)((unsigned char *)ui.layer+0x38);
    if(ui.ticker_layer)owned[17]=*(void **)((unsigned char *)ui.ticker_layer+0x38);
    ui_input_capture(&a6400_ui_input_journal_v1,stage,ui.system,owned,
        UI_INPUT_OWNED,now_ms(),(uint32_t)syscall(SYS_gettid));
#else
    (void)stage;
#endif
}
static void record(const char *event,int a,int b) {
    if(!g_trace)return;
    ui_trial_record_pid(g_trace,event,now_ms(),g_pid,(uint32_t)syscall(SYS_gettid),
                        (uint32_t)a,(uint32_t)b,ui.ticks,
                        (unsigned)ui.running | ((unsigned)ui.view.menu.visible<<1));
}
static void *api(void *scope,const char *name,uint32_t offset) {
    void *p=dlsym(scope,name);
    if((uintptr_t)p!=g_libobj_base+offset){record("API_MISMATCH",(int)offset,(int)(uintptr_t)p);return 0;}
    return p;
}
static __attribute__((noinline)) int resolve(infra_inst_fn *infra_inst) {
    void *obj=dlopen(A7R4A_LIBOBJ_PATH,RTLD_LAZY|RTLD_NOLOAD);
    void *scope=obj?obj:RTLD_DEFAULT;
#define API(member,type,name,offset) do {ui.member=(type)api(scope,name,offset);if(!ui.member){if(obj)dlclose(obj);return -1;}}while(0)
    API(widget_ctor,ctor_fn,"_ZN2ux6wgtsys6WidgetC1Ev",A7R4A_RVA_WIDGET_CTOR);
    API(panel_ctor,ctor_fn,"_ZN9GEN_PanelC1Ev",A7R4A_RVA_PANEL_CTOR);
    API(label_ctor,ctor_fn,"_ZN9GEN_LabelC1Ev",A7R4A_RVA_LABEL_CTOR);
    API(set_layer,layer_id_fn,"_ZN2ux6wgtsys6Widget10setLayerIDERKNS_4core2IDE",A7R4A_RVA_WIDGET_SET_LAYER_ID);
    API(set_position,position_fn,"_ZN2ux6wgtsys6Widget11setLocalPosERKNS_4core7Vector2E",A7R4A_RVA_WIDGET_SET_LOCAL_POS);
    API(panel_size,size_fn,"_ZN9GEN_Panel7setSizeEff",A7R4A_RVA_PANEL_SET_SIZE);
    API(label_size,size_fn,"_ZN9GEN_Label7setSizeEff",A7R4A_RVA_LABEL_SET_SIZE);
    API(set_text,text_fn,"_ZN9GEN_Label13setTextDirectEPKc",A7R4A_RVA_LABEL_SET_TEXT);
    API(set_font,font_fn,"_ZN9GEN_Label7setFontEj",A7R4A_RVA_LABEL_SET_FONT);
    API(set_color,color_fn,"_ZN9GEN_Panel14setColorDirectERKN6stella6CColorE",A7R4A_RVA_PANEL_SET_COLOR);
    API(layer_active,bool_fn,"_ZN2ux6wgtsys5Layer9setActiveEb",A7R4A_RVA_LAYER_SET_ACTIVE);
    API(layer_clear,bool_fn,"_ZN2ux6wgtsys5Layer8setClearEb",A7R4A_RVA_LAYER_SET_CLEAR);
    API(set_animate,bool_fn,"_ZN2ux6wgtsys10WidgetBase10setAnimateEb",A7R4A_RVA_WIDGET_SET_ANIMATE);
    API(move_last,unary_fn,"_ZN2ux6wgtsys9LayerBase8moveLastEv",A7R4A_RVA_LAYER_MOVE_LAST);
    API(make_id,id_fn,"_ZN2ux4core2ID5genIDEPKc",A7R4A_RVA_ID_GEN_ID);
    API(create_layer,create_layer_fn,"_ZN2ux6wgtsys12WidgetSystem11createLayerERKNS_4core2IDE",A7R4A_RVA_CREATE_LAYER);
    API(mouse_position,mouse_position_fn,"_ZNK2ux6wgtsys12WidgetSystem24getMousePosInFramebufferEhRsS2_",A7R4A_RVA_GET_MOUSE_POS);
    API(mouse_state,mouse_state_fn,"_ZNK2ux6wgtsys12WidgetSystem13getMouseStateEhh",A7R4A_RVA_GET_MOUSE_STATE);
#undef API
    *infra_inst=(infra_inst_fn)api(scope,"_ZN10InfraInput11GetInstanceEv",A7R4A_RVA_INFRA_GET_INSTANCE);
    if(obj)dlclose(obj);
    return *infra_inst?0:-1;
}
/* Read live key/event state directly from InfraInput's RAM tables without
 * IPC or semaphores. Prefers static [infra+0x24] PHYST (via unsigned binary
 * search on [infra+0x18]), falling back to [infra+0x9c] KEYSET_REC nodes.
 * Maps >=0x7ffffffc sentinels to 0; returns -1 only if phy is completely unknown. */
static int a7r4a_phy_state(const unsigned char *infra,int phy) {
    if(!infra)return -1;
    if(*(const unsigned char *)(infra+0x20u))return 0;
    int in_phytab=0;
    const unsigned char *tab=*(const unsigned char *const *)(infra+A7R4A_INFRA_PHYTAB_OFF);
    uint32_t cnt=*(const uint32_t *)(infra+A7R4A_INFRA_PHYCNT_OFF);
    const unsigned char *physt=*(const unsigned char *const *)(infra+A7R4A_INFRA_PHYST_OFF);
    if((uintptr_t)tab>=0x10000u && !((uintptr_t)tab&3u) && cnt>0u && cnt<=2048u) {
        uint32_t target=(uint32_t)phy,lo=0,hi=cnt-1u;
        while(lo<=hi) {
            uint32_t mid=lo+((hi-lo)>>1);
            uint32_t kid=*(const uint32_t *)(tab+mid*A7R4A_PHYTAB_STRIDE+A7R4A_PHYTAB_ID_OFF);
            if(kid==target) {
                in_phytab=1;
                if((uintptr_t)physt>=0x10000u && !((uintptr_t)physt&3u)) {
                    int ps=*(const int *)(physt+mid*A7R4A_PHYST_STRIDE+A7R4A_PHYST_STATE_OFF);
                    if((uint32_t)ps<0x7ffffffcu)return ps;
                }
                break;
            }
            if(kid<target)lo=mid+1u;
            else {
                if(mid==0u)break;
                hi=mid-1u;
            }
        }
    }
    const unsigned char *n=*(const unsigned char *const *)(infra+A7R4A_INFRA_NODES_OFF);
    int seen=0,st=0;
    for(unsigned h=0;n && h<64u;h++) {
        if((uintptr_t)n<0x10000u || ((uintptr_t)n&3u))return in_phytab?0:-1;
        int ncnt=*(const int *)(n+A7R4A_NODE_CNT_OFF);
        const unsigned char *r=*(const unsigned char *const *)(n+A7R4A_NODE_RECS_OFF);
        if(ncnt>0 && ncnt<=256 && (uintptr_t)r>=0x10000u && !((uintptr_t)r&3u)) {
            for(int i=0;i<ncnt;i++,r+=A7R4A_REC_STRIDE) {
                if(*(const int *)(r+A7R4A_REC_PHY_OFF)==phy) {
                    int s=*(const int *)(r+A7R4A_REC_STATE_OFF);
                    seen=1;
                    if(s==1)st=1;
                    else if(!st && s>0 && (uint32_t)s<0x7ffffffcu)st=s;
                }
            }
        }
        n=*(const unsigned char *const *)(n+A7R4A_NODE_NEXT_OFF);
    }
    return (seen || in_phytab)?st:-1;
}
static void place(void *widget,int label,int x,int y,int w,int h) {
    const float xy[2]={(float)x,(float)y};
    ui.set_position(widget,xy);
    (label?ui.label_size:ui.panel_size)(widget,(float)w,(float)h);
}
static void show_menu_chrome(void) {
    static const unsigned char background[4]={8,10,14,148};
    static const unsigned char card_border[4]={84,92,106,224};
    static const unsigned char card_panel[4]={18,22,29,230};
    static const unsigned char header_accent[4]={234,106,28,255};
    static const unsigned char header_divider[4]={58,66,78,200};
    place(ui.background,0,0,0,640,480);ui.set_color(ui.background,background);
    place(ui_theme.card_border,0,54,34,532,412);ui.set_color(ui_theme.card_border,card_border);
    place(ui_theme.card_panel,0,56,36,528,408);ui.set_color(ui_theme.card_panel,card_panel);
    place(ui_theme.header_accent,0,56,36,528,4);ui.set_color(ui_theme.header_accent,header_accent);
    place(ui_theme.header_divider,0,76,86,488,1);ui.set_color(ui_theme.header_divider,header_divider);
}
static void hide_menu_widgets(void) {
    static const unsigned char clear_c[4]={0,0,0,0};
    place(ui.background,0,0,0,0,0);ui.set_color(ui.background,clear_c);
    place(ui_theme.card_border,0,0,0,0,0);ui.set_color(ui_theme.card_border,clear_c);
    place(ui_theme.card_panel,0,0,0,0,0);ui.set_color(ui_theme.card_panel,clear_c);
    place(ui_theme.header_accent,0,0,0,0,0);ui.set_color(ui_theme.header_accent,clear_c);
    place(ui_theme.header_divider,0,0,0,0,0);ui.set_color(ui_theme.header_divider,clear_c);
    place(ui_theme.page_panel,0,0,0,0,0);ui.set_color(ui_theme.page_panel,clear_c);
    place(ui_theme.page_label,1,0,0,0,0);ui.set_text(ui_theme.page_label,"");
    for(unsigned i=0;i<6;i++){
        place(ui.panels[i],0,0,0,0,0);ui.set_color(ui.panels[i],clear_c);
        place(ui_theme.row_accents[i],0,0,0,0,0);ui.set_color(ui_theme.row_accents[i],clear_c);
    }
    for(unsigned i=0;i<8;i++){
        if(i<7u)ui.texts[i][0]=0;
        place(ui.labels[i],1,0,0,0,0);
        ui.set_text(ui.labels[i],"");
    }
}
static void hide_toast_widgets(void) {
    static const unsigned char clear_c[4]={0,0,0,0};
    if(!ui_toast.bg)return;
    place(ui_toast.bg,0,0,0,0,0);ui.set_color(ui_toast.bg,clear_c);
    place(ui_toast.accent,0,0,0,0,0);ui.set_color(ui_toast.accent,clear_c);
    ui_toast.text[0]=0;
    place(ui_toast.label,1,0,0,0,0);ui.set_text(ui_toast.label,ui_toast.text);
}
static void visibility(void *unused,int active) {
    (void)unused;
    input_snapshot(active?3:5);
    record(active?"SHOW_BEFORE_MOVE":"HIDE_BEFORE_ACTIVE",active,0);
    if(active){ui.move_last(ui.layer);record("SHOW_AFTER_MOVE",0,0);}
    ui.layer_active(ui.layer,active);
    input_snapshot(active?4:6);
    if(active){ui.opened_ms=now_ms();ui.ever_opened=1;}
#if defined(A6400_UI_COMBO_DIAGNOSTIC) || (TRIAL_MS == 0 && !defined(A6400_UI_RENDER_DIAGNOSTIC))
    else ui.opened_ms=0;
#endif
    record(active?"FILM_OPEN":"FILM_CLOSE",ui.view.menu.settings.enabled,ui.view.menu.settings.stock);
}
static void draw(void *unused,const struct film_menu *menu) {
    (void)unused;
    record("DRAW_BEGIN",menu->page,menu->selected);
    hide_toast_widgets();
    ui_toast.visible=0;
    show_menu_chrome();
    place(ui.labels[0],1,76,48,488,34);
    const unsigned char row_plain[4]={30,36,46,226};
    const unsigned char row_active[4]={58,44,24,236};
    const unsigned char row_pressed[4]={234,114,36,245};
    const unsigned char pill_plain[4]={42,49,62,232};
    const unsigned char pill_pressed[4]={92,104,122,248};
    const unsigned char accent_orange[4]={234,140,42,255};
    const unsigned char accent_gold[4]={212,162,58,220};
    const unsigned char accent_muted[4]={68,76,90,200};
    if(a6400_ui_status_v1.magic==0x53544156u && a6400_ui_status_v1.seq>0u) {
        ui_theme.status_seq=a6400_ui_status_v1.seq;
        unsigned qmax=a6400_ui_status_v1.max_files?(unsigned)a6400_ui_status_v1.max_files:1024u;
        if(a6400_ui_status_v1.active_jobs>0u) {
            snprintf(ui.texts[0],sizeof(ui.texts[0]),"V3.9 | %s | Jobs:%u Done:%u",
                     menu->settings.enabled?"ON":"OFF",
                     (unsigned)a6400_ui_status_v1.active_jobs,(unsigned)a6400_ui_status_v1.applied);
        } else {
            snprintf(ui.texts[0],sizeof(ui.texts[0]),"V3.9 | %s | Done:%u (%u/%u)",
                     menu->settings.enabled?"ON":"OFF",
                     (unsigned)a6400_ui_status_v1.applied,(unsigned)a6400_ui_status_v1.scanned,qmax);
        }
    } else {
        snprintf(ui.texts[0],sizeof(ui.texts[0]),"A7R4A V3.9 | LUT %s",
                 menu->settings.enabled?"ON":"OFF");
    }
    ui.set_text(ui.labels[0],ui.texts[0]);
    int is_stocks=(menu->page==FILM_MENU_STOCKS || menu->page==FILM_MENU_STOCKS_2 || menu->page==FILM_MENU_STOCKS_3);
    int details_page=(menu->page==FILM_MENU_DETAILS);
    int base=menu->page==FILM_MENU_STOCKS?0:(menu->page==FILM_MENU_STOCKS_2?5:10);
    for(unsigned row=0;row<6;row++) {
        int x=0,y=0,w=0,h=0;
        int used=film_native_view_row(menu,row,ui.texts[row+1],sizeof(ui.texts[0]),&x,&y,&w,&h);
        if(used<=0){ui.texts[row+1][0]=0;w=h=0;}
        record("ROW_PANEL_GEOMETRY",row,used);
        place(ui.panels[row],0,x,y,w,h);
        int is_dismiss=(menu->page==FILM_MENU_HOME && row==2) || (is_stocks && row==5) || (details_page && row==5);
        int is_active=(menu->page==FILM_MENU_HOME && row==0 && menu->settings.enabled) ||
                      (menu->page==FILM_MENU_HOME && row==4 && (menu->settings.grain!=2 || !menu->settings.halation)) ||
                      (details_page && ((row==0 && menu->settings.grain>0) || (row==1 && menu->settings.halation))) ||
                      (is_stocks && used>0 && !is_dismiss && base+(int)row==menu->settings.stock);
        const unsigned char *panel_color=row_plain;
        if(menu->selected==(int)row)panel_color=is_dismiss?pill_pressed:row_pressed;
        else if(is_dismiss)panel_color=pill_plain;
        else if(is_active)panel_color=row_active;
        record("ROW_PANEL_COLOR",row,0);
        ui.set_color(ui.panels[row],panel_color);
        if(used>0 && !is_dismiss) {
            place(ui_theme.row_accents[row],0,x,y,5,h);
            const unsigned char *stripe=is_active?accent_orange:
                (menu->page==FILM_MENU_HOME && (row==1 || row==4)?accent_gold:accent_muted);
            ui.set_color(ui_theme.row_accents[row],stripe);
        } else {
            place(ui_theme.row_accents[row],0,0,0,0,0);
        }
        record("ROW_LABEL_GEOMETRY",row,0);
        if(used<=0)place(ui.labels[row+1],1,0,0,0,0);
        else if(is_dismiss)place(ui.labels[row+1],1,x+20,y+6,w>40?w-40:0,h>12?h-12:0);
        else place(ui.labels[row+1],1,x+22,y+4,w>38?w-38:0,h>8?h-8:0);
        record("ROW_TEXT_BEFORE",row,0);
        ui.set_text(ui.labels[row+1],ui.texts[row+1]);
        record("ROW_TEXT_AFTER",row,0);
    }
    if(is_stocks) {
        char page_buf[32]={0};
        int px=0,py=0,pw=0,ph=0;
        int pused=film_native_view_row(menu,6,page_buf,sizeof(page_buf),&px,&py,&pw,&ph);
        place(ui.labels[7],1,0,0,0,0);
        ui.set_text(ui.labels[7],"");
        if(pused>0) {
            place(ui_theme.page_panel,0,px,py,pw,ph);
            ui.set_color(ui_theme.page_panel,menu->selected==6?pill_pressed:pill_plain);
            place(ui_theme.page_label,1,px+16,py+6,pw>32?pw-32:0,ph>12?ph-12:0);
            ui.set_text(ui_theme.page_label,page_buf);
        } else {
            place(ui_theme.page_panel,0,0,0,0,0);
            place(ui_theme.page_label,1,0,0,0,0);
            ui.set_text(ui_theme.page_label,"");
        }
    } else if(menu->page==FILM_MENU_HOME) {
        place(ui_theme.page_panel,0,76,386,264,42);
        ui.set_color(ui_theme.page_panel,
                     menu->selected==7?pill_pressed:(a6400_ui_hires_v1?row_active:pill_plain));
        place(ui_theme.page_label,1,0,0,0,0);
        ui.set_text(ui_theme.page_label,"");
        snprintf(ui.texts[7],sizeof(ui.texts[0]),"%s",
                 a6400_ui_hires_v1?"Quality: Full 61MP":"Quality: Fast 4MP");
        place(ui.labels[7],1,92,392,232,30);
        ui.set_text(ui.labels[7],ui.texts[7]);
    } else if(details_page) {
        place(ui_theme.page_panel,0,76,386,264,42);
        ui.set_color(ui_theme.page_panel,pill_plain);
        place(ui_theme.page_label,1,0,0,0,0);
        ui.set_text(ui_theme.page_label,"");
        unsigned qmax=(a6400_ui_status_v1.magic==0x53544156u && a6400_ui_status_v1.max_files)
                      ?(unsigned)a6400_ui_status_v1.max_files:1024u;
        unsigned qcur=a6400_ui_status_v1.magic==0x53544156u?(unsigned)a6400_ui_status_v1.scanned:0u;
        snprintf(ui.texts[7],sizeof(ui.texts[0]),"Queue:%u/%u | %s",
                 qcur,qmax,a6400_ui_hires_v1?"61MP":"4MP");
        place(ui.labels[7],1,92,392,232,30);
        ui.set_text(ui.labels[7],ui.texts[7]);
    } else {
        place(ui_theme.page_panel,0,0,0,0,0);
        place(ui_theme.page_label,1,0,0,0,0);
        ui.set_text(ui_theme.page_label,"");
        ui.texts[7][0]=0;
        place(ui.labels[7],1,0,0,0,0);
        ui.set_text(ui.labels[7],ui.texts[7]);
    }
    record("FILM_DRAW",menu->page,menu->selected);
}
static void settings(void *unused,const struct film_settings *s) {
    (void)unused;
    /* The persistent reader samples ui.view.menu.settings directly. Keep the
     * callback native and allocation-free; photo work stays in the daemon. */
    record("FILM_SETTINGS_SNAPSHOT",s->enabled,s->stock | (s->grain<<8) | (s->halation<<16));
}
static void stop_trial(const char *why) {
    record("STOP_BEGIN",0,0);
    film_native_view_hide(&ui.view);
    record("STOP_MENU_HIDDEN",0,0);
    hide_toast_widgets();
    if(ui.layer)ui.layer_active(ui.layer,0);
    ui_toast.visible=0;
    ui.running=0;
    input_snapshot(7);
    record("STOP_TICKER_BEFORE",0,0);
    if(ui.ticker)ui.set_animate(ui.ticker,0);
    record("STOP_LAYER_BEFORE",0,0);
    if(ui.ticker_layer)ui.layer_active(ui.ticker_layer,0);
    input_snapshot(8);
    record(why,(int)ui.ticks,0);
}
int a6400_ui_stop_v2(void) {
    if(!ui.attached || (uint32_t)syscall(SYS_gettid)!=ui.tid)return -1;
    stop_trial("FILM_EXPLICIT_STOP");return 0;
}
static void animate(void *self) {
    if(!ui.running)return;
    if(self!=ui.ticker || (uint32_t)syscall(SYS_gettid)!=ui.tid) {
        ui.running=0;record("FILM_BAD_TICK_CONTEXT",0,0);return;
    }
    if(a6400_ui_stop_requested_v1==1u) {
        stop_trial("FILM_STOP_REQUESTED");
        __sync_synchronize();a6400_ui_stop_requested_v1=2u;return;
    }
    uint32_t now=now_ms();
    if(!now){stop_trial("FILM_CLOCK_ERROR");return;}
    if(!ui.started) {ui.started=now;record("FILM_TICK_READY",(int)(uintptr_t)self,0);}
    ++ui.ticks;
#if TRIAL_MS != 0
    if(now-ui.started>=TRIAL_MS) {
        stop_trial("FILM_TRIAL_STOPPED");return;
    }
#endif
    if(ui.opened_ms && now-ui.opened_ms>=A6400_UI_DISPLAY_MS) {
#if defined(A6400_UI_COMBO_DIAGNOSTIC) || (TRIAL_MS == 0 && !defined(A6400_UI_RENDER_DIAGNOSTIC))
        film_native_view_hide(&ui.view);
        record("COMBO_AUTO_HIDE",0,0);return;
#else
        stop_trial("TRIAL_AUTO_HIDE");return;
#endif
    }
#if !defined(A6400_UI_COMBO_DIAGNOSTIC) && !(TRIAL_MS == 0 && !defined(A6400_UI_RENDER_DIAGNOSTIC))
    if(ui.ever_opened && !ui.view.menu.visible) {
        stop_trial("TRIAL_CLOSE_DONE");return;
    }
#endif
    if(now-ui.last_tick<15u)return;
    ui.last_tick=now;
    if(ui_toast.bg && ui.layer) {
        if(ui.view.menu.visible) {
            ui_toast.visible=0;
        } else {
            uint32_t seq=a6400_ui_toast_v1.seq;
            if(seq!=ui_toast.seq && a6400_ui_toast_v1.magic==0x54534f54u) {
                for(unsigned i=0;i<63;i++)ui_toast.text[i]=a6400_ui_toast_v1.text[i];
                ui_toast.text[63]=0;
                uint32_t kind=a6400_ui_toast_v1.kind;
                uint32_t dur=a6400_ui_toast_v1.duration_ms;
                ui_toast.seq=seq;
                if(ui_toast.text[0]) {
                    static const unsigned char bg_c[4]={18,22,29,235};
                    static const unsigned char k1_c[4]={234,158,28,255};
                    static const unsigned char k2_c[4]={46,196,96,255};
                    static const unsigned char k3_c[4]={220,62,62,255};
                    hide_menu_widgets();
                    place(ui_toast.bg,0,24,390,480,36);
                    place(ui_toast.accent,0,24,390,8,36);
                    place(ui_toast.label,1,40,394,456,28);
                    ui.set_color(ui_toast.bg,bg_c);
                    ui.set_color(ui_toast.accent,kind==2?k2_c:kind==3?k3_c:k1_c);
                    ui.set_text(ui_toast.label,ui_toast.text);
                    ui.move_last(ui.layer);
                    ui.layer_active(ui.layer,1);
                    ui_toast.visible=1;
                    ui_toast.until_ms=now+(dur?dur:3000u);
                } else if(ui_toast.visible) {
                    hide_toast_widgets();
                    ui.layer_active(ui.layer,0);
                    ui_toast.visible=0;
                }
            }
            if(ui_toast.visible && (int32_t)(now-ui_toast.until_ms)>=0) {
                hide_toast_widgets();
                ui.layer_active(ui.layer,0);
                ui_toast.visible=0;
            }
        }
    }
#ifdef A6400_UI_RENDER_DIAGNOSTIC
    if(!ui.ever_opened && now-ui.started>=1000u) {
        record("RENDER_DIAGNOSTIC_BEGIN",(int)(now-ui.started),0);
        ui.view.menu.visible=1;
        draw(0,&ui.view.menu);
        visibility(0,1);
    }
#ifdef A6400_UI_TOUCH_CLOSE_DIAGNOSTIC
    if(ui.view.menu.visible) {
        short x=32767,y=32767;
        ui.mouse_position(ui.system,0,&x,&y);
        int state=ui.mouse_state(ui.system,0,0);
        if(state!=ui.last_touch) {
            record(state==5?"CLOSE_TOUCH_DOWN":state==6?"CLOSE_TOUCH_UP":"CLOSE_TOUCH_INVALID",x,y);
            ui.last_touch=state;
        }
        if(state==5 && film_menu_hit(ui.view.menu.page,x,y)!=2)x=y=32767;
        unsigned change=film_native_view_step(&ui.view,now,0,0,0,state,x,y);
        if(change&FILM_MENU_CLOSED) {
            stop_trial("TRIAL_TOUCH_CLOSE");return;
        }
    }
#endif
    return;
#endif
    if(*g_key_instance_slot!=ui.keys){stop_trial("FILM_KEY_INSTANCE_CHANGED");return;}
    const unsigned char *infra=(const unsigned char *)ui.keys;
    int c1_raw=a7r4a_phy_state(infra,A7R4A_PHY_C1);
    int c2_raw=a7r4a_phy_state(infra,A7R4A_PHY_C2);
    int menu_raw=a7r4a_phy_state(infra,A7R4A_PHY_MENU);
    int c1_on=(c1_raw<0)?-1:(c1_raw==1);
    int c2_on=(c2_raw<0)?-1:(c2_raw==1);
    int menu_on=(menu_raw<0)?-1:(menu_raw==1);
    int keys_ok=(c1_on>=0 && c2_on>=0 && menu_on>=0);
    int menu=keys_ok?c1_on:-1;
    int zoom=keys_ok?(c2_on || menu_on):-1;
    int other=0;
    static const int dismiss_keys[]={
        A7R4A_PHY_ENTER,A7R4A_PHY_MOVIE,A7R4A_PHY_PLAY,A7R4A_PHY_DELETE,
        A7R4A_PHY_DISP,A7R4A_PHY_FN,A7R4A_PHY_AEL,A7R4A_PHY_AFMF,
        A7R4A_PHY_AFON,A7R4A_PHY_C3,A7R4A_PHY_CAP,
        A7R4A_PHY_LOCK,A7R4A_PHY_DPAD_UP,A7R4A_PHY_DPAD_LEFT,
        A7R4A_PHY_DPAD_DOWN,A7R4A_PHY_DPAD_RIGHT
    };
    for(unsigned i=0;i<sizeof(dismiss_keys)/sizeof(*dismiss_keys);i++)
        if(a7r4a_phy_state(infra,dismiss_keys[i])==1)other=1;
    int dpad5=a7r4a_phy_state(infra,A7R4A_PHY_DPAD_5WAY);
    if(dpad5>0 && (dpad5&0x1f)!=0)other=1;
    static const int nonzero_keys[]={
        A7R4A_PHY_JOYSTICK,A7R4A_PHY_KEY_F7,A7R4A_PHY_KEY_F8
    };
    for(unsigned i=0;i<sizeof(nonzero_keys)/sizeof(*nonzero_keys);i++)
        if(a7r4a_phy_state(infra,nonzero_keys[i])>0)other=1;
    short x=32767,y=32767;
    ui.mouse_position(ui.system,0,&x,&y);
    int touch=ui.mouse_state(ui.system,0,0);
    if(ui.view.menu.have_sample && now-ui.view.menu.last_sample>250u &&
       (ui.view.menu.pending || c1_raw>0 || c2_raw>0 || menu_raw>0))
        record("COMBO_GAP",(int)(now-ui.view.menu.last_sample),
            ui.view.menu.released | (ui.view.menu.pending<<1) |
            (ui.view.menu.fired<<2) | ((c1_on==1)<<3) |
            ((c2_on==1)<<4) | ((other!=0)<<5));
    int other_pack=(menu_raw&0xffff) | ((other?1:0)<<16);
    if(c1_raw!=ui.last_menu || c2_raw!=ui.last_zoom || other_pack!=ui.last_other) {
        record("COMBO_KEYS",c1_raw,c2_raw);
        if(menu_raw>0 || other)record("COMBO_OTHER",menu_raw,other);
        ui.last_menu=c1_raw;ui.last_zoom=c2_raw;ui.last_other=other_pack;
    }
#if defined(A6400_UI_COMBO_DIAGNOSTIC) && !defined(A6400_UI_SETTINGS_DIAGNOSTIC)
    if(touch==5 && film_menu_hit(ui.view.menu.page,x,y)!=
       (ui.view.menu.page==FILM_MENU_HOME?2:5))x=y=32767;
#endif
    unsigned change=film_native_view_step(&ui.view,now,menu,zoom,other,touch,x,y);
    if(change&FILM_MENU_SETTINGS_CHANGED)record("FILM_TAP",x,y);
    if(change&FILM_MENU_HIRES_TOGGLED) {
        a6400_ui_hires_v1^=1u;
        draw(0,&ui.view.menu);
        record("FILM_HIRES_TOGGLE",(int)a6400_ui_hires_v1,0);
    }
    if(ui.view.menu.visible && a6400_ui_status_v1.magic==0x53544156u &&
       a6400_ui_status_v1.seq!=ui_theme.status_seq) {
        draw(0,&ui.view.menu);
    }
    if(ui.ticks%300u==0)record("FILM_ALIVE",(int)ui.ticks,ui.view.menu.visible);
}
static void *make(int label) {
    void *p=calloc(1,label?0x400u:0x200u);
    if(p){
        record(label?"LABEL_CTOR_BEFORE":"PANEL_CTOR_BEFORE",0,0);
        (label?ui.label_ctor:ui.panel_ctor)(p);
        if(label){
            static const uint32_t attributes[6]={0x02076000,0x0207a00f,
                0x0207a000,0x02066027,0x02076002,0xffffffff};
            memcpy((unsigned char *)p+0x2b4,attributes,sizeof(attributes));
            record("LABEL_FONT_BEFORE",(int)(uintptr_t)p,0);
            ui.set_font(p,attributes[0]);
            record("LABEL_FONT_AFTER",*(int *)((unsigned char *)p+0x1f4),0);
            if(!*(void **)((unsigned char *)p+0x1f4))return 0;
        }
        record("WIDGET_LAYER_BEFORE",label,0);
        ui.set_layer(p,&ui.layer_id);
        record("WIDGET_LAYER_AFTER",label,0);
    }
    return p;
}
int a7r4a_ui_attach_v1(void *system,unsigned tid) {
    if(ui.attached || tid!=(uint32_t)syscall(SYS_gettid))return -1;
    if(resolve_self_bases())return -2;
    void *expected_system=*(void *const *)(g_libobj_base+A7R4A_SYSTEM_PTR_RVA);
    record("FILM_ATTACH_BEGIN",(int)tid,(int)(uintptr_t)system);
    if(!system || system!=expected_system)return -1;
    infra_inst_fn infra_inst=0;
    if(resolve(&infra_inst))return -2;
    ui.system=system;ui.tid=tid;
    ui.keys=infra_inst();
    if(!ui.keys || ui.keys!=*g_key_instance_slot ||
       a7r4a_phy_state((const unsigned char *)ui.keys,A7R4A_PHY_C1)<0 ||
       a7r4a_phy_state((const unsigned char *)ui.keys,A7R4A_PHY_C2)<0 ||
       a7r4a_phy_state((const unsigned char *)ui.keys,A7R4A_PHY_MENU)<0)return -3;
    input_snapshot(0);
    struct film_settings initial={0,0,2,1};
    const struct film_native_view_ops ops={visibility,draw,settings};
    film_native_view_init(&ui.view,&ops,0,&initial);
#if defined(A6400_UI_COMBO_DIAGNOSTIC)
    ui.layer_id=ui.make_id("A7R4A_FILM_COMBO_V3" A6400_UI_INSTANCE_SUFFIX);
#elif defined(A6400_UI_TOUCH_CLOSE_DIAGNOSTIC)
    ui.layer_id=ui.make_id("A7R4A_FILM_CLOSE_TOUCH_V3");
#elif defined(A6400_INPUT_DIAGNOSTIC) && A6400_INPUT_DIAGNOSTIC == 2
    ui.layer_id=ui.make_id("A7R4A_FILM_INPUT2_V3");
#elif defined(A6400_INPUT_DIAGNOSTIC)
    ui.layer_id=ui.make_id("A7R4A_FILM_INPUT1_V3");
#elif defined(A6400_UI_RENDER_DIAGNOSTIC)
    ui.layer_id=ui.make_id("A7R4A_FILM_NATIVE_FONTFIX3_V3");
#else
    ui.layer_id=ui.make_id("A7R4A_FILM_NATIVE_TRIAL_V3");
#endif
    record("CREATE_LAYER_BEFORE",0,0);
    ui.layer=ui.create_layer(system,&ui.layer_id);
    if(!ui.layer)return -4;
    ui.layer_active(ui.layer,0);ui.layer_clear(ui.layer,1);
    record("FILM_LAYER_CREATED",(int)(uintptr_t)ui.layer,0);
    ui.background=make(0);
    if(!ui.background)return -5;
    place(ui.background,0,0,0,640,480);
    const unsigned char background[4]={8,10,14,148};
    ui.set_color(ui.background,background);
    ui_theme.card_border=make(0);
    ui_theme.card_panel=make(0);
    ui_theme.header_accent=make(0);
    ui_theme.header_divider=make(0);
    if(!ui_theme.card_border || !ui_theme.card_panel ||
       !ui_theme.header_accent || !ui_theme.header_divider)return -5;
    place(ui_theme.card_border,0,54,34,532,412);
    const unsigned char card_border[4]={84,92,106,224};
    ui.set_color(ui_theme.card_border,card_border);
    place(ui_theme.card_panel,0,56,36,528,408);
    const unsigned char card_panel[4]={18,22,29,230};
    ui.set_color(ui_theme.card_panel,card_panel);
    place(ui_theme.header_accent,0,56,36,528,4);
    const unsigned char header_accent[4]={234,106,28,255};
    ui.set_color(ui_theme.header_accent,header_accent);
    place(ui_theme.header_divider,0,76,86,488,1);
    const unsigned char header_divider[4]={58,66,78,200};
    ui.set_color(ui_theme.header_divider,header_divider);
    for(unsigned i=0;i<6;i++){
        ui.panels[i]=make(0);
        ui_theme.row_accents[i]=make(0);
        if(!ui.panels[i] || !ui_theme.row_accents[i])return -5;
    }
    for(unsigned i=0;i<8;i++){ui.labels[i]=make(1);if(!ui.labels[i])return -5;}
    ui_theme.page_panel=make(0);
    ui_theme.page_label=make(1);
    if(!ui_theme.page_panel || !ui_theme.page_label)return -5;
    place(ui_theme.page_panel,0,0,0,0,0);
    place(ui_theme.page_label,1,0,0,0,0);
    ui.set_text(ui_theme.page_label,"");
    ui_toast.bg=make(0);
    ui_toast.accent=make(0);
    ui_toast.label=make(1);
    if(ui_toast.bg && ui_toast.accent && ui_toast.label) {
        hide_toast_widgets();
    } else {
        ui_toast.bg=ui_toast.accent=ui_toast.label=0;
    }
    strcpy(ui.texts[0],"A7R4A V3.9 | LUT OFF");
#ifdef A6400_UI_SETTINGS_DIAGNOSTIC
    strcpy(ui.texts[7],"C1+C2 or MENU+C1 | Tap");
#elif defined(A6400_UI_COMBO_DIAGNOSTIC)
    strcpy(ui.texts[7],"C1 + C2 | Tap Close");
#elif defined(A6400_UI_TOUCH_CLOSE_DIAGNOSTIC)
    strcpy(ui.texts[7],"Tap Close; 20s auto-hide");
#elif defined(A6400_UI_RENDER_DIAGNOSTIC) && A6400_UI_DISPLAY_MS % 1000 == 0
    snprintf(ui.texts[7],sizeof(ui.texts[7]),"%u second display test",A6400_UI_DISPLAY_MS/1000u);
#elif defined(A6400_UI_RENDER_DIAGNOSTIC)
    snprintf(ui.texts[7],sizeof(ui.texts[7]),"%u ms display test",(unsigned)A6400_UI_DISPLAY_MS);
#else
    strcpy(ui.texts[7],"C1+C2 or MENU+C1");
#endif
    record("TITLE_TEXT_BEFORE",0,0);
    place(ui.labels[0],1,76,48,488,34);ui.set_text(ui.labels[0],ui.texts[0]);
    place(ui.labels[7],1,76,394,312,28);ui.set_text(ui.labels[7],ui.texts[7]);
    record("FILM_WIDGETS_CREATED",7,8);
    input_snapshot(1);
    ui.ticker=calloc(1,0x200u);
    if(!ui.ticker)return -5;
    ui.widget_ctor(ui.ticker);
    uintptr_t *original=*(uintptr_t **)ui.ticker;
    if((uintptr_t)original!=g_libobj_base+A7R4A_WIDGET_VPTR_RVA ||
       original[32]!=g_libobj_base+A7R4A_RVA_ON_ANIMATE)return -6;
    memcpy(ui.vtable,original-2,sizeof(ui.vtable));
    ui.vtable[34]=(uintptr_t)animate;
    *(uintptr_t **)ui.ticker=ui.vtable+2;
#if defined(A6400_UI_COMBO_DIAGNOSTIC)
    ui.ticker_layer_id=ui.make_id("A7R4A_FILM_COMBO_TICKER_V3" A6400_UI_INSTANCE_SUFFIX);
#elif defined(A6400_UI_TOUCH_CLOSE_DIAGNOSTIC)
    ui.ticker_layer_id=ui.make_id("A7R4A_FILM_CLOSE_TOUCH_TICKER_V3");
#elif defined(A6400_INPUT_DIAGNOSTIC) && A6400_INPUT_DIAGNOSTIC == 2
    ui.ticker_layer_id=ui.make_id("A7R4A_FILM_INPUT2_TICKER_V3");
#elif defined(A6400_INPUT_DIAGNOSTIC)
    ui.ticker_layer_id=ui.make_id("A7R4A_FILM_INPUT1_TICKER_V3");
#elif defined(A6400_UI_RENDER_DIAGNOSTIC)
    ui.ticker_layer_id=ui.make_id("A7R4A_FILM_NATIVE_FONTFIX3_TICKER_V3");
#else
    ui.ticker_layer_id=ui.make_id("A7R4A_FILM_NATIVE_TICKER_V3");
#endif
    ui.ticker_layer=ui.create_layer(system,&ui.ticker_layer_id);
    if(!ui.ticker_layer)return -4;
    ui.layer_clear(ui.ticker_layer,0);
    ui.set_layer(ui.ticker,&ui.ticker_layer_id);
    ui.layer_active(ui.ticker_layer,1);
    ui.attached=ui.running=1;
    ui.set_animate(ui.ticker,1);
    input_snapshot(2);
    record("FILM_ATTACHED_NATIVE",(int)ui.layer_id,(int)(uintptr_t)ui.ticker);
    return 0;
}
int a6400_ui_attach_v1(void *system,unsigned tid) {
    return a7r4a_ui_attach_v1(system,tid);
}
