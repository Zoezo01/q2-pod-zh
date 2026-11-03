#!/usr/bin/env python3
"""Compose native-size review sheets; these are layout illustrations, not AWTK captures.
Usage: python3 test/ui_preview.py [--themes BEFORE,AFTER] BEFORE_BUILD AFTER_BUILD OUTPUT [BEFORE_CF AFTER_CF]
Each side is drawn in an iPod Theme, classic or minimal (default classic,minimal); one build passed
twice compares its two themes. Uses the existing ImageMagick dependency and the original firmware's
native font/assets.
"""
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
sys.path.insert(0, sys.path[0] + '/../tools')
from ipod import (decode, walk, imagemagick, png_header, HOME_TEXT_X, HOME_LIST_W, STATUS_MARGIN,
                  HOME_LABEL_END, HOME_ROW, HOME_TOP, INC, inc, SETTINGS_SLIDERS)

argv = sys.argv[1:]
themes = ['classic', 'minimal']
if argv[:1] == ['--themes']: themes, argv = argv[1].split(','), argv[2:]
assert len(themes) == 2 and set(themes) <= {'classic', 'minimal'}
before, after, out = map(pathlib.Path, argv[:3])
cf_paths = list(map(pathlib.Path, argv[3:]))
assert len(cf_paths) in (0, 2)
out.mkdir(parents=True, exist_ok=True)
# Classic: patch/offsets.inc ACCENTS (top, bottom, light); Minimal has no accent.
classic_accents = [tuple(int(v, 16) for v in g) for g in re.findall(r'\{ 0x(\w+), 0x(\w+), 0x(\w+), 0x(\w+), 0x\w+ \}', INC)]
minimal = (0xeeeeec, 0xeeeeec, inc('MINIMAL_FILL'))

