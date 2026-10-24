#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工牌配置页（MicroPython 路线的 PC 端小工具）。

改「姓名 / 角色岗位 / 简介」及其**字体与字号** → 浏览器里实时预览 → 一键生成 home.raw 并上传到板子。
另支持上传**头像人像**（分镜框内，可拖动/缩放取景）、**微信 / 钉钉二维码**（自适应进白框）与
**启动页图片**（整屏 240×320，cover 铺满）；上传图只替换对应画面元素，HERO 角标与 BOOM 贴纸等装饰保持不变。

为什么不用重刷固件：板端 home.py 只做「读 home.raw 整屏画一次 + 定点贴小块」，
姓名/岗位/简介全部烘焙在 home.raw 这张静态底板里，且它们所在的右栏与
时钟/日期/电量三个精灵块窗口不重叠，因此换配置 = 只换 home.raw 一个文件。
（字体/字号同理：只在 PC 端烘焙时生效，板端无需任何改动，也不用重传 sprites。）

本工具直接 import bake_badge 复用渲染管线 → 页面预览与板子上屏逐像素一致。

运行：python mp-badge/tools/profile_studio.py
然后浏览器打开 http://127.0.0.1:8519

安全：只绑 127.0.0.1；上传仅在页面上显式点击「生成并上传」后执行。
"""
from __future__ import annotations

import base64
import io
import json
import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
MPB = HERE.parent
GEN = MPB / "generated"
PROFILE_JSON = HERE / "profile.json"
HTML_PATH = HERE / "profile_studio.html"
RENDER_LOCK = threading.Lock()   # 保护 bake_badge 的模块级 NAME/ROLE/BIO

RAW_NAME = "home.raw"
ANNIV_RAW = "anniv.raw"                               # 板端 anniv.py 读这个文件名
QR_RAW = {"ding": "qr_ding.raw", "wx": "qr_wx.raw"}   # 板端 qr.py 读这两个文件名
SPLASH_RAW = "splash.raw"                             # 板端 splash.py 读这个文件名
DEFAULT_PORT = "COM7"
UPLOAD_MAX_SIDE = 1600   # 上传图长边超过它就先在 PC 端等比缩小，避免超慢缩放

sys.path.insert(0, str(HERE))
import bake_badge as B  # noqa: E402  复用渲染管线（含 profile.json 读取）

# 纪念日页（P3）配置：出厂默认值取自烘焙侧常量，配置非法时回退到它
ANNIV_MODES = ("until", "since")
ANNIV_LABEL_DEFAULT = B.ANNIV_LABEL
ANNIV_DATE_DEFAULT = B.ANNIV_DATE
ANNIV_LABEL_PX = B.ANNIV_SENT_MAX_W   # 顶部句式行总宽上限（事件名过长会被截断）

WIFI_CFG = MPB / "wifi_cfg.py"        # PC 副本 = WiFi 配置真相源，上传即把这份传到板子
WIFI_MAX_NETS = 8                     # 板端逐个尝试有超时成本，限制候选数量

SPLASH_W, SPLASH_H = B.W, B.H   # 启动页整屏 240x320

# 上传素材槽位：路径与烘焙侧 bake_badge 共用，保证「上传即烘焙即上屏」同一份文件
ASSET_DIR = B.ASSET_DIR
SPLASH_PNG = ASSET_DIR / "splash.png"   # 用户上传的启动图；未上传时回退工程现有 splash.raw
ASSETS = {
    "avatar":  {"path": B.AVATAR_PNG,      "label": "头像人像",   "fit": "cover"},
    "qr_ding": {"path": B.QR_PNG["ding"],  "label": "钉钉二维码", "fit": "contain"},
    "qr_wx":   {"path": B.QR_PNG["wx"],    "label": "微信二维码", "fit": "contain"},
    "splash":  {"path": SPLASH_PNG,        "label": "启动页图片", "fit": "cover"},
}

# 配置页对外开放的字体：排除 "latin"/"time"（日期章/标签/时钟用的内部字体）
SELECTABLE_FONTS = tuple(k for k in B.FONTS if k not in ("latin", "time"))
FONT_LABEL = {k: B.FONTS[k]["label"] for k in SELECTABLE_FONTS}
# 不含中文字形的字体：中文会渲染成空白/方块，页面需提示
LATIN_ONLY_FONTS = {"impact", "ariblk", "segoeui_b"}
DEFAULT_FONT = "cn"

# 套用与上屏一致的版面容量的软限制（超出会被裁掉/画到框外，页面给提示）
NAME_INNER_PX = B.NAME_BOX_INNER_W   # 姓名块内框可用宽（与 bake_home 自适应字距同一基准）
ROLE_INNER_PX = 100   # 岗位条幅基准内容区：116 - 2*2(border) - 2*6(pad_h)
BIO_WRAP_PX = 102     # 与 bake_home 中 wrap(BIO, ..., 102) 一致
BIO_TOP, BIO_BOTTOM = 163, 245   # 简介正文首行顶点 / 内容区下缘
ELEM_LABEL = {"name": "姓名", "role": "角色岗位", "bio": "简介"}


# ---------------------------------------------------------------------------
# profile 读写与校验
# ---------------------------------------------------------------------------

def _anniv_date_ok(s) -> str | None:
    """校验目标日期并归一化为固定格式「xxxx年x月x日」，非法返回 None。"""
    ymd = B.parse_anniv_date(s)
    if ymd is None:
        return None
    return "%d年%d月%d日" % ymd


# ---------------------------------------------------------------------------
# WiFi 配置（wifi_cfg.py）：解析现文件 / 生成新文件
# ---------------------------------------------------------------------------

_WIFI_TEMPLATE = """# FoloToy AI Passport —— WiFi 连接配置（PC 配置页生成，也可手改）
#
# 只填 2.4G 频段的 SSID（ESP32-C3 不支持 5G）。
# 注意：本文件以明文形式存放在板子 flash 上。
#
# 支持配置多个 WiFi：换环境后板子按列表顺序逐个尝试（先连扫描到的在场网络，
# 不在场的放最后兜底），哪个连上就用哪个校时。按需增删条目即可。

