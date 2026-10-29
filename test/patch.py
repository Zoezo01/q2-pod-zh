#!/usr/bin/env python3
"""Execute the actual patched MIPS callback with stock filter and mocked UI services.
Requires unicorn==2.1.4. Does not emulate the entire device or flash hardware.
"""
import json, math, pathlib, re, sqlite3, struct, sys
from functools import cmp_to_key
from unicorn import Uc, UcError, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE, UC_HOOK_BLOCK, UC_HOOK_INTR
from unicorn.mips_const import *
sys.path.insert(0, sys.path[0] + '/../tools')  # tools/ first
from build import jal, j, segments, load_elf, symbols, BASE, SCRATCH, HOOKS, IPOD_HOOKS, WM_PAINT_LEAF, FUNCTIONS, GLOBALS, CONTEXT_DATA, ROOT, source_sha256, sha, PRIVATE_FUNCTIONS, VERSIONS, VERSION, dev_tag
B=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else 'build')
payload_syms=symbols(B/'patch.elf')
manifest=json.loads((B/'manifest.json').read_text())
if manifest.get('source_sha256') != source_sha256():
    raise SystemExit(f'{B}/manifest.json does not match the current patch sources; rebuild into a fresh directory and pass it here')
for name,key in (('demo','demo_sha256'),('stock-demo','stock_demo_sha256'),('patch.bin','patch_sha256')):
    if sha((B/name).read_bytes()) != manifest.get(key):
        raise SystemExit(f'{B/name} does not match manifest.json; rebuild into a fresh directory')
variant = manifest.get('variant')
CF_PLACEHOLDER = 'default_album_flow' if variant=='ipod' else 'default_album_big'
# iPod writes times as 0:02 and -1:02:05; Legacy keeps stock's 00:02 and -01:02:05.
def mmss(t): return t if variant=='ipod' else re.sub(r'^(-?)(\d):',r'\g<1>0\2:',t)
v = VERSIONS.get(variant, '')
assert v and manifest['version'] == (dev_tag(manifest['dev'], v[-1]) if manifest.get('dev') else v), 'Wrong variant/version'
assert (manifest.get('compact_code') != []) == (variant == 'ipod')
from ipod import INC, O  # patch/offsets.inc and its integer #defines
DC=O['DOUBLE_CLICK_MS']  # centre double-press window
# iPod accent presets: {gradient top, bottom, light tone, red tone, highlight} per Accent setting value.
ACCENTS=[tuple(int(v,16) for v in g) for g in re.findall(r'\{ 0x(\w+), 0x(\w+), 0x(\w+), 0x(\w+), 0x(\w+) \}',INC)]
# MINIMAL_ACCENT's row (Minimal only, Ivory) draws as Graphite.
assert ACCENTS[O['MINIMAL_ACCENT']]==ACCENTS[0]
CLASSIC=[p for p in range(len(ACCENTS)) if p!=O['MINIMAL_ACCENT']]  # the values Classic's Accent row takes
def color_t(rgb): return 0xff000000|(rgb&255)<<16|(rgb>>8&255)<<8|rgb>>16
O_GLYPH=re.search(r'#define BT_GLYPH "(\w+)"',INC)[1]  # the plain Bluetooth glyph a codec badge fades into
CONFIG={}  # config.ini [IPOD] keys a new Machine starts with; the payload reads them on first use
FILL,SHADE,OUTLINE=((O[a]<<24)|O[c] for a,c in (('FILL_ALPHA','FILL_RGB'),('SHADE_ALPHA','FILL_RGB'),('OUTLINE_ALPHA','OUTLINE_RGB')))
LCD_COLORS=(0x9abcdef0,0x12345678)
syms=symbols(B/'stock-demo')
syms.update(PRIVATE_FUNCTIONS)
VG_MOCKS=[n for n in syms if n.startswith(('vgcanvas_','vg_gradient_'))]

def _fp64_fix():
    """The stock binary is -mfp64 (FR=1); Unicorn's MIPS32 FPU only implements FR=0, so
    L-format conversions are rewritten to their W equivalents when an image is loaded. Every
    value the traversed canvas code converts fits 32 bits, so the results are identical."""
    data=(B/'demo').read_bytes(); fixes=[]
    for _,(t,o,v,_,f,m,flags,_) in segments(data):
        if t!=1 or not flags&1: continue
        for off in range(o,o+f,4):
            w=struct.unpack_from('<I',data,off)[0]
            if w>>26&0x3f==0x11 and w>>21&0x1f==0x15:
                fixes.append((v+off-o,(w&~(0x1f<<21))|(0x14<<21)))
    return fixes
FP64_FIX=_fp64_fix()
REGS=[UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3]
SAVED=[UC_MIPS_REG_S0,UC_MIPS_REG_S1,UC_MIPS_REG_S2,UC_MIPS_REG_S3,
       UC_MIPS_REG_S4,UC_MIPS_REG_S5,UC_MIPS_REG_S6,UC_MIPS_REG_S7,UC_MIPS_REG_FP]

def signed(x): return x if x<0x80000000 else x-0x100000000

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
        self.next=0x1001000; self.nodes={}; self.calls=[]; self.animating=0; self.pressed=0
        self.top=0; self.wm=0x1000000; self.event=0x1000100
        self.strokes=[]; self.rounded=[]; self.bands=[]; self.icons=[]; self.letters=[]; self.font=None; self.vg_calls=[]; self.fake_vg=0; self.global_alpha=0
        self.rounded_fail=False; self.count=self.budget  # call()'s instruction limit, which nested() shares
        self.allocs={}; self.config=dict(CONFIG); self.config_reads=[]; self.card=(); self.dirs=set()  # /mnt/mmc's (name, d_type) entries; deeper folders
        self.rebind=None; self.on_click=None; self.glide=True
        self.timers={}; self.next_timer=1; self.timer_fail=False; self.clicks=[]; self.started=[]
        self.screens=[]
        self.props={}; self.slides={}; self.slide_fail=False; self.slide_on_fail=False; self.slide_callbacks={}
        self.canvas=0x1000200; self.lcd=0x1000300; self.now=1000
        self.word(self.canvas+O['CANVAS_LCD'],self.lcd)
        for off,v in zip(('LCD_FILL_COLOR','LCD_STROKE_COLOR'),LCD_COLORS): self.word(self.lcd+O[off],v)
        self.clip=(0,0,240,240)
        self.handlers={}
        for name in FUNCTIONS: self.handlers[syms[name]]=name
        # canvas_get_vgcanvas runs stock, down to the mocked lcd_get_vgcanvas, as the rounded wrappers need
        for name in ('slide_menu_item_width','slide_menu_on_scroll_done','canvas_get_vgcanvas',
                     'widget_animator_scroll_set_params','slide_menu_set_value','toolsTimeItoa','on_wm_keyup_fun'):
            self.handlers.pop(syms[name],None)
        self.mock('widget_is_instance_of','widget_animator_scroll_create','widget_animator_on',
                  'widget_set_focused','widget_layout_children','event_init','value_set_int')
        if patched:
            for name in ('paint','dispatch','paint_bg'):
                self.handlers[int(manifest['patch_symbols']['stock_'+name+'_trampoline'],16)]='stock_'+name
        self.mock('pthread_create@GLIBC_2.2','pthread_detach@GLIBC_2.0')
        self.mock('reset_poweroptions_timer','screen_action','enable_fb','usleep@GLIBC_2.0','sprintf@GLIBC_2.0',
                  'airplayGetFlag','playpause_quick_click','time@GLIBC_2.0','localtime@GLIBC_2.0','tzset@GLIBC_2.0',
                  'strlen@GLIBC_2.0','strncpy@GLIBC_2.0','strrchr@GLIBC_2.0','strcasecmp@GLIBC_2.0','strncasecmp@GLIBC_2.0',
                  'strcasestr@GLIBC_2.2',
                  'unlink@GLIBC_2.0','atoi@GLIBC_2.0','memcmp@GLIBC_2.0','access@GLIBC_2.0',
                  'fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0',  # blob_io's sync before rename
                  'fopen@GLIBC_2.2')  # no /proc/mounts: the boot logo's poll sees no card
        self.present=set()  # paths access finds (the UI loop's Spotify poll asks for its login)
        self.stats={}  # paths stat finds, with their mtime (songs on the card: a card app's)
        self.image_size=(50,50)  # what widget_load_image decodes
        self.clock=(18,14)  # local (hour, minute) for time/localtime, or the one of them that fails
        self.clock_minutes=False
        self.wall=(86400,86400)  # time's result, and the seconds localtime must be given
        self.rockbox_mode=0o100755
        self.handlers[syms['__xstat@GLIBC_2.0']]='rockbox_stat'
        self.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
        self.handlers[syms['memcpy@GLIBC_2.0']]='memcpy'
        self.handlers[syms['memset@GLIBC_2.0']]='memset'
        self.mock('canvas_set_global_alpha')
        self.mock('sqrtf@GLIBC_2.0','sinf@GLIBC_2.0','acosf@GLIBC_2.0',prefix='float:')
        self.mock(*VG_MOCKS,prefix='vg:')
        self.handlers[payload_syms['peq_lib_localtime_r']]='localtime_r'
        self.code_hook=self.u.hook_add(UC_HOOK_CODE,self.hook)
        for name in GLOBALS: self.byte(syms[name],0)
        for name,size in CONTEXT_DATA.items(): self.u.mem_write(syms[name],bytes(size))
        self.word(syms['g_class_type'],0xf001)
        self.byte(syms['g_backlight_status'],1)
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
    def mock(self,*names,prefix=''):
        for n in names: self.handlers[syms[n]]=prefix+n
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
        while a and (c:=self.u.mem_read(a,1))!=b'\0': out+=c; a+=1
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
        if variant=='ipod':
            self.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(w,self.canvas,0,0),gap=gap); gap=0
        return self.call(address=HOOKS['widget_on_paint_border'][0],args=(w,self.canvas,0,0),gap=gap,clear=variant!='ipod')
    def drawn(self): return self.rounded or self.strokes or self.bands
    def sel(self):
        """The selected row's rectangle, from the outline (inset 1) or the bar's bands."""
        if variant=='ipod':
            if self.rounded and self.rounded[0]['kind']=='fill' and self.rounded[0]['color']==color_t(0xeeeeec):
                x,y,w,h=self.rounded[0]['rect']
                return (x-O['SEL_INSET'],y,w+2*O['SEL_INSET'],h) if x else (x,y,w,h)  # Minimal's card; a square list row's spans the surface
            ys=[b[1] for b in self.bands]; x=min(b[0] for b in self.bands); w=max(b[2] for b in self.bands)
            if x==O['SEL_INSET']: x,w=0,w+2*O['SEL_INSET']  # Classic's list pill, inset as Minimal's card
            return (x,min(ys),w,max(ys)-min(ys)+1)
        x,y,w,h=self.rounded[0]['rect'] if self.rounded else self.strokes[0][:4]
        return (x-1,y-1,w+2,h+2)
    def touch(self): return self.call(address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'])
    def click(self,w,gap=1000): return self.call(address=HOOKS['widget_dispatch'][0],args=(w,self.event,0,0),event_type=O['EVT_CLICK'],gap=gap)
    def hook(self,u,address,size,_):
        if address==syms['widget_animator_scroll_set_params']:
            assert u.reg_read(UC_MIPS_REG_A0) not in self.slides, 'retarget must pause first'
            self.calls.append(('widget_animator_scroll_set_params',*[u.reg_read(r) for r in REGS[:3]]))
        if address not in self.handlers: return
        name=self.handlers[address]
        if not name.startswith('stock_') and address!=payload_syms['peq_lib_localtime_r']:
            assert u.reg_read(UC_MIPS_REG_T9)==address, (name,'PIC call missing t9')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        n=self.nodes.get(a,{})
        self.calls.append((name,a,b,c))
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
            while a:
                x-=signed(self.get(a+O['W_X'])); y-=signed(self.get(a+O['W_Y']))
                a=self.get(a+O['W_PARENT'])
            self.word(b,x); self.word(b+4,y); ret=0
        elif name in ('widget_set_text_utf8','widget_set_tr_text'): n['text']=self.text(b); ret=0  # no language has our keys
        elif name=='image_base_set_image': n['image']=self.text(b); ret=0
        elif name=='widget_use_style': n['style']=self.text(b); ret=0
        elif name.startswith('hscroll_label_set_') or name=='set_hscroll_label_attribute':
            n[name]=b if name!='set_hscroll_label_attribute' else True; ret=0
        elif name=='image_set_draw_type': n['draw_type']=b; ret=a
        elif name=='deque_at': ret=self.row_record
        elif name=='deque_size': ret=1
        elif name in ('tk_snprintf','snprintf@GLIBC_2.0'):
            fmt=self.text(c); values=iter([d]+[self.get(u.reg_read(UC_MIPS_REG_SP)+off) for off in range(16,80,4)])
            params=[]  # %.*s takes a length, then the string (its bytes)
            for star,kind in re.findall(r'%(\.\*)?\d*([sdxXu])',fmt):
                if star: n=next(values); params.append(bytes(self.u.mem_read(next(values),n)).split(b'\0')[0].decode())
                else: value=next(values); params.append(self.text(value) if kind=='s' else value if kind in 'xXu' else signed(value))
            result=(re.sub(r'%\.\*s','%s',fmt) % tuple(params)).encode(); self.u.mem_write(a,result[:b-1]+b'\0'); ret=len(result)
        elif name=='atoi@GLIBC_2.0': ret=int(re.match(r'[+-]?\d+',self.text(a))[0]) if re.match(r'[+-]?\d+',self.text(a)) else 0
        elif name=='access@GLIBC_2.0': ret=0 if self.text(a) in self.present else -1
        elif name=='rockbox_stat':
            assert a==3
            if self.text(b) in ['/mnt/mmc/'+n for n,t in self.card if t==4] or self.text(b) in self.dirs: self.word(c+20,0o40755); ret=0  # a card folder
            elif self.text(b) in self.stats: self.word(c+64,self.stats[self.text(b)]); ret=0  # its mtime: info[16]
            elif self.text(b)!='/mnt/mmc/.rockbox/rockbox': ret=-1  # Tidal Downloads' rows: no such files here
            else: self.word(c+20,self.rockbox_mode); ret=0 if self.rockbox_mode else -1
        elif name=='strlen@GLIBC_2.0': ret=len(self.raw(a))  # bytes: stock strips a path's UTF-8 name byte by byte
        elif name=='strrchr@GLIBC_2.0': i=self.text(a).encode().rfind(bytes([b&255])); ret=a+i if i>=0 else 0
        elif name=='memcmp@GLIBC_2.0': x,y=bytes(u.mem_read(a,c)),bytes(u.mem_read(b,c)); ret=(x>y)-(x<y)
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
            if hasattr(self,'epoch'): # ResumeMachine's time(): navigation.c's clock_back of it
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
            value=self.config.get(key,self.config_reads[-1][3])  # stock copies the default when the key is missing
            self.u.mem_write(d,value.encode()+b'\0'); ret=1 if key in self.config else -1
        elif name=='tk_str_end_with': ret=self.text(a).endswith(self.text(b))
        elif name=='tk_str_start_with': ret=self.text(a).startswith(self.text(b))
        elif name=='strtol@GLIBC_2.0': t=re.match(r'\s*[-+]?\d+',self.text(a)); ret=int(t[0]) if t else 0
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
            ret=lookup(a) if a else 0
        elif name=='scroll_bar_cast': ret=a
        elif name=='scroll_bar_is_mobile': ret=n.get('type')=='scroll_bar_m'
        elif name=='widget_set_opacity': self.byte(a+0x34,b); ret=0
        elif name in ('widget_set_visible','widget_set_visible_only','widget_set_sensitive'):
            n['sensitive' if name=='widget_set_sensitive' else 'visible']=b; ret=0
        elif name=='widget_animator_prop_create':
            ret=self.alloc(0x80); self.word(ret+4,a); self.word(ret+O['ANIM_DURATION'],b)
            self.word(ret+0x24,c); n['animated']=self.text(self.get(u.reg_read(UC_MIPS_REG_SP)+16)); assert n['animated'] in ('opacity','x')
        elif name=='widget_animator_prop_set_params':
            assert self.get(u.reg_read(UC_MIPS_REG_SP)+16)==0 and self.get(u.reg_read(UC_MIPS_REG_SP)+20)==0
            if self.nodes[self.get(a+4)].get('animated')=='x':
                self.props[a]=(struct.unpack('<d',struct.pack('<II',c,d))[0],0)
            ret=0
        elif name=='window_manager': ret=self.wm
        elif name=='window_manager_get_top_window': ret=self.top
        elif name=='window_manager_is_animating': ret=self.animating
        elif name=='window_manager_get_pointer_pressed': ret=self.pressed
        elif name=='widget_get_visible': ret=n.get('visible',0)
        elif name=='widget_get_type': ret=self.string(n.get('type',''))
        elif name=='widget_get_prop_str':
            # Stock text is VALUE_TYPE_WSTRING; value_str does not convert it to UTF-8.
            ret=0 if self.text(b)=='text' else self.string(n.get(self.text(b),''))
        elif name=='widget_get_text': ret=self.wide_string(n.get('text',''))
        elif name=='player_playtime_and_length' and hasattr(self,'playtime'): self.word(a,self.playtime); self.word(b,225); ret=0
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
        elif name=='widget_count_children': ret=len(n['children'])
        elif name=='widget_get_child': ret=n['children'][b] if b<len(n['children']) else 0
        elif name=='widget_set_prop_int': n[self.text(b)]=signed(c); ret=0
        elif name=='widget_set_prop_str': n[self.text(b)]=self.text(c); ret=0
        elif name=='text_selector_count_options': ret=n.get('options',0)
        elif name=='text_selector_set_selected_index': self.word(a+O['SELECTOR_INDEX'],b); ret=0
        elif name=='widget_restack' and getattr(self,'restack',False):  # AWTK clamps the index
            kids=next((v['children'] for v in self.nodes.values() if a in v.get('children',())),None)
            if kids is not None: kids.remove(a); kids.insert(min(b,len(kids)),a)
            ret=0
        elif name=='pointer_event_init':
            self.word(a,b); self.word(a+0x10,c); ret=a
        elif name=='time_now_ms': ret=self.now & 0xffffffff
        elif name=='timer_add':
            ret=0 if self.timer_fail else self.next_timer
            if ret:
                self.next_timer+=1
                # the bar's clock (clock_due) runs only where a check asks for it (clock_minutes)
                if a!=payload_syms.get('clock_due') or self.clock_minutes: self.timers[ret]=(self.now+c,a,b,c)
        elif name=='timer_remove': self.timers.pop(a,None); ret=0
        elif name=='screen_action': self.screens.append(a); ret=1
        elif name=='player_start': self.started.append((a,b,c,d)); ret=1
        elif name in ('tk_strcmp','strcmp@GLIBC_2.0'): x,y=self.text(a),self.text(b); ret=0 if a and b and x==y else -1 if x<=y else 1
        elif name=='stock_dispatch':
            if self.get(b)==O['EVT_CLICK']:
                self.clicks.append(a)
                if self.on_click: self.on_click(a,b)
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
                if self.nodes.get(a,{}).get('type')=='slide_menu': assert c==0 and d==O['SLIDE_EASING']
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
        elif name=='scroll_view_scroll_to':  # stock's clamps the end to the content (0x5f0178)
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
        elif name=='widget_load_image':  # list_into is 50x50; image_size None fails the load
            ret=1 if self.image_size is None else 0
            if not ret: self.word(c,self.image_size[0]); self.word(c+4,self.image_size[1])
        elif name=='canvas_draw_icon': self.icons.append((signed(c),signed(d),self.clip)); ret=0
        elif name=='canvas_set_font': self.font=(self.text(b) if b else 'default',c); ret=0
        elif name=='canvas_set_text_color': self.word(self.lcd+O['LCD_TEXT_COLOR'],b); ret=0
        elif name=='canvas_draw_text_in_rect':
            self.letters.append(dict(text=''.join(chr(self.get(b+4*j)) for j in range(c)),
                rect=tuple(signed(self.get(d+4*j)) for j in range(4)),color=self.get(self.lcd+O['LCD_TEXT_COLOR']),font=self.font,
                align=(self.get(a+O['CANVAS_ALIGN_H']),self.get(a+O['CANVAS_ALIGN_V'])),clip=self.clip)); ret=0
        elif name=='canvas_measure_text': u.reg_write(UC_MIPS_REG_F0,struct.unpack('<I',struct.pack('<f',10.0*c))[0]); ret=0
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
            fn={'sqrtf@GLIBC_2.0':math.sqrt,'sinf@GLIBC_2.0':math.sin,'acosf@GLIBC_2.0':math.acos}[name[6:]]
            ret=struct.unpack('<I',struct.pack('<f',fn(x)))[0]
            u.reg_write(UC_MIPS_REG_F0,ret)
        elif name in ('alloc:tk_calloc','alloc:tk_free'):
            if name=='alloc:tk_calloc':
                size=a*b; p=self.alloc(size+16); self.allocs[p]=size; ret=p
            else:
                self.allocs.pop(a,None); ret=0
        else: ret=0
        self.returns(ret,clobber=True)
    def nested(self,f,*args):
        """Run guest f(*args) from inside a mock, on a stack below the caller's, and keep the caller's registers."""
        regs=[UC_MIPS_REG_PC,*range(UC_MIPS_REG_0,UC_MIPS_REG_31+1)]; saved=[self.u.reg_read(r) for r in regs]
        for r,v in ((UC_MIPS_REG_SP,self.u.reg_read(UC_MIPS_REG_SP)-0x400),(UC_MIPS_REG_RA,0x1000000),(UC_MIPS_REG_T9,f),*zip(REGS,args)):
            self.u.reg_write(r,v)
        # unicorn's count is one per engine: a nested run's limit stands for the rest of the caller's call too
        self.u.emu_start(f,0x1000000,count=self.count); ret=signed(self.u.reg_read(UC_MIPS_REG_V0))
        for r,v in zip(regs,saved): self.u.reg_write(r,v)
        return ret
    def qsort(self,base,n,size,compare):
        pair=self.alloc(2*size)
        def cmp(x,y): self.u.mem_write(pair,x); self.u.mem_write(pair+size,y); return self.nested(compare,pair,pair+size)
        for i,v in enumerate(sorted([bytes(self.u.mem_read(base+size*i,size)) for i in range(n)],key=cmp_to_key(cmp))): self.u.mem_write(base+size*i,v)
    def returns(self,ret,clobber=False):
        """Return ret from a mocked call; clobber caller-saved registers to catch accidental ABI assumptions."""
        if clobber:
            for r in [UC_MIPS_REG_V1,*REGS,UC_MIPS_REG_T0,UC_MIPS_REG_T1,UC_MIPS_REG_T2,
                      UC_MIPS_REG_T3,UC_MIPS_REG_T4,UC_MIPS_REG_T5,UC_MIPS_REG_T6,
                      UC_MIPS_REG_T7,UC_MIPS_REG_T8,UC_MIPS_REG_T9]:
                self.u.reg_write(r,0xdeadbeef)
        self.u.reg_write(UC_MIPS_REG_V0,ret&0xffffffff)
        self.u.reg_write(UC_MIPS_REG_PC,self.u.reg_read(UC_MIPS_REG_RA))
    def fast(self):
        """Hook stock code and the payload's trampolines only, so the payload's own loops (the
        Coverflow renderer) run at native emulator speed."""
        self.u.hook_del(self.code_hook)
        self.u.hook_add(UC_HOOK_CODE,self.hook,begin=0,end=BASE-1)
        self.u.hook_add(UC_HOOK_CODE,self.hook,begin=SCRATCH,end=0xffffffff)
        for a in list(self.handlers):
            if BASE<=a<SCRATCH: self.u.hook_add(UC_HOOK_CODE,self.hook,begin=a,end=a)
    budget=100000  # instructions per call
    def call(self,key=O['KEY_NEXT'],address=HOOKS['on_wm_keyup_before_fun'][0],args=None,event_type=0x114,gap=1000,stack=(),clear=True,debounce=False,count=None):
        # Independent input steps occur after the stock key debounce timer expires.
        if gap: self.advance(gap,clear=False)
        if not debounce: self.byte(0xa37c89,0)  # stock key filter latch
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
            self.word(info+0x20,ctx); self.word(info+0x28,tid)
            ret=self.call(address=callback,args=(info,0,0,0),gap=0,clear=False)
            assert ret in (0,8) and (ret==0 or period>0)
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
        self.top=self.node('window',name,[child])
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
def passed():
    global checks
    checks+=1

# The failed-exec path exits through the kernel, without libc's copied buffers or handlers.
m=Machine(); exited=[]
def child_syscall(u,number,unused):
    exited.append((u.reg_read(UC_MIPS_REG_V0),u.reg_read(UC_MIPS_REG_A0)))
    u.reg_write(UC_MIPS_REG_PC,0x1000000)
m.u.hook_add(UC_HOOK_INTR,child_syscall)
m.call(address=payload_syms['child_exit'],args=(127,0,0,0),gap=0)
assert exited==[(4001,127)];passed()

# Speed's 42x61 image hit targets highlight only the visible 42x24 buttons (#135).
m=Machine(); w=m.page('playermore_page'); row=m.node('button',style='s_btn_forwardspeed')
m.word(row+O['W_PARENT'],w); m.nodes[w]['children']=[row]
buttons=[]
for x in (120,177,231,283):
    e=m.entry(row,9); m.nodes[e]['type']='image'
    m.word(e+O['W_X'],x); m.word(e+O['W_W'],42); m.word(e+O['W_H'],61); buttons.append(e)
m.nodes[row]['children']=buttons; m.word(w+O['W_W'],375)
m.paint(w)
for i in range(4):
    if i: assert m.call()==11
    m.paint(w)
    inset=2 if variant=='ipod' else 1
    assert m.selected(w)==i and m.rounded[0]['rect']==((120,177,231,283)[i]+inset,9+inset,42-2*inset,24-2*inset)
assert m.confirm()==11 and m.dispatched()[0][1]==buttons[-1]; passed()

# Execute the stock offset setter after cancelling an animator headed to either boundary.
# Cover selected rows and the pixel-scroll fallback.
for populated in (False,True):
    for endpoint,key,selected,want in ((0,O['KEY_NEXT'],4,204),
                                      (864,O['KEY_PREV'],5,180)):
        m=Machine(); w,es=m.page_list(20 if populated else 0)
        m.word(w+0x74,syms['g_scroll_view_vtable'])
        m.paint(w)
        m.nodes[w]['_ringnav_index']=selected if populated else -1
        top=120 if endpoint==0 else 240
        m.word(w+O['SCROLL_Y'],top)
        animator=m.alloc(0x80); m.word(animator+0x64,endpoint)
        m.word(w+O['VIEW_ANIMATOR'],animator)
        del m.handlers[syms['scroll_view_set_offset']]
        m.mock('widget_animator_scroll_create','widget_animator_on',
               'widget_animator_start','widget_dispatch_simple_event')
        assert m.call(key)==11
        active=m.get(w+O['VIEW_ANIMATOR'])
        expected=want if populated else top+(48 if endpoint==0 else -48)
        assert active==0 and m.get(w+O['SCROLL_Y'])==expected
        assert any(c[0]=='widget_animator_destroy' and c[1]==animator for c in m.calls)
        passed()

for t,off in [('scroll_view',O['SCROLL_Y']),('table_client',O['TABLE_TOP'])]:
    m=Machine(); w=m.page(t=t)
    for _ in range(20): assert m.call()==11
    assert m.get(w+off)==min(20*manifest['ring_step_pixels'],720 if t=='scroll_view' else 4560)
    assert m.call(O['KEY_PREV'])==11 and m.moved()
    for _ in range(120): m.call(O['KEY_PREV'])
    assert m.get(w+off)==0
    # Empty/short lists consume input without turning into volume changes.
    m.word(w+(O['VIEW_CONTENT_H'] if t=='scroll_view' else O['TABLE_ROWS']),0)
    assert m.call()==11 and m.get(w+off)==0
    passed()

for name in ['playing_page','volume_dialog','saverscreen_page','usbmode_page','unknown_page']:
    m=Machine(); m.page(name); assert m.call()==0 and not m.moved(); passed()
GATES=[('g_backlight_status',0),('g_lockscreen_pageflag',1),('g_testmode_flag',1),
       ('g_guideflag',1),('g_poweroff_state',2),('g_usblink_status',2)]
for flag,value in GATES:
    m=Machine(); m.page(); m.byte(syms[flag],value); assert m.call()==0 and not m.moved(); passed()
# The Bluetooth receiver's gear opens Audio settings: its wheel navigates, while the player
# and its volume dialog still pass wheel input to stock volume handling.
for flag in (1,2):
    m=Machine(); m.page('audiosetting_page'); m.byte(syms['bt__recv_pageflag'],flag)
    assert m.call()==11 and m.moved()
    assert m.call(O['KEY_PREV'])==11 and m.moved(); passed()
    for name in ('bluetoothrecv_page','volume_dialog'):
        m=Machine(); m.page(name); m.byte(syms['bt__recv_pageflag'],flag)
        assert m.call()==0 and not m.moved(); passed()
for field in ['animating','pressed']:
    m=Machine(); m.page(); setattr(m,field,1); assert m.call()==11 and not m.moved(); passed()

m=Machine(); w=m.page('home_page','slide_menu')
assert m.call()==11 and m.get(m.get(w+O['SLIDE_ANIMATOR'])+O['ANIM_X_TO'])==(-240&0xffffffff)
assert m.call(O['KEY_PREV'])==11 and len(m.slides)==1; passed()
m=Machine(); hidden=m.node(visible=0); shown=m.node(); pages=m.node('pages',children=[hidden,shown],active=1)
m.top=m.node('window','artistinfo_page',[pages]); assert m.call()==11 and m.moved()[0][1]==shown; passed()
m.nodes[hidden]['visible']=1
assert m.call()==11 and m.moved()[0][1]==shown; passed()
m.top=m.node('window','sysset_page',[hidden,shown]); assert m.call()==11 and m.moved()[-1][1]==hidden; passed()
# Run the stock setters so incorrect shared offsets cannot make the mocks agree with a bug.
for setter,field in (('scroll_view_set_xslidable','VIEW_HORIZONTAL'),
                     ('scroll_view_set_yslidable','VIEW_VERTICAL'),
                     ('scroll_view_set_snap_to_page','VIEW_SNAP')):
    m=Machine(); vert=m.node(); excluded=m.node()
    m.word(excluded+0x74,syms['g_scroll_view_vtable'])
    value=0 if field=='VIEW_VERTICAL' else 1
    assert m.call(address=syms[setter],args=(excluded,value,0,0))==0
    assert m.u.mem_read(excluded+O[field],1)==bytes([value])
    m.top=m.node('window','sysset_page',[excluded,vert])
    assert m.call()==11 and m.moved()[0][1]==vert
    passed()
# Two navigable panes: only the pane that already holds the selection is used.
m=Machine(); a,b,_,_=m.panes(96)
m.nodes[a]['_ringnav_index']=0
assert m.call()==11 and m.call()==11
assert m.moved()[-1][1]==a
m.nodes[b]['_ringnav_index']=0
assert m.call()==11 and not m.moved(); passed()
for attribute in ['visible','enable']:
    m=Machine(); w=m.page(); m.nodes[w][attribute]=0; assert m.call()==11 and not m.moved(); passed()
# Wheel scrolling sets the offset immediately from the current viewport.
m=Machine(); w=m.page(); m.word(w+O['SCROLL_Y'],100)
assert m.call()==11 and m.moved()[0][0]=='scroll_view_set_offset' and m.moved()[0][3]==148
assert m.get(w+O['SCROLL_Y'])==148 and m.get(w+O['VIEW_ANIMATOR'])==0; passed()

# Painting establishes selection without a sacrificial button press or native focus.
m=Machine(); w,entries=m.page_list(5,extent=1000)
assert m.paint(w)==0 and m.selected(w)==0
if variant=='stock':
    # Default outline: translucent fill, one dark shade stroke, one white stroke, all clipped.
    assert [r['kind'] for r in m.rounded]==['fill','stroke','stroke']
    fill,shade,white=m.rounded
    assert fill['rect']==(1,1,238,46) and fill['bg']==0 and fill['clip']==(0,0,240,96)
    assert fill['color']==FILL and fill['radius']==O['RADIUS'] and fill['width'] is None
    assert shade['rect']==(1,1,238,46) and shade['bg']==0
    assert shade['color']==SHADE
    assert shade['radius']==O['RADIUS'] and shade['width']==1 and shade['clip']==(0,0,240,96)
    assert white['rect']==(2,2,236,44) and white['bg']==0 and white['color']==OUTLINE
    assert white['radius']==O['RADIUS']-1 and white['width']==1
    assert not m.strokes and m.global_alpha==0
    assert m.clip==(0,0,240,240)
    assert m.lcd_colors()==LCD_COLORS
    assert not any(m.get(e+O['W_FOCUS'])&0x80 for e in entries)
    assert [c[0] for c in m.calls if c[0].startswith('canvas_')][-6:]==[
        'canvas_fill_rounded_rect','canvas_stroke_rounded_rect','canvas_stroke_rounded_rect',
        'canvas_set_fill_color','canvas_set_stroke_color','canvas_set_clip_rect']; passed()
else:
    # The bar is painted behind the rows: every band precedes the border hook, which draws nothing.
    names=[c[0] for c in m.calls]
    bg,border=names.index('stock_paint_bg'),names.index('stock_paint')
    assert all(bg<i<border for i,n in enumerate(names) if n=='canvas_fill_rounded_rect')
    assert not m.bands and not m.strokes and m.global_alpha==0
    # Minimal: one off-white card over the full surface width, square (Corners: Square, no key).
    [card]=m.rounded; inset=O['SEL_INSET']
    assert card['kind']=='fill' and card['rect']==(0,0,240,48) and card['radius']==0
    assert card['color']==color_t(0xeeeeec) and card['clip']==(0,0,240,96)
    assert m.clip==(0,0,240,240) and m.lcd_colors()==LCD_COLORS
    assert not any(m.get(e+O['W_FOCUS'])&0x80 for e in entries); passed()
    # Corners: Rounded insets it SEL_INSET from each side, a pill.
    k=Machine(); k.config['ROUNDED']='1'; kw,_=k.page_list(5,extent=1000); k.paint(kw)
    assert [(c['rect'],c['radius']) for c in k.rounded]==[((inset,0,240-2*inset,48),24)]
    # Theme: Classic draws the accent's bar (its highlight, then Graphite's flat fill) instead, full
    # width; under Corners: Rounded the same inset pill, each band a chord of a 48px circle at either end.
    k=Machine(); k.config['THEME']='1'; kw,_=k.page_list(5,extent=1000); k.paint(kw)
    assert [b[:3] for b in k.bands]==[(0,j,240) for j in range(48)]
    k=Machine(); k.config.update(THEME='1',ROUNDED='1'); kw,_=k.page_list(5,extent=1000); k.paint(kw)
    def chord(j,h):
        out=8*h-math.isqrt((h*h-(2*j+1-h)**2)*64)
        return (out+15)//16, out&15
    want=[]
    for band_y in range(48):
        n,frac=chord(band_y,48)
        color=color_t(ACCENTS[0][4 if band_y==0 else 0])
        want.append((inset+n,band_y,240-2*inset-2*n,1,color))
        if frac:
            edge=(color&0xffffff)|((16-frac)*17<<24)
            want.extend([(inset+n-1,band_y,1,1,edge),(240-inset-n,band_y,1,1,edge)])
    assert [b[:5] for b in k.bands]==want
    # Coverage and geometry mirror at both ends and across the horizontal centre.
    assert any(0<b[4]>>24<255 for b in k.bands)
    assert [b[:4] for b in k.bands if b[1]==0]==[(inset+20,0,240-2*inset-40,1),(inset+19,0,1,1),(240-inset-20,0,1,1)]
    passed()
    # Cards stay two pixels inside all three columns, including scrolled first/last rows.
    for classic in (0,1):
        for offset in (0, 24):
            for selected in range(6):
                g=Machine(); g.config.update(THEME=str(classic),ACCENT='2'); gw=g.page('album_page'); tiles=[g.entry(gw,(i//3)*48) for i in range(6)]
                g.nodes[gw]['children']=tiles
                for i,t in enumerate(tiles): g.word(t+O['W_X'],80*(i%3)); g.word(t+O['W_W'],80)
                g.word(gw+O['W_H'],96); g.word(gw+O['SCROLL_Y'],offset)
                g.paint(gw)
                g.nodes[gw]['_ringnav_index']=selected
                g.paint(gw)
                x,y,width,height=g.rounded[-1]['rect']
                assert g.selected(gw)==selected, (offset,selected,g.selected(gw),g.rounded)
                assert (x,width,height)==(80*(selected%3)+2,76,44)
                assert y==48*(selected//3)-g.get(gw+O['SCROLL_Y'])+2
                assert g.rounded[-1]['radius']==0  # Corners: Square
                assert g.rounded[-1]['color']==color_t(ACCENTS[2][0] if classic else O['MINIMAL_FILL']); passed()
    # Touch mode draws nothing; the wheel brings the bar back.
    c=Machine(); cw,_=c.page_list(5,extent=1000); c.paint(cw)
    c.touch(); c.paint(cw); assert not c.drawn() and c.clip==(0,0,240,240)
    c.call(); c.paint(cw); assert c.selected(cw)==1 and c.sel()==(0,36,240,48); passed()
    # Status bar: a solid darker fill on the bar widget alone, no highlight, LCD fill restored.
    s=Machine(); s.clock_minutes=True; title=s.node('hscroll_label','label_clock')
    def top_level(t,name,children=()):
        w=s.node(t,name,children); s.word(w+O['W_PARENT'],s.wm); return w
    bar=top_level('system_bar','system_bar',[title]); s.word(bar+O['W_W'],375); s.word(bar+O['W_H'],30)
    s.word(syms['system_bar'],bar)
    def bg(w): return s.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(w,s.canvas,0,0),gap=0)
    assert bg(bar)==0 and [b[:5] for b in s.bands]==[(0,0,375,30,color_t(O['BAR_COLOR']))]
    assert s.lcd_colors()==LCD_COLORS; passed()
    k=Machine(); k.config['THEME']='1'; kb=k.node('system_bar','system_bar'); k.word(syms['system_bar'],kb)
    k.word(kb+O['W_PARENT'],k.wm); k.word(kb+O['W_W'],375); k.word(kb+O['W_H'],30)
    k.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(kb,k.canvas,0,0))
    assert [b[:5] for b in k.bands]==[(0,0,375,30,color_t(O['BAR_COLOR']))]; passed()  # Classic's black, as Minimal's
    # The clock: local time as 12-hour h:mm AM/PM, written only when the minute shown changes, from a
    # timer due at the next minute (clock_show). A paint reads no time; the bar's first paint and a new
    # 24-hour setting run the clock on the next pass. The mock's localtime is always at :59, so the
    # next minute is a second away.
    def clock(): return s.nodes[title].get('text')
    def writes(): return [c for c in s.calls if c[0]=='widget_set_text_utf8']
    def reads(): return [c for c in s.calls if c[0] in ('time@GLIBC_2.0','localtime_r')]
    def minute(ms=1000): s.advance(ms)
    s.advance(0); assert clock()=='6:14 PM'; passed()  # the gradient check's paint above started it
    for h,mi,want in ((0,0,'12:00 AM'),(0,5,'12:05 AM'),(9,7,'9:07 AM'),(11,59,'11:59 AM'),(12,0,'12:00 PM'),
                      (12,59,'12:59 PM'),(13,0,'1:00 PM'),(23,59,'11:59 PM')):
        s.clock=(h,mi); minute(); assert clock()==want,(h,mi,clock())
    passed()
    # Repaints read no time and leave the label alone; the next minute rewrites it.
    bg(bar); assert not reads() and not writes(); passed()
    s.clock=(0,0); minute(); assert clock()=='12:00 AM' and len(writes())==1; passed()
    # Stock's 24-hour toggle updates on the pass after the next paint, including leading zeroes.
    s.u.mem_write(syms['g_time24h_flag'], b'\x01')
    bg(bar); s.advance(0); assert clock()=='00:00' and len(writes())==1
    bg(bar); s.advance(0); assert not writes()
    for h,mi,want in ((0,5,'00:05'),(9,7,'09:07'),(12,0,'12:00'),(18,14,'18:14'),(23,59,'23:59')):
        s.clock=(h,mi); minute(); assert clock()==want,(h,mi,clock())
    s.u.mem_write(syms['g_time24h_flag'], b'\x00')
    bg(bar); s.advance(0); assert clock()=='11:59 PM' and len(writes())==1
    bg(bar); s.advance(0); assert not writes(); passed()
    # A failed time or localtime shows --:-- once and tries again a minute later.
    for fail in ('time','localtime'):
        s.clock=fail; minute(); assert clock()=='--:--' and len(writes())==1
        minute(); assert not writes()
        s.clock=(7,30); minute(60000); assert clock()=='7:30 AM'
    passed()
    # Past 2038 time() wraps; read unsigned and taken 28 years back, localtime keeps the 32-bit range.
    s.wall=(86400+3*883612800,86400+2*883612800); s.clock=(7,31); minute(); assert clock()=='7:31 AM'
    s.wall=(86400,86400); s.clock=(7,30); minute(); assert clock()=='7:30 AM'; passed()
    # Pages, a dialog on top and pages with their own navbar all keep the clock; their paints read no
    # time and write nothing.
    heading=s.node('hscroll_label',text='System Setting')
    nav=s.node('view','view_navbar',[s.node('image','img_return'),heading],visible=0)
    pages=[top_level('window',n,[nav] if n=='sysset_page' else []) for n in
           ('sysset_page','home_page','playing_page','coverflow_page','tidal_main_page')]+[top_level('dialog','sortselect_dialog')]
    for pg in pages:
        s.top=pg; bg(pg)
        assert clock()=='7:30 AM' and not writes() and not reads()
    passed()
    s.clock=(19,31); s.top=pages[-1]; minute(); assert clock()=='7:31 PM' and len(writes())==1; passed()
    # The codec badge and the Battery setting (bar_sync, on each bar paint): stock sets img_bt's image
    # each second; a new badge shows CODEC_MS, fades out and BT_GLYPH fades in over CODEC_STEPS steps
    # each way, and BT_GLYPH then replaces the badge stock sets again. The battery shows the icon, the
    # percentage or both in view_battery, and the icon while the group's ink would reach past BATT_ROOM.
    # The percentage is sized to its digits once the first paint measured them (10 px each here).
    def battery_checks():
        def battery_bar(mode):
            b=Machine(); b.config={'BATTERY':str(mode)}
            level=b.node('progress_bar','progress_battery',value=50)
            w={n:b.node(t,n,list(c),visible=v,**kw) for n,t,c,v,kw in (
                ('img_bt','image',(),1,dict(image='bar_ldac')),('img_wifi','image',(),1,{}),
                ('label_battery','label',(),0,{}),('view_battery','view',(),0,{}),
                ('img_battery','image',(level,),1,dict(image='bar_battery')))}
            b.word(w['img_wifi']+O['W_W'],14); b.word(w['view_battery']+O['W_H'],30)
            bar=b.node('system_bar','system_bar',[b.node('view','view_right',list(w.values()))])
            w['progress_battery']=level
            b.word(bar+O['W_PARENT'],b.wm); b.word(syms['system_bar'],bar)
            return b,bar,w,lambda x=bar: b.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(x,b.canvas,0,0),gap=0)
        def shown(b,w): return [n for n in ('img_battery','label_battery','view_battery') if b.nodes[w[n]]['visible']]
        def alpha(b,w): return b.get(w['img_bt']+0x34)&255
        b,bar,w,paint=battery_bar(1); paint()
        # LDAC's badge (36px of ink) and Wi-Fi leave no room for the percentage: the icon, until it fades.
        assert b.nodes[w['img_bt']]['image']=='bar_ldac' and alpha(b,w)==255 and shown(b,w)==['view_battery'] and len(b.timers)==1
        b.advance(O['CODEC_MS']-1); paint(); assert b.nodes[w['img_bt']]['image']=='bar_ldac' and alpha(b,w)==255
        steps=[]
        for _ in range(2*O['CODEC_STEPS']):
            b.advance(O['CODEC_STEP_MS'] if steps else 1); steps.append((alpha(b,w),b.nodes[w['img_bt']]['image']))
        n=O['CODEC_STEPS']
        assert steps==[(255*abs(n-k)//n,'bar_ldac' if k<n else O_GLYPH) for k in range(1,2*n+1)] and not b.timers, steps
        paint(); assert shown(b,w)==['view_battery'] and b.get(w['view_battery']+O['W_W'])==O['BATT_PCT_W']; passed()
        # Stock's next tick sets the badge again: the glyph goes straight back, with no new fade.
        b.nodes[w['img_bt']]['image']='bar_ldac'; paint()
        assert b.nodes[w['img_bt']]['image']==O_GLYPH and not b.timers and shown(b,w)==['view_battery']; passed()
        # A new codec flashes again; AAC's narrow badge leaves room, so the percentage stays.
        b.nodes[w['img_bt']]['image']='bar_aac'; paint()
        assert b.nodes[w['img_bt']]['image']=='bar_aac' and len(b.timers)==1 and shown(b,w)==['view_battery']; passed()
        # Bluetooth off ends the flash; a reconnect with the same codec flashes again.
        b.nodes[w['img_bt']]['visible']=0; paint(); assert not b.timers and alpha(b,w)==255
        b.nodes[w['img_bt']]['visible']=1; paint(); assert len(b.timers)==1; passed()
        # Icon + Percent: the outline, nub and level in one colour, canvas state restored; charging
        # is a full BATT_CHARGE_RGB icon, low the accent's red tone; the slot repaints only when one
        # of them changes.
        b,bar,w,paint=battery_bar(2); b.nodes[w['img_bt']]['visible']=0
        lcd=lambda: (b.lcd_colors(),b.get(b.lcd+O['LCD_TEXT_COLOR']),b.get(b.canvas+O['CANVAS_ALIGN_V']),b.get(b.canvas+O['CANVAS_ALIGN_H']))
        b.nodes[w['label_battery']]['text']='50%'
        paint(); assert shown(b,w)==['view_battery']; before=lcd()
        def slot(): b.bands=[]; b.letters=[]; paint(w['view_battery']); assert lcd()==before; return b.bands,b.letters
        bw,bh,y=O['BATT_BODY_W'],O['BATT_BODY_H'],(30+1-O['BATT_BODY_H'])//2-O['BATT_PCT_DY']
        measured=False
        for image,value,rgb,text in (('bar_battery',0,0xffffff,'0'),('bar_battery',1,0xffffff,'1'),('bar_battery',50,0xffffff,'50'),('bar_battery',100,0xffffff,'100'),('bar_battery',88,0xffffff,'88'),('bar_charge',63,O['BATT_CHARGE_RGB'],'63'),('bar_charge',100,O['BATT_CHARGE_RGB'],'100'),
                                     ('bar_lowcharge',5,O['BATT_LOW_RGB'],'5')):
            # the level is label_battery's text: stock zeroes progress_battery while charging
            b.nodes[w['img_battery']]['image']=image; b.nodes[w['label_battery']]['text']=f'{value}%'
            b.nodes[w['progress_battery']]['value']=0 if image=='bar_charge' else value
            b.calls=[]; paint(); assert ('widget_invalidate_force',w['view_battery']) in [c[:2] for c in b.calls]
            b.calls=[]; paint(); assert ('widget_invalidate_force',w['view_battery']) not in [c[:2] for c in b.calls]
            pct=10*len(text)+10 if measured else O['BATT_PCT_W']
            assert b.get(w['view_battery']+O['W_W'])==bw+O['BATT_NUB_W']+O['BATT_GAP']+pct
            bands,letters=slot(); measured=True
            fill=bw-4 if image=='bar_charge' else (bw-4)*value//100
            expected=[(1,y,bw-2,1,color_t(rgb)),(1,y+bh-1,bw-2,1,color_t(rgb)),(0,y+1,1,bh-2,color_t(rgb)),
                      (bw-1,y+1,1,bh-2,color_t(rgb)),(bw,y+(bh-O['BATT_NUB_H'])//2,O['BATT_NUB_W'],O['BATT_NUB_H'],color_t(rgb)),
                      (2,y+2,fill,bh-4,color_t(rgb))]
            assert [x[:5] for x in bands]==expected
            assert [(l['text'],l['rect'],l['color'],l['font'],l['align']) for l in letters]==[(text+'%',(bw+O['BATT_NUB_W']+O['BATT_GAP'],0,pct,30),color_t(rgb),('default',O['BATT_PX']),(1,1))]
        # Percent: the digits alone, in a slot their width, the charge colour, after the first paint.
        b,bar,w,paint=battery_bar(1); b.nodes[w['img_bt']]['visible']=0
        for image,value,rgb in (('bar_battery',100,0xffffff),('bar_battery',88,0xffffff),('bar_charge',7,O['BATT_CHARGE_RGB'])):
            b.nodes[w['img_battery']]['image']=image; b.nodes[w['label_battery']]['text']=f'{value}%'
            paint(); paint(w['view_battery']); paint(); before=lcd(); bands,letters=slot()
            pct=10*len(str(value))+10
            assert b.get(w['view_battery']+O['W_W'])==pct and not bands
            assert [(l['text'],l['rect'],l['color']) for l in letters]==[(f'{value}%',(0,0,pct,30),color_t(rgb))]

        # Percent Inside: compact rounded body and dark digits, including 100, in every state.
        for theme in (0,1):
            b,bar,w,paint=battery_bar(3); b.config['THEME']=str(theme)
            b.nodes[w['img_bt']]['visible']=0
            for image,rgb in (('bar_battery',0xffffff),('bar_charge',O['BATT_CHARGE_RGB']),('bar_lowcharge',O['BATT_LOW_RGB'])):
                for value in (0,1,9,10,99,100):
                    b.nodes[w['img_battery']]['image']=image
                    b.nodes[w['label_battery']]['text']=f'{value}%'
                    paint(); before=lcd(); bands,letters=slot()
                    bw,bh,nh=O['BATT_INSIDE_W'],O['BATT_INSIDE_H'],O['BATT_NUB_H']; y=(30+1-bh)//2
                    assert b.get(w['view_battery']+O['W_W'])==bw+O['BATT_NUB_W']
                    assert [x[:5] for x in bands]==[(bw,y+(bh-nh)//2,O['BATT_NUB_W'],nh,color_t(rgb))]
                    assert [(r['kind'],r['rect'],r['color'],r['radius']) for r in b.rounded]==[('fill',(0,y,bw,bh),color_t(rgb),3)]
                    assert [(l['text'],l['rect'],l['color'],l['font'],l['align']) for l in letters]==[(str(value),(2,y+O['BATT_INSIDE_TEXT_DY'],bw-4,bh),color_t(0),('default',O['BATT_INSIDE_PX']),(1,1))]
        # Percent Inside fits beside the widest codec and Wi-Fi.
        b,bar,w,paint=battery_bar(3); b.nodes[w['label_battery']]['text']='100%'
        b.nodes[w['img_bt']]['image']='bar_aptxhd'
        paint(); assert b.get(w['view_battery']+O['W_W'])==O['BATT_INSIDE_W']+O['BATT_NUB_W']
        # A crowded codec temporarily uses the small icon and recovers without a level change.
        b,bar,w,paint=battery_bar(2); b.nodes[w['label_battery']]['text']='100%'
        b.nodes[w['img_bt']]['image']='bar_aptxhd'
        paint(); assert b.get(w['view_battery']+O['W_W'])==O['BATT_BODY_W']+O['BATT_NUB_W']
        b.advance(O['CODEC_MS'])
        for _ in range(O['CODEC_STEPS']): b.advance(O['CODEC_STEP_MS'])
        b.calls=[]; paint()
        assert b.get(w['view_battery']+O['W_W'])>O['BATT_BODY_W']+O['BATT_NUB_W']+O['BATT_GAP']
        assert ('widget_invalidate_force',w['view_battery']) in [c[:2] for c in b.calls]
        for invalid in ('4','9','-1','33','bad',''):
            b,bar,w,paint=battery_bar(invalid); paint()
            assert b.get(w['view_battery']+O['W_W'])==O['BATT_BODY_W']+O['BATT_NUB_W']

        for mode in range(4):
            for bt in (0,1):
                for wifi in (0,1):
                    b,bar,w,paint=battery_bar(mode)
                    b.nodes[w['img_bt']].update(visible=bt,image=O_GLYPH)
                    b.nodes[w['img_wifi']]['visible']=wifi
                    b.nodes[w['label_battery']]['text']='100%'
                    paint()
                    assert shown(b,w)==['view_battery']
                    assert b.get(w['view_battery']+O['W_W'])==(O['BATT_PCT_W'] if mode==1 else (O['BATT_INSIDE_W'] if mode==3 else O['BATT_BODY_W'])+O['BATT_NUB_W']+(O['BATT_GAP']+O['BATT_PCT_W'] if mode==2 else 0))
        passed()
    battery_checks()
assert m.confirm()==11 and m.dispatched()[0][1]==entries[0]; passed()
assert m.call()==11 and m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==12
assert m.call()==11 and m.selected(w)==2 and m.get(w+O['SCROLL_Y'])==60
assert m.confirm()==11 and m.dispatched()[0][1]==entries[2]; passed()
# The separate Play/Pause key remains native even with an active selection.
assert m.call(O['KEY_PLAY'])==0 and not m.dispatched(); passed()
# Touching a different row selects it before the native callback runs; no extra click.
assert m.touch()==0
m.on_click=lambda a,b: (None if m.selected(w)==1 else (_ for _ in ()).throw(AssertionError('late selection')))
assert m.click(entries[1])==0 and len(m.dispatched())==1 and m.selected(w)==1
m.on_click=None
assert m.confirm()==11 and m.dispatched()[0][1]==entries[1]; passed()
# Native touch focus may move anywhere without altering the logical selection.
m.word(entries[4]+O['W_FOCUS'],0x80)
assert m.confirm()==11 and m.dispatched()[0][1]==entries[1]; passed()
# Swipe preserves selection during momentum; settle adopts the visible row nearest the centre.
m.touch(); m.word(w+O['SCROLL_Y'],110); m.word(w+O['VIEW_ANIMATOR'],0x1234)
m.paint(w); assert m.selected(w)==1
m.word(w+O['VIEW_ANIMATOR'],0); m.paint(w); assert m.selected(w)==3
assert m.confirm()==11 and m.dispatched()[0][1]==entries[3]; passed()
# Wheel interrupts touch momentum, and centre while a finger is down is consumed without a click.
m.touch(); m.word(w+O['VIEW_ANIMATOR'],0x1234)
assert m.call(O['KEY_PREV'])==11 and m.get(w+O['VIEW_ANIMATOR'])==0 and m.selected(w)==2
m.pressed=1
assert m.confirm()==11 and not m.dispatched()
assert m.call()==11 and m.selected(w)==2
m.pressed=0; passed()
# With three rows in view the difference shows: the middle one wins, not the top edge.
m=Machine(); w,es=m.page_list(8,height=192,extent=1000)
m.paint(w); m.touch(); m.word(w+O['SCROLL_Y'],200); m.word(w+O['VIEW_ANIMATOR'],0x1234)
m.paint(w); assert m.selected(w)==0
m.word(w+O['VIEW_ANIMATOR'],0); m.paint(w); assert m.selected(w)==6; passed()
# Native click may destroy the current page. Nothing dereferences its target afterwards.
def destroy(a,b):
    m.nodes.clear(); m.top=0
m.on_click=destroy
assert m.confirm()==11 and len(m.dispatched())==1; passed()

# Execute native row-pool constructors, including the untouched album grid branch.
# The 52px artwork keeps the stock nine-pixel inset in Stock; iPod keeps that same 52px
# artwork at natural size (no rescaling) and gives it an even eight-pixel inset on all four
# sides of the 68px row body. The playing overlay follows.
ARTWORK = {
    0x523038: [('img_icon', (4, 9, 52, 52), (0, 8, 52, 52)),
               ('git_playing', (0, 9, 52, 52), (0, 8, 52, 52))],
    0x4aa2cc: [('img_icon', (48, 0, 50, 70), (48, 0, 52, 68)),
               ('img_gifbg', (0, 9, 50, 52), (0, 8, 50, 52))],
    0x4b0efc: [('img_icon', (48, 0, 50, 70), (48, 0, 52, 68))],
    0x4a4ae8: [('img_icon', (48, 0, 50, 70), (48, 0, 52, 68)),
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
    assert (vtable!=stock_vtable)==(variant=='ipod')
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
                    edge=O['CHEVRON_W']+gap if drill and not more and variant=='ipod' else margin
                    right=m.get(button+O['W_W'])-edge-sum(widths[c]+gap for c in after if m.nodes[c]['visible'])
                    assert m.get(text+O['W_X'])==left
                    if variant=='ipod':
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
                    else:
                        assert m.get(text+O['W_W'])==widths[text] and m.get(title+O['W_W'])==title_width
                    assert (m.get(title+O['W_Y']),m.get(title+O['W_H']))==title_yh
                    assert m.nodes[title]['text']==label
                    assert all(m.nodes[title][k]==v for k,v in attributes.items())
                    for c in children:
                        if c!=text: assert m.get(c+O['W_W'])==widths[c]

def check_album_tile(m, button):
    def descendants(w):
        return [c for child in m.nodes[w]['children'] for c in [child,*descendants(child)]]
    def geometry(w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
    children=descendants(button)
    original={c:geometry(c) for c in children}
    draw_types=[c for c in children if m.nodes[c]['type']=='image' and original[c][2:]==(110,110)]
    tile=geometry(button)
    for c in children:
        if m.nodes[c]['type']=='hscroll_label': m.nodes[c]['text']='A very long album or artist label '*10
    for _ in range(3):
        # Both themes inset the cover inside the selection card; repeated paints never compound it.
        m.call(address=payload_syms['album_children'],args=(button,0,0,0),gap=0)
        assert geometry(button)==tile
        for c in draw_types: assert m.nodes[c].get('draw_type',0)==O['IMAGE_DRAW_SCALE_DOWN']
        for c,(x,y,w,h) in original.items():
            actual=geometry(c)
            if w==h==110:
                inset=6 if m.get(c+O['W_PARENT'])==button else 0
                assert actual==(inset,inset,98,98), actual
            elif m.nodes[c]['type']=='hscroll_label':
                assert actual==(6,y-2,98,h), actual
                assert actual[1]+h<=154
                assert m.nodes[c]['text'].startswith('A very long')
            else: assert actual==(x,y,w,h)
    m.calls=[]; m.call(address=payload_syms['album_children'],args=(button,0,0,0),gap=0)
    assert not any(c[0] in ('widget_move_resize','image_set_draw_type') for c in m.calls)
    # The public paint hook keeps the same geometry, in Classic too.
    m.config['THEME']='1'
    m.paint(button)
    assert all(geometry(c)[2]==98 for c in children if original[c][2]==110)
    passed()

if variant=='ipod':
    from ipod import decode
    def asset_node(m,n,parent):
        kind,rect,props,children=n
        w=m.node(kind,props.get('name',''))
        m.word(w+O['W_PARENT'],parent)
        for k,v in zip(('W_X','W_Y','W_W','W_H'),rect): m.word(w+O[k],v)
        m.nodes[w]['children']=[asset_node(m,c,w) for c in children]
        return w
    m=Machine(); root=asset_node(m,decode((B/'ui/localmusic/album_page.bin').read_bytes()),m.wm); m.top=root
    for button in [w for w,n in m.nodes.items() if n['type']=='button' and n['children']]:
        check_album_tile(m,button)

for address in (0x523038, 0x4aa2cc, 0x4b0efc, 0x4a4ae8):
    for grid in ((0, 1) if address == 0x4a4ae8 else (0,)):
        m = Machine(); w = m.page('artistinfo_page' if grid else 'folder_page', 'table_client')
        m.mock('table_row_create', 'button_create', 'image_create', 'view_create',
               'hscroll_label_create', 'gif_image_create', 'widget_use_style',
               'widget_set_name', 'widget_set_children_layout', 'image_set_draw_type',
               'image_base_set_image', 'set_hscroll_label_attribute')
        m.word(syms['album_modetype'], grid)
        assert m.call(address=address, args=(w, w, 4, 0)) == 0
        rows = m.nodes[w]['children']
        assert len(rows) == 4
        for row in rows:
            assert m.get(row+O['W_H']) == (210 if grid else 72 if variant == 'ipod' else 78)
            for button in m.nodes[row]['children']:
                assert m.get(button+O['W_H']) == (160 if grid else 68 if variant == 'ipod' else 70)
        if grid and variant=='ipod':
            m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
            for row in rows:
                for button in m.nodes[row]['children']: check_album_tile(m,button)
        if not grid:
            for name, stock, compact_geometry in ARTWORK[address]:
                arts = [n for n, v in m.nodes.items() if v.get('name') == name]
                assert arts, name
                for art in arts:
                    geometry = tuple(signed(m.get(art+O[off])) for off in ('W_X', 'W_Y', 'W_W', 'W_H'))
                    assert geometry == (compact_geometry if variant == 'ipod' else stock), (name, geometry)
        # Preparing an existing pool does not recreate or resize its rows.
        before = len(m.nodes)
        assert m.call(address=address, args=(w, w, 4, 0)) == 0 and len(m.nodes) == before
        if not grid:
            for row in rows[:2]:  # independently recreated rows as well as repeated reuse
                check_title_bounds(m,m.nodes[row]['children'][0])
            into=[n for n,v in m.nodes.items() if v.get('name')=='img_into']
            if into and variant=='ipod':  # the payload's chevron column is stock's, after layout
                button=m.nodes[rows[0]]['children'][0]
                assert m.get(into[0]+O['W_X'])==m.get(button+O['W_W'])-O['CHEVRON_W'] and m.get(into[0]+O['W_W'])==50
            if address==0x523038:
                # Execute the real folder text/style rebind after layout: its stock 140/190px
                # reset must no longer undo the computed width while a row pool is recycled.
                button=m.nodes[rows[0]]['children'][0]
                title=next(c for c in m.nodes[button]['children'] if m.nodes[c]['name']=='scrlabel_name')
                width=m.get(title+O['W_W'])
                m.row_record=m.alloc(0x80); m.word(m.row_record+0x34,8)
                m.mock('deque_at','tk_snprintf','widget_set_text_utf8','file_is_playing')
                for navbar,label in ((0,'再利用された長いフォルダー名 '*5), (1,'99% Invisible'),
                                     (0,'100% true %s %n.mp3'), (1,'plain.mp3'), (0,'x'*600)):
                    m.byte(syms['g_navbar_status'],navbar)
                    m.word(m.row_record+8,m.string(label))
                    assert m.call(address=0x522304,args=(button,7,0,0))==0
                    assert m.nodes[title]['text']==label.encode()[:511].decode()
                    assert m.get(title+O['W_W'])==(width if variant=='ipod' else 140 if navbar else 190)
        else:
            assert all(m.get(n+O['W_CHILDREN_LAYOUT'])==0 for row in rows for n in m.nodes[row]['children'])
passed()

# The three non-pooled constructors: album tracks, artist tracks and playlists.
# These use distinct title/container names and must receive the same native layout behavior;
# only a drill window (playlist_page) reserves the chevron's space.
for address,page in ((0x4a62c0,'artistinfo_page'),(0x4adcbc,'artistinfo_page'),(0x4b2864,'artistinfo_page'),(0x4b2864,'playlist_page')):
    for reopen in range(2):
        m=Machine(); w=m.page(page,'scroll_view')
        m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
        m.nodes[w]['name']='scroll_view_track'
        m.row_record=m.alloc(0x80)
        for off in (8,12,16,24): m.word(m.row_record+off,m.string('日本語 Title'))
        m.word(m.row_record+0x48,1)
        m.mock('button_create','list_item_create','image_create','view_create','hscroll_label_create',
               'gif_image_create','widget_use_style','widget_set_name','widget_set_text_utf8',
               'widget_set_visible','widget_on','widget_destroy_children','image_set_draw_type',
               'image_base_set_image','set_hscroll_label_attribute','hscroll_label_set_only_focus',
               'hscroll_label_set_ellipses','hscroll_label_set_speed','gif_image_play','deque_at',
               'deque_size','tk_snprintf','batch_get_selectitem','file_is_playing','getFormatString',
               'getMusicByPlayList','get_albumcover_listsize','mclGetPlayStatus','list_get_img_pic',
               'strcmp@GLIBC_2.0','toolsTrimLeft','toolsTrimRight')
        m.handlers[0x4aad10]='row_toolbar'
        assert m.call(address=address,args=(w,0,0,0))==0
        buttons=[n for n,v in m.nodes.items() if v['type']=='button']
        assert len(buttons)==1
        check_title_bounds(m,buttons[0],drill=page=='playlist_page')
        if variant=='ipod' and address==0x4b2864:
            button=buttons[0]
            more=next(c for c in m.nodes[button]['children'] if m.nodes[c]['name'].startswith('img_more'))
            native_row_layout(m,button)
            # Stock opens More for pointer x >= 286: the dots must keep that trailing position.
            assert m.get(button+O['W_X'])+m.get(more+O['W_X'])>=286
passed()

# Long Return executes the stock gates and release filter in both variants.
def long_machine():
    m = Machine()
    m.mock('netdisk_folder_clear', 'navigator_back_to_home', 'awake_screen',
           'getFormatString', 'navigator_to_with_context', 'navigator_window_is_exist')
    return m

def long_return(m):
    return m.call(170, address=syms['on_wm_keylong_fun'], event_type=0x111, gap=0)

def destinations(m):
    return [c for c in m.calls if c[0] in ('navigator_back_to_home', 'navigator_switch_to_with_context')]

for page in ('home_page', 'folder_page', 'playing_page', 'sysset_page'):
    m = long_machine(); m.page(page)
    # No playback data is initialized: the switch must work with an empty queue as well.
    assert long_return(m) == 0
    dest = destinations(m)
    assert len(dest) == 1
    assert dest[0][0] == ('navigator_switch_to_with_context' if page != 'playing_page' else 'navigator_back_to_home')
    if page != 'playing_page':
        assert m.text(dest[0][1]) == 'playing_page'
        assert [m.get(dest[0][2] + 4*i) for i in range(4)] == [0, 0, 255, 2]
    # Here the navigator is only recorded, so the switch never lands and the latch is left
    # armed; the NavigationMachine checks below cover the landed switch that drops it.
    held = 11
    assert m.call(170, gap=0) == held
    assert m.call(170, gap=0) == 0   # next short Return still reaches stock Back
    for _ in range(3):
        assert long_return(m) == 0
        assert len(destinations(m)) == 1
    assert m.call(170, gap=0) == held
passed()

# The stock long-key gates stay effective, with the shared navigation restrictions added.
for flag, value in GATES:
    m = long_machine(); m.page('folder_page'); m.byte(syms[flag], value)
    long_return(m)
    # Stock itself blocks power-off, guide and test mode; iPod adds the shared restrictions
    # checked by usable(), so every listed flag blocks in both builds.
    assert not destinations(m), flag
for light in (0, 1):
    for lock in (0, 1):
        for mode in range(4):
            m = long_machine(); m.page('folder_page')
            m.byte(syms['g_backlight_status'], light)
            m.byte(syms['g_keylock_flag'], lock); m.byte(syms['g_keylock_mode'], mode)
            long_return(m)
            blocked = not light
            assert bool(destinations(m)) == (not blocked)
passed()
m = long_machine(); m.page_list()
assert m.call(O['KEY_CENTER']) == 11 and m.timers
long_return(m)
assert not m.timers
m.advance(201)
assert not m.dispatched()
# A hold blocked elsewhere still swallows its release, so no delayed page action runs.
m = long_machine(); m.page('folder_page'); m.byte(syms['g_usblink_status'], 2)
long_return(m)
assert not destinations(m)
assert m.call(170, gap=0) == 11
passed()
# Hold Return on Now Playing goes back to the page it opened over while that lies right under it; otherwise,
# or with no such page, Home.
for under,want in ((True,'navigator_back'),(False,'navigator_back_to_home')):
    m = long_machine(); m.mock('navigator_back'); m.page('folder_page'); folder = m.top
    assert long_return(m) == 0 and destinations(m)[0][0] == 'navigator_switch_to_with_context'
    np = m.node('window', 'playing_page'); other = m.node('window', 'sysset_page')
    m.nodes[m.wm] = {'children': [folder, np] if under else [folder, other, np]}; m.top = np
    m.call(170, gap=0); m.calls = []
    assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home')] == [want]
    m.call(170, gap=0); m.calls = []
    assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home')] == ['navigator_back_to_home']
passed()
# Hold Return: Home (Q2POD HOLDRETURN 1) goes Home from any page but Home, Now Playing included;
# Home still opens Now Playing. Going Home leaves the stock latch armed, as the stock Home call did:
# the hold's release is swallowed and the next short Return passes.
for page in ('folder_page', 'playing_page', 'sysset_page', 'home_page'):
    m = long_machine(); m.mock('navigator_back'); m.config['HOLDRETURN'] = '1'; m.page(page)
    want = 'navigator_switch_to_with_context' if page == 'home_page' else 'navigator_back_to_home'
    assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home', 'navigator_switch_to_with_context')] == [want], page
    if page != 'home_page': assert m.call(170, gap=0) == 11 and m.call(170, gap=0) == 0, page
passed()
if variant == 'ipod':  # over quick settings, the release goes to that latch, not to closing it
    m = long_machine(); m.mock('dialog_quit'); m.config['HOLDRETURN'] = '1'
    m.top = m.node('dialog', 'statusbar_dialog')
    assert long_return(m) == 0 and [c[0] for c in destinations(m)] == ['navigator_back_to_home']
    assert m.call(170, gap=0) == 11 and not [c for c in m.calls if c[0] == 'dialog_quit']
    assert m.call(170, gap=0) == 11 and [c[1:3] for c in m.calls if c[0] == 'dialog_quit'] == [(m.top, 0)]
    passed()
# Now Playing opened over Home, which lies right under it: Recent would go Back, Home goes Home.
m = long_machine(); m.mock('navigator_back'); m.config['HOLDRETURN'] = '1'; m.page('home_page'); home = m.top
long_return(m); np = m.node('window', 'playing_page')
m.nodes[m.wm] = {'children': [home, np]}; m.top = np; m.call(170, gap=0); m.calls = []
assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home')] == ['navigator_back_to_home']
passed()
# Other long-key paths remain byte-for-byte stock; exercise the inert keys and power gate.
# Home is not a local list, so a Play/Pause hold there stays stock too (queue menu below).
for key in (171, 172, 173, 222, 223, 218):
    m = long_machine(); m.page('home_page')
    if key == 218: m.byte(syms['g_poweroff_state'], 2)
    m.call(key, address=syms['on_wm_keylong_fun'], event_type=0x111)
    assert not destinations(m)
passed()

# Four complete compact rows resolve the same target for touch and centre at every row.
for index in range(4):
    m = Machine(); w, es = m.page_list(4, height=288, extent=288, name='folder_page')
    for i, e in enumerate(es):
        m.word(e+O['W_Y'], i*72); m.word(e+O['W_H'], 68)
    m.touch(); m.click(es[index])
    assert m.confirm() == 11 and m.dispatched()[0][1] == es[index]
passed()

# Recycle a small row pool: selection belongs to the logical index, never the widget.
m=Machine(); w,rows,entries=m.table_page(rebind=True)
m.paint(w); assert m.selected(w)==0
assert m.call()==11 and m.selected(w)==1
assert m.call()==11 and m.selected(w)==2 and m.get(w+O['TABLE_TOP'])==60
assert m.moved()[-1][0]=='table_client_set_yoffset'
assert m.confirm()==11 and m.dispatched()[0][1]==entries[1]; passed()
# A wheel step ends its scroll as a touch scroll does, so stock lists load the covers in view.
m=Machine(); w,rows,entries=m.table_page(rebind=True); m.mock('widget_dispatch_simple_event')
m.paint(w); m.call(); m.call()
assert ('widget_dispatch_simple_event',w,O['EVT_SCROLL_END']) in [c[:3] for c in m.calls]; passed()
# A touch click in a rebound row immediately changes what centre opens.
m.touch(); m.click(entries[0]); assert m.selected(w)==1
assert m.confirm()==11 and m.dispatched()[0][1]==entries[0]; passed()
# Swipe out of the old pool, then centre: settle/re-resolve before dispatch.
m.touch(); m.word(w+O['TABLE_TOP'],480); m.bind(rows,480); m.word(w+O['TABLE_ANIMATOR'],0x9876)
assert m.confirm()==11 and m.selected(w)==10 and m.dispatched()[0][1]==entries[0]
assert m.get(w+O['TABLE_ANIMATOR'])==0; passed()
# Returning to a surviving menu keeps a valid selection; shrinking data repairs it.
oldtop=m.top; m.page('playing_page'); assert m.confirm()==0
m.top=oldtop; m.paint(w); assert m.selected(w)==10
m.word(w+O['TABLE_ROWS'],1); m.word(w+O['TABLE_TOP'],0); m.bind(rows); m.paint(w)
assert m.selected(w)==0; passed()

# A recreated page recalls the last selected row and reveals it without a sacrificial press.
def walk(n,steps,texts=(),extent=1000,**kw):
    """Paint a fresh labelled list of n entries, then wheel down steps rows."""
    m=Machine(); w,es=m.page_list(n,extent=extent,**kw); m.label(es,texts); m.paint(w)
    for _ in range(steps): assert m.call()==11
    assert m.selected(w)==steps
    return m,w,es
m,w,es=walk(10,5); assert m.get(w+O['SCROLL_Y'])==204
w2,es2=m.page_list(10,extent=1000)
assert m.paint(w2)==0 and m.selected(w2)==5 and m.get(w2+O['SCROLL_Y'])==204
assert m.sel()==(0,36,240,48)
assert m.confirm()==11 and m.dispatched()[0][1]==es2[5]; passed()

# Memory is per audited context: visiting another page leaves it alone.
w3,es3=m.page_list(10,extent=1000,name='display_page')
assert m.paint(w3)==0 and m.selected(w3)==0
w4,es4=m.page_list(10,extent=1000)
assert m.paint(w4)==0 and m.selected(w4)==5; passed()

# A stale remembered row is ignored when the new list is shorter.
w5,es5=m.page_list(2,extent=300)
assert m.paint(w5)==0 and m.selected(w5)==0
assert m.confirm()==11 and m.dispatched()[0][1]==es5[0]; passed()

# A settled swipe stores the row the user sees, not the pre-swipe selection.
m=Machine(); w,es=m.page_list(10,extent=1000)
m.paint(w); m.touch(); m.word(w+O['SCROLL_Y'],240); m.word(w+O['VIEW_ANIMATOR'],0x1234)
m.paint(w); assert m.selected(w)==0
m.word(w+O['VIEW_ANIMATOR'],0); m.paint(w); assert m.selected(w)==5
w2,es2=m.page_list(10,extent=1000)
assert m.paint(w2)==0 and m.selected(w2)==5 and m.get(w2+O['SCROLL_Y'])==204; passed()

# Position memory follows row text across a recreation, not just the index.
m,w,es=walk(6,4,[f'track {i}' for i in range(6)])
w2,es2=m.page_list(6,extent=1000); m.label(es2,[f'track {i}' for i in (4,0,1,2,3,5)])
assert m.paint(w2)==0 and m.selected(w2)==0
assert m.confirm()==11 and m.dispatched()[0][1]==es2[0]; passed()
# The same recall with (rows, detents, texts before, texts after recreation, recalled row).
for n,steps,before,after,want in (
        (6,3,[],[],3),  # rows without text fall back to the remembered index
        # Duplicate row text restores the occurrence the user left, not the first duplicate.
        (6,4,['Intro']*6,['Intro']*6,4),
        # After a re-sort, equal-text rows resolve to the occurrence nearest the old position.
        (6,4,[None]*4+['dup'],[None,'dup',None,None,None,'dup'],5),
        (6,3,[None]*3+['dup'],[None,None,'dup',None,'dup'],2),  # equal distance keeps the earlier
        # Two rows share a title; the second text decides before proximity does.
        (4,1,[('Intro','Artist A'),('Intro','Artist B'),'Intro','Intro'],
             ['Intro','Intro',('Intro','Artist A'),('Intro','Artist B')],3),
        # A subtitle missing from the recreated rows never blocks the primary match.
        (4,1,['Intro',('Intro','Artist B'),'Intro','Intro'],['Intro',None,None,'Intro'],0)):
    m,w,es=walk(n,steps,before)
    w2,es2=m.page_list(n,extent=1000); m.label(es2,after)
    assert m.paint(w2)==0 and m.selected(w2)==want
    passed()

# Run the actual stock label text accessor, including value_wstr and the label vtable.
# U+0100 and U+0200 share a low zero byte; titles/subtitles must use complete code points.
m=Machine(); w,es=m.page_list(4,extent=1000)
del m.handlers[syms['widget_get_text']]
m.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
def stock_text(e,text):
    m.word(e+0x74,syms['g_label_vtable'])
    m.word(e+0x38,len(text)); m.word(e+0x40,m.wide_string(text))
for i,e in enumerate(es):
    stock_text(e,'\u0100 Intro')
    sub=m.node('label'); stock_text(sub,['\u0100','\u0200','\U0001f600','音楽'][i])
    m.nodes[e]['children']=[sub]
# The narrow accessor really returns NULL, even though widget_get_text sees the label.
del m.handlers[syms['widget_get_prop_str']]
assert m.call(address=syms['widget_get_prop_str'],args=(es[0],m.string('text'),0,0))==0
m.mock('widget_get_prop_str')
m.paint(w); m.call(); assert m.selected(w)==1
w2,es2=m.page_list(4,extent=1000)
for i,e in enumerate(es2):
    stock_text(e,'\u0100 Intro')
    sub=m.node('label'); stock_text(sub,['\u0100','\U0001f600','音楽','\u0200'][i])
    m.nodes[e]['children']=[sub]
assert m.paint(w2)==0 and m.selected(w2)==3
assert m.confirm()==11 and m.dispatched()[0][1]==es2[3]; passed()

# Folder memory belongs to the full path, even when every visible row title is identical.
m=Machine(); m.folder('/sd/Folder A')
w,es=m.page_list(8,name='folder_page'); m.label(es,['Intro']*8)
m.paint(w)
for _ in range(5): m.call()
w2,es2=m.page_list(8,name='folder_page'); m.label(es2,['Intro']*8)
m.paint(w2); assert m.selected(w2)==5  # same path still recalls
m.folder('/sd/Folder B')
w3,es3=m.page_list(8,name='folder_page'); m.label(es3,['Intro']*8)
m.paint(w3); assert m.selected(w3)==0
# A surviving surface rebound to another folder also resets, even at the same row count.
for _ in range(3): m.call()
m.folder('/sd/Folder C')
m.word(w3+O['SCROLL_Y'],0); m.paint(w3); assert m.selected(w3)==0; passed()
# Nested folder returns restore each level on recreated or rebound surfaces.
for reuse in (False,True):
    m=Machine(); w,es=m.page_list(10,name='folder_page')
    for path,wanted in (('/sd',6),('/sd/child',4),('/sd/child/grandchild',2)):
        m.folder(path)
        if not reuse: w,es=m.page_list(10,name='folder_page')
        m.word(w+O['SCROLL_Y'],0); m.paint(w); assert m.selected(w)==0
        for _ in range(wanted): m.call()
    for path,wanted in (('/sd/child',4),('/sd',6),('/sd/child/grandchild',2)):
        m.folder(path)
        if not reuse: w,es=m.page_list(10,name='folder_page')
        m.word(w+O['SCROLL_Y'],0); m.paint(w)
        assert m.selected(w)==wanted and m.get(w+O['SCROLL_Y'])==(wanted-1)*48+12
    passed()

# Exactly 64 scopes fit. Selection promotes; restoration alone does not change recency.
for promote in (False,True):
    m=Machine(); w,es=m.page_list(4,name='folder_page')
    for i in range(64):
        m.folder(f'/sd/{i}',w); m.call(); m.call()
    m.folder('/sd/0',w); assert m.selected(w)==2
    if promote: m.call()  # update and protect the oldest entry
    m.folder('/sd/64',w); m.call()
    for i,wanted in ((0,3),(2,2)) if promote else ((1,2),(63,2)):
        m.folder(f'/sd/{i}',w); assert m.selected(w)==wanted
    evicted=1 if promote else 0
    m.folder(f'/sd/{evicted}',w); assert m.selected(w)==0
    passed()

# A full history must not add work to steady painting or turns in the current scope.
costs=[]
for occupancy in (1,64):
    m=Machine(); w,es=m.page_list(20,height=192,extent=960,name='folder_page')
    for i in range(occupancy):
        m.folder(f'/sd/{i:02}',w); m.call()
    instructions=[0]
    def count_payload(*args): instructions[0]+=1
    hook=m.u.hook_add(UC_HOOK_CODE,count_payload,begin=BASE,end=SCRATCH-1)
    m.paint(w); paint_cost=instructions[0]; instructions[0]=0
    m.call(); costs.append((paint_cost,instructions[0]))
    m.u.hook_del(hook)
assert costs[0]==costs[1],costs
passed()

# Returning after another scope still applies text matching and stale-index rejection.
for shortened in (False,True):
    m=Machine(); m.folder('/sd/parent')
    w,es=m.page_list(8,name='folder_page'); m.label(es,[f'track {i}' for i in range(8)]); m.paint(w)
    for _ in range(6): m.call()
    m.folder('/sd/child')
    w,es=m.page_list(8,name='folder_page'); m.paint(w); m.call()
    m.folder('/sd/parent')
    w,es=m.page_list(3 if shortened else 8,name='folder_page'); m.label(es,[f'track {i}' for i in range(8)])
    if not shortened: m.nodes[es[3]]['text']='track 6'; m.nodes[es[6]]['text']='other'
    m.paint(w); assert m.selected(w)==(0 if shortened else 3)
    passed()

# An unavailable or unterminated path cannot supply a content identity.
for path in (b'\0',b'x'*1024):
    m=Machine(); m.u.mem_write(syms['g_folder_path'],path)
    w,es=m.page_list(8,name='folder_page'); m.paint(w); m.call(); m.call()
    w2,es2=m.page_list(8,name='folder_page'); m.paint(w2)
    assert m.selected(w2)==0; passed()

# Root collections keep selection after a detail page overwrites the shared query.
for cls in (O['CLASS_ALBUMS'],0xf004,0xf005,0xf006):
    for reuse in (False,True):
        m=Machine(); m.word(syms['g_class_type'],cls)
        w,rs,es=m.table_page(n=12,name='localclass_page',rebind=True)
        m.word(w+O['W_H'],288); m.paint(w)
        for _ in range(7): m.call()
        before=(m.selected(w),m.get(w+O['TABLE_TOP'])); assert before==(7,108)
        page=m.top; m.page('artistinfo_page')
        m.u.mem_write(syms['g_local_classinfo_save']+0x10d,b'Selected Artist\0')
        m.top=page
        if not reuse: w,rs,es=m.table_page(n=12,name='localclass_page',rebind=True)
        m.word(w+O['W_H'],288); m.paint(w)
        assert (m.selected(w),m.get(w+O['TABLE_TOP']))==before
        # Switching Artist / Album Artist still opens a separate collection.
        m.word(syms['g_artist_type'],1)
        w,rs,es=m.table_page(n=12,name='localclass_page',rebind=True); m.paint(w)
        assert m.selected(w)==0
        passed()

# Same-sized local lists must not inherit selection across query or browsing-mode changes.
for changed in ('g_class_type','g_local_classinfo_save','g_artist_type','album_modetype'):
    m=Machine(); w,rs,es=m.table_page(); m.paint(w); m.call(); m.call()
    w2,rs2,es2=m.table_page(); m.paint(w2); assert m.selected(w2)==2
    m.word(syms[changed],m.get(syms[changed])+1)
    w3,rs3,es3=m.table_page(); m.paint(w3); assert m.selected(w3)==0
    m.call(); m.call()
    m.word(syms[changed],m.get(syms[changed])+1)
    m.word(w3+O['TABLE_TOP'],0); m.paint(w3); assert m.selected(w3)==0
    passed()
# Each query can be revisited, including on a surface reused for other queries.
for reuse in (False,True):
    for changed in ('g_class_type','g_local_classinfo_save','g_artist_type','album_modetype'):
        m=Machine(); w,rs,es=m.table_page(n=20)
        for query,wanted in ((10,7),(20,4),(30,2)):
            m.word(syms[changed],query)
            if not reuse: w,rs,es=m.table_page(n=20)
            m.word(w+O['TABLE_TOP'],0); m.paint(w); assert m.selected(w)==0
            for _ in range(wanted): m.call()
        for query,wanted in ((10,7),(20,4),(30,2)):
            m.word(syms[changed],query)
            if not reuse: w,rs,es=m.table_page(n=20)
            m.word(w+O['TABLE_TOP'],0); m.paint(w)
            assert m.selected(w)==wanted and m.get(w+O['TABLE_TOP'])==(wanted-1)*48+12
        passed()
# Without an audited content identity, a recreated detail, dialog or network page starts fresh.
for name in ('netdiskfolder_page','tidal_albuminfo_page','playerqueue_page',
             'search_dialog','tidal_search_dialog'):
    m=Machine(); w,es=m.page_list(8,name=name); m.paint(w)
    for _ in range(3): m.call()
    m.paint(w); assert m.selected(w)==3
    w2,es2=m.page_list(8,name=name); m.paint(w2); assert m.selected(w2)==0
    passed()

# Search result dialogs navigate their list and centre opens the highlighted result.
for name in ('search_dialog','tidal_search_dialog'):
    m=Machine(); w,es=m.page_list(8,name=name)
    m.paint(w)
    for _ in range(2): assert m.call()==11
    assert m.selected(w)==2
    assert m.confirm()==11 and m.dispatched()[0][1]==es[2]
    passed()
# The search input dialogs have no navigable pane: they stay off the allowlist so the wheel
# keeps changing volume instead of being consumed by a window that cannot scroll.
for name in ('searchbox_dialog','tidal_searchbox_dialog'):
    m=Machine(); m.top=m.node('window',name,[m.node('view')])
    assert m.call()==0 and not m.moved(); passed()
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

for name in ('search_dialog','tidal_search_dialog','searchbox_dialog','tidal_searchbox_dialog'):
    for keyboard in (False,True):
        m=SearchMachine(name,keyboard)
        assert m.call(O['KEY_RETURN'],gap=0)==11 and m.nodes[m.edit]['focused']==0
        assert m.top==m.dialog and m.get(m.im+O['IM_WIDGET'])==0
        assert len([c for c in m.calls if c[0]=='search:close'])==1
        assert m.call(O['KEY_RETURN'],gap=0)==0 and not [c for c in m.calls if c[0]=='search:close']
        passed()
# Our T9 edits (peq_ui.c edit_create, EDIT_NAME): Return empties the box before the blur, so its
# EVT_VALUE_CHANGED is a cancel, then detaches the keyboard; stock's and the playlist search keep their text.
for name,text in (('q2_edit',''),('search_edit','query')):
    m=SearchMachine(); m.nodes[m.edit]['name']=name; m.nodes[m.edit]['text']='query'
    assert m.call(O['KEY_RETURN'],gap=0)==11 and m.nodes[m.edit]['focused']==0 and m.nodes[m.edit]['text']==text
    assert m.get(m.im+O['IM_WIDGET'])==0 and len([c for c in m.calls if c[0]=='search:close'])==1; passed()
# An idle input method, a different editor and unrelated keys leave the keyboard alone.
for case in ('idle','other','key','missing'):
    m=SearchMachine()
    if case=='idle': m.word(m.im+O['IM_WIDGET'],0)
    elif case=='other': m.nodes[m.edit]['name']='password'
    elif case=='missing': m.im=0
    key=O['KEY_NEXT'] if case=='key' else O['KEY_RETURN']
    assert m.call(key,gap=0)==0 and m.nodes[m.edit]['focused']==1
    assert not [c for c in m.calls if c[0]=='search:close']; passed()

# iPod: a confirm dialog's buttons are its rows (contexts.inc BUTTONS). The wheel moves between the
# side-by-side buttons without scrolling anything, the bar is the button's own tile, the ends are
# hard and Centre clicks the selected button. The Stock build leaves the dialog stock: the wheel is volume.
m=Machine(); d=m.node('dialog','confirminfo_dialog'); m.word(d+O['W_PARENT'],m.wm)
m.word(d+O['W_W'],375); m.word(d+O['W_H'],320); m.clip=(0,0,375,320); m.top=d
buttons=[m.entry(d,220) for _ in range(2)]; m.nodes[d]['children']=buttons
pair=(53,242) if variant=='ipod' else (56,240)  # a pair of tiles side by side, as Tidal's confirm keeps
for b,x in zip(buttons,pair): m.word(b+O['W_X'],x); m.word(b+O['W_W'],80); m.word(b+O['W_H'],80)
if variant=='ipod':
    m.paint(d); assert m.selected(d)==0 and m.sel()==(53,220,80,80)
    # The focused tile is framed in constant white (no accent lookup), over the bar; stroke color restored.
    frame=[r for r in m.rounded if r['kind']=='stroke']
    assert not m.strokes and [(r['rect'],r['radius'],r['color']) for r in frame]==[((53,220,80,80),O['SEL_RADIUS'],0xffffffff),((54,221,78,78),O['SEL_RADIUS']-1,0xffffffff)]
    assert m.lcd_colors()==LCD_COLORS
    assert m.call()==11 and m.selected(d)==1 and not m.moved()
    m.paint(d); assert m.sel()==(242,220,80,80) and m.clip==(0,0,375,320)
    assert not m.strokes and [r['rect'] for r in m.rounded if r['kind']=='stroke']==[(242,220,80,80),(243,221,78,78)]
    assert m.call()==11 and m.selected(d)==1 and not m.moved()
    assert m.call(O['KEY_PREV'])==11 and m.selected(d)==0
    assert m.confirm()==11 and m.dispatched()[0][1]==buttons[0]; passed()
    # A wide button (autoshutdown's Cancel) gets the full-width bar.
    m.nodes[d]['name']='autoshutdown_dialog'; m.nodes[d]['children']=[buttons[0]]; m.word(buttons[0]+O['W_W'],287)
    m.paint(d); assert m.sel()==(0,220,375,80) and not m.strokes; passed()
    # iPod's confirminfo_dialog.bin: Cancel and OK are full-width rows, so the bar is a list's, unframed.
    m=Machine(); d=m.node('dialog','confirminfo_dialog'); m.word(d+O['W_PARENT'],m.wm)
    m.word(d+O['W_W'],375); m.word(d+O['W_H'],320); m.clip=(0,0,375,320); m.top=d
    rows=[m.entry(d,208+48*i) for i in range(2)]; m.nodes[d]['children']=rows
    for r in rows: m.word(r+O['W_W'],375)
    m.paint(d); assert m.selected(d)==0 and m.sel()==(0,208,375,48) and not m.strokes
    assert m.call()==11 and m.selected(d)==1; m.paint(d); assert m.sel()==(0,256,375,48)
    assert m.confirm()==11 and m.clicks==[rows[1]]; passed()
    # A runtime prompt is styled once: stock's style setters invalidate even equal values.
    prompt=m.node('label','prompt'); m.word(prompt+O['W_H'],40)
    m.nodes[d]['children'].append(prompt)
    m.paint(d)
    assert tuple(m.get(prompt+O[k]) for k in ('W_X','W_Y','W_W','W_H'))==(0,0,375,O['CONFIRM_TOP'])
    assert m.nodes[prompt]['style:normal:font_size']==O['CONFIRM_PROMPT_PX']
    assert m.nodes[prompt]['style:normal:text_align_h']=='center' and m.nodes[prompt]['style:normal:text_align_v']=='middle'
    m.paint(d)
    assert not [c for c in m.calls if c[0].startswith('widget_set_prop') and c[1]==prompt]; passed()
else:
    assert m.call()==0 and m.call(O['KEY_CENTER'])==0 and not m.moved(); passed()

# USB mode's exit asks whether to scan before it leaves the mode: with the cable out, the prompt
# takes the wheel (iPod: its buttons); with the cable in, input stays stock's.
for cable in (0,1):
    m=Machine(); d=m.node('dialog','confirminfo_dialog',[]); m.word(d+O['W_PARENT'],m.wm); m.top=d
    m.nodes[d]['children']=[m.entry(d,220) for _ in range(2)]
    m.byte(syms['g_usblink_status'],2); m.byte(syms['g_usbdet_value'],cable)
    assert m.call()==(11 if not cable else 0); passed()

# The real input entry handles USB confirmation before native USB/volume routing.
m=Machine(); d=m.node('dialog','confirminfo_dialog',[]); m.word(d+O['W_PARENT'],m.wm); m.top=d
m.nodes[d]['children']=[m.entry(d,220) for _ in range(2)]
m.byte(syms['g_usblink_status'],2); m.byte(syms['g_usbdet_value'],0)
m.handlers[payload_syms['stock_input_trampoline']]='stock_input'
assert m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)==0
assert m.selected(d)==1 and not any(c[0]=='stock_input' for c in m.calls)
assert any(c[0]=='reset_poweroptions_timer' for c in m.calls)
m.call(O['KEY_NEXT'],address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),event_type=O['EVT_POINTER_DOWN'],gap=0)
assert any(c[0]=='stock_input' for c in m.calls)
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=1000)
assert m.selected(d)==1
m.byte(syms['g_usbdet_value'],1)
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
assert any(c[0]=='stock_input' for c in m.calls); passed()
# Transition-time volume applies once, without a queued overlay; fresh input stays native.
m=Machine(); m.top=m.node('window','playing_page'); m.animating=1
m.byte(syms['g_volume'],20); m.byte(syms['g_maxvolume'],21)
m.handlers[payload_syms['stock_input_trampoline']]='stock_input'
for key,want in ((O['KEY_NEXT'],21),(O['KEY_NEXT'],21),(O['KEY_PREV'],20)):
    m.call(key,address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
    assert m.u.mem_read(syms['g_volume'],1)[0]==want
    assert not any(c[0] in ('stock_input','navigator_to') for c in m.calls)
m.byte(syms['g_volume'],100); m.byte(syms['g_maxvolume'],200)
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
assert m.u.mem_read(syms['g_volume'],1)[0]==100
m.animating=0
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
assert any(c[0]=='stock_input' for c in m.calls) and not any(c[0]=='device_set_volume' for c in m.calls); passed()

# Slider settings: the wheel presses the page's own +/-, Centre goes back once the double-press
# window has passed, and a double Centre is stock's screen toggle instead.
for name in ('backlight_page','maxvol_page','bootvol_page','balance_page'):
    m=Machine(); add,dec=m.node('image','img_add'),m.node('image','img_dec')
    m.top=m.node('window',name,[m.node('view','view_body',[dec,add])]); m.mock('on_wm_keyup_fun')
    assert m.call()==11 and m.clicks==[add]
    assert m.call(O['KEY_PREV'])==11 and m.clicks==[add,dec]
    m.byte(syms['bt__recv_pageflag'],2)
    assert m.call()==11 and m.call(O['KEY_PREV'])==11 and m.clicks==[add,dec,add,dec]
    m.byte(syms['bt__recv_pageflag'],0)
    assert m.confirm()==11 and [c[0] for c in m.calls].count('navigator_back')==1
    assert m.call(O['KEY_CENTER'])==11 and m.call(O['KEY_CENTER'],gap=50)==11
    m.advance(300,clear=False); names=[c[0] for c in m.calls]
    assert 'on_wm_keyup_fun' in names and 'navigator_back' not in names; passed()
# Quick Settings: the wheel sets its brightness slider within its range; Centre and Return close it,
# as its swipe up does (dialog_quit), not stock's screen off and Home.
m=Machine(); bright=m.node('slider','slider_backlight',value=99)
m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])]); m.mock('dialog_quit')
assert m.call()==11 and m.nodes[bright]['value']==100
assert m.call()==11 and m.nodes[bright]['value']==100
assert m.call(O['KEY_PREV'])==11 and m.nodes[bright]['value']==99
for key in ('KEY_CENTER','KEY_RETURN')*(variant=='ipod'):  # Legacy keeps stock's keys
    assert m.call(O[key])==11 and [c[1:3] for c in m.calls if c[0]=='dialog_quit']==[(m.top,0)]
passed()
# A swipe down whose release the pull-down never got (stock state 1 or 2, no touch, no slide timer)
# leaves it on top off screen: the next key closes it untouched; a live drag or slide keeps it.
for state,pressed,timer,stuck in ((1,0,None,1),(2,0,-1,1),(3,0,None,0),(1,1,None,0),(1,0,7,0)):
    m=Machine(); bright=m.node('slider','slider_backlight',value=50)
    m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])],**({} if timer is None else {'timer_idex':timer}))
    m.mock('dialog_quit'); m.word(syms['g_dropdown_menu_state'],state); m.pressed=pressed
    down=m.call(address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)
    m.call()
    assert (down==11 and m.nodes[bright]['value']==50)==bool(stuck)
    assert [c[1:3] for c in m.calls if c[0]=='dialog_quit']==[(m.top,0)]*stuck, (state,pressed,timer)
passed()
# A held Play/Pause there switches its wheel to stock's volume and back, kept as Q2POD QSVOLUME.
m=Machine(); bright=m.node('slider','slider_backlight',value=50)
m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])])
hold=lambda: m.call(O['KEY_PLAY'],address=syms['on_wm_keylong_fun'],event_type=O['EVT_KEY_LONG'],gap=0)
saved=lambda: [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
assert hold()==11 and saved()==[(1,'Q2POD','QSVOLUME')]
assert m.call()!=11 and m.nodes[bright]['value']==50  # stock's volume takes the tick
assert hold()==11 and saved()==[(0,'Q2POD','QSVOLUME')]
assert m.call()==11 and m.nodes[bright]['value']==51
m=Machine(); m.config['QSVOLUME']='1'; bright=m.node('slider','slider_backlight',value=50)
m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])])
assert m.call()!=11 and m.nodes[bright]['value']==50; passed()
# A swipe up that closes it may start on the brightness slider: the slider gets its press only once the
# touch goes 10 px more sideways than up or down, or with a tap's release; a touch that goes up first,
# or any while stock slides the pull-down (g_dropdown_menu_state 1, 2), never reaches it, and a press
# it has is aborted. Withheld events return 0, not STOP, so the window manager still sees the touch.
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
D,M,U,A=O['EVT_POINTER_DOWN']-1,O['EVT_POINTER_MOVE_BEFORE']-1,O['EVT_POINTER_UP_BEFORE']-1,O['EVT_POINTER_ABORT']
assert slider_touch([('down',100,300),('move',103,290),('move',104,270),('move',140,250),('up',140,200)])==[(0,[])]*5  # a swipe up
assert slider_touch([('down',100,300),('move',105,301),('move',120,302),('up',150,302)])==[(0,[]),(0,[]),(0,[D,M]),(0,[U])]  # a drag
assert slider_touch([('down',100,300),('up',101,301)])==[(0,[]),(0,[D,U])]  # a tap
assert slider_touch([('down',100,300),('move',120,300),('up',120,250)],state=1)==[(0,[]),(0,[D,M]),(0,[A])]  # then stock slides it
assert slider_touch([('down',100,300),('up',100,300)],state=2)==[(0,[]),(0,[])]
assert slider_touch([('down',100,300),('move',103,280)],dialog='backlight_page')==[(0,[D]),(0,[M])]  # Settings' slider is stock's
passed()

if variant=='ipod':
    # Run stock's slider event handler with the packaged hit rectangle. Position arithmetic is
    # stock FP64 (unsupported by Unicorn); this checks capture and delivery, including outside drags.
    from ipod import decode,walk as ui_walk
    node=next(n for n in ui_walk(decode((B/'ui/playing_page.bin').read_bytes())) if n[2].get('name')=='slider_play')
    for x in (22,187,353):
      for y in (234,255,276):
       for end in (U,A):
        m=Machine(); slider=m.node('slider','slider_play'); parent=m.node('window','playing_page',[slider])
        m.word(slider+O['W_PARENT'],parent)
        for k,v in zip(('W_X','W_Y','W_W','W_H'),node[1]): m.word(slider+O[k],v)
        assert node[1][0]<=x<node[1][0]+node[1][2] and node[1][1]<=y<node[1][1]+node[1][3]
        m.word(slider+0x98,int(node[2]['bar_size'])); m.byte(slider+0xa6,node[2]['slide_with_bar']=='true')
        m.handlers[0x61a3f4]='stock_slider_preview'
        m.mock('widget_find_animator','widget_set_state','widget_grab','widget_ungrab')
        def pointer(typ,x,y):
            m.byte(m.event+0x24,1); m.word(m.event+O['EVENT_Y'],y)
            return m.call(x,address=0x61a934,args=(slider,m.event,0,0),event_type=typ,gap=0)
        assert pointer(D,x,y)==11 and bytes(m.u.mem_read(slider+0xac,1))==b'\x01'
        assert any(c[0]=='widget_grab' and c[1:3]==(parent,slider) for c in m.calls)
        pointer(M,400,210)  # drift well above and beyond the bar without losing the drag
        assert bytes(m.u.mem_read(slider+0xad,1))==b'\x01'
        assert any(c[0]=='stock_slider_preview' for c in m.calls)
        pointer(end,400,210)
        assert bytes(m.u.mem_read(slider+0xac,2))==b'\x00\x00'
        assert any(c[0]=='widget_ungrab' and c[1:3]==(parent,slider) for c in m.calls)
        passed()

# Set date and time: the wheel turns the outlined field, Centre moves on (to the time page after
# the day) and then to OK, which Centre presses; a turn on OK goes back to the fields.
def time_page(name):
    m=Machine(); names=['tselector_year','tselector_month','tselector_day','tselector_hour','tselector_min']
    if name=='sleepshutdown_page': names=names[3:]
    sels=[m.node('text_selector',n,options=12) for n in names]
    for w in sels: m.word(w+O['SELECTOR_INDEX'],5); m.word(w+O['W_W'],80); m.word(w+O['W_H'],170)
    ok=m.node('button','btn_enter'); slide=m.node('slide_view','slide_view',sels,value=0)
    m.top=m.node('window',name,[slide,ok]); return m,sels,ok,slide
m,sels,ok,slide=time_page('manualtime_page')
assert m.call()==11 and m.get(sels[0]+O['SELECTOR_INDEX'])==6
m.paint(sels[0]); assert not m.strokes and m.lcd_colors()==LCD_COLORS
assert [(r['kind'],r['rect'],r['radius'],r['color']) for r in m.rounded]==[('stroke',(0,0,80,170),O['SEL_RADIUS'],0xffffffff),('stroke',(1,1,78,168),O['SEL_RADIUS']-1,0xffffffff)]
m.rounded_fail=True; m.paint(sels[0]); assert [s[:4] for s in m.strokes]==[(0,0,80,170),(1,1,78,168)] and m.lcd_colors()==LCD_COLORS
m.rounded_fail=None; m.paint(sels[1]); assert not m.strokes and not m.rounded
for f in range(1,6): assert m.confirm()==11 and m.nodes[m.top]['_edit_field']==f
assert m.nodes[slide]['value']==1 and not m.clicks
# OK, when next, is a selected row: the off-white card with its label dark, not a frame.
m.word(ok+O['W_W'],287); m.word(ok+O['W_H'],40); m.nodes[ok]['text']='OK'; m.paint(ok)
assert not m.strokes and [(r['rect'],r['radius']) for r in m.rounded]==[((0,0,287,40),O['SEL_RADIUS'])]
assert [l['text'] for l in m.letters]==['OK'] and m.lcd_colors()==LCD_COLORS
backs=lambda: [c[0] for c in m.calls].count('navigator_back')
assert m.confirm()==11 and m.clicks==[ok] and backs()==1  # OK sets the time, then back to Time
assert m.call(O['KEY_PREV'])==11 and m.nodes[m.top]['_edit_field']==4
for _ in range(9): m.call()
assert m.get(sels[4]+O['SELECTOR_INDEX'])==11
m.nodes[slide]['value']=0; assert m.call(O['KEY_PREV'])==11 and m.get(sels[0]+O['SELECTOR_INDEX'])==5; passed()
# The side keys move between the fields and OK, stopping at the ends; a tap on OK goes back too.
m,sels,ok,slide=time_page('manualtime_page'); field=lambda: m.nodes[m.top].get('_edit_field',0)
assert m.call(O['KEY_BACK_BTN'])==11 and field()==0
for f in (1,2,3,4,5,5): assert m.call(O['KEY_FWD_BTN'])==11 and field()==f
assert m.nodes[slide]['value']==1 and not m.clicks
assert m.call(O['KEY_BACK_BTN'])==11 and field()==4 and m.call(O['KEY_BACK_BTN'])==11 and field()==3
assert not any(c[0] in ('media_key','stock_input') for c in m.calls)
m.call(address=HOOKS['widget_dispatch'][0],args=(ok,m.event,0,0),event_type=O['EVT_CLICK'],gap=0)
assert m.clicks==[ok] and [c[0] for c in m.calls].count('navigator_back')==1; passed()
m,sels,ok,slide=time_page('sleepshutdown_page')
assert m.call()==11 and m.get(sels[0]+O['SELECTOR_INDEX'])==6
assert m.call(O['KEY_FWD_BTN'])==11 and m.nodes[m.top]['_edit_field']==1 and m.call(O['KEY_BACK_BTN'])==11
assert m.confirm()==11 and m.confirm()==11 and m.confirm()==11 and m.clicks==[ok]
assert 'navigator_back' not in [c[0] for c in m.calls]; passed()  # the sleep timer's OK stays stock's

# A picker opens on its checked option, not the row last chosen there.
m=Machine(); w,es=m.page_list(4,name='playmode_page')
m.nodes[es[2]]['children']=[m.node('image',image='select')]
m.paint(w); assert m.selected(w)==2; passed()
# iPod Minimal: the selected row's white radio is drawn again dark on the off-white card: a ring,
# and its dot when checked. Other rows keep stock's.
if variant=='ipod':
    for row,image,fills in ((2,'select',3),(0,'unselect',0),(2,'more',4),(0,'more',0)):
        m=Machine(); w,es=m.page_list(4,name='playmode_page')
        radio=m.node('image',image=image); m.nodes[es[row]]['children']=[radio]
        if image=='more':
            m.nodes[radio]['name']='img_more0'
            if row==2: m.nodes[es[row]]['children'].append(m.node('image',image='select'))
        m.word(radio+O['W_PARENT'],es[row]); m.word(radio+O['W_W'],50 if image=='more' else 26); m.word(radio+O['W_H'],70)
        if row==0: m.nodes[es[2]]['children']=[m.node('image',image='select')]
        m.paint(w); m.paint(radio)
        expected=[(12,31,26,8),(14,33,4,4),(21,33,4,4),(28,33,4,4)] if image=='more' else [(0,22,26,26),(2,24,22,22),(7,29,12,12)]
        assert [r['rect'] for r in m.rounded]==expected[:fills] and m.lcd_colors()==LCD_COLORS, (image,m.rounded,m.lcd_colors())
        passed()

# iPod: a playlist's More page (Rename, Delete) is a BUTTONS page: the wheel moves between them.
if variant=='ipod':
    m=Machine(); top=m.node('window','playlistsmore_page'); m.word(top+O['W_PARENT'],m.wm); m.top=top
    m.word(top+O['W_W'],375); m.word(top+O['W_H'],290)
    rows=[m.node('list_item',children=[m.entry(0)]) for _ in range(2)]
    for i,r in enumerate(rows): m.word(r+O['W_Y'],10+i*78); m.word(m.nodes[r]['children'][0]+O['W_PARENT'],r); m.word(r+O['W_PARENT'],top)
    m.nodes[top]['children']=rows
    assert m.call()==11 and m.selected(top)==1 and m.call()==11 and m.selected(top)==1
    assert m.confirm()==11 and m.clicks==[m.nodes[rows[1]]['children'][0]]; passed()

# An interrupted recall glide keeps the remembered row instead of adopting a visible one.
m,w,es=walk(10,5); w2,es2=m.page_list(10,extent=1000); m.glide=False
assert m.touch()==0
m.paint(w2); assert m.selected(w2)==5
w3,es3=m.page_list(10,extent=1000)
assert m.paint(w3)==0 and m.selected(w3)==5; passed()
# A wheel detent after recreation interrupts recall and sets the offset immediately.
m,w,es=walk(10,5); w2,es2=m.page_list(10,extent=1000); m.glide=False
assert m.call()==11 and m.selected(w2)==6
assert m.moved()[-1][0]=='scroll_view_set_offset' and m.moved()[-1][3]==252; passed()

# A live list whose row count changes resets the selection without re-reading the table.
m,w,es=walk(10,5)
m.nodes[w]['children']=es[:6]; m.word(w+O['VIEW_CONTENT_H'],6*48); m.word(w+O['SCROLL_Y'],0)
m.paint(w); assert m.selected(w)==0; passed()

# Virtual music tables recall a logical row and scroll to it on recreation.
m=Machine(); w,_,entries=m.table_page()
m.paint(w)
for _ in range(2): assert m.call()==11
assert m.selected(w)==2 and m.get(w+O['TABLE_TOP'])==60
m.rebind=None
w2,_,entries2=m.table_page()
m.paint(w2); assert m.selected(w2)==2 and m.get(w2+O['TABLE_TOP'])==60
assert m.confirm()==11 and m.dispatched()[0][1]==entries2[2]; passed()

# Table recall survives interruption even when the target is outside the recycled row pool.
for wanted in (2,12):
    m=Machine(); w,rs,es=m.table_page(n=20); m.paint(w)
    for _ in range(wanted): m.call()
    w2,rs2,es2=m.table_page(); m.glide=False
    m.touch(); m.paint(w2)
    assert m.selected(w2)==wanted and m.moved()[-1][2]==(wanted-1)*48+12
    # Rebind the pool as the restarted glide completes.
    top=(wanted-1)*48+12
    m.word(w2+O['TABLE_TOP'],top); m.word(w2+O['TABLE_ANIMATOR'],0)
    m.bind(rs2,top)
    m.paint(w2); assert m.selected(w2)==wanted
    m.confirm(); assert m.dispatched()[0][1]==es2[1]
    passed()
# A wheel during recall advances the logical target; an explicit tap instead replaces it.
m=Machine(); w,rs,es=m.table_page(n=20); m.paint(w)
for _ in range(12): m.call()
w2,rs2,es2=m.table_page(rebind=True); m.glide=False
m.touch(); m.call(); assert m.selected(w2)==13
m.click(es2[0]); assert m.get(w2+O['TABLE_ANIMATOR'])==0
m.paint(w2)
assert m.selected(w2)==12
m.confirm(); assert m.dispatched()[0][1]==es2[0]; passed()

# A synchronous restore rebind must discard the pre-scroll pool before centre dispatch.
m=Machine(); w,rs,es=m.table_page(n=20); m.paint(w)
for _ in range(12): m.call()
w2,rs2,es2=m.table_page(rebind=True)
m.paint(w2); assert m.selected(w2)==12
m.confirm(); assert m.dispatched()[0][1]==es2[1]; passed()

# Home keeps its native carousel presentation and value (including touch changes).
m=Machine(); w=m.page('home_page','slide_menu'); m.word(w+O['SLIDE_INDEX'],1)
child=[m.entry(w),m.entry(w)]; m.nodes[w]['children']=child
assert m.paint(w)==0 and not m.drawn()
assert any(c[0]=='stock_paint' for c in m.calls)
assert m.confirm()==11 and m.dispatched()[0][1]==child[1]
m.word(w+O['SLIDE_INDEX'],0)
assert m.confirm()==11 and m.dispatched()[0][1]==child[0]; passed()
# Long-press/boot release must reach stock cleanup, never activate a menu item.
for addr in [syms['g_power_longkey'],syms['g_ingore_bootkey_flag'],O['BOOT_KEY_GUARD']]:
    m.byte(addr,1); assert m.confirm()==0 and not m.dispatched(); m.byte(addr,0); passed()
# A single release confirms exactly once at DOUBLE_CLICK_MS, never a millisecond before.
m=Machine(); w,es=m.page_list(3)
assert m.release()==11 and not m.clicks
m.advance(DC-1); assert not m.clicks
m.advance(1); assert m.clicks==[es[0]] and not m.timers and not m.screens
m.advance(1000); assert m.clicks==[es[0]]; passed()
# Stock's wheel lockout after the centre key ends when the press opens its row: the next tick moves.
m=Machine(); w,es=m.page_list(3)
assert m.release()==11 and m.u.mem_read(O['KEY_LOCKOUT'],1)==b'\x08'
m.advance(DC); assert m.clicks==[es[0]] and m.u.mem_read(O['KEY_LOCKOUT'],1)==b'\0'
assert m.call(gap=0,debounce=True)==11 and m.selected(w)==1; passed()
# Before the deadline, the second release cancels the click and executes stock screen-off.
for gap in (0,100,DC-1):
    m=Machine(); w,es=m.page_list(3)
    assert m.release()==11 and m.release(gap)==0
    assert m.screens==[0] and not m.u.mem_read(syms['g_backlight_status'],1)[0]
    m.advance(1000); assert not m.clicks and not m.timers
    # A release while off follows the real stock wake path, with no menu activation.
    assert m.release()==0 and m.screens==[0,1]
    assert m.u.mem_read(syms['g_backlight_status'],1)[0]==1 and not m.clicks; passed()
# A page still sliding in takes Centre, but a double press there still turns the screen off; a slow
# second press does not, and the first press alone never does.
m=Machine(); w,es=m.page_list(3); m.animating=1
assert m.release()==11 and not m.screens and m.release(DC-1)==11 and m.screens==[0]
m=Machine(); w,es=m.page_list(3); m.animating=1
assert m.release()==11 and m.release(DC)==11 and not m.screens; passed()
# At/after expiry, the first single has dispatched; the next release starts a new single.
for gap in (DC,DC+1):
    m=Machine(); w,es=m.page_list(3)
    assert m.release()==11 and m.release(gap)==11 and m.clicks==[es[0]]
    m.advance(300); assert m.clicks==[es[0],es[0]] and not m.screens; passed()
# Touch and either wheel direction cancel pending confirmation, including invalid navigation.
for action in ('touch','next','prev'):
    m=Machine(); w,es=m.page_list(3); m.release()
    if action=='touch': m.call(address=HOOKS['on_wm_tsdown_before_fun'][0],gap=100)
    else: m.call(O['KEY_NEXT'] if action=='next' else O['KEY_PREV'],gap=100)
    m.advance(1000); assert not m.clicks and not m.timers
    assert m.release()==11
    m.advance(300); assert len(m.clicks)==1 and not m.screens; passed()
# Changed, destroyed, or address-reused targets cannot receive a delayed click.
for change in ('top','surface','scope','selection','count','text','hidden','disabled',
               'animating','pressed','screen_off','locked','long','boot','reused_top','reused_surface'):
    m=Machine(); w,es=m.page_list(3,name='folder_page')
    m.folder('/first')
    m.nodes[es[0]]['text']='First'
    m.release()
    if change=='top': m.page_list(3,name='display_page')
    elif change=='surface': m.nodes[m.top]['children']=[m.node()]
    elif change=='scope': m.folder('/other')
    elif change=='selection': m.nodes[w]['_ringnav_index']=1
    elif change=='count': m.nodes[w]['children']=es[:2]
    elif change=='text': m.nodes[es[0]]['text']='Rebound'
    elif change=='hidden': m.nodes[w]['visible']=0
    elif change=='disabled': m.nodes[es[0]]['enable']=0
    elif change=='animating': m.animating=1
    elif change=='pressed': m.pressed=1
    elif change=='screen_off': m.byte(syms['g_backlight_status'],0)
    elif change=='locked': m.byte(syms['g_lockscreen_pageflag'],1)
    elif change=='long': m.byte(syms['g_power_longkey'],1)
    elif change=='boot': m.byte(O['BOOT_KEY_GUARD'],1)
    else: m.nodes[m.top if change=='reused_top' else w].pop('_ringnav_confirm')
    m.advance(300); assert not m.clicks and not m.timers,change; passed()
# Non-virtual rows cannot inherit confirmation merely by sharing an index and text.
for kind in ('scroll_view','slide_menu'):
    for text in ('','Same title'):
        for reused in (False,True):
            m=Machine(); w,es=m.page_list(3)
            m.nodes[w]['type']=kind
            if kind=='slide_menu': m.word(w+O['SLIDE_INDEX'],0)
            m.nodes[es[0]]['text']=text
            m.release()
            if reused:
                m.nodes[es[0]].pop('_ringnav_confirm',None)
            else:
                replacement=m.entry(w)
                m.nodes[replacement]['text']=text
                m.nodes[w]['children'][0]=replacement
            m.advance(300)
            assert not m.clicks and not m.timers,(kind,text,reused)
            passed()

# A recycled table pool resolves the original logical index from the live row mapping.
m=Machine(); w,rs,es=m.table_page(); m.release()
m.word(rs[0]+O['ROW_INDEX'],1); m.word(rs[1]+O['ROW_INDEX'],0)
m.advance(300); assert m.clicks==[es[1]]; passed()
m=Machine(); w,rs,es=m.table_page(); m.release(); m.bind(rs,480)
m.advance(300); assert not m.clicks; passed()
# Invalid state observed before expiry stays cancelled even if the same page returns.
m=Machine(); w,es=m.page_list(3); m.release(); m.animating=1
m.call(gap=100); m.animating=0; m.advance(300)
assert not m.clicks and not m.timers; passed()
# Timer allocation failure consumes both releases without activation; the double press still turns
# the screen off, as on any page that keeps Centre.
m=Machine(); w,es=m.page_list(3); m.timer_fail=True
assert m.release()==11 and m.release(100)==11
m.advance(1000); assert not m.clicks and m.screens==[0] and not m.timers; passed()
# Empty menus never arm confirmation; a double press there still turns the screen off.
m=Machine(); m.page()
assert m.release()==11 and m.release(100)==11
assert not m.timers and not m.clicks and m.screens==[0]; passed()
# Audit the stock creation path independently: live offset, duration, easing,
# completion address and deselection agree with the fields used by the patch.
m=Machine(); w=m.page('home_page','slide_menu')
m.call(address=0x5f3400,args=(w,-240,0,0),gap=0)
a=m.get(w+O['SLIDE_ANIMATOR'])
assert m.get(a+O['ANIM_DURATION'])==150 and signed(m.get(a+O['ANIM_X_TO']))==-240
assert m.slide_callbacks[a]==(syms['slide_menu_on_scroll_done'],w)
assert m.nodes[m.nodes[w]['children'][0]]['focused']==0
m.advance(150); assert m.get(w+O['SLIDE_INDEX'])==1 and not m.slides; passed()

# Execute native parameter writes and stock completion; only the scheduler is
# deterministic. Check live origin, destination, duration, focus and one active animator.
def slide(m,w):
    a=m.get(w+O['SLIDE_ANIMATOR'])
    assert a and list(m.slides)==[a]
    return a,signed(m.get(a+0x68)),signed(m.get(a+O['ANIM_X_TO'])),m.get(a+O['ANIM_DURATION'])

for start in (0,0xfffffff0):
    for key,direction in ((O['KEY_NEXT'],1),(O['KEY_PREV'],-1)):
        for count in (3,4,5):
            m=Machine(); m.now=start; w=m.page('home_page','slide_menu')
            assert m.call(key,gap=0)==11
            a,origin,goal,duration=slide(m,w)
            assert (origin,goal,duration)==(0,-direction*240,200)
            for i in range(1,count):
                m.advance(30); live=signed(m.get(w+O['SLIDE_OFFSET']))
                assert m.call(key,gap=0)==11
                assert slide(m,w)==(a,live,-direction*240*(i+1),120)
            m.advance(120)
            assert not m.slides and m.get(w+O['SLIDE_ANIMATOR'])==0
            assert m.get(w+O['SLIDE_OFFSET'])==0
            assert m.get(w+O['SLIDE_INDEX'])==(direction*count)%7
            assert m.nodes[m.nodes[w]['children'][(direction*count)%7]]['focused']==1
            m.advance(126); m.call(key,gap=0)
            assert slide(m,w)[3]==200
            m.advance(200); settled=m.get(w+O['SLIDE_INDEX']); m.advance(500)
            assert m.get(w+O['SLIDE_INDEX'])==settled and not m.slides
            passed()
# Reversal at either speed starts at the live position and actually travels backward,
# even when multiple intended destinations have accumulated.
for fast in (False,True):
    for key,reverse,sign in ((O['KEY_NEXT'],O['KEY_PREV'],1),(O['KEY_PREV'],O['KEY_NEXT'],-1)):
        m=Machine(); w=m.page('home_page','slide_menu'); m.call(key,gap=0)
        if fast:
            for _ in range(3): m.call(key,gap=20)
        m.advance(30); live=signed(m.get(w+O['SLIDE_OFFSET']))
        a=m.get(w+O['SLIDE_ANIMATOR']); m.call(reverse,gap=0)
        active,origin,goal,duration=slide(m,w)
        assert active==a and origin==live and duration==200 and (goal-live)*sign>0
        m.advance(200)
        assert m.get(w+O['SLIDE_INDEX'])==(-goal//240)%7 and not m.slides
        assert m.nodes[m.nodes[w]['children'][(-goal//240)%7]]['focused']==1
        passed()
# Exact fast-window boundary, stock rejection, and interaction resets.
for gap,want in ((200,120),(201,200)):
    m=Machine(); w=m.page('home_page','slide_menu'); m.call(gap=0); m.call(gap=gap)
    assert slide(m,w)[3]==want; passed()
for action in ('touch','center','click','leave','animating','pressed','unusable','missing'):
    m=Machine(); w=m.page('home_page','slide_menu'); home=m.top; m.call(gap=0)
    if action=='touch': m.call(address=HOOKS['on_wm_tsdown_before_fun'][0],gap=20)
    elif action=='center': m.release(20)
    elif action=='click': m.click(m.nodes[w]['children'][0],gap=20)
    elif action=='leave': m.page('playing_page'); m.call(gap=20); m.top=home
    elif action=='unusable':
        m.byte(syms['g_backlight_status'],0); m.call(gap=20); m.byte(syms['g_backlight_status'],1)
    elif action=='missing': m.call(args=(m.wm,0,0,0),gap=20)
    else: setattr(m,action,1); m.call(gap=20); setattr(m,action,0)
    assert m.call(gap=1)==11 and slide(m,w)[3]==200; passed()
# Stock's wheel lockout after Centre guards only its first LOCKOUT_KEEP x 100 ms, then clears.
m=Machine(); w=m.page('home_page','slide_menu'); m.call(gap=0)
m.byte(0xa37c89,8); assert m.call(O['KEY_PREV'],gap=20,debounce=True)==11
assert not m.moved(); m.byte(0xa37c89,0); m.call(gap=20); assert slide(m,w)[3]==120; passed()
m=Machine(); w=m.page('home_page','slide_menu'); m.call(gap=0)
m.byte(0xa37c89,8-O['LOCKOUT_KEEP']); m.call(gap=20)
assert m.u.mem_read(O['KEY_LOCKOUT'],1)==b'\0' and slide(m,w)[3]==120; passed()
# Animator and callback allocation failures settle through stock selection.
for failure in ('slide_fail','slide_on_fail'):
    m=Machine(); w=m.page('home_page','slide_menu'); setattr(m,failure,True)
    m.call(gap=0)
    assert m.get(w+O['SLIDE_INDEX'])==1 and not m.get(w+O['SLIDE_ANIMATOR'])
    assert not m.slides and not m.slide_callbacks
    assert m.nodes[m.nodes[w]['children'][1]]['focused']==1
    setattr(m,failure,False); m.call(gap=20); assert slide(m,w)[3]==200; passed()
# Centre arms the intended final icon while its slide is still unfinished.
m=Machine(); w=m.page('home_page','slide_menu')
for _ in range(5): m.call(gap=20)
m.release(0); m.advance(75); m.advance(O['DOUBLE_CLICK_MS']-75)
assert m.clicks==[m.nodes[w]['children'][5]] and not m.slides, (m.clicks,m.get(w+O['SLIDE_INDEX']),m.timers,m.slides); passed()
m=Machine(); w=m.page('home_page','slide_menu'); m.release(0); m.call(gap=20)
m.advance(300); assert not m.clicks; passed()
# Other slide menus retain stock next/previous handling.
m=Machine(); m.page('sysset_page','slide_menu')
assert m.call(gap=0)==11 and m.call(gap=1)==11 and m.moved(); passed()
# Even if the UI services a release before an overdue timer, both singles confirm once.
m=Machine(); w,es=m.page_list(3); m.release(); m.now+=O['DOUBLE_CLICK_MS']+1
assert m.release()==11 and m.clicks==[es[0]] and len(m.timers)==1
m.advance(300); assert m.clicks==[es[0],es[0]] and not m.screens; passed()
# Deadline expiry may navigate: the following release must resolve the new menu.
m=Machine(); w,es=m.page_list(3)
def navigate(a,b):
    m.on_click=None
    m.page_list(3,name='display_page')
m.on_click=navigate; m.release(); old=m.top
assert m.release(O['DOUBLE_CLICK_MS']+1)==11 and m.top!=old and len(m.clicks)==1
m.advance(300); assert len(m.clicks)==2 and m.clicks[0]!=m.clicks[1] and not m.screens; passed()
# The downstream stock cleanup still owns long-press and boot-key releases.
for addr in (syms['g_power_longkey'],syms['g_ingore_bootkey_flag'],O['BOOT_KEY_GUARD']):
    m=Machine(); m.page_list(3); m.byte(addr,1)
    assert m.release()==0
    m.advance(300)
    assert not m.clicks and not m.screens and not m.timers and m.u.mem_read(addr,1)==b'\0'; passed()
# Play/Pause reaches the real stock downstream handler, including on a supported page.
m=Machine(); m.page_list(3)
assert m.call(O['KEY_PLAY'])==0
m.call(O['KEY_PLAY'],address=syms['on_wm_keyup_fun'],gap=0)
assert any(c[0]=='playpause_quick_click' for c in m.calls) and not m.clicks; passed()
# A root-window paint observes leaving navigation even when the new page has no pane.
m=Machine(); w,es=m.page_list(3); m.release(); old=m.top
m.top=m.node('window','playing_page'); m.paint(m.top,gap=100)
m.top=old; m.advance(300); assert not m.clicks and not m.timers; passed()
m=Machine(); w=m.page('home_page','slide_menu'); home=m.top; m.call(gap=0)
m.top=m.node('window','playing_page'); m.paint(m.top,gap=20)
m.top=home; assert m.call(gap=1)==11 and m.moved(); passed()
# Local query and home selection changes invalidate pending confirmation too.
m=Machine(); w,rs,es=m.table_page(); m.release()
m.word(syms['g_class_type'],0xf002); m.advance(300); assert not m.clicks; passed()
m=Machine(); w=m.page('home_page','slide_menu')
m.word(w+O['SLIDE_INDEX'],0); m.nodes[w]['children']=[m.entry(w),m.entry(w)]
m.release(); m.word(w+O['SLIDE_INDEX'],1); m.advance(300); assert not m.clicks; passed()
# Unsigned milliseconds may wrap while confirmation is pending.
m=Machine(); m.now=0xfffffff0; m.page_list(3)
assert m.release()==11 and m.release(DC-1)==0
m.advance(300); assert not m.clicks and m.screens==[0]; passed()
# Short lists stay precise; virtual tables use total logical rows, not their row pool.
for table in (False,True):
    for count in (0,1,16,17):
        m=Machine()
        if table:
            # A dynamic page keeps hard ends, so this stays an acceleration check.
            w,rs,es=m.table_page(n=min(count,4),name='playerqueue_page',rebind=True)
            m.word(w+O['TABLE_ROWS'],count)
        else: w,es=m.page_list(count,extent=count*48)
        m.paint(w)
        if count==17:
            # Seventeen rows accelerate even at a 50ms cadence: the second tick is still one
            # row (50ms of spin), the third crosses 100ms and steps two. iPod's row lists wait
            # for 300ms of spin: six one-row ticks, then the seventh steps two.
            for want in (1,2,4) if variant=='stock' else (1,2,3,4,5,6,8):
                assert m.call(gap=50)==11 and m.selected(w)==want
            for _ in range(20): m.call(gap=50)
            assert m.selected(w)==count-1      # held at the end
            assert m.call(O['KEY_PREV'],gap=50)==11 and m.selected(w)==count-2 # ramp resets there
        else:
            for want in (1,2,3):
                assert m.call(gap=50)==11 and m.selected(w)==min(want,count-1)
        if count==16:
            for want in range(4,16):
                assert m.call(gap=50)==11 and m.selected(w)==want
            for want in range(14,-1,-1):
                assert m.call(O['KEY_PREV'],gap=50)==11 and m.selected(w)==want
            assert m.call(gap=50)==11 and m.selected(w)==1 # and at the start
        passed()

# Resizing across the short-list boundary cannot carry a previous fast run with it.
m=Machine(); w,es=m.page_list(17,height=960,extent=17*48)
m.nodes[w]['children']=es; m.paint(w)
for want in (1,3,6) if variant=='stock' else (1,2,3,5,7):  # iPod: two rows from 300ms of spin
    assert m.call(gap=100)==11 and m.selected(w)==want
m.nodes[w]['children']=es[:16]; m.paint(w)
assert m.call(gap=100)==11 and m.selected(w)==1   # fresh one-row run on the short list
for want in (2,3):
    assert m.call(gap=100)==11 and m.selected(w)==want
m.nodes[w]['children']=es; m.paint(w)
assert m.call(gap=100)==11 and m.selected(w)==1   # and again after growing back
for want in (3,) if variant=='stock' else (2,3,5):
    assert m.call(gap=100)==11 and m.selected(w)==want
passed()

# Sustained ticks ramp one row per 100ms of same-direction spin up to eight rows, then hold;
# pauses, spacing past the 140ms window and reversal reset. Byte-exact boundaries, wrapped clock.
# iPod's row lists ramp gently (navigation.c LIST_FIRST_MS, LIST_RAMP_MS): one row until 300ms of
# spin, two from there and one more per further 200ms, eight from 1500ms. IPOD_SPIN is (gap since
# the last tick, rows per step then); every gap is inside the 140ms window, so the run is one.
IPOD_SPIN=((0,1),(100,1),(100,1),(99,1),(1,2),(100,2),(99,2),(1,3),(100,3),(99,3),(1,4),(100,4),(99,4),
           (1,5),(100,5),(99,5),(1,6),(100,6),(99,6),(1,7),(100,7),(99,7),(1,8),(100,8))
for table in (False,True):
    for start in (0,0xfffffff0):
        m=Machine(); m.now=start
        w,rs=m.list_surface(table,n=500)
        if table: m.word(w+O['TABLE_ROWS'],500)
        # 99ms of spin is still one row; the next millisecond enters two-row speed, and each
        # further 100ms adds one row until the eight-row ceiling holds.
        seq=((0,1),(99,2),(1,4),(99,6),(1,9),(99,12),(1,16),(99,20),(1,25),(99,30),(1,36),
             (99,42),(1,49),(99,56),(1,64),(99,72),(1,80))
        if variant=='ipod':  # 299/300, 499/500 ... 1499/1500ms
            seq=[(gap,sum(g for _,g in IPOD_SPIN[:i+1])) for i,(gap,_) in enumerate(IPOD_SPIN)]
        for gap,want in seq:
            assert m.call(gap=gap)==11 and m.selected(w)==want
        # 140ms still counts as spinning, 141ms breaks the run and drops back to one row.
        assert m.call(gap=140)==11 and m.selected(w)==want+8
        assert m.call(gap=141)==11 and m.selected(w)==want+9
        assert m.call(O['KEY_PREV'],gap=1)==11 and m.selected(w)==want+8
        passed()
# Pixel-scroll fallback keeps the 100ms ramp in both variants, and immediate offsets.
m=Machine(); w=m.page(t='table_client')
for want in (48,144,288,480,720,1008,1344,1728):
    assert m.call(gap=100)==11 and m.get(w+O['TABLE_TOP'])==want
assert m.call(gap=141)==11 and m.get(w+O['TABLE_TOP'])==1776; passed()
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
    elif change=='rejected': m.byte(0xa37c89,8); assert m.call(gap=10,debounce=True)==11
    elif change=='touch': m.call(address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'],gap=10)
    elif change=='click': m.click(m.node('button'),gap=10)
    elif change=='missing': m.call(args=(0,0,0,0),gap=10)
    elif change=='centre': m.call(O['KEY_CENTER'],gap=10)
    elif change=='return': assert m.call(O['KEY_RETURN'],gap=10)==0
    elif change=='play': assert m.call(O['KEY_PLAY'],gap=10)==0
    elif change=='long': long_return(m)
    elif change=='reverse': assert m.call(O['KEY_PREV'],gap=10)==11 and m.selected(w)==1
    return w
# A fast spin belongs to its live menu, pane and browsing scope.
for change in ('window','pane','scope','context','count','gesture','screen','unsupported','touch','click','missing','centre'):
    m=Machine(); w,es=m.page_list(40,extent=40*48,
        name='sysset_page' if change=='context' else 'allmusic_page')
    for _ in range(7): m.call(gap=100)
    assert m.selected(w)==(28 if variant=='stock' else 13)  # iPod: 1,1,1,2,2,3,3 rows
    w=disturb(m,w,change,40)
    m.paint(w,gap=0)
    before=m.selected(w)
    assert m.call(gap=50)==11 and m.selected(w)==before+1,change
    passed()
# A tick while a page slides is held and plays into it once the slide ends, leaving no timer;
# Centre or Return ends a held turn, and a held turn expires after HOLD_MS.
m=Machine(); w,es=m.page_list(20,extent=960,name='allmusic_page'); m.paint(w)
assert m.call(gap=1000)==11 and m.selected(w)==1
m.animating=1; assert m.call(gap=1000)==11 and m.call(gap=20)==11 and m.selected(w)==1
m.advance(O['HOLD_POLL_MS']*3); assert m.selected(w)==1
m.animating=0; m.advance(O['HOLD_POLL_MS']); assert m.selected(w)==2
m.advance(O['HOLD_POLL_MS']); assert m.selected(w)==3 and not m.timers; passed()
for key in ('KEY_RETURN','KEY_CENTER',None):
    m=Machine(); w,es=m.page_list(20,extent=960,name='allmusic_page'); m.paint(w)
    assert m.call(gap=1000)==11 and m.selected(w)==1
    m.animating=1; m.call(gap=1000)
    if key: m.call(O[key],gap=0)
    else: m.advance(O['HOLD_MS']+O['HOLD_POLL_MS'])
    m.animating=0; m.advance(1000); assert m.selected(w)==1 and not m.timers,key; passed()
# Rejected stock wheel input resets a sustained list run.
m=Machine(); w,es=m.page_list(40,extent=40*48)
for _ in range(7): m.call(gap=100)
fast=28 if variant=='stock' else 13
assert m.selected(w)==fast
m.byte(0xa37c89,8)
assert m.call(gap=10,debounce=True)==11 and m.selected(w)==fast
assert m.call(gap=40)==11 and m.selected(w)==fast+1; passed()
# A click on a clickable child selects its collected ancestor, not a stale row.
m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
row1=m.entry(w,0); row2=m.entry(w,96); deep=m.entry(row1,0)
m.nodes[row1]['children']=[deep]; m.nodes[w]['children']=[row1,row2]
m.paint(w); assert m.selected(w)==0
assert m.call(O['KEY_NEXT'])==11 and m.selected(w)==1
m.click(deep); assert m.selected(w)==0
m.click(m.node('button')); assert m.selected(w)==0; passed()
# Real canvas ABI, translation and clip code execute; only the LCD rectangle sink is mocked.
# A 20px row at canvas origin (7,20) under a (10,30)-(229,199) clip; iPod in Classic, whose bar is bands.
m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
if variant=='ipod': m.config.update(THEME='1',ROUNDED='1')
e=m.entry(w,0); m.word(e+O['W_H'],20); m.nodes[w]['children']=[e]
sink='lcd_stroke_rect' if variant=='stock' else 'lcd_fill_rect'
for name in ('canvas_get_clip_rect','canvas_set_clip_rect',
             *(('canvas_set_stroke_color','canvas_stroke_rect') if variant=='stock' else ('canvas_fill_rect',))):
    del m.handlers[syms[name]]
m.mock(sink)
m.word(m.lcd+0x3c,1); m.word(m.lcd+0xb0,240); m.word(m.lcd+0xb4,240)
m.word(m.canvas+O['CANVAS_X'],7); m.word(m.canvas+O['CANVAS_Y'],20)
for off,val in [(0x10,10),(0x14,30),(0x18,229),(0x1c,199)]: m.word(m.canvas+off,val)
m.paint(w)
if variant=='stock':
    # The square fallback (radius 9 needs more height) runs the stock square code.
    assert not m.rounded and [s[:4] for s in m.strokes]==[(8,21,238,18),(9,22,236,16)]
    assert m.strokes[0][4:]==((10,30,220,86),SHADE)
    assert m.strokes[1][4:]==((10,30,220,86),OUTLINE)
else:
    # The bands reach the LCD only for the row pixels inside the clip.
    want=[]
    for band_y in range(10,20):
        n,frac=chord(band_y,20)
        want.append((7+O['SEL_INSET']+n,20+band_y))
        if frac: want.extend([(7+O['SEL_INSET']+n-1,20+band_y),(7+240-O['SEL_INSET']-n,20+band_y)])
    want=[(x,y) for x,y in want if 10<=x<=229]
    assert [(c[2],c[3]) for c in m.calls if c[0]==sink]==want
assert [m.get(m.canvas+off) for off in (0x10,0x14,0x18,0x1c)]==[10,30,229,199]
assert m.lcd_colors()==LCD_COLORS; passed()
# Wheel selection is immediate even when restoration animations would still be running.
m=Machine(); w,es=m.page_list(6)
m.paint(w); m.glide=False
m.call(); m.call(); assert m.selected(w)==2 and m.get(w+O['SCROLL_Y'])==60
m.paint(w); assert m.selected(w)==2
assert m.confirm()==11 and m.dispatched()[0][1]==es[2]
m.call(); assert m.selected(w)==3
m.call(O['KEY_PREV']); assert m.selected(w)==2
m.call(O['KEY_PREV']); assert m.selected(w)==1 and m.moved()[-1][3]==36
m.call(O['KEY_PREV']); assert m.selected(w)==0 and m.get(w+O['VIEW_ANIMATOR'])==0; passed()
# Empty menus never activate or turn off the screen; touch doesn't swallow its first event.
m=Machine(); w=m.page(); assert m.confirm()==11 and not m.dispatched()
assert m.touch()==0 and not m.dispatched(); passed()
# Clip an oversized target to its surface without losing its selection.
m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
e=m.entry(w); m.word(e+O['W_H'],140); m.nodes[w]['children']=[e]
m.paint(w)
assert m.selected(w)==0 and m.sel()==(0,0,240,140)
assert {r['clip'] for r in m.rounded}|{b[5] for b in m.bands}=={(0,0,240,96)}; passed()
# Both list kinds leave breathing room, shrink it in tight viewports, and clamp at either end.
for virtual in (False,True):
    for height,margin in ((96,12),(60,6),(49,0),(48,0),(40,0)):
        m=Machine()
        if virtual:
            w,rs,es=m.table_page(n=10,rebind=True); m.word(w+O['TABLE_ROWS'],10); off=O['TABLE_TOP']
        else:
            w,es=m.page_list(10,extent=480); off=O['SCROLL_Y']
        m.word(w+O['W_H'],height); m.paint(w)
        assert m.get(w+off)==0 and not m.moved()
        for _ in range(3): m.call()
        assert m.selected(w)==3
        assert m.get(w+off)==(144 if height<48 else 192-height+margin)
        m.call(O['KEY_PREV'])
        assert m.selected(w)==2 and m.get(w+off)==96-margin
        m.call(O['KEY_PREV']); m.call(O['KEY_PREV'])
        assert m.selected(w)==0 and m.get(w+off)==0
        for _ in range(9): m.call()
        assert m.selected(w)==9 and m.get(w+off)==(432 if height<48 else 480-height)
        m.paint(w); assert not m.moved(); passed()
# Entering a tall row from either direction reveals its title, including after recreation.
m=Machine(); w,es=m.page_list(3,height=96,extent=400)
m.word(es[1]+O['W_H'],140); m.word(es[2]+O['W_Y'],188)
m.call(); assert m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==48
m.call(); m.call(O['KEY_PREV'])
assert m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==48
w,es=m.page_list(3,height=96,extent=400)
m.word(es[1]+O['W_H'],140); m.word(es[2]+O['W_Y'],188)
m.paint(w); assert m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==48; passed()

# Small rows keep the square shade-plus-white outline; the rounded path starts only when both
# outer dimensions exceed 2*RADIUS. Tiny rows are skipped outright, never with negative sizes.
for ww,hh in [(240,20),(20,48),(6,6),(21,21)] if variant=='stock' else ():
    m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
    e=m.entry(w,0); m.word(e+O['W_W'],ww); m.word(e+O['W_H'],hh); m.nodes[w]['children']=[e]
    assert m.paint(w)==0
    if ww>2*O['RADIUS']+2 and hh>2*O['RADIUS']+2:
        assert [r['kind'] for r in m.rounded]==['fill','stroke','stroke'] and not m.strokes,(ww,hh,m.rounded,m.strokes)
    else:
        assert not m.rounded and [s[:4] for s in m.strokes]==[(1,1,ww-2,hh-2),(2,2,ww-4,hh-4)]
        assert [s[5] for s in m.strokes]==[SHADE,OUTLINE]
    assert m.global_alpha==0 and m.clip==(0,0,240,240)
    assert m.get(m.lcd+O['LCD_STROKE_COLOR'])==0x12345678; passed()
m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
e=m.entry(w,0); m.word(e+O['W_W'],4); m.word(e+O['W_H'],4); m.nodes[w]['children']=[e]
assert m.paint(w)==0 and not m.drawn() and m.clip==(0,0,240,240); passed()
# A page with no entries paints nothing and leaves every saved property alone.
m=Machine(); w=m.page()
assert m.paint(w)==0 and m.selected(w)==-1
assert not m.drawn() and m.global_alpha==0 and m.clip==(0,0,240,240)
assert m.lcd_colors()==LCD_COLORS; passed()

# Every saved value is unusual: the rounded path restores fill, stroke and clip exactly and
# never touches the canvas or LCD global-alpha bytes.
m=Machine(); w,es=m.page_list(3,extent=1000)
m.word(m.lcd+O['LCD_FILL_COLOR'],0x00000001); m.word(m.lcd+O['LCD_STROKE_COLOR'],0x80ffffff)
m.byte(m.canvas+0x0e,0x7f); m.byte(m.lcd+0xe4,0x11)
m.paint(w)
assert m.get(m.lcd+O['LCD_FILL_COLOR'])==0x00000001 and m.get(m.lcd+O['LCD_STROKE_COLOR'])==0x80ffffff
assert m.u.mem_read(m.canvas+0x0e,1)[0]==0x7f and m.u.mem_read(m.lcd+0xe4,1)[0]==0x11
assert m.global_alpha==0 and m.clip==(0,0,240,240); passed()

# Taps choose their pane even with no initial owner or with both panes previously selected.
for seeded in ((),(0,),(0,1)):
    m=Machine(); a,b,ae,be=m.panes()
    nested=m.entry(be[1]); m.nodes[be[1]]['children']=[nested]
    for i in seeded: m.nodes[(a,b)[i]]['_ringnav_index']=0
    m.touch(); m.click(nested)
    assert m.selected(a)==-1 and m.selected(b)==1
    m.paint(a); assert not m.drawn()
    m.paint(b); assert not m.drawn()
    m.confirm(); assert m.dispatched()[0][1]==be[1]
    m.call(); assert m.selected(b)==2
    m.touch(); m.click(ae[0]); m.confirm()
    assert m.selected(b)==-1 and m.dispatched()[0][1]==ae[0]
    passed()

# An inactive pane is never painted, so the outline cannot appear on two panes at once.
m=Machine(); a,b,_,_=m.panes(96); m.nodes[a]['_ringnav_index']=0
m.paint(b); assert not m.drawn()
m.paint(a); assert m.sel()==(0,0,240,48); passed()

# A canvas backend that declines the rounded stroke keeps the outline via the square fallback.
if variant=='stock':
    m=Machine(); w,es=m.page_list(3,extent=1000); m.rounded_fail=True
    m.paint(w)
    assert [r['kind'] for r in m.rounded]==['fill','stroke'] and [s[:4] for s in m.strokes]==[(1,1,238,46),(2,2,236,44)]
    assert m.rounded[1]['color']==SHADE
    assert [s[5] for s in m.strokes]==[SHADE,OUTLINE]
    assert m.lcd_colors()==LCD_COLORS
    assert m.global_alpha==0 and m.clip==(0,0,240,240); passed()

# The real stock rounded wrappers run with canvas services mocked. This firmware declines with
# RET_FAIL when the canvas has no vgcanvas, and changes no fill/stroke color or alpha byte.
m=Machine(); m.probe_canvas(rect=(1,1,238,46))
assert m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(O['RADIUS'],))==2
assert m.call(address=syms['canvas_stroke_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(O['RADIUS'],1))==2
assert m.lcd_colors()==LCD_COLORS
assert m.global_alpha==0 and not m.vg_calls; passed()

# With a vgcanvas present the same wrappers take the AGGE-facing path the Q2 firmware links:
# the color pointer and the radius/border-width stack slots reach the backend, canvas colors and
# the canvas alpha byte stay put. (The fill call then enters stock FP64 dither math that Unicorn's
# MIPS32 FR=0 FPU cannot execute; its vgcanvas calls up to that point are still asserted.)
m=Machine(); m.probe_canvas(vg=0x1000400,color=0xffffffff)
assert m.call(address=syms['canvas_stroke_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(O['RADIUS'],2))==0
names=[c[0] for c in m.vg_calls]
assert 'vgcanvas_set_stroke_color' in names and 'vgcanvas_set_line_width' in names
assert [c[2]&0xffffffff for c in m.vg_calls if c[0]=='vgcanvas_set_stroke_color']==[0xffffffff]
assert [struct.unpack('<f',struct.pack('<I',c[2]&0xffffffff))[0] for c in m.vg_calls if c[0]=='vgcanvas_set_line_width']==[2.0]
assert m.lcd_colors()==LCD_COLORS
assert m.global_alpha==0 and m.u.mem_read(m.canvas+0x0e,1)[0]==0; passed()
m=Machine(); m.probe_canvas(vg=0x1000400)
try: m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(O['RADIUS'],))
except UcError: pass
assert [c[2] for c in m.vg_calls if c[0]=='vgcanvas_set_fill_color']==[FILL]
assert m.get(m.lcd+O['LCD_FILL_COLOR'])==0x9abcdef0 and m.global_alpha==0
assert not m.allocs; passed()

# CPU-LCD branch: the stock fill has a real rasterizer that Unicorn can run end to end. With a
# non-color LCD type it fills the rounded rect through LCD sinks, frees every allocation, and
# declines radius <= 2 exactly as the stock code does.
m=Machine(); m.probe_canvas(lcd_type=0)
m.mock('lcd_fill_rect','lcd_draw_hline','lcd_draw_vline',prefix='sink:')
assert m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(O['RADIUS'],))==0
ops=[c for c in m.calls if c[0].startswith('sink:')]
assert ops and all(10<=signed(c[2])<=109 and 10<=signed(c[3])<=49 for c in ops)
assert not m.allocs
assert m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(2,))==2
assert not [c for c in m.calls if c[0].startswith('sink:')]; passed()
# The actual stock filter runs first, including each screen-off lock mode.
for backlight in [0,1]:
    for mode in range(4):
        for key in [170,O['KEY_PLAY'],O['KEY_PREV'],O['KEY_NEXT'],O['KEY_CENTER'],222,223,42]:
            results=[]
            for patched in [False,True]:
                m=Machine(patched); m.page('playing_page')
                m.byte(syms['g_backlight_status'],backlight)
                m.byte(syms['g_keylock_flag'],1); m.byte(syms['g_keylock_mode'],mode)
                results.append(m.call(key))
            assert results[0]==results[1],(backlight,mode,key,results)
            passed()
# Non-ring keys on supported pages must pass through unchanged.
for key in [0,13,170,O['KEY_PLAY'],222,223,0xffffffff]:
    m=Machine(); m.page(); assert m.call(key)==0 and not m.moved(); passed()
# A tap on a recreated table must not recall/rebind its target before native delivery.
for pointer_down in (False,True):
    for virtual in (False,True):
        m=Machine()
        if virtual: w,rs,es=m.table_page(n=20)
        else: w,es=m.page_list(20)
        m.paint(w)
        for _ in range(12): m.call()
        if virtual: w,rs,es=m.table_page(rebind=True)
        else: w,es=m.page_list(20)
        off=O['TABLE_TOP'] if virtual else O['SCROLL_Y']
        if pointer_down:
            m.touch()
            assert m.get(w+off)==0 and not m.moved(), 'pointer-down recalled selection'
            m.pressed=1; m.paint(w)
            assert m.get(w+off)==0 and not m.moved(), 'paint recalled under pointer'
            m.pressed=0
        def check_tapped_row(a,b):
            if virtual: assert m.get(rs[0]+O['ROW_INDEX'])==0, 'tap rebound'
        m.on_click=check_tapped_row
        m.click(es[0])
        assert m.selected(w)==0 and m.get(w+off)==0 and m.clicks==[es[0]]
        assert not m.moved(); passed()

# Saturate before adding: large valid tables cannot wrap their viewport or logical selection.
for pooled in (False,True):
    m=Machine(); w,rs,es=m.table_page(n=1 if pooled else 0,name='playerqueue_page')
    m.word(w+O['ROW_HEIGHT'],1); m.word(w+O['TABLE_ROWS'],0x7fffffff)
    m.word(w+O['W_H'],1); m.word(w+O['TABLE_TOP'],0x7ffffffe)
    if pooled:
        m.word(rs[0]+O['ROW_INDEX'],0x7ffffffe)
        m.word(rs[0]+O['W_Y'],0x7ffffffe); m.word(es[0]+O['W_H'],1)
    m.paint(w)
    for _ in range(9):
        assert m.call(gap=50)==11 and m.get(w+O['TABLE_TOP'])==0x7ffffffe
        if pooled: assert m.selected(w)==0x7ffffffe
    passed()

# A pages widget inside a navigation surface exposes only its active child's targets.
for active in (-1,0,1,2):
    m=Machine(); w=m.page()
    tabs=m.node('pages',active=active); m.word(tabs+O['W_PARENT'],w)
    es=[m.entry(tabs) for _ in range(2)]
    m.nodes[tabs]['children']=es; m.nodes[w]['children']=[tabs]
    m.paint(w); m.confirm()
    assert m.clicks==([es[active]] if active in (0,1) else []),active
    passed()

# If the white rounded stroke fails, draw the square fallback as well.
if variant=='stock':
    m=Machine(); w,es=m.page_list(3); m.rounded_fail=O['RADIUS']-1
    m.paint(w)
    assert [s[5] for s in m.strokes]==[SHADE,OUTLINE]
    assert m.lcd_colors()==LCD_COLORS; passed()

# An overdue confirmation can change power state before the next release is processed.
for flag in ('g_backlight_status','g_power_longkey','g_ingore_bootkey_flag'):
    m=Machine(); w,es=m.page_list(3)
    m.on_click=lambda a,b: m.byte(syms[flag],0 if flag=='g_backlight_status' else 1)
    m.release(); m.now+=O['DOUBLE_CLICK_MS']+1
    assert m.call(O['KEY_CENTER'],gap=0)==0,flag
    assert m.clicks==[es[0]] and not m.timers
    passed()

# Reject a missing key event before calling the stock filter, cancelling pending input.
m=Machine(); w,es=m.page_list(3); m.release()
assert m.call(args=(0,0,0,0),gap=0)==0
m.advance(300); assert not m.clicks and not m.timers; passed()

# Any native click supersedes delayed confirmation, even without a pointer-down callback.
for target_kind in ('same', 'nested', 'outside'):
    m=Machine(); w,es=m.page_list(3); m.release()
    target=es[0] if target_kind=='same' else m.entry(es[0]) if target_kind=='nested' else m.node('button')
    m.click(target,gap=100)
    m.advance(300)
    assert m.clicks==[target] and not m.timers,target_kind
    passed()

# A native activation also ends the prior wheel gesture when no pointer-down was delivered.
for home in (False,True):
    m=Machine(); w,es=m.page_list(40,extent=1920)
    if home:
        m.nodes[m.top]['name']='home_page'; m.nodes[w]['type']='slide_menu'
        m.word(w+O['SLIDE_INDEX'],0)
    m.call(gap=0)
    if not home: m.call(gap=50)
    m.click(es[0 if home else 2],gap=50)
    assert m.call(gap=50)==11
    if home: assert slide(m,w)[3]==200
    else: assert m.selected(w)==3
    passed()

# Centre consumes a touch interruption once; its second release must not stop the recall
# glide again and replace the armed row with a different visible row.
m=Machine(); w,es=m.page_list(10); m.paint(w)
for _ in range(5): m.call()
w,es=m.page_list(10); m.touch(); m.glide=False
assert m.release()==11 and m.selected(w)==5
assert m.release(100)==0 and m.screens==[0]
m.advance(300)
assert not m.clicks and not m.timers
passed()

# Rejecting an expired confirmation must not restore or consume the new scope's recall.
for virtual in (False,True):
    m=Machine()
    if virtual: w,rs,es=m.table_page(n=20)
    else: w,es=m.page_list(20,name='allmusic_page')
    off=O['TABLE_TOP'] if virtual else O['SCROLL_Y']
    m.paint(w)
    for _ in range(12): m.call()
    m.word(syms['g_class_type'],2); m.word(w+off,0); m.paint(w); m.release()
    before=dict(m.nodes[w])
    m.word(syms['g_class_type'],0xf001); m.advance(300)
    assert not m.clicks and not m.moved() and m.get(w+off)==0
    assert m.nodes[w]==before, 'rejected confirmation changed live selection properties'
    m.paint(w)
    assert m.selected(w)==12 and m.get(w+off)==540
    passed()

# Touch mode survives settling, unsupported pages, recreation and native clicks anywhere.
for virtual in (False,True):
    for click_only in (False,True):
        m=Machine()
        w,rs=m.list_surface(virtual)
        m.paint(w); assert m.drawn()
        if click_only: m.click(m.node('button'))
        else: m.touch()
        assert any(c[0]=='widget_invalidate_force' and c[1]==m.top for c in m.calls)
        off=O['TABLE_TOP'] if virtual else O['SCROLL_Y']
        anim=O['TABLE_ANIMATOR'] if virtual else O['VIEW_ANIMATOR']
        m.word(w+off,110)
        if virtual: m.bind(rs,110)
        m.word(w+anim,0x1234)
        m.paint(w); assert not m.drawn()
        m.word(w+anim,0); m.paint(w)
        assert m.selected(w)==3 and not m.drawn()
        old=m.top; m.page('playing_page'); m.touch(); m.call()
        m.top=old; m.paint(w); assert not m.drawn()
        # Returning to a recreated list preserves remembered selection, still without drawing.
        w,rs=m.list_surface(virtual)
        m.paint(w); assert m.selected(w)==3 and not m.drawn()
        m.confirm(); m.paint(w); assert m.drawn()  # centre-generated click stays visible
        m.touch(); m.pressed=1; m.call(); m.paint(w)
        assert not m.drawn()  # rejected wheel does not restore drawing
        m.pressed=0; m.call(); m.paint(w); assert m.drawn()
        passed()
# Boundary wheel turns restore drawing and cancel momentum even without selection movement.
for virtual in (False,True):
    m=Machine()
    w,rs=m.list_surface(virtual,rebind=False)
    m.paint(w); m.touch()
    anim=O['TABLE_ANIMATOR'] if virtual else O['VIEW_ANIMATOR']
    m.word(w+anim,0x1234)
    assert m.call(O['KEY_PREV'])==11 and m.selected(w)==0 and m.get(w+anim)==0
    m.paint(w); assert m.drawn()
    passed()
# Touch on a page without a supported pane also hides the next page's outline.
m=Machine(); m.page('playing_page'); m.touch(); w,es=m.page_list(3)
m.paint(w); assert not m.drawn()
m.call(O['KEY_PLAY']); m.paint(w); assert not m.drawn()
m.call(); m.paint(w); assert m.drawn(); passed()

# Immediate table rebinding replaces widgets, not just their indices. Centre resolves new rows.
m=Machine(); w,rs,es=m.table_page(); m.glide=False
old=list(es)
def replace_pool(a,offset):
    global rs,es
    rs=[m.node('table_row') for _ in range(4)]
    es=[m.entry(r) for r in rs]
    m.nodes[a]['children']=rs
    for r,e in zip(rs,es):
        m.nodes[r]['children']=[e]; m.word(r+O['W_PARENT'],a)
    m.bind(rs,offset)
    for e in old: m.nodes.pop(e,None)
m.rebind=replace_pool
m.word(w+O['TABLE_ANIMATOR'],0x1234)
m.call(); m.call()
assert m.selected(w)==2 and m.get(w+O['TABLE_TOP'])==60
assert m.get(w+O['TABLE_ANIMATOR'])==0
m.confirm(); assert m.clicks==[es[1]] and m.clicks[0] not in old
passed()

# Ring lists bump at both ends and hard-stop while the wheel keeps turning; the first
# same-direction detent after a pause wraps to the other end.
m,w,es=walk(6,5,height=96,extent=288,name='playlist_page'); assert m.get(w+O['SCROLL_Y'])==192
assert m.call(gap=100)==11 and m.selected(w)==5 and m.get(w+O['SCROLL_Y'])==192
m.paint(w,gap=0)
assert m.sel()==(0,42,240,48)   # bumped up against the end
for _ in range(3): assert m.call(gap=100)==11 and m.selected(w)==5   # still spinning: hard stop
assert m.call(gap=O['EDGE_PAUSE_MS'])==11 and m.selected(w)==0 and m.get(w+O['SCROLL_Y'])==0
m.paint(w,gap=0); assert m.sel()==(0,0,240,48)
assert m.call(O['KEY_PREV'],gap=100)==11 and m.selected(w)==0
m.paint(w,gap=0); assert m.sel()==(0,6,240,48)   # bumped down at the top
assert m.call(O['KEY_PREV'],gap=100)==11 and m.selected(w)==0
assert m.call(O['KEY_PREV'],gap=O['EDGE_PAUSE_MS'])==11 and m.selected(w)==5 and m.get(w+O['SCROLL_Y'])==192
m.paint(w,gap=0); assert m.sel()==(0,48,240,48)
passed()

# The pause is measured from the last stopped detent, to the millisecond.
m=Machine(); w,es=m.page_list(6,height=96,extent=288,name='folder_page')
m.folder('/sd/albums')
m.paint(w)
for _ in range(5): assert m.call()==11
assert m.call(gap=100)==11 and m.selected(w)==5
assert m.call(gap=O['EDGE_PAUSE_MS']-1)==11 and m.selected(w)==5
m.paint(w,gap=0); assert m.sel()==(0,42,240,48)
assert m.call(gap=O['EDGE_PAUSE_MS']-1)==11 and m.selected(w)==5   # re-armed, not wrapped
assert m.call(gap=O['EDGE_PAUSE_MS'])==11 and m.selected(w)==0
passed()

# Virtual music tables carry over too; the wrap lands on the first logical row.
m=Machine(); w,rs,es=m.table_page(n=4,rebind=True)
m.word(w+O['W_H'],96); m.word(w+O['TABLE_ROWS'],20)
m.paint(w)
for _ in range(19): assert m.call()==11
assert m.selected(w)==19 and m.get(w+O['TABLE_TOP'])==864
assert m.call(gap=100)==11 and m.selected(w)==19
assert m.call(gap=O['EDGE_PAUSE_MS'])==11 and m.selected(w)==0 and m.get(w+O['TABLE_TOP'])==0
assert any(c[0]=='table_client_set_yoffset' for c in m.calls)
passed()

# The Library's Albums grid (album_page, 0xf003) carries over at its ends as Artists do; an album's
# songs (0xff10) on the same page keep hard ends. The class is set before the list loads, as the
# Library sets it: changing it under a live list reloads the list and drops the bump.
for cls,wraps in ((0xf003,True),(0xff10,False)):
    m=Machine(); m.word(syms['g_class_type'],cls)
    w,es=m.page_list(6,height=96,extent=288,name='album_page'); m.paint(w)
    for _ in range(5): assert m.call()==11
    assert m.call(gap=100)==11 and m.selected(w)==5
    m.paint(w,gap=0)
    if wraps:
        assert m.call(gap=O['EDGE_PAUSE_MS'])==11 and m.selected(w)==0,(cls,m.selected(w))
    else:
        assert m.call(gap=O['EDGE_PAUSE_MS'])==11 and m.selected(w)==5,(cls,m.selected(w))
    passed()
# Other settings, dynamic pages and other grids keep hard ends; one-row lists never wrap.
for name in ('display_page','playerqueue_page'):
    m,w,es=walk(6,5,height=96,extent=288,name=name)
    assert m.call(gap=100)==11 and m.selected(w)==5
    m.paint(w,gap=0); assert m.sel()==(0,48,240,48)
    assert m.call(gap=100)==11 and m.selected(w)==5
    passed()
# The hold menu and Now Playing's More page carry over as Library lists do.
for name in ('playermore_page','sortselect_dialog','playset_page','sysset_page'):
    m,w,es=walk(6,5,height=96,extent=288,name=name)
    assert m.call(gap=100)==11 and m.selected(w)==5
    assert m.call(gap=O['EDGE_PAUSE_MS'])==11 and m.selected(w)==0,(name,m.selected(w))
    passed()
m=Machine(); w,es=m.page_list(1,height=96,extent=96,name='playlist_page')
m.paint(w)
for _ in range(3): assert m.call(gap=100)==11 and m.selected(w)==0
m.paint(w,gap=0); assert m.sel()==(0,0,240,48)
passed()

# Run the stock list callback and thumb setter: immediate wheel moves must sync the value.
for populated in (False,True):
    m=Machine(); w,es=m.page_list(20 if populated else 0,height=96,extent=960)
    bar=m.node('scroll_bar_m'); m.word(bar+0x78,1000)  # native bar range
    parent=m.node('list_view',children=[w,bar])
    m.word(parent+0x84,w); m.word(parent+0x88,bar)
    m.word(w+O['W_PARENT'],parent); m.nodes[m.top]['children']=[parent]
    m.word(w+O['VIEW_ON_SCROLL'],0x5ec070)  # installed by stock list_view
    m.word(w+0x74,syms['g_scroll_view_vtable'])
    m.handlers.pop(syms['scroll_view_set_offset'])
    m.mock('scroll_bar_cast','scroll_bar_is_mobile','widget_set_opacity','widget_set_visible_only',
           'widget_dispatch_simple_event')
    m.paint(w)
    for key in [O['KEY_NEXT']]*20+[O['KEY_PREV']]*20:
        assert m.call(key)==11
        offset=m.get(w+O['SCROLL_Y'])
        assert m.get(bar+O['BAR_VALUE'])==int(offset/864*1000)
        events=[c[2] for c in m.calls if c[0]=='widget_dispatch_simple_event' and c[1]==w]
        if m.moved(): assert events[-2:]==[O['EVT_SCROLL'],O['EVT_SCROLL_END']]
    assert m.get(w+O['SCROLL_Y'])==m.get(bar+O['BAR_VALUE'])==0
    passed()

# Native scrollbar lifecycle, including a fully transparent/invisible mobile bar.
for kind in ('scroll_view','table_client'):
    m=Machine()
    w,es=m.page_list(20,extent=960) if kind=='scroll_view' else (lambda t:(t[0],t[2]))(m.table_page(20))
    bar=m.node('scroll_bar_m',visible=0); m.byte(bar+0x8d,1); m.word(bar+O['BAR_VALUE'],17)
    parent=m.node('list_view' if kind=='scroll_view' else 'table_view',children=[w,bar])
    m.word(w+O['W_PARENT'],parent); m.nodes[m.top]['children']=[parent]
    m.handlers.pop(syms['scroll_bar_scroll_to'])
    m.mock('scroll_bar_cast','scroll_bar_is_mobile','widget_set_opacity','widget_set_sensitive',
           'widget_set_visible_only','widget_animator_prop_create','widget_animator_prop_set_params')
    m.paint(w); m.call(gap=0)
    assert m.nodes[bar]['visible'] and m.u.mem_read(bar+0x34,1)==b'\xff'
    animator=m.get(bar+0x98)
    assert animator and m.get(animator+O['ANIM_DURATION'])==500 and m.get(animator+0x24)==300
    assert m.get(bar+O['BAR_VALUE'])==17  # waking must not calculate or move the thumb
    m.touch(); m.call(gap=0)
    assert m.get(bar+0x98)!=animator
    assert any(c[0]=='widget_animator_destroy' and c[1]==animator for c in m.calls)
    # The stock opacity completion disables and hides the bar; no payload fade timer exists.
    animator=m.get(bar+0x98); callback,ctx=m.slide_callbacks[animator]
    m.byte(bar+0x34,0)
    assert m.call(address=callback,args=(ctx,0,0,0),gap=0)==7
    assert not m.nodes[bar]['visible'] and not m.nodes[bar]['sensitive'] and not m.get(bar+0x98)
    m.call(gap=0); assert m.nodes[bar]['visible']
    m.top=0; m.nodes.clear(); m.advance(1000)  # payload retains no native bar/animator pointer
    passed()
for count,bar_present in ((2,True),(20,False)):
    m=Machine(); w,es=m.page_list(count,height=96,extent=count*48)
    parent=m.node('list_view',children=[w]+([m.node('scroll_bar_m')] if bar_present else []))
    m.word(w+O['W_PARENT'],parent); m.paint(w); m.call()
    assert not any(c[0]=='scroll_bar_scroll_to' for c in m.calls)
    passed()

# Bump repaints at precisely 120 ms, independently of the pause-to-wrap arm.
m=Machine(); w,es=m.page_list(2,height=96,extent=96,name='playlist_page')
m.paint(w); m.call(); m.call(gap=0)
assert m.timers and min(t[0] for t in m.timers.values())==m.now+O['BUMP_MS']
m.advance(O['BUMP_MS']-1); assert not m.calls
m.advance(1); assert any(c[0]=='widget_invalidate_force' and c[1]==w for c in m.calls)
m.paint(w,gap=0); assert m.sel()==(0,48,240,48)
m.call(gap=O['EDGE_PAUSE_MS']); assert m.selected(w)==0
passed()
for cancel in ('touch','activate','recycle','destroy','scope'):
    m=Machine(); w,es=m.page_list(2,height=96,extent=96,name='folder_page')
    m.folder('/sd/a'); m.paint(w); m.call(); m.call(gap=0)
    if cancel=='touch': m.touch()
    elif cancel=='activate': m.click(es[-1])
    elif cancel=='recycle': m.nodes[w].pop('_ringnav_fx')
    elif cancel=='destroy': m.top=0; m.nodes.clear()
    else: m.folder('/sd/b'); m.paint(w,gap=0)
    m.advance(O['BUMP_MS'])
    assert not any(c[0]=='widget_invalidate_force' and c[1]==w for c in m.calls)
    passed()

# Execute stock navigator, Now Playing initialization/key-up, and native window switch/close.
# Resource loading, array storage, paint and animation services remain mocked.
class NavigationMachine(Machine):
    def __init__(self):
        super().__init__()
        self.order=[]; self.block_open=False
        self.word(self.wm+0x94,0x9a0490)
        for name in ('navigator_switch_to_with_context','navigator_back_to_home','navigator_to_with_context',
                     'navigator_to','window_close'):
            self.handlers.pop(syms[name],None)
        self.mock('widget_child','window_open_and_close','widget_restack','widget_get_window',
                  'widget_is_keyboard','widget_is_dialog','widget_is_window','widget_is_normal_window',
                  'widget_remove_child','widget_destroy','window_manager_dispatch_window_event',
                  'widget_foreach','widget_on','playing_timer_start','access@GLIBC_2.0',
                  'remove@GLIBC_2.0','idle_add','netdisk_folder_clear','awake_screen',prefix='nav:')
        self.rockbox_mode=0o100755
        self.handlers[syms['__xstat@GLIBC_2.0']]='rockbox_stat'
        self.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
        for address in (0x52b6f4,0x68541c,0x6885c0,0x6861d4): self.handlers[address]='nav:'+hex(address)
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
        elif name=='0x6885c0': ret=1  # stock no-window-animation completion branch
        elif name=='0x6861d4':ret=self.top
        elif name=='access@GLIBC_2.0':ret=-1
        self.returns(ret)

if variant=='ipod':
    for existing in (False,True):
        for empty in (False,True):
            m=NavigationMachine(); w,es=m.page_list(20,name='folder_page'); browse=m.top
            home=m.node('window','home_page',stage=3)
            playing=m.node('window','playing_page',stage=3) if existing else 0
            m.order=[home]+([playing] if playing else [])+[browse]; m.sync_order()
            m.folder('/sd/music'); m.paint(w)
            for _ in range(6):m.call()
            selected,offset=m.selected(w),m.get(w+O['SCROLL_Y'])
            for visit in range(3):
                long_return(m)
                assert m.nodes[m.top]['name']=='playing_page'
                assert not any(c[0]=='player_start' for c in m.calls)
                # The switch dropped the hold-release latch: the hold's release is ignored
                # and the first short Return goes back instead of being swallowed.
                assert m.u.mem_read(O['RETURN_RELEASE_LATCH'],1)==b'\0'
                assert m.call(170,gap=0)==0 and m.nodes[m.top]['name']=='playing_page'
                m.call(170,address=0x52c248,args=(m.top,m.event,0,0),gap=0)
                assert m.top==browse, (existing,visit,[m.nodes[p]['name'] for p in m.order])
                m.paint(w,gap=0); assert (m.selected(w),m.get(w+O['SCROLL_Y']))==(selected,offset)
            passed()
    m=NavigationMachine(); m.page('folder_page'); browse=m.top; m.order=[browse]; m.sync_order()
    m.block_open=True; long_return(m); assert m.top==browse
    assert m.call(170,gap=0)==11
    m.block_open=False; m.animating=1; long_return(m); assert m.top==browse
    passed()

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

# Stock constructors/dispatch establish these event numbers and fields, independently of mocks.
m=Machine()
for n in ('pointer_event_init','event_init'):m.handlers.pop(syms[n],None)
p=m.alloc(48);m.call(address=syms['pointer_event_init'],args=(p,O['EVT_POINTER_DOWN'],m.wm,37),stack=(91,))
assert m.get(p+O['EVENT_TARGET'])==m.wm and m.get(p+O['EVENT_X'])==37 and m.get(p+O['EVENT_Y'])==91
assert m.get(p+4)==48;passed()

# The stock wm registration fixes the type that reaches the hooked down callback. The
# move-before thunk forwards 0x102; 0xff belongs to the separate stock on_wm_tsdown_fun.
def wm_event_type(address):
    return struct.unpack('<I',m.u.mem_read(address,4))[0]&0xffff
assert wm_event_type(0x523ef0)==O['EVT_POINTER_DOWN']==0x100
assert wm_event_type(0x523ed4)==O['EVT_POINTER_MOVE_BEFORE']==0x102
assert wm_event_type(0x523f28)==0xff
assert wm_event_type(0x523e6c)==O['EVT_KEY_DOWN_BEFORE']==0x112
assert wm_event_type(0x523eb8)==O['EVT_KEY_UP']==0x114
assert wm_event_type(0x523e80)==O['EVT_KEY_UP_BEFORE']==0x115
passed()

for page_name in ('folder_page','localmusic_page','allmusic_page','radio_page'):
    for empty in (False,True):
        for distance in (0,7,8,47,48,49):
            m=PullMachine(); m.handlers[payload_syms['radio_search']]='stock_radio_search'  # a payload call: no t9
            if page_name=='folder_page':
                w,rows,es=m.table_page(name=page_name)
                if empty:m.word(w+O['TABLE_ROWS'],0);m.nodes[w]['children']=[]
            else:w,es=m.page_list(0 if empty else 5,name=page_name)
            page=m.top;m.start_pull(page,w)
            m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=60+distance)
            # Local Songs' stock search is iPod's; Internet Radio's own search is in both.
            enabled=page_name=='radio_page' or (variant=='ipod' and page_name=='localmusic_page')
            assert bool(m.prompt(page)['visible'])==(enabled and distance>=8)
            if enabled and distance>=8:
                assert m.prompt(page)['text']==('Release to search' if distance>=48 else 'Pull to search')
                assert m.click(es[0])==11 if es else True
            # click() normally uses a one-second gap; gesture state still belongs to the page.
            m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=60+distance)
            radio=lambda: [c for c in m.calls if c[0]=='stock_radio_search']
            assert len(m.searches()+radio())==(1 if enabled and distance>=48 else 0) and not (radio() and page_name!='radio_page')
            m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
            assert len(m.searches()+radio())==(1 if enabled and distance>=48 else 0)
            assert not m.prompt(page)['visible']
            passed()

# A failed registration leaves no partial page callbacks; retry binds each event once.
for fail in ('EVT_WINDOW_BACKGROUND','EVT_POINTER_UP_BEFORE'):
    m=PullMachine();m.handlers[payload_syms['radio_search']]='stock_radio_search'
    w,es=m.page_list(5,name='radio_page');page=m.top
    before=list(m.nodes[page].get('handlers',[]));m.pull_bind_fail=O[fail]
    m.start_pull(page,w)
    assert m.nodes[page].get('handlers',[])==before and not m.nodes[page].get('_pull_bound',0)
    m.pull_bind_fail=None;m.start_pull(page,w)
    handlers=m.nodes[page]['handlers'];assert len(handlers)==len(before)+6 and len(handlers)==len(set(handlers))
    m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120);m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
    assert len([c for c in m.calls if c[0]=='stock_radio_search'])==1
    passed()

if variant=='ipod':
    # Local Songs' virtual table keeps its rows and top offset across a search.
    m=PullMachine();w,rows,es=m.table_page(name='localmusic_page');page=m.top
    m.start_pull(page,w);count=m.get(w+O['TABLE_ROWS'])
    m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120);m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
    assert len(m.searches())==1 and m.get(w+O['TABLE_ROWS'])==count and m.get(w+O['TABLE_TOP'])==0
    # Stock search opens a dialog on this page; resuming it needs no index translation.
    m.start_pull(page,w);m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=60)
    assert m.click(es[0])==0
    passed()
    for cancel in ('below','horizontal','upward','midlist','outside','edge0','edge30','edge31',
                   'wheel','button','screen','navigate','background','close','destroy','surface','abort','move_hook'):
        m=PullMachine();w,es=m.page_list(8,height=240,name='localmusic_page');page=m.top
        y=30 if cancel=='edge30' else 0 if cancel=='edge0' else 31 if cancel=='edge31' else 60
        if cancel=='midlist':m.word(w+O['SCROLL_Y'],20)
        m.start_pull(page,w,x=250 if cancel=='outside' else 100,y=y)
        if cancel=='horizontal':m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],x=170,y=y+20)
        elif cancel=='upward':m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=y-10)
        m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=y+60)
        if cancel=='below':m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=y+47)
        elif cancel in ('wheel','button'):m.call(O['KEY_NEXT'] if cancel=='wheel' else O['KEY_PLAY'],gap=0)
        elif cancel=='screen':m.byte(syms['g_backlight_status'],0);m.paint(w,gap=0)
        elif cancel=='navigate':m.page('home_page');m.paint(m.top,gap=0)
        elif cancel in ('background','close','destroy','surface','abort'):
            typ=O[{'background':'EVT_WINDOW_BACKGROUND','close':'EVT_WINDOW_CLOSE','destroy':'EVT_DESTROY',
                   'surface':'EVT_DESTROY','abort':'EVT_POINTER_ABORT'}[cancel]]
            m.emit(w if cancel=='surface' else page,typ)
        elif cancel=='move_hook':
            # The native wm move-before (0x102) tail-calls the down-before hook (0x100).
            # The move type must not restart the pull.
            m.word(m.event+O['EVENT_Y'],y+60)
            m.call(key=100,address=syms['on_wm_tsmove_before_fun'],event_type=O['EVT_POINTER_MOVE_BEFORE'],gap=0,clear=False)
        m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=y+(47 if cancel=='below' else 60))
        assert len(m.searches())==(1 if cancel in ('edge31','move_hook') else 0),cancel
        assert not m.prompt(page)['visible'],cancel
        passed()

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

if variant=='ipod':
    for distance in (7,48):
        m=PullDispatchMachine();w,es=m.page_list(5,height=240,name='localmusic_page');page=m.top
        m.start_pull(page,w)
        m.word(page+0x74,1);m.word(page+O['W_EMITTER'],page)
        m.word(m.event+O['EVENT_Y'],60+distance)
        for fn,typ in (('widget_on_pointer_move',0x101),('widget_on_pointer_up',0x103)):
            m.calls=[]
            ret=m.call(key=100,address=syms[fn],args=(page,m.event,0,0),event_type=typ,gap=0,clear=False)
            child_calls=[c for c in m.calls if c[0]=='input:'+fn+'_children']
            assert (ret==11 and not child_calls) if distance>=8 else bool(child_calls)
        assert len(m.searches())==(distance>=48)
        passed()
    # Native abort delivery follows the pressed target chain; it is not an EVT_CLICK.
    m=PullMachine();w,es=m.page_list(3,height=240,name='localmusic_page');page=m.top
    m.start_pull(page,w);m.handlers.pop(syms['widget_dispatch_event_to_target_recursive'])
    m.word(page+0x4c,w);m.word(w+0x4c,es[0])
    m.calls=[];m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120)
    assert [c[1] for c in m.dispatched()]==[w,es[0]] and not m.clicks
    m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=100);assert not m.searches()
    assert m.click(es[0])==11
    passed()

    # Use the stock search callback, navigator and Back path. Only resource initialization
    # and window storage are mocked, as in the Now Playing navigation cases above.
    class SearchNavigationMachine(PullMachine,NavigationMachine):
        def __init__(self):
            super().__init__()
            self.handlers.pop(syms['stock_search'],None)
            self.handlers.pop(syms['navigator_to'],None)
            self.mock('dialog_search_dialog_init',prefix='nav:'); self.mock('widget_on')
    m=SearchNavigationMachine();w,es=m.page_list(6,height=240,name='localmusic_page');page=m.top
    m.order=[page];m.sync_order()
    m.start_pull(page,w);m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120)
    m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
    assert m.nodes[m.top]['name']=='dialog/search_dialog' and m.order[0]==page
    # The stock dialog owns its Back handling and its callbacks are outside this harness;
    # model the window manager popping it. The Local Songs page must still be the same
    # instance with its offset and rows untouched.
    m.order.remove(m.top);m.sync_order()
    assert m.top==page
    assert m.get(w+O['SCROLL_Y'])==0 and len(m.nodes[w]['children'])==6
    passed()

# Artist detail opens on Albums: the real init reaches the stock Albums tab handler, which
# selects tab 1 and queries albums before building; stock builds Songs instead.
for patched in (True, False):
    m=Machine(patched)
    for address,name in ((0x4adcbc,'songs_view'),(0x4ac084,'albums_view'),(0x4ad4c8,'timer_idex'),(0x4ed0c4,'query_run')):
        m.handlers[address]=name
    m.mock('load_artistinfo_list','load_localartist_list','widget_get_window','widget_foreach','strcpy@GLIBC_2.0')
    arg=m.alloc(8); m.word(arg+4,m.alloc(0x40))
    assert m.call(address=syms['localmusic_artistinfo_page_init'],args=(m.node('window','artistinfo_page'),arg,0,0))==0
    names=[c[0] for c in m.calls if c[0] in ('songs_view','albums_view','load_localartist_list','query_run')]
    tab=m.get(m.get(0xa26cc0-0x5810)+0x7678)
    if patched:
        assert names==['query_run','load_localartist_list','query_run','albums_view'] and tab==1, names
        assert [c[1] for c in m.calls if c[0]=='load_localartist_list']==[1]
    else: assert names==['query_run','songs_view'] and tab==0, names
    passed()

# Play/Pause hold queue menu. libcstl deques are Python lists of element addresses; the stock
# mclLoadPlayList, mclNextSong and key filters run for real.
from build import SHUFFLE_CALL, fileoff
class QueueMachine(Machine):
    def __init__(self,page='allmusic_page',rows=20,queue=3,pos=0,mode=0,cls=0xf001):
        super().__init__()
        self.deqs={}; self.toasts=[]; self.sent=[]; self.picks=[]; self.airplay=0
        for n in ('_create_deque','deque_init','deque_init_copy','deque_size','deque_at','_deque_push_back',
                  'deque_assign','deque_clear','deque_destroy','deque_pop_back','send@GLIBC_2.0','window_manager_get_input_device_status',
                  'navigator_to','navigator_to_with_context','window_close','widget_on','widget_destroy_children',
                  'getMusicByAlbum','getMusicByAlbumAndSonger','getMusicByAlbumAndAlbumSonger','toolsLoadDirectory',
                  'getMusicBySonger','getMusicByAlbumArtist','getMusicByComposer','getMusicByGenre',
                  'getMusicByAlbumAndComposer','getMusicByAlbumAndGenre','checkFavExist','navigator_window_is_exist',
                  'mcl_shuffle_pick','airplayGetFlag','calloc@GLIBC_2.0','fopen@GLIBC_2.2','qsort@GLIBC_2.0'): self.handlers[syms[n]]='q:'+n
        self.favs=set(); self.open_pages=set(); self.opened=[]
        self.handlers.pop(syms['mclLoadPlayList'])
        self.mock('strlen@GLIBC_2.0','snprintf@GLIBC_2.0'); self.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
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
    def dialog(self):
        self.view=self.node('scroll_view','scroll_view',[self.node('list_item') for _ in range(5)])
        self.title=self.node('hscroll_label','scrlabel_title'); self.back=self.node('image','img_return')
        return self.node('dialog','sortselect_dialog',[self.node('view','view_navbar',[self.title,self.back]),
                                                       self.node('list_view','list_view',[self.view])])
    def bootvol(self):  # stock's Boot volume page, which crossfade.c re-dresses
        self.slider=self.node('slider','slider_bootvol'); self.vol=self.node('label','label_vol')
        self.dec,self.add=self.node('image','img_dec'),self.node('image','img_add')
        self.title,self.switch=self.node('label','label_title'),self.node('image','img_bootswitch')
        return self.node('window','bootvol_page',[self.node('view','view_navbar',[self.title,self.switch]),
                         self.node('view','view_bootvol',[self.slider,self.dec,self.add,self.vol],visible=0)])
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
        elif name=='fopen@GLIBC_2.2': ret=0  # no ratings file
        elif name=='qsort@GLIBC_2.0': self.qsort(a,b,c,d)  # Albums' sorted lookups, the payload's comparator run nested
        else:  # the album/folder queries fill the staging deque
            self.query=(name,self.text(a) if a else None,(self.text(b) if b else None) if 'And' in name else None,c)
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

sel=lambda m:[c[:2] for c in m.calls if c[0].startswith('batch_')]

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
            if variant!='ipod': self.stack.pop(); self.top=self.stack[-1]
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

for callback,button,pages in (
    (0x4ad0b4,'track', ['artistinfo_page']),
    (0x4a5ea4,'albuminfo', ['artistinfo_page','albuminfo_page']),
    (0x526b74,'', ['playerjumpinfo_page']),
):
    for lookup in (False,True):
        for selected in (0,2):
            m=LookupMachine(['home_page','playing_page']+pages,selected,lookup)
            w=m.node('button',f'{button}_{selected}'); m.word(w+0x10,m.string(f'{button}_{selected}'))
            m.word(w+0x78,selected); m.word(m.event+0x10,w)
            assert m.call(address=callback,args=(w,m.event,0,0),gap=0)==0
            assert m.opened[-1]==('playing_page',['Row 0','Row 1','Row 2'],selected,0xf001,2)
            unwind=lookup or callback==0x526b74
            assert [m.nodes[w]['name'] for w in m.stack]==(['home_page'] if unwind else ['home_page','playing_page']+pages)
            assert m.backs==0 if variant=='ipod' else m.backs==(len(pages) if unwind else 0)
            assert m.closed==((list(reversed(pages)) if variant=='ipod' else [])+['playing_page'] if unwind else [])
            passed()

# Folder view's row click (ringnav_folder_click over stock's 0x521bc4): a video file goes to books_video,
# as Library's Videos row plays it, and stock never queues it as a song; a song row, a video books_video
# refuses and any row in select mode stay stock's (folder_enter's 8, then the queue and Now Playing).
class FolderClickMachine(LookupMachine):
    def __init__(self,*a):
        super().__init__(*a); self.videos=[]
        self.handlers[payload_syms['books_video']]='lookup:books_video'; self.handlers[syms['folder_enter']]='lookup:folder_enter'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='lookup:books_video':
            path=self.text(u.reg_read(UC_MIPS_REG_A0)); self.videos.append(path); self.returns(int(path.endswith('.mp4')),clobber=True); return
        if name=='lookup:folder_enter': self.returns(8,clobber=True); return
        return super().hook(u,address,size,unused)
for selected,name,navbar,played in ((1,'clip.mp4',0,True),(1,'clip.mp4',1,True),(0,'Row 0',0,False)):  # select mode selects
    m=FolderClickMachine(['home_page','folder_page'],selected,False); m.byte(syms['g_navbar_status'],navbar)
    m.items(m.get(syms['p_deque_showlist']))[1]=m.song('clip.mp4')
    w=m.node('button','row'); m.word(w+O['ROW_INDEX'],selected); m.word(m.event+O['EVENT_TARGET'],w)
    m.call(address=payload_syms['ringnav_folder_click'],args=(w,m.event,0,0),gap=0)
    assert m.videos==([] if navbar else ['/p/'+name]),(name,navbar,m.videos)
    assert played==(not any(o[0]=='playing_page' for o in m.opened)),(name,navbar)
passed()

SONG_MENU=['Play Next','Add to Queue','Add to Favourites','Add to Playlist','Go to Album','Go to Artist']
# Now Playing targets the queue even when the last browsed list has unrelated rows.
def playing_menu(**kw):
    m=QueueMachine(page='playing_page',pos=1,**kw)
    return m
for state in (3,2):  # paused and playing both expose the same actions
    m=playing_menu(); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa3beac,state)
    m.press(5000); assert m.release()==1
    m.press(6000); assert m.hold()==11 and m.labels()==SONG_MENU[:2]+['View Queue']+SONG_MENU[2:]+["Shuffle","Repeat","Group by: Album"]
    assert m.nodes[m.title]['text']=='B' and m.release()==0
    m.press(7000); assert m.release()==1 and m.get(0xa3beac)==state; passed()
# A tagged song's menu is titled by its tag, not its file name.
m=playing_menu(); m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[1]+O['REC_TITLE'],m.string('Bee'))
m.press(6000); assert m.hold()==11 and m.nodes[m.title]['text']=='Bee'; passed()
# A folder play's untagged record: the player's parsed title once it has parsed that file.
m=playing_menu(); id3=syms['g_play_id3_info']; m.u.mem_write(id3,b'/p/B\0'); m.u.mem_write(id3+O['ID3_TITLE'],b'Parsed\0')
m.press(6000); assert m.hold()==11 and m.nodes[m.title]['text']=='Parsed'; passed()
m=playing_menu(); assert m.run(2) is None and m.opened[-1][0]=='playerqueue_page' and m.names()==['A','B','C'] and not m.playback(); passed()
for action,expected,said in ((0,['A','B','B','C'],None),(1,['A','B','C','B'],None)):
    m=playing_menu(); assert m.run(action)==said and m.names()==expected and not m.playback(); passed()
m=playing_menu(); assert m.run(3) is None
assert [c[2:] for c in m.calls if c[0]=='batch_add_file']==[(0xf00a,m.get(syms['mcl_pdeqplaylist']))]
assert sel(m)[:2]==[('batch_init_selectrecord',3),('batch_set_selectitem',1)]; passed()
m=playing_menu(); m.favs.add('B'); assert m.run(3) is None
assert [c[1] for c in m.calls if c[0]=='deleteMusicFromFav']==[m.items(m.get(syms['mcl_pdeqplaylist']))[1]]; passed()
m=playing_menu(); m.run(4)
assert m.opened[-1]==('localmusic/playlist_page',0x2f001)
assert sel(m)==[('batch_init_selectrecord',3),('batch_set_selectitem',1)]; passed()
for action,page in ((5,'playerjumpinfo_page'),(6,'localmusic/artistinfo_page')):
    m=playing_menu(); m.config['ARTISTCF']='0'; m.run(action); assert m.opened[-1][0]==page
    if action==6: assert m.opened[-1][2]==m.items(m.get(syms['mcl_pdeqplaylist']))[1]
    passed()
for stage in ('open','action'):
    for change in ('advance','replace','resize','other','song','cue','stream'):
        m=playing_menu(); m.press(100)
        if stage=='action': assert m.hold()==11; m.release(); m.pick(0)
        else: assert m.call(O['KEY_PLAY'],address=syms['on_wm_keylong_fun'],event_type=O['EVT_KEY_LONG'],gap=0)==11
        q=m.get(syms['mcl_pdeqplaylist'])
        if change=='advance': m.word(O['MCL_POS'],2)
        elif change=='replace': m.word(syms['mcl_pdeqplaylist'],m.deque([m.song(c) for c in 'ABC']))
        elif change=='resize': m.items(q).append(m.song('D'))
        elif change=='other': m.word(m.items(q)[0]+O['REC_PATH'],m.string('/different'))
        elif change=='song': m.word(m.items(q)[1]+O['REC_PATH'],m.string('/different'))
        elif change=='cue': m.word(m.items(q)[1]+O['REC_CUE_START'],123)
        else: m.word(O['MCL_TYPE'],5)
        m.advance(0)
        if stage=='open': assert m.nodes[m.top]['name']=='playing_page'
        else: assert m.names(q)==(['A','B','C','D'] if change=='resize' else ['A','B','C'])
        assert not m.playback(); passed()
for kw in ({'queue':0},{'queue':1},{'queue':3}):
    m=playing_menu(**kw)
    if kw['queue']==3: m.word(O['MCL_TYPE'],5)
    m.press(100); assert m.hold()==0 and m.nodes[m.top]['name']=='playing_page'; passed()
m=playing_menu(); m.airplay=2; m.press(100); assert m.hold()==0; passed()

# The existing non-pooled playlist rows are guarded at their final click dispatch.
class PlayingPlaylistMachine(QueueMachine):
    STOCK_PICK=0x4b1d24
    def __init__(self):
        super().__init__(page='playing_page',pos=1)
        self.added=[]
        self.handlers[self.STOCK_PICK]='playlist_pick'
    def hook(self,u,address,size,unused):
        if address==self.STOCK_PICK:
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
for stale in (False,True):
    m=PlayingPlaylistMachine(); m.run(4)
    assert m.nodes[m.top]['name']=='playlist_page'
    for row in m.playlist_rows:
        f,ctx=m.handler(row,O['EVT_CLICK']); assert f!=m.STOCK_PICK and ctx==row
    if stale: m.word(O['MCL_POS'],2)
    f,ctx=m.handler(m.playlist_rows[2],O['EVT_CLICK'])
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0  # a STOP would leave the scroll_view its grab
    assert m.added==([] if stale else ['B'])
    if not stale: assert sel(m)==[('batch_init_selectrecord',3),('batch_set_selectitem',1)]
    f,ctx=m.handler(m.top,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    m.top=m.stack[0]; m.press(200); assert m.hold()==11; passed()

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
        self.handlers[0x4aad10]='row_toolbar'
        self.handlers[0x4b1438]='playlist_load'
        self.handlers[syms['widget_restack']]='picker_restack'
        self.handlers[syms['navigator_to']]='picker_dialog'
        self.word(syms['p_deque_playlist'],self.deque([self.song(f'List {i}') for i in range(count)]))
        # Stock's private playlist add-mode global, loaded through its GOT.
        data=(B/'stock-demo').read_bytes()
        from build import fileoff, GP
        self.mode=struct.unpack_from('<I',data,fileoff(data,GP-0x5810))[0]+0x7894
        assert self.mode==O['PLAYLIST_ADD_MODE']
        self.word(self.mode,mode)
        self.view=self.node('scroll_view','scroll_view')
        self.top=self.node('window','playlist_page',[self.view])
        self.word(self.view+O['W_PARENT'],self.top); self.word(self.top+O['W_PARENT'],self.wm)
    def rebuild(self):
        assert self.call(address=0x4b2dac,args=(self.top,0,0,0),gap=0)==0
        kids=self.nodes[self.view]['children']
        for i,row in enumerate(kids): self.word(row+O['W_Y'],i*48)
        self.word(self.view+O['VIEW_CONTENT_H'],len(kids)*48)
        return kids
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b,c,d=[u.reg_read(r) for r in REGS]
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

for count in (0,3):
    for mode in (0,1,2):
        m=PlaylistMachine(count,mode); kids=m.rebuild()
        assert len(kids)==count+(0 if mode else 1)
        previous=len(kids); kids=m.rebuild(); assert len(kids)==previous
        # Restacking leaves stock's numeric button names/context unchanged.
        if count:
            row=kids[0 if mode else 1]
            button=m.nodes[row]['children'][0]
            assert m.nodes[button]['name']=='0'
            assert m.handler(button,O['EVT_CLICK'])==(m.STOCK_PICK,button)
        passed()

if variant=='ipod':
    for stale in (False,True):
        m=PlaylistMachine(3,2)
        # Open through Now Playing so the picker is tracked, then rebuild as creation does.
        m.top=m.stack[0]; m.run(4); page=m.top
        m.view=m.nodes[page]['children'][0]; m.nodes[m.view]['name']='scroll_view'
        m.word(syms['p_deque_playlist'],m.deque([m.song('Created')]))
        kids=m.rebuild(); button=m.nodes[kids[0]]['children'][0]
        f,ctx=m.handler(button,O['EVT_CLICK']); assert f!=m.STOCK_PICK
        if stale: m.word(O['MCL_POS'],2)
        assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
        assert m.added==([] if stale else ['B']); passed()

# Short press toggles once; a hold opens one menu titled by its row, its release is swallowed and
# the next short press toggles again. A repeated long event of the same press opens nothing.
m=QueueMachine(); page=m.top
m.press(5000); assert m.release()==1
m.press(6000); assert m.hold()==11 and m.nodes[m.top]['name']=='sortselect_dialog'
assert {c[1:] for c in m.calls if c[0]=='widget_off_by_func'}=={(m.top,O['EVT_KEY_UP'],O['SORTSELECT_KEYUP']),(m.back,O['EVT_CLICK'],O['SORTSELECT_CLOSE'])}
assert m.nodes[m.title]['text']=='Row 0' and m.labels()==SONG_MENU
assert m.hold()==0 and not any(c[0]=='navigator_to' for c in m.calls) and len(m.stack)==2
assert m.release()==0
m.press(6500); assert m.release()==1; passed()
# Return (the replaced dialog key-up) dismisses without the stock sort flag; other keys pass.
f,ctx=m.handler(m.top,O['EVT_KEY_UP']); ev=m.alloc(0x40); m.word(ev,O['EVT_KEY_UP']); m.word(ev+O['EVENT_KEY'],O['KEY_NEXT'])
assert m.call(address=f,args=(ctx,ev,0,0),gap=0)==0 and m.top!=page
m.word(ev+O['EVENT_KEY'],O['KEY_RETURN']); assert m.call(address=f,args=(ctx,ev,0,0),gap=0)==11
assert m.top==page and not m.toasts and m.names()==['A','B','C'] and m.u.mem_read(syms['g_sort_changeflag'],1)==b'\0'
passed()
# A dropped release (AWTK aborts keys when windows change) leaves a latch that must not eat a later
# press: the press time differs.
m=QueueMachine(); m.press(1); assert m.hold()==11
m.u.mem_write(m.status+O['INPUT_KEYS'],bytes(O['INPUT_KEY_SIZE'])); m.press(2); assert m.release()==1; passed()
# The hold cancels a pending centre click. Wheel and centre then act on the menu only and the list
# keeps its selection. Touch and centre arrive as the same click and run once.
m=QueueMachine(); m.call(); m.call(); assert m.selected(m.surface)==2
m.call(O['KEY_CENTER']); m.press(100); m.hold(); m.release(); m.advance(300); assert not m.dispatched()
m.paint(m.view); assert m.call()==11 and m.selected(m.view)==1 and m.selected(m.surface)==2
assert m.confirm()==11 and m.dispatched()[0][1]==m.nodes[m.view]['children'][1]
m.pick(1); m.pick(1); m.advance(0)
assert m.names()==['A','B','C','Row 2'] and not m.toasts
assert m.top==page and m.selected(m.surface)==2 and not m.playback(); passed()
# Play next lands after the playing track, on the logical row of a recycled table. Later tracks,
# the shuffle pool and the previous index shift; duplicates stay; playback is never touched.
m=QueueMachine(); m.word(O['MCL_LASTPOS'],2)
assert m.run(0,steps=4) is None and m.names()==['A','Row 4','B','C']
assert m.pool()==[0,2,3,1] and m.mcl('MCL_LASTPOS')==3 and m.mcl('MCL_POS')==0
m.run(0); assert m.names()==['A','Row 4','Row 4','B','C'] and not m.playback(); passed()
# An artist's albums use the stock detail queries (Albums, below Coverflow's); a folder row loads
# like folder_enter, then its subfolders in listing order, dot folders skipped. Only songs join, in
# order, and the staging deque is restored.
for cls,artist_type,page,query in ((0xff01,1,'album_page',('getMusicByAlbumAndAlbumSonger','Album','Artist')),
        (0xff01,0,'localclass_page',('getMusicByAlbumAndSonger','Album','Artist')),
        (0xf001,0,'folder_page',('toolsLoadDirectory','/mnt/sd/Music/Row 0/dir',None))):
    m=QueueMachine(page=page,cls=cls,pos=2); m.word(syms['g_artist_type'],artist_type)
    if page=='folder_page':
        m.word(m.row(0)+O['REC_TYPE'],4); m.listings['/mnt/sd/Music/Row 0/dir']=[m.song('T3'),m.song('.hidden',4)]
    want=['A','B','C','T1','T3','T2'] if page=='folder_page' else ['A','B','C','T1','T2']
    assert m.run(1) is None and m.names()==want and m.query[:3]==query, m.query
    assert page!='folder_page' or [m.text(c[1]) for c in m.calls if c[0]=='toolsLoadDirectory']==['/mnt/sd/Music/Row 0','/mnt/sd/Music/Row 0/dir']
    assert m.names(m.get(syms['tools_pdeq_directory']))==['staged'] and m.pool()==list(range(len(want)))
    passed()
# Videos never enter an audio queue, including through a folder's hold menu.
for action in (0,1):
    m=QueueMachine(page='folder_page'); m.word(m.row(0)+O['REC_TYPE'],4)
    m.found=[m.song('film.mp4'),m.song('song.flac'),m.song('film.MKV')]
    assert m.run(action) is None
    assert m.names()==(['A','song.flac','B','C'] if action==0 else ['A','B','C','song.flac']); passed()
m=QueueMachine(page='folder_page'); m.word(m.row(0)+O['REC_PATH'],m.string('/mnt/mmc/Videos/film.mp4'))
m.press(100); m.hold(); assert m.nodes[m.top]['name']=='folder_page' and m.names()==['A','B','C']; passed()
m=QueueMachine(page='folder_page'); m.word(m.row(0)+O['REC_TYPE'],4)
m.word(m.row(0)+O['REC_PATH'],m.string('/mnt/mmc/Videos/Album.mp4'))
m.press(100); assert m.hold()==11 and m.nodes[m.top]['name']=='sortselect_dialog'; passed()
# Artist, composer and genre rows, and their album lists, use batch_add_file's per-class queries.
for cls,artist_type,query in ((0xf004,0,('getMusicBySonger','Artist',None)),(0xf004,1,('getMusicByAlbumArtist','Artist',None)),
        (0xf005,0,('getMusicByComposer','Comp',None)),(0xf006,0,('getMusicByGenre','Pop',None)),
        (0xff02,0,('getMusicByAlbumAndComposer','Album','Comp')),(0xff03,0,('getMusicByAlbumAndGenre','Album','Pop'))):
    m=QueueMachine(page='localclass_page',cls=cls,pos=2); m.word(syms['g_artist_type'],artist_type)
    m.word(m.row(0)+0x20,m.string('Comp')); m.word(m.row(0)+0x1c,m.string('Pop'))
    assert m.run(1) is None and m.names()==['A','B','C','T1','T2'] and m.query[:3]==query, m.query
    assert m.query[3]==0 or cls<0xff00; passed()
# Queue category expansion uses music scope; folder expansion still includes spoken tracks.
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
for cls in (0xf004,0xf005,0xf006,0xff01,0xff02,0xff03,1):
    for action in (0,1):
        m=ScopedQueueMachine(page='folder_page' if cls==1 else 'localclass_page',cls=cls)
        m.found=[m.song('Music'),m.song('Episode'),m.song('Book')]
        for r,path in zip(m.found,('/mnt/mmc/Music/song.flac','/mnt/mmc/pOdCaStS/episode.mp3','/mnt/mmc/elsewhere/BOOK.M4B')):
            m.word(r+O['REC_PATH'],m.string(path))
        m.word(m.row(0)+0x20,m.string('Comp')); m.word(m.row(0)+0x1c,m.string('Pop'))
        if cls==1: m.word(m.row(0)+O['REC_TYPE'],4)
        assert m.run(action) is None
        added=['Music','Episode','Book'] if cls==1 else ['Music']
        assert m.names()==(['A']+added+['B','C'] if action==0 else ['A','B','C']+added)
        assert m.get(payload_syms['library_music'])==0
        assert m.names(m.get(syms['tools_pdeq_directory']))==['staged']
passed()
# An Unknown row (id -1) queries the empty name; an artist's All songs row (-2) sets the flag.
m=QueueMachine(page='localclass_page',cls=0xf004); m.word(m.row(0)+O['REC_ID'],-1); m.run(1)
assert m.query[:2]==('getMusicBySonger',None); passed()
m=QueueMachine(page='localclass_page',cls=0xff01); m.word(m.row(0)+O['REC_ID'],-2); m.run(1)
assert m.query==('getMusicByAlbumAndSonger','Album','Artist',1); passed()
# Each row gets the menu that fits it. Songs: favourite as stock's heart, add to a playlist and go
# to album and artist; collections shuffle instead; folders have no favourite; Coverflow (below)
# has no playlist. Go to is left out where it would land on the page itself or a second instance.
def menu(m,setup=None):
    if setup: setup(m)
    m.press(5); assert m.hold()==11; t=m.nodes[m.title]['text']; got=m.labels(); m.release(); return t,got
GROUP=['Play Next','Add to Queue','Shuffle','Add to Playlist']
for kw,setup,want in (({},None,('Row 0',SONG_MENU)),
        ({},lambda m:m.favs.add('Row 0'),('Row 0',[*SONG_MENU[:2],'Remove from Favourites',*SONG_MENU[3:]])),
        ({},lambda m:m.open_pages.update({'artistinfo_page','playerjumpinfo_page'}),('Row 0',SONG_MENU[:4])),
        ({'cls':0xff10,'page':'album_page'},None,('Row 0',[*SONG_MENU[:4],'Go to Artist'])),
        ({},lambda m:m.word(m.row(0)+O['REC_ALBUM'],0),('Row 0',[*SONG_MENU[:4],'Go to Artist'])),
        ({'cls':0xf003,'page':'album_page'},None,('Album',[*GROUP[:3],'Add to Favourites','Rate Album',GROUP[3],'Go to Artist'])),
        ({'cls':0xff01,'page':'album_page'},None,('Album',GROUP)),
        # An artist's tabs set classinfo +0, not g_class_type: Albums first, then Songs after an album.
        ({'cls':0xff07,'page':'artistinfo_page'},lambda m:m.word(syms['g_local_classinfo_save'],0xff01),('Album',GROUP)),
        ({'cls':0xff01,'page':'artistinfo_page'},lambda m:(m.word(syms['g_local_classinfo_save'],0xff07),m.open_pages.add('artistinfo_page')),('Row 0',SONG_MENU[:5])),
        ({'cls':0xf004,'page':'localclass_page'},None,('Artist',GROUP)),
        ({'cls':0xf006,'page':'localclass_page'},lambda m:m.word(m.row(0)+0x1c,m.string('Pop')),('Pop',GROUP)),
        ({'page':'folder_page','cls':1},lambda m:m.word(m.row(0)+O['REC_TYPE'],4),('Row 0',GROUP))):
    assert menu(QueueMachine(**kw),setup)==want, (kw,want); passed()
# Favourites: Add runs batch-select's Add to My Fav for the row alone, which tags a folder file;
# Remove deletes it, and on My Fav itself flags the list to reload as Now Playing's heart does.
m=QueueMachine(); assert m.run(2,steps=3) is None and m.names()==['A','B','C']
assert sel(m)==[('batch_init_selectrecord',20),('batch_set_selectitem',3),('batch_add_file',0xf001)]
assert [c[2:] for c in m.calls if c[0]=='batch_add_file']==[(0xf00a,m.get(syms['p_deque_showlist']))]; passed()
for cls,flag in ((0xf001,0),(0xf00a,1)):
    m=QueueMachine(cls=cls); m.favs.add('Row 0'); assert m.run(2) is None
    assert [c[1] for c in m.calls if c[0]=='deleteMusicFromFav']==[m.row(0)] and m.u.mem_read(syms['g_delete_flag'],1)[0]==flag
    passed()
# Add to playlist opens the stock playlist page in its add mode (context: 1 << 16 | class) with the
# row selected, as the batch Add to playlist does; the page adds the row's songs itself.
for kw,row in (({},3),({'cls':0xf003,'page':'album_page'},5)):
    m=QueueMachine(**kw); assert m.run(row,steps=1) is None
    assert m.opened[-1]==('localmusic/playlist_page',0x10000|m.get(syms['g_class_type']))
    assert sel(m)==[('batch_init_selectrecord',20),('batch_set_selectitem',1)] and m.names()==['A','B','C']; passed()
# Shuffle plays the collection's songs from a random one with shuffle saved, as Shuffle Songs.
# Then the payload's Shuffle Songs takes the queue over (test/playback.py), so Up Next has an order.
m=QueueMachine(cls=0xf004,page='localclass_page'); m.handlers[payload_syms['playback_shuffle']]='stock_playback_shuffle'  # a payload call: no t9
assert m.run(2) is None
assert m.opened[-1][:2]==('playing_page',['T1','T2']) and m.opened[-1][2] in (0,1) and m.opened[-1][3:]==(0xf001,2) and [c[1:3] for c in m.calls if c[0]=='config_playmode']==[(2,1)]
assert [c[0] for c in m.calls].count('stock_playback_shuffle')==1; passed()
m=QueueMachine(cls=0xf004,page='localclass_page'); m.found=[]; assert m.run(2)=='Queue unchanged' and m.opened[-1][0]!='playing_page'; passed()
# Go to album fills the album query as Now Playing's Album info and opens playerjumpinfo_page; Go to
# artist opens artistinfo_page with {class, record}. Closing either puts the browsing state back.
info=syms['g_local_classinfo_save']
for row,want in ((4,'playerjumpinfo_page'),(5,'localmusic/artistinfo_page')):
    m=QueueMachine(); m.config['ARTISTCF']='0'; m.u.mem_write(info,bytes(range(256))*3+bytes(144)); before=bytes(m.u.mem_read(info,912))
    page=m.top; assert m.run(row,steps=2) is None and m.opened[-1][0]==want and m.top!=page
    if row==4:
        assert m.get(info)==0xff10 and m.u.mem_read(info+9,1)==b'\0' and m.text(info+0xd)=='Album' and m.text(info+0x10d)=='Artist'
    else: assert m.opened[-1][1:]==(0xf001,m.row(2))
    f,ctx=m.handler(m.top,O['EVT_WINDOW_CLOSE']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    assert bytes(m.u.mem_read(info,912))==before and m.names()==['A','B','C']; passed()
# An empty queue is filled by the stock loader without starting playback.
m=QueueMachine(queue=0); assert m.run(0) is None and m.names()==['Row 0'] and m.mcl('MCL_POS')==0
assert m.mcl('MCL_TYPE')==0xf001 and m.pool()==[0] and not m.playback(); passed()
# Gapless: a preload of pos+1 is closed exactly when the new tracks land there.
for row,pos,closed in ((0,0,True),(1,0,False),(1,2,True)):
    m=QueueMachine(pos=pos); m.word(O['MCL_PRELOAD'],1); m.word(O['MCL_FD'],9); m.run(row)
    assert (m.sent==[(9,b'{mcl-closegapless\\null}',23,0)] and m.mcl('MCL_PRELOAD')==-1) if closed else (not m.sent and m.mcl('MCL_PRELOAD')==1)
    passed()
# Shuffle: the patched mclNextSong call runs the stock pick, then plays the Play next track once.
demo=(B/'demo').read_bytes()
assert struct.unpack_from('<I',demo,fileoff(demo,SHUFFLE_CALL[0]))[0]==jal(payload_syms['ringnav_shuffle'])
m=QueueMachine(mode=2); m.run(0,steps=5); assert m.names()==['A','Row 5','B','C']
# LDAC: stock switchBtPriority sends quality 0x16 for both HQ and Standard; patched HQ sends 0x15.
for patched,want in ((True,(0x15,0x16,0x18)),(False,(0x16,0x16,0x18))):
    bt=Machine(patched); bt.mock('btctl_set_codec_priority','btctl_set_ldac_quality','btctl_set_sbc_quality')
    for row,quality in enumerate(want):
        assert bt.call(address=syms['switchBtPriority'],args=(row,0,0,0),gap=0)==1
        assert [c[1] for c in bt.calls if c[0]=='btctl_set_ldac_quality']==[quality], (patched,row)
    passed()
# A boot reconnect can arrive as the control thread starts, before openBluetooth's GPIO write.
from build import BT_INIT, BT_PARAMS
assert struct.unpack_from('<I',demo,fileoff(demo,BT_INIT[0]))[0]==jal(payload_syms['headset_bt_init'])
assert struct.unpack_from('<I',demo,fileoff(demo,BT_PARAMS[0]))[0]==0
# The old radio-power hook is gone: startup owns registration, and stock retains its toggle path.
assert struct.unpack_from('<I',demo,fileoff(demo,0x4efcbc))[0]==0x0320f809
class BtInitMachine(Machine):
    def hook(self,u,addr,size,_):
        if self.handlers.get(addr)=='btctl_create_daemon_thread':
            self.calls.append(('btctl_create_daemon_thread',u.reg_read(REGS[0]),0))
            self.word(u.reg_read(REGS[0]),0x12345678)
            self.returns(self.result,clobber=True)
            return
        super().hook(u,addr,size,_)
for result in (0,-7):
    bt_init=BtInitMachine(); bt_init.result=result; thread=bt_init.alloc(4)
    assert bt_init.call(address=payload_syms['headset_bt_init'],args=(thread,0,0,0),gap=0)==result
    assert bt_init.calls[0][0]=='initBluetoothParam'
    assert [c[:3] for c in bt_init.calls[1:]]==[
        ('btctl_set_prompt_callback',0x4ef600,0),
        ('btctl_device_status_callback',payload_syms['headset_bt_status'],0),
        ('btctl_set_a2dp_callback',syms['btrecvA2dpFunc'],0x01020304),
        ('btctl_create_daemon_thread',thread,0)]
    assert bt_init.get(thread)==0x12345678
    passed()
# An incoming link while a link thread waits for the saved device (BT_STATUS) is adopted; auto-link
# skips while a listed one is connected (BT_AUTOLINK).
from build import BT_STATUS, BT_AUTOLINK
assert struct.unpack_from('<I',demo,fileoff(demo,BT_STATUS[0]))[0]==jal(payload_syms['headset_status_register'])
assert struct.unpack_from('<I',demo,fileoff(demo,BT_AUTOLINK[0]))[0]==jal(payload_syms['headset_bt_autolink'])
class BtLinkMachine(Machine):
    def __init__(self,links,paired=True,connecting=1,saved='AA:AA:AA:AA:AA:AA'):
        super().__init__(); self.links=[list(x) for x in links]; self.paired=paired
        self.u.mem_write(0xa38168,saved.encode()+b'\0')  # BTLINKMAC
        self.word(syms['bt_connectstatus'],connecting); self.handlers[0x4f1a50]='device_status'
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
link=BtLinkMachine([]); link.call(address=payload_syms['headset_status_register'],args=(0,0,0,0),gap=0)
assert [c[:3] for c in link.calls]==[('btctl_device_status_callback',payload_syms['headset_bt_status'],0)]
link=BtLinkMachine([(a_mac,5)])  # 5 while the thread's Connect runs
link.call(address=payload_syms['headset_bt_status'],args=(3,link.string(b_mac),0,0),gap=0)
assert link.links==[[a_mac,4],[b_mac,3]] and 'device_status' not in [c[0] for c in link.calls]; passed()
# Otherwise stock's callback gets the event as libbtctl sent it: not connecting, thread ended or judged,
# already listed, unpaired, empty list, target connected or not yet taken by the thread (1), a thread
# for another device, other events.
for event,links,paired,connecting,saved in ((3,[(a_mac,5)],True,0,a_mac),(3,[(a_mac,5)],True,2,a_mac),
        (3,[(a_mac,5)],True,3,a_mac),(3,[(a_mac,5),(b_mac,1)],True,1,a_mac),(3,[(a_mac,5)],False,1,a_mac),
        (3,[],True,1,''),(3,[(a_mac,3)],True,1,a_mac),(3,[(a_mac,1)],True,1,a_mac),(3,[(a_mac,5)],True,1,'CC:CC:CC:CC:CC:CC'),
        (4,[(a_mac,5)],True,1,a_mac)):
    link=BtLinkMachine(links,paired,connecting,saved); addr=link.string(b_mac)
    link.call(address=payload_syms['headset_bt_status'],args=(event,addr,0x1234,0x5678),gap=0)
    assert link.links==[list(x) for x in links] and link.calls[-1]==('device_status',event,addr,0x1234,0x5678)
    passed()
for links,paired,want in (([],True,1),([],False,0),([(b_mac,3)],True,0),([(b_mac,4)],True,1),
                          ([(b_mac,5)],True,1),([(b_mac,3),(a_mac,5)],True,0)):
    link=BtLinkMachine(links,paired)
    assert link.call(address=payload_syms['headset_bt_autolink'],args=(link.string(a_mac),0,0,0),gap=0)==want
    passed()
# Bluetooth volume runs through the shared hook in both variants. Mock only headset I/O
# and stock gain; device_set_volume itself executes its native output routing.
class OutputVolumeMachine(Machine):
    def __init__(self,config=()):
        super().__init__(); self.way=0; self.config.update(config)
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

ov=OutputVolumeMachine(); assert ov.output(1)==20  # first connection inherits the current level
ov.save(65); assert ov.config=={'VOLUME':'20','BT_VOLUME':'65'}
assert ov.output(0)==20; ov.save(13)
assert ov.output(1)==65 and ov.output(1)==65 and ov.output(2)==13
assert ov.output(0)==13 and ov.output(1)==65; passed()
# A fresh boot uses wired VOLUME; reconnect restores Bluetooth, including muted and capped levels.
for value,limit,want in (('0',100,0),('100',40,40),('37',100,37)):
    ov=OutputVolumeMachine({'VOLUME':'13','BT_VOLUME':value}); ov.byte(syms['g_volume'],13)
    ov.byte(syms['g_maxvolume'],limit)
    assert ov.output(1)==want and ov.output(0)==13; passed()
for value in ('','-1','101','999999999999999999','20junk',' 20'):
    ov=OutputVolumeMachine({'BT_VOLUME':value}); assert ov.output(1)==20; passed()
ov=OutputVolumeMachine(); ov.output(1); ov.save(7,'OTHER','VOLUME'); ov.save(8,'PLAYSET','OTHER')
assert ov.config=={'VOLUME':'7','OTHER':'8'}; passed()  # other writes keep their section/key

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

for left,right,want in ((0,0,0),(0,20,25),(20,0,25),(1,1,1),(20,20,25),(40,30,51),(50,50,64),(100,100,127),
                        (-5,-1,0),(101,200,127)):
    gain=(max(0,left)*100//max(left,right),max(0,right)*100//max(left,right)) if want else (left,right)  # AVRCP 0 isn't silent on every headset: keep stock's mute
    b=BtVolumeMachine(); assert b.volume(left,right)==[gain] and b.writes()==[want]
    assert b.volume(left,right)==[gain] and not b.writes(); passed()
# Control Device Volume off (the default): the Q2's volume is stock's gain alone, the device's own
# volume is never read or written, and the poll leaves it be.
b=BtVolumeMachine(link=False); assert b.volume(20,20)==[(20,20)] and b.volume(40,30)==[(40,30)]
b.poll(); b.poll(); assert not [c for c in b.calls if c[0].startswith('btctl_')]; passed()
# Volume 0 and back up: the headset gets 0 then 1, stock gain 0 then full.
b=BtVolumeMachine(); assert b.volume(0,0)==[(0,0)] and b.writes()==[0]
assert b.volume(1,1)==[(100,100)] and b.writes()==[1]; passed()
b=BtVolumeMachine(); b.byte(syms['g_volume'],0); b.poll()
assert b.writes()==[0] and [c[1:3] for c in b.calls if c[0]=='stock_btvol']==[(0,0)]; passed()
for cur in (-1,128,255):
    b=BtVolumeMachine(); b.absolute=cur
    assert b.volume(20,30)==[(20,30)] and not b.writes(); passed()
b=BtVolumeMachine(); b.write_result=-1
assert b.volume(20,30)==[(20,30)] and b.writes()==[38]; passed()
for name,value in (('g_bluetoothflag',0),('bt_linkstatus',0),('bt__recv_pageflag',1),('bt_showcoding',0)):
    b=BtVolumeMachine()
    (b.word if name=='bt_showcoding' else b.byte)(syms[name],value)
    assert b.volume(20,30)==[(20,30)] and not b.writes(); passed()
for way in (0,2):
    b=BtVolumeMachine(); b.way=way
    assert b.volume(20,30)==[(20,30)] and not b.writes(); passed()
b=BtVolumeMachine(); b.absolute=-1; b.poll()
assert not b.writes()
b.absolute=10; b.poll(O['BT_VOLUME_SLOW_MS']-1); assert not b.writes()  # no absolute volume yet: the slow pace
b.poll(1); assert b.writes()==[25]
b.absolute=5; b.poll(); assert not b.writes()  # exact rounded remote value
b.byte(syms['bt_linkstatus'],0); b.poll(); assert not b.writes()
b.byte(syms['bt_linkstatus'],1); b.word(syms['bt_showcoding'],0); b.poll(); assert not b.writes()
b.word(syms['bt_showcoding'],1); b.poll(); assert not b.writes(); passed()
b=BtVolumeMachine(); b.write_result=-1; b.poll()
assert b.writes()==[25] and [c[1:3] for c in b.calls if c[0]=='stock_btvol']==[(20,20)]
b.write_result=0; b.poll(); assert not b.writes()  # a refusal retries at the slow pace
b.poll(O['BT_VOLUME_SLOW_MS']-400); assert b.writes()==[25]; passed()
# Dark, the read-back slows down.
b=BtVolumeMachine(); b.poll(); b.calls=[]; b.byte(syms['g_backlight_status'],0); b.absolute=50
b.poll(); assert not any(c[0]=='btctl_transport_get_volume' for c in b.calls)
b.poll(O['BT_VOLUME_SLOW_MS']-400); assert any(c[0]=='btctl_transport_get_volume' for c in b.calls); passed()
# Dark with a volume limit, the headset's own keys are still held to it, every BT_VOLUME_LIMIT_MS.
b=BtVolumeMachine(); b.byte(syms['g_maxvolume'],60); b.poll(); b.byte(syms['g_backlight_status'],0); b.absolute=127
b.poll(); assert not b.writes()
b.poll(O['BT_VOLUME_LIMIT_MS']-400); assert b.writes()==[76] and b.u.mem_read(syms['g_volume'],1)[0]==60; passed()
# A headset that can't report absolute volume is asked again at the slow pace, lit too, not every 400 ms.
b=BtVolumeMachine(); b.absolute=-1; asks=[]
for _ in range(2*O['BT_VOLUME_SLOW_MS']//400): b.poll(); asks.append(sum(c[0]=='stock_btvol' for c in b.calls))
assert asks==[1,0,0,0,0]*2; passed()
# Native device_set_volume calls LocalVol and BtVol for the DAC/Bluetooth, USB's own
# setter for USB. g_output_way=0 avoids stock's fixed line-out branch; notify is covered too.
for way,soft in ((0,0),(1,0),(2,0),(2,1)):
    b=BtVolumeMachine(); b.handlers.pop(syms['device_set_volume'],None)
    b.byte(syms['g_output_way'],0); b.way=way; b.byte(syms['g_usbvol_mode'],soft)
    b.call(address=syms['device_set_volume'],args=(20,1,0,0),gap=0)
    gains=[c[1:3] for c in b.calls if c[0]=='stock_btvol']
    local=[c[1:3] for c in b.calls if c[0]=='mclSetLocalVol']
    usb=[c[1:3] for c in b.calls if c[0]=='mclUsbAudioSetVol']
    assert gains==([(100,100)] if way==1 else [(20,20)] if way==0 else [])
    assert local==([(20,20)] if way in (0,1) else [])
    assert usb==([(20,20)] if soft else [(100,100)]) if way==2 else not usb
    assert b.writes()==([25] if way==1 else [])
    assert any(c[0]=='notifyVolume' for c in b.calls); passed()

# Screen off, the UI loop idles SCREEN_OFF_SLEEP_MS before stock's own pacing; screen on, stock alone.
sleep_hook=HOOKS['main_loop_sleep_default'][0]
for light,want in ((1,[]),(0,[O['SCREEN_OFF_SLEEP_MS']])):
    s=Machine(); s.handlers[sleep_hook+12]='stock_sleep'; s.byte(syms['g_backlight_status'],light)
    assert s.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)==0
    assert [c[1] for c in s.calls if c[0]=='sleep_ms']==want and s.calls[-1][:2]==('stock_sleep',0x1234); passed()
    # The next pass with the backlight on repaints the whole screen once; staying on, nothing more.
    s.byte(syms['g_backlight_status'],1)
    for wake in (not light,False):
        s.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)
        assert [c[1] for c in s.calls if c[0]=='widget_invalidate_force']==([s.wm] if wake else [])
    passed()
# Screen off, the window manager does not paint: stock blanks the framebuffer but paints on. A window
# animation still paints.
for light,animating in ((1,0),(0,0),(0,1)):
    s=Machine(); s.handlers[sleep_hook+12]='stock_sleep'
    vt=s.alloc(16); s.word(s.wm+0x94,vt); s.word(vt+0xc,0x400100); s.handlers[0x400100]='wm_vt_paint'
    s.byte(syms['g_backlight_status'],light); s.animating=animating
    s.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)  # the pass that sees the screen go off
    s.calls=[]; s.call(address=WM_PAINT_LEAF[1],args=(s.wm,0,0,0),gap=0,clear=False)
    assert ('wm_vt_paint' in [c[0] for c in s.calls])==bool(light or animating), (light,animating)
passed()
for want in (1,3):
    m.call(address=syms['mclNextSong'],args=(0,0,0,0),gap=0)
    assert m.picks[-1]==1 and m.mcl('MCL_POS')==want and any(c[0]=='mclStartPlayer' for c in m.calls)
passed()
# Previous then Next in stock Shuffle returns to the track Previous left, not a new random pick.
m=QueueMachine(mode=2); m.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
m.call(address=syms['mclNextSong'],args=(0,0,0,0),gap=0); m.word(O['MCL_POS'],1)
m.call(address=syms['mclPrevSong'],args=(0,0,0,0),gap=0); assert m.picks[-1]==0 and m.mcl('MCL_POS')==0
for want in (1,2):
    m.call(address=syms['mclNextSong'],args=(0,0,0,0),gap=0); assert m.mcl('MCL_POS')==want
passed()
# Update Local Music: the stock scanner, nested folders and all, checks ringnav_scan_room (65,000
# songs; stock 20,000) before each entry. Only the folder listing, paths and database insert are mocked.
from build import SCAN_LIMIT
assert struct.unpack_from('<2I',demo,fileoff(demo,SCAN_LIMIT[0]))==(jal(payload_syms['ringnav_scan_room']),SCAN_LIMIT[3])
SONGS,STOP,LISTING=0xa3c354,0xa3c358,0xa3bf48  # toolsGetMusicNum's count, toolsStopUpdateMusic's flag, the listing deque
class ScanMachine(Machine):
    def __init__(self,tree,songs,patched=True,stop_at=None):
        super().__init__(patched); self.tree=tree; self.added=[]; self.stop_at=stop_at
        for n in ('toolsSetScanFolderPath','toolsCheckItemExist','deque_size','deque_at','sprintf@GLIBC_2.0'): self.handlers[syms[n]]='scan:'+n
        self.handlers[0x5c38c8]='scan:list'; self.handlers[0x5c73e4]='scan:insert'  # private: lists a folder, adds a song
        self.word(SONGS,songs); self.word(LISTING,self.alloc(0x20))
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('scan:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address
        name=name[5:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='list':
            self.path=self.text(a); self.listing=[]
            for n in self.tree[self.path]:
                r=self.alloc(0x60); self.word(r+O['REC_NAME'],self.string(n))
                self.word(r+O['REC_TYPE'],4 if f'{self.path}/{n}' in self.tree else 8); self.listing.append(r)
        elif name=='deque_size': assert a==self.get(LISTING); ret=len(self.listing)
        elif name=='deque_at': ret=self.listing[b]
        elif name=='sprintf': assert self.text(b)=='%s/%s'; u.mem_write(a,f'{self.text(c)}/{self.text(d)}'.encode()+b'\0')
        elif name=='insert':
            self.added.append(f'{self.path}/{self.text(self.get(a+O["REC_NAME"]))}'); self.word(SONGS,self.get(SONGS)+1)
            if len(self.added)==self.stop_at: self.byte(STOP,1)  # Back on the Update Local Music dialog
        self.returns(ret)
def scan(songs,tree={'/m':['a']},**kw):
    """(return, songs added, stop flag, count): 1 finished, 0 stopped."""
    m=ScanMachine(tree,songs,**kw)
    return m.call(address=syms['toolsLoadAllFile'],args=(m.string('/m'),0,0,0),gap=0),m.added,m.u.mem_read(STOP,1)[0],m.get(SONGS)
for patched,limit in ((True,65000),(False,20000)):
    for songs in (19999,20000,32767,32768,64999,65000,65001):
        more=songs<limit
        assert scan(songs,patched=patched)==(more,['/m/a']*more,1-more,songs+more),(patched,songs); passed()
nested={'/m':['a','sub','b'],'/m/sub':['c','d']}
assert scan(0,nested)==(1,['/m/a','/m/sub/c','/m/sub/d','/m/b'],0,4); passed()
assert scan(64998,nested)==(0,['/m/a','/m/sub/c'],1,65000); passed()  # the limit inside a subfolder ends the scan
assert scan(0,nested,stop_at=2)==(0,['/m/a','/m/sub/c'],1,2); passed()  # the bnez delay slot's stop flag
assert scan(0,{'/m':['a','b','c']},stop_at=1)==(0,['/m/a'],1,1); passed()
# A folder rescan refreshes existing tags in place, including quoted paths/tags and CUE rows.
# Run the actual recursive scanner and refresh wrapper against SQLite; full scans still add songs.
from build import SCAN_REFRESH
assert struct.unpack_from('<I',demo,fileoff(demo,SCAN_REFRESH[0]))[0]==jal(payload_syms['ringnav_scan_refresh'])
class FolderTagsMachine(ScanMachine):
    def __init__(self,patched=True,cue=False,valid=True,songs=1,title="New artist's tags"):
        super().__init__({'/m':["it's.flac"]},songs,patched=patched)
        self.valid=valid; self.cue=cue; self.title=title; self.reads=0; self.updates=[]
        self.db=sqlite3.connect(':memory:')
        self.db.execute('create table songtable(id primary key,filename,fileurl,album,songer,style,name,composer,albumsonger,year,track,cd_num,time,time_end,time_create,Initial)')
        self.old=(7,"it's.flac","/m/it's.flac",'Old album','Old artist','Old genre','Old title','Old composer','Old album artist',1990,1,1,10 if cue else 0,20 if cue else 0,123,ord('O'))
        self.db.execute('insert into songtable values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',self.old)
        # Another CUE segment in the same file must keep its own tags.
        self.db.execute('insert into songtable values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(8,*self.old[1:12],30,40,456,ord('O')))
        for n in ('taglib_file_new','taglib_file_tag','taglib_file_free','toolsGetMusicInfo','toolsUpdateDbTable','toolsTrimLeft','toolsTrimRight','toolsHanziToPinyin','toolsGetMusicNum'):
            self.handlers[syms[n]]='refresh:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        if name=='scan:toolsCheckItemExist':
            # The real folder listing includes the path and any parsed CUE tags.
            self.word(a+O['REC_PATH'],self.string("/m/it's.flac"))
            if self.cue:
                for off,v in ((0x48,10),(0x4c,20),(0x30,2026),(0x3c,3),(0x40,2)): self.word(a+off,v)
                for off in (0x14,0x18,0x1c,0x10,0x20,0x24): self.word(a+off,self.string("CUE's tags"))
            ret=self.text(self.get(a+O['REC_NAME']))!="new.flac"
        elif name.startswith('refresh:'):
            name=name[8:]; ret=0
            if name=='toolsGetMusicNum': ret=self.get(SONGS)
            elif name=='taglib_file_new':
                assert b==0  # TagLib's vendor ABI: tags only, no audio-properties scan.
                ret=123 if self.valid else 0
            elif name=='taglib_file_tag': assert a==123; ret=456
            elif name in ('toolsTrimLeft','toolsTrimRight'): u.mem_write(a,self.text(a).strip().encode()+b'\0')
            elif name=='toolsHanziToPinyin': u.mem_write(b,self.text(a).encode()+b'\0')
            elif name=='toolsGetMusicInfo':
                self.reads+=1; assert (a,self.text(c))==(0,"/m/it's.flac"); ret=1
                for off in (0,0x100,0x200,0x280,0x480,0x580): u.mem_write(b+off,(self.title if off==0x280 else "New artist's tags").encode()+b'\0')
                for off,v in ((0x680,2026),(0x684,3),(0x688,2)): self.word(b+off,v)
            elif name=='toolsUpdateDbTable':
                assert self.text(a)=='/mnt/data/database.db'; self.updates.append(self.text(b)); self.db.execute(self.text(b)); ret=1
        else: return super().hook(u,address,size,unused)
        self.returns(ret)
    def rescan(self):
        return self.call(address=syms['toolsLoadAllFile'],args=(self.string('/m'),self.string('songtable'),1,0),gap=0)
for patched in (False,True):
    for cue in (False,True):
        for valid in (False,True):
            m=FolderTagsMachine(patched,cue,valid); assert m.rescan()==1 and not m.added and m.get(SONGS)==1
            row=m.db.execute('select * from songtable where id=7').fetchone()
            changed=patched and (cue or valid)
            assert row==((7,*m.old[1:3],*(["CUE's tags" if cue else "New artist's tags"]*6),2026,3,2,*m.old[12:15],ord('C' if cue else 'N')) if changed else m.old)
            assert m.db.execute('select * from songtable where id=8').fetchone()==(8,*m.old[1:12],30,40,456,ord('O'))
            assert len(m.updates)==changed and m.reads==(patched and not cue and valid); passed()
for title,letter in (("  lower title  ",'L'),('7 songs','*'),('!song','#'),('','I'),('(null)','I')):
    m=FolderTagsMachine(title=title); assert m.rescan()==1
    assert m.db.execute('select Initial from songtable where id=7').fetchone()==(ord(letter),); passed()
m=FolderTagsMachine(songs=65000)
m.tree['/m']=['new.flac',"it's.flac"]
assert m.rescan()==1 and not m.added and m.get(SONGS)==65000 and not m.u.mem_read(STOP,1)[0]
assert m.db.execute('select albumsonger from songtable where id=7').fetchone()==("New artist's tags",)
passed()
# Library Artists rows hide the head picture (CLASS_ICON); Composers and Genres, sharing the rebound
# rows, show theirs again through stock's setter.
from build import CLASS_ICON
assert struct.unpack_from('<I',demo,fileoff(demo,CLASS_ICON))[0]==jal(payload_syms['ringnav_class_icon'])
m=Machine(); m.mock('class_icon'); icon=m.node('image','img_icon'); row=m.node('view','',[icon])
for cls,shown in ((0xf004,0),(0xf006,1),(0xf005,1),(0xf004,0)):
    m.word(syms['g_class_type'],cls); m.calls=[]
    m.call(address=payload_syms['ringnav_class_icon'],args=(row,3,0,0),gap=0)
    assert m.nodes[icon]['visible']==shown and [c[1:3] for c in m.calls if c[0]=='class_icon']==([(row,3)] if shown else [])
passed()
# Library sorts: both stock name comparators, on the patched trim calls, skip a leading article.
from build import SORT_TRIMS
for a in SORT_TRIMS: assert struct.unpack_from('<I',demo,fileoff(demo,a))[0]==jal(payload_syms['ringnav_sort_key'])
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
m=SortMachine()
def sort_rec(name):
    r=m.alloc(0x60); m.word(r+0x10,m.string(name)); m.word(r+8,m.string(name+'.flac')); return r
def ordered(names,cmp):
    out=m.alloc(4)
    def less(x,y):  # the comparator stores x < y
        m.call(address=cmp,args=(x,y,out,0),gap=0,count=2_000_000); return m.get(out)
    rs={sort_rec(n):n for n in names}
    return [rs[r] for r in sorted(rs,key=cmp_to_key(lambda x,y: -1 if less(x,y) else 1 if less(y,x) else 0))]
names=['Daft Punk','The Cure','Coldplay','Them Crooked Vultures','A Tribe Called Quest','Anthrax','The','an Orchestra','Toto']
for cmp in (0x5ba658,0x5b9d40):
    assert ordered(names,cmp)==['Anthrax','Coldplay','The Cure','Daft Punk','an Orchestra','The','Them Crooked Vultures','Toto','A Tribe Called Quest'],ordered(names,cmp)
    passed()
key=lambda t: (m.u.mem_write(0x1100000,t.encode()+b'\0'), m.call(address=payload_syms['ringnav_sort_key'],args=(0x1100000,0,0,0),gap=0), m.text(0x1100000))[2]
assert [key(t) for t in ('  The Cure','THE CURE','The ','The  Cure','A','An ','Ant','周杰伦','The 周杰伦')]==['Cure','CURE','The ','The  Cure','A','An ','Ant','周杰伦','周杰伦']; passed()
# iPod boots to Home: home_page_init's memory-play resume, run from its stock context build, starts
# the restored queue paused (mode 3) through player_start as playing_page_init would, and opens no
# page; a 0xff class starts nothing, as the page's init. Car mode (mode 2) opens Now Playing as stock.
if variant=='ipod':
    boot=payload_syms['ringnav_boot']
    assert struct.unpack_from('<I',demo,fileoff(demo,0x523de0))[0]==jal(boot)
    for car,cls,want in ((0,1,[(0x5550,4,1,3)]),(0,0xff,[]),(1,0xf003,[])):
        m=Machine(); m.word(syms['g_memory_info'],cls); m.byte(syms['g_carmode'],car)
        m.u.reg_write(UC_MIPS_REG_GP,0xa26cc0); m.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
        m.u.reg_write(UC_MIPS_REG_S0,0x5550); m.u.reg_write(UC_MIPS_REG_S1,4)
        m.u.emu_start(0x523dac,0x523de8,count=1000)
        nav=[c for c in m.calls if c[0]=='navigator_to_with_context']
        assert m.started==want
        if car:
            assert len(nav)==1 and m.text(nav[0][1])=='playing_page' and nav[0][2]==0x7000f018
            assert [m.get(0x7000f018+4*i) for i in range(4)]==[0x5550,4,0xf003,2]
        else: assert not nav
        passed()
# Refusals leave the queue alone: a stream queue, or a row whose record changed under the menu.
m=QueueMachine(); m.word(O['MCL_TYPE'],2); assert m.run(0)=='Queue unchanged' and m.names()==['A','B','C']
m=QueueMachine(); m.press(1); m.hold(); m.release(); m.word(m.row(0)+O['REC_NAME'],m.string('other'))
m.pick(0); m.advance(0); assert m.toasts[-1][3]=='Queue unchanged' and m.names()==['A','B','C']; passed()
# Unsupported pages and states keep the stock long key: no menu, and the release is stock.
for setup,toggles in ((lambda m:m.byte(syms['g_lockscreen_pageflag'],1),1),(lambda m:m.byte(syms['g_backlight_status'],0),1),
        (lambda m:m.byte(syms['g_navbar_status'],1),1),
        (lambda m:m.word(m.surface+O['TABLE_ROWS'],7),1),(lambda m:m.word(m.row(0)+O['REC_TYPE'],4),1),
        (lambda m:setattr(m,'airplay',2),0),
        (lambda m:setattr(m,'top',m.node('window','home_page',[m.node()])),1)):
    m=QueueMachine(); setup(m); m.press(3)
    assert m.hold()==0 and len(m.stack)==1 and m.release()==toggles
    passed()
# Unknown rows (docs/internals.md#unknown-rows): stock's list (here the showlist as built, its size
# returned) keeps its trailing Unknown row (id -1) only when the query its press runs finds a song;
# the staging deque is left cleared. Other rows and classes stay stock.
class ClassMachine(QueueMachine):
    tramp=int(manifest['patch_symbols']['stock_localclass_trampoline'],16)
    split=[]  # Albums' own grouping (split), which toolsQueryDbTable stages
    def __init__(self,**kw): super().__init__(**kw); self.word(0xa2638c,0x1000010)  # free's GOT slot, as Coverflow's
    def hook(self,u,address,size,unused):
        if address==syms['toolsQueryDbTable']:
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.split]
            self.returns(len(self.split)); return
        libc={syms['strdup@GLIBC_2.0']:'strdup',0x1000010:'free'}
        if address in libc:  # the untagged rows' titles (ringnav_localclass)
            a=u.reg_read(REGS[0]); self.returns(self.string(self.text(a)) if libc[address]=='strdup' else 0); return
        if address!=self.tramp: return super().hook(u,address,size,unused)
        self.calls.append(('stock_localclass',u.reg_read(REGS[0]),0,0))
        assert self.get(payload_syms['library_music']) == 1
        self.returns(len(self.items(self.get(syms['p_deque_showlist']))))
def unknown(cls,found,last=-1,artist=0,rows=3):
    m=ClassMachine(page='localclass_page',cls=0xf001,rows=rows); m.found=[m.song('T1')]*found; m.query=None
    m.word(syms['artist_type'],artist); m.word(syms['g_artist_type'],1-artist)
    if rows: m.word(m.row(rows-1)+O['REC_ID'],last)
    n=m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
    assert m.get(payload_syms['library_music']) == 0
    shown=m.names(m.get(syms['p_deque_showlist']))
    # Artists follow the artist page's own switch (artist_type), which stock never copied to g_artist_type
    assert n==len(shown) and m.calls[0][:2]==('stock_localclass',cls) and m.get(syms['g_artist_type'])==artist
    return shown,m.query,m.items(m.get(syms['tools_pdeq_directory']))
for cls,artist,query in ((0xf003,0,'getMusicByAlbum'),(0xf004,0,'getMusicBySonger'),(0xf004,1,'getMusicByAlbumArtist'),
                         (0xf005,0,'getMusicByComposer'),(0xf006,0,'getMusicByGenre')):
    shown,q,staging=unknown(cls,0,artist=artist)
    assert shown==['Row 0','Row 1'] and q[:2]==(query,None) and not staging, (cls,artist)
    shown,q,staging=unknown(cls,1,artist=artist)
    assert shown==['Row 0','Row 1','Row 2'] and q[:2]==(query,None) and not staging, (cls,artist); passed()
for cls,last,rows in ((0xf004,1,3),(0xff01,-1,3),(0xff10,-1,3),(0xff11,-1,3),(0xf007,-1,3),(0xf004,-1,0)):
    shown,q,_=unknown(cls,0,last=last,rows=rows)
    assert len(shown)==rows and q is None, (cls,last,rows); passed()
# Every Library class and detail load scopes SQL, including empty results and failures, and
# restores an already-scoped caller. The trampoline supplies stock's displayed copies.
class ScopedClassMachine(ClassMachine):
    def hook(self,u,address,size,unused):
        super().hook(u,address,size,unused)
        if address==self.tramp and getattr(self,'failed',False): self.returns(-1)
for cls in (*range(0xf001,0xf00c),*range(0xff01,0xff15),0xff20):
    for scoped,rows,failed in ((0,1,False),(1,0,False),(1,0,True),(0,0,True)):
        m=ScopedClassMachine(rows=rows); m.failed=failed
        m.word(payload_syms['library_music'],scoped)
        m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
        assert m.get(payload_syms['library_music'])==scoped
# Saved Favourites and playlist entries stay stored, in order, including spoken-only lists.
paths=['/mnt/mmc/pOdCaStS/Show/episode.mp3','/mnt/mmc/Music/song.flac',
       '/mnt/mmc/AUDIOBOOKS/chapter.mp3','/mnt/mmc/elsewhere/BOOK.M4B',
       '/mnt/mmc/Podcasts Extra/song.mp3','/mnt/mmc/Music/Podcasts/song.mp3']
for cls in (0xf00a,0xff20):
    for selected in (paths,[paths[0],paths[2],paths[3]]):
        m=ClassMachine(rows=len(selected))
        for i,path in enumerate(selected):
            m.word(m.row(i)+O['REC_PATH'],m.string(path))
            m.word(m.row(i)+O['REC_TITLE'],m.string('Tagged'))
        stored=m.deque(m.items(m.get(syms['p_deque_showlist'])))
        before=[bytes(m.u.mem_read(r,0x60)) for r in m.items(stored)]
        n=m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
        want=[f'Row {i}' for i,path in enumerate(selected) if cls==0xf00a or path in paths[1:2]+paths[4:]]
        assert m.names(m.get(syms['p_deque_showlist']))==want and n==len(want)
        assert [bytes(m.u.mem_read(r,0x60)) for r in m.items(stored)]==before
# A playlist container remains even when its name/path looks like spoken audio.
m=ClassMachine(rows=1); m.word(m.row(0)+O['REC_TYPE'],4)
m.word(m.row(0)+O['REC_PATH'],m.string(paths[0]))
assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf00b,0,0,0),gap=0)==1
passed()
# iPod song rows: an untagged song's title is its file name without the extension; a tag stays.
m=ClassMachine(page='localclass_page',cls=0xf001,rows=2); m.word(m.row(1)+O['REC_TITLE'],m.string('Tagged'))
m.word(m.row(0)+O['REC_NAME'],m.string('a.flac')); m.word(m.row(0)+O['REC_PATH'],m.string('/p/a.flac'))
m.call(address=HOOKS['load_localclass_list'][0],args=(0xf001,0,0,0),gap=0)
assert variant!='ipod' or [m.text(m.get(m.row(i)+O['REC_TITLE'])) for i in (0,1)]==['a','Tagged']; passed()
# My Fav, Frequent and Recent: a row stock copied without its title takes the library's, by path, not
# its file name.
for cls in (0xf00a,0xf008,0xf007):
    m=ClassMachine(page='localclass_page',cls=0xf001,rows=2)
    m.word(m.row(0)+O['REC_NAME'],m.string('05-song.flac')); m.word(m.row(0)+O['REC_PATH'],m.string('/p/05-song.flac'))
    m.handlers.pop(syms['getAllMusic']); m.split=[m.song('Row 0'),m.song('05-song.flac')]; m.word(m.split[1]+O['REC_TITLE'],m.string('Song'))
    m.word(m.split[0]+O['REC_PATH'],m.string('/p/05-song.flac')); m.word(m.split[0]+O['REC_CUE_START'],1)  # a CUE image's other track
    m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
    t=m.get(m.row(1)+O['REC_TITLE'])  # no library title: as before
    assert m.text(m.get(m.row(0)+O['REC_TITLE']))=='Song' and (m.text(t)=='Row 1' if variant=='ipod' else not t), hex(cls)
passed()
# An empty category list says so once its page is up, instead of a black page.
m=ClassMachine(page='localclass_page',cls=0xf001,rows=0)
assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf006,0,0,0),gap=0)==0 and not m.toasts
m.advance(0); assert [t[3] for t in m.toasts]==['No genres']; passed()
# Albums: each stock row (a name) becomes that name's albums, told apart by album artist, else folder.
m=ClassMachine(page='localclass_page',cls=0xf001,rows=2); m.word(m.row(1)+O['REC_ALBUM'],m.string('Other'))
m.split=[m.song('Baroness'),m.song('STP')]; m.word(m.split[1]+O['REC_PATH'],m.string('/q/STP'))
assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf003,0,0,0),gap=0)==3
assert m.names(m.get(syms['p_deque_showlist']))==['Baroness','STP','Row 1']; passed()
# Run the actual Library query wrapper against SQLite, with mixed music/book/podcast tags and Unknowns.
# The flag is scoped by ringnav_localclass above; outside it (Coverflow, folders, Podcasts) SQL stays intact.
class QueryDone(Exception): pass
class LibraryQueryMachine(Machine):
    def __init__(self):
        super().__init__(); self.mock('strstr@GLIBC_2.0'); self.handlers.pop(syms['toolsQueryDbTable'])
        self.db=sqlite3.connect(':memory:')
        self.db.execute('create table songtable(id,album,songer,albumsonger,fileurl)')
        self.db.executemany('insert into songtable values(?,?,?,?,?)',[
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
lm=LibraryQueryMachine(); AA="ifnull(nullif(albumsonger,''),songer)"
for query in ('select id,songer from songtable group by songer COLLATE NOCASE',
              'select id,albumsonger from songtable group by albumsonger COLLATE NOCASE',
              'select id,album,songer,fileurl from songtable group by album COLLATE NOCASE',
              "select id,album,songer,fileurl,albumsonger from songtable group by album COLLATE NOCASE,ifnull(albumsonger,'') COLLATE NOCASE,case ifnull(albumsonger,'') when '' then rtrim(fileurl,replace(fileurl,'/','')) end",
              "select * from songtable where ifnull(songer,'')='' order by id"):
    for scoped in (0,1,0):
        lm.word(payload_syms['library_music'],scoped)
        lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
        assert (lm.sql==(query.replace('albumsonger',AA) if 'by albumsonger' in query else query)) == (not scoped)
        assert {r[0] for r in lm.results} == ({2} if scoped and 'group by' in query else set() if scoped else
                                             {1,3,4,5,6} if 'group by' in query else {6}),lm.results
# The stock Songs title sorter uses the separate AndSort helper.
lm.word(payload_syms['library_music'],1)
lm.call(address=syms['sort_first_alphabet'],args=(0,0,0,0),gap=0)
assert [r[0] for r in lm.results]==[2] and 'where '+ 'fileurl' in lm.sql
passed()
# Podcast paths are case insensitive and bounded by the top-level folder. Shared tags must use
# the music row for artwork, and untagged episodes must not create Unknown Artist/Album rows.
lm.db.executemany('insert into songtable values(?,?,?,?,?)',[
    (10,'Shared','Shared','Shared','/mnt/mmc/pOdCaStS/Show/episode.mp3'),
    (11,'','','','/mnt/mmc/PODCASTS/untagged.mp3'),
    (12,'Music','Singer','Singer','/mnt/mmc/Podcasts Extra/song.mp3'),
    (13,'Nested','Singer','Singer','/mnt/mmc/Music/Podcasts/song.mp3'),
])
for scoped in (1,0):
    lm.word(payload_syms['library_music'],scoped)
    for column in ('album','songer','albumsonger'):
        query=f'select id,{column},fileurl from songtable group by {column} COLLATE NOCASE'
        lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
        if scoped:
            assert {r[0] for r in lm.results} == ({2,12,13} if column=='album' else {2,12}),lm.results
        else:
            assert any(r[0]==5 for r in lm.results) and any(not r[1] for r in lm.results)
# Genre/composer lists, their albums and song detail queries use the same pre-group scope.
for column in ('genre','composer'):
    lm.db.execute(f'alter table songtable add column {column}')
    lm.db.execute(f"update songtable set {column}=case when id in (6,11) then '' else album end")
    for query,want in ((f'select id,{column},fileurl from songtable group by {column} COLLATE NOCASE',{2,12,13}),
                       (f"select id,album,fileurl from songtable where {column}='Shared' group by album COLLATE NOCASE",{2}),
                       (f"select id,fileurl from songtable where {column}='Shared' and album='Shared'",{2}),
                       (f"select id,fileurl from songtable where ifnull({column},'')=''",set())):
        lm.word(payload_syms['library_music'],1)
        lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
        assert {r[0] for r in lm.results}==want,(query,lm.results)
# Direct song/history/Hi-Res loads retain only the music rows, in the requested order.
for order in ('id','id desc'):
    query=f'select id,fileurl from songtable order by {order}'
    lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
    assert [r[0] for r in lm.results]==sorted([2,12,13],reverse=order.endswith('desc'))
for column in ('genre','composer'): lm.db.execute(f'alter table songtable drop column {column}')
lm.db.execute('delete from songtable where id >= 10')
# Music staging applies the scope for stock queries and restores either caller state.
class MusicStageMachine(QueueMachine):
    def hook(self,u,address,size,unused):
        if address==syms['getMusicByAlbum']:
            assert self.get(payload_syms['library_music'])==1
        return super().hook(u,address,size,unused)
for scoped in (0,1):
    ms=MusicStageMachine(); ms.word(payload_syms['library_music'],scoped)
    rows=ms.call(address=payload_syms['staged_music'],args=(syms['getMusicByAlbum'],ms.string('Album'),ms.alloc(4),0),gap=0)
    assert ms.get(payload_syms['library_music'])==scoped and ms.query[:2]==('getMusicByAlbum','Album')
class MusicLoadMachine(QueueMachine):
    def __init__(self):
        super().__init__(); self.handlers[syms['getAllMusic']]='music_load'
    def hook(self,u,address,size,unused):
        if address!=syms['getAllMusic']: return super().hook(u,address,size,unused)
        assert self.get(payload_syms['library_music'])==1
        self.returns(self.result)
for loader,args in (('all_songs',(1,)),('songs_unsorted',(1,))):
    for scoped in (0,1):
        for result in (-1,0,2):
            ms=MusicLoadMachine(); ms.result=result; ms.word(payload_syms['library_music'],scoped)
            assert ms.call(address=payload_syms[loader],args=args,gap=0)==result
            assert ms.get(payload_syms['library_music'])==scoped
passed()
# Album Artist: an album without the tag lists under its artist (stock's albumsonger queries), a
# tagged one under its album artist; Coverflow's own grouping keeps the tag as it is.
lm.db.executemany('insert into songtable values(?,?,?,?,?)',[(7,'Disintegration','The Cure','','/mnt/mmc/Music/D/CD1/1.flac'),
                  (8,'Wish','The Cure','The Cure','/mnt/mmc/Music/W/1.flac'),(9,'Guest','The Cure','Other','/mnt/mmc/Music/G/1.flac')])
lm.word(payload_syms['library_music'],0)
for query,want in (("select id,album,songer,fileurl,albumsonger from songtable where albumsonger like 'the cure' group by album COLLATE NOCASE",{7,8}),
                   ("select * from songtable where album like 'Disintegration' and albumsonger like 'The Cure'",{7}),
                   ('select * from songtable where albumsonger=""',{6}),
                   ("select id,album,songer,fileurl,albumsonger from songtable group by album COLLATE NOCASE,ifnull(albumsonger,'') COLLATE NOCASE,case ifnull(albumsonger,'') when '' then rtrim(fileurl,replace(fileurl,'/','')) end having album like 'Disintegration'",{7})):
    lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
    assert {r[0] for r in lm.results}==want and (lm.sql==query)==('ifnull(albumsonger' in query),(query,lm.sql,lm.results)
lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string('select id,albumsonger from songtable group by albumsonger COLLATE NOCASE'),123,1),gap=0)
assert sorted(r[1] for r in lm.results)==['','Author','Host','Other','Shared','The Cure','Writer'],lm.results
# An artist's albums tab: getAlbumByAlbumArtist runs its two queries through toolsQueryContentDbTable,
# rewritten and scoped the same way: Unknown Artist's albums are its songs' (neither tag).
for scoped,artist,want in ((0,'The Cure',{7,8}),(0,'',{6}),(1,'The Cure',{7,8}),(1,'',set())):
    lm.word(payload_syms['library_music'],scoped)
    try: lm.call(address=syms['getAlbumByAlbumArtist'],args=(lm.string(artist),),gap=0)
    except QueryDone: pass
    assert lm.content[1:]==(artist,1) and AA in lm.sql and {r[0] for r in lm.results}==want,(artist,lm.sql,lm.results)
lm.word(payload_syms['library_music'],1); lm.sql=None
assert lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string('select * from songtable'+' '*2048),123,1),gap=0)==-1
assert lm.sql is None
lm.db.close(); passed()
# Their stock art cache names (/mnt/mmc/.sldp/<md5>0.jpg): a shared name adds the album artist,
# a name alone stays; the album page's big cover follows its row's cover task name.
for e in m.items(m.get(syms['p_deque_showlist']))[:2]: m.word(e+O['REC_ALBUM_ARTIST'],m.get(e+O['REC_NAME']))
art=payload_syms; buf=m.alloc(0x100)
keys=['Album\x1fBaroness','Album\x1fSTP','Other']
for i,want in enumerate(keys):
    m.call(address=art['ringnav_art_name'],args=(buf,0x100,i,0),gap=0); assert m.text(buf)==want
covers=[m.alloc(12) for _ in keys]
for c,k in zip(covers,keys): m.word(c+4,m.string(k))
m.word(syms['pdeq_albumcoverlist'],m.deque(covers))
for row,want in ((1,'Album\x1fSTP'),(2,'Album'),(9,'Album')):
    m.word(O['ALBUMINFO_ROW'],row); m.call(address=art['ringnav_art_header'],args=(buf,0x100,m.string('%s'),m.string('Album')),gap=0)
    assert m.text(buf)==want, row
passed()
# An artist's albums tab has one row of a shared name: it takes its album's key in the split
# order, by folder, else by album artist; a name alone, and the songs tab, stay stock's.
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
for path,artist,want in (('/p/x','Artist','Album\x1fBaroness'),('/z/x','STP','Album\x1fSTP'),('/z/x','Artist','Album')):
    m=ArtistMachine(page='localclass_page',cls=0xff01,rows=2); m.word(m.row(1)+O['REC_ALBUM'],m.string('Other'))
    m.word(m.row(0)+O['REC_PATH'],m.string(path)); m.word(m.row(0)+O['REC_ARTIST'],m.string(artist))
    m.split=[m.song('Baroness'),m.song('STP')]; m.word(m.split[1]+O['REC_PATH'],m.string('/q/STP'))
    for e in m.split: m.word(e+O['REC_ALBUM_ARTIST'],m.get(e+O['REC_NAME']))
    assert m.call(address=HOOKS['load_localartist_list'][0],args=(1,0,0,0),gap=0)==2
    # Run the stock Album-tab and page-return call sites, not just the key helper: 9.8
    # left these unhooked, so the worker wrote the old cache name the renderer never read.
    m.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
    m.cover_flags=0x101  # an old job completes between stock init and key renaming
    for site in (0x4ac840,0x4ae860,0x4ae968,0x4aebc0):
        m.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
        m.u.reg_write(UC_MIPS_REG_V0,syms['p_deque_showlist'])
        m.u.reg_write(UC_MIPS_REG_T9,syms['albumcoverinfo_init'])
        m.u.emu_start(site,site+8,count=m.budget)
        assert m.u.reg_read(UC_MIPS_REG_PC)==site+8
        covers=m.items(m.get(syms['pdeq_albumcoverlist']))
        assert [m.text(m.get(c+4)) for c in covers]==[want,'Other'], hex(site)
        assert [m.get(c)&0xffff for c in covers]==[0 if want!='Album' else 0x101,0x101]
        assert [m.text(m.get(c+8)) for c in covers]==[path,'/p/Row 1']
    for i,k in enumerate((want,'Other')):
        m.u.reg_write(UC_MIPS_REG_GP,0xa26cc0)
        m.u.reg_write(UC_MIPS_REG_S1,buf); m.u.reg_write(UC_MIPS_REG_S2,i)
        m.u.reg_write(UC_MIPS_REG_V0,m.row(i)); m.u.reg_write(UC_MIPS_REG_A1,0x100)
        m.u.emu_start(0x4ab7f4,0x4ab80c,count=m.budget)
        assert m.u.reg_read(UC_MIPS_REG_PC)==0x4ab80c
        assert m.text(buf)==k, (path,artist,i,m.text(buf))
    m.call(address=HOOKS['load_localartist_list'][0],args=(0,0,0,0),gap=0)
    m.call(address=art['ringnav_artist_art_name'],args=(buf,0x100,0,m.string('Album')),gap=0); assert m.text(buf)=='Album'
passed()
# Artist albums: Coverflow (Display settings): the albums tab's split albums go to Coverflow once the artist page is
# up, once per page, so coming back from it shows the grid; Grid, the songs tab or another page leave it stock. Until
# then the artist page is unseen (opacity 0) and has no slide, so the Artists list cuts straight to the covers; once
# they are up it is shown under them with its slide back.
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
for on,tab,page,want in ((1,1,'artistinfo_page',[1]),(2,1,'artistinfo_page',[1]),(0,1,'artistinfo_page',[]),(1,0,'artistinfo_page',[]),(1,1,'localclass_page',[])):
    m=ArtistCoverflow(page='localclass_page',cls=0xff01,rows=2); m.handed=[]
    m.word(m.row(1)+O['REC_ALBUM'],m.string('Other album'))
    m.config['ARTISTCF']=str(on)
    m.split=[m.song('Baroness'),m.song('STP')]
    for r in m.split: m.word(r+O['REC_ALBUM_ARTIST'],m.get(r+O['REC_NAME']))
    m.found=[m.split[0]]; m.artist_page(page)
    m.call(address=HOOKS['load_localartist_list'][0],args=(tab,0,0,0),gap=0)
    assert m.veiled()==bool(want); m.advance(0)
    assert m.handed==want and m.shown(),(on,tab,page,m.handed)
    if want:  # back from Coverflow, the tab loads again on the same page: the grid stays, seen
        m.call(address=HOOKS['load_localartist_list'][0],args=(tab,0,0,0),gap=0); assert m.shown(); m.advance(0); assert len(m.handed)==1
        # The page gone, another at the same address gets Coverflow again.
        f,ctx=m.handler(m.top,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
        m.call(address=HOOKS['load_localartist_list'][0],args=(tab,0,0,0),gap=0); m.advance(0); assert len(m.handed)==2
    passed()
# Artist albums: Grid shows the artist page's hidden stock toolbar (its multi-select) on either
# tab, the tabs moved down under it; other modes, pages or a toolbar already up stay as they are.
for on,tab,page,up in ((0,1,'artistinfo_page',0),(0,0,'artistinfo_page',0),(1,1,'artistinfo_page',0),
                       (2,1,'artistinfo_page',0),(0,1,'localclass_page',0),(0,1,'artistinfo_page',1)):
    m=ArtistCoverflow(page='localclass_page',cls=0xff01,rows=1); m.handed=[]; m.found=[]
    m.config['ARTISTCF']=str(on); m.artist_page(page)
    album=m.node('list_view','list_view_album'); pages=m.node('pages','pages',[album])
    bar=m.node('tab_control','tab_control',[pages]); nav=m.node('view','view_navbar',visible=up)
    m.nodes[nav]['enable']=up; m.nodes[m.top]['children']+=[nav,bar]
    geo=lambda w:[m.get(w+O[o]) for o in ('W_X','W_Y','W_W','W_H')]
    for w,g in ((nav,(0,0,375,50)),(bar,(0,0,375,290)),(pages,(0,40,375,250)),(album,(0,0,375,250))):
        for o,v in zip(('W_X','W_Y','W_W','W_H'),g): m.word(w+O[o],v)
    m.call(address=HOOKS['load_localartist_list'][0],args=(tab,0,0,0),gap=0)
    want=not on and page=='artistinfo_page' and not up
    assert bool(m.nodes[nav]['visible'])==bool(want or up) and bool(m.nodes[nav]['enable'])==bool(want or up),(on,tab,page,up)
    assert geo(bar)==([0,50,375,240] if want else [0,0,375,290]) and geo(album)[3]==(200 if want else 250),(on,tab,page,up)
    assert m.nodes[pages].get('self_layout')==(('0','40','100%','200') if want else None),(on,tab,page,up)
    passed()
# Same-named albums by other artists stay out, in both artist modes. Membership follows
# the artist's songs, so a guest artist and multiple albums of one name work too.
for mode in (0,1):
    m=ArtistCoverflow(page='localclass_page',cls=0xff01,rows=1); m.handed=[]; m.config['ARTISTCF']='1'
    m.word(syms['artist_type'],mode)
    m.u.mem_write(syms['g_local_classinfo_save']+0x10d,b'Artist\0')
    m.word(m.row(0)+O['REC_ARTIST'],m.string('Guest singer'))  # selected album artist comes from page context
    m.split=[m.song(name) for name in ('Selected','Other','Guest')]
    for r in m.split:
        m.word(r+O['REC_ALBUM_ARTIST'],m.get(r+O['REC_NAME']))
    m.found=[m.split[0],m.split[2],m.split[0]]  # duplicates must not create extra cards
    staging=m.names(m.get(syms['tools_pdeq_directory'])); m.artist_page('artistinfo_page')
    assert m.call(address=HOOKS['load_localartist_list'][0],args=(1,0,0,0),gap=0)==1
    assert m.query[:2]==(('getMusicByAlbumArtist' if mode else 'getMusicBySonger'),'Artist')
    assert m.names(m.get(syms['tools_pdeq_directory']))==staging
    m.advance(0)
    assert m.covers==['Selected','Guest'] and m.handed==[2],(mode,m.covers)
    assert m.names(m.get(syms['p_deque_showlist']))==['Row 0']  # the Library grid stays intact
    passed()
# No albums in the selected artist query leaves the grid rather than opening an empty Coverflow.
m=ArtistCoverflow(page='localclass_page',cls=0xff01,rows=1); m.handed=[]; m.config['ARTISTCF']='1'; m.found=[]
m.artist_page('artistinfo_page'); m.call(address=HOOKS['load_localartist_list'][0],args=(1,0,0,0),gap=0)
m.advance(0)
assert m.handed==[0] and m.shown(); passed()  # coverflow_artist rejects the empty list (test/coverflow.py)
# A local art job that finishes after a list rebuild must not mark the new row ready.
# Run the worker's real completion call site, including its stack-buffer argument, then stock's
# readiness check (used by the renderer and by the request queue to skip completed covers).
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
m=CoverQueueMachine()
def cover_requests(start,big):
    name='local_albumcover_task' if big else 'local_albumsmallcover_task'
    m.call(address=syms[name],args=(start,9,0,0),gap=0)
    return [row for row,kind,size in m.pending if kind==0 and size==big]
def cover_ready(row,big):
    return m.call(address=syms['check_albumcover_flag'],args=(row,big,0,0),gap=0)
def cover_done(row,big,key):
    sp=0x7000d000
    m.u.mem_write(sp+0x1688,key.encode()+b'\0')
    m.word(sp+0x1828,row); m.word(sp+0x1830,big); m.word(sp+0x18,0xa26cc0)
    m.u.reg_write(UC_MIPS_REG_SP,sp); m.u.reg_write(UC_MIPS_REG_GP,0xa26cc0)
    m.u.emu_start(0x4edd98,0x4edda8,count=m.budget)
    assert m.u.reg_read(UC_MIPS_REG_PC)==0x4edda8
for visit in range(5):
    keys=[f'Artist {visit} album {i}' for i in range(12)]
    covers=[]
    for key in keys:
        c=m.alloc(0x60); m.word(c+4,m.string(key)); covers.append(c)
    m.word(syms['pdeq_albumcoverlist'],m.deque(covers))
    if visit==0:
        fixed=bytes(m.u.mem_read(0x4edd9c,8))
        m.u.mem_write(0x4edd9c,struct.pack('<II',0x8f99b6d4,0x0411ff44))
        m.u.ctl_remove_cache(0x4edd98,0x4edda8)
        cover_done(11,1,'previous artist')
        assert cover_ready(11,1)==1  # stock falsely completes the last album
        assert cover_requests(9,1)==[9,10]  # scrolling cannot request its missing art
        m.byte(m.items(m.get(syms['pdeq_albumcoverlist']))[11]+1,0)
        m.u.mem_write(0x4edd9c,fixed)
        m.u.ctl_remove_cache(0x4edd98,0x4edda8)
    for row in (0,8,9,11):  # first screen, scroll boundary and last album
        for big in (0,1):
            cover_done(row,big,f'Artist {visit-1} album {row}')
            assert cover_ready(row,big)==0, (visit,row,big)
            assert row in cover_requests(row//9*9,big), (visit,row,big)
            cover_done(row,big,keys[row])
            assert cover_ready(row,big)==1, (visit,row,big)
            assert row not in cover_requests(row//9*9,big), (visit,row,big)
    for row in (-1,12): cover_done(row,1,'gone')
# The folder-cover path (0x4eeddc) reaches the same completion call with its own copied key, not
# whatever its last call left in a2.
def folder_done(row,key,a2):
    sp=0x7000d000
    m.u.mem_write(sp+0x1688,key.encode()+b'\0'); m.word(sp+0x1828,row); m.word(sp+0x18,0xa26cc0)
    m.u.reg_write(UC_MIPS_REG_SP,sp); m.u.reg_write(UC_MIPS_REG_GP,0xa26cc0)
    m.u.reg_write(UC_MIPS_REG_S2,1); m.u.reg_write(UC_MIPS_REG_A2,m.string(a2))
    m.u.emu_start(0x4eedd4,0x4edda8,count=m.budget)
    assert m.u.reg_read(UC_MIPS_REG_PC)==0x4edda8
for key,a2,ready in ((keys[3],'left over',1),('previous folder',keys[3],0)):
    m.byte(m.items(m.get(syms['pdeq_albumcoverlist']))[3],0)
    folder_done(3,key,a2); assert cover_ready(3,0)==ready, (key,a2)
passed()
# Scrolling never leaves the shared cover worker paused (albumcover_taskstate 2): stock resumed it only
# when scroll end saw a new first row, so a wheel tick or a return to a scrolled grid stopped every
# grid's art. An artist's albums scroll end takes rows as 160 px, three a row, not 256.
class ScrollEndMachine(CoverQueueMachine):
    def hook(self,u,address,size,unused):
        if address!=syms['toolsGetSysMemFree']: return super().hook(u,address,size,unused)
        self.returns(1<<20)
m=ScrollEndMachine(); m.mock('widget_lookup','widget_get_prop_int')
for pause in (0x4aab40,0x4a22b0):  # an artist's albums, Library Albums
    m.word(syms['albumcover_taskstate'],1)
    m.call(address=pause,args=(0,0,0,0),gap=0); assert m.get(syms['albumcover_taskstate'])==1, hex(pause)
covers=[]
for i in range(30):
    c=m.alloc(0x60); m.word(c+4,m.string(f'album {i}')); covers.append(c)
m.word(syms['pdeq_albumcoverlist'],m.deque(covers))
view=m.node('scroll_view','scroll_view_album',yoffset=800); win=m.node('window','artistinfo_page',[view])
m.word(0xa27678,1); m.word(0xa2767c,0)  # the Albums tab; its last requested first row
for tick in range(2):  # the second, at the same offset, requests nothing and leaves the worker running
    m.pending.clear(); m.call(address=0x4ad7c0,args=(win,0,0,0),gap=0)
    assert [row for row,kind,size in m.pending]==([] if tick else list(range(15,24))), m.pending
    assert m.get(0xa2767c)==15 and m.get(syms['albumcover_taskstate'])==1
passed()
# A row opens its name's songs (0xff10, getMusicByAlbum): only its own album's stay.
m=ClassMachine(page='album_page',cls=0xf003,rows=2); m.word(m.row(1)+O['REC_PATH'],m.string('/q/Row 1'))
assert m.call(address=HOOKS['load_album_detaillist'][0],args=(0xf003,m.song('Album'),0,0),gap=0)==1
assert m.names(m.get(syms['p_deque_showlist']))==['Row 0'] and ('stock_localclass',0xff10,0,0) in m.calls; passed()

# Coverflow (docs/internals.md): the Home card, the runtime coverflow_page over a stock slide_menu,
# the tracks query and handoff. The art thread itself runs on the host (test/coverflow.py).
from ipod import HOME_TEXT_X, HOME_LIST_W, HOME_PAGE, HOME_ROW, HOME_ROWS, HOME_TOP, decode
cards=decode((B/'ui'/HOME_PAGE).read_bytes())[3][1 if variant=='ipod' else 0]  # the carousel, or iPod's list_view
if variant=='ipod': cards=cards[3][0]  # its scroll_view of rows
cards=[c[2]['name'] for c in cards[3]]
assert len(cards)==7 and cards[2]=='btn_coverflow', cards
home_hook=HOOKS['home_page_init']
assert struct.unpack_from('<I',demo,fileoff(demo,home_hook[0]))[0]==j(payload_syms['coverflow_home'])
passed()
def asset_tree(m,path):
    """A built UI asset as mock widgets, the window's parent the window manager, with what the payload
    reads: type, name, visible, enable, geometry and, for a scroll view, its content height and the
    slidable flags as AWTK sets them. scroll_view_create (0x5f146c) leaves both off, the asset's
    xslidable/yslidable props set them (0x5f1d34), and a list_view's layout (0x5ea3a4) clears x and
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
    imgs=[named(m,m.top,'img_'+r) for r in HOME_ROWS]
    for img in imgs[:2]+imgs[3:]: click_target(m,img)
    return named(m,m.top,'scroll_view_home'),imgs

# Return is a button too: after a touch, the page it goes back to shows its selection again.
m=Machine(); w,es=m.page_list(3); m.paint(w); m.touch(); m.paint(w,gap=0); assert not m.drawn()
assert m.call(O['KEY_RETURN'])==0; m.advance(0); m.paint(w,gap=0); assert m.drawn(); passed()

if variant=='ipod':
    # Page slides (navigation.c slides()): the page under a sliding top window draws the row it holds; no load, recall or scroll.
    HINT='htranslate'  # any non-empty anim_hint
    def under_slide(hint=HINT):
        m=Machine(); w,es=m.page_list(3); m.paint(w); assert m.call()==11 and m.selected(w)==1
        m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
        w2,_=m.page_list(3,name='display_page'); m.nodes[m.top]['anim_hint']=hint
        return m,w,w2
    m,w,w2=under_slide(); m.paint(w,gap=0)
    assert m.drawn() and m.sel()[1]==48-m.get(w+O['SCROLL_Y']) and m.selected(w)==1 and not m.moved(); passed()
    m,w,w2=under_slide(''); m.paint(w,gap=0); assert not m.drawn(); passed()  # under a page that does not slide
    # Touch mode hides it there too, until a Return by button, which shows the page behind its row
    # while the closing page keeps hiding its own.
    m,w,w2=under_slide(); m.paint(w2); m.touch(); m.paint(w,gap=0); assert not m.drawn()
    assert m.call(O['KEY_RETURN'])==0; m.paint(w,gap=0); assert m.drawn()
    m.paint(w2,gap=0); assert not m.drawn(); passed()
    # A sliding page's first paint is its snapshot, so a recalled row is revealed at once, not by a glide.
    m,w,es=walk(10,5); w2,es2=m.page_list(10,extent=1000); m.nodes[m.top]['anim_hint']=HINT
    assert m.paint(w2)==0 and m.selected(w2)==5 and m.get(w2+O['SCROLL_Y'])==204
    assert [c[0] for c in m.moved()]==['scroll_view_set_offset']; passed()

    # Home is an ordinary list: the wheel walks the seven rows one by one, bumps at both ends and
    # wraps after a pause, Return selects Now Playing, the bar spans the list's width and centre
    # clicks the image.
    m=Machine(); view,imgs=home_list(m); click_target(m,imgs[2])
    m.paint(view)
    assert m.selected(view)==0
    for i in range(1,7): assert m.call()==11 and m.selected(view)==i and m.get(view+O['SCROLL_Y'])==0
    m.paint(view); assert m.rounded[0]['rect']==(25,6*HOME_ROW+HOME_ROW//2-3,6,6)
    assert m.call(gap=50)==11 and m.selected(view)==6   # bumped
    assert m.call(gap=O['EDGE_PAUSE_MS'])==11 and m.selected(view)==0
    assert m.call(O['KEY_PREV'],gap=50)==11 and m.selected(view)==0   # bumped
    assert m.call(O['KEY_PREV'],gap=O['EDGE_PAUSE_MS'])==11 and m.selected(view)==6
    for i in range(5,2,-1): assert m.call(O['KEY_PREV'])==11 and m.selected(view)==i
    assert m.call(O['KEY_RETURN'])==11 and m.selected(view)==0 and not destinations(m)
    for i in (1,2): assert m.call(gap=1000)==11 and m.selected(view)==i
    assert not m.slides and m.confirm()==11 and m.dispatched()[0][1]==imgs[2]; passed()

    # Boot: if Home's first paint comes before the screen is usable, no row is chosen or drawn. The
    # status bar's next paint repaints the list, once, and Now Playing gets the bar.
    m=Machine(); view,imgs=home_list(m); bar=m.node('window','system_bar'); m.word(syms['system_bar'],bar); m.word(bar+O['W_PARENT'],m.wm)
    paint_bar=lambda: m.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(bar,m.canvas,0,0))
    m.byte(syms['g_backlight_status'],0); m.paint(view); assert m.selected(view)==-1 and not m.drawn()
    paint_bar(); assert ('widget_invalidate_force',view) not in [c[:2] for c in m.calls]
    m.touch(); m.byte(syms['g_backlight_status'],1)
    paint_bar(); assert ('widget_invalidate_force',view) in [c[:2] for c in m.calls]
    m.paint(view); assert m.selected(view)==0 and m.rounded[0]['rect']==(25,HOME_ROW//2-3,6,6)
    paint_bar(); assert ('widget_invalidate_force',view) not in [c[:2] for c in m.calls]; passed()

    # Chevrons: the stock list_into, where stock rows put img_into, on each visible row of a
    # drill window (contexts.inc), clipped to the surface; Home's only on its selection bar.
    # Stock draws its own on folder, category, album-list and Local Music rows, so those windows,
    # song lists and grids get none from here.
    def window(m,name,w):
        m.top=m.node('window',name,[w]); m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
    def loaded(m): return [m.text(c[2]) for c in m.calls if c[0]=='widget_load_image']
    m=Machine(); view,imgs=home_list(m); click_target(m,imgs[2]); m.clip=(0,0,375,320)
    m.paint(view)
    half=O['CHEVRON_W']-25  # centre of the 50px image, as stock img_into
    bar=lambda: []
    assert m.rounded[0]['rect']==(25,HOME_ROW//2-3,6,6) and m.icons==bar()
    assert loaded(m)==[] and m.clip==(0,0,375,320); passed()
    m.call(); m.paint(view); assert m.selected(view)==1 and m.rounded[0]['rect'][1]>0 and m.icons==bar(); passed()  # follows the bar
    # Drawn in touch mode too, with Home's bar, which touch never hides; none while stock
    # multi-select hides its own.
    m.touch(); m.paint(view); assert m.icons==bar() and m.selected(view)==1; passed()
    m.byte(syms['g_navbar_status'],1); m.paint(view); assert not m.icons and not loaded(m); passed()
    # Classic: Home's accent bar spans the list and the chevron rides it alone.
    m=Machine(); m.config['THEME']='1'; view,imgs=home_list(m); click_target(m,imgs[2]); m.clip=(0,0,375,320)
    m.paint(view)
    cbar=lambda: [(HOME_LIST_W-half+O['HOME_CHEVRON_SHIFT'],m.sel()[1]+HOME_ROW//2,(0,0,HOME_LIST_W,7*HOME_ROW))]
    assert m.sel()==(0,0,HOME_LIST_W,HOME_ROW) and m.icons==cbar() and not m.rounded
    assert m.bands[1][4]==color_t(ACCENTS[0][0]) and loaded(m)==['list_into'] and m.clip==(0,0,375,320)
    m.call(); m.paint(view); assert m.selected(view)==1 and m.sel()[1]>0 and m.icons==cbar(); passed()
    # Playlists: every row follows the scroll, clipped to the viewport; the half-width
    # Import/Export tiles get none.
    m=Machine(); w,es=m.page_list(10,height=96,extent=480,name='playlist_page'); window(m,'playlist_page',w)
    tile=m.entry(w,0); m.word(tile+O['W_W'],100); m.nodes[w]['children'].insert(0,tile)
    for top in (0,24):
        m.word(w+O['SCROLL_Y'],top); m.paint(w)
        assert m.icons==[(240-half,48*i+24-top,(0,0,240,96)) for i in range(10)]; passed()
    # Leaf lists, grids and windows whose rows stock already marks draw none.
    for name in ('allmusic_page','albuminfo_page','artistinfo_page','album_page','folder_page',
                 'localclass_page','localmusic_page','sysset_page'):
        m=Machine(); w,_=m.page_list(5,extent=240,name=name); window(m,name,w); m.paint(w)
        assert not m.icons and not loaded(m), name; passed()

if variant=='ipod':
    # The status bar as built (ui/system_bar.bin), laid out by the stock row layouter
    # (children_layouter_default, 0x628838) with every icon shown: the icons, 16px high and drawn
    # centred in their 30px cells, and the title clear the glass's rounded top corners.
    from ipod import STATUS_BAR, corner_inset
    m=Machine(); m.mock('strtol@GLIBC_2.0','strstr@GLIBC_2.0'); m.mock('tk_calloc','tk_free',prefix='alloc:')
    bar=asset_tree(m,STATUS_BAR); m.word(bar+O['W_W'],375)
    views=[named(m,bar,n) for n in ('view_left','view_right')]
    for v in views:
        layout=m.call(address=syms['children_layouter_default_create'],args=(0,0,0,0),gap=0)
        for param in re.fullmatch(r'default\((.*)\)',m.nodes[v]['asset']['children_layout'])[1].split(','):
            assert m.call(address=syms['children_layouter_set_param_str'],args=(layout,*map(m.string,param.split('=')),0),gap=0)==0
        m.word(v+O['W_CHILDREN_LAYOUT'],layout)
    def laid_out():
        for v in views: native_row_layout(m,v)
        return [(signed(m.get(v+O['W_X']))+signed(m.get(c+O['W_X'])),m.get(c+O['W_W']),m.get(c+O['W_H'])) for v in views
                for c in m.nodes[v]['children'] if m.nodes[c]['visible']]
    cells=laid_out()
    inset=corner_inset((30-16)//2)
    assert all(h==30 and inset<=x and x+w<=375-inset for x,w,h in cells), (inset,cells)
    title=named(m,bar,'label_clock'); x,w=m.get(title+O['W_X']),m.get(title+O['W_W'])
    left,right=(cells[len(m.nodes[views[0]]['children'])-1],cells[len(m.nodes[views[0]]['children'])])
    assert left[0]+left[1]<=x and x+w<=right[0] and x+w/2==375/2, (left,right,x,w); passed()
    # Each Battery mode (bar_sync shows one of the three): the layout skips the hidden two, the
    # battery ends at the icons' margin, a percentage's text clears the corner there, and with
    # the plain Bluetooth glyph and Wi-Fi the group's ink stays CLOCK_GAP clear of the widest clock.
    from ipod import CLOCK_TEXT, CLOCK_GAP, STATUS_MARGIN, corner_x
    batt=[named(m,bar,n) for n in ('img_battery','label_battery','view_battery')]
    for mode,want in enumerate((10,O['BATT_PCT_W'],O['BATT_BODY_W']+O['BATT_NUB_W']+O['BATT_GAP']+O['BATT_PCT_W'])):
        for i,b in enumerate(batt): m.nodes[b]['visible']=int(i==mode)
        cells=laid_out(); bt=cells[len(m.nodes[views[0]]['children'])]
        assert cells[-1][0]+cells[-1][1]==375-STATUS_MARGIN and cells[-1][1]==want, (mode,cells)
        if mode==1: assert corner_x((30-O['BATT_PCT_PX'])//2,O['BATT_PCT_PX'])<=STATUS_MARGIN
        ink=bt[0]+bt[1]-O['BT_REACH']
        assert ink>=375/2+CLOCK_TEXT/2+CLOCK_GAP and 375-STATUS_MARGIN-ink<=O['BATT_ROOM'], (mode,cells)
    passed()

    # Cycle the live Battery setting with the actual parent row layouter. Icon
    # remains visible after Icon + Percent, so a width change must reposition it.
    m.config={'BATTERY':'0'}
    m.word(bar+O['W_PARENT'],m.wm); m.word(syms['system_bar'],bar)
    m.nodes[named(m,bar,'img_bt')]['visible']=0
    m.nodes[named(m,bar,'img_wifi')]['visible']=0
    slot=named(m,bar,'view_battery')
    m.nodes[named(m,bar,'label_battery')]['text']='50%'
    def paint_live_bar():
        m.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(bar,m.canvas,0,0),gap=0)
    paint_live_bar()
    idle=named(m,bar,'label_idle'); state=named(m,bar,'img_state'); eq=named(m,bar,'label_eq')
    assert m.get(eq+O['W_W'])==28
    assert all(m.nodes[eq]['asset'][f'style:{s}:font_size']=='16' for s in ('normal','disable','focused'))
    clock_box=tuple(m.get(title+O[k]) for k in ('W_X','W_Y','W_W','W_H'))
    # bar_sync shows the play state (here local's, mclGetPlayStatus: 2 playing, 3 paused) itself.
    m.handlers.pop(syms['mclGetPlayStatus'],None)
    m.advance(0); shown=(m.nodes[state]['visible'],m.nodes[state].get('image'))
    for status,equalizer,icon in ((0,0,''),(2,0,'bar_play'),(3,0,'bar_pause'),(3,0,'bar_pause'),(0,1,''),(2,1,'bar_play'),(0,0,'')):
        m.word(0xa3beac,status); m.nodes[eq]['visible']=equalizer
        paint_live_bar(); playing=int(bool(icon))
        # #116: a change made mid-paint repaints the whole bar once, after the frame.
        m.advance(0)
        now=(playing,m.nodes[state].get('image'))
        changed=shown[0]!=playing or (playing and shown[1]!=now[1])
        assert (('widget_invalidate_force',bar) in [c[:2] for c in m.calls])==changed, (status,icon)
        shown=now
        assert m.nodes[state]['visible']==playing and (not playing or m.nodes[state]['image']==icon)
        assert m.nodes[idle]['visible']==int(not playing and not equalizer)
        assert tuple(m.get(title+O[k]) for k in ('W_X','W_Y','W_W','W_H'))==clock_box
    passed()
    # Stock's 1 s tick (systembar_showface) hides img_state while its own player is stopped, as it
    # is under Spotify or radio; the hook shows the payload's state again before any paint.
    m.handlers[payload_syms['stock_showface_trampoline']]='stock_showface'
    m.word(0xa3beac,2); m.nodes[eq]['visible']=1; m.nodes[state]['visible']=0
    assert m.call(address=IPOD_HOOKS['systembar_showface'][0],args=(bar,0,0,0),gap=0)==0
    assert ('stock_showface',bar) in [c[:2] for c in m.calls] and m.nodes[state]['visible']==1 and m.nodes[state]['image']=='bar_play'
    passed()
    for mode in (1,2,3,0,1,2,3,0):
        m.call(address=payload_syms['setting_click'],args=(2,m.event,0,0),gap=0)
        m.calls=[]; paint_live_bar()
        assert ('widget_invalidate_force',slot) in [c[:2] for c in m.calls]
        if mode != 1:
            assert m.nodes[slot]['visible']
            assert m.get(slot+O['W_W'])==(O['BATT_INSIDE_W'] if mode==3 else O['BATT_BODY_W'])+O['BATT_NUB_W']+(O['BATT_GAP']+O['BATT_PCT_W'] if mode==2 else 0)
            assert signed(m.get(views[1]+O['W_X']))+signed(m.get(slot+O['W_X']))+m.get(slot+O['W_W'])==375-STATUS_MARGIN
    passed()

# Fast-scroll letter (iPod): once the wheel ramp moves more than one row per detent on a long list,
# the selected row's first character (a-z upper-cased, leading spaces skipped) is drawn centred over
# the list on a translucent dark rounded square, LETTER_MS after the last detent a timer repaints it
# away, and every canvas text/fill/clip state it touched is restored. Stock draws none.
def canvas_state(m):
    return (m.lcd_colors(),m.get(m.lcd+O['LCD_TEXT_COLOR']),m.get(m.canvas+O['CANVAS_ALIGN_V']),
            m.get(m.canvas+O['CANVAS_ALIGN_H']),m.clip)
def letter_machine(virtual,n=40):
    m=Machine(); m.word(m.canvas+O['CANVAS_ALIGN_V'],2)
    m.word(m.canvas+O['CANVAS_ALIGN_H'],3); m.word(m.lcd+O['LCD_TEXT_COLOR'],0x11223344)
    if virtual:
        w,rs,es=m.table_page(rebind=True); m.word(w+O['TABLE_ROWS'],n)
    else:
        w,es=m.page_list(n,extent=n*48,name='allmusic_page'); rs=es
    for e in es: m.nodes[e]['text']='row'
    m.paint(w,gap=0); return m,w,rs,es
def selected_entry(m,w,rs,es):
    return next(e for r,e in zip(rs,es) if (m.get(r+O['ROW_INDEX']) if r!=e else es.index(e))==m.selected(w))
box=(120-O['LETTER_BOX']//2,48-O['LETTER_BOX']//2,O['LETTER_BOX'],O['LETTER_BOX'])
for virtual in (False,True):
    m,w,rs,es=letter_machine(virtual); before=canvas_state(m)
    slow=1 if variant=='stock' else 3  # one-row ticks 100ms apart before the ramp's second row
    for _ in range(slow): assert m.call(gap=100)==11; m.paint(w,gap=0); assert not m.letters   # step 1
    assert m.call(gap=100)==11 and m.selected(w)==slow+2; m.paint(w,gap=0)     # step 2
    if variant!='ipod':
        assert not m.letters and not m.timers and not any(c[0]=='canvas_set_font' for c in m.calls); passed(); continue
    assert m.letters==[dict(text='R',rect=box,color=0xffffffff,font=('default',O['LETTER_PX']),align=(1,1),
                            clip=(0,0,240,96))]
    fills=[r for r in m.rounded if r['kind']=='fill' and r['color']!=color_t(0xeeeeec)]  # not Minimal's selection card
    assert fills==[dict(kind='fill',rect=box,bg=0,color=O['LETTER_ALPHA']<<24|O['FILL_RGB'],
                        radius=O['LETTER_RADIUS'],width=None,clip=(0,0,240,96))]
    assert canvas_state(m)==before; passed()
    # A library list shows the letter its row sorts under: a leading article is skipped (ringnav_sort_key).
    for text,glyph in (('zeta','Z'),('  apple','A'),('Émile','Émile'[0]),('東京','東'),('9 lives','9'),('The Cure','C'),
                       ('Them Crooked Vultures','T'),('a Tribe','T'),('An Émile','Émile'[0]),('Anthrax','A'),('The','T')):
        m.nodes[selected_entry(m,w,rs,es)]['text']=text; m.paint(w,gap=0)
        assert [l['text'] for l in m.letters]==[glyph],text; passed()
    m.nodes[selected_entry(m,w,rs,es)]['text']='   '; m.paint(w,gap=0)
    assert not m.letters and canvas_state(m)==before; passed()
    m.nodes[selected_entry(m,w,rs,es)]['text']='row'
    # A further fast detent re-arms; the letter clears LETTER_MS after the last one.
    assert m.call(gap=100)==11; m.advance(O['LETTER_MS']-1); m.paint(w,gap=0); assert m.letters
    m.advance(1); assert any(c[:2]==('widget_invalidate_force',w) for c in m.calls)
    m.paint(w,gap=0); assert not m.letters and canvas_state(m)==before; passed()
if variant=='ipod':
    # Never at one row per detent, nor on a list of at most SHORT_LIST_MAX (16) rows.
    for n,gap in ((40,141),(16,100)):
        m,w,rs,es=letter_machine(False,n)
        for _ in range(6): m.call(gap=gap)
        m.paint(w,gap=0); assert not m.letters and not m.timers; passed()
    # Touch drops the spin, and with it the letter.
    m,w,rs,es=letter_machine(False)
    for _ in range(5): m.call(gap=100)
    m.paint(w,gap=0); assert m.letters
    m.touch(); m.paint(w,gap=0); assert not m.letters; passed()
    # Elsewhere (a folder sorts by its file name) the first character stays.
    m=Machine(); w,es=m.page_list(40,extent=40*48,name='folder_page')
    for e in es: m.nodes[e]['text']='The Cure'
    for _ in range(5): m.call(gap=100)
    m.paint(w,gap=0); assert [l['text'] for l in m.letters]==['T']; passed()
    # A settings menu is not alphabetical: no letter however long.
    m=Machine(); w,es=m.page_list(40,extent=40*48,name='playset_page')
    for e in es: m.nodes[e]['text']='row'
    for _ in range(5): m.call(gap=100)
    m.paint(w,gap=0); assert not m.letters; passed()

WRITERS={'scanAllMusicFile':'stock_scan_all','scanSpecFolder':'stock_scan_folder','deleteMusicFromMusicDb':'stock_delete_song'}
class CoverflowMachine(QueueMachine):
    FREE=0x1000010  # stock free's GOT slot is 0 until lazy binding; give it a stub
    def __init__(self,albums=3,cached=True,**queue):
        super().__init__(rows=4,**queue)
        self.cached=cached; self.missing=False; self.joins=[]; self.threads=[]; self.plays=[]; self.homes=0; self.freed=0
        for n in ('window_create','widget_factory_create_widget','image_base_set_image','getAllAlbum','list_view_create',
                  'scroll_view_create','navigator_back_to_home','navigator_to_with_context','access@GLIBC_2.0',
                  'calloc@GLIBC_2.0','strdup@GLIBC_2.0','mkdir@GLIBC_2.0','statfs@GLIBC_2.0','pthread_create@GLIBC_2.2',
                  'pthread_join@GLIBC_2.0','fopen@GLIBC_2.2','qsort@GLIBC_2.0','toolsQueryDbTable'): self.handlers[syms[n]]='c:'+n
        self.word(0xa2638c,self.FREE); self.handlers[self.FREE]='c:free'
        self.handlers[home_hook[0]+12]='stock_home'
        for n,stub in WRITERS.items(): self.handlers[HOOKS[n][0]+12]=stub  # the writers' stock bodies
        self.albums=[self.song('T0') for _ in range(albums)]
        for i,r in enumerate(self.albums): self.word(r+O['REC_ALBUM'],self.string(f'Album {i}'))
        self.found=[self.song('T1'),self.song('T2')]
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('c:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address, name
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
            if kind=='slide_menu': self.word(ret+O['SLIDE_INDEX'],0); self.word(ret+0x5c,self.alloc())
        elif name=='image_base_set_image': self.nodes[a]['image']=self.text(b)
        elif name=='getAllAlbum':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.albums]; ret=len(self.albums)
        elif name=='navigator_back_to_home': self.homes+=1
        elif name=='navigator_to_with_context':
            if self.text(a)=='playing_page':
                self.play_names=self.names(self.get(b))
                self.plays.append((self.text(a),*[signed(self.get(b+4*i)) for i in range(4)]))
            elif self.text(a)=='dialog/msginfo_dialog': self.toasts.append((self.text(a),self.get(b),self.get(b+4),self.text(b+8)))
            else: self.opened.append((self.text(a),self.get(b),self.get(b+4))); self.top=self.node('window',self.text(a)); self.stack.append(self.top)
        elif name=='access': path=self.text(a); ret=0 if (self.cached if '/mnt/mmc/.coverflow/' in path else not self.missing) else -1
        elif name=='fopen': ret=0  # no saved album: remember() keeps the first
        elif name=='toolsQueryDbTable':
            rows=self.albums if 'fileurl not like' in self.text(b) else []
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in rows]
            ret=len(rows)
        elif name=='qsort': self.qsort(a,b,c,d)  # sorted by the payload's comparator, run nested
        elif name=='calloc': ret=self.alloc(a*b+4)
        elif name=='strdup': ret=self.string(self.text(a))
        elif name=='free':
            self.freed+=a!=0
            if a in getattr(self,'poison',{}): self.u.mem_write(a,b'\0'*self.poison.pop(a))
        elif name=='statfs': self.word(b+4,4096); self.word(b+28,1<<20)
        elif name=='pthread_create': self.word(a,77); self.threads.append((c,d))
        elif name=='pthread_join': self.joins.append(a)
        self.returns(ret,clobber=True)
    def open(self):
        """Home with the card at index 2: centre confirms its image, whose click opens Coverflow."""
        if variant=='ipod':
            view,self.imgs=home_list(self); self.img=self.imgs[2]; self.home=self.top; self.stack=[self.top]
            self.art=named(self,self.top,'img_homeart'); self.list=named(self,self.top,'list_view_home')
            assert self.call(address=home_hook[0],args=(self.top,0,0,0),gap=0)==0
            self.paint(view); self.nodes[view]['_ringnav_index']=2
        else:
            slide=self.node('slide_menu'); self.word(slide+O['SLIDE_INDEX'],2); self.word(slide+0x5c,self.alloc())
            self.img=self.node('image','img_coverflow'); card=self.node('button',children=[self.img])
            self.nodes[slide]['children']=[self.entry(slide),self.entry(slide),card,*[self.entry(slide) for _ in range(4)]]
            self.home=self.top=self.node('window','home_page',[slide]); self.stack=[self.top]
            assert self.call(address=home_hook[0],args=(self.top,0,0,0),gap=0)==0
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
        """A songtable writer runs: its hook, then its (mocked) stock body."""
        assert self.call(address=HOOKS[writer][0],args=(1,2,0,0),gap=0)==0 and self.calls[0][:3]==(WRITERS[writer],1,2)
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
    def key(self,k=O['KEY_RETURN']):
        f,ctx=self.handler(self.page,O['EVT_KEY_UP']); ev=self.alloc(0x40); self.word(ev,O['EVT_KEY_UP']); self.word(ev+O['EVENT_KEY'],k)
        ret=self.call(address=f,args=(ctx,ev,0,0),gap=0); self.advance(0); return ret

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
        for n in ('sqlite3_open','sqlite3_close','sqlite3_exec','getMusicByPlayList','getAllMusic','checkPlayListExist','batch_add_file','navigator_back','_create_vector','vector_init','_vector_push_back'):
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
        elif name=='getMusicByPlayList':
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
    def open_editor(self):
        more=self.node('window','playlistsmore_page'); self.word(more+O['W_PARENT'],self.wm)
        self.top=more; self.stack.append(more)
        deleted=self.node('hscroll_label',name='label_del')
        self.word(deleted+O['W_PARENT'],more); self.nodes[more]['children'].append(deleted)
        self.call(address=HOOKS['localmusic_playlistsmore_page_init'][0],args=(more,0,0,0),gap=0)
        assert self.nodes[deleted]['text']=='Delete Playlist'
        row=self.nodes[more]['children'][-1]; button=self.nodes[row]['children'][0]
        image,label=self.nodes[button]['children']
        h,inner,y=(72,68,154) if variant=='ipod' else (78,70,216)
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

# iPod: the wheel reaches Edit Playlist below stock's Rename and Delete, and Centre opens the editor.
if variant=='ipod':
    m=PlaylistEditMachine(); more=m.node('window','playlistsmore_page'); m.word(more+O['W_PARENT'],m.wm)
    m.word(more+O['W_W'],375); m.word(more+O['W_H'],290); m.top=more; m.stack.append(more)
    for i,name in enumerate(('label_rename','label_del')):
        row=m.node('list_item',children=[m.entry(0)]); m.word(row+O['W_Y'],10+i*72); m.word(row+O['W_PARENT'],more)
        m.word(m.nodes[row]['children'][0]+O['W_PARENT'],row); m.nodes[more]['children'].append(row)
    m.call(address=HOOKS['localmusic_playlistsmore_page_init'][0],args=(more,0,0,0),gap=0)
    m.on_click=lambda w,e: m.nested(*m.handler(w,O['EVT_CLICK']),e)
    assert m.call()==11 and m.call()==11 and m.selected(more)==2
    assert m.confirm()==11 and m.nodes[m.top]['name']=='playlistedit_page'; passed()

m=PlaylistEditMachine(); m.open_editor()
# The wheel still selects rows and Centre opens the selected song's actions.
m.on_click=lambda w,e: m.nested(*m.handler(w,O['EVT_CLICK']),e)
assert m.call()==11 and m.selected(m.view)==3
assert m.confirm()==11 and m.showlabels()==['Move Up','Move Down','Remove from Playlist']
assert m.key()==11 and m.selected(m.find('scroll_view'))==3
m.close()
m=PlaylistEditMachine(); m.open_editor()
assert m.showlabels()==['Add Songs','Done','A','B','C']
assert m.nodes[m.nodes[m.page]['children'][0]]['text']=='Editing: Mix'
# A repeat and long press cannot move again, and releases never reach playback.
for key,want in ((O['KEY_FWD_BTN'],['B','A','C']),(O['KEY_FWD_BTN'],['B','C','A']),(O['KEY_BACK_BTN'],['B','A','C'])):
    assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
    for _ in range(2):
        assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
        assert m.call(key,address=HOOKS['on_wm_keylong_fun'][0],event_type=O['EVT_KEY_LONG'],gap=0)==11
    assert m.order()==want
    assert m.call(key,gap=0)==11
    m.view=m.find('scroll_view'); assert m.selected(m.view)==want.index('A')+2
assert m.names()==m.before_queue and not m.playback(); passed()
# Touch actions and confirmation; cancel and Back leave the entry intact.
m.choose(3); assert m.showlabels()==['Move Up','Move Down','Remove from Playlist']
m.choose(2); assert m.showlabels()==['Remove from Playlist','Cancel']; m.choose(1)
assert m.order()==['B','A','C']; m.choose(3); m.choose(0); assert m.order()==['A','B','C']
m.choose(2); m.choose(1); assert m.order()==['B','A','C']
m.choose(3); m.choose(2); m.choose(0); assert m.order()==['B','C'] and m.selected(m.view)==3
assert m.order(1)==['Other']; passed()
# The fixed destination picker stays open; failed writes never report success.
saved=m.alloc(16); m.word(saved,0x12345678); m.word(syms['p_vector_select_record'],saved)
m.choose(0); assert m.showlabels()==['Search','T1','T2']; m.choose(1)
assert m.get(syms['p_vector_select_record'])==saved and m.get(saved)==0x12345678
assert not any(c[0].startswith('batch_') for c in m.calls)
assert m.toasts[-1][3]=='Added to Playlist' and m.showlabels()==['Search','T1','T2']
m.choose(1); assert m.toasts[-1][3]=='Already in playlist'
m.fail='write'; m.choose(2); assert m.toasts[-1][3]=='Could not save playlist'; m.fail=''
assert m.ticks()==[False,True,False]  # ticked on add, not on a failed write
assert m.key()==11 and m.showlabels()==['Add Songs','Done','B','C','T1']
m.choose(1); assert m.top!=m.page and not m.get(payload_syms['ple'])
assert m.names()==m.before_queue; passed()
# Add Songs' Search row filters by tagged title, artist or album, case-insensitively; the query
# stays for adding several and an empty one restores the whole picker.
def ple_search(m,text,event='EVT_IM_ACTION'):
    m.choose(0)
    edit=m.find('edit'); assert m.nodes[edit]['name']=='search_edit' and m.nodes[edit]['focused']==1
    assert m.get(edit+O['W_Y'])+m.get(edit+O['W_H'])<=30 and not m.find('scroll_view')
    m.nodes[edit]['text']=text; f,ctx=m.handler(edit,O[event])
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; m.advance(0)
    m.view=m.find('scroll_view')
m=PlaylistEditMachine(); m.word(m.found[0]+O['REC_ARTIST'],m.string('Beatles'))
m.word(m.found[1]+O['REC_ALBUM'],m.string('Zebra')); m.open_editor(); m.choose(0)
assert m.showlabels()==['Search','T1','T2']
ple_search(m,'T2'); assert m.showlabels()==['Search: T2','T2'] and not m.find('edit')
m.choose(0); assert m.nodes[m.find('edit')]['text']=='T2'  # prefilled for editing
m.nodes[m.find('edit')]['text']='beat'; f,ctx=m.handler(m.find('edit'),O['EVT_IM_ACTION'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; m.advance(0); m.view=m.find('scroll_view')
assert m.showlabels()==['Search: beat','T1']
m.choose(1); assert m.toasts[-1][3]=='Added to Playlist' and m.showlabels()==['Search: beat','T1']
ple_search(m,'zeb'); assert m.showlabels()==['Search: zeb','T2']
ple_search(m,''); assert m.showlabels()==['Search','T1','T2'] and m.ticks()==[False,True,False]
ple_search(m,'zzz'); assert m.showlabels()==['Search: zzz','No songs match']
# Return from the box applies its text, and a filtered pick can close the page cleanly.
m.choose(0); m.nodes[m.find('edit')]['text']='t1'; assert m.key()==11
assert m.showlabels()==['Search: t1','T1']; m.close(); passed()
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
        # Run stock's parent-show gate with the playlist category unchanged after More closes.
        self.word(syms['g_class_type'],0xf00b); self.u.reg_write(UC_MIPS_REG_GP,0xa26cc0)
        reached=[]
        def stop(u,address,size,unused):
            if address in (0x4b321c,0x4b3238): reached.append(address); u.emu_stop()
        hook=self.u.hook_add(UC_HOOK_CODE,stop)
        self.u.emu_start(0x4b31fc,0x1000000,count=100)
        self.u.hook_del(hook)
        assert len(reached)==1
        return reached[0]==0x4b321c

m=PlaylistLifecycleMachine(); assert m.create("O'Brien")==6
assert m.db.execute('select id,filename,name,cd_num from playlist where id=6').fetchone()==(6,"O'Brien","O'Brien",6)
assert m.create("O'Brien")==-2 and not m.db.in_transaction; passed()
# A name stored cut to 63 bytes never matches the whole one, as in stock: an M3U import takes any
# result but -1 as the playlist's ID.
m=PlaylistLifecycleMachine(); assert m.create('L'*70)==6 and m.create('L'*70)==7; passed()
for failure in ('open','begin','insert','commit'):
    m=PlaylistLifecycleMachine(); before=m.db.execute('select * from playlist').fetchall(); m.fail=failure
    assert m.create('New')==-1
    assert m.db.execute('select * from playlist').fetchall()==before and not m.db.in_transaction; passed()
m=PlaylistLifecycleMachine(); m.db.executemany('insert into playlist(id) values(?)',[(i,) for i in range(6,100)])
assert m.create('Full')==-1; passed()
for failure in ('','open','begin','delete','delete from playlist where id>100','commit'):
    m=PlaylistLifecycleMachine(); before=m.db.execute('select * from playlist').fetchall(); m.fail=failure; m.delete()
    if failure:
        assert m.db.execute('select * from playlist').fetchall()==before and m.backs==0
        assert m.toasts[-1][3]=='Could not delete playlist'
    else:
        assert m.db.execute('select id from playlist where id=0 or id=101').fetchall()==[]
        assert m.db.execute('select id from playlist where id=1 or id=102').fetchall()==[(1,),(102,)]
        assert m.backs==1 and bytes(m.u.mem_read(syms['g_delete_flag'],1))==b'\x01' and m.toasts[-1][3]=='Playlist deleted'
    assert m.reload_pending()==(not failure)
    assert not m.db.in_transaction; passed()
m=PlaylistLifecycleMachine(); m.db.execute('delete from playlist where id=101'); m.delete()
assert not m.db.execute('select id from playlist where id=0').fetchall() and m.toasts[-1][3]=='Playlist deleted'
assert m.create('Replacement')==0; passed()
m=PlaylistLifecycleMachine(); m.db.execute('delete from playlist where id=0'); m.delete()
assert m.db.execute('select id from playlist where id=101').fetchall()==[(101,)] and m.toasts[-1][3]=='Could not delete playlist'; passed()
m=PlaylistLifecycleMachine(); m.answer=0; before=m.db.execute('select * from playlist').fetchall(); m.delete()
assert m.db.execute('select * from playlist').fetchall()==before and not m.backs and not m.reload_pending(); passed()

# Holding Play/Pause on a playlist row opens the same More page as its trailing dots.
# The Add to Playlist picker shares playlist_page's name and keeps stock's hold.
# Its context is the playlist index from the dots' name: row positions also count Import/Export.
m=QueueMachine(page='playlist_page',rows=3); m.word(O['PLAYLIST_ADD_MODE'],0)
for i,e in enumerate(m.nodes[r]['children'][0] for r in m.nodes[m.surface]['children']):
    if i: dots=m.node('image',f'img_more{i-1}'); m.word(dots+O['W_PARENT'],e); m.nodes[e]['children'].append(dots)
m.paint(m.surface)
m.press(7000); assert m.hold()!=11 and not m.opened  # Import: no dots, stock keeps the hold
m.paint(m.surface); m.call(); m.paint(m.surface)
m.press(7100); assert m.hold()==11 and m.opened[-1][:2]==('localmusic/playlistsmore_page',0)
assert m.release()==0  # the locked hold's release never toggles playback
m.opened=[]; m.word(O['PLAYLIST_ADD_MODE'],1); m.press(7300)
m.hold(); assert not [o for o in m.opened if o[0]=='localmusic/playlistsmore_page']; passed()
for count in (0,1):
    m=PlaylistEditMachine(count); m.open_editor(); before=m.order()
    m.move(O['KEY_BACK_BTN']); m.move(); assert m.order()==before
    assert m.key()==11 and not m.get(payload_syms['ple']); passed()
# Roll back failed statements/commits, stale IDs, wrong playlist, CUE/path changes and ignored writes.
for failure in ('open','begin','update','commit'):
    m=PlaylistEditMachine(); m.fail=failure
    assert m.mutate()==0 and m.order()==list('ABC') and not m.db.in_transaction
    assert m.closed_db==1; passed()
m=PlaylistEditMachine(); m.db.execute("create trigger no_move before update on playlist begin select raise(ignore); end")
assert m.mutate()==0 and m.order()==list('ABC'); passed()
for change in ("delete from playlist where id=101", "update playlist set cd_num=1 where id=101",
               "update playlist set fileurl='/new' where id=101", "update playlist set time=30 where id=101"):
    m=PlaylistEditMachine(); a=m.record(m.db.execute('select * from playlist where id=101').fetchone()); b=m.record(m.db.execute('select * from playlist where id=102').fetchone())
    m.db.execute(change); before=m.db.execute('select * from playlist order by id').fetchall()
    assert m.call(address=payload_syms['ple_mutate'],args=(0,a,b,0),gap=0)==0
    assert m.db.execute('select * from playlist order by id').fetchall()==before; passed()
# Duplicate paths and CUE segments are removed by entry ID; hidden records keep their positions.
m=PlaylistEditMachine(); m.db.execute("update playlist set fileurl='/same',time=id,time_end=id+1 where cd_num=0 and id>100")
m.db.execute("insert into playlist values(150,0,'/hidden',0,0,'Hidden')")
assert m.mutate(101,103)==1 and m.order()==['C','B','A','Hidden']
assert m.mutate(102,0)==1 and m.order()==['C','A','Hidden'] and m.order(1)==['Other']
# Reopening uses the database order and leaves the live queue unchanged.
m.open_editor(); assert m.showlabels()==['Add Songs','Done','C','A','Hidden'] and m.names()==m.before_queue
assert m.mutate(101,200,0)==0
m.db.execute('update playlist set id=2147483647 where id=200'); assert m.mutate(101,103)==0
passed()
# Back cancels a deferred confirmation before returning to the editor.
m=PlaylistEditMachine(); m.open_editor(); m.choose(2); m.choose(2)
m.pick(0); assert m.key()==11 and m.order()==list('ABC')
assert m.showlabels()==['Add Songs','Done','A','B','C']; passed()
# A side press during a deferred click cannot change the row that click refers to.
m.choose(2); assert m.key()==11
m.view=m.find('scroll_view')
m.pick(3); m.move(); m.advance(0)
assert m.order()==list('ABC') and m.showlabels()==['Move Up','Move Down','Remove from Playlist']; passed()
# Simultaneous side-button presses retain independent repeat/release latches.
m=PlaylistEditMachine(); m.open_editor()
for key in (O['KEY_FWD_BTN'],O['KEY_BACK_BTN']):
    assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
assert m.order()==list('ABC')
assert m.call(O['KEY_BACK_BTN'],gap=0)==11
assert m.call(O['KEY_FWD_BTN'],address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
assert m.order()==list('ABC') and m.call(O['KEY_FWD_BTN'],gap=0)==11; passed()
# Failed removal and identical occurrences still target one entry only.
for failure in ('delete','commit'):
    m=PlaylistEditMachine(); m.fail=failure
    assert m.mutate(101,0)==0 and m.order()==list('ABC') and not m.db.in_transaction; passed()
m=PlaylistEditMachine(); m.db.execute("update playlist set fileurl='/same',time=0,time_end=0 where id in (101,102)")
assert m.mutate(102,0)==1 and m.order()==['A','C']; passed()
# Native delivery is consumed too; closing during a press consumes its remaining release.
m=PlaylistEditMachine(); m.open_editor(); key=O['KEY_FWD_BTN']
assert m.call(key,address=HOOKS['widget_dispatch'][0],args=(m.view,m.event,0,0),event_type=O['EVT_KEY_LONG'],gap=0)==11
assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
m.close(); assert m.call(key,gap=0)==11
assert m.call(address=payload_syms['ple_key'],args=(key,3,0,0),gap=0)==0
assert not m.playback(); passed()
# Reopen an on-disk database, independently of the editor's record cache.
import tempfile
with tempfile.TemporaryDirectory() as directory:
    path=str(pathlib.Path(directory)/'database.db'); disk=sqlite3.connect(path)
    m.db.backup(disk); disk.close(); disk=sqlite3.connect(path)
    assert [r[0] for r in disk.execute('select filename from playlist where cd_num=0 and id>100 order by id')]==['B','A','C']
    disk.close()
passed()
# A success/error toast covering the editor also consumes media keys, without moving songs.
m=PlaylistEditMachine(); m.open_editor(); page=m.top; m.top=m.node('dialog','msginfo_dialog')
m.move(); assert m.order()==list('ABC') and not m.playback()
m.top=page; m.move(); assert m.order()==['B','A','C']; passed()
# A subsequent stock playback load takes the saved order; editing itself left the queue alone.
m=PlaylistEditMachine(); assert m.mutate()==1; m.open_editor()
assert m.names()==m.before_queue
songs=m.get(payload_syms['ple']+8)
m.call(address=syms['mclLoadPlayList'],args=(songs,0,0xff13,0),gap=0)
assert m.names()==['B','A','C']; passed()
# A Videos folder's listed videos never join a queue (#128): stock loads a copy without them, its start
# on the same song, or the next after a video; the caller's list stays whole. Videos alone leave an empty queue.
for names,at,queued,pos in ((('a.mp3','v.mp4','b.flac','w.MKV'),2,['a.mp3','b.flac'],1),
                            (('a.mp3','v.mp4','b.flac'),1,['a.mp3','b.flac'],1),
                            (('v.mp4','a.mp3'),0,['a.mp3'],0),(('a.mp3','b.flac'),1,['a.mp3','b.flac'],1),
                            (('v.mp4','w.mkv'),1,[],0)):
    m=QueueMachine(); src=m.deque([m.song(n) for n in names])
    m.call(address=syms['mclLoadPlayList'],args=(src,at,1,0),gap=0)
    assert m.names()==queued and m.get(O['MCL_POS'])==pos and len(m.items(src))==len(names),(names,m.names(),m.get(O['MCL_POS']))
    passed()
# Explicit stock loading order also reaches export and later playback loads through the shared query hook.
m=LibraryQueryMachine(); m.db.execute('create table playlist(id,cd_num)')
m.db.executemany('insert into playlist values(?,?)',[(103,0),(101,0),(102,0),(100,0),(104,1)])
m.call(address=HOOKS['toolsQueryDbTable'][0],args=(m.string('/mnt/data/database.db'),m.string('select * from playlist where id > 100 and cd_num=0'),123,1),gap=0)
assert m.sql.endswith('order by id asc') and m.results==[(101,0),(102,0),(103,0)]; passed()

if variant=='ipod':
    # Both themes: the art itself follows the playing track, when Home or the status bar paints, in
    # the right panel beside the Split list: the Coverflow thumbnail until the player has parsed this
    # track, then the player's cover. Nothing loads or draws for Now Playing.
    for theme in ('0','1'):
      CONFIG.clear(); CONFIG.update(THEME=theme); m=CoverflowMachine(); m.open(); m.top=m.home; CONFIG.clear()
      bar=m.node('window','system_bar'); m.word(syms['system_bar'],bar)
      for w in (bar,m.home): m.word(w+O['W_PARENT'],m.wm)
      SPLIT=O['HOME_SPLIT_W']; PW=375-SPLIT
      def art_image(w):
          m.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0))
          return m.nodes[m.art].get('image')
      m.byte(syms['g_playcover_type'],1)
      assert art_image(m.home).startswith('file:///mnt/mmc/.coverflow/') and m.get(m.list+O['W_W'])==SPLIT
      m.u.mem_write(syms['g_lastcover_url'],b'/p/A\0'); assert art_image(bar)=='file:///tmp/coverpic.jpg'
      m.nodes[m.art]['image']='unchanged'; assert art_image(m.home)=='unchanged'  # same track and cover
      np=m.node('window','playing_page'); m.word(np+O['W_PARENT'],m.wm); m.top=np
      m.u.mem_write(syms['g_lastcover_url'],b'/p/B\0'); art_image(np); art_image(bar)
      assert not [c for c in m.calls if c[0] in ('widget_load_image','canvas_draw_image')]; m.top=m.home
      m.u.mem_write(syms['g_lastcover_url'],b'/p/A\0')
      m.byte(syms['g_playcover_type'],3); assert art_image(m.home).startswith('file:///mnt/mmc/.coverflow/'); passed()
      # Sized to the cover's proportions, just covering the panel and centred on it (native fill then
      # draws it whole), and clipped to the panel from the background hook to the border hook, so it
      # crops evenly and never stretches.
      assert m.nodes[m.art].get('sensitive')==0
      def geometry(): return [signed(m.get(m.art+O[k])) for k in ('W_X','W_Y','W_W','W_H')]
      def cover(w,h):
          m.image_size=(w,h)
          m.u.mem_write(syms['g_lastcover_url'],b'/p/other\0'); art_image(m.home)
          m.u.mem_write(syms['g_lastcover_url'],b'/p/A\0'); art_image(m.home)
          return geometry()
      for (w,h),want in (((300,300),[SPLIT+int((PW-290)/2),0,290,290]),((2*PW,580),[SPLIT,0,PW,290]),
                         ((600,400),[SPLIT+int((PW-435)/2),0,435,290]),((100,400),[SPLIT,int((290-4*PW)/2),PW,4*PW])):
          assert cover(w,h)==want,((w,h),geometry(),want)
      passed()
      m.clip=(0,0,375,320); m.word(m.canvas+O['CANVAS_X'],SPLIT); m.word(m.canvas+O['CANVAS_Y'],30+want[1])
      m.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(m.art,m.canvas,0,0))
      assert m.clip==(SPLIT,30,PW,290),m.clip
      m.call(address=HOOKS['widget_on_paint_border'][0],args=(m.art,m.canvas,0,0)); assert m.clip==(0,0,375,320)
      # The list paints only left of the panel; the placeholder (no size) fills the panel.
      m.word(m.canvas+O['CANVAS_X'],0); m.word(m.canvas+O['CANVAS_Y'],30+HOME_TOP)
      m.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(m.list,m.canvas,0,0))
      assert m.clip==(0,30+HOME_TOP,SPLIT,7*HOME_ROW),m.clip
      m.call(address=HOOKS['widget_on_paint_border'][0],args=(m.list,m.canvas,0,0)); assert m.clip==(0,0,375,320)
      m.image_size=None
      m.u.mem_write(syms['g_lastcover_url'],b'/p/none\0')
      art_image(m.home); assert m.nodes[m.art]['image']=='default_album_home' and geometry()==[SPLIT,0,PW,290]; passed()

    # Now Playing: stock init runs first, then "n of m", the album and "-remaining" (slider max less
    # value, in seconds) fill in; later paints rewrite a label only when its source changed.
    playing=HOOKS['playing_page_init'][0]
    assert struct.unpack_from('<I',demo,fileoff(demo,playing))[0]==j(payload_syms['ringnav_playing'])
    m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
    for i,r in enumerate(m.items(m.get(syms['mcl_pdeqplaylist']))): m.word(r+O['REC_ALBUM'],m.string(f'Album {i}'))
    bar=m.node('window','system_bar'); m.word(syms['system_bar'],bar)
    pos,album,remain=(m.node('label',n) for n in ('label_ipod_pos','label_ipod_album','label_ipod_remain'))
    slider=m.node('slider','slider_play',max=225,value=100)
    win=m.node('window','playing_page',[m.node('view','view_buttons',[pos]),album,slider,remain])
    for w in (bar,win): m.word(w+O['W_PARENT'],m.wm)
    m.top=win
    def shown(): return [m.nodes[w].get('text') for w in (pos,album,remain)]
    def written(): return [m.nodes[c[1]]['name'] for c in m.calls if c[0]=='widget_set_text_utf8']
    def repaint(w=win): m.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0)); return written()
    assert m.call(address=playing,args=(win,7,0,0),gap=0)==0 and m.calls[0][:3]==('stock_playing',win,7)
    assert shown()==['2 of 3','Album 1','-2:05']; passed()
    assert repaint()==[] and repaint(bar)==[]; passed()
    m.nodes[slider]['value']=101; assert repaint()==['label_ipod_remain'] and shown()[2]=='-2:04'; passed()
    m.word(O['MCL_POS'],2); assert repaint(bar)==['label_ipod_pos','label_ipod_album'] and shown()[:2]==['3 of 3','Album 2']; passed()
    # A rebuilt queue can reuse the same string address with new text; the text itself is hashed.
    m.u.mem_write(m.get(m.items(m.get(syms['mcl_pdeqplaylist']))[2]+O['REC_ALBUM']),b'Other\0')
    assert 'label_ipod_album' in repaint() and shown()[1]=='Other'; passed()
    # Issue #7: once the player has parsed the playing file (g_play_id3_info starts with its path)
    # the label shows the album it parsed, which a next folder's untagged records lack.
    id3=syms['g_play_id3_info']; m.u.mem_write(id3+O['ID3_ALBUM'],b'Parsed\0')
    m.u.mem_write(id3,b'/p/other\0'); assert repaint()==[] and shown()[1]=='Other'
    m.u.mem_write(id3,b'/p/C\0'); assert repaint()==['label_ipod_pos','label_ipod_album'] and shown()[1]=='Parsed'
    m.u.mem_write(id3,b'\0'); assert repaint()==['label_ipod_pos','label_ipod_album'] and shown()[1]=='Other'; passed()
    for mx,v,want in ((3725,0,'-1:02:05'),(3600,0,'-1:00:00'),(3599,0,'-59:59'),(90,90,'-0:00'),(90,95,'-0:00')):
        m.nodes[slider].update(max=mx,value=v); repaint(); assert shown()[2]==want,(mx,v)
    passed()
    queue=m.deqs[m.get(syms['mcl_pdeqplaylist'])][1]
    del queue[1:]; m.word(O['MCL_POS'],0); repaint(); assert shown()[:2]==['1 of 1','Album 0']
    queue.clear(); repaint(); assert shown()[:2]==['','']; passed()
    # Another top window, or the page once destroyed, leaves the labels alone.
    queue.append(m.copy('stSongInfo',m.song('X'))); m.top=m.node('window','home_page'); m.word(m.top+O['W_PARENT'],m.wm)
    assert repaint(m.top)==[] and repaint(bar)==[]; m.top=win
    f,ctx=m.handler(win,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    assert repaint()==[] and shown()[0]==''; passed()
    # Minimal (docs/ipod.md#now-playing): the album is drawn in capitals over its transparent label;
    # nothing else is drawn and the text keeps the asset's places. Classic draws nothing.
    def minimal_np(theme):
        CONFIG.clear(); CONFIG.update(THEME=theme); m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'; CONFIG.clear()
        for i,r in enumerate(m.items(m.get(syms['mcl_pdeqplaylist']))):
            m.word(r+O['REC_PATH'],m.string(('/p/a.mp3','/p/b.FLAC','/p/c')[i])); m.word(r+O['REC_ALBUM'],m.string('Kid A'))
        title,artist=m.node('hscroll_label','scrlabel_title'),m.node('hscroll_label','scrlabel_artist')
        album=m.node('label','label_ipod_album')
        for w,y in ((title,55),(artist,87),(album,111)):
            for k,v in (('W_X',194),('W_Y',y),('W_W',165),('W_H',20)): m.word(w+O[k],v)
        page=m.node('view','view_album',[title,artist,album]); slide=m.node('slide_view','slide_view'); m.word(slide+O['W_H'],186)
        box=m.node('view','slide_view_view',[slide,page]); m.word(box+O['W_Y'],40)
        win=m.node('window','playing_page',[box,m.node('slider','slider_play',max=225,value=100),m.node('label','label_ipod_remain')])
        for w,parent in ((title,page),(artist,page),(album,page),(page,box),(slide,box),(box,win),(win,m.wm)): m.word(w+O['W_PARENT'],parent)
        m.top=win; m.call(address=playing,args=(win,7,0,0),gap=0)
        minimal=theme=='0'
        assert [m.get(w+O['W_Y']) for w in (title,artist,album)]==[55,87,111]
        assert m.u.mem_read(album+0x34,1)==bytes([0 if minimal else 255])
        def border(w): m.letters=[]; m.call(address=HOOKS['widget_on_paint_border'][0],args=(w,m.canvas,0,0),gap=0); return m.letters
        letters=border(page)+border(win)
        if not minimal: assert not letters; passed(); return
        assert ''.join(l['text'] for l in letters)=='KID A' and len({l['y'] for l in letters})==1
        assert letters[0]['x']==194+O['NP_CAPS_INSET']
        assert {l['color'] for l in letters}=={color_t(O['NP_ALBUM_RGB'])} and m.font==('default',O['NP_CAPS_PX']); passed()
        # Wider than its label (5 letters, 58 px, in 40 past the inset), the album holds, scrolls 2 px a step to its
        # end, holds there, then starts over; the canvas gets its clip back.
        m.timers.clear() # stock's own, not emulated
        m.word(album+O['W_W'],40+O['NP_CAPS_INSET']); border(page); assert len(m.timers)==1
        def x(): return border(page)[0]['x']
        x0=194+O['NP_CAPS_INSET']
        m.advance(1499); assert x()==x0
        m.advance(1); assert ('widget_invalidate_force',album) in [c[:2] for c in m.calls] and x()==x0-2
        m.advance(50*8); assert x()==x0-18
        m.advance(1499); assert x()==x0-18
        clip=m.clip; m.advance(1); assert x()==x0 and m.clip==clip
        m.word(album+O['W_W'],165); x(); m.advance(1500); assert not m.timers and x()==x0; passed()
    for theme in ('0','1'): minimal_np(theme)

    # The visualizer page: a paint while not settled (a window still over Now Playing, closing) looks
    # again VIS_RETRY_MS later and repaints the page once settled; at rest (nothing heard for 1.5 s, not
    # playing) the track's title and artist show at the bottom.
    m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
    title,artist=m.node('hscroll_label','scrlabel_title',text='Charge'),m.node('hscroll_label','scrlabel_artist',text='Elbow')
    vslide=m.node('slide_view','slide_view',value=0); m.word(vslide+O['W_H'],186)
    box=m.node('view','slide_view_view',[vslide]); m.word(box+O['W_Y'],40)
    win=m.node('window','playing_page',[box,title,artist])
    for w,parent in ((vslide,box),(box,win),(title,win),(artist,win),(win,m.wm)): m.word(w+O['W_PARENT'],parent)
    m.top=win; m.call(address=playing,args=(win,7,0,0),gap=0); vis=m.nodes[vslide]['children'][-1]
    def vis_paint(): m.letters=[]; [m.call(address=HOOKS['widget_on_paint_border'][0],args=(w,m.canvas,0,0),gap=0) for w in (vis,win)]; return [l['text'] for l in m.letters]
    m.timers.clear(); m.top=m.node('window','saverscreen_page'); vis_paint(); assert len(m.timers)==1
    m.advance(O['VIS_RETRY_MS']); assert len(m.timers)==1
    m.top=win; m.advance(O['VIS_RETRY_MS']-1); assert ('widget_invalidate_force',vslide) not in [c[:2] for c in m.calls]
    m.advance(1,clear=False); assert ('widget_invalidate_force',vslide) in [c[:2] for c in m.calls] and not m.timers; passed()
    # The tick (-mfp64 doubles: not emulated) sets vz.quiet, the struct's last word, at rest.
    from build import run
    quiet=[int(l.split()[1],16)+int(l.split()[2])-4 for l in run('readelf','-Ws',str(B/'patch.elf')).splitlines() if l.split()[-1:]==['vz']][0]
    m.now+=1000; assert vis_paint()==[] and len(m.timers)==1
    m.word(quiet,1); assert vis_paint()==['Charge \xb7 Elbow']; passed()
    # VIS_W (343 px; 10 px a letter mocked): too long, the artist goes, then the title ends in an ellipsis.
    m.nodes[title]['text']='T'*30; assert vis_paint()==['T'*30]
    m.nodes[title]['text']='T'*40; assert vis_paint()==['T'*33+'\u2026']; passed()

    # The full-screen art: stock init runs first; the song name and its shade show BC_MS, then fade
    # out together over BC_STEPS steps. Closing it mid-fade stops the fade.
    big=IPOD_HOOKS['bigcover_page_init'][0]
    assert struct.unpack_from('<I',demo,fileoff(demo,big))[0]==j(payload_syms['ringnav_bigcover'])
    def bigcover():
        b=QueueMachine(queue=3,pos=1); b.handlers[big+12]='stock_bigcover'
        title,shade=b.node('hscroll_label','scrlabel_title'),b.node('view','view_shade')
        bw=b.node('window','bigcover_page',[shade,title])
        for w in (title,shade): b.byte(w+0x34,255) # the asset's opacity
        assert b.call(address=big,args=(bw,7,0,0),gap=0)==0 and b.calls[0][:3]==('stock_bigcover',bw,7)
        return b,bw,lambda: [b.get(w+0x34)&255 for w in (title,shade)]
    b,bw,alpha=bigcover()
    b.advance(O['BC_MS']-1); assert alpha()==[255,255] and len(b.timers)==1
    steps=[]
    for _ in range(O['BC_STEPS']):
        b.advance(O['BC_STEP_MS'] if steps else 1); steps.append(alpha()[0]); assert alpha()[0]==alpha()[1]
    n=O['BC_STEPS']; assert steps==[255*(n-k)//n for k in range(1,n+1)] and not b.timers,steps; passed()
    # A new song while it is open shows its name again and restarts the fade; the same song does not.
    b.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
    tick=lambda: b.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=0,clear=False)
    tick(); assert alpha()==[0,0] and not b.timers
    b.word(O['MCL_POS'],2); tick(); assert alpha()==[255,255] and len(b.timers)==1
    b.advance(O['BC_MS']-1); tick(); assert alpha()==[255,255] and len(b.timers)==1; passed()
    b,bw,alpha=bigcover(); b.advance(O['BC_MS']+O['BC_STEP_MS'])
    f,ctx=b.handler(bw,O['EVT_DESTROY']); b.call(address=f,args=(ctx,b.event,0,0),gap=0)
    assert not b.timers; passed()
    # bc_fit: a 16:9 cover takes the widest box clear of the glass, the name and shade on its foot.
    # A player file counts once it is its type's and this track's, decoded past the image manager's
    # cached copy; until then the art keeps the shape of g_lastcover_url's cover (a skip), or the
    # square once that is another's (as Tidal, whose URL is never a queue path). Stock's placeholder
    # is the square. An unchanged paint decodes and bc_mv nothing; a layout back to the square
    # refits without a decode.
    from ipod import BC_ART
    b,bw,alpha=bigcover(); shade,title=b.nodes[bw]['children']; b.word(bw+O['W_PARENT'],b.wm)
    img=b.node('image','img_bigcover'); b.nodes[bw]['children'].append(img); b.word(img+O['W_PARENT'],bw)
    def put(w,*g):
        for k,v in zip(('W_X','W_Y','W_W','W_H'),g): b.word(w+O[k],v)
    def geo(w): return [signed(b.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H')]
    def bc_track(i): return b.text(b.get(b.items(b.get(syms['mcl_pdeqplaylist']))[i]+O['REC_PATH'])).encode()+b'\0'
    sq=[(375-BC_ART)//2,(320-BC_ART)//2,BC_ART,BC_ART]
    put(img,*sq); put(shade,sq[0],sq[1]+BC_ART-60,BC_ART,60); put(title,sq[0]+12,sq[1]+BC_ART-45,BC_ART-24,30)
    b.nodes[img]['image']='file:///tmp/coverpic.jpg'; b.image_size=(1600,900)
    bc_dec=['image_manager_unload_bitmap_by_name','widget_load_image']; bc_mv=['widget_move_resize']*3
    def fit():
        b.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(bw,b.canvas,0,0),gap=0)
        return [c[0] for c in b.calls if c[0] in bc_dec+bc_mv]
    b.byte(syms['g_playcover_type'],1); b.u.mem_write(syms['g_lastcover_url'],b'/p/other\0')
    assert fit()==[] and geo(img)==sq  # the last track's cover file
    b.u.mem_write(syms['g_lastcover_url'],bc_track(1)); assert fit()==bc_dec+bc_mv
    x,y,w,h=geo(img); assert w>BC_ART and h==w*900//1600 and x==(375-w)//2 and y==(320-h)//2,geo(img)
    assert geo(shade)==[x,y+h-60,w,60] and geo(title)==[x+12,y+h-45,w-24,30] and len(b.timers)==2
    assert fit()==[]
    put(img,*sq); assert fit()==bc_mv and geo(img)==[x,y,w,h]
    b.word(O['MCL_POS'],2); assert fit()==[] and geo(img)==[x,y,w,h]  # a skip: still the last cover
    b.byte(syms['g_playcover_type'],1); b.nodes[img]['image']='file:///tmp/externpic.jpg'
    b.u.mem_write(syms['g_lastcover_url'],bc_track(2)); assert fit()==bc_mv and geo(img)==sq  # not type 1's file
    b.image_size=(900,1600); b.byte(syms['g_playcover_type'],2); assert fit()==bc_dec+bc_mv
    x,y,w,h=geo(img); assert h>BC_ART and w==h*900//1600 and x==(375-w)//2,geo(img)
    b.nodes[img]['image']='file:///tmp/album_tidal.jpg'; b.u.mem_write(syms['g_lastcover_url'],b'\0')
    assert fit()==bc_mv and geo(img)==sq  # Tidal
    b.u.mem_write(syms['g_lastcover_url'],bc_track(2)); b.nodes[img]['image']='file:///tmp/externpic.jpg'; fit()
    b.nodes[img]['image']='default_bigcover'; assert fit()==bc_mv and geo(img)==sq; passed()

    # Volume: with the stock volume dialog directly over Now Playing, the dialog's own paint draws
    # the band from the progress bar to the times: black, the track, a white fill to the volume and
    # "Volume N". Its slider and label hide, the slider moved onto the band so stock's invalidation
    # repaints it. Over any other window it draws a compact panel.
    # The page's bar is the Theme's: Minimal's 4px line, Classic's 8px capsule (np_theme).
    for theme,bh in (('0',4),('1',8)):
      CONFIG.clear(); CONFIG.update(THEME=theme)
      m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
      slider,elapsed,remain=m.node('slider','slider_play',max=225,value=100,bar_size=4),m.node('label','label_playtime'),m.node('label','label_ipod_remain')
      win=m.node('window','playing_page',[slider,elapsed,remain]); m.word(win+O['W_PARENT'],m.wm); m.top=win
      def put(w,*g):
          for k,v in zip(('W_X','W_Y','W_W','W_H'),g): m.word(w+O[k],v)
      put(win,0,30,375,290); put(slider,24,240,327,30); put(elapsed,16,265,80,16)
      m.word(syms['system_bar'],m.node('window','system_bar'))
      assert m.call(address=playing,args=(win,7,0,0),gap=0)==0
      sv,lv=m.node('slider','slider_vol',max=100,value=40),m.node('label','label_vol')
      for w in (sv,lv): m.nodes[w]['visible']=0  # hidden by the asset before any paint
      dlg=m.node('dialog','volume_dialog',[sv,lv]); m.word(dlg+O['W_PARENT'],m.wm); put(dlg,0,0,375,320)
      def paint(w): m.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0))
      def did(n): return [c[1:] for c in m.calls if c[0]==n]
      band=(0,270,375,30+265+16-270); bar=(24,270+(30-bh)//2,327,bh)
      m.nodes[m.wm]={'children':[win,dlg]}; m.top=dlg; paint(dlg)
      assert [m.nodes[w]['visible'] for w in (sv,lv,slider,elapsed)]==[0,0,1,1]
      assert [signed(m.get(sv+O[k])) for k in ('W_X','W_Y','W_W','W_H')]==list(band)
      assert m.nodes[slider]['bar_size']==bh and m.nodes[slider]['style:pressed:round_radius']==bh//2
      fills=[b[:5] for b in m.bands]
      assert fills==[(*band,color_t(0))],fills  # then the capsule, as the progress bar
      assert [(r['rect'],r['radius'],r['color']) for r in m.rounded]==[(bar,bh//2,color_t(O['TRACK_COLOR'])),((24,bar[1],327*40//100,bh),bh//2,color_t(0xffffff))],m.rounded
      assert [(t['text'],t['rect'],t['font'][1]) for t in m.letters]==[('Volume 40',(0,295,375,16),O['NP_TIMES_PX'])]; passed()
    assert len(m.timers)==1; poll=next(iter(m.timers))
    m.advance(O['VOL_POLL_MS']); assert not did('widget_invalidate_force') and poll in m.timers  # unchanged: nothing
    m.nodes[sv]['value']=41; m.advance(O['VOL_POLL_MS'])  # changed: the whole dialog repaints
    assert [c[0] for c in did('widget_invalidate_force')]==[dlg]; passed()
    m.nodes[sv]['value']=100; paint(dlg)
    assert m.rounded[-1]['rect'][2]==327 and m.letters[0]['text']=='Volume 100' and len(m.timers)==1; passed()

    m.nodes[m.wm]['children']=[win]; m.top=win; m.advance(O['VOL_POLL_MS']); assert not m.timers; passed()  # closed: it stops
    # Over any other window (Quick Settings included) a rounded panel in the fast-scroll letter's
    # style holds a pill bar on a grey track and the number, clear of the glass corners.
    other=m.node('window','home_page'); m.word(other+O['W_PARENT'],m.wm); sv2,lv2=m.node('slider','slider_vol',max=100,value=7),m.node('label','label_vol')
    dlg2=m.node('dialog','volume_dialog',[sv2,lv2]); m.word(dlg2+O['W_PARENT'],m.wm); put(dlg2,0,0,375,320)
    m.nodes[m.wm]['children']=[win,other,dlg2]; m.top=dlg2; paint(dlg2)
    X,Y,H,P,T=(O['VOL_PANEL_'+k] for k in ('X','Y','H','PAD','TEXT')); panel=(X,Y,375-2*X,H); bw=panel[2]-2*P-T
    assert [m.nodes[w]['visible'] for w in (sv2,lv2)]==[0,0] and [signed(m.get(sv2+O[k])) for k in ('W_X','W_Y','W_W','W_H')]==list(panel)
    BH=O['VOL_PANEL_BAR']; bar=(X+P,Y+(H-BH)//2,bw,BH)
    assert [(r['rect'],r['radius'],r['color']) for r in m.rounded]==[(panel,O['LETTER_RADIUS'],(O['LETTER_ALPHA']<<24)|O['FILL_RGB']),
        (bar,BH//2,color_t(O['VOL_PANEL_TRACK'])),((*bar[:2],bw*7//100,BH),BH//2,color_t(0xffffff))],m.rounded
    assert not m.bands and [(t['text'],t['rect'],t['font'][1]) for t in m.letters]==[('7',(X+P+bw,Y,T,H),O['VOL_PANEL_PX'])]; passed()
    m.nodes[sv2]['value']=1; paint(dlg2)  # a sliver is still a round dot
    assert m.rounded[2]['rect']==(*bar[:2],BH,BH),m.rounded; passed()
    R=O['LETTER_RADIUS']; assert math.hypot(80-(X+R),Y+H-R-240)+R<=80  # its rounded corner clears the glass's

    # The art's corners: after the image (the border hook), each of the NP_ART_RADIUS rows of a
    # corner is black outside the arc, then one edge pixel at the alpha it leaves uncovered.
    m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
    cover=m.node('image','img_cover'); win=m.node('window','playing_page',[cover]); m.word(win+O['W_PARENT'],m.wm); m.top=win
    put(cover,16,10,166,166); m.call(address=playing,args=(win,7,0,0),gap=0)
    m.word(m.lcd+O['LCD_FILL_COLOR'],0x12345678)
    m.bands.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(cover,m.canvas,0,0))
    R=O['NP_ART_RADIUS']; want=[]
    for i in range(R):
        out=16*R-math.isqrt((4*R*R-(2*(R-i)-1)**2)*64); n=out>>4
        for c in range(4):
            y=166-1-i if c&1 else i; left=not c&2
            want+=[(0 if left else 166-n,y,n,1,color_t(0)),(n if left else 166-n-1,y,1,1,(out&15)*17<<24)]
    assert [b[:5] for b in m.bands]==want and want[0][2]==8 and want[-2][2]==0,want[:2]
    assert m.get(m.lcd+O['LCD_FILL_COLOR'])==0x12345678; passed()  # restored
    m.bands.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(win,m.canvas,0,0)); assert not m.bands; passed()

    # Full-screen corners use destination pixels, independent of source-image scaling.
    from ipod import BC_ART
    bigart=m.node('image','img_bigcover')
    bx,by=(375-BC_ART)//2,(320-BC_ART)//2
    put(bigart,bx,by,BC_ART,BC_ART)
    bigwin=m.node('window','bigcover_page',[bigart]); m.handlers[big+12]='stock_bigcover'
    m.call(address=big,args=(bigwin,7,0,0),gap=0)  # ringnav_bigcover keeps the window
    m.bands.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(bigart,m.canvas,0,0))
    assert not m.bands  # rounded after all children, including the title's shade
    m.call(address=HOOKS['widget_on_paint_border'][0],args=(bigwin,m.canvas,0,0))
    pixels={}
    for x,y,w,h,color,*_ in m.bands:
        for xx in range(x,x+w): pixels[xx-bx,y-by]=color>>24
    assert any(0<a<255 for a in pixels.values())
    for (x,y),alpha in pixels.items():
        assert pixels.get((BC_ART-1-x,y))==alpha
        assert pixels.get((x,BC_ART-1-y))==alpha
    assert m.get(m.lcd+O['LCD_FILL_COLOR'])==0x12345678; passed()

    # Scrub: a double centre press toggles it; the wheel then moves a target of SCRUB_STEP
    # seconds times the ramp, previewed on the slider and both labels however far apart the ticks,
    # and committed once through player_seek_time (track seconds) when the scrub ends. Stock's page
    # timer stops meanwhile.
    CONFIG.clear(); FILL_HI=signed(color_t(O['MINIMAL_FILL'])); FG='style:normal:fg_color'  # Minimal's
    def scrub_page(value=100,mx=225,m=None):  # m: Now Playing opened again on that machine
        if not m: m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
        m.slider=m.node('slider','slider_play',max=mx,value=value)
        m.elapsed,m.remain=m.node('label','label_playtime'),m.node('label','label_ipod_remain')
        m.win=m.top=m.node('window','playing_page',[m.slider,m.elapsed,m.remain])
        m.word(m.win+O['W_PARENT'],m.wm)
        assert m.call(address=playing,args=(m.win,7,0,0),gap=0)==0
        return m
    def did(m,name): return [c[1] for c in m.calls if c[0]==name]
    def keep(m):
        m.seeks=getattr(m,'seeks',[])+did(m,'player_seek_time'); m.starts=getattr(m,'starts',[])+did(m,'playing_timer_start')
    def step(m,*a,**k):  # one input or timer step, keeping every seek and timer restart it makes
        r=(m.advance if k.pop('wait',False) else m.call)(*a,**k); keep(m); return r
    def centre(m,gap=1000):  # a double press
        assert m.call(O['KEY_CENTER'],gap=gap)==11 and not did(m,'playing_timer_clear')
        assert m.call(O['KEY_CENTER'],gap=100,clear=False)==11
        m.advance(DC,clear=False); keep(m); assert not m.screens  # no single press left behind
    def ended(m,seeks,label=''):  # the scrub is over: fill, stock timer and the wheel's volume are back
        assert m.seeks==seeks and m.starts==[m.win], (label,m.seeks,m.starts)
        assert m.nodes[m.slider][FG]==FILL_HI and not m.timers and m.call()==0 and not did(m,'player_seek_time'), label
    m=scrub_page(); centre(m)
    assert did(m,'playing_timer_clear')==[m.win] and m.nodes[m.slider][FG]==-1 and not m.screens; passed()
    # Ticks a second apart, far past the old 150 ms seek, each preview at once and none seeks.
    assert step(m)==11 and m.nodes[m.slider]['value']==105 and m.nodes[m.elapsed]['text']=='1:45'
    assert m.nodes[m.remain]['text']=='-2:00'
    assert step(m,gap=1500)==11 and m.nodes[m.slider]['value']==110 and m.nodes[m.elapsed]['text']=='1:50'
    assert m.nodes[m.remain]['text']=='-1:55'
    step(m,O['SCRUB_MS']-100,wait=True); assert m.seeks==[] and m.nodes[m.slider]['value']==110; passed()
    # A spin ramps the step as in a long list and still only previews.
    for _ in range(12): assert step(m,gap=20)==11
    assert m.nodes[m.slider]['value']-110>12*O['SCRUB_STEP'] and m.seeks==[]; passed()
    # The target stops at both ends while the wheel keeps turning; it reverses at once.
    for key,end in ((O['KEY_NEXT'],225),(O['KEY_PREV'],0)):
        for _ in range(40): step(m,key,gap=20)
        assert m.nodes[m.slider]['value']==end and m.seeks==[]
    assert step(m,gap=300)==11 and m.nodes[m.slider]['value']==5 and m.seeks==[]; passed()
    # Centre commits the target once; nothing seeks afterwards.
    centre(m,50); step(m,O['SCRUB_MS'],wait=True); ended(m,[5]); passed()
    # Paused (mclGetPlayStatus 3), the seek alone, in file seconds (a CUE track's start added), not
    # player_seek_time, which resumes the track: the player's second stands still until Play, the bar
    # keeps the target and stock's timer stays off until it plays; playing, SETTLE_PLAY_MS without
    # reaching it gives the bar back to stock's timer.
    PLAY_STATUS=0xa3beac
    m=scrub_page(); m.handlers.pop(syms['mclGetPlayStatus']); m.word(PLAY_STATUS,3); centre(m); step(m)
    m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[1]+O['REC_CUE_START'],30); centre(m,50)
    assert did(m,'mclSetSeek')==[135] and not m.seeks and not did(m,'mclSetResume')
    step(m,O['SCRUB_MS']+5000,wait=True); assert not m.seeks and not m.starts and m.nodes[m.slider]['value']==105
    m.word(m.event+O['EVENT_Y'],0)  # call's key is EVENT_X: a touch right of the slider
    m.call(374,address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'])
    assert not m.starts and m.nodes[m.slider]['value']==105 and m.timers
    m.word(PLAY_STATUS,2); step(m,1000,wait=True); assert not m.starts
    step(m,1500,wait=True); assert m.starts==[m.win] and not m.timers; passed()
    # Leaving and reopening Now Playing, still paused on that track and second, puts the bar back on
    # the paused seek's target and waits for it again, so a new scrub starts from it; resumed (the
    # second moved), the bar is stock's.
    def reopen(m):
        f,ctx=m.handler(m.win,O['EVT_DESTROY']); step(m,address=f,args=(ctx,m.event,0,0),gap=0)
        m.starts=[]; m.calls.clear(); scrub_page(m=m); keep(m)
    m=scrub_page(); m.handlers.pop(syms['mclGetPlayStatus']); m.word(PLAY_STATUS,3); m.playtime=100
    centre(m); step(m); centre(m,50); assert did(m,'mclSetSeek')==[105]
    reopen(m); assert m.nodes[m.slider]['value']==105 and m.nodes[m.elapsed]['text']=='1:45'
    assert did(m,'playing_timer_clear')==[m.win]; step(m,5000,wait=True); assert not m.starts
    centre(m); step(m); assert m.nodes[m.slider]['value']==110; passed()
    centre(m,50); m.playtime=111; reopen(m); assert m.nodes[m.slider]['value']==111 and m.starts==[]
    m.playtime=100; reopen(m); assert m.nodes[m.slider]['value']==100; passed()
    # SCRUB_MS without input commits once and gives the wheel back to the volume, not a tick sooner.
    m=scrub_page(); centre(m); step(m)
    step(m,O['SCRUB_MS']-1,wait=True); assert m.seeks==[] and not m.starts
    step(m,1,wait=True); assert m.seeks==[105] and not m.starts  # settle_poll restarts it
    step(m,O['SCRUB_MS'],wait=True); ended(m,[105]); assert m.nodes[m.slider]['value']==105; passed()
    # Reversal: the target is where the wheel left it, committed once.
    m=scrub_page(); centre(m); step(m); step(m); step(m,O['KEY_PREV'])
    assert m.nodes[m.slider]['value']==105; centre(m,50); step(m,O['SCRUB_MS'],wait=True); ended(m,[105]); passed()
    # No movement, including turning against an end, seeks nothing on any exit.
    for end in ('centre','timeout','bound'):
        m=scrub_page(value=225 if end=='bound' else 100); centre(m)
        if end=='bound': assert step(m)==11 and m.nodes[m.slider]['value']==225
        if end=='timeout': step(m,O['SCRUB_MS'],wait=True)
        else: centre(m,50)
        ended(m,[],end); passed()
    # Another double press, Return (swallowed) and touch each end it, committing the target once.
    for end in ('centre','return','touch'):
        m=scrub_page(); centre(m); step(m)
        if end=='centre': centre(m,50)
        elif end=='return': assert step(m,O['KEY_RETURN'],gap=50)==11
        else: step(m,address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'],gap=50)
        step(m,O['SCRUB_MS'],wait=True); ended(m,[105],end); passed()
    # A single press turns the screen off DOUBLE_CLICK_MS later, not a millisecond sooner, and
    # leaves no toggle behind.
    m=scrub_page()
    assert Machine.release(m)==11; m.advance(DC-1,clear=False); assert not m.screens
    m.advance(1,clear=False); assert m.screens==[0] and not m.u.mem_read(syms['g_backlight_status'],1)[0]
    keep(m); m.byte(syms['g_backlight_status'],1); step(m,1000+O['SCRUB_MS'],wait=True)
    assert m.seeks==[] and not did(m,'playing_timer_clear') and not m.timers and m.call()==0; passed()
    # Scrubbing, a single press commits at once and the screen stays on; a second as quick (a
    # double press) is swallowed too.
    for presses in (1,2):
        m=scrub_page(); centre(m); step(m)
        assert step(m,O['KEY_CENTER'])==11 and m.seeks==[105] and not m.starts  # settle_poll restarts it
        if presses==2: assert step(m,O['KEY_CENTER'],gap=100)==11
        step(m,DC+O['SCRUB_MS'],wait=True); assert not m.screens and m.u.mem_read(syms['g_backlight_status'],1)[0]
        ended(m,[105],presses); passed()
    # Another window on top ends it with the one commit; the page's destruction drops the target and
    # keeps the timer off.
    m=scrub_page(); centre(m); step(m)
    m.top=m.node('window','home_page'); step(m); assert m.seeks==[105] and not m.starts
    step(m,O['SCRUB_MS'],wait=True); assert m.seeks==[105] and m.starts==[m.win] and not m.timers; passed()
    m=scrub_page(); centre(m); step(m)
    f,ctx=m.handler(m.win,O['EVT_DESTROY']); step(m,address=f,args=(ctx,m.event,0,0),gap=0)
    assert m.seeks==[] and not m.starts and not m.timers; passed()
    # A track change since the scrub began drops the target: the next tick (without moving it), the
    # timeout or an exit each end the scrub without seeking.
    for end in ('tick','timeout','return'):
        m=scrub_page(); centre(m); step(m); m.word(O['MCL_POS'],2)
        if end=='tick': assert step(m)==11 and m.nodes[m.slider]['value']==105
        elif end=='timeout': step(m,O['SCRUB_MS'],wait=True)
        else: assert step(m,O['KEY_RETURN'],gap=50)==11
        ended(m,[],end); passed()
    # The lyrics page (slide value 1) with lyrics, on stock's rows, which the page keeps when it has no
    # memory for its table (lyrics.c; its own rows below). Each UI loop pass (ringnav_sleep) takes the wheel
    # raw (wheel_raw: nothing ticks) and glides scroll_lrc by its travel, a pixel at a time: a tick's
    # 28 units at 50% are a 20 px row, eased in over the passes, within the content padded to a row
    # top. Stock's timer keeps the bar going, but its follow leaves lyrics the wheel moved where they
    # are until the slide_view leaves the lyrics page or another song starts, at the top. Let go, the
    # wheel snaps them to the nearest row top; until then a finger stays on it (wheel_held). Another
    # page, no lyrics, another window, the screen off or a finger on the screen gives the wheel back
    # to its ticks and the volume.
    ROWS=(20,50,20,20,25)+(20,)*7  # tops 0 20 70 90 110 ...; 275 px of content, at most 97 down
    sleep_hook=HOOKS['main_loop_sleep_default'][0]
    def lyric_page(page=1,size=9,lines=len(ROWS),config=None):  # stock's rows: no memory for the page's table
        m=scrub_page(); m.mock('calloc@GLIBC_2.0','pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
        m.word(0xa2638c,0x1000010); m.handlers[0x1000010]='free'  # free's GOT slot, as Coverflow's
        m.lyric_size=size; m.handlers[sleep_hook+12]='stock_sleep'; m.config.update(config or {})
        rows=[m.node('label') for _ in range(lines)]
        for i,r in enumerate(rows): m.word(r+O['W_Y'],sum(ROWS[:i])); m.word(r+O['W_H'],ROWS[i])
        m.lrc=m.node('scroll_view','scroll_lrc',rows)
        m.word(m.lrc+O['W_H'],178); m.word(m.lrc+O['VIEW_CONTENT_H'],sum(ROWS))
        m.slide=m.node('slide_view','slide_view',[m.lrc],value=page)
        m.nodes[m.win]['children'].append(m.slide)
        assert m.call(address=playing,args=(m.win,7,0,0),gap=0)==0; m.calls=[]
        m.word(payload_syms['wheel_held'],1)
        return m
    def y(m): return m.get(m.lrc+O['SCROLL_Y'])
    def raw(m): return m.get(payload_syms['wheel_raw'])
    def loop(m,travel=None,ms=8):  # a UI loop pass ms on, the wheel turned travel raw units since the last
        if travel is not None: m.word(payload_syms['wheel_travel'],travel&0xffffffff)
        n=len(m.calls); m.advance(ms,clear=False); m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False)
        for name,seen in (('player_seek_time','seeks'),('playing_timer_start','starts')):
            setattr(m,seen,getattr(m,seen,[])+[c[1] for c in m.calls[n:] if c[0]==name])
        return y(m)
    def settle(m,n=60): return [loop(m) for _ in range(n)]
    def follow(m,to): m.call(address=payload_syms['ringnav_lyric_follow'],args=(m.lrc,0,to,10),gap=0,clear=False); return y(m)
    for kw in ({'page':0},{'size':0},{'lines':0}):
        m=lyric_page(**kw); loop(m); assert raw(m)==0 and m.get(payload_syms['wheel_travel'])==0
        assert m.call()==0 and not did(m,'playing_timer_clear'),kw; passed()
    # Clockwise (x falling) scrolls down: a tick's travel glides one row, never a jump, slowing in.
    m=lyric_page(); assert loop(m)==0 and raw(m)==1; m.calls=[]
    ys=[loop(m,-28)]+settle(m); steps=[b-a for a,b in zip([0]+ys,ys)]
    assert ys[-1]==20 and 0<ys[0]<5 and all(0<=s<=4 for s in steps) and steps[-1]==0,ys
    assert ('reset_poweroptions_timer',1,1,1) in [c[:4] for c in m.calls]  # the screen stays on
    assert not did(m,'playing_timer_clear') and not did(m,'sleep_ms'); passed()  # nor does the loop idle
    # A pixel's worth moves a pixel, fractions carried: three single units are 34/16 px.
    for _ in range(3): loop(m,-1)
    settle(m); assert y(m)==22; passed()
    # A spin keeps gliding and stops at the padded end, a row top: 110 + 178, not 275 - 178 = 97.
    for _ in range(60): loop(m,-12)
    assert settle(m)[-1]==110 and m.get(m.lrc+O['VIEW_CONTENT_H'])==288; passed()
    # Anticlockwise back up, to the top.
    for _ in range(60): loop(m,12)
    assert settle(m)[-1]==0; passed()
    # Wheel Sensitivity sets the travel per row as per tick: 14 units at 100%.
    m=lyric_page(config={'WHEELSENSITIVITY':'100'}); loop(m); loop(m,-14); assert settle(m)[-1]==20; passed()
    # Held: stock's follow leaves them, also after SCRUB_MS, while the bar keeps going.
    m=lyric_page(); loop(m); loop(m,-56); settle(m); assert y(m)==40
    assert follow(m,0)==40; m.advance(O['SCRUB_MS']+1000); assert settle(m)[-1]==40 and follow(m,0)==40
    assert not did(m,'playing_timer_clear') and not m.starts; passed()
    # Swiped away, the hold goes and the follow jumps (stock's scroll_to) while the page is hidden;
    # back on the page the follow glides.
    m.nodes[m.slide]['value']=0; loop(m); assert raw(m)==0 and follow(m,70)==70
    m.nodes[m.slide]['value']=1; loop(m); assert raw(m)==1 and follow(m,90)==70
    ys=settle(m); assert ys[-1]==90 and ys[0]<90,ys; passed()
    # Another song starts at the top, on the page or off it, the hold gone: its follow glides to its line.
    for off in (0,1):
        m=lyric_page(); loop(m); loop(m,-56); settle(m); assert follow(m,0)==40
        if off: m.top=m.node('window','home_page')
        m.word(O['MCL_POS'],2); assert loop(m)==0; m.top=m.win; loop(m)
        follow(m,20); assert settle(m)[-1]==20; passed()
    # The same song (its queue place and file) leaves them where they are.
    m=lyric_page(); loop(m); loop(m,-56); settle(m); m.word(O['MCL_POS'],1); assert loop(m)==40 and follow(m,0)==40; passed()
    # The page's row rebuild (LYRIC_ROWS, stock's mocked) keeps the same song's place; another song's
    # rows start at the top whatever moved the view since, and drop the hold.
    def rows(m): m.call(address=payload_syms['ringnav_lyric_rows'],args=(m.win,0,0,0),gap=0,clear=False); return y(m)
    m=lyric_page(); m.handlers[0x529f0c]='stock_lyric_rows'; loop(m); loop(m,-56); settle(m)
    assert rows(m)==40 and follow(m,0)==40; passed()
    m.word(O['MCL_POS'],2); m.word(m.lrc+O['SCROLL_Y'],60); assert rows(m)==0 and loop(m)==0
    follow(m,20); assert settle(m)[-1]==20; passed()  # the follow is back
    # A swipe that brings the lyrics in holds nothing, even while something else moves them (stock's
    # scroll animation, ending after the screen comes on); a finger that lands on them stops that.
    m=lyric_page(page=0); loop(m); m.pressed=1; loop(m); m.nodes[m.slide]['value']=1; loop(m)
    m.word(m.lrc+O['SCROLL_Y'],60); loop(m); m.pressed=0; loop(m); follow(m,0); assert settle(m)[-1]==0; passed()
    m=lyric_page(); loop(m); a=m.alloc(0x80); m.word(m.lrc+O['VIEW_ANIMATOR'],a); m.pressed=1; loop(m)
    assert m.get(m.lrc+O['VIEW_ANIMATOR'])==0 and ('widget_animator_destroy',a) in [c[:2] for c in m.calls]; passed()
    # The report: scroll, swipe away, screen off, next song, screen on, swipe back: the new song's top.
    m=lyric_page(); m.handlers[0x529f0c]='stock_lyric_rows'; loop(m); loop(m,-56); settle(m)
    m.nodes[m.slide]['value']=0; loop(m); assert follow(m,20)==20  # off the page: stock's jump
    m.byte(syms['g_backlight_status'],0); loop(m); m.word(O['MCL_POS'],2); assert loop(m)==0
    m.word(m.lrc+O['SCROLL_Y'],60); assert rows(m)==0  # a stale follow, then the new song's rows
    m.byte(syms['g_backlight_status'],1); loop(m); m.pressed=1; loop(m); m.nodes[m.slide]['value']=1; loop(m)
    m.word(m.lrc+O['SCROLL_Y'],20); loop(m); m.pressed=0; loop(m)
    follow(m,0); assert settle(m)[-1]==0; passed()
    # Let go, the wheel snaps them to the nearest row top, down or up, gliding; held, they stay.
    m=lyric_page(); loop(m); loop(m,-36); assert settle(m,200)[-1]==26  # tops 0 20 70: 26 is nearer 20
    m.word(payload_syms['wheel_held'],0); ys=settle(m); assert ys[-1]==20 and ys[0]>20,ys
    m.word(payload_syms['wheel_held'],1); loop(m,-50); assert settle(m)[-1]==56  # nearer 70
    m.word(payload_syms['wheel_held'],0); assert settle(m)[-1]==70 and follow(m,0)==70; passed()  # still held
    # Let go during the glide, it snaps from the glide's target; a still finger let go moves nothing.
    m=lyric_page(); loop(m); loop(m,-36); m.word(payload_syms['wheel_held'],0); assert settle(m)[-1]==20
    m.word(payload_syms['wheel_held'],1); m.word(m.lrc+O['SCROLL_Y'],30); loop(m); m.word(payload_syms['wheel_held'],0)
    assert settle(m)[-1]==30; passed()  # not the wheel's doing: no snap
    # Another window, the screen off, a finger down or no lyrics hand the wheel back to the ticks,
    # dropping travel from before; on the page again the wheel is the lyrics' once more.
    def other(m): m.top=m.node('window','home_page')
    def back(m): m.top=m.win
    def dark(m,on=0): m.byte(syms['g_backlight_status'],on)
    def touch(m,on=1): m.pressed=on
    def bare(m,on=0): m.lyric_size=9 if on else 0
    for off,on in ((other,back),(dark,lambda m: dark(m,1)),(touch,lambda m: touch(m,0)),(bare,lambda m: bare(m,1))):
        m=lyric_page(); loop(m); assert raw(m)==1
        off(m); loop(m,-28); settle(m); assert raw(m)==0 and y(m)==0,off
        m.word(payload_syms['wheel_travel'],-99&0xffffffff); on(m); loop(m); assert raw(m)==1 and settle(m)[-1]==0,off
        loop(m,-28); assert settle(m)[-1]==20,off; passed()
    # A finger that drags them holds them as the wheel does; the follow waits while it is down. A tap
    # that moves nothing holds nothing.
    m=lyric_page(); loop(m); m.pressed=1; loop(m); m.word(m.lrc+O['SCROLL_Y'],40)
    assert follow(m,0)==40; m.pressed=0; loop(m); assert follow(m,0)==40 and settle(m)[-1]==40; passed()
    m=lyric_page(); loop(m); m.pressed=1; loop(m); m.pressed=0; loop(m); follow(m,20); assert settle(m)[-1]==20; passed()
    m=lyric_page(); loop(m); m.top=m.node('window','home_page'); m.pressed=1; loop(m); m.word(m.lrc+O['SCROLL_Y'],40)
    m.pressed=0; loop(m); m.top=m.win; loop(m); follow(m,0); assert settle(m)[-1]==0; passed()  # another window's touch
    # A tick (before a pass took the wheel raw) glides to the next or previous row's top, quick ticks
    # adding up from the glide's target, ahead of scrub and the volume.
    m=lyric_page()
    assert step(m)==11 and y(m)==0 and settle(m)[-1]==20
    ys=[]
    for _ in range(5): assert step(m,gap=200)==11; ys.append(settle(m)[-1])
    assert ys==[70,90,110,110,110],ys
    for _ in range(3): assert step(m,O['KEY_PREV'],gap=200)==11
    assert settle(m)[-1]==20 and not did(m,'playing_timer_clear') and not m.starts and follow(m,0)==20; passed()
    # A scrub begun on the lyrics page ends at the next tick, committing once, and the tick scrolls;
    # the seek's settle wait brings stock's timer back.
    m=lyric_page(); centre(m); m.nodes[m.slide]['value']=0; step(m); m.nodes[m.slide]['value']=1
    assert step(m)==11 and m.seeks==[105] and settle(m)[-1]==20
    step(m,O['SCRUB_MS'],wait=True); assert m.starts==[m.win] and not m.timers; passed()
    # The scrub's own touch end (np_cancel) keeps the hold; the page's destruction drops it and the
    # wheel's raw travel.
    m=lyric_page(); loop(m); loop(m,-28); settle(m)
    step(m,address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'],gap=50)
    assert follow(m,0)==20 and not m.starts and not m.timers; passed()
    f,ctx=m.handler(m.win,O['EVT_DESTROY']); step(m,address=f,args=(ctx,m.event,0,0),gap=0)
    loop(m); assert raw(m)==0 and not m.starts and not m.timers; passed()

# Lyrics from tags (lyrics.c ringnav_lyric): stock's loader (mocked) runs first; only when it left no
# lines for a local song does TagLib open the file, LYRICS then UNSYNCEDLYRICS, a line each into stock's
# list at a time no track reaches. Online queues, other rows and an unbound GOT entry ask nothing.
import subprocess
from build import GP
got=next(int(l.split()[0],16) for l in subprocess.run(['readelf','-AW',B/'stock-demo'],capture_output=True,text=True).stdout.splitlines() if l.endswith(' taglib_file_new'))
lib=symbols(B/'libtag_c.so.2.0.1')
assert got==O['TAGLIB_NEW_GOT'] and [lib[n] for n in ('taglib_file_new','taglib_property_get','taglib_property_free')]==[O['TAGLIB_FILE_NEW'],O['TAGLIB_PROPERTY_GET'],O['TAGLIB_PROPERTY_FREE']]
m=Machine(); assert m.get(GP-0x7f44)==0xa40000 and m.get(0x5aac60)==0x8c44bdac and m.get(0x5aac4c)==0x2624bdb8; passed()  # -0x4254, -0x4248
TAGLIB=0x1180000  # libtag_c's load address here
class LyricMachine(QueueMachine):
    def __init__(self,tags,lrc=(),cls=0xf001,kind=8,bound=True):
        super().__init__(cls=cls); self.tags=tags; self.lrc=list(lrc); self.bound=bound; self.lines=[]; self.asked=[]; self.tag_calls=[]
        self.word(self.items(self.get(syms['mcl_pdeqplaylist']))[0]+O['REC_TYPE'],kind)
        self.word(O['MCL_LYRICS'],self.deque([],'stLyricInfo'))
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
        self.word(0xa2638c,0x1000010); self.handlers[0x1000010]='lyric:libc_free'  # free's GOT slot, as Coverflow's (the stamps' table)
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
        elif name=='libc_free': pass
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
m=LyricMachine({'LYRICS':'x'},lrc=['[00:01.00]a']); assert m.load()==1 and m.tag_calls==['stock'] and m.lines==['[00:01.00]a']; passed()
m=LyricMachine({'LYRICS':'First\r\n\nThird\n'}); assert m.load()==1 and m.asked==['LYRICS']
assert m.lines==[(FAR,'First'),(FAR,'Third')] and m.tag_calls==['stock',('new','/p/A'),'pfree','free'],m.tag_calls; passed()
m=LyricMachine({'UNSYNCEDLYRICS':'Only\rline'}); assert m.load()==1 and m.asked==['LYRICS','UNSYNCEDLYRICS'] and m.lines==[(FAR,'Only'),(FAR,'line')]; passed()
for empty in ('',None):
    m=LyricMachine({'LYRICS':empty,'UNSYNCEDLYRICS':'Fallback'}); assert m.load()==1 and m.asked==['LYRICS','UNSYNCEDLYRICS'] and m.lines==[(FAR,'Fallback')]
    assert m.tag_calls==['stock',('new','/p/A'),'pfree','pfree','free']; passed()  # each property list freed once
m=LyricMachine({}); assert m.load()==-1 and m.asked==['LYRICS','UNSYNCEDLYRICS'] and not m.lines and m.tag_calls==['stock',('new','/p/A'),'free']; passed()
for kw in ({'cls':0xf1},{'cls':0xf2},{'kind':4}):
    m=LyricMachine({'LYRICS':'x'},**kw); assert m.load()==-1 and m.tag_calls==['stock'] and not m.lines,kw; passed()
m=LyricMachine({'LYRICS':'x'},bound=False); assert m.load()==-1 and not m.asked and not m.lines and m.tag_calls[-1]=='free'; passed()
m=LyricMachine({'LYRICS':('a'*99+'\n')*700}); assert m.load()==1 and len(m.lines)==656 and m.lines[-1]==(FAR,'a'*99); passed()  # 64 KB
# A tag in LRC form (q2-deezer's synced lyrics): each stamped line at its stamp's whole seconds, as stock's
# .lrc parser keeps them, in time order, a line with two stamps twice; "[ar:...]" and the lines before the
# first stamp, and the unstamped ones after it, left out, and so is a stamp alone.
m=LyricMachine({'LYRICS':'[ar:Someone]\nIntro\n[00:12.50]Two\r\n[00:03]One\n[01:02.00][00:20.10]Both\nloose\n[00:30.00]\n'})
assert m.load()==1 and m.lines==[(3,'One'),(12,'Two'),(20,'Both'),(62,'Both')],m.lines; passed()
# Bracketed text that is no stamp leaves the lyrics plain.
m=LyricMachine({'LYRICS':'[Chorus]\nLa la\n[1:2]\n[00:61]x'}); assert m.load()==1
assert m.lines==[(FAR,'[Chorus]'),(FAR,'La la'),(FAR,'[1:2]'),(FAR,'[00:61]x')],m.lines; passed()

# Millisecond stamps and A2 word stamps ("<mm:ss.xx>" before a word): stock's list keeps whole seconds and
# the text without the word stamps, in millisecond order.
m=LyricMachine({'LYRICS':'[00:12.50]<00:12.50>Hel<00:12.90>lo <00:13.40>world<00:14.00>\n[00:15.05][00:40.00]<00:15.10>Twice\n[00:16]<00:16.00>\n'})
assert m.load()==1 and m.lines==[(12,'Hello world'),(15,'Twice'),(40,'Twice')],m.lines; passed()
m=LyricMachine({'LYRICS':'[00:01.999]a\n[00:01.5]b\n[00:01:25]c\n[00:01.012345]d\n'})
assert m.load()==1 and m.lines==[(1,'d'),(1,'c'),(1,'b'),(1,'a')],m.lines; passed()
m=LyricMachine({'LYRICS':'a<b>c <1:2> <00:61.00>x <00:01.00\n'}); assert m.load()==1 and m.lines==[(FAR,'a<b>c <1:2> <00:61.00>x <00:01.00')]; passed()
# Even spacing: no line keeps the spaces at its ends (ASCII's, no-break, ideographic, zero-width, a byte-order
# mark), and a blank one goes, an empty line between verses, a line of spaces, a stamp alone or CR CR LF's;
# lyrics that are all blank leave stock's result.
m=LyricMachine({'LYRICS':'One\r\r\nTwo\r\r\n\r\r\n\n\nThree\r\r\n'}); assert m.load()==1 and m.lines==[(FAR,'One'),(FAR,'Two'),(FAR,'Three')],m.lines; passed()
m=LyricMachine({'LYRICS':'  \t\n\u3000\n\u00a0x\u00a0 \n\u200b\n\ufeff  y  \v\n'}); assert m.load()==1 and m.lines==[(FAR,'x'),(FAR,'y')],m.lines; passed()
m=LyricMachine({'LYRICS':'[00:01.00]A\n[00:02.00]\n[00:03.00]  \u3000\n\n[00:04.00]  B  \n'}); assert m.load()==1 and m.lines==[(1,'A'),(4,'B')],m.lines; passed()
m=LyricMachine({'LYRICS':'[00:01.00] <00:01.00>Hi <00:01.50>there <00:02.00>\n'}); assert m.load()==1 and m.lines==[(1,'Hi there')]; passed()
m=LyricMachine({'LYRICS':' \n\r\n[00:02.00]\n'}); assert m.load()==-1 and not m.lines and m.tag_calls==['stock',('new','/p/A'),'pfree','free']; passed()

# A .lrc (stock's loader mocked: its fopen comes here, LYRIC_OPEN, then its parse) is read again, as a tag:
# its synced lines replace stock's whole seconds, word stamps cut out, blank lines gone, a UTF-8 mark at its
# start skipped; unsynced, stock's stay.
from build import LYRIC_OPEN
assert struct.unpack_from('<I',demo,fileoff(demo,LYRIC_OPEN[0]))[0]==jal(payload_syms['ringnav_lrc_open'])
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
lrc='\ufeff[ar:x]\n[00:01.20]<00:01.20>A<00:01.70>b\n[00:01.90]\n\n[00:00.50]  Zero\r\r\n'.encode()
m=LrcMachine(path='/mnt/mmc/a.lrc',files={'/mnt/mmc/a.lrc':lrc},lrc=[(1,'<00:01.20>A<00:01.70>b'),(1,' '),(0,'  Zero')])
assert m.load()==1 and m.lines==[(0,'Zero'),(1,'Ab')],m.lines; passed()
m=LrcMachine(path='/mnt/mmc/a.lrc',files={'/mnt/mmc/a.lrc':b'plain\nwords'},lrc=[(FAR,'plain'),(FAR,'words')])
assert m.load()==1 and m.lines==[(FAR,'plain'),(FAR,'words')]; passed()

# The lyrics page (lyrics.c, iPod). Each load sends its lines, milliseconds
# and word stamps, to the page; stock's rows (its builder mocked: a label a line, its text and lrc_time
# seconds, offset 0; its page init builds them too) become blank rows as high as their lines, LYRIC_SLOT for
# one row, which the list layout (mocked) stacks after a blank LYRIC_SLOT, each painted with its line; the
# page centres its playing line by its own clock, the player's whole seconds and time_now_ms. The canvas
# measures a character as half its font's pixels here.
if variant=='ipod':
    SLOT=O['LYRIC_SLOT']; DIM,REST,LIT=(color_t(O[k]) for k in ('LYRIC_DIM','LYRIC_REST','LYRIC_LIT'))
    class KaraokeMachine(LrcMachine):
        def __init__(self,tags={}):
            super().__init__(tags)
            self.k_inks=[]  # self.lrc: the page's scroll_lrc
            self.handlers[playing+12]='stock_playing'; self.handlers[0x529f0c]='k:rows'
            for n in ('widget_layout_children','widget_get_canvas','canvas_draw_text','canvas_measure_text'):
                self.handlers[syms[n]]='k:'+n
            self.handlers.pop(syms['mclGetPlayStatus']); self.word(PLAY_STATUS,2)
        def hook(self,u,address,size,unused):
            name=self.handlers.get(address,'')
            a,b,c,d=[u.reg_read(r) for r in REGS[:4]]; ret=0
            if name not in ('k:rows','k:widget_layout_children','k:widget_get_canvas','k:canvas_draw_text','k:canvas_measure_text'):
                return super().hook(u,address,size,unused)
            name=name[2:]
            if name=='rows': self.build()
            elif name=='widget_layout_children':  # list_view(x=X,y=Y,s=0): stacked from Y
                at=int(re.search(r'y=(\d+)',self.nodes[a]['children_layout'])[1])
                for k in self.nodes[a]['children']: self.word(k+O['W_Y'],at); at+=self.get(k+O['W_H'])
            elif name=='widget_get_canvas': ret=self.canvas
            elif name=='canvas_measure_text': u.reg_write(UC_MIPS_REG_F0,struct.unpack('<I',struct.pack('<f',c*self.font[1]/2))[0])
            elif name=='canvas_draw_text':
                self.k_inks.append((''.join(chr(self.get(b+4*j)) for j in range(c)),signed(d),signed(self.get(u.reg_read(UC_MIPS_REG_SP)+16)),
                                  self.get(self.lcd+O['LCD_TEXT_COLOR']),self.font[1],self.clip,self.font[0]))
            self.returns(ret,clobber=True)
        def build(self):  # stock's rows: a 225 px label a line, 20 px high
            kids=[self.node('label',text=t,lrc_time=sec) for sec,t in self.lines]
            for k in kids: self.word(k+O['W_PARENT'],self.lrc); self.word(k+O['W_W'],225); self.word(k+O['W_H'],20)
            self.nodes[self.lrc]['children']=kids; self.word(self.lrc+O['SCROLL_Y'],0)
        def show(self,playtime=0,lrc=None):  # Now Playing opened: stock's init builds the rows, then ringnav_playing
            self.playtime=playtime; self.lrc=lrc or self.node('scroll_view','scroll_lrc')  # lrc: AWTK reusing its memory
            self.word(self.lrc+O['W_W'],375); self.word(self.lrc+O['W_H'],178); self.build()
            self.slide=self.node('slide_view','slide_view',[self.lrc],value=1)
            self.win=self.top=self.node('window','playing_page',[self.slide]); self.word(self.win+O['W_PARENT'],self.wm)
            assert self.call(address=playing,args=(self.win,7,0,0),gap=0)==0
            self.handlers[sleep_hook+12]='stock_sleep'; self.word(payload_syms['wheel_held'],1)
            return y(self)
        def rows(self): self.call(address=payload_syms['ringnav_lyric_rows'],args=(self.win,0,0,0),gap=0,clear=False); return y(self)
        def lyric_row(self,i): return self.nodes[self.lrc]['children'][i]
        def at_ms(self,ms=0): self.advance(ms,clear=False); return self.call(address=payload_syms['lyrics_now'],args=(0,0,0,0),gap=0,clear=False)
        def ink(self,i):  # each row's draws but the bold's (LYRIC_BOLD) a pixel right
            self.k_inks=[]; self.clip=(0,0,375,SLOT); self.paint(self.lyric_row(i),gap=0); return [x for x in self.k_inks if x[1]==16]
        def dirty(self): return [c[1] for c in self.calls if c[0]=='widget_invalidate_force' and c[1] in self.nodes[self.lrc]['children']]
    def karaoke(lines=((5,'One'),(9,'Two two'),(12,'Three'),(20,'Four'),(30,'Five')),**kw):
        m=KaraokeMachine(**kw); m.lines=list(lines); return m
    # The rows stock's init built turn blank rows the page's width, LYRIC_SLOT for a line of one row, line i's
    # top (i + 1) slots down; with no load's table, the labels' (whole seconds, no word stamps). Before the
    # first line the first sits in the middle, unlit, all LYRIC_REST; the playing line glides there on the page.
    m=karaoke(); assert m.show(4)==0 and m.nodes[m.lrc]['children_layout']==f'list_view(x=0,y={SLOT},s=0)'
    assert [(m.nodes[r]['text'],m.get(r+O['W_H']),m.get(r+O['W_W']),m.get(r+O['W_Y'])) for r in m.nodes[m.lrc]['children']]==[('',SLOT,375,(i+1)*SLOT) for i in range(5)]
    assert [(x[0],x[3],x[6]) for x in m.ink(0)]==[('One',REST,'default')]; passed()  # the system font
    assert settle(m)[-1]==0 and m.get(m.lrc+O['VIEW_CONTENT_H'])==4*SLOT+178; passed()  # the last line can reach the middle
    m.playtime=5; assert settle(m)[-1]==0; m.playtime=9; ys=settle(m); assert ys[-1]==SLOT and 0<ys[0]<SLOT,ys; passed()
    # Off the page it jumps, at stock's timer's pace (LYRIC_FOLLOW_MS); the wheel moves them a line a tick's
    # worth and holds them, off the follow, until the page swipes away and back.
    m.nodes[m.slide]['value']=0; m.playtime=12; assert loop(m)==SLOT and loop(m,ms=250)==2*SLOT
    m.nodes[m.slide]['value']=1; loop(m)
    loop(m,-28); assert settle(m)[-1]==3*SLOT; m.playtime=30; assert settle(m)[-1]==3*SLOT; passed()
    m.nodes[m.slide]['value']=0; assert loop(m,ms=250)==4*SLOT; m.nodes[m.slide]['value']=1; assert settle(m)[-1]==4*SLOT; passed()
    # Dark, the page in front follows at that pace too, by the whole second, reading no clock between.
    m.byte(syms['g_backlight_status'],0); m.playtime=20; assert loop(m)==4*SLOT and loop(m,ms=250)==3*SLOT
    m.byte(syms['g_backlight_status'],1); m.playtime=30; assert settle(m)[-1]==4*SLOT; passed()
    # Another song starts at the top, and its first rows too; a refresh's rows for the same song keep the table
    # and the place; a finger on the screen keeps them where they are.
    m.word(O['MCL_POS'],2); m.playtime=0; assert loop(m)==0 and settle(m)[-1]==0 and m.rows()==0; passed()
    m.playtime=12; assert settle(m)[-1]==2*SLOT and m.rows()==2*SLOT and settle(m)[-1]==2*SLOT; passed()
    m.pressed=1; m.playtime=20; assert settle(m)[-1]==2*SLOT; m.pressed=0; assert settle(m)[-1]==3*SLOT; passed()
    # The clock: the first second seen puts it anywhere in that second (its middle, with a poll's slack);
    # the next second narrows it to within a poll after that change; it never runs a poll past its second;
    # paused it stays, and on Play goes on from there until the next second; a seek starts it again.
    m=karaoke(); m.show(); POLL=250
    m.playtime=8; assert m.at_ms()==8000+(999+POLL)//2 and m.at_ms(1000)==8999+POLL
    m.playtime=9; assert m.at_ms()==9000+POLL//2 and m.at_ms(275)==9400
    m.word(PLAY_STATUS,3); assert m.at_ms(500)==9400; m.word(PLAY_STATUS,2); assert m.at_ms()==9400 and m.at_ms(100)==9500
    m.playtime=10; assert m.at_ms(10)==10000+POLL//2; m.playtime=60; assert m.at_ms(10)==60000+(999+POLL)//2; passed()
    # The playing line, timed by the line alone (these lyrics have no word stamps), lit LYRIC_LIT whole as it
    # starts, in the system font drawn three times a pixel apart: "Two two" one 34 px row (pitch 38) from x 16,
    # centred in its slot. The other lines are LYRIC_REST; the clip comes back.
    m=karaoke(); m.show(8); m.at_ms(); m.at_ms(1000); m.playtime=9; m.at_ms(); m.at_ms(275); loop(m,ms=0)  # 9400
    assert m.ink(1)==[('Two two',16,12,LIT,34,(0,0,375,SLOT),'default')] and [x[1] for x in m.k_inks]==[16,17,18],m.k_inks
    assert {x[3] for x in m.ink(0)+m.ink(2)}=={REST} and m.clip==(0,0,375,SLOT); passed()
    m.advance(600,clear=False); loop(m,ms=0); assert [(x[3],x[5]) for x in m.ink(1)]==[(LIT,(0,0,375,SLOT))]; passed()
    # Its rows repaint as the lit width moves, and the last line's as the next one starts.
    m.calls=[]; loop(m); assert not m.dirty(); m.playtime=12; m.calls=[]; loop(m); assert m.dirty()[:2]==[m.lyric_row(1),m.lyric_row(2)]; passed()
    # A line too wide for a row wraps at its last space, at the same 34 px (20 characters a row here, pitch 38),
    # its row on the page as high as its rows plus the gap between lines (LYRIC_SLOT less a pitch), its text
    # centred in it, two pixels less room for the bold; CJK breaks anywhere;
    # past LYRIC_ROWS (4) rows the rest is cut.
    m=karaoke([(10,'abcdefghi '*3+'abcdefghi'),(20,'字'*40),(30,'x'*60),(40,'One'),(50,'y'*150)]); m.show()
    gap=SLOT-38; hs=[2*38+gap,2*38+gap,3*38+gap,SLOT,4*38+gap]
    assert [m.get(m.lyric_row(i)+O['W_H']) for i in range(5)]==hs
    assert [m.get(m.lyric_row(i)+O['W_Y']) for i in range(5)]==[SLOT+sum(hs[:i]) for i in range(5)]
    def whole(i):  # its row at the top; each row's first draw
        m.word(m.lrc+O['SCROLL_Y'],m.get(m.lyric_row(i)+O['W_Y'])); return [x for x in m.ink(i) if x[1]==16]
    assert [(x[0],x[2],x[4],x[6]) for x in whole(0)]==[('abcdefghi abcdefghi',12,34,'default'),('abcdefghi abcdefghi',50,34,'default')]
    assert [(x[0],x[4],x[6]) for x in whole(1)]==[('字'*20,34,'default'),('字'*20,34,'default')] and [x[1] for x in m.k_inks]==[16,17,18]*2
    assert [(len(x[0]),x[2]) for x in whole(2)]==[(20,12),(20,50),(20,88)] and [x[2] for x in whole(3)]==[12]
    assert [len(x[0]) for x in whole(4)]==[20]*4; m.word(m.lrc+O['SCROLL_Y'],0); passed()
    # The playing line is centred, however many rows it has: line 2's 135 px centre on the page's.
    m.playtime=30; settle(m); off=SLOT+hs[0]+hs[1]-(178-hs[2])//2; assert y(m)==off; passed()  # C's division
    # A row the page's edge cuts fades with the square of its share in view; out of view, or nearly, nothing.
    def seen(i): top=SLOT+sum(hs[:i])-off; return max(0,min(top+hs[i],178)-max(top,0))
    assert [seen(i) for i in range(5)]==[0,21,135,22,0]
    assert [{x[3]>>24 for x in m.ink(i)} for i in range(5)]==[set(),{255*21*21//(97*97)},{255},{255*22*22//(SLOT*SLOT)},set()]; passed()
    # With no memory for its table the page keeps stock's rows, their text and layout, and paints nothing over them.
    m=karaoke(); m.mock('calloc@GLIBC_2.0'); m.show(9)
    assert [m.nodes[r]['text'] for r in m.nodes[m.lrc]['children']]==['One','Two two','Three','Four','Five']
    assert 'children_layout' not in m.nodes[m.lrc]
    assert m.ink(1)==[] and loop(m)==0 and m.ink(1)==[]; passed()
    # Now Playing opened again in the last page's scroll view (AWTK's memory reused): its rows become the
    # page's the same way, no stock text left.
    m=karaoke(); m.show(9); f,ctx=m.handler(m.win,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    m.show(9,lrc=m.lrc)
    assert {m.nodes[r]['text'] for r in m.nodes[m.lrc]['children']}=={''} and [x[3] for x in m.ink(1)]; passed()
    # A load's table: its milliseconds and word stamps light each word as it is sung, stamp to stamp ("Hello
    # world" 187 px, "Hel" 51, "Hello " 102), the last word at the sung pace (LYRIC_UNIT_MS a letter,
    # LYRIC_MIN_MS at least); a line without word stamps lights whole; the page takes the table for the rows
    # built from those lines, before them or after, and never for others: those get the labels' table.
    tag='[00:12.50]<00:12.50>Hel<00:12.90>lo <00:13.40>world\n[00:15.00]Next\n'
    def lit(m): return m.ink(0)[-1][5][2]-16
    def at13125(m): m.at_ms(); m.at_ms(275); m.playtime=13; m.at_ms(); loop(m,ms=0)
    m=karaoke([],tags={'LYRICS':tag}); m.load(); m.show(12); at13125(m); assert lit(m)==51+51*225//500
    m.advance(675,clear=False); loop(m,ms=0); assert lit(m)==102+85*400//800; passed()  # 800 ms: "world" is 450
    # Its row repaints as the lit width moves, at most every LYRIC_PAINT_MS (66 ms): 33 passes 8 ms apart, at
    # the first pass past each, 3 times (it moved on most).
    m.calls=[]; [loop(m) for _ in range(33)]; assert m.dirty().count(m.lyric_row(0))==3,m.dirty(); passed()
    m.playtime=15; loop(m,ms=0); assert [(x[0],x[3],x[5]) for x in m.ink(1)]==[('Next',LIT,(0,0,375,SLOT))]; passed()
    m=karaoke([(12,'Hello world'),(15,'Next')],tags={'LYRICS':tag}); m.show(12); m.load(); at13125(m); assert lit(m)==51+51*225//500; passed()
    m=karaoke([],tags={'LYRICS':tag}); m.load(); m.lines=[(12,'Hello world'),(15,'Nest')]; m.show(12); at13125(m)
    assert [x[3] for x in m.ink(0)]==[LIT]; passed()  # from 12 s, 900 ms: all sung
    # Spaces trimmed from a line's ends take its word stamps with them: "Hi " is 51 px, "Hi there" 136, its last
    # stamp at its end.
    m=karaoke([],tags={'LYRICS':'[00:13.00] <00:13.00>Hi <00:13.50>there <00:14.00>\n[00:15.00]Next\n'}); m.load(); m.show(12)
    at13125(m); assert lit(m)==51*125//500; m.advance(625,clear=False); loop(m,ms=0); assert lit(m)==51+85*250//500; passed()
# Download Album Lyrics (onlinematch.c ringnav_playermore): after stock's 8 ⋯ rows (its init mocked), one
# more in stock's row widgets and 20 px label, under Download Cover & Lyrics (PLAYMORE_ROWS' kind 4), its
# button unnamed so stock's row click (an atoi of the name) never sees it; only for a playing song on the
# card with an album. A press asks for Wi-Fi first, copies the album's tracks off stock's records (a CUE
# track left out) and starts the worker; the row shows its progress, a press while it runs cancels, the
# page's end leaves it running, and the timer joins it and toasts the summary. USB storage stops it quietly.
# The job's own steps are test/peq.py's album_check.
assert [m.get(O['PLAYMORE_ROWS']+4*i) for i in range(O['PLAYMORE_COUNT'])]==[2,3,4,5,6,7,0,1]  # Download Cover & Lyrics third
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
        return self.nodes[view]['children']
m=AlbumLyricsMachine(); kids=m.more(); row=kids[3]
assert len(kids)==9 and [k for k in kids if k!=row]==m.stock and m.nodes[row]['style']=='s_listitem_black'
button=m.nodes[row]['children'][0]; icon,label=m.nodes[button]['children']
assert m.nodes[button]['style']=='s_btn_listitem' and not m.nodes[button].get('name')
assert [m.get(button+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[20,0,335,70]
assert m.nodes[icon]['image']=='player_onlinesearch' and [m.get(icon+O[k]) for k in ('W_X','W_W','W_H')]==[10,52,70]
assert m.nodes[label]['style']=='s_scrlabel_white20l' and m.nodes[label]['text']=='Download Album Lyrics'
assert [m.get(label+O[k]) for k in ('W_X','W_W','W_H')]==[72,253,70]; passed()
m=AlbumLyricsMachine(cls=0xf1); assert len(m.more())==8; passed()  # an online queue
for off,v in (('REC_TYPE',4),('REC_ALBUM',0),('REC_PATH',0)):  # a folder, no album tag, no path
    m=AlbumLyricsMachine(); m.word(m.playing()+O[off],v); assert len(m.more())==8,off; passed()
m=AlbumLyricsMachine(); m.word(m.playing()+O['REC_PATH'],m.string('http://x/y')); assert len(m.more())==8; passed()
m=AlbumLyricsMachine(); assert len(m.more(7))==7; passed()  # not stock's rows: left as they are
def album_lyrics_page(**queue):
    m=AlbumLyricsMachine(**queue); button=m.nodes[m.more()[3]]['children'][0]; label=m.nodes[button]['children'][1]
    cue=m.song('Cue'); m.word(cue+O['REC_CUE_END'],90)
    m.found=[m.song('T1'),cue,m.song('T2')]
    def press():
        f,ctx=m.handler(button,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
    return m,label,press
m,label,press=album_lyrics_page()
m.wifi=-1; press(); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.threads; m.wifi=3
press(); assert len(m.threads)==1 and m.nodes[label]['text']=='Downloading 1 of 2'
worker,arg=m.threads[-1]; press(); assert len(m.threads)==1  # a second press: cancelling, no second job
assert m.nodes[label]['text']=='Cancelling…'; passed()
m,label,press=album_lyrics_page(); press(); worker,arg=m.threads[-1]
m.advance(500,clear=False); assert ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls] and m.nodes[label]['text']=='Downloading 1 of 2'
f,ctx=m.handler(label,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)  # the page closes: the job goes on
m.missing=False; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0
assert m.accessed==['/p/T1','/p/T1.lrc','/p/T2','/p/T2.lrc'],m.accessed  # each song's own .lrc found: none fetched
n=len(m.toasts); m.advance(500,clear=False)
assert len(m.toasts)==n+1 and m.toasts[-1][3]=='Album lyrics: 2 already had lyrics' and m.joins==[77]; passed()
press_count=len(m.threads); m.advance(1000); assert len(m.threads)==press_count and len(m.toasts)==n+1; passed()  # the timer has ended
m,label,press=album_lyrics_page(); press(); worker,arg=m.threads[-1]; press()  # cancelled before its first song
assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0 and not m.accessed
m.advance(500,clear=False); assert m.toasts[-1][3]=='Cancelled' and m.nodes[label]['text']=='Download Album Lyrics'; passed()
m,label,press=album_lyrics_page(); press(); worker,arg=m.threads[-1]; n=len(m.toasts)
assert m.call(address=payload_syms['album_lyrics_stop'],args=(0,0,0,0),gap=0)==0 and m.joins==[77]  # USB storage: joined first
m.advance(500,clear=False); assert len(m.toasts)==n and m.nodes[label]['text']=='Download Album Lyrics'; passed()
m.found=[]; press(); assert m.toasts[-1][3]=='No album songs on the card' and len(m.threads)==1; passed()

# Sparse populated initials: one destination per sustained tick, both directions, utilities
# and wrap edges. Album and Artist share article handling; nonalphabetical sorts stay single-step.
def cf_jump(m,at,direction):
    letter=m.alloc(4)
    steps=m.call(address=payload_syms['coverflow_jump'],args=(m.slide,at,direction,letter),gap=0)
    return steps,m.get(letter)
m=CoverflowMachine(albums=8)
for r,name in zip(m.albums,['Apple One','The Apple','Dawn','Dusk','Zulu','9 Lives','-Noise','Été']):
    m.word(r+O['REC_ALBUM'],m.string(name))
m.open()
assert cf_jump(m,0,1)==(2,ord('D')) and cf_jump(m,3,-1)==(3,ord('A'))
assert cf_jump(m,2,-1)==(2,ord('A')) and cf_jump(m,4,1)==(1,ord('#'))
assert cf_jump(m,5,1)==(2,ord('É')) and cf_jump(m,7,1)==(1,0)
assert cf_jump(m,0,-1)==(1,0) and cf_jump(m,1,-1)==(2,0)
assert cf_jump(m,8,1)==(1,0) and cf_jump(m,9,1)==(1,0); passed()
# Unicode punctuation/numbers and missing names share #; letters retain their codepoints.
for name,want in (('١ Song',ord('#')),('० Song',ord('#')),('-Song',ord('#')),('',ord('#')),
                  ('Жизнь',ord('Ж')),('東京',ord('東')),('Ａ Song',ord('Ａ')),('Ｂ Song',ord('Ｂ')),
                  ('Ⰰ Song',ord('Ⰰ'))):
    m=CoverflowMachine(albums=2)
    m.word(m.albums[1]+O['REC_ALBUM'],m.string(name)); m.open()
    assert cf_jump(m,0,1)==(1,want); passed()
m=CoverflowMachine(albums=2); m.word(m.albums[1]+O['REC_ALBUM'],m.string('Unknown Album'))
m.word(m.albums[1]+O['REC_ID'],0xffffffff); m.open(); assert cf_jump(m,0,1)==(1,ord('#')); passed()
# Coverflow owns both halves of a wheel tick: the stock press filter runs, then stops before
# native focus navigation can move the Artists list underneath; release still moves the covers.
m=CoverflowMachine(albums=3)
for page,want in (('localclass_page',0),('coverflow_page',11)):
    if page=='coverflow_page': m.open()
    else: m.top=m.node('window',page)
    for key in (O['KEY_PREV'],O['KEY_NEXT'],O['KEY_CENTER']):
        ret=m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],
                   event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)
        assert ret==(want if key!=O['KEY_CENTER'] else 0)
        assert any(c[0]=='reset_poweroptions_timer' for c in m.calls)
assert m.call()==11; m.advance(300); assert m.get(m.slide+O['SLIDE_INDEX'])==1; passed()

# Native delivery also reaches the old focused widget after the before-filter. Stop every
# wheel-key phase at the covered page/child, before its handler or native vtable can navigate.
m=CoverflowMachine(); under=m.node('window','artistinfo_page'); row=m.node('list_item')
m.word(row+O['W_PARENT'],under); m.word(under+O['W_PARENT'],m.wm); m.open()
m.word(m.page+O['W_PARENT'],m.wm)
for typ in (O['EVT_KEY_DOWN'],0x111,O['EVT_KEY_DOWN_BEFORE'],O['EVT_KEY_UP'],O['EVT_KEY_UP_BEFORE']):
    for target in (under,row):
        for key in (O['KEY_PREV'],O['KEY_NEXT']):
            assert m.call(key,address=HOOKS['widget_dispatch'][0],args=(target,m.event,0,0),event_type=typ,gap=0)==11
            assert not m.dispatched()
for target in (m.page,m.slide,m.wm):
    assert m.call(address=HOOKS['widget_dispatch'][0],args=(target,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0)==0
    assert m.dispatched()
for key in (O['KEY_CENTER'],O['KEY_RETURN']):
    assert m.call(key,address=HOOKS['widget_dispatch'][0],args=(row,m.event,0,0),event_type=O['EVT_KEY_UP'],gap=0)==0
    assert m.dispatched()
# Execute stock's dispatch-then-vtable keydown helper (0x65f688), with the native navigation
# callback replaced by a recorded service; STOP must prevent it from running at all.
m.handlers[0x667974]='reset_poweroptions_timer'
assert m.call(address=0x65f688,args=(row,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0)==11
assert not m.dispatched() and not any(c[0]=='reset_poweroptions_timer' for c in m.calls)
m.top=under
assert m.call(address=0x65f688,args=(row,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0)==0
assert m.dispatched() and any(c[0]=='reset_poweroptions_timer' for c in m.calls)
# The artist page on top: the Artists list under it gets no wheel key either.
artists=m.node('window','localclass_page'); m.word(artists+O['W_PARENT'],m.wm)
low=m.node('list_item'); m.word(low+O['W_PARENT'],artists)
for key in (O['KEY_PREV'],O['KEY_NEXT']):
    assert m.call(key,address=HOOKS['widget_dispatch'][0],args=(low,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0)==11
    assert not m.dispatched()
passed()

# Slow steps stay precise. Continuous 50ms ticks cross Coverflow's 700ms threshold and jump groups.
m=CoverflowMachine(albums=19)
for r,name in zip(m.albums,['A1','A2','A3','D1','D2','D3']+[f'G{i}' for i in range(1,12)]+['Z1','Z2']): m.word(r+O['REC_ALBUM'],m.string(name))
m.open()
stride=m.call(address=syms['slide_menu_item_width'],args=(m.slide,0,0,0),gap=0)+m.get(m.slide+O['SLIDE_SPACER'])
for _ in range(14): assert m.call(gap=50)==11
assert m.get(m.slide+O['SLIDE_INDEX'])==0 and slide(m,m.slide)[2]==-14*stride
assert m.call(gap=50)==11
assert slide(m,m.slide)[2]==-17*stride  # intended index 14 jumps to Z at 17
m.paint(m.slide,gap=0); assert any(t['text']=='Z' for t in m.letters)
a=slide(m,m.slide)[0]
m.advance(400); assert m.get(m.slide+O['SLIDE_INDEX'])==17
m.paint(m.slide,gap=0); assert not any(t['text']=='Z' for t in m.letters)
assert m.call(O['KEY_PREV'],gap=50)==11  # reversal resets sustained scrolling
m.advance(200); assert m.get(m.slide+O['SLIDE_INDEX'])==16; passed()
# A reversal while moving reuses the animator and returns to the adjacent live cover.
m=CoverflowMachine(albums=10); m.open(); m.call(gap=0); m.call(gap=20)
a=slide(m,m.slide)[0]; assert m.call(O['KEY_PREV'],gap=20)==11 and slide(m,m.slide)[0]==a
m.advance(300); assert m.get(m.slide+O['SLIDE_INDEX'])==0; passed()
# Single stops through the wrap and back, even with a sustained run: the library has no cards after
# its albums (its Sort and Refresh are Display settings' rows).
m=CoverflowMachine(albums=3); m.open()
for expected in (1,2,0):
    assert m.call(gap=50)==11; m.advance(200)
    assert m.get(m.slide+O['SLIDE_INDEX'])==expected
for expected in (2,1,0):
    assert m.call(O['KEY_PREV'],gap=50)==11; m.advance(200)
    assert m.get(m.slide+O['SLIDE_INDEX'])==expected
passed()


# The Home card is index 2 of seven; its click opens coverflow_page with every album as a cover
# and nothing after them, the wheel steps the stock slide_menu and centre confirms the cover.
m=CoverflowMachine(); page=m.open()
assert m.nodes[page]['name']=='coverflow_page' and m.slide and not m.threads
covers=m.nodes[m.slide]['children']; assert len(covers)==3
assert all(m.nodes[c]['image'].startswith('file:///mnt/mmc/.coverflow/') for c in covers)
assert 'Album 0' in m.texts(); passed()
m.press(3); assert m.hold()==11 and m.nodes[m.title]['text']=='Album 0'
f,ctx=m.handler(m.back,O['EVT_CLICK']); m.call(O['KEY_RETURN'],address=f,args=(ctx,m.event,0,0),gap=0)
assert m.top==page and m.release()==0; passed()
# Covers step like Home: fast ticks retarget one animator, never stock next/previous, whose
# scroll_to orphans the running animator.
assert m.call(gap=0)==11; a,_,to,dur=slide(m,m.slide); assert to<0 and dur==200
assert m.call(gap=20)==11 and slide(m,m.slide)[0]==a and slide(m,m.slide)[2:]==(2*to,120)
m.advance(300); assert m.get(m.slide+O['SLIDE_INDEX'])==2 and not m.slides and not m.get(m.slide+O['SLIDE_OFFSET'])
assert m.call(O['KEY_PREV'],gap=20)==11 and slide(m,m.slide)[2]==-to; m.advance(300)
assert m.get(m.slide+O['SLIDE_INDEX'])==1 and not m.slides
assert not any(c[0].startswith('slide_menu_scroll_to_') for c in m.calls)
m.clicks=[]; m.word(m.slide+O['SLIDE_INDEX'],1); assert m.confirm()==11 and m.clicks==[covers[1]]; passed()
m.clicks=[]; assert Machine.release(m)==11 and Machine.release(m,100)==0 and m.screens==[0] and not m.clicks; passed()
# The cover's click queries its tracks (staging restored) and lists them; a track hands playing_page
# our deque with its index as an album queue (class 0xff10, mode 2) and the album for stock resume;
# a missing file refuses.
m.byte(syms['g_backlight_status'],1); f,ctx=m.handler(covers[1],O['EVT_CLICK'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; m.advance(0)
assert m.query[:2]==('getMusicByAlbum','Album 1') and m.names(m.get(syms['tools_pdeq_directory']))==['staged']
rows=[w for w in m.nodes if m.nodes[w]['type']=='list_item' and m.alive(w)]; assert len(rows)==2
assert not m.nodes[m.get(m.slide+O['W_PARENT'])]['visible']
f,ctx=m.handler(rows[1],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert len(m.plays)==1 and m.plays[0][0]=='playing_page' and m.plays[0][2:]==(1,0xff10,2)
assert m.text(syms['g_memory_info']+0x611)=='Album 1' and m.u.mem_read(syms['g_memory_info']+0x60d,1)==b'\0'
dq=m.plays[-1][1]&0xffffffff; assert m.names(dq)==['T1','T2']; passed()
m.missing=True; m.call(address=f,args=(ctx,m.event,0,0),gap=0); assert len(m.plays)==1 and 'Storage unavailable' in m.texts(); passed()
# Return: tracks -> covers on the same album, covers -> Home.
assert m.key()==11 and m.nodes[m.get(m.slide+O['W_PARENT'])]['visible'] and m.get(m.slide+O['SLIDE_INDEX'])==1 and not m.homes
assert m.key(O['KEY_NEXT'])==0 and m.key()==11 and m.homes==1; passed()
# The album list outlives the page: a reopen queries nothing until one of the songtable writers runs.
m=CoverflowMachine(); m.open(); m.close(); m.calls=[]
m.open(); assert not m.queried() and len(m.nodes[m.slide]['children'])==3; m.close(); passed()
for writer in WRITERS:
    m.rescan(writer); m.calls=[]; m.open(); assert m.queried()==1; m.close()
    m.calls=[]; m.open(); assert not m.queried(); m.close(); passed()
# Albums without art start the thread behind a progress screen; destroy joins it and drops the poll.
m=CoverflowMachine(cached=False); page=m.open()
assert len(m.threads)==1 and m.timers and any((t or '').startswith('Preparing artwork') for t in m.texts()) and 'Cancel' in m.texts()
f,ctx=m.handler(page,O['EVT_DESTROY']); m.freed=0; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
assert m.joins==[77] and not m.timers and m.freed==4; passed()  # three job paths and the job array
# Tracks list and play in album order (disc, track, then path, then CUE start), named without
# their file's extension; a CUE title keeps its dot.
def track(m,name,path,disc=0,no=0,cue=0):
    r=m.song(name); m.word(r+O['REC_PATH'],m.string(path))
    for k,v in (('REC_DISC',disc),('REC_TRACK',no),('REC_CUE_START',cue)): m.word(r+O[k],v)
    return r
for found,want in (((('b.mp3','/p/b.mp3',2,1),('a.mp3','/p/a.mp3',1,2),('c.mp3','/p/c.mp3',1,1)),['c','a','b']),
                   ((('Mr. Blue','/p/img.flac',0,0,300),('Intro','/p/img.flac'),('01 x.flac','/p/01 x.flac')),['01 x','Intro','Mr. Blue'])):
    m=CoverflowMachine(); m.found=[track(m,*t) for t in found]; m.open(); view=m.tracks()
    assert [m.nodes[m.nodes[i]['children'][0]].get('text') for i in m.nodes[view]['children']]==want
    assert all(m.nodes[m.nodes[i]['children'][0]]['style']=='s_scrlabel_white20c' for i in m.nodes[view]['children'])
    body=next(n for n in m.nodes.values() if m.find('list_view') in n.get('children',())); assert m.nodes[body['children'][0]]['style:normal:font_size']==O['CF_NAME_PX']
    f,ctx=m.handler(m.nodes[view]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    assert [n.rsplit('.',1)[0] if n.endswith(('.mp3','.flac')) else n for n in m.names(m.plays[-1][1]&0xffffffff)]==want; passed()

COVER_ALBUM=['Play Next','Add to Queue','Shuffle','Add to Favourites','Rate Album']  # a cover's menu, Go to artist aside
# Album holds use the live carousel before tracks have ever been opened, and the same ordering.
for action,want in ((0,['A','early','late','B','C']),(1,['A','B','C','early','late'])):
    m=CoverflowMachine(); m.found=[track(m,'late','/p/z',2,1),track(m,'early','/p/a',1,2)]
    m.open(); m.word(m.slide+O['SLIDE_INDEX'],2)
    m.press(100); assert m.hold()==11 and m.release()==0
    assert m.nodes[m.title]['text']=='Album 2' and m.labels()==[*COVER_ALBUM,'Go to Artist']
    assert m.hold()==0
    m.pick(action); m.pick(action); m.advance(0)
    assert m.names()==want and not m.playback() and not m.plays and m.top==m.page
    assert m.query[:2]==('getMusicByAlbum','Album 2') and m.names(m.get(syms['tools_pdeq_directory']))==['staged']; passed()
m=CoverflowMachine(); m.open(); assert len(m.nodes[m.slide]['children'])==3; passed()  # no Sort or Refresh card to hold
for kwargs in ({'albums':0},{'cached':False}):
    m=CoverflowMachine(**kwargs); m.open(); m.press(100); assert m.hold()==0; passed()
for invalidate in ('selection','closed','tracks'):
    m=CoverflowMachine(); m.open(); m.press(100); assert m.hold()==11; m.release()
    if invalidate=='selection': m.word(m.slide+O['SLIDE_INDEX'],1)
    elif invalidate=='closed': m.close()
    else: m.tracks(1)
    m.pick(0); m.advance(0)
    assert m.names()==['A','B','C'] and m.toasts[-1][3]=='Queue unchanged'; passed()
m=CoverflowMachine(); m.open(); m.tracks(2); m.key(); m.word(m.slide+O['SLIDE_INDEX'],1)
assert m.run(2) is None and m.plays[-1][2] in (0,1) and m.plays[-1][3:]==(0xf001,2) and m.play_names==['T1','T2']; passed()
# A Library Albums row queues its own album alone, as a cover does: getMusicByAlbum finds every
# album of the name, here T1 in another folder.
m=CoverflowMachine(page='album_page',cls=O['CLASS_ALBUMS'],pos=2); m.word(m.found[0]+O['REC_PATH'],m.string('/q/T1'))
assert m.run(1) is None and m.names()==['A','B','C','T2'] and m.query[:3]==('getMusicByAlbum','Album',None)
assert m.names(m.get(syms['tools_pdeq_directory']))==['staged'] and m.pool()==[0,1,2,3]; passed()

# Go to artist uses the album record directly; an absent artist omits that action.
m=CoverflowMachine(); m.open(); m.run(5)
assert m.opened[-1][:2]==('localmusic/artistinfo_page',O['CLASS_ALBUMS']); passed()
m=CoverflowMachine(); m.word(m.albums[0]+O['REC_ARTIST'],0); m.open(); m.press(100); assert m.hold()==11
assert m.labels()==COVER_ALBUM; passed()
# Closing the source before the deferred open never creates a dialog.
m=CoverflowMachine(); m.open(); m.press(100)
assert m.call(O['KEY_PLAY'],address=syms['on_wm_keylong_fun'],event_type=O['EVT_KEY_LONG'],gap=0)==11
m.close(); m.advance(0); assert not any(p[0]=='dialog/sortselect_dialog' for p in m.opened); passed()

# Album favourites and ratings (navigation.c rate_*, coverflow.c album_id): a cover keeps 0-5 hearts of its own in
# /mnt/data/ringnav-album-ratings by album_id, the low three bits its hearts, 5 its favourite; songs' ratings, My Fav and
# the queue are untouched. Hold Play/Pause on a cover: Add/Remove from Favourites and Rate album, whose picker is Now
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
                f=0x2000000+16*next(i for i in range(64) if 0x2000000+16*i not in self.open_files); self.open_files[f]=[path,b'']; return f
        elif name=='fread': data=self.files[self.open_files[d][0]]; u.mem_write(a,data[:b*c].ljust(b*c,b'\0')); return min(c,len(data)//b)
        elif name=='fwrite': self.open_files[d][1]+=bytes(u.mem_read(a,b*c)); return c
        elif name=='fclose':
            path,data=self.open_files.pop(a)
            if data: self.files[path]=data
        elif name=='rename': self.files[self.text(b)]=self.files.pop(self.text(a))
        return 0
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
m=AlbumRateMachine(); m.open(); m.word(m.slide+O['SLIDE_INDEX'],1); a1=m.aid(m.albums[1])
assert m.menu()==[*COVER_ALBUM,'Go to Artist'] and m.top==m.page; passed()
# Add, then Remove from Favourites: 5 hearts, then none; the other covers stay unrated.
assert m.run(3) is None and m.kept()==[(a1&~7)|5] and not song_side_effects(m)
assert m.menu()[3:5]==['Remove from Favourites','Rate Album  ♥♥♥♥♥']
assert m.run(3) is None and m.kept()==[] and not song_side_effects(m)
m.word(m.slide+O['SLIDE_INDEX'],0); assert m.menu()[3:5]==['Add to Favourites','Rate Album']
m.word(m.slide+O['SLIDE_INDEX'],1); passed()
# The picker over the covers, on the album's hearts; the wheel moves the rating, never the covers.
assert m.run(4) is None and m.caption()=='Unrated' and m.hearts()==['play_unfav']*5
for _ in range(3): assert m.call()==11
assert m.caption()=='3 of 5' and m.hearts()==['play_fav']*3+['play_unfav']*2 and m.get(m.slide+O['SLIDE_INDEX'])==1 and not m.slides
# Hold Play/Pause meanwhile opens nothing, and its release does not toggle playback.
m.press(9000); assert m.hold()==11 and m.top==m.page and m.release()==0 and m.panel(); passed()
# Return leaves it as it was (taken before the page's own Return), and Coverflow stays; Centre saves, without opening
# the cover.
assert m.call(O['KEY_RETURN'])==11 and m.panel() is None and m.homes==0 and m.top==m.page and m.kept()==[]
assert m.rate(3) is None and m.kept()==[(a1&~7)|3] and not song_side_effects(m)
m.advance(500); assert not m.plays and m.find('scroll_view')==0 and m.nodes[m.get(m.slide+O['W_PARENT'])]['visible']; passed()
# Every rating, each shown on the menu's row; 0 unrated, 5 the favourite.
for want in range(6):
    m.rate(want); assert m.kept()==([(a1&~7)|want] if want else [])
    rows=m.menu(); assert rows[4]==('Rate Album  '+'♥'*want+'♡'*(5-want) if want else 'Rate Album')
    assert rows[3]==('Remove from Favourites' if want==5 else 'Add to Favourites')
passed()
# A tap on a heart saves that many.
assert m.run(4) is None; heart=m.nodes[m.panel()]['children'][2]; f,ctx=m.handler(heart,O['EVT_CLICK'])
m.call(address=f,args=(ctx,m.event,0,0),gap=0); assert m.panel() is None and m.kept()==[(a1&~7)|2]; passed()
# Kept across boots, apart from the songs' table.
m2=AlbumRateMachine(files={ALBUM_RATES:m.files[ALBUM_RATES]}); m2.open(); m2.word(m2.slide+O['SLIDE_INDEX'],1)
assert m2.menu()[4]=='Rate Album  ♥♥♡♡♡' and '/mnt/data/ringnav-ratings' not in m2.files; passed()
# Another cover centred, or the page gone, before Centre: nothing is saved.
assert m.run(4) is None; m.call(); m.word(m.slide+O['SLIDE_INDEX'],0)
assert m.call(O['KEY_CENTER'])==11 and m.toasts[-1][3]=='Album changed' and m.kept()==[(a1&~7)|2]
m.word(m.slide+O['SLIDE_INDEX'],1); assert m.run(4) is None and m.panel()
for t,f,ctx in m.nodes[m.page]['handlers']:
    if t==O['EVT_DESTROY']: m.call(address=f,args=(ctx,m.event,0,0),gap=0)
m.stack.remove(m.page); m.top=m.stack[-1]; m.open(); assert m.panel() is None and m.call()==11 and m.slides; passed()
# A full table and a failed write report it, the rating as it was.
full=b''.join(struct.pack('<I',(i+1)<<3|1) for i in range(O['RATE_SLOTS']))
m=AlbumRateMachine(files={ALBUM_RATES:full}); m.open(); m.word(m.slide+O['SLIDE_INDEX'],1)
assert m.run(3)=='Ratings are full' and m.files[ALBUM_RATES]==full and m.rate(4)=='Ratings are full'; passed()
m=AlbumRateMachine(); m.open(); m.word(m.slide+O['SLIDE_INDEX'],1); m.fail_write=True
assert m.run(3)=='Rating not saved' and m.menu()[3:5]==['Add to Favourites','Rate Album'] and ALBUM_RATES not in m.files; passed()
# A malformed slot (6 or 7 hearts) reads unrated; a truncated file reads empty.
for data in (struct.pack('<I',(a1&~7)|7).ljust(4*O['RATE_SLOTS'],b'\0'),b'\x05\0'):
    m=AlbumRateMachine(files={ALBUM_RATES:data}); m.open(); m.word(m.slide+O['SLIDE_INDEX'],1)
    assert m.menu()[3:5]==['Add to Favourites','Rate Album']; passed()
# An album is album_cmp's: its name and album artist, else its folder (disc folders as one), ASCII case aside but
# the folder's; the song artist never counts, so a compilation is one album. The Unknown card is one album.
def rec(album='Album',aa=None,path='/m/A/1.mp3',artist='Artist',rid=1):
    r=m.song('x'); m.word(r+O['REC_ID'],rid&0xffffffff); m.word(r+O['REC_PATH'],m.string(path)); m.word(r+O['REC_ARTIST'],m.string(artist))
    m.word(r+O['REC_ALBUM'],m.string(album) if album is not None else 0); m.word(r+O['REC_ALBUM_ARTIST'],m.string(aa) if aa else 0)
    return r
same=[(rec(aa='Band'),rec(album='ALBUM',aa='band',path='/x/2.mp3',artist='Other')),
      (rec(path='/m/A/CD1/1.mp3'),rec(path='/m/A/Disc 2/1.mp3')),
      (rec(path='/m/A/1.mp3',artist='One'),rec(path='/m/A/2.mp3',artist='Two')),
      (rec(album='',path='/m/A/1.mp3'),rec(album=None,path='/m/A/2.mp3')),
      (rec(rid=-1,album='Unknown Album'),rec(rid=-1,album=None,path='/z/9.mp3'))]
apart=[(rec(aa='Band'),rec(aa='Other')),(rec(path='/m/A/1.mp3'),rec(path='/m/B/1.mp3')),(rec(path='/m/A/1.mp3'),rec(path='/m/a/1.mp3')),
       (rec(),rec(album='Other')),(rec(aa='Band'),rec()),(rec(rid=-1),rec())]
assert all(m.aid(x)==m.aid(y)!=0 for x,y in same) and all(m.aid(x)!=m.aid(y) for x,y in apart); passed()
# An artist's albums in Coverflow (coverflow_artist) have the same rows and picker; Return leaves the picker first.
m=AlbumRateMachine(); dq=m.deque(m.albums[:2]); m.top=m.node('window','artistinfo_page'); m.stack=[m.top]
assert m.call(address=payload_syms['coverflow_artist'],args=(dq,0,0,0),gap=0)==1; m.page=m.top; m.slide=m.find('slide_menu')
assert m.menu()==[*COVER_ALBUM,'Go to Artist'] and m.rate(5) is None and m.kept()==[(m.aid(m.albums[0])&~7)|5]
assert m.run(4) is None and m.call(O['KEY_RETURN'])==11 and m.panel() is None and m.top==m.page; passed()
# An artist's covers stop at both ends (All albums the last): a step past kicks the end and springs it back, never wrapping.
w=m.slide; index=lambda: m.get(w+O['SLIDE_INDEX'])
assert m.call(O['KEY_PREV'],gap=500)==11; _,kick,goal,_=slide(m,w); assert kick>0 and goal==0
m.advance(300); assert index()==0 and not m.slides
for i in (1,2): m.call(O['KEY_NEXT'],gap=500); m.advance(300); assert index()==i
m.call(O['KEY_NEXT'],gap=500); assert slide(m,w)[1:3]==(-kick,0)
m.advance(300); assert index()==2 and not m.slides and m.get(w+O['SLIDE_OFFSET'])==0; passed()
# The covers' Return closes the unseen artist page under them first, so the Artists list is next.
artist=m.stack[0]; m.stack.insert(0,m.node('window','localclass_page')); m.nodes[m.wm]={'children':m.stack}; m.mock('navigator_back','window_manager_close_window_force')
f,ctx=m.handler(m.page,O['EVT_KEY_UP'])
assert m.call(O['KEY_RETURN'],address=f,args=(ctx,m.event,0,0),gap=0)==11
calls=[c for c in m.calls if c[0] in ('window_manager_close_window_force','navigator_back')]
assert [c[0] for c in calls]==['window_manager_close_window_force','navigator_back'] and calls[0][1:3]==(m.wm,artist); passed()
# Go to Album from a song played in Coverflow: one coverflow_page, on the album's tracks with no covers under them
# (the open one closes); Return goes back to where you were. An album with no tracks opens nothing.
m=CoverflowMachine(); old=m.open(); m.tracks(); m.mock('navigator_back','window_manager_close_window_force',prefix='c:')
assert m.call(address=payload_syms['coverflow_goto_album'],args=(m.albums[1],0,0,0),gap=0)==1
m.page=m.top; assert m.page!=old and [c[2] for c in m.calls if c[0]=='window_manager_close_window_force']==[old]
assert not m.find('slide_menu') and m.texts()[:3]==['Album 1','T1','T2']
assert m.call(address=payload_syms['coverflow_shows_album'],args=(m.albums[1],0,0,0),gap=0)==1
assert m.call(address=payload_syms['coverflow_shows_album'],args=(m.albums[0],0,0,0),gap=0)==0
f,ctx=m.handler(m.page,O['EVT_KEY_UP']); assert m.call(O['KEY_RETURN'],address=f,args=(ctx,m.event,0,0),gap=0)==11
assert [c[0] for c in m.calls].count('navigator_back')==1 and not m.find('slide_menu'); passed()
# The covers' caption is the album artist with Artists: Album Artist, when tagged (a compilation's "Various Artists" or
# its compiler), else the artist.
for kind,aa,want in ((1,'Various Artists','Various Artists'),(2,'Various Artists','Bobby Womack'),(1,'','Bobby Womack')):
    m=CoverflowMachine(); m.word(syms['artist_type'],kind)
    m.word(m.albums[0]+O['REC_ARTIST'],m.string('Bobby Womack')); m.word(m.albums[0]+O['REC_ALBUM_ARTIST'],m.string(aa))
    m.open(); assert m.texts()[:2]==['Album 0',want],m.texts(); passed()
m=CoverflowMachine(); m.found=[]; top=m.top
assert m.call(address=payload_syms['coverflow_goto_album'],args=(m.albums[0],0,0,0),gap=0)==0 and m.top==top; passed()
# Its hold menu row: Coverflow open, or Artist albums: Coverflow, goes there; else, or with no tracks, playerjumpinfo_page.
for cf_open,view,found,want in ((True,'0',True,'coverflow_page'),(False,'1',True,'coverflow_page'),(False,'0',True,'playerjumpinfo_page'),
                                (True,'1',False,'playerjumpinfo_page')):
    m=CoverflowMachine(cls=0xf001); m.config['ARTISTCF']=view
    if cf_open: m.open_pages.add('coverflow_page')
    if not found: m.found=[]
    page=m.top; assert m.run(4,steps=2) is None and m.top!=page
    assert m.nodes[m.top].get('name')==want or m.opened[-1][0]==want; passed()
# An artist's All albums card, after the covers, plays every album from the first song as a library queue: the
# covers' order, each album's songs in album order. A Play/Pause hold on it opens nothing; one album has no card.
class AllAlbums(CoverflowMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='q:getMusicByAlbum': self.found=self.by_album[self.text(u.reg_read(REGS[0]))]
        return super().hook(u,address,size,unused)
for albums,want in (([1,0],['c','d','a','b']),([0],None)):
    m=AllAlbums(); m.by_album={'Album 0':[track(m,'b','/p/b',1,2),track(m,'a','/p/a',1,1)],
                               'Album 1':[track(m,'d','/p/d',2,1),track(m,'c','/p/c',1,1)]}
    m.top=m.node('window','artistinfo_page'); m.stack=[m.top]
    assert m.call(address=payload_syms['coverflow_artist'],args=(m.deque([m.albums[i] for i in albums]),0,0,0),gap=0)==1
    m.page=m.top; m.slide=m.find('slide_menu'); cards=m.nodes[m.slide]['children']
    assert len(cards)==len(albums)+(want is not None)
    if want:
        m.word(m.slide+O['SLIDE_INDEX'],2); m.press(100); assert m.hold()==0 and m.top==m.page
        f,ctx=m.handler(cards[2],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
        assert m.plays[-1][2:]==(0,0xf001,2) and m.play_names==want,(m.plays,m.play_names)
    passed()
# Library Albums rows rate as covers do, their album split as Coverflow's.
m=AlbumRateMachine(page='album_page',cls=O['CLASS_ALBUMS']); m.page=m.top; a0=m.aid(m.row(0))
assert m.menu()==[*COVER_ALBUM,'Add to Playlist','Go to Artist']
assert m.rate(4) is None and m.kept()==[(a0&~7)|4] and not song_side_effects(m)
assert m.run(3) is None and m.kept()==[(a0&~7)|5]; passed()
# A browse change under the picker saves nothing.
assert m.run(4) is None; m.u.mem_write(syms['g_folder_path'],b'/elsewhere\0')
assert m.call(O['KEY_CENTER'])==11 and m.toasts[-1][3]=='Album changed' and m.kept()==[(a0&~7)|5]; passed()
# An artist's albums tab (Grid): each row's album found among the split albums when the tab loads.
m=ArtistMachine(page='artistinfo_page',cls=0xff01,rows=2); m.word(syms['g_local_classinfo_save'],0xff01)
m.split=[m.song('Baroness')]; m.word(m.split[0]+O['REC_ALBUM_ARTIST'],m.string('Baroness'))
m.call(address=HOOKS['load_localartist_list'][0],args=(1,0,0,0),gap=0)
assert menu(m)==('Album',[*COVER_ALBUM,'Add to Playlist']); passed()
# Library > Era: a row per decade with dated music, oldest first, its albums counted (an album's year its tracks'
# latest); undated albums, audiobooks, podcasts and M4B left out. A decade's page: Play All, Shuffle, then its
# albums by year, each "Artist • year".
class EraMachine(PlaylistEditMachine):
    def __init__(self,songs=()):
        super().__init__()
        self.db.execute('create table songtable(id integer primary key,album,songer,albumsonger,fileurl,year)')
        self.db.executemany('insert into songtable(album,songer,albumsonger,fileurl,year) values(?,?,?,?,?)',songs)
    def open_era(self):
        self.call(address=payload_syms['era_menu'],args=(0,self.event,0,0),gap=0); self.page=self.top
        return self.texts()
    def decade(self,i):
        row=self.nodes[self.find('scroll_view')]['children'][i]; f,ctx=self.handler(row,O['EVT_CLICK'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0); self.page=self.top
        return self.texts()
SONGS=[('Alpha','A1','','/mnt/mmc/m/a/1.mp3',1990),('Beta','B1','Beta Band','/mnt/mmc/m/b/1.mp3',1998),
       ('Beta','B2','Beta Band','/mnt/mmc/m/b/2.mp3',1999),('Gamma','C1','','/mnt/mmc/m/c/1.mp3',2000),
       ('Delta','D1','','/mnt/mmc/m/d/1.mp3',0),('Eta','E1','','/mnt/mmc/m/e/1.mp3',1989),
       ('Book','W1','','/mnt/mmc/audiobooks/w/1.mp3',1995),('Tale','T1','','/mnt/mmc/m/t.m4b',1993),
       ('Pod','P1','','/mnt/mmc/podcasts/p/1.mp3',1996),('','U1','','/mnt/mmc/m/u.mp3',1994)]
m=EraMachine(SONGS); assert m.open_era()==['Era','80s','1 album','90s','2 albums','2000s','1 album']
assert m.nodes[m.top]['name']=='era_page' and m.closed_db==1; passed()
into=lambda: [m.nodes[m.nodes[r]['children'][-1]].get('image')=='list_into' for r in m.nodes[m.find('scroll_view')]['children']]
assert into()==[True]*3  # a decade opens its albums
assert m.decade(1)==['90s','Play All','2 albums','Shuffle','Every song, shuffled','Alpha','A1 \u2022 1990','Beta','Beta Band \u2022 1999']
assert m.nodes[m.top]['name']=='era_albums_page' and into()==[False,False,True,True]; passed()  # an album opens Coverflow
# The full library is counted past stock's old 20,000-song limit, with a bounded SQLite cache.
m=EraMachine((f'Large {i//10}','Artist','Artist',f'/mnt/mmc/music/large/{i}.flac',2020) for i in range(65000))
assert m.open_era()==['Era','2020s','6500 albums']
assert m.db.execute('PRAGMA cache_size').fetchone()==(64,)
assert m.db.execute('PRAGMA temp_store').fetchone()==(1,); passed()
for failure in ('open','PRAGMA','select'):
    m=EraMachine(SONGS); m.fail=failure; top=m.top; m.open_era()
    assert m.top==top and m.toasts[-1][3]=='Era unavailable' and m.closed_db==1; passed()
# Full-date tags belong to their year; normalize each track before picking the album's latest.
dates=[('Dates','Artist','','/mnt/mmc/music/d1.flac',20220510),
       ('Dates','Artist','','/mnt/mmc/music/d2.flac',2023),
       ('ISO','Artist','','/mnt/mmc/music/i.flac','2021-05-10'),
       ('Month','Artist','','/mnt/mmc/music/m.flac',202004),
       ('Bad','Artist','','/mnt/mmc/music/b.flac','unknown')]
m=EraMachine(dates); assert m.open_era()==['Era','2020s','3 albums']
assert m.decade(0)==['2020s','Play All','3 albums','Shuffle','Every song, shuffled',
                     'Month','Artist \u2022 2020','ISO','Artist \u2022 2021','Dates','Artist \u2022 2023']; passed()
# Play All plays the decade's albums from the first song, each in album order; an album opens its tracks in Coverflow.
class EraPlay(EraMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='q:getMusicByAlbum': self.found=self.by_album.get(self.text(u.reg_read(REGS[0])),[])
        return super().hook(u,address,size,unused)
def era_track(m,album,aa,*t):
    r=track(m,*t); m.word(r+O['REC_ALBUM'],m.string(album)); m.word(r+O['REC_ALBUM_ARTIST'],m.string(aa)); return r
m=EraPlay(SONGS); m.by_album={'Alpha':[era_track(m,'Alpha','','a1','/mnt/mmc/m/a/1.mp3',1,1)],
                              'Beta':[era_track(m,'Beta','Beta Band','b2','/mnt/mmc/m/b/2.mp3',1,2),era_track(m,'Beta','Beta Band','b1','/mnt/mmc/m/b/1.mp3',1,1)]}
m.open_era(); m.decade(1); albums=m.page
row=lambda i: m.handler(m.nodes[m.find('scroll_view',albums)]['children'][i],O['EVT_CLICK'])
f,ctx=row(0); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.plays[-1][2:]==(0,0xf001,2) and m.play_names==['a1','b1','b2']; passed()
f,ctx=row(3); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.page=m.top
assert m.nodes[m.top]['name']=='coverflow_page' and m.texts()[:3]==['Beta','b1','b2']; passed()
# Two valid paths collide under the old FNV32 membership check: only the dated song belongs.
paths=('/mnt/mmc/Music/z4dmr4cvs8lw.flac','/mnt/mmc/Music/mhf1gqxzbiuf.flac')
m=EraMachine([('Old','Artist','Artist',paths[0],1990),('New','Artist','Artist',paths[1],2020)])
m.found=[m.song('old'),m.song('new')]
for ident,(r,path) in enumerate(zip(m.found,paths),1):
    m.word(r+O['REC_ID'],ident); m.word(r+O['REC_PATH'],m.string(path))
m.open_era(); m.decade(0)
f,ctx=m.handler(m.nodes[m.find('scroll_view')]['children'][1],O['EVT_CLICK'])
m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.play_names==['old'] and m.names(m.get(syms['tools_pdeq_directory']))==['staged']; passed()
m=EraMachine(); top=m.top; assert m.open_era() is not None and m.top==top and m.toasts[-1][3]=='No dated albums yet'; passed()
m=EraMachine([s for s in SONGS if s[4]==0]); top=m.top; m.open_era(); assert m.top==top and m.toasts[-1][3]=='No dated albums yet'; passed()
# Library > Ratings > Albums: the rated albums as Coverflow, most hearts first, with the rated Sort card alone
# (Rating, Album, Artist, Year, kept); closing it brings the library's covers back, with no card.
def ratings_albums(m):
    m.call(address=payload_syms['ratings_menu'],args=(0,m.event,0,0),gap=0)  # the Library's Ratings row
    menu_page=m.top; assert m.nodes[menu_page]['name']=='ratings_page'; row=m.nodes[m.find('scroll_view',menu_page)]['children'][2]; f,ctx=m.handler(row,O['EVT_CLICK'])
    m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000); m.advance(0)
    return menu_page
m=AlbumRateMachine(); ids=[m.aid(r) for r in m.albums]
assert ratings_albums(m)==m.top and m.toasts[-1][3]=='No rated albums yet'; passed()
data=b''.join(struct.pack('<I',(ids[i]&~7)|h) for i,h in ((0,2),(2,5))).ljust(4*O['RATE_SLOTS'],b'\0')
m=AlbumRateMachine(files={ALBUM_RATES:data}); menu_page=ratings_albums(m)
m.page=m.top; m.slide=m.find('slide_menu'); assert m.page!=menu_page and m.nodes[m.page]['name']=='coverflow_page'
def shown(m,i): m.word(m.slide+O['SLIDE_INDEX'],i); f,ctx=m.handler(m.slide,O['EVT_VALUE_CHANGED']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); return m.texts()[0]
assert len(m.nodes[m.slide]['children'])==3 and [shown(m,i) for i in range(3)]==['Album 2','Album 0','Sort: Rating']
shown(m,0); assert m.menu()[3:5]==['Remove from Favourites','Rate Album  ♥♥♥♥♥']  # the cover's menu, as Coverflow's
f,ctx=m.handler(m.nodes[m.slide]['children'][2],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
m.slide=m.find('slide_menu'); assert [shown(m,i) for i in range(3)]==['Album 0','Album 2','Sort: Album']
assert [v for k,v in m.files.items() if k.endswith('ringnav-ratedsort')]==[struct.pack('<i',0)]; passed()
# Then Artist, Year and Most Played (saved as the library's SORT_PLAYED, 3), round again to Rating.
for want,saved in (('Sort: Artist',1),('Sort: Year',6),('Sort: Most Played',3),('Sort: Rating',5)):
    f,ctx=m.handler(m.nodes[m.slide]['children'][2],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000); m.advance(0)
    m.slide=m.find('slide_menu'); assert shown(m,2)==want
    assert [v for k,v in m.files.items() if k.endswith('ringnav-ratedsort')]==[struct.pack('<i',saved)]
passed()
m.close(); m.stack.remove(m.page); m.top=m.stack[-1]; m.open(); assert len(m.nodes[m.slide]['children'])==3
assert shown(m,2)=='Album 2'; passed()
# Display settings' Coverflow Sort (navigation.c's pod rows): each press saves the next of Album, Artist,
# Year, Recently Added, Most Played and Random, round again, which Coverflow takes at its next open; the
# next album hold follows the reordered deque.
m=AlbumRateMachine(); m.word(m.albums[2]+O['REC_ARTIST'],m.string('Aardvark'))
sort_name=lambda: m.text(m.call(address=payload_syms['coverflow_sort_name'],args=(0,0,0,0),gap=0)&0xffffffff)
assert sort_name()=='Sort: Album'
for want in ('Sort: Artist','Sort: Year','Sort: Recently Added','Sort: Most Played','Sort: Random','Sort: Album','Sort: Artist'):
    m.call(address=payload_syms['coverflow_sort_step'],args=(0,0,0,0),gap=0); assert sort_name()==want
assert [v for k,v in m.files.items() if k.endswith('ringnav-coversort')]==[struct.pack('<i',1)]  # SORT_ARTIST
m.open(); m.slide=m.find('slide_menu'); m.word(m.slide+O['SLIDE_INDEX'],0); m.press(101); assert m.hold()==11
assert m.nodes[m.title]['text']=='Album 2'; m.release(); m.pick(1); m.advance(0)
assert m.query[:2]==('getMusicByAlbum','Album 2'); passed()

# Coverflow's own song deque feeds the shared queue menu, independently of stock browsing state.
for action,want in ((0,['A','T2','B','C']),(1,['A','B','C','T2'])):
    m=CoverflowMachine(cls=O['CLASS_ALBUMS']); m.open(); view=m.tracks()
    m.call(); assert m.selected(view)==1
    m.press(100); assert m.hold()==11 and m.nodes[m.title]['text']=='T2' and m.release()==0
    assert m.labels()==['Play Next','Add to Queue','Add to Favourites','Go to Artist']
    assert m.hold()==0  # a repeated hold while the dialog is open cannot open another
    m.pick(action); m.pick(action); m.advance(0)
    assert m.names()==want and m.top==m.page and m.selected(view)==1 and not m.playback() and not m.plays
    assert m.names(m.get(syms['p_deque_showlist']))==['Row 0','Row 1','Row 2','Row 3']
    m.press(200); assert m.release()==1; passed()
# Its favourite is batch-select's over Coverflow's own deque, its songs taken as All Songs rows.
m=CoverflowMachine(cls=O['CLASS_ALBUMS']); m.open(); view=m.tracks(); assert m.run(2) is None
assert [c[1:3] for c in m.calls if c[0]=='batch_add_file']==[(0xf001,0xf00a)] and [c[1] for c in m.calls if c[0]=='batch_init_selectrecord']==[2]
assert [c[3] for c in m.calls if c[0]=='batch_add_file'][0]!=m.get(syms['p_deque_showlist']); passed()
m=CoverflowMachine(queue=0,cls=O['CLASS_ALBUMS']); m.open(); m.tracks()
assert m.run(1) is None and m.names()==['T1'] and m.mcl('MCL_TYPE')==0xff10 and not m.playback(); passed()
m=CoverflowMachine(mode=2); m.open(); m.tracks(); m.run(0)
m.call(address=syms['mclNextSong'],args=(0,0,0,0),gap=0)
assert m.names()==['A','T1','B','C'] and m.mcl('MCL_POS')==1; passed()
# Return cancels without losing the highlighted song. Changed or closed sources refuse.
m=CoverflowMachine(); m.open(); view=m.tracks(); m.call(); m.press(100); m.hold(); m.release()
f,ctx=m.handler(m.top,O['EVT_KEY_UP']); ev=m.alloc(0x40); m.word(ev,O['EVT_KEY_UP']); m.word(ev+O['EVENT_KEY'],O['KEY_RETURN'])
assert m.call(address=f,args=(ctx,ev,0,0),gap=0)==11 and m.top==m.page
assert m.names()==['A','B','C'] and m.selected(view)==1 and not m.playback(); passed()
for invalidate in ('changed','closed','covers'):
    m=CoverflowMachine(); m.open(); m.tracks(); m.press(100); m.hold(); m.release()
    if invalidate=='changed':
        tracks=next(d for d in m.deqs if m.names(d)==['T1','T2'] and m.deqs[d][0]=='stSongInfo')
        m.word(m.items(tracks)[0]+O['REC_NAME'],m.string('changed'))
    elif invalidate=='closed': m.close()
    else: m.key()  # simulate the underlying page returning to covers before the action
    m.pick(0); m.advance(0)
    assert m.toasts[-1][3]=='Queue unchanged' and m.names()==['A','B','C'] and not m.playback(); passed()
# Album identity survives closing/reopening and library reordering; song positions are per album.
m=CoverflowMachine(); m.open()
m.call(address=syms['slide_menu_set_value'],args=(m.slide,2,0,0),gap=0)
f,ctx=m.handler(m.slide,O['EVT_VALUE_CHANGED']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
m.close(); m.albums.reverse(); m.rescan(); m.open()
assert m.get(m.slide+O['SLIDE_INDEX'])==0 and 'Album 2' in m.texts(); passed()
view=m.tracks(0); m.call(); assert m.selected(view)==1
m.key(); other=m.tracks(1); assert m.selected(other)==0
m.key(); view=m.tracks(0); assert m.selected(view)==1
m.close(); m.open(); view=m.tracks(0); assert m.selected(view)==1; passed()
# Long metadata and track titles use the looping stock scrolling label, with the complete text.
m=CoverflowMachine(); long='A very long title or artist name ' * 8
m.word(m.albums[0]+O['REC_ALBUM'],m.string(long)); m.word(m.albums[0]+O['REC_ARTIST'],m.string(long))
m.word(m.found[0]+O['REC_NAME'],m.string(long)); m.open(); assert m.texts().count(long)==2
m.tracks(); assert m.texts().count(long)==4  # covers stay alive, hidden behind the tracks
labels=[n for w,n in m.nodes.items() if m.alive(w) and n.get('text')==long]
assert all(n['type']=='hscroll_label' and n['loop']==1 and n['set_hscroll_label_attribute'] for n in labels); passed()
# Wheel only: the covers' slide_menu takes no touch.
m=CoverflowMachine(); m.open()
assert m.nodes[m.slide].get('sensitive')==0; passed()
# Text geometry. Both builds: album over artist under the covers' frame (CF_TEXT_Y), the album larger
# and white, the artist grey, CF_EDGE from the sides and clear of the rounded glass. iPod keeps every
# other label (the track list's title and rows, whose last visible row is lowest) CF_EDGE in too;
# Stock keeps its track list layout.
from ipod import corner_inset
def cf_geometry(m,w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
def clear(x,top,w,px):  # a label's text band, in screen rows (the window starts at y 30)
    return max(corner_inset(30+top),corner_inset(30+top+px))<=x and x+w<=375-max(corner_inset(30+top),corner_inset(30+top+px))
m=CoverflowMachine(); page=m.open(); labels=[w for w in m.nodes[m.get(m.slide+O['W_PARENT'])]['children'] if m.nodes[w]['type']=='hscroll_label']
name,artist=labels
E=O['CF_EDGE']; y=O['CF_TEXT_Y']
assert y>=O['CF_VIEW_H'] and y+O['CF_NAME_H']+O['CF_ARTIST_H']<=290
assert [cf_geometry(m,w) for w in labels]==[(E,y,375-2*E,O['CF_NAME_H']),(E,y+O['CF_NAME_H'],375-2*E,O['CF_ARTIST_H'])]
assert m.nodes[name]['style:normal:font_size']==O['CF_NAME_PX']==24 and m.nodes[artist]['style:normal:font_size']==O['CF_ARTIST_PX']==20
assert m.nodes[artist]['style:normal:text_color']==signed(O['CF_GREY']) and 'style:normal:text_color' not in m.nodes[name]
for w,px in ((name,O['CF_NAME_PX']),(artist,O['CF_ARTIST_PX'])):
    x,top,wd,h=cf_geometry(m,w); assert clear(x,top+(h-px)//2,wd,px)
if variant=='ipod':
    view=m.tracks(); lv=m.get(view+O['W_PARENT']); title=next(w for w in m.nodes[m.get(lv+O['W_PARENT'])]['children'] if m.nodes[w]['type']=='hscroll_label')
    assert cf_geometry(m,title)[::2]==(E,375-2*E) and clear(E,14,375-2*E,20)
    rows=(290-48)//48
    for item in m.nodes[view]['children']:
        label=m.nodes[item]['children'][0]; assert cf_geometry(m,label)[::2]==(E,375-2*E)
    assert clear(E,48+(rows-1)*48+14,375-2*E,20)  # the lowest visible row
else:
    view=m.tracks(); assert {cf_geometry(m,m.nodes[i]['children'][0])[::2] for i in m.nodes[view]['children']}=={(12,350)}
passed()

# Execute stock sibling selection and playback scheduling; mock filesystem enumeration,
# tag decoder and image codec boundaries, never the resulting player globals.
class RolloverMachine(CoverflowMachine):
    def __init__(self,sizes=(2,2,1,1)):
        super().__init__(queue=0,cls=1)
        self.handlers.pop(syms['player_mainscheduling'])  # runs stock's, as tick() calls it
        self.native_trace={syms[n]:n for n in ('mclAutoChange','mclNextSong','on_player_autochange','toolsLoadNextDir',
                          'player_refresh_playqueue','mclLoadPlayList','player_get_id3info','player_set_coverinfo','mclClearChangeFlag')}
        self.trace=[]; self.files={}; self.dirs={}; self.artfiles=set(); self.worker=False; self.forks=0
        self.handlers[0x5c29bc]='r:directory'; self.handlers[0x5c2c8c]='r:directory'
        self.handlers[0x5ace14]='r:decoder_start'
        # Lazy-bound malloc, as used by stock player_refresh_playqueue.
        self.word(0xa26cc0-0xabc,0x1000014); self.handlers[0x1000014]='r:malloc'
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
        self.word(0xa3bda8,syms['on_player_autochange']) # registered by stock player startup
        self.byte(0xa3be53,1) # mclSetJumpFolder's flag
        self.call(address=syms['mclLoadPlayList'],args=(self.deque(self.dirs['/mnt/mmc/0']),0,1,0),gap=0)
        self.call(address=syms['mclStartPlayer'],args=(0,0,0,0),gap=0)
        self.call(address=syms['player_get_id3info'],args=(0,0,0,0),gap=0)
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
        elif name=='decoder_start': self.byte(0xa3be55,1)
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
        self.call(address=0x52bffc,args=(self.info,0,0,0),gap=0) # stock playing_timer_start callback
        if variant=='ipod':
            self.paint(self.win,gap=0)
            self.top=self.home; self.paint(self.home,gap=0); self.top=self.win
    def setup_ui(self):
        bar=self.node('window','system_bar'); self.word(syms['system_bar'],bar); self.word(bar+O['W_PARENT'],self.wm)
        if variant=='ipod':
            home_list(self); self.home=self.top; self.art=named(self,self.home,'img_homeart')
            self.call(address=home_hook[0],args=(self.home,0,0,0),gap=0)
        self.cover=self.node('image','img_cover'); self.songlabel=self.node('hscroll_label','scrlabel_title')
        self.albumlabel=self.node('label','label_ipod_album'); self.poslabel=self.node('label','label_ipod_pos')
        self.win=self.node('window','playing_page',[self.cover,self.songlabel,self.albumlabel,self.poslabel])
        self.word(self.win+O['W_PARENT'],self.wm); self.top=self.win
        self.info=self.alloc(0x40); self.word(self.info+0x20,self.win)
        if variant=='ipod':
            playing=HOOKS['playing_page_init'][0]; self.handlers[playing+12]='stock_playing'
            self.call(address=playing,args=(self.win,0,0,0),gap=0)
        self.byte(syms['g_forcerefresh_flag'],1)

from build import PLAYER_COVER
assert struct.unpack_from('<I',demo,fileoff(demo,PLAYER_COVER[0]))[0]==jal(payload_syms['coverflow_player_cover'])
for sizes in ((2,2,1,1),(1,1,1,1)):
    m=RolloverMachine(sizes); m.setup_ui(); m.artwork(); m.ui()
    paths=[path for path in m.files]
    assert m.text(syms['g_play_id3_info'])==paths[0]
    for index,path in enumerate(paths[1:],1):
        old=m.text(syms['g_play_id3_info']); m.byte(0xa3be55,0) # hciplayer EOF
        # Delay the UI scheduler after stock queue replacement on alternate transitions.
        if index%2:
            m.call(address=syms['mclAutoChange'],args=(0,0,0,0),gap=0)
            assert m.text(syms['g_play_id3_info'])==old
            m.ui()
            if variant=='ipod':
                assert m.nodes[m.albumlabel]['text']=='' and m.nodes[m.art]['image']=='default_album_home'
        m.tick() # native auto-change, sibling traversal, queue reload, parsing and notification
        assert m.text(syms['g_play_id3_info'])==path
        assert m.text(syms['g_play_id3_info']+O['ID3_ALBUM'])==m.files[path][0]
        assert m.text(syms['g_play_cover_info']+8)==path
        assert m.text(syms['g_lastcover_url'])==old # worker has not completed yet
        m.ui()
        if variant=='ipod':
            assert m.nodes[m.albumlabel]['text']==m.files[path][0]
            assert m.nodes[m.art]['image']=='default_album_home'
        forks=m.forks; m.artwork()
        assert m.text(syms['g_lastcover_url'])==path
        assert m.u.mem_read(syms['g_playcover_finishflag'],1)==b'\1'
        m.ui()
        # No art: the folder-image call went through coverflow_player_cover to ffmpeg (refused here).
        assert (m.forks>forks)!=m.files[path][1]
        want='file:///tmp/coverpic.jpg' if m.files[path][1] else 'default_album_home'
        assert m.nodes[m.cover]['image']==(want if m.files[path][1] else 'play_defaultcover'),(path,m.nodes[m.cover])
        assert m.nodes[m.songlabel]['text']==path.rsplit('/',1)[1]
        if variant=='ipod':
            assert m.nodes[m.art]['image']==want
            assert m.nodes[m.poslabel]['text']==f"{m.mcl('MCL_POS')+1} of {len(m.names())}"
        queue=m.items(m.get(syms['mcl_pdeqplaylist']))
        assert all(not m.get(r+O['REC_ALBUM']) for r in queue) # tags came from parsing, not library rows
        assert ('tags',path) in m.trace and ('decoder_start',path) in m.trace
        passed()
    assert [path for event,path in m.trace if event=='directory']==['/mnt/mmc','/mnt/mmc/1','/mnt/mmc','/mnt/mmc/2','/mnt/mmc','/mnt/mmc/3']
    events=[event for event,_ in m.trace]
    for event in ('on_player_autochange','toolsLoadNextDir','player_refresh_playqueue'): assert events.count(event)==3
    assert events.count('mclLoadPlayList')==4 and events.count('mclClearChangeFlag')==len(paths)-1
# A CUE track (issue #59): its record's PERFORMER is kept, a missing sheet TITLE takes the file's album.
from build import ID3_PATH
assert struct.unpack_from('<I',demo,fileoff(demo,ID3_PATH[0]))[0]==jal(payload_syms['ringnav_id3_path'])
m=RolloverMachine((1,1)); m.handlers.pop(syms['mclGetCueFlag'])  # stock's, on the queue
r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; id3=syms['g_play_id3_info']
m.word(r+O['REC_CUE_START'],10); m.word(r+O['REC_TITLE'],m.string('Chapter')); m.word(r+O['REC_ARTIST'],m.string('Author'))
m.call(address=syms['player_get_id3info'],args=(0,0,0,0),gap=0)
assert [m.text(id3+O[k]) for k in ('ID3_TITLE','ID3_ARTIST','ID3_ALBUM')]==['Chapter','Author','Album 0']
assert m.text(id3)=='/mnt/mmc/0/0.flac'; passed()
# ...and a sheet TITLE without PERFORMER keeps the album and takes the file's artist, not Unknown Artist.
m=RolloverMachine((1,1)); m.handlers.pop(syms['mclGetCueFlag'])
r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
m.word(r+O['REC_CUE_START'],10); m.word(r+O['REC_TITLE'],m.string('Chapter')); m.word(r+O['REC_ALBUM'],m.string('Book'))
m.call(address=syms['player_get_id3info'],args=(0,0,0,0),gap=0)
assert [m.text(id3+O[k]) for k in ('ID3_TITLE','ID3_ARTIST','ID3_ALBUM')]==['Chapter','Artist','Book']; passed()

# Tag text (TAG_TEXT): stock toolsFixId3ErrorCode, run as patched, took a field TagLib found all Latin-1
# for GBK when its Latin-1 bytes went to UTF-8 and back unchanged ("Lo que la música me enseñó" does) and
# showed Chinese; now only when they also read as GBK (ringnav_gbk_tag): a GBK-tagged MP3's Latin-1 frame
# still shows Chinese, Western text its UTF-8 copy. Either is stored through its tidy-up (0x5b5a30) into
# the 256-byte field.
from build import TAG_TEXT, CUE_TEXT, SCAN_DIRS
assert struct.unpack_from('<I',demo,fileoff(demo,TAG_TEXT[0]))[0]==jal(payload_syms['ringnav_gbk_tag'])
class TagTextMachine(Machine):
    def __init__(self):
        super().__init__()
        self.word(0xa26cc0-0xabc,0x1000014); self.handlers[0x1000014]='t:malloc'  # lazy-bound, as RolloverMachine's
        self.word(0xa2638c,0x1000010); self.handlers[0x1000010]='t:free'
        self.handlers[syms['strcpy@GLIBC_2.0']]='t:strcpy'; self.handlers[syms['toolsIconvConvert']]='t:iconv'
        self.handlers[syms['strcmp@GLIBC_2.0']]='t:strcmp'  # on bytes: GBK isn't UTF-8
        self.mock('snprintf@GLIBC_2.0'); self.iconv=0
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
for utf8,raw in (('Lo que la música me enseñó',None),('Ciencia Ficción',None),('Mateo Nieto',None),('今','今'.encode()),
                 ('Ölüdeniz',None),('Canção do Mar',None),('Informações',None),('Grüße',None),('周杰伦','周杰伦'.encode('gbk')),('Jay 周杰伦 - 晴天','Jay 周杰伦 - 晴天'.encode('gbk'))):
    m=TagTextMachine(); out=m.alloc(0x100); latin1=raw is None or raw!=utf8.encode()
    raw=utf8.encode('latin-1') if raw is None else raw  # taglib_tag_<field>(tag, 0): its Latin-1 bytes
    copy=raw.decode('latin-1') if latin1 else utf8  # (tag, 1): TagLib's UTF-8, of the bytes as Latin-1
    rawp=m.alloc(len(raw)+4); m.u.mem_write(rawp,raw+b'\0')
    m.call(address=0x5c5cf8,args=(int(latin1),rawp,m.string(copy),out),stack=(1,),gap=0)
    assert m.raw(out).decode()==utf8,(utf8,m.raw(out)); passed()
# CUE sheets (CUE_TEXT): its seven field conversions go to ringnav_cue_text: UTF-8 stays, GBK goes through
# stock's conversion, any other text is Windows-1252 re-encoded as UTF-8, cut at a whole character to 511 bytes.
for address in CUE_TEXT: assert struct.unpack_from('<I',demo,fileoff(demo,address))[0]==jal(payload_syms['ringnav_cue_text'])
for given,want in ((b'Caf\xc3\xa9 \xc3\x91and\xc3\xba','Café Ñandú'),(b'Caf\xe9 \x93Hi\x94 \x80','Café “Hi” €'),
                   (b'\xc3(','Ã('),(b'a\xe9'*200,'aé'*170+'a'),(b'plain','plain'),(b'\xed\xa0\x80','í\xa0€'),
                   ('周杰伦 晴天'.encode('gbk'),'周杰伦 晴天'),('Lo que la música'.encode('cp1252'),'Lo que la música'),
                   ('Canção'.encode('cp1252'),'Canção'),('Beyoncé’s'.encode('cp1252'),'Beyoncé’s'),('周'.encode('gbk')*171,('ÖÜ'*171)[:255])):
    m=TagTextMachine(); buf=m.alloc(512); m.u.mem_write(buf,given+b'\0')
    assert m.call(address=payload_syms['ringnav_cue_text'],args=(buf,m.string('utf-8'),m.string('gbk'),0),gap=0)==1 and m.raw(buf).decode()==want,(given,m.raw(buf)); passed()
# The library scan (SCAN_DIRS) skips every hidden folder: q2-deezer's .deezer/stream never joins the Library.
assert struct.unpack_from('<I',demo,fileoff(demo,SCAN_DIRS[0]))[0]==jal(payload_syms['ringnav_scan_dir'])
m=Machine(); dot=m.string('.')
assert [m.call(address=payload_syms['ringnav_scan_dir'],args=(m.string(n),dot,0,0),gap=0)!=0 for n in ('.','..','.deezer','.Trashes','Music','Deezer')]==[False,False,False,False,True,True]; passed()

# Now Playing's lyric follow (LYRIC_FOLLOW): stock's scroll_to past the end clamps to a row top, the
# content padded to it, not to 275 - 178 = 97 through a row; repeated it stays; a short page is left.
from build import LYRIC_FOLLOW
assert struct.unpack_from('<I',demo,fileoff(demo,LYRIC_FOLLOW[0]))[0]==jal(payload_syms['ringnav_lyric_follow'])
m=Machine(); rows=[m.node('label') for _ in range(12)]
for i,(y,h) in enumerate(zip((0,20,70,90,110,135,155,175,195,215,235,255),(20,50,20,20,25)+(20,)*7)):
    m.word(rows[i]+O['W_Y'],y); m.word(rows[i]+O['W_H'],h)
lrc=m.node('scroll_view','scroll_lrc',rows); m.word(lrc+O['W_H'],178); m.word(lrc+O['VIEW_CONTENT_H'],275)
ys=[]
for y in (195,0,20,195): m.call(address=payload_syms['ringnav_lyric_follow'],args=(lrc,0,y,10),gap=0); ys.append(m.get(lrc+O['SCROLL_Y']))
assert ys==[110,0,20,110] and m.get(lrc+O['VIEW_CONTENT_H'])==288,ys
m.word(lrc+O['VIEW_CONTENT_H'],100); m.call(address=payload_syms['ringnav_lyric_follow'],args=(lrc,0,0,10),gap=0)
assert m.get(lrc+O['VIEW_CONTENT_H'])==100 and m.get(lrc+O['SCROLL_Y'])==0; passed()

# A refresh's lyric rebuild (LYRIC_ROWS), which stock ends at offset 0, keeps the view where it was.
from build import LYRIC_ROWS
assert struct.unpack_from('<I',demo,fileoff(demo,LYRIC_ROWS[0]))[0]==jal(payload_syms['ringnav_lyric_rows'])
class LyricRowsMachine(Machine):
    def __init__(self): super().__init__(); self.handlers[0x529f0c]='rows'
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='rows': return super().hook(u,address,size,unused)
        self.word(self.lrc+O['SCROLL_Y'],0); self.returns(0,clobber=True)
for y in (110,0):
    m=LyricRowsMachine(); m.lrc=m.node('scroll_view','scroll_lrc'); win=m.node('window','playing_page',[m.lrc])
    m.word(m.lrc+O['SCROLL_Y'],y); m.call(address=payload_syms['ringnav_lyric_rows'],args=(win,),gap=0)
    assert m.get(m.lrc+O['SCROLL_Y'])==y; passed()

# Albums grids and Folder view (GRID_COVER): stock's embedded art first; ffmpeg only for an Ogg track
# stock found none in, never for an MP3 or FLAC, run with parse_cover_mutex dropped. It writes dst.ff,
# renamed onto dst only when non-empty (an empty one, left on exit 0, is removed and dst kept); waitpid
# is retried on EINTR; a miss is remembered, so the track's next row forks nothing. The Albums grid's
# call reads the folder image (toolsGetExternCover) before ffmpeg, which then never runs for it.
from build import GRID_COVER
for call,_,fn in GRID_COVER:
    assert struct.unpack_from('<I',demo,fileoff(demo,call))[0]==jal(payload_syms[fn])
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
m=GridCoverMachine()
for track,found in (('01.opus',1),('01.mp3',0),('01.flac',0),('opus',0)):
    assert m.cover(track,found=found)==found and m.seen==['toolsGetAlbumCover'],(track,m.seen)
m.files={m.dst:b'old'}
assert m.cover('01.OGG')==0 and m.seen==['toolsGetAlbumCover','calloc','pthread_mutex_unlock','fork','unlink',
                                         'pthread_mutex_lock'],m.seen  # fork refused: not a miss
assert m.cover('01.OGG')==0 and 'fork' in m.seen,m.seen  # so the next request tries again
assert m.cover('02.opus',out=b'')==0 and m.files=={m.dst:b'old'} and m.seen.count('waitpid')==2,(m.files,m.seen)
assert m.cover('02.opus',out=b'')==0 and m.seen==['toolsGetAlbumCover'],m.seen  # the miss is remembered
assert m.cover('03.opus',out=b'jpeg')==1 and m.files=={m.dst:b'jpeg'} and 'toolsGetExternCover' not in m.seen
assert m.seen.index('pthread_mutex_unlock')<m.seen.index('fork')<m.seen.index('pthread_mutex_lock')
assert m.cover('04.opus','coverflow_album_cover',extern=1)==1 and m.seen==['toolsGetAlbumCover','toolsGetExternCover']
assert m.cover('05.opus','coverflow_album_cover',extern=0)==1 and 'fork' in m.seen
passed()

# Coverflow depth (docs/internals.md#coverflow-depth): the payload's renderer, run as MIPS, draws
# byte for byte what the host build of the same source draws (test/coverflow.py checks that
# one's geometry); on the page it spans the frame, draws through the stock canvas and hit-tests taps.
BIG=0x2000000
def cover_pixels(seed):
    return b''.join(struct.pack('<I',0xff000000|((i*2654435761+seed*40503)>>7)&0xffffff) for i in range(160*160))
class DepthMachine(CoverflowMachine):
    budget=50_000_000  # decoding and rendering run in the payload, at native emulator speed (fast)
    def __init__(self,**kw):
        super().__init__(**kw)
        self.u.mem_map(BIG,0x800000); self.big=BIG
        self.frames=[]; self.destroyed=[]; self.draws=[]; self.loads=[]; self.unloads=0; self.pixels={}
        self.art_size=(160,160)  # the covers' decoded size; the placeholder is always 160 square
        self.fast()
    def big_alloc(self,n):
        a=self.big; self.big+=(n+15)&~15; assert self.big<=BIG+0x800000; return a
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        if name=='c:calloc@GLIBC_2.0' and a*b>0x10000: ret=self.big_alloc(a*b)
        elif name=='bitmap_create_ex':
            ret=self.big_alloc(0x48); data=self.big_alloc(b*c)
            self.word(ret,a); self.word(ret+4,b); self.word(ret+8,c); self.u.mem_write(ret+0xc,struct.pack('<HH',0,d)); self.word(ret+0x14,data)
            self.frames.append(ret)
        elif name=='bitmap_destroy': self.destroyed.append(a); ret=0
        elif name=='bitmap_lock_buffer_for_read': ret=self.get(a+0x14)
        elif name=='widget_load_image':
            url=self.text(b); self.loads.append(url) if a==self.top else None  # Coverflow's, not iPod Home's
            w,h=(160,160) if url==CF_PLACEHOLDER else self.art_size
            if url not in self.pixels:
                self.pixels[url]=self.big_alloc(w*h*4); self.u.mem_write(self.pixels[url],cover_pixels(len(self.pixels))[:w*h*4])
            self.word(c,w); self.word(c+4,h); self.word(c+8,w*4); self.u.mem_write(c+0xc,struct.pack('<HH',2,1)); self.word(c+0x14,self.pixels[url]); ret=0
        elif name=='widget_unload_image': self.unloads+=a==self.top; ret=0
        elif name=='slide_menu_set_spacer': self.word(a+O['SLIDE_SPACER'],b); ret=0
        elif name=='canvas_draw_image':
            src,dst=[tuple(signed(self.get(r+4*i)) for i in range(4)) for r in (c,d)]
            line=self.get(b+8); data=self.get(b+0x14)
            frame=b''.join(bytes(self.u.mem_read(data+y*line,4*O['CF_VIEW_W'])) for y in range(O['CF_VIEW_H']))
            self.draws.append((a,b,src,dst,struct.unpack_from('<H',bytes(self.u.mem_read(b+0xc,2)))[0],frame)); ret=0
        else: return super().hook(u,address,size,unused)
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)

def host_render(textures,cases):
    """The same renderer source built for the host: frames for (frac, mask of drawn slots) cases."""
    import subprocess, tempfile
    from coverflow import SHIM_H
    main=r"""#include <stdio.h>
void coverflow_render(unsigned *, int, int, const unsigned *const[7]);
int ipod_light(void) { return 0; }
static unsigned tex[7][160 * 160], frame[%d * %d];
int main(void) {
    const unsigned *ring[7];
    if (fread(tex, 4, 7 * 160 * 160, stdin) != 7 * 160 * 160) return 1;
    int frac, mask;
    while (scanf("%%d %%d", &frac, &mask) == 2) {
        for (int j = 0; j < 7; ++j) ring[j] = mask >> j & 1 ? tex[j] : 0;
        coverflow_render(frame, %d, frac, ring);
        fwrite(frame, 4, sizeof(frame) / 4, stdout);
        fflush(stdout);
    }
    return 0;
}""" % (O['CF_VIEW_H'],O['CF_VIEW_W'],O['CF_VIEW_W'])
    with tempfile.TemporaryDirectory(prefix='q2-render-') as d:
        d=pathlib.Path(d); (d/'shim.h').write_text(SHIM_H); (d/'main.c').write_text(main)
        subprocess.run(['cc','-O1','-DPEQ_HOST','-DPEQ_ROOT=""',f'-DIPOD={int(variant=="ipod")}','-D_GNU_SOURCE',
                        '-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-I',str(ROOT/'patch'),'-include',str(d/'shim.h'),
                        str(ROOT/'patch/coverflow.c'),str(d/'main.c'),'-o',str(d/'render')],check=True)
        feed=b''.join(textures)+''.join(f'{f} {m}\n' for f,m in cases).encode()
        out=subprocess.run([str(d/'render')],input=feed,capture_output=True,check=True).stdout
    size=4*O['CF_VIEW_W']*O['CF_VIEW_H']
    return [out[i*size:(i+1)*size] for i in range(len(cases))]

ps=payload_syms
textures=[cover_pixels(100+j) for j in range(7)]
cases=[(0,127),(16384,127),(-16384,127),(32767,127),(-32768,127),(5000,0b0111110),(0,0)]
want=host_render(textures,cases)
m=DepthMachine(); tex=m.big_alloc(7*160*160*4); m.u.mem_write(tex,b''.join(textures))
ring=m.big_alloc(28); frame=m.big_alloc(4*O['CF_VIEW_W']*O['CF_VIEW_H'])
for (frac,mask),host in zip(cases,want):
    for k in range(7): m.word(ring+4*k,tex+k*160*160*4 if mask>>k&1 else 0)
    m.call(address=ps['coverflow_render'],args=(frame,O['CF_VIEW_W'],frac,ring),gap=0,count=50_000_000)
    assert bytes(m.u.mem_read(frame,len(host)))==host,(frac,mask)
    passed()

# On the page: the slide_menu spans the frame at CF_STRIDE per album, its children stay empty, and a
# paint of it draws the frame 1:1 at its origin, marked opaque; the covers around the position are
# decoded once, each load dropped at once. What is drawn is what the renderer draws for that ring.
m=DepthMachine(); page=m.open(); s=m.slide
assert len(m.frames)==1 and cf_geometry(m,s)==(0,0,O['CF_VIEW_W'],O['CF_VIEW_H'])
stride=signed(m.get(s+O['SLIDE_SPACER']))+O['CF_VIEW_H']; assert stride==O['CF_STRIDE']
assert all('image' not in m.nodes[c] for c in m.nodes[s]['children'])
def paint(m):
    return m.call(address=HOOKS['widget_on_paint_border'][0],args=(m.slide,m.canvas,0,0),gap=0,clear=False,count=50_000_000)
instructions=[0]
def count_block(u,address,size,unused): instructions[0]+=size//4
counter=m.u.hook_add(UC_HOOK_BLOCK,count_block,begin=BASE,end=SCRATCH-1)
paint(m); rest_cost=instructions[0]
assert len(m.draws)==1 and m.draws[0][1]==m.frames[0] and m.draws[0][2]==m.draws[0][3]==(0,0,O['CF_VIEW_W'],O['CF_VIEW_H'])
assert m.draws[0][4]&1  # BITMAP_FLAG_OPAQUE
# The placeholder first (fx_open), then the ring from -3 round album 0 of three, which repeats as the
# slide_menu wraps: 0, 1, 2, 0, 1, 2, 0, each album decoded once.
assert len(m.loads)==4 and m.loads[0]==CF_PLACEHOLDER and all(u.startswith('file://') for u in m.loads[1:]) and m.unloads==3
tex_of={u:bytes(m.u.mem_read(m.pixels[u],160*160*4)) for u in m.pixels}
ring_tex=[tex_of[m.loads[1+i%3]] for i in range(7)]
assert host_render(ring_tex,[(0,127)])[0]==m.draws[0][5]; passed()
# A non-square cover is fitted whole and centred between black bars, not cropped (#74).
def fitted(px,w,h):
    side=max(w,h); out=[]
    for y in range(160):
        sy=y*side//160-(side-h)//2
        for x in range(160):
            sx=x*side//160-(side-w)//2
            out.append(px[(sy*w+sx)*4:(sy*w+sx)*4+4] if 0<=sx<w and 0<=sy<h else struct.pack('<I',0xff000000))
    return b''.join(out)
for size in ((200,100),(60,150)):
    n=DepthMachine(); n.art_size=size; n.open(); paint(n)
    tex={u:fitted(bytes(n.u.mem_read(n.pixels[u],size[0]*size[1]*4)),*size) for u in n.loads[1:]}
    tex[CF_PLACEHOLDER]=bytes(n.u.mem_read(n.pixels[CF_PLACEHOLDER],160*160*4))
    ring_tex=[tex[n.loads[1+i%3]] for i in range(7)]
    assert host_render(ring_tex,[(0,127)])[0]==n.draws[0][5],size; passed()
# A repaint in place draws again without rendering; a quarter turn renders from the live offset.
instructions[0]=0; paint(m); assert len(m.draws)==2 and instructions[0]<rest_cost//20
m.word(s+O['SLIDE_OFFSET'],-O['CF_STRIDE']//4); instructions[0]=0; paint(m); turn_cost=instructions[0]
assert m.draws[-1][5]!=m.draws[0][5] and len(m.loads)==4 and turn_cost>rest_cost//2; passed()
m.u.hook_del(counter)
m.word(s+O['SLIDE_OFFSET'],0); paint(m)
# The wheel's centre press clicks the centre child, which opens its tracks.
m.word(s+O['SLIDE_INDEX'],2); m.tracks(2)
assert m.query[:2]==('getMusicByAlbum','Album 2') and not m.nodes[m.get(s+O['W_PARENT'])]['visible']; passed()
# Closing releases the frame and the textures; allocation failure keeps the flat covers.
m.close(); assert m.destroyed==m.frames; passed()
class Starved(DepthMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'')=='bitmap_create_ex':
            self.returns(0); return
        return super().hook(u,address,size,unused)
m=Starved(); m.open(); s=m.slide
assert cf_geometry(m,s)==(0,24,375,160) and all(m.nodes[c].get('image') for c in m.nodes[s]['children'])
paint(m); assert not m.draws; passed()
print(f'Coverflow depth on MIPS: {rest_cost} instructions for the first paint (decoding the covers in reach, then the frame), {turn_cost} for a frame a quarter turn on (payload only; stock drawing mocked)')

if variant=='ipod':
    # Accent (docs/internals.md#accent). The mapping: stock red blended with a neutral becomes the same
    # blend of the preset's red tone, alpha kept; greys, other hues and every Crimson color stay.
    ps=payload_syms; RED=(0xff,0x14,0x48)
    # WCAG contrast of each preset: white text on the bar's top, the progress fill on its track, and
    # white on the red tone (Graphite's silver is chosen for its lit look instead; Crimson is stock).
    def lum(c):
        v=[(c>>s&255)/255 for s in (16,8,0)]; v=[x/12.92 if x<=0.04045 else ((x+0.055)/1.055)**2.4 for x in v]
        return 0.2126*v[0]+0.7152*v[1]+0.0722*v[2]
    def ratio(a,b): return (max(lum(a),lum(b))+0.05)/(min(lum(a),lum(b))+0.05)
    for i,(top,bottom,light,tone,_) in enumerate(ACCENTS):
        assert ratio(0xffffff,top)>=4.5 and ratio(0xffffff,bottom)>=4.5 and ratio(light,O['TRACK_COLOR'])>=3,i
        assert i in (0,O['CRIMSON'],O['MINIMAL_ACCENT']) or ratio(0xffffff,tone)>=3,i
    assert ACCENTS[0][:2] == (0x424242, 0x424242)
    assert all(top==bottom for top,bottom,*_ in ACCENTS)
    assert ratio(O['NP_ARTIST_RGB'], 0) > ratio(0xaaaaaa, 0) >= 7
    passed()
    def mapped(c,preset,tone=3): return Machine().call(address=ps['accent_map'],args=(c,preset,tone,0),gap=0)&0xffffffff
    def rgba(r,g,b,a=255): return r|g<<8|b<<16|a<<24
    def blend(t,k,to,a=255): return rgba(*(round(t*x+k) for x in to),a)
    def close(x,y): return x>>24==y>>24 and all(abs((x>>s&255)-(y>>s&255))<=2 for s in (0,8,16))
    for (preset,accent),column in ((p,c) for p in enumerate(ACCENTS) for c in (2,3)):  # light and red tones
        hi=(accent[column]>>16,accent[column]>>8&255,accent[column]&255)
        same=preset==O['CRIMSON']
        for c in (rgba(0,0,0),rgba(255,255,255),rgba(0x80,0x80,0x80),rgba(0x2b,0x2b,0x2b,0x40),  # greys
                  rgba(0xff,0x9f,0x0a),rgba(0x16,0x9a,0xa6),rgba(0xff,0,0xff),rgba(0x0a,0x84,0xff),  # other hues
                  *(color_t(c) for a in ACCENTS for c in a if a is not ACCENTS[1])):  # accents never map again
            assert mapped(c,preset,column)==c,(preset,hex(c))
        table=[(rgba(*RED),blend(1,0,hi)),                         # pure red
               (rgba(*RED,0x40),blend(1,0,hi,0x40)),               # translucent #FF144840: alpha kept
               (rgba(0x7f,0x0a,0x24),blend(0.5,0,hi)),             # red on black, half coverage
               (rgba(*(round((x+255)/2) for x in RED)),blend(0.5,127.5,hi)),  # anti-aliased onto white
               (rgba(0x3d,0x19,0x20),blend(0.153,21.6,hi))]        # the pressed tint
        for c,want in table:
            got=mapped(c,preset,column)
            assert (got==c) if same else close(got,want),(preset,hex(c),hex(got),hex(want))
            if not same: assert mapped(got,preset,column)==got
        passed()

    # Settings: IPOD/ACCENT, HOME and BATTERY come from the stock config.ini once, a missing or bad value
    # is the default; the style color hook returns stock values under Crimson and maps under others.
    red=color_t(0xff1448); grey=color_t(0x808080)
    tramp={n:int(manifest['patch_symbols'][f'stock_{n}_trampoline'],16) for n in ('color','image','display')}
    def style_color(m,stock,name='text_color'):
        m.handlers[tramp['color']]='stock_color'; out=m.alloc(4); m.stock_color=stock
        return m.call(address=IPOD_HOOKS['style_get_color'][0],args=(out,0x1234,m.string(name),0),gap=0)&0xffffffff,m.get(out),out
    orig_hook=Machine.hook; GET=0x1000600  # a style vtable's get_gradient
    def hook(self,u,address,size,x):
        if self.handlers.get(address)=='stock_color':
            a=u.reg_read(UC_MIPS_REG_A0); self.word(a,self.stock_color); self.calls.append(('stock_color',a))
            self.returns(a); return
        if address==GET and getattr(self,'get_gradient',None): return self.get_gradient(u)
        return orig_hook(self,u,address,size,x)
    Machine.hook=hook
    # Each theme keeps its own accent: Classic ACCENT (MINIMAL_ACCENT not among them), Minimal (no THEME,
    # or 0) MINIMAL_ACCENT, whose default draws as Graphite whatever ACCENT holds.
    C={'THEME':'1'}
    for config,preset in (({},0),({**C,'ACCENT':'1'},1),({**C,'ACCENT':'2'},2),({**C,'ACCENT':'3','HOME':'1'},3),({**C,'ACCENT':'7'},0),
                          ({**C,'ACCENT':'12'},0),({**C,'ACCENT':'4'},4),({**C,'ACCENT':'5'},5),({**C,'ACCENT':'6'},6),({'ACCENT':'1'},0),
                          ({'MINIMAL_ACCENT':'1'},1),({'MINIMAL_ACCENT':'6'},6),({'MINIMAL_ACCENT':'7'},0),({'MINIMAL_ACCENT':'8'},8),({**C,'ACCENT':'8'},8),({**C,'MINIMAL_ACCENT':'2'},0),({'ACCENT':'2','THEME':'0'},0),({'ACCENT':'2','THEME':'2'},0)):  # 4 is Cobalt (once Custom, gone before 0.9.9)
        m=Machine(); m.config=config
        ret,got,out=style_color(m,red)
        assert ret==out and got==(red if preset==O['CRIMSON'] else color_t(ACCENTS[preset][3])),(config,hex(got))
        # Text takes the red tone, every other color property the light tone (Graphite: silver text,
        # #6E6E6E fills under white text).
        for name in ('highlight_text_color','bg_color','fg_color','border_color','selected_fg_color'):
            want=ACCENTS[preset][3 if name.endswith('text_color') else 2]
            assert style_color(m,red,name)[1]==(red if preset==O['CRIMSON'] else color_t(want)),(config,name)
        assert style_color(m,grey)[1]==grey
        assert len(m.config_reads)==8  # every key, once, on first use
        passed()
    # Real paint order, no persistent widget styles: normal, metadata, disabled alpha,
    # recycled text, touch hiding, Home siblings, and every accent.
    def begin(m,w): m.call(address=IPOD_HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0),gap=0)
    def end(m,w): m.call(address=HOOKS['widget_on_paint_border'][0],args=(w,m.canvas,0,0),gap=0)
    for preset in range(4):
        m=Machine(); m.config['ACCENT']=str(preset); w,rows=m.page_list(3)
        labels=[]
        for row in rows:
            l=m.node('hscroll_label'); m.nodes[row]['children'].append(l); m.word(l+O['W_PARENT'],row); labels.append(l)
        begin(m,w); begin(m,rows[0]); begin(m,labels[0])
        assert style_color(m,0xffffffff)[1]==0xff171717
        assert style_color(m,0xffaaaaaa)[1]==0xff484848
        assert style_color(m,0x66ffffff)[1]==0x66171717
        end(m,labels[0]); end(m,rows[0]); begin(m,rows[1]); begin(m,labels[1])
        assert style_color(m,0xffffffff)[1]==0xffffffff
        end(m,labels[1]); end(m,rows[1]); end(m,w)
        m.call(); begin(m,w); begin(m,labels[0]); assert style_color(m,0xffffffff)[1]==0xffffffff
        begin(m,labels[1]); assert style_color(m,0xffffffff)[1]==0xff171717
        end(m,labels[1]); end(m,w)
        m.touch(); begin(m,w); begin(m,labels[1]); assert style_color(m,0xffffffff)[1]==0xffffffff
        # Unselected metadata takes Minimal's quieter grey; Classic keeps stock's.
        assert style_color(m,0xffaaaaaa)[1]==0xff000000|O['SUDO_MUTED']
        k=Machine(); k.config.update(ACCENT=str(preset),THEME='1'); kw,_=k.page_list(3); k.touch(); begin(k,kw)
        assert style_color(k,0xffaaaaaa)[1]==0xffaaaaaa
    # Minimal with another accent: its card keeps the rows' own ink, metadata stock's grey there only.
    m=Machine(); m.config['MINIMAL_ACCENT']='4'; w,rows=m.page_list(3)
    begin(m,w); begin(m,rows[0]); assert style_color(m,0xffffffff)[1]==0xffffffff and style_color(m,0xffaaaaaa)[1]==0xffaaaaaa
    end(m,rows[0]); begin(m,rows[1]); assert style_color(m,0xffaaaaaa)[1]==0xff000000|O['SUDO_MUTED']
    end(m,rows[1]); end(m,w)
    m=Machine(); view,imgs=home_list(m); begin(m,view)
    labels=[m.nodes[m.get(img+O['W_PARENT'])]['children'][0] for img in imgs]
    begin(m,labels[0]); assert style_color(m,0xffffffff)[1]==0xffffffff
    end(m,labels[0]); begin(m,labels[1]); assert style_color(m,0xffffffff)[1]==0xffaaaaaa
    end(m,labels[1]); end(m,view); passed()

    # Decorative action labels may be disabled themselves; availability belongs to the button.
    for label_name in ('label_del','label_rename'):
        m=Machine(); label=m.node('hscroll_label',label_name); m.nodes[label]['enable']=0
        button=m.node('button','button',[label]); item=m.node('list_item','item',[button])
        m.word(label+O['W_PARENT'],button); m.word(button+O['W_PARENT'],item)
        for enabled in (1,0,1):
            m.nodes[button]['enable']=enabled
            begin(m,label)
            assert style_color(m,0xffaaaaaa)[1]==(0xffffffff if enabled else 0xff000000|O['SUDO_MUTED'])
        m.nodes[item]['enable']=0; begin(m,label)
        assert style_color(m,0xffffffff)[1]==0xff000000|O['SUDO_MUTED']
    passed()

    m=Machine(); style_color(m,red)
    assert m.config_reads==[('/mnt/data/config.ini','IPOD',key,'0') for key in ('ACCENT','HOME','BATTERY','THEME')]+[('/mnt/data/config.ini','IPOD','SHORTCUT','7'),('/mnt/data/config.ini','IPOD','MINIMAL_ACCENT','7'),('/mnt/data/config.ini','IPOD','ROUNDED','0'),('/mnt/data/config.ini','IPOD','LIGHT','0')]; passed()

    # Gradients: the leaf's null checks, then the caller's stops mapped (nr @8, stops @0xc).
    def gradient(config,stops,same_out=True,vt_get=True,style=True):
        m=Machine(); m.config=config
        out=m.alloc(0x4c); other=m.alloc(0x4c); vt=m.alloc(0x20); st=m.alloc(8)
        m.word(st,vt); m.word(vt+0x18,GET if vt_get else 0)
        def get(u):
            assert u.reg_read(UC_MIPS_REG_T9)==GET and u.reg_read(UC_MIPS_REG_A0)==st and u.reg_read(UC_MIPS_REG_A2)==out
            g=out if same_out else other
            m.word(g+8,len(stops))
            for i,c in enumerate(stops): m.word(g+0xc+8*i,c); m.word(g+0x10+8*i,i)
            m.returns(g)
        m.get_gradient=get
        ret=m.call(address=ps['ringnav_style_gradient'],args=(st if style else 0,0,out,0),gap=0)&0xffffffff
        g=out if same_out else other
        return ret,[m.get(g+0xc+8*i) for i in range(len(stops))],out,other
    ret,cols,out,_=gradient({},[red,grey,color_t(0x7f0a24)])
    assert ret==out and cols==[color_t(ACCENTS[0][2]),grey,mapped(color_t(0x7f0a24),0,2)]; passed()
    assert gradient({'ACCENT':'1','THEME':'1'},[red,grey])[1]==[red,grey]; passed()
    assert gradient({'ACCENT':'1'},[red])[1]==[color_t(ACCENTS[0][2])]; passed()  # Minimal: Graphite
    ret,cols,_,other=gradient({},[red],same_out=False); assert ret==other and cols==[red]; passed()
    assert gradient({},[red],vt_get=False)[0]==0 and gradient({},[red],style=False)[0]==0; passed()
    # Through stock style_get_color, which asks style_get_gradient first for every color: its call
    # is left unmapped, so the color hook still gives text the red tone and fills the light tone.
    for name,column in (('text_color',3),('bg_color',2)):
        m=Machine(); vt=m.alloc(0x20); st=m.alloc(8); m.word(st,vt); m.word(vt+0x14,0x1000700); m.word(vt+0x18,GET)
        def get(u):
            g=u.reg_read(UC_MIPS_REG_A2); m.word(g+8,1); m.word(g+0xc,red)
            m.returns(g)
        m.get_gradient=get; out=m.alloc(4)
        m.call(address=IPOD_HOOKS['style_get_color'][0],args=(out,st,m.string(name),0),gap=0)
        assert m.get(out)==color_t(ACCENTS[0][column]),(name,hex(m.get(out))); passed()

    # Light Mode: neutral colours flip between LIGHT_PAPER and LIGHT_INK, other hues stay; red takes
    # the accent's own tones (Graphite's: its silver, flipped dark), a red tint over black the same
    # tint over paper; gradient stops alike.
    P,K=O['LIGHT_PAPER'],O['LIGHT_INK']
    flipv=lambda v: P-v*(P-K)//255
    paper,ink=0xff000000|P*0x010101,0xff000000|K*0x010101
    g=flipv(0xd8)*0x010101
    for config,text,fill in (({'LIGHT':'1'},g,g),({'LIGHT':'1','MINIMAL_ACCENT':'4'},ACCENTS[4][3],ACCENTS[4][2]),({'LIGHT':'1','THEME':'1','ACCENT':'1'},ACCENTS[1][3],ACCENTS[1][2])):
        m=Machine(); m.config=config
        assert style_color(m,red)[1]==color_t(text) and style_color(m,red,'bg_color')[1]==color_t(fill),config
        assert style_color(m,0xff000000)[1]==paper and style_color(m,0xffffffff)[1]==ink and style_color(m,grey)[1]==0xff000000|flipv(0x80)*0x010101
        assert style_color(m,0xffff8000)[1]==0xffff8000  # another hue
        tint=style_color(m,color_t(0x3d1920),'bg_color')[1]  # stock's pressed tint: light now, of the tone
        assert all(c>0x90 for c in tint.to_bytes(4,'little')[:3]),hex(tint)
        assert gradient(config,[0xff000000,0xffffffff])[1]==[paper,ink]; passed()
    # Theme images flip in Light Mode when colourless and mostly glyph; a disc or a coloured image,
    # and stock's, stay. 4x4 BGRA: one white pixel on transparent, or on a grey plate.
    def light_image(config,name,plate):
        m=Machine(); m.config=config; m.handlers[tramp['image']]='stock_image'
        bm=m.alloc(0x60); data=m.alloc(64)
        m.word(bm,4); m.word(bm+4,4); m.word(bm+8,16); m.u.mem_write(bm+0xe,struct.pack('<H',3)); m.word(bm+0x14,data)
        m.u.mem_write(data,bytes([0xff,0xff,0xff,0xff])+(bytes([0x44,0x44,0x44,0xff]) if plate else bytes(4))*15)
        m.call(address=IPOD_HOOKS['image_manager_add'][0],args=(0x1000500,m.string(name),bm,0),gap=0)
        return bytes(m.u.mem_read(data,8))
    assert light_image({'LIGHT':'1'},'scover_wait0',False)==bytes([K,K,K,0xff])+bytes(4); passed()
    assert light_image({'LIGHT':'1'},'scover_wait0',True)==bytes([0xff,0xff,0xff,0xff,0x44,0x44,0x44,0xff]); passed()
    assert light_image({},'scover_wait0',False)==bytes([0xff]*4)+bytes(4); passed()
    assert light_image({'LIGHT':'1'},'file:///tmp/coverpic.jpg',False)[:4]==bytes([0xff]*4); passed()

    # Images: decoded 32-bit theme bitmaps are mapped in place before stock caches them; covers by
    # path or URL, Crimson and other formats are left alone. bitmap_t w @0, h @4, line_length @8, format @0xe.
    def image(config,name,fmt=3):
        m=Machine(); m.config=config; m.handlers[tramp['image']]='stock_image'
        bm=m.alloc(0x60); data=m.alloc(64)
        m.word(bm,2); m.word(bm+4,2); m.word(bm+8,12); m.u.mem_write(bm+0xe,struct.pack('<H',fmt)); m.word(bm+0x14,data)
        px=[bytes([0x48,0x14,0xff,0x80]),bytes([0x80,0x80,0x80,0xff])]  # BGRA: translucent red, grey
        for y in range(2): m.u.mem_write(data+12*y,px[0]+px[1]+b'\xee'*4)  # 4 bytes of row padding
        ret=m.call(address=IPOD_HOOKS['image_manager_add'][0],args=(0x1000500,m.string(name),bm,0),gap=0)
        assert ret==0 and [c[1:] for c in m.calls if c[0]=='stock_image'][0][2]==bm
        return [bytes(m.u.mem_read(data+12*y,12)) for y in range(2)]
    hi=ACCENTS[0][3]; want=bytes([hi&255,hi>>8&255,hi>>16,0x80])+bytes([0x80,0x80,0x80,0xff])+b'\xee'*4
    assert image({},'switch_on')==[want,want]; passed()
    stock=bytes([0x48,0x14,0xff,0x80,0x80,0x80,0x80,0xff])+b'\xee'*4
    for config,name,fmt in (({'ACCENT':'1','THEME':'1'},'switch_on',3),({},'file:///tmp/coverpic.jpg',3),({},'/mnt/mmc/a/cover.jpg',3),
                            ({},'https://resources.tidal.com/images/a/320x320.jpg',3),({},'switch_on',5)):
        assert image(config,name,fmt)==[stock,stock],(config,name,fmt); passed()

    # Artwork is rounded before caching, including the cached opacity flag. Straight RGB and row
    # padding survive; every byte order and an already transparent source corner are supported.
    for name in ('file:///mnt/mmc/.sldp/album0.jpg', 'file:///tmp/coverpic.jpg',
                 'file:///tmp/externpic.jpg', 'file:///tmp/album_tidal.jpg', 'file:///mnt/mmc/photo.jpg'):
        for fmt in range(1,5):
            m=Machine(); m.handlers[tramp['image']]='stock_image'
            w,h,stride=60,52,248; bm=m.alloc(0x60); data=m.alloc(stride*h)
            m.word(bm,w); m.word(bm+4,h); m.word(bm+8,stride)
            m.u.mem_write(bm+0xc,struct.pack('<HH',3,fmt)); m.word(bm+0x14,data)
            alpha=(3,0,3,0)[fmt-1]
            pixel=bytearray([47]*4); pixel[alpha]=255
            source=bytearray((pixel*w+b'\xee'*8)*h); source[alpha]=0
            m.u.mem_write(data,bytes(source))
            args=(0x1000500,m.string(name),bm,0)
            m.call(address=IPOD_HOOKS['image_manager_add'][0],args=args,gap=0)
            rounded=bytes(m.u.mem_read(data,stride*h))
            if not name.startswith('file:///mnt/mmc/.sldp/'):
                assert rounded==source and m.get(bm+0xc)&1
            else:
                assert not m.get(bm+0xc)&1 and m.get(bm+0xc)&2
                assert all(rounded[y*stride+4*x+alpha]==0 for x,y in ((0,0),(w-1,0),(0,h-1),(w-1,h-1)))
                assert rounded[4*(w//2)+alpha]==rounded[(h//2)*stride+alpha]==255
                assert all(rounded[i]==value for i,value in enumerate(source) if i%stride>=w*4 or i%4!=alpha)
                assert any(0<rounded[y*stride+4*x+alpha]<255 for y in range(4) for x in range(4))
                m.call(address=IPOD_HOOKS['image_manager_add'][0],args=args,gap=0)
                assert bytes(m.u.mem_read(data,stride*h))==rounded
            passed()

    # The confirm pop-up's red discs take the dark CONFIRM_SURFACE under every accent, Crimson too:
    # the stock OK disc (#FF1448, white glyph) and Cancel's tint (#FF4871 with a #FFE4EA glyph) keep
    # their glyphs at 4.5:1 or more; the pressed images darken; other images keep the accent's tone.
    def rgba(config,name,pixels_,fmt=3):
        m=Machine(); m.config=config; m.handlers[tramp['image']]='stock_image'
        bm=m.alloc(0x60); data=m.alloc(4*len(pixels_))
        m.word(bm,len(pixels_)); m.word(bm+4,1); m.word(bm+8,4*len(pixels_)); m.u.mem_write(bm+0xe,struct.pack('<H',fmt)); m.word(bm+0x14,data)
        at=((0,1,2,3),(3,2,1,0),(2,1,0,3),(1,2,3,0))[fmt-1]
        def packed(c,a):
            p=bytearray(4)
            for offset,value in zip(at,(c>>16,c>>8&255,c&255,a)): p[offset]=value
            return p
        m.u.mem_write(data,b''.join(packed(c,a) for c,a in pixels_))
        m.call(address=IPOD_HOOKS['image_manager_add'][0],args=(0x1000500,m.string(name),bm,0),gap=0)
        raw=bytes(m.u.mem_read(data,4*len(pixels_)))
        return [(raw[4*i+at[0]]<<16|raw[4*i+at[1]]<<8|raw[4*i+at[2]],raw[4*i+at[3]]) for i in range(len(pixels_))]
    def pixels(config,name,colors):
        return [c for c,_ in rgba(config,name,[(c,255) for c in colors])]
    S=O['CONFIRM_SURFACE']
    for preset in CLASSIC:
        config={'ACCENT':str(preset),'THEME':'1'}
        disc,glyph=pixels(config,'confirm_ok',[0xff1448,0xffffff])
        assert disc==S and glyph==0xffffff and ratio(glyph,disc)>=4.5,(preset,hex(disc))
        disc,glyph=pixels(config,'confirm_cancel',[0xff4871,0xffe4ea])
        assert ratio(glyph,disc)>=4.5 and ratio(disc,0)<ratio(glyph,0),(preset,hex(disc),hex(glyph))
        assert pixels(config,'confirm_okdown',[0x7f0a24])[0]<S  # pressed: darker than the disc
        assert pixels(config,'switch_on',[0xff1448])==[0xff1448 if preset==O['CRIMSON'] else ACCENTS[preset][3]]
        passed()

    # Quick settings: the active controls' stock red discs (#FF1448, white glyph, pink anti-aliased
    # glyph edges) take the accent's red tone, as everything red does; on a tone brighter than
    # GLYPH_LIGHT_MAX (Graphite's silver) the glyph turns CONFIRM_SURFACE so it stays legible, else
    # it stays white. Glyph and edges keep 3:1 on the disc and transparent pixels are untouched.
    # Inactive (#444444) and disabled discs keep their stock greys, and an active disc reads clearly
    # apart from them. The brightness suns, on black rather than a disc, keep the accent's red tone.
    ACTIVE=('drop_wifiopen','drop_btopen','drop_keylockopen','drop_highgain','drop_lo','drop_usbaudio','drop_usbdac')
    INACTIVE=('drop_wifi','drop_bt','drop_keylock','drop_lowgain','drop_po','drop_usbstorage','drop_playset','drop_sysset')
    disc=[(0xff1448,255),(0xffffff,255),(0xffc0d0,255),(0xff1448,0),(0x000000,0)]  # disc, glyph, glyph edge, transparent
    def perceived(c): return ((c>>16)*299+(c>>8&255)*587+(c&255)*114)//1000
    for preset in CLASSIC:
        config={'ACCENT':str(preset),'THEME':'1'}
        tone=0xff1448 if preset==O['CRIMSON'] else ACCENTS[preset][3]
        light=preset!=O['CRIMSON'] and perceived(tone)>O['GLYPH_LIGHT_MAX']
        for name in ACTIVE:
            (d,da),(g,ga),(e,ea),*clear=rgba(config,name,disc)
            assert d==tone and da==255 and g==(S if light else 0xffffff),(preset,name,hex(d),hex(g))
            assert ratio(g,d)>=3 and ratio(e,d)>=2,(preset,name,hex(d),hex(e))
            assert max(ratio(d,0x444444),ratio(0x444444,d))>=1.5,(preset,name,hex(d))  # reads apart from inactive
            assert [a for _,a in clear]==[0,0],(preset,name)  # transparency kept
        assert light==(preset==0)  # only Graphite's silver takes the dark glyph
        for name in INACTIVE+('drop_highgaindisable','drop_lowgaindisable'):
            grey=[(0x444444,255),(0xffffff,255),(0x5b5b5b,255),(0x222222,255)]
            assert rgba(config,name,grey)==grey,(preset,name)
        red=[(0xff1448,255)]
        for name in ('drop_lighleft','drop_lightright','eqdrop_dot','dropdown','xdrop_bt'):
            assert rgba(config,name,red)==[(tone,255)],(preset,name)
        # Red under white is a surface and takes the light tone, so the white stays legible (4.5:1
        # on Graphite, not white on silver): a switch's knob, a disc's glyph, or the label over a
        # btn_ image, which holds no white itself. A red mark alone (a tick, the knob-less pixels
        # of the tests above) keeps the red tone, and transparent white is not a glyph.
        lit=0xff1448 if preset==O['CRIMSON'] else ACCENTS[preset][2]
        for name in ('switch_on','add','dec','mulselect'):
            assert rgba(config,name,disc[:2])==[(lit,255),(0xffffff,255)] and ratio(0xffffff,lit)>=3,(preset,name)
            assert rgba(config,name,[(0xffffff,255),(0xff1448,255)])==[(0xffffff,255),(lit,255)],(preset,name)
        for name in ('btn_enter','btn_red'):
            assert rgba(config,name,red)==[(lit,255)],(preset,name)
        # Pressed images (#7F0A24, a disc's glyph #7F7F7F) follow: darker than the same red alone.
        alone=rgba(config,'select',[(0x7f0a24,255)])[0][0]
        for name,px in (('btn_enterdown',[(0x7f0a24,255)]),('add_down',[(0x7f0a24,255),(0x7f7f7f,255)])):
            dim=rgba(config,name,px)
            assert dim[1:]==px[1:] and (dim[0][0]<alone if preset==0 else dim[0][0]==alone),(preset,name,hex(dim[0][0]))
        for name in ('select','sleepdot'):
            assert rgba(config,name,red+[(0xffffff,0)])==[(tone,255),(0xffffff,0)],(preset,name)
        # The settings rows' category icons keep their stock colours, red ones included, and
        # list_into its white.
        pink=[(0xcf2f53,255),(0xff1448,255),(0xffffff,255)]
        for name in ('system_netservice','netservice_dlna','wifiset_wifi','display_backlight','system_language'):
            assert rgba(config,name,pink)==pink,(preset,name)
        assert rgba(config,'list_into',[(0xffffff,255),(0xffffff,0)])==[(0xffffff,255),(0xffffff,0)]
        # A cover whose file name starts like an asset stays unmapped.
        assert rgba(config,'file:///mnt/mmc/drop_bt.png',red)==red
        passed()
    # Minimal: the settings icons' discs turn MINIMAL_CHEVRON, their glyphs stay white, edges blend
    # by saturation against the disc's, and list_into's ink is MINIMAL_CHEVRON, alpha kept; other
    # images take Graphite whatever ACCENT holds.
    ink=O['MINIMAL_CHEVRON']
    px=[(0xff1448,255),(0xff8aa4,255),(0xffffff,255),(0xff1448,128),(0xffffff,0)]
    for icon in (px,[(0x00a0ff,255),(0xffffff,255)],[(0xc425ff,255),(0xffffff,255)]):
        got=rgba({},'system_netservice',icon)
        assert got[0]==(ink,255) and (0xffffff,255) in got,got
    for config in ({},{'ACCENT':'2'}):
        assert rgba(config,'system_netservice',px)==[(ink,255),(0xbcbcbc,255),(0xffffff,255),(ink,128),(0xffffff,0)]
        assert rgba(config,'list_into',px)==[(O['MINIMAL_CHEVRON'],a) for _,a in px]
        assert rgba(config,'list_delete',[(0xffffff,255),(0xffffff,0)])==[(O['MINIMAL_CHEVRON'],255),(O['MINIMAL_CHEVRON'],0)]
        # Codec badges take SUDO_MUTED, legible on black and on the selected card; other img_ art doesn't.
        assert rgba(config,'img_flac',[(0xff1448,255),(0xff1448,0)])==[(O['SUDO_MUTED'],255),(O['SUDO_MUTED'],0)]
        assert rgba(config,'img_delete',[(0xff1448,255)])!=[(O['SUDO_MUTED'],255)]
        assert rgba({**config,'THEME':'1'},'img_flac',[(0xff1448,255)])!=[(O['SUDO_MUTED'],255)]
        assert rgba(config,'switch_on',disc[:2])==[(ACCENTS[0][2],255),(0xffffff,255)]
        assert rgba(config,'file:///mnt/mmc/system_netservice',px)==px
        # Active Delete keeps stock's white; the pressed/disabled glyph keeps its grey.
        assert rgba(config,'navbar_delete',[(0xffffff,255)])==[(0xffffff,255)]
        assert rgba(config,'navbar_deletedown',[(0x777777,255)])==[(0x777777,255)]
        for name in ('navbar_add','home_stream','img_edit','stream_spotify','stream_radio','tidal_fav_add','play_fav','btn_blue'):
            got=rgba(config,name,[(0x158bcd,255),(0xff1448,128),(0xffffff,0)])
            assert all(c>>16 == (c>>8&255) == (c&255) for c,a in got), (name,got)
            assert [a for c,a in got]==[255,128,0]
        # A card app's Streaming icon (addon.c, by its path on the card) greys as stream_spotify does,
        # its white glyph white and alpha kept; Classic keeps it, and the app's other images stay.
        for name in ('file:///mnt/mmc/.deezer/icon-40.png','file:///mnt/mmc/.deezer/icon.png'):
            got=rgba(config,name,[(0xa238ff,255),(0xffffff,255),(0x2800ff,128)])
            assert all(c>>16 == (c>>8&255) == (c&255) for c,a in got) and got[1]==(0xffffff,255),(name,got)
            assert [a for c,a in got]==[255,255,128] and got[0]!=got[2]
            assert rgba({**config,'THEME':'1'},name,[(0xa238ff,255)])==[(0xa238ff,255)]
        for name in ('file:///mnt/mmc/.deezer/cover.png','file:///mnt/mmc/Music/icon.png','file:///mnt/mmc/.deezer/icon.png.jpg'):
            assert rgba(config,name,[(0xa238ff,255)])==[(0xa238ff,255)],name
        for name in ('local_album','local_shuffle','list_tidal','small_stream','player_playlist'):
            assert rgba(config,name,px)==[(ink,255),(0xbcbcbc,255),(0xffffff,255),(ink,128),(0xffffff,0)],name
            grey=[(0x777777,255),(0xffffff,255),(0xbcbcbc,128),(0xffffff,0)]
            assert rgba(config,name,grey)==grey,name
        for name in ('default_album_big','play_defaultcover','playlist_default','tidal_user','tidal_shanling',
                     'bar_charge','bar_lowcharge','/mnt/mmc/local_photo.png','https://example.org/play_fav.png'):
            colors=[(0x12a3ce,128),(0xffffff,0)]
            assert rgba(config,name,colors)==colors,name
    passed()

    # Packaged icon pixels: monochrome, alpha preserved and existing grey glyphs unchanged.
    for fmt in (1,2,3,4):
        assert rgba({},'local_album',px,fmt)==rgba({},'local_album',px)
        assert rgba({},'stream_spotify',px,fmt)==rgba({},'stream_spotify',px)
    for name in ACTIVE:
        active=rgba({},name,disc[:2]); inactive=rgba({},INACTIVE[0],[(0x444444,255),(0xffffff,255)])
        assert active==[(0xd8d8d8,255),(S,255)] and inactive==[(0x444444,255),(0xffffff,255)]
        assert ratio(active[0][0],active[1][0])>=3
    passed()
    import subprocess
    from ipod import imagemagick
    for name in ('local_album','local_photos','list_folder','list_tidal','player_playlist',
                 'small_stream','stream_spotify','stream_radio','tidal_fav_add','play_fav'):
        png=subprocess.check_output(['unsquashfs','-cat',str(B/'rootfs.squashfs'),
                                     'release/assets/default/raw/images/xx/'+name+'.png'])
        raw=imagemagick('png:-','-depth','8','rgba:-',data=png)
        colors=list(dict.fromkeys((raw[i]<<16|raw[i+1]<<8|raw[i+2],raw[i+3]) for i in range(0,len(raw),4)))
        sample=colors[::max(1,len(colors)//32)]
        got=rgba({},name,sample)
        assert all(c>>16==(c>>8&255)==(c&255) for c,a in got),name
        assert [a for c,a in got]==[a for c,a in sample],name
        if name in ('list_folder','list_tidal','player_playlist'): assert got==sample,name
    passed()

    # Display settings: after the stock rows, Appearance, Coverflow and Controls, each opening stock's page
    # again (display_sub) with its stock rows dropped and its own rows instead. Appearance holds Theme,
    # Accent, Corners, Home and Battery in the native row widgets and styles, with the Display (Theme and
    # Accent), cover mode and power manager icons; Centre or tap cycles and saves each; a new theme or
    # accent drops the image cache and repaints, a new Battery mode repaints the bar.
    def display_page(m,sub):
        m.word(payload_syms['display_sub'],sub)
        view=m.node('scroll_view','scroll_view_display',[m.entry(0) for _ in range(3)])
        for e in m.nodes[view]['children']: m.word(e+O['W_PARENT'],view)
        m.top=m.node('window','display_page',[m.node('list_view','list_view_display',[view])])
        m.nodes[m.wm]={'type':'window_manager','children':[m.top]}
        m.calls=[]; assert m.call(address=HOOKS['systemset_display_page_init'][0],args=(m.top,5,0,0),gap=0)==0
        assert m.calls[0][:3]==('stock_display',m.top,5)
        m.icons_set=[m.text(c[2]) for c in m.calls if c[0]=='image_base_set_image']
        return view
    def display(config,card=True,sub=1):
        CONFIG.clear(); CONFIG.update(config); m=QueueMachine(); m.restack=True; m.rockbox_mode=0o100755 if card else 0; m.handlers[tramp['display']]='stock_display'
        view=display_page(m,sub)
        return m,view,setting_rows(m,view) if sub==1 else None
    def setting_rows(m,view):  # Accent, Home, Battery, Theme: as ringnav_display's index
        lab=lambda r: m.nodes[m.nodes[m.nodes[r]['children'][0]]['children'][1]].get('text','')
        kids=m.nodes[view]['children']
        return [next(r for r in kids if lab(r).startswith(k)) for k in ('Accent','Home','Battery','Theme')]
    def row_text(r): return m.nodes[m.nodes[m.nodes[r]['children'][0]]['children'][1]]['text']
    m,main,_=display({},sub=0)
    kids=m.nodes[main]['children']
    assert len(kids)==6 and [row_text(r) for r in kids[3:]]==['Appearance','Coverflow','Controls']
    assert m.icons_set==['system_display','list_into','playset_covermode','list_into','playset_folderjump','list_into']
    f,ctx=m.handler(m.nodes[kids[4]]['children'][0],O['EVT_CLICK']); m.calls=[]
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.opened[-1]==('systemset/display_page',None) and m.get(payload_syms['display_sub'])==0; passed()
    # Coverflow: Artist Albums, Coverflow Sort, Refresh Coverflow; Controls: Shortcut, Hold Return, Wheel QS, Wheel.
    cfv=display_page(m,2); cf,cf_sort,cf_refresh=m.nodes[cfv]['children']
    assert m.icons_set==['playset_covermode','playset_covermode','system_update']
    ctl=display_page(m,3); shortcut,hold,qs,wheel=m.nodes[ctl]['children']
    assert m.icons_set==['playset_covermode','playset_folderjump','system_display','switch_off','system_display','list_into']
    assert [row_text(r) for r in (shortcut,hold,qs)]==['Shortcut: Rockbox','Hold Return: Recent','Wheel QS']; passed()
    view=display_page(m,1); rows=setting_rows(m,view)
    kids=m.nodes[view]['children']; light,corners=kids[1],kids[3]
    assert kids==[rows[3],light,rows[0],corners,rows[1],rows[2]] and m.nodes[rows[0]].get('visible',1)==1  # Theme, Light Mode, Accent, Corners, Home, Battery
    assert m.icons_set==['system_display','system_display','system_display','system_display','switch_off','playset_covermode','system_powermanager'] and all(m.nodes[r]['type']=='list_item' and m.nodes[r]['style']=='s_listitem_black' for r in rows)
    buttons=[m.nodes[r]['children'][0] for r in rows]; labels=[m.nodes[b]['children'][1] for b in buttons]
    for b,l in zip(buttons,labels):
        icon=m.nodes[b]['children'][0]  # stock's 0x4c19bc icon geometry, which the row layouter maps
        assert m.nodes[icon]['type']=='image' and [m.get(icon+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[10,0,52,70]
        assert m.nodes[b]['style']=='s_btn_listitem' and [m.get(b+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[20,0,335,70]
        assert m.nodes[l]['type']=='hscroll_label' and m.nodes[l]['style']=='s_scrlabel_white24l' and m.get(l+O['W_X'])==72
    def texts(): return [m.nodes[l]['text'] for l in labels]
    # Artist albums (Q2POD ARTISTCF), the other layout choice, after Home; no key is Coverflow.
    cf_label=m.nodes[m.nodes[cf]['children'][0]]['children'][1]; assert m.nodes[cf_label]['text']=='Artist Albums: Coverflow'
    f,ctx=m.handler(m.nodes[cf]['children'][0],O['EVT_CLICK']); m.calls=[]
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.nodes[cf_label]['text']=='Artist Albums: List'
    assert [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(2,'Q2POD','ARTISTCF')]
    m.call(address=f,args=(ctx,m.event,0,0),gap=0); assert m.nodes[cf_label]['text']=='Artist Albums: Grid'
    m.call(address=f,args=(ctx,m.event,0,0),gap=0); assert m.nodes[cf_label]['text']=='Artist Albums: Coverflow'; passed()
    # Coverflow Sort and Refresh Coverflow (coverflow.c), the library Coverflow's Sort and Refresh cards
    # before: no saved order is Album; Refresh empties the art cache for the next open, with a toast.
    sort_label,refresh_label=(m.nodes[m.nodes[r]['children'][0]]['children'][1] for r in (cf_sort,cf_refresh))
    assert m.nodes[sort_label]['text']=='Coverflow Sort: Album' and m.nodes[refresh_label]['text']=='Refresh Coverflow'
    f,ctx=m.handler(m.nodes[cf_refresh]['children'][0],O['EVT_CLICK']); m.mock('opendir@GLIBC_2.0'); m.calls=[]
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.toasts[-1][3]=='Coverflow rebuilds its artwork next time'
    assert [m.text(c[1]) for c in m.calls if c[0]=='opendir@GLIBC_2.0']==['/mnt/mmc/.coverflow'] and m.nodes[refresh_label]['text']=='Refresh Coverflow'; passed()
    # Shortcut: no key is Rockbox on a card with it, else Favorites.
    for card,first in ((True,'Rockbox'),(False,'Favorites')):
        CONFIG.clear(); d=Machine(); d.rockbox_mode=0o100755 if card else 0
        assert d.text(d.call(address=payload_syms['ipod_home_shortcut'],args=(0,0,0,0),gap=0))==first
        assert d.call(address=payload_syms['ipod_home_rockbox'],args=(0,0,0,0),gap=0)==1  # shown either way
    d.rockbox_mode=0o100755  # a card mounted after the first read: the default follows it
    assert d.text(d.call(address=payload_syms['ipod_home_shortcut'],args=(0,0,0,0),gap=0))=='Rockbox'
    passed()
    assert texts()==['Accent: Ivory','Home: Split','Battery: Icon','Theme: Minimal']; passed()
    def click(i):
        m.calls=[]; f,ctx=m.handler(buttons[i],O['EVT_CLICK'])
        assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
        return [c[1:] for c in m.calls if c[0]=='write_int_config']
    # Initialize the real Home asset on this machine, then toggle its cached settings in Display.
    home=asset_tree(m,HOME_PAGE)
    m.handlers[HOOKS['home_page_init'][0]+12]='stock_home'
    m.call(address=HOOKS['home_page_init'][0],args=(home,0,0,0),gap=0)
    art=named(m,home,'img_homeart')
    def home_bounds(full,classic=False):
        x0,end=(O['HOME_CLASSIC_TEXT_X'],O['CHEVRON_W']-10) if classic else (HOME_TEXT_X,O['HOME_LABEL_END' if full else 'HOME_SPLIT_LABEL_END'])
        split=O['HOME_SPLIT_W']
        row=(O['HOME_CLASSIC_ROW'] if classic else O['HOME_FULL_ROW']) if full else split
        for name in ('list_view_home','list_view_homeset'):
            menu=named(m,home,name)
            assert m.get(menu+O['W_W'])==(375 if full else split)
            assert m.get(menu+O['W_Y'])==(0 if classic else O['HOME_TOP'])
            for r in m.nodes[m.nodes[menu]['children'][0]]['children']:
                label=m.nodes[r]['children'][0]
                x,y,width,height=cf_geometry(m,label)
                assert x==x0 and width>0 and x+width==row-end and m.get(r+O['W_W'])==row
        assert m.nodes[art]['visible']==(not full)
        assert tuple(signed(m.get(art+O[k])) for k in ('W_X','W_Y','W_W','W_H'))==(split,0,375-split,290)
    shortcut_button=m.nodes[shortcut]['children'][0]
    sf,sc=m.handler(shortcut_button,O['EVT_CLICK'])
    for label in ('Favorites','Shuffle','Rated Albums','Artists','Albums','Games','Rockbox'):
        m.call(address=sf,args=(sc,m.event,0,0),gap=0)
        assert m.nodes[named(m,home,'label_rockbox')]['text']==label
    home_bounds(False)
    writes=click(1); assert [(w[0],m.text(w[2])) for w in writes]==[(1,'HOME')] and texts()[1]=='Home: Full'
    home_bounds(True)
    assert not [c for c in m.calls if c[0]=='image_manager_unload_all']; passed()
    click(1); assert texts()[1]=='Home: Split'; home_bounds(False); passed()
    # Theme: Classic shows its own accent (ACCENT) after Theme, lays Home out with its own text inset
    # and chevron row, and the accent's bar; back to Minimal, all of it reverts to Minimal's accent.
    # Rounded Corners: a switch, off with no key; a click saves it on, and Theme leaves it alone.
    corners_label=m.nodes[m.nodes[corners]['children'][0]]['children'][1]; assert m.nodes[corners_label]['text']=='Rounded Corners'
    def corners_switch(): return [m.nodes[c]['image'] for c in m.nodes[m.nodes[corners]['children'][0]]['children'] if m.nodes[c].get('name')=='_setting_switch']
    assert corners_switch()==['switch_off']
    f,ctx=m.handler(m.nodes[corners]['children'][0],O['EVT_CLICK']); m.calls=[]
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and corners_switch()==['switch_on']
    assert [(c[1],m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(1,'ROUNDED')] and ('widget_invalidate_force',m.wm) in [c[:2] for c in m.calls]
    # Light Mode: no key is Off; a click saves On, then Auto (LIGHT=2), then Off; each drops the cached images and repaints.
    light_label=m.nodes[m.nodes[light]['children'][0]]['children'][1]; assert m.nodes[light_label]['text']=='Light Mode: Off'
    f,ctx=m.handler(m.nodes[light]['children'][0],O['EVT_CLICK'])
    for value,text in ((1,'On'),(2,'Auto'),(0,'Off')):
        m.calls=[]; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.nodes[light_label]['text']=='Light Mode: '+text
        assert [(c[1],m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(value,'LIGHT')]
        assert ('image_manager_unload_all',0x1000500) in [c[:2] for c in m.calls] and ('widget_invalidate_force',m.wm) in [c[:2] for c in m.calls]
        if value==2:  # Auto: the clock's minute drops the images and repaints as the hour crosses, once
            for clock,flip in (((6,59),1),((6,59),0),((7,0),1),((7,1),0),((19,0),1)):
                m.clock=clock; m.calls=[]
                m.call(address=payload_syms['clock_due'],args=(0,0,0,0),gap=0,clear=False)
                assert [c[:2] for c in m.calls if c[0]=='image_manager_unload_all']==[('image_manager_unload_all',0x1000500)]*flip,clock
            m.clock=(18,14)
            m.call(address=payload_syms['clock_due'],args=(0,0,0,0),gap=0,clear=False)
    passed()
    # Theme: Classic shows its own accent (ACCENT) after Theme, lays Home out with its own text inset
    # and chevron row, and the accent's bar; back to Minimal, all of it reverts to Minimal's accent.
    writes=click(3); assert [(w[0],m.text(w[1]),m.text(w[2])) for w in writes]==[(1,'IPOD','THEME')]
    assert corners_switch()==['switch_on']
    assert ('image_manager_unload_all',0x1000500) in [c[:2] for c in m.calls] and ('widget_invalidate_force',m.wm) in [c[:2] for c in m.calls]
    kids=m.nodes[view]['children']
    assert kids==[rows[3],light,rows[0],corners,rows[1],rows[2]] and m.nodes[rows[0]].get('visible',1)==1
    assert texts()==['Accent: Graphite','Home: Split','Battery: Icon','Theme: Classic']; home_bounds(False,True); passed()
    # Theme, the first row, keeps the selection, painted.
    m.paint(view); assert m.selected(view)==0; passed()
    click(1); assert texts()[1]=='Home: Full'; home_bounds(True,True); click(1); home_bounds(False,True); passed()
    for value,name in ((1,'Crimson'),(2,'Tidal'),(3,'Champagne'),(4,'Cobalt'),(5,'Tangerine'),(6,'Plum'),(8,'Green'),(0,'Graphite'),(1,'Crimson')):  # Ivory skipped
        writes=click(0)
        assert len(writes)==1 and writes[0][0]==value and m.text(writes[0][1])=='IPOD' and m.text(writes[0][2])=='ACCENT'
        assert ('image_manager_unload_all',0x1000500) in [c[:2] for c in m.calls] and ('widget_invalidate_force',m.wm) in [c[:2] for c in m.calls]
        assert texts()[0]=='Accent: '+name
        m.paint(view)
        assert {b[4]&0xffffff for b in m.bands if b[1]>m.bands[0][1]}=={color_t(ACCENTS[value][0])&0xffffff}
        assert m.bands[0][4]==color_t(ACCENTS[value][4]); passed()
    writes=click(3); assert [(w[0],m.text(w[2])) for w in writes]==[(0,'THEME')] and texts()[3]=='Theme: Minimal' and corners_switch()==['switch_on']
    m.paint(view); assert m.selected(view)==0
    kids=m.nodes[view]['children']
    assert kids==[rows[3],light,rows[0],corners,rows[1],rows[2]] and texts()[0]=='Accent: Ivory' and texts()[1]=='Home: Split'
    home_bounds(False)
    m.paint(view); assert not m.bands and m.rounded[0]['color']==color_t(0xeeeeec); passed()
    # Minimal's accents (MINIMAL_ACCENT): Ivory, then Classic's; a coloured one is the card's fill.
    for value,name in ((0,'Graphite'),(1,'Crimson'),(2,'Tidal'),(3,'Champagne'),(4,'Cobalt'),(5,'Tangerine'),(6,'Plum'),(8,'Green'),(7,'Ivory')):
        writes=click(0); assert [(w[0],m.text(w[2])) for w in writes]==[(value,'MINIMAL_ACCENT')] and texts()[0]=='Accent: '+name
        m.paint(view); assert not m.bands and m.rounded[0]['color']==color_t(0xeeeeec if value==7 else ACCENTS[value][0])
    passed()
    click(3); home_bounds(False,True); click(3); home_bounds(False); passed()
    bar=m.node('window','system_bar'); m.word(syms['system_bar'],bar)
    for value,name in ((1,'Percent'),(2,'Icon + Percent'),(3,'Percent Inside'),(0,'Icon')):
        writes=click(2); assert [(w[0],m.text(w[2])) for w in writes]==[(value,'BATTERY')] and texts()[2]=='Battery: '+name
        assert ('widget_invalidate_force',bar) in [c[:2] for c in m.calls] and not [c for c in m.calls if c[0]=='image_manager_unload_all']
    passed()
    m,view,rows=display({'ACCENT':'2','HOME':'1','BATTERY':'2','SHORTCUT':'1'}); got=[m.nodes[m.nodes[m.nodes[r]['children'][0]]['children'][1]]['text'] for r in rows]; assert got==['Accent: Ivory','Home: Full','Battery: Icon + Percent','Theme: Minimal']; passed()
    m,view,rows=display({'BATTERY':'3'}); assert m.nodes[m.nodes[m.nodes[rows[2]]['children'][0]]['children'][1]]['text']=='Battery: Percent Inside'; passed()
    m,view,rows=display({'ACCENT':'2','HOME':'1','THEME':'1'}); got=[m.nodes[m.nodes[m.nodes[r]['children'][0]]['children'][1]]['text'] for r in rows]
    assert got==['Accent: Tidal','Home: Full','Battery: Icon','Theme: Classic'] and m.nodes[view]['children'][0]==rows[3] and m.nodes[view]['children'][2]==rows[0]; passed()
    # Home's Rockbox row (docs/boot.md#rockbox-from-home): its click saves the queue as power-off does,
    # stops the player, leaves S90play's flag and kills demo; without Rockbox on the card it says so.
    class RockboxMachine(Machine):
        def hook(self,u,address,size,unused):
            name=self.handlers.get(address,'')
            if not name.startswith('r:'): return super().hook(u,address,size,unused)
            name=name[2:].split('@')[0]; a=u.reg_read(UC_MIPS_REG_A0); self.calls.append((name,a))
            self.returns(0x2000000 if name=='fopen' else 0)
    def rockbox(card):
        m=RockboxMachine(); m.rockbox_mode=0o100755 if card else 0
        for n in ('fopen@GLIBC_2.2','fclose@GLIBC_2.2','system@GLIBC_2.0'): m.handlers[syms[n]]='r:'+n
        m.mock('save_memoryplay_info','player_stop','switch_charge_enable','navigator_to_with_context')
        assert m.call(address=payload_syms['rockbox_open'],args=(0,m.event,0,0),gap=0)==0
        return m,[c[0] for c in m.calls]
    m,names=rockbox(True)
    texts={c[0]:m.text(c[1]) for c in m.calls if c[0] in ('fopen','system')}
    assert texts=={'fopen':'/tmp/q2pod-rockbox',
                   'system':'killall checkappprocess.sh; killall -9 hciplayer; kill -9 $(cat /tmp/q2-librespot); '
                             'killall -9 librespot aplay; rm -f /tmp/q2-librespot /tmp/q2-librespot.state /tmp/q2-librespot.out /tmp/q2-librespot.download; sync; kill -9 $PPID'}
    assert [n for n in names if n in ('fclose','save_memoryplay_info','player_stop','system')]==['fclose','save_memoryplay_info','player_stop','system']; passed()
    m,names=rockbox(False)
    assert not {'fopen','player_stop','system'} & set(names)
    assert [m.text(c[1]) for c in m.calls if c[0]=='navigator_to_with_context']==['dialog/msginfo_dialog']; passed()
    # ipod_home_rockbox follows the card.
    m=Machine(); m.rockbox_mode=0; m.config['SHORTCUT']='0'
    assert m.call(address=payload_syms['ipod_home_rockbox'],args=(0,0,0,0),gap=0)==0
    m.rockbox_mode=0o100755
    assert m.call(address=payload_syms['ipod_home_rockbox'],args=(0,0,0,0),gap=0)==1; passed()
    # The wheel walks onto the new rows and Centre clicks them, as any fixed settings list.
    m,view,_=display({},sub=0)
    m.paint(view)
    for _ in range(3): m.call()
    assert m.selected(view)==3 and m.confirm()==11 and m.dispatched()[0][1]==m.nodes[m.nodes[view]['children'][3]]['children'][0]; passed()
    # PEQ's value edit has action_text "done": stock edit_on_event reports the keyboard's OK only for it (or "next").
    stock=(B/'stock-demo').read_bytes()
    def at(va,n): return next(stock[o+va-v:o+va-v+n] for _,(t,o,v,_,f,*_) in segments(stock) if t==1 and v<=va<v+f)
    assert at(0x613ee8,4)==bytes.fromhex('1cc7a524') and at(0x77c71c,5)==b'done\0'; passed()  # edit_on_event: strcmp(action_text, "done")

    # Settings rows (docs/ipod.md#settings). The real stock builders create their rows; the build
    # points the list_view layouter's vtable slot at ipod_list_layout, which normalises stock 78px
    # items, runs the stock layout (stacking modelled here: item_height, else the item's own height,
    # else default_item_height, as 0x5ea5c4 onward) and maps each row's children. Stock's
    # list_view(m=0,s=0) has no keep_invisible, so widget_get_children_for_layout (0x5ea1e4) drops a
    # hidden row: it is neither placed nor sized.
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
    def geometry(m,w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
    def settings(builder,default=SET['ROW'],sub=0):
        m=SettingsMachine(); m.layouts=0
        m.mock('list_item_create','button_create','image_create','hscroll_label_create','label_create','widget_use_style',
               'widget_set_name','image_set_draw_type','image_base_set_image','set_hscroll_label_attribute','widget_on',
               'widget_set_text_utf8','widget_set_visible','widget_move_resize')
        m.handlers[syms['widget_destroy_children']]='widget_destroy_children'
        for n in syms:  # string helpers the builders format their names with
            if n.endswith('@GLIBC_2.0') and syms[n] not in m.handlers: m.handlers[syms[n]]=n
        m.handlers[O['LIST_VIEW_LAYOUT']]='stock_list_layout'
        view=m.node('scroll_view'); lst=m.node('list_view','list_view',[view]); m.top=m.node('window','display_page' if builder=='display' else 'sysset_page',[lst])
        m.word(view+O['W_PARENT'],lst); m.word(lst+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
        m.word(lst+O['ROW_HEIGHT'],0); m.word(lst+O['LIST_DEFAULT_ITEM_HEIGHT'],default)
        m.word(lst+O['W_Y'],SET['TOP']); m.word(lst+O['W_H'],SET['ROWS']*SET['ROW'])
        m.nodes[m.wm]={'type':'window_manager','children':[m.top]}
        m.word(view+O['W_W'],375); m.word(view+O['W_H'],SET['ROWS']*SET['ROW'])
        if builder=='display':  # the payload's Accent and Home rows, after three stock-shaped ones
            m.handlers[tramp['display']]='stock_display'; m.nodes[view]['name']='scroll_view_display'; m.word(payload_syms['display_sub'],sub)
            assert m.call(address=HOOKS['systemset_display_page_init'][0],args=(m.top,5,0,0),gap=0)==0
        else:
            m.call(address=builder,args=(m.top,0,0,0),gap=0)  # learn the name it looks up
            m.nodes[view]['name']=m.text(next(c for c in m.calls if c[0]=='widget_lookup')[2])
            assert m.call(address=builder,args=(m.top,0,0,0),gap=0)==0
        return m,view
    def lay(m,view): return m.call(address=m.get(O['LIST_VIEW_LAYOUT_SLOT']),args=(0x1234,view,0,0),gap=0)
    def tree(m,view):
        return [(geometry(m,i),[(geometry(m,b),[geometry(m,c) for c in m.nodes[b]['children']]) for b in m.nodes[i]['children']])
                for i in m.nodes[view]['children']]
    # Rows at the top and bottom of the four visible slots: text bands, icons and the trailing
    # bitmaps' ink (switch_off/on span 0..50 x 13..38 of their 50px square; list_into and ticks fit
    # inside) stay clear of the rounded glass, and nothing overlaps.
    def check_row(m,item,slot):
        top=30+m.get(m.get(m.get(item+O['W_PARENT'])+O['W_PARENT'])+O['W_Y'])+slot*SET['ROW']
        for b in m.nodes[item]['children']:
            bx,by,bw,bh=geometry(m,b)
            assert (bx,by,bw,bh)==(0,0,375,geometry(m,item)[3])
            icon=label=trail=None
            for c in m.nodes[b]['children']:
                x,y,w,h=geometry(m,c); kind=m.nodes[c]['type']
                if kind=='image' and w==SET['ICON']:
                    icon=(x,y,w,h); box=(x,top+y,w,h)
                    assert (x,h)==(SET['ICON_X'],SET['ICON']) and y*2+h==bh and m.nodes[c]['draw_type']==O['IMAGE_DRAW_SCALE_DOWN']
                elif kind=='image':
                    assert w==50 and x+w==375-SET['EDGE'], (x,w); trail=x; box=(x,top+y+(h-50)//2+13,50,25)
                else:
                    label=(x,w); size=20 if h<30 else 24
                    box=(x,top+y+(h-size)//2,w,size)
                x,y,w,h=box; inset=max(corner_inset(y),corner_inset(y+h))
                assert inset<=x and x+w<=375-inset, (m.nodes[c]['type'],box,inset)
            if icon and label: assert label[0]==icon[0]+icon[2]+SET['GAP']
            elif label: assert label[0]==SET['TEXT_X']
            if label and not trail: assert label[0]+label[1]==375-SET['TEXT_X']
            if label and trail: assert label[0]+label[1]<=trail+30  # the chevron's glyph starts 20px in
    for classic in (0,1):
        CONFIG['THEME']=str(classic)
        for builder in (0x4c43e4, 0x4c0f6c, 0x4cbcc4, 0x4ccc70, 'display'):  # language, BT quality, System settings, Wi-Fi, Display
            m,view=settings(builder); items=[i for i in m.nodes[view]['children'] if m.nodes[i]['visible']]
            before=tree(m,view); assert lay(m,view)==0 and m.layouts==1
            assert m.get(m.get(view+O['W_PARENT'])+O['W_Y'])==(0 if classic else SET['TOP'])
            assert [geometry(m,i)[1] for i in items]==[SET['ROW']*k for k in range(len(items))], builder  # 78px items too
            assert all(geometry(m,i)[3]==SET['ROW'] for i in items)
            for item in items: check_row(m,item,0); check_row(m,item,SET['ROWS']-1)
            after=tree(m,view); assert after!=before
            assert lay(m,view)==0 and tree(m,view)==after, builder  # a later layout changes nothing
            passed()
    # However often Theme flips, the Accent row stays right after Theme as a whole settings row, the
    # cached pages take the new inset, the wheel and Centre reach the row after Theme, and a tap
    # selects it.
    CONFIG.clear(); m,view=settings('display',sub=1); items=m.nodes[view]['children']; theme,accent=items[0:2]  # Appearance: then Corners, Home, Battery
    button=lambda i: m.nodes[i]['children'][0]
    cached=m.node('list_view'); library=m.node('list_view')
    m.word(cached+O['LIST_DEFAULT_ITEM_HEIGHT'],SET['ROW']); m.word(cached+O['ROW_HEIGHT'],0); m.word(cached+O['W_Y'],SET['TOP'])
    m.word(library+O['W_Y'],0); m.word(library+O['LIST_DEFAULT_ITEM_HEIGHT'],72)
    previous=m.node('window','sysset_page',[cached,library]); m.nodes[m.wm]['children'].insert(0,previous)
    for classic in (1,0,1,0,1):  # each a click on Theme, from Minimal
        f,ctx=m.nodes[button(theme)]['click']; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
        assert m.get(cached+O['W_Y'])==(0 if classic else SET['TOP'])  # cached page, before any layout
        assert m.get(m.get(view+O['W_PARENT'])+O['W_Y'])==(0 if classic else SET['TOP'])
        assert m.get(library+O['W_Y'])==0
        assert lay(m,view)==0 and m.nodes[view]['children'][0:2]==[theme,accent] and m.nodes[accent].get('visible',1)==1
        assert m.get(m.get(view+O['W_PARENT'])+O['W_Y'])==(0 if classic else SET['TOP'])
        shown=[i for i in items if m.nodes[i]['visible']]; after=shown[shown.index(theme)+1]
        assert after==accent
        assert [geometry(m,i)[1] for i in shown]==[SET['ROW']*k for k in range(len(shown))]
        for i in shown: check_row(m,i,0)
        for _ in range(len(shown)): m.call(O['KEY_PREV'])
        for _ in range(shown.index(after)): m.call()
        m.paint(view); assert m.selected(view)==shown.index(after)
        assert m.confirm()==11 and m.dispatched()[0][1]==button(after)
        m.click(button(shown[1])); m.click(button(after)); assert m.selected(view)==shown.index(after)
    passed()
    # A list whose default_item_height is not SET_ROW (local pages, Home) keeps its rows as built.
    m,view=settings(0x4cbcc4,default=72); before=tree(m,view); lay(m,view); after=tree(m,view)
    assert [r[1] for r in after]==[r[1] for r in before] and all(r[0][3]==72 for r in after); passed()
    # A 78px item some other builder made (no stock settings button) keeps its height and children.
    m,view=settings(0x4cbcc4); odd=m.node('list_item'); other=m.node('button')
    m.nodes[odd]['children']=[other]; m.word(odd+O['W_H'],78); m.word(odd+O['W_W'],0)
    for k,v in (('W_X',8),('W_Y',0),('W_W',359),('W_H',70)): m.word(other+O[k],v)
    m.nodes[view]['children'].append(odd); lay(m,view)
    assert geometry(m,odd)[3]==78 and geometry(m,other)==(8,0,359,70); passed()
    # The wheel walks the laid-out rows, the bar spans the whole 68px row, the list
    # scrolls to keep the row in view, and Centre and a tap reach the same button.
    m,view=settings(0x4cbcc4); lay(m,view); items=m.nodes[view]['children']
    buttons=[m.nodes[i]['children'][0] for i in items]
    m.paint(view); assert m.selected(view)==0
    for k in range(1,len(items)):
        m.call(); m.paint(view); i=k; assert m.selected(view)==i
        y=geometry(m,items[i])[1]-signed(m.get(view+O['SCROLL_Y']))
        assert 0<=y and y+SET['ROW']<=SET['ROWS']*SET['ROW'], (k,y)
        assert not m.bands and m.sel()[2:]==(375,SET['ROW'])
    assert m.confirm()==11 and m.dispatched()[0][1]==buttons[-1]; passed()
    m.click(buttons[2]); assert m.selected(view)==2; passed()

    # The payload's own accent drawing follows the preset, live: the bar, Now Playing's fill and
    # the fill a scrub restores.
    m,view,rows=display({'ACCENT':'2'}); m.paint(view)
    assert not m.bands and m.rounded[-1]['color']==color_t(0xeeeeec); passed()
    m,view,rows=display({'ACCENT':'2','THEME':'1'}); m.paint(view)
    assert [m.bands[i][4] for i in (0,1,47)]==[color_t(ACCENTS[2][i]) for i in (4,0,1)]; passed()
    f,ctx=m.handler(m.nodes[rows[0]]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    m.paint(view); assert [m.bands[i][4] for i in (0,1,47)]==[color_t(ACCENTS[3][i]) for i in (4,0,1)]; passed()
    CONFIG.clear(); CONFIG.update(ACCENT='3'); m=scrub_page()
    assert m.nodes[m.slider][FG]==FILL_HI; passed()  # Minimal: no accent
    CONFIG.update(THEME='1'); m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
    m.slider=m.node('slider','slider_play',max=225,value=100)
    m.win=m.top=m.node('window','playing_page',[m.slider,m.node('label','label_playtime')]); m.word(m.win+O['W_PARENT'],m.wm)
    m.call(address=playing,args=(m.win,7,0,0),gap=0)
    assert m.nodes[m.slider][FG]==signed(color_t(ACCENTS[3][2])); centre(m)
    assert m.nodes[m.slider][FG]==-1; centre(m,50); assert m.nodes[m.slider][FG]==signed(color_t(ACCENTS[3][2]))
    assert {m.nodes[m.slider][f'style:{s}:fg_color'] for s in ('normal','pressed','over','focused')}=={m.nodes[m.slider][FG]}; passed()  # a touch keeps the accent

    # Home at init: Split halves the screen in both themes and shows the art; Full widens the list
    # to the screen and its rows' tap targets to HOME_FULL_ROW (Classic: HOME_CLASSIC_ROW) and hides it.
    for config,listw,roww,shown in (({'HOME':'1'},375,O['HOME_FULL_ROW'],0),({},O['HOME_SPLIT_W'],O['HOME_SPLIT_W'],1),
                                    ({'HOME':'1','THEME':'1'},375,O['HOME_CLASSIC_ROW'],0),({'THEME':'1'},O['HOME_SPLIT_W'],O['HOME_SPLIT_W'],1)):
        CONFIG.clear(); CONFIG.update(config); m=CoverflowMachine(); m.open()
        rowsw=[m.get(w+O['W_W']) for w in [m.list,*m.imgs]]
        assert rowsw==[listw]+[roww]*7 and m.nodes[m.art].get('visible',1)==shown,(config,rowsw); passed()
    Machine.hook=orig_hook; CONFIG.clear()

# Wheel precision (docs/internals.md, Wheel movement): every tick moves, the list ends, and Key Tone
# moved from the press to the row change.
KEYDOWN=HOOKS['on_wm_keydown_before_fun'][0]
NEXT,PREV,CENTER,RETURN=O['KEY_NEXT'],O['KEY_PREV'],O['KEY_CENTER'],O['KEY_RETURN']
TONE=syms['g_keytone_flag']; SOUND_LATCH=0xa37c90; KEY_LATCH=0xa37c89  # stock: one click a press; wheel lockout
def audible(m,tone=1):
    """Key Tone as on the device: the real stock buzzeer_switch runs, and only the system() command it
    hands the MCU, the hardware boundary, is mocked."""
    m.handlers.pop(syms['buzzeer_switch'],None); m.mock('system@GLIBC_2.0'); m.byte(TONE,tone)
    m.handlers[syms['pthread_create@GLIBC_2.2']]='thread:run'  # the click's worker, run at once
    return m
def buzzes(m): return sum(c[0]=='system@GLIBC_2.0' and m.text(c[1])=='cmd_mcu write_str buzzer' for c in m.calls)
def press(m,key=NEXT,gap=1000):
    """The window manager's key-down-before through the stock entry (iPod: the hook), leaving the stock
    latches as stock does. Returns its buzzer commands; the Key Tone byte must come back as it was."""
    tone=bytes(m.u.mem_read(TONE,1))
    m.down=m.call(key,address=KEYDOWN,event_type=O['EVT_KEY_DOWN_BEFORE'],gap=gap,debounce=True)
    assert bytes(m.u.mem_read(TONE,1))==tone
    return buzzes(m)
def lift(m,key=NEXT):
    """Its key-up-before, at once; m.ret is the hook's result."""
    tone=bytes(m.u.mem_read(TONE,1))
    m.ret=m.call(key,event_type=O['EVT_KEY_UP_BEFORE'],gap=0,debounce=True)
    assert bytes(m.u.mem_read(TONE,1))==tone and m.u.mem_read(SOUND_LATCH,1)==b'\0'
    return buzzes(m)
def tick(m,key=NEXT,gap=1000): return press(m,key,gap)+lift(m,key)
def debounced(m):
    """Stock's wheel lockout after a button runs out: on_wm_timer_setup_state counts it down."""
    for _ in range(8): assert m.call(address=syms['on_wm_timer_setup_state'],args=(0,0,0,0),gap=0,debounce=True)==8
    assert m.u.mem_read(KEY_LATCH,1)==b'\0'
PLAY_STATUS,OUTPUT_WAY=0xa3beac,0xa3beb0  # mclGetPlayStatus, mclGetOutputWay: their stock leaves read these
def routed(m,status=3,way=0,po=0,bal=0):
    """Key Tone's routing (ringnav_buzzer): the stock leaves run on these play status, output way and jacks."""
    for n in ('mclGetPlayStatus','mclGetOutputWay'): m.handlers.pop(syms[n],None)
    m.word(PLAY_STATUS,status); m.word(OUTPUT_WAY,way); m.byte(syms['g_po_status'],po); m.byte(syms['g_bal_status'],bal)
    return m
# Every Key Tone click (stock's three and ringnav()'s) goes through buzzeer_switch: with nothing plugged
# in, the buzzer as stock, playing or not; with either jack plugged in or a Bluetooth (1) or USB (2)
# output, none; Key Tone off, none.
for kw,want in (({'status':1},1),({},1),({'status':2},1),({'po':1},0),({'bal':1},0),({'way':1},0),
                ({'way':2},0),({'status':2,'po':1},0)):
    m=routed(audible(Machine()),**kw); m.call(address=syms['buzzeer_switch'],args=(1,0,0,0))
    assert buzzes(m)==want,kw
m=routed(audible(Machine(),tone=0)); m.call(address=syms['buzzeer_switch'],args=(1,0,0,0)); assert not buzzes(m); passed()
# A reboot keeps the place (playback.c ringnav_reboot): Restart (device_reboot) and the firmware update's
# dialog, which stops the player before it flashes, save as power-off does (save_memoryplay_info, which
# ringnav_savequeue hooks) before stock runs, with the dialog's arguments; a stopped player (1) saves nothing.
for entry,stock in (('device_reboot','reboot'),('dialog_checkfw_dialog_init','checkfw')):
    for status in (1,2,3):
        m=Machine(); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(PLAY_STATUS,status)
        m.handlers[payload_syms[f'stock_{stock}_trampoline']]='stock_'+stock
        m.call(address=syms[entry],args=(0x1234,0x5678,0,0),gap=0)
        got=[c for c in m.calls if c[0] in ('save_memoryplay_info','stock_'+stock)]
        assert [c[0] for c in got]==['save_memoryplay_info'][:status!=1]+['stock_'+stock],(entry,status)
        assert stock!='checkfw' or got[-1][1:3]==(0x1234,0x5678)
passed()

if variant=='ipod':
    def long_list(n=40,m=None):
        m=m or Machine(); w,es=m.page_list(n,extent=n*48,name='allmusic_page'); m.paint(w); return m,w,es
    def turn(m,w,gaps,key=NEXT):
        got=[]
        for gap in gaps: assert m.call(key,gap=gap)==11; got.append(m.selected(w))
        return got
    # 1. Every tick moves, however soon after the last (the wheel's ticks are real travel).
    # Short and long lists and virtual tables, from row 0.
    for table in (False,True):
        for count in (16,40):
            for gaps in ((1000,80),(1000,140),(1000,100,100),(1000,50,100),(1000,100,141,100,100)):
                m=Machine()
                if table:
                    w,rs,es=m.table_page(n=4,name='playerqueue_page',rebind=True); m.word(w+O['TABLE_ROWS'],count)
                else: w,es=m.page_list(count,extent=count*48)
                m.paint(w); assert turn(m,w,gaps)==list(range(1,len(gaps)+1)),(table,count,gaps)
                passed()
    # Home as built: the same, its ends bump then wrap after a pause, and Centre opens the row that is highlighted.
    m=Machine(); view,imgs=home_list(m); click_target(m,imgs[2]); m.paint(view)
    assert turn(m,view,(1000,100,100,1000,100,100,100,400))==[1,2,3,4,5,6,6,0]
    assert m.get(view+O['SCROLL_Y'])==0 and turn(m,view,(1000,400,100,100),PREV)==[0,6,5,4]
    assert m.confirm()==11 and m.dispatched()[0][1]==imgs[4]; passed()
    # A BUTTONS dialog: the same between its buttons, and nothing scrolls.
    m=Machine(); d=m.node('dialog','confirminfo_dialog'); m.word(d+O['W_PARENT'],m.wm); m.top=d
    buttons=[m.entry(d,220) for _ in range(3)]; m.nodes[d]['children']=buttons
    for b,x in zip(buttons,(20,147,274)): m.word(b+O['W_X'],x); m.word(b+O['W_W'],80); m.word(b+O['W_H'],80)
    m.paint(d); got=[]
    for gap in (1000,100,100): assert m.call(gap=gap)==11 and not m.moved(); got.append(m.selected(d))
    assert got==[1,2,2] and m.confirm()==11 and m.dispatched()[0][1]==buttons[2]; passed()

    # 4. List ends. Reaching an end ends the run, and every
    # tick against an end bumps and re-arms the stop, so ticks 100 or 200ms apart never wrap, and only a tick
    # EDGE_PAUSE_MS after the last one does, once. The tick after the wrap is a first one.
    def ring(name='playlist_page'):
        m=Machine(); w,es=m.page_list(6,height=96,extent=288,name=name); m.paint(w)
        assert turn(m,w,(1000,)*5)==[1,2,3,4,5]
        return m,w,es
    m,w,es=ring()
    for gap in (100,100,200,200,O['EDGE_PAUSE_MS']-1):
        assert m.call(gap=gap)==11 and m.selected(w)==5 and m.get(w+O['SCROLL_Y'])==192,gap
        m.paint(w,gap=0); assert m.sel()==(0,42,240,48)  # bumped against the end
    assert m.call(gap=O['EDGE_PAUSE_MS'])==11 and m.selected(w)==0 and m.get(w+O['SCROLL_Y'])==0
    m.paint(w,gap=0); assert m.sel()==(0,0,240,48)
    assert turn(m,w,(100,100,100))==[1,2,3] and turn(m,w,(100,100,100),PREV)==[2,1,0]
    assert m.call(PREV,gap=100)==11 and m.selected(w)==0
    m.paint(w,gap=0); assert m.sel()==(0,6,240,48)   # bumped down at the top, on the first tick
    assert m.call(PREV,gap=O['EDGE_PAUSE_MS'])==11 and m.selected(w)==5; passed()
    # Settings keep hard ends: no bump and no wrap, however long the pause.
    m,w,es=ring('display_page')
    for gap in (100,O['EDGE_PAUSE_MS'],1000):
        assert m.call(gap=gap)==11 and m.selected(w)==5
        m.paint(w,gap=0); assert m.sel()==(0,48,240,48)
    passed()

    # 5. Key Tone follows the row. Each tick is the pair the window manager delivers: key-down-before
    # through the hook and the stock callback, then key-up-before. The count is the buzzer commands
    # the real buzzeer_switch issues. On Home as built, four ticks leave rows 1, 2, 3, 4 and click
    # once each; stock's one-click-per-press latch is set by the press and cleared by the
    # release, as without the hook.
    def home_machine(tone=1):
        m=audible(Machine(),tone); view,imgs=home_list(m); click_target(m,imgs[2]); m.paint(view)
        return m,view
    m,view=home_machine(); got=[]
    for gap in (1000,100,100,1000):
        n=press(m,gap=gap); assert n==0 and m.down==0 and m.u.mem_read(SOUND_LATCH,1)==b'\x01'
        got.append((n+lift(m),m.selected(view))); assert m.ret==11
    assert got==[(1,1),(1,2),(1,3),(1,4)],got; passed()
    # Slow ticks click on every row; Home's end bumps silently, then the wrap after a pause clicks;
    # a repaint and a long wait add nothing.
    m,view=home_machine()
    assert [tick(m) for _ in range(6)]==[1]*6 and m.selected(view)==6
    assert [tick(m,gap=gap) for gap in (1000,50,400,100)]==[0,0,1,1] and m.selected(view)==1
    m.paint(view); assert not buzzes(m)
    m.advance(10000); assert not buzzes(m); passed()
    # buzzeer_switch also runs when it is silent, so its calls are not the count: stock always calls
    # it on the press, with Key Tone held off; a row change adds the release's call.
    m,view=home_machine(); seen=[]
    m.u.hook_add(UC_HOOK_CODE,lambda u,a,size,x: seen.append(u.mem_read(TONE,1)[0]),begin=syms['buzzeer_switch'],end=syms['buzzeer_switch'])
    assert tick(m)==1 and seen==[0,1] and tick(m,gap=100)==1 and seen==[0,1,0,1]; passed()
    # An accelerated step is one click however many rows it takes (100ms ticks step
    # 1,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8 rows).
    m,w,es=long_list(100,audible(Machine()))
    assert [tick(m,gap=100) for _ in range(16)]==[1]*16 and m.selected(w)==65; passed()
    # An end bump is silent and the wrap clicks once; the tick after it is a first one and clicks.
    m=audible(Machine()); w,es=m.page_list(6,height=96,extent=288,name='playlist_page'); m.paint(w)
    assert sum(tick(m) for _ in range(5))==5 and m.selected(w)==5
    assert [tick(m,gap=gap) for gap in (100,200,200,O['EDGE_PAUSE_MS']-1)]==[0,0,0,0] and m.selected(w)==5
    assert tick(m,gap=O['EDGE_PAUSE_MS'])==1 and m.selected(w)==0
    assert tick(m,gap=100)==1 and m.selected(w)==1; passed()
    # Rejected input is silent: a finger down, a window animation (held, its tick plays after),
    # stock's wheel lockout after a button (until its countdown ends), and a turn while the screen
    # is locked still clicks as stock.
    m,w,es=long_list(8,audible(Machine()))
    for field in ('pressed','animating'):
        setattr(m,field,1); assert tick(m)==0 and m.ret==11 and m.selected(w)==0; setattr(m,field,0)
    m.advance(O['HOLD_POLL_MS']); assert m.selected(w)==1 and not m.timers  # the slide's tick plays after it
    assert press(m,RETURN)==1 and lift(m,RETURN)==0 and m.ret==0 and m.u.mem_read(KEY_LATCH,1)==b'\x08'
    assert tick(m)==0 and m.ret==11 and m.selected(w)==1
    debounced(m); assert tick(m)==1 and m.selected(w)==2; passed()
    # Restoring the selection is not a step: a recreated page recalls its row on paint silently.
    m,w,es=long_list(20,audible(Machine()))
    assert [tick(m) for _ in range(6)]==[1]*6 and m.selected(w)==6
    w2,es2=m.page_list(20,extent=960,name='allmusic_page'); m.paint(w2)
    assert m.selected(w2)==6 and not buzzes(m); passed()
    # Centre is a button: its press clicks as stock, and the delayed confirmation adds nothing.
    m,w,es=long_list(8,audible(Machine()))
    assert press(m,CENTER)==1 and lift(m,CENTER)==0 and m.ret==11
    m.advance(O['DOUBLE_CLICK_MS']); assert m.clicks==[es[0]] and not buzzes(m); passed()

    # 6. The Key Tone setting. Off: everything above is silent and the rows still move. The click
    # uses the setting at the moment of the step, and the byte is never left changed: press() and
    # lift() check it after every call, whatever its value.
    m,view=home_machine(tone=0)
    assert [tick(m) for _ in range(4)]==[0,0,0,0] and m.selected(view)==4 and m.u.mem_read(TONE,1)==b'\0'; passed()
    m,view=home_machine()
    assert press(m)==0; m.byte(TONE,0); assert lift(m)==0 and m.selected(view)==1      # switched off in between
    assert press(m)==0; m.byte(TONE,1); assert lift(m)==1 and m.selected(view)==2      # and back on
    m.byte(TONE,0x5a); assert tick(m)==1 and m.u.mem_read(TONE,1)==b'\x5a'; passed()
    # The paths outside row navigation sound exactly as the stock binary does, press for press,
    # and do what they did: the volume, a scrub, the carousels, the pixel-scroll fallback, the
    # buttons, a held key, a double press, and the wheel with the screen off or locked.
    def stock(keys,**flags):
        s=audible(Machine(patched=False))
        for flag,value in flags.items(): s.byte(syms[flag],value)
        out=[]
        for key in keys:
            out.append(press(s,key)); s.call(key,address=HOOKS['on_wm_keyup_before_fun'][0],gap=0,debounce=True)
        return out
    m=audible(Machine()); m.page('playing_page')
    assert [tick(m,key) for key in (NEXT,PREV,NEXT)]==stock((NEXT,PREV,NEXT))==[1,1,1] and m.ret==0; passed()
    m=audible(scrub_page()); centre(m); debounced(m)  # the double press's stock wheel lockout runs out
    assert [tick(m,gap=100) for _ in range(3)]==stock((NEXT,)*3)==[1,1,1] and m.nodes[m.slider]['value']==130; passed()
    m=audible(CoverflowMachine()); m.open()
    assert [tick(m,gap=20) for _ in range(2)]==[1,1] and slide(m,m.slide)[3]==120; passed()
    m=audible(Machine()); m.page('sysset_page','slide_menu')
    assert tick(m)==1 and [c[0] for c in m.moved()]==['slide_menu_scroll_to_next']; passed()
    m=audible(Machine()); w=m.page(t='table_client')
    assert [tick(m,gap=100) for _ in range(3)]==[1,1,1] and m.get(w+O['TABLE_TOP'])==288; passed()
    keys=(CENTER,RETURN,O['KEY_PLAY'],222,223)
    m,w,es=long_list(8,audible(Machine()))
    assert [tick(m,key) for key in keys]==stock(keys)==[1]*5 and m.selected(w)==0; passed()
    # Either jack or Bluetooth in: the buttons and a row change are silent.
    for kw in ({'po':1},{'bal':1},{'way':1}):
        m,w,es=long_list(8,routed(audible(Machine()),**kw))
        assert tick(m)==0 and m.selected(w)==1 and [tick(m,key) for key in keys]==[0]*5
        assert m.u.mem_read(TONE,1)==b'\x01'
    passed()
    # Music playing with nothing plugged in: the speaker still clicks.
    m,w,es=long_list(8,routed(audible(Machine()),status=2))
    assert tick(m)==1 and m.selected(w)==1 and [tick(m,key) for key in keys]==[1]*5; passed()
    # A held key clicks once: stock's latch holds through its repeats and the long-press event.
    m,w,es=long_list(8,audible(long_machine()))
    assert [press(m,RETURN),press(m,RETURN,gap=500)]==[1,0]; long_return(m); assert not buzzes(m) and lift(m,RETURN)==0
    assert len(destinations(m))==0 and press(m,RETURN)==1; passed()
    m,w,es=long_list(3,audible(Machine()))
    assert press(m,CENTER)==1 and m.release()==11 and press(m,CENTER,gap=100)==1 and m.release()==0 and m.screens==[0]; passed()
    for flags in (dict(g_backlight_status=0),dict(g_backlight_status=0,g_keylock_flag=1,g_keylock_mode=1),
                  dict(g_lockscreen_pageflag=1),dict(g_testmode_flag=1)):
        m,w,es=long_list(8,audible(Machine()))
        for flag,value in flags.items(): m.byte(syms[flag],value)
        locked='g_keylock_flag' in flags
        assert [tick(m),tick(m,CENTER)]==stock((NEXT,CENTER),**flags)==[0 if locked else 1,1],flags
        assert m.selected(w)==0; passed()
    # Between a press and its release the page may change. List to volume: the silenced press is
    # spent, the volume step is stock's, and the next press there clicks. Volume to list: stock
    # already clicked, so the row change adds none.
    m,w,es=long_list(8,audible(Machine())); lst=m.top
    assert press(m)==0; m.page('playing_page'); assert lift(m)==0 and m.ret==0 and m.selected(w)==0
    assert tick(m)==1 and m.ret==0
    assert press(m)==1; m.top=lst; assert lift(m)==0 and m.ret==11 and m.selected(w)==1
    assert tick(m)==1 and m.selected(w)==2; passed()
    # A recreated page between the two, or a surface rebuilt at the same address: one click for the
    # one row change on the live list.
    m,w,es=long_list(8,audible(Machine()))
    assert press(m)==0; w2,es2=m.page_list(8,extent=8*48,name='allmusic_page'); m.paint(w2,gap=0)
    assert lift(m)==1 and m.selected(w2)==1 and m.selected(w)==0
    wiped(m,w2); m.paint(w2,gap=0); assert m.selected(w2)==1 and tick(m)==1 and m.selected(w2)==2; passed()
    # A dropped release (AWTK aborts pressed keys when windows change) leaves nothing behind. The
    # next pair on the list clicks once. A new press elsewhere is stock's again: stock's own latch,
    # still set by the dropped press, skips one click there, then the volume clicks as before. A
    # release without its press moves the row silently, and a button's press takes the ownership
    # back (stock's lockout then rejects that wheel release). Key Tone is never left off.
    m,w,es=long_list(8,audible(Machine())); lst=m.top
    assert press(m)==0 and tick(m)==1 and m.selected(w)==1
    assert press(m)==0; m.page('playing_page'); assert [tick(m),tick(m)]==[0,1] and m.ret==0
    m.top=lst; assert lift(m)==0 and m.ret==11 and m.selected(w)==2
    assert press(m)==0 and press(m,RETURN)==0 and lift(m)==0 and m.ret==11 and m.selected(w)==2
    assert lift(m,RETURN)==0 and press(m,RETURN)==1 and lift(m,RETURN)==0
    debounced(m); assert tick(m)==1 and m.selected(w)==3
    m.advance(10000); assert not buzzes(m) and m.u.mem_read(TONE,1)==b'\x01'; passed()
    CONFIG.clear()
else:
    # Outside Coverflow, Stock's key-down hook keeps every press clicking as the stock binary
    # does whether or not the row moves; release adds nothing, every tick steps a row, no setting is read.
    m=audible(Machine()); w,es=m.page_list(3,name='display_page'); m.paint(w)
    s=audible(Machine(patched=False)); got=[]
    for _ in range(4):
        got.append((press(m),lift(m),m.selected(w)))
        assert press(s)==got[-1][0]; s.call(address=HOOKS['on_wm_keyup_before_fun'][0],gap=0,debounce=True)
    assert got==[(1,0,1),(1,0,2),(1,0,2),(1,0,2)] and not m.config_reads; passed()

def fnv(s,h=2166136261):
    for c in s.encode(): h=((h^c)*16777619)&0xffffffff
    return (h*16777619)&0xffffffff

# Shuffle and Most Played: after stock's 11 Local Music rows, more in the same widgets and styles,
# moved first. The Shuffle row opens shuffle_page; its Shuffle Songs row saves shuffle as the
# play-mode setting does and plays every song (library class 0xf001) from a random track, leaving the staging deque
# as it was. Picks leave the page open; an empty library only says so. Most Played plays the
# counted songs, most played first, and leaves the play mode alone.
class ShuffleMachine(CoverflowMachine):
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
m=ShuffleMachine(); stock=[m.node('list_item') for _ in range(11)]
view=m.node('scroll_view','scroll_view_localmusic',stock); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0
assert m.calls[0][:3]==('stock_localmusic',m.top,5)
# Library order: Shuffle; Artist, Album, All Songs, Genre, Era, Playlist, Ratings (in My Fav's place, hidden);
# Recently Added, Recent, Most Played, Frequent, Hi-Res, Long Songs; iPod's Games; then Update Local Music, last
# (stock rows by get_localmusic_showinfo index).
kids=m.nodes[view]['children']; row,era,rated,top,long=kids[0],kids[5],kids[7],kids[11],kids[14]; GAMES=variant=='ipod'  # its Games row
assert [kids[i] for i in (*range(1,5),6,8,9,10,12,13,15+GAMES)]==[stock[i] for i in (3,2,1,4,10,6,9,8,7,5,0)] and m.nodes[row]['style']=='s_listitem_black'
icon,label,into=m.nodes[m.nodes[era]['children'][0]]['children']
assert m.nodes[icon]['image']=='local_era' and m.nodes[label]['text']=='Era' and m.nodes[into]['image']=='list_into'
if GAMES:
    icon,label,into=m.nodes[m.nodes[kids[15]]['children'][0]]['children']
    assert m.nodes[icon]['image']=='local_games' and m.nodes[label]['text']=='Games' and len(kids)==17
icon,label,into=m.nodes[m.nodes[long]['children'][0]]['children']
assert m.nodes[icon]['image']=='local_longsongs' and m.nodes[label]['text']=='Long Songs'
assert m.nodes[stock[6]]['visible']==0 and all(m.nodes[k].get('visible',1) for k in kids if k!=stock[6])
icon,label,into=m.nodes[m.nodes[top]['children'][0]]['children']
assert m.nodes[icon]['image']=='local_frequentplay' and m.nodes[label]['text']=='Most Played'
icon,label,into=m.nodes[m.nodes[rated]['children'][0]]['children']
assert m.nodes[icon]['image']=='local_fav' and m.nodes[label]['text']=='Ratings' and m.nodes[into]['image']=='list_into'
# The Shuffle row: the local_shuffle icon, its label and stock's list_into trailing image, as the
# stock Local Music categories place them. Its button has no name, so stock's row click ignores it.
button=m.nodes[row]['children'][0]; icon,label,into=m.nodes[button]['children']
# Compare with the shipped constructor, whose iPod body and icon x differ from stock's.
demo=(B/'demo').read_bytes()
library_body,library_icon_w,library_icon_x,library_draw_type=[struct.unpack_from('<I',demo,fileoff(demo,a))[0]&0xffff
                                                           for a in (0x524820,0x524938,0x524944,0x524970)]
assert (library_body,library_icon_w,library_icon_x,library_draw_type)==((68,52,8,1) if variant=='ipod' else (70,52,10,1))
assert m.nodes[button]['style']=='s_btn_listitem' and [m.get(button+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[20,0,335,library_body]
assert m.nodes[icon]['image']=='local_shuffle'
def library_geometry(m,k):
    b=m.nodes[k]['children'][0]; icon=m.nodes[b]['children'][0]
    assert [m.get(icon+O[f]) for f in ('W_X','W_Y','W_W','W_H')]==[library_icon_x,0,library_icon_w,library_body] and m.nodes[icon]['draw_type']==library_draw_type
    assert all(m.get(c+O['W_H'])==library_body for c in [b,*m.nodes[b]['children']])
for k in (row,rated,top,long): library_geometry(m,k)
assert m.nodes[label]['style']=='s_scrlabel_white24l' and m.nodes[label]['text']=='Shuffle' and not m.nodes[button].get('name')
assert m.nodes[into]['image']=='list_into' and [m.get(into+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[282,0,50,library_body]
f,ctx=m.handler(button,O['EVT_CLICK'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
# It opens the menu: a black shuffle_page holding the three 48px rows and the grey note under them.
# The note is no tap target, so the wheel never lands on it. A second press opens nothing new.
page=m.top; assert m.nodes[page]['name']=='shuffle_page' and m.nodes[page]['style:normal:bg_color']==-0x1000000
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.top==page
m.page=page; menu=m.find('scroll_view'); rows=m.nodes[menu]['children']
def caption(r): return m.nodes[m.nodes[r]['children'][0]]['text']
assert [m.get(r+O['W_H']) for r in rows]==[48]*3 and [caption(r) for r in rows]==['Shuffle Songs','Shuffle Albums','Shuffle Folders']
assert m.texts()==['Shuffle']+[caption(r) for r in rows]+['Albums and folders take a moment to shuffle']
note=m.nodes[page]['children'][-1]
assert m.nodes[note]['type']=='hscroll_label' and m.nodes[note]['style:normal:text_color']==-0x555556 and m.nodes[note]['style:normal:font_size']==16 and not m.nodes[note].get('handlers')
# Shuffle Songs from the menu: the stock play-mode save, a random start, the staging deque kept.
def called(n): return [c[1:3] for c in m.calls if c[0]==n]
f0,ctx0=m.handler(rows[0],O['EVT_CLICK']); m.handlers[payload_syms['playback_shuffle']]='stock_playback_shuffle'  # a payload call: no t9
assert m.call(address=f0,args=(ctx0,m.event,0,0),gap=0)==0
assert called('config_playmode')==[(2,1)] and called('stock_playback_shuffle') and len(m.plays)==1 and m.plays[0][0]=='playing_page' and m.plays[0][2] in (0,1) and m.plays[0][3:]==(0xf001,2)
assert called('getAllMusic')==[(3,1)] and m.get(payload_syms['library_music'])==0
assert m.names(m.get(syms['tools_pdeq_directory']))==['staged'] and not m.toasts and m.top==page; passed()
m.found=[]; m.plays=[]; m.calls=[]
assert m.call(address=f0,args=(ctx0,m.event,0,0),gap=0)==0
assert not called('config_playmode') and not m.plays and m.toasts[-1][0]=='dialog/msginfo_dialog' and m.toasts[-1][3]=='Update Local Music first' and m.top==page; passed()
# The other two rows queue and group the whole library, then play from the first shuffled group;
# the same handler runs with the row's index, and the page stays for another pick.
m.found=[m.song('T1'),m.song('T2')]; m.toasts=[]
for i in (1,2):
    m.plays=[]; m.calls=[]
    f2,ctx2=m.handler(rows[i],O['EVT_CLICK']); assert f2==f0 and ctx2==i
    assert m.call(address=f2,args=(ctx2,m.event,0,0),gap=0)==0 and m.top==page
    assert called('getAllMusic')==[(3,1)] and m.get(payload_syms['library_music'])==0
    assert m.plays and m.plays[-1][0]=='playing_page' and signed(m.plays[-1][3])==0xf001 and not m.toasts; passed()
# Return leaves the menu; a later press opens a fresh one. (key()'s advance clears calls, so the
# navigator_back check drives the page's own handler.)
m.plays=[]
assert m.key()==11
fk,ck=m.handler(page,O['EVT_KEY_UP'])
ev=m.alloc(0x40); m.word(ev,O['EVT_KEY_UP']); m.word(ev+O['EVENT_KEY'],O['KEY_RETURN'])
assert m.call(address=fk,args=(ck,ev,0,0),gap=0)==11 and [c[0] for c in m.calls].count('navigator_back')==1
m.close(); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.top!=page and m.nodes[m.top]['name']=='shuffle_page'; passed()
def home_settings(full,classic=0):
        # Home's Rockbox row shows only while the card has Rockbox: hidden, it moves after Settings, so the
        # list's layout leaves no gap; it comes back after Folder once the card has it.
        CONFIG['THEME']=str(classic); CONFIG['SHORTCUT']='0'; CONFIG['HOME']=str(full); m=ShuffleMachine(); del CONFIG['THEME']; del CONFIG['SHORTCUT']; del CONFIG['HOME']; m.rockbox_mode=0; view,imgs=home_list(m)
        assert m.call(address=home_hook[0],args=(m.top,0,0,0),gap=0)==0
        btn=named(m,m.top,'btn_rockbox'); order=lambda: [m.nodes[r]['name'][4:] for r in m.nodes[view]['children']]
        assert order()==['playing','localmusic','coverflow','folder','stream','settings','rockbox'] and not m.nodes[btn]['visible']
        assert m.nodes[btn]['sensitive']==0  # hidden rows keep their taps: Streaming's would launch Rockbox
        m.rockbox_mode=0o100755; m.call(address=payload_syms['coverflow_home_layout'],args=(0,0,0,0),gap=0)
        assert order()==HOME_ROWS and m.nodes[btn]['visible'] and m.nodes[btn]['sensitive']==1; passed()
        # Its click and Settings' are bound at Home init. Settings slides the Settings list, stock's
        # Playback and System setting rows (stock binds them), into the list's place from the right,
        # the art staying; the wheel walks it, and Return slides the list back from the left with
        # its row kept.
        assert m.handler(named(m,m.top,'img_rockbox'),O['EVT_CLICK'])[0]==payload_syms['home_shortcut_open']
        m.mock('widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start')
        lst,sets,art=(named(m,m.top,n) for n in ('list_view_home','list_view_homeset','img_homeart'))
        sview=named(m,m.top,'scroll_view_homeset'); assert not m.nodes[sets]['visible']
        assert m.get(lst+O['W_Y'])==m.get(sets+O['W_Y'])==(0 if classic else O['HOME_TOP'])
        if classic:
            png=subprocess.check_output(['unsquashfs','-cat',str(B/'rootfs.squashfs'),
                                         'release/assets/default/raw/images/xx/list_into.png'])
            rgba=imagemagick('png:-','-depth','8','rgba:-',data=png)
            ink=[(i//4%50,i//4//50) for i in range(0,len(rgba),4) if rgba[i+3]]
        def arrow(v):
            if not classic: return
            m.clip=(0,0,375,320); m.paint(v)
            roww=O['HOME_CLASSIC_ROW'] if full else O['HOME_SPLIT_W']
            x,y,_=m.icons[0]
            shift=O['HOME_FULL_CHEVRON_SHIFT'] if full else O['HOME_CHEVRON_SHIFT']
            assert x==roww-O['CHEVRON_W']+25+shift and y==m.sel()[1]+HOME_ROW//2
            # Check the shipped glyph's ink, including its tapered ends, against the glass.
            for ix,iy in ink:
                inset=corner_inset(30+y-25+iy)
                assert inset<=x-25+ix<375-inset
        arrow(view)
        assert m.nodes[sets].get('sensitive')==0  # hidden rows take no taps on the top level
        for img in (named(m,m.top,'img_playset'),named(m,m.top,'img_sysset')): click_target(m,img)
        m.paint(view)
        for _ in range(6): m.call()
        assert m.selected(view)==6
        arrow(view)
        m.calls=[]; f,ctx=m.handler(named(m,m.top,'img_settings'),O['EVT_CLICK'])
        assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
        assert m.nodes[sets]['visible'] and not m.nodes[lst]['visible'] and m.nodes[art]['visible']==(not full) and m.nodes[sets]['animated']=='x'
        assert m.nodes[sets]['sensitive']==1 and m.nodes[lst]['sensitive']==0
        assert [c[1:3] for c in m.calls if c[0]=='widget_move_resize'][:1]==[(sets,375 if full else O['HOME_SPLIT_W'])]; passed()
        assert next(c[2] for c in m.calls if c[0]=='widget_animator_prop_create')==O['PAGE_SLIDE_MS']==120
        m.advance(119); assert signed(m.get(sets+O['W_X']))>0
        m.advance(1); assert m.get(sets+O['W_X'])==0 and not m.props
        m.paint(sview); arrow(sview); assert m.selected(sview)==0 and m.call()==11 and m.selected(sview)==1; arrow(sview); passed()
        m.calls=[]; assert m.call(O['KEY_RETURN'])==11
        assert m.nodes[lst]['visible'] and not m.nodes[sets]['visible'] and m.nodes[lst]['animated']=='x'
        assert m.nodes[lst]['sensitive']==1 and m.nodes[sets]['sensitive']==0
        assert [c[1:3] for c in m.calls if c[0]=='widget_move_resize'][:1]==[(lst,-(375 if full else O['HOME_SPLIT_W'])&0xffffffff)]
        assert next(c[2] for c in m.calls if c[0]=='widget_animator_prop_create')==120
        m.advance(120); assert m.get(lst+O['W_X'])==0 and not m.props
        m.paint(view); assert m.selected(view)==6; passed()
        # Return on Home's main list selects Now Playing; Return there opens it without starting a track.
        m.calls=[]; assert m.call(O['KEY_RETURN'])==11 and m.selected(view)==0 and not destinations(m)
        m.calls=[]; assert m.call(O['KEY_RETURN'])==11 and m.selected(view)==0
        dest=destinations(m); assert len(dest)==1 and m.text(dest[0][1])=='playing_page'
        assert [m.get(dest[0][2]+4*i) for i in range(4)]==[0,0,255,2]
        assert not any(c[0]=='player_start' for c in m.calls); passed()
def home_back(full):
    CONFIG['HOME']=str(full); m=NavigationMachine(); del CONFIG['HOME']
    view,imgs=home_list(m); home=m.top; m.order=[home]; m.sync_order(); m.paint(view)
    assert m.call(O['KEY_RETURN'])==11 and m.nodes[m.top]['name']=='playing_page'
    assert not any(c[0]=='player_start' for c in m.calls)
    m.call(O['KEY_RETURN'],address=0x52c248,args=(m.top,m.event,0,0),gap=0)
    assert m.top==home; passed()
    # Screen, lock and held-release gates still precede the shortcut; animation also blocks it.
    for flag,value in GATES+[(None,1)]:
        m=Machine(); view,imgs=home_list(m); m.paint(view)
        if flag: m.byte(syms[flag],value)
        else: m.animating=value
        m.call(O['KEY_RETURN'])
        assert not destinations(m), flag
    m=long_machine(); view,imgs=home_list(m); m.paint(view)
    long_return(m); m.call(O['KEY_RETURN'],gap=0)
    assert not destinations(m); passed()
if variant=='ipod':
    for full in (0,1):
        home_settings(full); home_settings(full,1); home_back(full)
# Most Played opens a black mostplayed_page list (nothing played: "No plays yet", no rows); a second
# press while it is open does nothing. Its rows are the top PLAYS_TOP, most played first and, among
# equal counts, the most recently counted (earlier slot) first; a row plays that ranked list as a
# library queue (0xf001) from itself, leaving the play mode alone.
f,ctx=m.handler(m.nodes[top]['children'][0],O['EVT_CLICK'])
m.found=[m.song(n) for n in ('T1','T2','T3')]; m.calls=[]; toasts=len(m.toasts)
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and not m.plays and len(m.toasts)==toasts
m.page=m.top; assert m.nodes[m.page]['name']=='mostplayed_page' and m.nodes[m.page]['style:normal:bg_color']==-0x1000000
assert m.texts()==['No plays yet'] and not m.nodes[m.find('scroll_view')]['children']
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.top==m.page; m.close(); passed()
m=ShuffleMachine(); m.found=[m.song(f'T{i}') for i in range(130)]
m.word(m.found[1]+O['REC_ARTIST'],0)  # untagged: the count alone
slots=[(fnv('/p/T3'),5),(fnv('/p/T1'),2),(fnv('/p/T7'),5)]+[(fnv(f'/p/T{i}'),1) for i in range(8,130)]
m.counts=b''.join(struct.pack('<2I',*e) for e in slots).ljust(8*O['PLAYS_SLOTS'],b'\0')
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000)==0; m.page=m.top
ranked=['T3','T7','T1']+[f'T{i}' for i in range(8,130)][:O['PLAYS_TOP']-3]
# Each 64px row: the title over its artist (when tagged) and play count, in 16px stock grey.
details=['Artist · 5 plays','Artist · 5 plays','2 plays']+['Artist · 1 play']*(O['PLAYS_TOP']-3)
assert m.texts()==['Most Played']+[x for p in zip(ranked,details) for x in p]
rows=m.nodes[m.find('scroll_view')]['children']; assert len(rows)==O['PLAYS_TOP']
assert [(m.get(rows[1]+O['W_Y']),m.get(rows[1]+O['W_H']))]==[(64,64)]
title,detail=m.nodes[rows[1]]['children']
assert m.nodes[title]['style']==m.nodes[detail]['style'] and (variant!='ipod' or m.nodes[title]['style']=='s_scrlabel_white20l') and m.get(title+O['W_Y'])+m.get(title+O['W_H'])<=m.get(detail+O['W_Y'])
assert m.nodes[detail]['style:normal:text_color']==-0x555556 and m.nodes[detail]['style:normal:font_size']==16
assert m.get(detail+O['W_Y'])+m.get(detail+O['W_H'])<=64
r=m.nodes[m.find('scroll_view')]['children'][2]; g,c=m.handler(r,O['EVT_CLICK'])
assert m.call(address=g,args=(c,m.event,0,0),gap=0)==0
assert m.queued==ranked and m.plays==[('playing_page',m.plays[0][1],2,0xf001,2)] and not called('config_playmode'); passed()
# A Play/Pause hold on a row opens the song menu, as Coverflow's tracks do, over the ranked list.
m.handlers[syms['navigator_to']]='q:navigator_to'; view=m.find('scroll_view'); m.paint(view); m.call()
m.press(100); assert m.hold()==11 and m.nodes[m.title]['text']=='T7' and m.release()==0
assert m.labels()==['Play Next','Add to Queue','Add to Favourites','Go to Artist']; passed()
# Opened again, the kept tracks are ranked again without reading the library; a library change reads it again.
m.close(); m.found=m.found[:8]; m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000)==0 and not called('getAllMusic')
m.page=m.top; assert m.texts()==['Most Played']+[x for p in zip(ranked,details) for x in p]
m.close(); m.calls=[]; m.rescan(); m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000)==0 and called('getAllMusic')
m.page=m.top; assert m.texts()==['Most Played']+[x for p in zip(ranked[:3],details[:3]) for x in p]; passed()

# View Queue on a shuffle (Now Playing's hold menu, Up Next's list): a Play/Pause hold on a row plays its track
# next, a move, not Play Next's copy, the queue as it was; the rows are taken again with the pick at row 0, the
# selection there, with no menu, toast or play/pause. A row already next stays there; playback is not touched; once
# the queue changes under the page a hold says so and takes the rows again from the live queue.
m=CoverflowMachine(page='playing_page',pos=1); q=m.deque([m.song(c) for c in 'ABCDE'])
m.word(syms['mcl_pdeqplaylist'],q); m.word(O['MCL_POOL'],m.deque(list(range(5)),'int'))
assert m.call(address=payload_syms['playback_set'],args=(0,1,0,0),gap=0)==1
m.press(10); assert m.hold()==11 and 'Up Next' not in m.labels(); m.release()
m.pick(m.labels().index('View Queue')); m.advance(0); m.page=m.top; assert m.nodes[m.page]['name']=='upnext_page'
up=m.texts()[1::2]; assert len(up)==4 and 'B' not in up and m.texts()[0]=='View Queue'
view=m.find('scroll_view'); m.paint(view); m.call(); assert m.selected(view)==1
m.press(20); assert m.hold()==11 and m.release()==0 and not m.toasts
want=[up[1],up[0]]+up[2:]; assert m.texts()[1::2]==want and m.names()==list('ABCDE') and m.mcl('MCL_POS')==1
view=m.find('scroll_view'); assert m.selected(view)==0 and not m.playback() and m.top==m.page
m.paint(view); m.call(O['KEY_NEXT']); assert m.selected(view)==1
m.call(O['KEY_PREV']); assert m.selected(view)==0
toasts=len(m.toasts); m.press(30); assert m.hold()==11 and m.release()==0 and len(m.toasts)==toasts
assert m.texts()[1::2]==want and m.names()==list('ABCDE')
m.deqs[q][1][4]=m.song('F'); m.call(O['KEY_NEXT']); toasts=len(m.toasts)
m.press(40); assert m.hold()==11 and m.release()==0 and len(m.toasts)==toasts+1 and m.toasts[-1][3]=='Queue changed'
fresh=m.texts()[1::2]; assert fresh[0]==up[1] and 'F' in fresh and 'E' not in fresh and m.selected(m.find('scroll_view'))==0
m.close(); passed()

# Ratings (navigation.c rate_*): 5 hearts are stock's My Fav, 1-4 kept in /mnt/data/ringnav-ratings by listen_key with
# the hearts in its low three bits. Now Playing's heart: stock's click (0x52b154, bound to img_fav with the window) is
# swapped for one telling a tap, stock's toggle that leaves nothing kept, from a RATE_HOLD_MS hold, which opens the
# picker: five of the heart's images on a dark panel, the wheel moving the rating, Centre saving and Return leaving it.
FAV_CLICK=0x52b154
class RateMachine(FileMocks,ShuffleMachine):
    def __init__(self,saved=b''):
        super().__init__()
        self.budget=2_000_000  # a RATE_SLOTS table scan or two a call
        self.word(O['MCL_POS'],1)  # B of ABC plays
        self.file_mocks('r:',{'/mnt/data/ringnav-ratings':saved} if saved else {}); self.fav_songs=[]
        for n in ('getAllFav','fseek@GLIBC_2.0','ftell@GLIBC_2.0','taglib_file_new','taglib_file_audioproperties','taglib_file_free'):
            self.handlers[syms[n]]='r:'+n
        self.lengths={}; self.probed=[]; self.handlers[0x1000030]='r:length'  # AudioProperties::length(), vtable +0xc
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
        f,ctx=self.handler(self.fav,O['EVT_POINTER_DOWN']); self.call(address=f,args=(ctx,self.event,0,0),gap=0)
        self.pressed=1; self.advance(ms,clear=False); self.pressed=0
def key_of(name): k=fnv('/p/'+name); return k|(k==0)
m=RateMachine()
# Bound: stock's click off img_fav, the payload's click and press on, the badge hidden; nothing read yet beyond the file.
assert ('widget_off_by_func',m.fav,O['EVT_CLICK'],FAV_CLICK) in m.calls and m.badge() is None and m.panel() is None
assert [t for t,f,ctx in m.nodes[m.fav]['handlers']]==[O['EVT_CLICK'],O['EVT_POINTER_DOWN']]
assert m.nodes[m.nodes[m.get(m.fav+O['W_PARENT'])]['children'][-1]]['sensitive']==0; passed()  # the badge leaves the heart its taps
# A tap is stock's heart: B (playing) into My Fav, nothing kept; again, out. Its click returns 0, as stock's:
# a STOP would skip the slide_view's own release and leave its grab on the window.
assert m.tap()==0 and m.favs=={'B'} and m.nodes[m.fav]['image']=='play_fav' and m.kept()==[]
assert m.tap()==0 and m.favs==set(); passed()
# A press shorter than the hold, then its click: still the tap.
def aborts(): return [c for c in m.calls if c[0]=='widget_dispatch_event_to_target_recursive' and m.get(c[2])==O['EVT_POINTER_ABORT']]
m.calls=[]; m.heart(O['RATE_HOLD_MS']-1); assert m.panel() is None and not aborts() and m.tap(press=False)==0 and m.favs=={'B'}; passed()
# A hold opens the picker on 5 (My Fav), aborting the press so the slide_view drops its grab; its release's click does nothing.
m.calls=[]; m.heart(); assert m.hearts()==['play_fav']*5 and m.caption()=='5 of 5' and [c[1] for c in aborts()]==[m.np]
assert m.tap(press=False)==0 and m.favs=={'B'}; passed()
# The wheel moves the rating within 0-5; Return leaves it as it was.
m.top=m.np
for k,want in ((O['KEY_NEXT'],5),(O['KEY_PREV'],4),(O['KEY_PREV'],3)): assert m.call(k)==11
assert m.hearts()==['play_fav']*3+['play_unfav']*2 and m.caption()=='3 of 5' and m.favs=={'B'}
assert m.call(O['KEY_RETURN'])==11 and m.panel() is None and m.favs=={'B'} and m.kept()==[]; passed()
# Centre saves 3: My Fav out through stock's own click, 3 kept, the badge on the empty heart.
m.heart()
for _ in range(2): m.call(O['KEY_PREV'])
assert m.call(O['KEY_CENTER'])==11 and m.panel() is None and m.favs==set()
assert m.kept()==[(key_of('B')&~7)|3] and m.badge()=='3'; passed()
# Down to 0 clears it; a tap on the third heart saves 3 again; a tap on the heart (into My Fav) leaves nothing kept.
m.heart()
for _ in range(4): m.call(O['KEY_PREV'])
assert m.caption()=='Unrated' and m.hearts()==['play_unfav']*5
m.call(O['KEY_CENTER']); assert m.kept()==[] and m.badge() is None and m.favs==set()
m.heart(); p=m.panel(); assert m.nodes[p]['sensitive']==1; heart=[h for h in m.nodes[p]['children'] if m.nodes[h]['type']=='image'][2]
f,ctx=m.handler(heart,O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.panel() is None and m.nodes[p]['sensitive']==0 and m.kept()==[(key_of('B')&~7)|3] and m.badge()=='3'
assert m.tap()==0 and m.favs=={'B'} and m.kept()==[] and m.badge() is None; passed()
# Kept across boots; the badge follows the playing track on the UI tick.
assert m.tap()==0 and m.favs==set(); m.heart(); m.call(O['KEY_NEXT']); m.call(O['KEY_NEXT']); m.call(O['KEY_CENTER'])
m2=RateMachine(m.files['/mnt/data/ringnav-ratings']); assert m2.badge()=='2'
m2.word(O['MCL_POS'],0); m2.advance(500); assert m2.badge() is None; passed()
# Hold Play/Pause on Now Playing: Rate last, with the hearts, and no Next/Previous album outside Shuffle Albums/Folders; its row opens the same picker.
m2.word(O['MCL_POS'],1); m2.handlers[syms['navigator_to']]='q:navigator_to'; m2.press(100)
assert m2.hold()==11 and m2.labels()[-2:]==['Group by: Album','Rate  ♥♥♡♡♡'] and m2.release()==0
m2.pick(len(m2.labels())-1); m2.advance(0); m2.top=m2.np; assert m2.caption()=='2 of 5' and m2.call(O['KEY_RETURN'])==11; passed()
# Ratings: Top Rated (4-5), Loved (5), All Rated and Disliked (1), most hearts first, then title order.
m=RateMachine(b''.join(struct.pack('<I',(fnv(path)&~7)|h) for path,h in (('/p/A',1),('/mnt/mmc/Audiobooks/C.m4b',4),('/mnt/mmc/Podcasts/E.mp3',2))).ljust(4*O['RATE_SLOTS'],b'\0'))
m.found=[m.song(n) for n in ('A','B','C','D','E')]; m.fav_songs=[m.song('D')]
m.word(m.found[2]+O['REC_PATH'],m.string('/mnt/mmc/Audiobooks/C.m4b'))
m.word(m.found[4]+O['REC_PATH'],m.string('/mnt/mmc/Podcasts/E.mp3'))
m.top=m.node('window','localmusic_page'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.nodes[m.top]['children']=[view]; m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
f,ctx=m.handler(m.nodes[m.nodes[view]['children'][7]]['children'][0],O['EVT_CLICK'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; menu=m.top; m.page=menu
assert m.nodes[menu]['name']=='ratings_page' and m.texts()==['Ratings','Top Rated','Loved','Albums','All Rated','Disliked']
want={0:['D','C'],1:['D'],2:['D','C','E','A'],3:['A']}
hearts={'D':'♥♥♥♥♥','C':'♥♥♥♥♡','E':'♥♥♡♡♡','A':'♥♡♡♡♡'}
for i,names in want.items():
    row=m.nodes[m.find('scroll_view',menu)]['children'][i+(i>=2)]; g,c=m.handler(row,O['EVT_CLICK'])  # Albums aside
    assert m.call(address=g,args=(c,m.event,0,0),gap=0,count=50_000_000)==0; m.page=m.top
    assert m.nodes[m.page]['name']=='rated_page'
    assert [c for c in m.calls if c[0]=='getAllMusic'][-1][1:]==(0,0)
    assert m.texts()==[('Top Rated','Loved','All Rated','Disliked')[i]]+[x for n in names for x in (n,hearts[n]+' · Artist')],m.texts()
    if i==2:  # a row plays the list from there, as a library queue
        r=m.nodes[m.find('scroll_view')]['children'][1]; g,c=m.handler(r,O['EVT_CLICK'])
        assert m.call(address=g,args=(c,m.event,0,0),gap=0)==0 and m.queued==names and m.plays[-1][2:]==(1,0xf001,2)
    m.close(); m.page=menu
m.found=[m.song('Z')]; m.fav_songs=[]; m.calls=[]; row=m.nodes[m.find('scroll_view',menu)]['children'][0]; g,c=m.handler(row,O['EVT_CLICK'])
m.call(address=g,args=(c,m.event,0,0),gap=0); m.page=m.top
assert m.texts()==['Top Rated','No songs yet','Hold the heart on Now Playing to rate']; passed()

# Long Songs: over LONG_SONG_S, longest first. Files never probed are probed once by TagLib on a worker (a CUE track
# with both ends needs none) and kept by path hash in /mnt/data/ringnav-lengths, sorted; 0 for an unreadable file.
cached=struct.pack('<2I',fnv('/p/E'),1200)
m=RateMachine(); m.files['/mnt/data/ringnav-lengths']=cached
m.found=[m.song(n) for n in 'ABCDE']; m.word(m.found[2]+O['REC_CUE_END'],900)
m.lengths={'/p/A':700,'/p/B':300}
m.top=m.node('window','localmusic_page'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.nodes[m.top]['children']=[view]; m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
f,ctx=m.handler(m.nodes[m.nodes[view]['children'][14]]['children'][0],O['EVT_CLICK'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; m.page=m.top
assert m.nodes[m.page]['name']=='longsongs_page' and m.texts()==['Measuring songs…','Cancel'] and len(m.threads)==1
worker,arg=m.threads[0]; assert m.call(address=worker,args=(arg,0,0,0),gap=0)==0 and m.probed==['/p/A','/p/B','/p/D']
m.advance(250)
want=['Long Songs','E','Artist · 20:00','C','Artist · 15:00','A','Artist · 11:40']
assert m.texts()==want,m.texts()
saved=m.files['/mnt/data/ringnav-lengths']; pairs=sorted(struct.unpack(f'<{len(saved)//4}I',saved)[0::2])
assert pairs==sorted(fnv('/p/'+n) for n in 'EABD') and dict(zip(*[iter(struct.unpack(f'<{len(saved)//4}I',saved))]*2))[fnv('/p/D')]==0
r=m.nodes[m.find('scroll_view')]['children'][2]; g,c=m.handler(r,O['EVT_CLICK'])
assert m.call(address=g,args=(c,m.event,0,0),gap=0)==0 and m.queued==['E','C','A']; passed()
# Again, nothing to probe: the list at once. Cancel keeps what was measured.
m.close(); m.probed=[]; m.threads=[]; m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.page=m.top
assert not m.threads and not m.probed and m.texts()==want; m.close(); passed()
m=RateMachine(); m.found=[m.song(n) for n in 'AB']; m.lengths={'/p/A':700,'/p/B':900}
m.top=m.node('window','localmusic_page'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.nodes[m.top]['children']=[view]; m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
f,ctx=m.handler(m.nodes[m.nodes[view]['children'][14]]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.page=m.top
cancel=m.nodes[m.find('scroll_view')]['children'][0]; g,c=m.handler(cancel,O['EVT_CLICK']); m.call(address=g,args=(c,m.event,0,0),gap=0)
worker,arg=m.threads[0]; m.call(address=worker,args=(arg,0,0,0),gap=0); assert m.probed==[]  # cancelled before the first
m.advance(250); assert m.texts()==['Long Songs','No songs over 10 minutes'] and m.joins==[77]; passed()
# Two CUE tracks to the end of one file probe it twice; the cache keeps it once.
m=RateMachine(); m.found=[m.song('A'),m.song('A')]; m.lengths={'/p/A':1300}; m.word(m.found[1]+O['REC_CUE_START'],600)
m.top=m.node('window','localmusic_page'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.nodes[m.top]['children']=[view]; m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
f,ctx=m.handler(m.nodes[m.nodes[view]['children'][14]]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.page=m.top
worker,arg=m.threads[0]; m.call(address=worker,args=(arg,0,0,0),gap=0); m.advance(250)
assert m.files['/mnt/data/ringnav-lengths']==struct.pack('<2I',fnv('/p/A'),1300)
assert m.texts()==['Long Songs','A','Artist · 21:40','A','Artist · 11:40']; passed()

# Upload Scrobbles: the second-last row, above Update Local Music, only with an account in the card's .scrobble.ini (a
# ListenBrainz token or all four Last.fm keys). Without Wi-Fi it only says so; otherwise scrobble.c's thread
# starts, a second press finds it running, and a timer reports the result once the thread ends.
m=ShuffleMachine(); m.config.update(USER='u',PASSWORD='p',API_KEY='k')  # no API_SECRET: no row
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and len(m.nodes[view]['children'])==16+GAMES
assert ('/mnt/mmc/.scrobble.ini','LASTFM','API_SECRET','') in m.config_reads
m.config['TOKEN']='tok'; m.nodes[view]['children']=[m.node('list_item') for _ in range(11)]
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and len(m.nodes[view]['children'])==17+GAMES
button=m.nodes[m.nodes[view]['children'][-2]]['children'][0]; icon,label=m.nodes[button]['children']
assert m.nodes[icon]['image']=='local_scrobble' and m.nodes[label]['text']=='Upload Scrobbles' and not m.nodes[button].get('name')
library_geometry(m,m.nodes[view]['children'][-2])
f,ctx=m.handler(button,O['EVT_CLICK'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.threads
m.wifi=3; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.toasts[-1][3]=='Uploading scrobbles' and len(m.threads)==1
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.toasts[-1][3]=='Already uploading' and len(m.threads)==1
m.advance(2000); assert m.toasts[-1][3]=='Already uploading' and not m.joins  # still uploading
worker,arg=m.threads[0]; assert m.call(address=worker,args=(arg,0,0,0),gap=0)==0  # no log on the card
m.advance(600); assert m.toasts[-1][3]=='Nothing to upload' and m.joins==[77] and not m.timers; passed()

# Podcasts and Audiobooks: a row each, above upkeep, only for the card's top-level folder of that name (case
# aside, a directory). A press opens folder_page there as a deeper folder (layer 3, stock reload, title
# and rebuild); Back there leaves the page (layer 1 before stock's step down), and only for that root.
m=ShuffleMachine(); m.card=[('Music',4),('PODCASTS',4),('Audiobooks',8)]
m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and len(m.nodes[view]['children'])==17+GAMES
button=m.nodes[m.nodes[view]['children'][-2-GAMES]]['children'][0]; icon,label,into=m.nodes[button]['children']
assert m.nodes[icon]['image']=='local_podcasts' and m.nodes[label]['text']=='Podcasts' and not m.nodes[button].get('name')
f,ctx=m.handler(button,O['EVT_CLICK']); m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']==['folder_page']
win=m.node('window','folder_page'); m.calls=[]
assert m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)==0
assert m.text(syms['g_folder_path'])=='/mnt/mmc/PODCASTS' and m.u.mem_read(syms['g_folder_layer'],1)[0]==3
assert [c[0] for c in m.calls]==['stock_folder','folder_reset_variable','strlen@GLIBC_2.0','memcpy','folder_reload_data','folder_reinit_navbarname','folder_refresh'] and m.calls[-1][1]==win
assert m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)==0 and m.calls[-1][0]=='stock_folder_back'
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==1; passed()
m.byte(syms['g_folder_layer'],3); assert m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)==0  # the Folder view
m.folder('/mnt/mmc/PODCASTS'); m.byte(syms['g_folder_layer'],3); m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==3; passed()

# Audiobooks uses the same shortcut path, even with OTG connected; a nested Back stays in Folder.
m=ShuffleMachine(); m.card=[('Audiobooks',4)]; m.byte(syms['g_otg_status'],1)
m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.top=m.node('window','localmusic_page',[view])
m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
button=m.nodes[m.nodes[view]['children'][-2-GAMES]]['children'][0]
f,ctx=m.handler(button,O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
win=m.node('window','folder_page'); m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)
assert m.text(syms['g_folder_path'])=='/mnt/mmc/Audiobooks' and m.u.mem_read(syms['g_folder_layer'],1)[0]==3
m.folder('/mnt/mmc/Audiobooks/Author'); m.byte(syms['g_folder_layer'],4)
m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==4
m.folder('/mnt/mmc/Audiobooks'); m.byte(syms['g_folder_layer'],3)
m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==1; passed()

# Folder visits bypass the storage selector only when OTG was absent and the card present on entry
# (stock's check_tfstatus: /tmp/mmc_add).
for otg,card in ((0,1),(1,1),(0,0)):
    m=Machine()
    if card: m.present.add('/tmp/mmc_add')
    m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
    m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
    win=m.node('window','folder_page'); m.byte(syms['g_otg_status'],otg); m.byte(syms['g_folder_layer'],1)
    assert m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)==0
    direct=card and not otg; calls=[c[0] for c in m.calls if not c[0].startswith('access')]
    assert m.u.mem_read(syms['g_folder_layer'],1)[0]==(2 if direct else 1)
    if direct:
        assert m.text(syms['g_folder_path'])=='/mnt/mmc'
        assert calls==['stock_folder','memcpy','toolsReadConfig','tk_snprintf','folder_reset_variable','strlen@GLIBC_2.0','memcpy',
                       'folder_reload_data','folder_reinit_navbarname','folder_refresh'],calls
    else: assert calls==['stock_folder','toolsReadConfig','tk_snprintf'][:1+2*card],calls
    # Nested Back stays stock. Plugging/unplugging OTG mid-visit does not change root Back.
    m.folder('/mnt/mmc/Music'); m.byte(syms['g_folder_layer'],3)
    m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
    assert m.u.mem_read(syms['g_folder_layer'],1)[0]==3
    m.folder('/mnt/mmc'); m.byte(syms['g_folder_layer'],2); m.byte(syms['g_otg_status'],not otg)
    m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
    assert m.u.mem_read(syms['g_folder_layer'],1)[0]==(1 if direct else 2)
    passed()
# A direct visit left without Back (as by Home), then a selector visit with OTG: the old root is gone, so
# Back at the card's root on the selector stays stock.
m=Machine(); m.present.add('/tmp/mmc_add')
m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
win=m.node('window','folder_page'); m.byte(syms['g_otg_status'],0); m.byte(syms['g_folder_layer'],1)
m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)
assert m.text(syms['g_folder_path'])=='/mnt/mmc' and m.u.mem_read(syms['g_folder_layer'],1)[0]==2
m.byte(syms['g_otg_status'],1); m.byte(syms['g_folder_layer'],1)
m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==1
m.folder('/mnt/mmc'); m.byte(syms['g_folder_layer'],2)
m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==2; passed()

# Folder Root (#49): with a card, Folder opens in the saved top-level folder (Q2POD FOLDERROOT) as a
# layer-3 folder, a fresh visit (stock folder_reset_variable, run here: no enter history, no M3U).
# Stock folder_back, run here, walks up to the card at layer 2 with stock's title; its history being
# empty, the position stays the caller's (the list's top). Back there leaves without a drive and
# reaches the storage selector with one. A missing, unsafe or file root opens as before.
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
for otg in (0,1):
    m=RootMachine('Music',otg=otg); m.push(5,6); m.u.mem_write(syms['g_m3u_path'],b'/mnt/mmc/Music/list.m3u\0')  # a visit left deep
    assert m.open()==('/mnt/mmc/Music',3) and not any(m.stacks.values()) and m.text(syms['g_m3u_path'])==''
    calls=[c[0] for c in m.calls if c[0] in ('stock_folder','folder_reload_data','folder_reinit_navbarname','folder_refresh')]
    assert calls==['stock_folder','folder_reload_data','folder_reinit_navbarname','folder_refresh'] and m.calls[-1][1]==m.win
    m.calls=[]; assert m.back()==(1,'/mnt/mmc',2,(7,9)) and m.text(syms['g_navbar_name'])=='SD'
    assert [c[1] for c in m.calls if c[0]=='toolsLoadDirectory']==[syms['g_folder_path']]
    assert m.back()[::2]==((1,1) if otg else (0,0)); passed()  # the selector, or out of the page
# Gone, a file, hidden, a path, or no card: as before Folder Root (the card at layer 2, or stock's page).
for root in ('Gone','notes.txt','.Trash','Music/..',''):
    assert RootMachine(root).open()==('/mnt/mmc',2),root
for m in (RootMachine('Gone',otg=1),RootMachine('Music')):
    m.missing=m.config['FOLDERROOT']=='Music'  # no card: CoverflowMachine's access finds nothing
    assert m.open()[1]==1 and 'folder_reload_data' not in [c[0] for c in m.calls]
passed()
# Podcasts keeps its own root, Back there leaving the page, whatever Folder Root says.
m=RootMachine('Music'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.top=m.node('window','localmusic_page',[view]); m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
f,ctx=m.handler(m.nodes[m.nodes[view]['children'][-2-GAMES]]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.open()==('/mnt/mmc/Podcasts',3) and m.back()[::2]==(0,0); passed()

# Go to Folder: a card song's hold menu row, after Go to Artist, opens folder_page in the song's folder as a
# fresh visit at the layer stock's enters would have reached (the card 2, each folder one more), so stock
# folder_back, run here, climbs a folder at a time to the card and out of the page, or to the storage
# selector with a drive. The path is copied as it is: percent signs, format directives and UTF-8 stay literal.
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
GO=[*SONG_MENU,'Go to Folder']
DEEP='/mnt/mmc/Music/100% %s%n/Ünï cødé'
for otg in (0,1):
    m=GoMachine(DEEP+'/song.flac',['/mnt/mmc/Music/100% %s%n',DEEP],otg=otg); m.push(5,6)  # a visit left deep
    assert m.menu()==('Row 0',GO); m.go(); assert m.opened[-1][0]=='folder_page'
    assert (m.text(syms['g_folder_path']),m.u.mem_read(syms['g_folder_layer'],1)[0])==(DEEP,5) and not any(m.stacks.values())
    for want,layer in (('/mnt/mmc/Music/100% %s%n',4),('/mnt/mmc/Music',3),('/mnt/mmc',2)): assert RootMachine.back(m)[:3]==(1,want,layer)
    assert RootMachine.back(m)[::2]==((1,1) if otg else (0,0)); passed()  # the selector, or out of the page
# A song at the card's root opens the card itself.
m=GoMachine('/mnt/mmc/x.mp3',['/mnt/mmc']); assert m.menu()[1]==GO; m.go()
assert (m.text(syms['g_folder_path']),m.u.mem_read(syms['g_folder_layer'],1)[0])==('/mnt/mmc',2) and RootMachine.back(m)[::2]==(0,0); passed()
# No row off the card, for a path stock's enters never build, a gone folder, one too long for g_folder_path
# (1023 bytes fit), or while a folder_page is open.
for path,dirs,row in (('/mnt/mmcx/a.mp3',['/mnt/mmcx'],0),('/mnt/mmc/A/../a.mp3',['/mnt/mmc/A/..'],0),
        ('/mnt/mmc/./a.mp3',['/mnt/mmc/.'],0),('/mnt/mmc/A//a.mp3',['/mnt/mmc/A/'],0),('/mnt/mmc/Gone/a.mp3',[],0),
        ('/mnt/mmc/'+'d'*1015+'/a.mp3',['/mnt/mmc/'+'d'*1015],0),('/mnt/mmc/'+'d'*1014+'/a.mp3',['/mnt/mmc/'+'d'*1014],1),
        ('/mnt/mmc/..a/.b/a.mp3',['/mnt/mmc/..a/.b'],1)):
    assert GoMachine(path,dirs).menu()[1]==(GO if row else SONG_MENU),path; passed()
m=GoMachine(DEEP+'/a.mp3',[DEEP]); m.open_pages.add('folder_page'); assert m.menu()[1]==SONG_MENU; passed()
# Gone between the menu and the pick: a toast, no page, and the next Folder visit opens as before.
m=GoMachine(DEEP+'/a.mp3',[DEEP]); m.menu(); m.dirs.clear(); m.go()
assert m.toasts[-1][3]=='Folder not found' and m.opened[-1][0]!='folder_page' and m.open()==('/mnt/mmc',2); passed()
# The card gone as the page opens: stock's page, the pending folder dropped.
m=GoMachine(DEEP+'/a.mp3',[DEEP]); m.menu(); m.missing=True; m.go()
assert m.opened[-1][0]=='folder_page' and m.u.mem_read(syms['g_folder_layer'],1)[0]==1; m.missing=False
assert m.open()==('/mnt/mmc',2); passed()
# Now Playing: the playing song's row sits after Go to Artist, before the play modes.
m=playing_menu(); m.dirs.add('/mnt/mmc/Music'); m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[1]+O['REC_PATH'],m.string('/mnt/mmc/Music/B'))
m.press(6000); assert m.hold()==11 and m.labels()==SONG_MENU[:2]+['View Queue']+SONG_MENU[2:]+['Go to Folder','Shuffle','Repeat','Group by: Album']
passed()

# Photos (patch/photos.c): the helpers first. Names: .jpg, .jpeg and .png, case aside, not hidden.
m=Machine(); m.mock('memcmp@GLIBC_2.0',prefix='cmp:')
def ph_call(name,*args): return m.call(address=ps[name],args=(*args,0,0,0,0)[:4],gap=0,count=5_000_000)
assert [ph_call('photo_file',m.string(n)) for n in ('a.JPG','b.jpeg','c.Png','d.gif','._e.jpg','.f.jpg','g','h.jpg.txt')]==[1,1,1,0,0,0,0,0]
passed()
# EXIF orientation: either byte order, after another segment, within the bytes read; else 1.
def exif(o,le=True,pad=b''):
    e='<' if le else '>'
    tiff=(b'II' if le else b'MM')+struct.pack(e+'HI',42,8)+struct.pack(e+'H',2)+struct.pack(e+'HHI',0x10f,2,1)+bytes(4)
    tiff+=struct.pack(e+'HHIH',0x112,3,1,o)+bytes(2)+bytes(4)
    app1=b'Exif\0\0'+tiff
    return b'\xff\xd8'+pad+b'\xff\xe1'+struct.pack('>H',len(app1)+2)+app1+b'\xff\xda'
JFIF=b'\xff\xe0'+struct.pack('>H',16)+b'JFIF\0'+bytes(9)
def orientation(data): a=m.alloc(len(data)+4); m.u.mem_write(a,data); return ph_call('photo_orientation',a,len(data))
assert [orientation(d) for d in (exif(6),exif(8,le=False),exif(3,pad=JFIF),exif(9),exif(6)[:30],b'\x89PNG\r\n\x1a\n',b'\xff\xd8\xff\xda')]==[6,8,3,1,1,1,1]
passed()
# Orientation: each of the eight against its EXIF meaning (PIL's exif_transpose), the channels from
# the format's byte order, alpha over black, and a crop to the box.
W,H=3,2
def stored(x,y): return (10*x+y+1,100+x,200+y,255)
def shown(o,x,y):  # the stored pixel shown at x, y
    return {1:(x,y),2:(W-1-x,y),3:(W-1-x,H-1-y),4:(x,H-1-y),5:(y,x),6:(y,H-1-x),7:(W-1-y,H-1-x),8:(W-1-y,x)}[o]
src=m.alloc(64); at=m.alloc(4); dst=m.alloc(256); size=m.alloc(8)
for o in range(1,9):
    for y in range(H):
        for x in range(W): r,g,b,a=stored(x,y); m.u.mem_write(src+y*16+x*4,bytes([b,g,r,a]))  # BGRA, format 3
    m.u.mem_write(at,bytes([2,1,0,3]))
    m.call(address=ps['photo_orient'],args=(src,W,H,16),stack=(at,o,dst,4,size),gap=0,count=5_000_000)
    sw,sh=(H,W) if o>=5 else (W,H)
    assert (m.get(size),m.get(size+4))==(sw,sh),o
    for y in range(sh):
        for x in range(sw):
            r,g,b,_=stored(*shown(o,x,y)); assert m.get(dst+(y*4+x)*4)==0xff000000|b<<16|g<<8|r,(o,x,y)
m.u.mem_write(src,bytes([200,100,50,128]))  # half transparent, over black
m.call(address=ps['photo_orient'],args=(src,W,H,16),stack=(at,1,dst,2,size),gap=0,count=5_000_000)
assert (m.get(size),m.get(size+4))==(2,2) and m.get(dst)==0xff000000|(200*128//255)<<16|(100*128//255)<<8|50*128//255
passed()
# Placing: fitted into the box, centred, never enlarged.
r=m.alloc(16)
for (w,h,bw,bh),want in (((100,50,85,72),(0,15,85,42)),((40,30,85,72),(22,21,40,30)),((50,100,85,72),(24,0,36,72)),
                         ((281,375,375,290),(79,0,217,290))):
    m.call(address=ps['photo_place'],args=(w,h,0,0),stack=(bw,bh,r),gap=0); assert tuple(signed(m.get(r+4*i)) for i in range(4))==want,(w,h,want)
passed()

class PhotosMachine(DepthMachine):
    """The card as a tree of {path: [(name, d_type)]}, photo bytes by path, and the cache's files."""
    def __init__(self,tree,data):
        super().__init__()
        self.tree=tree; self.data=data; self.cache=set(); self.thumbs=[]; self.forks=0; self.bad=set(); self.open_files={}; self.dirs={}
        for n in ('opendir@GLIBC_2.0','readdir@GLIBC_2.0','closedir@GLIBC_2.0','qsort@GLIBC_2.0','fopen@GLIBC_2.2',
                  'fread@GLIBC_2.0','fclose@GLIBC_2.2','access@GLIBC_2.0','rename@GLIBC_2.0','unlink@GLIBC_2.0',
                  'memcmp@GLIBC_2.0','toolsThumbSpecCover','fork@GLIBC_2.0','pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0'):
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
            name=name[2:].split('@')[0]; ret=0; text=self.text(a) if name not in ('readdir','closedir','qsort','fread','fclose','memcmp','fork') else ''
            if name=='opendir':
                ret=0 if text not in self.tree else self.alloc(4); self.dirs[ret]=list(self.tree.get(text,[]))
            elif name=='readdir' and self.dirs.get(a):
                n,t=self.dirs[a].pop(0); ret=self.alloc(268); self.byte(ret+10,t); self.u.mem_write(ret+11,n.encode()+b'\0')
            elif name=='qsort':
                ptrs=[self.get(a+4*i) for i in range(b)]
                for i,p in enumerate(sorted(ptrs,key=self.text)): self.word(a+4*i,p)
            elif name=='fopen':
                if text in self.data: ret=self.alloc(4); self.open_files[ret]=self.data[text]
                elif text.endswith('.bad'): self.bad.add(text); ret=self.alloc(4)
            elif name=='fread': chunk=self.open_files.get(d,b'')[:b*c]; self.u.mem_write(a,chunk); ret=len(chunk)//b
            elif name=='access': ret=0 if text in self.cache or text in self.bad or text in self.data else -1
            elif name=='toolsThumbSpecCover':
                self.thumbs.append((self.text(a),self.text(b),c,d))
                ret=0 if self.data.get(self.text(a))==b'corrupt' else 1
                if ret: self.cache.add(self.text(b))
            elif name=='rename': self.cache.discard(text); self.cache.add(self.text(b))
            elif name=='unlink': self.cache.discard(text)
            elif name=='fork': self.forks+=1; ret=-1  # thumb()'s ffmpeg fallback, refused
            elif name=='memcmp': ret=0 if bytes(self.u.mem_read(a,c))==bytes(self.u.mem_read(b,c)) else 1
        else: return super().hook(u,address,size,unused)
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)

# Local Music's last row opens the card's Photos folder (case aside) as albums: All Photos, then
# each subfolder holding a photo, by name; hidden files, other types and empty folders are left out.
R='/mnt/mmc/photos'
tree={'/mnt/mmc':[('Music',4),('photos',4)],R:[('b.jpg',8),('A.png',8),('._b.jpg',8),('notes.txt',8),('Trip',4),('Empty',4),('.hidden',4)],
      R+'/Trip':[('2.JPG',8),('1.jpg',0)],R+'/Empty':[('x.txt',8)],R+'/.hidden':[('h.jpg',8)]}
data={R+'/b.jpg':exif(6),R+'/A.png':b'\x89PNG',R+'/Trip/1.jpg':b'corrupt',R+'/Trip/2.JPG':exif(1)}
m=PhotosMachine(tree,data)
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
button=m.nodes[m.nodes[view]['children'][-1-GAMES]]['children'][0]; icon,label,into=m.nodes[button]['children']
assert m.nodes[icon]['image']=='local_photos' and m.nodes[label]['text']=='Photos' and len(m.nodes[view]['children'])==17+GAMES
f,ctx=m.handler(button,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
page=m.top; assert m.nodes[page]['name']=='photos_page' and m.nodes[page]['style:normal:bg_color']==-0x1000000
albums,grid,viewer=m.nodes[page]['children']; assert not m.nodes[grid]['visible'] and not m.nodes[viewer]['visible']
def labels(w): return [m.nodes[x].get('text') for x in m.nodes if m.nodes[x]['type']=='hscroll_label' and m.get(x+O['W_PARENT']) in m.nodes and x in m.nodes[m.get(x+O['W_PARENT'])]['children'] and under(x,w)]
def under(x,w):
    while x and x!=w: x=m.get(x+O['W_PARENT']) if m.get(x+O['W_PARENT']) in m.nodes else 0
    return x==w
assert labels(albums)==['Photos','All Photos','Trip']; passed()
# All Photos: the folder's own, then Trip's, by name; four tiles of three whole rows' grid, each a
# s_listitem_black list_item in a bare vertical scroll view, and the worker for the uncached ones.
rows=m.find('scroll_view',albums); first=m.nodes[rows]['children'][0]
f,ctx=m.handler(first,O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
assert not m.nodes[albums]['visible'] and m.nodes[grid]['visible'] and labels(grid)==['All Photos']
tiles_view=m.find('scroll_view',grid); tiles=m.nodes[tiles_view]['children']
assert m.nodes[tiles_view].get('yslidable')==1 and m.nodes[tiles_view].get('virtual_h')==O['PH_TILE_H']
assert [cf_geometry(m,t) for t in tiles]==[(O['PH_GRID_X']+i*O['PH_TILE_W'],0,O['PH_TILE_W'],O['PH_TILE_H']) for i in range(4)]
assert all(m.nodes[t]['style']=='s_listitem_black' for t in tiles) and len(m.threads)==1
paths=[R+'/A.png',R+'/b.jpg',R+'/Trip/1.jpg',R+'/Trip/2.JPG']; keys=[fnv(p) for p in paths]
def cache(i,s): return f'/mnt/mmc/.photos/{keys[i]:08x}{s}'
# Grey until made; the worker makes each photo's screen copy (sides swapped for a turned photo),
# then its thumbnail from that copy, and marks the corrupt one bad.
def border(w): return m.call(address=HOOKS['widget_on_paint_border'][0],args=(w,m.canvas,0,0),gap=0,clear=False,count=50_000_000)
border(tiles[0]); assert m.bands[-1][:5]==((O['PH_TILE_W']-O['PH_THUMB_W'])//2,(O['PH_TILE_H']-O['PH_THUMB_H'])//2,O['PH_THUMB_W'],O['PH_THUMB_H'],O['PH_GREY'])
worker,arg=m.threads[0]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=20_000_000)==0
SW,SH,TW,TH=O['PH_SHOT_W'],O['PH_SHOT_H'],O['PH_THUMB_W'],O['PH_THUMB_H']
assert m.thumbs==[(paths[0],cache(0,'.tmp'),SW,SH),(cache(0,'.jpg'),cache(0,'.tmp'),TW,TH),
                  (paths[1],cache(1,'.tmp'),SH,SW),(cache(1,'.jpg'),cache(1,'.tmp'),TH,TW),
                  (paths[2],cache(2,'.tmp'),SW,SH),
                  (paths[3],cache(3,'.tmp'),SW,SH),(cache(3,'.jpg'),cache(3,'.tmp'),TW,TH)],m.thumbs
assert m.forks==1 and m.bad=={cache(2,'.bad')} and cache(2,'.jpg') not in m.cache
m.advance(250); assert m.joins==[77]; passed()
# The turned photo's thumbnail is drawn upright, centred in its tile; the bad one stays grey.
border(tiles[1]); assert m.draws[-1]==((0,0,54,72),((O['PH_TILE_W']-54)//2,(O['PH_TILE_H']-O['PH_THUMB_H'])//2,54,72))
border(tiles[2]); assert m.bands[-1][4]==O['PH_GREY']
# Centre on a tile: the viewer, a slide_menu one photo per child, a screen wide per step, drawing
# the photo fitted and centred; at rest its neighbours are decoded too.
f,ctx=m.handler(tiles[1],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
s=m.find('slide_menu',viewer); m.slide=s
assert m.nodes[viewer]['visible'] and not m.nodes[grid]['visible'] and len(m.nodes[s]['children'])==4
assert signed(m.get(s+O['SLIDE_SPACER']))+O['PH_SHOT_H']==375 and m.get(s+O['SLIDE_INDEX'])==1
m.draws=[]; m.loads=[]; paint(m)
assert m.draws==[((0,0,281,375),((375-217)//2,0,217,290))] and m.loads==['file://'+cache(1,'.jpg'),'file://'+cache(0,'.jpg')]
# Mid-slide back to the first photo: this one moves right and the first follows a screen behind it.
m.draws=[]; m.word(s+O['SLIDE_OFFSET'],375//4); paint(m)
(_,a),(_,b)=m.draws; assert len(m.draws)==2 and a[0]-(375-217)//2==b[0]+375 and 0<a[0]-(375-217)//2<375//2 and b[1:]==(4,375,281)
m.word(s+O['SLIDE_OFFSET'],0); passed()
# Centre shows "2 of 4" and the name; a bad photo says so whatever.
info=[x for x in m.nodes[viewer]['children'] if m.nodes[x]['type']=='hscroll_label'][0]
x,y,bw,bh=cf_geometry(m,info); inset=max(corner_inset(30+y),corner_inset(30+y+bh))
assert not m.nodes[info]['visible'] and inset<=x and x+bw<=375-inset  # the whole rounded band clears the glass
f,ctx=m.handler(m.nodes[s]['children'][1],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.nodes[info]['visible'] and m.nodes[info]['text']=='2 of 4  b.jpg' and m.nodes[info]['style:normal:bg_color']==signed(0xb3000000)
m.call(address=f,args=(ctx,m.event,0,0),gap=0); assert not m.nodes[info]['visible']
m.word(s+O['SLIDE_INDEX'],2); f,ctx=m.handler(s,O['EVT_VALUE_CHANGED']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.nodes[info]['visible'] and m.nodes[info]['text']=="Can't open this photo"; passed()
# The wheel steps the viewer a screen at a time, as Coverflow's covers.
m.top=page; assert m.call()==11 and signed(m.get(m.get(s+O['SLIDE_ANIMATOR'])+O['ANIM_X_TO']))==-375; passed()
# Return: the grid with the photo last shown selected, then the albums, then Local Music.
m.page=page; assert m.key()==11 and m.nodes[grid]['visible'] and not m.nodes[viewer]['visible']
assert m.nodes[tiles_view]['_ringnav_index']==2 and m.nodes[tiles_view]['_ringnav_count']==4
assert m.key()==11 and m.nodes[albums]['visible'] and not m.nodes[grid]['visible']
assert m.key()==11 and [c[0] for c in m.calls].count('navigator_back')==1
m.close(); assert sorted(m.destroyed)==sorted(m.frames) and m.frames; passed()
# Failed key/state allocation releases the scanned paths too, so reopening Photos can recover.
for fail_size in (5, 20):
    m=PhotosMachine(tree,data); m.fail_size=fail_size
    m.call(address=payload_syms['photos_open'],args=(m.string(R),0,0,0),gap=0,count=5_000_000)
    m.page=m.top
    rows=m.find('scroll_view'); first=m.nodes[rows]['children'][0]
    f,ctx=m.handler(first,O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    m.advance(0); assert m.freed==4 and not m.threads
    m.close(); assert m.freed==8
passed()

class BooksMachine(DepthMachine):
    """The card as a tree of {path: [(name, d_type)]} and files as {path: bytearray}, with stdio
    handles over them, and a fixed-pitch font: every character BOOK_PITCH wide."""
    def __init__(self,tree,files):
        super().__init__()
        self.tree=tree; self.files=files; self.handles={}; self.dirs={}; self.lines=[]
        for n in ('opendir@GLIBC_2.0','readdir@GLIBC_2.0','closedir@GLIBC_2.0','qsort@GLIBC_2.0','fopen@GLIBC_2.2',
                  'fread@GLIBC_2.0','fwrite@GLIBC_2.0','fseek@GLIBC_2.0','ftell@GLIBC_2.0','fclose@GLIBC_2.2',
                  'access@GLIBC_2.0','rename@GLIBC_2.0','unlink@GLIBC_2.0','mkdir@GLIBC_2.0','memcmp@GLIBC_2.0',
                  'strlen@GLIBC_2.0','strstr@GLIBC_2.0','canvas_measure_text','canvas_draw_text',
                  'fork@GLIBC_2.0','execl@GLIBC_2.0','exit@GLIBC_2.0','waitpid@GLIBC_2.0','socket@GLIBC_2.0',
                  'sendto@GLIBC_2.0','close@GLIBC_2.0'):
            self.handlers[syms[n]]='b:'+n
        self.handlers[syms['mclGetOutputWay']]='b:mclGetOutputWay'
        self.handlers[payload_syms['child_exit']]='b:exit'
        self.u.hook_add(UC_HOOK_CODE,self.hook,begin=payload_syms['child_exit'],end=payload_syms['child_exit'])
        self.forked=4242; self.waited=0; self.way=0; self.execs=[]; self.sent=[]
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
            if 'w' in mode: self.files[path]=bytearray()
            if path in self.files: ret=self.alloc(4); self.handles[ret]=[path,0]
        elif name in ('fread','fwrite'):
            path,pos=self.handles[d]; f=self.files[path]
            if name=='fread': chunk=bytes(f[pos:pos+b*c]); self.u.mem_write(a,chunk)
            else: chunk=bytes(u.mem_read(a,b*c)); f[pos:pos+len(chunk)]=chunk
            self.handles[d][1]+=len(chunk); ret=len(chunk)//b
        elif name=='fseek':
            h=self.handles[a]; h[1]=signed(b)+(0,h[1],len(self.files[h[0]]))[c]; ret=0
        elif name=='ftell': ret=self.handles[a][1]
        elif name=='fclose': self.handles.pop(a)
        elif name=='access': ret=0 if self.text(a) in self.files or self.text(a) in self.tree else -1
        elif name=='rename': self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='unlink': self.files.pop(self.text(a),None)
        elif name=='memcmp': ret=0 if bytes(u.mem_read(a,c))==bytes(u.mem_read(b,c)) else 1
        elif name=='strlen': ret=len(self.text(a).encode())
        elif name=='strstr':
            i=self.text(a).encode().find(self.text(b).encode()); ret=a+i if i>=0 else 0
        elif name=='canvas_measure_text': u.reg_write(UC_MIPS_REG_F0,struct.unpack('<I',struct.pack('<f',BOOK_PITCH*c))[0])
        elif name=='mclGetOutputWay': ret=self.way
        elif name=='fork': ret=self.forked
        elif name=='execl':
            sp=u.reg_read(UC_MIPS_REG_SP); e=self.get(sp+16)  # a volume argv, then its terminator
            self.execs.append((self.text(a),self.text(b),self.text(c),self.text(d),e and (self.text(e),self.get(sp+20)))); ret=-1
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

# Books (patch/books.c): Local Music's last row opens the card's Books folder (case aside): its own
# .txt and .epub files and its subfolders', by path, named without the extension.
import io, zipfile
BOOK_PITCH=9
R='/mnt/mmc/books'
para=lambda i: ' '.join(f'w{i}x{k}' for k in range(30))
txt='﻿'+'\n\n'.join(para(i) for i in range(40))
buf=io.BytesIO()
with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
    z.writestr('mimetype','application/epub+zip')
    z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
    z.writestr('content.opf','<package><manifest><item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/></manifest>'
               '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
    z.writestr('a.xhtml','<html><head><title>T</title></head><body><h1>Chapter 1</h1><p>Caf&#233; &amp; tea.</p></body></html>')
    z.writestr('b.xhtml','<html><body><p>Chapter 2</p></body></html>')
tree={'/mnt/mmc':[('Music',4),('books',4)],R:[('Zed.txt',8),('notes.md',8),('._Zed.txt',8),('Series',4)],R+'/Series':[('A Tale.EPUB',8)]}
files={R+'/Zed.txt':bytearray(txt.encode()),R+'/Series/A Tale.EPUB':bytearray(buf.getvalue())}
m=BooksMachine(tree,files)
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
button=m.nodes[m.nodes[view]['children'][-1-GAMES]]['children'][0]; icon,label,into=m.nodes[button]['children']
assert m.nodes[icon]['image']=='local_books' and m.nodes[label]['text']=='Books' and len(m.nodes[view]['children'])==17+GAMES
library_geometry(m,m.nodes[view]['children'][-1-GAMES])
f,ctx=m.handler(button,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
page=m.page=m.top; assert m.nodes[page]['name']=='books_page' and m.nodes[page]['style:normal:bg_color']==-0x1000000
books,reader=m.nodes[page]['children']; sheet,info=m.nodes[reader]['children']
assert not m.nodes[reader]['visible'] and labels(books)==['Books','A Tale','Zed']; passed()
# A .txt opens on its first page: up to ten white lines of the default font, the BOM dropped,
# wrapped at spaces within the margins, every line clear of the glass's rounded corners.
rows=m.nodes[m.find('scroll_view',books)]['children']
def open_book(i):
    f,ctx=m.handler(rows[i],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
def page_lines():
    m.lines=[]; m.call(address=HOOKS['widget_on_paint_border'][0],args=(sheet,m.canvas,0,0),gap=0,clear=False,count=50_000_000)
    return m.lines
open_book(1); assert m.nodes[reader]['visible'] and not m.nodes[books]['visible'] and not m.nodes[info]['visible']
first=page_lines(); words=txt[1:].split()
assert len(first)==10 and all(c==0xffffffff and font==('default',20) for _,_,_,c,font in first)
assert ' '.join(t for t,*_ in first if t).split()==words[:len(' '.join(t for t,*_ in first).split())] and first[0][0].startswith('w0x0 ')
for t,x,y,*_ in first:
    top,bottom=30+y,30+y+26; inset=max(corner_inset(top),corner_inset(bottom))
    assert inset<=x and x+BOOK_PITCH*len(t)<=375-inset and len(t)*BOOK_PITCH<=375-2*x,(t,x,y)
assert [y for _,_,y,*_ in first]==[6+26*i for i in range(10)] and len(first[0][0])*BOOK_PITCH>375-2*24-BOOK_PITCH*6; passed()
# The wheel turns the page (the next starts where this one ended), back again, and stops at the start.
m.top=page; assert m.call()==11; second=page_lines()
seen=' '.join(t for t,*_ in first+second).split(); assert seen==words[:len(seen)] and second[0][0]!=first[0][0]
assert m.call(O['KEY_PREV'])==11 and page_lines()==first and m.call(O['KEY_PREV'])==11 and page_lines()==first; passed()
# Centre shows how far in and the title on a band clear of the glass; Return keeps the page.
m.call(); m.call(); third=page_lines()
assert m.call(O['KEY_CENTER'])==11 and m.nodes[info]['visible'] and re.fullmatch(r'\d%  Zed',m.nodes[info]['text'])
x,y,bw,bh=cf_geometry(m,info); inset=max(corner_inset(30+y),corner_inset(30+y+bh)); assert inset<=x and x+bw<=375-inset
assert m.key()==11 and m.nodes[books]['visible'] and '/mnt/data/ringnav-books' in m.files
assert m.nodes[m.find('scroll_view',books)]['_ringnav_index']==1
open_book(1); assert page_lines()==third; passed()
# An EPUB is made into text on the worker first ("Preparing…"), then read like a .txt: chapters
# on new pages, head dropped, entities decoded.
m.key(); open_book(0); assert m.nodes[info]['visible'] and m.nodes[info]['text']=='Preparing…' and len(m.threads)==1 and not page_lines()
worker,arg=m.threads[0]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=50_000_000)==0
m.advance(250); cache=f'/mnt/mmc/.books/{fnv(R+"/Series/A Tale.EPUB"):08x}.txt'
assert bytes(m.files[cache])=='Chapter 1\n\nCafé & tea.\f\nChapter 2'.encode() and not m.nodes[info]['visible']
assert [t for t,*_ in page_lines()]==['Chapter 1','','Café & tea.'] and m.call()==11 and [t for t,*_ in page_lines()]==['Chapter 2']
passed()
# A broken EPUB says so; closing joins nothing and frees the window.
m.key(); m.files[R+'/Series/A Tale.EPUB']=bytearray(b'PK junk'); m.files.pop(cache); open_book(0)
worker,arg=m.threads[-1]; m.call(address=worker,args=(arg,0,0,0),gap=0,count=50_000_000); m.advance(250)
assert m.nodes[info]['visible'] and m.nodes[info]['text']=="Can't open this book" and m.call()==11 and not page_lines()
m.close(); passed()

# Games (patch/games.c, iPod): the Library's Games row, always there, opens a chooser of Sudoku,
# Vortex, Solitaire and Snake. A game's keys never reach stock's filter or the media keys; Return goes game -> chooser ->
# Library. Vortex's frame timer and raw wheel run only while it plays: Play/Pause, the screen going
# off and leaving each stop them, and closing frees the state.
if variant=='ipod':
    CONFIG['SHORTCUT']='6'; m=BooksMachine({},{}); del CONFIG['SHORTCUT']
    G_SCREEN,G_PICKING,G_PAUSED,G_CELL,G_CUR,G_VX=36,44,56,76,157,164
    def games_open():
        view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
        assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
        button=m.nodes[m.nodes[view]['children'][-1]]['children'][0]; icon,label,into=m.nodes[button]['children']
        assert m.nodes[icon]['image']=='local_games' and m.nodes[label]['text']=='Games'
        library_geometry(m,m.nodes[view]['children'][-1])
        f,ctx=m.handler(button,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
        m.page=m.top; assert m.nodes[m.page]['name']=='games_page' and m.nodes[m.page]['style:normal:bg_color']==-0x1000000
        return m.nodes[m.page]['children']
    assert m.text(m.call(address=payload_syms['ipod_home_shortcut'],args=(0,0,0,0),gap=0))=='Games'
    assert m.call(address=payload_syms['home_shortcut_open'],args=(0,m.event,0,0),gap=0,count=5_000_000)==0
    m.page=m.top; assert m.nodes[m.page]['name']=='games_page'
    m.close()
    sheet,chooser=games_open(); G=m.get(payload_syms['g'])
    assert labels(chooser)==['Games','Sudoku','Vortex','Solitaire','Snake'] and not m.nodes[sheet]['visible']
    rows=m.nodes[m.find('scroll_view',chooser)]['children']
    def pick(i):
        f,ctx=m.handler(rows[i],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
    def paint(): m.call(address=HOOKS['widget_on_paint_border'][0],args=(sheet,m.canvas,0,0),gap=0,clear=False,count=50_000_000)
    stock=[]; tramp=int(manifest['patch_symbols']['stock_keyup_trampoline'],16)
    for a in (tramp,payload_syms['media_key']): m.u.hook_add(UC_HOOK_CODE,lambda u,a,n,_: stock.append(a),begin=a,end=a)
    def key(k): r=m.call(k,count=2_000_000); assert r==11 and not stock,(k,stock); return r
    byte=lambda o: m.u.mem_read(G+o,1)[0]; word=lambda o: signed(m.get(G+o))
    frames=lambda: [t for t in m.timers.values() if t[1]==payload_syms['frame']]
    pick(0); assert m.nodes[sheet]['visible'] and not m.nodes[chooser]['visible'] and word(G_SCREEN)==1; paint()
    key(O['KEY_NEXT']); assert byte(G_CUR)==1
    key(O['KEY_PREV']); key(O['KEY_PREV']); assert byte(G_CUR)==80
    key(O['KEY_FWD_BTN']); assert byte(G_CUR)==8
    key(O['KEY_BACK_BTN']); assert byte(G_CUR)==80; passed()
    # Centre on a blank cell picks its digit with the wheel, Centre writes it and saves; Return cancels; a
    # clue takes no digit. Play/Pause then asks before a new game.
    cells=lambda: bytes(m.u.mem_read(G+G_CELL,81))
    blank=cells().index(0); clue=next(i for i,c in enumerate(cells()) if c&16)
    m.byte(G+G_CUR,clue); key(O['KEY_CENTER']); assert not word(G_PICKING)
    m.byte(G+G_CUR,blank); key(O['KEY_CENTER']); assert word(G_PICKING)
    for _ in range(3): key(O['KEY_NEXT'])
    paint(); key(O['KEY_CENTER']); assert cells()[blank]==3 and not word(G_PICKING)
    m.advance(2000); saved=bytes(m.files['/mnt/data/ringnav-sudoku']); assert len(saved)==92 and saved[4:85]==cells()
    key(O['KEY_CENTER']); key(O['KEY_NEXT']); key(O['KEY_RETURN']); assert cells()[blank]==3 and not word(G_PICKING) and word(G_SCREEN)==1
    before=cells(); key(O['KEY_PLAY']); assert cells()==before and word(52); paint()
    key(O['KEY_RETURN']); assert cells()==before and not word(52)
    key(O['KEY_RETURN']); m.advance(0); assert word(G_SCREEN)==0 and m.nodes[chooser]['visible'] and not m.nodes[sheet]['visible']
    assert m.key()==11; passed()
    # Vortex: the frame timer and the raw wheel while it plays; the wheel's travel turns the paddle.
    m.page=m.top; pick(1); assert word(G_SCREEN)==2 and len(frames())==1 and m.get(payload_syms['wheel_raw'])==1
    m.word(payload_syms['wheel_travel'],-50&0xffffffff); m.advance(34); assert word(G_VX+16)==0 and m.get(payload_syms['wheel_travel'])==0
    made=lambda *a: [c[0] for c in m.calls if c[0] in a]
    assert made('reset_poweroptions_timer') and made('widget_invalidate_force')
    # Idle, waiting to launch: no repaint and the screen's timeout runs; the ball in play repaints, still
    # leaving the screen to time out; a key the game ignores paints nothing.
    m.advance(500); assert not made('reset_poweroptions_timer','widget_invalidate_force')
    key(O['KEY_CENTER']); m.advance(500); assert word(G_VX)==1 and made('widget_invalidate_force') and not made('reset_poweroptions_timer'); paint()
    key(O['KEY_FWD_BTN']); assert not made('widget_invalidate_force')
    key(O['KEY_PLAY']); assert word(G_PAUSED)==1 and not frames() and m.get(payload_syms['wheel_raw'])==0
    x=word(G_VX+20),word(G_VX+24); m.advance(500); assert (word(G_VX+20),word(G_VX+24))==x; paint()
    key(O['KEY_PLAY']); assert word(G_PAUSED)==0 and len(frames())==1 and m.get(payload_syms['wheel_raw'])==1
    m.byte(syms['g_backlight_status'],0); m.advance(40); m.byte(syms['g_backlight_status'],1)
    assert word(G_PAUSED)==1 and not frames() and m.get(payload_syms['wheel_raw'])==0
    key(O['KEY_RETURN']); assert word(G_PAUSED)==2; key(O['KEY_NEXT']); paint(); key(O['KEY_CENTER']); m.advance(0)
    assert word(G_SCREEN)==0 and not frames() and m.get(payload_syms['wheel_raw'])==0; passed()
    # Solitaire (piles 0 stock, 1 waste, 2-5 foundations, 6-12 columns; a card suit*13+rank, 64 up): Centre on
    # the stock draws and saves; the wheel walks the piles, Previous/Next jump between the rows; Centre picks a
    # run up and drops it where it fits, turning the card under it; Centre twice sends a card home; Return
    # drops the held run, then leaves.
    G_SOL=220; sol=lambda: bytes(m.u.mem_read(G+G_SOL,336)); n=lambda p: byte(G_SOL+316+p); cur=lambda: byte(G_SOL+329)
    def put(p,cards): m.u.mem_write(G+G_SOL+4+24*p,bytes(cards)); m.byte(G+G_SOL+316+p,len(cards))
    m.page=m.top; pick(2); assert word(G_SCREEN)==3 and cur()==0 and [n(p) for p in range(13)]==[24,0,0,0,0,0,1,2,3,4,5,6,7]; paint()
    key(O['KEY_CENTER']); m.advance(2000); assert (n(0),n(1))==(23,1) and bytes(m.files['/mnt/data/ringnav-solitaire'])==sol()
    key(O['KEY_NEXT']); assert cur()==1; key(O['KEY_FWD_BTN']); assert cur()==7; key(O['KEY_BACK_BTN']); assert cur()==1
    key(O['KEY_PREV']); key(O['KEY_PREV']); assert cur()==12; key(O['KEY_BACK_BTN']); assert cur()==5; passed()
    put(6,[64|13+11]); put(7,[3,64|10]); m.byte(G+G_SOL+329,7)  # Q of hearts; a face-down 4 under the J of spades
    key(O['KEY_CENTER']); assert word(G_PICKING) and word(48)==7; paint()
    key(O['KEY_FWD_BTN']); key(O['KEY_CENTER']); assert word(G_PICKING)  # not onto the waste
    key(O['KEY_FWD_BTN']); key(O['KEY_PREV']); key(O['KEY_CENTER']); assert not word(G_PICKING); m.advance(2000)
    assert (n(6),n(7),byte(G_SOL+4+24*6+1),byte(G_SOL+4+24*7))==(2,1,64|10,64|3) and bytes(m.files['/mnt/data/ringnav-solitaire'])==sol()
    put(1,[64|26]); m.byte(G+G_SOL+329,1); key(O['KEY_CENTER']); key(O['KEY_CENTER'])  # the Ace of diamonds, home
    assert not word(G_PICKING) and n(1)==0 and n(2)==1 and byte(G_SOL+4+48)==64|26
    m.byte(G+G_SOL+329,6); key(O['KEY_CENTER']); key(O['KEY_CENTER']); assert not word(G_PICKING) and n(6)==2  # nothing home: put back
    key(O['KEY_CENTER']); key(O['KEY_RETURN']); assert not word(G_PICKING) and word(G_SCREEN)==3
    before_sol=sol(); key(O['KEY_PLAY']); assert word(52); paint(); key(O['KEY_RETURN']); assert not word(52) and sol()==before_sol
    key(O['KEY_RETURN']); m.advance(0); assert word(G_SCREEN)==0 and m.nodes[chooser]['visible']; passed()
    # Snake (the body a ring of cells y*20+x, head last; dir 0 up 1 right 2 down 3 left): its frame timer steps it
    # with side keys only; wheel ticks are consumed without changing the game. One turn waits for the next step. Play/Pause
    # pauses, Return's menu quits; a wall ends it, a record saves Best and says so, Centre plays again.
    G_SN=G_SOL+336+52+12; sn=lambda o: m.get(G+G_SN+o)&0xffff; body=lambda: [sn(2*((sn(800)+i)%400)) for i in range(sn(802))]
    def skey(k): r=m.call(k,gap=0,count=2_000_000); assert r==11 and not stock,(k,stock)  # no time between keys
    sdir=lambda: byte(G_SN+806); sstate=lambda: byte(G_SN+808)
    m.page=m.top; pick(3); assert word(G_SCREEN)==4 and len(frames())==1 and m.get(payload_syms['wheel_raw'])==0
    assert body()==[207,208,209] and sdir()==1 and sstate()==1; paint()
    snake_before=bytes(m.u.mem_read(G+G_SN,820))
    skey(O['KEY_NEXT']); skey(O['KEY_PREV']); assert bytes(m.u.mem_read(G+G_SN,820))==snake_before
    skey(O['KEY_FWD_BTN']); skey(O['KEY_FWD_BTN']); assert sdir()==2
    m.advance(170); assert body()[-1]==229 and sdir()==3 and made('widget_invalidate_force')  # the second turn, waiting
    skey(O['KEY_BACK_BTN']); assert sdir()==3  # waits for the next step
    skey(O['KEY_PLAY']); assert word(G_PAUSED)==1 and not frames(); was=body(); m.advance(500); assert body()==was; paint()
    skey(O['KEY_NEXT']); assert sdir()==3  # no turning while paused
    skey(O['KEY_PLAY']); assert word(G_PAUSED)==0 and len(frames())==1
    m.word(G+G_SN+812,5); m.advance(170*12); assert sstate()==2 and not frames() and body()[-1]==388  # left, then down to the wall
    best=bytes(m.files['/mnt/data/ringnav-snake']); assert len(best)==12 and best[:8]==b'SNK1'+struct.pack('<I',5)
    paint(); skey(O['KEY_CENTER']); assert sstate()==1 and body()==[207,208,209] and len(frames())==1
    skey(O['KEY_BACK_BTN']); assert sdir()==0  # side Previous turns left, without the wheel
    m.advance(170); assert body()[-1]==189
    skey(O['KEY_FWD_BTN']); assert sdir()==1  # side Next turns right
    skey(O['KEY_PLAY']); skey(O['KEY_BACK_BTN']); skey(O['KEY_FWD_BTN']); assert sdir()==1
    skey(O['KEY_PLAY'])
    skey(O['KEY_RETURN']); assert word(G_PAUSED)==2 and word(60)==0
    skey(O['KEY_NEXT']); assert word(60)==1  # the wheel picks in the menu
    skey(O['KEY_PREV']); assert word(60)==0
    skey(O['KEY_BACK_BTN']); assert word(60)==1
    skey(O['KEY_FWD_BTN']); assert word(60)==0
    skey(O['KEY_FWD_BTN']); paint(); skey(O['KEY_CENTER']); m.advance(0)
    assert word(G_SCREEN)==0 and not frames(); passed()
    # Closing frees the state, also mid-game; opening again starts clean and resumes the Sudoku.
    m.close(); assert m.get(payload_syms['g'])==0 and not frames()
    sheet,chooser=games_open(); G=m.get(payload_syms['g']); rows=m.nodes[m.find('scroll_view',chooser)]['children']
    pick(0); assert cells()==before
    key(O['KEY_RETURN']); m.advance(0); pick(2); assert sol()[:-4]==before_sol[:-4]  # and the cursor, kept on leaving
    # Moves save once, SAVE_MS after the first unsaved one, not per move; leaving with nothing new writes nothing.
    saves=[]; m.u.hook_add(UC_HOOK_CODE,lambda u,a,n,_: saves.append(a),begin=syms['rename@GLIBC_2.0'],end=syms['rename@GLIBC_2.0'])
    m.byte(G+G_SOL+329,0)
    for _ in range(10): m.call(O['KEY_CENTER'],gap=100,count=2_000_000)
    assert not saves; m.advance(2000); assert len(saves)==1 and bytes(m.files['/mnt/data/ringnav-solitaire'])==sol()
    key(O['KEY_RETURN']); m.advance(0); pick(0); key(O['KEY_RETURN']); m.advance(0); assert len(saves)==1; pick(2)
    key(O['KEY_RETURN']); m.advance(0); pick(1); assert len(frames())==1
    m.close(); assert m.get(payload_syms['g'])==0 and not frames() and m.get(payload_syms['wheel_raw'])==0; passed()

# Videos (patch/books.c, docs/internals.md#videos): the card's Videos folder (case aside) lists its and
# its subfolders' video files as Books does; a row stops the music and starts /usr/bin/q2video with
# the headphone DAC's PCM and the path as plain argv, never through a shell.
R='/mnt/mmc/VIDEOS'
tree={'/mnt/mmc':[('Music',4),('VIDEOS',4)],R:[('b.MKV',8),('a.mp4',8),('notes.txt',8),('._a.mp4',8),('Trip',4)],R+'/Trip':[('c.avi',8)]}
m=BooksMachine(tree,{})
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
button=m.nodes[m.nodes[view]['children'][-1-GAMES]]['children'][0]; icon,label,into=m.nodes[button]['children']
assert m.nodes[icon]['image']=='local_videos' and m.nodes[label]['text']=='Videos' and len(m.nodes[view]['children'])==17+GAMES
f,ctx=m.handler(button,O['EVT_CLICK']); m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']==['folder_page']; m.page=m.node('window','folder_page'); passed()
# Folder view's video (books_video) starts the same q2video, with no Videos page open; anything else is left to stock.
for path,ok in ((R+'/b.MKV',1),(R+'/notes.txt',0),(R+'/._a.mp4',0)):
    v=BooksMachine(tree,{}); assert v.call(address=payload_syms['books_video'],args=(v.string(path),0,0,0),gap=0)==ok,path
    v=BooksMachine(tree,{}); v.forked=0; v.call(address=payload_syms['books_video'],args=(v.string(path),0,0,0),gap=0)  # the child
    assert v.execs==([('/usr/bin/q2video','/usr/bin/q2video','plughw:0,0',path,0)] if ok else []),path
passed()
def play(path):  # Folder view's row (books_video), with the child run as the fork's
    m.calls=[]; m.call(address=payload_syms['books_video'],args=(m.string(path),0,0,0),gap=0)
    return [c[0] for c in m.calls]
# The headphone output is readied as stock's player_start readies it (config_outputchannel, PCM
# mode, unmuted, the volume) before q2video starts; a DAC check_dacoff_state powered off comes on with it. Bluetooth (way 1) plays on hciplayer's
# plug:bluealsa with the volume for the helper's soft volume. A USB DAC (2) plays on "usb" (q2video
# finds its card), through plughw: with the volume marked h (the DAC's own control, or a fixed USB volume),
# or plain when the USB volume is variable and the DAC has no control (USB_MIXER -2: hciplayer's soft volume).
m.forked=0; m.word(syms['g_dacoff_time'],0xffffffff); m.byte(syms['g_headset_output'],1); names=play(R+'/b.MKV')
take=[n for n in names if n in ('player_stop','config_outputchannel','mclSetPcmMode','mclSetMute','device_set_volume','fork')]
assert take==['player_stop','config_outputchannel','mclSetPcmMode','mclSetMute','device_set_volume','fork'] and m.execs[-1][2:5]==('plughw:0,0',R+'/b.MKV',0)
assert ('config_outputchannel',1,2) in [c[:3] for c in m.calls]
m.word(syms['g_dacoff_time'],5); m.byte(syms['g_volume'],42)
m.way=1; names=play(R+'/Trip/c.avi'); assert 'mclSetDacPwr' not in names and 'config_outputchannel' not in names and m.execs[-1][2:]==('plug:bluealsa',R+'/Trip/c.avi',('42',0))
m.way=2; m.byte(syms['g_usbvol_mode'],0); m.word(O['USB_MIXER'],0xfffffffe)
assert 'mclSetDacPwr' not in play(R+'/Trip/c.avi') and m.execs[-1][2:]==('usb',R+'/Trip/c.avi',('h42',0))
m.byte(syms['g_usbvol_mode'],1); m.word(O['USB_MIXER'],0); play(R+'/Trip/c.avi'); assert m.execs[-1][2:]==('usb',R+'/Trip/c.avi',('h42',0))
m.word(O['USB_MIXER'],0xfffffffe); play(R+'/Trip/c.avi'); assert m.execs[-1][2:]==('usb',R+'/Trip/c.avi',('42',0))
passed()
# Playing: no key or touch reaches the UI; a key's release goes to the player's socket as a datagram:
# Play/Pause pauses, the side buttons seek, Return quits.
m.way=0; m.forked=4242; play(R+'/Trip/c.avi')
# The helper starts at the output's level: the first poll sends nothing (no volume bar flash); a change sends v then g.
def vpoll(): m.now+=500; m.sent=[]; m.call(address=payload_syms['video_poll'],args=(0,0,0,0),gap=0,clear=False); return [x[1] for x in m.sent]
assert vpoll()==[]; m.byte(syms['g_volume'],43); assert vpoll()==['v+','g+']; passed()
inp=HOOKS['window_manager_dispatch_input_event'][0]; m.handlers[inp+12]='stock_input'
def event(kind,key=0):
    m.calls=[]; m.sent=[]
    assert m.call(key,address=inp,args=(m.wm,m.event,0,0),event_type=kind,gap=0,clear=False)==0
    return [c[0] for c in m.calls if c[0]!='time_now_ms']  # the hook times Low power's idle, not input
for key,c in ((O['KEY_PLAY'],'p'),(O['KEY_FWD_BTN'],'f'),(O['KEY_BACK_BTN'],'b'),(O['KEY_RETURN'],'q')):
    assert 'stock_input' not in event(O['EVT_KEY_UP'],key) and m.sent==[(7,c,1,0x40,1,'/tmp/q2video.sock',110)]
assert event(0x110,O['KEY_CENTER'])==[] and event(O['EVT_POINTER_DOWN'])==[] and not m.sent; passed()
# The wheel is the volume, as stock's volume dialog steps it: 1 a tick through device_set_volume, saved as
# PLAYSET VOLUME, up to g_maxvolume (and 100) and down to 0; each tick sends v and the volume, at an end too.
def key(k):
    names=event(O['EVT_KEY_UP'],k); assert 'stock_input' not in names and m.sent[0][2]==len(m.sent[0][1])
    assert len(m.sent)==(2 if m.sent[0][1].startswith('v') else 1)
    if len(m.sent)==2: assert m.sent[1][1]=='g'+m.sent[0][1][1]
    vol=[c[1:3] for c in m.calls if c[0]=='device_set_volume']; saved=[(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
    assert len(vol)==len(saved)<=1 and all(v==(s[0],1) and s[1:]==('PLAYSET','VOLUME') for v,s in zip(vol,saved))
    return m.sent[0][1],vol and vol[0][0]
m.byte(syms['g_volume'],50); m.byte(syms['g_maxvolume'],52)
assert [key(O['KEY_NEXT']) for _ in range(3)]==[('v\x33',51),('v\x34',52),('v\x34',[])] and m.u.mem_read(syms['g_volume'],1)[0]==52
m.byte(syms['g_maxvolume'],100); m.byte(syms['g_volume'],100); assert key(O['KEY_NEXT'])==('v\x64',[])
m.byte(syms['g_volume'],1); assert [key(O['KEY_PREV']) for _ in range(2)]==[('v\0',0),('v\0',[])]; passed()
# Centre toggles the wheel to seeking (s, then f and b, no volume) and back (v); seeking also ends
# SCRUB_MS after the last tick, on the UI loop's poll.
m.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
def poll(ms):
    m.now+=ms; m.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=0,clear=False)
m.byte(syms['g_volume'],30)
assert key(O['KEY_CENTER'])==('s',[]) and key(O['KEY_NEXT'])==('f',[]) and key(O['KEY_PREV'])==('b',[])
poll(O['SCRUB_MS']-1); assert key(O['KEY_NEXT'])==('f',[]) and key(O['KEY_FWD_BTN'])==('f',[]) and key(O['KEY_PLAY'])==('p',[])
poll(O['SCRUB_MS']-1); assert key(O['KEY_NEXT'])==('f',[]); poll(O['SCRUB_MS']); assert key(O['KEY_NEXT'])==('v\x1f',31)
assert key(O['KEY_CENTER'])==('s',[]) and key(O['KEY_CENTER'])==('v\x1f',[]) and key(O['KEY_PREV'])==('v\x1e',30); passed()
# Nor does the window manager paint over it; the screen, standby and DAC power-off timeouts are held off.
vt=m.alloc(16); m.word(m.wm+0x94,vt); m.word(vt+0xc,0x400100); m.handlers[0x400100]='wm_vt_paint'
def wm_paint():
    m.calls=[]; m.call(address=WM_PAINT_LEAF[1],args=(m.wm,0,0,0),gap=0,clear=False); return [c[0] for c in m.calls]
assert wm_paint()==[]
sleep_hook=HOOKS['main_loop_sleep_default'][0]; m.handlers[sleep_hook+12]='stock_sleep'
m.calls=[]; m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False)
assert ('reset_poweroptions_timer',1,1,1) in [c[:4] for c in m.calls] and m.get(syms['g_dacoff_time'])==0 and 'widget_invalidate_force' not in [c[0] for c in m.calls]
# Its end: the socket closes, the whole screen repaints once, and the UI has its input and paint again.
m.waited=4242; m.calls=[]; m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False)
assert ('close',7) in [c[:2] for c in m.calls] and ('widget_invalidate_force',m.wm,0) in [c[:3] for c in m.calls]
assert 'stock_input' in event(O['EVT_KEY_UP'],O['KEY_RETURN']) and not m.sent and wm_paint()==['wm_vt_paint']
m.calls=[]; m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False); assert 'waitpid' not in [c[0] for c in m.calls]
passed()

# Spotify (patch/spotify.c, docs/internals.md#spotify): librespot from the card's .spotify folder, its
# state from the q2-librespot fork's --status-file, its commands to --control-socket.
STATE,SOCK='/tmp/q2-librespot.state','/tmp/q2-librespot.sock'
class SpotMachine(BooksMachine):
    """BooksMachine's card and files, plus librespot's side: system() starts, the monotonic clock is
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
        elif name=='clock_gettime': assert a==1; self.word(b,self.now//1000); self.word(b+4,self.now%1000*1000000)
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

# The reentrant clock import resolves tzset's lazy GOT slot once (no shared buffer written), then
# calls localtime_r with the caller's buffer, preserving both arguments and the return address.
from build import run
clock_got=next(int(w[0],16) for line in run('readelf','-AW',B/'stock-demo').splitlines()
               if len(w:=line.split())>=6 and 'UND' in w and w[-1].split('@')[0]=='tzset')
class ClockImportMachine(Machine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='tzset@GLIBC_2.0':
            self.word(clock_got,syms['tzset@GLIBC_2.0']+4+7724)  # loader's resolved tzset
        super().hook(u,address,size,unused)
m=ClockImportMachine(); del m.handlers[payload_syms['peq_lib_localtime_r']]
m.handlers[syms['tzset@GLIBC_2.0']+4]='localtime_r'
now=m.alloc(4); buf=m.alloc(44); m.word(now,m.wall[1])
assert m.call(address=payload_syms['peq_lib_localtime_r'],args=(now,buf,0,0),gap=0)==buf
assert m.get(buf+8)==18 and m.get(buf+4)==14
m.word(clock_got,0); m.clock=(7,30)
assert m.call(address=payload_syms['peq_lib_localtime_r'],args=(now,buf,0,0),gap=0)==buf
assert m.get(buf+8)==7 and m.get(buf+4)==30; passed()

# Every saved timezone index applies its TZ; stock's indices keep their city.
from build import TIMEZONES, TIMEZONE_ORDER, timezone_offset
class TimezoneMachine(Machine):
    def __init__(self):
        super().__init__()
        self.mock('strcpy@GLIBC_2.0')
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='strcpy@GLIBC_2.0': return super().hook(u,address,size,unused)
        a,b=[u.reg_read(r) for r in REGS[:2]]
        self.u.mem_write(a,self.text(b).encode()+b'\0'); self.returns(a,clobber=True)
zones=TimezoneMachine()
assert TIMEZONES[22]==('GMT+8 Shanghai','Asia/Shanghai') and TIMEZONES[6][1]=='CST6' and TIMEZONES[27][1]=='Pacific/Marquesas'
for index, (_, name) in enumerate(TIMEZONES):
    zones.byte(syms['g_time_zone'],index)
    zones.call(address=HOOKS['select_timezone'][0],args=(0,0,0,0),gap=0)
    assert [zones.text(c[1]) for c in zones.calls if c[0]=='toolsSetTimeZone']==[name]
    assert len([c for c in zones.calls if c[0]=='tzset@GLIBC_2.0'])==1
passed()
# autotime_page's zone rows show the offset each zone has now and run west to east by it, ties in
# TIMEZONE_ORDER; each is built, ticked and named (image_choice) by its saved index, then the saved
# zone is set again. Here New York and Halifax are on daylight saving. A label too long for the row keeps its ellipsis rather than scrolling once.
assert [timezone_offset(TIMEZONES[i][0]) for i in TIMEZONE_ORDER]==sorted(timezone_offset(l) for l,_ in TIMEZONES)
SUMMER={'America/New_York','America/Halifax'}
class ZoneRowMachine(TimezoneMachine):
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address)
        if name=='toolsSetTimeZone': self.tz=self.text(u.reg_read(REGS[0]))
        if name!='localtime_r': return super().hook(u,address,size,unused)
        self.calls.append((name,)); label=next(l for l,z in TIMEZONES if z==self.tz); tm=u.reg_read(REGS[1])
        self.word(tm+36,(timezone_offset(label)*60+3600*(self.tz in SUMMER))&0xffffffff)
        self.returns(tm,clobber=True)
zones=ZoneRowMachine(); zones.tz=None
zones.mock('image_create','hscroll_label_create','widget_use_style','widget_set_name','toolsSetTimeZone',
           'image_set_draw_type','image_base_set_image','set_hscroll_label_attribute','widget_set_tr_text','widget_on','tk_snprintf')
zone_now={i:timezone_offset(l)+60*(z in SUMMER) for i,(l,z) in enumerate(TIMEZONES)}
zone_rows=sorted(TIMEZONE_ORDER,key=lambda i:zone_now[i])
def zone_label(i):
    m=abs(zone_now[i]); return f"GMT{'-' if zone_now[i]<0 else '+'}{m//60}{f':{m%60:02d}' if m%60 else ''} {TIMEZONES[i][0].split(' ',1)[1]}"
saved=zone_rows[5]; zones.byte(syms['g_time_zone'],saved)
for row in range(1,len(TIMEZONES)+1):
    item=zones.node('list_item'); zones.calls=[]; zones.call(address=HOOKS['timezone_row'][0],args=(item,row,0,0),gap=0)
    if row==1:
        assert len([c for c in zones.calls if c[0]=='tzset@GLIBC_2.0'])==len(TIMEZONES)+1
        assert [zones.text(c[1]) for c in zones.calls if c[0]=='toolsSetTimeZone']==[z for _,z in TIMEZONES]+[TIMEZONES[saved][1]]
    index=zone_rows[row-1]; button,=zones.nodes[item]['children']; label,image=zones.nodes[button]['children']
    assert zones.nodes[button]['name']==str(index+1)  # the click handler's atoi, less one, is the saved index
    assert zones.nodes[label]['text']==zone_label(index) and zones.nodes[label]['hscroll_label_set_only_focus']==1
    assert zones.nodes[image]['name']==f'image_choice{index}' and zones.nodes[image]['image']==('select' if index==saved else 'unselect')
zone_labels=[zone_label(i) for i in zone_rows]
assert zone_labels.index('GMT-4 New York')<zone_labels.index("GMT-3:30 St. John's")<zone_labels.index('GMT-3 Halifax')<zone_labels.index('GMT-3 Buenos Aires')
assert 'GMT-5 Chicago' not in zone_labels and 'GMT-6 Chicago' in zone_labels
passed()
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
    assert m.nodes[m.top]['name']=='tidal_downloads' and 'No downloads yet' in [n.get('text') for n in m.nodes.values()]
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
def texts(w): return [t for t in labels(w) if t is not None]
BIN='/mnt/mmc/.spotify/librespot'
# The row follows stock's, with its icon. Without librespot on the card the page says so and
# nothing starts.
m=SpotMachine({}); button=stream_page(m)
icon,label=m.nodes[button]['children'][:2]
assert m.nodes[icon]['image']=='stream_spotify' and m.nodes[label]['text']=='Spotify' and not m.nodes[button].get('name')
page=spot_open(m,button); info,msg=m.nodes[page]['children']
assert not m.nodes[info]['visible'] and m.nodes[msg]['visible'] and texts(msg)==["Spotify isn't on the card",'See the Q2 Pod guide to add it']
m.poll(); assert not m.systems; m.close(); passed()
# With it, the first open starts it once (its launcher backgrounds itself); with no state yet, or no
# Connect session, the page waits for a confirmed authentication response.
m=SpotMachine({BIN:bytearray()}); button=stream_page(m); page=spot_open(m,button); info,msg=m.nodes[page]['children']
assert m.systems==['/bin/sh /mnt/mmc/.spotify/run'] and texts(msg)==['Loading…','Waiting for Spotify']
m.state('none',track=''); m.poll(); m.advance(250); assert m.nodes[msg]['visible'] and not m.nodes[info]['visible']
m.close(); spot_open(m,button); assert len(m.systems)==1; m.close(); passed()
# Until the row is opened nothing Spotify runs, a saved login on the card or not: no start, no
# state read, no held timers.
bare=SpotMachine({}); m=SpotMachine({BIN:bytearray(),'/mnt/mmc/.spotify/cache/credentials.json':bytearray()})
m.state('playing')
for _ in range(3): assert m.poll()==bare.poll()  # the same calls as a card without Spotify
assert not m.systems; passed()
# Playing: local music stops and the headphone output is set up as stock's AirPlay page does it,
# once; meanwhile standby and auto power-off (not the screen's timer) are held and the DAC kept on.
m=SpotMachine({BIN:bytearray()}); button=stream_page(m); page=spot_open(m,button); info,msg=m.nodes[page]['children']
m.byte(syms['g_headset_output'],1); m.byte(syms['g_volume'],37); m.play=3; m.word(syms['g_dacoff_time'],5)
m.state('playing',position=10000); names=m.poll()
take=[(c[0],*c[1:3]) for c in m.calls if c[0] in ('player_stop','config_outputchannel','mclSetPcmMode','mclSetMute','device_set_volume','mclSetDacPwr')]
assert [t[0] for t in take]==['player_stop','config_outputchannel','mclSetPcmMode','mclSetMute','device_set_volume']
assert take[1][1:]==(1,2) and take[3][1]==0 and take[4][1:]==(37,1)
assert ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls] and m.get(syms['g_dacoff_time'])==0
m.play=1; m.word(syms['g_dacoff_time'],3); names=m.poll()
assert 'config_outputchannel' not in names and 'reset_poweroptions_timer' in names and m.get(syms['g_dacoff_time'])==0
# Bluetooth's way is left alone: a DAC check_dacoff_state powered off is only powered on.
m.state('paused'); m.poll(); m.byte(syms['g_headset_output'],2); m.word(syms['g_dacoff_time'],0xffffffff); m.state('playing'); m.poll()
assert 'config_outputchannel' not in [c[0] for c in m.calls] and ('mclSetDacPwr',1) in [c[:2] for c in m.calls]; passed()
# The card's aplay.sh plays where Videos would: no SPOT_OUT on the headphone DAC; Bluetooth's
# plug:bluealsa and the volume, rewritten when it changes, which a running q2video sink also gets.
OUT='/tmp/q2-librespot.out'; assert OUT not in m.files
m.way=1; m.byte(syms['g_volume'],42); m.poll(); assert m.files[OUT]==b'plug:bluealsa 42\n' and m.sent[-1][1::4]==('v*','/tmp/q2sink.sock')
m.poll(); assert not m.sent; m.byte(syms['g_volume'],43); m.poll(); assert m.files[OUT]==b'plug:bluealsa 43\n'
m.way=0; m.poll(); assert OUT not in m.files; passed()
# Swap alone selects the same headphone PCM helper as Mono Audio.
sw=SpotMachine({BIN:bytearray(b'bin')}); sw.config['SWAPLR']='1'; sw.byte(syms['g_volume'],42)
spot_open(sw,stream_page(sw)); sw.now+=500
sw.call(address=payload_syms['spot_poll'],args=(0,0,0,0),gap=0)
assert sw.files[OUT]==b'plughw:0,0 h42\n'; passed()
# The page: the art (librespot's cover sized by Coverflow's thumbnailer, once a track), title, artist
# and album, the position moving on from at= and the remaining time, all clear of the glass.
m.state('playing',position=10000,at=m.now+500,cover=1); m.files[STATE+'.jpg']=bytearray(b'cover'); m.poll(); m.advance(0)
assert m.nodes[info]['visible'] and not m.nodes[msg]['visible']
assert m.thumbs==[(STATE+'.jpg','/tmp/q2spot.jpg',166,166)] and 'file:///tmp/q2spot.jpg' in [m.text(c[2]) for c in m.calls if c[0]=='widget_load_image']
art=next(w for w in m.nodes if under(w,info) and m.nodes[w]['type']=='image')
assert ('widget_invalidate_force',art) in [c[:2] for c in m.calls]
assert texts(info)==['Spotify','Song','A, B','LP',mmss('0:10'),mmss('-3:10')]
for value in ('Song','A, B','LP'):
    w=next(w for w in m.nodes if under(w,info) and m.nodes[w].get('text')==value)
    assert m.nodes[w]['hscroll_label_set_only_focus']==0 and m.nodes[w]['hscroll_label_set_ellipses']==0
for text, rgb in [('Song', 0xffffff), ('A, B', O['NP_ARTIST_RGB'] if variant=='ipod' else 0xaaaaaa), ('LP', O['NP_ALBUM_RGB'] if variant=='ipod' else 0xaaaaaa)]:
    label=next(w for w in m.nodes if under(w,info) and m.nodes[w].get('text')==text)
    assert m.nodes[label]['style:normal:text_color']==signed(color_t(rgb))
m.advance(2500); assert texts(info)[4:]==[mmss('0:12'),mmss('-3:08')] and len(m.thumbs)==1
# Every replacement at the same file URL repaints the entire image, even if the URL is unchanged.
for track in ('t2','t3'):
    m.state('playing',position=12500,at=m.now+500,cover=1,track=track); m.poll()
    m.call(address=payload_syms['spot_refresh'],args=(0,0,0,0),gap=0)
    assert ('widget_invalidate_force',art) in [c[:2] for c in m.calls]
assert len(m.thumbs)==3
for w in [x for x in m.nodes if m.nodes[x]['type'] in ('hscroll_label','image','view') and under(x,info) and x!=info]:
    x,y,bw,bh=cf_geometry(m,w); inset=max(corner_inset(30+y),corner_inset(30+y+bh))  # the window starts at y 30
    assert inset<=x and x+bw<=375-inset,(m.nodes[w].get('text'),x,y,bw,bh)
# The bar: a TRACK_COLOR capsule and the elapsed share in the accent (iPod) or stock red (Stock).
bar_height=4 if variant=='ipod' else 8  # iPod Minimal's line, at the top of the 8px view
bar=[w for w in m.nodes if m.nodes[w]['type']=='view' and cf_geometry(m,w)==(21,251,333,8)][0]
m.rounded=[]; m.call(address=HOOKS['widget_on_paint_border'][0],args=(bar,m.canvas,0,0),gap=0,clear=False)
track,fill=m.rounded
assert track['rect']==(0,0,333,bar_height) and track['color']==color_t(O['TRACK_COLOR']) and fill['rect']==(0,0,(12500>>4)*333//((200000>>4)|1),bar_height)
assert fill['color']==color_t(O['MINIMAL_FILL'] if variant=='ipod' else O['STOCK_RED']); passed()
# Play/Pause and the side buttons are Spotify's, on any page, after stock's key-lock filter, while it
# is the last thing played; stock's downstream handler never sees them.
m.top=m.node('window','home_page')
for k,c in ((O['KEY_PLAY'],'p'),(O['KEY_FWD_BTN'],'n'),(O['KEY_BACK_BTN'],'b')):
    assert m.key(k)==11 and m.sent==[(m.sent[0][0],c,1,0x40,1,SOCK,110)]
m.state('paused'); m.poll(); assert m.key(O['KEY_PLAY'])==11 and m.sent[0][1]=='p'
# Local music playing takes them back.
m.play=2; m.poll(); assert m.key(O['KEY_PLAY'])!=11 and not m.sent; passed()
# Local music starting (mclStartPlayer) while Spotify plays pauses it first and waits until
# librespot's aplay is gone (it says paused); otherwise it starts at once.
m.play=1; m.state('playing'); m.poll()
def start():
    m.calls=[]; m.sent=[]; m.call(address=HOOKS['mclStartPlayer'][0],args=(0,0,0,0),gap=0,clear=False,count=5_000_000)
    return [c[0] for c in m.calls]
m.on_sleep=lambda: m.state('paused') if m.now>=t0+60 else None; t0=m.now
names=start(); assert m.sent[0][1]=='s' and names[-1]=='stock_start_player' and 60<=m.now-t0<=100
m.on_sleep=None; names=start(); assert not m.sent and names.count('sleep_ms')==0 and names[-1]=='stock_start_player'
# A librespot that never answers holds local music back at most SPOT_YIELD_MS.
m.state('playing'); m.play=1; m.poll(); t0=m.now; start(); assert 1500<=m.now-t0<=1540; passed()
# On the page, a double centre press starts a scrub: the wheel moves it 5 s a tick (not the volume),
# shown on the bar in white, and a press or SCRUB_MS later seeks there. A single press otherwise turns
# the screen off, as on Now Playing. Without a scrub the wheel is stock's volume.
m.state('paused',position=60000); m.poll(); m.top=page
assert m.key(O['KEY_NEXT'])!=11
m.mock('on_wm_keyup_fun')  # stock's screen toggle
def double(): return m.key(O['KEY_CENTER'])==11 and m.key(O['KEY_CENTER'])==11
assert double() and m.key(O['KEY_NEXT'])==11 and m.key(O['KEY_NEXT'])==11 and m.key(O['KEY_PREV'])==11 and not m.sent
m.advance(250); assert texts(info)[4]==mmss('1:05') and 'on_wm_keyup_fun' not in [c[0] for c in m.calls]
m.poll(O['SCRUB_MS']-260); assert not m.sent; m.poll(); assert m.sent[0][1]=='S65000'  # 250 ms already passed
assert double() and m.key(O['KEY_PREV'])==11 and m.key(O['KEY_CENTER'])==11 and m.sent[0][1]=='S55000'
m.advance(O['DOUBLE_CLICK_MS']); assert m.key(O['KEY_CENTER'])==11; m.advance(O['DOUBLE_CLICK_MS']); assert 'on_wm_keyup_fun' in [c[0] for c in m.calls] and not m.sent
# Scrubbing, a single press seeks at once and the screen stays on.
m.advance(200); assert double() and m.key(O['KEY_PREV'])==11; m.sent=[]; assert m.key(O['KEY_CENTER'])==11 and m.sent[0][1]=='S55000'
m.advance(O['DOUBLE_CLICK_MS']); assert 'on_wm_keyup_fun' not in [c[0] for c in m.calls]
# Return goes back to Streaming.
assert m.call(O['KEY_RETURN'],address=m.handler(page,O['EVT_KEY_UP'])[0],args=(0,m.event,0,0),event_type=O['EVT_KEY_UP'],gap=0)==11
m.advance(0); assert 'navigator_back' in [c[0] for c in m.calls]; m.close(); passed()
# SPOT_IDLE_MS (30 s) not playing after it has played, the browser shut, stops librespot and its
# loop; before the first play only after SPOT_CAST_MS (150 s), never while the browser is open (Now Playing is on it), and playing
# again in time restarts the wait. The keys are local music's again, and the row starts it again.
m=SpotMachine({BIN:bytearray()}); button=stream_page(m); page=spot_open(m,button)
m.state('none'); m.poll(40000); assert len(m.systems)==1
m.state('playing'); m.poll(); m.state('paused'); m.poll(60000); assert len(m.systems)==1
m.close(); m.page=m.browser; m.close(); m.top=m.node('window','home_page')
m.state('playing'); m.poll(); m.state('paused'); m.poll(20000); m.state('playing'); m.poll(); m.state('paused')
m.poll(29000); assert len(m.systems)==1 and m.key(O['KEY_PLAY'])==11
m.poll(1000); assert 'killall -9 librespot aplay' in m.systems[-1] and STATE in m.systems[-1]
assert m.key(O['KEY_PLAY'])!=11
spot_open(m,button); assert m.systems[-1]=='/bin/sh /mnt/mmc/.spotify/run'; m.close(); passed()
# Never played: the browser shut, SPOT_CAST_MS (150 s) leaves time to cast from the phone, then it stops.
m=SpotMachine({BIN:bytearray()}); button=stream_page(m); page=spot_open(m,button); m.state('none'); m.poll()
m.close(); m.page=m.browser; m.close(); m.top=m.node('window','home_page')
m.poll(149000); assert len(m.systems)==1
m.poll(1000); assert 'killall -9 librespot aplay' in m.systems[-1]; passed()

# Internet Radio (patch/radio.c, docs/internals.md#internet-radio): favourites on the card and
# radio-browser.info's directory, a station played by q2video -r, its tags from RADIO_STATE.
RSTATE,RSOCK,RDIR='/tmp/q2radio.state','/tmp/q2radio.sock','/mnt/mmc/Radio'
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
        w=w or self.page
        return [n.get('text','') for x,n in self.nodes.items() if n['type']=='hscroll_label' and linked(x,w)]  # a new label's text is ''
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
# The row follows Spotify's. The first open makes the card's Radio folder but leaves Favourites
# empty, the user's to fill; the starter stations are Featured. The menu has no Now Playing until
# something has played.
m=RadioMachine(); page=radio_open(m)
assert RDIR in m.tree and RDIR+'/favourites.m3u' not in m.files
assert m.labels()==['Internet Radio','Favourites','Featured','Top Stations','By Country','By Genre'] and m.nodes[m.find('scroll_view')]['_ringnav_index']==0
m.row(0); assert m.labels()==['No favourites']; m.back()
m.row(1); featured=m.labels(); assert len(featured)==7 and featured[:3]==['Featured','Radio Paradise','SomaFM Groove Salad']; m.back()
# The user's favourites.m3u is never touched by opening the page.
m.close(); m.files[RDIR+'/favourites.m3u']=bytearray(b'#EXTM3U\n'); radio_open(m); assert RDIR+'/favourites.m3u' in m.files
# Search, from the page's pull-down (radio_search): a T9 box over the title, focused for its keyboard.
# Its text, %-encoded, is the directory's byname query; Return goes back to the menu. Left empty,
# or without a search, the page is as it was.
def search(text,event='EVT_VALUE_CHANGED'):
    assert m.call(address=payload_syms['radio_search'],gap=0,count=5_000_000)==0
    edit=m.find('edit'); assert m.nodes[edit]['focused']==1
    assert m.get(edit+O['W_Y'])+m.get(edit+O['W_H'])<=30 and not m.find('scroll_view')  # above the keyboard, alone
    m.nodes[edit]['text']=text; f,ctx=m.handler(edit,O[event])
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.advance(0,clear=False)
search('jazz fm'); assert m.labels()==['Loading…'] and not m.find('edit')
m.reply=b'#EXTM3U\n#EXTINF:1,Jazz FM\nhttp://jazz/fm\n'; m.worker()
assert m.opts[10002]=='https://all.api.radio-browser.info/m3u/stations/byname/jazz%20fm?order=votes&reverse=true&limit=100&hidebroken=true'
assert m.labels()==['jazz fm','Jazz FM']; m.back(); assert m.labels()[0]=='Internet Radio'
search('café'); m.worker(); assert '/byname/caf%C3%A9?' in m.opts[10002] and m.labels()[0]=='café'; m.back()  # UTF-8, not narrowed wchar
for text,event in (('','EVT_VALUE_CHANGED'),('','EVT_IM_ACTION'),('abc','EVT_BLUR')):
    search(text,event); assert m.labels()[:2]==['Internet Radio','Favourites'] and not m.find('edit')
# Return with the keyboard open empties and blurs the box, never destroying it under its keyboard; the blur shuts it.
assert m.call(address=payload_syms['radio_search'],gap=0,count=5_000_000)==0; edit=m.find('edit'); m.nodes[edit]['text']='jazz'
m.back(); assert m.nodes[edit]['focused']==0 and m.nodes[edit]['text']==''; f,ctx=m.handler(edit,O['EVT_BLUR'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.advance(0,clear=False)
assert m.labels()[:2]==['Internet Radio','Favourites'] and not m.find('edit')
search('é'*31); m.worker(); assert m.labels()[0]=='é'*31; m.back()  # 62 UTF-8 bytes fit
m.row(1); assert m.labels()==featured
for event in ('EVT_VALUE_CHANGED','EVT_IM_ACTION'):
    before=len(m.toasts)
    search('',event); assert m.labels()==featured and not m.find('edit') and len(m.toasts)==before
for query in ('x'*64,'é'*32):
    before=len(m.toasts); workers=len(m.threads)
    search(query)
    assert m.labels()==featured and not m.find('edit') and len(m.threads)==workers
    assert len(m.toasts)==before+1 and m.toasts[-1][3]=='Search is too long'
m.back()
search('zzz'); m.reply=b'#EXTM3U\n'; m.worker(); assert m.labels()==['No stations']; m.close(); passed()
# Favourites: favourites.m3u, then the folder's other .m3u and .pls files (a .pls's TitleN names its FileN).
m=RadioMachine({RDIR+'/favourites.m3u':bytearray(b'#EXTM3U\r\n#EXTINF:-1,One\r\nhttp://one/a\r\nhttp://two/b\r\n'),
                RDIR+'/x.pls':bytearray(b'[playlist]\nFile1=https://three/c?x=1\nTitle1=Three\nNumberOfEntries=1\n'),
                RDIR+'/notes.txt':bytearray(b'http://no/')})
m.tree[RDIR]=[('favourites.m3u',8),('x.pls',8),('notes.txt',8),('._x.pls',8)]; page=radio_open(m)
m.row(0); assert m.labels()==['Favourites','One','http://two/b','Three']; passed()
# A station: local music stops, then q2video -r on the headphone DAC's PCM with the URL as plain
# argv, Now Playing over the list, and the station kept as the last. The page says it connects.
m.forked=0; m.row(2)  # the child
names=[c[0] for c in m.calls]; assert names.index('player_stop')<names.index('config_outputchannel')<names.index('mclSetMute')<names.index('fork')
assert m.execs==[('/usr/bin/q2video','/usr/bin/q2video','-r','plughw:0,0',('https://three/c?x=1',0))]
m.forked=4343; m.row(2); np=m.top; assert m.nodes[np]['name']=='radionp_page' and m.files[RDIR+'/.last']==b'#EXTM3U\n#EXTINF:-1,Three\nhttps://three/c?x=1\n'
m.advance(500); assert m.labels(np)==['Internet Radio','Three','','','Connecting…','']
# The heart, at local Now Playing's place: Three is no favourite.
heart=next(w for w in m.nodes[np]['children'] if m.nodes[w].get('image') in ('play_fav','play_unfav'))
heart_x,heart_y=320,176
if variant=='ipod':
    heart_y=O['NP_ART_TOP']+O['SPOT_ART_PX']-50+O['NP_ICON_PAD']
    from ipod import NP_ICONS_END, NP_ICON
    heart_x=NP_ICONS_END-NP_ICON
assert m.nodes[heart]['image']=='play_unfav' and [m.get(heart+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[heart_x,heart_y,50,50]; passed()
# Playing: its tags (the StreamTitle as artist - title), the stream and the time since the sound
# started; standby and auto power-off held (not the screen's timer) and the DAC kept on.
m.word(syms['g_dacoff_time'],5)
m.files[RSTATE]=bytearray(f'state=playing\ntitle=Some Artist - A Song\ncodec=MP3\nbitrate=128\nat={m.now}\n'.encode())
m.poll(); assert ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls] and m.get(syms['g_dacoff_time'])==0
m.advance(3000); assert m.labels(np)==['Internet Radio','Three','Some Artist','A Song','MP3  128 kbps',mmss('0:03')]
# The volume reaches q2video for Bluetooth's and USB's soft volume, once a change.
m.byte(syms['g_volume'],44); m.poll(); assert m.sent==[(m.sent[0][0],'v,',2,0x40,1,RSOCK,110)]; m.poll(); assert not m.sent; passed()
# Play/Pause stops it (q, then its end) and starts it again; the side buttons step through the
# list it was played from, round the ends; on any page.
def last(): return m.files[RDIR+'/.last'].decode().split('\n')[2]
def forks(): return [c[0] for c in m.calls].count('fork')
m.top=m.node('window','home_page'); m.waited=4343
assert m.key(O['KEY_PLAY'])==11 and m.sent[0][1]=='q' and RSTATE not in m.files and not forks()
assert m.key(O['KEY_PLAY'])==11 and forks()==1 and last()=='https://three/c?x=1'
assert m.key(O['KEY_FWD_BTN'])==11 and m.sent[0][1]=='q' and forks()==1 and last()=='http://one/a'
assert m.key(O['KEY_BACK_BTN'])==11 and last()=='https://three/c?x=1'; passed()
# Media keys target the active radio even with local Now Playing visible.
m.top=m.node('window','playing_page')
assert m.key(O['KEY_FWD_BTN'])==11 and forks()==1
m.top=m.node('window','home_page'); passed()
# Local music starting takes the output: q2video quits first, and the keys are local music's again.
names=start(); assert m.sent[0][1]=='q' and names.index('waitpid')<names.index('stock_start_player')
assert m.key(O['KEY_PLAY'])!=11 and not forks(); passed()
# A q2video that does not quit within RADIO_QUIT_MS is killed.
m.top=page; m.row(1); m.waited=0; m.top=m.node('window','home_page')
m.handlers[syms['waitpid@GLIBC_2.0']]='s:waitpid'  # WNOHANG polls say running; the blocking wait reaps
t0=m.now; assert m.key(O['KEY_PLAY'])==11 and 1500<=m.now-t0<=1540 and m.systems[-1]=='kill -9 %d'%m.forked
m.handlers[syms['waitpid@GLIBC_2.0']]='b:waitpid@GLIBC_2.0'; passed()
# One that gives up says so.
assert m.key(O['KEY_PLAY'])==11; m.files[RSTATE]=bytearray(b'state=error\n'); m.waited=4343; m.poll()
m.advance(500); assert m.labels(np)[4]=="Can't play this station" and 'reset_poweroptions_timer' not in [c[0] for c in m.calls]
# Its ALSA reason, when it had no device, is shown.
assert m.key(O['KEY_PLAY'])==11; m.files[RSTATE]=bytearray(b'state=error\nerror=open: Device or resource busy\n'); m.waited=4343; m.poll()
m.advance(500); assert m.labels(np)[4]=="Can't play: open: Device or resource busy"
# Holding Play/Pause on Now Playing saves the station playing, and again takes it out.
m.top=np; m.status=m.alloc(0x200); m.press(77)
playing=last().encode(); before=bytes(m.files[RDIR+'/favourites.m3u']); assert playing in before  # http://two/b, a favourite
assert m.hold()==11 and m.toasts[-1][3]=='Removed from Favourites' and playing not in m.files[RDIR+'/favourites.m3u']
m.press(77); assert m.hold()==11 and m.toasts[-1][3]=='Added to Favourites' and m.files[RDIR+'/favourites.m3u'].endswith(playing+b'\n')
# Tapping the heart toggles it too, and the heart follows the file.
heart=next(w for w in m.nodes[np]['children'] if m.nodes[w].get('image') in ('play_fav','play_unfav'))
m.advance(500); assert m.nodes[heart]['image']=='play_fav'
f,ctx=m.handler(heart,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
assert m.toasts[-1][3]=='Removed from Favourites' and playing not in m.files[RDIR+'/favourites.m3u']
m.advance(500); assert m.nodes[heart]['image']=='play_unfav'
m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000); m.advance(500)
assert m.toasts[-1][3]=='Added to Favourites' and m.nodes[heart]['image']=='play_fav'; passed()
# Holding Play/Pause on a station takes it out of favourites.m3u, or adds it; the release is
# swallowed, the list follows, and elsewhere the hold is stock's.
m.back(np); assert 'navigator_back' in [c[0] for c in m.calls]; m.destroy(np); m.top=page
rows=m.find('scroll_view'); m.nodes[rows]['_ringnav_index']=0; m.status=m.alloc(0x200); m.press(77)
assert m.hold()==11 and m.toasts[-1][3]=='Removed from Favourites'
assert m.files[RDIR+'/favourites.m3u']==b'#EXTM3U\n#EXTINF:-1,http://two/b\nhttp://two/b\n' and m.labels()==['Favourites','http://two/b','Three']
m.nodes[m.find('scroll_view')]['_ringnav_index']=1; assert m.hold()==11 and m.toasts[-1][3]=='Added to Favourites'
assert m.files[RDIR+'/favourites.m3u'].endswith(b'#EXTINF:-1,Three\nhttps://three/c?x=1\n')
assert m.call(O['KEY_PLAY'],gap=0)==11 and not m.sent; passed()
# Editing a capped or partially read list must never discard unseen favourites. Exactly 200
# stations still supports removal; adding to a full list keeps the existing limit.
for count, padding, saved in ((200, b'', True), (201, b'', False),
                              (1, b'#' + b'x' * (256 << 10), False), (1, b'\0http://hidden\n', False)):
    original = b'#EXTM3U\n' + b''.join(f'http://station/{i}\n'.encode() for i in range(count)) + padding
    large = RadioMachine({RDIR+'/favourites.m3u': bytearray(original)})
    large.budget = 5_000_000
    radio_open(large); large.row(0)
    large.nodes[large.find('scroll_view')]['_ringnav_index'] = 0
    large.status = large.alloc(0x200); large.press(77); assert large.hold() == 11
    assert large.toasts[-1][3] == ('Removed from Favourites' if saved else 'Could not save Favourites')
    assert bytes(large.files[RDIR+'/favourites.m3u']) == (b'#EXTM3U\n' + b''.join(
        f'#EXTINF:-1,http://station/{i}\nhttp://station/{i}\n'.encode() for i in range(1, count)) if saved else original)
passed()
# Top Stations: the directory's m3u, fetched on the worker over verified TLS, "Loading…" meanwhile.
# Return goes up a level at a time, then back to Streaming. Without Wi-Fi it says so.
m.back(); assert m.labels()[0]=='Internet Radio' and m.labels()[1]=='Now Playing'
m.row(3); assert m.labels()==['Loading…'] and len(m.threads)==1
m.reply=b'#EXTM3U\n#RADIOBROWSERUUID:7\n#EXTINF:1,MANGORADIO\nhttps://mango/r\n\n#EXTINF:1,Dance Wave!\nhttps://dance/w.mp3\n'
m.worker(); assert m.labels()==['Top Stations','MANGORADIO','Dance Wave!']
assert m.opts[10002]=='https://all.api.radio-browser.info/m3u/stations/topvote/100?hidebroken=true'
assert m.opts[10065]=='/etc/scrobble-ca.pem' and m.opts[64]==1 and m.opts[81]==2 and m.opts[99]==1
ctx,param=m.alloc(0x104),m.alloc(16); m.word(ctx+0x100,param); m.word(param+12,1)  # OpenSSL 1.1.1f's offsets
assert m.nested(m.opts[20108],0x5150,ctx,0)==0 and m.get(param+12)==0x200001  # | X509_V_FLAG_NO_CHECK_TIME
m.back(); assert m.labels()[0]=='Internet Radio'
m.wifi=-1; m.row(3); assert m.labels()==['No Wi-Fi'] and len(m.threads)==1; m.back(); m.wifi=3; passed()
# By Genre: the tags' CSV, then a tag's stations by its name, %-encoded.
m.row(5); m.reply=b'name,stationcount\npop,6387\nclassic rock,3312\n'; m.worker()
assert m.labels()==['Genres','pop','classic rock']
m.row(1); m.reply=b'#EXTM3U\n#EXTINF:1,Rock FM\nhttp://rock/fm\n'; m.worker()
assert m.opts[10002]=='https://all.api.radio-browser.info/m3u/stations/bytagexact/classic%20rock?order=votes&reverse=true&limit=100&hidebroken=true'
assert m.labels()==['classic rock','Rock FM']
# By Country: by code; a name may be quoted. A failed fetch says so; Return leaves the note.
m.back(); m.back(); m.row(4); m.reply=b'name,iso_3166_1,stationcount\n"Taiwan, Republic Of China",TW,214\nGermany,DE,6496\n'; m.worker()
assert m.labels()==['Countries','Taiwan, Republic Of China','Germany']
m.row(0); m.code=0; m.worker(); assert m.labels()==['Directory unavailable'] and 'bycountrycodeexact/TW?' in m.opts[10002]
m.back(); assert m.labels()==['Countries','Taiwan, Republic Of China','Germany']
m.back(); m.calls=[]; m.back(); assert 'navigator_back' in [c[0] for c in m.calls]; m.close(); passed()

# Download Cover & Lyrics' lyrics (onlinematch.c ringnav_lyric_search, at kugou_obtainlyric's entry): LRCLIB
# alone. keyinfo's title and artist are searched; the first result with synced lyrics goes to out with a BOM,
# less its opening credits. None found, the names without accents are searched, if that changes them; a busy
# reply is asked again, three requests in all. Found or not, it returns 1, so online_lyricthd skips NetEase,
# and Kugou's own search (past the entry's jump) never runs. demo's cJSON is stood in for by Python's json.
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
        key=0xa40424  # stock's keyinfo: file name, title (+0x200), album (+0x400), artist (+0x500)
        self.u.mem_write(key,bytes(0x600)); self.u.mem_write(key+0x200,title.encode()); self.u.mem_write(key+0x500,artist.encode())
        self.replies,self.code,self.written,self.calls,self.urls=list(replies),code,{},[],[]
        # Each request's 512 KB reply buffer is freed, but the mock's free keeps it: start over, zeroed as calloc's.
        self.u.mem_write(BIG,bytes(self.big-BIG)); self.big=BIG
        return self.call(address=HOOKS['kugou_obtainlyric'][0],args=(self.string(f'{title}/{artist}'),self.string('/tmp/oslyric_7'),0,0),gap=0,count=5_000_000)
m=LyricSearchMachine()
found=json.dumps([{'syncedLyrics':None,'plainLyrics':'x'},{'syncedLyrics':'[00:00.00] Lyrics : A\n[00:12.00]Look at the stars'},
                  {'syncedLyrics':'[00:01.00]Second'}]).encode()
stars='﻿[00:12.00]Look at the stars'.encode()  # the first synced result, its credit cut
LRCLIB='https://lrclib.net/api/search?track_name=%s&artist_name=%s'
busy=b'{"message":"The server is busy, please retry in a moment","name":"ServerOverloaded","statusCode":503}'
def kugou(): return 'kugou' in [c[0] for c in m.calls]
assert m.search('Yellow','Coldplay',found)==1 and m.urls==[LRCLIB%('Yellow','Coldplay')] and m.written=={'/tmp/oslyric_7':stars}
assert m.search('Yellow','Coldplay',b'[]')==1 and len(m.urls)==1 and not m.written and not kugou()  # nothing to fold
# Hataałii: none under its own name, so its folded one, Hataalii; an instrumental counts as none.
plain=LRCLIB%('Rains%20Over%20Dzil','Hataalii')
assert m.search('Rains Over Dził','Hataałii',b'[]',found)==1 and m.urls[1:]==[plain] and m.written=={'/tmp/oslyric_7':stars}
assert m.urls[0]==LRCLIB%('Rains%20Over%20Dzi%C5%82','Hataa%C5%82ii')
for none in (b'[]',json.dumps([{'syncedLyrics':None,'instrumental':True}]).encode()):
    assert m.search('Rains Over Dził','Hataałii',none,none)==1 and len(m.urls)==2 and not m.written and not kugou()
# Busy: asked again, three requests at most, the fold only after an answer; offline: one request.
assert m.search('Yellow','Coldplay',busy,found)==1 and len(m.urls)==2 and m.written=={'/tmp/oslyric_7':stars}
assert m.search('Rains Over Dził','Hataałii',b'[]',busy,b'[]',found)==1 and m.urls[1::2]==[plain,plain] and m.written
assert m.search('Yellow','Coldplay',busy,busy,busy,found)==1 and len(m.urls)==3 and not m.written and not kugou()
assert m.search('Yellow','Coldplay',found,code=0)==1 and len(m.urls)==1 and not m.written and not kugou()
assert m.search('','Coldplay',found)==1 and not m.written and not m.urls; passed()  # no title: no request

# Its accept (onlinematch.c ringnav_lyric_save, at LYRIC_SAVE_CALL): stock's snprintf of cp -rf /tmp/oslyric_N
# "/mnt/mmc/.downpic/<name>" for system() becomes a copy beside the song, through .part, as Download Album Lyrics
# saves, the .downpic copy stock's loader would show first goes, and system() gets ":". The song is the queue's
# whose file name stock turns into name. Not in the queue, a CUE track's, or a card that can't be written: stock's.
from build import LYRIC_SAVE_CALL
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
        cmd=self.alloc(0x400); fmt=0x772630  # stock's cp -rf %s "/mnt/mmc/.downpic/%s"
        self.call(address=payload_syms['ringnav_lyric_save'],args=(cmd,0x400,fmt,self.string('/tmp/oslyric_7')),stack=(self.string(name),),gap=0)
        return self.text(cmd)
assert m.text(0x772630)=='cp -rf %s "/mnt/mmc/.downpic/%s"' and m.get(LYRIC_SAVE_CALL)==jal(payload_syms['ringnav_lyric_save'])
LRC=b'\xef\xbb\xbf[00:12.00]Look at the stars\n'*700  # over one 4 KB read
def lyric_save(**rec):
    m=LyricSaveMachine({'/tmp/oslyric_7':LRC,'/mnt/mmc/.downpic/01 Yellow.lrc':b'[00:00.00]Kugou'}); r=m.playing()
    for off,v in (('REC_NAME','01 Yellow.flac'),('REC_PATH','/mnt/mmc/Coldplay/01 Yellow.flac'))+tuple(rec.items()):
        m.word(r+O[off],m.string(v) if isinstance(v,str) else v)
    return m
stock='cp -rf /tmp/oslyric_7 "/mnt/mmc/.downpic/01 Yellow.lrc"'
m=lyric_save(); assert m.save('01 Yellow.lrc')==':' and m.files['/mnt/mmc/Coldplay/01 Yellow.lrc']==LRC
assert m.unlinked==['/mnt/mmc/.downpic/01 Yellow.lrc'] and '/mnt/mmc/.downpic/01 Yellow.lrc' not in m.files
assert not [p for p in m.files if p.endswith('.part')]; passed()
m=lyric_save(); assert m.save('02 Other.lrc')==stock.replace('01 Yellow','02 Other') and not m.unlinked; passed()  # not in the queue
m=lyric_save(REC_CUE_END=90); assert m.save('01 Yellow.lrc')==stock and '/mnt/mmc/Coldplay/01 Yellow.lrc' not in m.files; passed()
m=lyric_save(); m.fail_write=True; assert m.save('01 Yellow.lrc')==stock and '/mnt/mmc/.downpic/01 Yellow.lrc' in m.files; passed()  # can't write the card

# Spotify's browser (patch/spotify.c, docs/librespot.md's Q2SPOT/1): the row opens spotbrowse_page,
# each level a request on the fork's --browse-socket, sent and read on a pthread.
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
def track(uri,title,ctx='',pos=''): return ('track',uri,title,'An Artist',ctx,pos)
# The row opens the browser and asks for the account, written, shut for writing and read on the worker;
# signed in, the menu: Now Playing, Queue and Playback settings only with a track.
m=BrowseMachine(); m.open(); assert m.labels()==['Loading…'] and m.systems==['/bin/sh /mnt/mmc/.spotify/run']
m.answer(*ME); assert m.wire==[b'Q2SPOT/1\n1\taccount\n',b'Q2SPOT/1\n2\tcaps\n']
MENU=['Spotify','Liked Songs','Playlists','Albums','Artists','Search','Account','Downloaded Tracks','Downloads']
assert m.labels()==MENU
m.state('playing'); m.poll(); m.advance(250)
assert m.labels()==['Spotify','Now Playing','Liked Songs','Playlists','Albums','Artists','Search','Queue','Playback Settings','Account','Downloaded Tracks','Downloads']
m.row(0); assert m.nodes[m.top]['name']=='spotify_page'; m.destroy(m.top); m.top=m.page; m.state('none',track=''); m.poll(); m.advance(250); assert m.labels()==MENU; passed()
m.row(7)  # an older add-on: its message-only Download Status stays reachable
assert m.labels()==['Downloads','Downloaded Tracks','','Download Status',"The latest download's progress",'Update Spotify on the card','for saved playlists and download controls']
assert [chevron(m,i) for i in range(3)]==[True,False,False]
m.row(1); assert m.asked()==('downloadstatus',); m.answer('ok','Mix B: 3 of 10 saved','',[])
assert m.toasts[-1][3]=='Mix B: 3 of 10 saved' and m.labels()[0]=='Downloads'
m.back(); assert m.labels()==MENU; passed()
# Downloaded tracks stay reachable while the service cannot answer catalogue requests, and a row
# uses the cache-only play operation.
m.row(6); assert m.asked()==('downloaded',)
m.answer(*OK,[track('spotify:track:cached','Cached')])
m.row(0); assert m.asked()==('playdownload','spotify:track:cached')
m.answer(*OK,[]); assert m.nodes[m.top]['name']=='spotify_page'; m.destroy(m.top); m.top=m.page; m.back(); passed()
# Loading, an error with Retry (offline, throttled, error, or no answer), one without, and empty.
m.row(0); assert m.labels()==['Loading…'] and m.asked()==('liked',)
m.answer('offline',"Spotify can't be reached",'',[]); assert m.labels()==["Spotify can't be reached",'Retry']
m.row(0); assert m.labels()==['Loading…']; m.answer('premium','Spotify Premium is needed','',[]); assert m.labels()==['Spotify Premium is needed']
m.back(); m.row(0); m.answer(*OK,[]); assert m.labels()==['Nothing here']
# A short or empty page with a cursor (the backend's paging cap) still offers More….
m.back(); m.row(0); m.answer('ok','','c%251',[]); assert m.labels()==['Liked Songs','More…','']
m.row(0); assert m.asked()==('liked','c%251'); m.answer(*OK,[track('spotify:track:9','Nine','spotify:user:alice:collection','50')])
assert m.labels()==['Liked Songs','Nine','An Artist']; passed()
# A reply without the request's id (a late one, or id 0 from a backend that couldn't read it) is
# dropped: its rows never show.
m.back(); m.back(); m.row(0); m.answer(b'Q2SPOT/1\n0\tok\t\t\ntrack\tspotify:track:1\tOne\tA\t\t\n')
assert m.labels()==["Spotify didn't answer",'Retry']
# Malformed or oversized ones too.
m.row(0); m.answer(b'Q2SPOT/1\n{id}\tok\t%zz\t\n'); assert m.labels()==["Spotify didn't answer",'Retry']
m.row(0); m.answer(b'x'*(65537)); assert m.labels()==["Spotify didn't answer",'Retry']; m.back(); passed()
# Playlists, a playlist's tracks by its URI exactly as received, More… by the cursor, and Back
# through them to the row each was left on. A track plays in its context at its position (and its
# URI), and once it plays Now Playing opens; a failure is toasted. Alone (empty context), by its URI.
m.row(1); assert m.asked()==('playlists',)
m.answer('ok','','next%09page',[('playlist','spotify:playlist:a','Mix A','alice','',''),('playlist','spotify:playlist:b%25','Mix B','bob','','')])
assert m.labels()==['Playlists','Mix A','alice','Mix B','bob','More…','']
m.pick(1); assert m.asked()==('playlist','spotify:playlist:b%25')
m.answer(*OK,[track('spotify:track:1','One','spotify:playlist:b%25','0'),track('spotify:track:1','One','spotify:playlist:b%25','1'),track('spotify:track:3','Three')])
assert m.labels()==['Mix B','One','An Artist','One','An Artist','Three','An Artist','Download Playlist','For offline listening']
m.pick(3); assert m.asked()==('download','spotify:playlist:b%25')
m.answer('ok','Playlist download started','',[])
assert m.toasts[-1][3]=='Playlist download started' and m.top==m.page
m.pick(3); m.answer('offline','Connect to Wi-Fi to download playlists','',[])
assert m.toasts[-1][3]=='Connect to Wi-Fi to download playlists' and m.labels()[0]=='Mix B'
m.pick(1); assert m.asked()==('play','spotify:playlist:b%25','1','spotify:track:1')
m.answer('unavailable','This track is unavailable','',[]); assert m.toasts[-1][3]=='This track is unavailable' and m.top==m.page
m.pick(2); assert m.asked()==('play','spotify:track:3'); n=len(m.toasts); m.answer(*OK,[])
assert len(m.toasts)==n and m.nodes[m.top]['name']=='spotify_page'; m.destroy(m.top); m.top=m.page
m.back(); assert m.labels()[:2]==['Playlists','Mix A'] and m.nodes[m.find('scroll_view')]['_ringnav_index']==1
m.pick(2); assert m.asked()==('playlists','next%09page'); m.answer(*OK,[('playlist','spotify:playlist:c','Mix C','carol','','')])
m.back(); assert m.nodes[m.find('scroll_view')]['_ringnav_index']==2; passed()
# Back while loading stays usable: the fetch is dropped, and a late answer touches nothing.
m.pick(0); assert m.labels()==['Loading…']; t=len(m.threads); m.back(); assert m.labels()[:2]==['Playlists','Mix A']
assert len(m.threads)==t and ('pthread_join' in [c[0] for c in m.calls]); passed()
# Artists open an artist: top tracks, then albums; an album opens its tracks. The history keeps the
# top SPOT_KEEP levels' rows; Back to an older one fetches it again.
m.back(); m.row(3); m.answer(*OK,[('artist','spotify:artist:x','X','','','')]); m.pick(0); assert m.asked()==('artist','spotify:artist:x')
m.answer(*OK,[track('spotify:track:t','Top'),('album','spotify:album:y','Y','X','','')]); assert m.labels()==['X','Top','An Artist','Y','X']
m.pick(1); assert m.asked()==('album','spotify:album:y'); m.answer('ok','','p2',[track('spotify:track:z','Z','spotify:album:y','0')])
m.pick(0); assert m.asked()==('play','spotify:album:y','0','spotify:track:z'); m.answer(*OK,[]); m.destroy(m.top); m.top=m.page
m.pick(1); assert m.asked()==('album','spotify:album:y','p2'); m.answer(*OK,[])
asked=len(m.threads); m.back(); m.back(); assert len(m.threads)==asked and m.labels()[:2]==['X','Top']
m.back(); assert m.labels()==['Loading…'] and m.asked()==('artists',)
m.answer(*OK,[('artist','spotify:artist:x','X','','','')]); assert m.labels()==['Artists','X',''] and m.nodes[m.find('scroll_view')]['_ringnav_index']==0
m.back(); assert m.labels()==MENU; passed()
# No saved login: the pairing prompt, asking again every 2 s while it is open, so a pairing from the
# phone opens the menu in place.
m=BrowseMachine(); m.open(); m.answer('login','Open Spotify on your phone','',[])
PAIR=['Spotify','Open Spotify on your phone and choose Q2','Both on the same Wi-Fi']
assert m.labels()==PAIR; t=len(m.threads); m.advance(1500); assert len(m.threads)==t
m.advance(750); assert len(m.threads)==t+1 and m.asked()==('account',); m.answer('login','','',[]); assert m.labels()==PAIR
m.advance(2000); m.answer(*ME); assert m.labels()==MENU; passed()
# An old add-on: librespot runs (its state file) but no browse socket after SPOT_OLD_MS. Now Playing
# still works, and the page asks for the update; until then it waits. No state file either: not running.
m=BrowseMachine({STATE:bytearray(b'state=none\n')}); m.open(); m.worker(); assert m.labels()==['Loading…']
m.advance(500); m.worker(); assert m.labels()==['Loading…']
m.advance(2500); m.worker(); assert m.labels()==['Spotify','Now Playing','','Update Spotify on the card','to browse on the Q2']
m.row(0); assert m.nodes[m.top]['name']=='spotify_page'; m.destroy(m.top); m.top=m.page; passed()
m=BrowseMachine(); m.open(); m.advance(3000); m.worker(); assert m.labels()==["Spotify isn't running",'Retry']
m.row(0); m.answer(*ME); assert m.labels()==MENU; passed()
# Search: the T9 box over the title, run on its OK only. Empty, left or Returned from, the menu as it was; else
# the term's categories, each searched by its name and the term, escaped.
def search(text,event='EVT_VALUE_CHANGED'):
    m.row(4); edit=m.find('edit'); assert m.nodes[edit]['focused']==1 and m.nodes[edit]['tips']=='Search Spotify'
    assert m.get(edit+O['W_Y'])+m.get(edit+O['W_H'])<=30 and not m.find('scroll_view')  # above the keyboard, alone
    m.nodes[edit]['text']=text; f,ctx=m.handler(edit,O[event])
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.advance(0,clear=False)
asked=len(m.threads)
for text,event in (('','EVT_VALUE_CHANGED'),('','EVT_IM_ACTION'),('abc','EVT_BLUR')): search(text,event); assert m.labels()==MENU and not m.find('edit')
m.row(4); edit=m.find('edit'); m.nodes[edit]['text']='abc'  # Return: emptied, no search; blurred, its keyboard shut first
m.back(); assert m.nodes[edit]['focused']==0 and m.nodes[edit]['text']==''
f,ctx=m.handler(edit,O['EVT_BLUR']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
m.advance(0,clear=False); assert m.labels()==MENU and not m.find('edit')
search('50%\toff'); assert len(m.threads)==asked and m.labels()==['50%\toff','Tracks','Albums','Artists','Playlists']
m.row(1); assert m.asked()==('search','albums','50%25%09off'); m.answer(*OK,[('album','spotify:album:q','Q','R','','')])
assert m.labels()==['Albums','Q','R']; m.back(); m.back(); assert m.labels()==MENU; passed()
# Holding Play/Pause on a track row adds it to the queue, on a playlist row downloads it, its reply
# toasted; on another row, or another page, the hold is stock's.
m.row(0); m.answer(*OK,[track('spotify:track:1','One','c','0'),('album','spotify:album:a','A','B','',''),
                        ('playlist','spotify:playlist:p','P','Q','','')])
m.top=m.page; m.status=m.alloc(0x200); m.nodes[m.find('scroll_view')]['_ringnav_index']=0; m.press(77)
toasts=len(m.toasts)
assert m.hold()==11 and m.asked()==('enqueue','spotify:track:1'); m.answer(*OK,[]); assert len(m.toasts)==toasts
m.press(78); assert m.hold()==11; m.answer('unavailable','Nothing is playing on Q2','',[]); assert m.toasts[-1][3]=='Nothing is playing on Q2'
m.nodes[m.find('scroll_view')]['_ringnav_index']=2; m.press(80)
assert m.hold()==11 and m.asked()==('download','spotify:playlist:p'); m.answer('ok','Downloading P','',[]); assert m.toasts[-1][3]=='Downloading P'
m.nodes[m.find('scroll_view')]['_ringnav_index']=1; m.press(79); asked=len(m.threads); m.hold(); assert len(m.threads)==asked; m.back(); passed()
# Playing: Queue's rows play by their place and URI, then Now Playing. Playback settings: shuffle and
# repeat as they are, a press setting the next and reading them again; never sent again on failure.
m.state('playing'); m.poll(); m.advance(250); m.row(6); assert m.asked()==('queue',)
m.answer(*OK,[track('spotify:track:q','Q',pos='3')]); m.row(0); assert m.asked()==('skip','3','spotify:track:q')
m.answer(*OK,[]); assert m.nodes[m.top]['name']=='spotify_page'; m.destroy(m.top); m.top=m.page; m.back()
m.row(7); m.answer(*OK,[('setting','shuffle','off','','',''),('setting','repeat','context','','','')])
assert m.labels()==['Playback Settings','Shuffle','','Repeat','All'] and switch(m,0)=='switch_off' and switch(m,1)!='switch_off'
m.row(0); assert m.asked()==('shuffle','on'); m.answer(*OK,[]); assert m.labels()==['Loading…'] and m.asked()==('settings',)
freed=m.freed
m.answer(*OK,[('setting','shuffle','on','','',''),('setting','repeat','track','','','')]); assert m.labels()==['Playback Settings','Shuffle','','Repeat','One'] and switch(m,0)=='switch_on' and m.freed==freed+1
m.row(1); assert m.asked()==('repeat','off'); t=len(m.threads); m.answer('offline',"Spotify didn't answer in time",'',[]); m.advance(1000)
assert m.toasts[-1][3]=="Spotify didn't answer in time" and len(m.threads)==t and m.labels()[1]=='Shuffle'; m.back(); passed()
# Account: the name, Premium, and Sign out after a confirmation; then the pairing prompt.
m.row(8); assert m.asked()==('account',); m.answer(*ME); assert m.labels()==['Account','Alice','Premium','Sign Out','']
m.row(1); assert m.labels()==['Sign Out of Spotify?','Cancel','Sign Out']; m.row(0); assert m.labels()[:2]==['Account','Alice']
t=len(m.threads); m.row(1); m.row(1); assert m.asked()==('signout',) and len(m.threads)==t+1
m.answer(*OK,[]); assert m.labels()==PAIR
# librespot exits on signout and its launcher restarts it: meanwhile no socket is no old add-on.
m.serve=None; m.advance(2000); m.worker(); assert m.labels()==PAIR; m.advance(5000); m.worker(); assert m.labels()==PAIR
# A login lost while browsing goes to pairing too.
m.advance(2000); m.answer(*ME); m.row(1); m.answer('login','','',[]); assert m.labels()==PAIR; passed()
# Browsing keeps librespot: paused past SPOT_IDLE_MS with the browser open, it runs on; once the
# browser is shut the wait starts, and the row starts it again, waiting for its socket.
m.state('playing'); m.poll(); m.state('paused'); m.poll(60000); assert len(m.systems)==1
m.destroy(m.page); m.poll(29500); assert len(m.systems)==1; m.poll(500); assert 'killall -9 librespot aplay' in m.systems[-1]
m.serve=None; m.open(); assert m.systems[-1]=='/bin/sh /mnt/mmc/.spotify/run'; m.worker(); assert m.labels()==['Loading…']
m.advance(500); m.answer(*ME); assert m.labels()==MENU; passed()
# Left open with the screen off, the browser holds nothing: SPOT_IDLE_MS on, it shuts and librespot stops.
m.state('playing'); m.poll(); m.state('paused'); m.poll(); m.byte(syms['g_backlight_status'],0); kills=len(m.systems)
m.poll(29500); assert len(m.systems)==kills
names=m.poll(500); assert 'window_close' in names and 'killall -9 librespot aplay' in m.systems[-1]
m.byte(syms['g_backlight_status'],1); m.destroy(m.page); passed()

# Downloads outlive the browser without taking the audio output or stopping local playback.
m=BrowseMachine(); m.open(); m.answer(*ME); m.state('playing'); m.poll()
m.state('paused'); m.poll(); m.destroy(m.page)
m.files['/tmp/q2-librespot.download']=bytearray(b'downloading=1\n')
m.play=2; names=m.poll(60000)
assert len(m.systems)==1 and 'reset_poweroptions_timer' in names and 'player_stop' not in names
assert not m.call(address=payload_syms['spot_status'],gap=0)
m.files['/tmp/q2-librespot.download']=bytearray(b'downloading=0\n')
m.poll(500); m.poll(30000); assert 'killall -9 librespot aplay' in m.systems[-1]; passed()
# A crashed librespot's files still say playing and downloading: once its socket refuses, no hold,
# and it idles out; a new state (the restart) or a socket that answers again trusts them again.
m=BrowseMachine(); m.open(); m.answer(*ME); m.state('playing'); m.poll(); m.destroy(m.page)
m.files['/tmp/q2-librespot.download']=bytearray(b'downloading=1\n')
m.alive=False; names=m.poll(10000); assert 'reset_poweroptions_timer' not in names
names=m.poll(); assert 'reset_poweroptions_timer' not in names
m.state('playing',position=1); names=m.poll(); assert 'reset_poweroptions_timer' in names
m.poll(10000); m.alive=True; names=m.poll(10000); assert 'reset_poweroptions_timer' in names
m.alive=False
m.alive=False; m.poll(10000); m.poll(30000); assert 'killall -9 librespot aplay' in m.systems[-1]; passed()

# Back reveals the saved More row, and paint keeps it selected.
m=BrowseMachine(); m.open(); m.answer(*ME); m.row(0)
m.answer('ok','','next',[track('spotify:track:'+str(i),str(i)) for i in range(50)])
m.pick(50); m.answer(*OK,[]); m.back(); view=m.find('scroll_view')
assert m.nodes[view]['_ringnav_index']==50 and m.get(view+O['SCROLL_Y'])>3000
m.paint(view); assert m.nodes[view]['_ringnav_index']==50; passed()
# More beyond the history limit keeps the operation, context and title while recycling a level.
m=BrowseMachine(); m.open(); m.answer(*ME); m.row(2)
m.answer(*OK,[('album','spotify:album:a','Album A','Artist','','')]); m.row(0)
for i in range(12):
    assert m.asked()==(('album','spotify:album:a') if not i else ('album','spotify:album:a',str(i)))
    m.answer('ok','',str(i+1),[track('spotify:track:1','One','spotify:album:a',str(i*50))])
    assert m.labels()[0]=='Album A'
    m.pick(1)
m.answer(*OK,[]); m.back(); assert m.labels()[0]=='Album A'; passed()
# A stalled write times out without sending the operation; Back also abandons a pending play.
m=BrowseMachine(); m.open(); m.stalled=True; start=m.now; m.answer(*ME)
assert m.now-start>=15000 and not m.wire and m.labels()==["Spotify didn't answer",'Retry']
m.stalled=False; m.row(0); m.answer(*ME); m.row(0); m.answer(*OK,[track('spotify:track:1','One')])
m.row(0); assert m.asked()==('play','spotify:track:1'); m.back(); assert m.labels()==MENU
assert m.get(payload_syms['wk']+32)==0; m.advance(1000); assert m.top==m.page; passed()

# A successful play waits for the requested track, including offline playback. Missing metadata
# and another track's position updates cannot flash pairing or reveal the previous track.
for size in (1, 2, 3, 4, 255, 256):
    m=SpotMachine({}); out=m.alloc(300); title='A汉字é'*80
    m.call(address=payload_syms['field'],args=(m.string('\ntitle='+title+'\n'),m.string('title'),out,size),gap=0)
    assert m.text(out)==title.encode()[:size-1].decode('utf-8',errors='ignore')
passed()
for op in ('liked','downloaded'):
    m=BrowseMachine(); m.open(); m.answer(*ME)
    m.state('paused',track='old'); m.poll(); m.advance(250)
    m.row(1 if op=='liked' else 9)
    m.answer(*OK,[track('spotify:track:new','New')]); m.row(0); m.answer(*OK,[])
    np=m.top; info,msg=m.nodes[np]['children']
    assert texts(msg)==['Loading…','Starting playback'] and not m.nodes[info]['visible']
    m.state('playing',track='old',position=2000); m.poll(); m.advance(250)
    assert m.nodes[msg]['visible'] and texts(msg)[0]=='Loading…'
    m.state('none',track=''); m.poll(); m.advance(250)
    assert texts(msg)[0]=='Loading…'
    m.state('playing',track='new',title='相同的标题 Long title '*6,artist='Long artist 艺术家 '*6,album='Long album 专辑 '*6)
    m.poll(); m.advance(250)
    assert m.nodes[info]['visible'] and not m.nodes[msg]['visible']
    assert texts(info)[1:4]==['相同的标题 Long title '*6,'Long artist 艺术家 '*6,'Long album 专辑 '*6]
    # Same title, different artist and album: all three fields have their own change detection.
    m.state('paused',track='next',title='相同的标题 Long title '*6,artist='Other 艺术家',album='Other 专辑')
    m.poll(); m.advance(250); assert texts(info)[2:4]==['Other 艺术家','Other 专辑']
    fields=[w for w in m.nodes if under(w,info) and m.nodes[w].get('text') in texts(info)[1:4]]
    assert all(m.nodes[w]['xoffset']==0 for w in fields)
    m.state('stopped',track=''); m.poll(); m.advance(250)
    assert texts(msg)==['Nothing playing','Choose music in Spotify']
    m.state('none',track=''); m.poll(); m.advance(250)
    assert 'Open Spotify on your phone' not in texts(msg)
    passed()

# Metadata can also precede the acknowledgement: do not wait for a second state change.
m=BrowseMachine(); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:early','Early')]); m.row(0)
m.state('playing',track='early',title='Early'); m.poll(); m.answer(*OK,[])
info,msg=m.nodes[m.top]['children']; assert m.nodes[info]['visible'] and texts(info)[1]=='Early'
passed()
# A relinked track (another id than asked for) is the play's too.
m=BrowseMachine(); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:asked','Asked')]); m.row(0); m.answer(*OK,[])
m.state('playing',track='relinked',title='Asked'); m.poll(); m.advance(250)
info,msg=m.nodes[m.top]['children']; assert m.nodes[info]['visible'] and texts(info)[1]=='Asked'
passed()

# The loading wait is bounded, does not repeat play, and accepts late metadata after a timeout.
m=BrowseMachine(); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:late','Late')]); m.row(0); m.answer(*OK,[])
np=m.top; info,msg=m.nodes[np]['children']; requests=len(m.requests)
m.advance(15250)
assert texts(msg)==["Playback didn't start",'Go back and try again'] and len(m.requests)==requests
m.state('playing',track='late'); m.poll(); m.advance(250); assert m.nodes[info]['visible']
m.destroy(np); m.top=m.page; m.back(); m.row(8); m.answer(*ME); m.row(1); m.row(1); m.answer(*OK,[])
m.call(address=payload_syms['spot_np'],args=(0,0,0,0),gap=0,count=5_000_000)
info,msg=m.nodes[m.top]['children']; assert texts(msg)==['Open Spotify on your phone','and choose Q2']
passed()

# A refused key (librespot-org/librespot#1649): librespot logs it and skips on through the list, which
# no pause stops. At the next poll the page says why and librespot is killed (run starts it again),
# once, without sending play again; a line from before the play is not this play's.
LOG='/mnt/mmc/.spotify/spot.log'; REFUSED=b'ERROR librespot_core::audio_key] error audio key 0 1\n'
m=BrowseMachine({LOG:bytearray(b'[old] '+REFUSED)}); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:shut','Shut')]); m.row(0); m.answer(*OK,[])
np=m.top; info,msg=m.nodes[np]['children']; requests=len(m.requests); m.systems=[]
m.poll(); m.advance(250); assert texts(msg)==['Loading…','Starting playback'] and not m.systems
m.files[LOG]+=b'[new] INFO  librespot_playback::player] Loading <Shut>\n[new] '+REFUSED
m.poll(); m.advance(250)
assert texts(msg)==['Spotify refused this account',"Its tracks can't play on the Q2"]
assert m.systems==['killall -9 librespot aplay'] and len(m.requests)==requests
m.files[LOG]+=b'[new] '+REFUSED; m.poll(); m.advance(15250)
assert m.systems==['killall -9 librespot aplay'] and texts(msg)[0]=='Spotify refused this account'
m.state('playing',track='shut'); m.poll(); m.advance(250); assert m.nodes[info]['visible']
passed()
# The log line cut between two polls is still found; a log without one times out as before.
m=BrowseMachine({LOG:bytearray()}); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:cut','Cut')]); m.row(0); m.answer(*OK,[]); msg=m.nodes[m.top]['children'][1]
m.systems=[]; m.files[LOG]+=b'x'*5000+REFUSED[:40]; m.poll(); assert not m.systems
m.files[LOG]+=REFUSED[40:]; m.poll(); m.advance(250); assert m.systems==['killall -9 librespot aplay']
m=BrowseMachine({LOG:bytearray()}); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:slow','Slow')]); m.row(0); m.answer(*OK,[]); msg=m.nodes[m.top]['children'][1]
m.systems=[]; m.files[LOG]+=b'[new] WARN  something else\n'; m.poll(); m.advance(15250)
assert texts(msg)==["Playback didn't start",'Go back and try again'] and not m.systems
passed()

def run(m,name,*args): return m.call(address=payload_syms[name],args=(*args,0,0,0,0)[:4],gap=0,clear=False)

# Saved-collection capabilities replace the legacy flat Downloads action with a persistent hub.
m=BrowseMachine(); m.caps=('collections','collection','playcollection','downloadliked','jobs','job','remove','download')
m.open(); m.answer(*ME)
assert m.labels()==['Spotify','Liked Songs','Playlists','Albums','Artists','Search','Account','Downloads']
m.row(0); m.answer(*OK,[])
assert m.labels()==['Liked Songs','Download Liked Songs','For offline listening']
m.row(0); assert m.asked()==('downloadliked',); m.answer('ok','Queued','',[]); m.back()
m.row(6); assert m.labels()==['Downloads','Downloaded Playlists','','Downloaded Tracks','','Download Progress','','Storage Management','']
assert all(chevron(m,i) for i in range(4))  # each opens a page
m.row(0); assert m.asked()==('collections',)
m.answer(*OK,[('collection','liked','Liked Songs','2/3 · partial','','')]); m.row(0)
assert m.asked()==('collection','liked')
m.answer('ok','One unavailable','next',[track('spotify:track:one','One','liked','49'),('missing','local:missing','Missing','Unavailable','liked','50'),track('spotify:track:one','One','liked','51')])
m.row(2); assert m.asked()==('playcollection','liked','51')
m.answer(*OK,[]); m.destroy(m.top); m.top=m.page
m.row(1); assert m.toasts[-1][3]=='Unavailable'
m.row(3); assert m.asked()==('collection','liked','next'); m.answer(*OK,[])
m.back(); m.row(4); assert m.labels()==['Download Controls','Pause','Resume','Cancel Download','Retry Failed','Update Download']
assert [(m.get(r+O['W_Y']),m.get(r+O['W_H'])) for r in m.rows()]==[(48*i,48) for i in range(5)]
m.row(3); assert m.asked()==('job','liked','retry'); m.answer('ok','Retry queued','',[])
m.back(); m.back(); m.back()
assert m.labels()[0]=='Downloads'
m.row(2); assert m.asked()==('jobs',)
m.answer(*OK,[('job','liked','paused','2/3 · Choose Resume','Liked Songs','2')])
m.advance(2250); assert m.asked()==('jobs',)
m.answer('ok','','p2',[('job','liked','downloading','2/3 · Saving 3','Liked Songs','2')])
# A failed refresh keeps the list, its fields (the cursor too) still on the reply it shows, not the
# failed one, which is freed.
shown=m.labels(); m.poison={}; m.advance(2250); assert m.asked()==('jobs',); m.answer('offline','No Wi-Fi','',[])
assert m.labels()==shown and shown[-2]=='More…'; del m.poison
m.row(1); assert m.asked()==('jobs','p2'); m.answer(*OK,[]); m.back()
m.row(0); m.row(0)
assert m.asked()==('job','liked','pause'); m.answer('ok','Paused','',[])
m.back(); m.back(); m.row(3); assert m.labels()[0]=='Storage Management'
m.row(0); assert m.asked()==('collections',)
m.answer('ok','','p2',[('collection','a','A','1/1','',''),('collection','b','B','1/1','','')])
m.row(0); assert 'Selected' in m.labels()
m.row(1); m.row(0); assert m.labels().count('Selected')==1  # toggle A off, B remains
m.row(2); assert m.asked()==('collections','p2')
m.answer(*OK,[('collection','c','C','1/1','','')]); m.row(0); m.row(1)
assert m.asked()==('remove','collections','b,c')
m.answer('ok','Remove 2 playlists, 1 tracks?','',[])
assert m.labels()[0]=='Remove Downloads?' and 'Remove 2 playlists, 1 tracks?' in m.labels()
m.row(2); assert m.asked()==('remove','collections','b,c','confirm')
m.answer('ok','Removed','',[]); assert m.labels()[0]=='Storage Management'
m.row(2); assert m.asked()==('remove','all','')
m.answer('ok','Remove 1 playlists, 3 tracks?','',[]); m.row(1)  # Cancel preserves downloads
assert m.labels()[0]=='Storage Management'
# Return before Clear All's preview answers: a later action's ok reply never opens Remove Downloads?.
m.row(2); assert m.asked()==('remove','all',''); m.back(); assert m.labels()[0]=='Downloads'
m.row(2); m.answer(*OK,[('job','liked','paused','2/3','Liked Songs','2')]); assert chevron(m,0); m.row(0); m.row(0)
assert m.asked()==('job','liked','pause'); m.answer('ok','Paused','',[])
assert m.labels()[0]=='Liked Songs' and m.toasts[-1][3]=='Paused'
m.back(); m.back(); m.row(3); assert m.labels()[0]=='Storage Management'
m.row(1); m.answer(*OK,[track('spotify:track:a','A')]); m.row(0); m.row(1)
assert m.asked()==('remove','tracks','spotify:track:a')
m.answer('ok','Remove 0 playlists, 1 tracks? 2 playlists may become incomplete','',[])
assert 'Track removal can make playlists incomplete' in m.labels(); m.row(1); passed()
# caps answered after Downloads opened (asked again once the base was idle, a request having cancelled
# it): the open level is laid out again, so its rows and what a press opens agree.
HUB=('collections','collection','playcollection','downloadliked','jobs','job','remove','download')
m=BrowseMachine(); m.caps=HUB; m.open(); m.answer(*ME)
br=payload_syms['br']; m.word(br+96,0); m.word(br+100,0)  # caps_checked, downloads: as a cancelled caps leaves them
m.advance(2250); assert m.asked()==('caps',)
m.row(6); assert m.labels()[:4]==['Downloads','Downloaded Tracks','','Download Status']
m.serve=lambda op,args: ('ok','','',[('op',n,'','','','') for n in HUB]); m.worker()
assert m.labels()==['Downloads','Downloaded Playlists','','Downloaded Tracks','','Download Progress','','Storage Management',''],m.labels()
m.row(3); assert m.labels()[0]=='Storage Management'; passed()
# A transport or temporary caps failure retries after two seconds; a valid unsupported response
# is terminal for an older add-on, without repeatedly probing it.
for failure in (None, b'Q2SPOT/1\nmalformed', ('offline','Starting','',[]), ('throttled','Wait','',[])):
    m=BrowseMachine(); m.open(); m.serve=lambda op,args: ME; m.worker(); assert m.asked()==('caps',)
    m.serve=(lambda op,args: failure) if failure is not None else None; m.worker()
    assert m.labels()==MENU and m.get(br+96)==0
    threads=len(m.threads); m.advance(1750); assert len(m.threads)==threads
    m.advance(250); assert len(m.threads)==threads+1 and m.asked()==('caps',)
    m.serve=lambda op,args: ('ok','','',[('op',n,'','','','') for n in HUB]); m.worker()
    m.row(6); assert m.labels()[1]=='Downloaded Playlists'; passed()
m=BrowseMachine(); m.open(); m.serve=lambda op,args: ME; m.worker()
m.serve=lambda op,args: ('error','Unsupported operation','',[]); m.worker()
threads=len(m.threads); m.advance(5000); assert m.get(br+96)==1 and len(m.threads)==threads; passed()
# A jobs refresh that shrinks the list must cancel a click queued against its previous rows.
m=BrowseMachine(); m.caps=HUB; m.open(); m.answer(*ME); m.row(6); m.row(2)
jobs=[('job',k,'paused','1/2',k,'1') for k in ('a','b')]
m.answer(*OK,jobs); m.advance(2250); assert m.asked()==('jobs',)
f,ctx=m.handler(m.rows()[1],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
m.serve=lambda op,args: (*OK,jobs[:1]); worker,arg=m.threads[-1]
m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)
m.call(address=payload_syms['br_tick'],args=(0,0,0,0),gap=0); m.advance(0)
assert m.labels()==['Download Progress','paused','1/2'] and m.get(br+16)==0; passed()
# Backends that don't advertise download support hide the action, with an update explanation in Downloads.
m=BrowseMachine(); m.caps=(); m.open(); m.answer(*ME); m.row(1)
m.answer(*OK,[('playlist','spotify:playlist:a','A','','','')]); m.row(0); m.answer(*OK,[])
assert m.labels()==['Nothing here']; passed()

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
# Streaming: the card apps after Spotify, by name, each with its icon and no name (stock's row
# click never sees it); a folder without its binary or its manifest, or an id that can't be one (it
# goes into shell commands), is no app.
m=AppMachine(); card_app(m,'filebrowser','name=File Browser\nqr=1\n'); card_app(m,'deezer','name=Deezer\n')
card_app(m,'gone','name=Gone\n'); m.files.pop('/mnt/mmc/.gone/q2-gone'); card_app(m,'Bad_Id','name=Bad\n')
m.tree['/mnt/mmc']+=[('.spotify',4),('Music',4)]; m.files['/mnt/mmc/.spotify/librespot']=bytearray()
# Its own icon when it ships one: the edition's size (iPod's rows 40 px, Legacy's 52), else the other.
for f in ('/mnt/mmc/.deezer/icon.png','/mnt/mmc/.deezer/icon-40.png','/mnt/mmc/.soulseek/icon.png'): m.files[f]=bytearray()
rows=stream_rows(m)
assert list(rows)==['Spotify','Deezer','File Browser','Soulseek','Tidal Downloads','Internet Radio','AirPlay','Configuration'],list(rows)
icons={'Deezer':'file:///mnt/mmc/.deezer/'+('icon-40.png' if variant=='ipod' else 'icon.png'),
       'Soulseek':'file:///mnt/mmc/.soulseek/icon.png','File Browser':'stream_app'}
for t,want in icons.items():
    icon=m.nodes[rows[t]]['children'][0]; assert m.nodes[icon]['image']==want and not m.nodes[rows[t]].get('name'),(t,m.nodes[icon]['image'])
passed()
# Stock's Baidu Netdisk row (a Chinese cloud drive; list_baidu's icon) is gone from Streaming, destroyed; its
# TIDAL row stays first.
m=AppMachine(); view=m.node('scroll_view','scroll_view_streamsset')
def stock_row(icon,label):
    item,b=m.node('list_item'),m.node('button'); m.nodes[b]['children']=[m.node('image',image=icon),m.node('hscroll_label',text=label)]
    m.nodes[item]['children']=[b]; return item
tidal,baidu=stock_row('list_tidal','TIDAL'),stock_row('list_baidu','Baidu Netdisk'); m.nodes[view]['children']=[tidal,baidu]
m.top=m.node('window','stream_page',[view])
assert m.call(address=HOOKS['stream_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
kids=m.nodes[view]['children']; assert kids[0]==tidal and baidu not in kids and m.nodes[baidu].get('destroyed') and len(kids)==7,len(kids); passed()
# Streaming's Configuration: after Stop Streaming Services, a switch row per service Streaming lists
# (stock's TIDAL, Spotify, the card apps, Tidal Downloads, Internet Radio, AirPlay), all on when
# Q2POD STREAMHIDE is missing. Centre switches one off and hides its Streaming row at once, again shows
# it; card apps are kept by id, the others by a fixed upper-case key. Configuration always shows.
m=AppMachine(); card_app(m,'deezer','name=Deezer\n'); rows=stream_rows(m); stream=m.top
m.nodes[m.wm]=dict(type='window_manager',name='',children=[stream],visible=1)
view=m.find('scroll_view',stream)
def streaming():
    kids=m.nodes[view]['children']
    return [m.nodes[kids[0]]['visible']]+[m.nodes[m.nodes[m.nodes[r]['children'][0]]['children'][1]]['text'] for r in kids[1:] if m.nodes[r]['visible']]
assert streaming()==[1,'Spotify','Deezer','Soulseek','Tidal Downloads','Internet Radio','AirPlay','Configuration']
m.click(rows['Configuration']); m.page=m.top; assert m.nodes[m.page]['name']=='streamconf_page'
SVC=['TIDAL','Spotify','Deezer','Soulseek','Tidal Downloads','Internet Radio','AirPlay']
assert m.labels()==['Configuration','Tidal Cache: 2 GB','Spotify Cache: 2 GB','Stop Streaming Services',*SVC]
cap=m.rows()[3]; assert not m.nodes[cap]['children'] and not m.nodes[cap].get('handlers')
def ticks(): return [switch(m,i)=='switch_on' for i in range(4,len(m.rows()))]  # Shanling's switches
assert ticks()==[True]*7
m.row(6); assert m.config['STREAMHIDE']=='deezer' and ticks()==[True,True,False,True,True,True,True]
assert streaming()==[1,'Spotify','Soulseek','Tidal Downloads','Internet Radio','AirPlay','Configuration']
m.row(9); m.row(4); assert m.config['STREAMHIDE']=='deezer,RADIO,TIDAL'
assert streaming()==[0,'Spotify','Soulseek','Tidal Downloads','AirPlay','Configuration']
m.row(6); assert m.config['STREAMHIDE']=='RADIO,TIDAL' and ticks()==[False,True,True,True,True,False,True]
assert streaming()==[0,'Spotify','Deezer','Soulseek','Tidal Downloads','AirPlay','Configuration']
m.row(9); m.row(4); assert m.config['STREAMHIDE']=='' and ticks()==[True]*7; passed()
# Saved: the next Streaming leaves them out, an app by its id whatever its place by name.
m=AppMachine(); card_app(m,'deezer','name=Deezer\n'); m.config['STREAMHIDE']='soulseek,AIRPLAY,SPOTIFY'
rows=stream_rows(m); view=m.find('scroll_view',m.top)
assert streaming()==[1,'Deezer','Tidal Downloads','Internet Radio','Configuration']
m.click(rows['Configuration']); m.page=m.top; assert ticks()==[True,False,True,False,True,True,False]; passed()
# Soulseek: q2-soulseek from the card's .soulseek folder.
SBIN,SBROWSE,SSTATE,SPID='/mnt/mmc/.soulseek/q2-soulseek','/tmp/q2-soulseek.browse.sock','/tmp/q2-soulseek.state','/tmp/q2-soulseek'
class SlskMachine(AppMachine): pass
SMENU=[('remote','','Phone Remote','192.168.1.23:8080','',''),('search','','Search','Online as alice','',''),
       ('list','downloads','Downloads','None yet','',''),('action','set','Sharing: Off','Nothing is shared','sharing',''),
       ('action','stop','Stop Soulseek','','','')]
SLABELS=['Soulseek','Phone Remote','192.168.1.23:8080','Search','Online as alice','Downloads','None yet','Sharing: Off',
         'Nothing is shared','Stop Soulseek','']
# Gone from the card since Streaming listed it: the page says so and nothing starts.
m=SlskMachine(); m.gone(); m.open()
assert m.labels()==["Soulseek isn't on the card",'Install it again in System settings → Apps'] and not m.systems and not m.threads
m.row(0); assert not m.threads; m.poll(); assert not m.systems; m.close(); passed()
# With it: started, the menu asked; no socket yet is the add-on starting, asked again; its rows.
m=SlskMachine(); m.open(); assert m.systems==['/bin/sh /mnt/mmc/.soulseek/run'] and m.labels()==['Loading…']
m.worker(); assert m.labels()==['Starting Soulseek…']; m.advance(500)
m.answer('ok','refresh','',SMENU); assert m.requests[-1]==('menu',) and m.labels()==SLABELS; passed()
# A live level is asked again every 2 s while on top; the same rows aren't rebuilt, changed ones are,
# their selected row kept.
rows=m.rows(); m.nodes[m.find('scroll_view')]['_ringnav_index']=2; m.advance(2000); m.worker()
assert len(m.requests)==2 and m.requests[-1]==("menu",) and m.rows()==rows
changed=[*SMENU[:2],('list','downloads','Downloads','1 downloading','',''),*SMENU[3:]]
m.advance(2000); m.answer('ok','refresh','',changed)
assert m.rows()!=rows and m.labels()[6]=='1 downloading' and m.nodes[m.find('scroll_view')]['_ringnav_index']==2,(m.labels(),m.nodes[m.find('scroll_view')].get('_ringnav_index')); passed()
# A list row opens its level; Back asks for the menu again, the row kept.
m.pick(2); m.answer('ok','refresh','',[('info','','No downloads yet','Search, then press a file','','')])
assert m.requests[-1]==('downloads',) and m.labels()==['Downloads','No downloads yet','Search, then press a file']; asked=len(m.threads); m.row(0); assert len(m.threads)==asked
m.back(); m.answer('ok','refresh','',changed)
assert m.requests[-1]==('menu',) and m.nodes[m.find('scroll_view')]['_ringnav_index']==2,(m.requests[-1],m.nodes[m.find('scroll_view')].get('_ringnav_index'))
# An action row sends its op with its context, toasts the reply's message and asks for the level again.
m.pick(3); m.answer('ok','Sharing: While charging','',[])
assert m.requests[-1]==('set','sharing') and m.toasts[-1][3]=='Sharing: While charging',(m.requests[-1],m.toasts[-1:])
m.answer('ok','refresh','',SMENU); assert m.requests[-1]==('menu',) and m.labels()==SLABELS; passed()
# Search: the T9 box, its OK opening "search" for the text, escaped.
m.pick(1); edit=m.find('edit'); assert m.nodes[edit]['tips']=='Search Soulseek'
m.nodes[edit]['text']='aphex\ttwin'; f,ctx=m.handler(edit,O['EVT_VALUE_CHANGED'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.advance(0,clear=False)
m.answer('ok','refresh','50',[('action','download','01 Xtal.flac','bob · FLAC 16/44.1 · 31 MB · free','12.3','downloadfolder')])
assert m.wire[-1].endswith(b'\tsearch\taphex%09twin\n'),m.wire[-1]
assert m.labels()==['aphex\ttwin','01 Xtal.flac','bob · FLAC 16/44.1 · 31 MB · free','More…',''],m.labels()
# A press downloads the file; Play/Pause held, the hold op (its whole folder); More… the next page.
m.pick(0); m.answer('ok','Downloading 01 Xtal.flac','',[]); assert m.requests[-1]==('download','12.3') and m.toasts[-1][3]=='Downloading 01 Xtal.flac'
m.answer('ok','refresh','50',[('action','download','01 Xtal.flac','bob','12.3','downloadfolder')])
m.top=m.page; m.status=m.alloc(0x200); m.nodes[m.find('scroll_view')]['_ringnav_index']=0; m.press(80)
assert m.hold()==11; m.answer('ok','Downloading the folder','',[]); assert m.requests[-1]==('downloadfolder','12.3') and m.toasts[-1][3]=='Downloading the folder'
m.answer('ok','refresh','50',[('action','download','01 Xtal.flac','bob','12.3','downloadfolder')])
m.pick(1); m.answer('ok','','100',[('info','','Second','','','')]); assert m.requests[-1]==('search','aphex%09twin','50'),m.requests[-1]
# More… again: the list's own arguments and the newest cursor alone, Q2SPOT/1's last argument (to 1.4.1 each
# cursor followed the ones before, "search aphex 50 100", which q2-deezer read as the second page again).
m.pick(1); m.answer('ok','','',[]); assert m.requests[-1]==('search','aphex%09twin','100'),m.requests[-1]
m.back(); m.answer('ok','','100',[('info','','Second','','','')]); assert m.requests[-1]==('search','aphex%09twin','50'); passed()
# Back twice to the menu; Phone Remote shows the QR code page from the add-on's status file.
m.back(); m.answer('ok','refresh','50',[]); m.back(); m.answer('ok','refresh','',SMENU); assert m.labels()==SLABELS
code=[[(x*7+y*3)%5<2 for x in range(21)] for y in range(21)]
hexed=''.join(''.join('%x'%sum(8>>b for b in range(4) if d*4+b<21 and row[d*4+b]) for d in range(6)) for row in code)
m.files[SSTATE]=bytearray(f'addr=192.168.1.23:8080\ndl=0\nhold=0\nnote=Online as alice\npin=548001\nqr={hexed}\nqrsize=21\n'.encode())
m.pick(0); qp=m.top; assert m.nodes[qp]['name']=='qrcode_page'
assert {'Soulseek','192.168.1.23:8080','548 001','Online as alice','Scan with your',"phone's camera"}<=set(texts(qp)),texts(qp)
qr=next(w for w in m.nodes if m.nodes[w]['type']=='view' and m.get(w+O['W_PARENT'])==qp and cf_geometry(m,w)==(16,40,186,186))
m.bands.clear(); m.rounded.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(qr,m.canvas,0,0))
mod,off=186//25,(186-186//25*21)//2; want=[]
for y,row in enumerate(code):
    x=0
    while x<21:
        r=0
        while x+r<21 and row[x+r]: r+=1
        if r: want.append((off+x*mod,off+y*mod,r*mod,mod))
        x+=r or 1
assert [b[:4] for b in m.bands]==want and {b[4] for b in m.bands}=={color_t(0)} and m.rounded[0]['rect']==(0,0,186,186),(m.bands[:3],want[:3],m.rounded[:1]); passed()
# No address (no Wi-Fi): the code hides and the page says so; a second later it follows the file.
m.files[SSTATE]=bytearray(b'dl=0\nhold=0\nnote=Connect the Q2 to Wi-Fi\npin=548001\nqr=\nqrsize=0\n'); m.advance(1000)
assert not m.nodes[qr]['visible'] and 'Connect the Q2 to Wi-Fi' in texts(qp),texts(qp); m.destroy(qp); m.top=m.page; passed()
# Holding standby off: while the add-on asks (hold=1), each poll resets standby and auto power-off
# (not the screen's); once it stops by itself (its loop's pid gone), it is let go.
m.files[SSTATE]=bytearray(b'addr=x\ndl=1\nhold=1\n'); names=m.poll()
assert ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls]
m.files[SSTATE]=bytearray(b'addr=x\ndl=0\nhold=0\n'); m.poll(); assert 'reset_poweroptions_timer' not in [c[0] for c in m.calls]
m.files[SPID]=bytearray(b'123'); m.poll(11000); m.files[SSTATE]=bytearray(b'hold=1\n'); m.files.pop(SPID); m.poll(); m.poll()
assert 'reset_poweroptions_timer' not in [c[0] for c in m.calls]; passed()
# USB mode, before stock unmounts the card: the add-on asked to end and waited for (its loop's pid
# file gone), then stock's page; started again once USB mode is over and the card is back.
m=SlskMachine(); m.open(); m.answer('ok','refresh','',SMENU); m.destroy(m.page); m.files[SPID]=bytearray(b'123')
m.on_sleep=lambda: m.files.pop(SPID,None) if m.now>=t0+300 else None; t0=m.now; m.systems=[]
tramp=int(manifest['patch_symbols']['stock_usbmode_trampoline'],16); m.handlers[tramp]='stock_usbmode'; m.u.hook_add(UC_HOOK_CODE,m.hook,begin=tramp,end=tramp)
m.call(address=HOOKS['usbmode_page_init'][0],args=(0x55,0x66,0,0),gap=0,count=5_000_000); m.on_sleep=None
names=[c[0] for c in m.calls]
assert m.systems==['kill $(cat /tmp/q2-soulseek) 2>/dev/null || rm -f /tmp/q2-soulseek'] and names.index('stock_usbmode')>names.index('sleep_ms') and SPID not in m.files,(m.systems,names[-12:])
m.open_pages={'usbmode_page'}; m.poll(); assert m.systems==['kill $(cat /tmp/q2-soulseek) 2>/dev/null || rm -f /tmp/q2-soulseek']
m.open_pages=set(); m.poll(); assert m.systems[-1]=='/bin/sh /mnt/mmc/.soulseek/run'; passed()
# Stop Streaming Services stops it too.
m.systems=[]; run(m,'addon_stop'); assert m.systems==['kill $(cat /tmp/q2-soulseek) 2>/dev/null || rm -f /tmp/q2-soulseek']; passed()
# Requests no longer answered while the app may still run (its socket gone, or the app frozen): the
# error page offers Stop Soulseek beside Retry. It ends the app through its loop, which a frozen app
# can't refuse as it could its own stop, toasts and leaves the page; nothing starts it again.
SKILL='kill $(cat /tmp/q2-soulseek) 2>/dev/null || rm -f /tmp/q2-soulseek'
def slsk_gone():
    """Soulseek's menu, then its loop still running (its pid file) but its socket gone."""
    m=SlskMachine(); m.open(); m.answer('ok','refresh','',SMENU); m.files[SPID]=bytearray(b'123')
    m.serve=None; m.advance(10000); m.worker(); return m
m=slsk_gone(); assert m.labels()==["Soulseek isn't running",'Retry','Stop Soulseek'],m.labels()
m.systems=[]; m.row(1)
assert m.systems==[SKILL] and m.toasts[-1][3]=='Soulseek stopped' and 'navigator_back' in [c[0] for c in m.calls],(m.systems,m.toasts[-1:])
m.close(); m.files.pop(SPID); m.poll(); m.poll(); assert m.systems==[SKILL]; passed()
# Retry there restarts it: its loop asked to end, the page waiting as for a start, and the app started
# again once the loop's pid file is gone; then its rows.
m=slsk_gone(); m.systems=[]; m.row(0); m.worker()
assert m.systems==[SKILL] and m.labels()==['Starting Soulseek…'],(m.systems,m.labels())
m.poll(); assert m.systems==[SKILL]  # the old loop still there
m.files.pop(SPID); m.poll(); assert m.systems==[SKILL,'/bin/sh /mnt/mmc/.soulseek/run'],m.systems
m.advance(500); m.answer('ok','refresh','',SMENU); assert m.labels()==SLABELS,m.labels(); passed()
# A level loading for 3 s (a frozen app holding the request, which gives up at 15 s) shows Stop
# Soulseek under Loading….
m=SlskMachine(); m.open(); m.answer('ok','refresh','',SMENU); m.files[SPID]=bytearray(b'123'); m.pick(2)
assert m.labels()==['Loading…']; m.advance(2900); assert m.labels()==['Loading…']
m.advance(400); assert m.labels()==['Loading…','Stop Soulseek'],m.labels()  # the page's next 250 ms tick
m.systems=[]; m.row(0); assert m.systems==[SKILL] and m.toasts[-1][3]=='Soulseek stopped' and 'navigator_back' in [c[0] for c in m.calls]
m.close(); passed()
# An app that isn't running (it stopped by itself) has nothing to stop: Retry alone, which starts it.
m=SlskMachine(); m.open(); m.answer('ok','refresh','',SMENU); m.serve=None; m.advance(10000); m.poll(); m.worker()
assert m.labels()==["Soulseek isn't running",'Retry'],m.labels()
m.systems=[]; m.row(0); assert m.systems==['/bin/sh /mnt/mmc/.soulseek/run'],m.systems; m.close(); passed()

# File Browser: q2-filebrowser from the card's .filebrowser folder, the same page on its own socket.
# Its manifest's qr=1: turned on by its row, its QR code page opens once it answers, the menu behind
# it; opened again while it runs, the menu alone.
FBIN,FBROWSE,FSTATE,FPID='/mnt/mmc/.filebrowser/q2-filebrowser','/tmp/q2-filebrowser.browse.sock','/tmp/q2-filebrowser.state','/tmp/q2-filebrowser'
class FileBrowserMachine(AppMachine):
    app,manifest='filebrowser','name=File Browser\nsearch=Search\nqr=1\n'
FMENU=[('remote','','Open on Your Phone','http://192.168.1.23:8000 · PIN 548 001','',''),('info','','Card: 12.3 GB free','of 59.4 GB','',''),
       ('action','readonly','Read Only: Off','The phone can upload, rename, move and delete','',''),
       ('action','stop','Turn Off File Browser','It also turns off after 15 minutes unused','','')]
FLABELS=['File Browser','Open on Your Phone','http://192.168.1.23:8000 · PIN 548 001','Card: 12.3 GB free','of 59.4 GB','Read Only: Off',
         'The phone can upload, rename, move and delete','Turn Off File Browser','It also turns off after 15 minutes unused']
m=FileBrowserMachine(); m.gone(); m.open()
assert m.labels()==["File Browser isn't on the card",'Install it again in System settings → Apps'] and not m.systems; m.close(); passed()
m=FileBrowserMachine(); m.open(); assert m.systems==['/bin/sh /mnt/mmc/.filebrowser/run'] and m.labels()==['Loading…']
m.worker(); assert m.labels()==['Starting File Browser…']; m.advance(500)
m.files[FSTATE]=bytearray(f"addr=192.168.1.23:8000\nhold=0\nnote=Manage the card's files there\npin=548001\nqr={hexed}\nqrsize=21\nversion=q2f-v1.0\n".encode())
m.answer('ok','refresh','',FMENU); assert m.requests[-1]==('menu',) and m.labels()==FLABELS
qp=m.top; assert m.nodes[qp]['name']=='qrcode_page'
assert {'File Browser','192.168.1.23:8000','548 001',"Manage the card's files there",'Scan with your'}<=set(texts(qp)),texts(qp)
m.destroy(qp); m.top=m.page
# Asked again while on top: the same rows, and no second code page.
m.advance(2000); m.worker(); assert m.requests[-1]==('menu',) and m.top==m.page and m.labels()==FLABELS; passed()
# Read Only from the Q2; Open on Your Phone shows the code again.
m.pick(2); m.answer('ok','Read only','',[]); assert m.requests[-1]==('readonly',) and m.toasts[-1][3]=='Read only'
m.answer('ok','refresh','',FMENU); m.pick(0); assert m.nodes[m.top]['name']=='qrcode_page'; m.destroy(m.top); m.top=m.page; passed()
# Opened again while it runs (its loop's pid there): nothing started, the menu alone.
m.close(); m.systems=[]; m.files[FPID]=bytearray(b'9'); m.open(); assert not m.systems
m.answer('ok','refresh','',FMENU); assert m.top==m.page and m.labels()==FLABELS; passed()
# Turn Off: its reply toasted, the page left.
m.calls=[]; m.pick(3); m.answer('ok','File Browser off','',[])
assert m.requests[-1]==('stop',) and m.toasts[-1][3]=='File Browser off' and 'navigator_back' in [c[0] for c in m.calls]; m.close()
# Stop Streaming Services stops it too.
m.systems=[]; run(m,'addon_stop'); assert m.systems==['kill $(cat /tmp/q2-filebrowser) 2>/dev/null || rm -f /tmp/q2-filebrowser']; passed()

# Deezer: q2-deezer from the card's .deezer folder. Nothing streams: a press downloads, to Deezer/ on
# the card, and a song on the card, a local row, plays from it as local music (navigation.c songs_open).
DZBIN,DZBROWSE,DZSTATE,DZPID='/mnt/mmc/.deezer/q2-deezer','/tmp/q2-deezer.browse.sock','/tmp/q2-deezer.state','/tmp/q2-deezer'
class DeezerMachine(AppMachine):
    app,manifest='deezer','name=Deezer\nsearch=Search Deezer\nqr=0\n'
m=DeezerMachine(); m.gone(); m.open()
assert m.labels()==["Deezer isn't on the card",'Install it again in System settings → Apps'] and not m.systems; m.close(); passed()
# Started, the menu asked; signed out, its sign-in row opens the code over its status file (and only
# then: Deezer's row doesn't open it by itself).
m=DeezerMachine(); m.open(); assert m.systems==['/bin/sh /mnt/mmc/.deezer/run'] and m.labels()==['Loading…']
m.worker(); assert m.labels()==['Starting Deezer…']; m.advance(500)
DZOUT=[('remote','','Sign In from Your Phone','Press to show the code','',''),('action','stop','Stop Deezer','Downloads carry on next time','','')]
m.files[DZSTATE]=bytearray(f"addr=192.168.1.23:8090\nhold=0\nnote=Paste your Deezer ARL there\npin=135790\nqr={hexed}\nqrsize=21\n".encode())
m.answer('ok','refresh','',DZOUT); assert m.requests[-1]==('menu',) and m.top==m.page
assert m.labels()==['Deezer','Sign In from Your Phone','Press to show the code','Stop Deezer','Downloads carry on next time']
assert [chevron(m,i) for i in range(2)]==[True,False]  # remote opens the QR page
m.pick(0); qp=m.top; assert m.nodes[qp]['name']=='qrcode_page'
assert {'Deezer','192.168.1.23:8090','135 790','Paste your Deezer ARL there'}<=set(texts(qp)),texts(qp); m.destroy(qp); m.top=m.page; passed()
# Signed in: Play/Pause held on a list downloads all of it; a list's song row, pressed, downloads it.
SONGS='/mnt/mmc/Deezer'
DZMENU=[('local',SONGS+'/','Downloaded Songs','2 songs on the card','',''),('list','tracks','Liked Songs','','liked','download'),
        ('search','','Search Deezer','','',''),('action','stop','Stop Deezer','Downloads carry on next time','','')]
m.advance(2000); m.answer('ok','refresh','',DZMENU)
assert m.labels()==['Deezer','Downloaded Songs','2 songs on the card','Liked Songs','','Search Deezer','','Stop Deezer','Downloads carry on next time'],m.labels()
assert [chevron(m,i) for i in range(4)]==[True,True,False,False]  # a folder and a list open pages; search and actions don't
m.top=m.page; m.status=m.alloc(0x200); m.nodes[m.find('scroll_view')]['_ringnav_index']=1; m.press(80)
assert m.hold()==11; m.answer('ok','Downloading 12 songs','',[])
assert m.requests[-1]==('download','liked') and m.toasts[-1][3]=='Downloading 12 songs'
m.answer('ok','refresh','',DZMENU); m.pick(2); assert m.nodes[m.find('edit')]['tips']=='Search Deezer'
f,ctx=m.handler(m.find('edit'),O['EVT_BLUR']); m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000); m.advance(0,clear=False)
LIKED=[('action','download','Download All','12 songs','liked',''),('action','download','Valerie','Broadcast','liked#5','download'),
       ('local',SONGS+'/Corporeal - Broadcast [6].flac','Corporeal','Broadcast · on the card','','')]
m.pick(1); m.answer('ok','refresh','',LIKED); assert m.requests[-1]==('tracks','liked') and m.labels()[:3]==['Liked Songs','Download All','12 songs'],m.labels()
m.pick(1); m.answer('ok','Downloading','',[]); assert m.requests[-1]==('download','liked#5') and m.toasts[-1][3]=='Downloading'
m.answer('ok','refresh','',LIKED); passed()
# A song on the card plays its folder from it, newest first, as local music: the card's songs only.
names=('Valerie - Broadcast [5].flac','Corporeal - Broadcast [6].flac','.Valerie - Broadcast [5]-1.flac.part')
recs=[m.song(n) for n in names]
for r,n,t in zip(recs,names,(100,200,300)):
    m.word(r+O['REC_PATH'],m.string(f'{SONGS}/{n}')); m.files[f'{SONGS}/{n}']=bytearray(); m.stats[f'{SONGS}/{n}']=t
m.word(recs[2]+O['REC_TYPE'],0)  # a download's .part isn't a song
m.found=recs; m.plays=[]; m.pick(2)
assert [p[2:4] for p in m.plays]==[(0,0xf001)] and m.play_names==['Corporeal - Broadcast [6].flac','Valerie - Broadcast [5].flac'],(m.plays,m.play_names)
assert [m.text(c[1]) for c in m.calls if c[0]=='toolsLoadDirectory'][-1]==SONGS; passed()
# Downloaded Songs (its uri a folder) lists them, newest first, labelled as OpenDeezer names them,
# and a row plays from it; a song gone from the card says so.
m.back(); m.answer('ok','refresh','',DZMENU); m.plays=[]; m.pick(0); songs=m.top
assert m.nodes[songs]['name']=='app_songs' and [l for l in m.labels(songs) if l]==['Downloaded Songs','Corporeal - Broadcast','Valerie - Broadcast'],m.labels(songs)
m.click(m.nodes[m.find('scroll_view',songs)]['children'][1])
assert [p[2:4] for p in m.plays]==[(1,0xf001)] and m.play_names==['Corporeal - Broadcast [6].flac','Valerie - Broadcast [5].flac'],(m.plays,m.play_names)
m.destroy(songs); m.top=m.page; m.found=recs[:1]; m.advance(2000); m.answer('ok','refresh','',DZMENU)
m.pick(1); m.answer('ok','refresh','',LIKED); n=len(m.toasts); m.pick(2)
assert m.toasts[n:] and m.toasts[-1][3]=="It isn't on the card any more",m.toasts[n:]; passed()
# Tidal Downloads ends in Clear All Downloads…, which asks first, as Spotify's Storage Management does:
# Cancel or Return keeps the songs; Remove deletes the files in .tidal, a .tmp too, but not .cache-size
# (Configuration's cap), and the page lists what is left: none.
T='/mnt/mmc/.tidal'; names=('11-2 A - One.flac','12-2 B - Two.m4a'); recs=[m.song(n) for n in names]
for r,n,t in zip(recs,names,(100,200)):
    m.word(r+O['REC_PATH'],m.string(f'{T}/{n}')); m.files[f'{T}/{n}']=bytearray(); m.stats[f'{T}/{n}']=t
m.files[T+'/13-2 C.flac.tmp']=bytearray(); m.files[T+'/.cache-size']=bytearray(b'2048M'); m.files['/mnt/mmc/Music/keep.flac']=bytearray()
m.tree[T]=[(n,8) for n in (*names,'13-2 C.flac.tmp','.cache-size')]; m.found=recs
assert run(m,'tidal_downloads',0,0)==0; td=m.top; tdrows=lambda: m.nodes[m.find('scroll_view',td)]['children']
LIST=['Tidal Downloads','B - Two','A - One','Clear All Downloads…']
assert m.nodes[td]['name']=='tidal_downloads' and [l for l in m.labels(td) if l]==LIST,m.labels(td)
ASK=['Remove Downloads?','2 tracks','Keeps settings','Cancel','Remove']
m.click(tdrows()[2]); assert [l for l in m.labels(td) if l]==ASK,m.labels(td)
m.click(tdrows()[1]); assert [l for l in m.labels(td) if l]==LIST  # Cancel, back on the Clear row
assert m.nodes[m.find('scroll_view',td)]['_ringnav_index']==2
m.click(tdrows()[2]); m.back(td); assert [l for l in m.labels(td) if l]==LIST and m.top==td  # Return is Cancel
m.click(tdrows()[2]); m.found=[]  # stock's listing of the emptied folder
m.click(tdrows()[2]); assert [p for p in m.files if p.startswith(T)]==[T+'/.cache-size'] and '/mnt/mmc/Music/keep.flac' in m.files
assert [l for l in m.labels(td) if l]==['No downloads yet']; m.destroy(td); passed()
# Stream mode (q2-deezer's stream.go, addon.c stream_poll): its .play file names a session's folder; its
# first song, once there, plays as a local album does (Now Playing opening); songs that come later join the
# queue's end, as Add to Queue does, while the queue is still the session's; the song playing is left in
# .playing for the app; once another queue plays, the session is left alone.
STREAM='/mnt/mmc/.deezer/stream/1700'
def stream_songs(*names):  # the folder's mtime steps with each change: stream_newer scans only then
    m.tree[STREAM]=[(n,8) for n in names]; rs=[]; m.stats[STREAM]=m.stats.get(STREAM,0)+1
    for n in names:
        if n.endswith('.part'): continue
        r=m.song(n); m.word(r+O['REC_PATH'],m.string(f'{STREAM}/{n}')); m.stats[f'{STREAM}/{n}']=1; rs.append(r)
    m.found=rs; return rs
m.files[DZPID]=bytearray(b'4242')  # running: its loop's pid
m.plays=[]; m.files['/tmp/q2-deezer.play']=bytearray(f'1700 {STREAM}\n'.encode()); stream_songs('01 - Snow [12].flac.part')
m.poll(); m.advance(0); assert not m.plays  # nothing whole yet
recs=stream_songs('01 - Snow [12].flac','02 - Oakwood [13].flac.part')
m.poll(); m.advance(0)
assert [p[2:4] for p in m.plays]==[(0,0xf001)] and m.play_names==['01 - Snow [12].flac'],(m.plays,m.play_names); passed()
m.word(syms['mcl_pdeqplaylist'],m.deque(recs)); m.word(O['MCL_POOL'],m.deque([0],'int')); m.word(O['MCL_POS'],0)
m.poll(); assert m.files['/tmp/q2-deezer.playing']==f'{STREAM}/01 - Snow [12].flac\n'.encode() and len(m.plays)==1
seen=m.stats[STREAM]; stream_songs('01 - Snow [12].flac','02 - Oakwood [13].flac'); m.stats[STREAM]=seen
m.poll(); assert len(m.names())==1  # the folder's mtime unchanged: not scanned
stream_songs('01 - Snow [12].flac','02 - Oakwood [13].flac','03 - Chateau [14].flac'); m.poll()
assert m.names()==['01 - Snow [12].flac','02 - Oakwood [13].flac','03 - Chateau [14].flac'] and len(m.plays)==1,m.names()
m.word(O['MCL_POS'],1); m.poll(); assert m.files['/tmp/q2-deezer.playing']==f'{STREAM}/02 - Oakwood [13].flac\n'.encode(); passed()
# Played on past the first ones, which the app deleted: a later song still joins, after the last queued.
stream_songs('03 - Chateau [14].flac','04 - Cellar Door [15].flac'); m.poll()
assert m.names()[-2:]==['03 - Chateau [14].flac','04 - Cellar Door [15].flac'] and len(m.names())==4; passed()
# Another queue: no song joins it, and a new session plays again.
m.word(syms['mcl_pdeqplaylist'],m.deque([m.song('Valerie - Broadcast [5].flac')])); stream_songs('05 - Snow [16].flac'); m.poll()
assert m.names()==['Valerie - Broadcast [5].flac'] and len(m.plays)==1
STREAM='/mnt/mmc/.deezer/stream/1800'; m.files['/tmp/q2-deezer.play']=bytearray(f'1800 {STREAM}\n'.encode())
stream_songs('01 - Bloodhound [20].flac'); m.poll(); m.advance(0)
assert len(m.plays)==2 and m.play_names==['01 - Bloodhound [20].flac']; passed()
# Only a hidden folder of the card is played.
m.files['/tmp/q2-deezer.play']=bytearray(b'1900 /mnt/mmc/Music\n'); m.tree['/mnt/mmc/Music']=[('a.flac',8)]; m.poll(); m.advance(0)
assert len(m.plays)==2; passed()
# An album's folder (Organize Files: Artist/Album/07 - Title [id].flac), its row's context "album":
# its songs listed and played in track order, by name, whatever their dates.
ALBUM=SONGS+'/Broadcast/Tender Buttons'
names=('02 - Valerie [5].flac','01 - Tears in the Typing Pool [4].flac','03 - Corporeal [6].flac')
recs=[m.song(n) for n in names]
for r,n,t in zip(recs,names,(300,200,100)):
    m.word(r+O['REC_PATH'],m.string(f'{ALBUM}/{n}')); m.files[f'{ALBUM}/{n}']=bytearray(); m.stats[f'{ALBUM}/{n}']=t
m.found=recs; m.back(); m.answer('ok','refresh','',[('local',ALBUM+'/','Tender Buttons','3 songs','album','')])
m.plays=[]; m.pick(0); songs=m.top
assert m.nodes[songs]['name']=='app_songs' and [l for l in m.labels(songs) if l]==['Tender Buttons','01 - Tears in the Typing Pool','02 - Valerie','03 - Corporeal'],m.labels(songs)
m.click(m.nodes[m.find('scroll_view',songs)]['children'][1])
assert [p[2:4] for p in m.plays]==[(1,0xf001)] and m.play_names==list(sorted(names)),(m.plays,m.play_names)
m.destroy(songs); m.top=m.page; m.advance(2000); m.answer('ok','refresh','',[('local',f'{ALBUM}/{names[0]}','Valerie','Broadcast · on the card','album','')])
m.plays=[]; m.pick(0); assert [p[2:4] for p in m.plays]==[(1,0xf001)] and m.play_names==list(sorted(names)),(m.plays,m.play_names); passed()
# Stop Streaming Services, USB storage and Rockbox stop it as the other apps (it writes to the card).
m.close(); m.files[DZPID]=bytearray(b'7'); m.systems=[]; run(m,'addon_stop')
assert 'kill $(cat /tmp/q2-deezer) 2>/dev/null || rm -f /tmp/q2-deezer' in m.systems,m.systems; passed()

# About: FW. Version shows the stock firmware's version again, not the updater tag in demo's
# literal, and a CFW. Version row follows it. Stock's own row builder (0x4bc274) builds Model and FW.
# Version, so the added row is checked against the real stock widgets, geometry and styles.
m=ShuffleMachine(); m.handlers[int(manifest['patch_symbols']['stock_about_trampoline'],16)]='stock_about'
m.mock('strcpy@GLIBC_2.0','widget_set_tr_text')
def tree(w):
    n=m.nodes[w]
    return (n['type'],[m.get(w+O[k]) for k in ('W_X','W_Y','W_W','W_H')],n.get('style'),
            n.get('hscroll_label_set_only_focus'),n.get('hscroll_label_set_ellipses'),[tree(c) for c in n['children']])
view=m.node('scroll_view','scroll_view_about'); m.top=m.node('window','about_page',[view])
for i in range(7):
    row=m.node('list_item'); m.nodes[view]['children'].append(row)
    if i<2: assert m.call(address=0x4bc274,args=(row,i,0,0),gap=0)==0
rows=list(m.nodes[view]['children'])
assert m.call(address=HOOKS['systemset_about_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and m.calls[0][:3]==('stock_about',m.top,5)
kids=m.nodes[view]['children']; assert kids[:2]==rows[:2] and kids[3:]==rows[2:]
fw=m.nodes[m.nodes[rows[1]]['children'][0]]['children'][1]; assert m.nodes[fw]['text']=='V1.32'
item=kids[2]; button=m.nodes[item]['children'][0]; title,value=m.nodes[button]['children']
# Stock's row, title and value widgets alike.
want=tree(m.nodes[rows[1]]['children'][0])
assert m.nodes[item]['style']=='s_listitem_black' and tree(button)==want
assert m.nodes[title]['text']=='CFW. Version' and m.nodes[value]['text']==f"{VERSION}{' Pod' if variant=='ipod' else ''}{f" dev {manifest['dev']}" if manifest['dev'] else ''}"
assert not m.nodes[button].get('handlers') and not m.nodes[button].get('name'); passed()

# Network Update (netupdate.c): System Update's second row, built by stock's own row builder
# (0x4c3140), is renamed "2" so stock's click (atoi of the name: 0 TF card, 1 Shanling) skips it, and
# fetches the latest GitHub release over verified TLS to the card's update.tar (a .part, renamed),
# then runs stock's TF card update (0x4c2994). A failure keeps the old update.tar.
m=RadioMachine(); resume=int(manifest['patch_symbols']['stock_fwupdate_trampoline'],16); m.handlers[resume]='stock_fwupdate'
m.u.hook_add(UC_HOOK_CODE,m.hook,begin=resume,end=resume)  # fast(): payload addresses hook one by one
m.handlers[0x4c2994]='fwupdate_tf'; m.mock('strcpy@GLIBC_2.0','widget_set_tr_text')
view=m.node('scroll_view','scroll_view_fwupdate'); m.top=m.node('window','fwupdate_page',[view])
for i in range(2):
    row=m.node('list_item'); m.nodes[view]['children'].append(row)
    assert m.call(address=0x4c3140,args=(row,i,0,0),gap=0)==0
tf,online=(m.nodes[r]['children'][0] for r in m.nodes[view]['children'])
assert m.call(address=HOOKS['systemset_fwupdate_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
assert m.calls[0][:3]==('stock_fwupdate',m.top,5) and m.nodes[tf]['name']=='0' and m.nodes[online]['name']=='2'
label=m.nodes[online]['children'][0]; assert m.nodes[label]['type']=='hscroll_label'
def press():
    f,ctx=[(f,c) for t,f,c in m.nodes[online]['handlers'] if t==O['EVT_CLICK']][-1]
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.advance(0,clear=False)
UPDATE='/mnt/mmc/update.tar'; ASSET='update.tar' if variant=='ipod' else 'update-legacy.tar'
RELEASES=f"https://github.com/{manifest['update_repo']}/releases"  # build.py --update-repo
m.files[UPDATE]=bytearray(b'old')
m.wifi=-1; press(); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.threads; m.wifi=3
def fetch(codes,tag,reply=b'',done=500):
    press(); assert m.nodes[label]['text']=='Checking…'
    m.codes=list(codes); m.location=f'{RELEASES}/tag/{tag}'; m.reply=reply
    m.advance(500,clear=False)  # while it runs: the percentage, and standby and auto power-off wait
    assert m.nodes[label]['text']=='Checking…' and ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls]
    worker,arg=m.threads[-1]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0
    threads=len(m.threads); press(); assert len(m.threads)==threads  # ended, not yet joined: nothing
    m.advance(done,clear=False)
    assert ('widget_set_tr_text',label) in [c[:2] for c in m.calls] and UPDATE+'.part' not in m.files
fetch([302,200],'9.9.9',b'TAR'*100)
assert m.opts[10002]==f'{RELEASES}/latest/download/{ASSET}' and m.opts[52]==1
assert m.opts[10065]=='/etc/scrobble-ca.pem' and m.opts[64]==1 and m.opts[81]==2 and m.opts[99]==1 and m.opts[20108]
assert bytes(m.files[UPDATE])==b'TAR'*100 and 'fwupdate_tf' in [c[0] for c in m.calls]
m.files[UPDATE]=bytearray(b'old')
fetch([302,404],'9.9.9'); assert m.toasts[-1][3]=='No update for this build' and m.files[UPDATE]==b'old'
fetch([200],'9.9.9'); assert m.toasts[-1][3]=="Can't reach GitHub"  # no redirect: no tag
if not manifest['dev']:  # --dev builds have no release tag, so always fetch
    fetch([302],VERSION); assert m.toasts[-1][3]=='Latest firmware installed' and m.files[UPDATE]==b'old'
    fetch([302],'1.0.0'); assert m.toasts[-1][3]=='Latest firmware installed'  # older: never a downgrade
# Leaving the page cancels before the download starts: the card may be unmounted or shared next.
press(); m.codes=[302,200]; m.location=f'{RELEASES}/tag/9.9.9'; m.reply=b'NEW'
f,ctx=m.handler(label,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
worker,arg=m.threads[-1]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0
m.advance(500,clear=False); assert m.toasts[-1][3]=='Download cancelled' and m.files[UPDATE]==b'old'
assert 'fwupdate_tf' not in [c[0] for c in m.calls]; passed()

# Q2 Shelf (netupdate.c, docs/internals.md#q2-shelf): System settings' Apps page, a row per app with
# its state from .q2/apps/<id>.tag, a manual install's binary and, with Wi-Fi, a check of GitHub's
# latest releases on opening, kept SH_STALE_MS. Install and Update start a worker at once; Remove, Erase
# and Rockbox's first Install (the power-on default) want a second press. A press on the running
# install cancels it; one during the opening check stops the check.
SPOT_TAG,SPOT_FILES='/mnt/mmc/.q2/apps/spotify.tag','/mnt/mmc/.q2/apps/spotify.files'
class ShelfMachine(RadioMachine):
    """RadioMachine with busybox's side scripted: unzip -l prints listing, unzip -o stages staged
    ((path, bytes), in find's order), find lists them; statfs has free bytes; renames kept in order."""
    def __init__(self,files=None):
        super().__init__(files); self.free=1<<32; self.moves=[]; self.listing=''; self.staged=[]
        self.mock('rmdir@GLIBC_2.0')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
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
LIST='/mnt/mmc/.q2/sources.list'
m=ShelfMachine({LIST:bytearray((ROOT/'sources.list').read_bytes()),SPOT_TAG:bytearray(b'q2-v1.8'),SPOT_FILES:bytearray(b'.spotify/run\n.spotify/librespot\n'),
                '/mnt/mmc/.spotify/run':bytearray(b'sh'),'/mnt/mmc/.spotify/librespot':bytearray(b'ELF')})
m.rockbox_mode=0; m.wifi=-1; apps_open(m)
assert m.labels()==['App Store','Rockbox','Install','Spotify','Installed','Deezer','Install','Soulseek','Install','File Browser','Install'] and not m.threads; passed()  # no themes without Rockbox; the card apps last
m.close(); m.wifi=3; m.rockbox_mode=0o100755; m.dirs['/mnt/mmc/.spotify']=[]; apps_open(m)
assert m.labels()==['App Store','Rockbox','Update','Rockbox Themes','Install','Spotify','Installed','Erase Spotify Data','Login and cache','Deezer','Install','Soulseek','Install','File Browser','Install']
m.reply=(ROOT/'test/shelf/q2-librespot.json').read_bytes(); shelf_job(m)
assert m.opts[10002]=='https://api.github.com/repos/group-a-study/q2-filebrowser/releases/latest' and m.opts[10018]=='Q2Pod'  # the last of six
assert m.opts[10065]=='/etc/scrobble-ca.pem' and m.opts[64]==1 and m.opts[81]==2 and m.opts[20108]
assert m.labels()[6]=='Update' and '/tmp/q2-shelf.json' not in m.files; passed()  # q2-v1.8 against the latest q2-v1.9
m.close(); m.now+=9*60*1000; apps_open(m); assert len(m.threads)==1 and m.labels()[6]=='Update'; passed()  # still fresh: no requests
m.row(2); assert m.labels()[6]=='Checking…' and len(m.threads)==2 and KILL in m.systems[-1]; m.row(2); assert m.labels()[6]=='Cancelling…'
shelf_job(m); assert m.toasts[-1][3]=='Download cancelled' and m.labels()[6]=='Update'; passed()
m.wifi=-1; m.row(2); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and len(m.threads)==2; m.wifi=3; passed()
m.codes=[403]; m.row(2); shelf_job(m); assert m.toasts[-1][3]=='GitHub is busy, try later'; passed()  # the API's rate limit
m.files[SPOT_TAG]=bytearray(b'q2-v1.9'); m.close(); m.wifi=-1; apps_open(m); assert m.labels()[6]=='Installed' and len(m.threads)==3
m.row(2); assert m.labels()[6]=='Press again to remove' and len(m.threads)==3
m.row(3); assert m.labels()[6]=='Installed' and m.labels()[8]=='Press again to erase the login and cache'  # one row armed at a time
m.row(2); m.row(2); assert m.labels()[6]=='Removing…' and m.labels()[8]=='Login and cache'; shelf_job(m)
assert m.toasts[-1][3]=='Spotify removed' and KILL in m.systems[-3] and m.systems[-2]=='sync' and KILL in m.systems[-1]  # spot_stop: before and after
assert not {SPOT_TAG,SPOT_FILES,'/mnt/mmc/.spotify/run','/mnt/mmc/.spotify/librespot'}&set(m.files) and m.labels()[6]=='Install'; passed()
m.row(3); assert m.labels()[8]=='Press again to erase the login and cache'; m.row(3)
assert m.labels()[6]=='Install' and m.labels()[8]=='Erasing…'; shelf_job(m)
assert 'rm -rf /mnt/mmc/.spotify/cache /mnt/mmc/.spotify/.cache-size' in m.systems and m.toasts[-1][3]=="Spotify's login and cache erased"; passed()
# A stale check, stopped by a press (it needs no answer); then Rockbox from its release to "ready".
m.close(); m.wifi=3; m.now+=11*60*1000; apps_open(m); assert len(m.threads)==6
m.row(1); assert m.toasts[-1][3]=='Stopped checking for updates'; shelf_job(m); assert len(m.threads)==6; passed()
m.close(); m.rockbox_mode=0; m.dirs.pop('/mnt/mmc/.spotify'); m.reply=(ROOT/'test/shelf/q2-rockbox.json').read_bytes()
apps_open(m); shelf_job(m); assert m.labels()==['App Store','Rockbox','Install','Spotify','Install','Deezer','Install','Soulseek','Install','File Browser','Install']  # not stopped: checked again
m.row(0); assert m.labels()[2]=='Starts at power-on; Boot to changes that. Press again to install' and len(m.threads)==7
m.row(0); assert len(m.threads)==8 and m.labels()[2]=='Checking…'; m.row(0); shelf_job(m); assert m.toasts[-1][3]=='Download cancelled'
m.free=10<<20; m.row(0); m.row(0); shelf_job(m)  # the zip (13018581 bytes) and 64 MB left over
assert m.toasts[-1][3]==f'Needs {(13018581+(54<<20)+(1<<20)-1)>>20} MB more space' and not [f for f in m.files if '.q2/staging/' in f]; passed()
m.free=1<<32; m.staged=[('.rockbox/rockbox',b'ELF3.2'),('.rockbox/codecs/flac.codec',b'c'),('.rockbox/fonts/a b.fnt',b'f')]
m.listing=''.join(f'{len(d):9}  10-06-2026 13:18   {f}\n' for f,d in m.staged)
m.row(0); m.row(0); m.rockbox_mode=0o100755; m.moves=[]; shelf_job(m)  # Rockbox on the card by the end
assert m.toasts[-1][3]==('Rockbox is ready. Find it on Home' if variant=='ipod' else 'Rockbox is ready. It starts at power-on')
assert [x for x in m.moves if x.startswith('/mnt/mmc/.rockbox/')][-1]=='/mnt/mmc/.rockbox/rockbox'  # the binary last
assert m.moves[-2:]==['/mnt/mmc/.q2/apps/rockbox.files','/mnt/mmc/.q2/apps/rockbox.tag'] and m.files['/mnt/mmc/.q2/apps/rockbox.tag']==b'v3.2'
assert m.files['/mnt/mmc/.rockbox/rockbox']==b'ELF3.2' and m.files['/mnt/mmc/.rockbox/fonts/a b.fnt']==b'f'
assert m.files['/mnt/mmc/.q2/apps/rockbox.files']==b'.rockbox/rockbox\n.rockbox/codecs/flac.codec\n.rockbox/fonts/a b.fnt\n'
assert not [f for f in m.files if '.q2/staging' in f] and 'sync' in m.systems; passed()
assert m.labels()==['App Store','Rockbox','Installed','Rockbox Themes','Install','Spotify','Install','Deezer','Install','Soulseek','Install','File Browser','Install']; passed()  # the rows built again
m.listing='        1  10-06-2026 13:18   .rockbox/../../x\n'; m.files.pop('/mnt/mmc/.q2/apps/rockbox.tag'); m.row(0); shelf_job(m)
assert m.toasts[-1][3]=='The download is not a usable app' and m.files['/mnt/mmc/.rockbox/rockbox']==b'ELF3.2'; passed()
m.close(); passed()
# No list on the card yet: one row, which loads it with Wi-Fi. The fetched list (its bad lines
# skipped) is kept on the card and its rows shown; a list with no app leaves the one in use.
m=ShelfMachine(); m.rockbox_mode=0; m.wifi=-1; apps_open(m)
assert m.labels()==['App Store','App list','Connect to Wi-Fi to load the list'] and not m.threads
m.row(0); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.threads; passed()
m.wifi=3; m.row(0); assert m.labels()[2]=='Loading…' and len(m.threads)==1; m.row(0); assert len(m.threads)==1  # loading already
m.reply=b'# apps\nme/q2-foo Foo\nme/q2-Bad\n'; shelf_job(m)
assert m.labels()==['App Store','Foo','Install'] and m.files[LIST]==b'me/q2-foo Foo\n'; passed()
m.close(); m.now+=11*60*1000; m.reply=b'nothing'; apps_open(m); shelf_job(m)
assert m.labels()==['App Store','Foo','Install'] and m.files[LIST]==b'me/q2-foo Foo\n'; m.close(); passed()

# Resume: once a second the UI loop polls the playing track; one of Q2POD RESUMEMIN minutes (30) or
# longer keeps its place in a ring written whole to /mnt/data (a .tmp, renamed), every RESUME_SAVE_S
# of play, after a pause and on a track change, and jumps back there when it next starts playing near
# its beginning.
# A place near either end is forgotten; a short or CUE track, or a paused start, is left alone.
class ResumeMachine(QueueMachine):
    def __init__(self,files=None):
        super().__init__()
        self.files={} if files is None else files; self.open={}; self.play=(0,0); self.seeks=[]; self.opens=self.renames=0
        self.handlers[sleep_hook+12]='stock_sleep'
        for n in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0',
                  'access@GLIBC_2.0','time@GLIBC_2.0','player_playtime_and_length','player_seek_time'): self.handlers[syms[n]]='r:'+n
        self.epoch=1700000000
        self.mock('tk_snprintf')
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')  # scrobble.c's log lock
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0; self.calls.append((name,a,b,c))
        if name=='fopen':
            path,mode=self.text(a),self.text(b)
            # the boot logo's lit-only mount poll; dark, any open counts
            self.opens+=path!='/proc/mounts' or not self.u.mem_read(syms['g_backlight_status'],1)[0]
            if mode=='rb' and path not in self.files: ret=0
            else: ret=0x2000000+len(self.calls); self.open[ret]=[path,mode,0,b'' if mode=='wb' else self.files.get(path,b'')]
        elif name=='fread':
            f=self.open[d]; data=f[3][f[2]:f[2]+b*c]; f[2]+=len(data); self.u.mem_write(a,data); ret=len(data)//b
        elif name=='fwrite': self.open[d][3]+=bytes(self.u.mem_read(a,b*c)); ret=c
        elif name=='fclose':
            path,mode,_,data=self.open.pop(a)
            if mode in ('wb','ab'): self.files[path]=data
        elif name=='access': ret=0 if self.text(a) in self.files else -1
        elif name=='time': ret=self.epoch
        elif name=='rename': self.renames+=1; self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='player_playtime_and_length':
            if self.play[1]: self.word(a,self.play[0]); self.word(b,self.play[1]); ret=1
            else: ret=-1
        elif name=='player_seek_time': self.seeks.append(signed(a)); self.play=(signed(a),self.play[1])
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
def playing(m,sec,total=3600,pos=None,n=1):  # n polls a second apart while it plays on from sec
    m.poll(sec,total,pos)
    for _ in range(n-1): m.poll(m.play[0]+1,total)
    return m
m=ResumeMachine().poll(0,100,pos=1,n=3)  # a short track: no file read or write
assert not m.opens; m=ResumeMachine()
playing(m,0,pos=0,n=70)
assert len(m.places())==1 and m.places()[0][1]==61 and m.renames==1 and not m.seeks; passed()
m.poll(80,n=3); assert m.places()[0][1]==80 and m.renames==2; passed()  # paused: saved once it stands still
m.poll(80,n=5); assert m.renames==2  # still paused: nothing more to write
playing(m,81,n=5); m.poll(0,pos=1,n=3)  # another track: A's last second is kept
assert m.places()[0][1]==85 and not m.seeks; passed()
m.poll(0,pos=0,n=3); assert not m.seeks  # back to A, paused at its start: waits for Play
playing(m,1,n=3); assert m.seeks==[85] and m.places()[0][1]==85; passed()
m=ResumeMachine(m.files); m.poll(0,pos=0,n=2); playing(m,2,n=1); assert m.seeks==[85]; passed()  # after a reboot
m=ResumeMachine(m.files); m.poll(0,pos=0,n=2); playing(m,O['RESUME_START_S'],n=1); assert not m.seeks; passed()  # already under way
m.poll(3590,n=3); m.poll(0,pos=1,n=2); assert not m.places(); passed()  # finished: forgotten
m=ResumeMachine(); cue=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; m.word(cue+O['REC_CUE_START'],1200)
playing(m,0,pos=0,n=70); assert not m.places() and not m.renames; passed()
m=ResumeMachine()
for i in range(O['RESUME_SLOTS']+2):  # the last is saved by no later change
    m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[0]+O['REC_PATH'],m.string(f'/p/long{i}')); m.poll(100+i,pos=0,n=3)
assert len(m.places())==O['RESUME_SLOTS']>=512 and m.places()[0][1]==100+O['RESUME_SLOTS'] and m.places()[-1][1]==101; passed()
old32=struct.pack('<II',fnv('/p/old'),700)+bytes(8*31)  # an earlier release's 32-slot file keeps its places
m=ResumeMachine({'/mnt/data/ringnav-resume':old32}); m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[0]+O['REC_PATH'],m.string('/p/old'))
m.poll(0,pos=0,n=2); playing(m,1,n=3); assert m.seeks==[700]; passed()
# Q2POD RESUMEMIN (crossfade.c resume_min): the minutes a music track needs, 30 unless 0 (Off) or a
# five in 20-120; Off leaves music alone while Podcasts/Audiobooks still resume.
for config,total,kept in (({},1799,0),({},1800,1),({'RESUMEMIN':'20'},1199,0),({'RESUMEMIN':'20'},1200,1),
                          ({'RESUMEMIN':'0'},7200,0),({'RESUMEMIN':'125'},1800,1),({'RESUMEMIN':'25'},1500,1),
                          ({'RESUMEMIN':'22'},1799,0),({'RESUMEMIN':'x'},1800,1)):
    m=ResumeMachine(); m.config.update(config); playing(m,0,total,pos=0,n=70)
    assert len(m.places())==kept and ('/mnt/data/config.ini','Q2POD','RESUMEMIN','30') in m.config_reads,(config,total)
m=ResumeMachine(); m.config['RESUMEMIN']='0'; r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
m.word(r+O['REC_PATH'],m.string('/mnt/mmc/podcasts/Show/01.mp3')); playing(m,0,200,pos=0,n=70)
assert m.places()==[(fnv('/mnt/mmc/podcasts/Show/01.mp3'),61)]; passed()

m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]  # Podcasts/Audiobooks: any length, never counted
m.word(r+O['REC_PATH'],m.string('/mnt/mmc/audiobooks/Book/01.mp3')); playing(m,0,200,pos=0,n=110)
assert m.places()==[(fnv('/mnt/mmc/audiobooks/Book/01.mp3'),61)] and '/mnt/data/ringnav-plays' not in m.files
assert '/mnt/mmc/.scrobbler.log' not in m.files; passed()

# A spoken CUE book: a chapter keeps its file's place (CUE start + seconds) and never jumps by itself;
# a chapter row's hold menu starts with Resume, which plays the list's songs from the chapter holding
# the place and seeks into it once that chapter starts. A chapter within RESUME_EDGE_S of the file's
# start keeps the book's place; the last chapter's end forgets it. A music CUE image gets neither.
book='/mnt/mmc/Book/book.m4b'
def chapter(m,r,start,end,path=book):
    m.word(r+O['REC_PATH'],m.string(path)); m.word(r+O['REC_CUE_START'],start); m.word(r+O['REC_CUE_END'],end)
def place(sec,path=book): return {'/mnt/data/ringnav-resume':struct.pack('<II',fnv(path),sec)+bytes(8*O['RESUME_SLOTS']-8)}
m=ResumeMachine(); chapter(m,m.items(m.get(syms['mcl_pdeqplaylist']))[0],600,1200)
playing(m,0,600,pos=0,n=70); assert m.places()==[(fnv(book),661)] and '/mnt/data/ringnav-plays' not in m.files
m=ResumeMachine(m.files); chapter(m,m.items(m.get(syms['mcl_pdeqplaylist']))[0],600,1200)
m.poll(0,600,pos=0,n=2); playing(m,1,600,n=3); assert not m.seeks; passed()  # picking a chapter plays it
for start,end,sec,kept in ((0,600,10,[(fnv(book),700)]),(1200,0,590,[])):  # the book's first or last 30 s
    m=ResumeMachine(place(700)); chapter(m,m.items(m.get(syms['mcl_pdeqplaylist']))[0],start,end)
    playing(m,sec-5,600,pos=0,n=6); m.poll(0,pos=1,n=2); assert m.places()==kept; passed()
m=ResumeMachine(place(700)); rows=m.items(m.get(syms['p_deque_showlist']))
for r,(s0,s1) in zip(rows,((0,600),(600,1200),(1200,0))): chapter(m,r,s0,s1)
m.run(0); assert m.labels()[0]=='Resume' and m.opened[-1][2:]==(1,0xf001,2) and len(m.opened[-1][1])==20
q=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,q,600,1200)  # the chapter at 600 starts
m.poll(0,600,pos=0,n=2); playing(m,1,600,n=2); assert m.seeks==[100]; passed()
m=ResumeMachine(place(700)); rows=m.items(m.get(syms['p_deque_showlist']))  # chapter 1 paused at 10 s,
for r,(s0,s1) in zip(rows,((0,600),(600,1200),(1200,0))): chapter(m,r,s0,s1)  # the queue switching late
q=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,q,0,600); playing(m,0,600,pos=0,n=11); m.poll(10,600,n=3)
assert m.places()==[(fnv(book),700)]; m.run(0); m.poll(10,600,n=2); assert not m.seeks
chapter(m,q,600,1200); m.poll(0,600,n=2); playing(m,1,600,n=2); assert m.seeks==[100]; passed()
m=ResumeMachine(place(700)); q=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,q,2400,3000)  # chapter 5
playing(m,0,600,pos=0,n=11); m.poll(10,600,n=3); assert m.places()==[(fnv(book),700)]  # paused at 10 s: kept
playing(m,11,600,n=30); m.poll(40,600,n=3); assert m.places()==[(fnv(book),2440)]; passed()  # past 30 s: moved
m=ResumeMachine(place(700)); rows=m.items(m.get(syms['p_deque_showlist']))  # the chapter never comes: no seek
for r,(s0,s1) in zip(rows,((0,600),(600,1200),(1200,0))): chapter(m,r,s0,s1)
q=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,q,0,600); playing(m,40,600,pos=0,n=3)
m.run(0); m.poll(43,600,n=9); chapter(m,q,600,1200); m.poll(0,600,n=2); playing(m,1,600,n=2); assert not m.seeks; passed()
m=ResumeMachine(place(700)); rows=m.items(m.get(syms['p_deque_showlist']))  # Resume onto the chapter playing
for r,(s0,s1) in zip(rows,((0,600),(600,1200),(1200,0))): chapter(m,r,s0,s1)
q=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,q,600,1200); playing(m,40,600,pos=0,n=3)
m.run(0); m.poll(43,600); assert not m.seeks  # still the old position
m.poll(0,600); playing(m,1,600,n=1); assert m.seeks==[100]; passed()
m=ResumeMachine(place(700,'/p/album.flac'))
for r,(s0,s1) in zip(m.items(m.get(syms['p_deque_showlist'])),((0,600),(600,0))): chapter(m,r,s0,s1,'/p/album.flac')
m.run(0); assert 'Resume' not in m.labels()
m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,r,600,1200,'/p/album.flac')
playing(m,0,600,pos=0,n=70); assert not m.places(); passed()

# Play counts: a track over LISTEN_MIN_S counts once half of it, or LISTEN_MAX_S, has been heard,
# seeks aside; a repeat counts again. Counts are written whole to /mnt/data, most recent first, the
# least recently played of the lowest counts replaced when full.
def counts(m): return [(k,n) for k,n in struct.iter_unpack('<II',m.files.get('/mnt/data/ringnav-plays',b'')) if k]
m=ResumeMachine(); playing(m,0,200,pos=0,n=101); assert not counts(m)
playing(m,100,200,n=50); assert counts(m)==[(fnv('/p/A'),1)]; passed()
m.poll(0,200); playing(m,1,200,n=101); assert counts(m)==[(fnv('/p/A'),2)]; passed()  # repeat-one
playing(m,0,200,pos=1,n=5); m.poll(150,200); playing(m,151,200,n=40); assert len(counts(m))==1; passed()  # seeked past half
playing(m,0,20,pos=2,n=25); assert len(counts(m))==1; passed()  # too short
playing(m,0,3600,pos=1,n=242); assert counts(m)[0]==(fnv('/p/B'),1); passed()  # 4 minutes of a long track
log=m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')  # the same listens, scrobbled
assert log[:3]==['#AUDIOSCROBBLER/1.1','#TZ/UTC','#CLIENT/Q2 Pod'] and log[-1]=='' and len(log)==7
assert log[3]==f'Artist\tAlbum\tA\t\t200\tL\t{1700000000-100}\t' and log[4]==log[3] and log[5].startswith('Artist\tAlbum\tB\t\t3600\tL\t'); passed()
m=ResumeMachine(); m.epoch=2**32-1000; playing(m,0,200,pos=0,n=101); playing(m,100,200,n=50)  # 2106: time() wrapped
assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[3].endswith(f'\tL\t{2**32-1000-100}\t'); passed()
m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
m.word(r+O['REC_ARTIST'],m.string('')); m.word(r+O['REC_TITLE'],m.string('Tab\tbed')); m.word(r+O['REC_TRACK'],7)
playing(m,0,200,pos=0,n=110); assert counts(m) and '/mnt/mmc/.scrobbler.log' not in m.files  # no artist: counted only
m.word(r+O['REC_ARTIST'],m.string('X')); m.poll(0,200); playing(m,1,200,n=110)  # the tag's title, not the file name
assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[3].startswith('X\tAlbum\tTab bed\t7\t200\t'); passed()
m.word(r+O['REC_TITLE'],m.string('')); m.word(r+O['REC_NAME'],m.string('Song.flac')); m.word(r+O['REC_PATH'],m.string('/p/Song.flac'))
m.poll(0,200); playing(m,1,200,n=110)  # untitled: the file name without its extension
assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[4].startswith('X\tAlbum\tSong\t7\t200\t'); passed()
# ALBUM_ARTIST=1 (issue #15): the parsed album artist, as a folder queue's record has none; else the record's
m.config['ALBUM_ARTIST']='1'; id3=syms['g_play_id3_info']; m.u.mem_write(id3,b'/p/Song.flac\0')
m.u.mem_write(id3+O['ID3_ARTIST'],b'X\0'); m.u.mem_write(id3+O['ID3_ALBUM']+O['MUSIC_INFO_ALBUM_ARTIST'],b'AA\0')
m.poll(0,200); playing(m,1,200,n=110); assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[5].startswith('AA\t'); passed()
m.u.mem_write(id3+O['ID3_ALBUM']+O['MUSIC_INFO_ALBUM_ARTIST'],b'\0'); m.word(r+O['REC_ALBUM_ARTIST'],m.string('RA'))
m.poll(0,200); playing(m,1,200,n=110); assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[6].startswith('RA\t'); passed()
del m.config['ALBUM_ARTIST']
m=ResumeMachine(); m.epoch=86400; playing(m,0,200,pos=0,n=110)  # clock never set: counted only
assert counts(m) and '/mnt/mmc/.scrobbler.log' not in m.files; passed()
m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]  # CUE tracks of one image: apart
playing(m,0,200,pos=0,n=110); m.word(r+O['REC_CUE_START'],300); m.poll(0,200); playing(m,1,200,n=110)
assert len(counts(m))==2 and counts(m)[1]==(fnv('/p/A'),1); passed()
m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]  # a skipped CUE track's time stays its own
playing(m,0,200,pos=0,n=26); m.word(r+O['REC_CUE_START'],300); playing(m,0,200,n=80); assert not counts(m); passed()
full=[(i+1,1 if i==5 else 2) for i in range(O['PLAYS_SLOTS'])]
m=ResumeMachine({'/mnt/data/ringnav-plays':b''.join(struct.pack('<II',*e) for e in full)})
playing(m,0,200,pos=0,n=110); assert counts(m)==[(fnv('/p/A'),1)]+full[:5]+full[6:]; passed()

# Both lock UIs use stock's persistent flag/mode, and the existing key gates enforce each choice.
LOCK_NAMES=['Off','Everything','Buttons only','Volume only']
m=QueueMachine(); view=m.node('scroll_view','scroll_view_keylock')
m.byte(syms['g_keylock_flag'],1); m.byte(syms['g_keylock_mode'],2)  # stock's buttons-only is kept
m.top=m.node('window','keylock_page',[view])
assert m.call(address=HOOKS['keylock_rows'][0],args=(m.top,0,0,0),gap=0)==0
rows=m.nodes[view]['children']; buttons=[m.nodes[r]['children'][0] for r in rows]
labels=[next(c for c in m.nodes[b]['children'] if m.nodes[c]['type']=='hscroll_label') for b in buttons]; icons=[m.nodes[b]['children'][0] for b in buttons]
assert [m.nodes[l]['text'] for l in labels]==LOCK_NAMES
assert [m.nodes[i]['image'] for i in icons]==['unselect','unselect','select','unselect']
assert not [c for c in m.calls if c[0]=='write_int_config']
for choice in (1,2,3,0):
    f,ctx=m.handler(buttons[choice],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    assert [m.nodes[i]['image'] for i in icons]==['select' if j==choice else 'unselect' for j in range(4)]
    assert [c[1:2]+(m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[
        (4-choice if choice else 3,'SYSSET','LOCKMODE'),(int(choice!=0),'SYSSET','LOCKFLAG')]
passed()
for old_mode in (0,4):
    m.byte(syms['g_keylock_flag'],1); m.byte(syms['g_keylock_mode'],old_mode)
    m.call(address=HOOKS['keylock_rows'][0],args=(m.top,0,0,0),gap=0)
    assert m.u.mem_read(syms['g_keylock_mode'],1)==b'\x03'
    assert [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(3,'SYSSET','LOCKMODE')]
m.byte(syms['g_keylock_flag'],0)
img=m.node('image','img_lock'); label=m.node('label','label_lock')
m.top=m.node('window','statusbar_dialog',[img,label])
m.handlers[int(manifest['patch_symbols']['stock_quicksettings_trampoline'],16)]='stock_quicksettings'
for choice in (1,2,3,0):
    assert m.call(address=HOOKS['quick_lock'][0],args=(m.top,m.event,0,0),gap=0)==0
    assert m.nodes[label]['text']==LOCK_NAMES[choice]
    assert m.nodes[img]['image']==('drop_keylockopen' if choice else 'drop_keylock')
    # Reopening quick settings keeps the saved choice and paints its label immediately.
    m.call(address=HOOKS['dialog_statusbar_dialog_init'][0],args=(m.top,0,0,0),gap=0)
    assert m.nodes[label]['text']==LOCK_NAMES[choice]
    for light in (0,1):
        for key in (O['KEY_PREV'],O['KEY_NEXT'],O['KEY_PLAY'],O['KEY_BACK_BTN'],O['KEY_FWD_BTN']):
            gate=Machine(); gate.byte(syms['g_backlight_status'],light)
            gate.byte(syms['g_keylock_flag'],choice!=0); gate.byte(syms['g_keylock_mode'],4-choice if choice else 3)
            wheel=key in (O['KEY_PREV'],O['KEY_NEXT'])
            blocked=not light and (choice==1 or choice==2 and not wheel or choice==3 and wheel)
            for address in (HOOKS['on_wm_keydown_before_fun'][0],HOOKS['on_wm_keyup_before_fun'][0]):
                assert (gate.call(key,address=address)==11)==blocked,(choice,light,key,address)
passed()

# Quick settings (#70): a tap passes unchanged; a stationary hold opens settings without a click.
for radio,page in (('wifi','systemset/wifi_page'),('bt','systemset/bluetooth_page')):
    def quick_page():
        m=QueueMachine(); m.mock('dialog_quit','widget_dispatch_event_to_target_recursive')
        m.handlers[int(manifest['patch_symbols']['stock_quicksettings_trampoline'],16)]='stock_quicksettings'
        icon=m.node('image','img_'+radio+'switch')
        win=m.node('dialog','statusbar_dialog',[icon,m.node('image','img_lock'),m.node('label','label_lock')])
        m.top=win
        m.call(address=HOOKS['dialog_statusbar_dialog_init'][0],args=(win,0,0,0),gap=0)
        def down():
            m.pressed=1
            m.word(m.event+O['EVENT_X'],30); m.word(m.event+O['EVENT_Y'],40)
            f,ctx=m.handler(icon,O['EVT_POINTER_DOWN'])
            m.call(key=30,address=f,args=(ctx,m.event,0,0),event_type=O['EVT_POINTER_DOWN'],gap=0)
        def dispatch(kind,x=30):
            m.word(m.event+O['EVENT_X'],x); m.word(m.event+O['EVENT_Y'],40)
            return m.call(key=x,address=HOOKS['widget_dispatch'][0],args=(icon,m.event,0,0),event_type=kind,gap=0)
        return m,icon,win,down,dispatch
    m,icon,win,down,dispatch=quick_page(); down()
    m.advance(O['RATE_HOLD_MS']-1); assert not m.opened
    dispatch(O['EVT_POINTER_UP_BEFORE']); assert not m.timers
    assert dispatch(O['EVT_CLICK'])==0 and any(c[0]=='stock_dispatch' for c in m.calls)
    m.advance(O['RATE_HOLD_MS']); assert not m.opened; passed()
    m,icon,win,down,dispatch=quick_page(); down(); m.advance(O['RATE_HOLD_MS'])
    assert m.opened==[(page,None)] and [c[1:3] for c in m.calls if c[0]=='dialog_quit']==[(win,0)]
    assert any(c[0]=='widget_dispatch_event_to_target_recursive' for c in m.calls)
    assert dispatch(O['EVT_CLICK'])==11 and not any(c[0]=='stock_dispatch' for c in m.calls)
    # A later touch can tap the same icon normally.
    m.top=win; down(); dispatch(O['EVT_POINTER_UP_BEFORE']); assert dispatch(O['EVT_CLICK'])==0; passed()
    for kind in (O['EVT_POINTER_MOVE_BEFORE'],O['EVT_POINTER_ABORT'],O['EVT_KEY_DOWN_BEFORE']):
        m,icon,win,down,dispatch=quick_page(); down(); dispatch(kind,x=50)
        m.advance(O['RATE_HOLD_MS']); assert not m.opened, (kind,m.opened)
    for kind in (O['EVT_DESTROY'],O['EVT_WINDOW_CLOSE'],O['EVT_WINDOW_BACKGROUND']):
        m,icon,win,down,dispatch=quick_page(); down(); f,ctx=m.handler(win,kind)
        m.call(address=f,args=(ctx,m.event,0,0),event_type=kind,gap=0)
        m.advance(O['RATE_HOLD_MS']); assert not m.opened
    m,icon,win,down,dispatch=quick_page(); down(); m.top=m.node('window','other')
    m.advance(O['RATE_HOLD_MS']); assert not m.opened
    m,icon,win,down,dispatch=quick_page(); m.timer_fail=True; down()
    assert dispatch(O['EVT_CLICK'])==0 and not m.opened; passed()

# Settings rows (docs/internals.md#charge-limit, #low-power, #album-artists): Power management gains
# Charge limit and Low power, Audio settings gains Artists, after the stock rows in the same widgets
# and styles; Centre or tap toggles and saves each. Artists is stock's own PLAYSET ARTISTTYPE.
def settings_page(hook,view_name,config={},stock_rows=2,bt=1,boot=False,cls=QueueMachine,sub=0):
    m=cls(); m.config.update(config); m.restack=True
    if hook=='display': m.word(payload_syms['display_sub'],sub)  # 3: Display's Controls page
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
m,rows,texts,icons,click=settings_page('power','scroll_view_powermanager')
def switch_image(m,row):
    button=m.nodes[row]['children'][0]
    images=[c for c in m.nodes[button]['children'] if m.nodes[c].get('name')=='_setting_switch']
    assert len(images)==1
    return m.nodes[images[0]]['image']
assert all(switch_image(m,rows[i])=='switch_off' for i in (0,1,3))
assert len(rows)==5 and icons==['usb_chargeswitch','system_powermanager','system_keylock','system_keylock','system_powermanager'] and all(m.nodes[r]['style']=='s_listitem_black' for r in rows)
assert texts()==['Charge Limit (80%)','Low Power','Wake: Single Press','Volume Guard','Standby Limit: Off']
assert click(0)==[(1,'Q2POD','CHARGELIMIT')] and texts()[0]==f"Charge Limit ({O['CHARGE_STOP']}%)"
assert click(1)==[(1,'Q2POD','LOWPOWER')] and texts()[1]=='Low Power' and switch_image(m,rows[1])=='switch_on'
assert click(0)==[(0,'Q2POD','CHARGELIMIT')] and texts()==['Charge Limit (80%)','Low Power','Wake: Single Press','Volume Guard','Standby Limit: Off']; passed()
# Standby limit (its alarm with the power poll's tests): each click steps to the next limit, then Off.
limits=[f"Standby Limit: {O[f'STANDBY_LIMIT_{i}']} h" for i in (1,2,3)]
for v,want in ((1,limits[0]),(2,limits[1]),(3,limits[2]),(0,'Standby Limit: Off')):
    assert click(4)==[(v,'Q2POD','STANDBYLIMIT')] and texts()[4]==want
passed()
# Volume guard (#46; its wheel engine in test/playback.py): off by default, the dark wheel as stock;
# the row saves VOLGUARD and sets wheel_guard, which the encoder thread reads.
guard=lambda: m.get(payload_syms['wheel_guard'])
assert guard()==0 and click(3)==[(1,'Q2POD','VOLGUARD')] and texts()[3]=='Volume Guard' and guard()==1
assert click(3)==[(0,'Q2POD','VOLGUARD')] and texts()[3]=='Volume Guard' and guard()==0; passed()
# Wake (docs/internals.md#wake): screen off, a lone centre press and its release never reach the UI;
# a second press within WAKE_MS passes, press and release. Other keys, the screen on, or the
# setting off pass untouched, except one more centre press within WAKE_MS of the waking one (#45).
inp=HOOKS['window_manager_dispatch_input_event'][0]; m.handlers[inp+12]='stock_input'
def wake(kind,key=O['KEY_CENTER'],gap=0):
    m.calls=[]; assert m.call(key,address=inp,args=(m.wm,m.event,0,0),event_type=kind,gap=gap,clear=False)==0
    return any(c[0]=='stock_input' for c in m.calls)
DOWN,UP=O['EVT_KEY_DOWN'],O['EVT_KEY_UP']
m.byte(syms['g_backlight_status'],0)
assert wake(DOWN,gap=1000) and wake(UP)  # setting off: as stock
RET,LONG=O['KEY_RETURN'],O['EVT_KEY_LONG']
def back_dark(): return not any(wake(t,RET,gap=1000) for t in (DOWN,LONG,UP))  # Back does nothing dark (#28)
assert back_dark()
m.byte(O['BOOT_KEY_GUARD'],1); assert wake(DOWN,gap=1000) and m.u.mem_read(O['BOOT_KEY_GUARD'],1)==b'\0' and wake(UP)  # Single press wakes after Standby
# md-gpio-keys repeats DOWN at ~258 ms, then every 50 ms. AWTK generates LONG downstream
# of ringnav_input from the first DOWN it receives, never through wake_gate itself.
class WakeMachine(Machine):
    def __init__(self,double=False):
        super().__init__(); self.config['WAKEDOUBLE']=str(int(double)); self.held_at=None
        self.handlers[inp+12]='stock_input'; self.handlers[sleep_hook+12]='stock_sleep'
        self.mock('awake_screen','getFormatString','navigator_to_with_context')
        self.byte(syms['g_backlight_status'],0)
        self.call(address=sleep_hook,args=(0,0,0,0),gap=0)
    def key(self,kind,gap=0):
        self.calls=[]
        self.call(O['KEY_CENTER'],address=inp,args=(self.wm,self.event,0,0),event_type=kind,gap=gap,clear=False)
        passed=any(c[0]=='stock_input' for c in self.calls)
        if passed and kind==DOWN and self.held_at is None: self.held_at=self.now
        if kind==UP: self.held_at=None
        return passed
    def long(self):
        # Stock's default long timer is 1000 ms after an accepted down. Its callback bypasses inp.
        assert self.held_at is not None and self.now-self.held_at>=1000
        self.calls=[]
        self.call(O['KEY_CENTER'],address=syms['on_wm_keylong_fun'],event_type=LONG,gap=0,clear=False)
        assert any(c[0]=='awake_screen' for c in self.calls) and any(c[0]=='navigator_to_with_context' for c in self.calls)
for double in (False,True):
    for duration in (258,458,708):
        w=WakeMachine(double)
        if double: assert not w.key(DOWN,1000) and not w.key(UP,50)
        assert w.key(DOWN,100)
        assert w.key(DOWN,258), 'a waking hold must keep its first repeat'
        for _ in range(258,duration,50): assert w.key(DOWN,50)
        assert w.key(UP,10), 'a waking hold must keep the release that lights the screen'
    # A fresh Standby guard identifies an accepted down whose release never arrived.
    w=WakeMachine(double)
    if double: assert not w.key(DOWN,1000) and not w.key(UP,50)
    assert w.key(DOWN,100) and w.key(DOWN,258)
    w.byte(O['BOOT_KEY_GUARD'],1)
    assert w.key(DOWN,1000)==(not double) and w.key(UP,50)==(not double)
    assert w.u.mem_read(O['BOOT_KEY_GUARD'],1)==b'\0'
    # Standby may also lose a blocked press's release; consume its new guard once, not on repeats.
    w=WakeMachine(double)
    if not double:
        assert w.key(DOWN,100) and w.key(UP,50)
        w.byte(syms['g_backlight_status'],1)
    assert not w.key(DOWN,100)
    w.byte(syms['g_backlight_status'],0); w.byte(O['BOOT_KEY_GUARD'],1)
    assert w.key(DOWN,1000)==(not double)
    assert w.u.mem_read(O['BOOT_KEY_GUARD'],1)==b'\0'
    for gap in (258,50,750): assert w.key(DOWN,gap)==(not double)
    assert w.key(UP,10)==(not double)
    if double:
        assert not w.key(DOWN,100) and not w.key(UP,50)
        assert w.key(DOWN,100) and w.key(UP,50)
    # A blocked press stays blocked beyond WAKE_MS: holding adds no new wake gesture.
    for blind_second in (False,True):
        if not double and not blind_second: continue
        w=WakeMachine(double)
        if blind_second:
            if double: assert not w.key(DOWN,1000) and not w.key(UP,50)
            assert w.key(DOWN,100) and w.key(UP,50)
            w.byte(syms['g_backlight_status'],1)
        assert not w.key(DOWN,100) and not w.key(DOWN,258)
        for _ in range(25): assert not w.key(DOWN,50)
        assert w.held_at is None and not w.key(UP,10)
    # Accepted Single presses and eligible Double presses retain stock long-press power-off.
    w=WakeMachine(double)
    if double: assert not w.key(DOWN,1000) and not w.key(UP,50)
    assert w.key(DOWN,100) and w.key(DOWN,258)
    for _ in range(15): assert w.key(DOWN,50)
    w.long()
    assert w.key(UP,10)
passed()
def eaten():  # blind presses and their repeats stay dropped with the release
    m.byte(syms['g_backlight_status'],1)
    r=not wake(DOWN,gap=100) and not wake(UP,gap=50) and not wake(DOWN,gap=50) and not wake(DOWN,gap=258)
    r=r and not wake(DOWN,gap=750) and not wake(UP)
    m.byte(syms['g_backlight_status'],0); return r
assert eaten() and wake(DOWN,gap=O['WAKE_MS']) and wake(UP)  # a blind double press only wakes
m.byte(syms['g_backlight_status'],1); assert not wake(DOWN,gap=100) and not wake(UP,gap=50); m.byte(syms['g_backlight_status'],0)
assert wake(DOWN,gap=1000) and wake(UP)  # a new press does not inherit the dropped press's flag
assert click(2)==[(1,'Q2POD','WAKEDOUBLE')] and texts()[2]=='Wake: Double Press' and back_dark()
assert not wake(DOWN,gap=1000) and not wake(UP,gap=50)  # a pocket bump
assert wake(DOWN,gap=100) and wake(UP,gap=50) and eaten()  # the double press; a third only wakes
assert not wake(DOWN,gap=O['WAKE_MS']) and not wake(UP) and not wake(DOWN,gap=O['WAKE_MS']) and not wake(UP)  # too slow
assert wake(DOWN,O['KEY_PLAY'],gap=1000) and wake(UP,O['KEY_PLAY']) and wake(UP,O['KEY_NEXT'])  # media and wheel work dark
m.byte(syms['g_backlight_status'],1); assert wake(DOWN,gap=1000) and wake(UP) and all(wake(t,RET,gap=1000) for t in (DOWN,LONG,UP)); passed()
# Standby's resume (STANDBY_WAKE, device_standby's awake_screen) stays dark in both modes, so the
# resuming key acts as dark (#27); a wake press clears stock's resume guard, which would drop its release.
from build import STANDBY_WAKE
assert struct.unpack_from('<I',demo,fileoff(demo,STANDBY_WAKE[0]))[0]==STANDBY_WAKE[2]
m.byte(syms['g_backlight_status'],0)
m.byte(O['BOOT_KEY_GUARD'],1); assert not wake(DOWN,gap=1000) and not wake(UP,gap=50) and m.u.mem_read(O['BOOT_KEY_GUARD'],1)==b'\0'
assert wake(DOWN,gap=100) and m.u.mem_read(O['BOOT_KEY_GUARD'],1)==b'\0' and wake(UP,gap=50)
assert click(2)==[(0,'Q2POD','WAKEDOUBLE')]; passed()
# SyncLink (#113): Eddict Player's keys, which stock posts with main_loop_post_key_event, reach
# stock's lock filters dark with the Buttons lock off; the same keys from the buttons stay locked.
post=HOOKS['main_loop_post_key_event'][0]
class LockMachine(Machine):
    def hook(self,u,address,size,_):
        if self.handlers.get(address)=='stock_input': self.locks.append(self.u.mem_read(syms['g_keylock_flag'],1)[0])
        return super().hook(u,address,size,_)
l=LockMachine(); l.handlers[inp+12]='stock_input'; l.handlers[post+12]='stock_post_key'
l.byte(syms['g_backlight_status'],0); l.byte(syms['g_keylock_flag'],1)
def lock_seen(key,kind,posted=None,gap=0):
    if posted: l.call(address=post,args=(0x1000400,int(kind==DOWN),posted,0),gap=1000)
    l.advance(gap,clear=False); l.word(l.event+O['EVENT_TIME'],l.now); l.locks=[]
    l.call(key,address=inp,args=(l.wm,l.event,0,0),event_type=kind,gap=0)
    return l.locks
for key in (O['KEY_PLAY'],O['KEY_BACK_BTN'],O['KEY_FWD_BTN']):
    assert lock_seen(key,DOWN,key)==[0] and lock_seen(key,UP,key)==[0]
    assert lock_seen(key,DOWN,gap=1000)==[1] and lock_seen(key,UP,gap=1000)==[1]
assert lock_seen(O['KEY_FWD_BTN'],DOWN,O['KEY_PLAY'])==[1] and lock_seen(O['KEY_PLAY'],DOWN)==[0]  # by key
assert lock_seen(O['KEY_PLAY'],UP,O['KEY_PLAY'],gap=1)==[1]  # and by its stamp
assert l.u.mem_read(syms['g_keylock_flag'],1)==b'\1'; passed()
# Play/Pause and Return hold after KEY_HOLD_MS (HOLD_KEYS), every other key at stock's time.
from build import HOLD_KEYS
assert all(struct.unpack_from('<I',demo,fileoff(demo,a))[0]==(new or jal(payload_syms[fn])) for a,_,new,fn in HOLD_KEYS)
for key,ms in ((O['KEY_PLAY'],700),(O['KEY_RETURN'],700),(O['KEY_CENTER'],1000)):
    l.timers={}; l.call(address=payload_syms['ringnav_hold_timer'],args=(0x1234,l.wm,1000,key),gap=0)
    assert [t[3] for t in l.timers.values()]==[ms]
for key in (O['KEY_PLAY'],O['KEY_RETURN']):
    info=l.call(address=payload_syms['ringnav_hold_info'],args=(l.wm,key),gap=0)
    assert struct.unpack('<2i',l.u.mem_read(info,8))==(key,700)
passed()
# Lighting the screen stalls the UI loop about WAKE_MS (stock's on_wm_keyup_fun, the paint), so a
# blind double press's last press is dispatched only after the first lit loop pass: still dropped.
for double in ('0','1'):
    w=Machine(); w.config['WAKEDOUBLE']=double; w.handlers[inp+12]='stock_input'; w.handlers[sleep_hook+12]='stock_sleep'
    def press(kind,gap):
        w.calls=[]; assert w.call(O['KEY_CENTER'],address=inp,args=(w.wm,w.event,0,0),event_type=kind,gap=gap,clear=False)==0
        return any(c[0]=='stock_input' for c in w.calls)
    def loop_pass(): w.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False)
    w.byte(syms['g_backlight_status'],0); loop_pass()
    assert double=='0' or not press(DOWN,1000) and not press(UP,50)
    assert press(DOWN,100) and press(UP,50)  # it wakes the screen, the loop stalls, then its first lit pass
    w.byte(syms['g_backlight_status'],1); w.now+=O['WAKE_MS']; loop_pass()
    assert not press(DOWN,0) and not press(UP,50) and press(DOWN,O['WAKE_MS']) and press(UP,50),double
passed()
m,rows,texts,*_=settings_page('power','scroll_view_powermanager',{'CHARGELIMIT':'1','LOWPOWER':'1','WAKEDOUBLE':'1','VOLGUARD':'1','STANDBYLIMIT':'2'})
assert texts()==[f"Charge Limit ({O['CHARGE_STOP']}%)",'Low Power','Wake: Double Press','Volume Guard',limits[1]]
assert m.get(payload_syms['wheel_guard'])==1; passed()
m,rows,texts,*_=settings_page('power','scroll_view_powermanager',{'STANDBYLIMIT':'4'})
assert texts()[4]=='Standby Limit: Off'; passed()  # out of range: Off
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=15)
assert len(rows)==9 and icons==['playset_covermode','playset_folderjump','playset_covermode','playset_gapless','playset_folderjump','playset_memplay','playset_balance','playset_balance','playset_dsdoutput']
assert texts()==['Year Order: Newest First','Artists: Artist','Artist Album Order: Year','Crossfade: Off','Folder Root: Card','Resume Songs: 30 min','Mono Audio','Swap L/R','MQA']
assert click(2)==[(1,'Q2POD','ARTISTAZ')] and texts()[2]=='Artist Album Order: A–Z'
assert click(2)==[(0,'Q2POD','ARTISTAZ')] and texts()[2]=='Artist Album Order: Year'
assert click(0)==[(1,'Q2POD','YEAROLD')] and texts()[0]=='Year Order: Oldest First'
assert click(0)==[(0,'Q2POD','YEAROLD')] and texts()[0]=='Year Order: Newest First'
assert click(1)==[(1,'PLAYSET','ARTISTTYPE')] and m.get(syms['artist_type'])==1 and texts()[1]=='Artists: Album Artist'
assert click(1)==[(0,'PLAYSET','ARTISTTYPE')] and m.get(syms['artist_type'])==0; passed()
# Artists is Album Artist until a choice is saved (read_artistype_value); a saved Artist stays.
for config,want in (({},1),({'ARTISTTYPE':'0'},0),({'ARTISTTYPE':'1'},1)):
    a=Machine(); a.config.update(config); a.word(syms['artist_type'],7)
    a.call(address=HOOKS['read_artistype_value'][0],args=(0,0,0,0),gap=0)
    assert a.get(syms['artist_type'])==want and a.config_reads[-1][1:3]==('PLAYSET','ARTISTTYPE'),config
passed()
# A FLAC's "ALBUM ARTIST" comment (foobar2000's spelling) when stock's libtag found no ALBUMARTIST (ringnav_music_info).
class TagMachine(BooksMachine):
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
t=TagMachine({'/mnt/mmc/a.flac':flac(b'ARTIST=Chikoi The Maid',b'Album Artist=Chikoi The Maid',picture=70000),
              '/mnt/mmc/b.flac':flac(b'ARTIST=Evan Call'),'/mnt/mmc/c.FLAC':flac(b'ALBUM ARTIST='+b'x'*300),
              '/mnt/mmc/d.mp3':flac(b'ALBUM ARTIST=No')})
t.files['/mnt/mmc/e.flac']=flac(b'METADATA_BLOCK_PICTURE='+b'A'*100000,b'ALBUM ARTIST=Past the cover')  # a block over 64 KB
assert t.album_artist('/mnt/mmc/a.flac')=='Chikoi The Maid'
assert t.album_artist('/mnt/mmc/a.flac',b'Stock')=='Stock'  # stock's ALBUMARTIST wins
assert t.album_artist('/mnt/mmc/b.flac')=='' and t.album_artist('/mnt/mmc/c.FLAC')=='x'*255
assert t.album_artist('/mnt/mmc/d.mp3')=='' and t.album_artist('/mnt/mmc/none.flac')==''
assert t.album_artist('/mnt/mmc/e.flac')=='Past the cover'
assert not t.handles  # every file closed
passed()
# Boot reads it too (ringnav_bootmode), so Audio settings shows it before any Library page has opened.
for config,text in (({},'Artists: Album Artist'),({'ARTISTTYPE':'0'},'Artists: Artist')):
    shown=settings_page('audioset','scroll_view_playset',config,stock_rows=15,boot=True)[2]
    assert shown()[1]==text,config
passed()
# Mono Audio (crossfade.c mono_click): saved as Q2POD MONO, the row's text following, and the int
# hciplayer and q2video map (MONO_FILE) set live. No file until it is first turned on.
class MonoMachine(QueueMachine):
    def __init__(self):
        super().__init__(); self.flag=self.alloc(4); self.word(syms['g_equalizer_flag'],1)  # the filter is in
        for n in ('open','ftruncate','rename','mmap','close'): self.handlers[syms[n+'@GLIBC_2.0']]='mono:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('mono:'): return super().hook(u,address,size,unused)
        self.calls.append((name,)); self.returns(self.flag if name=='mono:mmap' else 3 if name=='mono:open' else 0)
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=15,cls=MonoMachine)
assert not [c for c in m.calls if c[0]=='mono:open']
assert click(6)==[(1,'Q2POD','MONO')] and texts()[6]=='Mono Audio' and m.get(m.flag)==1
assert click(6)==[(0,'Q2POD','MONO')] and texts()[6]=='Mono Audio' and m.get(m.flag)==0 and switch_image(m,rows[6])=='switch_off'
assert [c[0] for c in m.calls if c[0]=='mono:open']==[]; passed()  # mapped once
# A failed map must never publish an inode that consumers could retain forever.
class FailedMapMachine(MonoMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='mono:mmap':
            self.calls.append(('mono:mmap',)); self.returns(0xffffffff); return
        return super().hook(u,address,size,unused)
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=15,cls=FailedMapMachine)
assert click(6)==[(1,'Q2POD','MONO')]
assert 'mono:mmap' in [c[0] for c in m.calls] and 'mono:rename' not in [c[0] for c in m.calls]
passed()
# Mono Audio with the EQ off and the player stopped (mono_sync): the filter is not attached until something
# plays (peq_chain), then is attached; once the filter is in (peq_chained) a later sync does not attach it again.
class MonoStoppedMachine(MonoMachine):
    def __init__(self):
        super().__init__(); self.word(syms['g_equalizer_flag'],0); self.handlers.pop(syms['mclGetPlayStatus'],None)
        self.word(PLAY_STATUS,1); self.handlers[payload_syms['peq_attach']]='mono:peq_attach'
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=15,cls=MonoStoppedMachine)
assert click(6)==[(1,'Q2POD','MONO')] and not [c for c in m.calls if c[0]=='mono:peq_attach']
m.word(PLAY_STATUS,2)
m.call(address=payload_syms['mono_sync'],args=(0,0,0,0),gap=0)
assert [c[0] for c in m.calls].count('mono:peq_attach')==1
m.word(payload_syms['peq_chained'],1)  # what the attach's peq_stock_eq leaves, as any EQ switch this boot
m.call(address=payload_syms['mono_sync'],args=(0,0,0,0),gap=0)
assert 'mono:peq_attach' not in [c[0] for c in m.calls]; passed()
# Playback Setting restacks by use (navigation.c playset_order): stock's rows by id, then ours.
def stacked(m,view_name): return [(m.entries+rows).index(c) for c in m.nodes[named(m,m.top,view_name)]['children'] if c in m.entries+rows]
assert stacked(m,'scroll_view_playset')==[3,2,9,18,10,8,20,0,4,5,11,21,22,16,17,15,19,14,13,12,1,6,23,7]; passed()
assert all(r in m.entries+rows for r in m.nodes[m.settings_view]['children'])  # no headings
for i in (3,5):
    button=m.nodes[rows[i]]['children'][0]
    assert m.nodes[m.nodes[button]['children'][-1]]['image']=='list_into'
passed()
# Swap L/R uses its own persistent flag, immediately below Mono Audio.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=15,cls=MonoMachine)
assert texts()[7]=='Swap L/R'
assert click(7)==[(1,'Q2POD','SWAPLR')] and texts()[7]=='Swap L/R' and m.get(m.flag)==1
assert click(7)==[(0,'Q2POD','SWAPLR')] and texts()[7]=='Swap L/R' and m.get(m.flag)==0
m,rows,texts,*_=settings_page('audioset','scroll_view_playset',{'SWAPLR':'1'},stock_rows=15)
assert texts()[7]=='Swap L/R'; passed()
# MQA (crossfade.c mqa_click): off by default, saved as Q2POD MQA, and sent to a connected hciplayer
# at once: 2 skips its start-of-track probe, 0 is its default. No player socket, nothing sent.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=15)
assert texts()[8]=='MQA' and icons[8]=='playset_dsdoutput'
m.word(O['MCL_FD'],9)
assert click(8)==[(1,'Q2POD','MQA')] and texts()[8]=='MQA' and m.sent[-1]==(9,b'{mcl-mqadecodemode\\0}',21,0)
assert click(8)==[(0,'Q2POD','MQA')] and texts()[8]=='MQA' and m.sent[-1]==(9,b'{mcl-mqadecodemode\\2}',21,0)
m.word(O['MCL_FD'],-1); sent=len(m.sent)
assert click(8)==[(1,'Q2POD','MQA')] and len(m.sent)==sent
m,rows,texts,*_=settings_page('audioset','scroll_view_playset',{'MQA':'1'},stock_rows=15)
assert texts()[8]=='MQA'; passed()
# Folder Root (#49): the row cycles the card's top-level folders, case aside, then the card, saving Q2POD
# FOLDERROOT each time. Not offered: files, dot-folders, names with an edge space or over 200 bytes (a
# config.ini line can't keep them). A saved folder gone moves on to the next name after it.
card=[('Zeta',4),('.Trash',4),('Música ñ',4),('alpha',4),('notes.txt',8),(' Edge',4),('B'*200,4),('C'*201,4),('Unknown',0)]
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',{'FOLDERROOT':'Deleted'},stock_rows=15,cls=lambda:RootMachine(card=card))
assert texts()[4]=='Folder Root: Deleted'
saved=[]
for _ in range(6):
    assert click(4)==[] and [(m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='toolsWriteConfig']==[('Q2POD','FOLDERROOT')]
    saved.append((m.config['FOLDERROOT'],texts()[4]))
assert [v for v,_ in saved]==['Música ñ','Unknown','Zeta','','alpha','B'*200]
assert [t for _,t in saved]==['Folder Root: '+(v or 'Card') for v,_ in saved]; passed()
# Crossfade (crossfade.c): the row shows the saved length while on; an unreadable one is the default.
for config,text in (({'XFADE':'1','XFADESEC':'7'},'Crossfade: 7 s'),({'XFADE':'1','XFADESEC':'15'},'Crossfade: 15 s'),({'XFADE':'1','XFADESEC':'16'},'Crossfade: 5 s'),({'XFADESEC':'7'},'Crossfade: Off')):
    m,rows,texts,*_=settings_page('audioset','scroll_view_playset',config,stock_rows=15)
    assert texts()[3]==text and m.nodes[m.nodes[m.nodes[rows[3]]['children'][0]]['children'][1]].get('name')=='label_xfade'
passed()
# Resume Songs (crossfade.c resume_min): the row shows Q2POD RESUMEMIN, 30 min unless 0 or a five in 20-120.
for config,text in (({},'30 min'),({'RESUMEMIN':'0'},'Off'),({'RESUMEMIN':'120'},'120 min'),({'RESUMEMIN':'125'},'30 min'),
                    ({'RESUMEMIN':'22'},'30 min'),({'RESUMEMIN':'15'},'30 min'),({'RESUMEMIN':'x'},'30 min')):
    m,rows,texts,*_=settings_page('audioset','scroll_view_playset',config,stock_rows=15)
    assert texts()[5]=='Resume Songs: '+text and m.nodes[m.nodes[m.nodes[rows[5]]['children'][0]]['children'][1]].get('name')=='label_resumemin',config
passed()
# Wheel sensitivity (playback.c wheel_pct, test/playback.py's travel): Display → Controls' last row in both
# builds, Q2POD WHEELSENSITIVITY as 1.0.6 saved it, 50% unless a canonical ten in 50-200.
for config,pct in (({},50),({'WHEELSENSITIVITY':'130'},130),({'WHEELSENSITIVITY':'200'},200),({'WHEELSENSITIVITY':'40'},50),
                   ({'WHEELSENSITIVITY':'135'},50),({'WHEELSENSITIVITY':'210'},50),({'WHEELSENSITIVITY':'x'},50)):
    m,rows,texts,icons,click=settings_page('display','scroll_view_display',config,stock_rows=3,sub=3)
    label=m.nodes[m.nodes[rows[-1]]['children'][0]]['children'][1]
    assert texts()[-1]==f'Wheel Sensitivity: {pct}%' and icons[-1]=='system_display' and m.nodes[label]['name']=='label_wheel'
    assert m.nodes[named(m,m.top,'scroll_view_display')]['children'][-1]==rows[-1],config  # the list's end
passed()
# Hold Return (Q2POD HOLDRETURN; compact_now_playing): Controls' row before Wheel QS and Wheel
# sensitivity in both builds, Recent unless set; Centre or tap toggles and saves it.
for config,text in (({},'Recent'),({'HOLDRETURN':'1'},'Home')):
    m,rows,texts,icons,click=settings_page('display','scroll_view_display',config,stock_rows=3,sub=3)
    assert texts()[-3]=='Hold Return: '+text and icons[-3]=='playset_folderjump'
    assert m.nodes[named(m,m.top,'scroll_view_display')]['children'][-3]==rows[-3],config
assert click(-3)==[(0,'Q2POD','HOLDRETURN')] and texts()[-3]=='Hold Return: Recent'
assert click(-3)==[(1,'Q2POD','HOLDRETURN')] and texts()[-3]=='Hold Return: Home'
passed()
# Wheel QS (Q2POD WHEELQS, playback.c's swipe): off unless set, the row between
# Hold Return and Wheel sensitivity; Centre or tap toggles it and saves it.
for config,on in (({},0),({'WHEELQS':'0'},0),({'WHEELQS':'1'},1)):
    m,rows,texts,icons,click=settings_page('display','scroll_view_display',config,stock_rows=3,sub=3)
    assert texts()[-2]=='Wheel QS' and icons[-2]=='system_display',config
    assert m.nodes[named(m,m.top,'scroll_view_display')]['children'][-2]==rows[-2],config
    assert click(-2)==[(1-on,'Q2POD','WHEELQS')], config
passed()
# Its row opens stock's Boot volume page re-dressed (crossfade.c): titled, no switch, the percent
# beside the slider, which runs 50-200 in tens. The wheel and +/- step 10%, live and saved, the
# slider snaps to a ten, and Centre goes back (edit_key, as on Brightness).
m,rows,texts,icons,click=settings_page('display','scroll_view_display',stock_rows=3,sub=3)
display_page=m.top; m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(-1)==[] and m.top!=display_page and m.nodes[m.top]['name']=='bootvol_page'
assert m.nodes[m.title]['text']=='Wheel Sensitivity' and m.nodes[m.switch]['visible']==0 and m.nodes[m.vol]['text']=='50%'
assert m.nodes[named(m,m.top,'view_bootvol')]['visible']==1
def press(w,kind=O['EVT_CLICK']):
    m.calls=[]; f,ctx=m.handler(w,kind); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
    return [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
def turn(key=O['KEY_NEXT']):
    assert m.call(key)==11; return [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
W='Q2POD','WHEELSENSITIVITY'
assert turn()==[(60,*W)] and m.nodes[m.vol]['text']=='60%' and texts()[-1]=='Wheel Sensitivity: 60%'
assert turn(O['KEY_PREV'])==[(50,*W)] and m.nodes[m.vol]['text']=='50%' and turn(O['KEY_PREV'])==[]  # 50% is the bottom
assert press(m.add)==[(60,*W)] and m.nodes[m.vol]['text']=='60%' and texts()[-1]=='Wheel Sensitivity: 60%'
assert press(m.dec)==[(50,*W)] and press(m.dec)==[] and texts()[-1]=='Wheel Sensitivity: 50%'
m.nodes[m.slider]['value']=127; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(130,*W)] and m.nodes[m.vol]['text']=='130%'
m.nodes[m.slider]['value']=300; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[(200,*W)] and press(m.add)==[] and turn()==[]
assert texts()[-1]=='Wheel Sensitivity: 200%' and m.nodes[m.switch]['visible']==0
assert m.confirm()==11 and [c[0] for c in m.calls].count('navigator_back')==1; passed()
# Resume Songs' row opens the same page: no switch, Off below 20, then 20-120 min in fives, live and
# saved; Centre goes back.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=15)
m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(5)==[] and m.nodes[m.top]['name']=='bootvol_page' and m.nodes[m.title]['text']=='Resume Songs'
assert m.nodes[m.switch]['visible']==0 and m.nodes[m.vol]['text']=='30 min'
R='Q2POD','RESUMEMIN'
assert turn()==[(35,*R)] and m.nodes[m.vol]['text']=='35 min' and texts()[5]=='Resume Songs: 35 min'
assert press(m.dec)==[(30,*R)] and turn(O['KEY_PREV'])==[(25,*R)] and turn(O['KEY_PREV'])==[(20,*R)]
assert turn(O['KEY_PREV'])==[(0,*R)] and m.nodes[m.vol]['text']=='Off' and texts()[5]=='Resume Songs: Off'
assert turn(O['KEY_PREV'])==[] and press(m.add)==[(20,*R)]  # Off is the bottom; up from it is 20
m.nodes[m.slider]['value']=63; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(65,*R)] and m.nodes[m.vol]['text']=='65 min'
m.nodes[m.slider]['value']=300; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[(120,*R)] and turn()==[]
m.nodes[m.slider]['value']=15; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(0,*R)] and texts()[5]=='Resume Songs: Off'
assert m.confirm()==11 and [c[0] for c in m.calls].count('navigator_back')==1; passed()
# Bluetooth's list (btmenu_rows, rebuilt on every search) gains Resume on connect and Control Device Volume
# with Bluetooth on, right after its fourth row, Bluetooth Headset Volume Control, as stock's rows: no icon,
# 78 high. Both off unless Q2POD RESUME / BTVOLLINK is 1 (docs/internals.md#resume-on-connect,
# #headset-controls-and-volume); off, the Q2's volume is its own gain and the device keeps its own volume.
m,rows,texts,icons,click=settings_page('btrows','scroll_view_bluetooth',stock_rows=7)  # 5 rows, 2 devices
assert len(rows)==2 and icons==[None,None] and texts()==['Resume on Connect','Control Device Volume']
for k,row in enumerate(rows):
    kids=m.nodes[m.nodes[row]['children'][0]]['children']  # the label and its switch
    assert len(kids)==2 and m.get(kids[0]+O['W_X'])==10 and m.get(row+O['W_H'])==78 and switch_image(m,row)=='switch_off'
    assert m.nodes[named(m,m.top,'scroll_view_bluetooth')]['children'].index(row)==4+k
assert click(0)==[(1,'Q2POD','RESUME')] and switch_image(m,rows[0])=='switch_on'
assert click(0)==[(0,'Q2POD','RESUME')] and switch_image(m,rows[0])=='switch_off'
assert click(1)==[(1,'Q2POD','BTVOLLINK')] and switch_image(m,rows[1])=='switch_on'
assert click(1)==[(0,'Q2POD','BTVOLLINK')] and switch_image(m,rows[1])=='switch_off'
assert texts()==['Resume on Connect','Control Device Volume']; passed()
m,rows,texts,*_=settings_page('btrows','scroll_view_bluetooth',{'RESUME':'1','BTVOLLINK':'1'},stock_rows=7)
assert [switch_image(m,r) for r in rows]==['switch_on','switch_on']; passed()
m,rows,*_=settings_page('btrows','scroll_view_bluetooth',stock_rows=1,bt=0)  # just the switch
assert rows==[]; passed()

# Lock screen (docs/internals.md#lock-screen). The picture list gains the six built-ins between
# stock's default and the card images, once however often it is rebuilt, and a picked built-in
# survives stock's reset of a picture that is no file.
WALLS=['q2pod:'+n for n in ('aurora','sunrise','graphite','tide','ember','black')]
class LockMachine(QueueMachine):
    reset=False
    def hook(self,u,address,size,unused):
        if address==syms['strcpy@GLIBC_2.0']:
            a,b=[u.reg_read(r) for r in REGS[:2]]
            self.u.mem_write(a,self.raw(b)+b'\0'); self.returns(a); return
        if self.handlers.get(address)=='stock_saverlist' and self.reset:
            self.u.mem_write(syms['g_screensaver_picture'],b'screensaver_default\0')
        return super().hook(u,address,size,unused)
def lock_machine(picture='screensaver_default',config={}):
    m=LockMachine(); m.config.update(config); m.restack=True
    for h in ('saverlist','saverrows','saver','savercover'): m.handlers[int(manifest['patch_symbols'][f'stock_{h}_trampoline'],16)]='stock_'+h
    m.u.mem_write(syms['g_screensaver_picture'],picture.encode()+b'\0')
    return m
# Startup and list validation call the same narrow access wrapper, without repair writes.
for picture in WALLS+['q2pod:invalid','/mnt/mmc/missing.jpg','/mnt/mmc/exists.jpg']:
    m=lock_machine(picture); m.present.add('/mnt/mmc/exists.jpg')
    result=m.call(address=payload_syms['lockscreen_access'],args=(m.string(picture),4,0,0),gap=0)
    assert (result==0)==(picture in WALLS or picture.endswith('/exists.jpg'))
    assert not any(c[0]=='toolsWriteConfig' for c in m.calls)
passed()
# Execute config_init's real validation/copy block, including the patched access call.
for picture in WALLS+['screensaver_default','/mnt/mmc/missing.jpg','/mnt/mmc/exists.jpg']:
    m=lock_machine(); m.present.add('/mnt/mmc/exists.jpg')
    path=m.string(picture); stack=0x7000f000
    for _ in range(2):
        m.calls=[]; m.u.reg_write(UC_MIPS_REG_SP,stack); m.word(stack+24,0xa26cc0)
        m.u.reg_write(UC_MIPS_REG_GP,0xa26cc0); m.u.reg_write(UC_MIPS_REG_S0,path)
        m.u.emu_start(0x4fa410,0x4fa460,count=100000)
        assert m.u.reg_read(UC_MIPS_REG_PC)==0x4fa460
        expected='screensaver_default' if picture.endswith('/missing.jpg') else picture
        assert m.text(syms['g_screensaver_picture'])==expected
        assert not any(c[0]=='toolsWriteConfig' for c in m.calls)
passed()
cards=['/mnt/mmc/lockscreenimage/a.jpg','/mnt/mmc/lockscreenimage/b.png']
m=lock_machine('q2pod:tide'); m.reset=True
m.word(syms['p_deque_screensaverlist'],m.deque([m.string(n) for n in ['screensaver_default',*cards]],'char*'))
listed=lambda:[m.text(e) for e in m.items(m.get(syms['p_deque_screensaverlist']))]
for _ in range(2):
    m.calls=[]; assert m.call(address=HOOKS['get_screensaver_image'][0],args=(0,0,0,0),gap=0)==0
    assert listed()==['screensaver_default',*WALLS,*cards] and m.text(syms['g_screensaver_picture'])=='q2pod:tide'
    assert not [c for c in m.calls if c[0]=='toolsWriteConfig']
m.u.mem_write(syms['g_screensaver_picture'],cards[0].encode()+b'\0'); m.reset=False; m.calls=[]
assert m.call(address=HOOKS['get_screensaver_image'][0],args=(0,0,0,0),gap=0)==0
assert listed()==['screensaver_default',*WALLS,*cards] and not [c for c in m.calls if c[0]=='toolsWriteConfig']; passed()
# The Lock screen page's list: Clock right after Time show, iconless and 78 high as stock's rows,
# Standard unless Q2POD LOCKCLOCK is 1; the built-ins' thumbnails lose their (file://) image.
def lock_page(config={}):
    m=lock_machine(config=config)
    m.word(syms['p_deque_screensaverlist'],m.deque([m.string(n) for n in ['screensaver_default',*WALLS,cards[0]]],'char*'))
    stock=[m.entry(0) for _ in range(2)]
    thumbs=[m.node('image',f'image_{i}',image='file://x') for i in range(8)]
    grid=[m.node('list_item','',thumbs[i:i+3]) for i in range(0,8,3)]
    view=m.node('scroll_view','scroll_view',stock+grid)
    for e in m.nodes[view]['children']: m.word(e+O['W_PARENT'],view)
    m.top=m.node('window','screensaver_page',[m.node('list_view','list_view',[view])]); m.nodes[m.wm]={'children':[m.top]}
    assert m.call(address=HOOKS['screensaver_rows'][0],args=(m.top,0,0,0),gap=0)==0 and m.calls[0][:2]==('stock_saverrows',m.top)
    row=m.nodes[view]['children'][1]; button=m.nodes[row]['children'][0]; label=m.nodes[button]['children'][-1]
    return m,row,button,label,thumbs
m,row,button,label,thumbs=lock_page()
assert len(m.nodes[named(m,m.top,'scroll_view')]['children'])==6 and m.get(row+O['W_H'])==78  # 2 stock rows, Clock, 3 grid rows
assert len(m.nodes[button]['children'])==1 and m.get(label+O['W_X'])==10 and m.nodes[label]['text']=='Clock: Standard'
assert [m.nodes[t]['image'] for t in thumbs]==['file://x']+['']*6+['file://x']
def clock_click():
    m.calls=[]; f,ctx=m.handler(button,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
    return [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
assert clock_click()==[(1,'Q2POD','LOCKCLOCK')] and m.nodes[label]['text']=='Clock: Large'
assert clock_click()==[(0,'Q2POD','LOCKCLOCK')] and m.nodes[label]['text']=='Clock: Standard'; passed()
m,_,_,label,_=lock_page({'LOCKCLOCK':'1'}); assert m.nodes[label]['text']=='Clock: Large'; passed()
# The lock window: a built-in drops stock's picture name (the wallpaper paints over it); Large lays
# the date, battery and time out across the top; Standard and card pictures stay stock's.
def lock_window(picture,config={}):
    m=lock_machine(picture,config)
    w={n:m.node(k,n) for k,n in (('image','img_screensaver'),('digit_clock','clock_time'),('digit_clock','clock_date'),
                                   ('label','label_battery'),('image','img_battery'))}
    m.nodes[w['img_screensaver']]['image']=picture
    info=m.node('view','view_timeinfo',[w[n] for n in ('clock_time','label_battery','img_battery','clock_date')])
    m.top=m.node('window','saverscreen_page',[m.node('gesture','app_gesture',[w['img_screensaver'],info])])
    assert m.call(address=HOOKS['saverscreen_page_init'][0],args=(m.top,0,0,0),gap=0)==0
    box=lambda n:tuple(signed(m.get(w[n]+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
    return m,w,info,box
m,w,info,box=lock_window('q2pod:aurora',{'LOCKCLOCK':'1'})
assert m.nodes[w['img_screensaver']]['image']=='' and box('clock_time')[::2]==(0,375) and box('clock_date')[:3]==(0,0,375)
assert m.nodes[w['clock_time']]['style:normal:text_align_h']=='center' and m.nodes[w['clock_time']]['style:normal:font_size']>70
assert signed(m.get(info+O['W_Y']))+box('clock_time')[1]+box('clock_time')[3]<=190 and not m.nodes[w['img_battery']]['visible']
assert box('label_battery')[2]>=60 and box('label_battery')[0]+box('label_battery')[2]<=375; passed()
# Battery: Icon keeps the percentage off the Large clock; every percentage mode shows it (iPod only).
for mode in range(4 if variant=='ipod' else 0):
    CONFIG.update(BATTERY=str(mode)); m,w,_,_=lock_window('q2pod:aurora',{'LOCKCLOCK':'1'}); CONFIG.clear()
    assert m.nodes[w['label_battery']]['visible']==(mode!=0) and not m.nodes[w['img_battery']]['visible']
passed()
m,w,info,box=lock_window(cards[0])
assert m.nodes[w['img_screensaver']]['image']==cards[0] and 'style:normal:font_size' not in m.nodes[w['clock_time']]; passed()
# A new cover: stock sets img_playcover by the same name and never invalidates it; the hook does.
m=lock_machine(); cover=m.node('image','img_playcover'); m.top=m.node('window','saverscreen_page',[m.node('view','view_playinfo',[cover])])
assert m.call(address=HOOKS['saver_cover'][0],args=(m.top,0,0,0),gap=0)==0
assert [c[:2] for c in m.calls if c[0] in ('stock_savercover','widget_invalidate_force')]==[('stock_savercover',m.top),('widget_invalidate_force',cover)]; passed()

# The power poll, on the UI loop. Charge limit: the charger stops (switch_charge_enable(0), the
# active-low charge enable) at CHARGE_STOP, again whenever it charges meanwhile, and is handed
# back at CHARGE_RESUME: on, or USB mode's own choice while its page is open. Every CHARGE_POLL_MS.
class PowerMachine(ResumeMachine):
    def __init__(self,config={},files=None):
        super().__init__(files); self.config.update(config); self.writes=[]
        for n in ('fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0','unlink@GLIBC_2.0','open@GLIBC_2.0','close@GLIBC_2.0'):
            self.handlers[syms[n]]='p:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a=u.reg_read(REGS[0])
        if name.startswith('r:fclose') and self.open.get(a,[0,0])[1]=='w':
            path,_,_,data=self.open.pop(a); self.files[path]=data; self.writes.append((path,data)); self.calls.append(('fclose',a,0,0))
            self.returns(0); return
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
m=PowerMachine({'CHARGELIMIT':'1'})
for pct,charging,want in ((79,2,[]),(80,2,[0]),(79,0,[]),(78,2,[0]),(76,0,[]),(75,0,[1]),(79,2,[])):
    level(m,pct,charging); assert m.pass_()[0]==want,(pct,charging); passed()
level(m,90); assert m.pass_(O['CHARGE_POLL_MS']-1)[0]==[] and m.pass_(1)[0]==[0]; passed()  # polled, not every pass
m.open_pages=['usbmode_page']; m.byte(syms['g_usbdac_chargeflag'],0); level(m,70); assert m.pass_()[0]==[0]; passed()
m=PowerMachine(); level(m,95,2); assert m.pass_()[0]==[]; passed()  # the limit off: stock charges
# Low power: CPU1 offline while the screen is off (the first time bracketed by a synced marker),
# back with the screen. Off, no file is touched. Either way the screen-off pass idles
# SCREEN_OFF_SLEEP_MS, and a screen-on pass with no input or animation for IDLE_MS idles IDLE_SLEEP_MS.
CPU1,MARK='/sys/devices/system/cpu/cpu1/online','/mnt/data/q2pod-cpu1'
inp_hook=HOOKS['window_manager_dispatch_input_event'][0]
m=PowerMachine()
m.byte(syms['g_backlight_status'],0); assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']] and not m.writes and not m.opens; passed()
# Dark with nothing playing and the DAC off (check_dacoff_state's -1), a pass idles DARK_IDLE_SLEEP_MS,
# Low power or not; playing, a DAC still counting, or a Wake press that may get its second, SCREEN_OFF_SLEEP_MS.
for config in ({},{'LOWPOWER':'1'}):
    m=PowerMachine(config); m.handlers.pop(syms['mclGetPlayStatus'],None); m.byte(syms['g_backlight_status'],0)
    off=O['SCREEN_OFF_SLEEP_MS']
    for play,dac,want in ((3,-1,O['DARK_IDLE_SLEEP_MS']),(2,-1,off),(3,5,off),(1,-1,O['DARK_IDLE_SLEEP_MS'])):
        m.word(0xa3beac,play); m.word(syms['g_dacoff_time'],dac&0xffffffff)
        assert m.pass_(1)[1]==[want],(config,play,dac); passed()
m=PowerMachine({'WAKEDOUBLE':'1'}); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa3beac,3)
m.byte(syms['g_backlight_status'],0); m.word(syms['g_dacoff_time'],0xffffffff); m.handlers[inp_hook+12]='stock_input'
assert m.pass_(1)[1]==[O['DARK_IDLE_SLEEP_MS']]  # reads Wake
m.call(O['KEY_CENTER'],address=inp_hook,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0,clear=False)
assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']] and m.pass_(O['WAKE_MS'])[1]==[O['DARK_IDLE_SLEEP_MS']]; passed()
m=PowerMachine({'LOWPOWER':'1'}); m.byte(syms['g_backlight_status'],0)
assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']] and m.writes==[(MARK,b''),(CPU1,b'0')] and MARK not in m.files
assert [c[0] for c in m.calls if c[0] in ('fsync','unlink')]==['fsync','fsync','unlink','fsync']; passed()  # the marker, then its removal, on flash
m.writes=[]; m.byte(syms['g_backlight_status'],1); m.pass_(1); assert m.writes==[(CPU1,b'1')]; passed()
m.writes=[]; m.byte(syms['g_backlight_status'],0); m.pass_(1); assert m.writes==[(CPU1,b'0')]; passed()  # marked once a boot
m=PowerMachine(); m.animating=1; m.pass_(1)  # idle pacing without Low power
assert m.pass_(O['IDLE_MS'])[1]==[]; m.animating=0
assert m.pass_(1)[1]==[O['IDLE_SLEEP_MS']] and not m.writes; passed()
inp=HOOKS['window_manager_dispatch_input_event'][0]; m.handlers[inp+12]='stock_input'
m.call(address=inp,args=(m.wm,m.event,0,0),gap=0); assert m.pass_(1)[1]==[]; passed()  # input: full speed again
# A refused online is retried once a second, not every pass.
class Refused(PowerMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'').startswith('r:fwrite') and self.text(u.reg_read(REGS[0]))=='1':
            self.calls.append(('refused',0,0,0)); self.returns(0); return
        return super().hook(u,address,size,unused)
m=Refused({'LOWPOWER':'1'}); m.byte(syms['g_backlight_status'],0); m.pass_(1); m.byte(syms['g_backlight_status'],1)
tries=lambda ms:[c[0] for c in m.pass_(ms) and m.calls if c[0]=='refused']
assert tries(1)==['refused'] and tries(500)==[] and tries(500)==['refused']; passed()
# A marker that cannot be written: no offline, so a stall could not go unnoticed.
class Full(PowerMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'').startswith('p:fsync'):
            self.calls.append(('fsync',0,0,0)); self.returns(0xffffffff); return
        return super().hook(u,address,size,unused)
m=Full({'LOWPOWER':'1'}); m.byte(syms['g_backlight_status'],0); m.pass_(1); m.pass_(1)
assert not any(p==CPU1 for p,_ in m.writes); passed()
# Now Playing keeps stock's pace only while its visualizer repaints; idle, it idles like any page.
if variant=='ipod':  # stock has no visualizer: np_animating() is 0 there
    class Live(PowerMachine):
        def hook(self,u,address,size,unused):
            if self.handlers.get(address)=='v:live':
                self.returns(self.live); return
            return super().hook(u,address,size,unused)
    m=Live(); m.handlers[payload_syms['visualizer_live']]='v:live'; m.live=1
    m.top=m.node('window','playing_page'); m.pass_(1)
    assert m.pass_(O['IDLE_MS'])[1]==[]; m.live=0; assert m.pass_(1)[1]==[O['IDLE_SLEEP_MS']]; passed()
else:
    m=PowerMachine(); m.top=m.node('window','playing_page'); m.pass_(1)
    assert m.pass_(O['IDLE_MS'])[1]==[O['IDLE_SLEEP_MS']]; passed()
# A boot that stalled offlining CPU1 left the marker: the next one renames it and never tries.
m=PowerMachine({'LOWPOWER':'1'},files={MARK:b''}); m.byte(syms['g_backlight_status'],0); m.pass_(1)
assert not m.writes and '/mnt/data/q2pod-cpu1.bad' in m.files and MARK not in m.files; passed()

# Even if renaming a previous crash marker fails, never offline the core again.
class RenameFailed(PowerMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'').startswith('r:rename'):
            self.returns(0xffffffff); return
        return super().hook(u,address,size,unused)
m=RenameFailed({'LOWPOWER':'1'},files={MARK:b''}); m.byte(syms['g_backlight_status'],0); m.pass_(1)
assert not m.writes and MARK in m.files; passed()

# A flushed inode alone is insufficient if its directory entry cannot reach flash.
class DirectoryFull(PowerMachine):
    syncs = 0
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'').startswith('p:fsync'):
            self.syncs += 1
            if self.syncs == 2:
                self.returns(0xffffffff); return
        return super().hook(u,address,size,unused)
m=DirectoryFull({'LOWPOWER':'1'}); m.byte(syms['g_backlight_status'],0); m.pass_(1)
assert not any(p==CPU1 for p,_ in m.writes); passed()

# Standby limit (docs/internals.md#standby-limit): with a limit, toolsIntoStandby's suspend runs
# between the RTC wake alarm's set (the limit past since_epoch) and its clear. A resume at or past the
# alarm powers off on the next pass (poweroff_page), once, unless a cable is in or the music plays; a
# key's resume before it, an unreadable RTC or a refused alarm leave it on. Off, no file is touched.
RTC_NOW,RTC_ALARM='/sys/class/rtc/rtc0/since_epoch','/sys/class/rtc/rtc0/wakealarm'
class StandbyMachine(PowerMachine):
    def __init__(self,config={},key=None,readable=True,refuse=False,rtc=1792363806):
        super().__init__(config,files={RTC_NOW:f'{rtc}\n'.encode()} if readable else {})
        self.rtc,self.key,self.readable,self.refuse,self.alarm,self.events=rtc,key,readable,refuse,0,[]
        self.handlers[int(manifest['patch_symbols']['stock_standby_trampoline'],16)]='stock_standby'
        self.handlers.pop(syms['mclGetPlayStatus'],None); self.word(0xa3beac,3)
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
start=1792363806
m=StandbyMachine(key=600); assert m.standby()==['suspend'] and not m.opens and not m.off(); passed()
for i in (1,2,3):
    alarm=start+O[f'STANDBY_LIMIT_{i}']*3600; m=StandbyMachine({'STANDBYLIMIT':str(i)})
    assert m.standby()==[('alarm',0),('alarm',alarm),'suspend',('alarm',0)] and m.rtc==alarm
    assert m.off() and not m.off(); passed()
limit=O['STANDBY_LIMIT_2']*3600; m=StandbyMachine({'STANDBYLIMIT':'2'},key=600)
assert m.standby()==[('alarm',0),('alarm',start+limit),'suspend',('alarm',0)] and not m.alarm and not m.off()
m.key=None; assert m.standby()[1]==('alarm',start+600+limit) and m.off(); passed()  # the next one counts from zero
m=StandbyMachine({'STANDBYLIMIT':'1'}); m.alarm=start+60; assert m.standby()[:2]==[('alarm',0),('alarm',start+3600)]; passed()
m=StandbyMachine({'STANDBYLIMIT':'1'},key=7200,readable=False); assert m.standby()==['suspend'] and not m.off(); passed()
m=StandbyMachine({'STANDBYLIMIT':'1'},key=7200,refuse=True); assert m.standby()==[('alarm',0),'suspend'] and not m.off(); passed()
for usb,play in ((1,3),(0,2)):  # a cable in, or playing: it stays on, and no later pass powers off
    m=StandbyMachine({'STANDBYLIMIT':'1'}); m.standby(); m.byte(syms['g_usbdet_value'],usb); m.word(0xa3beac,play)
    assert not m.off() and not m.off(); passed()
# The Wi-Fi signal (navigation.c ringnav_wifi_signal): networkGetWifiSignal, which the status bar and
# the network monitor thread each call about once a second, reads the driver's rx_signal and never
# sends stock's SIOCGIWSTATS, whose TX report takes the radio out of power save. It answers stock's
# dBm (the ioctl's unsigned level less 256, get_station's signal_strength less 100), except that
# 100% is 0 dBm, where stock wraps to -256; a missing file is 0, as stock's failed ioctl. The file
# is read again only after WIFI_SIGNAL_MS.
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
for pct in (0,20,35,64,99,100):
    stock,m=WifiMachine(pct,patched=False),WifiMachine(pct)
    assert stock.signal()==(pct-100 if pct<100 else -256) and stock.used()==['socket','ioctl','close']
    assert m.signal()==pct-100 and m.used()==['open','read','close']
    assert [m.text(c[1]) for c in m.calls if c[0]=='open']==[RX_SIGNAL]; passed()
m=WifiMachine(None); assert m.signal()==0 and m.used()==['open']; passed()  # no driver: as a failed ioctl
m=WifiMachine(60); assert m.signal()==-40; m.pct=30; m.calls=[]
m.now+=O['WIFI_SIGNAL_MS']-1; assert m.signal()==-40 and m.used()==[]
m.now+=1; assert m.signal()==-70 and m.used()==['open','read','close']; passed()



# Boot logo: a card mount (boot, USB storage's end) starts a thread that keeps the card's logo as the
# splash's /mnt/data copy; the poll toasts its failure. The thread is run here once the poll starts it.
LOGO_SRC,LOGO_DST,LOGO_ROT='/mnt/mmc/Q2Pod/boot/boot-logo.jpg','/mnt/data/boot-logo.jpg','/tmp/boot-logo.jpg'
LOGO_BAD,LOGO_FAIL='Boot logo must be a 320x375 JPEG','Could not save boot logo'
def jpg(w,h,sof=0xc0,bits=8,n=3,pad=0):  # an APP0 behind a fill byte, then the frame header
    return b'\xff\xd8\xff\xff\xe0\x00\x04ab'+bytes([0xff,sof])+struct.pack('>HBHHB',8+3*n,bits,h,w,n)+bytes(3*n)+b'\xff\xd9'+bytes(pad)
class LogoMachine(PowerMachine):
    failure=''
    def __init__(self,files=None):
        super().__init__(files=files); self.threads=[]; self.execs=[]; self.toasts=[]; self.rotated=None; self.child=False; self.exits=[]
        self.handlers[payload_syms['child_exit']]='l:exit'
        self.word(0xa2638c,0x1000010); self.handlers[0x1000010]='l:free'  # free's GOT slot, as Coverflow's
        for n in ('fseek@GLIBC_2.0','ftell@GLIBC_2.0','memcmp@GLIBC_2.0','strstr@GLIBC_2.0','pthread_create@GLIBC_2.2',
                  'fork@GLIBC_2.0','execl@GLIBC_2.0','exit@GLIBC_2.0','waitpid@GLIBC_2.0','navigator_to_with_context'): self.handlers[syms[n]]='l:'+n
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
        elif name=='navigator_to_with_context': self.toasts.append(self.text(b+8))
        self.returns(ret,clobber=True)
    def sync(self,mounted=True):  # a poll with the card in or out; returns the threads it started
        self.files['/proc/mounts']=b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n' if mounted else b'none /mnt/data ubifs rw 0 0\n'
        self.threads=[]; self.pass_()
        for t in self.threads: self.call(address=t,args=(0,0,0,0),gap=0,clear=False)
        started=len(self.threads); self.pass_(1)  # the next pass, too soon to poll, toasts a failure
        return started
m=LogoMachine({LOGO_SRC:jpg(320,375)})
assert m.sync()==1 and m.files[LOGO_DST]==jpg(320,375) and m.renames==1 and LOGO_DST+'.tmp' not in m.files and not m.toasts; passed()
assert m.sync()==0 and m.renames==1; passed()  # still mounted: no edge, no sync
assert m.sync(False)==0 and m.sync()==1 and m.renames==1 and not m.toasts; passed()  # remounted, the same bytes: no write
m.files[LOGO_SRC]=jpg(320,375,pad=1); m.sync(False); m.sync(); assert m.files[LOGO_DST]==jpg(320,375,pad=1) and m.renames==2; passed()
del m.files[LOGO_SRC]; m.sync(False); m.sync(); assert LOGO_DST not in m.files and not m.toasts; passed()  # gone: the stock logo
m=LogoMachine(); assert m.sync()==1 and LOGO_DST not in m.files and not m.toasts and not m.execs; passed()
for bad in (jpg(375,375),jpg(320,375,n=1),jpg(320,375,sof=0xc2),jpg(320,375,bits=12),jpg(320,375,pad=512*1024),jpg(0,375),
            b'\xff\xd8\xff\xda\x00\x02',jpg(320,375)[:12],b'not a jpeg',b''):
    m=LogoMachine({LOGO_SRC:bad,LOGO_DST:b'old'}); m.sync()
    assert m.files[LOGO_DST]==b'old' and not m.renames and m.toasts==[LOGO_BAD] and not m.execs,bad[:16]; passed()
m=LogoMachine({LOGO_SRC:jpg(375,320)}); m.child=True; m.sync()
assert m.exits==[127] and LOGO_DST not in m.files and m.execs==[['/usr/bin/ffmpeg','/usr/bin/ffmpeg','-nostdin','-v','quiet','-i',LOGO_SRC,'-vf','transpose=1','-pix_fmt',
                  'yuvj444p','-q:v','2','-f','mjpeg','-y',LOGO_ROT]],m.execs; passed()
m=LogoMachine({LOGO_SRC:jpg(375,320)}); m.rotated=jpg(320,375,pad=2); m.sync()
assert not m.execs and not m.exits
assert m.files[LOGO_DST]==m.rotated and LOGO_ROT not in m.files and not m.toasts; passed()
for rotated in (None,jpg(375,320)):  # ffmpeg failed, or wrote something else
    m=LogoMachine({LOGO_SRC:jpg(375,320),LOGO_DST:b'old'}); m.rotated=rotated; m.sync()
    assert m.files[LOGO_DST]==b'old' and m.toasts==[LOGO_BAD]; passed()
for failure in ('fopen','fwrite','fclose','rename'):
    m=LogoMachine({LOGO_SRC:jpg(320,375),LOGO_DST:b'old'}); m.failure=failure; m.sync()
    assert m.files[LOGO_DST]==b'old' and LOGO_DST+'.tmp' not in m.files and m.toasts==[LOGO_FAIL],failure; passed()
print('Boot logo: mount edges, validation, rotation, unchanged bytes and failed writes passed.')

# System boot row in both variants: regular-file gating, persisted choice and transactional failures.
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
    m.handlers[int(manifest['patch_symbols']['stock_systemset_trampoline'],16)]='stock_systemset'
    m.mock('ferror@GLIBC_2.0','fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0','navigator_to_with_context')
    entries=[m.entry(0) for _ in range(12)]
    view=m.node('scroll_view','scroll_view_sysset',entries.copy())
    m.top=m.node('window','sysset_page',[m.node('list_view','list_view_sysset',[view])])
    assert m.call(address=HOOKS['systemset_sysset_page_init'][0],args=(m.top,5,0,0),gap=0)==0
    assert m.calls[0][:3]==('stock_systemset',m.top,5)
    kids=m.nodes[view]['children']
    if mode!=0o100755:  # Apps (System Update's icon, netupdate.c's page) before Update; no Boot to
        assert kids[:9]+kids[10:]==[entries[i] for i in (1,2,3,4,8,7,5,0,6,9,10,11)]
        apps=m.nodes[kids[9]]['children'][0]; icon,label=m.nodes[apps]['children'][:2]
        assert m.nodes[icon]['image']=='system_update' and m.nodes[label]['text']=='App Store' and m.handler(apps,O['EVT_CLICK'])[0]==payload_syms['shelf_open']
        return m,None,None
    assert len(kids)==14
    assert kids[:9]+kids[11:]==[entries[i] for i in (1,2,3,4,8,7,5,0,6,9,10,11)]
    button=m.nodes[kids[9]]['children'][0]
    label=m.nodes[button]['children'][1]
    return m,button,label
def toggle_boot(m,button):
    f,ctx=m.handler(button,O['EVT_CLICK']); m.calls=[]
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
for value,expected in ((None,'Rockbox'),(b'bad','Rockbox'),(b'q2pod','Q2 Pod'),(b'rockbox','Rockbox'),
                       (b'q2pod\n','Q2 Pod'),(b'q2pod\0bad','Rockbox')):
    m,button,label=boot_row(value)
    assert m.nodes[label]['text']=='Boot to: '+expected
    toggle_boot(m,button)
    next_value=b'rockbox' if expected=='Q2 Pod' else b'q2pod'
    assert m.files['/mnt/data/boot-target']==next_value
    assert m.nodes[label]['text']=='Boot to: '+('Rockbox' if next_value==b'rockbox' else 'Q2 Pod')
    restarted,_,l=boot_row(next_value); assert restarted.nodes[l]['text']==m.nodes[label]['text']
    passed()
for mode in (0,0o040755,0o020644): boot_row(mode=mode); passed()
for failure in ('fopen','fwrite','fflush','fsync','fclose','rename'):
    m,button,label=boot_row(b'rockbox'); m.failure=failure
    toggle_boot(m,button)
    assert m.files['/mnt/data/boot-target']==b'rockbox' and m.nodes[label]['text']=='Boot to: Rockbox'
    assert '/mnt/data/boot-target.tmp' not in m.files
    assert any(c[0]=='navigator_to_with_context' for c in m.calls)
    passed()
m,button,label=boot_row(); m.rockbox_mode=0; toggle_boot(m,button)
assert m.nodes[label]['text']=='Boot to: Rockbox' and '/mnt/data/boot-target' not in m.files; passed()
# Wheel centre reaches Boot to through the existing selection/dispatch path.
m,button,label=boot_row(); view=m.nodes[m.top]['children'][0]; view=m.nodes[view]['children'][0]
m.paint(view)
for _ in range(9): m.call()
assert m.confirm()==11 and m.dispatched()[0][1]==button; passed()
print('Boot settings: conditional row/order, restart, card removal and failed atomic writes passed.')

from build import AUDIO_EXTENSION_CALLS
for site in AUDIO_EXTENSION_CALLS:
    for ext, accepted in (('.m4b',1),('.M4B',1),('.m4B',1),('.M4b',1),('.m4a',1),('.M4A',1),('.flac',1),('.mp3',1),('.m4b.tmp',0),('.pdf',0)):
        m=Machine(); m.mock('strstr@GLIBC_2.0')
        formats=m.string('.mp3 .flac .m4a .M4A'); suffix=m.string(ext)
        m.word(0x7000f000+(0xdd4 if site==0x5c2054 else 0xd64),formats)
        def extension_return(u,address,size,unused):
            u.reg_write(UC_MIPS_REG_PC,0x1000000)
        m.u.hook_add(UC_HOOK_CODE,extension_return,begin=site+8,end=site+8)
        assert bool(m.call(address=site,args=(formats,suffix,0,0),gap=0))==bool(accepted)
        passed()
    # Folder view lists videos in the card's Videos folder (#128); the library scan and other folders never do.
    for folder in (b'/mnt/mmc/vIdEoS/Films',b'/mnt/mmc/Videos',b'/mnt/mmc/Music',b'/mnt/mmc/VideosOld',b'/mnt/sda1/Videos'):
        for ext in ('.mp4','.MKV','.pdf'):
            m=Machine(); m.mock('strstr@GLIBC_2.0'); m.u.mem_write(syms['g_folder_path'],folder+b'\0')
            formats=m.string('.mp3 .flac'); suffix=m.string(ext)
            m.word(0x7000f000+(0xdd4 if site==0x5c2054 else 0xd64),formats)
            m.u.hook_add(UC_HOOK_CODE,extension_return,begin=site+8,end=site+8)
            listed=site==0x5c2054 and folder in (b'/mnt/mmc/vIdEoS/Films',b'/mnt/mmc/Videos') and ext!='.pdf'
            assert bool(m.call(address=site,args=(formats,suffix,0,0),gap=0))==listed,(site,folder,ext)
            passed()

# M4B is spoken audio anywhere, at short duration; persistence and CUE rules stay shared.
for path in ('/mnt/mmc/Novel.m4b','/mnt/mmc/Music/Novel.M4B','/mnt/mmc/Novel.m4B'):
    m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
    m.word(r+O['REC_PATH'],m.string(path)); playing(m,0,200,pos=0,n=110)
    assert m.places()==[(fnv(path),61)] and '/mnt/data/ringnav-plays' not in m.files and '/mnt/mmc/.scrobbler.log' not in m.files
    m.poll(110,200,n=3)
    files=m.files
    m=ResumeMachine(files); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; m.word(r+O['REC_PATH'],m.string(path))
    m.poll(0,200,pos=0,n=2); playing(m,1,200,n=2); assert m.seeks==[110]
    m.poll(195,200,n=3); assert not m.places()
    passed()
m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
m.word(r+O['REC_PATH'],m.string('/mnt/mmc/cue.m4b')); m.word(r+O['REC_CUE_START'],200)
playing(m,0,200,pos=0,n=110); assert m.places()==[(fnv('/mnt/mmc/cue.m4b'),261)] and not m.seeks; passed()  # its file's place

# Mock only player endpoints, exercising the actual routing and ear/AVRCP state machine.
class HeadsetMachine(Machine):
    def __init__(self,owner=1):
        super().__init__(); self.owner=owner; self.playing=True; self.session=42; self.actions=[]; self.saved=0
        self.handlers[syms['mclGetSavePlayStatus']]='hs:saved'
        for name in ('spot_status','radio_status','video_status','spot_session','radio_session','video_session',
                     'spot_set_playing','radio_set_playing','video_set_playing','spot_media','radio_media','video_key'):
            self.handlers[payload_syms[name]]='hs:'+name
        self.handlers[syms['mclGetPlayStatus']]='hs:local_status'
        self.handlers[syms['player_play_pause']]='hs:local_toggle'
        self.handlers[syms['player_next_music']]='hs:local_next'
        self.handlers[syms['player_prev_music']]='hs:local_prev'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('hs:'): return super().hook(u,address,size,unused)
        name=name[3:]; a=u.reg_read(REGS[0]); ret=0
        owner=4 if name.startswith('video') else 3 if name.startswith('radio') else 2 if name.startswith('spot') else 1
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
for owner in (1,2,3,4):
    m=HeadsetMachine(owner); m.byte(syms['g_backlight_status'],0)
    m.ears(3); assert m.playing and not m.actions
    m.ears(2); assert not m.playing and m.actions==[(owner,'pause')]
    m.command(201); m.command(164); assert not m.playing and len(m.actions)==1
    m.ears(0); m.ears(1); assert not m.playing  # other bud alone does not resume
    m.ears(3); assert m.playing and m.actions[-1]==(owner,'play')
    m.ears(2); m.fn('headset_manual'); m.ears(3); assert not m.playing
    m=HeadsetMachine(owner); m.ears(3); m.command(201); m.ears(2); m.ears(3); assert m.playing
    m=HeadsetMachine(owner); m.ears(0); m.ears(3); assert not m.playing  # initial report establishes state
    m=HeadsetMachine(owner); m.ears(3); m.ears(2); m.owner=1 if owner!=1 else 2; m.ears(3); assert not m.playing
    m=HeadsetMachine(owner); m.ears(3); m.ears(2)
    if owner!=1: m.session+=1
    else: m.word(O['MCL_POS'],1)
    m.ears(3); assert not m.playing
    m=HeadsetMachine(owner); m.command(163); m.command(163,gap=1); assert len(m.actions)==1
    m.command(165); assert len(m.actions)==2 and all(a[0]==owner for a in m.actions)
    passed()
m=HeadsetMachine(); good=bytes([4,0,4,0,6,0,0,2])
for packet in (b'',good[:7],good+b'\0',bytes([4,0,4,0,6,0,3,0]),bytes([4,1,4,0,6,0,0,0])):
    p=m.alloc(max(1,len(packet))); m.u.mem_write(p,packet)
    assert m.call(address=payload_syms['ringnav_aap_parse'],args=(p,len(packet),0,0),gap=0)==0
p=m.alloc(8); m.u.mem_write(p,good); assert m.call(address=payload_syms['ringnav_aap_parse'],args=(p,8,0,0),gap=0)==2; passed()
# Ears are counted: the report's primary/secondary slots swap. One worn AirPod out pauses and back
# (now the other slot) resumes; a swap alone does nothing; a manual command still cancels resume.
for owner in (1,2,3,4):
    m=HeadsetMachine(owner); m.ears(1); m.ears(2); assert m.playing and not m.actions
    m.ears(0); assert not m.playing and m.actions==[(owner,'pause')]
    m.ears(2); assert m.playing and m.actions[-1]==(owner,'play')
    m.ears(1); m.ears(3); m.ears(2); assert not m.playing  # two worn: primary out, slots swapped
    m.ears(3); assert m.playing
    m.ears(1); m.fn('headset_manual'); m.ears(3); assert not m.playing
    passed()

for absolute,limit,want in ((127,40,40),(0,100,0),(64,100,50),(1,100,1),(126,100,99)):
    b=BtVolumeMachine(); b.poll(); b.byte(syms['g_maxvolume'],limit)
    b.absolute=absolute; b.poll()
    assert b.u.mem_read(syms['g_volume'],1)[0]==want
    assert b.absolute==(want*127+50)//100
    b.poll(); assert not b.writes()
    passed()
# A headset step that already reads as the Q2 volume stays: 2 is volume 2, never rewritten to 3.
b=BtVolumeMachine(); b.poll(); b.absolute=2; b.poll()
assert b.u.mem_read(syms['g_volume'],1)[0]==2 and not b.writes() and b.absolute==2
assert b.volume(2,2)==[(100,100)] and not b.writes(); b.poll(); assert not b.writes(); passed()
print('M4B recognition/resume and headset routing, ear masks, manual cancellation, malformed packets and volume limits passed.')

# Real helper APIs: explicit pause/resume is observable and repeatable, with shared gain changes.
def endpoint(m,name,value=0):
    return m.call(address=payload_syms[name],args=(value,0,0,0),gap=0,clear=False)
def ready_gain(m,ready,volume):
    m.way=1; m.config['BTVOLLINK']='1'; m.word(payload_syms['bt_vol_link'],1)  # Control Device Volume on, read or not
    for name in ('g_bluetoothflag','bt_linkstatus'): m.byte(syms[name],1)
    m.word(syms['bt_showcoding'],1); m.byte(syms['g_volume'],volume)
    m.word(payload_syms['btvol']+4 if 'btvol' in payload_syms else payload_syms['btvol.1'],ready)  # clang may split scalar fields
m=SpotMachine({BIN:bytearray()}); spot_open(m,stream_page(m)); m.state('playing'); m.poll()
for playing,command in ((0,'s'),(1,'P')):
    m.sent=[]; endpoint(m,'spot_set_playing',playing); endpoint(m,'spot_set_playing',playing)
    assert [x[1] for x in m.sent]==[command] and endpoint(m,'spot_status')==(2 if playing else 3)
ready_gain(m,1,32); m.now+=500; m.sent=[]; endpoint(m,'spot_poll'); assert m.sent[-1][1]=='v'+chr(100)
ready_gain(m,1,0); m.now+=500; endpoint(m,'spot_poll'); assert m.sent[-1][1]=='v\0'
ready_gain(m,0,32); m.now+=500; endpoint(m,'spot_poll'); assert m.sent[-1][1]=='v'+chr(32)
passed()
m=RadioMachine(); radio_open(m); m.row(1); m.row(0)
assert endpoint(m,'radio_status')==2
m.waited=m.forked
endpoint(m,'media_set_playing',0); endpoint(m,'media_set_playing',0); assert endpoint(m,'radio_status')==3
m.waited=0
endpoint(m,'media_set_playing',1); assert endpoint(m,'radio_status')==2
ready_gain(m,1,32); m.sent=[]; m.now+=500; endpoint(m,'radio_poll'); assert m.sent[-1][1]=='v'+chr(100)
ready_gain(m,0,32); m.sent=[]; m.now+=500; endpoint(m,'radio_poll'); assert m.sent[-1][1]=='v'+chr(32)
passed()
# Streaming's Configuration (docs/internals.md#streaming-configuration), after AirPlay: Tidal's and
# Spotify's cache caps cycle 512 MB to 8 GB from 2 GB, kept in their folders' .cache-size in
# librespot's form, which tidal.c's trim and the Spotify launcher read. Stop Streaming Services ends
# librespot and its loop, a station and a stock stream, and the keys are local music's again.
TCAP,SCAP='/mnt/mmc/.tidal/.cache-size','/mnt/mmc/.spotify/.cache-size'
m=RadioMachine({BIN:bytearray()}); spot_open(m,stream_page(m)); m.state('paused'); m.poll()
radio_open(m); m.row(1); m.row(0); assert endpoint(m,'radio_status')==2
m.top=m.node('window','home_page'); m.stream(); view=m.nodes[m.top]['children'][0]
conf=m.nodes[m.nodes[view]['children'][5]]['children'][0]; icon,label=m.nodes[conf]['children'][:2]
assert m.nodes[icon]['image']=='stream_config' and m.nodes[label]['text']=='Configuration'
m.click(conf); m.page=m.top; assert m.nodes[m.page]['name']=='streamconf_page'
assert m.labels()==['Configuration','Tidal Cache: 2 GB','Spotify Cache: 2 GB','Stop Streaming Services','TIDAL','Spotify','Tidal Downloads','Internet Radio','AirPlay']
m.row(0); assert m.files[TCAP]==b'4096M' and m.labels()[1]=='Tidal Cache: 4 GB' and endpoint(m,'stream_cache',0)==3
m.row(0); m.row(0); assert m.files[TCAP]==b'512M' and m.labels()[1]=='Tidal Cache: 512 MB'
m.row(1); assert m.files[SCAP]==b'4096M' and m.labels()[2]=='Spotify Cache: 4 GB' and endpoint(m,'stream_cache',1)==3
m.files[SCAP]=bytearray(b'9G'); assert endpoint(m,'stream_cache',1)==2  # not a choice: 2 GB
m.word(O['MCL_TYPE'],5); m.play=2; m.waited=m.forked
m.row(2); m.top=m.node('window','home_page')
assert 'killall -9 librespot aplay' in m.systems[-1] and 'player_stop' in [c[0] for c in m.calls]
assert [m.text(c[2]+8) for c in m.calls if c[0]=='navigator_to_with_context']==['Streaming stopped']
assert endpoint(m,'radio_status')==0 and endpoint(m,'spot_status')==0 and m.key(O['KEY_PLAY'])!=11; m.close(); passed()
# The wheel drives only allowlisted windows (patch/contexts.inc), so every runtime list page needs an
# entry; the two streaming Now Playing pages and a card app's QR code page (no list) handle their own keys.
pages=set(re.findall(r'page_open\("(\w+)"',''.join(p.read_text() for p in (ROOT/'patch').glob('*.c'))))
assert pages-set(re.findall(r'"(\w+)"',(ROOT/'patch/contexts.inc').read_text()))=={'radionp_page','spotify_page','qrcode_page'}; passed()
m=BooksMachine({},{}); vid=payload_syms['vid']; m.word(vid+12,4242); m.word(vid+16,7)
for playing,command in ((0,'a'),(1,'r')):
    m.sent=[]; endpoint(m,'video_set_playing',playing); endpoint(m,'video_set_playing',playing)
    assert [x[1] for x in m.sent]==[command,command] and endpoint(m,'video_status')==(2 if playing else 3)
ready_gain(m,1,32); m.now+=1500; m.sent=[]; endpoint(m,'video_poll')
assert [x[1] for x in m.sent]==['v'+chr(32),'g'+chr(100)]
ready_gain(m,0,32); m.now+=500; m.sent=[]; endpoint(m,'video_poll'); assert m.sent[-1][1]=='g'+chr(32)
m.files['/tmp/q2video.state']=bytearray(struct.pack('<i',1)); m.now+=500; endpoint(m,'video_poll'); assert endpoint(m,'video_status')==3
passed()
# Manual video pause must take effect before the helper's next state report.
m=BooksMachine({},{}); m.word(payload_syms['vid']+12,4242); m.word(payload_syms['vid']+16,7)
endpoint(m,'headset_ears',7); endpoint(m,'video_key',O['KEY_PLAY'])
assert endpoint(m,'video_status')==3
endpoint(m,'headset_ears',6); endpoint(m,'headset_ears',7)
assert endpoint(m,'video_status')==3
m.files['/tmp/q2video.state']=bytearray(struct.pack('<i',0)); m.now+=400; endpoint(m,'video_poll')
assert endpoint(m,'video_status')==3  # stale helper state cannot undo the manual pause
endpoint(m,'video_key',O['KEY_PLAY']); assert endpoint(m,'video_status')==2
passed()
# Stock Tidal shares the native queue controls and session cancellation.
m=HeadsetMachine(); m.word(O['MCL_TYPE'],2); m.ears(3); m.ears(1); assert not m.playing
m.ears(3); assert m.playing; m.command(163); assert m.actions[-1]==(1,'local_next'); passed()
# A focused streaming page owns the keys: Spotify's while it runs, else the owner's; Tidal's are local's.
for owner,page,want in ((2,'spotbrowse_page',[(2,O['KEY_PLAY'])]),(1,'spotify_page',[(2,O['KEY_PLAY']),(1,'pause')]),
                        (3,'radionp_page',[(3,O['KEY_PLAY'])]),(2,'tidal_downloads',[(1,'pause')]),(2,'home_page',[(2,O['KEY_PLAY'])])):
    m=HeadsetMachine(owner); m.top=m.node('window',page,[])
    m.fn('media_key',O['KEY_PLAY']); assert m.actions==want,(page,m.actions); passed()
# Resume on connect (docs/internals.md#resume-on-connect), local music and Spotify. Off (default): a
# headset's PLAY within CONNECT_HOLD_MS (5 s) of the link starts nothing, a later one plays. On: it plays.
for owner in (1,2):
    for off in (0,1):
        m=HeadsetMachine(owner); m.playing=False
        if not off: m.config['RESUME']='1'
        for name in ('g_bluetoothflag','bt_linkstatus'): m.byte(syms[name],1)
        m.fn('headset_poll',gap=200); m.command(200)
        if not off: assert m.playing; passed(); continue
        m.command(164); m.command(207); assert not m.playing and not m.actions
        m.command(200,gap=5000); assert m.playing and m.actions==[(owner,'play')]; passed()
# Stock's restart once A2DP comes up (BT_CONNECT_START): on, player_save_start(0) as stock. Off (default): a
# playing queue goes on, a paused one comes back paused, a stopped one or a stream's turn starts nothing.
from build import BT_CONNECT_START
assert struct.unpack_from('<I',demo,fileoff(demo,BT_CONNECT_START[0]))[0]==jal(payload_syms['ringnav_bt_connect_start'])
for off,owner,saved,want in ((0,1,3,[0]),(0,2,0,[0]),(1,1,2,[0]),(1,1,3,[1]),(1,1,0,[]),(1,2,3,[])):
    m=HeadsetMachine(owner); m.saved=saved
    if not off: m.config['RESUME']='1'
    m.fn('ringnav_bt_connect_start')
    assert [c[1] for c in m.calls if c[0]=='player_save_start']==want; passed()
print('Real Spotify/radio/video play-state APIs, helper gain updates and stock Tidal routing passed.')

# Now Playing's info page: its one fill (NP_INFO_CALL) goes through ringnav_np_info after stock's, which
# writes "Label:value" and the bare folder: ": " after each label, units spaced, the folder labelled
# from player_location, en_US's first unknown ("Unknown Artist") plain "Unknown"; a second pass changes nothing.
from build import NP_INFO_CALL
assert struct.unpack_from('<I',demo,fileoff(demo,NP_INFO_CALL))[0]==jal(payload_syms['ringnav_np_info'])
class InfoMachine(Machine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='i:format':
            self.u.mem_write(u.reg_read(UC_MIPS_REG_A1),{'player_location':getattr(self,'location',b'Location\0')}.get(self.text(u.reg_read(UC_MIPS_REG_A0)),b'?\0'))
            self.returns(0); return
        return super().hook(u,address,size,unused)
m=InfoMachine(); m.handlers[syms['getFormatString']]='i:format'
stock_lines={'label_album':'Album:Kid A','label_genre':'Genre:Unknown Artist','label_size':'File Size:35.92MB',
             'label_year':'Year:2000','label_encode':'Encoding:FLAC','label_bits':'Bit Depth:16bit','label_kbps':'Bitrate:950kbps',
             'label_sample':'Sample Rate:44.1kHz','label_location':'SD:/Music/Radiohead'}
lines={k:m.node('label',k,text=v) for k,v in stock_lines.items()}
win=m.node('window','playing_page',list(lines.values()))
want=['Album: Kid A','Genre: Unknown','File Size: 35.92 MB','Year: 2000','Encoding: FLAC','Bit Depth: 16-bit',
      'Bitrate: 950 kbps','Sample Rate: 44.1 kHz','Location: SD:/Music/Radiohead']
for _ in range(2):
    m.call(address=payload_syms['ringnav_np_info'],args=(win,0,0,0),gap=0)
    assert [c[1] for c in m.calls if c[0]=='np_info']==[win] and [m.nodes[l]['text'] for l in lines.values()]==want, [m.nodes[l]['text'] for l in lines.values()]
passed()
# A missing player_location ("(key)") reads Location; a long (CJK) folder, past 512 bytes, stays whole.
m=InfoMachine(); m.handlers[syms['getFormatString']]='i:format'; m.location=b'(player_location)\0'
folder='SD:/Music/'+'\u97f3\u697d'*150; label=m.node('label','label_location',text=folder)
m.call(address=payload_syms['ringnav_np_info'],args=(m.node('window','playing_page',[label]),0,0,0),gap=0)
assert m.nodes[label]['text']=='Location: '+folder; passed()

# on_wm_idle_status's player_mainscheduling (its status round trip) runs every MAINSCHEDULING_MS, a second
# while dark with nothing playing.
from build import MAINSCHEDULING_CALL
assert struct.unpack_from('<I',demo,fileoff(demo,MAINSCHEDULING_CALL[0]))[0]==jal(payload_syms['ringnav_mainscheduling'])
m=Machine()
def scheduled(): return sum(c[0]=='player_mainscheduling' for c in m.calls)
def schedule(): m.call(address=payload_syms['ringnav_mainscheduling'],args=(0,0,0,0),gap=0,clear=False)
for wait,runs in ((0,1),(50,0),(50,1),(99,0),(1,1)):
    m.now+=wait; m.calls=[]; schedule(); assert scheduled()==runs,(wait,runs)
m.word(payload_syms['dark_quiet'],1); m.now+=100; m.calls=[]; schedule(); assert scheduled()==0
m.now+=900; schedule(); assert scheduled()==1; m.word(payload_syms['dark_quiet'],0)
# Playing, from a second before the track's end (player_playtime_and_length: 225 s) until
# MAINSCHEDULING_END_MS after, every pass: a change without Gapless starts as soon as stock's did.
# Paused there, it stays paced.
m.handlers.pop(syms['mclGetPlayStatus']); m.word(PLAY_STATUS,2)
m.playtime=223; m.now+=100; schedule(); m.calls=[]; m.now+=10; schedule(); assert scheduled()==0
m.playtime=224; m.now+=10; m.calls=[]; schedule(); schedule(); assert scheduled()==2
del m.playtime; m.now+=1900; schedule(); m.calls=[]; m.now+=10; schedule(); assert scheduled()==1
m.now+=90; m.calls=[]; schedule(); assert scheduled()==0
m.playtime=224; m.word(PLAY_STATUS,3); m.now+=100; schedule(); m.calls=[]; m.now+=10; schedule()
assert scheduled()==0; passed()

# Chapter subprocess syscalls use Linux o32 and pass the filename through argv.
for name,number,args,error in (('chapter_pipe2',4328,(0x1001000,0x80000,0,0),False),
                                ('chapter_pipe2',4328,(0x1001000,0x80000,0,0),True),
                                ('chapter_kill',4037,(123,9,0,0),False),
                                ('chapter_execv',4011,(0x1001000,0x1002000,99,0),False)):
    m=Machine(); seen=[]
    def chapter_syscall(u,interrupt,unused):
        seen.append((u.reg_read(UC_MIPS_REG_V0),*[u.reg_read(r) for r in REGS[:3]]))
        u.reg_write(UC_MIPS_REG_A3,int(error)); u.reg_write(UC_MIPS_REG_V0,13 if error else 0)
    m.u.hook_add(UC_HOOK_INTR,chapter_syscall)
    result=m.call(address=payload_syms[name],args=args,gap=0)
    assert result==(-1 if error else 0)
    assert seen==[(number,args[0],args[1],0 if name=='chapter_execv' else args[2])]; passed()

# Chapter extraction is tested with real FFmpeg in chapters.py; here only that boundary is mocked.
class ChapterMachine(CoverflowMachine):
    def __init__(self, count=3, failed=False, ready=True):
        super().__init__(page='playing_page', pos=1)
        self.ready=ready; self.cancelled=0; self.playtime=70; self.fits=1
        self.job=self.alloc(32+512*260)
        for off,v in ((12,0),(16,int(failed)),(20,count)): self.word(self.job+off,v)
        for i,(sec,title) in enumerate(((0,'Opening'),(60,''),(120,'日本語'))):
            self.word(self.job+32+i*260,sec)
            self.u.mem_write(self.job+36+i*260,title.encode()+b'\0')
        for name in ('chapters_start','chapters_poll','chapters_cancel','mp4_fits'):
            self.handlers[payload_syms[name]]='chapter:'+name
        self.mock('navigator_back')
        self.handlers.pop(syms['mclGetPlayStatus'],None); self.word(0xa3beac,2)
        self.track=self.items(self.get(syms['mcl_pdeqplaylist']))[1]
        self.word(self.track+O['REC_PATH'],self.string('/mnt/mmc/Book ; $x.M4B'))
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('chapter:'): return super().hook(u,address,size,unused)
        a=u.reg_read(REGS[0]); ret=0
        if name=='chapter:chapters_start':
            assert self.text(a)=='/mnt/mmc/Book ; $x.M4B'; ret=self.job
        elif name=='chapter:mp4_fits':
            assert self.text(a)=='/mnt/mmc/Book ; $x.M4B'; ret=self.fits
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

for ext,shown in (('m4b',True),('M4B',True),('M4b',True),('mp3',False),('m4b.mp3',False)):
    m=playing_menu(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[1]
    m.word(r+O['REC_PATH'],m.string('/p/book.'+ext))
    m.press(6000); assert m.hold()==11 and ('Chapters' in m.labels())==shown; passed()
for state in (2,3):
    m=ChapterMachine(); m.word(0xa3beac,state); m.open_chapters()
    assert 'Loading chapters…' in m.texts()
    m.advance(50); view=m.find('scroll_view'); m.paint(view)
    assert m.nodes[view]['_ringnav_index']==1
    assert 'Chapter 2' in m.texts() and '0:01:00' in m.texts() and '日本語' in m.texts()
    m.choose(2)
    seeks=[c for c in m.calls if c[0] in ('player_seek_time','mclSetSeek','mclSetResume')]
    assert [(c[0],c[1]) for c in seeks]==[('player_seek_time' if state==2 else 'mclSetSeek',120)]
    assert any(c[0]=='navigator_back' for c in m.calls); passed()
# A book whose MP4 index would not fit in RAM: a toast, and no ffmpeg.
m=ChapterMachine(); m.fits=0; m.press(6000); assert m.hold()==11; m.release()
m.pick(m.labels().index('Chapters')); m.advance(0)
assert m.nodes[m.top]['name']!='chapters_page' and m.toasts[-1][3]=='Too long to read chapters on the Q2'; passed()
m=ChapterMachine(); m.open_chapters(); m.advance(50); m.word(O['MCL_POS'],2); m.choose(1)
assert not any(c[0] in ('player_seek_time','mclSetSeek') for c in m.calls)
assert any(c[0]=='navigator_back' for c in m.calls); passed()
for count,failed,title in ((0,False,'No embedded chapters'),(0,True,'Could not read chapters')):
    m=ChapterMachine(count,failed); m.open_chapters(); m.advance(50)
    assert title in m.texts() and not m.rows(); passed()
m=ChapterMachine(ready=False); m.open_chapters(); m.advance(50)
f,ctx=m.handler(m.page,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.cancelled==1
m.advance(50); assert m.freed==1 and not m.timers; passed()

# Centre/wheel and touch reach the same guarded row handler.
for touch in (False,True):
    m=ChapterMachine(); m.open_chapters(); m.advance(50); view=m.find('scroll_view'); m.paint(view)
    if touch:
        m.choose(2)
    else:
        assert m.call(O['KEY_NEXT'],gap=0)==11
        assert m.nodes[view]['_ringnav_index']==2
        m.on_click=lambda w,e: m.nested(m.handler(w,O['EVT_CLICK'])[0],m.handler(w,O['EVT_CLICK'])[1],e)
        assert m.confirm()==11
    assert any(c[0]=='player_seek_time' and c[1]==120 for c in m.calls); passed()

class ChapterResumeMachine(ChapterMachine):
    def __init__(self):
        super().__init__()
        self.files=place(85,'/mnt/mmc/Book ; $x.M4B'); self.open={}; self.play=(0,225)
        self.seeks=[]; self.opens=self.renames=0; self.epoch=1700000000
        self.handlers[sleep_hook+12]='stock_sleep'
        for name in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2',
                     'rename@GLIBC_2.0','access@GLIBC_2.0','time@GLIBC_2.0',
                     'player_playtime_and_length','player_seek_time'):
            self.handlers[syms[name]]='r:'+name
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'').startswith('r:'): return ResumeMachine.hook(self,u,address,size,unused)
        return super().hook(u,address,size,unused)
    poll=ResumeMachine.poll
for polls in (0,1,2):  # before first resume poll, during settling, or with a restore pending
    m=ChapterResumeMachine()
    if polls: m.poll(0,225,pos=1,n=polls)
    m.open_chapters(); m.advance(50); m.choose(0)
    assert m.seeks==[0]
    m.poll(0,225,n=2); m.poll(1,225,n=3)
    assert m.seeks==[0], (polls,m.seeks); passed()

# Stream threads (tidal.c): Tidal's poll always parks on its gate, lit or dark, whatever the queue;
# a Tidal track asked for posts it once stock's get_tidal_trackurl has run. Baidu's poll only ever
# waits, so it never wakes.
m=Machine(); m.mock('usleep@GLIBC_2.0','sem_wait@GLIBC_2.2','sem_post@GLIBC_2.2')
m.handlers[payload_syms['stock_tidaltrack_trampoline']]='stock_tidaltrack'
def stream_calls(name,*args):
    m.calls=[]; m.call(address=payload_syms[name],args=(*args,0,0,0,0)[:4],gap=0,clear=False)
    return [c[0].split('@')[0] for c in m.calls]
for lit,queue in ((1,0x20),(0,0xf001),(0,0x20)):
    m.byte(syms['g_backlight_status'],lit); m.word(O['MCL_TYPE'],queue)
    assert stream_calls('ringnav_tidal_poll',196608)==['sem_wait']
assert stream_calls('ringnav_tidal_track')==['stock_tidaltrack','sem_post']
assert stream_calls('ringnav_baidu_poll',10000)==['sem_wait']
passed()

print(f'{checks} MIPS execution scenarios passed; toolkit services mocked, stock lock filter executed.')
