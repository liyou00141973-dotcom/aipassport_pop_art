# FoloToy AI Passport —— 加好友页（P2，MicroPython 玩法页面）
#
# 素材（均由 mp-badge/tools/bake_badge.py 在 PC 端烘焙，板端零解码）：
#   qr_ding.raw / qr_wx.raw   240x320 静态底板：电量只画外壳+凸起，百分比与
#                             指示条由精灵块运行时覆盖
#   sprites.raw               含 batq_* 头条电量精灵块（黄底，与首页 bat_* 不通用）
#   sprites.py                精灵块偏移表（SPR）
#
# 板端 = 底板整屏画一次，之后只对头条电量窗口做定点 blit 覆盖。
#
# 依赖：st7789p3.py、sprites.py、sprites.raw
#
# 用法：
#   import qr
#   lcd = qr.show()                    # 初始化屏幕 + 钉钉版底板 + 电量
#   lcd = qr.show(lcd, 1)              # 复用已初始化的屏幕，显示微信版
#   qr.set_battery(86)                 # 只重贴头条电量窗口（指示条 + 百分比）

import st7789p3
import sprites as S

IMAGES = ("qr_ding.raw", "qr_wx.raw")
WIDTH, HEIGHT = 240, 320

_lcd = None
_f = None       # 常开的 sprites.raw 句柄：每次只 seek + read 一个小窗口


def _blit(name):
    """贴一个精灵块（batq_* 的规范位置写在 sprites.py 的 SPR 表里）。"""
    off, sx, sy, w, h = S.SPR[name]
    _f.seek(off)
    _lcd.blit(_f.read(w * h * 2), sx, sy, w, h)


def set_battery(soc, bar=True):
    """重贴头条电量窗口。bar=False 为指示条暗态（低电量闪烁用，仅 soc<=5 有烘焙）。"""
    name = "batq_%03d_off" % soc if not bar else "batq_%03d" % soc
    if name not in S.SPR:
        name = "batq_%03d" % soc
    _blit(name)


def show(lcd=None, sel=0, *, soc=86, bar=True):
    """整屏显示加好友页。sel: 0=钉钉 1=微信。lcd 为 None 时新建并初始化屏幕。"""
    global _lcd, _f
    if lcd is None:
        lcd = st7789p3.ST7789P3()
    if _f is None:
        _f = open(S.RAW, "rb")
    _lcd = lcd

    lcd.draw_image(IMAGES[sel & 1], 0, 0, WIDTH, HEIGHT)   # 静态底板
    set_battery(soc, bar=bar)
    return lcd


if __name__ == "__main__":
    import time
    t0 = time.ticks_ms()
    show()
    print("加好友页已显示 (%d ms)" % time.ticks_diff(time.ticks_ms(), t0))