NETS = (
%s
)
"""


def parse_wifi_cfg() -> list:
    """从 PC 副本 wifi_cfg.py 安全解析 NETS（literal_eval，不执行代码）；无则空列表。"""
    try:
        import ast
        tree = ast.parse(WIFI_CFG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 文件缺失/语法坏 → 视作未配置
        return []
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "NETS"
                        for t in node.targets)):
            try:
                val = ast.literal_eval(node.value)
                return [[str(s), str(p)] for s, p in val]
            except Exception:  # noqa: BLE001
                return []
    return []


def render_wifi_cfg(nets: list) -> str:
    """把候选列表生成为板端 wifi_cfg.py 文本（repr 转义引号/反斜杠等字符）。"""
    rows = "\n".join("    (%r, %r)," % (str(s), str(p)) for s, p in nets)
    return _WIFI_TEMPLATE % rows


def clean_wifi_nets(raw) -> list:
    """校验页面提交的 WiFi 列表：剔除空 SSID、同 SSID 去重、限制数量。"""
    if not isinstance(raw, list):
        raise RuntimeError("WiFi 列表格式不对")
    out: list = []
    for item in raw:
        try:
            s = str(item[0]).strip()
            p = str(item[1])
        except Exception:  # noqa: BLE001
            continue
        if not s or any(s == r[0] for r in out):
            continue
        out.append([s, p])
        if len(out) >= WIFI_MAX_NETS:
            break
    if not out:
        raise RuntimeError("至少需要配置一个 WiFi（2.4G 频段）")
    return out


def current_profile() -> dict:
    return {"name": B.NAME, "role": B.ROLE, "bio": B.BIO,
            "name_font": B.NAME_FONT, "name_size": B.NAME_SIZE,
            "role_font": B.ROLE_FONT, "role_size": B.ROLE_SIZE,
            "bio_font": B.BIO_FONT, "bio_size": B.BIO_SIZE,
            "avatar_zoom": float(B.AVATAR_ZOOM),
            "avatar_dx": float(B.AVATAR_DX),
            "avatar_dy": float(B.AVATAR_DY),
            "anniv_label": B.ANNIV_LABEL,
            "anniv_date": B.ANNIV_DATE,
            "anniv_mode": B.ANNIV_MODE,
            "wifi": parse_wifi_cfg()}


def set_profile(p: dict) -> None:
    B.NAME, B.ROLE, B.BIO = p["name"], p["role"], p["bio"]
    B.NAME_FONT, B.NAME_SIZE = p["name_font"], p["name_size"]
    B.ROLE_FONT, B.ROLE_SIZE = p["role_font"], p["role_size"]
    B.BIO_FONT, B.BIO_SIZE = p["bio_font"], p["bio_size"]
    B.AVATAR_ZOOM = float(p.get("avatar_zoom", 1.0))
    B.AVATAR_DX = float(p.get("avatar_dx", 0.0))
    B.AVATAR_DY = float(p.get("avatar_dy", 0.0))
    # 纪念日页：非法值一律回退默认，保证烘焙永远拿到合法配置（页面侧由 measure 报错拦截）
    B.ANNIV_LABEL = p.get("anniv_label") or ANNIV_LABEL_DEFAULT
    B.ANNIV_MODE = p.get("anniv_mode") if p.get("anniv_mode") in ANNIV_MODES else "until"
    B.ANNIV_DATE = _anniv_date_ok(p.get("anniv_date")) or ANNIV_DATE_DEFAULT


def clean_profile(body: dict) -> dict:
    """校验并归一化：非法字体键退回默认字体，非档位字号退回该字段默认字号。"""
    out = {k: str(body.get(k) or "").strip() for k in ("name", "role", "bio")}
    defaults = {"name": ("cn", B.NAME_SIZE), "role": ("cn", B.ROLE_SIZE),
                "bio": ("cn", B.BIO_SIZE)}
    for elem in ("name", "role", "bio"):
        fk = str(body.get(elem + "_font") or DEFAULT_FONT)
        if fk not in SELECTABLE_FONTS:
            fk = DEFAULT_FONT
        sizes = B.ELEM_SIZES[elem]
        try:
            sz = int(body.get(elem + "_size"))
        except (TypeError, ValueError):
            sz = -1
        if sz not in sizes:
            sz = defaults[elem][1] if defaults[elem][1] in sizes else sizes[0]
        out[elem + "_font"] = fk
        out[elem + "_size"] = sz
    # 头像取景：zoom 夹到合法区间，dx/dy 非法一律回退 0（即居中取景）
    try:
        zoom = float(body.get("avatar_zoom", 1.0))
    except (TypeError, ValueError):
        zoom = 1.0
    out["avatar_zoom"] = min(max(zoom, B.AVATAR_ZOOM_MIN), B.AVATAR_ZOOM_MAX)
    for k in ("avatar_dx", "avatar_dy"):
        try:
            out[k] = float(body.get(k, 0.0))
        except (TypeError, ValueError):
            out[k] = 0.0
    # 纪念日页：标签空串回退默认；模式白名单；日期合法则归一化、非法保留原串待 measure 报错
    out["anniv_label"] = str(body.get("anniv_label") or "").strip() or ANNIV_LABEL_DEFAULT
    out["anniv_mode"] = (body.get("anniv_mode") if body.get("anniv_mode") in ANNIV_MODES
                         else "until")
    out["anniv_date"] = _anniv_date_ok(body.get("anniv_date")) or str(
        body.get("anniv_date") or "").strip()
    return out


def save_profile_json(p: dict) -> None:
    # newline="\n"：Windows 下文本模式默认会把 \n 写成 \r\n，与工程其余源文件的 LF 不一致
    PROFILE_JSON.write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8", newline="\n")


def measure(p: dict) -> tuple[dict, list[dict]]:
    """按真实排版（含所选字体/字号）量出占用宽度/行数，返回 (指标, 提示列表)。"""
    name, role, bio = p["name"], p["role"], p["bio"]
    # 与 bake_home 同一套自适应字距：按姓名框内框宽度算，含汉字沿用装饰字距、纯拉丁收紧
    nls = B.name_letter_space(name, p["name_font"], p["name_size"]) if name else 0
    nw = B.text_w(name, B.F(p["name_font"], p["name_size"]), nls) if name else 0.0
    rw = B.text_w(role, B.F(p["role_font"], p["role_size"])) if role else 0.0
    step = B.line_h(p["bio_font"], p["bio_size"]) + 1
    lines = B.wrap(bio, B.F(p["bio_font"], p["bio_size"]), BIO_WRAP_PX) if bio else []
    bio_max = max(1, (BIO_BOTTOM - BIO_TOP) // step)

    hints: list[dict] = []
    if not name:
        hints.append({"level": "err", "text": "姓名不能为空"})
    elif nw > NAME_INNER_PX:
        hints.append({"level": "warn", "text":
                      "姓名宽 %.0f px 已超出姓名框内框 %.0f px（字距已自动压到 %d px，"
                      "字号保持你所选档位），多出的部分会被裁掉；建议缩小字号或减少字数"
                      % (nw, NAME_INNER_PX, nls)})
    if not role:
        hints.append({"level": "err", "text": "角色岗位不能为空"})
    elif rw > ROLE_INNER_PX:
        hints.append({"level": "warn", "text":
                      "岗位宽 %.0f px 已超出条幅内容区 %.0f px（条幅会自动加宽，"
                      "过长会顶到屏幕右缘）；建议缩小字号或减少字数" % (rw, ROLE_INNER_PX)})
    if len(lines) > bio_max:
        hints.append({"level": "warn", "text":
                      "简介排成 %d 行，超过旁白框 %d 行容量（当前字号行距 %d px），"
                      "后面的行会画到框外；建议缩小字号或精简文案"
                      % (len(lines), bio_max, step)})
    for elem in ("name", "role", "bio"):
        if p[elem] and p[elem + "_font"] in LATIN_ONLY_FONTS:
            hints.append({"level": "warn", "text":
                          "%s 用的是 %s（纯拉丁字体），其中的中文会渲染成空白/方块，"
                          "中文请改用思源黑体/微软雅黑/Noto Sans SC"
                          % (ELEM_LABEL[elem], FONT_LABEL[p[elem + "_font"]])})
    # 纪念日页（P3）：日期非法无法烘焙，直接拦截；事件名过长会被截断，给软提示
    if _anniv_date_ok(p.get("anniv_date")) is None:
        hints.append({"level": "err", "text":
                      "纪念日日期格式不对，应为 xxxx年x月x日（例如 2024年10月20日）"})
    label = p.get("anniv_label") or ""
    pre, suf = B.ANNIV_CAP[B.ANNIV_MODE]
    sent = "%s%s%s" % (pre, label, suf)
    alw = B.text_w(sent, B.F("cn", 20))
    if alw > ANNIV_LABEL_PX:
        hints.append({"level": "warn", "text":
                      "「%s」句子过宽（%.0f px > 可用 %.0f px），事件名会被截断；"
                      "建议精简（如「相识」「毕业」「上一次旅行」）" % (sent, alw, ANNIV_LABEL_PX)})
    if not hints:
        hints.append({"level": "ok", "text": "文案与字体合适，可直接生成"})
    return {"name_w": round(nw, 1), "name_ls": nls, "role_w": round(rw, 1),
            "bio_lines": len(lines), "bio_max_lines": bio_max,
            "bio_line_h": step, "anniv_label_w": round(alw, 1)}, hints


def font_options() -> dict:
    """给页面用的可选字体与字号档位。"""
    return {
        "fonts": [{"key": k, "label": FONT_LABEL[k]} for k in SELECTABLE_FONTS],
        "sizes": {k: list(v) for k, v in B.ELEM_SIZES.items()},
        "defaults": {"name_font": DEFAULT_FONT, "name_size": B.NAME_SIZE,
                     "role_font": DEFAULT_FONT, "role_size": B.ROLE_SIZE,
                     "bio_font": DEFAULT_FONT, "bio_size": B.BIO_SIZE},
    }


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def render_full(p: dict, hour: int | None = None):
    """P1 整页（含时钟/日期/电量），用于预览。hour 指定预览时钟。"""
    with RENDER_LOCK:                 # bake_badge 的 NAME/ROLE/BIO 是模块级全局，
        set_profile(p)                # 预览（HTTP 线程）与生成（工作线程）必须串行
        if hour is None:
            return B.bake_home()
        return B.bake_home(hh="%02d" % (int(hour) % 24))


def render_base(p: dict):
    """P1 静态底板（板端上屏用的那一张）。"""
    with RENDER_LOCK:
        set_profile(p)
        return B.bake_home(dynamic=False, bar=False)


def render_qr(p: dict, sel: int, full: bool = False):
    """P2 二维码页：full=True 整页（含电量，页面预览用）；默认静态底板（上传用）。"""
    with RENDER_LOCK:
        set_profile(p)
        return B.bake_qr(sel=sel, dynamic=full)


def render_anniv(p: dict, full: bool = True):
    """P3 纪念日倒计时页。

    full=True  → 整页（含倒计时数字与电量百分比），用于页面预览；
    full=False → 静态底板（板端上屏那一张，数字由 num_* 精灵块覆盖）。
    天数按 p 的目标日期与模式现算（bake_anniv 内部读 ANNIV_DATE/ANNIV_MODE）。
    """
    with RENDER_LOCK:
        set_profile(p)
        return B.bake_anniv(dynamic=full)


def _current_splash():
    """工程现有的 splash.raw（RGB565 大端）解回 RGB 图，作为未上传启动图时的回退来源。"""
    raw = (MPB / SPLASH_RAW).read_bytes()
    swapped = bytearray(len(raw))
    swapped[0::2], swapped[1::2] = raw[1::2], raw[0::2]   # 大端 → 小端
    return B.Image.frombytes("RGB", (SPLASH_W, SPLASH_H), bytes(swapped),
                             "raw", "BGR;16")


def render_splash():
    """启动页整屏 240x320：有上传图就 cover 铺满并居中裁切（不留白），否则沿用工程现有启动图。"""
    src = (B.Image.open(SPLASH_PNG).convert("RGB") if SPLASH_PNG.is_file()
           else _current_splash())
    sw, sh = src.size
    s = max(SPLASH_W / sw, SPLASH_H / sh)          # cover：铺满整屏，多余裁掉
    nw, nh = max(SPLASH_W, round(sw * s)), max(SPLASH_H, round(sh * s))
    img = src.resize((nw, nh), B.Image.LANCZOS)
    ox, oy = (nw - SPLASH_W) // 2, (nh - SPLASH_H) // 2
    return img.crop((ox, oy, ox + SPLASH_W, oy + SPLASH_H))


def png_dataurl(img, scale: int = 2) -> str:
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), B.Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# 上传素材（头像 / 二维码）
# ---------------------------------------------------------------------------

def decode_upload(data: str):
    """把页面传来的 dataURL（或裸 base64）解成 RGB 图；长边超限先等比缩小。"""
    s = str(data or "").strip()
    if "," in s and s.lstrip().startswith("data:"):
        s = s.split(",", 1)[1]
    img = B.Image.open(io.BytesIO(base64.b64decode(s))).convert("RGB")
    long_side = max(img.width, img.height)
    if long_side > UPLOAD_MAX_SIDE:
        k = UPLOAD_MAX_SIDE / long_side
        img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))),
                         B.Image.LANCZOS)
    return img


def asset_state() -> dict:
    """每个槽位：是否已上传 + 尺寸 + 缩略 dataURL（页面回显用）。"""
    out = {}
    for slot, meta in ASSETS.items():
        path = meta["path"]
        if path.is_file():
            img = B.Image.open(path).convert("RGB")
            thumb = img.copy()
            thumb.thumbnail((150, 150), B.Image.LANCZOS)
            out[slot] = {"custom": True, "label": meta["label"], "fit": meta["fit"],
                         "width": img.width, "height": img.height,
                         "thumb": png_dataurl(thumb, 1)}
        else:
            out[slot] = {"custom": False, "label": meta["label"], "fit": meta["fit"]}
    return out


def save_asset(slot: str, data: str) -> dict:
    img = decode_upload(data)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    ASSETS[slot]["path"].write_bytes(buf.getvalue())
    B._AVATAR_CACHE.clear()     # 烘焙侧按「路径 mtime」缓存，换图后必须清掉
    B._QR_CACHE.clear()
    return {"slot": slot, "width": img.width, "height": img.height}


def clear_asset(slot: str) -> bool:
    path = ASSETS[slot]["path"]
    had = path.is_file()
    if had:
        path.unlink()
    B._AVATAR_CACHE.clear()
    B._QR_CACHE.clear()
    return had


# ---------------------------------------------------------------------------
# 后台任务（串行执行：烘焙 + 写盘 + 上传）
# ---------------------------------------------------------------------------

JOB = {"running": False, "kind": "", "ok": None, "log": []}
JOB_LOCK = threading.Lock()
TASKQ: "queue.Queue[tuple]" = None  # type: ignore


def job_log(msg: str) -> None:
    with JOB_LOCK:
        for line in str(msg).splitlines() or [""]:
            JOB["log"].append(line)
        del JOB["log"][:-250]


def worker_loop() -> None:
    while True:
        kind, fn = TASKQ.get()
        with JOB_LOCK:
            JOB.update(running=True, kind=kind, ok=None, log=[])
        try:
            fn()
            job_log("[完成] " + kind)
            with JOB_LOCK:
                JOB["ok"] = True
        except Exception as e:  # noqa: BLE001 —— 后台任务兜底，错误带回页面
            job_log("[失败] %s: %s" % (kind, e))
            with JOB_LOCK:
                JOB["ok"] = False
        finally:
            with JOB_LOCK:
                JOB["running"] = False


def run_mpremote(port: str, files: list, exec_cmd: str | None = None) -> None:
    """把 files=[(本地路径, 板端文件名)] 逐个写进板子，最后软复位让 main.py 重新跑。

    exec_cmd 非空时在传文件后、复位前先执行一段板端代码（如立即校时验证）。
    mpremote 进 raw REPL 时会打断板端主循环，因此必须跟一条 soft-reset，
    否则板子停在 REPL 上、屏幕不动。多条命令之间用 "+" 分隔（mpremote 的命令终止符）。
    """
    cmd = [sys.executable, "-m", "mpremote", "connect", port]
    for src, dest in files:
        cmd += ["fs", "cp", str(src), ":" + dest, "+"]
    if exec_cmd:
        cmd += ["exec", exec_cmd, "+"]
    cmd += ["soft-reset"]
    job_log("执行：" + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    assert proc.stdout
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        del tail[:-8]
        job_log(line)
    code = proc.wait()
    if code != 0:
        raise RuntimeError("mpremote 退出码 %d（检查端口占用 / 串口名）\n%s"
                           % (code, "\n".join(tail)))
    job_log("已写入板端 %d 个文件，并已软复位重启玩法"
            % len(files))


def do_wifi(nets: list, port: str, sync_now: bool) -> None:
    """生成 wifi_cfg.py（PC 副本）→ 上传板子 → 可选立即校时验证（日志可见结果）。"""
    WIFI_CFG.write_text(render_wifi_cfg(nets), encoding="utf-8", newline="\n")
    job_log("已生成 %s（%d 个候选网络：%s）"
            % (WIFI_CFG, len(nets), "、".join(n[0] for n in nets)))
    exec_cmd = ("import timesync; print('sync ->', timesync.sync())"
                if sync_now else None)
    if sync_now:
        job_log("上传后立即校时验证（约 10-40s，取决于网络）…")
    run_mpremote(port, [(WIFI_CFG, "wifi_cfg.py")], exec_cmd=exec_cmd)


def do_apply(p: dict, upload: bool, port: str) -> None:
    save_profile_json(p)
    job_log("配置已存到 %s" % PROFILE_JSON)
    GEN.mkdir(parents=True, exist_ok=True)
    full = render_full(p)
    full.save(GEN / "home.png")
    raw = B.to_rgb565_be(render_base(p))
    (GEN / RAW_NAME).write_bytes(raw)
    job_log("已生成 %s（%d B）与预览 %s"
            % (GEN / RAW_NAME, len(raw), GEN / "home.png"))
    outs = [(GEN / RAW_NAME, RAW_NAME)]
    for sel, tag in ((0, "ding"), (1, "wx")):     # 二维码页也一并重烘焙（含上传图）
        qraw = B.to_rgb565_be(render_qr(p, sel, full=False))
        (GEN / QR_RAW[tag]).write_bytes(qraw)
        outs.append((GEN / QR_RAW[tag], QR_RAW[tag]))
        job_log("已生成 %s（%d B）" % (GEN / QR_RAW[tag], len(qraw)))
    sraw = B.to_rgb565_be(render_splash())        # 启动页整屏也一并重烘焙（含上传图）
    (GEN / SPLASH_RAW).write_bytes(sraw)
    outs.append((GEN / SPLASH_RAW, SPLASH_RAW))
    job_log("已生成 %s（%d B）" % (GEN / SPLASH_RAW, len(sraw)))
    # 纪念日页静态底板：底色 + 面板 + 标签（数字由 num_* 精灵块覆盖，电量复用 bat_*）
    araw = B.to_rgb565_be(render_anniv(p, full=False))
    (GEN / ANNIV_RAW).write_bytes(araw)
    outs.append((GEN / ANNIV_RAW, ANNIV_RAW))
    job_log("已生成 %s（%d B）" % (GEN / ANNIV_RAW, len(araw)))
    # 板端 anniv.py 的「目标日期 / 语义模式」读自 sprites.py 常量 → 配置改了必须同步重生成
    # 并上传 sprites.py；sprites.raw 与配置无关（num_*/bat_* 均为固定烘焙），内容一致就不重传。
    with RENDER_LOCK:
        set_profile(p)
        spayload, stable = B.pack(B.build_sprites())
    (HERE / "sprites.py").write_text(B.write_sprites_py(stable),
                                     encoding="utf-8", newline="\n")
    outs.append((HERE / "sprites.py", "sprites.py"))
    job_log("已重生成 tools/sprites.py（%d 条偏移，ANNIV_DATE=%s / MODE=%s）"
            % (len(stable), B.ANNIV_DATE, B.ANNIV_MODE))
    raw_path = GEN / "sprites.raw"
    same = raw_path.is_file() and raw_path.read_bytes() == spayload
    if not same:
        raw_path.write_bytes(spayload)
        outs.append((raw_path, "sprites.raw"))
        job_log("sprites.raw 与磁盘不一致，已重生成并加入上传列表（%d B）" % len(spayload))
    else:
        job_log("sprites.raw 与磁盘逐字节一致，无需重传（%d B）" % len(spayload))
    if upload:
        run_mpremote(port, outs)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def list_serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except Exception:  # noqa: BLE001 —— 未装 pyserial 时只返回空列表
        return []
    ports = [{"device": x.device, "description": x.description or ""}
             for x in list_ports.comports()]
    ports.sort(key=lambda d: ("蓝牙" in d["description"]
                              or "bluetooth" in d["description"].lower(),
                              d["device"]))
    return ports


class Handler(BaseHTTPRequestHandler):
    server_version = "BadgeProfileStudio/1.0"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _asset_image(self) -> None:
        """GET /api/asset?slot=avatar —— 把上传的原图回给页面（头像取景编辑器要用原图）。"""
        q = self.path.split("?", 1)[1]
        slot = ""
        for kv in q.split("&"):
            if kv.startswith("slot="):
                slot = kv[5:]
        meta = ASSETS.get(slot)
        if not meta or not meta["path"].is_file():
            self._json({"error": "not found"}, 404)
            return
        self._send(200, meta["path"].read_bytes(), "image/png")

    def do_GET(self) -> None:  # noqa: N802 —— http.server 命名约定
        if self.path in ("/", "/index.html"):
            self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/profile":
            self._json({"profile": current_profile(), "default_port": DEFAULT_PORT})
        elif self.path == "/api/fonts":
            self._json(font_options())
        elif self.path == "/api/assets":
            self._json({"assets": asset_state()})
        elif self.path.startswith("/api/asset?"):
            self._asset_image()
        elif self.path == "/api/ports":
            self._json({"ports": list_serial_ports()})
        elif self.path == "/api/job":
            with JOB_LOCK:
                snap = {"running": JOB["running"], "kind": JOB["kind"],
                        "ok": JOB["ok"], "log": list(JOB["log"])[-80:]}
            self._json(snap)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            if self.path == "/api/preview":
                p = clean_profile(body)
                page = str(body.get("page") or "home")
                if page == "qr_wx":
                    img = render_qr(p, 1)
                elif page == "qr_ding":
                    img = render_qr(p, 0)
                elif page == "anniv":
                    img = render_anniv(p)
                elif page == "splash":
                    img = render_splash()
                else:
                    try:
                        hour = int(body["hour"])
                    except (KeyError, TypeError, ValueError):
                        hour = None
                    img = render_full(p, hour)
                metrics, hints = measure(p)
                self._json({"ok": True, "image": png_dataurl(img),
                            "metrics": metrics, "hints": hints})
            elif self.path == "/api/apply":
                p = clean_profile(body)
                _m, hints = measure(p)
                if any(h["level"] == "err" for h in hints):
                    raise RuntimeError(next(h["text"] for h in hints
                                            if h["level"] == "err"))
                upload = bool(body.get("upload"))
                port = str(body.get("port") or DEFAULT_PORT).strip()
                if upload and not port:
                    raise RuntimeError("未指定串口")
                TASKQ.put(("生成并上传到 " + port if upload else "生成 home.raw",
                           lambda: do_apply(p, upload, port)))
                self._json({"ok": True, "hints": hints})
            elif self.path == "/api/wifi":
                nets = clean_wifi_nets(body.get("nets"))
                port = str(body.get("port") or DEFAULT_PORT).strip()
                if not port:
                    raise RuntimeError("未指定串口")
                sync_now = bool(body.get("sync", True))
                TASKQ.put(("WiFi 配置上传到 " + port,
                           lambda: do_wifi(nets, port, sync_now)))
                self._json({"ok": True})
            elif self.path == "/api/upload":
                slot = str(body.get("slot") or "")
                if slot not in ASSETS:
                    raise RuntimeError("未知素材槽位：%s" % slot)
                info = save_asset(slot, body.get("data"))
                self._json({"ok": True, "info": info, "assets": asset_state()})
            elif self.path == "/api/clear_asset":
                slot = str(body.get("slot") or "")
                if slot not in ASSETS:
                    raise RuntimeError("未知素材槽位：%s" % slot)
                had = clear_asset(slot)
                self._json({"ok": True, "cleared": had, "assets": asset_state()})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001 —— API 边界兜底，错误回传页面
            self._json({"ok": False, "error": str(e)}, 400)

    def log_message(self, fmt, *args):  # 静默访问日志，避免刷屏
        pass


def main() -> None:
    global TASKQ
    if not HTML_PATH.is_file():
        raise SystemExit("缺少页面文件：%s" % HTML_PATH)
    TASKQ = queue.Queue()
    threading.Thread(target=worker_loop, daemon=True).start()
    addr = ("127.0.0.1", 8519)
    print("工牌配置页：http://%s:%d  (Ctrl+C 退出)" % addr)
    ThreadingHTTPServer(addr, Handler).serve_forever()


if __name__ == "__main__":
    main()