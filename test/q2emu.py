"""test/patch.py's emulator: Machine runs the payload and stock's code under Unicorn against mocked UI services;
the machines and helpers its scenarios share. Imported by test/patch.py, test/scenarios/, test/playback.py and
test/headset_io.py, with the build directory as argv[1]."""
import ctypes, hashlib, io, json, math, os, pathlib, re, sqlite3, struct, subprocess, sys, tempfile
from functools import cmp_to_key
from unicorn import Uc, UcError, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE, UC_HOOK_BLOCK, UC_HOOK_INTR
from unicorn.unicorn_py3.unicorn import uclib, uccallback, uc_hook_h, HOOK_CODE_CFUNC  # 2.1.4's own: Machine.hooked registers a C hook
from unicorn.mips_const import *
sys.path.insert(0, sys.path[0] + '/../tools')  # tools/ first
from mips import load_elf
from build import jal, j, segments, symbols, BASE, SCRATCH, SCRATCH_MAX, HOOKS, WM_PAINT_LEAF, FUNCTIONS, GLOBALS, CONTEXT_DATA, ROOT, source_sha256, sha, PRIVATE_FUNCTIONS, TAG, VERSION, BETA, dev_tag
B=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else 'build')
payload_syms=symbols(B/'patch.elf')
manifest=json.loads((B/'manifest.json').read_text())
if manifest.get('source_sha256') != source_sha256():
    raise SystemExit(f'{B}/manifest.json does not match the current patch sources; rebuild into a fresh directory and pass it here')
for name,key in (('demo','demo_sha256'),('stock-demo','stock_demo_sha256'),('patch.bin','patch_sha256')):
    if sha((B/name).read_bytes()) != manifest.get(key):
        raise SystemExit(f'{B/name} does not match manifest.json; rebuild into a fresh directory')
assert manifest['version'] == (dev_tag(manifest['dev']) if manifest.get('dev') else TAG), 'Wrong version'
assert manifest.get('compact_code')
from ipod import INC, O  # patch/offsets.inc and its integer #defines
from theme import MOTION_WIDGET, MOTION_WINDOW, SCHEME_MODE_OFF, THEME_GEOM_OFF, THEME_MOTION_OFF, builtins as theme_builtins, geometry_sets
from theme import derive_gloss, turn, GLOSS_TOP  # tools/theme.py: the derived accents' bands
GEOMS=[g.name for g in geometry_sets(theme_builtins())]; GEOM_CLASSIC_LIGHT,GEOM_CLASSIC_LIGHT_TEXT=GEOMS.index('classic-light'),GEOMS.index('classic-light-text')
DC=O['DOUBLE_CLICK_MS']  # centre double-press window
# iPod accent presets: {gradient top, bottom, light tone, red tone, highlight} per Accent setting value.
ACCENTS=[tuple(int(v,16) for v in g) for g in re.findall(r'\{ 0x(\w+), 0x(\w+), 0x(\w+), 0x(\w+), 0x(\w+) \}',INC)]
# MINIMAL_ACCENT's row (Minimal only, Ivory) draws as Graphite.
assert ACCENTS[O['MINIMAL_ACCENT']]==ACCENTS[0]
CLASSIC=[p for p in range(len(ACCENTS)) if p!=O['MINIMAL_ACCENT']]  # the values Classic's Accent row takes
def color_t(rgb): return 0xff000000|(rgb&255)<<16|(rgb>>8&255)<<8|rgb>>16
O_GLYPH=re.search(r'#define BT_GLYPH "(\w+)"',INC)[1]  # the plain Bluetooth glyph a codec badge fades into
CONFIG={}  # config.ini [IPOD] keys a new Machine starts with; the payload reads them on first use
# Minimal's look the pixel checks were drawn under, read for a missing key: Ivory, square (the defaults are its
# preset's, Graphite and round)
SEED={'MINIMAL_ACCENT':'7','ROUNDED':'0','SCANDURATIONS':'1'}  # SCANDURATIONS: Cover Flow's track checks show song lengths
SETUP=[]  # functions a new Machine runs before anything can read config (QueueMachine's constructor does)
FILL,RADIUS=(0x40<<24)|O['FILL_RGB'],9  # the stock rounded-rect checks' translucent fill and radius
LCD_COLORS=(0x9abcdef0,0x12345678)
syms=symbols(B/'stock-demo')
syms.update(PRIVATE_FUNCTIONS)
VG_MOCKS=[n for n in syms if n.startswith(('vgcanvas_','vg_gradient_'))]

def _fp64_fix():
    """The stock binary is -mfp64 (FR=1); Unicorn's MIPS32 FPU only implements FR=0, so
    L-format conversions are rewritten to their W equivalents when an image is loaded. Every
    value the traversed canvas code converts fits 32 bits, so the results are identical. An mthc1
    just before one (the 64-bit integer's high word, as hscroll_label_step's 0x5fb3f8) is dropped:
    the W form doesn't read it, and under FR=0 it would overwrite the odd register beside it."""
    data=(B/'demo').read_bytes(); fixes=[]
    for _,(t,o,v,_,f,m,flags,_) in segments(data):
        if t!=1 or not flags&1: continue
        words=struct.unpack_from(f'<{f//4}I',data,o)
        for i,w in enumerate(words):
            if w>>26&0x3f==0x11 and w>>21&0x1f==0x15:
                fixes.append((v+4*i,(w&~(0x1f<<21))|(0x14<<21)))
            elif w>>26&0x3f==0x11 and w>>21&0x1f==0x07 and any(x>>26&0x3f==0x11 and x>>21&0x1f==0x15 for x in words[i+1:i+4]):
                fixes.append((v+4*i,0))
    return fixes
FP64_FIX=_fp64_fix()
REGS=[UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3]
SAVED=[UC_MIPS_REG_S0,UC_MIPS_REG_S1,UC_MIPS_REG_S2,UC_MIPS_REG_S3,
       UC_MIPS_REG_S4,UC_MIPS_REG_S5,UC_MIPS_REG_S6,UC_MIPS_REG_S7,UC_MIPS_REG_FP]
# What a mocked call may spoil, as the arrays uc_reg_write_batch takes: Machine.returns writes them in one call.
SPOILED=[UC_MIPS_REG_V1,*REGS,UC_MIPS_REG_T0,UC_MIPS_REG_T1,UC_MIPS_REG_T2,UC_MIPS_REG_T3,UC_MIPS_REG_T4,
         UC_MIPS_REG_T5,UC_MIPS_REG_T6,UC_MIPS_REG_T7,UC_MIPS_REG_T8,UC_MIPS_REG_T9]
SPOIL=ctypes.c_uint32(0xdeadbeef)
SPOILED_IDS=(ctypes.c_int*len(SPOILED))(*SPOILED); SPOILED_VALUES=(ctypes.c_void_p*len(SPOILED))(*[ctypes.addressof(SPOIL)]*len(SPOILED))
# A machine's hook is Python, and unicorn calling it at every instruction was most of a run's time. Unicorn calls
# FILTER.filter there instead, which calls the hook only where it has something to do: at each symbol's address, at
# each address a mock was ever set at (Handlers) or a test named with watch(), and anywhere from the mocks' own
# region (0x1000000) up. One marked for one machine is marked for all, which costs those only the call.
def _filter():
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(['cc','-O2','-shared','-fPIC','-x','c','-','-o',directory+'/filter.so'],check=True,input=b'''
#include <stdint.h>
static uint8_t marked[0x1000000 >> 5];  /* a bit an instruction */
void watch(uint32_t a) { if (a < 0x1000000) marked[a >> 5] |= 1 << (a >> 2 & 7); }
void filter(void *uc, uint64_t a, uint32_t size, void *hook) {
    if (a >= 0x1000000 || marked[a >> 5] >> (a >> 2 & 7) & 1) ((void (*)(void *, uint64_t, uint32_t, void *))hook)(uc, a, size, 0);
}''')
        return ctypes.CDLL(directory+'/filter.so')
FILTER=_filter()
watch=FILTER.watch; watch.argtypes=[ctypes.c_uint32]; watch.restype=None
ENTRIES={*syms.values(),*payload_syms.values(),*(int(v,16) for v in manifest['patch_symbols'].values())}
for a in ENTRIES: watch(a)

def signed(x): return x if x<0x80000000 else x-0x100000000
SCOPE_RULES='/mnt/data/q2-library-scope'  # library_scope.c's folder marker rules, written by a library update
LIBRARY_SCOPE=re.search(r'#define LIBRARY_SCOPE_TOKEN "(.*)"',(ROOT/'patch/library_scope.h').read_text())[1]
# peq.h's pod_rows rows, pod_value's argument, by name
POD={n:i for i,n in enumerate(re.findall(r'^\s*(POD_\w+)',re.search(r'enum \{\s*(POD_CHARGE\b.*?)\}',re.sub(r'/\*.*?\*/','',(ROOT/'patch/peq.h').read_text(),flags=re.S),re.S)[1],re.M))}
MUSIC_WHERE=re.search(r'#define MUSIC_WHERE "(.*)" LIBRARY_SCOPE_TOKEN',INC)[1]+LIBRARY_SCOPE  # the Library's music

# Q2_TRACE=FILE logs every draw and widget-property call, each hook's return and each passed case, by value.
TRACE=open(os.environ['Q2_TRACE'],'w') if os.environ.get('Q2_TRACE') else None
# Q2_CARD_THEME=FILE gives each Classic and Classic Modern machine FILE as its card theme (read_all's answer for the
# copy): those themes take none of a card theme's look, so with a FILE that sets no battery style a run's Q2_TRACE
# is the run's without one.
CARD_THEME=pathlib.Path(os.environ['Q2_CARD_THEME']).read_bytes() if os.environ.get('Q2_CARD_THEME') else None
CARD_READS=[]  # each read served: the count goes to stderr at the end, 0 meaning the run proved nothing
if CARD_THEME: __import__('atexit').register(lambda: print(len(CARD_READS),'card theme reads served',file=sys.stderr))
def trace(*fields):
    if TRACE: TRACE.write('\t'.join(fields)+'\n')
TRACE_ARGS={'canvas_get_clip_rect':'_','canvas_set_clip_rect':'_r','canvas_set_global_alpha':'_i','canvas_translate':'_ii','canvas_offline_create':'iii',
    **dict.fromkeys(('canvas_offline_begin_draw','canvas_offline_end_draw','canvas_offline_destroy'),'_'),
    **dict.fromkeys(('canvas_set_fill_color','canvas_set_stroke_color','canvas_set_text_color'),'_x'),
    **dict.fromkeys(('canvas_fill_rect','canvas_stroke_rect'),'_iiii'),'canvas_draw_icon':'_xii','canvas_set_font':'_si',
    'canvas_draw_text_in_rect':'_wr','canvas_draw_text_bidi_in_rect':'_wrsi','canvas_draw_text':'_wii','canvas_measure_text':'_w','canvas_draw_image':'_xrr',
    'canvas_fill_rounded_rect':'_rrdi','canvas_stroke_rounded_rect':'_rrdii',
    **{'vg:vgcanvas_'+n:c for n,c in (('save','_'),('restore','_'),('begin_path','_'),('close_path','_'),('fill','_'),
       ('stroke','_'),('translate','_ff'),('move_to','_ff'),('line_to','_ff'),('arc','_fffffi'),('set_fill_color','_x'),
       ('set_stroke_color','_x'),('set_fill_linear_gradient','_ffffxx'),('set_line_width','_f'),('set_line_cap','_s'))},
    'widget_set_prop_int':'nsi','widget_set_prop_str':'nss','widget_move_resize':'niiii','widget_resize':'nii',
    'widget_set_opacity':'ni','widget_use_style':'ns','widget_set_text_utf8':'ns','widget_set_tr_text':'ns',
    'widget_set_visible':'nii','widget_set_sensitive':'ni','widget_factory_create_widget':'_sniiii',
    **dict.fromkeys(('image_create','list_item_create','button_create','hscroll_label_create','label_create'),'niiii'),
    'image_set_draw_type':'ni','image_base_set_image':'ns',
    'image_manager':'','image_manager_unload_all':'_','image_manager_unload_bitmap_by_name':'_s'}
HOOK_NAMES={a:n for a,n,_ in HOOKS.values()}
PAYLOAD_NAMES={v:n for n,v in sorted(payload_syms.items())}
def render(m,code,v):
    """One traced value; 'w' takes (UTF-32 pointer, length). Addresses never appear, so traces survive a relink."""
    if code=='w': return json.dumps(''.join(chr(m.get(v[0]+4*k)) for k in range(v[1])))
    if code in 'srd' and not v: return 'null'
    if code=='s': return json.dumps(m.text(v))
    if code=='r': return ','.join(str(signed(m.get(v+4*k))) for k in range(4))
    if code=='d': return '%08x'%m.get(v)
    if code=='f': return '%g'%struct.unpack('<f',struct.pack('<I',v))[0]
    if code=='n' and v in m.nodes: return 'n%d'%list(m.nodes).index(v)
    # ponytail: an alpha-0 colour from BASE up to SCRATCH + SCRATCH_MAX reads as an address (P); none is drawn today
    if BASE<=v<SCRATCH+SCRATCH_MAX: return PAYLOAD_NAMES.get(v,'P')
    if 0x70000000<=v<0x70010000: return 'S'
    return str(signed(v)) if code=='i' else '%08x'%v

class Handlers(dict):
    """A machine's mocks, by address: one at an address no symbol names is marked for FILTER."""
    def __setitem__(self,a,name):
        if a not in ENTRIES: watch(a)
        super().__setitem__(a,name)
    def update(self,other):
        for a,name in other.items(): self[a]=name
class Machine:
    def __init__(self, patched=True):
        self.u=Uc(UC_ARCH_MIPS, UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
        data=(B/('demo' if patched else 'stock-demo')).read_bytes()
        load_elf(self.u,data)
        # Payload text is execute-only; its top page holds the scratch cell.
        if patched: self.u.mem_protect(BASE,SCRATCH-BASE,5)
        for v,w in FP64_FIX: self.u.mem_write(v,struct.pack('<I',w))
        self.u.mem_map(0x1000000,0x200000)
        self.u.mem_map(0x70000000,0x10000)
        self.next=0x1001000; self.nodes={}; self.calls=[]; self.animating=0; self.pressed=0; self.ups=[]
        self.top=0; self.wm=0x1000000; self.event=0x1000100
        self.char_px=10.0; self.strokes=[]; self.rounded=[]; self.bands=[]; self.icons=[]; self.letters=[]; self.font=None; self.vg_calls=[]; self.fake_vg=0; self.global_alpha=0
        self.rounded_fail=False; self.count=self.budget  # call()'s instruction limit, which nested() shares
        self.allocs={}; self.config=dict(CONFIG); self.seed=SEED; self.config_reads=[]; self.card=(); self.dirs=set()  # /mnt/mmc's (name, d_type) entries; deeper folders
        for f in SETUP: f(self)
        self.rebind=None; self.on_click=None; self.glide=True
        self.timers={}; self.next_timer=1; self.timer_fail=False; self.clicks=[]; self.started=[]
        self.screens=[]
        self.props={}; self.x_log=[]; self.prop_fail=False; self.slides={}; self.slide_fail=False; self.slide_on_fail=False; self.slide_easing=O['SLIDE_EASING']; self.slide_callbacks={}
        self.canvas=0x1000200; self.lcd=0x1000300; self.now=1000
        self.word(self.canvas+O['CANVAS_LCD'],self.lcd)
        for off,v in zip(('LCD_FILL_COLOR','LCD_STROKE_COLOR'),LCD_COLORS): self.word(self.lcd+O[off],v)
        self.clip=(0,0,240,240)
        self.handlers=Handlers()
        if patched:  # a file and a clock; test/playback.py tests them
            self.handlers[payload_syms['press_stamp']]='stock_press_stamp'
            self.handlers[payload_syms['mute_stamp']]='stock_mute_stamp'
            self.handlers[payload_syms['loop_futex']]='stock_loop_futex'  # the loop's wait and its wake (playback.c): answered 0 at once, so sleep_ms takes the idle
        for name in FUNCTIONS: self.handlers[syms[name]]=name
        # canvas_get_vgcanvas runs stock, down to the mocked lcd_get_vgcanvas, as the rounded wrappers need
        for name in ('slide_menu_item_width','slide_menu_on_scroll_done','canvas_get_vgcanvas',
                     'widget_animator_scroll_set_params','slide_menu_set_value','toolsTimeItoa','on_wm_keyup_fun','table_client_get_virtual_h',
                     'mclGetPlayType','networkGetConnectStatus','wifi_switch',  # Smart Wi-Fi's: stock's, as before it named them
                     'bitmap_get_physical_line_length','bitmap_get_physical_height',  # the flush's: stock's, as before it named them
                     'tk_sha256_init','tk_sha256_hash','tk_sha256_done'):  # Network Update's read-back: stock's own SHA-256
            self.handlers.pop(syms[name],None)
        self.mock('widget_is_instance_of','widget_animator_scroll_create','widget_animator_on',
                  'widget_set_focused','widget_layout_children','event_init','value_set_int')
        if patched:
            for name in ('paint','dispatch','paint_bg'):
                self.handlers[int(manifest['patch_symbols']['stock_'+name+'_trampoline'],16)]='stock_'+name
        self.mock('pthread_create@GLIBC_2.2','pthread_detach@GLIBC_2.0','pthread_self@GLIBC_2.0')  # pthread_self: playback_poll's UI thread
        self.mock('reset_poweroptions_timer','screen_action','enable_fb','usleep@GLIBC_2.0','sprintf@GLIBC_2.0','snprintf@GLIBC_2.0',
                  'airplayGetFlag','playpause_quick_click','time@GLIBC_2.0','localtime@GLIBC_2.0','tzset@GLIBC_2.0',
                  'strlen@GLIBC_2.0','strncpy@GLIBC_2.0','strrchr@GLIBC_2.0','strcasecmp@GLIBC_2.0','strncasecmp@GLIBC_2.0','strncmp@GLIBC_2.0',
                  'strcasestr@GLIBC_2.2',
                  'unlink@GLIBC_2.0','atoi@GLIBC_2.0','strtol@GLIBC_2.0','memcmp@GLIBC_2.0','access@GLIBC_2.0',
                  'open@GLIBC_2.0','fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0',  # the watchdog and the state record
                  'fopen@GLIBC_2.2', 'opendir@GLIBC_2.0', 'readdir@GLIBC_2.0',
                  'closedir@GLIBC_2.0', 'pthread_mutex_lock@GLIBC_2.0',
                  'pthread_mutex_unlock@GLIBC_2.0', 'calloc@GLIBC_2.0', 'realloc@GLIBC_2.0',
                  'strdup@GLIBC_2.0',  # no /proc/mounts: the boot logo's poll sees no card
                  'fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0')
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='scope_free'
        self.resized=[]  # the bytes each realloc kept
        self.sized={}  # a block's size, for realloc to spoil only that
        self.scope_tree={}; self.scope_markers=set(); self.scope_handles={}
        self.scope_files={}; self.scope_open={}  # SCOPE_RULES (and its .tmp): path -> bytes; handle -> [path, writing, bytes]
        self.present=set()  # paths access finds (the UI loop's Spotify poll asks for its login)
        self.stats={}  # paths stat finds, with their mtime (songs on the card: a card app's)
        self.image_size=(50,50)  # what widget_load_image decodes
        self.image_draws=[]; self.image_loads=[]
        self.style_ints={}  # (a style, its name): what style_get_int answers; else the fallback
        self.clock=(18,14)  # local (hour, minute) for time/localtime, or the one of them that fails
        self.clock_minutes=False; self.marquee=False
        self.widget_timers=False  # widget_add_timer schedules (real_hscroll), else it is only recorded
        self.wall=(86400,86400)  # time's result, and the seconds localtime must be given
        self.rockbox_mode=0o100755; self.groovebox_mode=0  # the launchers' binaries: Rockbox on the card, Groovebox not
        self.handlers[syms['__xstat@GLIBC_2.0']]='rockbox_stat'
        self.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
        self.handlers[syms['memcpy@GLIBC_2.0']]='memcpy'
        self.handlers[syms['memset@GLIBC_2.0']]='memset'
        self.mock('canvas_set_global_alpha')
        self.mock('sqrtf@GLIBC_2.0','sinf@GLIBC_2.0','cosf@GLIBC_2.0','acosf@GLIBC_2.0',prefix='float:')
        self.mock(*VG_MOCKS,prefix='vg:')
        self.handlers[payload_syms['peq_lib_localtime_r']]='localtime_r'
        self.code_hook=self.hooked()
        for name in GLOBALS: self.byte(syms[name],0)
        for name,size in CONTEXT_DATA.items(): self.u.mem_write(syms[name],bytes(size))
        self.word(syms['g_class_type'],0xf001)
        self.byte(syms['g_backlight_status'],1)
        self.byte(syms['g_maxvolume'],100)  # as stock boots: no Max Volume limit, so no mark under a volume bar
    def probe_canvas(self, lcd_type=1, vg=0, rect=(10,10,100,40), color=None):
        """Real stock canvas code runs; mock only the services it reaches."""
        for n in ('canvas_fill_rounded_rect','canvas_stroke_rounded_rect','canvas_set_fill_color'):
            del self.handlers[syms[n]]
        self.mock('lcd_get_vgcanvas'); self.mock('tk_calloc','tk_free',prefix='alloc:')
        for off,val in ((0x10,0),(0x14,0),(0x18,239),(0x1c,239)): self.word(self.canvas+off,val)
        self.word(self.lcd+0xd0,lcd_type)
        self.rounded_fail=False; self.fake_vg=vg
        self.rect=self.alloc(16); self.color=self.alloc(4)
        for i,val in enumerate(rect): self.word(self.rect+4*i,val)
        self.word(self.color,FILL if color is None else color)
    def lcd_colors(self): return tuple(self.get(self.lcd+O[off]) for off in ('LCD_FILL_COLOR','LCD_STROKE_COLOR'))
    def x_pushes(self): return self.x_log  # every x animator's (from, to), run or still running
    def mock(self,*names,prefix=''):
        for n in names: self.handlers[syms[n]]=prefix+n
    def record_syscalls(self,fail=lambda page:False):
        """The payload's o32 syscalls, answered 0; mprotect (4125) takes effect, except RWX on a page
        fail(page) picks, answered EACCES."""
        self.syscalls=[]
        def syscall(u,intno,unused):
            n,a,b,c=(u.reg_read(r) for r in (UC_MIPS_REG_V0,*REGS[:3])); self.syscalls.append((n,a,b,c))
            if n==4125 and c==7 and fail(a): u.reg_write(UC_MIPS_REG_A3,1); u.reg_write(UC_MIPS_REG_V0,13); return
            if n==4125: u.mem_protect(a,b,c)
            u.reg_write(UC_MIPS_REG_A3,0); u.reg_write(UC_MIPS_REG_V0,0)
        self.u.hook_add(UC_HOOK_INTR,syscall)
    def scope_scan(self):
        """A library update's marker walk over scope_tree and scope_markers, into SCOPE_RULES."""
        self.call(address=payload_syms['library_scope_scan'],args=(0,0,0,0),gap=0,clear=False)
    def folder(self,path,w=None):
        """Set the browsed folder; with a surface, also scroll it home and repaint."""
        self.u.mem_write(syms['g_folder_path'],path.encode()+b'\0')
        if w: self.word(w+O['SCROLL_Y'],0); self.paint(w)
    def label(self,es,specs):
        """Give each entry None, a text, or a (text, subtitle label) pair."""
        for e,spec in zip(es,specs):
            if spec is None: continue
            text,*sub=spec if isinstance(spec,tuple) else (spec,)
            self.nodes[e]['text']=text
            if sub: self.nodes[e]['children']=[self.node('label',text=sub[0])]
    def panes(self,height=None):
        """Two four-entry scroll panes on album_page; returns (a, b, a entries, b entries)."""
        a=self.node(); b=self.node(); es=[]
        for s in (a,b):
            if height: self.word(s+O['W_H'],height)
            es.append([self.entry(s,i*48) for i in range(4)]); self.nodes[s]['children']=es[-1]
        self.top=self.node('window','album_page',[a,b])
        return a,b,*es
    def byte(self,a,v): self.u.mem_write(a,bytes([v]))
    def word(self,a,v): self.u.mem_write(a,struct.pack('<I',v&0xffffffff))
    def get(self,a): return struct.unpack('<I',self.u.mem_read(a,4))[0]
    def alloc(self,n=0x200): a=self.next; self.next+=(n+3)&~3; return a  # word-aligned, as glibc
    def string(self,s):
        data=s.encode()+b'\0'
        a=self.alloc((len(data)+3)&~3); self.u.mem_write(a,data); return a
    def wide_string(self,s):
        data=(s+'\0').encode('utf-32-le')
        a=self.alloc(len(data)); self.u.mem_write(a,data); return a
    def raw(self,a):
        out=bytearray()
        while a:  # to the page's end at a time: a page is mapped whole, so this faults where a read by the byte would
            page=self.u.mem_read(a,0x1000-(a&0xfff)); end=page.find(b'\0')
            if end>=0: return bytes(out+page[:end])
            out+=page; a+=len(page)
        return bytes(out)
    def text(self,a): return self.raw(a).decode()
    def node(self,t='scroll_view',name='',children=(),visible=1,**kw):
        a=self.alloc(); self.nodes[a]=dict(type=t,name=name,children=list(children),visible=visible,enable=1,**kw)
        self.word(a+O['W_W'],240); self.word(a+O['W_H'],240); self.word(a+O['ROW_HEIGHT'],48)
        self.word(a+(O['VIEW_CONTENT_H'] if t=='scroll_view' else O['TABLE_ROWS']),960 if t=='scroll_view' else 100)
        self.byte(a+O['VIEW_VERTICAL'],1)
        return a
    def entry(self,parent,y=0):
        """A leafless tap target: emitter with one EVT_CLICK item, widget_y offset y, height 48."""
        a=self.node('list_item')
        em=self.alloc(4); it=self.alloc(0x28)
        self.word(a+O['W_EMITTER'],em); self.word(em,it); self.word(it+O['EMIT_TYPE'],O['EVT_CLICK'])
        self.word(a+O['W_PARENT'],parent); self.word(a+O['W_Y'],y); self.word(a+O['W_H'],48)
        return a
    def selected(self,w): return self.nodes[w].get('_ringnav_index',-1)
    def paint(self,w,gap=1000):
        """Stock order: background, then (children and) border; iPod hooks both."""
        self.call(address=HOOKS['widget_on_paint_background'][0],args=(w,self.canvas,0,0),gap=gap); gap=0
        return self.call(address=HOOKS['widget_on_paint_border'][0],args=(w,self.canvas,0,0),gap=gap,clear=False)
    def drawn(self): return self.rounded or self.strokes or self.bands
    def sel(self):
        """The selected row's rectangle, from the outline (inset 1) or the bar's bands."""
        if self.rounded and self.rounded[0]['kind']=='fill' and self.rounded[0]['color']==color_t(0xeeeeec):
            x,y,w,h=self.rounded[0]['rect']
            return (x-O['SEL_INSET'],y,w+2*O['SEL_INSET'],h) if x else (x,y,w,h)  # Minimal's card; a square list row's spans the surface
        ys=[b[1] for b in self.bands]; x=min(b[0] for b in self.bands); w=max(b[2] for b in self.bands)
        if x==O['SEL_INSET']: x,w=0,w+2*O['SEL_INSET']  # Modern's list pill, inset as Minimal's card
        return (x,min(ys),w,max(ys)-min(ys)+1)
        x,y,w,h=self.rounded[0]['rect'] if self.rounded else self.strokes[0][:4]
        return (x-1,y-1,w+2,h+2)
    def touch(self): return self.call(address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'])
    def click(self,w,gap=1000): return self.call(address=HOOKS['widget_dispatch'][0],args=(w,self.event,0,0),event_type=O['EVT_CLICK'],gap=gap)
    def hook(self,u,address,size,_):
        if address==syms['widget_animator_scroll_set_params']:
            assert u.reg_read(UC_MIPS_REG_A0) not in self.slides, 'retarget must pause first'
            self.calls.append(('widget_animator_scroll_set_params',*[u.reg_read(r) for r in REGS[:3]]))
        if CARD_THEME and address==payload_syms['read_all'] and self.config.get('THEME') in ('1','3') and self.text(u.reg_read(UC_MIPS_REG_A0))=='/mnt/data/q2pod-theme':
            p=0x11f0000; u.mem_write(p,CARD_THEME+b'\0'); self.word(u.reg_read(UC_MIPS_REG_A2),len(CARD_THEME))  # not alloc's: a test's own pointers stay where they were
            CARD_READS.append(p); return self.returns(p)
        if address not in self.handlers: return
        name=self.handlers[address]
        if not name.startswith('stock_') and address!=payload_syms['peq_lib_localtime_r']:
            assert u.reg_read(UC_MIPS_REG_T9)==address, (name,'PIC call missing t9')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        n=self.nodes.get(a,{})
        self.calls.append((name,a,b,c))
        if TRACE and name in TRACE_ARGS:
            sp=u.reg_read(UC_MIPS_REG_SP); it=iter([a,b,c,d]+[self.get(sp+16+4*k) for k in range(4)]); out=[]
            for k in TRACE_ARGS[name]:
                v=(next(it),next(it)) if k=='w' else next(it)
                if k!='_': out.append(render(self,k,v))
            trace(name,*out)
        if name=='memcpy': self.u.mem_write(a,bytes(self.u.mem_read(b,c))); ret=a
        elif name=='strncpy@GLIBC_2.0': self.u.mem_write(a,self.raw(b)[:c].ljust(c,b'\0')); ret=a
        elif name=='memset': self.u.mem_write(a,bytes([b&255])*c); ret=a
        elif name=='thread:run': self.nested(c,d); ret=0
        elif name in ('table_row_create', 'list_item_create', 'button_create', 'image_create', 'view_create',
                       'hscroll_label_create', 'gif_image_create', 'label_create'):
            kind = {'gif_image_create': 'gif'}.get(name, name.removesuffix('_create'))
            ret = self.node(kind)
            for off, value in zip((O['W_X'], O['W_Y'], O['W_W'], O['W_H']),
                                  (b, c, d, self.get(u.reg_read(UC_MIPS_REG_SP)+16))):
                self.word(ret+off, value)
            self.word(ret+O['W_PARENT'], a)
            self.nodes[a]['children'].append(ret)
        elif name=='widget_factory_create_widget':
            ret=self.node(self.text(b)); self.word(ret+O['W_PARENT'],c); self.nodes[c]['children'].append(ret)
            for off,value in zip(('W_X','W_Y','W_W','W_H'),(d,self.get(u.reg_read(UC_MIPS_REG_SP)+16),self.get(u.reg_read(UC_MIPS_REG_SP)+20),self.get(u.reg_read(UC_MIPS_REG_SP)+24))): self.word(ret+O[off],value)
        elif name=='widget_set_name': n['name']=self.text(b); ret=0
        elif name=='widget_set_enable': n['enable']=b; ret=0
        elif name=='widget_set_self_layout_params':
            n['self_layout']=tuple(self.text(x) for x in (b,c,d,self.get(u.reg_read(UC_MIPS_REG_SP)+16))); ret=0
        elif name=='widget_on': n.setdefault('handlers',[]).append((b,c,d)); ret=len(n['handlers'])
        elif name=='widget_to_local':
            x,y=signed(self.get(b)),signed(self.get(b+4))
            while a:  # stock's: each one's own offset (widget_get_offset) too, the first one's included
                if self.nodes.get(a,{}).get('type')=='scroll_view': x+=signed(self.get(a+O['SCROLL_X'])); y+=signed(self.get(a+O['SCROLL_Y']))
                x-=signed(self.get(a+O['W_X'])); y-=signed(self.get(a+O['W_Y']))
                a=self.get(a+O['W_PARENT'])
            self.word(b,x); self.word(b+4,y); ret=0
        elif name in ('widget_set_text_utf8','widget_set_tr_text'): n['text']=self.text(b); ret=0  # no language has our keys
        elif name=='image_base_set_image': n['image']=self.text(b); ret=0
        elif name=='widget_use_style': n['style']=self.text(b); ret=0
        elif name=='widget_set_text':
            chars=[]
            while self.get(b+4*len(chars)): chars.append(chr(self.get(b+4*len(chars))))
            n['text']=''.join(chars); ret=0
        elif name=='hscroll_label_cast': ret=a
        elif name=='hscroll_label_set_only_focus': self.byte(a+O['HSCROLL_ONLY_FOCUS'],b); n[name]=b; ret=0
        elif name in ('hscroll_label_start','hscroll_label_stop'):
            # stock's timer (0x5fb7ac) stops an only_focus label that isn't focused on its first tick
            n['scrolling']=name.endswith('start') and not self.u.mem_read(a+O['HSCROLL_ONLY_FOCUS'],1)[0]; ret=0
        elif name.startswith('hscroll_label_set_') or name=='set_hscroll_label_attribute':
            n[name]=b if name!='set_hscroll_label_attribute' else True; ret=0
        elif name=='image_set_draw_type': n['draw_type']=b; ret=a
        elif name=='deque_at': ret=self.row_record
        elif name=='deque_size': ret=1
        elif name in ('tk_snprintf','snprintf@GLIBC_2.0'):
            fmt=self.text(c); values=iter([d]+[self.get(u.reg_read(UC_MIPS_REG_SP)+off) for off in range(16,80,4)])
            params=[]  # %.*s takes a length, then the string (its bytes)
            for star,kind in re.findall(r'%(\.\*)?\d*(?:\.\d+)?([sdxXu])',fmt):  # %.500s: Python's % applies the precision
                if star: n=next(values); params.append(bytes(self.u.mem_read(next(values),n)).split(b'\0')[0].decode())
                else: value=next(values); params.append(self.text(value) if kind=='s' else value if kind in 'xXu' else signed(value))
            result=(re.sub(r'%\.\*s','%s',fmt) % tuple(params)).encode(); self.u.mem_write(a,result[:b-1]+b'\0'); ret=len(result)
        elif name=='atoi@GLIBC_2.0': ret=int(re.match(r'[+-]?\d+',self.text(a))[0]) if re.match(r'[+-]?\d+',self.text(a)) else 0
        elif name=='access@GLIBC_2.0': ret=0 if self.text(a) in self.present else -1
        elif name=='open@GLIBC_2.0': ret=0xffffffff  # no /dev/jz_watchdog here: the watchdog stays off
        elif name=='calloc@GLIBC_2.0':
            ret=0 if getattr(self,'fail_calloc',False) else self.alloc(a*b)
            self.sized[ret]=a*b
        elif name=='realloc@GLIBC_2.0' and getattr(self,'fail_realloc',False): ret=0
        elif name=='realloc@GLIBC_2.0' and not a: ret=self.alloc(b); self.sized[ret]=b  # realloc(NULL): a malloc
        elif name=='realloc@GLIBC_2.0':  # moved, as glibc may, the old block spoilt
            kept=bytes(self.u.mem_read(a,b)); ret=self.alloc(b); self.u.mem_write(ret,kept); self.u.mem_write(a,b'\xff'*min(b,self.sized.get(a,b))); self.resized.append(kept); self.sized[ret]=b
        elif name=='strdup@GLIBC_2.0': ret=self.string(self.text(a))
        elif name=='scope_free': ret=0
        elif name=='opendir@GLIBC_2.0':
            path=self.text(a); ret=self.alloc(4) if path in self.scope_tree else 0
            if ret: self.scope_handles[ret]=list(self.scope_tree[path])
        elif name=='readdir@GLIBC_2.0':
            ret=0
            if self.scope_handles.get(a):
                n,t=self.scope_handles[a].pop(0); ret=self.alloc(268)
                self.byte(ret+10,t); self.u.mem_write(ret+11,n.encode()+b'\0')
        elif name=='closedir@GLIBC_2.0': self.scope_handles.pop(a,None); ret=0
        elif name=='fopen@GLIBC_2.2':  # the marker rules file alone; any other file is missing
            path,mode=self.text(a),self.text(b); ret=0
            if path.startswith(SCOPE_RULES) and (mode=='wb' or path in self.scope_files):
                ret=self.alloc(4); self.scope_open[ret]=[path,mode=='wb',b'']
        elif name=='fwrite@GLIBC_2.0': self.scope_open[d][2]+=bytes(u.mem_read(a,b*c)); ret=c
        elif name=='fread@GLIBC_2.0': data=self.scope_files[self.scope_open[d][0]][:b*c]; u.mem_write(a,data); ret=len(data)//b
        elif name=='fclose@GLIBC_2.2':
            path,writing,data=self.scope_open.pop(a); ret=0
            if writing: self.scope_files[path]=data
        elif name=='rename@GLIBC_2.0':
            ret=0 if self.text(a) in self.scope_files else -1
            if not ret: self.scope_files[self.text(b)]=self.scope_files.pop(self.text(a))
        elif name=='unlink@GLIBC_2.0': self.scope_files.pop(self.text(a),None); ret=0
        elif name=='rockbox_stat':
            assert a==3
            if self.text(b) in self.scope_markers: self.word(c+20,0o100644); ret=0
            elif self.text(b) in ['/mnt/mmc/'+n for n,t in self.card if t==4] or self.text(b) in self.dirs: self.word(c+20,0o40755); ret=0  # a card folder
            elif self.text(b) in self.stats: self.word(c+64,self.stats[self.text(b)]); ret=0  # its mtime: info[16]
            elif self.text(b)=='/mnt/mmc/.groovebox/q2-groovebox': self.word(c+20,self.groovebox_mode); ret=0 if self.groovebox_mode else -1
            elif self.text(b)!='/mnt/mmc/.rockbox/rockbox': ret=-1  # Tidal Downloads' rows: no such files here
            else: self.word(c+20,self.rockbox_mode); ret=0 if self.rockbox_mode else -1
        elif name=='strlen@GLIBC_2.0': ret=len(self.raw(a))  # bytes: stock strips a path's UTF-8 name byte by byte
        elif name=='strrchr@GLIBC_2.0': i=self.text(a).encode().rfind(bytes([b&255])); ret=a+i if i>=0 else 0
        elif name=='memcmp@GLIBC_2.0': x,y=bytes(u.mem_read(a,c)),bytes(u.mem_read(b,c)); ret=(x>y)-(x<y)
        elif name=='strncmp@GLIBC_2.0':
            assert a and b,'strncmp(NULL) faults in libc'
            x,y=(bytes(self.raw(v))[:c] for v in (a,b)); ret=(x>y)-(x<y)
        elif name in ('strcasecmp@GLIBC_2.0','strncasecmp@GLIBC_2.0'):  # ASCII case, as the C locale
            x,y=(bytes(self.raw(v)).lower()[:c if name.startswith('strn') else None] for v in (a,b)); ret=(x>y)-(x<y)
        elif name=='sprintf@GLIBC_2.0':  # stock toolsTimeItoa's "%02d:%02d[:%02d]"
            fmt=self.text(b); values=(c,d,self.get(u.reg_read(UC_MIPS_REG_SP)+16))
            result=(fmt % tuple(signed(v) for v in values[:fmt.count('%')])).encode(); self.u.mem_write(a,result+b'\0'); ret=len(result)
        elif name=='time@GLIBC_2.0':
            ret=-1 if self.clock=='time' else self.wall[0]
            if a and ret!=-1: self.word(a,ret)
        elif name in ('localtime@GLIBC_2.0','localtime_r'):  # struct tm: sec, min, hour, ...
            ret=0
            if hasattr(self,'epoch'): # ResumeMachine's time(): clock.c's clock_back of it
                back=self.epoch
                while back>0x7fffffff: back-=883612800
                self.wall=(self.epoch,back)
            if self.clock!='localtime':
                assert self.get(a)==self.wall[1]; ret=b if name=='localtime_r' else self.alloc(44)
                for i,v in enumerate((59,self.clock[1],self.clock[0])): self.word(ret+4*i,v)
        elif name=='widget_set_children_layout':
            n['children_layout']=self.text(b)
            layout=self.alloc(32)
            self.word(layout+O['CHILDREN_LAYOUT_VTABLE'],O['DEFAULT_LAYOUT_VTABLE'])
            self.u.mem_write(layout+0x14,struct.pack('<HH',1,0))
            for param,off in (('xm','DEFAULT_LAYOUT_X_MARGIN'),('s','DEFAULT_LAYOUT_SPACING')):
                match=re.search(r'\b'+param+r'=(\d+)',self.text(b))
                self.byte(layout+O[off],int(match[1]) if match else 0)
            self.word(a+O['W_CHILDREN_LAYOUT'],layout); ret=0
        elif name=='widget_resize':
            self.word(a+O['W_W'],b); self.word(a+O['W_H'],c); ret=0
        elif name=='widget_vtable_on_layout_children': ret=3
        elif name in ('darray_init','darray_deinit','widget_layout_floating_children','widget_layout_self'): ret=0
        elif name=='widget_get_children_for_layout':
            children=[x for x in n['children'] if self.nodes[x]['visible']]
            values=self.alloc(4*len(children)+4)
            for i,child in enumerate(children): self.word(values+4*i,child)
            self.word(b,len(children)); self.word(b+8,values); ret=0
        elif name=='toolsWriteConfig': self.config[self.text(c)]=self.text(d); ret=0
        elif name=='toolsReadConfig':
            key=self.text(c)
            self.config_reads.append((self.text(a),self.text(b),key,self.text(self.get(u.reg_read(UC_MIPS_REG_SP)+16))))
            value=self.config.get(key,self.seed.get(key,self.config_reads[-1][3]))  # stock copies the default when the key is missing
            self.u.mem_write(d,value.encode()+b'\0'); ret=1 if key in self.config else -1
        elif name=='tk_str_end_with': ret=self.text(a).endswith(self.text(b))
        elif name=='tk_str_start_with': ret=self.raw(a).startswith(self.raw(b))  # bytes, as C: a cut UTF-8 tail is no error
        elif name=='strtol@GLIBC_2.0':  # saturates, as a 32-bit long's does; *endptr past the digits (nptr when none)
            t=re.match(r'\s*[-+]?\d+',self.text(a)); ret=max(-2**31,min(2**31-1,int(t[0]))) if t else 0
            if b: self.word(b,a+(len(t[0]) if t else 0))
        elif name=='strstr@GLIBC_2.0': i=self.text(a).find(self.text(b)); ret=a+i if i>=0 else 0
        elif name=='strcasestr@GLIBC_2.2': i=self.raw(a).lower().find(self.raw(b).lower()); ret=a+i if i>=0 else 0
        elif name=='bitmap_get_line_length': ret=self.get(a+8)
        elif name=='bitmap_lock_buffer_for_write': ret=self.get(a+0x14)  # a test bitmap keeps its pixels' address there
        elif name=='image_manager': ret=0x1000500
        elif name in ('widget_move_resize_ex','widget_move_resize'):
            for off,value in zip(('W_X','W_Y','W_W','W_H'),(b,c,d,self.get(u.reg_read(UC_MIPS_REG_SP)+16))):
                self.word(a+O[off],value)
            ret=0
        elif name=='widget_lookup':
            def lookup(w):
                if self.nodes[w].get('name')==self.text(b): return w
                for child in self.nodes[w]['children']:
                    found=lookup(child) if c else (child if self.nodes[child].get('name')==self.text(b) else 0)
                    if found: return found
                return 0
            ret=lookup(a) if a and (a!=self.wm or a in self.nodes) else 0  # a window manager that is no node has no windows to find (Album View)
        elif name=='widget_lookup_by_type':  # AWTK's: the first child of a type, at any depth if c
            def by_type(w):
                for child in self.nodes[w]['children']:
                    found=child if self.nodes[child].get('type')==self.text(b) else by_type(child) if c else 0
                    if found: return found
                return 0
            ret=by_type(a) if a in self.nodes else 0
        elif name=='scroll_bar_cast': ret=a
        elif name=='scroll_bar_is_mobile': ret=n.get('type')=='scroll_bar_m'
        elif name=='widget_set_opacity': self.byte(a+0x34,b); ret=0
        elif name in ('widget_set_visible','widget_set_visible_only','widget_set_sensitive'):
            n['sensitive' if name=='widget_set_sensitive' else 'visible']=b; ret=0
        elif name=='widget_animator_prop_create':
            ret=0 if self.prop_fail else self.alloc(0x80)
            if ret:
                self.word(ret+4,a); self.word(ret+O['ANIM_DURATION'],b)
                n['animated_ms']=b; n['animated_easing']=d; self.word(ret+0x24,c); n['animated']=self.text(self.get(u.reg_read(UC_MIPS_REG_SP)+16)); assert n['animated'] in ('opacity','x')
        elif name=='widget_animator_prop_set_params':
            assert self.get(u.reg_read(UC_MIPS_REG_SP)+16)==0 and self.get(u.reg_read(UC_MIPS_REG_SP)+20)==0
            if self.nodes[self.get(a+4)].get('animated')=='x':
                self.props[a]=(struct.unpack('<d',struct.pack('<II',c,d))[0],0)
                self.x_log.append((int(self.props[a][0]),0))
            ret=0
        elif name=='window_manager': ret=self.wm
        elif name=='window_manager_get_top_window': ret=self.top
        elif name=='window_manager_is_animating': ret=self.animating
        elif name=='window_manager_get_pointer_pressed': ret=self.pressed
        elif name=='widget_get_visible': ret=n.get('visible',0)
        elif name=='widget_get_type': ret=self.string(n.get('type',''))
        elif name=='widget_get_prop_str':
            # Stock text is VALUE_TYPE_WSTRING; value_str does not convert it to UTF-8.
            # An unnamed widget's name is NULL too, whatever the default: stock answers its value_str.
            ret=0 if self.text(b)=='text' else self.string(n.get(self.text(b),''))
            if self.text(b)=='name' and not n.get('name'): ret=0
            if self.text(b)=='image' and n.get('type')=='image' and not n.get('image'): ret=0  # so is an image's that names none
        elif name=='widget_get_text': ret=self.wide_string(n.get('text',''))
        elif name=='player_playtime_and_length' and hasattr(self,'playtime'): self.word(a,self.playtime); self.word(b,getattr(self,'length',225)); ret=0
        elif name=='widget_get_text_utf8':
            text=n.get('text','').encode()
            self.u.mem_write(b,bytes(c))  # stock's wide-string conversion writes nothing unless all bytes fit
            if len(text)<c: self.u.mem_write(b,text+b'\0')
            ret=0
        elif name in ('widget_get_prop_bool','widget_get_prop_int'): ret=n.get(self.text(b),c)
        elif name=='mclGetLyricSize': ret=getattr(self,'lyric_size',0)
        elif name=='widget_destroy':  # out of its parent's children
            for p in self.nodes.values():
                if a in p.get('children',()): p['children'].remove(a)
            n['destroyed']=True; ret=0
        elif name=='widget_count_children': ret=0 if a==self.wm and a not in self.nodes else len(n.get('children',()))  # a case with no window manager's tree: Auto's turn walks it
        elif name=='widget_get_child': ret=n['children'][b] if b<len(n['children']) else 0
        elif name=='widget_set_prop_int': n[self.text(b)]=signed(c); ret=0
        elif name=='slider_set_value':  # stock dispatches the value change at once (o32: the double in a2:a3)
            n['value']=int(struct.unpack('<d',struct.pack('<II',c,d))[0]); ret=0
            for t,f,ctx in list(n.get('handlers',())):
                if t==O['EVT_VALUE_CHANGED']: self.echoes=getattr(self,'echoes',0)+1; self.nested(f,ctx,self.event,0,0)
        elif name=='widget_set_prop_str': n[self.text(b)]=self.text(c); ret=0
        elif name=='text_selector_count_options': ret=n.get('options',0)
        elif name=='text_selector_set_selected_index': self.word(a+O['SELECTOR_INDEX'],b); ret=0
        elif name=='widget_restack' and getattr(self,'restack',False):  # AWTK clamps the index
            kids=next((v['children'] for v in self.nodes.values() if a in v.get('children',())),None)
            if kids is not None: kids.remove(a); kids.insert(min(b,len(kids)),a)
            ret=0
        elif name=='pointer_event_init':
            self.word(a,b); self.word(a+0x10,c); self.word(a+O['EVENT_X'],d); self.word(a+O['EVENT_Y'],self.get(u.reg_read(UC_MIPS_REG_SP)+16)); ret=a
        elif name=='time_now_ms': ret=self.now & 0xffffffff
        elif name=='timer_add':
            ret=0 if self.timer_fail else self.next_timer
            if ret:
                self.next_timer+=1
                # the bar's clock (clock_due) runs only where a check asks for it (clock_minutes)
                # likewise the selected row's marquee (marq_moved): marquee
                if (a!=payload_syms.get('clock_due') or self.clock_minutes) and (self.marquee or a!=payload_syms.get('marq_moved')): self.timers[ret]=(self.now+c,a,b,c)
        elif name=='widget_add_timer' and self.widget_timers: ret=self.next_timer; self.next_timer+=1; self.timers[ret]=(self.now+c,b,a,c)
        elif name=='timer_remove': self.timers.pop(a,None); ret=0
        elif name=='screen_action': self.screens.append(a); ret=1
        elif name=='player_start': self.started.append((a,b,c,d)); ret=1
        elif name in ('tk_strcmp','strcmp@GLIBC_2.0'): x,y=self.text(a),self.text(b); ret=0 if a and b and x==y else -1 if x<=y else 1
        elif name=='stock_dispatch':
            if self.get(b)==O['EVT_CLICK']:
                self.clicks.append(a)
                if self.on_click: self.on_click(a,b)
            elif self.get(b)==O['EVT_POINTER_UP_BEFORE']-1: self.ups.append(a)
            ret=0
        elif name=='table_client_stop_animator_scroll': self.word(a+O['TABLE_ANIMATOR'],0); ret=0
        elif name=='widget_is_instance_of': ret=1
        elif name=='widget_set_focused': n['focused']=b; ret=0
        elif name=='event_init': self.word(a,b); ret=a
        elif name=='value_set_int': self.word(a,b); ret=a
        elif name=='widget_animator_scroll_create':
            ret=0 if self.slide_fail else self.alloc(0x80)
            if ret:
                self.word(ret+4,a); self.word(ret+O['ANIM_DURATION'],b)
                if self.nodes.get(a,{}).get('type')=='slide_menu': assert c==0 and d==self.slide_easing
        elif name=='widget_animator_on':
            ret=0 if self.slide_on_fail else 1
            if ret: self.slide_callbacks[a]=(c,d)
        elif name=='widget_animator_pause': self.slides.pop(a,None); ret=0
        elif name=='widget_animator_destroy':
            self.slides.pop(a,None); self.slide_callbacks.pop(a,None); ret=0
        elif name=='widget_animator_start':
            if a in self.props and self.nodes[self.get(a+4)].get('animated')=='x':
                origin,goal=self.props[a]
                self.props[a]=(self.now,self.get(a+O['ANIM_DURATION']),self.get(a+4),origin,goal)
            if a in self.slide_callbacks:
                w=self.get(a+4)
                if self.nodes.get(w,{}).get('type')=='slide_menu':
                    assert self.get(a+O['ANIM_ELAPSED'])==self.get(a+O['ANIM_START_TIME'])==0
                    self.slides[a]=(self.now,self.get(a+O['ANIM_DURATION']),w,
                                    signed(self.get(a+0x68)),signed(self.get(a+O['ANIM_X_TO'])))
            ret=0
        elif name=='table_client_set_yoffset':
            assert self.get(a+O['TABLE_ANIMATOR'])==0
            self.word(a+O['TABLE_TOP'],b)
            if self.rebind: self.rebind(a,b)
            ret=0
        elif name=='scroll_view_set_offset':
            assert self.get(a+O['VIEW_ANIMATOR'])==0
            self.word(a+O['SCROLL_X'],b); self.word(a+O['SCROLL_Y'],c)
            ret=0
        elif name=='scroll_view_scroll_to':  # stock's clamps the end to the content (0x603008)
            end=signed(self.get(a+O['VIEW_CONTENT_H']))-signed(self.get(a+O['W_H']))
            self.word(a+O['SCROLL_X'],b); self.word(a+O['SCROLL_Y'],max(0,min(signed(c),end))); ret=0
        elif name=='table_client_scroll_to':
            if self.glide:
                self.word(a+O['TABLE_TOP'],b)
                if self.rebind: self.rebind(a,b)
            else: self.word(a+O['TABLE_ANIMATOR'],0x1234)
            ret=0
        elif name=='canvas_get_clip_rect':
            for j,v in enumerate(self.clip): self.word(b+4*j,v)
            ret=0
        elif name=='canvas_set_clip_rect': self.clip=tuple(signed(self.get(b+4*j)) for j in range(4)); ret=0
        elif name=='canvas_set_fill_color': self.word(self.lcd+O['LCD_FILL_COLOR'],b); ret=0
        elif name=='canvas_set_stroke_color': self.word(self.lcd+O['LCD_STROKE_COLOR'],b); ret=0
        elif name=='canvas_set_global_alpha': self.global_alpha+=1; ret=0
        elif name in ('canvas_fill_rounded_rect','canvas_stroke_rounded_rect'):
            sp=u.reg_read(UC_MIPS_REG_SP)
            # The stock rounded calls set the matching LCD color on their CPU branch; model that
            # stricter side effect so a missing restore fails the state assertions below.
            kind='fill' if name=='canvas_fill_rounded_rect' else 'stroke'
            radius=self.get(sp+16); width=None if kind=='fill' else self.get(sp+20)
            self.rounded.append(dict(kind=kind,rect=tuple(signed(self.get(b+4*j)) for j in range(4)),
                bg=signed(c),color=self.get(d),radius=radius,width=width,clip=self.clip))
            if kind=='stroke' and (self.rounded_fail is True or self.rounded_fail==radius):
                ret=2   # a backend that declines to draw, as the stock no-vgcanvas path does
            else:
                self.word(self.lcd+(O['LCD_FILL_COLOR'] if kind=='fill' else O['LCD_STROKE_COLOR']),self.get(d))
                ret=0
        elif name=='style_get_int': ret=self.style_ints.get((a,self.text(b)),c)
        elif name=='canvas_draw_image': self.image_draws.append(tuple(tuple(signed(self.get(r+4*i)) for i in range(4)) for r in (c,d))); ret=0
        elif name=='widget_load_image':  # list_into is 50x50; image_size None fails the load
            self.image_loads.append(self.text(b)); ret=1 if self.image_size is None or self.text(b) in getattr(self,'image_fail',()) else 0
            if not ret: self.word(c,self.image_size[0]); self.word(c+4,self.image_size[1]); self.u.mem_write(c+8,bytes(16))  # no pixels
        elif name=='canvas_draw_icon': self.icons.append((signed(c),signed(d),self.clip)); ret=0
        elif name=='canvas_set_font': self.font=(self.text(b) if b else 'default',c); ret=0
        elif name=='canvas_set_text_color': self.word(self.lcd+O['LCD_TEXT_COLOR'],b); ret=0
        elif name=='canvas_draw_text_in_rect':
            self.letters.append(dict(text=''.join(chr(self.get(b+4*j)) for j in range(c)),
                rect=tuple(signed(self.get(d+4*j)) for j in range(4)),color=self.get(self.lcd+O['LCD_TEXT_COLOR']),font=self.font,
                align=(self.get(a+O['CANVAS_ALIGN_H']),self.get(a+O['CANVAS_ALIGN_V'])),clip=self.clip)); ret=0
        elif name=='canvas_measure_text': u.reg_write(UC_MIPS_REG_F0,struct.unpack('<I',struct.pack('<f',self.char_px*c))[0]); ret=0
        elif name=='widget_get_canvas': ret=self.canvas
        elif name=='canvas_draw_text':
            self.letters.append(dict(text=''.join(chr(self.get(b+4*j)) for j in range(c)),x=signed(d),
                y=signed(self.get(u.reg_read(UC_MIPS_REG_SP)+16)),color=self.get(self.lcd+O['LCD_TEXT_COLOR']),font=self.font)); ret=0
        elif name=='canvas_fill_rect':
            self.bands.append((signed(b),signed(c),signed(d),signed(self.get(u.reg_read(UC_MIPS_REG_SP)+16)),
                               self.get(self.lcd+O['LCD_FILL_COLOR']),self.clip)); ret=0
        elif name in ('canvas_stroke_rect','lcd_stroke_rect'):
            h=self.get(u.reg_read(UC_MIPS_REG_SP)+16)
            clip=self.clip if name=='canvas_stroke_rect' else (self.get(self.canvas+0x10),self.get(self.canvas+0x14),self.get(self.canvas+0x18)-self.get(self.canvas+0x10)+1,self.get(self.canvas+0x1c)-self.get(self.canvas+0x14)+1)
            self.strokes.append((signed(b),signed(c),signed(d),signed(h),clip,self.get(self.lcd+O['LCD_STROKE_COLOR']))); ret=0
        elif name=='scroll_view_scroll_delta_to':
            n['glide_ms']=d
            if self.glide:
                self.word(a+O['SCROLL_X'],self.get(a+O['SCROLL_X'])+b); self.word(a+O['SCROLL_Y'],self.get(a+O['SCROLL_Y'])+c)
            else: self.word(a+O['VIEW_ANIMATOR'],0x1234)
            ret=0
        elif name=='lcd_get_vgcanvas': ret=self.fake_vg
        elif name.startswith('vg:'):
            self.vg_calls.append((name[3:],signed(a),signed(b)))
            ret=1 if name[3:]=='vgcanvas_save' else 0
        elif name.startswith('float:'):
            x=struct.unpack('<f',struct.pack('<I',u.reg_read(UC_MIPS_REG_F12)))[0]
            fn={'sqrtf@GLIBC_2.0':math.sqrt,'sinf@GLIBC_2.0':math.sin,'cosf@GLIBC_2.0':math.cos,'acosf@GLIBC_2.0':math.acos}[name[6:]]
            ret=struct.unpack('<I',struct.pack('<f',fn(x)))[0]
            u.reg_write(UC_MIPS_REG_F0,ret)
        elif name in ('alloc:tk_calloc','alloc:tk_free'):
            if name=='alloc:tk_calloc':
                size=a*b; p=self.alloc(size+16); self.allocs[p]=size; ret=p
            else:
                self.allocs.pop(a,None); ret=0
        elif name=='stock_tidal_load': self.word(payload_syms['playback_loads'],self.get(payload_syms['playback_loads'])+1); ret=0
        elif name.startswith('one:'): ret=1
        else: ret=0
        self.returns(ret,clobber=True)
    def nested(self,f,*args):
        """Run guest f(*args) from inside a mock, on a stack below the caller's, and keep the caller's registers."""
        regs=[UC_MIPS_REG_PC,*range(UC_MIPS_REG_0,UC_MIPS_REG_31+1)]; saved=[self.u.reg_read(r) for r in regs]
        for r,v in ((UC_MIPS_REG_SP,self.u.reg_read(UC_MIPS_REG_SP)-0x400),(UC_MIPS_REG_RA,0x1000000),(UC_MIPS_REG_T9,f),*zip(REGS,args)):
            self.u.reg_write(r,v)
        # unicorn's count is one per engine: a nested run's limit stands for the rest of the caller's call too
        self.u.emu_start(f,0x1000000,count=self.count); assert self.u.reg_read(UC_MIPS_REG_PC)==0x1000000, 'Instruction limit reached'
        ret=signed(self.u.reg_read(UC_MIPS_REG_V0))
        for r,v in zip(regs,saved): self.u.reg_write(r,v)
        return ret
    def qsort(self,base,n,size,compare):
        pair=self.alloc(2*size)
        def cmp(x,y): self.u.mem_write(pair,x); self.u.mem_write(pair+size,y); return self.nested(compare,pair,pair+size)
        for i,v in enumerate(sorted([bytes(self.u.mem_read(base+size*i,size)) for i in range(n)],key=cmp_to_key(cmp))): self.u.mem_write(base+size*i,v)
    def returns(self,ret,clobber=False):
        """Return ret from a mocked call; clobber caller-saved registers to catch accidental ABI assumptions."""
        if clobber:  # unicorn 2.1.4's private call: its public reg_write_batch builds the arrays anew, at fifteen writes' cost
            assert not self.u._do_reg_write_batch(SPOILED_IDS,SPOILED_VALUES,len(SPOILED))
        self.u.reg_write(UC_MIPS_REG_V0,ret&0xffffffff)
        self.u.reg_write(UC_MIPS_REG_PC,self.u.reg_read(UC_MIPS_REG_RA))
    def hooked(self,begin=1,end=0):
        """Hook begin..end (everything, as unicorn reads 1..0) with the hook behind FILTER; returns unicorn's handle."""
        hook=self.hook; python=uccallback(self.u,HOOK_CODE_CFUNC)(lambda u,address,size,_: hook(u,address,size,None))
        handle=uc_hook_h()
        assert not uclib.uc_hook_add(self.u._uch,ctypes.byref(handle),UC_HOOK_CODE,FILTER.filter,ctypes.cast(python,ctypes.c_void_p),begin,end)
        self.u._callbacks[handle.value]=python  # kept alive as Uc.hook_add's are, where hook_del looks
        return handle.value
    def fast(self):
        """Hook stock code and the payload's trampolines only, so the payload's own loops (the
        Coverflow renderer) run at native emulator speed."""
        self.u.hook_del(self.code_hook)
        self.hooked(0,BASE-1)
        self.hooked(SCRATCH,0xffffffff)
        for a in list(self.handlers):
            if BASE<=a<SCRATCH: self.u.hook_add(UC_HOOK_CODE,self.hook,begin=a,end=a)
    budget=100000  # instructions per call
    def call(self,key=O['KEY_NEXT'],address=HOOKS['on_wm_keyup_before_fun'][0],args=None,event_type=0x114,gap=1000,stack=(),clear=True,debounce=False,count=None):
        # Independent input steps occur after the stock key debounce timer expires.
        if gap: self.advance(gap,clear=False)
        if not debounce: self.byte(0xa4bbf8,0)  # stock key filter latch
        if clear: self.calls=[]; self.strokes=[]; self.rounded=[]; self.bands=[]; self.icons=[]; self.letters=[]; self.vg_calls=[]
        self.word(self.event+O['EVENT_KEY'],key)
        self.word(self.event+O['EVENT_TYPE'],event_type)
        self.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
        self.u.reg_write(UC_MIPS_REG_RA,0x1000000)
        self.u.reg_write(UC_MIPS_REG_T9,address)
        for i,v in enumerate(stack): self.word(0x7000f010+4*i,v)
        for r,v in zip(REGS,args or (self.wm,self.event,0,0)): self.u.reg_write(r,v&0xffffffff)
        for i,r in enumerate(SAVED): self.u.reg_write(r,0x12340000+i)
        self.count=count or self.budget
        self.u.emu_start(address,0x1000000,count=self.count)
        assert self.u.reg_read(UC_MIPS_REG_PC)==0x1000000, 'Instruction limit reached'
        assert self.u.reg_read(UC_MIPS_REG_SP)==0x7000f000
        assert [self.u.reg_read(r) for r in SAVED]==[0x12340000+i for i in range(len(SAVED))]
        if TRACE:
            v0=self.u.reg_read(UC_MIPS_REG_V0); name=HOOK_NAMES.get(address) or render(self,'p',address)
            px=[] if name!='ringnav_image_add' or args is None else [hashlib.sha1(bytes(self.u.mem_read(self.get(args[2]+0x14),self.get(args[2]+4)*self.get(args[2]+8)))).hexdigest()[:12]]
            trace('ret',name,render(self,'d' if name=='ringnav_style_color' else 'x',v0),*px)
        return signed(self.u.reg_read(UC_MIPS_REG_V0))
    def advance(self,ms,clear=True):
        """Run due UI timers deterministically, including the exact deadline; RET_REPEAT re-arms."""
        if clear: self.calls=[]
        end=self.now+ms
        while self.timers:
            tid,(due,callback,ctx,period)=min(self.timers.items(),key=lambda item:item[1][0])
            if due>end: break
            self.now=due
            del self.timers[tid]
            info=self.alloc(0x58)
            self.word(info+0x20,ctx); self.word(info+0x28,tid); self.word(info+0x40,period)  # timer_info_t's duration
            ret=self.call(address=callback,args=(info,0,0,0),gap=0,clear=False)
            assert ret in (0,7,8) and (ret!=8 or period>0)  # RET_OK or RET_REMOVE ends it; RET_REPEAT re-arms
            if ret==8 and tid not in self.timers: self.timers[tid]=(due+period,callback,ctx,period)
        self.now=end
        for a,values in list(self.props.items()):
            if len(values)!=5: continue
            start,duration,w,origin,goal=values
            elapsed=min(end-start,duration)
            self.word(w+O['W_X'],round(origin+(goal-origin)*elapsed/duration))
            if elapsed==duration: del self.props[a]
        for a,(start,duration,w,origin,goal) in list(self.slides.items()):
            elapsed=min(end-start,duration)
            self.word(w+O['SLIDE_OFFSET'],round(origin+(goal-origin)*elapsed/duration))
            self.word(a+O['ANIM_ELAPSED'],elapsed)
            if elapsed==duration:
                del self.slides[a]
                callback,ctx=self.slide_callbacks.pop(a)
                assert self.call(address=callback,args=(ctx,0,0,0),gap=0,clear=False)==7
    def confirm(self):
        """Single centre release followed by its full confirmation delay."""
        ret=self.call(O['KEY_CENTER'])
        self.advance(O['DOUBLE_CLICK_MS'],clear=False)
        return ret
    def release(self,gap=0):
        """Execute both the hook and the real stock downstream screen-toggle handler."""
        ret=self.call(O['KEY_CENTER'],gap=gap)
        if ret==0:
            self.call(O['KEY_CENTER'],address=syms['on_wm_keyup_fun'],gap=0,clear=False)
        return ret
    def page(self,name='sysset_page',t='scroll_view'):
        child=self.node(t)
        self.top=self.node('window',name,[child]); self.word(self.wm+O['W_KEY_TARGET'],self.top)  # opened: its keys' target
        if t=='slide_menu':
            self.word(child+O['SLIDE_INDEX'],0)
            self.nodes[child]['children']=[self.entry(child) for _ in range(7)]
            self.word(child+0x5c,self.alloc()) # stock completion checks the child array
        return child
    def page_list(self,n=10,height=96,extent=960,name='sysset_page'):
        """A page holding one scroll view of n 48px entries; returns (surface, entries)."""
        w=self.page(name); self.word(w+O['W_H'],height); self.word(w+O['VIEW_CONTENT_H'],extent)
        es=[self.entry(w,i*48) for i in range(n)]
        self.nodes[w]['children']=es
        return w,es
    def bind(self,rows,offset=0):
        """Reindex a recycled row pool the way the stock table rebind does."""
        for j,r in enumerate(rows):
            self.word(r+O['ROW_INDEX'],offset//48+j); self.word(r+O['W_Y'],(offset//48+j)*48)
    def table_page(self,n=4,name='allmusic_page',rebind=False):
        """A table_client page of n recycled rows, optionally rebound; returns (surface, rows, entries)."""
        w=self.page(name,'table_client')
        self.word(w+O['ROW_HEIGHT'],48); self.word(w+O['TABLE_ROWS'],20); self.word(w+O['W_H'],96)
        rs=[self.node('table_row') for _ in range(n)]
        es=[self.entry(r) for r in rs]; self.nodes[w]['children']=rs
        for r,e in zip(rs,es):
            self.nodes[r]['children']=[e]; self.word(r+O['W_PARENT'],w)
        self.bind(rs)
        if rebind: self.rebind=lambda a,offset: self.bind(rs,offset)
        return w,rs,es
    def list_surface(self,virtual,n=20,rebind=True):
        """A plain list of n 48px rows or a four-row virtual table; optionally rebound."""
        if not virtual:
            w,es=self.page_list(n,extent=n*48)
            return w,None
        w,rs,es=self.table_page(rebind=rebind)
        return w,rs
    def moved(self): return [x for x in self.calls if x[0] in ('scroll_view_set_offset','table_client_set_yoffset','scroll_view_scroll_delta_to','table_client_scroll_to','slide_menu_scroll_to_next','slide_menu_scroll_to_prev','widget_animator_scroll_set_params')]
    def dispatched(self): return [x for x in self.calls if x[0]=='stock_dispatch']

checks=0
def cost_line(name,n): print(f'COST\t{name}\t{n}')  # an instruction count, for test/score.py's P1
def passed(tally=[0]):  # tally counts again, out of a scenario's reach: one that names a variable checks is caught
    global checks
    checks+=1; tally[0]+=1
    assert checks==tally[0],f'a scenario assigned to checks, the count of them: {checks} after {tally[0]} passed'
    trace(f'== {checks}')
def theme_at(m): return m.call(address=payload_syms['theme'],args=(0,0,0,0),gap=0)&0xffffffff
def set_motion(m,kind,ms,easing):  # the active theme's motion row for a widget kind (motion_t, THEME_MOTION_OFF)
    m.u.mem_write(theme_at(m)+THEME_MOTION_OFF+8*(MOTION_WINDOW+MOTION_WIDGET).index(kind)+4,struct.pack('<HB',ms,easing))
def set_hint(m,kind,hint): m.word(theme_at(m)+THEME_MOTION_OFF+8*MOTION_WINDOW.index(kind),m.string(hint))  # a window kind's hint, as a theme with one has
def classic_light(m):  # Classic (THEME=1): its light half unless LIGHT is stored
    m.config['THEME']='1'; m.config.setdefault('LIGHT','1'); return m
def classic_light_new(cls,**kw):  # a Classic cls machine: THEME is stored before its constructor reads it (QueueMachine's does)
    SETUP[:]=[classic_light]
    try: return cls(**kw)
    finally: SETUP.clear()
def classic_light_boot(m,rowart=None,fail=None):  # ... then the first system_bar load writes its set; the names stock loaded
    classic_light(m)  # rowart: the ROWART config value; fail(page): RWX mprotects it picks answer EACCES
    if rowart is not None: m.config['ROWART']=rowart
    m.record_syscalls(fail or (lambda page:False)); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string('system_bar'),0,0,0),gap=0)
    return [m.text(c[1]) for c in m.calls if c[0]=='stock_ui_load']

# Q2_TRACE: draw calls and hook answers by value: widgets as mock node ids, strings by content, payload
# addresses by symbol (else P), stack addresses as S; cmp of two builds' traces survives a layout change.
saved,TRACE=TRACE,io.StringIO()
t=Machine(); w=t.node('label'); rect=t.alloc(16); text=t.alloc(8); paint=payload_syms['ringnav_paint']
for o,v in enumerate((1,2,-3,4)): t.word(rect+4*o,v)
t.word(text,ord('O')); t.word(text+4,ord('K'))
assert [render(t,c,v) for c,v in (('n',w),('n',0x1234),('s',t.string('Songs')),('s',0),('i',0xffffffe2),('x',0xff112233),
        ('r',rect),('r',0),('d',rect),('f',0x3fc00000),('p',paint),('i',paint),('x',SCRATCH+SCRATCH_MAX-1),('x',0x7000eff0),
        ('x',BASE-4),('x',SCRATCH+SCRATCH_MAX))]==['n0','00001234','"Songs"','null','-30','ff112233','1,2,-3,4','null','00000001',
        '1.5','ringnav_paint','ringnav_paint','P','S','%08x'%(BASE-4),'%08x'%(SCRATCH+SCRATCH_MAX)]
for r,v in zip((*REGS,UC_MIPS_REG_SP,UC_MIPS_REG_T9),(t.canvas,text,2,10,0x7000f000,syms['canvas_draw_text'])): t.u.reg_write(r,v)
t.word(0x7000f010,-20); t.hook(t.u,syms['canvas_draw_text'],4,None)
t.rockbox_mode=0; t.call(address=payload_syms['ipod_home_shortcut'],args=(0,0,0,0),gap=0)  # "Favourites", a literal: P
assert TRACE.getvalue().splitlines()==['canvas_draw_text\t"OK"\t10\t-20','ret\tipod_home_shortcut\tP']
COVER=r'(vg:vgcanvas_|canvas_|widget_set_prop|widget_move_resize$|widget_resize$|widget_set_opacity$|widget_use_style$|image_)'
assert {n for n in t.handlers.values() if re.match(COVER,n) and n.removeprefix('vg:') in FUNCTIONS}|{'canvas_set_global_alpha'}<=TRACE_ARGS.keys()
n=checks; TRACE.seek(0); TRACE.truncate(); passed(); assert TRACE.getvalue()==f'== {n+1}\n'
TRACE=saved

GATES=[('g_backlight_status',0),('g_lockscreen_pageflag',1),('g_testmode_flag',1),
       ('g_guideflag',1),('g_poweroff_state',2),('g_usblink_status',2)]

def chord(j,h):
    out=8*h-math.isqrt((h*h-(2*j+1-h)**2)*64)
    return (out+15)//16, out&15

def title_machine(**config):
    CONFIG.update(config); t=Machine(); CONFIG.clear(); t.clock_minutes=True
    label=t.node('hscroll_label','label_clock'); bar=t.node('system_bar','system_bar',[label])
    t.word(bar+O['W_PARENT'],t.wm); t.word(syms['system_bar'],bar)
    def show(top):
        t.top=top; t.call(address=HOOKS['widget_on_paint_background'][0],args=(bar,t.canvas,0,0),gap=0)
        t.wrote=any(c[0]=='widget_set_text' for c in t.calls); t.advance(1)
        return t.nodes[label].get('text')
    def page(title,shown=0,name='scrlabel_title'):
        kids=[t.node('hscroll_label',name,text=title)] if title is not None else []
        w=t.node('window','some_page',[t.node('view','view_navbar',kids,visible=shown)]); t.word(w+O['W_PARENT'],t.wm); return w
    return t,label,show,page

# Execute native row-pool constructors, including the untouched album grid branch.
# The 52px artwork keeps the stock nine-pixel inset in Stock; iPod keeps that same 52px
# artwork at natural size (no rescaling) and gives it an even eight-pixel inset on all four
# sides of the 68px row body. The playing overlay follows.
ARTWORK = {
    0x533bc4: [('img_icon', (4, 9, 52, 52), (0, 8, 52, 52)),
               ('git_playing', (0, 9, 52, 52), (0, 8, 52, 52))],
    0x4ac588: [('img_icon', (48, 0, 50, 70), (48, 0, 52, 68)),
               ('img_gifbg', (0, 9, 50, 52), (0, 8, 50, 52))],
    0x4b39f4: [('img_icon', (48, 0, 50, 70), (48, 0, 52, 68))],
    0x4a6910: [('img_icon', (48, 0, 50, 70), (48, 0, 52, 68)),
               ('img_gifbg', (0, 9, 50, 52), (0, 8, 50, 52))],
}

def native_row_layout(m, button):
    # Run the real stock layout dispatcher and horizontal layouter. Mock only toolkit
    # collection/geometry services, not the width arithmetic or native child positioning.
    m.handlers.pop(syms['widget_layout_children'],None)
    m.mock('widget_vtable_on_layout_children', 'widget_layout_self',
           'widget_layout_floating_children', 'widget_get_children_for_layout',
           'darray_init', 'darray_deinit', 'widget_move_resize_ex')
    for node, props in m.nodes.items():
        children=props['children']
        if children:
            array=m.alloc(12); values=m.alloc(4*len(children))
            for i,child in enumerate(children): m.word(values+4*i,child)
            m.word(array,len(children)); m.word(array+8,values); m.word(node+0x5c,array)
    assert m.call(address=syms['widget_layout_children'],args=(button,0,0,0))==0

def check_title_bounds(m, button, drill=False):
    children=m.nodes[button]['children']
    text=next(c for c in children if m.nodes[c]['type']=='hscroll_label' or any(
        m.nodes[t]['type']=='hscroll_label' for t in m.nodes[c]['children']))
    title=next((c for c in m.nodes[text]['children'] if m.nodes[c]['type']=='hscroll_label'),text)
    layout=m.get(button+O['W_CHILDREN_LAYOUT'])
    vtable=m.get(layout+O['CHILDREN_LAYOUT_VTABLE'])
    stock_vtable=O['DEFAULT_LAYOUT_VTABLE']
    assert vtable!=stock_vtable
    # Clone, destroy, parameter and serialization functions remain native.
    for i in (0,1,3,4,5,6,7): assert m.get(vtable+4*i)==m.get(stock_vtable+4*i)
    margin=m.u.mem_read(layout+O['DEFAULT_LAYOUT_X_MARGIN'],1)[0]
    gap=m.u.mem_read(layout+O['DEFAULT_LAYOUT_SPACING'],1)[0]
    widths={c:m.get(c+O['W_W']) for c in children}
    title_width=m.get(title+O['W_W'])
    title_yh=(m.get(title+O['W_Y']),m.get(title+O['W_H']))
    attributes={k:v for k,v in m.nodes[title].items() if k=='style' or 'hscroll' in k}
    for artwork in (0,1):
        for controls in (0,1):
            for choice in (0,1):
                for c in children:
                    if c==text: continue
                    name=m.nodes[c]['name']
                    m.nodes[c]['visible']=choice if name.startswith('img_choice') else controls if c in children[children.index(text)+1:] else artwork
                # Rebind the same row with different strings, then repeat layout (scroll/reuse).
                for label in ('Short','A very long title '*12,'日本語の長い曲名と歌手 - සිංහල - العربية'):
                    m.nodes[title]['text']=label
                    native_row_layout(m,button)
                    before=children[:children.index(text)]
                    after=children[children.index(text)+1:]
                    left=margin+sum(widths[c]+gap for c in before if m.nodes[c]['visible'])
                    # A drill row lays out as if a stock img_into (CHEVRON_W with the margin) came last.
                    more=any(m.nodes[c]['name'].startswith('img_more') and m.nodes[c]['visible'] for c in children)
                    edge=O['CHEVRON_W']+gap if drill and not more else margin
                    right=m.get(button+O['W_W'])-edge-sum(widths[c]+gap for c in after if m.nodes[c]['visible'])
                    assert m.get(text+O['W_X'])==left
                    # A long title keeps its ellipsis: it scrolls only focused, which no row is.
                    assert m.nodes[title].get('hscroll_label_set_only_focus')==1
                    assert left+m.get(text+O['W_W'])==right
                    if title!=text:
                        assert m.get(title+O['W_X'])+m.get(title+O['W_W'])==m.get(text+O['W_W'])
                    cursor=right+gap
                    for c in after:
                        if m.nodes[c]['visible']:
                            assert m.get(c+O['W_X'])==cursor
                            cursor+=widths[c]+gap
                    assert cursor-gap==m.get(button+O['W_W'])-edge
                    assert (m.get(title+O['W_Y']),m.get(title+O['W_H']))==title_yh
                    assert m.nodes[title]['text']==label
                    assert all(m.nodes[title][k]==v for k,v in attributes.items())
                    for c in children:
                        if c!=text: assert m.get(c+O['W_W'])==widths[c]

from ipod import decode
def asset_node(m,n,parent):
    kind,rect,props,children=n
    w=m.node(kind,props.get('name',''))
    m.word(w+O['W_PARENT'],parent)
    for k,v in zip(('W_X','W_Y','W_W','W_H'),rect): m.word(w+O[k],v)
    m.nodes[w]['children']=[asset_node(m,c,w) for c in children]
    return w

def album_row(boot):
    m=Machine(); boot(m); w=m.page('folder_page','table_client')
    m.mock('table_row_create','button_create','image_create','view_create','hscroll_label_create','gif_image_create',
           'widget_use_style','widget_set_name','widget_set_children_layout','image_set_draw_type','image_base_set_image',
           'set_hscroll_label_attribute','widget_set_visible')
    assert m.call(address=0x4a6910,args=(w,w,1,0))==0
    return m,m.nodes[m.nodes[w]['children'][0]]['children'][0]

# Long Return executes the stock gates and release filter.
def long_machine():
    m = Machine()
    m.mock('netdisk_folder_clear', 'navigator_back_to_home', 'awake_screen',
           'getFormatString', 'navigator_to_with_context', 'navigator_window_is_exist')
    return m

def long_return(m):
    return m.call(170, address=syms['on_wm_keylong_fun'], event_type=0x111, gap=0)

def destinations(m):
    return [c for c in m.calls if c[0] in ('navigator_back_to_home', 'navigator_switch_to_with_context')]

def ui_pass(m, gap=0):
    """One UI loop pass (ringnav_sleep), gap ms on; stock's own sleep is stubbed."""
    m.handlers[HOOKS['main_loop_sleep_default'][0] + 12] = 'stock_sleep'
    assert m.call(address=HOOKS['main_loop_sleep_default'][0], args=(0x1234, 0, 0, 0), gap=gap) == 0

def quits(m): return [c[1:3] for c in m.calls if c[0] == 'dialog_quit']

# The keyboard is a separate top window. Use the input method's owner, not that window's
# focus chain. Run stock input_method_request, mocking only its backend close operation.
class SearchMachine(Machine):
    def __init__(self,name='search_dialog',keyboard=True):
        super().__init__()
        self.dialog=self.node('dialog',name); self.edit=self.node('edit',name='search_edit')
        self.nodes[self.dialog]['children']=[self.edit]; self.word(self.edit+O['W_PARENT'],self.dialog)
        self.nodes[self.edit]['focused']=1
        self.top=self.node('keyboard','keyboard') if keyboard else self.dialog
        self.im=self.alloc(0x90); self.word(self.im+O['IM_WIDGET'],self.edit)
        self.word(self.im+0x7c,0x1000800); self.handlers[0x1000800]='search:close'
        self.handlers.pop(syms['input_method_request'],None)
        self.mock('input_method',prefix='search:')
        self.mock('widget_dispatch_simple_event')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='search:input_method':
            self.calls.append((name,)); self.returns(self.im)
        elif name=='search:close':
            a,b=[u.reg_read(r) for r in REGS[:2]]
            assert a==self.im and b==0
            self.calls.append((name,a,b)); self.word(self.im+O['IM_WIDGET'],0); self.top=self.dialog
            self.returns(0)
        else: super().hook(u,address,size,unused)

def slider_touch(steps,state=3,dialog='statusbar_dialog'):
    m=Machine(); bright=m.node('slider','slider_backlight',value=50)
    view=m.node('view','view_backlight',[bright]); m.top=m.node('dialog',dialog,[view])
    m.word(bright+O['W_PARENT'],view); m.word(view+O['W_PARENT'],m.top)
    m.word(syms['g_dropdown_menu_state'],3); got=[]
    for kind,x,y in steps:
        if kind=='up': m.word(syms['g_dropdown_menu_state'],state)
        m.word(m.event+O['EVENT_Y'],y)
        typ={'down':O['EVT_POINTER_DOWN']-1,'move':O['EVT_POINTER_MOVE_BEFORE']-1,'up':O['EVT_POINTER_UP_BEFORE']-1}[kind]
        ret=m.call(x,address=HOOKS['widget_dispatch'][0],args=(bright,m.event,0,0),event_type=typ,gap=0)  # EVENT_X is EVENT_KEY's word
        got.append((ret,[m.get(c[2]) for c in m.dispatched() if c[1]==bright]))
    return got

from ipod import decode,walk as ui_walk

def time_page(name):
    m=Machine(); names=['tselector_year','tselector_month','tselector_day','tselector_hour','tselector_min']
    if name=='sleepshutdown_page': names=names[3:]
    sels=[m.node('text_selector',n,options=12) for n in names]
    for w in sels: m.word(w+O['SELECTOR_INDEX'],5); m.word(w+O['W_W'],80); m.word(w+O['W_H'],170)
    ok=m.node('button','btn_enter'); slide=m.node('slide_view','slide_view',sels,value=0)
    m.top=m.node('window',name,[slide,ok]); return m,sels,ok,slide

def wiped(m,w):  # a destroyed surface recreated at the same address has no widget-owned props
    for k in [k for k in m.nodes[w] if k.startswith('_ringnav')]: del m.nodes[w][k]
def disturb(m,w,change,n):
    """One interruption of the n-row list w between wheel ticks; returns the live surface."""
    if change=='window': w,_=m.page_list(n,extent=n*48,name='allmusic_page')
    elif change=='pane':
        old=m.top; w,_=m.page_list(n,extent=n*48,name='allmusic_page'); m.nodes[old]['children']=[w]; m.top=old
    elif change=='scope': m.word(syms['g_class_type'],0xf002)
    elif change=='context': m.nodes[m.top]['name']='display_page'
    elif change=='count': m.nodes[w]['children'].pop()
    elif change=='recreated': wiped(m,w)
    elif change in ('gesture','animating'):
        field='pressed' if change=='gesture' else 'animating'
        setattr(m,field,1); assert m.call(gap=10)==11; setattr(m,field,0)
    elif change=='screen':
        m.byte(syms['g_backlight_status'],0); assert m.call(gap=10)==0; m.byte(syms['g_backlight_status'],1)
    elif change=='unsupported':  # the same tick is a volume step on Now Playing
        m.nodes[m.top]['name']='playing_page'; assert m.call(gap=10)==0; m.nodes[m.top]['name']='allmusic_page'
    elif change=='rejected': m.byte(0xa4bbf8,8); assert m.call(gap=10,debounce=True)==11
    elif change=='touch': m.call(address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'],gap=10)
    elif change=='click': m.click(m.node('button'),gap=10)
    elif change=='missing': m.call(args=(0,0,0,0),gap=10)
    elif change=='centre': m.call(O['KEY_CENTER'],gap=10)
    elif change=='return': assert m.call(O['KEY_RETURN'],gap=10)==0
    elif change=='play': assert m.call(O['KEY_PLAY'],gap=10)==0
    elif change=='long': long_return(m)
    elif change=='reverse': assert m.call(O['KEY_PREV'],gap=10)==11 and m.selected(w)==1
    return w

class GridMachine(Machine):
    def __init__(self,**kw):
        super().__init__(**kw); self.handlers[syms['deque_size']]='grid:deque_size'
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='grid:deque_size':
            assert u.reg_read(REGS[0])==self.showlist; return self.returns(self.albums)
        return super().hook(u,address,size,unused)
def album_grid(albums,m=None,grid=1):
    """Albums' page over albums: four rows of three tiles (one in the list form); returns (machine, surface, rows)."""
    if m is None:
        m=GridMachine(); m.showlist=m.alloc(0x20); m.word(syms['p_deque_showlist'],m.showlist); m.word(syms['g_class_type'],0xf003)
    m.albums=albums; m.word(syms['album_modetype'],grid); cells=O['ALBUM_GRID'] if grid else 1
    w,rs,_=m.table_page(name='album_page')
    m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm); m.word(w+O['W_W'],375); m.word(w+O['TABLE_ROWS'],-(-albums//cells))
    for r in rs:
        m.word(r+O['W_W'],375); m.nodes[r]['children']=[m.entry(r) for _ in range(cells)]
        for k,t in enumerate(m.nodes[r]['children'] if grid else ()): m.word(t+O['W_X'],14+118*k); m.word(t+O['W_W'],110)
    def rebind(a,offset):
        m.bind(rs,offset)
        for r in rs:
            for k,t in enumerate(m.nodes[r]['children']): m.nodes[t]['visible']=int(m.get(r+O['ROW_INDEX'])*cells+k<m.albums)
    rebind(w,0); m.rebind=rebind
    return m,w,rs
def album_tile(m,rs,id,cells=3):  # the pool's tile showing album id
    return m.nodes[next(r for r in rs if m.get(r+O['ROW_INDEX'])==id//cells)]['children'][id%cells]

def home_library():
    m=Machine(); classic_light_boot(m)
    w,es=m.page_list(15,height=294,extent=480,name='home_page'); m.nodes[w]['name']='scroll_view_localmusic'
    for i,e in enumerate(es): m.word(e+O['W_Y'],i*32); m.word(e+O['W_H'],32)
    m.paint(w)
    for _ in range(10): assert m.call(gap=200)==11
    assert m.selected(w)==10 and m.get(w+O['SCROLL_Y'])==108
    return m,w,es
def to_songs_and_back(m,w):
    home=m.top; songs,rows,_=m.table_page(); m.paint(songs); m.call(gap=200); m.top=home; m.paint(w)

# Execute stock navigator, Now Playing initialization/key-up, and native window switch/close.
# Resource loading, array storage, paint and animation services remain mocked.
class NavigationMachine(Machine):
    def __init__(self):
        super().__init__()
        self.order=[]; self.block_open=False
        self.word(self.wm+0x94,0x9b42b0)
        for name in ('navigator_switch_to_with_context','navigator_back_to_home','navigator_to_with_context',
                     'navigator_to','window_close'):
            self.handlers.pop(syms[name],None)
        self.mock('widget_child','window_open_and_close','widget_restack','widget_get_window',
                  'widget_is_keyboard','widget_is_dialog','widget_is_window','widget_is_normal_window',
                  'widget_remove_child','widget_destroy','window_manager_dispatch_window_event',
                  'widget_foreach','widget_on','playing_timer_start','access@GLIBC_2.0',
                  'remove@GLIBC_2.0','idle_add','netdisk_folder_clear','awake_screen',prefix='nav:')
        self.rockbox_mode=0o100755; self.groovebox_mode=0  # the launchers' binaries: Rockbox on the card, Groovebox not
        self.handlers[syms['__xstat@GLIBC_2.0']]='rockbox_stat'
        self.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
        for address in (0x53cf08,0x6989cc,0x69bb70,0x699784): self.handlers[address]='nav:'+hex(address)
    def sync_order(self):
        self.top=self.order[-1] if self.order else 0
        arr=self.alloc(12); items=self.alloc(max(4,len(self.order)*4))
        self.word(self.wm+0x5c,arr); self.word(arr,len(self.order)); self.word(arr+8,items)
        for i,w in enumerate(self.order): self.word(items+4*i,w); self.word(w+O['W_PARENT'],self.wm)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('nav:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address
        name=name[4:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        self.calls.append((name,a,b,c))
        if name=='widget_child':ret=next((w for w in self.order if self.nodes[w]['name']==self.text(b)),0)
        elif name=='window_open_and_close':
            if not self.block_open:
                ret=self.node('window',self.text(a),stage=3); self.order.append(ret); self.sync_order()
        elif name=='widget_restack':
            self.order.remove(a); self.order.insert(min(b,len(self.order)),a); self.sync_order()
        elif name=='widget_get_window': ret=a
        elif name in ('widget_is_window','widget_is_normal_window'): ret=int(a in self.order)
        elif name=='widget_remove_child': self.order.remove(b); self.sync_order()
        elif name=='0x69bb70': ret=1  # stock no-window-animation completion branch
        elif name=='0x699784':ret=self.top
        elif name=='access@GLIBC_2.0':ret=-1
        self.returns(ret)

# Pull-to-search observes the page's native before-children callbacks and stock dispatch.
class PullMachine(Machine):
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b,c,d=[u.reg_read(r) for r in REGS]
        if name=='widget_on' and b==getattr(self,'pull_bind_fail',None):
            self.returns(0); return
        if name=='widget_off_by_func':
            self.nodes[a]['handlers']=[h for h in self.nodes[a].get('handlers',[]) if h!=(b,c,d)]
        return super().hook(u,address,size,unused)
    def child(self,page,name):
        def walk(w):
            if self.nodes[w]['name']==name:return w
            for child in self.nodes[w]['children']:
                found=walk(child)
                if found:return found
            return 0
        return walk(page)
    def emit(self,w,typ,x=100,y=90):
        self.word(self.event+O['EVENT_TARGET'],w)
        self.word(self.event+O['EVENT_Y'],y)
        ret=0
        for t,callback,ctx in list(self.nodes[w].get('handlers',[])):
            if t==typ:
                ret=self.call(key=x,address=callback,args=(ctx,self.event,0,0),event_type=typ,gap=0,clear=False)
                if ret==11:break
        return ret
    def start_pull(self,page,w,x=100,y=60):
        self.top=page; self.word(w+O['W_PARENT'],page);self.word(page+O['W_Y'],30)
        self.word(self.event+O['EVENT_Y'],y)
        return self.call(key=x,address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'],gap=0)
    def prompt(self,page):
        w=self.child(page,'_pull_prompt')
        return self.nodes[w] if w else {'visible':0}
    def searches(self):return [c for c in self.calls if c[0]=='stock_search']

# Native before-children dispatch invokes the page callback before it can activate a row.
class PullDispatchMachine(PullMachine):
    def __init__(self):
        super().__init__()
        self.mock('widget_ref','widget_unref','widget_on_pointer_move_children','widget_on_pointer_up_children',
                  'widget_on_event_before_children','widget_vtable_on_pointer_move','widget_vtable_on_pointer_up','emitter_dispatch',prefix='input:')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('input:'):return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address
        a,b=[u.reg_read(r) for r in REGS[:2]];self.calls.append((name,a,b,0))
        if name=='input:emitter_dispatch':
            matches=[(cb,ctx) for typ,cb,ctx in self.nodes[a].get('handlers',[]) if typ==self.get(b)]
            assert len(matches)<=1
            if matches:
                cb,ctx=matches[0];u.reg_write(UC_MIPS_REG_A0,ctx)
                u.reg_write(UC_MIPS_REG_T9,cb);u.reg_write(UC_MIPS_REG_PC,cb);return
        self.returns(0)

class SearchNavigationMachine(PullMachine,NavigationMachine):
    def __init__(self):
        super().__init__()
        self.handlers.pop(syms['stock_search'],None)
        self.handlers.pop(syms['navigator_to'],None)
        self.mock('dialog_search_dialog_init',prefix='nav:'); self.mock('widget_on')

# Play/Pause hold queue menu. libcstl deques are Python lists of element addresses; the stock
# mclLoadPlayList, mclNextSong and key filters run for real.
from build import SHUFFLE_CALL, fileoff
class QueueMachine(Machine):
    budget=120000  # its constructor's paint, a folder list, flips every colour in Modern's Light Mode: about 104k
    def __init__(self,page='allmusic_page',rows=20,queue=3,pos=0,mode=0,cls=0xf001):
        super().__init__()
        self.deqs={}; self.toasts=[]; self.sent=[]; self.picks=[]; self.airplay=0
        self.imports=[]; self.import_ret=1; self.m3u_files=[]; self.uncreated=set(); self.on_toast=None; self.on_open=None
        for n in ('_create_deque','deque_init','deque_init_copy','deque_size','deque_at','_deque_push_back',
                  'deque_assign','deque_clear','deque_destroy','deque_pop_back','send@GLIBC_2.0','window_manager_get_input_device_status',
                  'navigator_to','navigator_to_with_context','window_close','widget_on','widget_destroy_children',
                  'getMusicByAlbum','getMusicByAlbumAndSonger','getMusicByAlbumAndAlbumSonger','toolsLoadDirectory',
                  'getMusicBySonger','getMusicByAlbumArtist','getMusicByComposer','getMusicByGenre',
                  'getMusicByAlbumAndComposer','getMusicByAlbumAndGenre','getMusicByPlayList','checkFavExist','navigator_window_is_exist',
                  'mcl_shuffle_pick','airplayGetFlag','calloc@GLIBC_2.0','qsort@GLIBC_2.0',
                  'importPlayList','toolsGetDirectoryM3u8'): self.handlers[syms[n]]='q:'+n
        self.favs=set(); self.open_pages=set(); self.opened=[]
        self.handlers.pop(syms['mclLoadPlayList'])
        # strstr: an artist page's groupings find ALBUM_GROUP in their SQL (coverflow.c of_names)
        self.mock('strlen@GLIBC_2.0','snprintf@GLIBC_2.0','strstr@GLIBC_2.0'); self.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
        self.mock('mclStartPlayer','mclStop','mclSetPause','mclSetResume','mclSetSeek')
        self.status=self.alloc(0x200); self.word(syms['g_class_type'],cls)
        self.surface,_,_=self.table_page(4,page,rebind=True); self.word(self.surface+O['TABLE_ROWS'],rows)
        self.word(syms['p_deque_showlist'],self.deque([self.song(f'Row {i}') for i in range(rows)]))
        self.word(syms['mcl_pdeqplaylist'],self.deque([self.song(c) for c in 'ABC'[:queue]]))
        self.word(syms['tools_pdeq_directory'],self.deque([self.song('staged')]))
        self.word(O['MCL_POOL'],self.deque(list(range(queue)),'int'))
        for k,v in (('MCL_POS',pos),('MCL_MODE',mode),('MCL_TYPE',cls),('MCL_LASTPOS',-1),('MCL_PRELOAD',0),('MCL_FD',-1)): self.word(O[k],v)
        self.found=[self.song('T1'),self.song('dir',4),self.song('T2')]; self.listings={}; self.stack=[self.top]
        self.folder('/mnt/sd/Music'); self.paint(self.surface)
    def song(self,name,kind=8):
        r=self.alloc(0x60)
        for off,v in (('REC_ID',1),('REC_NAME',self.string(name)),('REC_PATH',self.string('/p/'+name)),
                      ('REC_ALBUM',self.string('Album')),('REC_ARTIST',self.string('Artist')),('REC_TYPE',kind)): self.word(r+O[off],v)
        return r
    def deque(self,items,kind='stSongInfo'):
        h=self.alloc(0x20); self.deqs[h]=[kind,[self.copy(kind,e) for e in items]]; return h
    def copy(self,kind,e):
        a=self.alloc(0x60); self.u.mem_write(a,struct.pack('<I',e&0xffffffff) if kind=='int' else bytes(self.u.mem_read(e,0x60)))
        return a
    def items(self,h): return self.deqs[h][1]
    def row(self,i): return self.items(self.get(syms['p_deque_showlist']))[i]
    def names(self,h=None): return [self.text(self.get(e+O['REC_NAME'])) for e in self.items(h or self.get(syms['mcl_pdeqplaylist']))]
    def pool(self): return [signed(self.get(e)) for e in self.items(self.get(O['MCL_POOL']))]
    def mcl(self,k): return signed(self.get(O[k]))
    def songtable(self,sql,rows,values=False):
        """The rows songs_by_path's or songs_by_id's SQL picks (with values, the statement's own rows), run by SQLite
        over rows as songtable's (path, CUE start and end, album, album artist, and the artist, track and disc an artist
        page's album groupings read; no year), each its index as id, whose order stands for getAllMusic(3)'s time order."""
        text=lambda r,k: self.text(self.get(r+O[k])) if self.get(r+O[k]) else None
        db=sqlite3.connect(':memory:'); db.execute('create table songtable(id,fileurl,time,time_end,time_create,album,albumsonger,songer,track,cd_num,year)')
        db.executemany('insert into songtable values(?,?,?,?,?,?,?,?,?,?,null)',[(i,text(r,'REC_PATH'),self.get(r+O['REC_CUE_START']),
                                                                      self.get(r+O['REC_CUE_END']),-i,text(r,'REC_ALBUM'),text(r,'REC_ALBUM_ARTIST'),
                                                                      text(r,'REC_ARTIST'),self.get(r+O['REC_TRACK']),self.get(r+O['REC_DISC'])) for i,r in enumerate(rows)])
        return db.execute(sql).fetchall() if values else [rows[i] for i,*_ in db.execute(sql)]
    def dialog(self):
        self.view=self.node('scroll_view','scroll_view',[self.node('list_item') for _ in range(5)])
        self.title=self.node('hscroll_label','scrlabel_title'); self.back=self.node('image','img_return')
        return self.node('dialog','sortselect_dialog',[self.node('view','view_navbar',[self.title,self.back]),
                                                       self.node('list_view','list_view',[self.view])])
    def bootvol(self):  # stock's Boot volume page, which crossfade.c re-dresses
        self.slider=self.node('slider','slider_bootvol'); self.vol=self.node('label','label_vol')
        self.dec,self.add=self.node('image','img_dec'),self.node('image','img_add')
        self.title,self.switch=self.node('label','label_title'),self.node('image','img_bootswitch')
        view=self.node('view','view_bootvol',[self.slider,self.dec,self.add,self.vol],visible=0)
        self.nodes[view]['enable']=0  # stock hides and disables it while its switch is off
        return self.node('window','bootvol_page',[self.node('view','view_navbar',[self.title,self.switch]),view])
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('q:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address, name
        name=name[2:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        self.calls.append((name,a,b,c))
        if name=='_create_deque': ret=self.alloc(0x20); self.deqs[ret]=[self.text(a),[]]
        elif name in ('deque_init','deque_clear'): self.deqs[a][1]=[]
        elif name in ('deque_init_copy','deque_assign'): self.deqs[a][1]=[self.copy(self.deqs[a][0],e) for e in self.items(b)]
        elif name=='deque_size': ret=len(self.items(a))
        elif name=='deque_at': ret=self.items(a)[b]
        elif name=='_deque_push_back': self.items(a).append(self.copy(self.deqs[a][0],b))
        elif name=='deque_destroy': del self.deqs[a]
        elif name=='deque_pop_back': self.items(a).pop()
        elif name=='send@GLIBC_2.0': self.sent.append((a,bytes(u.mem_read(b,c)),c,d)); ret=c
        elif name=='window_manager_get_input_device_status': ret=self.status
        elif name=='airplayGetFlag': ret=self.airplay
        elif name=='navigator_to':
            page=self.text(a); self.top=self.dialog() if page=='dialog/sortselect_dialog' else self.bootvol() if page=='playset/bootvol_page' else self.node('window',page)
            self.stack.append(self.top); self.opened.append((page,None))
        elif name=='navigator_to_with_context':
            page=self.text(a)
            if page=='dialog/msginfo_dialog': self.toasts.append((page,self.get(b),self.get(b+4),self.text(b+8)))
            elif page=='playing_page': self.opened.append((page,self.names(self.get(b)),self.get(b+4),self.get(b+8),self.get(b+12)))
            elif page=='localmusic/playlist_page': self.opened.append((page,b))
            elif page=='localmusic/playlistsmore_page': self.opened.append((page,b))
            else: self.opened.append((page,self.get(b),self.get(b+4))); self.top=self.node('window',page); self.stack.append(self.top)
        elif name=='importPlayList':  # Playlists' list reloaded with the files' names, but a create that failed (-1)
            only=self.get(payload_syms['libup']); names=[]  # libup.only: each file's op and name, NUL-ended, an empty one last
            while only and self.text(only): names+=[self.text(only)[1:]]*(self.text(only)[0]=='i'); only+=len(self.raw(only))+1
            self.imports.append(''.join(f'/mnt/mmc/_explaylist_data/{n}\n' for n in names) if self.get(payload_syms['libup']) else None)
            ret=self.import_ret; names=[n.rsplit('.',1)[0] for n in names]  # addon_m3u_dir's
            if ret!=-2: self.word(syms['p_deque_playlist'],self.deque([self.song(n) for n in names if n not in self.uncreated]))
        elif name=='toolsGetDirectoryM3u8': self.deqs[b][1]=[self.copy('stSongInfo',r) for r in self.m3u_files]; ret=len(self.m3u_files)
        elif name=='checkFavExist': ret=self.text(self.get(a+O['REC_NAME'])) in self.favs
        elif name=='navigator_window_is_exist': ret=self.text(a) in self.open_pages
        elif name=='window_close': self.stack.remove(a); self.top=self.stack[-1]
        elif name=='widget_destroy_children': self.nodes[a]['children']=[]
        elif name=='widget_on':
            self.nodes[a].setdefault('handlers',[]).append((b,c,d)); ret=1
            if b==O['EVT_CLICK']:
                em=self.alloc(4); it=self.alloc(0x28); self.word(a+O['W_EMITTER'],em); self.word(em,it); self.word(it+O['EMIT_TYPE'],b)
        elif name=='mcl_shuffle_pick':  # as stock: back to the previous index once, else forward from it
            self.picks.append(a); last=self.mcl('MCL_LASTPOS'); self.word(O['MCL_LASTPOS'],-1 if not a else self.mcl('MCL_POS'))
            self.word(O['MCL_POS'],last if not a and last!=-1 else len(self.names())-1); ret=1
        elif name=='calloc@GLIBC_2.0': ret=self.alloc(a*b+4)  # the album ratings' table
        elif name=='qsort@GLIBC_2.0': self.qsort(a,b,c,d)  # Albums' sorted lookups, the payload's comparator run nested
        else:  # the album/folder queries fill the staging deque
            self.query=(name,a if name=='getMusicByPlayList' else self.text(a) if a else None,(self.text(b) if b else None) if 'And' in name else None,c)
            found=self.listings.get(self.text(a),self.found) if name=='toolsLoadDirectory' else self.found
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in found]; ret=3
            if name=='getMusicByAlbum' and a:  # songs of that album name
                for e in self.items(self.get(syms['tools_pdeq_directory'])): self.word(e+O['REC_ALBUM'],a)
        self.returns(ret,clobber=True)
    def press(self,t):
        self.word(self.status+O['INPUT_KEYS'],O['KEY_PLAY']); self.word(self.status+O['INPUT_KEYS']+O['INPUT_KEY_TIME'],t)
    def hold(self):
        ret=self.call(O['KEY_PLAY'],address=syms['on_wm_keylong_fun'],event_type=O['EVT_KEY_LONG'],gap=0)
        self.advance(0,clear=False)
        return ret
    def release(self):
        """Hook, then the real stock key-up; AWTK clears the key record afterwards."""
        if self.call(O['KEY_PLAY'],gap=0)==0: self.call(O['KEY_PLAY'],address=syms['on_wm_keyup_fun'],gap=0,clear=False)
        self.u.mem_write(self.status+O['INPUT_KEYS'],bytes(O['INPUT_KEY_SIZE']))
        return sum(c[0]=='playpause_quick_click' for c in self.calls)
    def handler(self,w,kind): return next((f,ctx) for t,f,ctx in self.nodes[w]['handlers'] if t==kind)
    def pick(self,row):
        f,ctx=self.handler(self.nodes[self.view]['children'][row],O['EVT_CLICK'])
        assert self.call(address=f,args=(ctx,self.event,0,0),gap=0)==0
    def run(self,row,steps=0):
        """Wheel down steps rows, hold, pick a menu row and let the deferred action run."""
        for _ in range(steps): self.call()
        n=len(self.toasts); self.t=getattr(self,"t",7000)+1; self.press(self.t); assert self.hold()==11 and self.release()==0
        self.pick(row); self.advance(0)
        return self.toasts[-1][3] if len(self.toasts)>n else None
    def labels(self): return [self.nodes[self.nodes[i]['children'][0]]['text'] for i in self.nodes[self.view]['children']]
    def playback(self): return [c for c in self.calls if c[0] in ('mclStartPlayer','mclStop','mclSetPause','mclSetResume','mclSetSeek','playpause_quick_click')]

# Lookup playback must unwind animated pages synchronously: navigator_back only schedules a
# close, so a stock loop waiting for the top window to change starves the animation forever.
class LookupMachine(QueueMachine):
    def __init__(self, pages, selected=1, lookup=True):
        super().__init__(rows=3)
        self.selected=selected; self.closed=[]; self.backs=0
        self.stack=[self.node('window',name) for name in pages]; self.top=self.stack[-1]
        for w in self.stack: self.word(w+0x10,self.string(self.nodes[w]['name']))
        self.byte(syms['g_songerinfo_flag'],int(lookup)); self.byte(syms['g_navbar_status'],0)
        for name in ('navigator_back','navigator_close'): self.handlers.pop(syms[name],None)
        self.mock('widget_get_window','widget_child','window_manager_back','window_manager_close_window_force',
                  'window_close','navigator_window_is_exist','strstr@GLIBC_2.0','strcpy@GLIBC_2.0',
                  'strtok@GLIBC_2.0','access@GLIBC_2.0','player_load_songlist','table_row_of',prefix='lookup:')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('lookup:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address
        name=name[7:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='widget_get_window': ret=self.top
        elif name=='widget_child': ret=next((w for w in self.stack if self.nodes[w]['name']==self.text(b)),0)
        elif name=='navigator_window_is_exist': ret=int(any(self.nodes[w]['name']==self.text(a) for w in self.stack))
        elif name=='window_manager_back':
            self.backs+=1
            # iPod close animation needs the main loop: no top-window change inside this callback.
        elif name in ('window_manager_close_window_force','window_close'):
            w=b if name=='window_manager_close_window_force' else a
            self.closed.append(self.nodes[w]['name']); self.stack.remove(w); self.top=self.stack[-1]
        elif name=='strstr@GLIBC_2.0':
            i=self.text(a).find(self.text(b)); ret=a+i if i>=0 else 0
        elif name=='strcpy@GLIBC_2.0': self.u.mem_write(a,self.text(b).encode()+b'\0'); ret=a
        elif name=='strtok@GLIBC_2.0':
            if a: self.token=a
            ret=self.token
            s=self.text(ret); i=s.find(self.text(b))
            if i>=0: self.byte(ret+i,0); self.token=ret+i+1
        elif name=='table_row_of': ret=a
        elif name=='player_load_songlist':
            assert self.text(b)==f'Row {self.selected}'
            self.deqs[a][1]=[self.copy('stSongInfo',e) for e in self.items(self.get(syms['p_deque_showlist']))]
            ret=self.selected
        self.returns(ret,clobber=True)

SONG_MENU=['Play Next','Add to Queue','Add to Favourites','Add to Playlist','Go to Album','Go to Artist']

def playing_menu(**kw):
    m=QueueMachine(page='playing_page',pos=1,**kw)
    return m

class PlayingPlaylistMachine(QueueMachine):
    STOCK_PICK=0x4b4840
    def __init__(self):
        super().__init__(page='playing_page',pos=1)
        self.added=[]
        self.handlers[self.STOCK_PICK]='playlist_pick'
    def hook(self,u,address,size,unused):
        if address==self.STOCK_PICK and address in self.handlers:
            self.added.append(self.names()[1])
            self.returns(0); return
        opening=self.handlers.get(address)=='q:navigator_to_with_context' and self.text(u.reg_read(UC_MIPS_REG_A0))=='localmusic/playlist_page'
        super().hook(u,address,size,unused)
        if opening:
            self.playlist_rows=[self.node('button',f'playlist_{i}') for i in range(3)]
            view=self.node('scroll_view',children=self.playlist_rows)
            page=self.node('window','playlist_page',[view]); self.top=page; self.stack.append(page)
            for row in self.playlist_rows:
                em,it=self.alloc(4),self.alloc(0x28)
                self.word(row+O['W_EMITTER'],em); self.word(em,it)
                self.word(it,row); self.word(it+O['EMIT_TYPE'],O['EVT_CLICK']); self.word(it+12,self.STOCK_PICK)

# Execute stock's rebuild and naming callback; only UI/dialog and database services are mocked.
class PlaylistMachine(PlayingPlaylistMachine):
    def __init__(self, count=0, mode=1):
        super().__init__()
        self.mock('button_create','list_item_create','image_create','view_create','hscroll_label_create',
                  'widget_use_style','widget_set_name','widget_set_text_utf8','widget_set_visible',
                  'image_set_draw_type','image_base_set_image','set_hscroll_label_attribute',
                  'hscroll_label_set_only_focus','hscroll_label_set_ellipses','hscroll_label_set_speed',
                  'gif_image_play','batch_get_selectitem','file_is_playing','getFormatString',
                  'getMusicByPlayList','get_albumcover_listsize','mclGetPlayStatus','list_get_img_pic',
                  'toolsTrimLeft','toolsTrimRight','tk_snprintf')
        self.handlers[0x4ad020]='row_toolbar'
        self.handlers[0x4b3f0c]='playlist_load'
        self.handlers[syms['widget_restack']]='picker_restack'
        self.handlers[syms['navigator_to']]='picker_dialog'
        self.word(syms['p_deque_playlist'],self.deque([self.song(f'List {i}') for i in range(count)]))
        # Stock's private playlist add-mode global, loaded through its GOT.
        data=(B/'stock-demo').read_bytes()
        from build import fileoff, GP
        self.mode=struct.unpack_from('<I',data,fileoff(data,GP-0x5810))[0]-0x482c
        assert self.mode==O['PLAYLIST_ADD_MODE']
        self.word(self.mode,mode)
        # playlist_art.c (test/coverflow.py tests it): art ready for the playlists (cd_num) in self.art, each ask kept
        self.art={}; self.asks=[]; self.refreshes=self.stops=0
        for n in ('playlist_art','playlist_art_refresh','playlist_art_stop'):
            if n in payload_syms: self.handlers[payload_syms[n]]='pl:'+n
        self.view=self.node('scroll_view','scroll_view')
        self.top=self.node('window','playlist_page',[self.view])
        self.word(self.view+O['W_PARENT'],self.top); self.word(self.top+O['W_PARENT'],self.wm)
    def rebuild(self):
        assert self.call(address=0x4b5444,args=(self.top,0,0,0),gap=0)==0
        kids=self.nodes[self.view]['children']
        for i,row in enumerate(kids): self.word(row+O['W_Y'],i*48)
        self.word(self.view+O['VIEW_CONTENT_H'],len(kids)*48)
        return kids
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b,c,d=[u.reg_read(r) for r in REGS]
        if name.startswith('pl:'):
            ret=0
            if name=='pl:playlist_art':
                self.asks.append((a,self.text(b),c)); url=self.art.get(a)
                assert self.get(u.reg_read(UC_MIPS_REG_SP)+16)>len(url or '')
                if url: self.u.mem_write(d,url.encode()+b'\0'); ret=1
            elif name=='pl:playlist_art_refresh': self.refreshes+=1
            else: self.stops+=1
            self.returns(ret); return
        if name=='picker_dialog' and self.text(a)!='dialog/addplaylist_dialog':
            self.handlers[address]='q:navigator_to'
            super().hook(u,address,size,unused)
            self.handlers[address]=name
            return
        if name in ('picker_restack','picker_dialog'):
            if name=='picker_restack':
                kids=self.nodes[self.view]['children']; kids.remove(a); kids.insert(b,a); ret=0
            else:
                assert self.text(a)=='dialog/addplaylist_dialog'
                ret=0
            self.returns(ret); return
        if name=='widget_off_by_func':
            self.nodes[a]['handlers']=[h for h in self.nodes[a].get('handlers',[]) if h!=(b,c,d)]
        super().hook(u,address,size,unused)
        if name=='q:widget_on' and b==O['EVT_CLICK']:
            it=self.get(self.get(a+O['W_EMITTER'])); self.word(it,d); self.word(it+12,c)

class ClassicPlaylistMachine(PlaylistMachine):
    REBUILD_DONE=0x4b58d4
    def __init__(self,count=3,mode=0,rowart='0',classic=True):  # classic=False: Minimal's page, its toolbar's own
        if classic: SETUP[:]=[lambda k: classic_light(k).config.update(ROWART=rowart)]  # stored before the first load picks the set
        try: super().__init__(count,mode)
        finally: SETUP.clear()
        if classic: classic_light_boot(self,rowart)
        self.handlers.pop(self.STOCK_PICK); self.handlers.pop(0x4b3f0c)
        self.handlers[syms['widget_restack']]='picker_restack'
        self.mock('batch_get_selectitem','batch_set_selectitem','batch_init_selectrecord','batch_clear_selectrecord','batch_empty_selectrecord',
                  'batch_set_allselect','batch_delete_file','exportPlayList','list_selectbox_visiable','list_selectbox_value','set_allchoice_img',
                  'widget_layout','widget_set_children_layout','load_localclass_list','navbar_show_playgif','hscroll_label_set_only_focus',
                  'getFormatString','gif_image_create','widget_add_timer','widget_set_tr_text','image_animation_play','image_animation_pause','navigator_to_with_context')
        self.nav=self.node('view','view_navbar',visible=0); self.nodes[self.nav]['enable']=0; self.word(self.nav+O['W_PARENT'],self.top)
        self.nodes[self.top]['children'].insert(0,self.nav)
        # Stock's page init binds Return as widget_on(win, EVT_KEY_UP, on_common_keyup, win) at 0x4b6024; the bytes and the
        # GOT entries it loads ($25 widget_on, $6 on_common_keyup) pin this fixture's ctx to what the device runs.
        gp=0x4b5fac+0x584b34
        assert [self.get(0x4b6024+4*i) for i in range(7)]==[0x24050114,0x8fbc0010,0x02003825,0x8f99a7bc,0x8f86a7e0,0x0320f809,0x02002025]
        assert self.get(gp-0x5844)==syms['widget_on'] and self.get(gp-0x5820)==syms['on_common_keyup']==0x4f2394
        self.nodes[self.top].setdefault('handlers',[]).append((O['EVT_KEY_UP'],0x4f2394,self.top))
        self.word(syms['g_class_type'],0xf00b)
        self.marks=[]; self.answer=10; self.confirms=[]; self.exports=[]; self.deletes=[]; self.dialogs=[]; self.mores=[]
        # AWTK's emitter_on puts a handler first, and emitter_dispatch runs from the first: the newest runs first
        self.on_click=lambda w,e:[self.nested(f,ctx,e,0,0) for t,f,ctx in reversed(list(self.nodes[w].get('handlers',()))) if t==O['EVT_CLICK']]
        self.stack=[self.node('window','localmusic_page'),self.top]; self.lists(*[f'List {i}' for i in range(count)])
        self.word(syms['tools_pdeq_directory'],self.deque([self.song(c) for c in 'ABC']))  # a playlist's songs, as stock's query leaves them
    def lists(self,*names):
        records=[self.song(n) for n in names]
        for i,(r,n) in enumerate(zip(records,names)): self.word(r+O['REC_TITLE'],self.string(n)); self.word(r+O['REC_DISC'],10+i)  # cd_num
        self.word(syms['p_deque_playlist'],self.deque(records))
    def rebuild(self):
        assert self.call(address=0x4b5444,args=(self.top,0,0,0),gap=0,clear=False)==0
        self.stack_rows(); return self.nodes[self.view]['children']
    def stack_rows(self):  # stock's list layout: the shown rows in order, each its own height
        y=0
        for row in self.nodes[self.view]['children']:
            if self.nodes[row]['visible']: self.word(row+O['W_Y'],y); y+=self.get(row+O['W_H'])
        self.word(self.view+O['VIEW_CONTENT_H'],y); self.word(self.view+O['W_H'],294); self.word(self.view+O['W_W'],375)
    def rows(self):  # the shown rows' first lines
        return [self.row_text(r) for r in self.nodes[self.view]['children'] if self.nodes[r]['visible']]
    def row_text(self,row):
        button=self.nodes[row]['children'][0]; kids=self.nodes[button]['children']
        info=next((k for k in kids if self.nodes[k]['name']=='view_info'),None)
        return self.nodes[self.nodes[info]['children'][0] if info else kids[0]].get('text')
    def row_button(self,text):
        return next(self.nodes[r]['children'][0] for r in self.nodes[self.view]['children'] if self.row_text(r)==text)
    def navbar(self): return self.u.mem_read(syms['g_navbar_status'],1)[0]
    def bar(self): return self.nodes[self.nodes[self.nav]['children'][0]].get('text')
    def tap(self,button,x=0):
        ev=self.alloc(0x30); self.word(ev,O['EVT_CLICK']); self.word(ev+O['EVENT_X'],x)
        self.on_click(button,ev); self.advance(0,clear=False)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b,c,d=[u.reg_read(r) for r in REGS]
        ret=None
        if name=='widget_set_name': self.word(a+0x10,self.string(self.text(b)))  # stock's row click reads the field itself
        if name=='batch_init_selectrecord': self.marks=[0]*a; ret=0
        elif name=='batch_clear_selectrecord': self.marks=[]; ret=0
        elif name=='batch_empty_selectrecord': ret=int(not any(self.marks))
        elif name=='batch_set_selectitem': self.marks[a]^=1; ret=int(all(self.marks))
        elif name=='batch_get_selectitem': ret=self.marks[a] if a<len(self.marks) else 0
        elif name=='batch_set_allselect': self.marks=[a]*len(self.marks); ret=0
        elif name=='batch_delete_file': self.deletes.append(list(self.marks)); ret=0
        elif name=='exportPlayList': self.exports.append((a,list(self.marks))); ret=0
        elif name=='list_selectbox_visiable' or name=='list_selectbox_value':
            def walk(w):
                for k in self.nodes[w]['children']:
                    nm=self.nodes[k].get('name','') or ''
                    if nm.startswith('img_choice'):
                        if name.endswith('visiable'): self.nodes[k]['visible']=b; self.nodes[k]['image']='unselect'
                        else: self.nodes[k]['image']='mulselect' if b else 'unselect'
                    if nm.startswith('img_more') and name.endswith('visiable'): self.nodes[k]['visible']=int(not b)
                    walk(k)
            walk(a); ret=0
        elif name=='widget_layout': self.stack_rows(); ret=0
        elif name=='getFormatString': self.u.mem_write(b,b'Total %d songs\0'); ret=0
        elif name=='navigator_to_with_context':
            page=self.text(a)
            if page=='dialog/confirminfo_dialog': self.confirms.append(self.get(b)); ret=self.answer
            elif page=='localmusic/playlistsmore_page': self.mores.append(b); ret=0
            else: ret=0
        elif name=='picker_dialog' and self.text(a)=='dialog/addplaylist_dialog':
            self.dialogs.append(self.text(a)); ret=0
        if ret is not None:
            self.calls.append((name,a,b,c)); self.returns(ret); return
        super().hook(u,address,size,unused)
def cpm_wheel(m,key,n=1):
    for _ in range(n): m.call(key); m.stack_rows()
    m.paint(m.view)
def cpm_centre(m):  # Centre on the selected row, the row's click running as stock's dispatch does
    m.confirm(); m.advance(0,clear=False); m.stack_rows(); m.paint(m.view)

LISTS=['List 0','List 1','List 2']

def pl_art_lists(m,n):  # List i at cd_num 10+i, as ClassicPlaylistMachine.lists
    records=[m.song(f'List {i}') for i in range(n)]
    for i,r in enumerate(records): m.word(r+O['REC_TITLE'],m.string(f'List {i}')); m.word(r+O['REC_DISC'],10+i)
    m.word(syms['p_deque_playlist'],m.deque(records))
def pl_art_of(m,button): return next(k for k in m.nodes[button]['children'] if m.nodes[k]['type']=='image' and not (m.nodes[k].get('name') or '').startswith('img_'))
def pl_ready(m,cd): m.call(address=m.get(payload_syms['playlist_art_ready']),args=(cd,0,0,0),gap=0,clear=False)

PL_HINT='htranslate(duration=200)'
def pl_cf(base,count=2,mode=0,on='1',theme=None,**kw):
    class M(base):
        def hook(self,u,address,size,unused):
            if address!=payload_syms['coverflow_playlists']: return super().hook(u,address,size,unused)
            self.handed.append(self.names(u.reg_read(REGS[0]))); self.returns(1)
    if theme: CONFIG.update(THEME=theme)
    try: m=M(count,mode,**kw)
    finally: CONFIG.pop('THEME',None)
    m.handlers[syms['widget_set_prop_str']]='widget_set_prop_str'; m.handed=[]; m.config['PLAYLISTCF']=on
    if base is PlaylistMachine: pl_art_lists(m,count)
    m.nodes[m.top]['anim_hint']=PL_HINT; m.byte(m.top+0x34,0xff)
    return m

def pl_under(m):  # the covers over the page, reordered after they opened; the page
    lists=m.items(m.get(syms['p_deque_playlist'])); m.word(syms['p_deque_playlist'],m.deque(lists[::-1])); m.rebuild()
    page=m.top; m.top=m.node('window','coverflow_page'); m.stack.append(m.top)
    return page

class PlShut(ClassicPlaylistMachine):
    shut=0
    def hook(self,u,address,size,unused):
        if address!=payload_syms['coverflow_playlists_close']: return super().hook(u,address,size,unused)
        self.shut+=1; self.returns(0)

class ScopedQueueMachine(QueueMachine):
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name.startswith('q:getMusic') or name=='q:toolsLoadDirectory':
            scope=self.get(payload_syms['library_music'])
            assert scope==int(name!='q:toolsLoadDirectory')
            found=self.found
            if scope: self.found=found[:1]  # the SQL hook's music-only result, checked against SQLite below
            super().hook(u,address,size,unused)
            self.found=found
            return
        return super().hook(u,address,size,unused)

class KeepsMachine(QueueMachine):
    def __init__(self,**kw):
        super().__init__(**kw); self.handlers[payload_syms['playback_shuffle']]='stock_playback_shuffle'; self.handlers[payload_syms['playback_keeps']]='keeps:'; self.drawn=[]
        self.handlers[payload_syms['playback_np']]='keeps:np'; self.nps=0
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='keeps:': self.drawn.append(u.reg_read(REGS[1])); return self.returns(len(self.drawn)==1)
        if name=='keeps:np': self.nps+=1; return self.returns(0)
        return super().hook(u,address,size,unused)

demo=(B/'demo').read_bytes()

class BtInitMachine(Machine):
    def hook(self,u,addr,size,_):
        if self.handlers.get(addr)=='btctl_create_daemon_thread':
            self.calls.append(('btctl_create_daemon_thread',u.reg_read(REGS[0]),0))
            self.word(u.reg_read(REGS[0]),0x12345678)
            self.returns(self.result,clobber=True)
            return
        super().hook(u,addr,size,_)

class BtLinkMachine(Machine):
    def __init__(self,links,paired=True,connecting=1,saved='AA:AA:AA:AA:AA:AA'):
        super().__init__(); self.links=[list(x) for x in links]; self.paired=paired
        self.u.mem_write(0xa4c0d8,saved.encode()+b'\0')  # BTLINKMAC
        self.word(syms['bt_connectstatus'],connecting); self.handlers[0x4fc180]='device_status'
    def hook(self,u,addr,size,_):
        name=self.handlers.get(addr)
        if name not in ('getBtLinkStatus','getBtLinkSize','getBtLinkItem','addBtLinkItem','updateBtLinkStatus','checkPairDev','device_status'):
            return super().hook(u,addr,size,_)
        assert u.reg_read(UC_MIPS_REG_T9)==addr, (name,'PIC call missing t9')
        a,b,c,d=[u.reg_read(r) for r in REGS]; self.calls.append((name,a,b,c,d)); ret=0
        if name=='getBtLinkStatus': ret=next((st for x,st in self.links if x==self.text(a)),0)
        elif name=='getBtLinkSize': ret=len(self.links)
        elif name=='getBtLinkItem' and 0<=signed(a)<len(self.links):
            self.word(b,self.links[a][1]); self.u.mem_write(c,self.links[a][0].encode()+b'\0')
        elif name=='addBtLinkItem': self.links.append([self.text(a),b])
        elif name=='updateBtLinkStatus':
            for x in self.links:
                if x[0]==self.text(b): x[1]=a; ret=1
        elif name=='checkPairDev': ret=self.paired
        self.returns(ret,clobber=True)
a_mac,b_mac='AA:AA:AA:AA:AA:AA','BB:BB:BB:BB:BB:BB'

# Bluetooth volume runs through the shared hook. Mock only headset I/O
# and stock gain; device_set_volume itself executes its native output routing.
class OutputVolumeMachine(Machine):
    def __init__(self,config=()):
        super().__init__(); self.way=0; self.config.update(config); self.present.add('/mnt/data/config.ini')
        self.byte(syms['g_volume'],20); self.byte(syms['g_maxvolume'],100)
        self.handlers.pop(syms['write_int_config'])
        for name,label in (('mclSetOutputWay','output'),('write_int_config','write_config')):
            self.handlers[HOOKS[name][0]+12]='stock_'+label
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name not in ('mclGetOutputWay','stock_output','stock_write_config'):
            return super().hook(u,address,size,unused)
        a,b,c=[u.reg_read(r) for r in REGS[:3]]
        self.calls.append((name,a,b,c))
        if name=='stock_output': self.way=a; ret=1
        elif name=='stock_write_config': self.config[self.text(c)]=str(a); ret=0
        else: ret=self.way
        self.returns(ret)
    def output(self,way):
        changed=(self.way==1)!=(way==1)
        assert self.call(address=syms['mclSetOutputWay'],args=(way,0,0,0),gap=0)==1
        volume=self.u.mem_read(syms['g_volume'],1)[0]
        assert [c[1:3] for c in self.calls if c[0]=='device_set_volume']==([(volume,1)] if changed else [])
        return volume
    def save(self,volume,section='PLAYSET',key='VOLUME'):
        self.byte(syms['g_volume'],volume)
        return self.call(address=syms['write_int_config'],args=(volume,self.string(section),self.string(key),0),gap=0)

class BtVolumeMachine(Machine):
    def __init__(self,link=True):
        super().__init__()
        if link: self.config['BTVOLLINK']='1'  # Bluetooth's Control Device Volume
        self.absolute=10; self.write_result=0; self.way=1
        self.byte(syms['g_bluetoothflag'],1); self.byte(syms['bt_linkstatus'],1)
        self.word(syms['bt_showcoding'],1); self.byte(syms['g_volume'],20); self.byte(syms['g_maxvolume'],100)
        self.handlers[HOOKS['mclSetBtVol'][0]+12]='stock_btvol'
        self.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
        self.mock('mclSetLocalVol','mclUsbAudioSetVol','notifyVolume','airplaySendVol')
        self.handlers.pop(syms['device_set_volume'],None)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name not in ('btctl_transport_get_volume','btctl_transport_set_volume','mclGetOutputWay'):
            return super().hook(u,address,size,unused)
        a=u.reg_read(REGS[0]); self.calls.append((name,a,0,0))
        if name=='btctl_transport_get_volume': ret=self.absolute
        elif name=='mclGetOutputWay': ret=self.way
        else:
            ret=self.write_result
            if ret==0: self.absolute=a
        self.returns(ret)
    def volume(self,left,right):
        self.call(address=HOOKS['mclSetBtVol'][0],args=(left,right,0,0),gap=0)
        return [(signed(c[1]),signed(c[2])) for c in self.calls if c[0]=='stock_btvol']
    def writes(self): return [c[1] for c in self.calls if c[0]=='btctl_transport_set_volume']
    def poll(self,gap=400):
        self.call(address=HOOKS['main_loop_sleep_default'][0],args=(self.wm,0,0,0),gap=gap)

sleep_hook=HOOKS['main_loop_sleep_default'][0]

class SpecFolderMachine(Machine):
    LISTINGS={'/m/0':(3,1),'/m/1':(5,0),'/m/2':(0,0)}  # url: (toolsLoadDirectory's count, a folder among them)
    TREE={'/mnt/mmc':('/mnt/mmc/A','/mnt/mmc/B'),'/mnt/mmc/A':('/mnt/mmc/A/1','/mnt/mmc/A/2'),'/mnt/mmc/B':('/mnt/mmc/B/1',)}  # the folders in each
    STOCK={'click':'stock','enter':'stock_enter','back':'stock_back','scan':'stock_scan'}
    def __init__(self):
        super().__init__(True); self.listed=[]; self.seen=[]; self.row=self.alloc(0x100)
        # stock's page: the folders above the listed one, its rows, their ticks (the batch record) and what a scan was given
        self.path=[]; self.rows=[]; self.ticks=[]; self.scans=[]; self.deqs={}; self.order=[]
        for n in ('table_row_of','get_specfolderurl','toolsLoadDirectory','toolsCheckTypeDeque','get_specfoldersize','batch_get_selectitem',
                  'batch_set_selectitem','_create_deque','deque_init','_deque_push_back','deque_destroy','player_stop','navigator_back',
                  'navigator_to_with_context','clear_specfoldervariate'): self.handlers[syms[n]]='spec:'+n
        for hook,name in self.STOCK.items(): self.handlers[int(manifest['patch_symbols'][f'stock_specfolder_{hook}_trampoline'],16)]='spec:'+name
        self.handlers[int(manifest['patch_symbols']['stock_cancelchoice_trampoline'],16)]='spec:stock_cancel'
    def list(self): self.rows=list(self.TREE[self.path[-1]]); self.ticks=[0]*len(self.rows); self.byte(syms['g_allselect_flag'],0)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('spec:'): return super().hook(u,address,size,unused)
        name=name[5:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='table_row_of': ret=a and self.row
        elif name=='get_specfolderurl': u.mem_write(b,(self.rows[a] if self.rows else f'/m/{a}').encode()+b'\0')
        elif name=='toolsLoadDirectory': self.listed.append(self.text(a)); ret=self.LISTINGS[self.text(a)][0]
        elif name=='toolsCheckTypeDeque': assert b==4; ret=self.LISTINGS[self.listed[-1]][1]
        elif name=='get_specfoldersize': ret=len(self.rows)
        elif name=='batch_get_selectitem': ret=self.ticks[a]
        elif name=='batch_set_selectitem': self.ticks[a]^=1; ret=int(all(self.ticks))  # stock's: 1 with every row ticked
        elif name=='stock_enter':  # specfolder_enter: -1 lists the card; a folder with none inside is refused, its ticks kept
            folder='/mnt/mmc' if signed(a)<0 else self.rows[a]; ret=1 if folder in self.TREE else -1
            if ret==1: self.path=self.path+[folder] if signed(a)>=0 else [folder]; self.list()
        elif name=='stock_back':  # specfolder_back: 0 from the card's own list, the page left
            self.path.pop(); ret=int(bool(self.path))
            if ret: self.list()
            else: self.rows=[]; self.ticks=[]
        elif name=='stock_cancel': self.ticks=[0]*len(self.ticks); self.order.append(name)
        elif name=='stock_scan': self.order.append(name)
        elif name=='_create_deque': ret=self.alloc(0x20); self.deqs[ret]=[self.text(a),None]
        elif name=='deque_init': self.deqs[a][1]=[]
        elif name=='_deque_push_back': self.deqs[a][1].append(self.text(b))
        elif name=='navigator_to_with_context':
            assert self.order[-1]=='navigator_back'  # the page goes first, as stock's
            self.scans.append((self.text(a),self.deqs[self.get(b)][0],sorted(self.deqs[self.get(b)][1]),[self.get(b+4*i) for i in (1,2,3)]))
        elif name=='deque_destroy': del self.deqs[a]
        elif name=='clear_specfoldervariate': self.path=[]; self.rows=[]; self.ticks=[]
        elif name not in ('player_stop','navigator_back'): self.seen.append((a,self.get(b+O['EVENT_X'])))
        if name in ('player_stop','navigator_back','navigator_to_with_context','deque_destroy','clear_specfoldervariate'): self.order.append(name)
        self.returns(ret)
    def tap(self,index,x,on_row=True):
        ev=self.alloc(0x30); self.word(ev+O['EVENT_X'],x); self.word(ev+O['EVENT_TARGET'],0x1000 if on_row else 0)
        self.word(self.row+O['ROW_INDEX'],index); self.call(address=HOOKS['specfolder_click'][0],args=(0x77,ev,0,0),gap=0); return self.listed,self.seen[-1]
    def enter(self,index=-1): return self.call(address=HOOKS['specfolder_enter'][0],args=(index,0,0,0),gap=0)
    def back(self): return self.call(address=HOOKS['specfolder_back'][0],args=(self.alloc(4),0,0,0),gap=0)
    def tick(self,index): self.ticks[index]^=1  # stock's row click, in its column
    def cancel(self): self.call(address=HOOKS['on_cancelchoice_onclick'][0],args=(0x77,0,0,0),gap=0)
    def scan(self):
        """The Scan button: what the scan was given, or 'stock' when the click was left to stock's."""
        self.order=[]; self.scans=[]; assert self.call(address=HOOKS['specfolder_scan'][0],args=(0x77,0,0,0),gap=0)==0
        if self.order==['stock_scan']: return 'stock'
        assert self.order==['player_stop','navigator_back','navigator_to_with_context','deque_destroy','clear_specfoldervariate'] and not self.deqs, self.order
        (page,kind,folders,rest),=self.scans; assert (page,kind,rest)==('dialog/updatemusic_dialog','char*',[0,1,0]); return folders
    def all(self): return self.get(syms['g_allselect_flag'])&0xff

class SongBindMachine(Machine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='b:format':
            self.formats.append(self.text(u.reg_read(UC_MIPS_REG_A0))); self.u.mem_write(u.reg_read(UC_MIPS_REG_A1),b'Unknown Artist\0'); self.returns(0); return
        return super().hook(u,address,size,unused)

class SortMachine(Machine):
    def __init__(self):
        super().__init__(); self.handlers.pop(syms['toolsTrimLeft']); self.mock('strlen@GLIBC_2.0','snprintf@GLIBC_2.0')
        for n in ('__ctype_b_loc@GLIBC_2.3','__ctype_tolower_loc@GLIBC_2.3','strncmp@GLIBC_2.0','atoi@GLIBC_2.0',
                  'strcasecmp@GLIBC_2.0','strcmp@GLIBC_2.0','strncpy@GLIBC_2.0'): self.handlers[syms[n]]='s:'+n
        b=self.alloc(0x400); t=self.alloc(0x400)  # glibc's tables from -128: the digit class, ASCII lowercase
        for c in range(-128,256):
            self.u.mem_write(b+256+2*c,struct.pack('<H',0x800*(48<=c<58)))
            self.word(t+512+4*c,ord(chr(c).lower()) if 0<=c<128 else c)
        self.word(b,b+256); self.word(t,t+512); self.tables=(b,t)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('s:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c=[u.reg_read(r) for r in REGS[:3]]
        s=lambda x,n=None: bytes(u.mem_read(x,4096)).split(b'\0')[0][:n]
        if name.startswith('__ctype'): ret=self.tables[name=='__ctype_tolower_loc']
        elif name=='atoi': ret=int(re.match(rb'\d*',s(a))[0] or 0)
        elif name=='strncpy': u.mem_write(a,s(b,c).ljust(c,b'\0')); ret=a
        else: x,y=(s(a,c),s(b,c)) if name=='strncmp' else (s(a),s(b)); x,y=(x.lower(),y.lower()) if name=='strcasecmp' else (x,y); ret=(x>y)-(x<y)
        self.returns(ret)

class ClassMachine(QueueMachine):
    tramp=int(manifest['patch_symbols']['stock_localclass_trampoline'],16)
    dbquery=int(manifest['patch_symbols']['stock_dbquery_trampoline'],16)
    split=[]  # Albums' own grouping (split), which toolsQueryDbTable stages
    fetched=0  # library rows the queries staged
    def __init__(self,**kw): super().__init__(**kw); self.word(0xa3a270,0x1000010)  # free's GOT slot, as Coverflow's
    def hook(self,u,address,size,unused):
        if address==syms['toolsQueryDbTable']:
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.split]
            self.fetched+=len(self.split); self.returns(len(self.split)); return
        if address==self.dbquery:  # songs_by_path's SQL, straight to stock's toolsQueryDbTable, over split
            found=self.songtable(self.text(u.reg_read(REGS[1])),self.split); self.fetched+=len(found)
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]+=[self.copy('stSongInfo',e) for e in found]
            self.returns(len(found)); return
        libc={syms['strdup@GLIBC_2.0']:'strdup',0x1000010:'free'}
        if address in libc:  # the untagged rows' titles (ringnav_localclass)
            a=u.reg_read(REGS[0]); self.returns(self.string(self.text(a)) if libc[address]=='strdup' else 0); return
        if address!=self.tramp: return super().hook(u,address,size,unused)
        self.calls.append(('stock_localclass',u.reg_read(REGS[0]),0,0))
        assert self.get(payload_syms['library_music']) == 1
        self.returns(len(self.items(self.get(syms['p_deque_showlist']))))

class FirstMachine(ClassMachine):
    dbquery=int(manifest['patch_symbols']['stock_dbquery_trampoline'],16)
    def __init__(self,untagged,**kw):
        super().__init__(**kw); self.mock('strstr@GLIBC_2.0'); self.read=[]
        self.db=sqlite3.connect(':memory:'); self.db.execute('create table songtable(id,album,songer,albumsonger,composer,style,fileurl)')
        self.db.executemany('insert into songtable values(?,?,?,?,?,?,?)',[(1,'A','B','C','D','E','/mnt/mmc/Music/a.flac'),
            (2,'','','','','','/mnt/mmc/Audiobooks/b.mp3')]+[(3+i,'','','','','',f'/mnt/mmc/Music/{i}.flac') for i in range(untagged)])
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name.startswith('q:getMusicBy') and not u.reg_read(REGS[0]):
            tag={'Album':'album','Songer':'songer','AlbumArtist':'albumsonger','Composer':'composer','Genre':'style'}[name[12:]]
            sql=self.string(f'select * from songtable where {tag}=""')
            self.returns(self.nested(payload_syms['ringnav_library_query'],self.string('/mnt/data/database.db'),sql,0,0)); return
        if address!=self.dbquery: return super().hook(u,address,size,unused)
        rows=self.db.execute(self.text(u.reg_read(REGS[1]))).fetchall(); self.read.append(len(rows))
        self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',self.song(f'S{r[0]}')) for r in rows]
        self.returns(len(rows))

class SongsMachine(ClassMachine):
    def hook(self,u,address,size,unused):
        if address==syms['getAllMusic']:
            self.calls.append(('getAllMusic',u.reg_read(REGS[0]),self.get(payload_syms['library_music'])))
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.found]
            self.returns(len(self.found)); return
        if address==syms['music_nagvation_title']:
            self.calls.append(('title',u.reg_read(REGS[0]),self.names(self.get(syms['p_deque_showlist'])))); self.returns(0); return
        super().hook(u,address,size,unused)

class SongCoversMachine(QueueMachine):
    def __init__(self):
        super().__init__(rows=0)
        self.live=set(); self.result=self.alloc(4)
        self.handlers.pop(syms['albumcoverinfo_init'])
        self.word(0xa3a0e8,0x1000014); self.handlers[0x1000014]='c:malloc'  # lazy-bound, as RolloverMachine's
        self.handlers[0x1000010]='c:free'; self.handlers[syms['strcpy@GLIBC_2.0']]='c:strcpy'
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0','clear_albumcover_task')
        self.word(syms['pdeq_albumcoverlist'],self.deque([],'stAlbumCoverInfo'))
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b=u.reg_read(REGS[0]),u.reg_read(REGS[1])
        cover=name in ('q:_deque_push_back','q:deque_clear') and self.deqs[a][0]=='stAlbumCoverInfo'
        if not cover and not name.startswith('c:'): return super().hook(u,address,size,unused)
        ret=0
        if name=='c:malloc': ret=self.alloc(a); self.live.add(ret)
        elif name=='c:free': assert not a or a in self.live, 'freed twice'; self.live.discard(a)
        elif name=='c:strcpy': u.mem_write(a,self.raw(b)+b'\0'); ret=a
        elif name=='q:_deque_push_back':
            e=self.alloc(12); u.mem_write(e,bytes(12)); self.nested(0x5c639c,e,b,self.result); self.items(a).append(e)
        else:
            for e in self.items(a): self.nested(0x5c5c48,e,self.result)
            self.deqs[a][1]=[]
        self.returns(ret,clobber=True)

class ScopedClassMachine(ClassMachine):
    def hook(self,u,address,size,unused):
        super().hook(u,address,size,unused)
        if address==self.tramp and getattr(self,'failed',False): self.returns(-1)

class PlaylistsMachine(ClassMachine):
    def hook(self,u,address,size,unused):
        super().hook(u,address,size,unused)
        if address==self.tramp: self.returns(18)

class QueryDone(Exception): pass
class LibraryQueryMachine(Machine):
    def __init__(self):
        super().__init__(); self.mock('strstr@GLIBC_2.0'); self.handlers.pop(syms['toolsQueryDbTable'])
        self.db=sqlite3.connect(':memory:')
        self.db.execute('create table songtable(id,album,songer,albumsonger,fileurl,track,cd_num)')
        self.db.executemany('insert into songtable(id,album,songer,albumsonger,fileurl) values(?,?,?,?,?)',[
            (1,'Shared','Shared','Shared','/mnt/mmc/Audiobooks/chapter.mp3'),
            (2,'Shared','Shared','Shared','/mnt/mmc/Music/track.flac'),
            (3,'Book','Narrator','Author','/mnt/mmc/elsewhere/BOOK.M4B'),
            (4,'Folder book','Reader','Writer','/mnt/mmc/AUDIOBOOKS/book.flac'),
            (5,'Podcast','Host','Host','/mnt/mmc/Podcasts/show/episode.mp3'),
            (6,'','','','/mnt/mmc/Audiobooks/untagged.mp3'),
        ])
    tramps={int(manifest['patch_symbols'][f'stock_{x}_trampoline'],16):x for x in ('dbquery','content_query','sorted_query')}
    def hook(self,u,address,size,unused):
        if address in (syms['strcpy@GLIBC_2.0'],syms['sprintf@GLIBC_2.0']):  # getAlbumByAlbumArtist's SQL: %s only
            a,b,*v=[u.reg_read(r) for r in REGS]; v=iter(v)
            u.mem_write(a,re.sub('%s',lambda _:self.text(next(v)),self.text(b)).encode()+b'\0')
            self.returns(a); return
        if address not in self.tramps: return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; self.sql=self.text(b)
        if self.tramps[address]=='content_query': self.content=(c,self.text(d),self.get(u.reg_read(UC_MIPS_REG_SP)+16))
        elif self.tramps[address]=='dbquery': assert self.text(a)=='/mnt/data/database.db' and (c,d)==(123,1)
        self.results=self.db.execute(self.sql).fetchall()
        if self.tramps[address]=='content_query': raise QueryDone  # stock's row handling after it isn't emulated
        self.returns(len(self.results))

class MusicStageMachine(QueueMachine):
    def hook(self,u,address,size,unused):
        if address==syms['getMusicByAlbum']:
            assert self.get(payload_syms['library_music'])==1
        return super().hook(u,address,size,unused)

class MusicLoadMachine(QueueMachine):
    def __init__(self):
        super().__init__(); self.handlers[syms['getAllMusic']]='music_load'
    def hook(self,u,address,size,unused):
        if address!=syms['getAllMusic']: return super().hook(u,address,size,unused)
        assert self.get(payload_syms['library_music'])==1
        self.returns(self.result)

class ArtistMachine(ClassMachine):
    tramp=int(manifest['patch_symbols']['stock_artist_trampoline'],16)
    def hook(self,u,address,size,unused):
        if address==syms['albumcoverinfo_init']:
            covers=[]
            for r in self.items(u.reg_read(REGS[0])):
                c=self.alloc(0x60)
                self.word(c,getattr(self,'cover_flags',0))
                self.word(c+4,self.string(self.text(self.get(r+O['REC_ALBUM']))))
                self.word(c+8,self.get(r+O['REC_PATH'])); covers.append(c)
            self.word(syms['pdeq_albumcoverlist'],self.deque(covers))
            self.returns(0); return
        a,b=u.reg_read(REGS[0]),u.reg_read(REGS[1]); libc={syms[n+'@GLIBC_2.0']:n for n in ('calloc','strdup')}|{0x1000010:'free'}
        if address not in libc: return super().hook(u,address,size,unused)
        self.returns({'calloc':lambda:self.alloc((a*b+7)&~3),'strdup':lambda:self.string(self.text(a)),'free':lambda:0}[libc[address]]())

SLIDE_HINT='htranslate(duration=200)'
class ArtistCoverflow(ArtistMachine):
    def __init__(self,*a,**k):
        super().__init__(*a,**k); self.handlers[syms['widget_set_prop_str']]='widget_set_prop_str'
    def artist_page(self,name):
        self.top=self.node('window',name); self.nodes[self.top]['anim_hint']=SLIDE_HINT; self.byte(self.top+0x34,0xff)
    def veiled(self): return self.u.mem_read(self.top+0x34,1)[0]==0 and self.nodes[self.top]['anim_hint']==''
    def shown(self): return self.u.mem_read(self.top+0x34,1)[0]==0xff and self.nodes[self.top]['anim_hint']==SLIDE_HINT
    def hook(self,u,address,size,unused):
        if address!=payload_syms['coverflow_artist']: return super().hook(u,address,size,unused)
        self.handed.append(len(self.items(u.reg_read(REGS[0])))); self.covers=self.names(u.reg_read(REGS[0])); self.returns(1)

class CoverQueueMachine(ArtistMachine):
    def __init__(self):
        super().__init__(rows=12)
        self.pending=[]
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
    def hook(self,u,address,size,unused):
        if address not in (syms['list_clear'],syms['_list_push_back']):
            return super().hook(u,address,size,unused)
        if address==syms['list_clear']: self.pending.clear()
        else:
            p=u.reg_read(REGS[1])
            self.pending.append(tuple(self.get(p+i) for i in (0,4,8)))
        self.returns(0)

class ScrollEndMachine(CoverQueueMachine):
    def hook(self,u,address,size,unused):
        if address!=syms['toolsGetSysMemFree']: return super().hook(u,address,size,unused)
        self.returns(1<<20)

from ipod import HOME_TEXT_X, HOME_LIST_W, HOME_OURS, HOME_PAGE, SET0, HOME_ROWS, HOME_TOP, decode

home_hook=HOOKS['home_page_init']

def asset_tree(m,path):
    """A built UI asset as mock widgets, the window's parent the window manager, with what the payload
    reads: type, name, visible, enable, geometry and, for a scroll view, its content height and the
    slidable flags as AWTK sets them. scroll_view_create (0x6042fc) leaves both off, the asset's
    xslidable/yslidable props set them (0x604bc4), and a list_view's layout (0x5fd234) clears x and
    sets y only when the list holds a mobile scroll bar."""
    def build(n,parent,parent_kind='',siblings=()):
        kind,g,props,children=n
        a=m.node(kind,props.get('name',''),visible=int(props.get('visible')!='false'))
        m.nodes[a].update(enable=int(props.get('enable')!='false'),asset=props)
        for off,v in zip(('W_X','W_Y','W_W','W_H'),[0,0,375,290] if kind=='window' else g): m.word(a+O[off],v)
        m.word(a+O['W_PARENT'],parent)
        if kind=='scroll_view':
            m.byte(a+O['VIEW_HORIZONTAL'],props.get('xslidable')=='true' and parent_kind!='list_view')
            m.byte(a+O['VIEW_VERTICAL'],props.get('yslidable')=='true' or parent_kind=='list_view' and 'scroll_bar_m' in siblings)
            m.word(a+O['VIEW_CONTENT_H'],max((c[1][1]+c[1][3] for c in children),default=0))
        m.nodes[a]['children']=[build(c,a,kind,[c[0] for c in children]) for c in children]
        return a
    return build(decode((B/'ui'/path).read_bytes()),m.wm)
def named(m,w,name):
    if m.nodes[w]['name']==name: return w
    return next((f for c in m.nodes[w]['children'] if (f:=named(m,c,name))),0)
def click_target(m,w):
    em=m.alloc(4); it=m.alloc(0x28); m.word(w+O['W_EMITTER'],em); m.word(em,it); m.word(it+O['EMIT_TYPE'],O['EVT_CLICK'])
def home_list(m):
    """iPod Home as built (ui/home_page.bin) on top: its scroll view and the rows' tap images. Stock
    binds every image but Coverflow's, which the payload binds."""
    m.top=asset_tree(m,HOME_PAGE)
    imgs=[named(m,m.top,'img_'+HOME_OURS.get(r,r)) for r in HOME_ROWS]
    for img in imgs[:2]+imgs[3:]: click_target(m,img)
    return named(m,m.top,'scroll_view_home'),imgs

HINT='htranslate'  # any non-empty anim_hint
def under_slide(hint=HINT):
    m=Machine(); w,es=m.page_list(3); m.paint(w); assert m.call()==11 and m.selected(w)==1
    m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
    w2,_=m.page_list(3,name='display_page'); m.nodes[m.top]['anim_hint']=hint
    return m,w,w2

def window(m,name,w):
    m.top=m.node('window',name,[w]); m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)

from ipod import STATUS_BAR, corner_inset

from ipod import CLOCK_TEXT, CLOCK_GAP, STATUS_MARGIN, corner_x

def canvas_state(m):
    return (m.lcd_colors(),m.get(m.lcd+O['LCD_TEXT_COLOR']),m.get(m.canvas+O['CANVAS_ALIGN_V']),
            m.get(m.canvas+O['CANVAS_ALIGN_H']),m.clip)
def letter_machine(virtual,n=40,**config):
    m=Machine(); m.config.update(config); m.word(m.canvas+O['CANVAS_ALIGN_V'],2)
    m.word(m.canvas+O['CANVAS_ALIGN_H'],3); m.word(m.lcd+O['LCD_TEXT_COLOR'],0x11223344)
    if virtual:
        w,rs,es=m.table_page(rebind=True); m.word(w+O['TABLE_ROWS'],n)
    else:
        w,es=m.page_list(n,extent=n*48,name='allmusic_page'); rs=es
    for e in es: m.nodes[e]['text']='row'
    m.paint(w,gap=0); return m,w,rs,es
def selected_entry(m,w,rs,es):
    return next(e for r,e in zip(rs,es) if (m.get(r+O['ROW_INDEX']) if r!=e else es.index(e))==m.selected(w))

def hscroll_machine():
    m=Machine(); m.widget_timers=True
    for name in ('hscroll_label_start','hscroll_label_stop','hscroll_label_set_xoffset'): m.handlers.pop(syms[name])
    m.mock('widget_invalidate'); return m
def hscroll_label(m,text_w=300,w=200,speed=0.1,delay=0,lull=2000,loop=1,yoyo=0,only_focus=0):
    """Stock's defaults (0x5fb968) after set_hscroll_label_attribute (speed 0.1, lull 2000); range text_w+4-w."""
    t=m.node('hscroll_label'); m.u.mem_write(t+0x78,bytes(0x38)); m.word(t+O['W_W'],w); m.word(t+O['HSCROLL_TEXT_W'],text_w)
    m.word(t+0x9c,0xffffffff); m.word(t+0x84,5000); m.word(t+0x80,lull); m.word(t+O['HSCROLL_DELAY'],delay)
    m.byte(t+O['HSCROLL_LOOP'],loop); m.byte(t+O['HSCROLL_YOYO'],yoyo); m.byte(t+O['HSCROLL_ONLY_FOCUS'],only_focus)
    m.u.mem_write(t+O['HSCROLL_SPEED'],struct.pack('<f',speed)); return t

def named_near(at):
    """The names stock gives widgets within 0x30 bytes of at: addiu $a1, $a1, a string's low half."""
    words=struct.unpack_from('<25I',demo,fileoff(demo,at-0x30))
    return {demo[o:demo.index(b'\0',o)].decode() for o in (fileoff(demo,0x780000+signed16(w&0xffff)) for w in words if w>>16==0x24a5)}
def signed16(x): return x-0x10000 if x&0x8000 else x

def falls(m,start,stops):
    sp=m.alloc(0x100); m.word(sp+0x10,0xa3aae0); reached=[]
    for r,v in ((UC_MIPS_REG_SP,sp),(UC_MIPS_REG_GP,0xa3aae0),(UC_MIPS_REG_V0,m.alloc(0x40)),(UC_MIPS_REG_S0,7)): m.u.reg_write(r,v)
    def stop(u,address,size,unused):
        if address in stops: reached.append(address); u.emu_stop()
    hook=m.u.hook_add(UC_HOOK_CODE,stop); m.u.emu_start(start,0x1000000,count=40); m.u.hook_del(hook)
    return reached[0],signed(m.u.reg_read(UC_MIPS_REG_S0))

def marquee_list(over=True,only_focus=1):
    m=hscroll_machine(); m.marquee=True; w,es=m.page_list(4,height=192,extent=192,name='folder_page')
    labels=[]
    for e in es:
        t=hscroll_label(m,text_w=300 if over else 150,loop=0,only_focus=only_focus); m.nodes[e]['children']=[t]; m.word(t+O['W_PARENT'],e); labels.append(t)
    m.call(); m.paint(w,gap=0)
    return m,w,labels

WRITERS={'scanAllMusicFile':'stock_scan_all','scanSpecFolder':'stock_scan_folder','deleteMusicFromMusicDb':'stock_delete_song'}
class CoverflowMachine(QueueMachine):
    FREE=0x1000010  # stock free's GOT slot is 0 until lazy binding; give it a stub
    def __init__(self,albums=3,cached=True,**queue):
        super().__init__(rows=4,**queue)
        self.cached=cached; self.missing=False; self.joins=[]; self.threads=[]; self.checks=[]; self.plays=[]; self.homes=0; self.freed=0; self.live={}
        self.cover_files=None  # the cover folder's (name, type) entries; None: every album's cover when cached, as access finds; False: no folder
        for n in ('window_create','widget_factory_create_widget','image_base_set_image','getAllAlbum','list_view_create',
                  'scroll_view_create','navigator_back_to_home','navigator_to_with_context','access@GLIBC_2.0',
                  'calloc@GLIBC_2.0','strdup@GLIBC_2.0','mkdir@GLIBC_2.0','statfs@GLIBC_2.0','pthread_create@GLIBC_2.2',
                  'pthread_join@GLIBC_2.0','qsort@GLIBC_2.0','toolsQueryDbTable'): self.handlers[syms[n]]='c:'+n
        self.word(0xa3a270,self.FREE); self.handlers[self.FREE]='c:free'
        # an artist's groupings' SQL (coverflow.c of_names), straight to stock's query by the payload's jal: no t9
        self.handlers[int(manifest['patch_symbols']['stock_dbquery_trampoline'],16)]='c:dbquery'
        self.handlers[home_hook[0]+12]='stock_home'
        for n,stub in WRITERS.items(): self.handlers[HOOKS[n][0]+12]=stub  # the writers' stock bodies
        self.albums=[self.song('T0') for _ in range(albums)]
        for i,r in enumerate(self.albums): self.word(r+O['REC_ALBUM'],self.string(f'Album {i}'))
        self.found=[self.song('T1'),self.song('T2')]
    def art_names(self):
        if self.cover_files is not None: return list(self.cover_files)
        return [(f'{self.nested(payload_syms["album_key"],r)&0xffffffff:08x}.jpg',8) for r in self.albums] if self.cached else []
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='opendir@GLIBC_2.0' and self.cover_files is not False and self.text(u.reg_read(REGS[0]))=='/mnt/mmc/.coverflow': self.scope_tree['/mnt/mmc/.coverflow']=self.art_names()
        if not name.startswith('c:'): return super().hook(u,address,size,unused)
        assert name=='c:dbquery' or u.reg_read(UC_MIPS_REG_T9)==address, name
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; sp=u.reg_read(UC_MIPS_REG_SP); ret=0
        self.calls.append((name,a,b,c))
        if name in ('window_create','widget_factory_create_widget','list_view_create','scroll_view_create'):
            kind,parent,h={'window_create':('window',0,0),'widget_factory_create_widget':(self.text(b),c,self.get(sp+24))}.get(
                name,(name.removesuffix('_create'),a,self.get(sp+16)))
            ret=self.node(kind)
            if kind=='window': self.top=ret; self.stack.append(ret)
            else: self.nodes[parent]['children'].append(ret); self.word(ret+O['W_PARENT'],parent); self.word(ret+O['W_H'],h)
            if name=='widget_factory_create_widget':  # x in a3; y, w on the stack
                for off,v in (('W_X',d),('W_Y',self.get(sp+16)),('W_W',self.get(sp+20))): self.word(ret+O[off],v)
            if kind=='list_view': self.word(ret+O['W_W'],d)  # x, y and w in a1 to a3
            if kind=='slide_menu': self.word(ret+O['SLIDE_INDEX'],0); self.word(ret+0x5c,self.alloc())
        elif name=='image_base_set_image': self.nodes[a]['image']=self.text(b)
        elif name in ('toolsGetMusicInfo','stock_music_info') and getattr(self,'music_tags',None) is not None:  # a card song's file (through ringnav_music_info)
            t=self.music_tags.get(self.text(c)); self.calls.append(('toolsGetMusicInfo',self.text(c)))
            if t:
                for off,v in zip((0x280,0x100,0,0x580),t[:4]): u.mem_write(b+off,v.encode()+b'\0')
                self.word(b+0x684,t[5]); self.word(b+0x688,t[4])  # disc, track
            ret=int(bool(t))
        elif name=='getAllAlbum':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.albums]; ret=len(self.albums)
        elif name=='navigator_back_to_home': self.homes+=1
        elif name=='navigator_to_with_context':
            if self.text(a)=='playing_page':
                self.play_names=self.names(self.get(b))
                self.play_recs=list(self.items(self.get(b)))  # its records, for their fields
                field=lambda e,k: self.text(self.get(e+O[k])) if self.get(e+O[k]) else ''
                self.play_tags=[(field(e,'REC_TITLE'),field(e,'REC_ARTIST'),field(e,'REC_ALBUM'),field(e,'REC_ALBUM_ARTIST'),
                                 self.get(e+O['REC_TRACK']),self.get(e+O['REC_DISC'])) for e in self.items(self.get(b))]
                self.plays.append((self.text(a),*[signed(self.get(b+4*i)) for i in range(4)])); self.natural=self.get(payload_syms['press_natural'])
                # stock_load: loads its queue at once, as stock's playing_page_init does (the real mclLoadPlayList)
                if getattr(self,'stock_load',0): self.nested(syms['mclLoadPlayList'],self.get(b),self.get(b+4),self.get(b+8),0)
            elif self.text(a)=='dialog/msginfo_dialog':
                self.toasts.append((self.text(a),self.get(b),self.get(b+4),self.text(b+8)))
                if self.on_toast: self.on_toast()  # what a nested poll under the toast's own loop would see
            else:
                self.opened.append((self.text(a),self.get(b),self.get(b+4))); self.top=self.node('window',self.text(a)); self.stack.append(self.top)
                if self.on_open: self.on_open(self.text(a))
        elif name=='access': path=self.text(a); ret=0 if (self.cached if '/mnt/mmc/.coverflow/' in path else not self.missing) else -1
        elif name in ('toolsQueryDbTable','dbquery'):
            rows=self.albums if 'fileurl not like' in self.text(b) else []
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in rows]
            ret=len(rows)
        elif name=='qsort': self.qsort(a,b,c,d)  # sorted by the payload's comparator, run nested
        elif name=='calloc': ret=self.alloc(a*b+4); self.live[ret]=a*b
        elif name=='strdup': ret=self.string(self.text(a))
        elif name=='free':
            self.freed+=a!=0; self.live.pop(a,0)
            if a in getattr(self,'poison',{}): self.u.mem_write(a,b'\0'*self.poison.pop(a))
        elif name=='statfs': self.word(b+4,4096); self.word(b+28,1<<20)
        elif name=='pthread_create':  # the release notice's daily check (netupdate_poll) apart: tests count a page's threads
            self.word(a,77); (self.checks if c==payload_syms.get('nc_worker') else self.threads).append((c,d))
        elif name=='pthread_join': self.joins.append(a)
        self.returns(ret,clobber=True)
    def open(self):
        """Home with the card at index 2: centre confirms its image, whose click opens Coverflow."""
        if self.config.get('THEME')=='1': self.slide_easing=0  # Classic's follow is set after the create, which takes linear
        view,self.imgs=home_list(self); self.img=self.imgs[2]; self.home=self.top; self.stack=[self.top]
        self.art=named(self,self.top,'img_homeart'); self.list=named(self,self.top,'list_view_home')
        assert self.call(address=home_hook[0],args=(self.top,0,0,0),gap=0)==0
        self.paint(view); self.nodes[view]['_ringnav_index']=2
        self.clicks=[]; assert self.confirm()==11 and self.clicks==[self.img]
        f,ctx=self.handler(self.img,O['EVT_CLICK']); assert self.call(address=f,args=(ctx,self.event,0,0),gap=0)==0
        self.page=self.top; self.slide=self.find('slide_menu')
        return self.page
    def find(self,kind,w=None):
        w=w or self.page
        if self.nodes[w]['type']==kind: return w
        return next((f for c in self.nodes[w]['children'] if (f:=self.find(kind,c))),0)
    def texts(self,kind='hscroll_label'): return [self.nodes[w].get('text') for w in self.nodes if self.nodes[w]['type']==kind and self.alive(w)]
    def tracks(self,album=0):
        f,ctx=self.handler(self.nodes[self.slide]['children'][album],O['EVT_CLICK'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0); self.advance(0)
        view=self.find('scroll_view'); self.paint(view)
        return view
    def rescan(self,writer='scanAllMusicFile'):
        """A songtable writer runs: its hook, then its (mocked) stock body. A scan walks the folder
        markers into SCOPE_RULES first and indexes songtable after it, a delete doesn't."""
        assert self.call(address=HOOKS[writer][0],args=(1,2,0,0),gap=0)==0
        assert [c[:3] for c in self.calls if c[0].startswith('stock_')]==[(WRITERS[writer],1,2)]
        walked=[c for c in self.calls if c[0].startswith('fopen') and self.text(c[1])==SCOPE_RULES+'.tmp']
        assert bool(walked)==(writer!='deleteMusicFromMusicDb')
        body=[c[0] for c in self.calls].index(WRITERS[writer])
        indexed=[self.text(c[1]) for c in self.calls if c[0]=='toolsUpdateDbTable']
        assert indexed==[self.text(c[1]) for c in self.calls[body:] if c[0]=='toolsUpdateDbTable']
        assert indexed==([] if writer=='deleteMusicFromMusicDb' else ['/mnt/data/database.db'])
    def queried(self): return sum(c[0]=='getAllAlbum' for c in self.calls)
    def close(self):
        f,ctx=self.handler(self.page,O['EVT_DESTROY'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0)
    def alive(self,w):
        """Still attached below the page (the destroy_children mock only unlinks)."""
        while w!=self.page:
            parent=self.get(w+O['W_PARENT'])
            if parent not in self.nodes or w not in self.nodes[parent]['children']: return False
            w=parent
        return True
    def key(self,k=O['KEY_RETURN'],run=True):
        f,ctx=self.handler(self.page,O['EVT_KEY_UP']); ev=self.alloc(0x40); self.word(ev,O['EVT_KEY_UP']); self.word(ev+O['EVENT_KEY'],k)
        ret=self.call(address=f,args=(ctx,ev,0,0),gap=0)
        if run: self.advance(0)
        return ret

class CaptionWeightMachine(CoverflowMachine):
    def __init__(self):
        self.caption_passes=[]
        super().__init__()
        self.mock('widget_vtable_on_paint_self')
        self.handlers.pop(syms['canvas_translate'],None)  # execute stock canvas translation
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='widget_vtable_on_paint_self':
            a,b=[u.reg_read(r) for r in REGS[:2]]
            self.caption_passes.append((a,signed(self.get(b+O['CANVAS_X'])),signed(self.get(b+O['CANVAS_Y']))))
            self.returns(16,clobber=True)
        else:
            super().hook(u,address,size,unused)

# Local playlist editor: real MIPS UI/control code and real SQLite transactions at its ABI boundary.
class PlaylistEditMachine(CoverflowMachine):
    def __init__(self,count=3):
        super().__init__()
        self.db=sqlite3.connect(':memory:',isolation_level=None)
        self.db.execute('create table playlist(id integer primary key,cd_num,fileurl,time,time_end,filename)')
        self.db.execute("insert into playlist values(0,0,'',0,0,'Mix')")
        self.db.executemany('insert into playlist values(?,?,?,?,?,?)',[(101+i,0,'/p/'+x,0,0,x) for i,x in enumerate('ABC'[:count])])
        self.db.execute("insert into playlist values(200,1,'/p/other',0,0,'Other')")
        self.fail=''; self.sql=[]; self.closed_db=0
        for n in ('sqlite3_open','sqlite3_close','sqlite3_exec','sort_playlist_addtime','getAllMusic','checkPlayListExist','batch_add_file','navigator_back','_create_vector','vector_init','_vector_push_back'):
            self.handlers[syms[n]]='edit:'+n
        self.handlers[int(manifest['patch_symbols']['stock_playlistsmore_trampoline'],16)]='edit:more'
        r=self.song('Mix',4); self.word(r+O['REC_TITLE'],self.string('Mix')); self.word(r+O['REC_DISC'],0)
        self.word(syms['p_deque_playlist'],self.deque([r]))
        self.before_queue=self.names()
    def record(self,row):
        ident,dest,path,start,end,name=row
        r=self.song(name); self.word(r+O['REC_ID'],ident); self.word(r+O['REC_DISC'],dest)
        self.word(r+O['REC_PATH'],self.string(path)); self.word(r+O['REC_CUE_START'],start); self.word(r+O['REC_CUE_END'],end)
        return r
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('edit:'): return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; name=name[5:]; ret=0
        if name=='sqlite3_open': self.word(b,123); ret=1 if self.fail=='open' else 0
        elif name=='sqlite3_close': self.closed_db+=1
        elif name=='sqlite3_exec':
            assert a==123
            try:
                for sql in self.text(b).split(';'):
                    self.sql.append(sql)
                    if self.fail and sql.startswith(self.fail): raise sqlite3.OperationalError('injected write failure')
                    # The firmware's SQLite 3.19.3 refuses this; the host's (3.39 and later) runs it.
                    if re.search(r'\bhaving\b',sql,re.I) and not re.search(r'\bgroup\s+by\b',sql,re.I):
                        raise sqlite3.OperationalError('a GROUP BY clause is required before HAVING')
                    cur=self.db.execute(sql)
                    if cur.description and c:
                        for row in cur.fetchall():
                            values=self.alloc(4*len(row)); names=self.alloc(4*len(row))
                            for i,v in enumerate(row): self.word(values+4*i,0 if v is None else self.string(str(v)))
                            if self.nested(c,d,len(row),values,names): raise sqlite3.OperationalError('callback aborted')
            except sqlite3.Error: ret=1
        elif name=='_create_vector': assert self.text(a)=='int'; ret=self.alloc(64)
        elif name=='_vector_push_back': self.word(a,b)
        elif name=='vector_init': pass
        elif name=='getAllMusic':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',r) for r in self.found]; ret=len(self.found)
        elif name=='sort_playlist_addtime':
            rows=self.db.execute('select * from playlist where id>100 and cd_num=? order by id',(a,)).fetchall()
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.record(row) for row in rows]; ret=len(rows)
        elif name=='checkPlayListExist':
            ret=self.db.execute('select count(*) from playlist where id>100 and cd_num=? and fileurl=? and time=?',
                                (b,self.text(self.get(a+O['REC_PATH'])),self.get(a+O['REC_CUE_START']))).fetchone()[0]
        elif name=='batch_add_file':
            assert (a,b)==(0xf001,0xf00b) and d!=self.get(syms['p_vector_select_record']) and self.get(d)==1
            dest=self.get(u.reg_read(UC_MIPS_REG_SP)+16); r=self.items(c)[0]
            if not self.fail:
                ident=self.db.execute('select max(id)+1 from playlist').fetchone()[0]
                self.db.execute('insert into playlist values(?,?,?,?,?,?)',(ident,dest,self.text(self.get(r+O['REC_PATH'])),
                                self.get(r+O['REC_CUE_START']),self.get(r+O['REC_CUE_END']),self.text(self.get(r+O['REC_NAME']))))
            ret=1
        elif name=='navigator_back':
            self.nested(*self.handler(self.page,O['EVT_DESTROY']),self.event)
            self.stack.remove(self.page); self.top=self.stack[-1]
        self.returns(ret,clobber=True)
    def open_editor(self,h=72,inner=68):
        more=self.node('window','playlistsmore_page'); self.word(more+O['W_PARENT'],self.wm)
        self.top=more; self.stack.append(more)
        deleted=self.node('hscroll_label',name='label_del')
        self.word(deleted+O['W_PARENT'],more); self.nodes[more]['children'].append(deleted)
        self.call(address=HOOKS['localmusic_playlistsmore_page_init'][0],args=(more,0,0,0),gap=0)
        assert self.nodes[deleted]['text']=='Delete Playlist'
        row=self.nodes[more]['children'][-1]; button=self.nodes[row]['children'][0]
        image,label=self.nodes[button]['children']
        y=10+2*h
        assert [self.get(row+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[0,y,375,h]
        assert self.nodes[row]['style']=='s_listitem_black'
        assert [self.get(button+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[20,0,335,inner]
        assert self.nodes[button]['style']=='s_btn_listitem'
        assert [self.get(image+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[10,0,52,inner]
        assert self.nodes[image]['image']=='rename' and self.nodes[image]['draw_type']==O['IMAGE_DRAW_CENTER']
        assert [self.get(label+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[72,0,253,inner]
        assert self.nodes[label]['style']=='s_scrlabel_white20l' and self.nodes[label]['text']=='Edit Playlist'
        f,ctx=self.handler(button,O['EVT_CLICK'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0)
        self.page=self.top; self.view=self.find('scroll_view'); self.paint(self.view)
        assert self.nodes[self.page]['name']=='playlistedit_page'
    def showlabels(self):
        self.view=self.find('scroll_view'); return self.labels()
    def ticks(self):  # rows holding stock's "select" check: songs already in the playlist
        return [any(self.nodes[c].get('image')=='select' for c in self.nodes[r]['children'])
                for r in self.nodes[self.view]['children']]
    def choose(self,i):
        self.view=self.find('scroll_view'); self.pick(i); self.advance(0)
        self.view=self.find('scroll_view')
    def move(self,key=O['KEY_FWD_BTN']):
        assert self.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
        assert self.call(key,gap=0)==11
        self.view=self.find('scroll_view')
    def order(self,dest=0): return [r[0] for r in self.db.execute('select filename from playlist where id>100 and cd_num=? order by id',(dest,))]
    def mutate(self,a=101,b=102,dest=0):
        records=[self.record(self.db.execute('select * from playlist where id=?',(i,)).fetchone()) if i else 0 for i in (a,b)]
        return self.call(address=payload_syms['ple_mutate'],args=(dest,*records,0),gap=0)

def ple_search(m,text,event='EVT_IM_ACTION'):
    m.choose(0)
    edit=m.find('edit'); assert m.nodes[edit]['name']=='search_edit' and m.nodes[edit]['focused']==1
    assert m.get(edit+O['W_Y'])+m.get(edit+O['W_H'])<=30 and not m.find('scroll_view')
    m.nodes[edit]['text']=text; f,ctx=m.handler(edit,O[event])
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; m.advance(0)
    m.view=m.find('scroll_view')

# Header creation uses the database IDs even when the displayed list is alphabetized.
class PlaylistLifecycleMachine(PlaylistEditMachine):
    def __init__(self):
        super().__init__()
        self.db.execute('drop table playlist')
        self.db.execute('create table playlist(id integer primary key,filename,fileurl,album,songer,style,name,composer,albumsonger,year,track,cd_num,time,time_end,time_create,Initial,bitdepth,samprate)')
        for ident,name in enumerate(('Zulu','Alpha','Echo','Bravo','Delta','Charlie')):
            self.db.execute('insert into playlist(id,filename,name,cd_num) values(?,?,?,?)',(ident,name,name,ident))
        self.db.execute("insert into playlist(id,filename,cd_num) values(101,'song',0),(102,'other',1)")
        records=[]
        for ident,name in self.db.execute('select id,name from playlist where id<=100 order by name'):
            r=self.song(name,4); self.word(r+O['REC_ID'],ident); self.word(r+O['REC_DISC'],ident); records.append(r)
        self.word(syms['p_deque_playlist'],self.deque(records))
        self.more=self.node('window','playlistsmore_page'); self.nodes[self.more]['_playlist']=5
        self.answer=10; self.backs=0
        self.handlers[syms['navigator_back']]='life:back'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='life:back': self.backs+=1; self.returns(0); return
        if name=='c:navigator_to_with_context' and self.text(u.reg_read(REGS[0]))=='dialog/confirminfo_dialog':
            assert self.text(u.reg_read(REGS[1])+8)=='Delete Playlist?'
            self.returns(self.answer); return
        super().hook(u,address,size,unused)
    def create(self,name):
        return signed(self.call(address=HOOKS['createPlayListItem'][0],args=(self.get(syms['p_deque_playlist']),self.string(name),0,0),gap=0))
    def delete(self):
        return self.call(address=HOOKS['playlist_delete_click'][0],args=(self.more,0,0,0),gap=0)
    def reload_pending(self):
        # Run stock's parent-show gate with the playlist category unchanged after More closes. Since V1.36 the page loads
        # its list again whatever the gate says (the jalr at 0x4b59ac); the gate's own branch only clears the flag.
        self.word(syms['g_class_type'],0xf00b); self.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
        reached=[]
        def stop(u,address,size,unused):
            if address in (0x4b59a0,0x4b59ac): reached.append(address)
            if address==0x4b59ac: u.emu_stop()
        hook=self.u.hook_add(UC_HOOK_CODE,stop)
        self.u.emu_start(0x4b5974,0x1000000,count=100)
        self.u.hook_del(hook)
        assert reached[-1]==0x4b59ac
        return reached[0]==0x4b59a0

def playlist_hold(steps=2):
    m=QueueMachine(page='playlist_page',rows=3); m.word(O['PLAYLIST_ADD_MODE'],0)
    for i,e in enumerate(m.nodes[r]['children'][0] for r in m.nodes[m.surface]['children']):
        if i: dots=m.node('image',f'img_more{i-1}'); m.word(dots+O['W_PARENT'],e); m.nodes[e]['children'].append(dots)
    lists=[m.song(f'List {i}',4) for i in range(2)]
    for i,r in enumerate(lists): m.word(r+O['REC_DISC'],7+i)
    m.word(syms['p_deque_playlist'],m.deque(lists)); m.found.append(m.song('Book.m4b'))
    m.word(m.surface+O['W_H'],144); m.paint(m.surface)  # every row on screen: the dots stay on their rows
    for _ in range(steps): m.call(); m.paint(m.surface)
    return m

from ipod import BC_ART

class ABMachine(QueueMachine):
    ab=0
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='ab:x': return super().hook(u,address,size,unused)
        self.calls.append(('ab_x',u.reg_read(UC_MIPS_REG_A0))); self.returns(self.ab,clobber=True)

from ipod import VALUE_NAMES

CL_BAR_LINE={'1':0x5a595a,'0':0x333333}

class UiLoad:  # the stock loader answers root
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='load:stock_ui_load': return super().hook(u,address,size,unused)
        self.calls.append(('stock_ui_load',u.reg_read(REGS[0]))); self.returns(self.root)
class LoadQueue(UiLoad,QueueMachine):
    ready=0
    def bootvol(self): return self.ready or super().bootvol()  # the page ui_loaded made, as navigator_to's load did
class LoadPlain(UiLoad,Machine): pass
def ui_loaded(m,name,root):
    m.root=root; m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='load:stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string(name),0,0,0),gap=0)

def wbox(m,w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
def parents(m,w):
    for c in m.nodes[w]['children']: m.word(c+O['W_PARENT'],w); parents(m,c)

def dirty(m,win): return [c[2] and tuple(signed(m.get(c[2]+4*i)) for i in range(4)) for c in m.calls if c[0]=='widget_invalidate_force' and c[1]==win]  # each invalidate's rect
def page_fill(m,y=0,rgb=0x63717b): return m.bands[0][:5]==(0,y,375,294,color_t(rgb))  # the page: one flat fill first, the half's Home pane colour
def art_at(m): return list(zip([m.text(c[2]) for c in m.calls if c[0]=='widget_load_image'],[d[1][:2] for d in m.image_draws]))

from art import corner_cover
def round_box(bands,x,y,w,h,R):  # a round box's fills at the head of bands: each row opaque between its corners' pixels, those its colour at their coverage; its rows' colours, the rest
    rows,i,cover=[],0,corner_cover(R)
    for r in range(h):
        row=r if r<R else h-1-r if r>=h-R else None; n=0; c=[]
        while row is not None and n<R and cover[row*R+n]<255:
            if cover[row*R+n]:
                got=bands[i:i+2]; i+=2
                assert [b[:4] for b in got]==[(x+n,y+r,1,1),(x+w-1-n,y+r,1,1)] and {b[4]>>24 for b in got}=={cover[row*R+n]},(r,n,got)
                c+=[b[4]&0xffffff for b in got]
            n+=1
        assert bands[i][:4]==(x+n,y+r,w-2*n,1) and bands[i][4]>>24==0xff and all(v==bands[i][4]&0xffffff for v in c),(r,n,bands[i])
        rows.append(bands[i][4]); i+=1
    return rows,bands[i:]
def corner_in(R,row=0): return next((n for n in range(R) if corner_cover(R)[row*R+n]==255),R)  # where a corner's row turns opaque
def triangle(m): return [b for b in m.bands if 1<b[2]<=10]  # Max Volume's, the only narrow rows; a corner's pixels are 1 wide
def art_size(name): return struct.unpack('>II',(B/f'{name}.png').read_bytes()[16:24])
def shadows(m): return [(t['text'],t['rect'],t['font'],t['color']) for t in m.letters]
def slider_paint_calls(m):  # what slider_paint called: from the page's image to the caption's shadow
    names=[c[0] for c in m.calls]; return set(names[names.index('widget_load_image'):len(names)-names[::-1].index('canvas_draw_text_in_rect')])

def vol_closed(q):
    q.nodes[q.wm]['children']=[q.win]; q.top=q.win; q.calls=[]; q.advance(O['VOL_POLL_MS'])
    assert not q.timers and [q.nodes[w]['visible'] for w in (q.elapsed,q.remain)]==[1,1]

QS_X=(31,115,199,283)

def qs_ink(on,light): return '' if on else '_l' if light=='1' else '_d'
QS_AT=[(QS_X[i%4],(20,138)[i//4]) for i in range(8)]

def qs_arts(q): return list(zip(q.image_loads,[d[1][:2] for d in q.image_draws]))

def qs_down(q,w):
    q.pressed=1; q.word(q.event+O['EVENT_X'],30); q.word(q.event+O['EVENT_Y'],40)
    f,ctx=q.handler(w,O['EVT_POINTER_DOWN']); q.call(key=30,address=f,args=(ctx,q.event,0,0),event_type=O['EVT_POINTER_DOWN'],gap=0)
def qs_event(q,w,kind):
    q.word(q.event+O['EVENT_X'],30); q.word(q.event+O['EVENT_Y'],40)
    return q.call(key=30,address=HOOKS['widget_dispatch'][0],args=(w,q.event,0,0),event_type=kind,gap=0)

def qs_tiles(q,rows,i=None):  # each tile's (top, foot, border), from the bands after the ground: a flat R 12 box, a 58 px R 11 fill in it
    out=[]
    for x,y in QS_AT:
        line,rows=round_box(rows,x,y,60,60,12); face,rows=round_box(rows,x+1,y+1,58,58,11); assert len(set(line))==1
        assert all(min(a>>k&255,b>>k&255)<=c>>k&255<=max(a>>k&255,b>>k&255) for c in face for k in (0,8,16) for a,b in [(face[0],face[-1])])  # one ramp
        out.append((face[0],face[-1],line[0]))
    return out
def qs_want(c): return tuple(map(color_t,c))

def qs_quarter(c): return sum(((c>>k&255)*3//4)<<k for k in (0,8,16))

def qs_dark(c): return sum(((c>>k&255)*5//6)<<k for k in (0,8,16))

def qs_lines(a,t,lines=None): return [(l,(a[0]-10,a[1]+64+14*i,80,18),0xffffffff) for i,l in enumerate(lines or (t,))]

def tm_hand(tm_run,deg):
    a=math.radians(deg); ux,uy=math.sin(a),-math.cos(a); cx,cy=101,128; along=[]; side=[]
    for x,y,w,h,*_ in tm_run:
        for px in range(x,x+w):
            along.append((px+.5-cx)*ux+(y+.5-cy)*uy); side.append(abs((px+.5-cx)*uy-(y+.5-cy)*ux))
    return min(along),max(along),max(side)

from ipod import corner_inset

from ipod import corner_inset

from fontTools.ttLib import TTFont
from ipod import CLASSIC_LIGHT_FONT_SRC

def opaque(m,y,h=30): return [b for b in m.bands if b[4]>>24==0xff and y<=b[1]<y+h and b[3]==1]

def fill_at(m,x,y): return [b[4] for b in m.bands if b[:4]==(x,y,1,1)]

from ipod import corner_x

def panel_frame(bands,x,y,w,h,light):  # the flip panel's border (coverflow_panel_paint): one flat opaque colour a half, a pixel outside the panel all round,
    c=color_t(0xb4b9c0 if light=='1' else 0x333333); sides=[b[:4] for b in bands if b[4]==c]  # so its left and right columns and its top and bottom rows match
    assert sorted(sides)==sorted([(x-1,y-1,w+2,1),(x-1,y+h,w+2,1),(x-1,y,1,h),(x+w,y,1,h)]),sides
    inside=[b for b in bands if b[4]!=c]; assert {b[0] for b in inside}=={x} and {b[0]+b[2] for b in inside}=={x+w},inside  # paper, header, shadow: edge to edge, nothing a column short on one side
def hold_panel(m,d,paper,light='1'):
    m.paint(d); L=wbox(m,named(m,d,'list_view'))[3]; H=44+L; y=26+(294-H)//2; rows=[b[:5] for b in m.bands]
    assert rows[0][:4]==(40,y,294,H) and rows[0][4]>>24==0xff and (paper is None or rows[0][4]==paper) and [b[:4] for b in rows[1:45]]==[(40,y+j,294,1) for j in range(44)] and rows[1][4]>>24==0xf0
    assert [b[:4] for b in rows[45:53]]==[(40,y+42+j,294,1) for j in range(8)] and rows[46][4]==28<<24; panel_frame(rows[:57],40,y,294,H,light)
    assert corner_x(y-1,H+1)<=39; return y

def span(rows): x,y=min(b[0] for b in rows),min(b[1] for b in rows); return x,y,max(b[0]+b[2] for b in rows)-x,max(b[1]+b[3] for b in rows)-y

class MsgMachine(LoadPlain):
    songs=704
    def __init__(self):
        super().__init__(); self.mock('sqlite3_open','sqlite3_close','strstr@GLIBC_2.0'); self.sqls=[]; self.threads=[]; self.thread_fails=self.busy=self.thread_first=False
        for n,h in (('sqlite3_exec','msg:exec'),('strdup@GLIBC_2.0','msg:strdup'),('pthread_create@GLIBC_2.2','msg:thread')): self.handlers[syms[n]]=h
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='msg:free'  # free's GOT slot
    def hook(self,u,address,size,unused):
        h=self.handlers.get(address)
        if h=='msg:strdup': self.returns(self.string(self.text(u.reg_read(REGS[0])))); return
        if h=='msg:thread':
            if not self.thread_fails: self.word(u.reg_read(REGS[0]),77); self.threads.append((u.reg_read(REGS[2]),u.reg_read(REGS[3])))
            self.calls.append(('pthread_create',))
            if self.thread_first and not self.thread_fails: fn,arg=self.threads.pop(); self.nested(fn,arg)
            self.returns(11 if self.thread_fails else 0); return
        if h=='msg:free': self.returns(0); return
        if h!='msg:exec': return super().hook(u,address,size,unused)
        sql,row=self.text(u.reg_read(REGS[1])),u.reg_read(REGS[2]); self.sqls.append(sql)
        if self.busy: self.returns(5); return
        if row:
            argv=self.alloc(4); self.word(argv,self.string(str(self.songs))); self.nested(row,u.reg_read(REGS[3]),1,argv,0)
        self.returns(0)
    def counted(self):  # the worker runs as its thread would
        while self.threads:
            fn,arg=self.threads.pop(0); self.call(address=fn,args=(arg,0,0,0),gap=0,count=5_000_000,clear=False)

def msg_close(m,win):
    f,ctx=next((f,c) for t,f,c in m.nodes[win]['handlers'] if t==O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)

def msg_track(m): return [b for b in m.bands if 72<=b[0] and b[0]+b[2]<=301 and 218<=b[1]<232 and b[3]==1 and b[4]>>24!=0xff]
def msg_fill(m): return [b for b in m.bands if 72<=b[0] and 218<=b[1]<232 and b[3]==1 and b[4]>>24==0xff]

def half_turn(m,hour): m.clock=(hour,0); m.call(address=payload_syms['clock_due'],args=(0,0,0,0),gap=0)

def hold_inks(m,d):  # its title's white, final in either half
    m.nodes[m.wm]={'children':[d]}; return m.nodes[named(m,d,'scrlabel_title')]['style:normal:text_color']

from ipod import corner_inset

from fontTools.ttLib import TTFont

from ipod import BC_ART

def diamond(x):
    M,top=O['NP_SCRUB_MARK'],(44-O['NP_BAR_IPOD'])//2
    return [(x-(M-abs(j)),top+(O['NP_BAR_IPOD']-1)//2+j,2*(M-abs(j))+1,1) for j in range(-M,M+1)]

def vol_up(m):
    m.dlg=m.node('dialog','volume_dialog'); m.word(m.dlg+O['W_PARENT'],m.wm)
    m.nodes[m.wm]={'children':[m.win,m.dlg]}; m.stack=[m.win,m.dlg]; m.top=m.dlg; m.word(m.wm+O['W_KEY_TARGET'],m.dlg)

sleep_hook=HOOKS['main_loop_sleep_default'][0]

from build import GP

TAGLIB=0x1180000  # libtag_c's load address here
class LyricMachine(QueueMachine):
    def __init__(self,tags,lrc=(),cls=0xf001,kind=8,bound=True):
        super().__init__(cls=cls); self.tags=tags; self.lrc=list(lrc); self.bound=bound; self.lines=[]; self.asked=[]; self.tag_calls=[]
        self.word(self.items(self.get(syms['mcl_pdeqplaylist']))[0]+O['REC_TYPE'],kind)
        self.word(O['MCL_LYRICS'],self.deque([],'stLyricInfo'))
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='lyric:libc_free'  # free's GOT slot, as Coverflow's (the stamps' table)
        self.handlers[int(manifest['patch_symbols']['stock_lyric_trampoline'],16)]='lyric:stock'
        for n,off in (('get',O['TAGLIB_PROPERTY_GET']),('pfree',O['TAGLIB_PROPERTY_FREE'])): self.handlers[TAGLIB+off]='lyric:'+n
        for n in ('taglib_file_new','taglib_file_free','mclGetLyricSize','_deque_push_back'): self.handlers[syms[n]]='lyric:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('lyric:'): return super().hook(u,address,size,unused)
        name=name[6:]; a,b=[u.reg_read(r) for r in REGS[:2]]; ret=0
        if name=='stock': self.tag_calls.append('stock'); self.lines=list(self.lrc); ret=1 if self.lrc else -1
        elif name=='mclGetLyricSize': ret=len(self.lines)
        elif name=='_deque_push_back' and a==self.get(O['MCL_LYRICS']): self.lines.append((signed(self.get(b)),self.text(self.get(b+4))))
        elif name=='_deque_push_back': return super().hook(u,address,size,unused)
        elif name=='libc_free':
            if a and getattr(self,'zero_freed',0): self.u.mem_write(a,b'\0'*len(self.raw(a)))  # a use after free reads nothing
        elif name=='taglib_file_new':
            assert b==0  # Embedded lyrics need tags only.
            self.tag_calls.append(('new',self.text(a))); ret=0x1170000
            if self.bound: self.word(O['TAGLIB_NEW_GOT'],TAGLIB+O['TAGLIB_FILE_NEW'])
        elif name=='get':
            assert a==0x1170000; key=self.text(b); self.asked.append(key)
            if key in self.tags: ret=self.alloc(8); self.word(ret,self.string(self.tags[key]) if self.tags[key] is not None else 0); self.word(ret+4,0)
        else: assert name=='pfree' or a==0x1170000; self.tag_calls.append(name.removeprefix('taglib_file_'))
        self.returns(ret,clobber=True)
    def load(self): return self.call(address=HOOKS['mclLoadExLyric'][0],args=(0,0,0,0),count=5000000)
FAR=0x7fffffff

class LrcMachine(LyricMachine):
    def __init__(self,tags={},lrc=(),path=None,files={}):
        super().__init__(tags); self.k_stock=list(lrc); self.k_path=path; self.k_files=dict(files)
        for n in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','fclose@GLIBC_2.2','deque_clear'): self.handlers[syms[n]]='k:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS[:4]]; ret=0
        if name=='lyric:stock':
            self.tag_calls.append('stock')
            if self.k_path: self.nested(payload_syms['ringnav_lrc_open'],self.string(self.k_path),self.string('r'))
            self.lines=list(self.k_stock); return self.returns(1 if self.k_stock else -1,clobber=True)
        if name=='k:fopen@GLIBC_2.2': ret=0x1170100 if self.text(a) in self.k_files else 0; self.k_opened=self.text(a)
        elif name=='k:fread@GLIBC_2.0': data=self.k_files[self.k_opened][:b*c]; self.u.mem_write(a,data); ret=len(data)//b
        elif name=='k:deque_clear': assert a==self.get(O['MCL_LYRICS']); self.lines=[]
        elif name!='k:fclose@GLIBC_2.2': return super().hook(u,address,size,unused)
        self.returns(ret,clobber=True)

def app_lrc(rec_id,gen=3,body=b'[00:01.00]Hi\n[00:02.50]there\n'):
    m=LrcMachine(files={'/tmp/q2-jellyfin.lyrics/2.lrc':body}); m.word(O['MCL_TYPE'],0xf1)
    m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[0]+O['REC_TIDAL_ID'],m.string(rec_id))
    au=payload_syms['au']; m.word(au+72,gen); m.u.mem_write(au+80,b'/tmp/q2-jellyfin.lyrics\0')
    return m

def moves(m): return [(c[0],c[1]) for c in m.calls if c[0] in ('player_seek_time','mclSetSeek','mclSetResume')]
def long_play(m):  # Play/Pause held and let go: whether it was taken, Play/Pause's own presses after it, and the seeks
    m.t=getattr(m,'t',7000)+1; m.press(m.t); took=m.hold()==11; seen=moves(m); return took,m.release(),seen
def song_menu(m): return any(p[0]=='dialog/sortselect_dialog' for p in m.opened)

def centre_of(m,i,view):  # the offset that centres line i's row in a view that high (C's division)
    r=m.lyric_row(i); return m.get(r+O['W_Y'])+int((m.get(r+O['W_H'])-view)/2)

def where(m,view): return (m.full()[:6],[m.get(m.col+O[k]) for k in ('W_Y','W_H')],m.nodes[m.dots].get('self_layout'),m.nodes[view].get('self_layout'))

def full_on(m): return m.full()[5]=='#000000'

class AlbumLyricsMachine(CoverflowMachine):
    def __init__(self,**queue):
        super().__init__(**queue); self.wifi=3; self.restack=True; self.accessed=[]
        self.handlers[int(manifest['patch_symbols']['stock_playermore_trampoline'],16)]='stock_playermore'
        self.handlers[syms['get_wifisignal']]='al:get_wifisignal'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='c:access@GLIBC_2.0': self.accessed.append(self.text(u.reg_read(REGS[0])))
        if name!='al:get_wifisignal': return super().hook(u,address,size,unused)
        self.returns(self.wifi,clobber=True)
    def playing(self): return self.items(self.get(syms['mcl_pdeqplaylist']))[0]
    def more(self,rows=8):
        self.stock=[self.node('list_item') for _ in range(rows)]
        view=self.node('scroll_view','scroll_view_more',list(self.stock)); self.top=self.node('window','playermore_page',[view])
        assert self.call(address=HOOKS['playermore_page_init'][0],args=(self.top,0,0,0),gap=0)==0
        assert self.calls[0][:2]==('stock_playermore',self.top)
        looked=self.accessed[:]; self.accessed.clear()  # over stock's rows the page looks for AirPlay's app (airplay.c)
        assert looked==(['/mnt/mmc/.airplay/q2-airplay'] if rows==8 else []),looked
        return self.nodes[view]['children']

def album_lyrics_page(**queue):
    m=AlbumLyricsMachine(**queue); button=m.nodes[m.more()[4]]['children'][0]; label=m.nodes[button]['children'][1]
    cue=m.song('Cue'); m.word(cue+O['REC_CUE_END'],90)
    m.found=[m.song('T1'),cue,m.song('T2')]
    def press():
        f,ctx=m.handler(button,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
    return m,label,press

def cf_jump(m,at,direction):
    letter=m.alloc(4)
    steps=m.call(address=payload_syms['coverflow_jump'],args=(m.slide,at,direction,letter),gap=0)
    return steps,m.get(letter)

def follow_share(m,k):
    m.u.reg_write(UC_MIPS_REG_F12,struct.unpack('<I',struct.pack('<f',k))[0])
    m.call(address=payload_syms['coverflow_follow'],args=(0,0,0,0),gap=0,clear=False)
    return struct.unpack('<f',struct.pack('<I',m.u.reg_read(UC_MIPS_REG_F0)&0xffffffff))[0]

class ScanLockMachine(UiLoad,CoverflowMachine):
    def __init__(self,path):
        super().__init__(); self.mock('strstr@GLIBC_2.0')
        self.db=sqlite3.connect(path,isolation_level=None,timeout=0)  # stock sets no busy handler
        self.handlers.pop(HOOKS['scanAllMusicFile'][0]+12); self.handlers.pop(syms['toolsGetUserStop'])  # stock's own code
        for n in ('sqlite3_open','sqlite3_exec','sqlite3_close','sqlite3_free','sprintf@GLIBC_2.0','toolsClearDbCount','toolsLoadAllFile','usleep@GLIBC_2.0'):
            self.handlers[syms[n]]='lock:'+n
        self.sqls=[]
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('lock:'): return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; name=name[5:].split('@')[0]; ret=0
        self.calls.append((name,a,b,c))
        if name=='sqlite3_open': assert self.text(a)=='/mnt/data/database.db'; self.word(b,123)
        elif name=='sprintf': v=iter((c,d)); u.mem_write(a,re.sub('%s',lambda _:self.text(next(v)),self.text(b)).encode()+b'\0')
        elif name=='usleep':  # the count ends meanwhile: its read lock goes, then its thread answers
            self.reading.close()
            while self.threads: self.nested(*self.threads.pop(0))
        elif name=='sqlite3_exec':
            assert a==123; self.sqls.append(self.text(b)); err=self.get(u.reg_read(UC_MIPS_REG_SP)+16)
            try:
                for sql in filter(str.strip,self.text(b).split(';')):  # each statement, as sqlite3_exec runs them
                    for row in self.db.execute(sql).fetchall():
                        values=self.alloc(4*len(row))
                        for i,x in enumerate(row): self.word(values+4*i,self.string(str(x)))
                        if c: self.nested(c,d,len(row),values,0)
                if err: self.word(err,0)
            except sqlite3.OperationalError as e:
                ret=5 if 'locked' in str(e) else 1  # SQLITE_BUSY
                if err: self.word(err,self.string(str(e)))
        self.returns(ret)

def cover_open(files,**kw):
    m=CoverflowMachine(albums=5,cached=False); m.__dict__.update(kw)
    for i,r in enumerate(m.albums): m.word(r+O['REC_PATH'],m.string(f'/mnt/mmc/a{i}.flac'))
    names=[f'{m.nested(payload_syms["album_key"],r)&0xffffffff:08x}.jpg' for r in m.albums]
    m.cover_names=names; m.cover_files=[(n,8) for n in files(names)] if files else False; m.open()
    return m,[m.text(c[1]) for c in m.calls if c[0]=='strdup' and m.text(c[1]).startswith('/mnt/mmc/a')]

COVER_ALBUM=['Play Next','Add to Queue','Shuffle','Add to Favourites','Rate Album']  # a cover's menu, Go to artist aside

class PlaylistCoverMachine(CoverflowMachine):
    def __init__(self):
        super().__init__()
        self.handlers[syms['navigator_to_with_context']]='q:navigator_to_with_context'  # the More page's index
        for n in ('playlist_art','playlist_art_refresh','playlist_art_stop','playlist_songs','playlists_edit'):
            if n in payload_syms: self.handlers[payload_syms[n]]='pl:'+n
        self.handlers[syms['getAllPlayList']]='pl:getAllPlayList'  # stock's headers, as the page reads them
        self.edits=[]; self.lists=[self.song(f'List {i}',4) for i in range(3)]; self.heads=self.lists
        for i,r in enumerate(self.lists): self.word(r+O['REC_TITLE'],self.string(f'List {i}')); self.word(r+O['REC_DISC'],10+i)
        self.word(syms['p_deque_playlist'],self.deque(self.lists))
        self.under=self.node('window','playlist_page'); self.stack=[self.under]; self.top=self.under
        self.nodes[self.wm]={'children':self.stack}
        assert self.call(address=payload_syms['coverflow_playlists'],args=(self.get(syms['p_deque_playlist']),0,0,0),gap=0)==1
        self.page=self.top; self.slide=self.find('slide_menu')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('pl:'): return super().hook(u,address,size,unused)
        if name=='pl:playlists_edit': self.edits.append(u.reg_read(REGS[0]))
        if name=='pl:getAllPlayList':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',r) for r in self.heads]
            return self.returns(len(self.heads))
        self.returns(0)

class ClosingCoverMachine(PlaylistCoverMachine):  # window_close animates: the dialog stays on top until destroyed
    def hook(self,u,address,size,unused):
        if address!=syms['window_close']: return super().hook(u,address,size,unused)
        self.closing.append(u.reg_read(REGS[0])); self.returns(0,clobber=True)

def pl_front(m):
    f,ctx=m.handler(m.page,O['EVT_WINDOW_FOREGROUND']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
    return m.find('slide_menu')

def pl_edit(edit,classic=True,covers=True,theme=None):
    class M(ClassicPlaylistMachine):
        def hook(self,u,address,size,unused):
            if address!=payload_syms['coverflow_playlists_close']: return super().hook(u,address,size,unused)
            self.shut+=1; page=0
            if self.covers in self.stack: self.stack.remove(self.covers); self.top=page=self.stack[-1]
            self.returns(page)
    if theme: CONFIG.update(THEME=theme)
    try: m=M(classic=classic)
    finally: CONFIG.pop('THEME',None)
    m.rebuild(); m.paint(m.view); m.shut=0; m.page=m.top; m.covers=m.node('window','coverflow_page')
    if covers: m.stack.append(m.covers); m.top=m.covers
    m.before=m.bar(); m.calls.clear()
    m.call(address=payload_syms['playlists_edit'],args=(edit,0,0,0),gap=0,clear=False); m.advance(0,clear=False); m.stack_rows()
    return m

# Album favourites and ratings (ratings.c rate_*, coverflow.c album_id): a cover keeps 0-5 hearts of its own in
# /mnt/data/ringnav-album-ratings by album_id, the low three bits its hearts, 5 its favourite and its songs My Fav's; songs'
# ratings and the queue are untouched. Hold Play/Pause on a cover: Add/Remove from Favourites and Rate album, whose picker is Now
# Playing's on Coverflow's page: the wheel moves it, Centre or a heart saves, Return leaves it as it was.
ALBUM_RATES='/mnt/data/ringnav-album-ratings'
class FileMocks:
    """stdio over self.files (path -> bytes): a write lands on fclose, then rename; fail_write refuses 'wb'."""
    FILE_FNS=('fopen@GLIBC_2.2','fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0')
    def file_mocks(self,prefix,files=None):
        self.files=dict(files or {}); self.open_files={}; self.fail_write=False
        for n in self.FILE_FNS: self.handlers[syms[n]]=prefix+n
    def file_op(self,u,name,a,b,c,d):
        if name=='fopen':
            path,mode=self.text(a),self.text(b)
            if (mode=='wb' and not self.fail_write) or (mode=='rb' and path in self.files):
                f=0x2000000+16*next(i for i in range(64) if 0x2000000+16*i not in self.open_files); self.open_files[f]=[path,b'',0]; return f
        elif name=='fread':
            o=self.open_files[d]; data=self.files[o[0]][o[2]:]; n=min(c,len(data)//b); o[2]+=n*b
            u.mem_write(a,data[:b*c].ljust(b*c,b'\0')); return n
        elif name=='fwrite': self.open_files[d][1]+=bytes(u.mem_read(a,b*c)); return c
        elif name=='fclose':
            path,data,_=self.open_files.pop(a)
            if data: self.files[path]=data
        elif name=='rename': self.files[self.text(b)]=self.files.pop(self.text(a))
        return 0

class UsbMachine(FileMocks,MsgMachine):
    def __init__(self): super().__init__(); self.file_mocks('u:'); self.widget_timers=True
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('u:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; self.calls.append((name,a,b,c)); self.returns(self.file_op(u,name,a,b,c,d),clobber=True)

class AlbumRateMachine(FileMocks,CoverflowMachine):
    def __init__(self,files=None,**kw): super().__init__(**kw); self.file_mocks('a:',files)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('a:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; self.calls.append((name,a,b,c))
        ret=self.file_op(u,name,a,b,c,d)
        self.returns(ret,clobber=True)
    def kept(self):
        data=self.files.get(ALBUM_RATES,b''); return sorted(v for v in struct.unpack(f'<{len(data)//4}I',data) if v)
    def aid(self,r): return self.call(address=payload_syms['album_id'],args=(r,0,0,0),gap=0)&0xffffffff
    def panel(self):
        p=[w for w in self.nodes[self.page]['children'] if self.nodes[w].get('name')=='rate_panel']
        return p[0] if p and self.nodes[p[0]].get('visible',1) else None
    def caption(self): return self.nodes[self.nodes[self.panel()]['children'][0]]['text']
    def hearts(self): return [self.nodes[h]['image'] for h in self.nodes[self.panel()]['children'][1:]]
    def menu(self):  # the hold's rows, then Return closes the dialog
        self.t=getattr(self,'t',7000)+1; self.press(self.t); assert self.hold()==11 and self.release()==0
        rows=self.labels(); f,ctx=self.handler(self.back,O['EVT_CLICK']); self.call(O['KEY_RETURN'],address=f,args=(ctx,self.event,0,0),gap=0)
        return rows
    def rate(self,want):  # Rate album, wheel down to 0 then up to want, Centre
        toasts=len(self.toasts)
        assert self.run(4) is None and self.top==self.page and self.panel()
        for k in [O['KEY_PREV']]*5+[O['KEY_NEXT']]*want: assert self.call(k)==11
        assert self.call(O['KEY_CENTER'])==11 and self.panel() is None
        return self.toasts[-1][3] if len(self.toasts)>toasts else None
def song_side_effects(m):
    return '/mnt/data/ringnav-ratings' in m.files or any(c[0] in ('batch_add_file','deleteMusicFromFav') for c in m.calls)

def rate_poll(m,left=5):
    m.byte(O['BC_IDLE'],left); m.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
    m.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=0); return m.u.mem_read(O['BC_IDLE'],1)[0]

def hold_pick(m,row):
    n=len(m.toasts); m.t=getattr(m,'t',7000)+1; m.press(m.t); assert m.hold()==11 and m.release()==0
    m.pick(row); m.advance(0)
    return m.toasts[-1][3] if len(m.toasts)>n else None
def classic_half(machine,light,**kw):
    SETUP[:]=[lambda k:(k.config.update(THEME='1',LIGHT=light),k)]
    try: fm=machine(**kw)
    finally: SETUP.clear()
    fm.mock('strstr@GLIBC_2.0'); return fm

def bare_hold(m,row=None):
    if row is not None: m.word(m.row(row)+O['REC_TYPE'],9)
    m.t=7001; m.press(m.t); return m.hold(),m.release()

def fav_calls(m,name): return [c for c in m.calls if c[0]==name]

def at_theme(t,cls,*a,**kw):  # a cls machine that reads THEME t and the light half as it is built
    CONFIG.update(THEME=t,LIGHT='1')
    try: return cls(*a,**kw)
    finally: CONFIG.pop('THEME'); CONFIG.pop('LIGHT')

class FolderPush(LookupMachine):  # stock's click and Back as their step of the layer
    step=0
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'')!='stock_folder_step': return super().hook(u,address,size,unused)
        self.byte(syms['g_folder_layer'],self.u.mem_read(syms['g_folder_layer'],1)[0]+self.step); self.returns(0,clobber=True)
def folder_push(t):
    m=at_theme(t,FolderPush,['home_page','folder_page'],1,False); m.top=asset_tree(m,'../classic-light_folder_page.bin'); m.stack[-1]=m.top
    for n in ('click','back'): m.handlers[int(manifest['patch_symbols'][f'stock_folder_{n}_trampoline'],16)]='stock_folder_step'
    m.mock('widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start'); m.byte(syms['g_folder_layer'],3); m.table=named(m,m.top,'tableview_folder')
    w=m.node('button','row'); m.word(w+O['ROW_INDEX'],1); m.word(m.event+O['EVENT_TARGET'],w)
    return m,w
def folder_step(m,w,step,back=False):
    m.step=step; m.calls=[]
    if back: m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
    else: m.call(address=payload_syms['ringnav_folder_click'],args=(w,m.event,0,0),gap=0)

class AllAlbums(CoverflowMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='q:getMusicByAlbum': self.found=self.by_album[self.text(u.reg_read(REGS[0]))]
        return super().hook(u,address,size,unused)

class EraMachine(PlaylistEditMachine):
    def __init__(self,songs=()):
        super().__init__()
        self.mock('strstr@GLIBC_2.0')
        self.db.execute('create table songtable(id integer primary key,album,songer,albumsonger,fileurl,year,track,cd_num)')
        self.db.executemany('insert into songtable(album,songer,albumsonger,fileurl,year) values(?,?,?,?,?)',songs)
    def open_era(self):
        self.call(address=payload_syms['era_menu'],args=(0,self.event,0,0),gap=0); self.page=self.top
        return self.texts()
    def decade(self,i):
        row=self.nodes[self.find('scroll_view')]['children'][i]; f,ctx=self.handler(row,O['EVT_CLICK'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0); self.page=self.top
        return self.texts()

class EraPlay(EraMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='q:getMusicByAlbum': self.found=self.by_album.get(self.text(u.reg_read(REGS[0])),[])
        return super().hook(u,address,size,unused)

class EraFill(EraMachine):
    def __init__(self,songs):
        super().__init__(songs); self.handlers[syms['db_song_row_callback']]='era:fill'
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='era:fill': return super().hook(u,address,size,unused)
        ident=int(self.text(self.get(u.reg_read(REGS[2]))))
        self.deqs[self.get(syms['tools_pdeq_directory'])][1].append(self.copy('stSongInfo',self.found[ident-1]))
        self.returns(0,clobber=True)

def ratings_albums(m):
    m.call(address=payload_syms['ratings_menu'],args=(0,m.event,0,0),gap=0)  # the Library's Ratings row
    menu_page=m.top; assert m.nodes[menu_page]['name']=='ratings_page'; row=m.nodes[m.find('scroll_view',menu_page)]['children'][2]; f,ctx=m.handler(row,O['EVT_CLICK'])
    m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000); m.advance(0)
    return menu_page

from ipod import corner_inset
def cf_geometry(m,w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))

# Execute stock sibling selection and playback scheduling; mock filesystem enumeration,
# tag decoder and image codec boundaries, never the resulting player globals.
class RolloverMachine(CoverflowMachine):
    def __init__(self,sizes=(2,2,1,1)):
        super().__init__(queue=0,cls=1)
        self.handlers.pop(syms['player_mainscheduling'])  # runs stock's, as tick() calls it
        self.native_trace={syms[n]:n for n in ('mclAutoChange','mclNextSong','on_player_autochange','toolsLoadNextDir',
                          'player_refresh_playqueue','mclLoadPlayList','player_get_id3info','player_set_coverinfo','mclClearChangeFlag')}
        self.native_trace[syms['open@GLIBC_2.0']]='press_open'  # only a stamp opens a file here; it fails at the write
        self.mock('open@GLIBC_2.0','write@GLIBC_2.0','close@GLIBC_2.0')
        self.trace=[]; self.files={}; self.dirs={}; self.artfiles=set(); self.worker=False; self.forks=0
        self.handlers[0x5d5614]='r:directory'; self.handlers[0x5d58e4]='r:directory'
        self.handlers[0x5bf504]='r:decoder_start'
        # Lazy-bound malloc, as used by stock player_refresh_playqueue.
        self.word(0xa3a0e8,0x1000014); self.handlers[0x1000014]='r:malloc'
        for name in ('strcpy@GLIBC_2.0','strncmp@GLIBC_2.0','strstr@GLIBC_2.0','strchr@GLIBC_2.0',
                     'toolsGetMusicInfo','toolsGetFileSize','toolsGetAlbumCover','toolsGetExternCover',
                     'access@GLIBC_2.0','remove@GLIBC_2.0','usleep@GLIBC_2.0','fork@GLIBC_2.0'):
            self.handlers[syms[name]]='r:'+name.split('@')[0]
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0','toolsFreeStSongInfo',
                  'mclLoadExLyric','sendBtHeadsetPlayStatus','notifyPlayInfo','notifyPlayStatus','notifyRefreshLyric',
                  'dmrNotifyPlayStatus','dlnaRenderSaveUrlMetadata','reset_repeatinfo','initializeDmrQCurrentInfo',
                  'player_reconfig','add_playrecord','toolsTrimLeft')
        self.handlers.pop(syms['mclStartPlayer'],None)
        for i,n in enumerate(sizes):
            folder=f'/mnt/mmc/{i}'; entries=[]
            for j in range(n):
                path=f'{folder}/{j}.flac'; r=self.song(f'{j}.flac')
                self.word(r+O['REC_PATH'],self.string(path)); self.word(r+0x38,1)
                for off in ('REC_ALBUM','REC_ARTIST'): self.word(r+O[off],0)
                entries.append(r); self.files[path]=(f'Album {i}' if i!=2 else '',i!=2)
            self.dirs[folder]=entries
        self.dirs['/mnt/mmc']=[]
        for folder in list(self.dirs)[:-1]:
            r=self.song(folder.rsplit('/',1)[1],4); self.word(r+O['REC_PATH'],self.string(folder)); self.dirs['/mnt/mmc'].append(r)
        self.word(0xa4ff80,syms['on_player_autochange']) # registered by stock player startup
        self.byte(0xa5002b,1) # mclSetJumpFolder's flag
        self.call(address=syms['mclLoadPlayList'],args=(self.deque(self.dirs['/mnt/mmc/0']),0,1,0),gap=0)
        self.call(address=syms['mclStartPlayer'],args=(0,0,0,0),gap=0)
        self.call(address=syms['player_get_id3info'],args=(0,0,0,0),gap=0)
        self.handlers.pop(payload_syms['press_stamp'])  # the natural changes from here run the real one
    def hook(self,u,address,size,unused):
        if address in getattr(self,'native_trace',{}): self.trace.append((self.native_trace[address],None))
        name=self.handlers.get(address,'')
        if name=='widget_load_image':
            path=self.text(u.reg_read(UC_MIPS_REG_A1))
            self.image_size=None if path.startswith('file://') and path[7:] not in self.artfiles else (50,50)
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        self.trace.append((name,self.text(a) if name in ('directory','decoder_start','toolsGetAlbumCover') else a))
        if name=='directory':
            path=self.text(a); self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',r) for r in self.dirs.get(path,[])]; ret=len(self.dirs.get(path,[]))
        elif name=='decoder_start': self.byte(0xa5002d,1)
        elif name=='malloc': ret=self.alloc((a+3)&~3)
        elif name=='strcpy': u.mem_write(a,self.text(b).encode()+b'\0'); ret=a
        elif name=='strncmp': x,y=self.text(a)[:c],self.text(b)[:c]; ret=(x>y)-(x<y)
        elif name in ('strchr','strstr'):
            i=self.text(a).find(chr(b) if name=='strchr' else self.text(b)); ret=a+i if i>=0 else 0
        elif name=='toolsGetMusicInfo':
            path=self.text(c); album,_=self.files[path]; self.trace.append(('tags',path))
            if album:
                u.mem_write(b,album.encode()+b'\0'); u.mem_write(b+0x100,b'Artist\0')
                u.mem_write(b+0x280,path.rsplit('/',1)[1].encode()+b'\0')
            ret=int(bool(album))
        elif name=='toolsGetFileSize': ret=4096
        elif name=='toolsGetAlbumCover':
            ret=int(self.files[self.text(a)][1])
            if ret: self.artfiles.add(self.text(b))
        elif name=='access': ret=0 if self.text(a) in self.artfiles else -1
        elif name=='remove': self.artfiles.discard(self.text(a))
        elif name=='fork': self.forks+=1; ret=-1  # coverflow_player_cover's ffmpeg, refused
        elif name=='usleep' and self.worker:
            u.reg_write(UC_MIPS_REG_PC,0x1000000); u.emu_stop(); return
        self.returns(ret,clobber=True)
    def tick(self): self.call(address=syms['player_mainscheduling'],args=(0,0,0,0),gap=0)
    def artwork(self):
        self.worker=True
        self.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
        self.u.reg_write(UC_MIPS_REG_T9,syms['player_parsecover_thd'])
        self.u.emu_start(syms['player_parsecover_thd'],0x1000000,count=self.budget)
        assert self.u.reg_read(UC_MIPS_REG_PC)==0x1000000
        self.worker=False

    def ui(self):
        self.top=self.win
        self.call(address=0x53d810,args=(self.info,0,0,0),gap=0) # stock playing_timer_start callback
        self.paint(self.win,gap=0)
        self.top=self.home; self.paint(self.home,gap=0); self.top=self.win
    def setup_ui(self):
        bar=self.node('window','system_bar'); self.word(syms['system_bar'],bar); self.word(bar+O['W_PARENT'],self.wm)
        home_list(self); self.home=self.top; self.art=named(self,self.home,'img_homeart')
        self.call(address=home_hook[0],args=(self.home,0,0,0),gap=0)
        self.cover=self.node('image','img_cover'); self.songlabel=self.node('hscroll_label','scrlabel_title')
        self.albumlabel=self.node('label','label_ipod_album'); self.poslabel=self.node('label','label_ipod_pos')
        self.win=self.node('window','playing_page',[self.cover,self.songlabel,self.albumlabel,self.poslabel])
        self.word(self.win+O['W_PARENT'],self.wm); self.top=self.win
        self.info=self.alloc(0x40); self.word(self.info+0x20,self.win)
        playing=HOOKS['playing_page_init'][0]; self.handlers[playing+12]='stock_playing'
        self.call(address=playing,args=(self.win,0,0,0),gap=0)
        self.byte(syms['g_forcerefresh_flag'],1)

class TagTextMachine(Machine):
    def __init__(self):
        super().__init__()
        self.word(0xa3a0e8,0x1000014); self.handlers[0x1000014]='t:malloc'  # lazy-bound, as RolloverMachine's
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='t:free'
        self.handlers[syms['strcpy@GLIBC_2.0']]='t:strcpy'; self.handlers[syms['toolsIconvConvert']]='t:iconv'
        self.handlers[syms['strcmp@GLIBC_2.0']]='t:strcmp'  # on bytes: GBK isn't UTF-8
        self.mock('snprintf@GLIBC_2.0'); self.iconv=0
        self.handlers.pop(syms['toolsFixId3ErrorCode'])  # stock's own runs here (ogg_music_info calls it)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('t:'): return super().hook(u,address,size,unused)
        a,b,c=[u.reg_read(r) for r in REGS[:3]]; ret=0
        if name=='t:malloc': ret=self.alloc((a+3)&~3)
        elif name=='t:strcpy': u.mem_write(a,self.raw(b)+b'\0'); ret=a
        elif name=='t:strcmp': x,y=self.raw(a),self.raw(b); ret=(x>y)-(x<y)
        elif name=='t:iconv':  # toolsIconvConvert(s, to, from) in place; a failed one leaves s
            self.iconv+=1
            try: u.mem_write(a,self.raw(a).decode(self.text(c)).encode(self.text(b))+b'\0'); ret=1
            except UnicodeError: pass
        self.returns(ret,clobber=True)

class LyricRowsMachine(Machine):
    def __init__(self): super().__init__(); self.handlers[0x53b67c]='rows'
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='rows': return super().hook(u,address,size,unused)
        self.word(self.lrc+O['SCROLL_Y'],0); self.returns(0,clobber=True)

class GridCoverMachine(Machine):
    MOCKS=('toolsGetAlbumCover','toolsGetExternCover','fork@GLIBC_2.0','waitpid@GLIBC_2.0','__errno_location@GLIBC_2.0',
           'fopen@GLIBC_2.2','fseek@GLIBC_2.0','ftell@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0',
           'unlink@GLIBC_2.0','access@GLIBC_2.0','pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0',
           'calloc@GLIBC_2.0')
    def __init__(self):
        super().__init__(); self.mock(*self.MOCKS); self.errno=self.alloc(4); self.word(self.errno,4)  # EINTR
        self.files={}; self.handles={}; self.found=self.extern=0; self.out=None  # ffmpeg's output; None: no fork
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name not in self.MOCKS: return super().hook(u,address,size,unused)
        name=name.split('@')[0]; a,b=(u.reg_read(r) for r in REGS[:2]); ret=0; self.seen.append(name)
        if name=='toolsGetAlbumCover': ret=self.found
        elif name=='toolsGetExternCover': ret=self.extern
        elif name=='fork':
            ret=-1 if self.out is None else 7
            if self.out is not None: self.files[self.dst+'.ff']=self.out
        elif name=='waitpid':
            ret=-1 if self.seen.count('waitpid')==1 else a  # interrupted once, then reaped
            if ret==a: self.word(b,0)
        elif name=='__errno_location': ret=self.errno
        elif name=='fopen': ret=self.alloc(4) if self.text(a) in self.files else 0; self.handles[ret]=self.text(a)
        elif name=='ftell': ret=len(self.files[self.handles[a]])
        elif name=='rename': self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='unlink': self.files.pop(self.text(a),None)
        elif name=='access': ret=0 if self.text(a).startswith('/mnt/mmc/A/') else -1
        elif name=='calloc': ret=self.alloc(a*b)
        self.returns(ret,clobber=True)
    def cover(self,track,fn='coverflow_grid_cover',**kw):
        for k,v in kw.items(): setattr(self,k,v)
        self.seen=[]; self.dst='/mnt/mmc/.sldp/x0.jpg'
        args=(self.string('/mnt/mmc/A/'+track),self.string(self.dst),110,110)
        return self.call(address=payload_syms[fn],args=args,gap=0)

class FolderCoverMachine(GridCoverMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='toolsGetAlbumCover': return super().hook(u,address,size,unused)
        track=self.text(u.reg_read(REGS[0])); self.tried.append(track); self.returns(int(track in self.art),clobber=True)
    def folder(self,track):
        self.tried=[]; self.seen=[]
        dst=self.string('/mnt/mmc/.downpic/fd_x.jpg')
        return self.call(address=payload_syms['coverflow_folder_cover'],args=(self.string(track),dst,80,80),gap=0)

# Coverflow depth (docs/internals.md#coverflow-depth): the payload's renderer, run as MIPS, draws
# byte for byte what the host build of the same source draws (test/coverflow.py checks that
# one's geometry); on the page it spans the frame, draws through the stock canvas and hit-tests taps.
BIG,BIG_SIZE=0x2000000,0x3000000  # the frames, the thumb's 6.8 MB texture pool and Home's large covers
def cover_pixels(seed,n=160*160):
    return b''.join(struct.pack('<I',0xff000000|((i*2654435761+seed*40503)>>7)&0xffffff) for i in range(n))
class DepthMachine(CoverflowMachine):
    budget=50_000_000  # decoding and rendering run in the payload, at native emulator speed (fast)
    def __init__(self,**kw):
        super().__init__(**kw)
        self.u.mem_map(BIG,BIG_SIZE); self.big=BIG
        self.frames=[]; self.destroyed=[]; self.draws=[]; self.loads=[]; self.unloads=0; self.pixels={}
        self.art_size=(160,160)  # the covers' decoded size; the placeholder is always 160 square
        self.art_sizes={}  # by URL ending, sizes other than art_size
        self.handlers[syms['sem_post@GLIBC_2.2']]='loader_wake'  # the loader's thread can't run here: its body, at each wake-up
        self.mock('strstr@GLIBC_2.0')  # mem_available_kb's, before the letters' pool: no /proc/meminfo here, so no estimate
        self.fast()
    def big_alloc(self,n):
        a=self.big; self.big+=(n+15)&~15; assert self.big<=BIG+BIG_SIZE; return a
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        if name=='c:calloc@GLIBC_2.0' and a*b>0x10000: ret=self.big_alloc(a*b); self.live[ret]=a*b
        elif name=='bitmap_create_ex':
            ret=self.big_alloc(0x48); data=self.big_alloc(b*c)
            self.word(ret,a); self.word(ret+4,b); self.word(ret+8,c); self.u.mem_write(ret+0xc,struct.pack('<HH',0,d)); self.word(ret+0x14,data)
            self.frames.append(ret)
        elif name=='bitmap_destroy': self.destroyed.append(a); ret=0
        elif name=='loader_wake':
            self.wakes=getattr(self,'wakes',0)+1
            while self.nested(payload_syms['ld_step']): pass
            ret=0
        elif name=='bitmap_lock_buffer_for_read': ret=self.get(a+0x14)
        elif name=='widget_load_image':
            url=self.text(b); self.loads.append(url) if a==self.top else None  # Coverflow's, not iPod Home's
            self.image_loads.append(url)
            if url in getattr(self,'image_fail',()): self.returns(1,clobber=True); return
            w,h=(160,160) if url=='default_album_flow' else next((v for k,v in self.art_sizes.items() if url.endswith(k)),self.art_size)
            if url not in self.pixels:
                self.pixels[url]=self.big_alloc(w*h*4); self.u.mem_write(self.pixels[url],cover_pixels(len(self.pixels),w*h))
            self.word(c,w); self.word(c+4,h); self.word(c+8,w*4); self.u.mem_write(c+0xc,struct.pack('<HH',2,1)); self.word(c+0x14,self.pixels[url]); ret=0
        elif name=='widget_unload_image': self.unloads+=a==self.top; ret=0
        elif name=='slide_menu_set_spacer': self.word(a+O['SLIDE_SPACER'],b); ret=0
        elif name=='canvas_draw_image':
            src,dst=[tuple(signed(self.get(r+4*i)) for i in range(4)) for r in (c,d)]
            line=self.get(b+8); data=self.get(b+0x14)
            frame=b''.join(bytes(self.u.mem_read(data+y*line,4*dst[2])) for y in range(dst[3]))
            self.draws.append((a,b,src,dst,struct.unpack_from('<H',bytes(self.u.mem_read(b+0xc,2)))[0],frame)); ret=0
        else: return super().hook(u,address,size,unused)
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)

def host_render(textures,cases,style=None,paper=None,windows=False,still=False):
    """The same renderer source built for the host: frames for (frac, mask of drawn slots) cases. style: a theme's cf.* values; paper: the light half's.
    15 textures make the thumb's wide ring; a case's (kind, lean, a, b) then sets the thumb: 1 letters, the row held as an entry from the row at
    lead a/1000 leaves it, 2 a ms into the fold from that row held at lead b/1000, 3 the entry, lead a/1000 and b/1000 albums to go, 4 letters
    with the row held as the case's next five numbers have it (hold_lead, hold_run, hold_xrun, hold_xs, hold_k).
    windows: also each case's window middle from the album at the middle (cf_frame_window), as a second list.
    still: nothing moving, as the page's own frame at rest (the shelf's seven); else moving, as a direct render is."""
    import subprocess, tempfile
    from coverflow import CF_SOURCES, SHIM_H
    from theme import ROLE_MACROS, builtins
    style=style or {f:v for f,v in builtins()[0].items() if f.startswith('cf.')}  # Minimal's, as the payload has it: a thumb theme, its shelf draws seven covers a side
    if len(textures)==7:  # the seven round the centre, the wide ring's ends left out
        textures=[textures[0]]*4+list(textures)+[textures[0]]*4; cases=[(c[0],c[1]<<4,*c[2:]) for c in cases]
    cf=', '.join(f'.{f[3:]} = {v}' for f,v in style.items()); rows=style['cf.view_h']
    main=r"""#include "theme.h"
static theme_t t = { .cf = { %s } };
int client_h(void) { return 290; }
const theme_t *theme(void) { return &t; }
#include <stdio.h>
void coverflow_render(unsigned *, int, int, const unsigned *const[7]);
int ipod_light(void) { return %d; }
int ipod_square_art(void) { return 0; }
unsigned time_now_ms(void) { return 0; } /* the fold's clock: fold_at counts back from 0 */
int pod_value(int i) { return 0; } /* the MIPS machines' own: Fast Navigation off */
void cf_thumb_lead(float), cf_thumb_lean(int), cf_thumb_fold(int, unsigned), cf_thumb_enter(int, float, float);
void cf_thumb_speed(float, float), cf_thumb_held(float, float, float, float, int);
float cf_thumb_hold(int, float, int);
int cf_frame_window(int);
void cf_shelf_still(int);
const scheme_t *scheme(void) { static scheme_t s = { .paper = %d }; return &s; }
unsigned flat_page(void) { return ipod_light() ? scheme()->paper : 0; } /* flat.c's, no card page */
void hold_card_flat(void *c, const void *r, int head, int over) {} /* flat.c's: no flip here */
#define N %d
static unsigned tex[N][160 * 160], frame[%d * %d];
int main(void) {
    const unsigned *ring[N];
    cf_shelf_still(%d);
    if (fread(tex, 4, N * 160 * 160, stdin) != N * 160 * 160) return 1;
    int frac, mask, kind, lean, a, b;
    while (scanf("%%d %%d %%d %%d %%d %%d", &frac, &mask, &kind, &lean, &a, &b) == 6) {
        for (int j = 0; j < N; ++j) ring[j] = mask >> j & 1 ? tex[j] : 0;
        float h[4];
        int k;
        if (kind == 4 && scanf("%%f %%f %%f %%f %%d", h, h + 1, h + 2, h + 3, &k) != 5) return 1;
        cf_thumb_enter(0, 0, 0), cf_thumb_lead(-1), cf_thumb_speed(0, 0);
        if (kind == 1 || kind == 2) cf_thumb_hold(lean, (kind == 1 ? a : b) / 1000.f, 0);
        if (kind == 4) cf_thumb_held(h[0], h[1], h[2], h[3], k);
        if (kind == 1 || kind == 4) cf_thumb_lean(lean);
        if (kind == 2) cf_thumb_fold(lean, -(unsigned)a);
        if (kind == 3) cf_thumb_enter(lean, a / 1000.f, b / 1000.f);
        coverflow_render(frame, %d, frac, ring + (N - 7) / 2);
        fwrite(frame, 4, sizeof(frame) / 4, stdout);
        fprintf(stderr, "window %%d\n", cf_frame_window(frac));
        fflush(stdout);
    }
    return 0;
}""" % (cf,paper is not None,paper or 0,len(textures),rows,O['CF_VIEW_W'],int(still),O['CF_VIEW_W'])
    with tempfile.TemporaryDirectory(prefix='q2-render-') as d:
        d=pathlib.Path(d); (d/'shim.h').write_text(SHIM_H); (d/'main.c').write_text(main); (d/'coverflow_all.c').write_text(CF_SOURCES)
        subprocess.run(['cc','-O1','-DPEQ_HOST','-DPEQ_ROOT=""','-D_GNU_SOURCE',
                        '-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-I',str(ROOT/'patch'),'-include',str(d/'shim.h'),
                        str(d/'coverflow_all.c'),str(d/'main.c'),'-o',str(d/'render')],check=True)
        feed=b''.join(textures)+''.join(' '.join('%.9g'%x if isinstance(x,float) else str(x) for x in (*c,0,0,0,0)[:max(6,len(c))])+'\n' for c in cases).encode()
        run=subprocess.run([str(d/'render')],input=feed,capture_output=True,check=True); out=run.stdout
    size=4*O['CF_VIEW_W']*rows
    frames=[out[i*size:(i+1)*size] for i in range(len(cases))]
    return (frames,[int(l.split()[1]) for l in run.stderr.decode().splitlines() if l.startswith('window ')]) if windows else frames

ps=payload_syms

from theme import builtins as theme_builtins

def columns(px,n=160):
    """An n x n image by rows, red low, as a texture holds it: by columns (texel x, y at x * n + y), the screen's BGRA8888."""
    return bytes(px[4*(y*n+x)+k] for x in range(n) for y in range(n) for k in (2,1,0,3))

def fitted(px,w,h):  # cf_decode's texture of a w x h image
    side=max(w,h); out=[]
    for y in range(160):
        sy=y*side//160-(side-h)//2
        for x in range(160):
            sx=x*side//160-(side-w)//2
            out.append(px[(sy*w+sx)*4:(sy*w+sx)*4+4] if 0<=sx<w and 0<=sy<h else struct.pack('<I',0xff000000))
    return columns(b''.join(out))

class Starved(DepthMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'')=='bitmap_create_ex':
            self.returns(0); return
        return super().hook(u,address,size,unused)

# The flip: Centre on a cover under Classic. Its stock calls are mocked here; the offline canvas is
# stock's own window snapshot path. widget_paint stands in for the panel's paint: a pattern at its screen
# place, BGRA as the screen's lcd, so the frames show what was captured and where.
class FlipMachine(DepthMachine):
    def __init__(self,**kw):
        super().__init__(**kw); self.lcds=[]; self.pattern=0x00203040; self.boxes=[]
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        if name in ('c:list_view_create','c:scroll_view_create'): self.boxes.append((b,c,d,self.get(u.reg_read(UC_MIPS_REG_SP)+16)))  # the machine keeps only some of a box
        if name=='canvas_offline_create':
            ret=self.big_alloc(0x90); lcd=self.big_alloc(0x140); fb=self.big_alloc(a*b*4); self.word(ret+O['CANVAS_LCD'],lcd)
            self.word(lcd+O['LCD_W'],a); self.word(lcd+O['LCD_H'],b); self.word(lcd+O['LCD_LINE_LENGTH'],a*4); self.word(lcd+O['LCD_FORMAT'],c); self.word(lcd+O['LCD_OFFLINE_FB'],fb)
            self.lcds.append(fb)
        elif name=='widget_paint':
            assert self.nodes[a]['visible'],'a hidden widget paints nothing'
            px=struct.pack('<I',0xff000000|self.pattern); self.u.mem_write(self.lcds[-1],px*(375*320))
            ret=0
        else: return super().hook(u,address,size,unused)
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)
    def covers(self): return self.get(self.slide+O['W_PARENT'])
    def panel(self): return next((w for w in self.nodes[self.page]['children'] if cf_geometry(self,w)[2]==294),0)
    def frame(self):
        self.draws=[]; self.call(address=HOOKS['widget_on_paint_border'][0],args=(self.covers(),self.canvas,0,0),gap=0,clear=False,count=50_000_000)
        assert len(self.draws)==1 and self.draws[0][2]==self.draws[0][3]==(0,0,375,294) and self.draws[0][4]&1
        return self.draws[0][5]
    def row_labels(self,i): return self.nodes[self.nodes[self.find('scroll_view')]['children'][i]]['children']
    def face(self,w): return self.nodes[w]["style:normal:font_name"],self.nodes[w]["style:normal:font_size"]
def flip_click(m):  # Centre's click, then the page's timer: the turn has just started
    f,ctx=m.handler(m.nodes[m.slide]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
def flip_open(**kw):
    m=classic_light_new(FlipMachine,**kw); m.open(); flip_click(m); return m
def px_at(frame,x,y): return struct.unpack_from('<I',frame,4*(y*375+x))[0]

def thumb_page(cls=None,**kw):
    m=classic_light_new(cls or DepthMachine,albums=9,**kw); m.open(); m.handlers[sleep_hook+12]='stock_sleep'; m.fresh=1
    return m
def tp(m,travel=0,ms=17):  # a UI loop pass ms on, the wheel turned travel raw units since the last
    if travel: m.word(ps['wheel_travel'],travel&0xffffffff)
    m.advance(ms,clear=False)
    if m.fresh: m.word(ps['wheel_frame_at'],m.now)  # as its frames stamp it
    m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False,debounce=True)
def traw(m): return m.get(ps['wheel_raw'])
def thold(m):  # a thumb down, past a stray contact
    m.word(ps['wheel_held'],1); tp(m); tp(m,ms=50)
def tspin(m,ms=600,units=-20):
    for _ in range(0,ms,17): tp(m,units)
def trest(m):
    m.word(ps['wheel_held'],0)
    for _ in range(200): tp(m)
def tindex(m): return m.get(m.slide+O['SLIDE_INDEX'])
def toff(m): return signed(m.get(m.slide+O['SLIDE_OFFSET']))
def tcaption(m): return m.texts()[0]
CF_FIELDS=[f for l in re.findall(r'^\s*short ([^;]+);',re.search(r'typedef struct \{([^}]*)\} cf_style_t;',(ROOT/'patch/theme.h').read_text())[1],re.M) for f in re.split(r',\s*',l)]
def no_letters(m): m.u.mem_write(ps['cfs']+2*CF_FIELDS.index('jump_ticks'),b'\0\0')  # the open page's cfs: jump_ticks 0, the cap alone

TH_AT={f:4*i for i,f in enumerate(f for l in re.findall(r'^\s*\w+ ([^;]+);',re.sub(r'/\*.*?\*/','',re.search(r'typedef struct \{([^}]*)\} cf_thumb_t;',(ROOT/'patch/coverflow.h').read_text())[1],flags=re.S),re.M) for f in re.split(r',\s*',l))}  # every field 4 bytes

def to_other(m): m.top=m.node('window','home_page')
def to_dialog(m): m.top=m.node('dialog','msginfo_dialog')
def to_np(m): m.top=m.node('window','playing_page')
def thumb_exits():
    def flag(name,on,off): return (lambda m:m.byte(syms[name],on),lambda m:m.byte(syms[name],off))
    back=lambda m: setattr(m,'top',m.page)
    yield 'window',to_other,back; yield 'dialog',to_dialog,back; yield 'now playing',to_np,back
    yield ('dark',)+flag('g_backlight_status',0,1); yield ('locked',)+flag('g_lockscreen_pageflag',1,0)
    yield ('usb',)+flag('g_usblink_status',2,0); yield ('power',)+flag('g_poweroff_state',2,0); yield ('test',)+flag('g_testmode_flag',1,0)
    yield 'sliding',(lambda m:setattr(m,'animating',1)),(lambda m:setattr(m,'animating',0))
    yield 'finger',(lambda m:setattr(m,'pressed',1)),(lambda m:setattr(m,'pressed',0))

def letter_on(m): return any(v[1]==ps['cf_letter_expire'] for v in m.timers.values())

def tfloat(m,f): return struct.unpack('<f',m.u.mem_read(ps['th']+TH_AT[f],4))[0]
def ttarget(m): return (tindex(m)+int(tfloat(m,'goal')))%len(FRUIT)  # where the row is going
def tarrive(m):  # the thumb still, the row there
    for _ in range(300):
        if tfloat(m,'pos')==tfloat(m,'goal'): return
        tp(m)
    assert False
FRUIT=('Apple','Avocado','Banana','Blueberry','Cherry','Date','Elder','Fig','Grape')

def fruit_page(big):
    m=classic_light_new(DepthMachine,albums=9)
    if big: m.config['BIGLIBRARY']='1'
    for r,name in zip(m.albums,FRUIT): m.word(r+O['REC_ALBUM'],m.string(name))
    m.open(); m.handlers[sleep_hook+12]='stock_sleep'; m.fresh=1; tp(m); return m
def cfs(m,f): return struct.unpack('<h',m.u.mem_read(ps['cfs']+2*CF_FIELDS.index(f),2))[0]

def theme_offset(field):  # offsetof(theme_t, field) as the payload's own compiler lays it out for MIPS o32
    import subprocess
    src=f'#include <stddef.h>\n#include "theme.h"\nint x = offsetof(theme_t, {field});\n'
    ir=subprocess.run(['clang','--target=mipsel-linux-gnu','-mabi=32','-ffreestanding','-I',str(ROOT/'patch'),'-x','c','-','-S','-emit-llvm','-o','-'],
                      input=src,capture_output=True,text=True,check=True).stdout
    return int(re.search(r'^@x = .*global i32 (\d+)',ir,re.M)[1])

def twake(m,kind,key=O['KEY_CENTER'],gap=30):
    inp=HOOKS['window_manager_dispatch_input_event'][0]; m.handlers[inp+12]='stock_input'; n=len(m.calls)
    assert m.call(key,address=inp,args=(m.wm,m.event,0,0),event_type=kind,gap=gap,clear=False,debounce=True)==0
    return any(c[0]=='stock_input' for c in m.calls[n:])

def th_set(m,kind=0,lean=0,a=0,b=0):  # host_render's (kind, lean, a, b)
    F=('lead','enter','vel','lead_v','pos')
    def put(**v):
        for f,x in v.items(): m.u.mem_write(ps['th']+TH_AT[f],struct.pack('<f',x) if f in F else struct.pack('<I',int(x)&0xffffffff))
    put(vel=0,lead_v=0)
    if kind in (1,2):  # the row held, as coverflow_hold leaves it from that entry
        put(pos=0,lean=lean,lead=(a if kind==1 else b)/1000); m.call(address=ps['coverflow_hold'],args=(0,0,0,0),gap=0)
    put(on=kind>0,jump=kind in (1,3),fold=kind==2,lean=lean,fold_at=m.now-a if kind==2 else 0,lead=a/1000 if kind==3 else 0,enter=b/1000 if kind==3 else 0)

class HalfMachine(FileMocks,DepthMachine):
    def __init__(self,**kw): super().__init__(**kw); self.file_mocks('h:'); self.opened=[]; self.cut=False
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('h:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; self.calls.append((name,a,b,c))
        if name=='fopen' and self.text(a).endswith('.jpg.half') and self.text(b)=='rb':  # each album's own copy
            path=self.text(a); self.opened.append(path)
            self.files.setdefault(path,cover_pixels(300+len(self.files),80*80)[:100 if self.cut else None])
        self.returns(self.file_op(u,name,a,b,c,d),clobber=True)
def spread(h):  # a half copy's texels (the file's, by rows), each two by two, as a texture
    rows=[b''.join(h[4*(y*80+x):4*(y*80+x)+4]*2 for x in range(80)) for y in range(80)]
    return columns(b''.join(r+r for r in rows))

class PlArtMachine(Machine):
    def __init__(self):
        super().__init__(); self.sql=[]; self.threads=0; self.order=[]; self.usb=0
        self.mock('mkdir@GLIBC_2.0')
        for n in ('sqlite3_open','sqlite3_exec','sqlite3_close','pthread_join@GLIBC_2.0','pthread_create@GLIBC_2.2','navigator_window_is_exist'): self.handlers[syms[n]]='pa:'+n
        self.handlers[int(manifest['patch_symbols']['stock_usbmode_trampoline'],16)]='pa:usb'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('pa:'): return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; name=name[3:]
        if name=='sqlite3_open': self.sql.append(('open',self.text(a))); self.word(b,0xdb)
        elif name=='sqlite3_exec': self.sql.append(('exec',a,self.text(b)))
        elif name.startswith('pthread_create'): self.threads+=1; self.word(a,0x77)
        elif name.startswith('pthread_join'): self.order.append(('join',a))
        elif name=='usb': self.order.append(('stock',self.get(payload_syms['pl']+20)))  # pl.running
        elif name=='navigator_window_is_exist': assert self.text(a)=='usbmode_page'; self.returns(self.usb); return
        self.returns(0)

from build import fileoff, STOCK_DB_OPEN

class NoCanvas(FlipMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'')=='canvas_offline_create': self.returns(0,clobber=True); return
        return super().hook(u,address,size,unused)

def flip_held(m): return dict(m.live),set(m.frames)-set(m.destroyed)
FLIP_BACK=294*285*4  # the back buffer, 4 bytes a pixel, by the size calloc was asked for
def flip_backs(m): return [p for p,n in m.live.items() if n==FLIP_BACK]

def rest_view(m):  # the covers' paint composed: each draw's source rect at its place, then each fill
    m.draws=[]; m.bands=[]; m.call(address=HOOKS['widget_on_paint_border'][0],args=(m.covers(),m.canvas,0,0),gap=0,clear=False,count=50_000_000)
    img=[[None]*375 for _ in range(294)]
    for _,b,src,dst,_,_ in m.draws:
        line,data=m.get(b+8),m.get(b+0x14)
        for y in range(dst[3]):
            row=bytes(m.u.mem_read(data+(src[1]+y)*line+4*src[0],4*dst[2])); img[dst[1]+y][dst[0]:dst[0]+dst[2]]=struct.unpack(f'<{dst[2]}I',row)
    for x,y,w,h,c,_ in m.bands:
        for yy in range(y,y+h): img[yy][x:x+w]=[c]*w
    return img

class LandMachine(FlipMachine):
    def hook(self,u,address,size,unused):
        opening=self.handlers.get(address,'')=='c:navigator_to_with_context' and self.text(u.reg_read(REGS[0]))=='playing_page'
        ret=super().hook(u,address,size,unused)
        if opening:
            art=self.node('image','img_cover'); self.np=self.node('window','playing_page',[art]); self.word(art+O['W_PARENT'],self.np)
            for off,v in (('W_X',O['SPOT_ART_X']),('W_Y',O['NP_IPOD_ART_Y']),('W_W',O['NP_IPOD_ART']),('W_H',O['NP_IPOD_ART']+40)): self.word(art+O[off],v)
            self.top=self.np; self.stack.append(self.np); self.np_art=art
        return ret
def land_click():
    m=classic_light_new(LandMachine); m.stock_load=1; m.open(); flip_click(m); m.advance(300); view=m.find('scroll_view'); r=m.nodes[view]['children'][1]
    f,ctx=m.handler(r,O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); assert not m.plays
    return m
def landing(m): return m.call(address=payload_syms['coverflow_landing'],args=(0,0,0,0),gap=0)
def land_paint(m):
    m.draws=[]; m.call(address=payload_syms['coverflow_paint'],args=(m.np,m.canvas,0,0),gap=0); assert len(m.draws)==1 and m.draws[0][3]==(0,0,375,294)  # its first paint: ms 0, opaque

# An unknown length is blank and what the cache has shows, toolsTimeItoa's 4:54, hours when long. The panel starts no worker and
# writes nothing; Long Songs fills the cache, and the next panel has it.
DUR_CACHE='/mnt/data/ringnav-durations-v1'
def dur_key(p):
    h=0xcbf29ce484222325
    for c in p.encode(): h=(h^c)*0x100000001b3&(1<<64)-1
    return h
def duration_cache(*pairs):
    return b'Q2DUR2\0\0'+b''.join(struct.pack('<Q3Ii',k,1000,2000,0,v) for k,v in sorted((dur_key(p),v) for p,v in pairs))
def duration_pairs(data,paths):
    assert data[:8]==b'Q2DUR2\0\0'
    keys={k:sec for k,_,_,_,sec in struct.iter_unpack('<Q3Ii',data[8:])}
    return {p:keys[dur_key(p)] for p in paths if dur_key(p) in keys}
def duration_syscalls(m):
    """The duration worker's syscalls: thread_lower's, answered 0, and ringnav_statx's (4366), a regular file of
    1000 bytes at mtime 2000 (struct statx: stx_mode at 28, stx_size 40, stx_mtime 112), its buffer the fifth argument."""
    def syscall(u,intno,unused):
        if u.reg_read(UC_MIPS_REG_V0)==4366:
            buf=m.get(u.reg_read(UC_MIPS_REG_SP)+16); u.mem_write(buf,bytes(256))
            m.word(buf+28,0o100644); m.word(buf+40,1000); m.word(buf+112,2000)
        u.reg_write(UC_MIPS_REG_A3,0); u.reg_write(UC_MIPS_REG_V0,0)
    m.u.hook_add(UC_HOOK_INTR,syscall)

class FlipLengths(FileMocks,FlipMachine):
    def __init__(self,**kw):
        super().__init__(**kw); self.file_mocks('fl:'); self.lengths={}; self.probed=[]
        for n in ('fseek@GLIBC_2.0','ftell@GLIBC_2.0','taglib_file_new','taglib_file_audioproperties','taglib_file_free','getAllMusic'): self.handlers[syms[n]]='fl:'+n
        self.handlers[0x1000030]='fl:length'; duration_syscalls(self)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('fl:'): return super().hook(u,address,size,unused)
        name=name[3:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name in ('fopen','fread','fwrite','fclose','rename'): ret=self.file_op(u,name,a,b,c,d)
        elif name=='ftell': ret=len(self.files[self.open_files[a][0]])
        elif name=='taglib_file_new':
            path=self.text(a); self.probed.append(path); ret=self.alloc(8); props=self.alloc(8); vt=self.alloc(16); self.word(props,vt); self.word(vt+12,0x1000030); self.word(ret,props); self.word(props+4,self.lengths[path])
        elif name=='taglib_file_audioproperties': ret=self.get(a)
        elif name=='length': ret=self.get(a+4)
        elif name=='getAllMusic': self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',r) for r in self.found]; ret=len(self.found)
        self.returns(ret,clobber=True)

class FlipStarved(FlipMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'')=='bitmap_create_ex': self.returns(0,clobber=True); return
        return super().hook(u,address,size,unused)

class FlipSecond(FlipMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'')=='bitmap_create_ex' and self.frames: self.returns(0,clobber=True); return
        return super().hook(u,address,size,unused)

def bspline(t):  # bspline_w: the cubic B-spline's 4 weights at phase t/256, in 1/256
    s=256-t; w0=(s**3+196608)//393216; w3=(t**3+196608)//393216; w1=(3*t**3+4*16777216-6*256*t*t+196608)//393216
    return w0,w1,256-w0-w1-w3,w3
def tap4(a,b,c_,d,w):  # tap4: 4 texels weighted, rounded
    return (0xff000000|((a&0xff00ff)*w[0]+(b&0xff00ff)*w[1]+(c_&0xff00ff)*w[2]+(d&0xff00ff)*w[3]+0x800080)>>8&0xff00ff
            |((a&0xff00)*w[0]+(b&0xff00)*w[1]+(c_&0xff00)*w[2]+(d&0xff00)*w[3]+0x8000)>>8&0xff00)

def lum(c):
    v=[(c>>s&255)/255 for s in (16,8,0)]; v=[x/12.92 if x<=0.04045 else ((x+0.055)/1.055)**2.4 for x in v]
    return 0.2126*v[0]+0.7152*v[1]+0.0722*v[2]
def ratio(a,b): return (max(lum(a),lum(b))+0.05)/(min(lum(a),lum(b))+0.05)

def mapped(c,preset,tone=3): return Machine().call(address=ps['accent_map'],args=(c,preset,tone,0),gap=0)&0xffffffff

def close(x,y): return x>>24==y>>24 and all(abs((x>>s&255)-(y>>s&255))<=2 for s in (0,8,16))

def begin(m,w): m.call(address=HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0),gap=0)

S=O['CONFIRM_SURFACE']

ACTIVE=('drop_wifiopen','drop_btopen','drop_keylockopen','drop_highgain','drop_lo','drop_usbaudio','drop_usbdac')
INACTIVE=('drop_wifi','drop_bt','drop_keylock','drop_lowgain','drop_po','drop_usbstorage','drop_playset','drop_sysset')

def perceived(c): return ((c>>16)*299+(c>>8&255)*587+(c&255)*114)//1000

def drawn_list(config):
    k=Machine(); k.config=dict(config); kw,_=k.page_list(5,extent=1000); k.paint(kw)
    return k.bands,k.rounded,k.strokes

from ipod import imagemagick

def setting_rows(m,view):  # Accent, Home, Battery, Theme: as ringnav_display's index
    lab=lambda r: m.nodes[m.nodes[m.nodes[r]['children'][0]]['children'][1]].get('text','')
    kids=m.nodes[view]['children']
    return [next(r for r in kids if lab(r).startswith(k)) for k in ('Accent','Home','Battery','Theme')]

from build import segments
from ipod import AUDIT, VALUE_NAMES

SITES={int(a,16):g['value'] for g in AUDIT['immediates'] if isinstance(g['value'],str) for a,_ in g['sites']}
def set_geom(m,g):  # the active theme's theme_t.geom (THEME_GEOM_OFF): a Theme switch to set g
    m.byte((m.call(address=payload_syms['theme'],args=(0,0,0,0),gap=0)&0xffffffff)+THEME_GEOM_OFF,g)
def set_value(m,name,v): m.u.mem_write(payload_syms['boot_values']+2*VALUE_NAMES.index(name),struct.pack('<H',v))

def classic_light_on(nm,rowart='1'):  # Classic, theme_boot done: st.geom is GEOM_CLASSIC_LIGHT (GEOM_CLASSIC_LIGHT_TEXT at ROWART=0)
    classic_light_boot(nm,rowart); return nm
def classic_light_bar(nm,*children):  # the system bar under Classic's set: 375x26
    b=nm.node('system_bar','system_bar',list(children)); nm.word(b+O['W_PARENT'],nm.wm)
    nm.word(b+O['W_W'],375); nm.word(b+O['W_H'],26); nm.word(syms['system_bar'],b); return b
def channels(c): return (c&255,c>>8&255,c>>16&255)  # color_t's R, G, B

def classic_light_battery_bar(mode,image='bar_battery',text='50%',light='1',status=None,eq=None,wifi=0,state=0,cable=0):
    bm=Machine(); bm.config.update(LIGHT=light,BATTERY=str(mode)); classic_light_on(bm)  # before the boot reads the settings
    bm.image_size=(24,11)  # classic_light_bolt's and classic_light_plug's real size (test/unit/test_art.py)
    bm.word(syms['g_power_chargestate'],state); bm.byte(syms['g_usbdet_value'],cable)  # 2: Full
    if status is not None: bm.handlers.pop(syms['mclGetPlayStatus'],None); bm.word(0xa50084,status)  # local's play state
    lv=bm.node('progress_bar','progress_battery',value=50)
    bw_={n:bm.node(t,n,list(c),visible=v,**kw) for n,t,c,v,kw in (
        ('img_bt','image',(),0,dict(image=O_GLYPH)),('img_wifi','image',(),wifi,{}),('label_battery','label',(),0,dict(text=text)),
        ('view_battery','view',(),0,{}),('img_battery','image',(lv,),1,dict(image=image)),('img_state','image',(),0,{}),
        *((('label_eq','label',(),eq,{}),) if eq is not None else ()))}
    bm.word(bw_['img_wifi']+O['W_W'],14); bm.word(bw_['img_state']+O['W_W'],15)
    if eq is not None: bm.word(bw_['label_eq']+O['W_W'],28)
    brow=bm.node('view','view_right',list(bw_.values())); bm.word(brow+O['W_H'],22); bm.word(bw_['view_battery']+O['W_PARENT'],brow)  # Classic's row (tools/ipod.py): the body centres on it
    bbar=classic_light_bar(bm,brow)
    paint=lambda x=bbar: bm.call(address=HOOKS['widget_on_paint_background'][0],args=(x,bm.canvas,0,0),gap=0)
    paint(); paint(); bm.bands=[]
    cost=[0]; hook=bm.u.hook_add(UC_HOOK_CODE,lambda *a: cost.__setitem__(0,cost[0]+1),begin=BASE,end=SCRATCH-1)
    paint(bw_['view_battery']); bm.u.hook_del(hook); assert cost[0]<100000,cost  # the battery under the cap
    return bm,bw_,[b[:5] for b in bm.bands]

def a_(rgb,a): return color_t(rgb)&0xffffff|a<<24

def into_paint(theme,light,row=0):
    pm=Machine(); pm.config.update(THEME=theme,LIGHT=light,CLASSIC_LIGHT_ACCENT='1',MINIMAL_ACCENT='1')  # a hue (Graphite's silver is neutral)
    if theme=='1': classic_light_on(pm)
    pw,pes=pm.page_list(10,height=96,extent=480,name='localmusic_page'); window(pm,'localmusic_page',pw); pm.paint(pw)
    img=pm.node('image','into',image='list_into'); pm.nodes[pes[row]]['children']=[img]
    pm.word(img+O['W_PARENT'],pes[row]); pm.word(img+O['W_W'],50); pm.word(img+O['W_H'],48); pm.paint(img)
    return pm,[i[:2] for i in pm.icons],[c for c in pm.calls if c[0] in ('canvas_fill_rounded_rect','canvas_set_fill_color','canvas_fill_rect')]

class RockboxMachine(Machine):
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a=u.reg_read(UC_MIPS_REG_A0); self.calls.append((name,a))
        self.returns(0x2000000 if name=='fopen' and self.text(a)=='/tmp/q2pod-rockbox' else 0)  # any other file is missing
def rockbox(card):
    m=RockboxMachine(); m.rockbox_mode=0o100755 if card else 0
    for n in ('fopen@GLIBC_2.2','fclose@GLIBC_2.2','system@GLIBC_2.0'): m.handlers[syms[n]]='r:'+n
    m.mock('save_memoryplay_info','player_stop','switch_charge_enable','navigator_to_with_context')
    assert m.call(address=payload_syms['rockbox_open'],args=(0,m.event,0,0),gap=0)==0
    return m,[c[0] for c in m.calls]

from ipod import corner_inset
class SettingsMachine(Machine):
    def hook(self,u,address,size,x):
        name=self.handlers.get(address,'')
        if name=='stock_list_layout':
            assert u.reg_read(UC_MIPS_REG_T9)==address
            view=u.reg_read(UC_MIPS_REG_A1); lst=self.get(view+O['W_PARENT']); y=0
            ih,dh=(self.get(lst+O[k]) for k in ('ROW_HEIGHT','LIST_DEFAULT_ITEM_HEIGHT'))
            for c in self.nodes[view]['children']:
                if not self.nodes[c]['visible']: continue
                if not self.get(c+O['W_W']): self.word(c+O['W_W'],self.get(view+O['W_W']))
                h=ih or self.get(c+O['W_H']) or dh
                self.word(c+O['W_Y'],y); self.word(c+O['W_H'],h); y+=h
            self.word(view+O['VIEW_CONTENT_H'],y); self.layouts+=1
            self.returns(0); return
        if name=='widget_on' and u.reg_read(UC_MIPS_REG_A1)==O['EVT_CLICK']:  # a clickable row
            a=u.reg_read(UC_MIPS_REG_A0); em=self.alloc(4); it=self.alloc(0x28)
            self.word(a+O['W_EMITTER'],em); self.word(em,it); self.word(it+O['EMIT_TYPE'],O['EVT_CLICK'])
            self.nodes[a]['click']=(u.reg_read(UC_MIPS_REG_A2),u.reg_read(UC_MIPS_REG_A3))
        if name=='image_set_draw_type': self.nodes[u.reg_read(UC_MIPS_REG_A0)]['draw_type']=u.reg_read(UC_MIPS_REG_A1)
        return super().hook(u,address,size,x)
SET={k:O['SET_'+k] for k in ('ROW','TOP','ROWS','ICON','ICON_X','GAP','TEXT_X','EDGE','STOCK_ROW')}

def stock_list(boot,default):
    m=SettingsMachine(); m.layouts=0; boot(m); m.handlers[O['LIST_VIEW_LAYOUT']]='stock_list_layout'
    m.mock('widget_move_resize','image_set_draw_type')
    view=m.node('scroll_view','scroll_view_more'); lst=m.node('list_view','list_view_more',[view]); m.top=m.node('window','playermore_page',[lst])
    m.word(view+O['W_PARENT'],lst); m.word(lst+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm); m.nodes[m.wm]={'type':'window_manager','children':[m.top]}
    m.word(lst+O['ROW_HEIGHT'],0); m.word(lst+O['LIST_DEFAULT_ITEM_HEIGHT'],default); m.word(view+O['W_W'],375); m.word(view+O['W_H'],294)
    return m,view

class TitleMachine(ArtistMachine):
    def hook(self,u,address,size,unused):
        if address==syms['getFormatString']:
            self.keys.append(self.text(u.reg_read(REGS[0]))); self.u.mem_write(u.reg_read(REGS[1]),'Unknown Artist'.encode()+b'\0'); self.returns(0); return
        return super().hook(u,address,size,unused)

def chev_cost(n):
    k=classic_light_on(Machine()); w,_=k.page_list(n,height=96,extent=48*n,name='playlist_page'); window(k,'playlist_page',w); k.paint(w); c=[0]; h=k.u.hook_add(UC_HOOK_CODE,lambda *a: c.__setitem__(0,c[0]+1)); k.paint(w); k.u.hook_del(h); assert len(k.icons)==1; return c[0]

def qs_move(m,win,y,x=200):
    m.word(m.event+O['EVENT_Y'],y)  # EVENT_X is EVENT_KEY's word
    m.call(x,address=HOOKS['widget_dispatch'][0],args=(win,m.event,0,0),event_type=O['EVT_POINTER_MOVE_BEFORE']-1,gap=0); return m.get(syms['g_pressdown_y'])

def stacked_row(m,view):
    title,detail=m.node('hscroll_label'),m.node('hscroll_label')
    for c,box in ((title,(10,12,266,20)),(detail,(10,44,266,16))):
        for k,v in zip(('W_X','W_Y','W_W','W_H'),box): m.word(c+O[k],v)
    b=m.node('button',children=[title,detail])
    for k,v in zip(('W_X','W_W','W_H'),(20,335,70)): m.word(b+O[k],v)
    item=m.node('list_item',children=[b]); m.word(item+O['W_H'],78); m.nodes[view]['children'].insert(1,item)
    return item,title,detail

from fontTools.ttLib import TTFont

def row_labels(m,k):
    n=m.nodes[k]
    if n['type']=='hscroll_label' and n.get('text') and not re.fullmatch(r'[a-z0-9_]+',n['text']): yield k
    for c in n.get('children',[]): yield from row_labels(m,c)
def stock_rows(m,view,n):  # stock's own rows (list_item, button at x 20, its label), as its page built them
    for _ in range(n):
        item=m.node('list_item'); lab=m.node('hscroll_label'); b=m.node('button','0',[lab]); m.nodes[item]['children']=[b]
        for k,parent in ((item,view),(b,item),(lab,b)): m.word(k+O['W_PARENT'],parent)
        for k,rect in ((b,(20,0,335,70)),(lab,(72,0,200,70))): [m.word(b_,v) for b_,v in zip((k+O['W_X'],k+O['W_Y'],k+O['W_W'],k+O['W_H']),rect)]
        m.word(item+O['W_H'],78); m.nodes[view]['children'].append(item)

def stops_row(stops,j,h):  # paint_stops' pixel row j of h, as the C mixes it
    p=j*25600//(h-1); k=0
    while k+2<len(stops) and p>stops[k+1][0]*256: k+=1
    (a,ca),(b,cb)=stops[k],stops[k+1]; n=(b-a)*256; i=p-a*256
    return sum(((ca>>s&255)*(n-i)+(cb>>s&255)*i)//n<<s for s in (0,8,16))

def action_page(nm,sorts=True):
    alabel=nm.node('hscroll_label',text='Play All'); aplay=nm.node('image'); asort=nm.node('image')
    for c,box in ((alabel,(70,0,130,50)),(aplay,(16,0,50,50)),(asort,(306,0,50,50))):
        for off,v in zip(('W_X','W_Y','W_W','W_H'),box): nm.word(c+O[off],v)
    aview=nm.node('view','view_navbar_allplay',[alabel,aplay]+[asort]*sorts); nm.word(aview+O['W_H'],28)
    awin=nm.node('window','allmusic_page',[aview]); nm.top=awin; nm.word(awin+O['W_PARENT'],nm.wm)
    atitle=nm.node('hscroll_label','label_title'); abar=classic_light_bar(nm,atitle)
    nm.call(address=HOOKS['widget_on_paint_background'][0],args=(abar,nm.canvas,0,0),gap=0)
    return alabel,aplay,asort,abar

SEP={'1':(0xfcfcfc,0xe2e3e2,0xf7f7f7),'0':(0x000000,0x2c2c2c,0x000000)}
def song_list(boot,address=0x4ac588,n=7):
    m=Machine(); boot(m); w=m.page('allmusic_page','table_client')
    m.mock('table_row_create','button_create','image_create','view_create','hscroll_label_create','gif_image_create',
           'widget_use_style','widget_set_name','widget_set_children_layout','image_set_draw_type','image_base_set_image',
           'set_hscroll_label_attribute','widget_set_visible')
    assert m.call(address=address,args=(w,w,n,0))==0
    m.word(w+O['ROW_HEIGHT'],52); m.word(w+O['TABLE_ROWS'],n); m.word(w+O['W_H'],266); m.word(w+O['W_W'],375)
    for i,row in enumerate(m.nodes[w]['children']):
        button=m.nodes[row]['children'][0]; em=m.alloc(4); it=m.alloc(0x28)  # a tap target, as entry()
        m.word(button+O['W_EMITTER'],em); m.word(em,it); m.word(it+O['EMIT_TYPE'],O['EVT_CLICK'])
        m.word(row+O['ROW_INDEX'],i); m.word(row+O['W_Y'],52*i); m.word(row+O['W_PARENT'],w)
        m.word(button+O['W_PARENT'],row); native_row_layout(m,button)
    m.bands=[]; m.call(address=HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0),gap=0)
    return m,w
def sep_bands(m,light='1'): return [b for b in m.bands if b[4] in [color_t(c) for c in SEP[light]]]

def tt_bar(timetitle):
    ttm=Machine(); ttm.config['TIMETITLE']=timetitle; classic_light_on(ttm); ttm.clock_minutes=True
    ttt=ttm.node('hscroll_label','label_title'); ttc=ttm.node('hscroll_label','label_clock'); ttb=classic_light_bar(ttm,ttt,ttc)
    def show(win):
        ttm.top=win; ttm.word(win+O['W_PARENT'],ttm.wm)
        ttm.call(address=HOOKS['widget_on_paint_background'][0],args=(ttb,ttm.canvas,0,0),gap=0); ttm.advance(1)
        return ttm.nodes[ttt].get('text',''),ttm.nodes[ttc].get('text')
    return ttm,ttc,show

def half_of(k): return k.call(address=payload_syms['scheme'],args=(0,0,0,0),gap=0)&0xffffffff

def art_boot(config):  # the names a fresh Classic's first system_bar loaded, with config stored
    k=Machine(); k.config.update(config); return classic_light_boot(k)

class RestartMachine(QueueMachine):
    answer=0  # the confirm's: 10 is OK
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='q:navigator_to_with_context' and self.text(u.reg_read(REGS[0]))=='dialog/confirminfo_dialog':
            self.prompts.append(self.text(u.reg_read(REGS[1])+8)); self.returns(self.answer); return
        super().hook(u,address,size,unused)
def offered(k): classic_light(k); k.config['THEME']='1'  # Classic, its light half unless LIGHT is stored

def font_row(setup,text):
    k=Machine()
    if setup: setup(k)
    w=k.page('artistinfo_page','scroll_view'); k.word(w+O['W_PARENT'],k.top); k.word(k.top+O['W_PARENT'],k.wm)
    k.nodes[w]['name']='scroll_view_track'; k.row_record=k.alloc(0x80)
    for off in (8,12,16,24): k.word(k.row_record+off,k.string(text))
    k.word(k.row_record+0x48,1)
    k.mock('button_create','list_item_create','image_create','view_create','hscroll_label_create',
           'gif_image_create','widget_use_style','widget_set_name','widget_set_text_utf8',
           'widget_set_visible','widget_on','widget_destroy_children','image_set_draw_type',
           'image_base_set_image','set_hscroll_label_attribute','hscroll_label_set_only_focus',
           'hscroll_label_set_ellipses','hscroll_label_set_speed','gif_image_play','deque_at',
           'deque_size','tk_snprintf','batch_get_selectitem','file_is_playing','getFormatString',
           'getMusicByPlayList','get_albumcover_listsize','mclGetPlayStatus','list_get_img_pic',
           'strcmp@GLIBC_2.0','toolsTrimLeft','toolsTrimRight')
    k.handlers[0x4ad020]='row_toolbar'
    assert k.call(address=0x4a7f44,args=(w,0,0,0))==0
    button,=[n for n,v in k.nodes.items() if v['type']=='button']
    native_row_layout(k,button)
    kids=k.nodes[button]['children']  # the title as compact_row_layout finds it (check_title_bounds' rule)
    text=next(c for c in kids if k.nodes[c]['type']=='hscroll_label' or any(k.nodes[t]['type']=='hscroll_label' for t in k.nodes[c]['children']))
    return k,button,next((c for c in k.nodes[text]['children'] if k.nodes[c]['type']=='hscroll_label'),text)

class LoadMachine(Machine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='tbl:load': return super().hook(u,address,size,unused)
        self.calls.append(('tbl:load',*[u.reg_read(r) for r in REGS[:3]]))
        for w,t in self.bound: self.nodes[w]['text']=t
        self.returns(0)
def table_load_faces(setup):
    lk=LoadMachine()
    if setup: setup(lk)
    title,tall,plain=(lk.node('hscroll_label',text='WurtS') for _ in range(3))
    box=lk.node('view',children=[tall,plain]); row=lk.node('table_row',children=[title,box]); client=lk.node('table_client',children=[row])  # as stock's rows: labels in a box too
    for w,parent in ((title,row),(box,row),(tall,box),(plain,box)): lk.word(w+O['W_PARENT'],parent)
    lk.word(row+O['W_PARENT'],client); lk.word(client+O['TABLE_LOAD_DATA'],0x1000900); lk.handlers[0x1000900]='tbl:load'
    for w,role in ((title,0),(tall,4)): lk.call(address=payload_syms['theme_font'],args=(w,role,-2,0),gap=0)
    def load(*texts):
        lk.bound=list(zip((title,tall,plain),texts)); lk.calls=[]
        assert lk.call(address=payload_syms['ringnav_table_load'],args=(0x1234,7,row,0),gap=0)==0 and lk.calls[0]==('tbl:load',0x1234,7,row)
        return [(lk.nodes[w].get('style:normal:font_name'),lk.nodes[w].get('style:normal:font_size')) for w in (title,tall,plain)]
    if setup:
        assert load('WurtS','yama','x')==[('classic-light',27),('classic-light',28),(None,None)]
        assert load('星野 源','須田景凪','原因は自分にある。')==[('default',22),('default',23),(None,None)]
        assert load('YOASOBI','Навстречу','x')==[('classic-light',27),('bold',23),(None,None)]
    else: assert load('星野 源','須田景凪','x')==[(None,None)]*3 and not [p for w in (title,tall) for p in lk.nodes[w] if p=='_font']

NEXT,PREV,CENTER,RETURN=O['KEY_NEXT'],O['KEY_PREV'],O['KEY_CENTER'],O['KEY_RETURN']

def buzzes(m): return sum(c[0]=='system@GLIBC_2.0' and m.text(c[1])=='cmd_mcu write_str buzzer' for c in m.calls)

def long_list(n=40,m=None):
    m=m or Machine(); w,es=m.page_list(n,extent=n*48,name='allmusic_page'); m.paint(w); return m,w,es

def fnv(s,h=2166136261):
    for c in s.encode(): h=((h^c)*16777619)&0xffffffff
    return (h*16777619)&0xffffffff

class ShuffleMachine(CoverflowMachine):
    dbquery=int(manifest['patch_symbols']['stock_dbquery_trampoline'],16)
    def __init__(self):
        super().__init__()
        self.counts=b''; self.queued=None; self.wifi=-1
        for n in ('getAllMusic','toolsRandnum','widget_restack','fread@GLIBC_2.0','fclose@GLIBC_2.2','get_wifisignal',
                  'opendir@GLIBC_2.0','readdir@GLIBC_2.0','closedir@GLIBC_2.0','navigator_to'): self.handlers[syms[n]]='s:'+n
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0','memcmp@GLIBC_2.0')
        self.handlers[syms['fopen@GLIBC_2.2']]='s:fopen'
        self.handlers[int(manifest['patch_symbols']['stock_localmusic_trampoline'],16)]='stock_localmusic'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if address==syms['navigator_to_with_context'] and self.text(u.reg_read(REGS[0]))=='playing_page':
            self.queued=self.names(self.get(u.reg_read(REGS[1])))
        if address in (syms['toolsQueryDbTable'],self.dbquery) and self.counted(u,address): return
        if not name.startswith('s:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b=u.reg_read(REGS[0]),u.reg_read(REGS[1]); ret=0; self.calls.append((name,a,b))
        if name=='fopen': ret=1 if self.text(a)=='/mnt/data/ringnav-plays' and self.counts else 0
        elif name=='fread': self.u.mem_write(a,self.counts); ret=1
        elif name=='getAllMusic':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.found]; ret=len(self.found)
            self.calls[-1]=(name,a,self.get(payload_syms['library_music']))  # scoped to music (MUSIC_WHERE)
        elif name=='toolsRandnum': ret=a-1  # stock: rand() % a
        elif name=='memcmp':
            n=u.reg_read(UC_MIPS_REG_A2); x,y=bytes(self.u.mem_read(a,n)),bytes(self.u.mem_read(b,n)); ret=(x>y)-(x<y)
        elif name=='get_wifisignal': ret=self.wifi
        elif name=='opendir': ret=0x3000000 if self.text(a)=='/mnt/mmc' else 0; self.listed=list(self.card)
        elif name=='readdir' and self.listed:
            n,t=self.listed.pop(0); ret=self.alloc(268); self.byte(ret+10,t); self.u.mem_write(ret+11,n.encode()+b'\0')
        elif name=='widget_restack':
            kids=next(n['children'] for n in self.nodes.values() if a in n['children']); kids.remove(a); kids.insert(b,a)
        self.returns(ret,clobber=True)
    def counted(self,u,address):
        """Most Played's reads over found as songtable's rows: its pass over every song's id, path and CUE start
        (library_lists.c counted_query, on toolsQueryDbTable) hands each row to the payload's own callback, as
        sqlite3_exec does, and the fetch of those rows by id, scoped to music (path_query, on stock's query), stages
        the records it picks."""
        a,b,c,d=[u.reg_read(r) for r in REGS]; sql=self.text(b)
        if sql=='select id,fileurl,time from songtable':
            rows=self.songtable(sql,self.found,values=True)
            for row in rows:
                values=self.alloc(4*len(row)); names=self.alloc(4*len(row))
                for i,v in enumerate(row): self.word(values+4*i,0 if v is None else self.string(str(v)))
                if self.nested(c,d,len(row),values,names): break
        elif sql.startswith('select * from songtable not indexed where id in ('):
            rows=self.songtable(sql,self.found)
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]+=[self.copy('stSongInfo',e) for e in rows]
        else: return False
        self.calls.append(('toolsQueryDbTable' if address==syms['toolsQueryDbTable'] else 'dbquery',a,b,c))
        self.returns(len(rows),clobber=True); return True

demo=(B/'demo').read_bytes()

def shuffle_note(m,row=0):  # row: Shuffle's (Classic's Library has Search first)
    view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
    assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0
    f,ctx=m.handler(m.nodes[m.nodes[view]['children'][row]]['children'][0],O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
    note=m.nodes[m.top]['children'][-1]; assert m.nodes[note]['text']=='Albums and folders take a moment to shuffle' and m.nodes[note]['style:normal:font_size']==16
    return note

from ipod import corner_inset

from fontTools.ttLib import TTFont

def classic_light_home(mode='0'):
    CONFIG['HOME']=mode; m=classic_light_new(ShuffleMachine); del CONFIG['HOME']; classic_light_on(m)
    m.top=asset_tree(m,'../classic-light_'+HOME_PAGE); m.mock('strstr@GLIBC_2.0','sqlite3_open','sqlite3_exec','sqlite3_close','on_home_music_onclick','widget_off_by_ctx','widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start','widget_layout')
    m.handlers[O['LOCALMUSIC_BG']]='local_bg'
    assert m.call(address=home_hook[0],args=(m.top,0,0,0),gap=0)==0
    return m

def pane_shows(m):
    art=named(m,m.top,'img_homeart'); m.letters=[]; m.calls=[]; m.image_draws=[]
    m.call(address=payload_syms['coverflow_home_clip'],args=(art,m.canvas,0,0),gap=0)
    return [m.text(c[2]) for c in m.calls if c[0]=='widget_load_image' and m.text(c[2]).startswith('classic_light_')],[l['text'] for l in m.letters]
def pane_nocover(m):
    m.image_size=None; m.call(address=payload_syms['coverflow_home_art'],args=(m.top,0,0,0),gap=0); m.image_size=(96,96)

def page_bar(pm):  # the bar's title: 94 px of text between hscroll_label's 2 px margins; its paint finds it
    t=pm.node('hscroll_label','label_title'); pm.word(t+O['W_W'],98); b=classic_light_bar(pm,t)
    pm.call(address=HOOKS['widget_on_paint_background'][0],args=(b,pm.canvas,0,0),gap=0); return t
def bar_reads(pm,bar):  # the old caption's destroy, which the mock's unlink doesn't fire, and the bar asked again
    pm.call(address=payload_syms['title_gone'],args=(0,0,0,0),gap=0); pm.call(address=payload_syms['bar_title_sync'],args=(pm.top,0,0,0),gap=0); return pm.nodes[bar]['text']
def plist(pm,caption,n,item_h,keep=False):
    out=pm.alloc(4) if keep else 0
    return pm.call(address=payload_syms['page_list'],args=(pm.top,pm.top,out,pm.string(caption)),stack=(n,item_h),gap=0)&0xffffffff
def prow(pm,view,i,caption,detail=None,into=False):
    f=payload_syms['page_row_detail_into' if into else 'page_row_detail']
    return pm.call(address=f,args=(view,i,pm.string(caption),pm.string(detail) if detail is not None else 0),stack=(0,),gap=0)&0xffffffff

def page_kids(pm,kind): return [c for c in pm.nodes[pm.top]['children'] if pm.nodes[c]['type']==kind]

def long_view(h,name='',top=100):
    lm=classic_light_on(Machine()); lw,les=lm.page_list(500,height=266,extent=500*h); lm.word(lw+O['W_W'],375)
    for i,e in enumerate(les): lm.word(e+O['W_Y'],i*h); lm.word(e+O['W_H'],h); lm.nodes[e]['name']=name
    lm.word(lw+O['SCROLL_Y'],(top*h)&0xffffffff); paint=HOOKS['widget_on_paint_background'][0]
    lm.call(address=paint,args=(lw,lm.canvas,0,0),gap=0)
    count=[0]; hook=lm.u.hook_add(UC_HOOK_CODE,lambda *a: count.__setitem__(0,count[0]+1))
    lm.call(address=paint,args=(lw,lm.canvas,0,0),gap=0); lm.u.hook_del(hook); assert count[0]<100000,count[0]
    rows=set(les); read=[c[1] for c in lm.calls if c[0]=='widget_get_prop_str' and c[1] in rows and lm.text(c[2])=='name']
    cost_line(f'classic scroll view {h}px {name or "unnamed"} top={top}',count[0])
    print(f'Classic scroll view of 500 {h} px rows: {count[0]} instructions, {len(read)} names read'); return lm,read

def swap_values(m,ms,easing):
    f,ctx=m.handler(named(m,m.top,'img_settings'),O['EVT_CLICK']); m.calls=[]; m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    assert [c[2] for c in m.calls if c[0]=='widget_animator_prop_create'][:1]==[ms] and m.nodes[named(m,m.top,'list_view_homeset')]['animated_easing']==easing

HINT_PROPS=('anim_hint','open_anim_hint','close_anim_hint','highlight')

class MotionMachine(UiLoad,CoverflowMachine): pass
def motion_machine(t):
    SETUP[:]=[lambda k:(k.config.update(THEME=t,LIGHT='1'),k)]
    try: m=MotionMachine()
    finally: SETUP.clear()
    m.handlers[syms['widget_set_prop_str']]='widget_set_prop_str'; return m
def hint_writes(m): return sum(c[0]=='widget_set_prop_str' and m.text(c[2]) in HINT_PROPS for c in m.calls)
def motion_load(m,name,kind='window',**props):  # as stock's loader hands it over; ui_loaded's call clears m.calls
    w=m.node(kind,name.rsplit('/')[-1]); m.nodes[w].update(props); ui_loaded(m,name,w); return w
def motion_open(m,name): return m.call(address=payload_syms['page_open'],args=(m.string(name),0,0,0),gap=0)&0xffffffff
def motion_over(m,name): m.nodes[m.wm]={'children':[m.node('window',name),m.node('window','opening')]}  # stock has added what opens by then

def library_page(lra):
    SETUP[:]=[lambda k: classic_light(k).config.update(ROWART=lra)]; lm=ShuffleMachine(); SETUP.clear(); classic_light_on(lm,rowart=lra)
    lstock=[]
    for i in range(11):
        kids=[lm.node('image'),lm.node('hscroll_label',text=f'Row {i}'),lm.node('image')]; h=28
        for c,(x,w) in zip(kids,((8,52),(72,253),(282,50))):
            for k,v in zip(('W_X','W_W','W_H'),(x,w,h)): lm.word(c+O[k],v)
        lb=lm.node('button',children=kids); lm.word(lb+O['W_X'],20); lstock.append(lm.node('list_item',children=[lb]))
    lv=lm.node('scroll_view','scroll_view_localmusic',lstock); lm.top=lm.node('window','localmusic_page',[lv])
    assert lm.call(address=HOOKS['localmusic_page_init'][0],args=(lm.top,5,0,0),gap=0)==0
    return lm,[lm.nodes[r]['children'][0] for r in lm.nodes[lv]['children']]
def library_labels(lm,buttons):
    return [next(c for c in lm.nodes[b]['children'] if lm.nodes[c]['type']=='hscroll_label') for b in buttons]

class FetchRefused(ShuffleMachine):
    fetch=len('select * from songtable not indexed where id in (')+len(f') and {MUSIC_WHERE} order by time_create desc,id')+2+11*125
    refused=0
    def hook(self,u,address,size,unused):
        if address==syms['calloc@GLIBC_2.0'] and (u.reg_read(REGS[0]),u.reg_read(REGS[1]))==(self.fetch,1):
            self.refused+=1; self.returns(0,clobber=True); return
        return super().hook(u,address,size,unused)

FAV_CLICK=0x53c968
class RateMachine(FileMocks,ShuffleMachine):
    def __init__(self,saved=b''):
        super().__init__()
        self.budget=2_000_000  # a RATE_SLOTS table scan or two a call
        self.word(O['MCL_POS'],1)  # B of ABC plays
        self.file_mocks('r:',{'/mnt/data/ringnav-ratings':saved} if saved else {}); self.fav_songs=[]
        for n in ('getAllFav','fseek@GLIBC_2.0','ftell@GLIBC_2.0','taglib_file_new','taglib_file_audioproperties','taglib_file_free'):
            self.handlers[syms[n]]='r:'+n
        self.lengths={}; self.probed=[]; duration_syscalls(self); self.handlers[0x1000030]='r:length'  # AudioProperties::length(), vtable +0xc
        self.handlers[FAV_CLICK]='r:fav_click'
        self.fav=self.node('image','img_fav'); self.word(self.fav+O['W_X'],303); self.word(self.fav+O['W_Y'],176)
        self.word(self.fav+O['W_W'],50); self.word(self.fav+O['W_H'],50); self.nodes[self.fav]['image']='play_unfav'
        self.np=self.node('window','playing_page',[self.node('view','view_album',[self.fav])])
        self.word(self.np+O['W_W'],375); self.word(self.np+O['W_H'],320); self.word(self.np+O['W_PARENT'],self.wm)
        for w in self.nodes[self.np]['children']: self.word(self.fav+O['W_PARENT'],w); self.word(w+O['W_PARENT'],self.np)
        em=self.alloc(4); it=self.alloc(0x28); self.word(self.fav+O['W_EMITTER'],em); self.word(em,it)
        self.word(it+O['EMIT_TYPE'],O['EVT_CLICK']); self.word(it+12,FAV_CLICK); self.word(it,self.np)
        playing=HOOKS['playing_page_init'][0]; self.handlers[playing+12]='stock_playing'
        self.top=self.np; self.stack=[self.np]
        assert self.call(address=playing,args=(self.np,0,0,0),gap=0)==0
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0; self.calls.append((name,a,b,c))
        if name in ('fopen','fread','fwrite','fclose','rename'): ret=self.file_op(u,name,a,b,c,d)
        elif name=='fseek': ret=0
        elif name=='ftell': ret=len(self.files[self.open_files[a][0]])
        elif name=='taglib_file_new':
            assert b==1  # Long Songs requires audio properties.
            path=self.text(a); self.probed.append(path)
            if path in self.lengths: ret=self.alloc(8); props=self.alloc(8); vt=self.alloc(16); self.word(props,vt); self.word(vt+12,0x1000030); self.word(ret,props); self.word(props+4,self.lengths[path])
        elif name=='taglib_file_audioproperties': ret=self.get(a)
        elif name=='length': ret=self.get(a+4)
        elif name=='pick': self.word(O['MCL_POS'],self.seq.pop(0)); ret=1
        elif name=='getAllFav':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.fav_songs]; ret=len(self.fav_songs)
        elif name=='fav_click':  # stock: My Fav toggled for the playing track, and the heart's image
            assert a==self.np; t=self.names()[self.get(O['MCL_POS'])]; self.favs^={t}
            self.nodes[self.fav]['image']='play_fav' if t in self.favs else 'play_unfav'
        self.returns(ret,clobber=True)
    def kept(self):
        data=self.files.get('/mnt/data/ringnav-ratings',b'')
        return sorted(v for v in struct.unpack(f'<{len(data)//4}I',data) if v)
    def badge(self):
        w=self.nodes[self.get(self.fav+O['W_PARENT'])]['children'][-1]; return self.nodes[w].get('text') if self.nodes[w]['visible'] else None
    def panel(self):
        p=[w for w in self.nodes[self.np]['children'] if self.nodes[w]['type']=='hscroll_label']
        return p[0] if p and self.nodes[p[0]]['visible'] else None
    def hearts(self):
        p=self.panel(); return [self.nodes[h]['image'] for h in self.nodes[p]['children'] if self.nodes[h]['type']=='image']
    def caption(self): return self.nodes[self.nodes[self.panel()]['children'][0]]['text']
    def tap(self,press=True):  # a press, then its click
        if press: f,ctx=self.handler(self.fav,O['EVT_POINTER_DOWN']); self.call(address=f,args=(ctx,self.event,0,0),gap=0)
        f,ctx=self.handler(self.fav,O['EVT_CLICK']); return self.call(address=f,args=(ctx,self.event,0,0),gap=0)
    def heart(self,ms=O['RATE_HOLD_MS']):  # a press held ms on the heart
        self.touch()  # the finger's down reaches the window manager first (pointer_down)
        f,ctx=self.handler(self.fav,O['EVT_POINTER_DOWN']); self.call(address=f,args=(ctx,self.event,0,0),gap=0)
        self.pressed=1; self.advance(ms,clear=False); self.pressed=0
def key_of(name): k=fnv('/p/'+name); return k|(k==0)

class RootMachine(ShuffleMachine):
    def __init__(self,root=None,otg=0,card=(('Music',4),('Podcasts',4),('notes.txt',8))):
        super().__init__(); self.card=list(card); self.byte(syms['g_otg_status'],otg)  # access finds /tmp/mmc_add
        if root is not None: self.config['FOLDERROOT']=root
        self.stacks={}
        for g in ('g_yoffset_save','g_index_save'): h=self.alloc(4); self.word(syms[g],h); self.stacks[h]=[]
        for n in ('stack_empty','stack_top','stack_pop','strcpy@GLIBC_2.0'): self.handlers[syms[n]]='r:'+n
        self.handlers.pop(syms['folder_reset_variable']); self.mock('strstr@GLIBC_2.0')
        for n in ('folder','folder_top'): self.handlers[int(manifest['patch_symbols'][f'stock_{n}_trampoline'],16)]='stock_'+n  # Back: stock's own
        self.win=self.node('window','folder_page'); self.byte(syms['g_folder_layer'],1)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b=u.reg_read(REGS[0]),u.reg_read(REGS[1]); ret=0; self.calls.append((name,a,b))
        if name=='stack_empty': ret=int(not self.stacks[a])
        elif name=='stack_top': ret=self.stacks[a][-1]
        elif name=='stack_pop': self.stacks[a].pop()
        elif name=='strcpy': self.u.mem_write(a,self.text(b).encode()+b'\0'); ret=a
        self.returns(ret,clobber=True)
    def push(self,yoffset,index):  # an enter's history entry, as folder_enter's stack_push
        for g,v in (('g_yoffset_save',yoffset),('g_index_save',index)):
            e=self.alloc(4); self.word(e,v); self.stacks[self.get(syms[g])].append(e)
    def open(self):
        self.calls=[]; assert self.call(address=HOOKS['folder_page_init'][0],args=(self.win,0,0,0),gap=0)==0
        return self.text(syms['g_folder_path']),self.u.mem_read(syms['g_folder_layer'],1)[0]
    def back(self):  # the folder page's Back handler zeroes both before stock fills them from the history
        out=self.alloc(8); self.word(out,7); self.word(out+4,9)
        ret=self.call(address=HOOKS['folder_back'][0],args=(out,out+4,0,0),gap=0)
        return ret,self.text(syms['g_folder_path']),self.u.mem_read(syms['g_folder_layer'],1)[0],(self.get(out),self.get(out+4))

class GoMachine(RootMachine):
    def __init__(self,path,dirs=(),otg=0):
        super().__init__(otg=otg); self.handlers[syms['navigator_to']]='q:navigator_to'; self.dirs=set(dirs)
        self.word(self.row(0)+O['REC_PATH'],self.string(path))
    def hook(self,u,address,size,unused):
        folder=address==syms['navigator_to'] and self.text(u.reg_read(REGS[0]))=='folder_page'
        super().hook(u,address,size,unused)
        if folder: self.win=self.top; self.nested(HOOKS['folder_page_init'][0],self.win,0)  # stock's init is synchronous
    def menu(self): self.press(5); assert self.hold()==11; got=self.nodes[self.title]['text'],self.labels(); self.release(); return got
    def go(self): self.calls=[]; self.pick(len(SONG_MENU)); self.advance(0)  # Go to Folder, from the open menu

def exif(o,le=True,pad=b''):
    e='<' if le else '>'
    tiff=(b'II' if le else b'MM')+struct.pack(e+'HI',42,8)+struct.pack(e+'H',2)+struct.pack(e+'HHI',0x10f,2,1)+bytes(4)
    tiff+=struct.pack(e+'HHIH',0x112,3,1,o)+bytes(2)+bytes(4)
    app1=b'Exif\0\0'+tiff
    sof=b'\xff\xc0'+struct.pack('>HBHHB',17,8,480,640,3)+bytes(9)  # baseline, so stock's decoder takes it
    return b'\xff\xd8'+pad+b'\xff\xe1'+struct.pack('>H',len(app1)+2)+app1+sof+b'\xff\xda'
JFIF=b'\xff\xe0'+struct.pack('>H',16)+b'JFIF\0'+bytes(9)

class PhotosMachine(DepthMachine):
    """The card as a tree of {path: [(name, d_type)]}, photo bytes by path, and the cache's files."""
    def __init__(self,tree,data):
        super().__init__()
        self.tree=tree; self.data=data; self.cache=set(); self.thumbs=[]; self.forks=0; self.ff_pid=-1; self.bad=set(); self.open_files={}; self.dirs={}
        for n in ('opendir@GLIBC_2.0','readdir@GLIBC_2.0','closedir@GLIBC_2.0','qsort@GLIBC_2.0','fopen@GLIBC_2.2',
                  'fread@GLIBC_2.0','fseek@GLIBC_2.0','fclose@GLIBC_2.2','access@GLIBC_2.0','rename@GLIBC_2.0','unlink@GLIBC_2.0',
                  'memcmp@GLIBC_2.0','toolsThumbSpecCover','fork@GLIBC_2.0','waitpid@GLIBC_2.0','pthread_mutex_lock@GLIBC_2.0',
                  'pthread_mutex_unlock@GLIBC_2.0'):
            self.handlers[syms[n]]='p:'+n
        tramp=int(manifest['patch_symbols']['stock_localmusic_trampoline'],16)
        self.handlers[tramp]='stock_localmusic'; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=tramp,end=tramp)  # fast() ran
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        if name=='c:calloc@GLIBC_2.0' and a*b<=0x10000:
            ret=0 if getattr(self,'fail_size',0)==a*b else self.alloc((a*b+7)&~3)
        elif name=='widget_load_image' and '/mnt/mmc/.photos/' in self.text(b):
            url=self.text(b); self.loads.append(url)
            if url[7:] not in self.cache: ret=1
            else:  # thumbnails decode 72x54, screen copies 375x281 (a turned photo's, as stored)
                w,h=(72,54) if url.endswith('t.jpg') else (375,281); px=self.big_alloc(w*h*4)
                self.u.mem_write(px,bytes([90,120,150,255])*(w*h))
                self.word(c,w); self.word(c+4,h); self.word(c+8,w*4); self.u.mem_write(c+0xc,struct.pack('<HH',0,1)); self.word(c+0x14,px); ret=0
        elif name=='canvas_draw_image':
            self.draws.append(tuple(tuple(signed(self.get(r+4*i)) for i in range(4)) for r in (c,d))); ret=0
        elif name.startswith('p:'):
            name=name[2:].split('@')[0]; ret=0; text=self.text(a) if name not in ('readdir','closedir','qsort','fread','fseek','fclose','memcmp','fork','waitpid') else ''
            if name=='opendir':
                ret=0 if text not in self.tree else self.alloc(4); self.dirs[ret]=list(self.tree.get(text,[]))
            elif name=='readdir' and self.dirs.get(a):
                n,t=self.dirs[a].pop(0); ret=self.alloc(268); self.byte(ret+10,t); self.u.mem_write(ret+11,n.encode()+b'\0')
            elif name=='qsort':
                ptrs=[self.get(a+4*i) for i in range(b)]
                for i,p in enumerate(sorted(ptrs,key=self.text)): self.word(a+4*i,p)
            elif name=='fopen':
                if text in self.data: ret=self.alloc(4); self.open_files[ret]=[self.data[text],0]  # bytes, position
                elif text.endswith('.bad'): self.bad.add(text); ret=self.alloc(4)
            elif name=='fread':
                f=self.open_files.get(d,[b'',0]); chunk=f[0][f[1]:f[1]+b*c]; f[1]+=len(chunk); self.u.mem_write(a,chunk); ret=len(chunk)//b
            elif name=='fseek': assert c==0; self.open_files[a][1]=b  # thumb's header read: SEEK_SET only
            elif name=='access': ret=0 if text in self.cache or text in self.bad or text in self.data else -1
            elif name=='toolsThumbSpecCover':
                self.thumbs.append((self.text(a),self.text(b),c,d))
                ret=0 if self.data.get(self.text(a))==b'corrupt' else 1
                if ret: self.cache.add(self.text(b))
            elif name=='rename': self.cache.discard(text); self.cache.add(self.text(b))
            elif name=='unlink': self.cache.discard(text)
            elif name=='fork': self.forks+=1; ret=self.ff_pid  # thumb()'s ffmpeg: refused (-1), or 99 to fail by itself
            elif name=='waitpid': self.u.mem_write(b,struct.pack('<i',1<<8)); ret=a  # exit 1: the picture's fault
            elif name=='memcmp': ret=0 if bytes(self.u.mem_read(a,c))==bytes(self.u.mem_read(b,c)) else 1
        else: return super().hook(u,address,size,unused)
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)

def png_head(w,h): return b'\x89PNG\r\n\x1a\n'+struct.pack('>I4sII',13,b'IHDR',w,h)+bytes(9)
def frame_jpeg(sof,w,h): return b'\xff\xd8'+JFIF+b'\xff'+bytes([sof])+struct.pack('>HBHHB',17,8,h,w,3)+bytes(9)+b'\xff\xda'+bytes(12)

def photo_grid(m):
    v=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[v])
    assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
    f,ctx=m.handler(m.nodes[m.nodes[v]['children'][-4]]['children'][0],O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
    page=m.top; _,grid,_=m.nodes[page]['children']; tv=m.find('scroll_view',grid); assert m.nodes[grid]['visible'] and len(m.nodes[tv]['children'])==20
    return page,tv,next(c[3] for c in reversed(m.calls) if c[0]=='scroll_view_create'),signed(m.get(tv+O['W_H']))
def photo_back(m,page,tv):
    f,ctx=m.handler(m.nodes[tv]['children'][19],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
    m.page=page; assert m.key()==11; return signed(m.get(tv+O['SCROLL_Y']))

def photos_at(tree,data):
    m=classic_light_new(PhotosMachine,tree=tree,data=data); classic_light_on(m); m.mock('widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start')
    v=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[v])
    assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
    f,ctx=m.handler(m.nodes[m.nodes[v]['children'][-4]]['children'][0],O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
    m.page=m.top; return m

class CardMachine(DepthMachine):
    """The card as a tree of {path: [(name, d_type)]} and files as {path: bytearray}, with stdio
    handles over them, and a fixed-pitch font: every character CHAR_PITCH wide."""
    def __init__(self,tree,files):
        super().__init__()
        self.tree=tree; self.files=files; self.handles={}; self.dirs={}; self.lines=[]
        for n in ('opendir@GLIBC_2.0','readdir@GLIBC_2.0','closedir@GLIBC_2.0','qsort@GLIBC_2.0','fopen@GLIBC_2.2',
                  'fread@GLIBC_2.0','fwrite@GLIBC_2.0','fseek@GLIBC_2.0','ftell@GLIBC_2.0','fclose@GLIBC_2.2',
                  'access@GLIBC_2.0','rename@GLIBC_2.0','unlink@GLIBC_2.0','mkdir@GLIBC_2.0','memcmp@GLIBC_2.0',
                  'strlen@GLIBC_2.0','strstr@GLIBC_2.0','canvas_measure_text','canvas_draw_text',
                  'fork@GLIBC_2.0','execl@GLIBC_2.0','exit@GLIBC_2.0','waitpid@GLIBC_2.0','socket@GLIBC_2.0',
                  'sendto@GLIBC_2.0','close@GLIBC_2.0','open@GLIBC_2.0','setenv@GLIBC_2.0'):
            self.handlers[syms[n]]='b:'+n
        self.handlers[syms['mclGetOutputWay']]='b:mclGetOutputWay'
        self.handlers[payload_syms['child_exit']]='b:exit'
        self.u.hook_add(UC_HOOK_CODE,self.hook,begin=payload_syms['child_exit'],end=payload_syms['child_exit'])
        self.forked=4242; self.waited=0; self.way=0; self.execs=[]; self.sent=[]
        self.write_cap=None; self.close_fails=0  # a full disk: a file holds write_cap bytes at most; the next close_fails closes of a .tmp fail
        self.on_fopen=None  # called with each path fopen opens, before it does
        tramp=int(manifest['patch_symbols']['stock_localmusic_trampoline'],16)
        self.handlers[tramp]='stock_localmusic'; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=tramp,end=tramp)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='c:calloc@GLIBC_2.0':
            ret=self.alloc((a*b+7)&~3) if a*b<=0x10000 else self.big_alloc(a*b)  # word-aligned
            if hasattr(self,'poison'): self.poison[ret]=a*b  # zeroed again when freed: a use after free reads ""
        elif name=='b:qsort@GLIBC_2.0' and c!=4:  # not names (Tidal's and a card app's songs): the payload's comparator
            self.handlers[address]='c:qsort@GLIBC_2.0'
            try: return super().hook(u,address,size,unused)
            finally: self.handlers[address]=name
        elif not name.startswith('b:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]
        if name=='opendir':
            path=self.text(a); ret=self.alloc(4) if path in self.tree else 0; self.dirs[ret]=list(self.tree.get(path,[]))
        elif name=='readdir' and self.dirs.get(a):
            n,t=self.dirs[a].pop(0); ret=self.alloc(268); self.byte(ret+10,t); self.u.mem_write(ret+11,n.encode()+b'\0')
        elif name=='qsort':
            ptrs=[self.get(a+4*i) for i in range(b)]
            for i,p in enumerate(sorted(ptrs,key=self.text)): self.word(a+4*i,p)
        elif name=='fopen':
            path,mode=self.text(a),self.text(b)
            if self.on_fopen: self.on_fopen(path)
            if 'w' in mode: self.files[path]=bytearray()
            if path in self.files: ret=self.alloc(4); self.handles[ret]=[path,0]
        elif name in ('fread','fwrite'):
            path,pos=self.handles[d]; f=self.files[path]
            if name=='fread': chunk=bytes(f[pos:pos+b*c]); self.u.mem_write(a,chunk)
            else:
                chunk=bytes(u.mem_read(a,b*c))
                if self.write_cap is not None: chunk=chunk[:max(0,self.write_cap-pos)]
                f[pos:pos+len(chunk)]=chunk
            self.handles[d][1]+=len(chunk); ret=len(chunk)//b
        elif name=='fseek':
            h=self.handles[a]; h[1]=signed(b)+(0,h[1],len(self.files[h[0]]))[c]; ret=0
        elif name=='ftell': ret=self.handles[a][1]
        elif name=='fclose':
            path=self.handles.pop(a)[0]
            if self.close_fails and path.endswith('.tmp'): self.close_fails-=1; ret=-1
        elif name=='access': ret=0 if self.text(a) in self.files or self.text(a) in self.tree else -1
        elif name=='rename': self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='unlink': self.files.pop(self.text(a),None)
        elif name=='memcmp': ret=0 if bytes(u.mem_read(a,c))==bytes(u.mem_read(b,c)) else 1
        elif name=='strlen': ret=len(self.raw(a))  # bytes: a buffer may end inside a UTF-8 character
        elif name=='strstr':
            i=self.raw(a).find(self.raw(b)); ret=a+i if i>=0 else 0
        elif name=='canvas_measure_text': u.reg_write(UC_MIPS_REG_F0,struct.unpack('<I',struct.pack('<f',(getattr(self,'char_em',0)*self.font[1] or CHAR_PITCH)*c))[0])
        elif name=='mclGetOutputWay': ret=self.way
        elif name=='open': ret=0xffffffff  # the child's oom_score_adj write: refused here, so nothing is written
        elif name=='fork': ret=self.forked
        elif name=='setenv': self.env=(self.text(a),self.text(b),c); ret=0  # a card game's Q2_POPUP, in the child
        elif name=='execl':
            sp=u.reg_read(UC_MIPS_REG_SP); e=self.get(sp+16)  # a volume argv, then its terminator
            if not c: self.execs.append((self.text(a),self.text(b))); ret=-1  # no arguments: the terminator third
            else: self.execs.append((self.text(a),self.text(b),self.text(c),self.text(d),e and (self.text(e),self.get(sp+20)))); ret=-1
        elif name=='waitpid': assert a==self.forked and c==1; ret=self.waited
        elif name=='socket': assert (a,b,c)==(1,1,0); ret=7
        elif name=='sendto':
            sp=u.reg_read(UC_MIPS_REG_SP); to=self.get(sp+16)
            self.sent.append((a,bytes(self.u.mem_read(b,c)).decode('latin1'),c,d,self.get(to)&0xffff,self.text(to+2),self.get(sp+20)))
        elif name=='canvas_draw_text':
            sp=u.reg_read(UC_MIPS_REG_SP)
            self.lines.append((''.join(chr(self.get(b+4*j)) for j in range(c)),signed(d),signed(self.get(sp+16)),
                               self.get(self.lcd+O['LCD_TEXT_COLOR']),self.font))
        self.calls.append((name,a,b,c))
        if name=='exit':
            for i,r in enumerate(SAVED): u.reg_write(r,0x12340000+i)
            u.reg_write(UC_MIPS_REG_V0,0);u.reg_write(UC_MIPS_REG_SP,0x7000f000);u.reg_write(UC_MIPS_REG_PC,0x1000000);return
        self.returns(ret,clobber=True)

CHAR_PITCH=9

# Spotify (patch/spotify.c, docs/internals.md#spotify): librespot from the card's .spotify folder, its
# state from the q2-librespot fork's --status-file, its commands to --control-socket.
STATE,SOCK='/tmp/q2-librespot.state','/tmp/q2-librespot.sock'
class SpotMachine(CardMachine):
    """CardMachine's card and files, plus librespot's side: system() starts, the monotonic clock is
    self.now, the player's status, and the thumbnailer (a .jpg it makes is a file)."""
    def __init__(self,files):
        super().__init__({},files)
        for n in ('system@GLIBC_2.0','clock_gettime@GLIBC_2.2','toolsThumbSpecCover','pthread_mutex_lock@GLIBC_2.0',
                  'pthread_mutex_unlock@GLIBC_2.0','sleep_ms','mclGetPlayStatus','connect@GLIBC_2.0'):
            self.handlers[syms[n]]='s:'+n
        self.alive=True  # librespot's control socket takes a connection (spot_alive)
        for x in ('stream','start_player'):
            tramp=int(manifest['patch_symbols'][f'stock_{x}_trampoline'],16)
            self.handlers[tramp]='stock_'+x; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=tramp,end=tramp)
        self.play=1; self.systems=[]; self.thumbs=[]; self.on_sleep=None
        self.handlers.pop(syms['mclStartPlayer'])  # its hook runs, down to the trampoline
        self.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
        self.handlers[HOOKS['window_manager_dispatch_input_event'][0]+12]='stock_input'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('s:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='system': self.systems.append(self.text(a))
        elif name=='clock_gettime': assert a in (1,7); self.word(b,self.now//1000); self.word(b+4,self.now%1000*1000000)
        elif name=='toolsThumbSpecCover':
            self.thumbs.append((self.text(a),self.text(b),c,d)); ret=1 if self.text(a) in self.files else 0
            if ret: self.files[self.text(b)]=bytearray(b'jpeg')
        elif name=='sleep_ms':
            self.now+=a
            if self.on_sleep: self.on_sleep()
        elif name=='mclGetPlayStatus': ret=self.play
        elif name=='connect': assert self.text(b+2)==SOCK; ret=0 if self.alive else -1
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)
    def state(self,state,position=0,at=None,cover=0,track='t1',title='Song',artist='A, B',album='LP',duration=200000):
        self.files[STATE]=bytearray((f'state={state}\ntrack={track}\ntitle={title}\nartist={artist}\nalbum={album}\n'
            f'duration={duration}\nposition={position}\nat={self.now if at is None else at}\ncover={cover}\n').encode())
    def poll(self,ms=500):  # the page in use: input each pass, so no screen-on idle pass moves the clock
        self.now+=ms; self.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(self.wm,self.event,0,0),gap=0)
        self.calls=[]; self.sent=[]
        self.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=0,clear=False)
        return [c[0] for c in self.calls]
    def key(self,k):
        self.sent=[]; self.calls=[]; return self.call(k,gap=0,clear=False)

from build import TIMEZONES, TIMEZONE_ORDER, timezone_offset
class TimezoneMachine(Machine):
    def __init__(self):
        super().__init__()
        self.mock('strcpy@GLIBC_2.0')
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='strcpy@GLIBC_2.0': return super().hook(u,address,size,unused)
        a,b=[u.reg_read(r) for r in REGS[:2]]
        self.u.mem_write(a,self.text(b).encode()+b'\0'); self.returns(a,clobber=True)

SUMMER={'America/New_York','America/Halifax'}
class ZoneRowMachine(TimezoneMachine):
    tz=TIMEZONES[0][1]
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address)
        if name=='toolsSetTimeZone': self.tz=self.text(u.reg_read(REGS[0]))
        if name!='localtime_r': return super().hook(u,address,size,unused)
        self.calls.append((name,)); label=next(l for l,z in TIMEZONES if z==self.tz); tm=u.reg_read(REGS[1])
        self.word(tm+36,(timezone_offset(label)*60+3600*(self.tz in SUMMER))&0xffffffff)
        self.returns(tm,clobber=True)

def stream_rows(m):
    """Streaming's page over stock's one Tidal row: the hook's rows' buttons by their labels, in order."""
    tidal=m.node('list_item','',[m.node('image',image='list_tidal')])
    view=m.node('scroll_view','scroll_view_streamsset',[tidal]); m.top=m.node('window','stream_page',[view])
    assert m.call(address=HOOKS['stream_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
    rows={}
    for r in m.nodes[view]['children'][1:]:
        b=m.nodes[r]['children'][0]; rows[m.nodes[m.nodes[b]['children'][1]]['text']]=b
    return rows
def stream_page(m):
    """Stock Streaming's one Tidal row, then the hook's Spotify, Tidal Downloads, Internet Radio and AirPlay
    rows; returns Spotify's button."""
    view=m.node('scroll_view','scroll_view_streamsset',[m.node('list_item')]); m.top=m.node('window','stream_page',[view])
    assert m.call(address=HOOKS['stream_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0 and m.calls[0][0]=='stock_stream'
    assert len(m.nodes[view]['children'])==6
    # Tidal Downloads, after Spotify: the card's cached Tidal tracks, here none.
    tidal=m.nodes[m.nodes[view]['children'][2]]['children'][0]; icon,label=m.nodes[tidal]['children'][:2]
    assert m.nodes[icon]['image']=='list_tidal' and m.nodes[label]['text']=='Tidal Downloads'
    top=m.top; f,ctx=m.handler(tidal,O['EVT_CLICK']); m.calls=[]; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
    assert [m.text(c[1]) for c in m.calls if c[0]=='toolsLoadDirectory']==['/mnt/mmc/.tidal']
    assert m.nodes[m.top]['name']=='tidal_downloads' and 'Download Playing Songs' in [n.get('text') for n in m.nodes.values()]  # its first row, Download All's
    f,ctx=m.handler(m.top,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.top=top
    # AirPlay, last: stock's own receiver, its switch on System settings' Network service page, one press away.
    airplay=m.nodes[m.nodes[view]['children'][4]]['children'][0]; icon,label=m.nodes[airplay]['children'][:2]
    assert m.nodes[icon]['image']=='stream_airplay' and m.nodes[label]['text']=='AirPlay'
    top=m.top; f,ctx=m.handler(airplay,O['EVT_CLICK']); m.calls=[]; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
    assert [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']==['systemset/netservice_page']; m.top=top
    return m.nodes[m.nodes[view]['children'][1]]['children'][0]
def spot_open(m,button):
    """The row, then (librespot on the card: the browser) its Now Playing page."""
    f,ctx=m.handler(button,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
    if BIN in m.files:
        if m.nodes[m.top]['name']=='spotbrowse_page': m.browser=m.top  # once: the row brings it back
        assert m.call(address=payload_syms['spot_np'],args=(0,0,0,0),gap=0,count=5_000_000)==0
    m.page=m.top; assert m.nodes[m.page]['name']=='spotify_page'; return m.page

BIN='/mnt/mmc/.spotify/librespot'

class RadioMachine(SpotMachine):
    """SpotMachine's side, plus Wi-Fi and demo's libcurl: perform hands reply to the write callback."""
    def __init__(self,files=None):
        super().__init__(files or {})
        for n in ('curl_easy_init','curl_easy_setopt','curl_easy_perform','curl_easy_getinfo','curl_easy_cleanup','get_wifisignal',
                  'mkdir@GLIBC_2.0'):
            self.handlers[syms[n]]='r:'+n
        self.mock('fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0','ferror@GLIBC_2.0')
        self.wifi=3; self.reply=b''; self.code=200; self.codes=[]; self.location=''; self.opts={}
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='get_wifisignal': ret=self.wifi
        elif name=='mkdir': self.tree[self.text(a)]=[]
        elif name=='curl_easy_init': ret=0x5150; self.opts={}  # each handle starts with curl's defaults
        elif name=='curl_easy_setopt': self.opts[b]=self.text(c) if b in (10002,10018,10065) else c
        elif name=='curl_easy_perform' and 20011 not in self.opts: ret=0  # NOBODY: headers only
        elif name=='curl_easy_perform':  # the write callback, run nested as qsort's comparator is
            data=self.alloc((len(self.reply)+7)&~3); u.mem_write(data,self.reply)
            took=self.nested(self.opts[20011],data,1,len(self.reply),self.opts.get(10001,0))
            ret=0 if took==len(self.reply) and self.code else 23  # CURLE_WRITE_ERROR
        elif name=='curl_easy_getinfo' and b==0x10001f:  # REDIRECT_URL
            text=self.alloc(len(self.location)+4); u.mem_write(text,self.location.encode()+b'\0'); self.word(c,text)
        elif name=='curl_easy_getinfo': self.word(c,self.codes.pop(0) if self.codes else self.code)
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)
    def stream(self):
        view=self.node('scroll_view','scroll_view_streamsset',[self.node('list_item')]); self.top=self.node('window','stream_page',[view])
        assert self.call(address=HOOKS['stream_page_init'][0],args=(self.top,5,0,0),gap=0,count=5_000_000)==0
        return self.nodes[self.nodes[view]['children'][3]]['children'][0]
    def click(self,w):
        f,ctx=self.handler(w,O['EVT_CLICK']); assert self.call(address=f,args=(ctx,self.event,0,0),gap=0,count=5_000_000)==0
        self.advance(0,clear=False)
    def rows(self): return self.nodes[self.find('scroll_view',self.page)]['children']
    def row(self,i): self.click(self.rows()[i])
    def labels(self,w=None):
        """Texts still linked below w (page_list's destroy_children mock only unlinks)."""
        def linked(x,w):
            while x!=w:
                parent=self.get(x+O['W_PARENT'])
                if parent not in self.nodes or x not in self.nodes[parent]['children']: return False
                x=parent
            return True
        def shown(x,n):  # an App Store row's state, app_store.c draws it at the row's end, over its detail
            row=self.nodes.get(self.get(x+O['W_PARENT']),{}); state=row.get('_state',-1)
            return ('Install','Update','Installed')[state] if state>=0 and row['children'][1:2]==[x] else n.get('text','')
        w=w or self.page
        return [shown(x,n) for x,n in self.nodes.items() if n['type']=='hscroll_label' and linked(x,w)]  # a new label's text is ''
    def back(self,w=None):
        """Return on page w (the radio list by default), and its timers."""
        w=w or self.page; f,ctx=self.handler(w,O['EVT_KEY_UP']); ev=self.alloc(0x40)
        self.word(ev,O['EVT_KEY_UP']); self.word(ev+O['EVENT_KEY'],O['KEY_RETURN'])
        assert self.call(address=f,args=(ctx,ev,0,0),gap=0,count=5_000_000)==11; self.advance(0,clear=False)
    def destroy(self,w):
        f,ctx=self.handler(w,O['EVT_DESTROY']); self.call(address=f,args=(ctx,self.event,0,0),gap=0)
    def worker(self):
        worker,arg=self.threads[-1]; assert self.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0; self.advance(250)
def radio_open(m):
    button=m.stream(); icon,label=m.nodes[button]['children'][:2]
    assert m.nodes[icon]['image']=='stream_radio' and m.nodes[label]['text']=='Internet Radio' and not m.nodes[button].get('name')
    m.click(button); m.page=m.top; assert m.nodes[m.page]['name']=='radio_page'; return m.page

class LyricSearchMachine(RadioMachine):
    def __init__(self):
        super().__init__(); self.written={}; self.json={}
        for n in ('fopen@GLIBC_2.2','fputs@GLIBC_2.0','fclose@GLIBC_2.2','cJSON_Parse','cJSON_GetArraySize',
                  'cJSON_GetArrayItem','cJSON_GetObjectItem','cJSON_Delete'):
            self.handlers[syms[n]]='ls:'+n
        self.handlers[HOOKS['kugou_obtainlyric'][0]+12]='ls:kugou'  # past the hook's jump: Kugou's search itself
        self.handlers.pop(syms['online_getkeyword_info'],None)  # stock's copy of keyinfo runs, under its (mocked) lock
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
    def item(self,v):
        a=self.alloc(0x30); self.json[a]=v; self.word(a+16,self.string(v) if isinstance(v,str) else 0); return a  # +16: valuestring
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='r:curl_easy_perform': self.reply=self.replies.pop(0) if self.replies else b'[]'; self.urls.append(self.opts[10002])
        if not name.startswith('ls:'): return super().hook(u,address,size,unused)
        name=name[3:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='fopen': self.fpath=self.text(a); self.written[self.fpath]=b''; ret=0x2000100
        elif name=='fputs': self.written[self.fpath]+=self.raw(a)
        elif name=='cJSON_Parse': ret=self.item(json.loads(self.text(a)))
        elif name=='cJSON_GetArraySize': ret=len(self.json[a])
        elif name=='cJSON_GetArrayItem': ret=self.item(self.json[a][b])
        elif name=='cJSON_GetObjectItem': v=self.json[a]; key=self.text(b); ret=self.item(v[key]) if isinstance(v,dict) and key in v else 0
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)
    def search(self,title,artist,*replies,code=200):
        """replies: one a request, in turn (then [])."""
        key=0xa545f4  # stock's keyinfo: file name, title (+0x200), album (+0x400), artist (+0x500)
        self.u.mem_write(key,bytes(0x600)); self.u.mem_write(key+0x200,title.encode()); self.u.mem_write(key+0x500,artist.encode())
        self.replies,self.code,self.written,self.calls,self.urls=list(replies),code,{},[],[]
        # Each request's 512 KB reply buffer is freed, but the mock's free keeps it: start over, zeroed as calloc's.
        self.u.mem_write(BIG,bytes(self.big-BIG)); self.big=BIG
        return self.call(address=HOOKS['kugou_obtainlyric'][0],args=(self.string(f'{title}/{artist}'),self.string('/tmp/oslyric_7'),0,0),gap=0,count=5_000_000)

class LyricSaveMachine(AlbumLyricsMachine):
    def __init__(self,files):
        super().__init__(); self.files=dict(files); self.open_files={}; self.unlinked=[]; self.fail_write=False
        for n in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0','unlink@GLIBC_2.0'):
            self.handlers[syms[n]]='lv:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('lv:'): return super().hook(u,address,size,unused)
        name=name[3:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='fopen':
            path,mode=self.text(a),self.text(b)
            if mode=='rb' and path in self.files or mode=='wb' and not self.fail_write:
                ret=0x2000000+16*len(self.open_files); self.open_files[ret]=[path,mode,0,b'']
        elif name=='fread':
            f=self.open_files[d]; data=self.files[f[0]][f[2]:f[2]+b*c]; f[2]+=len(data); u.mem_write(a,data); ret=len(data)//b
        elif name=='fwrite': self.open_files[d][3]+=bytes(u.mem_read(a,b*c)); ret=c
        elif name=='fclose':
            path,mode,_,data=self.open_files.pop(a)
            if mode=='wb': self.files[path]=data
        elif name=='rename': self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='unlink': self.unlinked.append(self.text(a)); self.files.pop(self.text(a),None)
        self.returns(ret,clobber=True)
    def save(self,name):
        cmd=self.alloc(0x400); fmt=0x7867f0  # stock's cp -rf %s "/mnt/mmc/.downpic/%s"
        self.call(address=payload_syms['ringnav_lyric_save'],args=(cmd,0x400,fmt,self.string('/tmp/oslyric_7')),stack=(self.string(name),),gap=0)
        return self.text(cmd)

LRC=b'\xef\xbb\xbf[00:12.00]Look at the stars\n'*700  # over one 4 KB read
def lyric_save(**rec):
    m=LyricSaveMachine({'/tmp/oslyric_7':LRC,'/mnt/mmc/.downpic/01 Yellow.lrc':b'[00:00.00]Kugou'}); r=m.playing()
    for off,v in (('REC_NAME','01 Yellow.flac'),('REC_PATH','/mnt/mmc/Coldplay/01 Yellow.flac'))+tuple(rec.items()):
        m.word(r+O[off],m.string(v) if isinstance(v,str) else v)
    return m

def lyric_drop(m,name):
    cmd=m.alloc(0x400); m.call(address=payload_syms['ringnav_lyric_drop'],args=(cmd,0x3ff,0x786814,m.string(name)),gap=0)
    return m.text(cmd)

def cover_copy(m,src):
    cmd=m.alloc(0x600); m.call(address=payload_syms['ringnav_cover_copy'],args=(cmd,0x600,0x782974,m.string(src)),stack=(m.string('/tmp/externpic.jpg'),),gap=0)
    return m.text(cmd)

BROWSE='/tmp/q2-librespot.browse.sock'
class BrowseMachine(RadioMachine):
    """RadioMachine's helpers, librespot on the card and a fake backend on its browse socket: serve(op,
    args) answers (status, message, cursor, rows), or raw bytes; no serve, no socket. A subclass names
    another app: its socket, its Streaming row and the page it opens."""
    browse,bin,page_name=BROWSE,BIN,'spotbrowse_page'
    def __init__(self,files=None):
        super().__init__({self.bin:bytearray(),**(files or {})})
        for n in ('socket@GLIBC_2.0','connect@GLIBC_2.0','send@GLIBC_2.0','select@GLIBC_2.0','recv@GLIBC_2.0','fcntl@GLIBC_2.28'):
            self.handlers[syms[n]]='x:'+n
        shut=payload_syms['spot_shut']; self.handlers[shut]='x:spot_shut'; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=shut,end=shut)  # fast(): payload hooks are per address
        self.serve=None; self.requests=[]; self.wire=[]; self.out=b''; self.pending=b''; self.stalled=False
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='widget_set_prop_int' and self.text(u.reg_read(REGS[1]))=='virtual_h':
            self.word(u.reg_read(REGS[0])+O['VIEW_CONTENT_H'],u.reg_read(REGS[2]))
        if not name.startswith('x:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='socket':
            assert a==1 and b in (1,2); ret=9 if b==2 else 7
            if b==2: self.out=b''; self.pending=b''
        elif name=='connect' and a!=9: assert self.text(b+2)==SOCK; ret=0 if self.alive else -1  # spot_alive's
        elif name=='connect': assert self.text(b+2)==self.browse; ret=0 if self.serve else -1
        elif name=='fcntl': assert (a,b,c)==(9,4,0x80)  # nonblocking before connect
        elif name=='send':
            if a==9: self.out+=bytes(u.mem_read(b,c))
            else: self.sent.append((a,bytes(u.mem_read(b,c)),c,d))  # QueueMachine's
            ret=c
        elif name=='spot_shut':  # the request is whole: answered
            self.wire.append(self.out); head,line,end=self.out.decode().split('\n'); assert head=='Q2SPOT/1' and end==''
            rid,op,*args=line.split('\t'); self.requests.append((op,*args))
            got=self.serve(op,args)
            if isinstance(got,bytes): self.pending=got.replace(b'{id}',rid.encode())
            else:
                status,msg,cursor,rows=got
                self.pending=f'Q2SPOT/1\n{rid}\t{status}\t{msg}\t{cursor}\n'.encode()+b''.join(('\t'.join(r)+'\n').encode() for r in rows)
        elif name=='select':
            assert a==10 and bool(b)!=bool(c)
            if self.stalled: self.now+=100; ret=0
            else: ret=1
        elif name=='recv': chunk=self.pending[:min(c,1000)]; self.pending=self.pending[len(chunk):]; u.mem_write(b,chunk); ret=len(chunk)
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)
    def add_on_row(self):
        """Its Streaming row, as RadioMachine.stream: Spotify's."""
        return stream_rows(self)['Spotify']
    def open(self):
        if not getattr(self,'button',0): self.button=self.add_on_row()
        self.click(self.button); self.page=self.top; assert self.nodes[self.page]['name']==self.page_name
    def asked(self):
        """The request the worker is about to send (spotify.c's wk: buf at 32, len at 28), split."""
        wk=payload_syms['wk']; head,line,_=bytes(self.u.mem_read(self.get(wk+32),self.get(wk+28))).decode().split('\n')
        assert head=='Q2SPOT/1'; return tuple(line.split('\t')[1:])
    def answer(self,*reply):
        """The request in flight answered with reply, and the page's tick."""
        self.serve=(lambda op,args: reply[0]) if len(reply)==1 else (lambda op,args: reply); self.worker()
        if self.page_name == 'spotbrowse_page' and self.get(payload_syms['wk']+32) and self.asked() == ('caps',):
            self.serve=lambda op,args: ('ok','','',[('op',name,'','','','') for name in getattr(self,'caps',('download','downloadstatus','downloaded','playdownload'))])
            self.worker()
    def pick(self,i):
        self.nodes[self.find('scroll_view')]['_ringnav_index']=i; self.row(i)
OK=('ok','','')
def chevron(m,i):
    """Row i ends in stock's list_into (page_row_detail_into), its labels ending before the glyph (x 322)."""
    *labels,last=m.nodes[m.rows()[i]]['children']
    return m.nodes[last].get('image')=='list_into' and all(m.get(l+O['W_X'])+m.get(l+O['W_W'])<=316 for l in labels)
def switch(m,i): return m.nodes[m.nodes[m.rows()[i]]['children'][-1]].get('image')
ME=OK+([('account','spotify:user:alice','Alice','Premium','','')],)

MENU=['Spotify','Liked Songs','Playlists','Albums','Artists','Search','Account','Downloaded Tracks','Downloads']

# Card apps (patch/addon.c): programs on the card Q2 Pod knows only by their folder, .<id> holding its
# manifest q2app, its launcher run and its binary q2-<id>; Streaming lists them after Spotify, by name,
# and a row's page is the app's list over Q2SPOT/1 on its own socket, each row's kind saying what a
# press does.
def card_app(m,app,manifest):
    """App `app` on the card: its folder in the card's listing, its manifest and its binary."""
    m.tree.setdefault('/mnt/mmc',[]).append(('.'+app,4))
    m.files[f'/mnt/mmc/.{app}/q2app']=bytearray(manifest.encode()); m.files[f'/mnt/mmc/.{app}/q2-{app}']=bytearray()
class AppMachine(BrowseMachine):
    """BrowseMachine with a card app in Spotify's place: on the card, its socket, its row, its page."""
    app,manifest,page_name='soulseek','name=Soulseek\nsearch=Search Soulseek\nqr=0\n','addon_page'
    def __init__(self,files=None):
        self.bin,self.browse=f'/mnt/mmc/.{self.app}/q2-{self.app}',f'/tmp/q2-{self.app}.browse.sock'
        super().__init__(files); card_app(self,self.app,self.manifest)
        self.row_text=re.search(r'name=(.*)',self.manifest)[1]
    def add_on_row(self): return stream_rows(self)[self.row_text]
    def gone(self):
        """Its row, then its binary gone from the card before the press."""
        self.button=self.add_on_row(); self.files.pop(self.bin)

def split_stream(mode='0',rowart='1',light=None):
    CONFIG['HOME']=mode; CONFIG['ROWART']=rowart
    if light: CONFIG['LIGHT']=light
    m=classic_light_new(AppMachine); CONFIG.pop('HOME'); CONFIG.pop('ROWART'); CONFIG.pop('LIGHT',None); classic_light_on(m,rowart)
    m.top=asset_tree(m,'../classic-light_'+HOME_PAGE); m.rockbox_mode=0o100755
    m.mock('on_home_stream_onclick','widget_off_by_ctx','widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start','widget_layout')
    m.handlers[O['STREAM_BG']]='stream_bg'
    for name in ('img_playing','img_library','img_folder','img_rockbox','img_services','img_settings','img_cover_flow'): click_target(m,named(m,m.top,name))
    assert m.call(address=HOOKS['home_page_init'][0],args=(m.top,0,0,0),gap=0)==0
    return m
def stock_tidal(m,view):  # stock's constructor destroys the view's rows and makes this one (0x540f7c's: button 335 x 70 at x 20)
    img,label,into=m.node('image',image='list_tidal'),m.node('hscroll_label',text='TIDAL'),m.node('image',image='list_into')
    for w,box in ((img,(10,0,52,70)),(label,(72,0,253,70)),(into,(282,0,50,70))):
        for o,v in zip(('W_X','W_Y','W_W','W_H'),box): m.word(w+O[o],v)
    b=m.node('button','0',[img,label,into]); m.word(b+O['W_X'],20); m.word(b+O['W_W'],335); m.word(b+O['W_H'],70); click_target(m,b)
    m.nodes[view]['children']=[m.node('list_item','list_item',[b])]
def stream_open(m):
    f,ctx=m.handler(named(m,m.top,'img_services'),O['EVT_CLICK']); assert f==payload_syms['home_streaming']
    return m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)
def stream_shows(m):  # what the pane shows: the bitmap's name, its source and screen boxes, the caption's text and px
    art=named(m,m.top,'img_homeart'); m.letters=[]; m.calls=[]; m.draws=[]; m.image_loads=[]
    m.call(address=payload_syms['coverflow_home_clip'],args=(art,m.canvas,0,0),gap=0)
    return m.image_loads,[d[2:4] for d in m.draws],[l['text'] for l in m.letters]

SBIN,SBROWSE,SSTATE,SPID='/mnt/mmc/.soulseek/q2-soulseek','/tmp/q2-soulseek.browse.sock','/tmp/q2-soulseek.state','/tmp/q2-soulseek'
class SlskMachine(AppMachine): pass
SMENU=[('remote','','Phone Remote','192.168.1.23:8080','',''),('search','','Search','Online as alice','',''),
       ('list','downloads','Downloads','None yet','',''),('action','set','Sharing: Off','Nothing is shared','sharing',''),
       ('action','stop','Stop Soulseek','','','')]

def app_levels(t):
    m=at_theme(t,SlskMachine); m.mock('widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start')
    m.open(); m.answer('ok','refresh','',SMENU); assert m.x_pushes()==[]
    return m

def sec_touch(m,kind,x,y):
    f,ctx=m.handler(m.page,O[kind]); m.word(m.event+O['EVENT_Y'],y)
    return m.call(key=x,address=f,args=(ctx,m.event,0,0),event_type=O[kind],gap=0,clear=False)
def sec_swipe(m,dx,dy=0):
    """A touch down at (200, 100), a move halfway and a lift at dx, dy on: the move's and the lift's answers."""
    sec_touch(m,'EVT_POINTER_DOWN',200,100)
    got=sec_touch(m,'EVT_POINTER_MOVE_BEFORE',200+dx//2,100+dy//2),sec_touch(m,'EVT_POINTER_UP_BEFORE',200+dx,100+dy)
    m.advance(0,clear=False); return got
def sec_aborts(m):  # the rows told the touch is gone: an abort event made, and sent down the page
    return [c[3] for c in m.calls if c[0]=='pointer_event_init' and c[2]==O['EVT_POINTER_ABORT']],[c[1] for c in m.calls if c[0]=='widget_dispatch_event_to_target_recursive']

def art_file(uri,ctx):
    h=2166136261
    for c in f'{uri}\t{ctx}'.encode(): h=((h^c)*16777619)&0xffffffff
    return f'/tmp/q2-soulseek.art/{h:08x}.jpg'

def heights(c): return [c.get(r+O['W_H']) for r in c.rows()]
def cover_images(c): return [k for r in c.rows() for k in c.nodes[r]['children'] if c.nodes[k].get('image','').startswith('file://')]

# A list's first row of kind search is the title bar's search icon instead (Tidal's s_img_search, addon.c
# take_find), the title narrowed before it; a tap opens the T9 box as the row did.
def find_icon(s): return next((w for w in s.nodes[s.page]['children'] if s.nodes[w].get('style')=='s_img_search'),0)
def find_tap(s,w):
    f,ctx=s.handler(w,O['EVT_CLICK']); assert s.call(address=f,args=(ctx,s.event,0,0),gap=0,count=5_000_000)==0; s.advance(0,clear=False)

code=[[(x*7+y*3)%5<2 for x in range(21)] for y in range(21)]
hexed=''.join(''.join('%x'%sum(8>>b for b in range(4) if d*4+b<21 and row[d*4+b]) for d in range(6)) for row in code)

def window_of_node(k,w):
    while k.get(w+O['W_PARENT']) and w!=k.top: w=k.get(w+O['W_PARENT'])
    return w

class TipsMachine(Machine):
    def hook(self,u,address,size,x):
        if self.handlers.get(address)=='stock_color':
            a=u.reg_read(UC_MIPS_REG_A0); self.word(a,self.stock_color); self.returns(a); return
        super().hook(u,address,size,x)
    def tips(self,stock,fallback):
        out=self.alloc(4); self.stock_color=stock
        self.call(address=HOOKS['style_get_color'][0],args=(out,0x1234,self.string('tips_text_color'),fallback),gap=0); return self.get(out)

def slsk_gone():
    """Soulseek's menu, then its loop still running (its pid file) but its socket gone."""
    m=SlskMachine(); m.open(); m.answer('ok','refresh','',SMENU); m.files[SPID]=bytearray(b'123')
    m.serve=None; m.advance(10000); m.worker(); return m

class FileBrowserMachine(AppMachine):
    app,manifest='filebrowser','name=File Browser\nsearch=Search\nqr=1\n'

class QobuzMachine(AppMachine): app,manifest='qobuz','name=Qobuz\nsearch=Search Qobuz\nqr=0\ngoto=1\n'

class PodMachine(AppMachine):
    app,manifest='podcasts','name=Podcasts\nsearch=Search Podcasts\nqr=0\n'
    def poll(self,ms=500):  # time() follows the clock, jump on (Wi-Fi setting it); the boot clock is self.now
        self.wms=getattr(self,'wms',0)+ms; self.wall=(86400+getattr(self,'jump',0)+self.wms//1000,self.wall[1]); return super().poll(ms)
    def hook(self,u,address,size,unused):  # unlink's path read as it is called: a stack buffer
        if self.handlers.get(address)=='unlink@GLIBC_2.0': self.unlinked.append(self.text(u.reg_read(UC_MIPS_REG_A0)))
        return super().hook(u,address,size,unused)
SLEEP='/tmp/q2-podcasts.sleep'
def pod_sleep():
    m=PodMachine(); m.unlinked=[]; m.open(); m.answer(*OK,[]); m.mock('player_play_pause','unlink@GLIBC_2.0'); m.play=2
    return m
paused=lambda names: names.count('player_play_pause')

def end_sleep(before=None):  # before: the app's request before "end"
    m=pod_sleep(); m.word(syms['mcl_pdeqplaylist'],m.deque([m.song(n) for n in 'ABC'])); m.word(O['MCL_POS'],0)
    m.playtime=100
    if before: m.files[SLEEP]=bytearray(before); m.poll()
    m.files[SLEEP]=bytearray(b'end\n'); assert not paused(m.poll()); return m

class DeezerMachine(AppMachine):
    app,manifest='deezer','name=Deezer\nsearch=Search Deezer\nqr=0\n'

LIB,DONE,EXP='/tmp/q2-deezer.library','/tmp/q2-deezer.library.done','/mnt/mmc/_explaylist_data'
def windows(m,*names):
    """The window manager's children: the status bar, Home, Streaming, the app's page, then names (open too)."""
    bar=m.get(syms['system_bar']) or m.node('system_bar','system_bar'); m.word(syms['system_bar'],bar)
    home=[m.node('window',n) for n in ('home_page','stream_page')]
    m.nodes[m.wm]=dict(type='window_manager',name='',children=[bar,*home,m.page,*(m.node('window',n) for n in names)],visible=1)
    m.open_pages=set(names)

def lib_machine():
    """Deezer's page open, stopped, a card in, Mix.m3u in the folder, stock's Playlists load mocked."""
    m=DeezerMachine(); m.open(); m.files['/tmp/mmc_add']=bytearray(); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,1)
    lc=int(manifest['patch_symbols']['stock_localclass_trampoline'],16); m.u.hook_add(UC_HOOK_CODE,lambda u,a,s,x: m.returns(0),begin=lc,end=lc)
    m.files[f'{EXP}/Mix.m3u']=bytearray(b'#EXTM3U\n'); windows(m)
    return m

class UpdateMachine(RadioMachine):
    """RadioMachine with the card in and GitHub's API: release answers the request for the tag's
    release in reply's place; a transfer carried on from a byte (RESUME_FROM, froms) gets reply from
    there; a file opened to append is written at its end."""
    def __init__(self):
        super().__init__({'/proc/mounts':bytearray(b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n')}); self.release=b''; self.froms=[]
        self.word(syms['g_power_capacity'],80)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='r:curl_easy_perform' and 20011 in self.opts:
            start=self.opts.get(21,0); self.froms.append(start); reply=self.reply
            self.reply=self.release if '/releases/tags/' in self.opts[10002] else reply[start:]
            try: return super().hook(u,address,size,unused)
            finally: self.reply=reply
        if name=='b:fopen@GLIBC_2.2' and 'a' in self.text(u.reg_read(UC_MIPS_REG_A1)):
            super().hook(u,address,size,unused); f=u.reg_read(UC_MIPS_REG_V0)
            if f: self.handles[f][1]=len(self.files[self.handles[f][0]])
            return
        return super().hook(u,address,size,unused)

class ShelfMachine(RadioMachine):
    """RadioMachine with busybox's side scripted: unzip -l prints listing, unzip -o stages staged
    ((path, bytes), in find's order), find lists them; statfs has free bytes; renames kept in order;
    listed, once set, answers every sources.list fetch (an install's first) in reply's place."""
    def __init__(self,files=None):
        super().__init__(files); self.free=1<<32; self.moves=[]; self.listing=''; self.staged=[]; self.listed=None
        self.mock('rmdir@GLIBC_2.0')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='r:curl_easy_perform' and self.listed is not None and self.opts.get(10002,'').endswith('/sources.list'):
            reply,self.reply=self.reply,self.listed
            try: return super().hook(u,address,size,unused)
            finally: self.reply=reply
        if name=='r:curl_easy_getinfo' and 20011 not in self.opts and self.opts.get(10002,'').startswith('https://github.com/'):
            # a check's releases/latest: the redirect to the tag reply's JSON names, else none (404)
            tag=re.search(rb'"tag_name" *: *"([^"]+)"',self.reply); saved=self.code,self.location,self.codes
            self.code,self.location,self.codes=(302,'https://github.com/o/r/releases/tag/'+tag[1].decode(),[]) if tag else (404,'',[])
            try: return super().hook(u,address,size,unused)
            finally: self.code,self.location,self.codes=saved
        if name=='c:statfs@GLIBC_2.0':
            b=u.reg_read(UC_MIPS_REG_A1); self.word(b+4,4096); self.word(b+28,self.free>>12); self.returns(0,clobber=True); return
        if name=='b:rename@GLIBC_2.0': self.moves.append(self.text(u.reg_read(UC_MIPS_REG_A1)))
        if name=='s:system@GLIBC_2.0':
            cmd=self.text(u.reg_read(UC_MIPS_REG_A0)); out=cmd.split(' > ')[-1]
            if 'unzip -l' in cmd: self.files[out]=bytearray(self.listing.encode())
            elif 'unzip -o' in cmd:
                for path,data in self.staged: self.files[cmd.split('-d ')[1].split(' ')[0]+'/'+path]=bytearray(data)
            elif ' find ' in cmd: self.files[out]=bytearray(''.join(path+'\n' for path,_ in self.staged).encode())
            elif cmd.startswith('rm -rf /mnt/mmc/.q2/staging'):
                for f in [f for f in self.files if f.startswith('/mnt/mmc/.q2/staging')]: del self.files[f]
        return super().hook(u,address,size,unused)
def apps_open(m):
    assert m.call(address=payload_syms['shelf_open'],args=(0,m.event,0,0),gap=0,count=5_000_000)==0
    m.advance(0,clear=False); m.page=m.top; assert m.nodes[m.page]['name']=='apps_page'
def shelf_job(m,ms=500):
    worker,arg=m.threads[-1]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=50_000_000)==0; m.advance(ms,clear=False)
KILL='killall -9 librespot aplay'

RECORD='/mnt/data/q2-state'  # system.c state_poll's record, written on the first pass and on a change
RECORD_FD=77  # PowerMachine's fileno for it, so its fsync is told apart
class ResumeMachine(QueueMachine):
    def __init__(self,files=None):
        super().__init__()
        self.files={} if files is None else files; self.open={}; self.play=(0,0); self.seeks=[]; self.opens=self.renames=0
        self.handlers[sleep_hook+12]='stock_sleep'
        for n in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0',
                  'access@GLIBC_2.0','time@GLIBC_2.0','player_playtime_and_length',
                  'fseek@GLIBC_2.0','ftell@GLIBC_2.0'): self.handlers[syms[n]]='r:'+n  # scrobble_append's checks
        self.handlers[payload_syms['stock_seek_time_trampoline']]='r:player_seek_time'
        self.epoch=1700000000
        self.mock('tk_snprintf','strstr@GLIBC_2.0')
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')  # scrobble.c's log lock
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0; self.calls.append((name,a,b,c))
        if name=='fopen':
            path,mode=self.text(a),self.text(b)
            # the boot logo's lit-only mount poll; dark, any open counts
            self.opens+=path!=RECORD and (path!='/proc/mounts' or not self.u.mem_read(syms['g_backlight_status'],1)[0])
            if mode=='rb' and path not in self.files: ret=0
            else: ret=0x2000000+len(self.calls); self.open[ret]=[path,mode,0,b'' if mode=='wb' else self.files.get(path,b'')]
        elif name=='fread':
            f=self.open[d]; data=f[3][f[2]:f[2]+b*c]; f[2]+=len(data); self.u.mem_write(a,data); ret=len(data)//b
        elif name=='fwrite': self.open[d][3]+=bytes(self.u.mem_read(a,b*c)); ret=c
        elif name=='fseek': f=self.open[a]; f[2]=signed(b)+(0,f[2],len(f[3]))[c]
        elif name=='ftell': ret=self.open[a][2]
        elif name=='fclose':
            path,mode,_,data=self.open.pop(a)
            if mode in ('wb','ab'): self.files[path]=data
        elif name=='access': ret=0 if self.text(a) in self.files else -1
        elif name=='time': ret=self.epoch
        elif name=='rename': self.renames+=1; self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='player_playtime_and_length':
            if self.play[1]: self.word(a,self.play[0]); self.word(b,self.play[1]); ret=1
            else: ret=-1
        elif name=='player_seek_time':  # a jump nobody pressed: past the hook, no stamp (docs/internals.md#wired-start)
            assert not isinstance(self,ResumeMachine) or not any(c[0]=='stock_press_stamp' for c in self.calls)
            self.seeks.append(signed(a)); self.play=(signed(a),self.play[1])
        self.returns(ret,clobber=True)
    def poll(self,sec,total=3600,pos=None,n=1):  # n polls a second apart, the time standing at sec
        if pos is not None: self.word(O['MCL_POS'],pos)
        for _ in range(n):
            self.play=(sec,total); self.now+=1000
            assert self.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)==0
            sec=self.play[0]  # where a seek left it
        return self
    def places(self):
        data=self.files.get('/mnt/data/ringnav-resume',b'')
        return [(k,signed(v)) for k,v in struct.iter_unpack('<II',data) if k]

book='/mnt/mmc/Book/book.m4b'
def chapter(m,r,start,end,path=book):
    m.word(r+O['REC_PATH'],m.string(path)); m.word(r+O['REC_CUE_START'],start); m.word(r+O['REC_CUE_END'],end)

def counts(m): return [(k,n) for k,n in struct.iter_unpack('<II',m.files.get('/mnt/data/ringnav-plays',b'')) if k]

def logged(m): return len(m.files.get('/mnt/mmc/.scrobbler.log',b'').split(b'\n'))-4

def settings_page(hook,view_name,config={},stock_rows=2,bt=1,boot=False,cls=QueueMachine,sub=0):
    m=cls(); m.config.update(config); m.restack=True
    m.present.add('/mnt/data/config.ini')  # a slider page's close writes only into a settings file that is there
    if hook=='display': m.word(payload_syms['display_sub'],sub)  # 3: Controls
    if hook=='audioset': m.word(payload_syms['playset_sub'],sub)  # 1: Library & Folders
    if boot:
        m.handlers[payload_syms['peq_boot']]='stock_peq_boot'  # FP64 storage checked by test/peq.py
        m.call(address=payload_syms['ringnav_bootmode'],args=(3,0,0,0),gap=0)  # config_init's play mode
    if hook=='btrows': m.byte(syms['g_bluetoothflag'],bt)
    m.handlers[int(manifest['patch_symbols'][f'stock_{hook}_trampoline'],16)]='stock_'+hook
    entries=[m.entry(0) for _ in range(stock_rows)]
    view=m.node('scroll_view',view_name,list(entries))
    for e in m.nodes[view]['children']: m.word(e+O['W_PARENT'],view)
    m.top=m.node('window','page',[m.node('list_view','list_view',[view])])
    target={'power':'systemset_powermanager_page_init','audioset':'playset_playset_page_init','btrows':'btmenu_rows','display':'systemset_display_page_init'}[hook]
    assert m.call(address=HOOKS[target][0],args=(m.top,5,0,0),gap=0)==0 and m.calls[0][:3]==('stock_'+hook,m.top,5)
    rows=sorted(r for r in set(m.nodes[view]['children'])-set(entries) if m.nodes[r]['children'] and m.nodes[m.nodes[r]['children'][0]]['type']=='button')  # ours, as made (the page may restack)
    buttons=[m.nodes[r]['children'][0] for r in rows]; labels=[next(c for c in m.nodes[b]['children'] if m.nodes[c]['type']=='hscroll_label') for b in buttons]
    icons=[m.nodes[m.nodes[b]['children'][0]].get('image') for b in buttons]  # None: no icon
    def click(i):
        m.calls=[]; f,ctx=m.handler(buttons[i],O['EVT_CLICK'])
        assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
        return [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
    m.entries=entries
    m.settings_view=view
    return m,rows,lambda:[m.nodes[l]['text'] for l in labels],icons,click

def switch_image(m,row):
    button=m.nodes[row]['children'][0]
    images=[c for c in m.nodes[button]['children'] if m.nodes[c].get('name')=='_setting_switch']
    assert len(images)==1
    return m.nodes[images[0]]['image']

from build import HOME_KEYUP
def home_keyup(patched):
    c=Machine(patched); home=c.node('window','home_page')
    for r,v in ((UC_MIPS_REG_SP,0x7000f000),(UC_MIPS_REG_S0,home)): c.u.reg_write(r,v)
    c.word(0x7000f000+0x10,GP); c.u.emu_start(HOME_KEYUP[0]-0x18,HOME_KEYUP[0]+8,count=20)
    return [x[2:] for x in c.calls if x[:2]==('widget_on',home)],c.u.reg_read(UC_MIPS_REG_A2)  # a mocked call clobbers $a2
def home_return(handler):
    c=Machine(False); c.mock('on_home_playing_onclick')
    return c.call(O['KEY_RETURN'],address=handler,args=(c.node('window','home_page'),c.event,0,0),gap=0),[x[0] for x in c.calls]

def lock_applies(patched,lit,window,on=1):
    c=Machine(patched)
    for name,v in (('g_backlight_status',lit),('g_lockscreen_pageflag',window),('g_keylock_flag',on),('g_keylock_mode',1)): c.byte(syms[name],v)
    for r,v in ((UC_MIPS_REG_SP,0x7000f000),(UC_MIPS_REG_GP,GP),(UC_MIPS_REG_V0,syms['g_backlight_status']),(UC_MIPS_REG_V1,0xac)): c.u.reg_write(r,v)
    c.u.emu_start(0x4f2714,0x4f276c,count=12)
    return c.u.reg_read(UC_MIPS_REG_PC)!=0x4f276c

def vol_steps(patched,lit):
    c=Machine(patched); c.mock('device_set_volume','write_int_config','widget_get_prop','value_uint32','timer_count','widget_remove_timer','value_set_uint32','widget_set_prop','widget_add_timer')
    for name,v in (('g_backlight_status',lit),('g_volume',10),('g_maxvolume',100)): c.byte(syms[name],v)
    ret=c.call(O['KEY_NEXT'],address=0x4a3688,args=(c.node('dialog','volume_dialog'),c.event,0,0),gap=0)
    return ret,c.u.mem_read(syms['g_volume'],1)[0],[x[0] for x in c.calls if x[0] in ('device_set_volume','write_int_config')]

def held_key(patched,changed=True):
    c=Machine(patched); c.mock('widget_on_keydown','widget_on_keyup'); c.mock('log_get_log_level',prefix='one:')
    c.handlers[syms['widget_get_window_manager']]='window_manager'; c.handlers.pop(syms['key_event_init'],None)
    ids=c.alloc(0x200); record=ids+O['INPUT_KEYS']
    sent=lambda: [x[0] for x in c.calls if x[0] in ('widget_on_keydown','widget_on_keyup')]
    def key(kind,gap): c.call(O['KEY_PLAY'],address=syms['input_device_status_on_input_event'],args=(ids,c.wm,c.event,0),event_type=kind,gap=gap); return sent()
    def wait(ms): c.advance(ms); return sent()  # its long-press timer runs, stock's 1000 ms or KEY_HOLD_MS
    out=[key(O['EVT_KEY_DOWN'],0),wait(1000)]; pressed=c.get(record+O['INPUT_KEY_TIME'])
    if changed: c.call(address=syms['input_device_status_abort_all_pressed_keys'],args=(ids,0,0,0),gap=0)
    return out+[key(O['EVT_KEY_DOWN'],250),c.get(record+O['INPUT_KEY_TIME'])-pressed,wait(2000),key(O['EVT_KEY_UP'],0),c.get(record)]

def keyups(patched,handler):
    """{key: (lit, dark)} for each key handler doesn't pass untouched both ways: the functions it calls, then stop if it stops the key."""
    c=Machine(patched); c.handlers.clear(); end=handler+4; names={a:n for n,a in syms.items()}
    while c.get(end)>>16!=0x3c1c: end+=4  # the next function's PIC prologue (lui $gp)
    c.byte(syms['g_wheelmode'],1)
    def stub(u,address,size,_):
        if not handler<=address<end: c.calls.append(names.get(address)); c.returns(0)
    c.u.hook_add(UC_HOOK_CODE,stub); out={}
    for key in (O['KEY_RETURN'],O['KEY_PLAY'],O['KEY_PREV'],O['KEY_NEXT'],O['KEY_CENTER'],O['KEY_BACK_BTN'],O['KEY_FWD_BTN']):
        row=[]
        for lit in (1,0):
            c.byte(syms['g_backlight_status'],lit); ret=c.call(key,address=handler,args=(c.alloc(0x100),c.event,0,0),gap=0)
            assert ret in (0,0xb),(hex(handler),key,ret)
            row.append(' '.join([n for n in c.calls if n not in (None,'widget_lookup','widget_focused_widget_in_window')]+['stop']*(ret==0xb)))
        if any(row): out[key]=tuple(row)
    return out
def stock_keyups():
    ret,play,prev,nxt,centre,rew,fwd=(O['KEY_'+k] for k in ('RETURN','PLAY','PREV','NEXT','CENTER','BACK_BTN','FWD_BTN'))
    stop=('stop',''); wheel={prev:stop,nxt:stop,centre:stop}; every={**wheel,ret:stop,play:stop,rew:stop,fwd:stop}
    for want,handlers in (
            # Return goes back by on_return_onclick: on_common_keyup (most pages), allmusic_page, album_page, artistinfo_page,
            # localclass_page, playerqueue_page, playerjumpinfo_page and search_dialog
            ({ret:('on_return_onclick stop','')},(syms['on_common_keyup'],0x4abc34,0x4a608c,0x4b0f50,0x4b2ab8,0x53a930,0x538a88,0x49eb70)),
            # ... or by a function of the window's own: folder_page, specfolder_page and sortselect_dialog
            ({ret:stop},(0x533310,syms['on_specfolder_keyup'],0x4a0b54)),
            # confirminfo_dialog: Return quits it and goes on; the wheel and Centre end here
            ({**wheel,ret:('dialog_quit','')},(0x499b9c,)),
            # msginfo_dialog, the toast: as the confirm, and Play/Pause and the side buttons end here too
            ({**every,ret:('dialog_quit','')},(0x49b7c8,)),
            # checkfw_dialog, the update's: every key
            (every,(0x499528,))):
        for handler in handlers: assert keyups(True,handler)==keyups(False,handler)==want,hex(handler)
    # playing_page: Return as the first group's. Stock V1.36 ends Centre there too (the screen off 350 ms on, or at once on
    # a second); under NP_CENTRE it goes on to on_wm_keyup_fun, which ringnav's passed-on releases are for.
    back={ret:('on_return_onclick stop','')}
    assert keyups(True,0x53da5c)==back and keyups(False,0x53da5c)=={**back,centre:('power_keyup_detect power_keyup_pending_custom stop','')}
    # updatemusic_dialog, the library scan's: Return and the wheel end there; stock's Centre too, which SCAN_CENTRE sends on
    assert keyups(True,0x4a2a94)=={prev:stop,nxt:stop,ret:stop} and keyups(False,0x4a2a94)=={**wheel,ret:stop}

class TagMachine(CardMachine):
    def __init__(self,files):
        super().__init__({},files); self.stock_artist=b''
        self.handlers.pop(syms['toolsGetMusicInfo'],None)  # run the hook; only stock's body is mocked
        tramp=int(manifest['patch_symbols']['stock_music_info_trampoline'],16)
        self.handlers[tramp]='stock_music_info'; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=tramp,end=tramp)
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='stock_music_info': return super().hook(u,address,size,unused)
        u.mem_write(u.reg_read(UC_MIPS_REG_A1)+0x580,self.stock_artist+b'\0')
        self.returns(1)
    def album_artist(self,path,stock=b''):
        self.stock_artist=stock; info=self.alloc(0x6a0); p=self.alloc(256); self.u.mem_write(p,path.encode()+b'\0')
        assert self.call(address=HOOKS['toolsGetMusicInfo'][0],args=(0,info,p,0),gap=0)==1
        return self.text(info+0x580)
def flac(*comments,picture=0):
    vc=struct.pack('<I',6)+b'vendor'+struct.pack('<I',len(comments))+b''.join(struct.pack('<I',len(c))+c for c in comments)
    blocks=[(0,bytes(34))]+([(6,bytes(picture))] if picture else [])+[(4,vc)]
    return bytearray(b'fLaC'+b''.join(bytes([t|(0x80 if i==len(blocks)-1 else 0)])+len(d).to_bytes(3,'big')+d
                                      for i,(t,d) in enumerate(blocks))+b'audio')

def ogg_pages(*packets):  # each packet from a fresh page, 255 lacing values a page
    out=bytearray(); seq=0
    for p in packets:
        lac=[255]*(len(p)//255)+[len(p)%255]; at=0
        for j in range(0,len(lac),255):
            seg=lac[j:j+255]; n=sum(seg)
            out+=b'OggS\0'+bytes([(1 if j else 0)|(0 if seq else 2)])+bytes(8)+struct.pack('<III',1,seq,0)+bytes([len(seg)])+bytes(seg)+p[at:at+n]
            at+=n; seq+=1
    return out
def opus(*comments,picture=0):
    items=([b'METADATA_BLOCK_PICTURE='+b'A'*picture] if picture else [])+list(comments)
    tags=b'OpusTags'+struct.pack('<I',4)+b'test'+struct.pack('<I',len(items))+b''.join(struct.pack('<I',len(c))+c for c in items)
    return ogg_pages(b'OpusHead'+bytes([1,2])+struct.pack('<HIhB',312,48000,0,0),tags,b'\xfc'*40)
class OggTagMachine(TagMachine):
    def __init__(self,files):
        super().__init__(files); self.fixes=[]; self.stock_calls=0
        self.handlers[syms['toolsFixId3ErrorCode']]='fix_id3'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address)
        if name=='stock_music_info': self.stock_calls+=1
        if name!='fix_id3': return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]
        self.fixes.append((self.get(u.reg_read(UC_MIPS_REG_SP)+16),a,self.raw(b),self.raw(c)))
        u.mem_write(d,self.raw(c)[:255]+b'\0'); self.returns(0,clobber=True)  # stock's snprintf of the UTF-8 copy
    def info(self,path,a=0):
        info=self.alloc(0x6a0); self.u.mem_write(info,bytes(0x6a0)); self.fixes=[]; self.stock_calls=0
        assert self.call(address=HOOKS['toolsGetMusicInfo'][0],args=(a,info,self.string(path),0),gap=0)==1
        return info

class OggCoverMachine(TagMachine):
    def __init__(self,files):
        super().__init__(files); self.stock_covers=[]; self.ffmpeg=0
        self.handlers.pop(syms['toolsGetAlbumCover'],None)  # the hook runs; stock's body is mocked
        tramp=int(manifest['patch_symbols']['stock_album_cover_trampoline'],16)
        self.handlers[tramp]='stock_album_cover'; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=tramp,end=tramp)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address)
        if name=='stock_album_cover': self.stock_covers.append(self.text(u.reg_read(REGS[0]))); return self.returns(1,clobber=True)
        if name!='b:waitpid@GLIBC_2.0': return super().hook(u,address,size,unused)
        self.ffmpeg+=1; self.files[self.dst+'.ff']=bytearray(b'jpeg')  # ffmpeg wrote its picture and exited 0
        self.word(u.reg_read(REGS[1]),0); self.returns(u.reg_read(REGS[0]),clobber=True)
    def cover(self,track):
        self.stock_covers=[]; self.ffmpeg=0; self.dst='/mnt/mmc/.sldp/x0.jpg'
        return self.call(address=payload_syms['coverflow_grid_cover'],args=(self.string(track),self.string(self.dst),110,110),gap=0)

class PngMachine(Machine):
    """Stock's libpng routines (the reader for an embedded PNG, 0x5c91bc, and toolsGrayPngConvRgbPng) over a libpng
    whose call `fail` refuses the file: it longjmps to the routine's last _setjmp with 1, as png_error does. The
    picture is gray, 3 by 2. png holds each library call with its first two arguments; live, what is allocated or
    open."""
    FILE,PNG,INFO,MALLOC=0x1100000,0x1100100,0x1100200,0x1000020
    LIBPNG=('sig_cmp','create_read_struct','create_info_struct','init_io','read_png','destroy_read_struct','read_info',
            'get_color_type','get_image_width','get_image_height','get_bit_depth','get_valid','set_filler','set_gray_to_rgb',
            'read_update_info','get_rowbytes','read_image','create_write_struct','set_IHDR','set_expand','write_info',
            'write_image','write_end','destroy_write_struct')
    def __init__(self,patched,fail):
        super().__init__(patched); self.fail=fail; self.png=[]; self.live=set()
        for n in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','rewind@GLIBC_2.0','_setjmp@GLIBC_2.0','fclose@GLIBC_2.2','printf@GLIBC_2.0',
                  *('png_%s@PNG12_0'%n for n in self.LIBPNG)): self.handlers[syms[n]]='png:'+n.split('@')[0]
        self.word(GP-0x9f8,self.MALLOC); self.handlers[self.MALLOC]='png:malloc'  # malloc's GOT slot, and free's
        self.handlers[self.get(GP-0x870)]='png:free'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('png:'): return super().hook(u,address,size,unused)
        name=name[4:]; a,b=(u.reg_read(r) for r in REGS[:2])
        assert name!='png_init_io' or u.reg_read(UC_MIPS_REG_T9)==address  # the guards' jump to it is a PIC call too
        self.png.append((name,self.get(a),self.get(b)) if 'destroy' in name else (name,a,b))
        if name=='_setjmp': self.jmpbuf=[(r,u.reg_read(r)) for r in (UC_MIPS_REG_RA,UC_MIPS_REG_SP,UC_MIPS_REG_GP,*SAVED)]
        if name==self.fail:
            for r,v in self.jmpbuf: u.reg_write(r,v)
            u.reg_write(UC_MIPS_REG_V0,1); u.reg_write(UC_MIPS_REG_PC,self.jmpbuf[0][1]); return
        ret={'fread':4,'png_create_read_struct':self.PNG,'png_create_write_struct':self.PNG,'png_create_info_struct':self.INFO,
             'png_get_image_width':3,'png_get_image_height':2,'png_get_bit_depth':8,'png_get_rowbytes':12}.get(name,0)
        if name in ('fopen','malloc'): ret=self.FILE+len([c for c in self.png if c[0]=='fopen'])-1 if name=='fopen' else self.alloc(a); self.live.add(ret)
        if name in ('fclose','free'): self.live.remove(a)
        self.returns(ret,clobber=True)
    def read(self,address=0x5c91bc,count=None):
        """The reader for an embedded PNG, or toolsGrayPngConvRgbPng at 0x5cb7b4: what it returns."""
        self.png=[]; out=[self.alloc(4) for _ in range(3)]  # the reader's width, height and channels: written only for a picture
        return self.call(address=address,args=(self.string('/mnt/mmc/cover.png'),*out),gap=0,count=count)
    def loops(self,address=0x5c91bc):
        """Stock's: the routine never returns, and the failing call comes round again and again."""
        try: self.read(address,count=6000)
        except AssertionError as e: return str(e)=='Instruction limit reached' and [c[0] for c in self.png].count(self.fail)>10
        return False

class ScanNoteMachine(TagMachine):
    STATM=b'30000 22750 1200 1 0 25000 0\n'  # 22,750 resident pages: 91,000 kB
    def __init__(self,files):
        super().__init__(files); self.fds={}; self.seen=[]
        for n in ('open','read','write','close'): self.handlers[syms[n+'@GLIBC_2.0']]='s:'+n
        self.mock('snprintf@GLIBC_2.0')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c=(u.reg_read(r) for r in REGS[:3]); ret=0
        if name=='stock_music_info': self.seen.append(bytes(self.files.get('/tmp/q2-scan-file',b'')))
        if not name.startswith('s:'): return super().hook(u,address,size,unused)
        if name=='s:open':
            path=self.text(a); ret=0xffffffff
            if path=='/proc/self/statm' and b&3==0: self.files[path]=bytearray(self.STATM)
            if path in self.files or b&0x100:
                if b&0x200 or path not in self.files: self.files[path]=bytearray()
                ret=100+len(self.fds); self.fds[ret]=[path,b]
        elif name=='s:read': data=bytes(self.files[self.fds[a][0]][:c]); u.mem_write(b,data); ret=len(data)
        elif name=='s:write': assert self.fds[a][1]&3==1; self.files[self.fds[a][0]]+=bytes(u.mem_read(b,c)); ret=c
        elif name=='s:close': self.fds.pop(a,None)
        self.returns(ret,clobber=True)

def fnv1a(b,h=2166136261):
    for c in b: h=((h^c)*16777619)&0xffffffff
    return h

class MonoMachine(QueueMachine):
    def __init__(self):
        super().__init__(); self.flag=self.alloc(4); self.word(syms['g_equalizer_flag'],1)  # the filter is in
        for n in ('open','write','rename','mmap','close'): self.handlers[syms[n+'@GLIBC_2.0']]='mono:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('mono:'): return super().hook(u,address,size,unused)
        self.calls.append((name,)); self.returns(self.flag if name=='mono:mmap' else 3 if name=='mono:open' else 4 if name=='mono:write' else 0)

class FailedMapMachine(MonoMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='mono:mmap':
            self.calls.append(('mono:mmap',)); self.returns(0xffffffff); return
        return super().hook(u,address,size,unused)

def tint_machine(config,w=8,h=4,row=None,mode=0,fmt=3,clock=None):  # clock: the mock's at boot
    m=QueueMachine(); m.config.update(config); m.handlers[payload_syms['peq_boot']]='stock_peq_boot'
    if clock: m.clock=clock
    m.call(address=payload_syms['ringnav_bootmode'],args=(3,0,0,0),gap=0)  # config_init's: boot reads the setting
    fb=m.alloc(4*w*h+64); end=syms['widget_invalidate_force']  # stock's end_frame, as a recorded call
    for off,v in ((0xb0,w),(0xb4,h),(0x124,4*(row or w)),(0x128,fmt),(0xcc,mode),(0x110,fb),(0x7c,end),
                  (0xf8,2),(0xfc,1),(0x100,3),(0x104,2)):
        m.word(m.lcd+off,v)
    for i in range(w*h): m.word(fb+4*i,0xff000000|i*0x070503)
    m.word(fb+4*(1*w+3),0xf7123456)  # in the rect, warmed already
    return m,fb,end
def tint_frame(m):
    m.calls=[]; m.call(address=syms['lcd_end_frame'],args=(m.lcd,0,0,0),gap=0)
    return [c[1] for c in m.calls if c[0]=='widget_invalidate_force']  # stock's end_frame ran, with the lcd
def warmed(c,k=3400):
    i=(k-2000)//100; g,b=[137,142,146,151,155,159,163,167,170,174,177,180,184,187,190,193,195,198,201,203,206,208,211,213,215,218,220,222,224,226,228][i],[14,27,39,50,61,70,79,87,95,103,110,117,123,129,135,141,146,151,157,161,166,171,175,179,183,187,191,195,199,202,206][i]
    return 0xf7000000|c&0xff0000|((c>>8&255)*g+127)//255<<8|((c&255)*b+127)//255

def sleep_page(boot):
    m,sels,ok,slide=time_page('sleepshutdown_page'); boot(m); sw=m.node('image','img_switch',image='switch_off'); fields_=m.node('view','view_sleep',[slide,ok],visible=0)
    m.top=m.node('window','sleepshutdown_page',[m.node('view','view_navbar',[sw],visible=0),fields_]); m.word(m.top+O['W_PARENT'],m.wm); m.nodes[m.wm]={'children':[m.top]}
    m.handlers[int(manifest['patch_symbols']['stock_sleeptimer_trampoline'],16)]='stock_sleeptimer'
    m.mock('button_create','hscroll_label_create','widget_use_style','set_hscroll_label_attribute','widget_set_name','widget_on')
    assert m.call(address=HOOKS['systemset_sleepshutdown_page_init'][0],args=(m.top,0,0,0),gap=0)==0 and m.calls[0][0]=='stock_sleeptimer'
    return m,sw,fields_,ok

def hand_tip(m,x,y): return [b for b in m.bands if b[3]==1 and b[4]&0xffffff==0xffffff and b[0]<=x+1 and b[0]+b[2]>=x-1 and abs(b[1]-y)<=1]
def screen_box(m,w):
    x,y,ww,h=wbox(m,w)
    for p in iter(lambda p=[w]: p.__setitem__(0,m.get(p[0]+O['W_PARENT'])) or p[0],0):
        if p==m.top or p==m.wm: break
        x,y=x+signed(m.get(p+O['W_X'])),y+signed(m.get(p+O['W_Y']))
    return x,y,ww,h

def key_ink(k):  # the style hook's answer for stock's white, on what painted last
    out=k.alloc(4); k.word(out,0xffffffff); k.handlers[int(manifest['patch_symbols']['stock_color_trampoline'],16)]='stock_color'
    k.call(address=HOOKS['style_get_color'][0],args=(out,0x1234,k.string('text_color'),0),gap=0); return k.get(out)

sb=lambda q,w: screen_box(q,w)

def search_rows(setup):
    k=Machine(); setup(k); w=k.page('search_dialog','scroll_view'); k.word(w+O['W_PARENT'],k.top); k.word(k.top+O['W_PARENT'],k.wm)
    k.nodes[w]['name']='scroll_view_track'; k.row_record=k.alloc(0x80)
    for off in (8,12,16,24): k.word(k.row_record+off,k.string('Billie Jean'))
    k.word(k.row_record+0x48,1)
    k.mock('button_create','list_item_create','image_create','view_create','hscroll_label_create','gif_image_create','widget_use_style','widget_set_name','widget_set_text_utf8',
           'widget_set_visible','widget_set_enable','widget_on','widget_destroy_children','image_set_draw_type','image_base_set_image','set_hscroll_label_attribute','hscroll_label_set_only_focus',
           'hscroll_label_set_ellipses','hscroll_label_set_speed','gif_image_play','deque_at','deque_size','tk_snprintf','batch_get_selectitem','file_is_playing','getFormatString',
           'getMusicByPlayList','get_albumcover_listsize','local_songercover_task','albumcoverinfo_init','list_get_img_pic','strcmp@GLIBC_2.0','toolsTrimLeft','toolsTrimRight')
    k.handlers[0x49c924]='search_reset'; assert k.call(address=0x49d2d8,args=(k.top,0,0,0))==0
    return k

class PowerMachine(ResumeMachine):
    def __init__(self,config={},files=None):
        super().__init__(files); self.config.update(config); self.writes=[]; self.records=[]  # records: the state record's
        for n in ('fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0','unlink@GLIBC_2.0','open@GLIBC_2.0','close@GLIBC_2.0'):
            self.handlers[syms[n]]='p:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a=u.reg_read(REGS[0])
        if name.startswith('r:fclose') and self.open.get(a,[0,0])[1]=='w':
            path,_,_,data=self.open.pop(a); self.files[path]=data; (self.records if path==RECORD else self.writes).append((path,data)); self.calls.append(('fclose',a,0,0))
            self.returns(0); return
        if name.startswith(('p:fflush','p:fileno','p:fsync')) and (a==RECORD_FD or self.open.get(a,[''])[0]==RECORD):
            self.returns(RECORD_FD if name.startswith('p:fileno') else 0); return  # the record's sync, apart from the marker's
        if name.startswith('r:fopen') and self.text(u.reg_read(REGS[1]))=='w':
            ret=0x2000000+len(self.calls); self.open[ret]=[self.text(a),'w',0,b'']; self.calls.append(('fopen',a,0,0))
            self.returns(ret); return
        if not name.startswith('p:'): return super().hook(u,address,size,unused)
        self.calls.append((name[2:].split('@')[0],a,0,0))
        if name.startswith('p:unlink'): self.files.pop(self.text(a),None)
        self.returns(0)
    def pass_(self,ms=O['CHARGE_POLL_MS']):
        self.now+=ms; self.calls=[]
        assert self.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)==0
        return [c[1] for c in self.calls if c[0]=='switch_charge_enable'],[c[1] for c in self.calls if c[0]=='sleep_ms']
def level(m,pct,charging=0): m.word(syms['g_power_capacity'],pct); m.word(syms['g_power_chargestate'],charging)

class Live(PowerMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='v:live':
            self.returns(self.live); return
        return super().hook(u,address,size,unused)

RTC_NOW,RTC_ALARM='/sys/class/rtc/rtc0/since_epoch','/sys/class/rtc/rtc0/wakealarm'
class StandbyMachine(PowerMachine):
    def __init__(self,config={},key=None,readable=True,refuse=False,rtc=1792363806):
        super().__init__(config,files={RTC_NOW:f'{rtc}\n'.encode()} if readable else {})
        self.rtc,self.key,self.readable,self.refuse,self.alarm,self.events=rtc,key,readable,refuse,0,[]
        self.handlers[int(manifest['patch_symbols']['stock_standby_trampoline'],16)]='stock_standby'
        self.handlers.pop(syms['mclGetPlayStatus'],None); self.word(0xa50084,3)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a=u.reg_read(REGS[0])
        if name=='stock_standby':  # asleep until the key (seconds in) or the alarm, whichever is first
            ends=[t for t in (self.alarm,self.key and self.rtc+self.key) if t]; assert ends,'nothing wakes it'
            self.rtc=min(ends)
            if self.rtc==self.alarm: self.alarm=0  # it fired
            if self.readable: self.files[RTC_NOW]=f'{self.rtc}\n'.encode()
            self.events.append('suspend'); self.returns(0); return
        if name.startswith('r:fclose') and self.open.get(a,[''])[0]==RTC_ALARM:
            v=int(self.open.pop(a)[3])  # rtc-sysfs: a time not ahead clears it; a set alarm refuses another
            ok=not self.refuse and (v<=self.rtc or not self.alarm)
            if ok: self.alarm=v if v>self.rtc else 0
            self.events.append(('alarm',v)); self.returns(0 if ok else 0xffffffff); return
        return super().hook(u,address,size,unused)
    def standby(self):
        self.events=[]; assert self.call(address=HOOKS['toolsIntoStandby'][0],args=(0,0,0,0),gap=0)==0
        return self.events
    def off(self):  # the next UI loop pass powers off
        self.opened=[]; self.pass_(1); return [p for p,*_ in self.opened]==['poweroff_page']

WDT_START,WDT_STOP,WDT_FEED=0x8004570d,0x2000570e,0x2000570f
class WatchdogMachine(StandbyMachine):
    def __init__(self,config={},fd=3,refuse_start=False,**k):
        super().__init__(config,**k); self.fd,self.refuse_start,self.ioctls,self.wdt_opens=fd,refuse_start,[],0
        self.handlers[syms['ioctl@GLIBC_2.0']]='d:ioctl'
        self.byte(syms['g_backlight_status'],1); self.byte(syms['g_usbdet_value'],0)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b,c=[u.reg_read(r) for r in REGS[:3]]
        if name=='p:open@GLIBC_2.0':
            assert self.text(a)=='/dev/jz_watchdog' and b==0x80002; self.wdt_opens+=1; self.returns(self.fd&0xffffffff); return
        if name=='d:ioctl':
            self.ioctls.append((a,b,self.get(c)) if b==WDT_START else (a,b))
            if b!=WDT_FEED: self.events.append(('wdt',b))
            self.returns(0xffffffff if b==WDT_START and self.refuse_start else 0); return
        return super().hook(u,address,size,unused)
    def record(self): return self.files.get(RECORD)

RX_SIGNAL='/proc/net/rtl8733bs/wlan0/rx_signal'
class WifiMachine(Machine):
    def __init__(self,pct,patched=True):
        super().__init__(patched); self.pct=pct
        self.mock('strstr@GLIBC_2.0','strncpy@GLIBC_2.0')
        for n in ('open','read','close','socket','ioctl'): self.handlers[syms[n+'@GLIBC_2.0']]='w:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('w:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address
        name=name[2:]; a,b,c=(u.reg_read(r) for r in REGS[:3]); ret=0
        self.calls.append((name,a,b,c))
        if name=='open': ret=3 if self.pct is not None and self.text(a)==RX_SIGNAL else -1
        elif name=='read':  # the driver's proc_get_rx_signal, as far as it matters
            data=f'rssi:{self.pct}\nsignal_strength:{self.pct}\nsignal_qual:100\n'.encode()[:c]
            u.mem_write(b,data); ret=len(data)
        elif name=='socket': ret=5
        elif name=='ioctl':  # cfg80211_wireless_stats: iw_statistics behind iwreq's data pointer
            assert (a,b)==(5,0x8b0f); u.mem_write(self.get(c+16),bytes([0,0,70,(self.pct-100)&255]))
        self.returns(ret,clobber=True)
    def signal(self): return self.call(address=HOOKS['networkGetWifiSignal'][0],args=(0,0,0,0),gap=0)
    def used(self): return [c[0] for c in self.calls if c[0] in ('open','read','close','socket','ioctl')]

# Smart Wi-Fi (docs/internals.md#smart-wi-fi): with Power's row on and the user's Wi-Fi on, the radio
# goes off once nothing has needed the network for SMART_IDLE_MS, through stock's own switch (wifi_switch
# runs here, as Quick Settings and the Wi-Fi page call it) with its WIFI= write dropped in the payload's
# write_int_config hook, and comes back when something needs it. The link and its signal are mocked;
# everything else is stock's or the payload's.
class SmartMixin:
    def __init__(self,*a,smart='1',**k):
        super().__init__(*a,**k)
        if smart: self.config['SMARTWIFI']=smart
        self.handlers.pop(syms['write_int_config'],None)  # the payload's hook runs; the write past it is recorded
        self.handlers[HOOKS['write_int_config'][0]+12]='w:write'
        self.mock('remove@GLIBC_2.0','stop_wifisynclink','networkStart','networkStop')
        for n in ('networkGetConnectStatus','get_wifisignal'): self.handlers[syms[n]]='w:'+n
        self.link=0; self.status=None  # status: stock's connect status when not the link's (its own poll)
        self.nodes[self.wm]=dict(type='window_manager',name='',children=[],visible=1,enable=1)
        self.byte(syms['g_wififlag'],1); self.word(O['NET_TYPE'],2)  # the user's Wi-Fi on, the module up
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('w:'): return super().hook(u,address,size,unused)
        if name=='w:write':  # stock's write past the hook (its trampoline jumps, no t9): recorded as the mock's
            self.calls.append(('write_int_config',*[u.reg_read(r) for r in REGS[:3]])); self.returns(0); return
        self.calls.append((name[2:],0,0,0))
        status=self.link if self.status is None else self.status
        self.returns(status if name=='w:networkGetConnectStatus' else 3 if self.link else -1,clobber=True)
    def windows(self,*names):
        self.nodes[self.wm]['children']=[self.node('window',n) for n in names]
        return self.nodes[self.wm]['children']
    def switch(self): return [c[0] for c in self.calls if c[0] in ('networkStart','networkStop','write_int_config')]
    def flag(self): return self.u.mem_read(syms['g_wififlag'],1)[0]
class SmartMachine(SmartMixin,PowerMachine):
    def __init__(self,smart='1'): super().__init__(smart=smart); self.mock('system@GLIBC_2.0')
SW_IDLE,SW_POLL,SW_CONNECT=O['SMART_IDLE_MS'],O['SMART_POLL_MS'],O['SMART_CONNECT_MS']
SW_INIT='/tmp/wpasupplicant_initsuccess'
SW_WAIT='Connecting to Wi-Fi…'
def wifi_calls(m):  # wifi_switch's own: its file, SyncLink, the name, the network and the write; the first argument
    calls=[(c[0].split('@')[0],c[1]) for c in m.calls]
    keep=lambda c: (c[0] in ('networkStop','networkStart','write_int_config','stop_wifisynclink') or
                    (c[0] in ('access','remove') and m.text(c[1])==SW_INIT) or (c[0]=='memset' and c[1]==syms['wifi_name']))
    return [c if c[0] in ('access','remove','memset','write_int_config') else c[:1] for c in calls if keep(c)]
def wifi_state(m):
    return [m.u.mem_read(a,1)[0] for a in (syms['g_wififlag'],syms['wifi_connectstatus'],syms['wifi_initstatus'],0xa4db3a,
                                           syms['wifilink_timeout'])]+[bytes(m.u.mem_read(syms['wifi_name'],4))]
def connected(m):  # stock's state on a link: its name, the connect state, the init file, SyncLink over Bluetooth
    m.files[SW_INIT]=b''; m.byte(syms['g_synclinkflag'],1); m.byte(syms['wifi_connectstatus'],2)
    m.byte(0xa4db3a,5); m.byte(syms['wifilink_timeout'],7); m.u.mem_write(syms['wifi_name'],b'Home\0')

def stream_queue(kind,status):
    def setup(m):
        m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(O['MCL_TYPE'],kind); m.word(0xa50084,status)
    return setup
def setb(name,value): return lambda m: m.byte(syms[name],value)

class SmartBusy(SmartMachine):
    def __init__(self,name,busy):
        super().__init__(); self.busy=busy; self.handlers[payload_syms[name]]='b:'+name  # every address is hooked here
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'').startswith('b:'): self.returns(self.busy); return
        return super().hook(u,address,size,unused)

class SmartScrobble(SmartMixin,PowerMachine):
    def __init__(self,on=True):
        super().__init__(); self.mock('system@GLIBC_2.0'); self.handlers[payload_syms['scrobble_start']]='s:start'
        self.handlers[payload_syms['scrobble_flag']]='s:flag'; self.on=on; self.starts=0; self.flags=0
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='s:start': self.starts+=1; self.returns(1); return
        if name=='s:flag': self.flags+=1; self.returns(int(self.on)); return
        return super().hook(u,address,size,unused)

class SmartRadio(SmartMixin,RadioMachine): pass

def held_machine(page,smart='1',mock=True):
    """Smart Wi-Fi's radio off, and page on top with stock's rows: a list_item per index holding a button
    of the same name, both named at +0x10 as stock's widget_set_name leaves them."""
    m=SmartRadio(smart=smart); m.poll(1); m.poll(SW_IDLE)
    items=[]
    for n in '012':
        b=m.node('button',n); item=m.node('list_item',n,[b]); items.append(item)
        for w in (item,b): m.word(w+0x10,m.string(n))
    win=m.node('window',page,[m.node('scroll_view','scroll_view_streamsset' if page=='stream_page' else 'scroll_view',items)])
    m.nodes[m.wm]['children']=[m.node('window','home_page'),win]
    for t in ('stream_row','netservice_row','online_match')*mock:
        a=int(manifest['patch_symbols'][f'stock_{t}_trampoline'],16); m.handlers[a]='stock_'+t
        m.u.hook_add(UC_HOOK_CODE,m.hook,begin=a,end=a)
    return m,win
def held_row(m,win,row): return m.nodes[m.nodes[win]['children'][0]]['children'][int(row)]  # the list_item
def held_press(m,hook,win,row):
    """Press as stock does: a row's button (its click's ctx) or More's window. The held action is later
    given the row's list_item (found by name) or the window."""
    arg=win if hook=='playermore_online' else m.nodes[held_row(m,win,row)]['children'][0]
    assert m.call(address=HOOKS[hook][0],args=(arg,m.event,0,0),gap=0)==0
    return held_row(m,win,row) if row else arg
def held_runs(m,t): return [c[1] for c in m.calls if c[0]=='stock_'+t]

class SmartShelf(SmartMixin,ShelfMachine): pass

class SmartAlbumLyrics(SmartMixin,AlbumLyricsMachine): pass

class SmartBrowse(SmartMixin,BrowseMachine): pass

LOGO_SRC,LOGO_DST,LOGO_ROT='/mnt/mmc/Q2Pod/boot/boot-logo.jpg','/mnt/data/boot-logo.jpg','/tmp/boot-logo.jpg'

def jpg(w,h,sof=0xc0,bits=8,n=3,pad=0):  # an APP0 behind a fill byte, then the frame header
    return b'\xff\xd8\xff\xff\xe0\x00\x04ab'+bytes([0xff,sof])+struct.pack('>HBHHB',8+3*n,bits,h,w,n)+bytes(3*n)+b'\xff\xd9'+bytes(pad)
class LogoMachine(PowerMachine):
    failure=''
    def __init__(self,files=None):
        super().__init__(files=files); self.threads=[]; self.execs=[]; self.toasts=[]; self.rotated=None; self.child=False; self.exits=[]
        self.handlers[payload_syms['child_exit']]='l:exit'
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='l:free'  # free's GOT slot, as Coverflow's
        for n in ('fseek@GLIBC_2.0','ftell@GLIBC_2.0','memcmp@GLIBC_2.0','strstr@GLIBC_2.0','pthread_create@GLIBC_2.2',
                  'fork@GLIBC_2.0','execl@GLIBC_2.0','exit@GLIBC_2.0','waitpid@GLIBC_2.0','open@GLIBC_2.0','navigator_to_with_context'): self.handlers[syms[n]]='l:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name.startswith('r:'+self.failure+'@') and (self.failure!='fopen' or self.text(u.reg_read(REGS[1]))=='wb'):
            self.returns(0 if self.failure in ('fopen','fwrite') else 0xffffffff); return
        if not name.startswith('l:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; self.calls.append((name,a,b,c)); ret=0
        if name=='fseek': f=self.open[a]; f[2]=signed(b)+(len(f[3]) if c==2 else 0)
        elif name=='ftell': ret=self.open[a][2]
        elif name=='memcmp': ret=int(bytes(u.mem_read(a,c))!=bytes(u.mem_read(b,c)))
        elif name=='strstr': i=self.text(a).find(self.text(b)); ret=a+i if i>=0 else 0
        elif name=='pthread_create': self.word(a,77); self.threads.append(c)
        elif name=='open': ret=0xffffffff  # the child's oom_score_adj write: refused here
        elif name=='fork': ret=0 if self.child else 4242
        elif name=='exit':  # the child ends: back to call()'s return, as if the thread had
            self.exits.append(a)
            for i,r in enumerate(SAVED): u.reg_write(r,0x12340000+i)
            u.reg_write(UC_MIPS_REG_SP,0x7000f000); u.reg_write(UC_MIPS_REG_PC,0x1000000); return
        elif name=='execl':
            sp=u.reg_read(UC_MIPS_REG_SP); args=[self.text(x) for x in (a,b,c,d)]
            while (x:=self.get(sp+16+4*(len(args)-4))): args.append(self.text(x))
            self.execs.append(args)
        elif name=='waitpid':
            assert a==4242; self.word(b,0); ret=a
            if self.rotated is not None: self.files[LOGO_ROT]=self.rotated
        elif name=='navigator_to_with_context':
            self.toasts.append(self.text(b+8)); assert len(self.toasts)<4,'a toast that keeps toasting'
            self.nested(sleep_hook,0x1234)  # its dialog's loop
        self.returns(ret,clobber=True)
    def sync(self,mounted=True):  # a poll with the card in or out; returns the threads it started
        self.files['/proc/mounts']=b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n' if mounted else b'none /mnt/data ubifs rw 0 0\n'
        self.threads=[]; self.pass_()
        for t in self.threads: self.call(address=t,args=(0,0,0,0),gap=0,clear=False)
        started=len(self.threads); self.pass_(1)  # the next pass, too soon to poll, toasts a failure
        return started

class BootMachine(ResumeMachine):
    failure=''
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='boot_memcmp':
            a,b,c=[u.reg_read(r) for r in REGS[:3]]
            self.returns(int(bytes(u.mem_read(a,c))!=bytes(u.mem_read(b,c)))); return
        if name=='unlink@GLIBC_2.0': self.files.pop(self.text(u.reg_read(UC_MIPS_REG_A0)),None)
        if name in ('r:fopen@GLIBC_2.2','r:fwrite@GLIBC_2.0','r:fclose@GLIBC_2.2','r:rename@GLIBC_2.0','fflush@GLIBC_2.0','fsync@GLIBC_2.0'):
            a=u.reg_read(UC_MIPS_REG_A0)
            fail=name.removeprefix('r:').split('@')[0]
            if self.failure==fail and (fail!='fopen' or self.text(u.reg_read(UC_MIPS_REG_A1))=='wb'):
                self.returns(0 if fail in ('fopen','fwrite') else 0xffffffff); return
        return super().hook(u,address,size,unused)
def boot_row(value=None,mode=0o100755):
    path='/mnt/data/boot-target'
    m=BootMachine({} if value is None else {path:value}); m.rockbox_mode=mode; m.restack=True
    m.handlers[syms['memcmp@GLIBC_2.0']]='boot_memcmp'
    m.handlers[int(manifest['patch_symbols']['stock_power_trampoline'],16)]='stock_power'
    m.mock('ferror@GLIBC_2.0','fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0','navigator_to_with_context')
    entries=[m.entry(0) for _ in range(2)]
    view=m.node('scroll_view','scroll_view_powermanager',entries.copy())
    m.top=m.node('window','powermanager_page',[m.node('list_view','list_view_powermanager',[view])])
    assert m.call(address=HOOKS['systemset_powermanager_page_init'][0],args=(m.top,5,0,0),gap=0)==0
    assert m.calls[0][:3]==('stock_power',m.top,5)
    kids=m.nodes[view]['children']
    if mode!=0o100755:  # no Boot to
        assert len(kids)==8  # stock's two, then Standby Limit, Charge Limit, USB DAC Charging, Smart Wi-Fi, In-Vehicle and Battery History
        return m,None,None
    assert len(kids)==9
    button=m.nodes[kids[8]]['children'][0]
    label=m.nodes[button]['children'][1]
    return m,button,label
def toggle_boot(m,button):
    f,ctx=m.handler(button,O['EVT_CLICK']); m.calls=[]
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0

class HeadsetMachine(Machine):
    def __init__(self,owner=1):
        super().__init__(); self.owner=owner; self.playing=True; self.session=42; self.actions=[]; self.saved=0
        self.handlers[syms['mclGetSavePlayStatus']]='hs:saved'
        for name in ('spot_status','radio_status','spot_session','radio_session',
                     'spot_set_playing','radio_set_playing','spot_media','radio_media'):
            self.handlers[payload_syms[name]]='hs:'+name
        self.handlers[syms['mclGetPlayStatus']]='hs:local_status'
        self.handlers[syms['player_play_pause']]='hs:local_toggle'
        self.handlers[syms['player_next_music']]='hs:local_next'
        self.handlers[syms['player_prev_music']]='hs:local_prev'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('hs:'): return super().hook(u,address,size,unused)
        name=name[3:]; a=u.reg_read(REGS[0]); ret=0
        owner=3 if name.startswith('radio') else 2 if name.startswith('spot') else 1
        if name=='saved': ret=self.saved
        elif name.endswith('_status'): ret=(2 if self.playing else 3) if self.owner==owner else 0
        elif name.endswith('_session'): ret=self.session
        elif name.endswith('_set_playing'): self.playing=bool(a); self.actions.append((owner,'play' if a else 'pause'))
        elif name=='local_toggle': self.playing=not self.playing; self.actions.append((1,'play' if self.playing else 'pause'))
        else: self.actions.append((owner,a if owner!=1 else name)); ret=int(name.endswith('_media') and self.owner==owner)
        self.returns(ret)
    def fn(self,name,value=0,gap=0): return self.call(address=payload_syms[name],args=(value,0,0,0),gap=gap)
    def ears(self,mask,gap=100): return self.fn('headset_ears',4|mask,gap)
    def command(self,code,gap=200): return self.fn('headset_command',code,gap)

def ready_gain(m,ready,volume):
    m.way=1; m.config['BTVOLLINK']='1'; m.word(payload_syms['bt_vol_link'],1)  # Control Device Volume on, read or not
    for name in ('g_bluetoothflag','bt_linkstatus'): m.byte(syms[name],1)
    m.word(syms['bt_showcoding'],1); m.byte(syms['g_volume'],volume)
    m.word(payload_syms['btvol']+4 if 'btvol' in payload_syms else payload_syms['btvol.1'],ready)  # clang may split scalar fields

def conf_open(m):
    m.top=m.node('window','home_page'); m.stream(); view=m.nodes[m.top]['children'][0]
    m.click(m.nodes[m.nodes[view]['children'][5]]['children'][0]); m.page=m.top

GATE=syms['check_forbid_play_no_headset']
def hp_on(m,**state):
    """m with stock's gate run, not answered, and the switch on; state's bytes are an output, test mode or the switch off."""
    del m.handlers[GATE]; m.byte(syms['g_block_withoutphone_flag'],1)
    for name,v in state.items(): (m.word if name=='bt_showcoding' else m.byte)(syms[name],v)
    return m
def forbids(patched,ask=0,**state):
    c=hp_on(Machine(patched),**state); c.mock('notifyPrompt'); ret=c.call(address=GATE,args=(ask,0,0,0),gap=0)
    return ret,[(c.text(x[1]),c.get(x[2]),c.get(x[2]+4)) if x[0]=='navigator_to_with_context' else x[0] for x in c.calls if x[0]!='memset']

def hp_keys(key,playing=False,**state):
    m=hp_on(HeadsetMachine(),**state); toggle=syms['player_play_pause']; del m.handlers[toggle]; m.handlers[toggle+12]='hs:local_toggle'
    m.playing=playing; m.fn('media_key',O[key]); return m.actions,sum(x[0]=='navigator_to_with_context' for x in m.calls)

def np_opens(start=1,**state):
    m=hp_on(QueueMachine(queue=3,pos=1),**state); win=m.node('window','playing_page'); m.word(win+O['W_PARENT'],m.wm)
    m.stack.append(win); m.top=win; ctx=m.alloc(16)
    for i,v in enumerate((m.get(syms['mcl_pdeqplaylist']),0,start,0)): m.word(ctx+4*i,v)
    ret=m.call(address=HOOKS['playing_page_init'][0],args=(win,ctx,0,0),gap=0)
    return ret,[x[0] for x in m.calls if x[0] in ('player_start','window_close')],any(x[0]=='widget_lookup' for x in m.calls),len(m.toasts)

class InfoMachine(Machine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='i:format':
            self.u.mem_write(u.reg_read(UC_MIPS_REG_A1),{'player_location':getattr(self,'location',b'Location\0')}.get(self.text(u.reg_read(UC_MIPS_REG_A0)),b'?\0'))
            self.returns(0); return
        return super().hook(u,address,size,unused)

class GenreMachine(InfoMachine,QueueMachine): pass
np_page=HOOKS['playing_page_init'][0]
def genre_page(genre,text_w):
    m=GenreMachine(queue=3,pos=1); m.handlers[syms['getFormatString']]='i:format'; m.handlers[np_page+12]='stock_playing'
    source=m.node('label','label_genre',text='Genre:'+genre); m.word(source+O['W_X'],75); m.word(source+O['W_W'],225)
    page=m.node('view','view_info',[source]); sv=m.node('slide_view','slide_view',value=2)
    win=m.node('window','playing_page',[sv,page,m.node('slider','slider_play',max=225,value=100)])
    for w,parent in ((source,page),(page,win),(sv,win),(win,m.wm)): m.word(w+O['W_PARENT'],parent)
    m.top=win; m.call(address=np_page,args=(win,7,0,0),gap=0); m.timers.clear()
    def fill(): m.call(address=payload_syms['ringnav_np_info'],args=(win,0,0,0),gap=0)
    fill()  # stock measures on the first paint: nothing to scroll yet
    s=next(w for w,n in m.nodes.items() if n.get('name')=='ringnav_genre_scroll')
    assert not m.timers and m.get(s+O['HSCROLL_TEXT_W'])==0
    m.word(s+O['HSCROLL_TEXT_W'],text_w)
    return m,win,sv,source,s,fill

# Chapter extraction is tested with real FFmpeg in chapters.py; here only that boundary is mocked.
class ChapterMachine(CoverflowMachine):
    def __init__(self, count=3, failed=False, ready=True, path='/mnt/mmc/Book ; $x.M4B'):
        super().__init__(page='playing_page', pos=1)
        self.ready=ready; self.cancelled=0; self.playtime=70; self.fits=1; self.path=path
        self.job=self.alloc(32+512*260)
        for off,v in ((12,0),(16,int(failed)),(20,count)): self.word(self.job+off,v)
        for i,(sec,title) in enumerate(((0,'Opening'),(60,''),(120,'日本語'))):
            self.word(self.job+32+i*260,sec)
            self.u.mem_write(self.job+36+i*260,title.encode()+b'\0')
        for name in ('chapters_start','chapters_poll','chapters_cancel','mp4_fits'):
            self.handlers[payload_syms[name]]='chapter:'+name
        self.mock('navigator_back')
        self.handlers.pop(syms['mclGetPlayStatus'],None); self.word(0xa50084,2)
        self.track=self.items(self.get(syms['mcl_pdeqplaylist']))[1]
        self.word(self.track+O['REC_PATH'],self.string(path))
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('chapter:'): return super().hook(u,address,size,unused)
        a=u.reg_read(REGS[0]); ret=0
        if name=='chapter:chapters_start':
            assert self.text(a)==self.path; ret=self.job
        elif name=='chapter:mp4_fits':
            assert self.text(a)==self.path; ret=self.fits
        elif name=='chapter:chapters_poll':
            ret=int(self.ready)
            if ret: self.word(self.job+12,1)
        else: self.cancelled+=1; self.ready=True
        self.returns(ret)
    def open_chapters(self):
        self.press(6000); assert self.hold()==11
        assert 'Chapters' in self.labels(); self.release()
        self.pick(self.labels().index('Chapters')); self.advance(0)
        self.page=self.top
        assert self.nodes[self.page]['name']=='chapters_page'
    def rows(self): return self.nodes[self.find('scroll_view')]['children']
    def choose(self,i):
        f,ctx=self.handler(self.rows()[i],O['EVT_CLICK'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0)

def shows_empty(m,bar,message,name): assert message in [n.get('text') for n in m.nodes.values()] and bar_reads(m,bar)==name,(message,m.nodes[bar]['text'])
def empty_row(m,row):  # Local Music's row, opened
    view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
    assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
    f,ctx=m.handler(m.nodes[m.nodes[view]['children'][row]]['children'][0],O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.page=m.top

class EqOutputMachine(BtLinkMachine):
    def __init__(self,links=(),way=0,headset=0,po=0,bal=0,on=1):
        super().__init__(links); self.way=way; self.on=on; self.keys=[]
        self.handlers.update({syms['mclGetOutputWay']:'eo:way',payload_syms['pod_value']:'eo:on',payload_syms['eq_output']:'eo:key'})
        self.byte(syms['g_headset_output'],headset); self.byte(syms['g_po_status'],po); self.byte(syms['g_bal_status'],bal)
    def hook(self,u,addr,size,_):
        name=self.handlers.get(addr,'')
        if not name.startswith('eo:'): return super().hook(u,addr,size,_)
        if name=='eo:key': self.keys.append(self.text(u.reg_read(REGS[0])))
        if name=='eo:on': assert u.reg_read(REGS[0])==POD['POD_EQ_OUTPUT']
        self.returns({'eo:way':self.way,'eo:on':self.on}.get(name,0))
    def poll(self): self.call(address=payload_syms['headset_eq_poll'],args=(0,0,0,0),gap=1000); return self.keys

class EqRowMachine(QueueMachine):  # a payload call has no t9 for Machine's handler check
    forgets=0
    def hook(self,u,addr,size,_):
        if self.handlers.get(addr)!='eq_output_forget': return super().hook(u,addr,size,_)
        self.forgets+=1; self.returns(0)

def sb_machine(on,content,value,light='1',y=60,table=False,h=96):
    m=Machine()
    if on: m.config['LIGHT']=light; classic_light(m)
    if table: w,_,_=m.table_page(); m.word(w+O['TABLE_ROWS'],content//48)
    else: w,_=m.page_list(20,height=h,extent=content)
    bar=m.node('scroll_bar_m'); m.word(bar+O['W_H'],240); m.word(bar+O['BAR_MAX'],max(content,240)); m.word(bar+O['BAR_VALUE'],value)
    parent=m.node('list_view' if not table else 'table_view',children=[w,bar]); m.word(parent+O['W_W'],240); m.word(parent+O['W_H'],h)
    m.word(w+O['W_PARENT'],parent); m.word(bar+O['W_PARENT'],parent); m.nodes[m.top]['children']=[parent]
    m.word(m.canvas+O['CANVAS_Y'],y); return m,parent,bar
def sb_stock(m,bar,name='fg_color'):  # stock's grey asked for while its bar paints
    begin(m,bar); m.handlers[int(manifest['patch_symbols']['stock_color_trampoline'],16)]='stock_color'; out=m.alloc(4); m.word(out,0xff808080)
    m.call(address=HOOKS['style_get_color'][0],args=(out,0x1234,m.string(name),0),gap=0); return m.get(out)
def sb_drawn(m,parent): m.bands=[]; m.paint(parent); return [b[:5] for b in m.bands if b[0]>=229]
def sb_thumb(y,n,rgb):
    c,a,b=(color_t(rgb)&0xffffff|k<<24 for k in (255,81,234)); x=234
    ends=lambda y:([(x,y,1,1,a),(x+3,y,1,1,a),(x+1,y,1,1,b),(x+2,y,1,1,b)],[(x,y,1,1,b),(x+3,y,1,1,b),(x+1,y,2,1,c)])
    top,bottom=ends(y),ends(y+n-1)
    return top[0]+[(b_[0],b_[1]+1,*b_[2:]) for b_ in top[1]]+[(x,y+2,4,n-4,c)]*(n>4)+[(b_[0],b_[1]-1,*b_[2:]) for b_ in bottom[1]]+bottom[0]
