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
MPB = TOOLS.parent                                   # mp-badge-clean/
OUT_DIR = MPB / "generated"

# 本工程完全自包含：出厂素材与开源字体随工程一起走，不依赖任何外部仓库。
VENDOR = TOOLS / "vendor"
IMG_DIR = VENDOR / "images"                          # 出厂素材（角色图 / 网点 / 占位二维码 .c）
FONT_SRC = VENDOR / "fonts"                          # 开源字体（思源黑体 / Bangers）

SHS = FONT_SRC / "SourceHanSansCN-Bold.otf"          # 中文字：思源黑体 Bold
BANGERS = FONT_SRC / "Bangers-Regular.ttf"           # 拉丁/数字：Bangers 漫画体

# 配置页可选的字体（全部随工程自带，工具自包含，不依赖系统字体目录）。
# 仅收录**开源授权**字体，规避公开发布的字体授权风险。
# "cn"/"latin"/"time" 是页面固定元素（日期章/标签/时钟）用的内部字体，不对外开放。
FONT_DIR = TOOLS / "fonts"
FONTS = {
    "cn":      {"file": SHS,                            "label": "思源黑体 Bold（默认）"},
    "latin":   {"file": BANGERS,                        "label": "Bangers（默认数字/拉丁）"},
    "time":    {"file": BANGERS,                        "label": "Bangers"},
    "noto_sc": {"file": FONT_DIR / "NotoSansSC-VF.ttf", "label": "Noto Sans SC"},
}

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

# --- 画布与色板（badge_theme.h） ----------------------------------------------
W, H = 240, 320
PAPER = (0xFF, 0xFD, 0xE8)      # BADGE_PAPER
PAPER_POP = (0xFF, 0xFD, 0xF0)  # BADGE_PAPER_POP
INK = (0x11, 0x11, 0x11)        # BADGE_INK
HILITE = (0xFF, 0xE6, 0x00)     # BADGE_HIGHLIGHT
RED = (0xFF, 0x2A, 0x55)        # BADGE_RED
CYAN = (0x00, 0xE5, 0xFF)       # BADGE_SCAN_CYAN
WGREEN = (0x00, 0xC8, 0x53)     # BADGE_WECHAT_GREEN
GRAY = (0x88, 0x88, 0x88)       # BADGE_GRAY
WHITE = (0xFF, 0xFF, 0xFF)      # BADGE_PANEL_WHITE
BATT_GREEN = (0x00, 0xC8, 0x53)
BATT_AMBER = (0xFF, 0xA0, 0x00)


def blend_over(fg, bg, alpha: float):
    """把 fg 以 alpha 叠到不透明 bg 上，返回等价的不透明色（供预烘焙精灵块使用）。"""
    return tuple(round(f * alpha + b * (1.0 - alpha)) for f, b in zip(fg, bg))


# 冒号闪烁暗态：LVGL 把整层降到 LV_OPA_30 叠在 INK 上，
# 颜色混合是线性的 → 等价于用预混色以不透明方式画在 INK 背景上。
COLON_OPA_DIM = 0.30
COL_OFF_FG = blend_over(HILITE, INK, COLON_OPA_DIM)   # 暗态字
COL_OFF_SH = blend_over(RED, INK, COLON_OPA_DIM)      # 暗态错位阴影

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

PROFILE_JSON = TOOLS / "profile.json"


def load_profile() -> None:
    """存在 tools/profile.json 时，用它覆盖文案与字体字号（非法字体键/字号退回默认）。"""
    global NAME, ROLE, BIO
    global NAME_FONT, NAME_SIZE, ROLE_FONT, ROLE_SIZE, BIO_FONT, BIO_SIZE
    global AVATAR_ZOOM, AVATAR_DX, AVATAR_DY
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


load_profile()

TAG_HOME = "HERO // 01"
SFX = "BOOM!"
CAP_HEADER = "PROFILE // EPISODE 01"
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
    d.text((hx, hy), CAP_HEADER, font=ft, fill=(0x55, 0x55, 0x55, 255), anchor="la")
    d.line([hx, hy + 13, 114 + 116 - 2 - 6, hy + 13], fill=INK, width=1)
    fd = F(BIO_FONT, BIO_SIZE)
    step = line_h(BIO_FONT, BIO_SIZE) + 1        # 默认 cn_20 时 = 22，零回归
    for i, ln in enumerate(wrap(BIO, fd, 102)):
        d.text((hx, hy + 20 + i * step), ln, font=fd, fill=INK + (255,), anchor="la")

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
def bake_qr(sel: int = 0, soc: int = 86) -> Image.Image:
    """sel: 0=钉钉（Tab0 选中，扫描青）1=微信（Tab1 选中，波普绿）。"""
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
    draw_battery(canvas, d, -7, 5, soc, p_right=right_c, p_top=top_c)

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


def write_sprites_py(table: dict) -> str:
    out = [
        "# -*- coding: utf-8 -*-",
        '"""由 tools/bake_badge.py 自动生成，请勿手改。P1 首页动态精灵块偏移表。',
        "",
        "SPR[name] = (offset, x, y, w, h)：offset 为 sprites.raw 内字节偏移，",
        "(x, y) 为 blit 起点，(w, h) 为窗口尺寸，payload = w*h*2 字节 RGB565 大端。",
        "日期块的 x 仅取该类的首个槽位，实际槽位见 DATE_SLOTS。",
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
    out += [")", "", "SPR = {"]
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

    for sel, tag in ((0, "qr_ding"), (1, "qr_wx")):
        qr = bake_qr(sel)
        qr.save(OUT_DIR / f"{tag}.png")
        raw = to_rgb565_be(qr)
        (OUT_DIR / f"{tag}.raw").write_bytes(raw)
        print(f"+ {tag}.raw  {W}x{H}  {len(raw)} B")
        qr.resize((W * 2, H * 2), Image.NEAREST).save(OUT_DIR / f"{tag}_2x.png")

    # P1 动态精灵块：烘焙 → 打包 → 偏移表 → 校验
    spr = build_sprites()
    payload, table = pack(spr)
    (OUT_DIR / "sprites.raw").write_bytes(payload)
    (TOOLS / "sprites.py").write_text(write_sprites_py(table), encoding="utf-8")
    print(f"+ sprites.raw  {len(table)} 块  {len(payload)} B")
    print(f"+ tools/sprites.py  {len(table)} 条")
    verify(base, spr)


if __name__ == "__main__":
    main()