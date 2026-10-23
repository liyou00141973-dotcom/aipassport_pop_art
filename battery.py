# FoloToy AI Passport —— CW2017 电量计驱动（MicroPython 版）
#
# 事实来源（禁止猜测，均取自官方仓库）：
#   ai-passport/components/bsp/include/bsp_pins.h
#     BSP_I2C_PORT   I2C_NUM_0
#     BSP_I2C_SDA    10
#     BSP_I2C_SCL    7
#     BSP_I2C_CW2017_ADDR 0x63
#   ai-passport/components/bsp/src/bsp_battery.c（寄存器与 profile 流程逐条对应）
#
# 寄存器：
#   0x00 VERSION    上电能应答即代表芯片在位
#   0x02 VCELL_H    14bit 电压，U(uV) = raw * 312.5
#   0x04 SOC_H      高字节 = 整数百分比（0x05 为 1/256 %）
#   0x08 CONFIG     0xF0 睡眠 / 0x30 复位态 / 0x00 正常
#   0x0B SOC_ALERT  bit7 = profile UPDATE_FLAG
#   0x10 PROFILE    80 字节电芯参数
#
# 用法：
#   import battery
#   b = battery.Battery()
#   b.init()            # 探测 + 必要时写 profile，返回 True/False
#   s = b.soc()         # 0..100；芯片未就绪返回 None
#   print(b.soc(), b.mv())
#
# 说明：SOC 读取必须在 active 态；初次上电芯片要重算 SOC，期间可能读到 >100，
#       init() 会最多轮询 5 秒（50 × 100ms，与原厂一致）。

import time
from machine import I2C, Pin

I2C_ID = 0
PIN_SDA = 10
PIN_SCL = 7
FREQ = 100000
ADDR = 0x63

REG_VERSION = 0x00
REG_VCELL_H = 0x02
REG_SOC_H = 0x04
REG_CONFIG = 0x08
REG_SOC_ALERT = 0x0B
REG_PROFILE = 0x10

CFG_ACTIVE = 0x00
CFG_RESTART = 0x30
CFG_SLEEP = 0xF0
UPDATE_FLAG = 0x80
PROFILE_SIZE = 80

# 优特利 500mAh 电芯 profile，与 bsp_battery.c 的 s_battery_profile 逐字节相同
PROFILE = bytes((
    0x64, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAD, 0xC7, 0xC8, 0xCA, 0xBD, 0xB1, 0xC1, 0x94,
    0x88, 0xD1, 0xBD, 0x97, 0x88, 0x66, 0x56, 0x4A,
    0x3F, 0x33, 0x26, 0x5C, 0x37, 0xD1, 0x27, 0xD8,
    0xCC, 0xB7, 0xCF, 0xB3, 0xB2, 0xAE, 0xA6, 0x9E,
    0x99, 0x97, 0x9B, 0x86, 0x47, 0x1E, 0x17, 0x26,
    0x49, 0x96, 0xD9, 0xE1, 0xDD, 0xDC, 0xD4, 0x59,
    0x00, 0x00, 0x90, 0x02, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x5C,
))


class Battery:
    def __init__(self, i2c=None):
        self.i2c = i2c if i2c is not None else I2C(
            I2C_ID, scl=Pin(PIN_SCL), sda=Pin(PIN_SDA), freq=FREQ)
        self.ok = False       # init() 通过后为 True

    # --- 底层读写：I2C 报错一律吞掉返回 None，不让玩法层崩 ---
    def read(self, reg, n=1):
        try:
            return self.i2c.readfrom_mem(ADDR, reg, n)
        except OSError:
            return None

    def write(self, reg, val):
        try:
            self.i2c.writeto_mem(ADDR, reg, bytes((val,)))
            return True
        except OSError:
            return False

    # --- 探测：VERSION 能读到即认为芯片在位 ---
    def present(self):
        b = self.read(REG_VERSION, 1)
        return b is not None

    def version(self):
        b = self.read(REG_VERSION, 1)
        return None if b is None else b[0]

    # --- CONFIG 睡眠 / 激活切换（严格按原厂时序）---
    def enter_sleep(self):
        if not self.write(REG_CONFIG, CFG_RESTART):
            return False
        time.sleep_ms(20)
        if not self.write(REG_CONFIG, CFG_SLEEP):
            return False
        time.sleep_ms(10)
        return True

    def enter_active(self):
        if not self.write(REG_CONFIG, CFG_RESTART):
            return False
        time.sleep_ms(20)
        if not self.write(REG_CONFIG, CFG_ACTIVE):
            return False
        time.sleep_ms(10)
        return True

    # --- profile 校验：UPDATE_FLAG 置位且 80 字节完全一致才算匹配 ---
    def profile_matches(self):
        b = self.read(REG_SOC_ALERT, 1)
        if b is None:
            return False
        if not (b[0] & UPDATE_FLAG):
            return False
        for i in range(PROFILE_SIZE):
            v = self.read(REG_PROFILE + i, 1)
            if v is None or v[0] != PROFILE[i]:
                return False
        return True

    # --- profile 写入：睡眠态逐字节写 → 回读校验 → 置 UPDATE_FLAG → 回 active ---
    def update_profile(self):
        if not self.enter_sleep():
            return False
        for i in range(PROFILE_SIZE):
            if not self.write(REG_PROFILE + i, PROFILE[i]):
                print("写 profile 失败: index=%d" % i)
                return False
        for i in range(PROFILE_SIZE):
            v = self.read(REG_PROFILE + i, 1)
            if v is None or v[0] != PROFILE[i]:
                print("profile 校验失败: index=%d expected=0x%02X actual=%s"
                      % (i, PROFILE[i], "None" if v is None else "0x%02X" % v[0]))
                return False
        b = self.read(REG_SOC_ALERT, 1)
        if b is None:
            return False
        if not self.write(REG_SOC_ALERT, b[0] | UPDATE_FLAG):
            return False
        return self.enter_active()

    # --- 首次 SOC 计算：最多 50 × 100ms ---
    def wait_soc_ready(self):
        for _ in range(50):
            time.sleep_ms(100)
            s = self.soc()
            if s is not None:
                return True
        return False

    def init(self):
        """探测 + 必要时写 profile + 等首次 SOC。返回是否可用。"""
        self.ok = False
        if not self.present():
            print("CW2017 未应答（0x%02X）—— 检查 I2C SDA/SCL 或电源键是否关机"
                  % ADDR)
            return False
        print("检测到 CW2017 VERSION=0x%02X" % self.version())

        if not self.profile_matches():
            print("写入优特利 500mAh 自定义 profile ...")
            if not self.update_profile():
                print("自定义 profile 写入失败")
                return False
        else:
            cfg = self.read(REG_CONFIG, 1)
            if cfg is not None and cfg[0] != CFG_ACTIVE:
                if not self.enter_active():
                    return False
            print("优特利 500mAh profile 已匹配")

        if not self.wait_soc_ready():
            print("等待首次 SOC 计算超时")
            return False

        self.ok = True
        return True

    # --- 读数 ---
    def soc(self):
        """返回 0..100 的整数；未就绪或读取失败返回 None。"""
        b = self.read(REG_SOC_H, 2)
        if b is None:
            return None
        v = b[0]
        return v if v <= 100 else None

    def mv(self):
        """返回电池电压 mV；失败返回 None。"""
        b = self.read(REG_VCELL_H, 2)
        if b is None:
            return None
        raw = ((b[0] << 8) | b[1]) & 0x3FFF
        return raw * 3125 // 10000