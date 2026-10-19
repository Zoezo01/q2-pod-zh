# 本地简体中文版

这是基于本仓库 Q2 Pod 1.4.8、用于 Shanling Q2 的个人自用固定简体中文版，不是山灵官方固件，也不是上游发布的正式中文版本。

## 翻译范围

新增菜单、设置选项和本地提示固定为简体中文：主菜单的音乐/Cover Flow/设置入口、评价、播放队列、随机/重复播放、均衡器、交叉淡化、转轮灵敏度、流媒体设置、Spotify、Tidal、网络电台、书籍/视频与照片、年代浏览、游戏，以及 1.4.x 新增的应用商店、下载管理、存储管理、在线歌词与封面下载等页面。

本轮以 Apple 的 [iPod classic 2009 简体中文使用手册](https://cdsassets.apple.com/live/6GJYWVAV/user/locale/zh-cn/ma1195_ipod_classic_160gb_user_guide.pdf) 为术语参考：主菜单入口使用“音乐”，保留“Cover Flow”，使用“表演者”“有声读物”“评价”“重复播放”。Q2 独有功能采用相近风格，不伪称为原版 iPod 功能；评价仍使用本项目的爱心图标，不改成星星。

设置选项统一为“主菜单：分栏/全屏”“低功耗模式：打开/关闭”“主题：简约/经典”“电池显示：图标与百分比”“外观：直角/圆角”“强调色：石墨/绯红/潮汐/香槟/钴蓝/橘橙/梅紫/象牙白/青绿”等。未重写原厂全部中文语言资源，因此原厂页面里可能仍有与新增菜单不同的历史用词。

开关型设置行沿用 1.4.x 的写法：`list_toggle` 只给行标题（如“单声道音频”“左右声道互换”“音量防误触”“联动设备音量”），开/关由行尾的原厂开关图标表示，不再写成“单声道音频：打开”。

原厂页面继续使用原厂语言机制。建议在设备的系统设置中选择简体中文；切换到英文或其他语言时，本次新增文案仍然显示中文。品牌、音频单位、歌曲/艺术家/专辑/文件名、外部服务回复及部分底层错误保持原文；没有修改存储卡目录名、配置键或协议字段。卡内应用（Soulseek、Deezer、File Browser 等由 `q2app` 自行提供的界面）的文字由应用本身给出，不在此固件内翻译。

自绘 caption 原先仅按 ASCII 字节绘制，现已支持 UTF-8 解码为 Unicode 码点再交给 `draw_centred`，缓冲随上游扩到 32 码点，并保留非法编码替换。自绘音量文字使用中文码点。

## 上游基线

汉化重放到 upstream 正式 `1.4.8` 之后（本仓库合并时的 HEAD 为 `89dd850`），保留上游全部逻辑改动：应用商店、Spotify 下载管理、年代浏览、Tidal 2000 首上限与标签、网络更新的“上游英文固件更新”标记、2038 年问题修复、强调色 Green、外观圆角选项、熄屏省电与蓝牙音量开关等。上游在 1.3.8 之后新增的界面文案本轮一并补译；同时新增的 `list_toggle` 开关行、`row_dim` 标志位、`rate_saved` 只报失败、`auto_text` 双语格式等结构差异按上游写法处理，不沿用旧版实现。

## 构建

需要通过项目校验的原厂 V1.32 固件，两种输入都可用：

- 官方 `Q2 Firmware V1.32.zip`，SHA-256 为 `154c17822d09be001be35c03d2d3488424dee195221790bd70864480d55b0f00`
- 或该 ZIP 内的 `update.tar`（山灵 V1.32 的 TF 卡升级包），SHA-256 为 `0a910790ef7902b0fe7de7d0f9f26cdac3f2c0909368c6c90778a67ee3b5dab1`

`update.tar` 入口同样是审计过的基线：校验包内 `firmware_v20.info` 必须是 `Shanling Q2` / `V1.32`，成员必须仍是那四个。用它构建的成品 `manifest.json` 记 `input_kind: "tar"`，`tools/release.py` 会拒绝把这类构建当作发布包，正式发布仍只认原厂 ZIP。

工具链为 LLVM 14、SquashFS 4.6.1（含 LZO 支持）与 ImageMagick；版本低于项目的 squashfs-tools 4.5 不能代替。使用 Python 3.12 虚拟环境（既有 MIPS 测试有 3.12 的 f-string 语法）：

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
export PATH="$PWD/work/toolchain/bin:$PATH"
.venv/bin/python tools/build.py '/path/Q2 Firmware V1.32.zip' --out build-cn-148 --ipod
# 或
.venv/bin/python tools/build.py /path/update.tar --out build-cn-148 --ipod
```

输出目录必须不存在；重复构建请换一个新目录。不带 `--dev` 的固件没有新增 q2remote 远程调试服务，适合日常使用。

## 验证与限制

可运行的中文专项检查与主要测试：

```bash
.venv/bin/python test/chinese.py build-cn-148
.venv/bin/python test/peq.py
.venv/bin/python test/coverflow.py
.venv/bin/python test/playback.py build-cn-148
.venv/bin/python test/patch.py build-cn-148
.venv/bin/python test/build.py --build build-cn-148
```

`test/chinese.py` 会扫描 `patch/*.c` 与 `tools/ipod.py` 里的全部汉字，要求它们在固件实际打包的 `fonts/default.ttf` 中都有字形，并抽查主菜单、确认框和载荷里的中文标签；卡内目录名（Podcasts、Audiobooks、Photos、Books、Videos）必须仍是英文。

本次合并没有在原机环境构建或运行上述测试：本机没有 C 编译器，也没有 WSL/Docker 可用的 Linux 工具链，因此没有 `update.tar` 成品。改动只做到源码与测试一致，C 侧只做了括号/字面量配平与 `tools/*.py`、`test/*.py` 的语法检查；实机仍需确认各菜单、滚动标题、蓝牙/流媒体流程和中文换行。

## 安装和回滚

先备份卡内资料，设备充足电。将安装包中的 `update.tar` 放入存储卡根目录，安全弹出后通过系统设置的 TF 卡升级安装。不要在刷机期间断电或拔卡。

本版与上游 1.4.8 使用同一个版本标记 `V148I`（关于页面版本 1.4.8）。设备已经是上游英文 1.4.8 时，直接刷日常包可能被判为同版本；先刷带 `--dev` 的版本标记不同的包，再刷日常包。从官方 V1.32 升级则不受影响。开发包包含新增的密钥保护远程调试服务，不建议长期停留。不要手工修改升级包或校验值。

需要回滚时，按山灵官方说明使用 Q2 官方 V1.32 固件从 TF 卡升级。本地中文修改不影响这一恢复途径。卡内文件通常不因正常升级而格式化，但设置、评分、续播记录等内部数据不保证被原厂固件保留或识别。

## 网络更新的重要提示

上游的应用商店/网络更新仍从 DiamondBond/q2-pod 下载原版英文固件，不会获取本地中文 Fork。为避免误会，固件更新行标题明确显示“上游英文固件更新”。点击并完成安装会失去本地汉化；如需保留中文，请使用本 Fork 构建的固件通过 TF 卡升级。没有悄悄将下载地址改为缺少 Release 产物的 Fork，也没有弱化 TLS、版本或写入检查。
