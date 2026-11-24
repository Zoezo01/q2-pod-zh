#!/usr/bin/env python3
"""Publish this fork's own GitHub Release so Network Update can find it.

Upstream's tools/release.py is its ZIP-input, English-changelog pipeline: it
runs twenty test suites and builds a PDF guide, none of which this fork ships.
This one only does what patch/netupdate.c actually needs --

  * a latest, non-draft release whose tag is exactly RELEASE_TAG (the bare
    VERSION for a line's first build, then VERSION point releases, 1.8.3.2 …),
    named Q2 Pod 简体中文固件 <VERSION>（<TAG>）like every release before it,
  * one asset named update.tar, with GitHub's own size and sha256 digest,
    beside INSTALL-zh.md and a SHA256.txt of the tar's own hashes,
  * release notes carrying every changelog-zh section since the last
    *published* line (LAST_RELEASED below), plus the flashing and checksum
    sections the older releases carry.

Usage:
    GITHUB_TOKEN=... python3 tools/release_zh.py ~/q2-build-v183d
    python3 tools/release_zh.py ~/q2-build-v183d --dry-run   # verify + show notes only

The token is read from the environment, never from a file, argument or URL.
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import tarfile
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build import ROOT, REPO, VERSION, TAG, RELEASE_TAG, ZIP_SHA, TAR_SHA, DEMO_SHA, sha  # noqa: E402

ASSET = 'update.tar'
API = 'https://api.github.com'
UPLOADS = 'https://uploads.github.com'
MEMBERS = ['firmware_v20.info', 'recovery-update', 'recovery-update/xImage',
           'recovery-update/rootfs.squashfs']
LAST_RELEASED = '1.8.9.4'  # the newest line with a published Release; notes carry the sections after it


def need(ok, message):
    if not ok:
        raise SystemExit(message)


def changelog_sections():
    """The changelog-zh sections published after LAST_RELEASED, newest first."""
    lines = (ROOT / 'docs/changelog-zh.md').read_text(encoding='utf-8').splitlines()
    sections, current = [], None
    for line in lines:
        if line.startswith('## '):
            if VERSION == LAST_RELEASED:
                if current is not None:  # same line: only its newest section, not the ones below
                    break
            elif line.split()[1].rstrip('（').startswith(LAST_RELEASED):
                break  # a new line: the published line's own section is not repeated
            current = [line, []]
            sections.append(current)
        elif current is not None and line.strip() != '---':
            current[1].append(line)
    out = []
    for head, body in sections:
        text = '\n'.join(body).strip()
        need(text, f'{head} has no release notes')
        out.append(f'{head}\n\n{text}\n')
    need(out, f'docs/changelog-zh.md has no section after {LAST_RELEASED}')
    return out


def notes():
    """The release body: the line's heading, what carries over, every section after
    LAST_RELEASED, and the flashing and checksum sections the older releases carry."""
    sections = changelog_sections()
    return f'''# Q2 Pod 简体中文固件 {VERSION}（{TAG}）

这一版跟上上游的 **{VERSION}**，底座仍是山灵官方 **V1.36** 固件，界面仍是简体中文。自上一版公开发布（{LAST_RELEASED}）以来的更新日志都在下面。

## 装上去能看到什么

- **界面简体中文**，iPod classic 的用语（「音乐」「表演者」「重复播放」）。
- **网络更新装的是本仓库的中文固件**，不会再被英文版覆盖掉汉化。
- 自制功能照旧：一键补全封面与歌词、酷狗封面优先、状态栏「正在播放 / 播放暂停」。完整记录见仓库的 [更新日志](https://github.com/Zoezo01/q2-pod-zh/blob/main/docs/changelog-zh.md)。

''' + '\n---\n\n'.join(sections) + f'''
## 刷机

1. 把 `update.tar` 拷到 TF 卡根目录（FAT32 或 exFAT），文件名不要改。
2. 关机，卡插进 Q2 Pod。
3. 按住返回键不放，插上充电器（或长按播放键）进入升级，松开按键，等进度走完自动重启。

包内 `firmware_v20.info` 第二行是 `{TAG}`。`xImage` 与官方 V1.36 原包逐字节相同，只换了 rootfs。

## 校验

`SHA256.txt` 记了 `update.tar` 的 SHA-256，以及包内 `xImage`、`rootfs.squashfs` 的 md5，刷之前可以对照。
'''


INSTALL_ZH = f'''# Q2 Pod 简体中文固件 {VERSION}（{TAG}）

包内是山灵 Q2 Pod **{VERSION}**（底座仍为官方 **V1.36** 固件）+ 简体中文汉化，另加自制功能。

> 跟上上游 **{VERSION}**（上一版公开发布是 {LAST_RELEASED}）。字母每换一条上游版本线从 A 重起：**{TAG} 比前面各版都新**，已刷 V190A 的机器也能直接 TF 卡刷。

## 这一版带来的（上游 1.9.0 → 1.9.1）

- **卡主题**：卡上 `Q2Pod/theme.txt` 可设定强调色、页面底色、正在播放与锁屏的布局与字号、首页各行、电池样式；重启生效，连续两次启动失败会自动关掉。
- **语言应用**：应用商店装 Languages 后，系统设置里可选 34 种语言（含阿拉伯语等从右往左的语言）；本包界面仍是简体中文。
- **Cover Flow**：位置条可以直接拖动跳专辑；新增「隐藏重复专辑」「隐藏重复歌曲」「记录失败封面」三个开关；极简 / 现代主题下专辑名两侧有短横线，歌曲行显示表演者。
- **正在播放**：两个新可视化「脉冲星」「轮廓」；跳歌后标题、表演者、爱心与封面立刻更新。
- **视频与阅读器变成卡带应用**：从应用商店安装，用卡上原来的 `Videos` / `Books` 文件夹；Wi-Fi 传输新增 WebM、MPEG、WMV、FLV、TS、M2TS、3GP、OGV。
- **EQ 高级模式**：在曲线上直接拖动频段调增益。
- **播放**：熄屏时长按下一曲 / 上一曲快进快退；记忆播放在关机或升级后回到原位置。
- **卡带应用**：不再以 root 运行，更安全；能全屏播放、调音量、设 EQ、随 Wi-Fi 启停、在状态栏显示图标、在系统设置 → 网络里有自己的一行。
- **修复**：待机时给存储卡最多 2 秒完成写入；曲库扫描保留音轨号与碟号；Wi-Fi 传输不再受别的网站请求影响；按键音更跟手；熄屏待机更省电。
- **中文版**：1.9.0 / 1.9.1 新增界面全部中文（卡主题提示、月份、上午 / 下午、Tidal 下载等）。
- **体积**：上游给字体瘦身（去掉设备不读的字形名与 hinting），字形数不变，包比上一版小约 810 KB。载荷窗口跟上游改为 `0xbf0000`（960 KB），余约 159 KB。

## 我们自己的东西

界面简体中文、一键补全封面与歌词、酷狗封面优先、状态栏「正在播放 / 播放暂停」、确认框按钮中文。

## 刷机

1. 把 `update.tar` 拷到 TF 卡根目录（FAT32 或 exFAT）。
2. 关机，卡插进 Q2 Pod。
3. 按住返回键不放，插上充电器（或长按播放键）进入升级，松开按键，等进度走完自动重启。

包内 `firmware_v20.info` 第二行是 `{TAG}`。`xImage` 与官方 V1.36 原包逐字节相同（内核未动）。

## 校验

- `update.tar` SHA256：见同目录 `SHA256.txt`
- 包内 `xImage` 与 `rootfs.squashfs` 的 md5 记录在 `SHA256.txt`
'''


def checksum_sheet(data, members):
    """members: [(basename, bytes)] in display order; the md5 lines as stock packs them."""
    lines = [ASSET, sha(data), '', '包内（recovery-update/）']
    for name, blob in members:
        lines += [name, hashlib.md5(blob).hexdigest()]
    lines += ['', f'firmware_v20.info 第二行：{TAG}',
              'xImage 与官方 V1.36 原包逐字节相同（内核未动）']
    return '\n'.join(lines) + '\n'


def api(method, path, payload=None, raw=None, host=API):
    token = os.environ.get('GITHUB_TOKEN')
    need(token, 'Set GITHUB_TOKEN to a token that can create releases in ' + REPO)
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'q2-pod-zh-release'}
    body = None
    if raw is not None:
        headers['Content-Type'] = 'application/octet-stream'
        body = raw
    elif payload is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(host + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            out = response.read()
            return json.loads(out) if out else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and method == 'GET':
            return None
        raise SystemExit(f'GitHub {method} {path}: {exc.code} {exc.read().decode()[:400]}')
    except urllib.error.URLError as exc:
        raise SystemExit(f'GitHub {method} {path}: {exc.reason}')


def publish(directory, data, text, extra):
    existing = api('GET', f'/repos/{REPO}/releases/tags/{RELEASE_TAG}')
    need(not existing, f'Tag {RELEASE_TAG} already has a release: delete or edit it by hand first')
    name = f'Q2 Pod 简体中文固件 {VERSION}（{TAG}）'
    release = api('POST', f'/repos/{REPO}/releases',
                  dict(tag_name=RELEASE_TAG, name=name, body=text, draft=True,
                       prerelease=False, make_latest='false'))
    rid = release['id']
    print(f'Draft {RELEASE_TAG} created: {release["html_url"]}')
    for asset_name, blob in [(ASSET, data), *extra.items()]:
        asset = api('POST', f'/repos/{REPO}/releases/{rid}/assets?name={asset_name}',
                    raw=blob, host=UPLOADS)
        need(asset.get('name') == asset_name, f'Upload did not return {asset_name}')
    listed = api('GET', f'/repos/{REPO}/releases/{rid}/assets')
    found = {a['name']: a for a in listed}
    need(found[ASSET].get('size') == len(data) and found[ASSET].get('digest') == 'sha256:' + sha(data),
         "GitHub's record of update.tar has no matching size and digest: Network Update would refuse it")
    for asset_name in extra:
        need(asset_name in found, f'{asset_name} missing from the release after upload')
    api('PATCH', f'/repos/{REPO}/releases/{rid}', dict(draft=False, make_latest='true'))
    print(f'Published {RELEASE_TAG}: https://github.com/{REPO}/releases/tag/{RELEASE_TAG}')
    print(f'  {ASSET}: {len(data)} bytes, sha256 {sha(data)}')
    for asset_name, blob in extra.items():
        print(f'  {asset_name}: {len(blob)} bytes')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('build', type=pathlib.Path, help='tools/build.py --out directory')
    ap.add_argument('--dry-run', action='store_true', help='verify the build and show the notes, publish nothing')
    a = ap.parse_args()
    directory = a.build.resolve()
    need(directory.is_dir(), f'No such build directory: {directory}')
    m = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    need(m.get('version') == TAG, f"Wrong build identity: {m.get('version')} != {TAG}")
    need(m.get('update_repo') == REPO,
         f"Network Update fetches {m.get('update_repo')}'s releases, not {REPO}'s")
    need(m.get('input_sha256') in (TAR_SHA, ZIP_SHA), 'Wrong stock input hash')
    need(m.get('stock_demo_sha256') == DEMO_SHA, 'Wrong stock demo hash')
    data = (directory / ASSET).read_bytes()
    need(sha(data) == m['update_sha256'], 'update.tar: hash mismatch')
    with tarfile.open(directory / ASSET) as t:
        need(t.getnames() == MEMBERS, 'Unexpected update members')
        info = t.extractfile('firmware_v20.info').read().decode().splitlines()
        need(info[:2] == ['Shanling Q2', TAG] and len(info) == 4, 'Wrong update identity')
        members = []
        for line, name in zip(info[2:], MEMBERS[2:]):
            blob = t.extractfile(name).read()
            need(line.split() == [hashlib.md5(blob).hexdigest(), name], f'{name}: md5 mismatch')
            members.append((name.rpartition('/')[2], blob))
    text = notes()
    extra = {'INSTALL-zh.md': INSTALL_ZH.encode('utf-8'),
             'SHA256.txt': checksum_sheet(data, members).encode('utf-8')}
    print(f'Verified {directory / ASSET}: {TAG}, {REPO}, {len(data)} bytes, sha256 {sha(data)}')
    if a.dry_run:
        print('--- release notes ---\n' + text)
        print('--- SHA256.txt ---\n' + extra['SHA256.txt'].decode('utf-8'))
        return
    publish(directory, data, text, extra)


if __name__ == '__main__':
    main()
