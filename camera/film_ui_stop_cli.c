/* Exact-module owned-word shutdown request. Never calls Sony or unloads UI.
 * Outer coordinator must stop dispatch/capture and wait for every job first.
 */
#define _GNU_SOURCE
#include "film_settings_source.h"
#include "film_settings_maps.h"
#include "film_ui_stop_build.h"
#ifdef A7R4A_DYNAMIC_IDENTITY
#include "a7r4a_target.h"
#include <dirent.h>
#include <stdlib.h>
#endif
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

_Static_assert(sizeof(unsigned)==4,"owned control word must be 32-bit");
_Static_assert((FILM_UI_STOP_CONTROL_RVA&3u)==0,"owned control word alignment");
_Static_assert(FILM_UI_STOP_CONTROL_RVA<FILM_UI_STOP_UI_RVA ||
    FILM_UI_STOP_CONTROL_RVA>=FILM_UI_STOP_UI_RVA+FILM_UI_SNAPSHOT_BYTES,
    "owned stop word must not overlap the UI snapshot ABI");

struct observed {
    uint32_t base,state,control,request;
    uint32_t expected_system,expected_tid;
    unsigned char first[FILM_UI_SNAPSHOT_BYTES],second[FILM_UI_SNAPSHOT_BYTES];
};
static char g_proc_root[64];

