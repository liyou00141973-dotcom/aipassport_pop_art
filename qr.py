# FoloToy AI Passport —— 加好友页（P2，MicroPython 玩法页面）
#
# 素材：qr_ding.raw / qr_wx.raw —— 240x320 裸 RGB565 大端，由
#       mp-badge/tools/bake_badge.py 在 PC 端烘焙（照 ai-passport/main/badge_page_qr.c
#       坐标 + v2.x 漫画风素材），板端不做任何解码。
# 依赖：st7789p3.py
#
# 用法：
#   import qr
#   lcd = qr.show()            # 默认钉钉版
#   lcd = qr.show(lcd, 1)      # 复用已初始化的屏幕，显示微信版

import st7789p3

IMAGES = ("qr_ding.raw", "qr_wx.raw")
WIDTH, HEIGHT = 240, 320


def show(lcd=None, sel=0):
    """整屏显示加好友页。sel: 0=钉钉 1=微信。lcd 为 None 时新建并初始化屏幕。"""
    if lcd is None:
        lcd = st7789p3.ST7789P3()
    lcd.draw_image(IMAGES[sel & 1], 0, 0, WIDTH, HEIGHT)
    return lcd


if __name__ == "__main__":
    import time
    t0 = time.ticks_ms()
    show()
    print("加好友页已显示 (%d ms)" % time.ticks_diff(time.ticks_ms(), t0))