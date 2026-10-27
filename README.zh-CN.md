# Q2 Pod 简体中文版（山灵 Q2）

山灵 Q2 的 iPod classic 风格固件补丁，**简体中文分支**。

这不是山灵官方固件，也不是上游的官方中文版，而是个人自用分支：把上游的固件补丁做成中文界面，并加了两个自己需要的功能。

![版本](https://img.shields.io/badge/基线-upstream%201.5.0-3D424B?style=flat-square)
![语言](https://img.shields.io/badge/%E7%95%8C%E9%9D%A2-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D77868?style=flat-square)
![许可](https://img.shields.io/badge/license-MIT-B99AC8?style=flat-square)

## 参考了哪两个项目

| 项目 | 是什么 | 本分支用了什么 |
|---|---|---|
| [**DiamondBond/q2-pod**](https://github.com/DiamondBond/q2-pod) | 固件补丁本体（MIT）：不重编固件，而是往山灵官方 V1.32 二进制里挂钩子，在 `0xb00000` 载荷区跑自己的 C 代码，换来 iPod classic 界面、Coverflow、PEQ、Spotify、Rockbox 等。 | **全部功能与绝大部分代码**。本分支的基线是它的 tag `1.5.0`（`d774d76`），上游历史完整保留在 git log 里。 |
| [**Ericwyn/q2-pod**](https://github.com/Ericwyn/q2-pod) | 同一项目的**简体中文汉化**分支，两条：`main` 是 1.3.8 + 汉化（`bb68635`），`update-1.4.8` 是 1.4.8 + 汉化（`d0b3f81`）。 | **汉化的来源**：术语表、翻译风格、`caption()` 支持 UTF-8 码点的做法。最早从 `main`（1.3.8）的汉化起步；上游升到 1.5.0 后按新结构重做；**第二轮又照他的 `update-1.4.8` 逐条补齐**我们第一轮漏翻的 50 条（卡内应用页、游戏横幅、`下载专辑歌词`、`80 年代` 等）。 |

## 相对上游 1.5.0 改了什么

### 1. 界面简体中文

- 汉化重放到 1.5.0：把 Ericwyn 的汉化（1.3.8 起点）按 1.5.0 的新写法逐文件重建（`list_toggle` 开关行只给标题、`row_dim` 标志位、`rate_saved` 只报失败、`auto_text` 双采样率格式等），解决 12 处合并冲突；之后又照他的 `update-1.4.8` 分支补齐漏翻的 50 条。
- 补译 1.4.9 / 1.5.0 新增文案：浅色模式、滚轮快捷、视频文件夹视图、断点续播、关于页新位置、时区偏移、Wheel QS 等。
- 术语按 Apple《iPod classic 简体中文使用手册》：主菜单「音乐」，保留 Cover Flow，用「表演者」「有声读物」「评价」「重复播放」。品牌、音频单位、歌曲/艺术家/文件名、外部服务回复保持原文。
- 自绘文字支持 UTF-8：`caption()` 把 UTF-8 解码成 Unicode 码点再交给 `draw_centred`，缓冲随上游扩到 32 码点。
- 第二轮补齐（对照 Ericwyn 的 `update-1.4.8` 分支逐条核对）：卡内应用页（`patch/addon.c` 的 `Soulseek 未运行`、`未响应`、`存储卡上没有 %s`、二维码页提示、`配对码`）、四个游戏的横幅（`开始新游戏？`、`游戏结束`、`新纪录！`、`你赢了！`、`继续`/`退出`）、`下载专辑歌词`、`网络电台`、`Tidal 下载`、信息页的 `位置`/`未知`、时钟的 `上午`/`下午`、年代页的 `80 年代`。这些原本是我们第一轮漏掉的英文。
- 汉字字形校验：`test/chinese.py` 要求界面用到的每个汉字在打包的 `fonts/default.ttf` 里都有字形（当前 442 个），卡内目录名（Podcasts、Audiobooks、Photos、Books、Videos）保持英文。

### 2. 构建脚本：不再把原厂中文改回英文

上游为了让界面变英文，构建时用 `english_ui()` 把每个 UI 资产里的原厂中文逐条换成英文。中文版要的是反过来的效果，所以 `tools/build.py` 增加：

```python
CHINESE_UI = True   # 资产按 patch_asset 的结果原样打包，只保留解码/编码往返校验
```

`ENGLISH` / `ENGLISH_IN` 两张表仍在，改成 `False` 就得到上游的英文界面。

同时 `tools/build.py` 支持直接输入山灵的 `update.tar`（不是官方 ZIP），用独立的 pinned SHA-256 校验，`manifest.json` 记 `input_kind: "tar"`；`tools/release.py` 拒绝把这类构建当作正式发布包。

### 3. 版本标记 `V150C` / `V150D`

上游英文版是 `V150I`，本分支是 `V150C`（第一批）、`V150D`（补全 add-on 与游戏文案之后）（`tools/build.py` 里 `VERSIONS['ipod']` 由 `I` 改成 `C`/`D`）。

原因：升级器（`update_firmware`，0x4f8440）只在"包内标记与设备已安装标记**完全相同**"时提示"已是最新版本"，并不比较版本高低。标记与上游英文版区分开，才能在一台刷过英文 1.5.0 的设备上直接升级到中文版。

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
- 上游 1.5.0 的 `a783c29`（Fixes #116）已经修了"图标不跟随"本身，本分支在此基础上只加了文字，没有保留自己那套 100 ms 轮询。

### 差异一览

| 项 | 上游 1.5.0 | 本分支 |
|---|---|---|
| 界面语言 | 英文 | 简体中文（`CHINESE_UI = True`） |
| 版本标记 | `V150I` | `V150C` / `V150D` |
| 无耳机时播放/切歌 | 照常开始播放 | 拒绝并提示，可在播放设置关闭 |
| 状态栏播放状态 | 只有图标 | 图标 +「播放中」/「已暂停」 |
| 汉化来源 | — | Ericwyn/q2-pod（1.3.8 起点 + `update-1.4.8` 补全），按 1.5.0 结构重做 |

其余全部保持上游行为：浅色模式、滚轮快捷手势、封面/歌词/信息页、Spotify、Tidal、网络电台、AirPods、评分、交叉淡化、断点续播、80% 充电上限、待机时限、应用商店与网络更新。

## 仓库里没有的东西

- **不含山灵官方固件**（`xImage`、`rootfs.squashfs`、官方 `update.tar` 一个都没有）。构建需要你自己下载官方 V1.32。
- **不含构建产物**。`update.tar` 里是山灵的二进制，本仓库只发布源码与文档。
- 不含卡内应用（Soulseek、Deezer、File Browser 等由 `q2app` 自带），它们的界面文字不由本固件翻译。

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

`--out` 目录必须不存在。不加 `--dev` 得到正式标记（`V150D`），加上会得到递增的开发标记（`V001c`…，附带需要密钥的远程调试服务）。

## 测试

```bash
.venv/bin/python test/chinese.py build-cn     # 汉字字形覆盖
.venv/bin/python test/patch.py build-cn       # 全量行为场景（约 45 分钟）
.venv/bin/python test/peq.py
.venv/bin/python test/coverflow.py
.venv/bin/python test/playback.py build-cn
.venv/bin/python test/build.py --build build-cn
```

## 刷机与回滚

把构建出的 `update.tar` 放在 TF 卡根目录，通过系统设置的 TF 卡升级安装；刷机期间不要断电拔卡。

- 本版标记 `V150D`（上一版 `V150C`），与官方 `V1.32`、上游英文 `V150I` 都不同，可以互相刷。
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
- [Ericwyn/q2-pod](https://github.com/Ericwyn/q2-pod)：简体中文汉化的起点，感谢把术语和翻译风格先趟了一遍。
- 本分支的改动同样以 MIT 发布。

文档：[汉化范围与构建细节](docs/chinese-local.md) · [上游更新日志](docs/changelog.md) · [内部机制](docs/internals.md)
