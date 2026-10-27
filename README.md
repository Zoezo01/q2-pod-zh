# Q2 Pod 简体中文版（山灵 Q2）

山灵 Q2 的 iPod classic 风格固件补丁，**简体中文分支**。

这不是山灵官方固件，也不是上游的官方中文版，而是个人自用分支：把上游的固件补丁做成中文界面，并加了两个自己需要的功能。

![版本](https://img.shields.io/badge/%E7%89%88%E6%9C%AC-V150F-3D424B?style=flat-square)
![语言](https://img.shields.io/badge/%E7%95%8C%E9%9D%A2-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D77868?style=flat-square)
![许可](https://img.shields.io/badge/license-MIT-B99AC8?style=flat-square)

**直接刷机：[下载 `update.tar`](https://github.com/Zoezo01/q2-pod-zh/releases/latest)**（见 [Releases](https://github.com/Zoezo01/q2-pod-zh/releases)，附 SHA256 与刷机说明）

## 参考了哪两个项目

| 项目 | 是什么 | 本分支用了什么 |
|---|---|---|
| [**DiamondBond/q2-pod**](https://github.com/DiamondBond/q2-pod) | 固件补丁本体（MIT）：不重编固件，而是往山灵官方 V1.32 二进制里挂钩子，在 `0xb00000` 载荷区跑自己的 C 代码，换来 iPod classic 界面、Coverflow、PEQ、Spotify、Rockbox 等。 | **全部功能与绝大部分代码**。本分支的基线是它的 tag `1.5.0`（`d774d76`），上游历史完整保留在 git log 里。 |
| [**Ericwyn/q2-pod**](https://github.com/Ericwyn/q2-pod) | 同一项目的**简体中文汉化**分支（`main` 上是 1.3.8 + 汉化，`update-1.4.8` 上是 1.4.8 + 汉化）。 | **汉化的底子**：术语表、翻译风格、`caption()` 支持 UTF-8 码点的做法都来自这里，译法也基本沿用它的用词。上游升到 1.5.0 后界面结构变化很大，汉化是**按 1.5.0 重做的**，不是直接 cherry-pick。 |

## 相对上游 1.5.0 改了什么

### 1. 界面简体中文

- **自绘界面全中文**：主菜单、正在播放、Coverflow、歌词页、PEQ、流媒体（Spotify / Tidal / 网络电台）、书籍 / 视频 / 照片、年代浏览、评分、播放队列、随机与重复、游戏、应用商店、下载与存储管理、在线歌词与封面下载——`patch/*.c` 里的界面文字全部译成简体中文。
- **卡内应用页**也翻了：Soulseek、Deezer、File Browser 的加载与错误提示（`%s 未运行`、`%s 未响应`、`存储卡上没有 %s`、`停止 %s`）、配对二维码页（`使用手机` / `相机扫描` / `配对码`）。
- **游戏的横幅**（数独、贪吃蛇、旋涡、纸牌）：`开始新游戏？`、`中心键：是  返回键：否`、`游戏结束`、`新纪录！`、`你赢了！`、`继续` / `退出`。
- **1.4.9 / 1.5.0 新增的文案一并翻译**：浅色模式、滚轮快捷、视频文件夹视图、断点续播、关于页新位置、时区偏移、`网络电台`、`Tidal 下载`、`下载专辑歌词`、年代页的 `80 年代`、信息页的 `位置` / `未知`、12 小时制的 `上午` / `下午`。
- **术语**按 Apple《iPod classic 简体中文使用手册》：主菜单「音乐」，保留 Cover Flow，用「表演者」「有声读物」「评价」「重复播放」。品牌、音频单位、歌曲 / 艺术家 / 文件名、外部服务回复保持原文。
- **自绘文字支持 UTF-8**：`caption()` 把 UTF-8 解码成 Unicode 码点再交给 `draw_centred`，缓冲随上游扩到 32 码点。
- **字形校验**：`test/chinese.py` 要求界面用到的每个汉字在打包的 `fonts/default.ttf` 里都有字形（当前 463 个），卡内目录名（Podcasts、Audiobooks、Photos、Books、Videos）保持英文。

### 2. 构建脚本：不再把原厂中文改回英文

上游为了让界面变英文，构建时用 `english_ui()` 把每个 UI 资产里的原厂中文逐条换成英文。中文版要的是反过来的效果，所以 `tools/build.py` 增加：

```python
CHINESE_UI = True   # 资产按 patch_asset 的结果原样打包，只保留解码/编码往返校验
```

`ENGLISH` / `ENGLISH_IN` 两张表仍在，改成 `False` 就得到上游的英文界面。

同时 `tools/build.py` 支持直接输入山灵的 `update.tar`（不是官方 ZIP），用独立的 pinned SHA-256 校验，`manifest.json` 记 `input_kind: "tar"`；`tools/release.py` 拒绝把这类构建当作正式发布包。

### 3. 版本标记 `V150F`

上游英文版是 `V150I`，本分支是 `V150F`（`tools/build.py` 里 `VERSIONS['ipod']` 用 `C`、`D`、`E`、`F`…，上游用 `I`）。

原因：升级器（`update_firmware`，0x4f8440）只在"包内标记与设备已安装标记**完全相同**"时提示"已是最新版本"，并不比较版本高低。与上游英文版区分开，才能在一台刷过英文 1.5.0 的设备上直接升级到中文版；以后重新构建把字母往后挪一位就能再刷。

### 4. 新功能：无耳机不播放

```text
主菜单 → 设置 → 播放设置 → 无耳机不播放（默认开）
```

没插 3.5 mm / 4.4 mm 耳机、没连蓝牙耳机、也没接 USB DAC 时，按播放键或切歌键不会开始播放，只提示一句「未连接耳机」（灭屏时不提示）。Q2 没有内置扬声器，没耳机时的播放只是空放耗电，这个功能就是防口袋里误触。

- `patch/headset.c`：`silent_output()` 判断输出通路（`g_po_status`、`g_bal_status`、`mclGetOutputWay()`、蓝牙列表 `status==3`），`headset_guard_refuse()` 读取配置键 `HEADSETGUARD`。
- `patch/playback.c`、`patch/headset.c`：`ringnav_change()`、`ringnav_play_pause()`、`media_key()` 加守卫，`KEY_PLAY` / `KEY_FWD_BTN` / `KEY_BACK_BTN` 全部拦截。
- `patch/navigation.c`：新增枚举 `POD_HEADSET_GUARD`，设置行插在「左右声道互换」之后。

### 5. 新功能：状态栏显示「播放中 / 已暂停」

左上角的播放/暂停图标只有两种位图，暂停又可能来自屏幕上没人按下的操作（拔 3.5 mm 耳机时 `config_po_output` 会自己 `mclSetPause`），一帧画完就分辨不出"在放"还是"已停"。所以图标右边加文字说明：

- `patch/navigation.c`：`bar_word_sync()` 在状态变化时写 `label_playword`，与图标共用上游 `bar_repaint` 那个 0 ms 定时器强制整条状态栏重画；文字宽 48 px，EQ 标记（`label_eq`）出现时自动让位，不压中间时钟。
- 上游 1.5.0 的 `a783c29`（Fixes #116）已经修了"图标不跟随"本身，本分支在此基础上只加了文字。

### 6. 新功能：一键补全封面与歌词

```text
主菜单 → 设置 → 播放设置 → 补全封面与歌词
```

原厂只有「正在播放 → ⋯ → 动态获取封面与歌词」，一次一首歌、封面还要自己挑。这一行把整张卡扫一遍：

- **第一段抓歌词**：每张专辑逐首问 LRCLIB，找到带时间轴的版本就写成歌曲同目录的同名 `.lrc`（与「下载专辑歌词」同一套 `album_lyrics`），已有 `.lrc` 的歌跳过。
- **第二段抓封面**：文件夹里没有任何封面文件时，取 MusicBrainz 前四个发行组的 Cover Art Archive 前置封面（500 px），取不到再试 iTunes 的 600 px，存成 `cover.jpg`；已有 `cover.jpg` / `folder.jpg` 的跳过。跑完调用 `coverflow_refresh_art()`，下次进 Cover Flow 就重建。
- **第三段抓单曲**：没有专辑标签的歌（原厂归到 Unknown Album）没有所属文件夹可放封面，所以只逐首补歌词，按各自的标题 + 歌手问 LRCLIB；没写标题的用文件名去掉扩展名去搜。
- **进度就显示在这一行上**，计数写清了单位：`专辑歌词 12/174 张`（174 是专辑数）→ `专辑封面 3/174 张` → `单曲歌词 8/56 首`；再按一次变成「正在停止…」，已下的不会丢。
- **行尾的转动小点**（`◐ ◓ ◑ ◒`，每 0.3 秒一格）表示后台还在跑：专辑之间可能要等好几秒，数字不动时看它就知道没卡死。
- **后台跑**：`library_fill_tick` 挂在主循环的 `ringnav_sleep` 上，退出这个页面、去听歌、熄屏都照旧推进，回到页面看到的是当前进度，跑完弹一句「补全完成：歌词 N 首，封面 N 张」。
- **为什么天然能续**：全靠「缺什么补什么」，所以中断、重启、再按一次都从没补到的地方接着走。
- **不占线程**：原厂的歌曲记录只允许 UI 线程碰，所以按「UI 线程取一张专辑 → 工作线程联网」一次一张地推进（`library_fill_tick`，挂在 `ringnav_sleep`），界面不卡，取消也能在两张专辑之间落地。

需要 Wi-Fi。LRCLIB 与 MusicBrainz 都约 1 请求/秒，2000 首歌词约 35–40 分钟、300 张专辑的封面约 10–15 分钟，建议插着电跑。封面按「搜索到的第一张」自动选定，同名专辑（一堆 *Greatest Hits*）可能配错，删掉 `cover.jpg` 重来即可。

- `patch/onlinematch.c`：`library_fill_*`（枚举、三段任务、带单位的进度、转动的指示、取消），`lf_cover_save()` 复用 `mb_url()` / `caa()` / `itunes_url()`，`lf_song_take()` / `lf_song_collect()` 处理没有专辑标签的单曲。
- `patch/navigation.c`：导出 `ringnav_fill_albums()`（全库专辑 SQL，复用年代页的 `ERA_ALBUMS`）、`ringnav_album_tracks()`（单张专辑曲目）与 `ringnav_fill_songs()`（无专辑标签的歌），新增枚举 `POD_LIBRARY_FILL` 与设置行。

### 差异一览

| 项 | 上游 1.5.0 | 本分支 |
|---|---|---|
| 界面语言 | 英文 | 简体中文（`CHINESE_UI = True`） |
| 版本标记 | `V150I` | `V150F` |
| 无耳机时播放/切歌 | 照常开始播放 | 拒绝并提示，可在播放设置关闭 |
| 状态栏播放状态 | 只有图标 | 图标 +「播放中」/「已暂停」 |
| 补全封面与歌词 | 一次一首，封面上手挑 | 播放设置里一键扫全卡 |
| 汉化来源 | — | Ericwyn/q2-pod 的汉化，按 1.5.0 结构重做 |

其余全部保持上游行为：浅色模式、滚轮快捷手势、封面/歌词/信息页、Spotify、Tidal、网络电台、AirPods、评分、交叉淡化、断点续播、80% 充电上限、待机时限、应用商店与网络更新。

## 仓库里有什么

- **全部源码**：`patch/`（钩子与载荷 C 代码）、`tools/`（构建与打包）、`test/`（约 1.2 万行行为测试）、`docs/`。
- **可以刷的固件**：`update.tar` 在 [Releases](https://github.com/Zoezo01/q2-pod-zh/releases) 里，不放进 git（`.gitignore` 挡着，50 MB 的东西不该进版本库）。
- **不含山灵官方固件**（`xImage`、`rootfs.squashfs`、官方 `update.tar`）：这些是山灵的版权文件，本仓库不转发；想自己编译，要先去山灵官网下官方 V1.32。
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
| `Q2 Firmware V1.32.zip` | `154c17822d09be001be35c03d2d3488424dee195221790bd70864480d55b0f00` |
| 该 ZIP 内的 `update.tar` | `0a910790ef7902b0fe7de7d0f9f26cdac3f2c0909368c6c90778a67ee3b5dab1` |

```bash
.venv/bin/python tools/build.py '/path/Q2 Firmware V1.32.zip' --out build-cn --ipod
# 或
.venv/bin/python tools/build.py /path/update.tar --out build-cn --ipod
```

`--out` 目录必须不存在。产出约 50.6 MiB、载荷见构建日志（上限 512 KB），标记 `V150F`；加 `--dev` 会得到递增的开发标记（`V001c`…），附带需要密钥的远程调试服务。

## 测试

```bash
.venv/bin/python test/chinese.py build-cn     # 汉字字形覆盖（463 个）
.venv/bin/python test/patch.py build-cn       # 全量行为场景（约 45 分钟）
.venv/bin/python test/peq.py
.venv/bin/python test/coverflow.py
.venv/bin/python test/playback.py build-cn
.venv/bin/python test/build.py --build build-cn
```

## 刷机与回滚

把 [Releases](https://github.com/Zoezo01/q2-pod-zh/releases) 里的 `update.tar`（或你自己编译出来的那个）放在 TF 卡根目录，通过系统设置的 TF 卡升级安装；刷机期间不要断电拔卡。文件名必须就叫 `update.tar`，不要解压、不要改名。

- 本版标记 `V150F`，与官方 `V1.32`、上游英文 `V150I` 都不同，可以互相刷。
- 同一个标记只能刷一次：设备已在本版时再刷同样的包会被拒绝。重刷请换 `--dev` 或用新版本标记。
- 回滚用山灵官方 V1.32 固件。注意别把官方原包当成自建包——官方包约 54 MB、`firmware_v20.info` 第二行是 `V1.32`，自建包约 51 MB。
- 网络更新（应用商店）下载的仍是 DiamondBond 的**英文**固件，所以更新行标题写成「上游英文固件更新」。装了会失去汉化，要保留中文请用 TF 卡刷本分支的包。

## 已知问题

分两类：一类是会真碰到的，一类是看着像问题、其实应该那样。

### 会碰到的

1. **别在系统设置里改界面语言。** 原厂页面用的是山灵自己的语言资源，而我们自绘的菜单、弹窗、状态栏是写死的简体中文。切成英文之后，原厂页面变英文、我们画的还是中文，两套混在一屏。保持「简体中文」用就好。
2. **卡内应用的菜单行是英文。** Soulseek、Deezer、File Browser 是卡里的独立程序（`q2app` 生态），行名、歌单名、错误说明由应用自己以 JSON 给出，固件只负责显示。本固件画的页面外壳（`加载中…`、`重试`、`%s 未运行`、`停止 %s`、二维码页的提示与 `配对码`）已经是中文。
3. **应用商店/网络更新装的是上游英文固件。** 那一行标题因此写着「上游英文固件更新」。装了会失去汉化；要中文请用 TF 卡刷本分支的包，网络更新只当检查版本用。
4. **原厂页面的用词不等于我们的术语表。** 原厂页面是山灵自己翻的中文，我们没有逐条重写（那等于改厂商文案），所以系统设置里个别词和新增菜单的译法可能不完全一致。

### 看着像问题，其实不是

- 品牌名、音频单位（`MB`、`kbps`、`kHz`、`bit`）、歌曲/专辑/文件名、外部服务返回的内容保持原文——译了反而错。
- 卡内目录名（Podcasts、Audiobooks、Photos、Books、Videos）必须保持英文：`test/chinese.py` 专门断言这一点，改了就读不到文件。
- 卡内应用自己的文件名、歌单名、`Spotify`/`Tidal` 这类服务名不翻译。
- 状态栏 12 小时制写成「6:14 下午」：实测宽 76.6 px，比布局预留的 70 px 多 6.6 px，左右各多占 3.3 px，仍留 0.7 px 间隙，不会压到电池图标。

## 许可与致谢

- [DiamondBond/q2-pod](https://github.com/DiamondBond/q2-pod)：MIT，`LICENSE` 原样保留，Copyright (c) 2026 Diamond Bond。这个项目的钩子框架、偏移表、测试体系全部是他们的工作，本仓库只是在此基础上做中文与两个自用功能。
- [Ericwyn/q2-pod](https://github.com/Ericwyn/q2-pod)：简体中文汉化，感谢把术语和翻译风格先趟了一遍。
- 本分支的改动同样以 MIT 发布。

文档：[汉化范围与构建细节](docs/chinese-local.md) · [上游更新日志](docs/changelog.md) · [内部机制](docs/internals.md) · [English version (upstream)](README.en.md)
