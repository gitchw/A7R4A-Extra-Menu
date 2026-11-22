#include "film_native_view.h"
#include <stdio.h>

void film_native_view_init(struct film_native_view *v,const struct film_native_view_ops *ops,
                          void *context,const struct film_settings *settings) {
    *v=(struct film_native_view){0};
    film_menu_init(&v->menu,settings);
    v->ops=*ops;v->context=context;
}
void film_native_view_hide(struct film_native_view *v) {
    if(film_menu_close(&v->menu))v->ops.visibility(v->context,0);
    v->drawn=0;
}
unsigned film_native_view_step(struct film_native_view *v,unsigned now,
                               int menu,int zoom,int other,int touch_state,int x,int y) {
    unsigned change=film_menu_input(&v->menu,now,menu,zoom,other);
    /* Sony's enum is 5=pressed, 6=released, NOT a boolean. Invalid state
     * cancels the gesture by hiding, rather than fabricating a release. */
    if(touch_state!=5 && touch_state!=6) {
        change|=film_menu_close(&v->menu);
    } else change|=film_menu_touch(&v->menu,now,touch_state==5,x,y);
    if(change & FILM_MENU_SETTINGS_CHANGED)v->ops.settings(v->context,&v->menu.settings);
    if(!v->menu.visible) {
        if(change & FILM_MENU_CLOSED)v->ops.visibility(v->context,0);
        v->drawn=0;
        return change;
    }
    if(!v->drawn || (change & FILM_MENU_SETTINGS_CHANGED) ||
       v->drawn_page!=v->menu.page || v->drawn_selected!=v->menu.selected ||
       v->drawn_enabled!=v->menu.settings.enabled || v->drawn_stock!=v->menu.settings.stock) {
        v->ops.draw(v->context,&v->menu);
        v->drawn=1;v->drawn_page=v->menu.page;v->drawn_selected=v->menu.selected;
        v->drawn_enabled=v->menu.settings.enabled;v->drawn_stock=v->menu.settings.stock;
    }
    if(change & FILM_MENU_OPENED)v->ops.visibility(v->context,1);
    return change;
}
static const char *grain_detail_name(int grain) {
    static const char *const names[4]={"OFF (Fast)","Light","Standard","Heavy"};
    return (grain>=0 && grain<4)?names[grain]:"Standard";
}
int film_native_view_row(const struct film_menu *m,unsigned row,char *out,unsigned capacity,
                         int *x,int *y,int *width,int *height) {
    if(!m || row>=7 || !out || !capacity)return -1;
    unsigned count=m->page==FILM_MENU_HOME?5u:m->page==FILM_MENU_DETAILS?6u:7u;
    if(row>=count || (m->page==FILM_MENU_HOME && row==3) ||
       (m->page==FILM_MENU_DETAILS && (row==3 || row==4))) {
        out[0]=0;return 0;
    }
    if(!x||!y||!width||!height)return -1;
    int n;
    if(m->page==FILM_MENU_STOCKS || m->page==FILM_MENU_STOCKS_2 || m->page==FILM_MENU_STOCKS_3) {
        unsigned pcount=m->page==FILM_MENU_STOCKS_3?2u:5u;
        int base=m->page==FILM_MENU_STOCKS?0:(m->page==FILM_MENU_STOCKS_2?5:10);
        if(row<pcount) {
            *x=76;*width=488;*y=98+(int)row*54;*height=46;
            n=snprintf(out,capacity,"%s %s",base+(int)row==m->settings.stock?"*":" ",
                       film_stock_display_name(base+(int)row));
        } else if(row==5) {
            *x=404;*width=160;*y=386;*height=42;
            n=snprintf(out,capacity,"Back");
        } else if(row==6) {
            *x=76;*width=160;*y=386;*height=42;
            n=snprintf(out,capacity,"%s",
                       m->page==FILM_MENU_STOCKS?"Next 2/3 >":
                       (m->page==FILM_MENU_STOCKS_2?"Next 3/3 >":"< Page 1"));
        } else {
            out[0]=0;return 0;
        }
    } else if(m->page==FILM_MENU_DETAILS) {
        if(row==0) {
            *x=76;*width=488;*y=106;*height=76;
            n=snprintf(out,capacity,"Grain: %s",grain_detail_name(m->settings.grain));
        } else if(row==1) {
            *x=76;*width=488;*y=198;*height=76;
            n=snprintf(out,capacity,"Halation: %s",m->settings.halation?"ON":"OFF (Fast)");
        } else if(row==2) {
            *x=76;*width=488;*y=290;*height=76;
            n=snprintf(out,capacity,"Reset Details (Std/ON)");
        } else {
            *x=404;*width=160;*y=386;*height=42;
            n=snprintf(out,capacity,"Back");
        }
    } else if(row<2) {
        *x=76;*width=488;*y=row==0?98:168;*height=60;
        if(row==0)n=snprintf(out,capacity,"LUT: %s",m->settings.enabled?"ON":"OFF");
        else n=snprintf(out,capacity,"Film: %s",film_stock_display_name(m->settings.stock));
    } else if(row==4) {
        *x=76;*width=488;*y=238;*height=60;
        n=snprintf(out,capacity,"Details: Grain %s / Halo %s",
                   film_grain_display_name(m->settings.grain),
                   m->settings.halation?"ON":"OFF");
    } else {
        *x=404;*width=160;*y=386;*height=42;
        n=snprintf(out,capacity,"Close");
    }
    return n>=0 && (unsigned)n<capacity?1:-1;
}
