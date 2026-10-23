# FoloToy AI Passport —— WiFi + NTP 校时（MicroPython 玩法层）
#
# 板子没有电池 RTC，断电即丢时间；本模块在开机时连一次 2.4G WiFi，从 NTP
# 取一次标准时间写入 RTC（含东八区偏移），随后立刻关掉电台省电。
#
# 依赖：wifi_cfg.py（SSID / PASSWORD）
# 用法：
#   import timesync
#   ok = timesync.sync()          # True=已校时，False=失败（保留旧时间）
#
# 说明：
#   * NTP 默认走 ntp.aliyun.com（国内可达性比 pool.ntp.org 好）。
#   * ntptime.settime() 写的是 UTC；这里再换算成本地时间写回 RTC，
#     之后 time.localtime() 直接就是本地时间（对齐原厂 local = now + 8h）。
#   * 无论成功失败都会关掉 WiFi，避免长时间挂着射频。

import time

import network
import ntptime

NTP_HOST = "ntp.aliyun.com"
TZ_OFFSET_S = 8 * 3600          # 东八区
CONNECT_TIMEOUT_MS = 12000      # 连不上就放弃，不阻塞开机画面


def sync(ssid=None, password=None, timeout_ms=CONNECT_TIMEOUT_MS):
    """连 WiFi 取 NTP，写入本地时间到 RTC。返回 True/False。"""
    if ssid is None or password is None:
        try:
            import wifi_cfg
        except ImportError:
            print("校时失败：缺少 wifi_cfg.py")
            return False
        ssid = wifi_cfg.SSID
        password = wifi_cfg.PASSWORD

    if not ssid or not password:
        print("校时跳过：未填写 WiFi 账号（见 wifi_cfg.py）")
        return False

    import machine

    ntptime.host = NTP_HOST
    wlan = network.WLAN(network.STA_IF)
    try:
        if not wlan.active():
            wlan.active(True)
        if not wlan.isconnected():
            wlan.connect(ssid, password)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                print("校时失败：WiFi 连接超时")
                return False
            time.sleep_ms(200)

        ntptime.settime()                       # RTC = UTC
        t = time.localtime(time.time() + TZ_OFFSET_S)
        machine.RTC().datetime((t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0))
        print("校时成功：%04d-%02d-%02d %02d:%02d:%02d"
              % (t[0], t[1], t[2], t[3], t[4], t[5]))
        return True
    except Exception as e:
        print("校时失败：", e)
        return False
    finally:
        # 不保留射频：既省电，也避免射频任务占用堆内存
        try:
            wlan.disconnect()
        except Exception:
            pass
        wlan.active(False)


if __name__ == "__main__":
    print("sync ->", sync())
    t = time.localtime()
    print("RTC 本地时间：%04d-%02d-%02d %02d:%02d:%02d"
          % (t[0], t[1], t[2], t[3], t[4], t[5]))