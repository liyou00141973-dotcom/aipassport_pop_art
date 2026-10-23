# st7789p3.py —— FoloToy AI Passport 240x320 屏驱动（MicroPython）
#
# 事实来源（禁止猜测 / 禁止改用通用 ST7789 默认序列）：
#   引脚与电气参数  ai-passport/components/bsp/include/bsp_pins.h
#   厂商初始化序列  ai-passport/components/bsp/src/bsp_display.c  ST7789P3_CMDS[]
#
# 内存约束：240*320*2 = 153,600 字节，整屏帧缓冲放不下（堆约 170KB）。
#           因此所有写屏接口一律按「行带」分块传输，默认单块 240*40*2 = 19,200 字节。
from machine import Pin, PWM, SPI
import time

# ---- 引脚（bsp_pins.h，不得改动）----
SCLK, MOSI, CS, DC, BL = 8, 9, 1, 20, 21
WIDTH, HEIGHT = 240, 320

BAUD_SAFE = 40_000_000   # 40MHz：已实测点亮成功
BAUD_BSP = 80_000_000    # 80MHz：与 BSP_LCD_PCLK_HZ 一致，尚未实测
BLOCK_ROWS = 40          # 分块行数

BL_PWM_HZ = 1000         # 背光 PWM 频率；原厂走 LEDC，这里用同量级低频避免啸叫

# ---- ST7789P3 厂商专属初始化序列（照抄 bsp_display.c，含两次 0xD0）----
_VENDOR = (
    (0xB2, bytes([0x05, 0x05, 0x00, 0x33, 0x33]), 0),  # PORCTRL
    (0xB7, bytes([0x35]), 0),                          # GCTRL
    (0xBB, bytes([0x21]), 0),                          # VCOMS
    (0xC0, bytes([0x2C]), 0),                          # LCMCTRL
    (0xC2, bytes([0x01]), 0),                          # VDVVRHEN
    (0xC3, bytes([0x0B]), 0),                          # VRHS
    (0xC4, bytes([0x20]), 0),                          # VDVSET
    (0xC6, bytes([0x0F]), 0),                          # FRCTRL2
    (0xD0, bytes([0xA7, 0xA1]), 0),                    # PWCTRL1
    (0xD0, bytes([0xA4, 0xA1]), 0),                    # PWCTRL1（参考例程重发覆盖）
    (0xD6, bytes([0xA1]), 0),
    (0xE0, bytes([0xD0, 0x04, 0x08, 0x0A, 0x09, 0x05, 0x2D, 0x43,
                  0x49, 0x09, 0x16, 0x15, 0x26, 0x2B]), 0),   # PVGAMCTRL 正伽马
    (0xE1, bytes([0xD0, 0x03, 0x09, 0x0A, 0x0A, 0x06, 0x2E, 0x44,
                  0x40, 0x3A, 0x15, 0x15, 0x26, 0x2A]), 10),  # NVGAMCTRL 负伽马 + 10ms
)


