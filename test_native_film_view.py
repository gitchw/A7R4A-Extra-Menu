from pathlib import Path
import ctypes
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class UiToastV1(ctypes.Structure):
    _fields_ = [
        ('magic', ctypes.c_uint32),
        ('seq', ctypes.c_uint32),
        ('kind', ctypes.c_uint32),
        ('duration_ms', ctypes.c_uint32),
        ('text', ctypes.c_char * 64),
    ]


class UiStatusV1(ctypes.Structure):
    _fields_ = [
        ('magic', ctypes.c_uint32),
        ('seq', ctypes.c_uint32),
        ('applied', ctypes.c_uint32),
        ('scanned', ctypes.c_uint32),
        ('max_files', ctypes.c_uint32),
        ('custom_charo', ctypes.c_uint32),
        ('active_jobs', ctypes.c_uint32),
    ]


class NativeFilmViewTests(unittest.TestCase):
    def test_native_enum_input_render_handoff_and_settings(self):
        self.assertTrue((ROOT/'camera/film_native_view.c').exists(),
                        'Native film view is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory)/'view-tests'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-g', '-O1', '-fsanitize=address,undefined',
                            str(ROOT/'camera/test_film_native_view.c'),
                            str(ROOT/'camera/film_native_view.c'),
                            str(ROOT/'camera/film_menu.c'), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertIn('NATIVE_FILM_VIEW_OK', result.stdout)

    def test_v31_corner_toast_hud_and_1120_abi(self):
        headers = {
            'dlfcn.h': '#define RTLD_DEFAULT ((void *)0)\n#define RTLD_LAZY 1\n#define RTLD_NOW 2\n#define RTLD_NOLOAD 4\nvoid *dlopen(const char *,int);\nvoid *dlsym(void *,const char *);\nint dlclose(void *);\n',
            'fcntl.h': '#define O_RDONLY 0\nint open(const char *,int,...);\n',
            'stdio.h': '#include <stddef.h>\nint snprintf(char *,size_t,const char *,...);\nint sscanf(const char *,const char *,...);\n',
            'stdlib.h': '#include <stddef.h>\nvoid *calloc(size_t,size_t);\n',
            'string.h': '#include <stddef.h>\nvoid *memcpy(void *,const void *,size_t);\nvoid *memset(void *,int,size_t);\nint memcmp(const void *,const void *,size_t);\nint strcmp(const char *,const char *);\nsize_t strlen(const char *);\nchar *strcpy(char *,const char *);\n',
            'sys/syscall.h': '#define SYS_gettid 224\n#define SYS_clock_gettime 263\n',
            'time.h': '#define CLOCK_MONOTONIC 1\nstruct timespec {long tv_sec,tv_nsec;};\n',
            'unistd.h': '#include <stddef.h>\ntypedef long ssize_t;\nlong syscall(long,...);\nssize_t read(int,void *,size_t);\nint close(int);\nint getpid(void);\n',
        }
        model_c = r'''
#include <stdarg.h>
#include "native_film_ui_trial3.c"
unsigned model_now_ms=1000;
int model_layer_is_active=0;
char model_toast_label[64];
char model_footer_label[64];
char model_header_label[80];
int model_toast_x=0,model_toast_y=0,model_toast_w=0,model_toast_h=0;
static unsigned char menu_marker[64],ticker_marker[64],infra_stub[256],trace_stub[4096];
static void *key_slot_stub;
static void model_position(const void *p,unsigned char n,short *x,short *y) {(void)p;(void)n;*x=32767;*y=32767;}
static int model_state(const void *p,unsigned char a,unsigned char b) {(void)p;(void)a;(void)b;return 6;}
static void model_layer(void *p,int active) {(void)p;model_layer_is_active=active;}
static void model_animation(void *p,int active) {(void)p;(void)active;}
static void model_unary(void *p) {(void)p;}
static void model_place(void *p,const float *xy) {
    if(p==ui_toast.bg && xy) { model_toast_x=(int)xy[0]; model_toast_y=(int)xy[1]; }
}
static void model_size(void *p,float w,float h) __attribute__((pcs("aapcs")));
static void model_size(void *p,float w,float h) {
    if(p==ui_toast.bg) { model_toast_w=(int)w; model_toast_h=(int)h; }
}
static void model_color(void *p,const unsigned char *c) {(void)p;(void)c;}
static void model_text(void *p,const char *s) {
    if(p==ui_toast.label && s)snprintf(model_toast_label,sizeof(model_toast_label),"%s",s);
    if(p==ui.labels[7] && s)snprintf(model_footer_label,sizeof(model_footer_label),"%s",s);
    if(p==ui.labels[0] && s)snprintf(model_header_label,sizeof(model_header_label),"%s",s);
}
long syscall(long n, ...) {
    if(n==SYS_gettid)return 380;
    if(n==SYS_clock_gettime) {
        va_list ap;
        va_start(ap, n);
        int clk = va_arg(ap, int);
        struct timespec *t = va_arg(ap, struct timespec *);
        va_end(ap);
        if(clk==CLOCK_MONOTONIC && t) {
            t->tv_sec=(long)(model_now_ms/1000u);
            t->tv_nsec=(long)((model_now_ms%1000u)*1000000u);
            return 0;
        }
    }
    return 0;
}
void model_init(void) {
    memset(&ui,0,sizeof(ui));
    memset(&ui_theme,0,sizeof(ui_theme));
    memset(&ui_toast,0,sizeof(ui_toast));
    memset(infra_stub,0,sizeof(infra_stub));
    infra_stub[0x20]=1;
    struct film_settings initial={0,0,2,1};
    const struct film_native_view_ops ops={visibility,draw,settings};
    film_native_view_init(&ui.view,&ops,0,&initial);
    ui.system=(void *)0x1000;
    ui.keys=infra_stub;
    key_slot_stub=ui.keys;
    g_key_instance_slot=&key_slot_stub;
    g_trace=(volatile struct ui_trial_trace *)trace_stub;
    ui.tid=380;ui.running=ui.attached=1;
    ui.layer=menu_marker;ui.ticker=ui.ticker_layer=ticker_marker;
    ui.background=(void *)0x1001;
    ui_theme.card_border=(void *)0x1002;
    ui_theme.card_panel=(void *)0x1003;
    ui_theme.header_accent=(void *)0x1004;
    ui_theme.header_divider=(void *)0x1005;
    ui_theme.page_panel=(void *)0x1006;
    ui_theme.page_label=(void *)0x1007;
    for(unsigned i=0;i<6;i++) {
        ui.panels[i]=(void *)(uintptr_t)(0x1100+i);
        ui_theme.row_accents[i]=(void *)(uintptr_t)(0x1200+i);
    }
    for(unsigned i=0;i<8;i++) ui.labels[i]=(void *)(uintptr_t)(0x1300+i);
    strcpy(ui.texts[7],"C1+C2 or MENU+C1");
    ui.mouse_position=model_position;ui.mouse_state=model_state;
    ui.layer_active=model_layer;ui.set_animate=model_animation;ui.move_last=model_unary;
    ui.set_position=model_place;ui.panel_size=ui.label_size=model_size;
    ui.set_color=model_color;ui.set_text=model_text;
    ui_toast.bg=(void *)0x2001;
    ui_toast.accent=(void *)0x2002;
    ui_toast.label=(void *)0x2003;
    model_now_ms=1000;
    model_layer_is_active=0;
    model_toast_label[0]=0;
    model_footer_label[0]=0;
}
void model_tick(unsigned dt) {
    model_now_ms += dt;
    animate(ui.ticker);
}
void model_open_menu(void) {
    ui.view.menu.visible=1;
    draw(0,&ui.view.menu);
    visibility(0,1);
}
void model_close_menu(void) {
    film_native_view_hide(&ui.view);
}
int model_layer_active(void) { return model_layer_is_active; }
int model_toast_visible(void) { return ui_toast.visible; }
const char *model_toast_text(void) { return model_toast_label; }
const char *model_footer_text(void) { return model_footer_label; }
const char *model_header_text(void) { return model_header_label; }
void model_toggle_hires(void) {
    a6400_ui_hires_v1 ^= 1u;
    draw(0, &ui.view.menu);
}
int model_toast_geom(int idx) {
    return idx==0?model_toast_x:(idx==1?model_toast_y:(idx==2?model_toast_w:model_toast_h));
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            inc = d / 'inc'
            (inc / 'sys').mkdir(parents=True)
            for name, content in headers.items():
                (inc / name).write_text(content, encoding='utf-8')
            for name in ('a7r4a_target.h', 'film_menu.h', 'film_native_view.h', 'ui_trial_trace.h'):
                (inc / name).write_text((ROOT / 'camera' / name).read_text(encoding='utf-8'), encoding='utf-8')
            src = (ROOT / 'camera/native_film_ui_trial3.c').read_text(encoding='utf-8')
            body = model_c.replace('#include "native_film_ui_trial3.c"', src)
            preamble = '#define _Static_assert(cond, msg)\n'
            (d / 'model.c').write_text(preamble + body, encoding='utf-8')
            so_file = d / 'toast_model.so'
            subprocess.run([
                'cc', '-std=c11', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                '-Wno-unknown-attributes', '-Wno-ignored-attributes', '-Wno-unused-function',
                '-DA6400_UI_TRIAL_MS=0', '-DA6400_UI_DISPLAY_MS=20000',
                '-I', str(inc), '-I', str(d),
                str(d / 'model.c'),
                str(ROOT / 'camera/film_native_view.c'),
                str(ROOT / 'camera/film_menu.c'),
                '-o', str(so_file),
            ], check=True)
            lib = ctypes.CDLL(str(so_file))
            lib.model_init()
            lib.model_toast_text.restype = ctypes.c_char_p
            lib.model_footer_text.restype = ctypes.c_char_p
            lib.model_header_text.restype = ctypes.c_char_p

            toast = UiToastV1.in_dll(lib, 'a6400_ui_toast_v1')
            status = UiStatusV1.in_dll(lib, 'a6400_ui_status_v1')
            hires = ctypes.c_uint32.in_dll(lib, 'a6400_ui_hires_v1')
            self.assertEqual(toast.magic, 0x54534F54)
            self.assertEqual(ctypes.sizeof(toast), 80)
            self.assertEqual(status.magic, 0x53544156)
            self.assertEqual(status.max_files, 1024)
            self.assertEqual(hires.value, 1)
            self.assertEqual(lib.model_toast_visible(), 0)

            toast.kind = 1
            toast.duration_ms = 3500
            toast.text = b'Processing: portra_400...'
            toast.seq = 1
            lib.model_tick(20)
            self.assertEqual(lib.model_toast_visible(), 1)
            self.assertEqual(lib.model_layer_active(), 1)
            self.assertEqual(lib.model_toast_text().decode('ascii'), 'Processing: portra_400...')
            self.assertEqual([lib.model_toast_geom(i) for i in range(4)], [24, 390, 480, 36])

            lib.model_tick(3600)
            self.assertEqual(lib.model_toast_visible(), 0)
            self.assertEqual(lib.model_layer_active(), 0)

            # Opening the menu hides toast and shows the V3.9 quality toggle + status header.
            status.applied = 3
            status.scanned = 12
            status.seq = 1
            lib.model_open_menu()
            self.assertEqual(lib.model_toast_visible(), 0)
            self.assertEqual(lib.model_layer_active(), 1)
            self.assertEqual(lib.model_footer_text().decode('ascii'), 'Quality: Full 61MP')
            self.assertEqual(lib.model_header_text().decode('ascii'), 'V3.9 | OFF | Done:3 (12/1024)')
            lib.model_toggle_hires()
            self.assertEqual(hires.value, 0)
            self.assertEqual(lib.model_footer_text().decode('ascii'), 'Quality: Fast 4MP')

            # While menu is open, incoming toast stays deferred until menu closes.
            toast.kind = 2
            toast.duration_ms = 4000
            toast.text = b'OK Saved SD: F0000001.JPG'
            toast.seq = 2
            lib.model_tick(20)
            self.assertEqual(lib.model_toast_visible(), 0)

            lib.model_close_menu()
            lib.model_tick(20)
            self.assertEqual(lib.model_toast_visible(), 1)
            self.assertEqual(lib.model_layer_active(), 1)
            self.assertEqual(lib.model_toast_text().decode('ascii'), 'OK Saved SD: F0000001.JPG')

            # Explicit empty text clears an active toast immediately.
            toast.text = b''
            toast.seq = 3
            lib.model_tick(20)
            self.assertEqual(lib.model_toast_visible(), 0)
            self.assertEqual(lib.model_layer_active(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
