# FoloToy AI Passport —— 板端开机入口
#
# 启动流程：boot.py（官方空模板，未改）→ 本文件。
#   1) 启动页（同时完成屏幕初始化），背光 100%
#   2) WiFi + NTP 校时：板子无电池 RTC，复位即丢时间，必须开机校时；
#      失败不致命，退回板端 RTC（时间会从 1970 起算）
#   3) 工牌首页 P1：静态底板 + 动态块（时钟 / 日期 / 电量）
#   4) 加好友页 P2：整屏预渲染二维码，钉钉 / 微信两版
#   5) 空闲时序调光（对齐原厂 badge_theme.h）：
#        45s  -> 背光 35%（DIM）
#        150s -> 背光 0（熄屏；GRAM 保留，不重绘）
#        按键唤醒 -> 重绘启动图（用户指定：唤醒页 = 启动图），停留 2s 后自动回首页。
#                   停留期间屏上不是首页，动态块停止刷新，避免把时钟贴到启动图上。
#
# 按键导航（对齐 AI-Passport-电子工牌应用设计.md §2 与原厂 badge_logic.c）：
#   P1：OK 单击 -> 进 P2（其余键单击无动作）
#   P2：任一键单击 -> 钉钉 ⇄ 微信 切换；任一键长按 -> 回 P1
#   熄屏（背光 0）时首键只唤醒，不执行页面动作。
#
# 首页可见时的动态刷新：
#   冒号每 700ms 亮/暗闪烁（原厂 badge_page_home.c 同周期）；
#   分钟变化时重贴时钟；跨天时重贴日期章。三者都只重贴小窗口，不重刷整屏。
#
# 注意：进入 while 循环后 REPL 被占用，按 Ctrl-C 可中断回到 REPL。

import time

import battery
import buttons
import home
import qr
import splash
import timesync

PG_HOME, PG_QR = 0, 1   # 逻辑页面：P1 首页 / P2 加好友

IDLE_DIM_MS = 45000     # 原厂 BADGE_IDLE_DIM_S = 45s
IDLE_OFF_MS = 150000    # 原厂 BADGE_IDLE_OFF_S = 150s
DIM_PCT = 35            # 原厂 BADGE_BACKLIGHT_DIM
OFF_PCT = 0
ON_PCT = 100
POLL_MS = 20
COLON_MS = 700          # 冒号闪烁半周期
WAKE_SPLASH_MS = 2000   # 熄屏唤醒时启动图的停留时长，到点自动回首页
SOC_POLL_MS = 60000     # 电量刷新周期（CW2017 的 SOC 本身变化很慢，60s 足够）
SOC_FALLBACK = 100      # 电量计不可用时的兜底显示值（保证画面正常，不崩）
SYNC_RETRY_MS = 300000  # 开机没校上时：空闲状态下每 5 分钟后台重试一次，直到成功


