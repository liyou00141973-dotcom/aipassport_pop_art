# FoloToy AI Passport —— 工牌首页（P1，MicroPython 玩法页面）
#
# 素材（均由 mp-badge/tools/bake_badge.py 在 PC 端烘焙，板端零解码）：
#   home.raw     240x320 静态底板：P1 去掉日期文字/时钟/电量数字后整屏渲染
#   sprites.raw  212 个「不透明精灵块」：会变化的小窗口，整页渲染后裁窗口得到
#   sprites.py   精灵块偏移表（SPR / DATE_SLOTS / WEEKDAYS ...）
#
# 板端 = 底板整屏画一次，之后只对会变的小窗口做定点 blit 覆盖。
# 精灵块窗口互不重叠且都取自同一整页渲染，因此「底板 + 精灵块 == 整页」逐像素成立。
#
# 依赖：st7789p3.py、sprites.py、sprites.raw
#
# 用法：
#   import home
#   lcd = home.show()                  # 初始化屏幕 + 底板 + 全部动态块
#   lcd = home.show(lcd)               # 复用已初始化的屏幕
#   home.set_clock("09", "07")         # 只重贴时钟（HH、MM 两个小窗口）
#   home.set_colon(False)              # 冒号暗态（闪烁用）
#   home.set_date(home.date_text())    # 只重贴日期章的各个字符槽
#   home.set_battery(86)               # 只重贴电量窗口（指示条 + 百分比）

import time

import st7789p3
import sprites as S

IMAGE = "home.raw"
WIDTH, HEIGHT = 240, 320

_lcd = None
_f = None       # 常开的 sprites.raw 句柄：每次只 seek + read 一个小窗口


def _blit(name, x=None, y=None):
    """贴一个精灵块。x/y 缺省用 sprites.py 里记录的规范位置。"""
    off, sx, sy, w, h = S.SPR[name]
    _f.seek(off)
    _lcd.blit(_f.read(w * h * 2), sx if x is None else x, sy if y is None else y, w, h)


def date_text(t=None):
    """按板端 RTC 生成日期章文字，形如 "2026-10-22 周四"。"""
    if t is None:
        t = time.localtime()
    return "%04d-%02d-%02d 周%s" % (t[0], t[1], t[2], S.WEEKDAYS[t[6]])


def set_date(text):
    """重贴日期章（text 为 13 字符："YYYY-MM-DD 周X"）。"""
    wi = S.WEEKDAYS.index(text[12])
    for i, (kind, x, _w) in enumerate(S.DATE_SLOTS):
        if kind == "d":
            _blit("date_" + text[i], x)
        elif kind == "da":
            _blit("date_da", x)
        elif kind == "zh":
            _blit("date_zh", x)
        elif kind == "wd":
            _blit("date_wd_%d" % wi, x)


def set_clock(hh, mm):
    """重贴时钟，hh/mm 为两位字符串（如 "09"）。"""
    _blit("hh_" + hh)
    _blit("mm_" + mm)


def set_colon(on):
    """冒号亮/暗（闪烁用）。暗态是预混色不透明块，与 LVGL 30% 不透明度逐像素等价。"""
    _blit("col_on" if on else "col_off")


def set_battery(soc, bar=True):
    """重贴电量窗口。bar=False 为指示条暗态（低电量闪烁用，仅 soc<=5 有烘焙）。"""
    name = "bat_%03d_off" % soc if not bar else "bat_%03d" % soc
    if name not in S.SPR:
        name = "bat_%03d" % soc
    _blit(name)


def show(image=IMAGE, lcd=None, *, hh=None, mm=None, date=None, soc=86, colon=True):
    """整屏显示工牌首页：静态底板 + 全部动态块。

    hh/mm/date 为 None 时取板端 RTC 当前时间（须先 timesync.sync() 校时）。
    """
    global _lcd, _f
    if lcd is None:
        lcd = st7789p3.ST7789P3()
    if _f is None:
        _f = open(S.RAW, "rb")
    _lcd = lcd

    if hh is None or mm is None or date is None:
        t = time.localtime()
        if date is None:
            date = date_text(t)
        if hh is None:
            hh = "%02d" % t[3]
        if mm is None:
            mm = "%02d" % t[4]

    lcd.draw_image(image, 0, 0, WIDTH, HEIGHT)      # 静态底板
    set_date(date)
    set_clock(hh, mm)
    set_colon(colon)
    set_battery(soc)
    return lcd


if __name__ == "__main__":
    t0 = time.ticks_ms()
    show()
    print("工牌首页已显示 (%d ms)" % time.ticks_diff(time.ticks_ms(), t0))