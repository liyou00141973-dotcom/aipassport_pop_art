# FoloToy AI Passport —— 三功能键驱动（MicroPython）
# 事实来源：ai-passport/components/bsp/include/bsp_pins.h   （按键章节）
#           ai-passport/components/bsp/src/bsp_button.c     （ADC 衰减与判定窗口）
#
# 电路：GPIO0(ADC1_CH0) 由外部 10k 上拉到 3.3V，按键按下经分压电阻到 GND
#   上  : 0 ohm   ->    0 mV     实测约   6 mV  (raw   7~ 18)
#   下  : 1k      ->  ~300 mV    实测约 305 mV  (raw 413~428)
#   确定: 2.2k    ->  ~595 mV    实测约 605 mV  (raw 813~829)
#   松开: 无通路  ->  ~3300 mV   实测 raw 饱和 4095 -> mv 2882（超过 ADC 满量程）
# 注意：不能改用内部上拉（约 45k），会把三档挤到 0~154mV，无法区分。
#
# 使用：
#   b = buttons.Buttons()
#   k, held = b.wait_key()        # 阻塞等一次按下->松开
#   if b.any_pressed(): ...       # 非阻塞查询

from machine import Pin, ADC
import time

KEY_UP, KEY_DOWN, KEY_OK = 0, 1, 2
KEY_NAMES = ("上 UP", "下 DOWN", "确定 OK")

EV_CLICK, EV_LONG = 0, 1
EV_NAMES = ("单击", "长按")

WINDOWS = ((0, 150), (150, 447), (447, 1900))   # BSP_BTN_MV_TABLE，半开区间 [lo, hi)
RELEASED_MIN = 1900                             # BSP_BTN_MV_RELEASED_MIN
SHORT_PRESS_MS = 180                            # BSP_BTN_SHORT_PRESS_MS
LONG_PRESS_MS = 500                             # BSP_BTN_LONG_PRESS_MS

SAMPLE_MS = 10
DEBOUNCE_N = 2                                  # 连续 N 次一致才认账


class Buttons:
    def __init__(self, pin=0, atten=ADC.ATTN_11DB,
                 sample_ms=SAMPLE_MS, debounce=DEBOUNCE_N):
        self.adc = ADC(Pin(pin), atten=atten)
        self.sample_ms = sample_ms
        self.debounce = debounce
        self._last = -1     # 上一帧采样值
        self._count = 0     # 连续一致次数
        self._key = -1      # 已消抖的当前键；-1 = 松开
        # events() 的状态：一次「按下 -> 松开」的开始时刻与是否已发过长按
        self._down_ms = None
        self._down_key = -1
        self._long_fired = False

    def read_mv(self):
        """读 GPIO0 电压（毫伏）。无校准数据时回退到线性换算。"""
        try:
            return self.adc.read_uv() // 1000
        except Exception:
            return self.adc.read() * 3300 // 4095

    def classify(self, mv):
        """毫伏 -> 键号；落在所有窗口之外（即松开）返回 -1。"""
        for i, (lo, hi) in enumerate(WINDOWS):
            if lo <= mv < hi:
                return i
        return -1

    def key(self):
        """非阻塞：返回当前键号（已消抖），松开返回 -1。"""
        k = self.classify(self.read_mv())
        if k == self._last:
            self._count += 1
        else:
            self._last = k
            self._count = 1
        if self._count >= self.debounce:
            self._key = k
        return self._key

    def events(self):
        """非阻塞：返回本次采样产生的 [(键号, 事件, 按住毫秒), ...]，无事件返回 []。
        长按：按住到 LONG_PRESS_MS 立即发一次（不重复）；
        单击：松手时发，上一步长按过的松手不再发单击。"""
        now = time.ticks_ms()
        out = []
        k = self.key()
        if k >= 0:
            if self._down_ms is None:           # 新按下
                self._down_ms = now
                self._down_key = k
                self._long_fired = False
            elif (not self._long_fired and
                  time.ticks_diff(now, self._down_ms) >= LONG_PRESS_MS):
                self._long_fired = True
                out.append((k, EV_LONG, time.ticks_diff(now, self._down_ms)))
        elif self._down_ms is not None:         # 松手
            if not self._long_fired:
                out.append((self._down_key, EV_CLICK,
                            time.ticks_diff(now, self._down_ms)))
            self._down_ms = None
            self._down_key = -1
            self._long_fired = False
        return out

    def any_pressed(self):
        return self.key() >= 0

    def is_pressed(self, k):
        return self.key() == k

    def wait_key(self, timeout_ms=-1):
        """阻塞等待一次「按下 -> 松开」。
        返回 (键号, 按住毫秒)；timeout_ms 超时返回 None（-1 = 不限时）。"""
        t0 = time.ticks_ms()
        while self.key() < 0:
            if timeout_ms >= 0 and time.ticks_diff(time.ticks_ms(), t0) >= timeout_ms:
                return None
            time.sleep_ms(self.sample_ms)
        k = self._key
        t_down = time.ticks_ms()
        while self.key() >= 0:
            time.sleep_ms(self.sample_ms)
        return k, time.ticks_diff(time.ticks_ms(), t_down)


if __name__ == "__main__":
    # 自检：屏幕颜色实时反馈 + 串口打印读数（屏幕: 红=上 绿=下 蓝=确定 黑=松开）
    import st7789p3
    COLORS = (0xF800, 0x07E0, 0x001F)

    b = Buttons()
    lcd = st7789p3.ST7789P3()
    lcd.fill(0x0000)
    print("屏幕: 红=上  绿=下  蓝=确定  黑=松开")
    shown = -1
    last_mv = -1
    while True:
        mv = b.read_mv()
        k = b.key()
        if k != shown:
            shown = k
            lcd.fill(COLORS[k] if k >= 0 else 0x0000)
            print("-> %s  (%d mV)"
                  % (KEY_NAMES[k] if k >= 0 else "松开", mv))
        elif k < 0 and abs(mv - last_mv) > 200:
            print("松开 %d mV" % mv)
        last_mv = mv
        time.sleep_ms(20)