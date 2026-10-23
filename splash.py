# FoloToy AI Passport —— 启动页（MicroPython 玩法页面）
#
# 素材：splash.raw —— 240x320 裸 RGB565 大端，由 tools/bake_splash.py 在 PC 端预渲染
#       （153,600 = 240*320*2 字节，无文件头；板端不做任何解码）
#       本工程自带的是中性占位启动图，换图请改 tools/assets/splash.png 后重跑烘焙
# 依赖：st7789p3.py
#
# 用法：
#   import splash
#   lcd = splash.show()        # 初始化屏幕并整屏显示启动图，返回 lcd
#   lcd = splash.show(lcd)     # 复用已初始化的屏幕，省掉复位/初始化等待

import st7789p3

IMAGE = "splash.raw"
WIDTH, HEIGHT = 240, 320
BLACK = 0x0000


def show(image=IMAGE, lcd=None):
    """显示启动页。lcd 为 None 时新建并初始化屏幕。

    整屏图按「行带」流式读出后送出，不会把 150KB 整图读进内存。
    """
    if lcd is None:
        lcd = st7789p3.ST7789P3()
    lcd.fill(BLACK)
    lcd.draw_image(image, 0, 0, WIDTH, HEIGHT)
    return lcd


if __name__ == "__main__":
    import time
    t0 = time.ticks_ms()
    show()
    print("启动页已显示 (%d ms)" % time.ticks_diff(time.ticks_ms(), t0))