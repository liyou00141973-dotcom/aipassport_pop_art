# FoloToy AI Passport —— WiFi + NTP 校时（MicroPython 玩法层）
#
# 板子没有电池 RTC，断电即丢时间；本模块连一次 2.4G WiFi，从 NTP 取标准时间
# 写入 RTC（含东八区偏移），随后立刻关掉电台省电。
#
# 依赖：wifi_cfg.py（SSID / PASSWORD）
# 用法：
#   import timesync
#   ok = timesync.sync()          # True=已校时，False=失败（保留旧时间）
#
# 为什么不用 ntptime.settime()：
#   ntptime 只发一次请求、内部 socket 超时很短，在 guest 网络上偶发
#   [Errno 116] ETIMEDOUT；开机只校一次的话，这一枪没打中时间就永远错。
#   这里改为自写查询：单个 socket 超时 4s、最多 3 次、在多个 NTP 主机之间
#   轮换，显著提高成功率。
#
# 说明：
#   * NTP 返回的是 UTC；这里换算成本地时间写回 RTC，之后 time.localtime()
#     直接就是本地时间（对齐原厂 local = now + 8h）。
#   * 无论成功失败都会关掉 WiFi，避免长时间挂着射频。
#   * 未填写 WiFi 账号（见 wifi_cfg.py）时直接跳过，不阻塞开机。

import time

import network
import socket
import struct

NTP_HOSTS = ("ntp.aliyun.com", "cn.pool.ntp.org", "ntp.tencent.com")
NTP_PORT = 123
NTP_TIMEOUT_S = 4               # 单个 socket 等待上限（秒）
NTP_ATTEMPTS = 3                # 总请求次数（在主机之间轮换）

NTP_EPOCH_DELTA = 2208988800    # NTP(1900) → Unix(1970) 的秒数
MPY_EPOCH_DELTA = 946684800     # Unix(1970) → MicroPython-ESP32 计时基准(2000) 的秒数
TZ_OFFSET_S = 8 * 3600          # 东八区

CONNECT_TIMEOUT_MS = 15000      # WiFi 关联上限；实测首次需 ~9s，留足余量


def _ntp_unix(host, timeout_s=NTP_TIMEOUT_S):
    """向 host 取一次 NTP，返回 Unix 时间戳（Unix/1970 基准，UTC 秒）。失败抛异常。"""
    addr = socket.getaddrinfo(host, NTP_PORT)[0][-1]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout_s)
    try:
        pkt = bytearray(48)
        pkt[0] = 0x1B                       # LI=0 VN=3 Mode=3（NTP 客户端）
        s.sendto(pkt, addr)
        data = s.recv(48)
    finally:
        s.close()
    if len(data) < 48:
        raise OSError("NTP 响应过短")
    secs = struct.unpack("!I", data[40:44])[0]   # Transmit Timestamp
    return secs - NTP_EPOCH_DELTA


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

        # 多个主机轮流试：guest 网络偶发丢包，重试 + 换源能显著提高成功率
        last_err = None
        for i in range(NTP_ATTEMPTS):
            host = NTP_HOSTS[i % len(NTP_HOSTS)]
            try:
                utc = _ntp_unix(host)
            except Exception as e:
                last_err = e
                print("NTP 第 %d 次失败（%s）：%s" % (i + 1, host, e))
                continue
            # utc 是 Unix(1970) 基准；本机 time.localtime() 以 2000 为基准，
            # 故先减去 MPY_EPOCH_DELTA，再叠加东八区偏移。
            t = time.localtime(utc - MPY_EPOCH_DELTA + TZ_OFFSET_S)
            machine.RTC().datetime(
                (t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0))
            print("校时成功：%04d-%02d-%02d %02d:%02d:%02d"
                  % (t[0], t[1], t[2], t[3], t[4], t[5]))
            return True

        print("校时失败：NTP 多次超时", last_err)
        return False
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