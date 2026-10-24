# FoloToy AI Passport —— 纪念日倒计时页（P3，MicroPython 玩法页面）
#
# 素材（均由 mp-badge/tools/bake_badge.py 在 PC 端烘焙，板端零解码）：
#   anniv.raw    240x320 静态底板：P3 去掉倒计时数字/电量数字后整屏渲染
#   sprites.raw  含 num_0..9 / num_0_dim / bat_* 精灵块（bat_* 与首页共用）
#   sprites.py   精灵块偏移表（SPR / ANNIV_SLOTS / ANNIV_YMD / ANNIV_MODE ...）
#
# 板端 = 底板整屏画一次，之后只对会变的数字槽与电量小窗口做定点 blit 覆盖。
#
# 依赖：st7789p3.py、sprites.py、sprites.raw
#
# 用法：
#   import anniv
#   lcd = anniv.show()                 # 初始化屏幕 + 底板 + 全部动态块
#   lcd = anniv.show(lcd)              # 复用已初始化的屏幕
#   anniv.set_days(365)                # 只重贴 4 个数字槽
#   anniv.set_battery(86)              # 只重贴电量窗口（指示条 + 百分比）

import time

import st7789p3
import sprites as S

IMAGE = "anniv.raw"
WIDTH, HEIGHT = 240, 320

_lcd = None
_f = None       # 常开的 sprites.raw 句柄：每次只 seek + read 一个小窗口


def _blit(name, x=None, y=None):
    """贴一个精灵块。x/y 缺省用 sprites.py 里记录的规范位置。"""
    off, sx, sy, w, h = S.SPR[name]
    _f.seek(off)
    _lcd.blit(_f.read(w * h * 2), sx if x is None else x, sy if y is None else y, w, h)


def _ord(y, m, d):
    """Howard Hinnant days_from_civil：公历 → 自 1970-01-01 起的天数（不依赖 datetime）。"""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def days_now(t=None):
    """按 sprites.py 里的 ANNIV_MODE 算显示天数：until=距离目标日（当天 0）；since=已是第 N 天（当天 1）。"""
    if t is None:
        t = time.localtime()
    ay, am, ad = S.ANNIV_YMD
    diff = _ord(t[0], t[1], t[2]) - _ord(ay, am, ad)
    if S.ANNIV_MODE == "since":
        return max(0, diff + 1)
    return max(0, -diff)


def set_days(n):
    """重贴倒计时数字（右对齐 4 位；空槽贴暗黄「0」占位，使数字组恒居中）。"""
    s = str(n)[-S.ANNIV_N:]
    pad = S.ANNIV_N - len(s)
    for i in range(pad):
        _blit("num_0_dim", S.ANNIV_SLOTS[i][0], S.ANNIV_Y)
    for i, ch in enumerate(s):
        _blit("num_" + ch, S.ANNIV_SLOTS[pad + i][0], S.ANNIV_Y)


def set_battery(soc, bar=True):
    """重贴电量窗口。bar=False 为指示条暗态（低电量闪烁用，仅 soc<=5 有烘焙）。"""
    name = "bat_%03d_off" % soc if not bar else "bat_%03d" % soc
    if name not in S.SPR:
        name = "bat_%03d" % soc
    _blit(name)


def show(image=IMAGE, lcd=None, *, days=None, soc=86, bar=True):
    """整屏显示纪念日倒计时页：静态底板 + 数字槽 + 电量。

    days 为 None 时按板端 RTC 与 sprites.py 里的目标日期现算。
    """
    global _lcd, _f
    if lcd is None:
        lcd = st7789p3.ST7789P3()
    if _f is None:
        _f = open(S.RAW, "rb")
    _lcd = lcd

    if days is None:
        days = days_now()

    lcd.draw_image(image, 0, 0, WIDTH, HEIGHT)      # 静态底板
    set_days(days)
    set_battery(soc, bar=bar)
    return lcd


if __name__ == "__main__":
    t0 = time.ticks_ms()
    show()
    print("纪念日页已显示 (%d ms)" % time.ticks_diff(time.ticks_ms(), t0))