#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC 端烘焙：v2.x 漫画风工牌页面 → 板端可直读的 RGB565 大端整屏 .raw。

事实来源
--------
  main/badge_page_home.c   P1 首页布局坐标（本脚本逐项复刻）
  main/badge_theme.h       色板
  main/badge_text.h        上屏文案
  main/badge_profile.h     工牌内容
  UI库/二次元/工牌.md       视觉基准（240x320 HTML 高保真稿）

素材
----
直接解析仓库既有 assets/images/*.c（LVGL RGB565 **小端**）→ PIL 图像，
不重跑 badge_gen_assets.py，保证与 C 版素材逐像素一致。

输出
----
  mp-badge/generated/home.raw       P1 **静态底板**（RGB565 大端，240x320，153600 B）
  mp-badge/generated/home.png       P1 整页预览（含动态元素，仅供肉眼比对）
  mp-badge/generated/qr_ding.raw    P2 钉钉版整页
  mp-badge/generated/qr_wx.raw      P2 微信版整页
  mp-badge/generated/sprites.raw    P1 动态精灵块打包（单文件，按偏移取用）
  mp-badge/tools/sprites.py         精灵块偏移表（生成后需拷到板子根目录）

动态元素策略（板端无 LVGL，用屏幕 GRAM 当合成器）
------------------------------------------------
  底板画一次；会变的只有 3 处 → 预烘焙成不透明精灵块，定点 blit 覆盖：
    时钟 HH / 冒号（亮·暗两态）/ MM    —— Bangers 30 数字宽度不均且溢出，必须整段烘焙
    日期章 12 个字符槽                —— cn_14 数字等宽 8px，可按字符烘焙
    电量（指示条 + 百分比）           —— 按 soc 0..100 逐值烘焙
  所有精灵块都是「整页渲染后裁窗口」得到的，因此「底板 + 精灵块 == 整页」逐像素成立。

用法：python mp-badge/tools/bake_badge.py
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

# --- 路径 ---------------------------------------------------------------------
TOOLS = Path(__file__).resolve().parent
MPB = TOOLS.parent                                   # mp-badge/
VENDOR = TOOLS / "vendor"                            # 出厂素材与开源字体（随仓库分发）
IMG_DIR = VENDOR / "images"                          # 角色 / 网点 / 占位二维码（LVGL .c）
OUT_DIR = MPB / "generated"

SHS = VENDOR / "fonts" / "SourceHanSansCN-Bold.otf"  # 中文字：思源黑体 Bold（OFL）
BANGERS = VENDOR / "fonts" / "Bangers-Regular.ttf"   # 拉丁/数字：Bangers 漫画体（OFL）

# 配置页可选的字体：只在字体文件实际存在时才暴露（仓库仅分发开源字体；
# 本地放入其它字体文件即可在配置页选用，缺省不影响烘焙）。
# "cn"/"latin"/"time" 是页面固定元素（日期章/标签/时钟）用的内部字体，不对外开放。
FONT_DIR = TOOLS / "fonts"
FONTS = {
    "cn":        {"file": SHS,                            "label": "思源黑体 Bold（默认）"},
    "latin":     {"file": BANGERS,                        "label": "Bangers（默认数字/拉丁）"},
    "time":      {"file": BANGERS,                        "label": "Bangers"},
    "yahei_b":   {"file": FONT_DIR / "msyhbd.ttc",        "label": "微软雅黑 Bold"},
    "yahei":     {"file": FONT_DIR / "msyh.ttc",          "label": "微软雅黑"},
    "yahei_l":   {"file": FONT_DIR / "msyhl.ttc",         "label": "微软雅黑 Light"},
    "noto_sc":   {"file": FONT_DIR / "NotoSansSC-VF.ttf", "label": "Noto Sans SC"},
    "impact":    {"file": FONT_DIR / "impact.ttf",        "label": "Impact"},
    "ariblk":    {"file": FONT_DIR / "ariblk.ttf",        "label": "Arial Black"},
    "segoeui_b": {"file": FONT_DIR / "segoeuib.ttf",      "label": "Segoe UI Bold"},
}
FONTS = {k: v for k, v in FONTS.items() if Path(v["file"]).is_file()}

# 用户上传素材（头像 / 二维码）：只在 PC 端 tools/assets/ 存在，不占板子 Flash。
# 文件缺失 → 一律回退出厂内置图（保证默认烘焙零回归）。
ASSET_DIR = TOOLS / "assets"
AVATAR_PNG = ASSET_DIR / "avatar.png"
QR_PNG = {"ding": ASSET_DIR / "qr_ding.png", "wx": ASSET_DIR / "qr_wx.png"}

# 姓名/岗位/简介 对外开放的字号档位。只给固定档位是为了避免连续调节把版面挤坏。
ELEM_SIZES = {
    "name": (20, 22, 24, 26, 28, 30),
    "role": (14, 16, 18, 20, 22),
    "bio": (14, 16, 18, 20, 22),
}

# --- 画布与主题色板（badge_theme.h） ------------------------------------------
W, H = 240, 320

# 主题皮肤 = 一组颜色。默认 "default" 即出厂漫画风，是零回归基线；
# 后续出主题皮肤包时，只要往 SKINS 里加一项，并让调用方传 load_skin("<名字>")，
# 所有绘制函数读到的仍是同一批模块级全局色常量，无需改动各自代码。
SKINS = {
    "default": {
        "label": "出厂漫画风",
        "paper": (0xFF, 0xFD, 0xE8),      # BADGE_PAPER
        "paper_pop": (0xFF, 0xFD, 0xF0),  # BADGE_PAPER_POP
        "ink": (0x11, 0x11, 0x11),        # BADGE_INK
        "hilite": (0xFF, 0xE6, 0x00),     # BADGE_HIGHLIGHT
        "red": (0xFF, 0x2A, 0x55),        # BADGE_RED
        "cyan": (0x00, 0xE5, 0xFF),       # BADGE_SCAN_CYAN
        "wgreen": (0x00, 0xC8, 0x53),     # BADGE_WECHAT_GREEN
        "gray": (0x88, 0x88, 0x88),       # BADGE_GRAY
        "white": (0xFF, 0xFF, 0xFF),      # BADGE_PANEL_WHITE
        "batt_green": (0x00, 0xC8, 0x53),
        "batt_amber": (0xFF, 0xA0, 0x00),
    },
}
SKIN_NAME = "default"


def blend_over(fg, bg, alpha: float):
    """把 fg 以 alpha 叠到不透明 bg 上，返回等价的不透明色（供预烘焙精灵块使用）。"""
    return tuple(round(f * alpha + b * (1.0 - alpha)) for f, b in zip(fg, bg))


# 冒号闪烁暗态：LVGL 把整层降到 LV_OPA_30 叠在 INK 上，
# 颜色混合是线性的 → 等价于用预混色以不透明方式画在 INK 背景上。
COLON_OPA_DIM = 0.30


def load_skin(name: str = SKIN_NAME) -> None:
    """把一套皮肤的颜色装进模块级全局色常量（本次烘焙的所有绘制函数都读它们）。

    注意：COL_OFF_FG / COL_OFF_SH 依赖当前色板，必须在换色板后重算，
    所以它们不写死在模块顶层，而是随 load_skin() 一起派生。
    """
    global PAPER, PAPER_POP, INK, HILITE, RED, CYAN, WGREEN, GRAY, WHITE
    global BATT_GREEN, BATT_AMBER, COL_OFF_FG, COL_OFF_SH
    s = SKINS[name]
    PAPER, PAPER_POP = s["paper"], s["paper_pop"]
    INK, HILITE, RED = s["ink"], s["hilite"], s["red"]
    CYAN, WGREEN, GRAY, WHITE = s["cyan"], s["wgreen"], s["gray"], s["white"]
    BATT_GREEN, BATT_AMBER = s["batt_green"], s["batt_amber"]
    COL_OFF_FG = blend_over(HILITE, INK, COLON_OPA_DIM)   # 暗态字
    COL_OFF_SH = blend_over(RED, INK, COLON_OPA_DIM)      # 暗态错位阴影


load_skin()

# --- 工牌内容（badge_profile.h / badge_text.h） --------------------------------
# 下面三个是出厂默认值；若 tools/profile.json 存在（由 tools/profile_studio.py 写入），
# 则读它覆盖，保证「配置页保存的内容」与「命令行重烘焙」用的是同一份文案。
NAME = "示例姓名"
ROLE = "示例岗位"
BIO = "示例简介，请替换"

# 姓名 / 岗位 / 简介 的字体与字号（配置页可改；默认值与出厂烘焙零回归）
NAME_FONT, NAME_SIZE = "cn", 26
ROLE_FONT, ROLE_SIZE = "cn", 20
BIO_FONT, BIO_SIZE = "cn", 20

# 头像取景（配置页里拖动/缩放得到）：zoom=1 时按 cover 铺满分镜框，dx/dy 为图像相对
# 居中位置的位移（正=图像右移/下移）。无上传图时这三项不参与渲染。
AVATAR_ZOOM = 1.0
AVATAR_DX = 0.0
AVATAR_DY = 0.0

# 纪念日倒计时页（P3）的目标日期与语义：
#   anniv_mode="until" -> 距离目标日还有几天（当天=0，过期后恒 0）
#   anniv_mode="since" -> 从目标日起已是第几天（当天=1）
# 下面三个是出厂占位值，仅为让页面可烘焙；真实值由 tools/profile.json 覆盖。
# 日期对外统一用固定格式「xxxx年x月x日」（解析时兼容 xxxx-x-x 写法）。
ANNIV_LABEL = "纪念日"
ANNIV_DATE = "2024年10月20日"
ANNIV_MODE = "until"

PROFILE_JSON = TOOLS / "profile.json"


def parse_anniv_date(s) -> tuple | None:
    """把「2024年10月20日」或「2024-10-20」解析为 (y, m, d)；非法返回 None。"""
    try:
        t = str(s or "").strip()
        for ch in ("年", "月", "日", "/"):
            t = t.replace(ch, "-")
        y, m, d = (int(v) for v in t.rstrip("-").split("-"))
        datetime.date(y, m, d)              # 合法性检查（含闰年/月长）
    except (TypeError, ValueError):
        return None
    return (y, m, d)


def load_profile() -> None:
    """存在 tools/profile.json 时，用它覆盖文案与字体字号（非法字体键/字号退回默认）。"""
    global NAME, ROLE, BIO
    global NAME_FONT, NAME_SIZE, ROLE_FONT, ROLE_SIZE, BIO_FONT, BIO_SIZE
    global AVATAR_ZOOM, AVATAR_DX, AVATAR_DY
    global ANNIV_LABEL, ANNIV_DATE, ANNIV_MODE
    if not PROFILE_JSON.is_file():
        return
    import json
    p = json.loads(PROFILE_JSON.read_text(encoding="utf-8"))
    NAME = p.get("name", NAME)
    ROLE = p.get("role", ROLE)
    BIO = p.get("bio", BIO)
    NAME_FONT = p.get("name_font", NAME_FONT)
    ROLE_FONT = p.get("role_font", ROLE_FONT)
    BIO_FONT = p.get("bio_font", BIO_FONT)
    if NAME_FONT not in FONTS:
        NAME_FONT = "cn"
    if ROLE_FONT not in FONTS:
        ROLE_FONT = "cn"
    if BIO_FONT not in FONTS:
        BIO_FONT = "cn"
    NAME_SIZE = int(p.get("name_size", NAME_SIZE))
    ROLE_SIZE = int(p.get("role_size", ROLE_SIZE))
    BIO_SIZE = int(p.get("bio_size", BIO_SIZE))
    try:
        AVATAR_ZOOM = float(p.get("avatar_zoom", AVATAR_ZOOM))
        AVATAR_DX = float(p.get("avatar_dx", AVATAR_DX))
        AVATAR_DY = float(p.get("avatar_dy", AVATAR_DY))
    except (TypeError, ValueError):
        pass
    ANNIV_LABEL = str(p.get("anniv_label", ANNIV_LABEL))
    # mode 白名单用字面量校验（ANNIV_CAP 定义在本函数调用之后，此处不可引用）
    ANNIV_MODE = p.get("anniv_mode", ANNIV_MODE)
    if ANNIV_MODE not in ("until", "since"):
        ANNIV_MODE = "until"
    ad = p.get("anniv_date", ANNIV_DATE)
    ymd = parse_anniv_date(ad)
    if ymd is not None:
        ANNIV_DATE = "%d年%d月%d日" % ymd       # 统一归一化为对外固定格式


load_profile()

TAG_HOME = "HERO // 01"
SFX = "BOOM!"
NAME_HEADER = "NAME"
KEY_OK = "OK"
OK_ACTION = "加好友"
WEEK_PREFIX = "周"
WEEKDAYS = "一二三四五六日"

# --- P2 加好友页文案（badge_text.h） ------------------------------------------
TAG_QR = "QR // 02"
QR_TITLE = "加好友"
QR_BADGE = "SCAN!"
TAB_NAMES = ("钉钉", "微信")
KEY_SWITCH = "UP/DN"
KEY_BACK = "LONG OK"

# --- P3 纪念日倒计时页文案 ------------------------------------------------
TAG_ANNIV = "ANNIV // 03"
TAG_DDAY = "D-DAY"
ANNIV_UNIT = "天"
ANNIV_CAP = {"until": ("距", "还有"), "since": ("自", "以来已")}
# 句式：距{name}还有 [N] 天 / 自{name}以来已 [N] 天（[N] 即表盘数字）
ANNIV_OFF = (0x4A, 0x42, 0x18)        # 前导空位「熄灭段」暗黄（黑窗上的占位 0）

# --- 字库行高（取自 assets/fonts/*.c 的 .line_height，与 LVGL 排版一致） -------
LINE_H = {
    ("cn", 14): 15, ("cn", 20): 21, ("cn", 26): 26,
    ("latin", 12): 11, ("latin", 20): 17, ("time", 30): 23,
}
_FONTS: dict = {}


def F(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key not in _FONTS:
        spec = FONTS[kind]
        _FONTS[key] = ImageFont.truetype(str(spec["file"]), size,
                                         index=spec.get("index", 0))
    return _FONTS[key]


def line_h(kind: str, size: int) -> int:
    """行高：已知组合用字库 .line_height（与 LVGL 一致，保证零回归）；
    配置页新引入的字体/字号组合用 FreeType 度量（ascent+descent）估算。"""
    k = (kind, size)
    if k in LINE_H:
        return LINE_H[k]
    a, desc = F(kind, size).getmetrics()
    return a + desc


# --- 素材解析：LVGL lv_image_dsc_t 源文件 → PIL 图像 ---------------------------
_IMG_CACHE: dict = {}
_TILE_CACHE: dict = {}


def load_image_c(name: str) -> Image.Image:
    """解析 assets/images/<name>.c 里的 s_data[]（RGB565 小端）为 RGB 图像（带缓存）。"""
    if name in _IMG_CACHE:
        return _IMG_CACHE[name]
    txt = (IMG_DIR / f"{name}.c").read_text(encoding="utf-8")
    body = txt.split("s_data[]", 1)[1].split("{", 1)[1].split("}", 1)[0]
    data = bytes(int(t, 16) for t in re.findall(r"0x([0-9A-Fa-f]{2})", body))
    w = int(re.search(r"\.header\.w\s*=\s*(\d+)", txt).group(1))
    h = int(re.search(r"\.header\.h\s*=\s*(\d+)", txt).group(1))
    if len(data) != w * h * 2:
        raise SystemExit(f"{name}.c 尺寸不符：{len(data)} != {w}x{h}x2")

    px = bytearray(w * h * 3)
    for i in range(w * h):
        v = data[2 * i] | (data[2 * i + 1] << 8)          # 小端
        r, g, b = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
        px[3 * i] = (r << 3) | (r >> 2)
        px[3 * i + 1] = (g << 2) | (g >> 4)
        px[3 * i + 2] = (b << 3) | (b >> 2)
    img = Image.frombytes("RGB", (w, h), bytes(px))
    _IMG_CACHE[name] = img
    return img


def tile_bg(w: int, h: int, tile: Image.Image) -> Image.Image:
    """平铺网点 tile（对应 LVGL bg_image_tiled，带缓存）。"""
    key = (id(tile), w, h)
    if key not in _TILE_CACHE:
        bg = Image.new("RGB", (w, h))
        for y in range(0, h, tile.height):
            for x in range(0, w, tile.width):
                bg.paste(tile, (x, y))
        _TILE_CACHE[key] = bg
    return _TILE_CACHE[key]


# --- 绘制原语 -----------------------------------------------------------------
def text_w(s: str, f: ImageFont.FreeTypeFont, ls: int = 0) -> float:
    if ls <= 0:
        return f.getlength(s)
    return sum(f.getlength(c) for c in s) + ls * (len(s) - 1)


def draw_text(d: ImageDraw.ImageDraw, xy, s: str, f, color, ls: int = 0, anchor="la", ):
    """逐字绘制以支持字距（LVGL text_letter_space 的等价物）。"""
    if ls <= 0:
        d.text(xy, s, font=f, fill=color + (255,), anchor=anchor)
        return
    x, y = xy
    for c in s:
        d.text((x, y), c, font=f, fill=color + (255,), anchor=anchor)
        x += f.getlength(c) + ls


def wrap(s: str, f, max_w: float) -> list[str]:
    lines, cur = [], ""
    for ch in s:
        if cur and f.getlength(cur + ch) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def build_chip(text: str, kind: str, size: int, fg, bg, *,
               pad_h=4, pad_v=1, border=2, radius=0, ls=0,
               shadow=None, sh_ofs=2, box_w=None, align="left"):
    """生成一个"漫画小标签"元素层。

    对应 LVGL：label + bg + border + pad + pop_shadow，字号行高取自 LINE_H。
    返回 (layer, w, h)：w/h 为元素本体尺寸（不含旋转与阴影留白）。
    box_w 给定则固定宽度（role 条幅），align 控制文字在内容区内的水平位置。
    """
    f = F(kind, size)
    lh = line_h(kind, size)
    tw = text_w(text, f, ls)

    w = box_w if box_w is not None else round(tw) + 2 * pad_h + 2 * border
    h = lh + 2 * pad_v + 2 * border

    ext = max(sh_ofs, 0) + 4
    layer = Image.new("RGBA", (w + 2 * ext, h + 2 * ext), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0 = ext, ext
    x1, y1 = ext + w - 1, ext + h - 1

    if shadow is not None:
        d.rounded_rectangle([x0 + sh_ofs, y0 + sh_ofs, x1 + sh_ofs, y1 + sh_ofs],
                            radius=radius, fill=shadow + (255,))
    d.rounded_rectangle([x0, y0, x1, y1], radius=radius,
                        fill=bg + (255,), outline=INK + (255,), width=border)

    inner_x = x0 + border + pad_h
    inner_w = w - 2 * border - 2 * pad_h
    if align == "center":
        tx = inner_x + (inner_w - tw) / 2
    elif align == "right":
        tx = inner_x + inner_w - tw
    else:
        tx = inner_x
    draw_text(d, (tx, y0 + border + pad_v), text, f, fg, ls)
    return layer, w, h


def place(canvas: Image.Image, layer: Image.Image, cx: float, cy: float, angle=0.0) -> None:
    """把元素层按中心点贴到画布（angle 为 CSS 角度，正=顺时针）。"""
    if angle:
        layer = layer.rotate(-angle, expand=True, resample=Image.BICUBIC)
    canvas.paste(layer, (round(cx - layer.width / 2), round(cy - layer.height / 2)), layer)


def shadow_box(d: ImageDraw.ImageDraw, x0, y0, x1, y1, *, fill, border=INK,
               width=1, radius=0, sh_ofs=0, shadow=INK) -> None:
    """LVGL pop_shadow：先在偏移处画一层纯色，再画本体（视觉≈CSS 硬偏移阴影）。"""
    if sh_ofs:
        d.rounded_rectangle([x0 + sh_ofs, y0 + sh_ofs, x1 + sh_ofs, y1 + sh_ofs],
                            radius=radius, fill=shadow)
    d.rounded_rectangle([x0, y0, x1, y1], radius=radius,
                        fill=fill, outline=border, width=width)


def paste_clipped(canvas: Image.Image, img: Image.Image, xy, clip) -> None:
    """把 img 按 xy 放到 canvas，但只保留落在 clip 矩形内的部分（模拟 overflow:hidden）。"""
    ox, oy = xy
    cx0, cy0, cx1, cy1 = clip
    sx0, sy0 = max(ox, cx0), max(oy, cy0)
    sx1, sy1 = min(ox + img.width, cx1), min(oy + img.height, cy1)
    if sx0 >= sx1 or sy0 >= sy1:
        return
    canvas.paste(img.crop((sx0 - ox, sy0 - oy, sx1 - ox, sy1 - oy)), (sx0, sy0))


def draw_battery(canvas: Image.Image, d: ImageDraw.ImageDraw, right_ofs: int, top: int, soc: int,
                 p_right: int = W, p_top: int = 0, *, pct: bool = True, bar: bool = True) -> None:
    """复刻 badge_battery_create/set（外壳 + 正极凸起 + 百分比文本）。

    全部对齐量都是相对**父容器内容区**的：p_right/p_top 为父内容区右缘/上缘。
    LVGL 里屏幕的 p_right 是 239；P1 首页当初按 240 出图（已验收，保持不动），
    P2 头条内则严格用 LVGL 值 223/12。

    pct=False 不画百分比文本；bar=False 不画电量指示条——用于烘焙「静态底板」
    与「低电量闪烁的条暗态」精灵块。
    """
    BODY_W, BODY_H, NUB_W, NUB_H, GAP = 18, 11, 3, 5, 5
    # 外壳：白底墨框，右缘 = p_right + right_ofs - NUB_W
    bx1 = p_right + right_ofs - NUB_W
    bx0 = bx1 - BODY_W + 1
    by0, by1 = p_top + top, p_top + top + BODY_H - 1
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=2,
                        fill=WHITE, outline=INK, width=2)
    # 电量指示条（内容区内缩 border2 + pad1）
    ix0, iy0, ix1, iy1 = bx0 + 3, by0 + 3, bx1 - 3, by1 - 3
    if bar:
        if soc >= 80:
            c = BATT_GREEN
        elif soc >= 20:
            c = BATT_AMBER
        else:
            c = RED
        iw = round((ix1 - ix0 + 1) * max(soc, 0) / 100)
        if iw > 0:
            d.rounded_rectangle([ix0, iy0, ix0 + iw - 1, iy1], radius=1, fill=c)
    # 正极凸起
    nx1 = p_right + right_ofs
    ny0 = p_top + top + (BODY_H - NUB_H) // 2
    d.rounded_rectangle([nx1 - NUB_W + 1, ny0, nx1, ny0 + NUB_H - 1],
                        radius=1, fill=INK)
    # 百分比文本：右缘 = p_right + right_ofs - NUB_W - BODY_W - GAP
    if pct:
        f = F("cn", 14)
        txt = f"{soc}%"
        tx1 = p_right + right_ofs - NUB_W - BODY_W - GAP
        ty0 = p_top + top + BODY_H // 2 - LINE_H[("cn", 14)] // 2
        d.text((tx1 - f.getlength(txt), ty0 - 3), txt, font=f, fill=INK, anchor="la")


# --- 姓名自适应字距 -----------------------------------------------------------
# 姓名黄框 (114,34)-(229,93)、边框 3px → 内框可用宽 117..226 = 110px，居中 x=172。
NAME_BOX_INNER_W = 110
NAME_LS_REF = (26, 6)        # 出厂 cn_26 的装饰字距 6px —— 零回归锚点


def has_cjk(s: str) -> bool:
    """是否含中日韩表意文字 / 全角标点（这类字需要拉开字距才好看）。"""
    return any("\u2e80" <= c <= "\u9fff" or "\u3000" <= c <= "\u303f"
               or "\uff00" <= c <= "\uffef" for c in s)


def name_letter_space(name: str, kind: str, size: int,
                      avail: int = NAME_BOX_INNER_W) -> int:
    """按姓名框内框宽度算出实际字距。

    装饰字距上限由**内容**决定：含汉字 → 沿用出厂等比规则 size*6/26（26px 时为 6，
    保证出厂画面零回归）；纯拉丁 → 上限 1px（拉丁字本身是比例宽度，原字距显得过稀）。
    余量不足时把字距一路压到 0，但**不缩字号**（字号是用户选的，超宽由配置页给警告）。
    """
    if len(name) < 2:
        return 0
    cap = max(0, round(size * NAME_LS_REF[1] / NAME_LS_REF[0])) if has_cjk(name) else 1
    if cap == 0:
        return 0
    tw0 = text_w(name, F(kind, size))
    if tw0 >= avail - 2:
        return 0
    room = (avail - 2 - tw0) / (len(name) - 1)
    return max(0, min(cap, int(room)))


# --- 上传素材：头像（cover 铺满分镜框，PC 端可拖动 / 缩放） --------------------
AVATAR_W, AVATAR_H = 98, 220      # 与出厂 badge_img_character 同尺寸 → 贴图几何零改动
AVATAR_ZOOM_MIN, AVATAR_ZOOM_MAX = 1.0, 4.0
_AVATAR_CACHE: dict = {}
_QR_CACHE: dict = {}


def avatar_render(src: Image.Image, zoom: float = 1.0, dx: float = 0.0, dy: float = 0.0,
                  w: int = AVATAR_W, h: int = AVATAR_H) -> Image.Image:
    """把任意图源按 cover 铺满 w×h，再按 zoom 放大、dx/dy 平移后裁出 w×h。

    zoom=1 时短边刚好贴满（无留白）；dx 为正表示**图像右移**（等价取景窗口左移）。
    缩放与平移量在本函数内夹到合法范围，任何输入都不会露出空白。
    """
    sw, sh = src.size
    scale = max(w / sw, h / sh) * max(float(zoom), AVATAR_ZOOM_MIN)
    nw, nh = max(w, int(round(sw * scale))), max(h, int(round(sh * scale)))
    img = src.resize((nw, nh), Image.LANCZOS)
    ox = min(max((nw - w) / 2.0 - float(dx), 0.0), float(nw - w))
    oy = min(max((nh - h) / 2.0 - float(dy), 0.0), float(nh - h))
    ox, oy = int(round(ox)), int(round(oy))
    return img.crop((ox, oy, ox + w, oy + h))


def load_avatar() -> Image.Image:
    """分镜框人像：有 tools/assets/avatar.png 就按 AVATAR_* 取景，否则回退出厂图。"""
    if not AVATAR_PNG.is_file():
        return load_image_c("badge_img_character")
    key = (AVATAR_PNG.stat().st_mtime_ns, round(AVATAR_ZOOM, 4),
           round(AVATAR_DX, 2), round(AVATAR_DY, 2))
    if key not in _AVATAR_CACHE:
        if len(_AVATAR_CACHE) > 16:      # bake_home 会跑 200+ 次，缓存避免重复缩放
            _AVATAR_CACHE.clear()
        _AVATAR_CACHE[key] = avatar_render(
            Image.open(AVATAR_PNG).convert("RGB"), AVATAR_ZOOM, AVATAR_DX, AVATAR_DY)
    return _AVATAR_CACHE[key]


# --- 上传素材：二维码（contain 自适应到 174x174，不裁切） ----------------------
QR_W = QR_H = 174                 # 与出厂 badge_img_qr_* 同尺寸 → 贴图坐标零改动


def load_qr(sel: int) -> Image.Image:
    """sel: 0=钉钉 1=微信。有上传图就等比缩放到框内、居中补白，否则回退出厂图。"""
    tag = "wx" if sel else "ding"
    path = QR_PNG[tag]
    if not path.is_file():
        return load_image_c("badge_img_qr_wx" if sel else "badge_img_qr_ding")
    key = (tag, path.stat().st_mtime_ns)
    if key not in _QR_CACHE:
        src = Image.open(path).convert("RGB")
        s = min(QR_W / src.width, QR_H / src.height)
        nw, nh = max(1, int(round(src.width * s))), max(1, int(round(src.height * s)))
        out = Image.new("RGB", (QR_W, QR_H), WHITE)
        out.paste(src.resize((nw, nh), Image.LANCZOS),
                  ((QR_W - nw) // 2, (QR_H - nh) // 2))
        _QR_CACHE[key] = out
    return _QR_CACHE[key]


# --- P1 动态精灵块窗口（均互不重叠；坐标已由 _probe_chars.py 实测合围） --------
# 时钟：Bangers 30 数字宽度不均且大量溢出，HH / MM 必须整段烘焙。
WIN_HH = (25, 277, 35, 27)     # 覆盖 HH 全值合围 x26..59 / y278..302
WIN_COL = (60, 277, 10, 27)    # 冒号 ink x60..69 / y284..301
WIN_MM = (73, 277, 36, 27)     # 覆盖 MM 全值合围 x74..107 / y278..302
WIN_BAT = (165, 14, 58, 11)    # 电量「百分比文本 + 指示条」合围 x165..222 / y14..24

# 日期章：cn_14 数字等宽 8px 无溢出 → 可按字符烘焙；槽位 x 由 adv 自 x=21 累加。
DATE_Y, DATE_H = 12, 20
DATE_W = {"d": 8, "da": 5, "zh": 14, "wd": 14}
DATE_SLOTS = (                 # (kind, x, w)：kind=None 表示空格（无精灵）
    ("d", 21, 8), ("d", 29, 8), ("d", 37, 8), ("d", 45, 8),
    ("da", 53, 5),
    ("d", 58, 8), ("d", 66, 8),
    ("da", 74, 5),
    ("d", 79, 8), ("d", 87, 8),
    (None, 95, 0),
    ("zh", 98, 14),
    ("wd", 112, 14),
)
# 日期章固定盒宽：任何合法日期文字宽度恒为 105 → 盒宽 119，与原 P1 零回归。
DATE_CHIP_W = round(text_w("2024-10-20 周一", F("cn", 14))) + 2 * 5 + 2 * 2


# --- P1 首页（badge_page_home.c） ---------------------------------------------
def bake_home(soc: int = 86, *, hh: str = "10", mm: str = "24", date_text: str | None = None,
              dynamic: bool = True, colon_dim: bool = False, bar: bool = True) -> Image.Image:
    """渲染 P1 整页。

    dynamic=False 时不画日期文字 / 时钟文字 / 电量百分比与指示条 → 输出「静态底板」；
    底板与整页的差异严格落在各精灵块窗口内，因此「底板 + 精灵块 == 整页」逐像素成立。
    """
    canvas = Image.new("RGB", (W, H), PAPER)
    canvas.paste(tile_bg(W, H, load_image_c("badge_img_dots_paper")), (0, 0))
    d = ImageDraw.Draw(canvas)

    # 顶部日期章 "2024-10-20 周一"（黄底墨框，shadow 2）
    if date_text is None:
        n = datetime.date.today()
        date_text = f"{n.year}-{n.month:02d}-{n.day:02d} {WEEK_PREFIX}{WEEKDAYS[n.weekday()]}"
    layer, cw, ch = build_chip(date_text if dynamic else "", "cn", 14, INK, HILITE,
                               pad_h=5, pad_v=1, border=2, radius=3,
                               shadow=INK, sh_ofs=2, box_w=DATE_CHIP_W)
    place(canvas, layer, 14 + cw / 2, 9 + ch / 2)

    # 右上角电量（系统位）
    draw_battery(canvas, d, -12, 13, soc, pct=dynamic, bar=dynamic and bar)

    # 左侧漫画角色分镜框 98x220（白底墨框 3px + shadow 3），角色图按底部居中并裁进内框
    shadow_box(d, 10, 34, 107, 253, fill=WHITE, width=3, radius=4, sh_ofs=3)
    inner = (13, 37, 104, 250)
    paste_clipped(canvas, load_avatar(), (10, 31), inner)

    # 分镜角标 HERO // 01（红底白字，-3°）
    layer, cw, ch = build_chip(TAG_HOME, "latin", 12, WHITE, RED,
                               pad_h=4, pad_v=1, border=2, shadow=INK, sh_ofs=2)
    place(canvas, layer, 14 + cw / 2, 38 + ch / 2, angle=-3.0)

    # 拟声词 BOOM!（黄底墨字，+6°；按 HTML 稿置于分镜框右上）
    layer, cw, ch = build_chip(SFX, "latin", 12, INK, HILITE,
                               pad_h=3, pad_v=1, border=2, radius=4, shadow=INK, sh_ofs=2)
    place(canvas, layer, 106 - cw / 2, 58 + ch / 2, angle=6.0)

    # 右栏姓名块 (114,34) 116x60（黄底墨框 3px + shadow 3）
    shadow_box(d, 114, 34, 229, 93, fill=HILITE, width=3, radius=3, sh_ofs=3)
    ft = F("latin", 12)
    d.text((120, 38), NAME_HEADER, font=ft, fill=RED + (255,), anchor="la")
    fn = F(NAME_FONT, NAME_SIZE)
    # 字距按姓名框内框宽度自适应：出厂 cn_26 仍得 6（零回归）；纯拉丁最多 1px
    nls = name_letter_space(NAME, NAME_FONT, NAME_SIZE)
    nw = text_w(NAME, fn, nls)
    draw_text(d, (172 - nw / 2, 68 - line_h(NAME_FONT, NAME_SIZE) / 2), NAME, fn, INK, ls=nls)

    # 岗位条幅 (114,98) 宽 116（红底白字，-1°）
    # 条幅基准宽 116；换了更大的字体/字号时按文字宽度撑开，避免文字溢出框外
    rw = round(text_w(ROLE, F(ROLE_FONT, ROLE_SIZE))) + 2 * 6 + 2 * 2
    layer, cw, ch = build_chip(ROLE, ROLE_FONT, ROLE_SIZE, WHITE, RED, pad_h=6, pad_v=4,
                               border=2, box_w=max(116, rw), align="center",
                               shadow=INK, sh_ofs=2)
    place(canvas, layer, 114 + cw / 2, 98 + ch / 2, angle=-1.0)

    # 旁白简介框 (114,135) 116x119（白底墨框 2px + shadow 2，pad 6）
    shadow_box(d, 114, 135, 229, 253, fill=WHITE, width=2, radius=2, sh_ofs=2)
    hx, hy = 114 + 2 + 6, 135 + 2 + 6
    fd = F(BIO_FONT, BIO_SIZE)
    step = line_h(BIO_FONT, BIO_SIZE) + 1        # 默认 cn_20 时 = 22，零回归
    for i, ln in enumerate(wrap(BIO, fd, 102)):
        d.text((hx, hy + i * step), ln, font=fd, fill=INK + (255,), anchor="la")

    # 底部墨色时钟面板 (10,264) 220x48（白框 2px，radius 6）
    bx0, by0, bx1, by1 = 10, 264, 229, 311
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=6, fill=INK, outline=WHITE, width=2)
    if dynamic:
        fc = F("time", 30)
        cy = 288 + 2
        col_fg = COL_OFF_FG if colon_dim else HILITE
        col_sh = COL_OFF_SH if colon_dim else RED
        segs = ((14, hh, HILITE, RED), (48, ":", col_fg, col_sh), (62, mm, HILITE, RED))
        for dx, s, fg, sh in segs:
            x = bx0 + 2 + dx
            d.text((x + 2, cy + 2), s, font=fc, fill=sh + (255,), anchor="lm")  # 红色错位阴影
            d.text((x, cy), s, font=fc, fill=fg + (255,), anchor="lm")

    # OK 加好友胶囊（黄底白框 radius 20 + 红 shadow，-1.5°）
    pw, ph = 92, 26
    ext = 6
    layer = Image.new("RGBA", (pw + 2 * ext, ph + 2 * ext), (0, 0, 0, 0))
    pl = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = ext, ext, ext + pw - 1, ext + ph - 1
    pl.rounded_rectangle([x0 + 2, y0 + 2, x1 + 2, y1 + 2], radius=20, fill=RED + (255,))
    pl.rounded_rectangle([x0, y0, x1, y1], radius=20, fill=HILITE + (255,),
                         outline=WHITE + (255,), width=2)
    fk = F("latin", 12)
    pl.text((x0 + 2 + 8, y0 + ph / 2), KEY_OK, font=fk, fill=RED + (255,), anchor="lm")
    pl.text((x0 + 2 + 30, y0 + ph / 2), OK_ACTION, font=F("cn", 14), fill=INK + (255,), anchor="lm")
    place(canvas, layer, 218 - pw / 2, 288, angle=-1.5)

    return canvas


# --- P2 加好友页（badge_page_qr.c） -------------------------------------------
def bake_qr(sel: int = 0, soc: int = 86, *, dynamic: bool = True,
            bar: bool = True) -> Image.Image:
    """sel: 0=钉钉（Tab0 选中，扫描青）1=微信（Tab1 选中，波普绿）。

    dynamic=False 输出「静态底板」：电量只画外壳+凸起，不画百分比文本与指示条
    （二者由 batq_* 精灵块运行时覆盖）→「底板 + 精灵块 == 整页」逐像素成立。
    电量画在黄底头条内，与 Tab 无关 → 两个 sel 的 batq_* 精灵块共用一套。
    """
    canvas = Image.new("RGB", (W, H), PAPER_POP)
    canvas.paste(tile_bg(W, H, load_image_c("badge_img_dots_pop")), (0, 0))
    d = ImageDraw.Draw(canvas)

    # 黄色漫画头条 pos(14,10) size(212,22)：墨框 2 + radius 2 + shadow 2
    shadow_box(d, 14, 10, 225, 31, fill=HILITE, width=2, radius=2, sh_ofs=2)
    # 头条内容区（LVGL：内缩 border 2）右缘 223 / 上缘 12 / 垂直中心 20.5
    cx_l, cy_m, right_c, top_c = 16, 20.5, 223, 12

    # QR // 02 徽章：白字红底，无边框，LEFT_MID(5,0)
    layer, cw, ch = build_chip(TAG_QR, "latin", 12, WHITE, RED,
                               pad_h=3, pad_v=1, border=0, radius=2)
    place(canvas, layer, cx_l + 5 + cw / 2, cy_m)

    # 标题「加好友」：cn_14 墨字，LEFT_MID(58,0)
    f14 = F("cn", 14)
    d.text((cx_l + 58, cy_m), QR_TITLE, font=f14, fill=INK + (255,), anchor="lm")

    # 头条内电量：相对头条内容区（p_right=223, p_top=12）
    draw_battery(canvas, d, -7, 5, soc, p_right=right_c, p_top=top_c,
                 pct=dynamic, bar=dynamic and bar)

    # 双 Tab：width 100，cn_14 居中，TOP_LEFT(16 + i*108, 40)
    active_bg = (CYAN, WGREEN)
    for i, name in enumerate(TAB_NAMES):
        on = (i == sel)
        border = 3 if on else 2
        tw, th = 100, LINE_H[("cn", 14)] + 2 * 5 + 2 * border
        layer = Image.new("RGBA", (tw + 14, th + 14), (0, 0, 0, 0))
        t = ImageDraw.Draw(layer)
        x0, y0, x1, y1 = 7, 7, 7 + tw - 1, 7 + th - 1
        if on:                                   # 选中：shadow(INK,2)，无 translate 之外的处理
            t.rounded_rectangle([x0 + 2, y0 + 2, x1 + 2, y1 + 2], radius=3, fill=INK + (255,))
        t.rounded_rectangle([x0, y0, x1, y1], radius=3,
                            fill=(active_bg[i] if on else WHITE) + (255,),
                            outline=INK + (255,), width=border)
        fg = INK if on else GRAY
        tx = x0 + border + (tw - 2 * border - text_w(name, f14)) / 2
        draw_text(t, (tx, y0 + border + 5), name, f14, fg)
        tl_x, tl_y = 16 + i * 108, 40
        if on:                                   # CSS translate(-1px,-1px)
            tl_x -= 1
            tl_y -= 1
        place(canvas, layer, tl_x + tw / 2, tl_y + th / 2)

    # 漫画分镜框 pos(18,72) size(204,202)：白底墨框 3 + radius 2 + shadow 3
    shadow_box(d, 18, 72, 221, 273, fill=WHITE, width=3, radius=2, sh_ofs=3)
    # 二维码 174x174 在内容区内居中（lv_obj_center）；上传图会被等比缩放进这个尺寸
    qr = load_qr(sel)
    canvas.paste(qr, (21 + (198 - qr.width) // 2, 75 + (196 - qr.height) // 2))

    # SCAN!：白字红底墨框 2 + shadow(INK,1)，TOP_RIGHT(-22,64)，+3°
    layer, cw, ch = build_chip(QR_BADGE, "latin", 12, WHITE, RED,
                               pad_h=5, pad_v=1, border=2, shadow=INK, sh_ofs=1)
    place(canvas, layer, 217 - (cw - 1) / 2, 64 + (ch - 1) / 2, angle=3.0)

    # 底部按键提示双胶囊：pos(14,286)/(128,286) size(98,24)
    for x, bg, key in ((14, HILITE, KEY_SWITCH), (128, WHITE, KEY_BACK)):
        shadow_box(d, x, 286, x + 97, 309, fill=bg, width=2, radius=3, sh_ofs=2)
        # 键名放大居中的墨底白字 chip（latin_20，pad_h 3，无边框）
        layer, cw, ch = build_chip(key, "latin", 20, WHITE, INK,
                                   pad_h=3, pad_v=0, border=0, radius=1)
        # 居中于胶囊内容区（内缩 border 2）：x 中心 x+49.5、y 中心 298
        place(canvas, layer, x + 2 + (94 - cw) // 2 + cw / 2, 288 + (20 - ch) // 2 + ch / 2)

    return canvas


# --- P3 纪念日倒计时页（badge_page_anniv.c） ----------------------------------
# 复用首页漫画风：米色网点纸 + 白底墨框大分镜面板 + 面板内墨色倒计时窗口 + 黄字红影数字
ANNIV_PANEL = (10, 34, 229, 253)      # 白底墨框大分镜面板（同首页分镜框：3px 框 + shadow 3）
ANNIV_HOLE = (14, 100, 225, 212)      # 面板内墨色倒计时窗口（白框 2 + radius 6）
ANNIV_NUM_SIZE = 52                   # 倒计时数字字号
ANNIV_N = 4                           # 固定 4 个数字槽（不足 4 位左补空槽）
ANNIV_CY = (ANNIV_HOLE[1] + ANNIV_HOLE[3]) // 2      # 数字行垂直中心
ANNIV_DH = line_h("cn", ANNIV_NUM_SIZE) + 8          # 槽高（含错位阴影余量）
ANNIV_Y = ANNIV_CY - ANNIV_DH // 2                   # 槽顶 y
ANNIV_DW = round(max(text_w(c, F("cn", ANNIV_NUM_SIZE)) for c in "0123456789")) + 10
_ANNIV_UNIT_F = F("cn", 26)
_ANNIV_PCX = (ANNIV_HOLE[0] + ANNIV_HOLE[2]) / 2     # 窗口水平中心
_ANNIV_GROUP_W = ANNIV_N * ANNIV_DW + 6 + round(text_w(ANNIV_UNIT, _ANNIV_UNIT_F))
ANNIV_X0 = round(_ANNIV_PCX - _ANNIV_GROUP_W / 2)    # 数字行左缘
ANNIV_UNIT_X = ANNIV_X0 + ANNIV_N * ANNIV_DW + 6     # 单位「天」左缘
ANNIV_SLOTS = tuple((ANNIV_X0 + i * ANNIV_DW, ANNIV_DW) for i in range(ANNIV_N))

# 面板顶部句式行「距{name}还有 / 自{name}以来已」的总宽上限：左缘 px0+8，
# 右缘不超过 D-DAY 角标左缘（≈186）再留 6px 间隙。
ANNIV_SENT_MAX_W = 180 - (ANNIV_PANEL[0] + 8)


def _ord(y: int, m: int, d: int) -> int:
    """Howard Hinnant days_from_civil：公历 → 自 1970-01-01 起的天数（不依赖平台日期库）。"""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def anniv_days(t: datetime.date = None) -> int:
    """按 ANNIV_MODE 算显示天数：until=距离目标日（当天 0）；since=已是第 N 天（当天 1）。"""
    if t is None:
        t = datetime.date.today()
    ay, am, ad = parse_anniv_date(ANNIV_DATE)
    diff = _ord(t.year, t.month, t.day) - _ord(ay, am, ad)
    if ANNIV_MODE == "since":
        return max(0, diff + 1)
    return max(0, -diff)


def _anniv_digits(d: ImageDraw.ImageDraw, s: str) -> None:
    """把 s 右对齐画进 4 个数字槽（黄字 + 红色错位阴影；字形按槽位居中 → 可跨槽复用）。

    左侧空位画暗黄「0」占位（数码管熄灭段）→ 数字组恒占满 4 槽 + 单位，
    整组在墨色窗口内水平居中，短数字时不会出现大片空洞。
    """
    s = s[-ANNIV_N:]
    pad = ANNIV_N - len(s)
    f = F("cn", ANNIV_NUM_SIZE)
    for i in range(ANNIV_N):
        cx = ANNIV_SLOTS[i][0] + ANNIV_DW / 2
        cy = ANNIV_Y + ANNIV_DH / 2
        if i < pad:
            d.text((cx, cy), "0", font=f, fill=ANNIV_OFF + (255,), anchor="mm")
            continue
        ch = s[i - pad]
        d.text((cx + 2, cy + 2), ch, font=f, fill=RED + (255,), anchor="mm")
        d.text((cx, cy), ch, font=f, fill=HILITE + (255,), anchor="mm")


def bake_anniv(*, dynamic: bool = True, days=None, soc: int = 86,
               pct: bool = True, bar: bool = True) -> Image.Image:
    """渲染 P3 纪念日倒计时整页。

    dynamic=False 时不画倒计时数字 / 电量百分比与指示条 → 输出「静态底板」；
    底板与整页的差异严格落在数字槽与电量窗口内，因此「底板 + 精灵块 == 整页」成立。
    顶部右侧电量与首页逐参相同且底色一致 → 直接复用首页的 bat_* 精灵块。
    """
    canvas = Image.new("RGB", (W, H), PAPER)
    canvas.paste(tile_bg(W, H, load_image_c("badge_img_dots_paper")), (0, 0))
    d = ImageDraw.Draw(canvas)

    # 顶部左侧页码徽章 ANNIV // 03（红底白字，-3°）
    layer, cw, ch = build_chip(TAG_ANNIV, "latin", 12, WHITE, RED,
                               pad_h=4, pad_v=1, border=2, shadow=INK, sh_ofs=2)
    place(canvas, layer, 14 + cw / 2, 9 + ch / 2, angle=-3.0)

    # 右上角电量（与首页逐参相同 → 该窗口像素一致，可复用 bat_*）
    draw_battery(canvas, d, -12, 13, soc, pct=dynamic and pct, bar=dynamic and bar)

    # 白底墨框大分镜面板（同首页分镜框：3px 墨框 + shadow 3）
    px0, py0, px1, py1 = ANNIV_PANEL
    shadow_box(d, px0, py0, px1, py1, fill=WHITE, width=3, radius=4, sh_ofs=3)

    # 面板顶部句式行（左对齐，替代原固定「纪念日」标签）：
    # 「距 {name} 还有」/「自 {name} 以来已」——事件名嵌在句子里，黄底小块强调。
    # 总宽超限先降字号（20→14）再截断事件名，右缘不越过右上 D-DAY 角标（≈186）。
    pre, suf = ANNIV_CAP[ANNIV_MODE]
    label = ANNIV_LABEL
    size = 20
    while True:
        fx = F("cn", size)
        wpre, wsuf = text_w(pre, fx), text_w(suf, fx)
        wname = text_w(label, fx)
        wtot = wpre + 4 + wname + 8 + 4 + wsuf          # 名字块左右各 pad 4
        if wtot <= ANNIV_SENT_MAX_W or size <= 14:
            break
        size = 14
    while wtot > ANNIV_SENT_MAX_W and len(label) > 1:   # 最小字号仍超 → 截事件名
        label = label[:-1]
        wname = text_w(label, fx)
        wtot = wpre + 4 + wname + 8 + 4 + wsuf
    sx = px0 + 8
    bh = line_h("cn", size) + 4                          # 名字块高
    sy = ANNIV_HOLE[1] - bh / 2 - 6                      # 句子行贴在表盘上方（块底距表盘 6px）
    d.text((sx, sy), pre, font=fx, fill=INK + (255,), anchor="lm")
    bx0 = sx + wpre + 4
    d.rounded_rectangle([bx0, sy - bh / 2, bx0 + wname + 8, sy + bh / 2],
                        radius=3, fill=HILITE)
    d.text((bx0 + 4 + wname / 2, sy), label, font=fx, fill=INK + (255,), anchor="mm")
    d.text((bx0 + wname + 12 + 4, sy), suf, font=fx, fill=INK + (255,), anchor="lm")

    # 面板内右上：红底白字漫画角标 D-DAY（+6°）
    layer, cw, ch = build_chip(TAG_DDAY, "latin", 12, WHITE, RED,
                               pad_h=4, pad_v=1, border=2, shadow=INK, sh_ofs=2)
    place(canvas, layer, px1 - 6 - cw / 2, py0 + 12 + ch / 2, angle=6.0)

    # 面板内墨色倒计时窗口（白框 2 + radius 6）：黄字红影 4 位数字 + 单位「天」
    hx0, hy0, hx1, hy1 = ANNIV_HOLE
    d.rounded_rectangle([hx0, hy0, hx1, hy1], radius=6, fill=INK, outline=WHITE, width=2)
    d.text((ANNIV_UNIT_X, ANNIV_Y + ANNIV_DH / 2), ANNIV_UNIT,
           font=_ANNIV_UNIT_F, fill=HILITE + (255,), anchor="lm")
    if dynamic:
        _anniv_digits(d, str(days if days is not None else anniv_days()))

    # 表盘下方一行：「{name}：{日期}」（墨字，居中；名称过长则截断保护）
    fx2 = F("cn", 14)
    cap_name = ANNIV_LABEL
    cap = "%s：%s" % (cap_name, ANNIV_DATE)
    while text_w(cap, fx2) > ANNIV_PANEL[2] - ANNIV_PANEL[0] - 8 and len(cap_name) > 1:
        cap_name = cap_name[:-1]
        cap = "%s：%s" % (cap_name, ANNIV_DATE)
    d.text((_ANNIV_PCX, hy1 + 16), cap, font=fx2, fill=INK + (255,), anchor="ma")

    # 底部按键提示双胶囊（与 P2 同款）
    for x, bg, key in ((14, HILITE, KEY_SWITCH), (128, WHITE, KEY_BACK)):
        shadow_box(d, x, 286, x + 97, 309, fill=bg, width=2, radius=3, sh_ofs=2)
        layer, cw, ch = build_chip(key, "latin", 20, WHITE, INK,
                                   pad_h=3, pad_v=0, border=0, radius=1)
        place(canvas, layer, x + 2 + (94 - cw) // 2 + cw / 2, 288 + (20 - ch) // 2 + ch / 2)

    return canvas


# --- 输出 ---------------------------------------------------------------------
def to_rgb565_be(img: Image.Image) -> bytes:
    """RGB888 → RGB565 **大端**（st7789p3.draw_image 要求大端，与 LVGL 小端相反）。"""
    out = bytearray()
    for r, g, b in img.convert("RGB").getdata():
        out += (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)).to_bytes(2, "big")
    return bytes(out)


# --- P1 动态精灵块：烘焙 → 打包 → 生成偏移表 → 逐像素校验 ----------------------
def _crop(page: Image.Image, rect) -> Image.Image:
    x, y, w, h = rect
    return page.crop((x, y, x + w, y + h))


def _diff_window(a: Image.Image, b: Image.Image, pad: int = 2) -> tuple:
    """两页像素差异的合围框（外扩 pad），用于自动求「动态元素」窗口。"""
    from PIL import ImageChops
    x0, y0, x1, y1 = ImageChops.difference(a, b).getbbox()
    return (max(0, x0 - pad), max(0, y0 - pad),
            min(W, x1 + pad) - max(0, x0 - pad), min(H, y1 + pad) - max(0, y0 - pad))


# P2 头条电量窗口：soc=100（满文本+满条）与 soc=0（最短文本+无条）逐像素差异，
# 自动覆盖百分比文本与指示条，几何参数变了无需手调。
WIN_BAT_QR = _diff_window(bake_qr(sel=0, soc=100), bake_qr(sel=0, soc=0, bar=False))


def sprite_rect(name: str):
    """精灵块的规范窗口 (x, y, w, h)（日期块的 x 取该类的首个槽位）。"""
    if name.startswith("hh_"):
        return WIN_HH
    if name.startswith("mm_"):
        return WIN_MM
    if name.startswith("col_"):
        return WIN_COL
    if name.startswith("bat_"):
        return WIN_BAT
    if name.startswith("batq_"):
        return WIN_BAT_QR
    if name.startswith("num_"):                       # 纪念日数字：x 取末槽位
        return (ANNIV_SLOTS[-1][0], ANNIV_Y, ANNIV_DW, ANNIV_DH)
    body = name[5:]                                   # date_xxx
    k = "d" if body.isdigit() else ("wd" if body.startswith("wd") else body)
    return ({"d": 21, "da": 53, "zh": 98, "wd": 112}[k], DATE_Y, DATE_W[k], DATE_H)


def build_sprites() -> dict:
    """渲染全部精灵块（name → RGB Image）。精灵块 = 整页渲染后裁窗口。"""
    spr = {}
    for i in range(24):
        s = f"{i:02d}"
        spr[f"hh_{s}"] = _crop(bake_home(hh=s), WIN_HH)
    for i in range(60):
        s = f"{i:02d}"
        spr[f"mm_{s}"] = _crop(bake_home(mm=s), WIN_MM)
    spr["col_on"] = _crop(bake_home(), WIN_COL)
    spr["col_off"] = _crop(bake_home(colon_dim=True), WIN_COL)

    # 日期：数字用两页凑齐 0-9；cn_14 等宽 8px，字形相对槽位左缘对齐 → 可跨槽复用
    pA = bake_home(date_text="1234-56-78 周一")
    pB = bake_home(date_text="9000-00-00 周一")
    src = {"1": (pA, 0), "2": (pA, 1), "3": (pA, 2), "4": (pA, 3),
           "5": (pA, 5), "6": (pA, 6), "7": (pA, 8), "8": (pA, 9),
           "9": (pB, 0), "0": (pB, 1)}
    for c, (page, si) in src.items():
        x = DATE_SLOTS[si][1]
        spr[f"date_{c}"] = _crop(page, (x, DATE_Y, DATE_W["d"], DATE_H))
    spr["date_da"] = _crop(pA, (53, DATE_Y, DATE_W["da"], DATE_H))
    for wi, wc in enumerate(WEEKDAYS):
        p = bake_home(date_text=f"2024-10-20 周{wc}")
        spr[f"date_wd_{wi}"] = _crop(p, (112, DATE_Y, DATE_W["wd"], DATE_H))
        if wi == 0:
            spr["date_zh"] = _crop(p, (98, DATE_Y, DATE_W["zh"], DATE_H))

    # 电量：0..100 逐值 + 低电量闪烁的「条暗态」变体（0..5）
    for soc in range(101):
        spr[f"bat_{soc:03d}"] = _crop(bake_home(soc=soc), WIN_BAT)
    for soc in range(6):
        spr[f"bat_{soc:03d}_off"] = _crop(bake_home(soc=soc, bar=False), WIN_BAT)

    # P2 头条电量（黄底，与首页米底不同 → 独立裁一套；两 sel 的电量区像素相同，共用）
    for soc in range(101):
        spr[f"batq_{soc:03d}"] = _crop(bake_qr(sel=0, soc=soc), WIN_BAT_QR)
    for soc in range(6):
        spr[f"batq_{soc:03d}_off"] = _crop(bake_qr(sel=0, soc=soc, bar=False),
                                           WIN_BAT_QR)

    # 纪念日倒计时数字：cn 数字非等宽 → 逐数字烘焙；字形按槽位居中 → 可跨槽复用
    for dv in range(10):
        page = bake_anniv(days="999%d" % dv)          # 4 位 → 末槽即该数字
        x = ANNIV_SLOTS[-1][0]
        spr[f"num_{dv}"] = _crop(page, (x, ANNIV_Y, ANNIV_DW, ANNIV_DH))
    spr["num_0_dim"] = _crop(bake_anniv(days="123"),   # 空位占位「0」（槽 0 即前导空位）
                             (ANNIV_SLOTS[0][0], ANNIV_Y, ANNIV_DW, ANNIV_DH))
    return spr


def pack(spr: dict):
    """拼成单个 sprites.raw + 偏移表 {name: (offset, x, y, w, h)}。"""
    payload = bytearray()
    table = {}
    for name in sorted(spr):
        img = spr[name]
        x, y, w, h = sprite_rect(name)
        if img.size != (w, h):
            raise SystemExit(f"精灵块尺寸不符：{name} {img.size} != {(w, h)}")
        table[name] = (len(payload), x, y, w, h)
        payload += to_rgb565_be(img)
    return bytes(payload), table


def compose(base: Image.Image, spr: dict, hh: str, mm: str, colon_dim: bool,
            soc: int, date_text: str, bar: bool = True) -> Image.Image:
    """按板端逻辑（底板 + 精灵块定点覆盖）复算出整页，用于校验。"""
    img = base.copy()
    wi = WEEKDAYS.index(date_text[12])
    for i, (kind, x, _w) in enumerate(DATE_SLOTS):
        if kind is None:
            continue
        nm = {"d": f"date_{date_text[i]}", "da": "date_da",
              "zh": "date_zh"}.get(kind, f"date_wd_{wi}")
        img.paste(spr[nm], (x, DATE_Y))
    for nm in (f"hh_{hh}", "col_off" if colon_dim else "col_on", f"mm_{mm}",
               f"bat_{soc:03d}" if bar else f"bat_{soc:03d}_off"):
        x, y, _w, _h = sprite_rect(nm)
        img.paste(spr[nm], (x, y))
    return img


def verify(base: Image.Image, spr: dict) -> None:
    """断言「底板 + 精灵块 == 整页」逐像素成立（含零回归基准）。"""
    t = datetime.date.today()
    today_txt = f"{t.year}-{t.month:02d}-{t.day:02d} {WEEK_PREFIX}{WEEKDAYS[t.weekday()]}"
    states = [
        ("10", "24", False, 86, today_txt, True),              # 与原 P1 一致（零回归基准）
        ("00", "00", True, 0, "2000-01-01 周六", True),
        ("08", "48", False, 5, "2026-10-22 周四", True),
        ("23", "59", False, 100, "2099-12-31 周日", True),
        ("19", "07", True, 5, "2035-06-03 周一", False),       # 低电量：条暗态
        ("12", "34", False, 1, "2024-01-09 周二", True),
    ]
    bad = 0
    for hh, mm, dim, soc, dt, bar in states:
        got = compose(base, spr, hh, mm, dim, soc, dt, bar)
        ref = bake_home(soc=soc, hh=hh, mm=mm, date_text=dt,
                        dynamic=True, colon_dim=dim, bar=bar)
        bb = ImageChops.difference(got, ref).convert("L").getbbox()
        tag = f"hh={hh} mm={mm} dim={int(dim)} soc={soc:3d} bar={int(bar)} {dt}"
        if bb is None:
            print(f"  ok   {tag}")
        else:
            bad += 1
            print(f"  FAIL {tag} -> 差异窗口 {bb}")
    if bad:
        raise SystemExit(f"底板 + 精灵块 != 整页：{bad}/{len(states)} 个状态不一致")
    print(f"  校验通过：底板 + 精灵块 == 整页（逐像素，{len(states)} 个状态）")


def compose_anniv(base: Image.Image, spr: dict, days, soc: int, bar: bool = True) -> Image.Image:
    """按板端逻辑（底板 + 数字/电量精灵块定点覆盖）复算出纪念日整页，用于校验。"""
    img = base.copy()
    s = str(days)[-ANNIV_N:]
    pad = ANNIV_N - len(s)
    for i in range(pad):
        img.paste(spr["num_0_dim"], (ANNIV_SLOTS[i][0], ANNIV_Y))
    for i, ch in enumerate(s):
        img.paste(spr["num_" + ch], (ANNIV_SLOTS[pad + i][0], ANNIV_Y))
    nm = f"bat_{soc:03d}" if bar else f"bat_{soc:03d}_off"
    x, y, _w, _h = sprite_rect(nm)
    img.paste(spr[nm], (x, y))
    return img


def verify_anniv(base: Image.Image, spr: dict) -> None:
    """断言「纪念日底板 + 精灵块 == 整页」逐像素成立（位数变化 + 电量亮/暗态）。"""
    states = [
        (0, 86, True),
        (7, 100, True),
        (42, 0, True),
        (365, 5, False),        # 低电量：指示条暗态
        (1234, 12, True),
        (9999, 100, True),
    ]
    bad = 0
    for days, soc, bar in states:
        got = compose_anniv(base, spr, days, soc, bar)
        ref = bake_anniv(days=days, soc=soc, dynamic=True, bar=bar)
        bb = ImageChops.difference(got, ref).convert("L").getbbox()
        tag = f"days={days:4d} soc={soc:3d} bar={int(bar)}"
        if bb is None:
            print(f"  ok   {tag}")
        else:
            bad += 1
            print(f"  FAIL {tag} -> 差异窗口 {bb}")
    if bad:
        raise SystemExit(f"纪念日底板 + 精灵块 != 整页：{bad}/{len(states)} 个状态不一致")
    print(f"  校验通过：纪念日底板 + 精灵块 == 整页（逐像素，{len(states)} 个状态）")


def compose_qr(base: Image.Image, spr: dict, soc: int, bar: bool = True) -> Image.Image:
    """按板端逻辑（底板 + batq_* 电量块定点覆盖）复算出 P2 整页，用于校验。"""
    img = base.copy()
    nm = f"batq_{soc:03d}" if bar else f"batq_{soc:03d}_off"
    x, y, _w, _h = sprite_rect(nm)
    img.paste(spr[nm], (x, y))
    return img


def verify_qr(base: Image.Image, spr: dict) -> None:
    """断言「P2 底板 + 电量块 == 整页」逐像素成立（两个 sel × 电量亮/暗态）。"""
    states = [(100, True), (86, True), (20, True), (5, False), (0, True)]
    bad = 0
    for sel, tag in ((0, "钉钉"), (1, "微信")):
        base = bake_qr(sel=sel, dynamic=False, bar=False)
        for soc, bar in states:
            got = compose_qr(base, spr, soc, bar)
            ref = bake_qr(sel=sel, soc=soc, dynamic=True, bar=bar)
            bb = ImageChops.difference(got, ref).convert("L").getbbox()
            label = f"sel={tag} soc={soc:3d} bar={int(bar)}"
            if bb is None:
                print(f"  ok   {label}")
            else:
                bad += 1
                print(f"  FAIL {label} -> 差异窗口 {bb}")
    if bad:
        raise SystemExit(f"P2 底板 + 电量块 != 整页：{bad}/{len(states) * 2} 个状态不一致")
    print(f"  校验通过：P2 底板 + 电量块 == 整页（逐像素，{len(states) * 2} 个状态）")


def write_sprites_py(table: dict) -> str:
    out = [
        "# -*- coding: utf-8 -*-",
        '"""由 tools/bake_badge.py 自动生成，请勿手改。P1 首页动态精灵块偏移表。',
        "",
        "SPR[name] = (offset, x, y, w, h)：offset 为 sprites.raw 内字节偏移，",
        "(x, y) 为 blit 起点，(w, h) 为窗口尺寸，payload = w*h*2 字节 RGB565 大端。",
        "日期块的 x 仅取该类的首个槽位，实际槽位见 DATE_SLOTS；",
        "纪念日数字块的 x 仅取末槽位，实际槽位见 ANNIV_SLOTS。",
        '"""',
        "",
        'RAW = "sprites.raw"',
        f'WEEKDAYS = "{WEEKDAYS}"',
        f"DATE_Y = {DATE_Y}",
        f"DATE_W = {DATE_W!r}",
        "DATE_SLOTS = (",
    ]
    for kind, x, w in DATE_SLOTS:
        out.append(f"    ({kind!r}, {x}, {w}),")
    out += [
        ")",
        "",
        f"ANNIV_N = {ANNIV_N}",
        f"ANNIV_Y = {ANNIV_Y}",
        f"ANNIV_DW = {ANNIV_DW}",
        f"ANNIV_DH = {ANNIV_DH}",
        f"ANNIV_DATE = {ANNIV_DATE!r}",
        f"ANNIV_YMD = {parse_anniv_date(ANNIV_DATE)!r}",
        f"ANNIV_MODE = {ANNIV_MODE!r}",
        "ANNIV_SLOTS = (",
    ]
    for x, w in ANNIV_SLOTS:
        out.append(f"    ({x}, {w}),")
    out += [
        ")",
        "",
        "SPR = {",
    ]
    for name in sorted(table):
        out.append(f"    {name!r}: {table[name]!r},")
    out += ["}", ""]
    return "\n".join(out)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # P1 静态底板（板端只画一次）+ 整页预览
    base = bake_home(dynamic=False, bar=False)
    raw = to_rgb565_be(base)
    (OUT_DIR / "home.raw").write_bytes(raw)
    print(f"+ home.raw (静态底板)  {W}x{H}  {len(raw)} B")
    full = bake_home()
    full.save(OUT_DIR / "home.png")
    full.resize((W * 2, H * 2), Image.NEAREST).save(OUT_DIR / "home_2x.png")

    # P3 纪念日倒计时页：静态底板 + 整页预览
    abase = bake_anniv(dynamic=False, bar=False)
    araw = to_rgb565_be(abase)
    (OUT_DIR / "anniv.raw").write_bytes(araw)
    print(f"+ anniv.raw (静态底板)  {W}x{H}  {len(araw)} B")
    afull = bake_anniv()
    afull.save(OUT_DIR / "anniv.png")
    afull.resize((W * 2, H * 2), Image.NEAREST).save(OUT_DIR / "anniv_2x.png")

    for sel, tag in ((0, "qr_ding"), (1, "qr_wx")):
        qbase = bake_qr(sel, dynamic=False, bar=False)     # 静态底板（板端上屏那张）
        raw = to_rgb565_be(qbase)
        (OUT_DIR / f"{tag}.raw").write_bytes(raw)
        print(f"+ {tag}.raw (静态底板)  {W}x{H}  {len(raw)} B")
        qr = bake_qr(sel)
        qr.save(OUT_DIR / f"{tag}.png")
        qr.resize((W * 2, H * 2), Image.NEAREST).save(OUT_DIR / f"{tag}_2x.png")

    # P1 动态精灵块：烘焙 → 打包 → 偏移表 → 校验
    spr = build_sprites()
    payload, table = pack(spr)
    (OUT_DIR / "sprites.raw").write_bytes(payload)
    (TOOLS / "sprites.py").write_text(write_sprites_py(table), encoding="utf-8")
    print(f"+ sprites.raw  {len(table)} 块  {len(payload)} B")
    print(f"+ tools/sprites.py  {len(table)} 条")
    verify(base, spr)
    verify_anniv(abase, spr)
    verify_qr(bake_qr(sel=0, dynamic=False, bar=False), spr)


if __name__ == "__main__":
    main()