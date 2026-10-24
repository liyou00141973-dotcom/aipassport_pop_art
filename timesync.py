# FoloToy AI Passport —— WiFi + NTP 校时（MicroPython 玩法层）
#
# 板子没有电池 RTC，断电即丢时间；本模块连一次 2.4G WiFi，从 NTP 取标准时间
# 写入 RTC（含东八区偏移），随后立刻关掉电台省电。
#
# 依赖：wifi_cfg.py（NETS 多网络列表，兼容旧版 SSID / PASSWORD 单网络）
# 用法：
#   import timesync
#   ok = timesync.sync()          # True=已校时，False=失败（保留旧时间）
#
# 多 WiFi 策略：
#   按 wifi_cfg.NETS 列表逐个尝试；先 wlan.scan() 一遍，把当前环境里
#   在场的候选排在前面（避免对着不在场的 SSID 干等超时），不在场的
#   保留在末尾兜底（隐藏 SSID 扫描不到）。哪个连上就用哪个校时。
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


def _nets():
    """读 wifi_cfg 的候选网络列表：NETS 多网络优先，回退旧版 SSID/PASSWORD。"""
    try:
        import wifi_cfg
    except ImportError:
        print("校时失败：缺少 wifi_cfg.py")
        return ()
    nets = getattr(wifi_cfg, "NETS", None)
    if nets:
        return tuple((str(s), str(p)) for s, p in nets if str(s))
    ssid = getattr(wifi_cfg, "SSID", None)
    pwd = getattr(wifi_cfg, "PASSWORD", None)
    return ((str(ssid), str(pwd)),) if ssid else ()


def _ssid_str(b):
    """scan 返回的 SSID 在部分固件上是 bytes，统一转成 str 用于比较。"""
    return b.decode() if isinstance(b, bytes) else b


def _order_by_scan(wlan, nets):
    """扫描周围 AP，把在场的候选网络排到前面；扫描失败/为空则保持原顺序。"""
    try:
        seen = {_ssid_str(t[0]) for t in wlan.scan() if t[0]}
    except Exception as e:
        print("WiFi 扫描失败（按原顺序尝试）：%s" % e)
        return nets
    if not seen:
        return nets
    here = tuple(n for n in nets if n[0] in seen)
    away = tuple(n for n in nets if n[0] not in seen)
    if here and away:
        print("在场候选：%s；不在场（末尾兜底）：%s"
              % ("、".join(n[0] for n in here), "、".join(n[0] for n in away)))
    return here + away


def _connect(wlan, ssid, password, timeout_ms):
    """关联单个网络，成功返回 True。"""
    try:
        wlan.disconnect()
    except Exception:
        pass
    try:
        wlan.connect(ssid, password)
    except Exception as e:
        print("  连接 %s 失败：%s" % (ssid, e))
        return False
    t0 = time.ticks_ms()
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
            print("  %s 连接超时（%d s）" % (ssid, timeout_ms // 1000))
            return False
        time.sleep_ms(200)
    print("  已连接 %s" % ssid)
    return True


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
    """按 wifi_cfg 的候选列表逐个连 WiFi，取 NTP 写入本地时间到 RTC。

    显式传 ssid/password 时只连这一个（覆盖列表行为）。返回 True/False。
    """
    if ssid is not None and password is not None:
        nets = ((str(ssid), str(password)),)
    else:
        nets = _nets()
        if not nets:
            return False

    import machine

    wlan = network.WLAN(network.STA_IF)
    try:
        if not wlan.active():
            wlan.active(True)
        nets = _order_by_scan(wlan, nets)    # 在场的候选排前面，减少无效等待
        for s, p in nets:
            if _connect(wlan, s, p, timeout_ms):
                break
        else:
            print("校时失败：%d 个候选 WiFi 都没连上" % len(nets))
            return False

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