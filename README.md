# 波普工牌 · Pop-Art Badge for AI Passport

把你的 AI Passport 变成一块波普漫画风的个人电子工牌：开机一张启动图，首页是姓名、岗位、简介与实时时钟，翻一页是倒计时纪念日，再翻就是钉钉 / 微信加好友二维码。

一个按键就能玩。整套界面在电脑上配置好再传到设备里，改文字、换头像、换二维码、换启动图、配 WiFi，都不用重新构建固件。

---

## 玩法一览

| 页面 | 内容 |
| --- | --- |
| 启动页 | 开机整屏显示你上传的那张图 |
| 首页（P1） | 姓名 / 岗位 / 简介 + 实时时钟（冒号每秒闪动）+ 日期章 + 真实电量 |
| 纪念日页（P3） | 大号倒计时 / 正计天数：事件名、目标日期、「距 XX 还有 N 天 / 自 XX 以来已 N 天」两种句式 |
| 加好友页（P2） | 大幅二维码，钉钉 ⇄ 微信一键切换；四个页面都显示真实电量 |

- **三个功能键**：上、下、确定
- **上下键**：在 启动页之外的 4 个页面间循环翻页（首页 → 纪念日 → 微信 → 钉钉）
- **确定键**：单击直达记住的加好友页（已在二维码页则原地钉钉 ⇄ 微信）；长按回首页
- **省电时序**：45 秒无操作自动变暗，150 秒无操作熄屏；任意键唤醒会先显示启动图，停留 2 秒后自动回首页
- **走时**：开机自动联网校时，支持配置多个 WiFi（自动先连在场的那个）；联网失败也能正常使用，只是时间从零点起算

---

## 两分钟上手

### 方式一：用现成的已发布版本（最省事）

在 AI Passport 社区里打开这个玩法，按页面提示安装到设备即可。

### 方式二：从源码使用

1. 下载本仓库
2. 用电脑上的配置页把首页文案和图片换成你自己的（见下一节）
3. 把下面这些文件拷贝到设备根目录：
   - 玩法脚本：`main.py`、`home.py`、`anniv.py`、`qr.py`、`splash.py`、`buttons.py`、`battery.py`、`timesync.py`、`st7789p3.py`、`wifi_cfg.py`、`sprites.py`
   - 预渲染素材：`splash.raw`，以及 `generated/` 里的 `home.raw`、`anniv.raw`、`qr_ding.raw`、`qr_wx.raw`、`sprites.raw`

> 素材文件名与脚本里引用的名字必须一致，请把 `generated/` 里的 `.raw` 平铺放到设备根目录。
> 想让设备开机就进入这个玩法，把 `main.py` 放到设备根目录即可。

### 让时钟走对（可选）

在配置页的「WiFi 配置」卡片里填入你自己的 2.4G 频段 WiFi 名称和密码（可配多个，上传后自动校时验证）；或者手工编辑 `wifi_cfg.py`：

```python
NETS = (
    ("你的WiFi名称", "WiFi密码"),
    ("第二个WiFi名称", "第二个密码"),   # 可选：换环境自动轮试
)
```

不填也能用，只是时间不会是真实时间。该文件以明文存放在设备里，请不要填写重要账号的密码。

---

## 电脑端配置页：改文字 / 换头像 / 换二维码 / 换启动图

仓库自带一个本地网页工具，改完实时预览，一键生成并直接传到设备。

**启动**（电脑上需要有 Python）：

```bash
python tools/profile_studio.py
```

**在浏览器打开**：<http://127.0.0.1:8519>

### 能改什么

| 项目 | 说明 |
| --- | --- |
| 姓名 / 岗位 / 简介 | 实时预览换行与占位是否合适，提示是否超框 |
| 字体与字号 | 内置开源字体，中文可选思源黑体 / Noto Sans SC，英文展示体可选 Bangers；提供固定字号档位 |
| 头像 | 上传人像，在取景框里拖动、缩放取景 |
| 纪念日倒计时 | 事件名（如「相识」「毕业」）、目标日期（xxxx年x月x日）、句式（距 XX 还有 N 天 / 自 XX 以来已 N 天） |
| WiFi 配置 | 多个 WiFi 增删改，保存并上传到设备，可选立即校时验证 |
| 钉钉 / 微信二维码 | 上传后自动缩放适配到二维码框内 |
| 启动页图片 | 上传任意竖版图片，自动铺满并居中裁切到整屏 |

### 怎么用

1. 在页面上改好内容（改文字、传图片都会即时预览）
2. 页面会列出设备串口，选好端口
3. 点「生成并上传」，工具会渲染好素材、自动传到设备，并让设备重新启动
4. 设备重启后就是你的新界面

