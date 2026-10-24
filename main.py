# FoloToy AI Passport —— 板端开机入口
#
# 启动流程：boot.py（官方空模板，未改）→ 本文件。
#   1) 启动页（同时完成屏幕初始化），背光 100%
#   2) WiFi + NTP 校时：板子无电池 RTC，复位即丢时间，必须开机校时；
#      失败不致命，退回板端 RTC（时间会从 1970 起算）
#   3) 工牌首页 P1：静态底板 + 动态块（时钟 / 日期 / 电量）
#   4) 纪念日页 P3：静态底板 + 倒计时数字槽 + 电量
#   5) 加好友页 P2：静态底板（钉钉/微信两版）+ 头条电量精灵块
#   6) 空闲时序调光（对齐原厂 badge_theme.h）：
#        45s  -> 背光 35%（DIM）
#        150s -> 背光 0（熄屏；GRAM 保留，不重绘）
#        按键唤醒 -> 重绘启动图（用户指定：唤醒页 = 启动图），停留 2s 后自动回首页。
#                   停留期间屏上不是首页，动态块停止刷新，避免把时钟贴到启动图上。
#
# 按键导航（批次 4 定型：4 面环，UP = 后退 / DOWN = 前进，取模 4 绕圈不断档）：
#   环序：首页 -> 纪念日 -> 微信码 -> 钉钉码 ->（回到首页）
#   任意面：UP / DOWN 单击翻页；
#           OK 单击 -> 直达加好友页（记住上次看的是钉钉还是微信，首次钉钉），
#                      已经在该面则原地 钉钉 ⇄ 微信；
#           OK 长按 -> 回首页（首页上无操作）。
#   熄屏（背光 0）时首键只唤醒，不执行页面动作。
#
# 首页可见时的动态刷新：
#   冒号每 700ms 亮/暗闪烁（原厂 badge_page_home.c 同周期）；
#   分钟变化时重贴时钟；跨天时重贴日期章。三者都只重贴小窗口，不重刷整屏。
#   低电量闪烁：SOC ≤ 5% 时电量指示条 500ms 亮/暗交替（对齐设计 §5 的 1Hz 闪烁；
#   「SOC ≤ 20% 条变红」由烘焙好的色阶自动满足，板端无需干预）。
#
# 注意：进入 while 循环后 REPL 被占用，按 Ctrl-C 可中断回到 REPL。

import time

import anniv
import battery
import buttons
import home
import qr
import splash
import timesync