static uint32_t word(const void *p,unsigned at) {
    uint32_t value;memcpy(&value,(const unsigned char *)p+at,4);return value;
}
static int fail(const char *reason) {
    printf("FILM_UI_STOP_UNAVAILABLE reason=%s\n",reason);return 11;
}
static int resolve_proc_root(void) {
#ifdef A7R4A_DYNAMIC_IDENTITY
    if(!strcmp(FILM_UI_STOP_PROC,"/proc/auto")) {
        DIR *dir=opendir("/proc");if(!dir)return 0;
        struct dirent *entry;long found=-1;unsigned matches=0;
        while((entry=readdir(dir))) {
            char *end=NULL;long pid=strtol(entry->d_name,&end,10);
            if(!*entry->d_name || *end || pid<=0)continue;
            char fpath[64],comm[32]={0};snprintf(fpath,sizeof(fpath),"/proc/%ld/comm",pid);
            FILE *f=fopen(fpath,"r");if(!f)continue;
            int ok=fgets(comm,sizeof(comm),f) && !strcmp(comm,A7R4A_PROC_COMM "\n");
            fclose(f);if(ok){found=pid;++matches;}
        }
        closedir(dir);
        if(matches!=1u || found<=0)return 0;
        int n=snprintf(g_proc_root,sizeof(g_proc_root),"/proc/%ld",found);
        return n>0 && (unsigned)n<sizeof(g_proc_root);
    }
#endif
    int n=snprintf(g_proc_root,sizeof(g_proc_root),"%s",FILM_UI_STOP_PROC);
    return n>0 && (unsigned)n<sizeof(g_proc_root);
}
static int path(char *out,unsigned capacity,const char *name) {
    int n=snprintf(out,capacity,"%s/%s",g_proc_root,name);
    return n>0 && (unsigned)n<capacity;
}
static int identity(char start[24]) {
    char file[512],line[1024],prefix[48];
    if(!path(file,sizeof(file),"comm"))return 0;
    FILE *f=fopen(file,"r");
    if(!f)return 0;
    int ok=fgets(line,sizeof(line),f) && !strcmp(line,"im.elf\n");
    fclose(f);if(!ok || !path(file,sizeof(file),"stat"))return 0;
    f=fopen(file,"r");if(!f)return 0;
    ok=fgets(line,sizeof(line),f)!=0;fclose(f);
    const char *pid_str=g_proc_root+6;
    int pn=snprintf(prefix,sizeof(prefix),"%s (im.elf) ",pid_str);
    if(!ok || pn<=0 || (unsigned)pn>=sizeof(prefix) || strncmp(line,prefix,(size_t)pn))return 0;
    char *p=line+pn;
    for(unsigned field=3;field<22;field++) {
        if(!*p || *p=='\n')return 0;
        while(*p && *p!=' ' && *p!='\n')++p;
        if(*p!=' ')return 0;
        while(*p==' ')++p;
    }
    unsigned length=0;
    while(p[length]>='0' && p[length]<='9' && length<23)++length;
    if(!length || length>20 || (p[length]!=' ' && p[length]!='\n'))return 0;
    memcpy(start,p,length);start[length]=0;return 1;
}
static int absent(const char *file) {
    int fd=open(file,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);
    if(fd>=0){close(fd);return 0;}
    return errno==ENOENT;
}
static const char *outer_stopped(void) {
    int fd=open(FILM_UI_STOP_MARKER,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);
    if(fd<0)return "outer_not_stopped";
    char byte;
    int ok=lseek(fd,0,SEEK_END)>=0 && read(fd,&byte,1)==0;
    close(fd);if(!ok)return "outer_not_stopped";
    if(!absent(FILM_UI_STOP_CAPTURE_LOCK) || !absent(FILM_UI_STOP_INFLIGHT) ||
       !absent(FILM_UI_STOP_JOB_LOCK))return "outer_busy";
    return 0;
}
struct bss_geometry {
    uint32_t image_page,segment_page,file_page,memory_page,offset_bias;
};
static uint32_t little32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 |
           (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static unsigned little16(const unsigned char *p) {
    return (unsigned)p[0] | (unsigned)p[1]<<8;
}
static int bss_geometry(struct bss_geometry *out) {
    unsigned char header[52],program[32];
    int fd=open(FILM_UI_STOP_MODULE,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);
    if(fd<0)return 0;
    int ok=pread(fd,header,sizeof(header),0)==sizeof(header);
    if(!ok || memcmp(header,"\177ELF\1\1\1",7) ||
       little16(header+16)!=3u || little16(header+18)!=40u ||
       little32(header+20)!=1u || little16(header+40)!=52u ||
       little16(header+42)!=32u){close(fd);return 0;}
    uint32_t at=little32(header+28);unsigned count=little16(header+44);
    if(!count || count>32u || at<sizeof(header) || at>FILM_UI_STOP_MODULE_BYTES ||
       count>(FILM_UI_STOP_MODULE_BYTES-at)/sizeof(program)){close(fd);return 0;}
    unsigned anchors=0,covers=0;struct bss_geometry found={0};
    for(unsigned i=0;ok && i<count;++i) {
        if(pread(fd,program,sizeof(program),at+i*sizeof(program))!=sizeof(program)){ok=0;break;}
        if(little32(program)!=1u)continue; /* PT_LOAD */
        uint32_t offset=little32(program+4),rva=little32(program+8);
        uint32_t files=little32(program+16),memory=little32(program+20),flags=little32(program+24);
        if(files>memory || offset>FILM_UI_STOP_MODULE_BYTES ||
           files>FILM_UI_STOP_MODULE_BYTES-offset || memory>UINT32_MAX-rva){ok=0;break;}
        if(!offset && !rva && flags==5u && files>=at+count*sizeof(program)) {
            if(memory>UINT32_MAX-4095u){ok=0;break;}
            ++anchors;found.image_page=(memory+4095u)&~4095u;
        }
        if(FILM_UI_STOP_CONTROL_RVA<rva || FILM_UI_STOP_CONTROL_RVA>UINT32_MAX-4u ||
           FILM_UI_STOP_CONTROL_RVA+4u>rva+memory)continue;
        ++covers;
        uint32_t file_end=rva+files,memory_end=rva+memory;
        if(flags!=6u || !files || files>=memory || FILM_UI_STOP_CONTROL_RVA<file_end ||
           rva<offset || ((rva-offset)&4095u) ||
           file_end>UINT32_MAX-4095u || memory_end>UINT32_MAX-4095u){ok=0;break;}
        found.segment_page=rva&~4095u;
        found.file_page=(file_end+4095u)&~4095u;
        found.memory_page=(memory_end+4095u)&~4095u;
        found.offset_bias=rva-offset;
    }
    close(fd);
    if(!ok || anchors!=1u || covers!=1u || found.file_page>=found.memory_page)return 0;
    *out=found;return 1;
}
struct stop_mapping {
    uint32_t low,high,offset;
    char permissions[5],device[16],inode[24];
    int module,anonymous;
};
static int map_record(struct stop_mapping *out,const char *line) {
    unsigned low,high,offset;int at=0;
    if(sscanf(line,"%x-%x %4s %x %15s %23s %n",&low,&high,out->permissions,&offset,
              out->device,out->inode,&at)!=6 || at<=0 || low>=high ||
       strlen(out->permissions)!=4u)return 0;
    out->low=low;out->high=high;out->offset=offset;
    size_t length=strcspn(line+at,"\r\n");
    out->module=strlen(FILM_UI_STOP_MODULE)==length && !memcmp(line+at,FILM_UI_STOP_MODULE,length);
    out->anonymous=!length && !offset && !strcmp(out->device,"00:00") && !strcmp(out->inode,"0");
    return 1;
}
static int mapping(struct observed *out) {
    char file[512],line[1024];
    if(!path(file,sizeof(file),"maps"))return 0;
    FILE *f=fopen(file,"r");if(!f)return 0;
    struct film_settings_map ui={0};unsigned control_count=0,control_ranges=0;uint32_t control=0;
    struct bss_geometry bss={0};int have_bss=bss_geometry(&bss);
    struct stop_mapping previous={0},image={0};int have_previous=0,have_image=0,ok=1;
#ifdef A7R4A_DYNAMIC_IDENTITY
    uint32_t libobj_base=0,view2_base=0;unsigned libobj_n=0,view2_n=0;
#endif
    while(fgets(line,sizeof(line),f)) {
        size_t length=strlen(line);
        if(length==sizeof(line)-1 && line[length-1]!='\n'){ok=0;break;}
        struct stop_mapping current={0};
        if(!map_record(&current,line) || (have_previous && current.low<previous.high)){ok=0;break;}
        film_settings_map_line(&ui,line,FILM_UI_STOP_MODULE,FILM_UI_STOP_UI_RVA);
#ifdef A7R4A_DYNAMIC_IDENTITY
        {
            unsigned lo2=0,hi2=0,off2=0;char p2[5]={0};int at2=0;
            if(sscanf(line,"%x-%x %4s %x %*s %*s %n",&lo2,&hi2,p2,&off2,&at2)==4 && at2>0) {
                size_t l2=strcspn(line+at2,"\r\n");
                if(!off2 && !strcmp(p2,"r-xp")) {
                    if(l2==strlen(A7R4A_LIBOBJ_PATH) && !memcmp(line+at2,A7R4A_LIBOBJ_PATH,l2)){libobj_base=lo2;++libobj_n;}
                    else if(l2==strlen(A7R4A_VIEW2_PATH) && !memcmp(line+at2,A7R4A_VIEW2_PATH,l2)){view2_base=lo2;++view2_n;}
                }
            }
        }
#endif
        if(current.module && !current.offset && !strcmp(current.permissions,"r-xp")) {
            image=current;++have_image;
        }
        if(ui.image_count==1 && FILM_UI_STOP_CONTROL_RVA<=UINT32_MAX-ui.base) {
            control=ui.base+FILM_UI_STOP_CONTROL_RVA;
            if(control>=current.low && control<=UINT32_MAX-4u && control+4u<=current.high) {
                ++control_ranges;
                if(!strcmp(current.permissions,"rw-p")) {
                    if(current.module)++control_count;
                    else if(have_bss && have_previous && have_image==1 && current.anonymous &&
                            bss.memory_page<=UINT32_MAX-ui.base &&
                            bss.image_page<=UINT32_MAX-ui.base && !(ui.base&4095u) &&
                            image.low==ui.base && image.high==ui.base+bss.image_page &&
                            current.low==ui.base+bss.file_page && current.high==ui.base+bss.memory_page &&
                            previous.module && !strcmp(previous.permissions,"rw-p") &&
                            !(previous.low&4095u) && !(previous.high&4095u) && !(previous.offset&4095u) &&
                            previous.high==current.low && previous.low>=ui.base &&
                            previous.low-ui.base>=bss.segment_page && previous.low-ui.base<bss.file_page &&
                            previous.low-ui.base>=bss.offset_bias &&
                            previous.offset==previous.low-ui.base-bss.offset_bias &&
                            !strcmp(previous.device,image.device) && !strcmp(previous.inode,image.inode) &&
                            ui.state>=previous.low && ui.state<=UINT32_MAX-FILM_UI_SNAPSHOT_BYTES &&
                            ui.state+FILM_UI_SNAPSHOT_BYTES<=previous.high)++control_count;
                }
            }
        }
        previous=current;have_previous=1;
    }
    ok=ok && !ferror(f);fclose(f);
    if(!ok || ui.image_count!=1 || ui.state_count!=1 || control_ranges!=1 || control_count!=1)return 0;
#ifdef A7R4A_DYNAMIC_IDENTITY
    if(libobj_n!=1u || view2_n!=1u || !libobj_base || !view2_base)return 0;
    out->expected_system=libobj_base+A7R4A_SYSTEM_PTR_RVA;
    out->expected_tid=view2_base+A7R4A_STATE_RVA;
#else
    out->expected_system=0x956f0u;
    out->expected_tid=380u;
#endif
    out->base=ui.base;out->state=ui.state;out->control=control;return 1;
}
static const char *read_state(int fd,struct observed *out) {
    uint32_t first,second;
    if(pread(fd,&first,4,out->control)!=4 ||
       pread(fd,out->first,sizeof(out->first),out->state)!=sizeof(out->first) ||
       pread(fd,out->second,sizeof(out->second),out->state)!=sizeof(out->second) ||
       pread(fd,&second,4,out->control)!=4)
        return "read";
#ifdef A7R4A_DYNAMIC_IDENTITY
    uint32_t exp_sys=0,trace_hdr[12]={0};
    if(pread(fd,&exp_sys,4,out->expected_system)!=4 ||
       pread(fd,trace_hdr,sizeof(trace_hdr),out->expected_tid)!=sizeof(trace_hdr))
        return "read";
    if(!exp_sys || trace_hdr[0]!=A7R4A_TRACE_MAGIC || trace_hdr[1]!=1u ||
       trace_hdr[8]!=1u || !trace_hdr[9] || trace_hdr[10]!=exp_sys)
        return "state";
    uint32_t exp_tid=trace_hdr[9];
#else
    uint32_t exp_sys=out->expected_system,exp_tid=out->expected_tid;
#endif
    if(first>2u || second>2u)return "control";
    if(first!=second)return "transition";
    out->request=second;
    if(second==2u) {
        static const unsigned fixed[]={0x68,0xc0,0xc4,0x18c,0x190};
        if(memcmp(out->first,out->second,16))return "state";
        for(unsigned i=0;i<sizeof(fixed)/sizeof(*fixed);i++)
            if(word(out->first,fixed[i])!=word(out->second,fixed[i]))return "state";
        if(word(out->second,0x68)!=exp_sys || word(out->second,0xc0)!=exp_tid ||
           !word(out->second,0xc4) || word(out->second,0x18c)!=1u ||
           word(out->second,0x190)!=0u)return "state";
        return 0;
    }
    uint32_t now;struct film_settings settings;
    if(film_settings_now(&now))return "state";
    int snapshot=film_settings_snapshot_get_for(&settings,out->first,
        out->second,sizeof(out->first),now,exp_sys,exp_tid);
    if(snapshot)return second==1u && (snapshot==1 || snapshot==2)?"transition":"state";
    return 0;
}
static const char *still_same(const struct observed *original,const char *start) {
    char current[24];struct observed map={0};
    if(!identity(current) || strcmp(current,start))return "identity";
    if(!film_file_matches(FILM_UI_STOP_MODULE,FILM_UI_STOP_MODULE_BYTES,
                          FILM_UI_STOP_MODULE_FNV))return "artifact";
    if(!mapping(&map) || map.base!=original->base || map.state!=original->state ||
       map.control!=original->control)return "maps";
    return 0;
}
#ifdef A7R4A_DYNAMIC_IDENTITY
static int dump_state_trace(void) {
    if(!resolve_proc_root())return fail("identity");
    char start[24];if(!identity(start))return fail("identity");
    char file[512],line[1024];
    if(!path(file,sizeof(file),"maps"))return fail("maps");
    FILE *f=fopen(file,"r");if(!f)return fail("maps");
    uint32_t view2_base=0,state_va=0,rw_hi=0;
    unsigned view2_n=0;int prev_rw=0,state_ok=0,ok=1;
    while(fgets(line,sizeof(line),f)) {
        size_t len=strlen(line);
        if(len==sizeof(line)-1 && line[len-1]!='\n'){ok=0;break;}
        unsigned lo=0,hi=0,off=0;char p[5]={0},dev[16]={0},inode[24]={0};int at=0;
        if(sscanf(line,"%x-%x %4s %x %15s %23s %n",&lo,&hi,p,&off,dev,inode,&at)!=6 ||
           at<=0 || lo>=hi){ok=0;break;}
        size_t plen=strcspn(line+at,"\r\n");
        int is_v2=(plen==strlen(A7R4A_VIEW2_PATH) && !memcmp(line+at,A7R4A_VIEW2_PATH,plen));
        int is_anon=(!plen && !off && !strcmp(dev,"00:00") && !strcmp(inode,"0"));
        if(!off && !strcmp(p,"r-xp") && is_v2) {
            view2_base=lo;state_va=lo+A7R4A_STATE_RVA;++view2_n;
        }
        if(!strcmp(p,"rw-p") && view2_n==1u &&
           (is_v2 || (is_anon && prev_rw && lo==rw_hi))) {
            if(state_va>=lo && state_va+A7R4A_STATE_SIZE<=hi)state_ok=1;
            rw_hi=hi;prev_rw=1;
        } else prev_rw=0;
    }
    ok=ok && !ferror(f);fclose(f);
    if(!ok || view2_n!=1u || !view2_base || !state_ok)return fail("maps");
    if(!path(file,sizeof(file),"mem"))return fail("read");
    int fd=open(file,O_RDONLY);if(fd<0)return fail("read");
    unsigned char buf[A7R4A_STATE_SIZE];
    ssize_t got=pread(fd,buf,sizeof(buf),state_va);
    close(fd);
    if(got!=(ssize_t)sizeof(buf))return fail("read");
    printf("LOADER magic=0x%08x ver=%u en=%u cnt=%u inf=%u cap=%u err=%u exp_sys=0x%08x\n",
           word(buf,0),word(buf,4),word(buf,8),word(buf,12),
           word(buf,16),word(buf,20),word(buf,24),word(buf,28));
    if(word(buf,12)>0u) {
        const unsigned char *e=buf+32u;
        printf("EVENT0 seq=%u tid=%u sys=0x%08x before=0x%x mouse=%u after=0x%x raw=0x%x btn=0x%x mod=0x%08x attach=%d native=0x%x tag=0x%x\n",
               word(e,0),word(e,4),word(e,8),word(e,12),word(e,16),word(e,20),
               word(e,24),word(e,28),word(e,32),(int)word(e,36),word(e,40),word(e,44));
    }
    const unsigned char *tr=buf+A7R4A_TRACE_OFFSET;
    printf("TRACE_HDR magic=0x%08x ver=%u seq=%u ms=%u pid=%u tid=%u cap=%u rec=%u\n",
           word(tr,0),word(tr,4),word(tr,8),word(tr,16),
           word(tr,20),word(tr,24),word(tr,28),word(tr,32));
    uint32_t seq=word(tr,8);
    if(word(tr,0)==0x33544955u && seq>0u) {
        uint32_t count=seq<22u?seq:22u;
        uint32_t first_seq=seq-count+1u;
        for(uint32_t s=first_seq;s<=seq;s++) {
            const unsigned char *r=tr+48u+((s-1u)%22u)*48u;
            uint32_t g0=word(r,0),g1=word(r,44);
            if(g0!=g1 || (g0&1u)!=0u || (g0>>1)!=s)continue;
            printf("TRACE_REC seq=%u hash=0x%08x ms=%u tid=%u a=%d b=%d ticks=%u flags=0x%x\n",
                   s,word(r,4),word(r,8),word(r,12),(int)word(r,16),(int)word(r,20),
                   word(r,24),word(r,28));
        }
    }
    return 0;
}
#endif
int main(int argc,char **argv) {
    if(setvbuf(stdout,NULL,_IONBF,0)) {
        int result=fail("stdout_buffering");(void)fflush(stdout);return result;
    }
    int request=argc==2 && !strcmp(argv[1],"--request");
    int dump_trace=argc==2 && !strcmp(argv[1],"--dump-trace");
    if(argc!=2 || (!request && !dump_trace && strcmp(argv[1],"--check"))) {
        fprintf(stderr,"usage: a6400-film-ui-stop.elf --check | --request | --dump-trace\n"
            "Stop dispatch and wait for capture/jobs to exit before --request.\n"
            "Create " FILM_UI_STOP_MARKER "; all capture/inflight/job locks must be absent.\n"
            "This tool does not manage jobs, call Sony, remove hooks or unload the UI.\n");
        return 2;
    }
    if(dump_trace) {
#ifdef A7R4A_DYNAMIC_IDENTITY
        return dump_state_trace();
#else
        return fail("unsupported");
#endif
    }
    const char *reason=request?outer_stopped():0;
    if(reason)return fail(reason);
    if(!film_file_matches(FILM_UI_STOP_MODULE,FILM_UI_STOP_MODULE_BYTES,
                          FILM_UI_STOP_MODULE_FNV))return fail("artifact");
    if(!resolve_proc_root())return fail("identity");
    char start[24];if(!identity(start))return fail("identity");
    struct observed state={0};if(!mapping(&state))return fail("maps");
    char file[512];if(!path(file,sizeof(file),"mem"))return fail("read");
    int fd=open(file,request?O_RDWR:O_RDONLY);
    if(fd<0)return fail("read");
    reason=read_state(fd,&state);
    if(reason){close(fd);return fail(!strcmp(reason,"transition")?"state":reason);}
    reason=still_same(&state,start);
    if(reason){close(fd);return fail(reason);}
    if(state.request==2u){close(fd);puts("FILM_UI_STOPPED");return 0;}
    if(!request) {
        close(fd);printf("FILM_UI_RUNNING request=%u\n",state.request);return 0;
    }
    reason=outer_stopped();
    if(reason){close(fd);return fail(reason);}
    if(state.request==0u) {
        const unsigned stop=1u;
        if(pwrite(fd,&stop,sizeof(stop),state.control)!=sizeof(stop)) {
            close(fd);return fail("write");
        }
    }
    uint32_t began,now;
    if(film_settings_now(&began)){close(fd);return fail("clock");}
    for(;;) {
        reason=still_same(&state,start);
        if(!reason)reason=outer_stopped();
        if(!reason)reason=read_state(fd,&state);
        if(reason && strcmp(reason,"transition")){close(fd);return fail(reason);}
        if(!reason && state.request==2u){close(fd);puts("FILM_UI_STOPPED");return 0;}
        if(!reason && state.request!=1u){close(fd);return fail("control");}
        if(film_settings_now(&now)){close(fd);return fail("clock");}
        if((uint32_t)(now-began)>=FILM_UI_STOP_WAIT_MS) {
            close(fd);puts("FILM_UI_STOP_TIMEOUT request=1");return 12;
        }
        (void)poll(0,0,20);
    }
}