class ST7789P3:
    """本板唯一可用的硬件 SPI 是 SPI(1)（实测 SPI(2) 不存在）。

    CS 由 MCU 手动控制（本板 SPI 上只有这一颗屏），沿用已验证的「每条命令一次 CS 脉冲」方式。
    """

    def __init__(self, baudrate=BAUD_SAFE, block_rows=BLOCK_ROWS, backlight_on=True):
        self.block_rows = block_rows
        self.spi = SPI(1, baudrate=baudrate, polarity=0, phase=0,
                       sck=Pin(SCLK), mosi=Pin(MOSI))
        self.cs = Pin(CS, Pin.OUT, value=1)
        self.dc = Pin(DC, Pin.OUT, value=1)
        self.bl = PWM(Pin(BL), freq=BL_PWM_HZ, duty_u16=0)   # 背光走 PWM，支持 0~100% 调光
        self._init_panel()
        if backlight_on:
            self.backlight(True)

    # ---------------- 底层：命令 / 数据 ----------------
    def _cmd(self, c, data=b""):
        self.cs.value(0)
        self.dc.value(0)
        self.spi.write(bytes([c]))
        if data:
            self.dc.value(1)
            self.spi.write(bytes(data))
        self.cs.value(1)

    def _blob(self, buf):
        self.cs.value(0)
        self.dc.value(1)
        self.spi.write(buf)
        self.cs.value(1)

    def _init_panel(self):
        self._cmd(0x01)                    # SWRESET（本板 BSP_LCD_RST = -1，软复位）
        time.sleep_ms(150)
        self._cmd(0x11)                    # SLPOUT
        time.sleep_ms(120)
        self._cmd(0x3A, bytes([0x55]))     # COLMOD = 16bpp RGB565
        for c, d, delay in _VENDOR:
            self._cmd(c, d)
            if delay:
                time.sleep_ms(delay)
        self._cmd(0x21)                    # INVON（BSP_LCD_INVERT_COLOR = 1，必须开）
        self._cmd(0x36, bytes([0x00]))     # MADCTL：不镜像 + RGB 元素顺序
        self._cmd(0x29)                    # DISPON
        time.sleep_ms(50)

    # ---------------- 高层接口 ----------------
    def backlight(self, on):
        """开/关背光，等价于 backlight_pct(100) / backlight_pct(0)。"""
        self.backlight_pct(100 if on else 0)

    def backlight_pct(self, pct):
        """设置背光亮度百分比 0~100（原厂调光档 BADGE_BACKLIGHT_DIM = 35）。"""
        if pct < 0:
            pct = 0
        elif pct > 100:
            pct = 100
        self.bl.duty_u16(int(pct) * 65535 // 100)

    def set_window(self, x0, y0, x1, y1):
        self._cmd(0x2A, bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))  # CASET
        self._cmd(0x2B, bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))  # RASET
        self._cmd(0x2C)                                                   # RAMWR

    def fill_rect(self, x, y, w, h, color):
        """单色填充矩形；按行带分块，不产生大块内存分配。color 为 RGB565。"""
        row = bytes([color >> 8, color & 0xFF]) * w
        self.set_window(x, y, x + w - 1, y + h - 1)
        done = 0
        while done < h:
            n = min(self.block_rows, h - done)
            self._blob(row * n)
            done += n

    def fill(self, color):
        self.fill_rect(0, 0, WIDTH, HEIGHT, color)

    def blit(self, buf, x=0, y=0, w=WIDTH, h=HEIGHT):
        """写入已备好的 RGB565 大端字节流，长度须为 w*h*2；按行带分块送出。"""
        view = memoryview(buf)
        stride = w * 2
        self.set_window(x, y, x + w - 1, y + h - 1)
        done = 0
        while done < h:
            n = min(self.block_rows, h - done)
            self._blob(view[done * stride:(done + n) * stride])
            done += n

    def draw_image(self, path, x=0, y=0, w=WIDTH, h=HEIGHT):
        """从文件按行带流式读入并显示，避免把整图读进内存。

        前提：文件是**裸 RGB565 大端**像素流，大小 = w*h*2（第6步会先确认该格式）。
        """
        chunk = self.block_rows * w * 2
        self.set_window(x, y, x + w - 1, y + h - 1)
        with open(path, "rb") as f:
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                self._blob(buf)


if __name__ == "__main__":
    # 自检：八色横条 + 左上 20x20 白块。预期由上到下 红 绿 蓝 白 黑 黄 青 紫。
    bars = (0xF800, 0x07E0, 0x001F, 0xFFFF, 0x0000, 0xFFE0, 0x07FF, 0xF81F)
    lcd = ST7789P3()
    bh = HEIGHT // len(bars)
    for i, c in enumerate(bars):
        lcd.fill_rect(0, i * bh, WIDTH, bh, c)
    lcd.fill_rect(0, 0, 20, 20, 0xFFFF)
    print("self-test ok")