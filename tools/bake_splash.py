#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成启动页 splash.raw（240x320 裸 RGB565 大端，无文件头）。

本工程自带的 splash.raw 是**中性占位图**，不含任何个人信息。
想换成自己的启动图，二选一：
  1) 把一张 240x320 的图片存为 mp-badge-clean/tools/assets/splash.png，
     再运行本脚本 `python tools/bake_splash.py`；
  2) 直接把预渲染好的 splash.raw 覆盖到 mp-badge-clean/ 根目录。

依赖：tools/vendor/fonts 下的开源字体（随工程自带，无需系统字体）。
用法：python mp-badge-clean/tools/bake_splash.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

import bake_badge as B   # 复用画布尺寸 / 色板 / 字体 / RGB565 编码

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "splash.raw"
SRC = HERE / "assets" / "splash.png"   # 可选：存在就用它，否则渲染默认占位图


def placeholder() -> Image.Image:
    """中性占位启动图：品牌名 + 「请替换」提示，零个人信息。"""
    img = Image.new("RGB", (B.W, B.H), B.PAPER)
    d = ImageDraw.Draw(img)
    cx = B.W // 2
    f_big, f_mid, f_small = B.F("latin", 40), B.F("cn", 20), B.F("cn", 14)
    for dx, dy, col in ((2, 3, B.RED), (0, 0, B.INK)):      # 先画错位红影，再压黑字
        d.text((cx + dx, 122 + dy), "AI PASSPORT", font=f_big,
               fill=col + (255,), anchor="mm")
    d.text((cx, 178), "示例启动页", font=f_mid, fill=B.INK + (255,), anchor="mm")
    d.text((cx, 208), "把它换成你自己的图片", font=f_small,
           fill=B.GRAY + (255,), anchor="mm")
    return img


def main() -> None:
    src = Image.open(SRC).convert("RGB") if SRC.is_file() else placeholder()
    if src.size != (B.W, B.H):
        raise SystemExit("启动图必须为 %dx%d，当前为 %dx%d"
                         % (B.W, B.H, src.width, src.height))
    OUT.write_bytes(B.to_rgb565_be(src))
    print("+ %s  %dx%d  %d B" % (OUT, B.W, B.H, B.W * B.H * 2))


if __name__ == "__main__":
    main()