# 4 面环（批次 4 定型）。DOWN = 前进(+1) / UP = 后退(-1)，取模 4 绕圈不断档。
F_HOME, F_ANNIV, F_QR_WX, F_QR_DING = 0, 1, 2, 3
QR_FACE = (F_QR_DING, F_QR_WX)  # qr_sel（0=钉钉 1=微信）-> 环上的面号
QR_NAMES = ("钉钉", "微信")

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
LOW_SOC = 5             # 低电量阈值：≤ 此值时指示条闪烁（设计 §5；_off 变体只烘焙了 0..5）
BAT_BLINK_MS = 500      # 低电量指示条亮/暗半周期（设计 §5 的 1Hz 闪烁）


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

    # 页面进入 helper：批次 0 把三处重复的「整屏重绘 + 复位动态刷新状态」收敛到一处，
    # 批次 4 在其上扩展成 4 面环（首页 / 纪念日 / 微信码 / 钉钉码），go_face 统一分发。
    def enter_home(now, note, tail=""):
        """整屏重绘首页并把动态刷新状态复位；note/tail 拼出与原来一致的日志。"""
        nonlocal colon, last_min, last_day, next_colon, face, screen
        nonlocal bat_bar, next_bat_blink
        home.show(lcd=lcd, soc=soc)
        t = time.localtime()
        colon = True
        bat_bar = True                  # 首页重绘后指示条默认亮，闪烁状态一并复位
        last_min, last_day = t[4], t[2]
        next_colon = time.ticks_add(now, COLON_MS)
        next_bat_blink = time.ticks_add(now, BAT_BLINK_MS)
        face, screen = F_HOME, "home"
        print("%s%s %02d:%02d%s" % (note, home.date_text(t), t[3], t[4], tail))
        return t

    def enter_anniv(now, note="翻到纪念日页"):
        """整屏重绘纪念日页并把电量/跨天刷新状态复位。"""
        nonlocal face, screen, last_days, bat_bar, next_bat_blink, next_soc
        days = anniv.days_now()
        bat_bar = True                  # 重绘后指示条默认亮，闪烁状态一并复位
        anniv.show(lcd=lcd, days=days, soc=soc, bar=bat_bar)
        last_days = days
        next_bat_blink = time.ticks_add(now, BAT_BLINK_MS)
        next_soc = time.ticks_add(now, SOC_POLL_MS)
        face, screen = F_ANNIV, "anniv"
        print("%s（倒计时 %d 天，电量 %d%%）" % (note, days, soc))

    def enter_qr(f, now, note="翻到加好友页"):
        """整屏重绘二维码面（f = F_QR_WX / F_QR_DING），电量状态与其它面对齐。"""
        nonlocal face, screen, next_soc
        sel = 1 if f == F_QR_WX else 0
        qr.show(lcd=lcd, sel=sel, soc=soc, bar=bat_bar)
        next_soc = time.ticks_add(now, SOC_POLL_MS)
        face, screen = f, "qr"
        print("%s（%s，电量 %d%%）" % (note, QR_NAMES[sel], soc))

    def set_batt(bar=True):
        """按当前面重贴电量窗口（首页/纪念日共用 bat_*，二维码页用 batq_*）。"""
        if screen == "anniv":
            anniv.set_battery(soc, bar=bar)
        elif screen == "qr":
            qr.set_battery(soc, bar=bar)
        else:
            home.set_battery(soc, bar=bar)

    def go_face(n, now):
        """切到环上的第 n 面（取模 4，绕圈不断档）。"""
        n %= 4
        if n == F_HOME:
            enter_home(now, "翻到首页 ")
        elif n == F_ANNIV:
            enter_anniv(now)
        else:
            enter_qr(n, now)

    t = enter_home(time.ticks_ms(), "首页已显示：", " 电量 %d%%" % soc)  # 首页：底板 + 动态块

    btn = buttons.Buttons()
    last_active = time.ticks_ms()
    level = ON_PCT
    face = F_HOME                       # 4 面环当前面号
    screen = "home"                     # 屏上实际内容：splash / home / anniv / qr
    qr_sel = 0                          # 记住的二维码面：0=钉钉 1=微信（OK 单击直达用）
    back_home_at = None                 # 启动图停留结束、回首页的时刻（ticks）
    colon = True
    last_min = t[4]
    last_day = t[2]
    last_days = anniv.days_now(t)       # 纪念日页当前显示天数（跨天时重贴）
    next_colon = time.ticks_add(time.ticks_ms(), COLON_MS)
    bat_bar = True                      # 低电量闪烁时指示条的当前亮/暗态
    next_bat_blink = time.ticks_add(time.ticks_ms(), BAT_BLINK_MS)
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
                if ev == buttons.EV_LONG:
                    # OK 长按：从任何非首页面回首页（首页上无操作）
                    if k == buttons.KEY_OK and face != F_HOME:
                        enter_home(now, "长按返回首页 ")
                elif k == buttons.KEY_UP:
                    go_face(face - 1, now)      # UP = 后退（绕圈）
                elif k == buttons.KEY_DOWN:
                    go_face(face + 1, now)      # DOWN = 前进（绕圈）
                else:                           # OK 单击
                    if face == F_QR_WX or face == F_QR_DING:
                        qr_sel ^= 1             # 已在二维码面：原地 钉钉 ⇄ 微信
                    go_face(QR_FACE[qr_sel], now)   # 其余面：直达记住的二维码面

        if back_home_at is not None and time.ticks_diff(now, back_home_at) >= 0:
            back_home_at = None
            enter_home(now, "唤醒结束：回到首页 ")   # 启动图停留结束：整屏重绘首页

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
        elif screen == "anniv":
            d = anniv.days_now()           # 跨天时倒计时 ±1，重贴数字槽
            if d != last_days:
                last_days = d
                anniv.set_days(d)
                print("倒计时 -> %d 天" % d)

        # 电量刷新：三个页面共用同一节奏（启动图屏上无电量窗口，不必刷）
        if screen in ("home", "anniv", "qr"):
            if time.ticks_diff(now, next_soc) >= 0:
                next_soc = time.ticks_add(now, SOC_POLL_MS)
                s = batt.soc()
                if s is not None and s != soc:
                    soc = s
                    set_batt(bat_bar)
                    print("电量 -> %d%%" % soc)

            # 低电量提示：SOC ≤ 5% 时指示条 500ms 亮/暗闪烁，重新变高后恢复常亮
            if soc <= LOW_SOC:
                if time.ticks_diff(now, next_bat_blink) >= 0:
                    bat_bar = not bat_bar
                    next_bat_blink = time.ticks_add(now, BAT_BLINK_MS)
                    set_batt(bat_bar)
            elif not bat_bar:
                bat_bar = True
                next_bat_blink = time.ticks_add(now, BAT_BLINK_MS)
                set_batt()

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
                face, screen = F_HOME, "splash"   # 停留结束后 enter_home 统一回首页
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