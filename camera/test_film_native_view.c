#include "film_native_view.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static unsigned now, drawings, visibility_calls, persistence;
static int visible;
static char labels[7][80];
static struct film_settings saved;
static void show(void *p, int active) {
    (void)p; visible=active; ++visibility_calls;
}
static void draw(void *p,const struct film_menu *m) {
    (void)p; ++drawings;
    for(unsigned i=0;i<7;i++) {
        char text[80];int x,y,w,h;
        int used=film_native_view_row(m,i,text,sizeof(text),&x,&y,&w,&h);
        assert(used>=0);
        if(used)assert((x==76 && w==488 && y>=98 && y+h<=370) ||
                       ((x==404 || x==76) && w==160 && y==386 && h==42));
        strcpy(labels[i],used?text:"");
    }
}
static void persist(void *p,const struct film_settings *s) {
    (void)p; saved=*s; ++persistence;
}
static void tick(struct film_native_view *v,int menu,int zoom,int other,int touch,int x,int y) {
    now+=20; film_native_view_step(v,now,menu,zoom,other,touch,x,y);
}
static void chord(struct film_native_view *v) {
    tick(v,0,0,0,6,0,0);
    for(int i=0;i<34;i++) tick(v,1,1,0,6,0,0);
    tick(v,0,0,0,6,0,0);
}
static void tap(struct film_native_view *v,int x,int y) {
    tick(v,0,0,0,5,x,y);
    tick(v,0,0,0,6,32767,32767);
}
int main(void) {
    const struct film_native_view_ops ops={show,draw,persist};
    struct film_native_view v;
    film_native_view_init(&v,&ops,0,0);
    assert(!v.menu.visible && !drawings && !persistence);
    chord(&v);assert(visible && v.menu.visible && visibility_calls==1);
    assert(!strcmp(labels[0],"LUT: OFF"));assert(!strcmp(labels[1],"Film: Portra 400"));
    assert(!strcmp(labels[4],"Details: Grain MEDIUM / Halo ON"));
    assert(!strcmp(labels[2],"Close"));
    unsigned previous=drawings;
    for(int i=0;i<20;i++)tick(&v,0,0,0,6,32767,32767);
    assert(drawings==previous); /* Release enum 6 is not truthy/down. */
    tick(&v,0,0,0,5,120,128);assert(!persistence && v.menu.selected==0);
    previous=drawings;
    for(int i=0;i<10;i++)tick(&v,0,0,0,5,120,128);
    assert(drawings==previous); /* No repeated rendering for a held touch. */
    tick(&v,0,0,0,6,32767,32767);
    assert(persistence==1 && saved.enabled==1 && !strcmp(labels[0],"LUT: ON"));
    tick(&v,0,0,0,6,32767,32767);assert(persistence==1);
    tap(&v,120,268);assert(v.menu.page==FILM_MENU_DETAILS);
    assert(!strcmp(labels[0],"Grain: Standard"));
    assert(!strcmp(labels[1],"Halation: ON"));
    assert(!strcmp(labels[5],"Back"));
    tap(&v,120,140);assert(persistence==2 && saved.grain==3 && !strcmp(labels[0],"Grain: Heavy"));
    tap(&v,120,230);assert(persistence==3 && saved.halation==0 && !strcmp(labels[1],"Halation: OFF (Fast)"));
    tap(&v,480,405);assert(v.menu.page==FILM_MENU_HOME);
    assert(!strcmp(labels[4],"Details: Grain HIGH / Halo OFF"));
    tap(&v,120,198);assert(v.menu.page==FILM_MENU_STOCKS);
    assert(!strcmp(labels[0],"* Portra 400"));
    assert(!strcmp(labels[2],"  Fuji Provia"));
    assert(!strcmp(labels[5],"Back"));
    assert(!strcmp(labels[6],"Next 2/3 >"));
    tap(&v,120,405);assert(v.menu.page==FILM_MENU_STOCKS_2);
    assert(!strcmp(labels[0],"  Classic Chrome"));
    assert(!strcmp(labels[4],"  Polaroid PX-680"));
    assert(!strcmp(labels[6],"Next 3/3 >"));
    tap(&v,120,405);assert(v.menu.page==FILM_MENU_STOCKS_3);
    assert(!strcmp(labels[0],"  Kodak Ektar 100"));
    assert(!strcmp(labels[1],"  Ilford HP5"));
    assert(!strcmp(labels[2],""));
    assert(!strcmp(labels[6],"< Page 1"));
    tap(&v,120,170);assert(v.menu.page==FILM_MENU_HOME && v.menu.settings.stock==11);
    assert(persistence==4 && saved.stock==11);
    tick(&v,0,0,1,6,0,0);assert(!visible && !v.menu.visible);
    chord(&v);assert(visible && v.menu.settings.enabled && v.menu.settings.stock==11 &&
                     v.menu.settings.grain==3 && v.menu.settings.halation==0);
    tap(&v,480,405);assert(!visible && !v.menu.visible);
    chord(&v);assert(visible);
    tick(&v,0,0,0,5,120,130); /* Incomplete gesture followed by invalid input. */
    tick(&v,0,0,0,0,120,130);assert(!visible && persistence==4);
    film_native_view_hide(&v);assert(!visible && persistence==4);
    assert(film_native_view_row(&v.menu,7,labels[0],80,0,0,0,0)==-1);
    puts("NATIVE_FILM_VIEW_OK");
}
