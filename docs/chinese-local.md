# 本地简体中文版

这是基于本仓库 Q2 Pod 1.5.2、用于 Shanling Q2 的个人自用固定简体中文版，不是山灵官方固件，也不是上游发布的正式中文版本。

## 翻译范围

新增菜单、设置选项和本地提示固定为简体中文：主菜单的音乐/Cover Flow/设置入口、评价、播放队列、随机/重复播放、均衡器、交叉淡化、转轮灵敏度、流媒体设置、Spotify、Tidal、网络电台、书籍/视频与照片、年代浏览、游戏，以及 1.4.x 新增的应用商店、下载管理、存储管理、在线歌词与封面下载等页面。

本轮以 Apple 的 [iPod classic 2009 简体中文使用手册](https://cdsassets.apple.com/live/6GJYWVAV/user/locale/zh-cn/ma1195_ipod_classic_160gb_user_guide.pdf) 为术语参考：主菜单入口使用“音乐”，保留“Cover Flow”，使用“表演者”“有声读物”“评价”“重复播放”。Q2 独有功能采用相近风格，不伪称为原版 iPod 功能；评价仍使用本项目的爱心图标，不改成星星。

设置选项统一为“主菜单：分栏/全屏”“低功耗模式：打开/关闭”“主题：简约/经典”“电池显示：图标与百分比”“外观：直角/圆角”“强调色：石墨/绯红/潮汐/香槟/钴蓝/橘橙/梅紫/象牙白/青绿”等。未重写原厂全部中文语言资源，因此原厂页面里可能仍有与新增菜单不同的历史用词。

开关型设置行沿用 1.4.x 的写法：`list_toggle` 只给行标题（如“单声道音频”“左右声道互换”“音量防误触”“联动设备音量”），开/关由行尾的原厂开关图标表示，不再写成“单声道音频：打开”。

原厂页面继续使用原厂语言机制。建议在设备的系统设置中选择简体中文；切换到英文或其他语言时，本次新增文案仍然显示中文。品牌、音频单位、歌曲/艺术家/专辑/文件名、外部服务回复及部分底层错误保持原文；没有修改存储卡目录名、配置键或协议字段。卡内应用（Soulseek、Deezer、File Browser 等由 `q2app` 自行提供的界面）的文字由应用本身给出，不在此固件内翻译。

界面文字有两个来源：载荷 C 代码自绘的（`patch/*.c`），以及原厂 UI 资产里的（`home_page.bin`、确认框、键盘、原厂页面）。原厂资产本身是中文，上游为了让界面变成英文，在构建时用 `english_ui` 把每个资产里的中文逐条换成英文。本版要的是中文界面，所以 `tools/build.py` 增加了 `CHINESE_UI = True`：资产按 `patch_asset` 的结果原样打包，不再做英文化改写，只保留它的解码/编码往返校验；`ENGLISH`/`ENGLISH_IN` 两张表仍在，Home 的中文标签也已补上对应英文项，改成 `False` 就得到上游的英文界面。这样资产里的原厂中文、本版新增的中文标签和载荷中文是同一套，不会出现半中文半英文。

自绘 caption 原先仅按 ASCII 字节绘制，现已支持 UTF-8 解码为 Unicode 码点再交给 `draw_centred`，缓冲随上游扩到 32 码点，并保留非法编码替换。自绘音量文字使用中文码点。

## 本分支新增的三个功能

除了汉化，本分支在上游 1.5.2 之上加了三个自用功能，都在 `patch/` 里，与汉化改动互不影响：

- **无耳机不播放**（`patch/headset.c`，设置行在 播放设置 → 无耳机不播放，默认开）：没有 3.5 mm / 4.4 mm 耳机、没连蓝牙耳机、也没接 USB DAC 时，按播放键或切歌键不开始播放，只提示「未连接耳机」。拦截点在 `ringnav_play_pause()`、`media_key()` 与 `playback.c` 的 `ringnav_change()`，配置键 `HEADSETGUARD`。
- **状态栏「播放中 / 已暂停」**（`patch/navigation.c`，`bar_word_sync()` + `label_playword`）：图标只有两种位图，暂停又可能来自屏幕外（拔耳机时 `config_po_output` 自己 `mclSetPause`），所以旁边加文字说明；与图标共用上游 `bar_repaint` 的 0 ms 定时器，EQ 标记出现时让位，不压中间时钟。
- **一键补全封面与歌词**（`patch/onlinematch.c` 的 `library_fill_*`，设置行在 播放设置 → 补全封面与歌词）：三段推进——专辑歌词、专辑封面、没有专辑标签的单曲歌词，进度直接写在这一行（`专辑歌词 12/174 张`、`单曲歌词 8/56 首`），行尾 `◐ ◓ ◑ ◒` 每 0.3 秒转一格表示后台还在跑；`library_fill_tick` 挂在 `ringnav_sleep` 上，退出页面、听歌、熄屏都照旧推进。

改动清单与刷机说明见仓库首页 [README.md](../README.md)。

## 上游基线

汉化最早重放到 upstream 正式 `1.5.0`（tag `1.5.0`，提交 `d774d76`），随后并入 `1.5.1` 与 `1.5.2`（merge 提交 `9adbb9b`）。汉化沿用 Ericwyn/q2-pod 的译法（`bb68635` 与 `d0b3f81` 两个提交），因 1.4.8 → 1.5.0 之间界面结构变化较大，按上游的新写法逐文件重做，并译出 1.4.9 / 1.5.0 新增文案，含卡内应用页与游戏横幅（浅色模式、滚轮快捷、视频文件夹视图、断点续播、关于页新位置、时区偏移等）。保留上游全部逻辑改动：应用商店、Spotify 下载管理、年代浏览、Tidal 2000 首上限与标签、网络更新的“上游英文固件更新”标记、2038 年问题修复、强调色 Green、熄屏省电与蓝牙音量开关等；1.5.1 的浅色模式 `自动`、外观页的 `圆角` 开关、Games 点触打开、行与胶囊底几何修正、超长有声书的拒绝提示，以及 1.5.2 的 MP3 编码器延迟裁剪（`patch/mp3_gapless.c`）都已并入并译好。上游在 1.3.8 之后新增的界面文案本轮一并补译；同时新增的 `list_toggle` 开关行、`row_dim` 标志位、`rate_saved` 只报失败、`auto_text` 双语格式等结构差异按上游写法处理，不沿用旧版实现。

## 构建

需要通过项目校验的原厂 V1.32 固件，两种输入都可用：

- 官方 `Q2 Firmware V1.32.zip`，SHA-256 为 `154c17822d09be001be35c03d2d3488424dee195221790bd70864480d55b0f00`
- 或该 ZIP 内的 `update.tar`（山灵 V1.32 的 TF 卡升级包），SHA-256 为 `0a910790ef7902b0fe7de7d0f9f26cdac3f2c0909368c6c90778a67ee3b5dab1`

`update.tar` 入口同样是审计过的基线：校验包内 `firmware_v20.info` 必须是 `Shanling Q2` / `V1.32`，成员必须仍是那四个。用它构建的成品 `manifest.json` 记 `input_kind: "tar"`，`tools/release.py` 会拒绝把这类构建当作发布包，正式发布仍只认原厂 ZIP。

工具链为 LLVM 14、SquashFS 4.6.1（含 LZO 支持）与 ImageMagick；版本低于项目的 squashfs-tools 4.5 不能代替，Debian 12 仓库里的 4.5.1 必须换成 4.6.1。使用 Python 3.12（既有 MIPS 测试有 3.12 的 f-string 语法；Debian 12 自带的 3.11 会报 `f-string: expecting '}'`）。

以下是实际用来构建出成品的环境（Debian 12 / arm64 均可，clang 跨到 MIPS 目标即可）：

```bash
apt-get install -y clang-14 lld-14 llvm-14 imagemagick build-essential \
                   zlib1g-dev liblzo2-dev liblzma-dev liblz4-dev libzstd-dev
for p in clang clang++ llvm-objcopy llvm-objdump ld.lld llvm-strip; do ln -sf /usr/bin/$p-14 /usr/local/bin/$p; done

# squashfs-tools 4.6.1（Debian 12 只有 4.5.1）
curl -L https://github.com/plougher/squashfs-tools/archive/refs/tags/4.6.1.tar.gz | tar xz
make -C squashfs-tools-4.6.1/squashfs-tools -j4 \
     GZIP_SUPPORT=1 LZO_SUPPORT=1 XZ_SUPPORT=1 LZ4_SUPPORT=1 ZSTD_SUPPORT=1 && \
make -C squashfs-tools-4.6.1/squashfs-tools install

uv python install 3.12 && uv venv --python 3.12 .venv && \
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/build.py /path/update.tar --out build-cn --ipod
# 或
.venv/bin/python tools/build.py '/path/Q2 Firmware V1.32.zip' --out build-cn --ipod
```

输出目录必须不存在；重复构建请换一个新目录。不带 `--dev` 的固件没有新增 q2remote 远程调试服务，适合日常使用。

## 验证与限制

可运行的中文专项检查与主要测试：

```bash
.venv/bin/python test/chinese.py build-cn
.venv/bin/python test/peq.py
.venv/bin/python test/coverflow.py
.venv/bin/python test/playback.py build-cn
.venv/bin/python test/patch.py build-cn
.venv/bin/python test/build.py --build build-cn
```

`test/chinese.py` 会扫描 `patch/*.c` 与 `tools/ipod.py` 里的全部汉字，要求它们在固件实际打包的 `fonts/default.ttf` 中都有字形，并抽查主菜单、确认框和载荷里的中文标签；卡内目录名（Podcasts、Audiobooks、Photos、Books、Videos）必须仍是英文。

整包构建在 Debian 12 / arm64 上完成：LLVM 14 的 MIPS 目标、自编的 squashfs-tools 4.6.1、ImageMagick，以及 Python 3.12。产出 `update.tar` 约 50.6 MiB、标记 `V152G`，载荷 466,376 B（上限 512 KB，余约 56 KB）。`test/chinese.py`（466 个字形）与 `test/patch.py` 的全量行为测试都跑在构建产物上，测试脚本的断言已随汉化逐条改中文。

本次构建前在 Windows 上做的先行验证仍然有效：用 MinGW-w64（gcc 16.2）配合 host shim 跑过能独立运行的那部分 C 测试——`books.c`、`games.c`、`visualizer.c` 与 3EQ（`peq.c` + `peq_ui.c` 的 shim）全部通过，其中 books 与 visualizer 覆盖 UTF-8 文本与自绘 caption。`patch/peq_ui.c` 在相同编译选项下与上游 HEAD 的诊断完全一致，即汉化没有引入新的编译问题。

C 侧还做了括号/字面量配平检查，`tools/*.py`、`test/*.py` 均通过 `py_compile`。

## 安装和回滚

先备份卡内资料，设备充足电。将安装包中的 `update.tar` 放入存储卡根目录，安全弹出后通过系统设置的 TF 卡升级安装。不要在刷机期间断电或拔卡。

本版的版本标记是 `V152G`，与上游英文 1.5.2 的 `V152I` 不同（`tools/build.py` 里 `VERSIONS['ipod']` 用 `C`…`G`，上游用 `I`）：`152` 是所基于的上游版本，`G` 是中文版第 7 次构建。设备只有在**已安装标记与包内标记完全相同**时才会提示“已是最新版本”（`update_firmware`，见 [Building](building.md)），所以本包可以直接刷在官方 V1.32、上游英文 1.5.2 或上一版中文 `V150F` 之上。

同一个标记只能刷一次：以后重新构建的中文包如果还是 `V152G`，已在本版上的设备同样会被拒绝。这时用 `--dev` 构建（每次生成递增的标记，如 `V001c`、`V002c`），或把 `tools/build.py` 里 `VERSIONS['ipod']` 的字母往后换一个（G→H）。开发包会额外安装需要密钥的远程调试服务，不建议长期停留。不要手工修改升级包或校验值。

刷机前务必确认拿的是**本次构建的包**（`build-152a/update.tar`，约 50.6 MiB），而不是山灵官方 V1.32 原包（约 54 MB，`firmware_v20.info` 第二行是 `V1.32`）——把原包刷回原厂固件，固件升级会因标记相同而拒绝。

需要回滚时，按山灵官方说明使用 Q2 官方 V1.32 固件从 TF 卡升级。本地中文修改不影响这一恢复途径。卡内文件通常不因正常升级而格式化，但设置、评分、续播记录等内部数据不保证被原厂固件保留或识别。

## 网络更新的重要提示

上游的应用商店/网络更新仍从 DiamondBond/q2-pod 下载原版英文固件，不会获取本地中文 Fork。为避免误会，固件更新行标题明确显示“上游英文固件更新”。点击并完成安装会失去本地汉化；如需保留中文，请使用本 Fork 构建的固件通过 TF 卡升级。没有悄悄将下载地址改为缺少 Release 产物的 Fork，也没有弱化 TLS、版本或写入检查。
