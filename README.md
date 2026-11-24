# Q2 Pod 简体中文版（山灵 Q2）

把山灵 Q2 刷成 iPod classic 的样子，界面全中文。

这不是山灵官方固件，也不是上游的官方中文版，是自己用的一个分支：在上游的固件补丁之上做中文界面，另外加了三个自己需要的功能。

![版本](https://img.shields.io/badge/%E7%89%88%E6%9C%AC-V190A-3D424B?style=flat-square)
![语言](https://img.shields.io/badge/%E7%95%8C%E9%9D%A2-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D77868?style=flat-square)
![许可](https://img.shields.io/badge/license-GPL--3.0-B99AC8?style=flat-square)

**直接刷机：[下载 `update.tar`](https://github.com/Zoezo01/q2-pod-zh/releases/latest)**（见 [Releases](https://github.com/Zoezo01/q2-pod-zh/releases)，附 SHA256 与刷机说明）｜[更新日志](docs/changelog-zh.md)

## 参考了哪两个项目

| 项目 | 是什么 | 本分支用了什么 |
|---|---|---|
| [**DiamondBond/q2-pod**](https://github.com/DiamondBond/q2-pod) | 固件补丁本体（1.7.2 起 GPL-3.0，之前是 MIT）：不重编固件，而是往山灵官方 V1.36 二进制里挂钩子，在 `0xb00000` 载荷区跑自己的 C 代码，换来 iPod classic 界面、Coverflow、PEQ、Spotify、Rockbox 等。 | **全部功能与绝大部分代码**。上游每发一版，本分支就把对应 tag 以一次 merge 提交并入，上游历史完整保留在 git log 里。 |
| [**Ericwyn/q2-pod**](https://github.com/Ericwyn/q2-pod) | 同一项目的**简体中文汉化**分支。 | **汉化的底子**：术语表、翻译风格、`caption()` 支持 UTF-8 码点的做法都来自这里，译法也基本沿用它的用词。 |

## 相对上游改了什么

### 1. 界面简体中文

- **自绘界面全中文**：主菜单、正在播放、Cover Flow、歌词页、PEQ、流媒体（Spotify / Tidal / 网络电台）、书籍 / 视频 / 照片、年代浏览、评分、播放队列、随机与重复、游戏、应用商店、下载与存储管理、在线歌词与封面下载——`patch/*.c` 里的界面文字全部译成简体中文。
- **卡内应用页也翻了**：Soulseek、Deezer、File Browser 的加载与错误提示（`%s 未运行`、`%s 未响应`、`存储卡上没有 %s`、`停止 %s`）、配对二维码页（`使用手机` / `相机扫描` / `配对码`）。
- **游戏的横幅**（数独、贪吃蛇、旋涡、纸牌）：`开始新游戏？`、`中心键：是  返回键：否`、`游戏结束`、`新纪录！`、`你赢了！`、`继续` / `退出`。
- **上游每版新增的文案跟着翻**：最近的 1.8.5–1.8.9 里，队列菜单、时钟的「上午 / 下午」与「保存」、快速设置的「滚轮：音量 / 亮度」、USB DAC 充电三档、App Store 的「应用 / 游戏」与「安装 / 更新 / 已安装」、「检查更新」的检查时间、Classic Modern 的扁平页面，都已经中文。
- **术语**按 Apple《iPod classic 简体中文使用手册》：主菜单「音乐」，保留 Cover Flow，用「表演者」「有声读物」「评价」「重复播放」。品牌、音频单位、歌曲 / 艺术家 / 文件名、外部服务回复保持原文。
- **自绘文字支持 UTF-8**：`caption()` 与 `text_at()` 都按 UTF-8 码点解码后再画（后者供 App Store 的中文状态词使用），不会再按字节画成 `å®‰è£…`。
- **字形校验**：`test/chinese.py` 要求界面用到的每个汉字在打包的 `fonts/default.ttf` 里都有字形（当前 508 个），卡内目录名（Podcasts、Audiobooks、Photos、Books、Videos）保持英文。

### 2. 构建脚本：不再把原厂中文改回英文

上游为了做出英文界面，构建时用 `english_ui()` 把每个 UI 资产里的原厂中文逐条换成英文。我们正好相反，所以 `tools/build.py` 里加了这么一行：

```python
CHINESE_UI = True   # 资产按 patch_asset 的结果原样打包，只保留解码/编码往返校验
```

`ENGLISH` / `ENGLISH_IN` 两张表仍在，改成 `False` 就得到上游的英文界面。

`tools/build.py` 也能直接吃山灵官方的 `update.tar`，不必先解出那个 ZIP。这类输入走另一条 pinned SHA-256 校验，`manifest.json` 里记 `input_kind: "tar"`。上游的 `tools/release.py` 是给 ZIP 输入 + 英文日志的正式发布流水线用的，本分支不用它；发 Release 用 `tools/release_zh.py`（见「网络更新」一节）。

### 3. 版本标记 `V190A`
>>>>>>> 1.9.1

上游英文版 1.9.0 叫 `V1900`，这个分支当前叫 `V190A`。前半截 `190` 是上游版本，末位字母是中文版在自己这条版本线上的发布序号：每换一条上游版本线（1.6.6 → 1.7.2 → 1.7.6 → 1.8.0 → 1.8.2 → 1.8.3 → 1.8.4 → 1.8.5 → 1.8.6 → 1.8.7 → 1.8.8 → 1.8.9 → 1.9.0）字母从 `A` 重新起，同一条线上顺次往下排。前七条线分别收在 `V180F`、`V182C`、`V183F`、`V184B`、`V187B`、`V188A` 和 `V189D`。（1.6.x 起上游只构建一个版本，所以 `VERSIONS` 表没了；1.8.0 起上游的标记改用四位数字。）

为什么非要多一个字母：固件升级（`update_firmware`，0x4f8440）判断"是不是最新"，只看包里的标记和设备上已装的标记**一模一样**，不比较版本高低。标记跟英文版错开，一台刷过英文 1.6.2 的机器才能直接升到中文版；以后重新构建，把字母往后挪一位就能再刷一次。

### 4. 无耳机不播放
>>>>>>> 1.9.1

原厂的 **Headphones Required**（1.8.9 起改名 **Require Output (Beta)**）就是这个功能：没插耳机、没连蓝牙耳机或 USB DAC 时，任何方式都开始不了播放，只提示一句。我们早期自己实现过一版（`patch/headset.c` 的守卫，并默认写开），V180A 起改回原厂：原厂在每个真正会出声的入口都会问 `check_forbid_play_no_headset`，提示也走原厂自己的中文，上游改实现时我们不用跟。

- 设置位置：主菜单 → 设置 → 播放设置 → 需要输出（Beta）（旧版叫「无耳机不播放」）。
- 默认值跟原厂走。我们不再向 `Q2POD / PLAYSET` 写 `WITHOUTPHONE` 的种子（1.8.9 合并时删掉了那段），想默认拦截就自己打开一次，设置会保存。

### 5. 新功能：状态栏显示「正在播放 / 播放暂停」

左上角的播放/暂停图标只有两种位图，暂停又可能来自屏幕上没人按下的操作（拔耳机时 `config_po_output` 自己 `mclSetPause`），一帧画完就分辨不出「在放」还是「已停」。所以图标右边加文字说明：

- `patch/navigation.c`：`bar_word_sync()` 在状态变化时写 `label_playword`，与图标共用上游 `bar_repaint` 那个 0 ms 定时器强制整条状态栏重画；文字固定 16 px，和时钟同高。
- **简约 / 现代**：页面名和状态词共用这一格，互斥——有页面名时显示页面名，没有页面名（主菜单、正在播放页）时才显示「正在播放 / 播放暂停」。长名字按左边的图标和右边的时钟、EQ 留白裁剪，不会压到电池。
- **经典主题**：图标挪到状态栏最左边，页面名跟在它后面（标题框右移 22 px、宽度相应减 22）；有页面名时只显示名字，没有页面名时才显示这四个字，正在播放页原来那行英文 `Now Playing` 也一并去掉。
- 中文页面名按 UTF-8 解码成宽字符（`wide_home_title`），否则「设置」会画成 `è®¾ç½®`。

### 6. 新功能：一键补全封面与歌词

```text
主菜单 → 设置 → 播放设置 → 补全封面与歌词
```

原厂只有「正在播放 → ⋯ → 动态获取封面与歌词」，一次一首歌、封面还要自己挑。这一行把整张卡扫一遍：

- **第一段抓歌词**：每张专辑逐首问 LRCLIB，找到带时间轴的版本就写成歌曲同目录的同名 `.lrc`（与「下载专辑歌词」同一套 `album_lyrics`），已有 `.lrc` 的歌跳过。
- **第二段抓封面**：文件夹里没有任何封面文件时，先问酷狗——就是原厂「动态获取封面」用的那套接口，中文专辑基本都在它库里，取 480 px 的图；酷狗没有才依次试 MusicBrainz 前四个发行组的 Cover Art Archive（500 px）和 iTunes（600 px），存成 `cover.jpg`；已有 `cover.jpg` / `folder.jpg` 的跳过。跑完调用 `coverflow_refresh_art()`，下次进 Cover Flow 就重建。
- **第三段抓单曲**：没有专辑标签的歌（原厂归到 Unknown Album）没有所属文件夹可放封面，所以只逐首补歌词，按各自的标题 + 歌手问 LRCLIB；没写标题的用文件名去掉扩展名去搜。
- **进度就显示在这一行上**，计数写清了单位：`专辑歌词 12/174 张`（174 是专辑数）→ `专辑封面 3/174 张` → `单曲歌词 8/56 首`；再按一次变成「正在停止…」，已下的不会丢。
- **行尾的转动小点**（`◐ ◓ ◑ ◒`，每 0.3 秒一格）表示后台还在跑：专辑之间可能要等好几秒，数字不动时看它就知道没卡死。
- **后台跑**：`library_fill_tick` 挂在主循环的 `ringnav_sleep` 上，退出这个页面、去听歌、熄屏都照旧推进，回到页面看到的是当前进度，跑完弹一句「补全完成：歌词 N 首，封面 N 张」。
- **为什么天然能续**：全靠「缺什么补什么」，所以中断、重启、再按一次都从没补到的地方接着走。
- **不占线程**：原厂的歌曲记录只允许 UI 线程碰，所以按「UI 线程取一张专辑 → 工作线程联网」一次一张地推进（`library_fill_tick`，挂在 `ringnav_sleep`），界面不卡，取消也能在两张专辑之间落地。

需要 Wi-Fi。2000 首歌词约 35–40 分钟（LRCLIB 约 1 请求/秒）；封面大多一次就在酷狗命中，300 张专辑通常十几分钟以内。建议插着电跑。封面按「搜到的第一张」自动选定，同名专辑（一堆 *Greatest Hits*）可能配错，删掉 `cover.jpg` 重来即可。

「正在播放 → ⋯ → 动态获取封面」也改成酷狗优先了：原厂的酷狗、网易、百度搜索结果排在最前面，我们补的 MusicBrainz 与 iTunes 排在后面当备选。改动只是把这一句里的两个调用调了个顺序。

- `patch/onlinematch.c`：`library_fill_*`（枚举、三段任务、带单位的进度、转动的指示、取消），`lf_cover_save()` 先问 `kg_cover_save()`（酷狗搜索 → 专辑信息 → 480 px 图），再复用 `mb_url()` / `caa()` / `itunes_url()`，`lf_song_take()` / `lf_song_collect()` 处理没有专辑标签的单曲。
- `patch/navigation.c`：导出 `ringnav_fill_albums()`（全库专辑 SQL，复用年代页的 `ERA_ALBUMS`）、`ringnav_album_tracks()`（单张专辑曲目）与 `ringnav_fill_songs()`（无专辑标签的歌），新增枚举 `POD_LIBRARY_FILL` 与设置行。

### 7. 载荷窗口从 512 KB 扩到 1 MB

上游的载荷（`patch/*.c` 编译结果）装在 `0xb00000`，紧跟着 `SCRATCH` 是状态区。1.6.3 时窗口只有 512 KB，编译出 523,800 B——离 524,288 的硬上限只剩 488 字节，再合并一版必然撞线（`tools/build.py` 里 `check(len(payload) < SCRATCH-BASE)` 会直接抛异常，包都出不来）。

上游自己也一路在挪这个地址：`0xb80000` → `0xb90000` → `0xbc0000`，1.8.4 挪到 `0xbd0000`（832 KB）。1.8.9 合并进来后载荷长到 847,032 B，只剩 4,936 B，所以我们不再跟着上游，自己往前挪一档到 **`0xc00000`**，窗口 1 MB（1,048,576 B）：
>>>>>>> 1.9.1

- `patch/link.ld`：`.scratch 0xc00000`
- `tools/build.py`：`SCRATCH = 0xc00000`

扩容前做过实测：`/proc/self/maps` 显示载荷上方下一处映射在 `0x68da0000`（字体文件），中间大段空闲，heap 在载荷下方、动态库都在 `0x75c00000` 以上；状态区 192 KB 紧跟在 1 MB 之后，段尾到 `0xc30000`，仍在同一片空闲里。
>>>>>>> 1.9.1

**余量**：1.8.9 这一版编译出约 847 KB，扩到 1 MB 后余约 197 KB。再往上到 1.5 MB 之前都还有空间，但每次上移都要先实测确认新位置没被占。

### 差异一览

| 项 | 上游 1.8.9 | 本分支 |
|---|---|---|
| 界面语言 | 英文 | 简体中文（`CHINESE_UI = True`） |
| 版本标记 | `V1900` | `V190A` |
| 无耳机时开始播放 | 官方 Require Output (Beta) | 沿用官方（不再替用户写开） |
| 状态栏播放状态 | 只有图标 | 图标 +「正在播放」/「播放暂停」 |
| 补全封面与歌词 | 一次一首，封面上手挑 | 播放设置里一键扫全卡 |
| 汉化来源 | — | Ericwyn/q2-pod 的汉化，按 1.6.2 结构重做 |

## 仓库里有什么

- **全部源码**：`patch/`（钩子与载荷 C 代码）、`tools/`（构建与打包）、`test/`（约 1.2 万行行为测试）、`docs/`。
- **可以刷的固件**：`update.tar` 在 [Releases](https://github.com/Zoezo01/q2-pod-zh/releases) 里，不放进 git（`.gitignore` 挡着，50 MB 的东西不该进版本库）。
- **不含山灵官方固件**（`xImage`、`rootfs.squashfs`、官方 `update.tar`）：这些是山灵的版权文件，本仓库不转发；想自己编译，要先去山灵官网下官方 V1.36。
- **不含卡内应用**（Soulseek、Deezer、File Browser 等由 `q2app` 自带），它们自己画的行名与状态文字不由本固件翻译。

## 构建

需要一个能跑 `mksquashfs` 的 Linux（Debian 12 / arm64 实测），工具链 **LLVM 14**、**squashfs-tools 4.6.1**、ImageMagick，**Python 3.12**（既有 MIPS 测试用了 3.12 的 f-string 语法，Debian 12 自带的 3.11 会报 `f-string: expecting '}'`）。

```bash
apt-get install -y clang-14 lld-14 llvm-14 imagemagick build-essential \
                   zlib1g-dev liblzo2-dev liblzma-dev liblz4-dev libzstd-dev
for p in clang clang++ llvm-objcopy llvm-objdump llvm-nm llvm-strip; do
  ln -sf /usr/bin/$p-14 /usr/local/bin/$p
done

# Debian 12 只有 squashfs-tools 4.5.1，必须换 4.6.1
curl -L https://github.com/plougher/squashfs-tools/archive/refs/tags/4.6.1.tar.gz | tar xz
make -C squashfs-tools-4.6.1/squashfs-tools -j4 \
     GZIP_SUPPORT=1 LZO_SUPPORT=1 XZ_SUPPORT=1 LZ4_SUPPORT=1 ZSTD_SUPPORT=1
make -C squashfs-tools-4.6.1/squashfs-tools install

uv python install 3.12 && uv venv --python 3.12 .venv
uv pip install -r requirements.txt
```

输入可以是官方 ZIP 或其内部的 `update.tar`，两者都要过 SHA-256：

| 输入 | SHA-256 |
|---|---|
| `Q2 Firmware V1.36.zip` | `4e298cab6c12d86cfc55565645f7e3ae11d3fb44677a93ca13991e7184ecec4e` |
| 该 ZIP 内的 `update.tar`（或官网直接给的 tar） | `7f57884420e324ac3263571d96a35f04bbef79f35dac34162655c5822bdaa684` |

```bash
.venv/bin/python tools/build.py '/path/Q2 Firmware V1.36.zip' --out build-cn
# 或
.venv/bin/python tools/build.py /path/update.tar --out build-cn
```

`--out` 目录必须不存在。产出一份约 51.6 MiB 的 `update.tar`，标记 `V190A`，载荷大小看构建日志（正文区从 V189B 起是 `0xc00000`，上限 1 MB）。加 `--dev` 会得到递增的开发标记（`V001c`…），附带一个需要密钥的远程调试服务。

## 测试

```bash
.venv/bin/python test/chinese.py build-cn     # 汉字字形覆盖（475 个）
.venv/bin/python test/patch.py build-cn       # 全量行为场景（约 45 分钟）
.venv/bin/python test/peq.py
.venv/bin/python test/coverflow.py
.venv/bin/python test/playback.py build-cn
.venv/bin/python test/build.py --build build-cn
```

## 刷机与回滚

把 [Releases](https://github.com/Zoezo01/q2-pod-zh/releases) 里的 `update.tar`（或你自己编译出来的那个）放在 TF 卡根目录，通过系统设置的 TF 卡升级安装；刷机期间不要断电拔卡。文件名必须就叫 `update.tar`，不要解压、不要改名。

- 本版标记 `V190A`，和官方 `V1.36`、上游英文 `V1900`、已刷过的中文 `V189D`（以及 `V189C`、`V189B`、`V189A`、`V188A`、`V187B`、`V184B`、`V183F`、`V182C`、`V180F`）都不一样，所以从这些版本都能直接刷上来。
- 同一个标记只能刷一次：设备已在本版时再刷同样的包会被拒绝。**代码改过就要把末位字母递进**（A→B→C→D…），否则设备认不出这是新包；临时重刷请用 `--dev`。
- 回滚用山灵官方 V1.36 固件。注意别把官方原包当成自建包——官方包约 54 MB、`firmware_v20.info` 第二行是 `V1.36`，自建包约 51 MB。
- 网络更新已经指向本仓库，装的是本分支的**中文**固件，不会失去汉化。发布条件见下面的「网络更新」一节；没发过 Release 前会显示「此版本暂无更新」。

## 网络更新（发 Release）

1.8.3 起，设备连着 Wi-Fi 时会每天查一次本仓库的 `releases/latest`：发现比当前 `Q2POD_TAG`（`tools/build.py` 的 `RELEASE_TAG`）更新的 tag，主页弹一次提示，系统设置里的固件更新行变成「升级到 x.y.z」。点进去可以断点续传下载，下完按 GitHub 记录的 size 与 SHA-256 校验，再交给原厂的 TF 卡升级流程。电量低于 16% 且没插线、或存储卡空间不够时会先拦下来。不想每天查，把同一页的「每日提醒」关掉即可。

要让它在自己的仓库里工作，**发布必须满足**：

| 项 | 值 | 为什么 |
|---|---|---|
| tag | `tools/build.py` 的 `RELEASE_TAG`：这条线首发（A）是裸 `1.9.0`，之后每个小版本加一位点位（B→`1.9.0.2`、C→`1.9.0.3`…），本版是 `1.9.0` | `strcmp(tag, Q2POD_TAG)` 字符串比较：`1.9.0.2` 比 `1.9.0` 大，所以同一条上游版本线内的 190B、190C 也能提示升级；点位只有一位，字母用到 I |
| asset | 文件名必须正好是 `update.tar` | `patch/netupdate.c` 的 `NU_ASSET` |
| release | 必须是 **latest**、非草稿、非 prerelease | 设备只读 `/releases/latest` 的 302 跳转 |
| 下载地址 | `Zoezo01/q2-pod-zh` | `tools/build.py` 的 `REPO`，同时编译成 `Q2POD_REPO`；`release.py` 也会校验 manifest 里的 `update_repo` |
| 校验值 | GitHub 自动为上传的 asset 生成 `digest: sha256:…` | 设备用它核对下载，损坏就拒绝覆盖卡上的 `update.tar` |

发一版的顺序：改 `VERSION` 与 `LETTER`（换上游版本线时 `LETTER` 退回 `A`；同线再改代码就递进字母，`RELEASE_TAG` 跟着加点位）→ 写 `docs/changelog-zh.md` → 服务器上 `tools/build.py --out` 构建 → 把 `update.tar` 传回 → 用 `tools/release_zh.py <构建目录>` 校验并发布（需要环境变量 `GITHUB_TOKEN`）。脚本会先核对标记、`update_repo`、`firmware_v20.info` 第二行和 SHA-256，再建 Release、上传 asset、核对 GitHub 记录的 size/digest，最后才取消草稿。也可以完全按上表在 GitHub 网页上手工发，效果一样。

**注意**：`Q2POD_TAG` 用的是 `RELEASE_TAG`（由 `VERSION` 加点位组成，本版 `1.9.0`），而刷机标记用的是 `TAG`（`V190A`）。两者是两套机制：前者决定「网络更新要不要提示新版本」，后者决定「设备肯不肯重刷这个包」。发新版时两边都要推进。

## 已知问题

分两类：一类是会真碰到的，一类是看着像问题、其实应该那样。

### 会碰到的

1. **别在系统设置里改界面语言。** 原厂页面用的是山灵自己的语言资源，而我们自绘的菜单、弹窗、状态栏是写死的简体中文。切成英文之后，原厂页面变英文、我们画的还是中文，两套混在一屏。保持「简体中文」用就好。
2. **卡内应用的菜单行是英文。** Soulseek、Deezer、File Browser 是卡里的独立程序（`q2app` 生态），行名、歌单名、错误说明由应用自己以 JSON 给出，固件只负责显示。本固件画的页面外壳（`加载中…`、`重试`、`%s 未运行`、`停止 %s`、二维码页的提示与 `配对码`）已经是中文。
3. **网络更新依赖本仓库的 GitHub Release。** 没建 Release、tag 不等于 `tools/build.py` 的 `RELEASE_TAG`、asset 不叫 `update.tar`、或者发布被标成草稿，设备都会认为「此版本暂无更新」。应用商店的卡应用列表仍从上游 `DiamondBond/q2-pod` 的 `sources.list` 拉（卡应用与英文版相同），卡应用本身还是各自作者发的。
4. **原厂页面的用词不等于我们的术语表。** 原厂页面是山灵自己翻的中文，我们没有逐条重写（那等于改厂商文案），所以系统设置里个别词和新增菜单的译法可能不完全一致。

### 看着像问题，其实不是

- 品牌名、音频单位（`MB`、`kbps`、`kHz`、`bit`）、歌曲/专辑/文件名、外部服务返回的内容保持原文——译了反而错。
- 卡内目录名（Podcasts、Audiobooks、Photos、Books、Videos）必须保持英文：`test/chinese.py` 专门断言这一点，改了就读不到文件。
- 卡内应用自己的文件名、歌单名、`Spotify`/`Tidal` 这类服务名不翻译。
- 状态栏 12 小时制写成「6:14 下午」：实测宽 76.6 px，比布局预留的 70 px 多 6.6 px，左右各多占 3.3 px，仍留 0.7 px 间隙，不会压到电池图标。

## 许可与致谢

- [DiamondBond/q2-pod](https://github.com/DiamondBond/q2-pod)：1.7.2 起 GPL-3.0（之前的版本是 MIT），`LICENSE` 原样保留，Copyright (c) 2026 Diamond Bond。这个项目的钩子框架、偏移表、测试体系全部是他们的工作，本仓库只是在此基础上做中文与两个自用功能。
- [Ericwyn/q2-pod](https://github.com/Ericwyn/q2-pod)：简体中文汉化，感谢把术语和翻译风格先趟了一遍。
- 本分支的改动同样以 GPL-3.0 发布。

文档：[更新日志（中文）](docs/changelog-zh.md) · [汉化范围与构建细节](docs/chinese-local.md) · [上游更新日志](docs/changelog.md) · [内部机制](docs/internals.md) · [English version (upstream)](README.en.md)