def run():
    lcd = splash.show()                 # 启动页（同时完成屏幕初始化），背光 100%

    batt = battery.Battery()            # CW2017：I2C SDA=10 / SCL=7 / addr 0x63
    if batt.init():
        soc = batt.soc()
        print("电量计就绪：SOC=%s%% 电压=%smV" % (soc, batt.mv()))
    else:
        soc = None
        print("电量计不可用，电量显示回退为 %d%%" % SOC_FALLBACK)
    if soc is None:
        soc = SOC_FALLBACK

    timesync.sync()                     # 连 WiFi 取 NTP；失败返回 False，不抛异常
    # RTC 是否已可信：软复位会保留上次校时结果，此时无需后台重试
    synced = time.localtime()[0] >= 2020

    home.show(lcd=lcd, soc=soc)         # 首页：底板 + 动态块（时间取板端 RTC）
    t = time.localtime()
    print("首页已显示：%s %02d:%02d 电量 %d%%"
          % (home.date_text(t), t[3], t[4], soc))

    btn = buttons.Buttons()
    last_active = time.ticks_ms()
    level = ON_PCT
    page = PG_HOME                      # 逻辑页面
    screen = "home"                     # 屏上实际内容：splash / home / qr
    qr_sel = 0                          # P2 当前二维码：0=钉钉 1=微信
    back_home_at = None                 # 启动图停留结束、回首页的时刻（ticks）
    colon = True
    last_min = t[4]
    last_day = t[2]
    next_colon = time.ticks_add(time.ticks_ms(), COLON_MS)
    next_soc = time.ticks_add(time.ticks_ms(), SOC_POLL_MS)
    next_sync = time.ticks_add(time.ticks_ms(), SYNC_RETRY_MS)

    while True:
        now = time.ticks_ms()

        if btn.key() >= 0:              # 任一功能键按下 -> 视为活动
            last_active = now

        off_before = (level == OFF_PCT)  # 本帧开始时是否已熄屏
        evs = btn.events()               # 消费按键事件（熄屏时丢弃 = 首键只唤醒）
        if not off_before:
            for k, ev, held in evs:
                if page == PG_HOME:
                    # P1：只有 OK 单击进 P2，其余键单击无操作
                    if ev == buttons.EV_CLICK and k == buttons.KEY_OK:
                        qr.show(lcd=lcd, sel=qr_sel)
                        page, screen = PG_QR, "qr"
                        print("进入 P2 加好友页（%s）"
                              % ("钉钉" if qr_sel == 0 else "微信"))
                else:
                    # P2：任一键单击切二维码；任一键长按回 P1
                    if ev == buttons.EV_LONG:
                        home.show(lcd=lcd, soc=soc)
                        colon = True
                        t = time.localtime()
                        last_min, last_day = t[4], t[2]
                        next_colon = time.ticks_add(now, COLON_MS)
                        page, screen = PG_HOME, "home"
                        print("长按返回 P1 首页 %s %02d:%02d"
                              % (home.date_text(t), t[3], t[4]))
                    elif ev == buttons.EV_CLICK:
                        qr_sel ^= 1
                        qr.show(lcd=lcd, sel=qr_sel)
                        print("P2 切换 -> %s"
                              % ("钉钉" if qr_sel == 0 else "微信"))

        if back_home_at is not None and time.ticks_diff(now, back_home_at) >= 0:
            back_home_at = None
            home.show(lcd=lcd, soc=soc)     # 启动图停留结束：整屏重绘首页
            colon = True
            t = time.localtime()
            last_min, last_day = t[4], t[2]
            next_colon = time.ticks_add(now, COLON_MS)
            page, screen = PG_HOME, "home"
            print("唤醒结束：回到首页 %s %02d:%02d" % (home.date_text(t), t[3], t[4]))

        if screen == "home":
            if time.ticks_diff(now, next_colon) >= 0:
                colon = not colon
                home.set_colon(colon)
                next_colon = time.ticks_add(now, COLON_MS)

            t = time.localtime()
            if t[4] != last_min:
                last_min = t[4]
                home.set_clock("%02d" % t[3], "%02d" % t[4])
                print("时钟 -> %02d:%02d" % (t[3], t[4]))
            if t[2] != last_day:
                last_day = t[2]
                home.set_date(home.date_text(t))
                print("日期 -> " + home.date_text(t))

            if time.ticks_diff(now, next_soc) >= 0:
                next_soc = time.ticks_add(now, SOC_POLL_MS)
                s = batt.soc()
                if s is not None and s != soc:
                    soc = s
                    home.set_battery(soc)
                    print("电量 -> %d%%" % soc)

        idle = time.ticks_diff(now, last_active)
        if idle >= IDLE_OFF_MS:
            want = OFF_PCT
        elif idle >= IDLE_DIM_MS:
            want = DIM_PCT
        else:
            want = ON_PCT

        # 开机没校上：在空闲（已变暗/熄屏、用户不在看）时每 5 分钟后台重试，
        # 直到成功。sync 会阻塞几秒，此时屏已暗，影响最小。
        if (not synced and idle >= IDLE_DIM_MS
                and time.ticks_diff(now, next_sync) >= 0):
            print("后台重试校时…")
            ok = timesync.sync()
            next_sync = time.ticks_add(time.ticks_ms(), SYNC_RETRY_MS)
            if ok:
                synced = True
                last_min = -1           # 触发分钟重贴
                last_day = -1           # 触发日期重贴
                print("后台校时成功，时钟待刷新")

        if want != level:
            if want == ON_PCT and level == OFF_PCT:
                splash.show(lcd=lcd)    # 从熄屏唤醒：先重绘启动图（背光尚关），再开背光
                page, screen = PG_HOME, "splash"
                back_home_at = time.ticks_add(now, WAKE_SPLASH_MS)
                print("唤醒：显示启动图 %d ms，随后回首页，背光 %d%%" % (WAKE_SPLASH_MS, want))
            elif want == ON_PCT:
                print("唤醒：背光 %d%%" % want)
            elif want == OFF_PCT:
                print("熄屏：背光 0%%（%d ms 无操作）" % IDLE_OFF_MS)
            else:
                print("变暗：背光 %d%%（%d ms 无操作）" % (want, IDLE_DIM_MS))
            lcd.backlight_pct(want)
            level = want

        time.sleep_ms(POLL_MS)


try:
    run()
except Exception as e:
    # 开机脚本失败时把原因打出来，而不是静默死掉
    print("main.py 启动失败:", e)