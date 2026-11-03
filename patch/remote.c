/* Development-only key-gated remote shell and read-only framebuffer capture. */
#ifndef PORT
#define PORT 2302
#endif
#ifndef KEY_FILE
#define KEY_FILE "/mnt/mmc/q2remote.key"
#endif
#ifndef CACHE_FILE
#define CACHE_FILE "/mnt/data/q2remote.key"
#endif
#ifndef CARD
#define CARD "/mnt/mmc"
#endif
#ifndef MOUNTS
#define MOUNTS "/proc/mounts"
#endif
#define KEY_MIN 16
#define LINE 4096
#define HELLO_S 10

#ifdef REMOTE_HOST
#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#define O_SAVE (O_WRONLY | O_CREAT | O_TRUNC)
#else
#define O_SAVE 0x301 /* MIPS O_WRONLY | O_CREAT | O_TRUNC */
#define AF_INET 2
#define SOCK_STREAM 2
#define SOL_SOCKET 0xffff
#define SO_REUSEADDR 4
#define SIGCHLD 18
#define SIG_DFL ((void (*)(int))0)
#define SIG_IGN ((void (*)(int))1)
#define O_RDWR 2
struct sockaddr_in { unsigned short sin_family, sin_port; unsigned sin_addr; char zero[8]; };
int socket(int,int,int), setsockopt(int,int,int,const void*,unsigned), bind(int,const void*,unsigned), listen(int,int), accept(int,void*,void*);
int fork(void), close(int), dup2(int,int), open(const char*,int,...), read(int,void*,unsigned), write(int,const void*,unsigned);
int ioctl(int,unsigned,...), execl(const char*,const char*,...);
void *mmap(void*,unsigned,int,int,int,long); unsigned alarm(unsigned); void (*signal(int,void(*)(int)))(int); void _exit(int); int usleep(unsigned);
int strcmp(const char*,const char*);
int unlink(const char*), rename(const char*,const char*), fsync(int), memcmp(const void*,const void*,unsigned);
char *strstr(const char*,const char*);
long ptrace(int,int,void*,void*); int waitpid(int,int*,int), atoi(const char*), snprintf(char*,unsigned,const char*,...);
int pread(int,void*,unsigned,long);
#endif
#define FBIOGET_VSCREENINFO 0x4600
#define FBIOGET_FSCREENINFO 0x4602

static int line(int fd, char *s, int cap) {
    for (int i=0; i<cap-1; ++i) {
        char c;
        if (read(fd,&c,1)!=1) return -1;
        if (c=='\n') { s[i]=0; return i; }
        if (!c || c=='\r') return -1;
        s[i]=c;
    }
    return -1;
}

static int key_load(const char *path, char *key) {
    int fd=open(path,0), n=0;
    if (fd<0) return -1;
    while (n<128) {
        int r=read(fd,key+n,128-n);
        if (r<=0) break;
        n+=r;
    }
    close(fd);
    if (n==128) return -1;
    while (n>0 && (key[n-1]=='\n' || key[n-1]=='\r')) --n;
    if (n<KEY_MIN) return -1;
    for (int i=0;i<n;++i) if (!((key[i]>='a'&&key[i]<='z')||(key[i]>='A'&&key[i]<='Z')||
                                 (key[i]>='0'&&key[i]<='9')||key[i]=='_'||key[i]=='-')) return -1;
    key[n]=0;
    return n;
}

/* demo unmounts the card while it is shared over USB storage, from boot if the cable is in, so the
 * last key read from the card is kept on internal storage and used only while the card is away.
 * Removing the card's key revokes the copy the next time the card is mounted and a client connects. */
static int card_mounted(void) {
    char m[4096]; /* ponytail: the Q2's few mounts fit; read in a loop if they ever don't */
    int fd=open(MOUNTS,0), n=fd<0 ? 0 : read(fd,m,sizeof m-1);
    if (fd>=0) close(fd);
    m[n>0 ? n : 0]=0;
    return strstr(m," " CARD " ")!=0;
}

static int key_read(char *key) {
    if (!card_mounted()) return key_load(CACHE_FILE,key);
    char old[128];
    int n=key_load(KEY_FILE,key);
    if (n<0) { unlink(CACHE_FILE); return n; }
    if (key_load(CACHE_FILE,old)!=n || memcmp(old,key,n)) {
        int fd=open(CACHE_FILE ".tmp",O_SAVE,0600);
        key[n]='\n';
        int ok=fd>=0 && write(fd,key,n+1)==n+1 && !fsync(fd);
        if (fd>=0) close(fd);
        key[n]=0;
        if (!ok || rename(CACHE_FILE ".tmp",CACHE_FILE)) unlink(CACHE_FILE ".tmp");
    }
    return n;
}

static int write_all(int fd, const void *data, unsigned n) {
    const char *p=data;
    while (n) { int r=write(fd,p,n); if (r<=0) return -1; p+=r; n-=r; }
    return 0;
}
#define SAY(fd,s) write_all(fd,s,sizeof s-1)