> 上传只替换对应的那一个元素：换头像不会动首页的 HERO 角标和 BOOM 贴纸，换二维码不会影响页面其他部分。

---

## 仓库结构

```
main.py            开机入口：导航与省电时序
home.py            首页：底板 + 时钟 / 日期 / 电量小窗口
anniv.py           纪念日页：底板 + 倒计时数字槽 + 电量
qr.py              加好友页：钉钉 / 微信二维码底板 + 头条电量
splash.py          启动页
buttons.py         三个功能键的读取与单击 / 长按判定
battery.py         电量读取
timesync.py        开机联网校时（多 WiFi 轮试）
st7789p3.py        屏幕驱动
wifi_cfg.py         WiFi 名称 / 密码列表（需自己填）
sprites.py         动态小窗口的坐标表

splash.raw         启动页素材（预渲染）
generated/         预渲染素材：home.raw / anniv.raw / qr_ding.raw / qr_wx.raw / sprites.raw
tools/             电脑端工具：配置页 + 素材烘焙脚本 + 字体
tools/vendor/      随仓库分发的开源字体与出厂素材
```

所有界面素材都在电脑上预先渲染成文件再传到设备，设备端只负责显示，不做解码。

---

## 换成你自己的内容

| 想改的东西 | 怎么做 |
| --- | --- |
| 姓名 / 岗位 / 简介 / 字体 | 用配置页，或改 `tools/profile.json` 后重跑 `tools/bake_badge.py` |
| 头像 / 二维码 / 启动页 | 用配置页上传（推荐） |
| 配色 / 贴纸 | 见 `tools/bake_badge.py` 里的调色板与 `tools/vendor/images/` 素材 |

---

## 第三方素材与许可

- 本玩法自有代码：MIT
- Bangers 字体：SIL Open Font License
- 思源黑体（Source Han Sans）、Noto Sans SC：SIL Open Font License
- 出厂占位素材与界面风格参考自 FoloToy AI Passport 官方开源仓库

仓库里是中性占位内容（示例姓名 / 岗位 / 简介、示例启动图、官方示例二维码），不含任何个人信息，请替换成你自己的。

---

## English

### What it is

Turn your AI Passport into a pop-art personal badge: a splash image on boot, a home screen with your name, role, short bio and a live clock, a countdown page for the dates you care about, and a one-tap page with your DingTalk / WeChat add-friend QR code.

### Pages

| Page | Content |
| --- | --- |
| Splash | Full-screen image of your choice on boot |
| Home (P1) | Name / role / bio, live clock with a blinking colon, date, and real battery level |
| Countdown (P3) | Big flip-style counter for a date that matters to you: name the event, pick the date, and choose "N days until ..." or "N days since ..." |
| Add friend (P2) | Full-screen QR code, switch between DingTalk and WeChat; every page shows the real battery level |

- **Three buttons**: Up, Down, OK
- **Up / Down**: cycle through the four pages (Home → Countdown → WeChat → DingTalk)
- **OK**: one click jumps to the remembered QR page (or flips DingTalk ⇄ WeChat when already there); long press returns Home
- **Power saving**: dims after 45s idle, turns off after 150s idle; any key wakes the device back to the splash image, then returns Home after 2s
- **Clock**: syncs time over Wi-Fi on boot with multiple-network fallback, so the clock shows the real local time (it still works without a network, just starting from zero)

### Quick start

1. Download this repository
2. Open the PC config page to set your own text and images (see below)
3. Copy these files to the root of your device:
   - Scripts: `main.py`, `home.py`, `anniv.py`, `qr.py`, `splash.py`, `buttons.py`, `battery.py`, `timesync.py`, `st7789p3.py`, `wifi_cfg.py`, `sprites.py`
   - Pre-rendered assets: `splash.raw` plus `home.raw`, `anniv.raw`, `qr_ding.raw`, `qr_wx.raw`, `sprites.raw` from `generated/`

### PC config page

Run `python tools/profile_studio.py` and open <http://127.0.0.1:8519>. You can edit the name / role / bio, pick fonts and sizes, upload an avatar, configure the countdown event and date, manage multiple Wi-Fi networks (with an instant sync check), upload your DingTalk and WeChat QR codes, and upload a splash image — then generate and push everything to the device in one click. Uploads only replace the single element you changed; the HERO badge and BOOM sticker on the home screen stay untouched.

### Credits and license

- This play's own code: MIT
- Bangers font: SIL Open Font License
- Source Han Sans and Noto Sans SC: SIL Open Font License
- Placeholder artwork and visual style inspired by the official FoloToy AI Passport open-source repository