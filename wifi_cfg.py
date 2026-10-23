# FoloToy AI Passport —— WiFi 连接配置（仅供 timesync.py 读取）
#
# 本文件用于「开机自动校时」：板子没有 RTC，每次复位后会用这里的 WiFi 连上
# NTP 服务器（ntp.aliyun.com）取回真实时间。
#
# 请填入你自己的 2.4G 频段 SSID（ESP32-C3 不支持 5G 频段）。
# 不填也能正常使用，只是时间走会默认起点、时钟不是真实时间。
# 注意：本文件以明文形式存放在板子 flash 上，请勿填写重要账号的密码。

SSID = ""
PASSWORD = ""