with tempfile.TemporaryDirectory(prefix='q2-ui-preview-') as tmp:
    tmp = pathlib.Path(tmp)
    def asset(build, name):
        p = tmp/(('before-' if build.resolve() == before.resolve() else 'after-') + pathlib.Path(name).name)
        if not p.exists():
            p.write_bytes(subprocess.check_output(['unsquashfs', '-cat', str(build/'rootfs.squashfs'),
                          'release/assets/default/raw/' + name]))
        return p
    font = asset(before, 'fonts/default.ttf')
    # Builds can patch quote glyphs; provenance records the exact packaged font hash.
    assert font.read_bytes() == asset(after, 'fonts/default.ttf').read_bytes(), 'Build fonts differ'
    def tinted(p, name, accent):
        dest=tmp/(str(accent)+'-'+p.name)
        if dest.exists(): return dest
        w,h=png_header(p.read_bytes())[:2]
        pixels=bytearray(imagemagick(p,'-depth','8','rgba:-',data=b''))
        tone=0x2b2b2b if name.startswith('confirm_') else classic_accents[accent][3]
        if name in ('add','dec'): tone=classic_accents[accent][2]
        glyph=0x2b2b2b if accent==0 and name=='drop_wifiopen' else 0xffffff
        red=(255,20,72); total=sum(red); delta=[3*c-total for c in red]; dd=sum(d*d for d in delta)
        for i in range(0,len(pixels),4):
            rgb=pixels[i:i+3]; sm=sum(rgb)
            t=int(sum((3*c-sm)*d for c,d in zip(rgb,delta))*4096/dd)
            if t<256 and glyph!=0xffffff: t=0
            k=int((sm*4096-t*total)/(3*4096))
            if (t and t<256) or t>4352 or k < -inc('RED_TOLERANCE'): continue
            if any(abs(c-(int(t*r/4096)+k))>inc('RED_TOLERANCE') for c,r in zip(rgb,red)): continue
            for j,shift in enumerate((16,8,0)):
                pixels[i+j]=max(0,min(255,int(t*(tone>>shift&255)/4096)+int(k*(glyph>>shift&255)/255)))
        dest.write_bytes(imagemagick('-size',f'{w}x{h}','-depth','8','rgba:-','png:-',data=bytes(pixels)))
        return dest
    def render(args, path):
        data = imagemagick('-respect-parentheses', *map(str, args), '-strip', '-define', 'png:exclude-chunks=date,time', 'png:-', data=b'')
        path.write_bytes(data)
        return png_header(data)[:2]
    def text(args, value, x, y, width, height, size=20, color='#FFFFFF'):
        # Clip the first scrolling frame to the widget's real bounds, without wrapping.
        args += ['(', '+size', '-background', 'none', '-fill', color, '-font', font, '-pointsize', size,
                 'label:'+value, '-gravity', 'West', '-crop', f'{width}x{height}+0+0', '+repage',
                 '-extent', f'{width}x{height}', ')', '-gravity', 'NorthWest', '-geometry', f'+{x}+{y}', '-composite']
    def image(args, p, x, y, w, h):
        args += ['(', '-background', 'none', p, '-resize', f'{w}x{h}^', '-gravity', 'Center', '-extent', f'{w}x{h}',
                 ')', '-gravity', 'NorthWest', '-geometry', f'+{x}+{y}', '-composite']
    def rect(args, x, y, w, h, color):
        args += ['-fill', color, '-draw', f'rectangle {x},{y} {x+w-1},{y+h-1}']
    def recolour(p, minimal_ink=None, luminance=False):
        """Minimal's runtime image work (navigation.c ringnav_image_add): a settings icon's disc
        MINIMAL_CHEVRON (blended to its white glyph by saturation) or list_into's ink, alpha kept."""
        dest=tmp/(('luma-' if luminance else 'ink-' if minimal_ink else 'grey-')+p.parent.name+'-'+p.name)
        if dest.exists(): return dest
        w,h=png_header(p.read_bytes())[:2]
        px=bytearray(imagemagick(p,'-depth','8','rgba:-',data=b''))
        sat=lambda i: max(px[i:i+3])-min(px[i:i+3])
        disc=max([0]+[sat(i) for i in range(0,len(px),4)])
        if not disc and not minimal_ink and not luminance: return p
        for i in range(0,len(px),4):
            grey=(px[i]*299+px[i+1]*587+px[i+2]*114)//1000 if luminance else minimal_ink if minimal_ink else 255-(255-0x77)*min(sat(i),disc)//disc
            px[i:i+3]=bytes([grey])*3
        dest.write_bytes(imagemagick('-size',f'{w}x{h}','-depth','8','rgba:-','-define','png:color-type=6','png:-',data=bytes(px)))
        return dest
    def selection(args, y, h, width, palette):
        top, bottom, _ = palette
        args += ['(', '-size', f'{width}x{h}', f'gradient:#{top:06x}-#{bottom:06x}',
                 ')', '-gravity', 'NorthWest', '-geometry', f'+0+{y}', '-composite']
        rect(args, 0, y, width, 1, '#eeeeec' if top==0xeeeeec else ('#555555' if palette[2]==0x6e6e6e else f'#{palette[2]:06x}'))
    bright, dark = tmp/'bright.png', tmp/'dark.png'
    render(['-size','166x166','gradient:#FCE6AF-#2C7E93'], bright)
    render(['-size','166x166','gradient:#10131D-#423459'], dark)
    cases = ['Home Split / bright art', 'Home Full / long labels / no Rockbox', 'Home Settings Full',
             'Local Now Playing / paused / dark art', 'Spotify / missing art / scrub',
             'Library / added media', 'Folders / long labels', 'Display settings',
             'Quick settings / active, inactive, disabled', 'Confirmation / Cancel focused',
             'Home Split / dark art', 'Home Split / missing art',
             'Now Playing / bright art / seeking', 'PEQ / focus and untouched plot', 'Media browser / multilingual names']
    cases += [f'{label} / {value}%' for label in ('Brightness','Balance','Maximum volume','Default volume')
              for value in (0,50,100)]
    cases += ['System Settings / with Rockbox', 'System Settings / without Rockbox', 'Internet Radio / favourite']
    for accent, name in enumerate(('Graphite','Crimson','Tidal','Champagne','Cobalt')):
        tiles=[]
        for case, title in enumerate(cases):
            for side, (theme, build) in enumerate(zip(themes, (before, after))):
                old = theme == 'classic'  # Classic draws the pre-1.0.1 look
                palette = classic_accents[accent][:3] if old else minimal
                tint = accent if old else 0  # Minimal's images take Graphite's greys
                a=['-size','375x320','xc:black']
                if case<8 or case>=10:
                    rect(a,0,0,375,30,f'#{inc("BAR_COLOR"):06x}')
                    text(a,'Ⅱ' if case==3 else 'Q2' if case in (1,2) or case>=15 else '▶',STATUS_MARGIN,7,28,16,16)
                    text(a,'12:59 PM',151,5,74,20,16)
                    text(a,'88%',280,5,40,20,16)
                if case<3 or case in (10,11):
                    full=case in (1,2); width=(inc('HOME_CLASSIC_ROW') if old else HOME_LIST_W) if full else inc('HOME_SPLIT_W')
                    labels = ['Now Playing','Library','Coverflow','Folders','Rockbox','Streaming','Settings']
                    if case==1: labels=['Now Playing','Library - a very long music collection','Coverflow','音楽フォルダー','Streaming','Settings']
                    if case==2: labels=['Playback','System']
                    if not full and case!=11:
                        image(a,dark if case==10 else bright,width,30,375-width,290)
                    elif not full: image(a,asset(build,'images/xx/default_album_home.png'),width,30,375-width,290)
                    selected=1 if case!=2 else 0
                    for i,value in enumerate(labels):
                        y=30+(0 if old else HOME_TOP)+i*HOME_ROW
                        if i==selected:
                            if old: selection(a,y,HOME_ROW,375 if full else width,palette)
                            else: a+=['-fill','white','-draw',f'circle {inc("HOME_DOT_X")+3},{y+HOME_ROW//2} {inc("HOME_DOT_X")+6},{y+HOME_ROW//2}']
                        x0=inc('HOME_CLASSIC_TEXT_X') if old else HOME_TEXT_X
                        label_width=width-x0-(inc('CHEVRON_W')-10 if old else HOME_LABEL_END if full else inc('HOME_SPLIT_LABEL_END'))
                        text(a,value,x0,y,label_width,HOME_ROW,color='#FFFFFF' if old or i==selected else '#AAAAAA')
                        if old and i==selected: image(a,asset(build,'images/xx/list_into.png'),width-inc('CHEVRON_W')+inc('HOME_FULL_CHEVRON_SHIFT' if full else 'HOME_CHEVRON_SHIFT'),y+(HOME_ROW-50)//2,50,50)
                elif case in (3,4,12):
                    nodes={n[2].get('name'):n for n in walk(decode((build/'ui/playing_page.bin').read_bytes()))}
                    art=dark if case==3 else (bright if case==12 else asset(build,'images/xx/default_album_flow.png'))
                    text(a,'Spotify' if case==4 else '3 of 12',16,30,180,40,16,'#AAAAAA')
                    # The real art mask is rounded in the payload; compose the same 12px radius.
                    cover=tmp/'cover.png'
                    render([art,'-resize','166x166!','(', '-size','166x166','xc:black','-fill','white',
                            '-draw','roundrectangle 0,0 165,165 12,12',')','-alpha','off','-compose','CopyOpacity','-composite'],cover)
                    image(a,cover,16,80,166,166)
                    for node,value in [('scrlabel_title','A long title - 夜の音楽'),('scrlabel_artist','Artist / アーティスト'),('label_ipod_album','Album / Collection')]:
                        n=nodes[node]; x,y,w,h=n[1]
                        text(a,value,x,y+70,w,h,int(n[2].get('style:normal:font_size',16)),n[2].get('style:normal:text_color','#FFFFFF'))
                    for node,icon in [('img_fav','play_unfav'),('img_more','play_more'),('img_playmode','play_order')]:
                        x,y,w,h=nodes[node][1]
                        y+=30 if node=='img_more' else 70
                        p=asset(build,'images/xx/'+icon+'.png'); iw,ih=png_header(p.read_bytes())[:2]
                        image(a,p,x+(w-iw)//2,y+(h-ih)//2,iw,ih)
                    if case==3: text(a,'•  ·  ·',168,252,70,12,12,'#AAAAAA')
                    bh=inc('NP_BAR_CLASSIC') if old else inc('NP_BAR_MINIMAL'); by=251 if case==4 else 270+(30-bh)//2
                    rect(a,21,by,333,bh,'#1C1C1C'); rect(a,21,by,132,bh,'#FFFFFF' if case in (4,12) else f'#{palette[2]:06x}')
                    text(a,'1:23',46,295,80,16,14,'#AAAAAA'); text(a,'-2:34',249,295,80,16,14,'#AAAAAA')
                elif case<8:
                    rows = [('Podcasts','local_podcasts'),('Games','local_games'),('Audiobooks','local_audiobooks'),('Videos','local_videos')]
                    if case==6: rows=[('Albums','list_folder'),('音楽 - 長いフォルダー名','list_folder'),('A very long track title','local_frequentplay'),('Live recordings','list_folder')]
                    if case==7: rows=[('Backlight','display_backlight'),('Theme: Classic','system_display'),('Accent: '+name,'system_display'),('Home: Full','playset_covermode')] if old else \
                        [('Backlight','display_backlight'),('Theme: Minimal','system_display'),('Home: Split','playset_covermode'),('Battery: Icon','system_powermanager')]
                    for i,(label,icon) in enumerate(rows):
                        y=30+i*72
                        if i==1: selection(a,y,72,375,palette)
                        p=build/(icon+'.png')
                        if not p.exists():
                            p=asset(build,'images/xx/'+icon+'.png')
                        if not old: p=recolour(p)
                        image(a,p,8 if case==5 else 16,y+(8 if case==5 else 14),52 if case==5 else 40,52 if case==5 else 40)
                        text(a,label,72,y,245,68,24,'#171717' if not old and i==1 else '#FFFFFF')
                        into=asset(build,'images/xx/list_into.png')
                        if case!=7 or i==0: image(a,into if old else recolour(into,inc('MINIMAL_CHEVRON')&255),317,y+9,50,50)
                elif 15 <= case < 27:
                    root=decode((build/'ui'/SETTINGS_SLIDERS[(case-15)//3]).read_bytes())
                    nodes={n[2].get('name'):n for n in walk(root)}
                    value=(0,50,100)[(case-15)%3]
                    text(a,str(value)+'%',100,45,175,40,32)
                    back=asset(build,'images/xx/plan_back.png'); front=asset(build,'images/xx/plan_front.png')
                    image(a,back,31,102,313,46)
                    if value:
                        width=313*value//100
                        a+=['(',front,'-crop',f'{width}x46+0+0','+repage',')','-gravity','NorthWest','-geometry','+31+102','-composite']
                    for n in nodes.values():
                        if n[0]!='image': continue
                        x,y,w,h=n[1]; y+=30
                        icon=n[2].get('image') or n[2].get('style:normal:bg_image')
                        if not icon: continue
                        if bg:=n[2].get('style:disable:bg_color'):
                            radius=int(n[2].get('style:disable:round_radius',0))
                            a+=['-fill',bg,'-draw',f'roundrectangle {x},{y} {x+w-1},{y+h-1} {radius},{radius}']
                        p=asset(build,'images/xx/'+icon+'.png')
                        if icon.startswith('drop_') or icon in ('add','dec'): p=tinted(p,icon,tint)
                        iw,ih=png_header(p.read_bytes())[:2]
                        image(a,p,x+(w-iw)//2,y+(h-ih)//2,iw,ih)
                elif case in (27,28):
                    rows=[('Boot to: Q2 Pod','system_powermanager')] if case==27 else []
                    rows += [('System Update','system_update'),('Reset Settings','system_reset'),('About','system_about')]
                    for i,(label,icon) in enumerate(rows):
                        y=38+i*68; p=asset(build,'images/xx/'+icon+'.png')
                        image(a,p if old else recolour(p),16,y+14,40,40)
                        text(a,label,72,y,245,68,24)
                elif case==29:
                    text(a,'Internet Radio',16,42,200,16,16)
                    p=asset(build,'images/xx/stream_radio.png')
                    image(a,p if old else recolour(p,luminance=True),16,80,166,166)
                    text(a,'Station name',194,125,165,28,22)
                    text(a,'Artist / Song',194,157,165,20,16,'#CCCCCC')
                    nodes={n[2].get('name'):n for n in walk(decode((build/'ui/playing_page.bin').read_bytes()))}
                    x,y,w,h=nodes['img_fav'][1]; p=asset(build,'images/xx/play_fav.png'); iw,ih=png_header(p.read_bytes())[:2]
                    image(a,p,x+(w-iw)//2,70+y+(h-ih)//2,iw,ih)
                elif case in (13,14):
                    if case==13:
                        text(a,'Parametric EQ',33,34,310,32,22)
                        rect(a,24,76,327,70,'#111111')
                        a+=['-stroke','#444444','-strokewidth','1','-draw','line 24,111 351,111',
                            '-stroke',f'#{palette[2]:06x}','-draw','path "M24,111 C110,111 130,82 187,98 S270,116 351,111"','+stroke']
                        rows=['Preamp              −3.0 dB','Band 1              100 Hz','Gain                  +2.0 dB']
                        top=150; height=44
                    else:
                        rows=['Albums / アルバム','A long concert recording.mp4','夜の写真.jpg','Books / Reading list']
                        top=38; height=68
                    for i,label in enumerate(rows):
                        y=top+i*height
                        if i==1: selection(a,y,height,375,palette)
                        text(a,label,33,y,309,height,20,'#171717' if not old and i==1 else '#FFFFFF')
                else:
                    page='dialog/statusbar_dialog.bin' if case==8 else 'dialog/confirminfo_dialog.bin'
                    root=decode((build/'ui'/page).read_bytes())
                    if case==9:
                        text(a,'Remove this playlist?',54,80,267,40,20)
                        x,y,w,h=root[3][0][1]
                        rect(a,x-2,y-2,w+4,h+4,'#FFFFFF'); rect(a,x,y,w,h,f'#{palette[0]:06x}')
                    def controls(n,ox=0,oy=0):
                        kind,(x,y,w,h),props,children=n; x+=ox; y+=oy
                        if kind=='slider':
                            rect(a,x,y+h//2-3,w,6,'#3A3A3A'); rect(a,x,y+h//2-3,w*2//5,6,'#FFFFFF')
                        if kind=='image':
                            icon=props.get('image') or props.get('style:normal:bg_image')
                            if not icon:
                                style=props.get('style','')
                                icon={'s_img_confirmcancel':'confirm_cancel','s_img_confirmok':'confirm_ok'}.get(style)
                            icon={'drop_wifi':'drop_wifiopen','drop_lowgain':'drop_lowgaindisable'}.get(icon,icon)
                            if icon:
                                p=asset(build,'images/xx/'+icon+'.png')
                                if icon.startswith(('confirm_','drop_')): p=tinted(p,icon,tint)
                                iw,ih=png_header(p.read_bytes())[:2]
                                image(a,p,x+(w-iw)//2,y+(h-ih)//2,iw,ih)
                        if kind=='label': text(a,props.get('text') or props.get('tr_text',''),x,y,w,h,16)
                        for c in children: controls(c,x,y)
                    root[1][:2]=[0,0]
                    controls(root)
                a+=['(', '-size','375x320','xc:black','-fill','white','-draw','roundrectangle 0,0 374,319 80,80',')',
                    '-alpha','off','-compose','CopyOpacity','-composite','-background','black','-alpha','remove','-compose','Over']
                p=tmp/f'{case}-{side}.png'; assert render(a,p)==(375,320)
                tile=tmp/f'tile-{case}-{side}.png'
                assert render(['(', '-size','375x34','xc:#202020',
                               '-font',font,'-pointsize','12','-fill','white','-gravity','NorthWest',
                               '-annotate','+5+8',f'{theme.title()} (composed) | '+title, ')', p, '-append'],tile)==(375,354)
                tiles.append(tile)
        args=[]
        for i in range(0,len(tiles),2): args+=['(',tiles[i],tiles[i+1],'+append',')']
        render(args+['-append'],out/(name.lower()+'.png'))
    # Album cards: native tile positions and cover/label geometry from album_children.
    # Include each column, the last row, scrolling, long names and a playing overlay.
    album_frames=[]
    for dynamic in (False, True):
        for offset, selected in ((0,0),(0,1),(0,2),(100,3),(100,4),(100,5)):
            a=['-size','375x320','xc:black']
            for i in range(6):
                x=14+118*(i%3); y=30+(210 if dynamic else 160)*(i//3)-offset
                pad=inc('TILE_INSET')+inc('TILE_CLEARANCE'); size=110-2*pad
                if i==selected:
                    inset=inc('TILE_INSET'); radius=inc('SEL_RADIUS')
                    a+=['-fill',f'#{inc("MINIMAL_FILL"):06x}','-draw',
                        f'roundrectangle {x+inset},{y+inset} {x+109-inset},{y+159-inset} {radius},{radius}']
                image(a,asset(after,'images/xx/default_album_big.png'),x+pad,y+pad,size,size)
                if i==selected:
                    image(a,str(asset(after,'images/xx/playing_gif_big.gif'))+'[0]',x+pad,y+pad,size,size)
                ink='#171717' if i==selected else '#FFFFFF'
                text(a,'A very long album name',x+pad,y+116-inc('TILE_INSET'),size,24 if dynamic else 20,20,ink)
                text(a,'Artist / Collection',x+pad,y+140-inc('TILE_INSET'),size,16,16,ink)
            rect(a,0,0,375,30,f'#{inc("BAR_COLOR"):06x}')
            text(a,f'{"Artist Albums" if dynamic else "Albums"} / scroll {offset}',16,5,340,20,16)
            frame=tmp/f'album-{dynamic}-{offset}-{selected}.png'
            assert render(a,frame)==(375,320)
            album_frames.append(frame)
    rows=[]
    for i in range(0,len(album_frames),3): rows+=['(',*album_frames[i:i+3],'+append',')']
    assert render(rows+['-append'],out/'album-grids.png')==(1125,1280)
    if cf_paths:
        before_cf, after_cf = cf_paths
        frames=[]
        for name in ('rest','quarter','half','three-quarter','one-album'):
            row=tmp/(name+'.png')
            a=['-size','750x238','xc:#202020','(',before_cf/(name+'.png'),after_cf/(name+'.png'),'+append',')',
               '-gravity','NorthWest','-geometry','+0+28','-composite']
            text(a,'Before renderer | '+name,5,0,365,28,12)
            text(a,'After renderer | '+name,380,0,365,28,12)
            assert render(a,row)==(750,238)
            frames.append(row)
        render(frames+['-append'],out/'coverflow-renderer.png')
    # Native-size status review: all four modes, both themes, crowded and critical states.
    battery_rows=[]
    for theme in themes:
        bg=f'#{inc("BAR_COLOR"):06x}'
        for state,level in [('normal',n) for n in (0,1,9,50,99,100)]+[('charging',50),('low',5),('codec',100)]:
            columns=[]
            for mode in range(4):
                color=f'#{inc("BATT_CHARGE_RGB") if state=="charging" else inc("BATT_LOW_RGB") if state=="low" else 0xffffff:06x}'
                a=['-size','375x50','xc:black']
                rect(a,0,0,375,30,bg)
                text(a,f'{theme} / {state} / {level}% / '+('Icon','Percent','Icon + Percent','Inside')[mode],5,30,365,20,11)
                # Materialize each glyph separately: ImageMagick reads inputs when render runs.
                def label(value,px,x,y,w,h,ink='white'):
                    glyph=tmp/f'status-{theme}-{state}-{level}-{mode}-{x}.png'
                    render(['-background','none','-fill',ink,'-font',font,'-pointsize',px,
                            'label:'+value,'-gravity','Center','-extent',f'{w}x{h}'],glyph)
                    a.extend([glyph,'-gravity','NorthWest','-geometry',f'+{x}+{y}','-composite'])
                    return imagemagick(glyph,'-format','%@','info:',data=b'').decode().strip()
                label('12:59 PM',16,142,0,90,30)
                image(a,asset(after,'images/xx/bar_play.png'),STATUS_MARGIN,7,12,16)
                effective=0 if state=='codec' else mode
                inside=effective==3
                bw,bh,nh=(inc(n) for n in (('BATT_INSIDE_W','BATT_INSIDE_H','BATT_NUB_H') if inside else ('BATT_BODY_W','BATT_BODY_H','BATT_NUB_H')))
                width=inc('BATT_PCT_W') if effective==1 else bw+inc('BATT_NUB_W')+(inc('BATT_GAP')+inc('BATT_PCT_W') if effective==2 else 0)
                x=375-STATUS_MARGIN-width; y=(31-bh)//2-(inc('BATT_PCT_DY') if effective==2 else 0)
                if effective!=1:
                    if inside: a += ['-fill',color,'-draw',f'roundrectangle {x},{y} {x+bw-1},{y+bh-1} 3,3']
                    else:
                        for dx,dy,w,h in ((1,0,bw-2,1),(1,bh-1,bw-2,1),(0,1,1,bh-2),(bw-1,1,1,bh-2)):
                            rect(a,x+dx,y+dy,w,h,color)
                        fill=(bw-4)*(100 if state=='charging' else level)//100
                        if fill: rect(a,x+2,y+2,fill,bh-4,color)
                    rect(a,x+bw,y+(bh-nh)//2,inc('BATT_NUB_W'),nh,color)
                if inside:
                    bounds=label(str(level),inc('BATT_INSIDE_PX'),x+2,y+inc('BATT_INSIDE_TEXT_DY'),bw-4,bh,'black')
                    gw,gh,gx,gy=map(int,re.fullmatch(r'(\d+)x(\d+)\+(\d+)\+(\d+)',bounds).groups())
                    assert 2+gx>=3 and 2+gx+gw<=bw-3 and gy+inc('BATT_INSIDE_TEXT_DY')>=3 and gy+inc('BATT_INSIDE_TEXT_DY')+gh<=bh-3, bounds
                elif effective in (1,2):
                    label(str(level)+'%',inc('BATT_PCT_PX'),375-STATUS_MARGIN-inc('BATT_PCT_W'),0,inc('BATT_PCT_W'),30,color)
                wifi=asset(after,'images/xx/bar_wifi4.png'); ww,wh=png_header(wifi.read_bytes())[:2]
                gap=inc('STATUS_GAP')
                image(a,wifi,x-gap-ww,(30-wh)//2,ww,wh)
                bt=asset(after,'images/xx/'+('bar_aptxhd' if state=='codec' else 'bar_btcon')+'.png'); tw,th=png_header(bt.read_bytes())[:2]
                image(a,bt,x-2*gap-ww-tw,(30-th)//2,tw,th)
                frame=tmp/f'status-{theme}-{state}-{level}-{mode}.png'
                assert render(a,frame)==(375,50)
                columns.append(frame)
            row=tmp/f'status-{theme}-{state}-{level}.png'
            render(columns+['+append'],row); battery_rows.append(row)
    assert render(battery_rows+['-append'],out/'status-bar.png')==(1500,50*len(battery_rows))
    (out/'provenance.json').write_text(json.dumps({
        'font_sha256':hashlib.sha256(font.read_bytes()).hexdigest(),
        'before_source_sha256':json.loads((before/'manifest.json').read_text())['source_sha256'],
        'after_source_sha256':json.loads((after/'manifest.json').read_text())['source_sha256'],
        'screen_size':[375,320], 'kind':'composed illustrations, not firmware captures',
        'artwork':'synthetic bright/dark gradients; missing art uses packaged placeholder or black',
        'themes':themes, 'cases':cases, 'renderer_size':[375,210] if cf_paths else None},indent=2)+'\n')
print('Composed sheets: native font and packaged assets; every screen is 375x320. These are not firmware captures.')