static void serve(int c) {
    char key[128], got[128], cmd[LINE];
    alarm(HELLO_S);
    int n=key_read(key), gn=line(c,got,sizeof got), cn=line(c,cmd,sizeof cmd), diff=0;
    if (n<KEY_MIN || gn!=n || cn<=0) { SAY(c,"ERR\n"); return; }
    for (int i=0;i<n;++i) diff|=key[i]^got[i];
    if (diff) { SAY(c,"ERR\n"); return; }
    alarm(0);
    SAY(c,"OK\n");
    signal(SIGCHLD,SIG_DFL);
    for (int i=0;i<3;++i) if (dup2(c,i)<0) { SAY(c,"q2remote: dup failed\n"); return; }
    close(c);
    execl("/bin/sh","sh","-c",cmd,(char*)0);
    SAY(1,"q2remote: exec /bin/sh failed\n");
    _exit(127);
}

static int capture(void) {
    unsigned var[40], fix[17];
    int fb=open("/dev/fb0",O_RDWR);
    if (fb<0 || ioctl(fb,FBIOGET_VSCREENINFO,var) || ioctl(fb,FBIOGET_FSCREENINFO,fix)) return 1;
    unsigned w=var[0], h=var[1], virtual_h=var[3], bpp=var[6], y=var[5], stride=fix[11], length=fix[5];
    if (w!=320 || h!=375 || virtual_h<2*h || bpp!=32 || stride<w*4 || y+h>virtual_h || length<virtual_h*stride) return 1;
    unsigned char *mem=mmap(0,length,1,1,fb,0);
    if (mem==(void*)-1) return 1;
    unsigned size=stride*h;
    if (write_all(1,"Q2FB\0",5)) return 1;
    unsigned meta[4]={w,h,stride,size};
    if (write_all(1,meta,sizeof meta)) return 1;
    return write_all(1,mem+y*stride,size) ? 1 : 0;
}

#define STACK_WORDS 160
/* --prof MS N TID...: N rounds, MS ms apart, of each thread's state letter, pc and ra, then the stack's
 * words that could be code addresses (tools/q2remote.py prof symbolizes them). Each thread is seized and
 * interrupted for an instant, which queues no SIGSTOP; a signal that arrives meanwhile goes on to it. A
 * thread in D waits until its I/O returns. MIPS PEEKUSR: 29 is $sp, 31 $ra, 64 the pc. */
static int prof(int argc, char **argv) {
    int ms=atoi(argv[2]), n=atoi(argv[3]);
    for (int i=0;i<n;++i) {
        for (int t=4;t<argc;++t) {
            int tid=atoi(argv[t]), w, fd, r, got;
            char buf[512], out[1024], state='?';
            snprintf(buf,sizeof buf,"/proc/%d/stat",tid);
            if ((fd=open(buf,0))<0) continue;
            r=read(fd,buf,sizeof buf-1);
            close(fd);
            buf[r>0 ? r : 0]=0;
            char *e=strstr(buf,") ");
            if (e) state=e[2];
            if (ptrace(0x4206,tid,0,0)) continue; /* PTRACE_SEIZE */
            ptrace(0x4207,tid,0,0);               /* PTRACE_INTERRUPT */
            /* until the interrupt's stop (PTRACE_EVENT_STOP); a signal's stop goes on with it */
            while ((got=waitpid(tid,&w,0x40000000))==tid && (w&0xff)==0x7f && (w>>16)!=0x80)
                ptrace(7,tid,0,(void*)(long)(w>>8&0xff)); /* PTRACE_CONT */
            if (got!=tid || (w&0xff)!=0x7f) { ptrace(17,tid,0,0); continue; } /* gone, or not stopped */
            unsigned long pc=ptrace(3,tid,(void*)64,0), ra=ptrace(3,tid,(void*)31,0),
                          sp=ptrace(3,tid,(void*)29,0), up[STACK_WORDS]={0};
            snprintf(buf,sizeof buf,"/proc/%d/mem",tid);
            if ((fd=open(buf,0))>=0) { pread(fd,up,sizeof up,(long)sp); close(fd); }
            ptrace(17,tid,0,0); /* PTRACE_DETACH, once the stack is read */
            int m=snprintf(out,sizeof out,"%d %c %lx %lx",tid,state,pc,ra);
            for (int k=0;k<STACK_WORDS && m<(int)sizeof out-12;++k)
                if (up[k]>=0x400000 && up[k]<0x80000000 && !(up[k]&3)) m+=snprintf(out+m,sizeof out-m," %lx",up[k]);
            out[m++]='\n';
            write_all(1,out,m);
        }
        usleep(ms*1000);
    }
    return 0;
}

int main(int argc,char **argv) {
    if (argc==2 && !strcmp(argv[1],"--shot")) return capture();
    if (argc>=5 && !strcmp(argv[1],"--prof")) return prof(argc,argv);
    int s=socket(AF_INET,SOCK_STREAM,0), one=1;
    if (s<0) return 1;
    struct sockaddr_in a={.sin_family=AF_INET,.sin_port=(PORT&255)<<8|PORT>>8};
    if (setsockopt(s,SOL_SOCKET,SO_REUSEADDR,&one,sizeof one) || bind(s,(void*)&a,sizeof a) || listen(s,4)) { close(s); return 1; }
    signal(SIGCHLD,SIG_IGN);
    /* Keep the key from boot: the card mounts around now, and with a USB cable in demo soon
     * unmounts it, before any client may connect. */
    if (!fork()) {
        for (int i=0;i<150 && !card_mounted();++i) usleep(200000);
        char key[128];
        key_read(key);
        _exit(0);
    }
    for (;;) {
        int c=accept(s,0,0);
        if (c<0) continue;
        int pid=fork();
        if (pid==0) { close(s); serve(c); close(c); _exit(1); }
        close(c);
    }
}
