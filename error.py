import os
import RPi.GPIO as GPIO
import time
import subprocess
import socket
import threading
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1107
from ina219 import INA219, DeviceRangeError
import re

# ✅ Wi-Fi 포털 모듈
import wifi_portal

# -------------------------
# 핀 / 상수
# -------------------------
LED_SUCCESS = 24
LED_ERROR = 25

BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17

SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1
MAX_VOLTAGE = 4.2

# 롱프레스(취소)
LONG_PRESS_CANCEL = 0.7
SOFT_DEBOUNCE_NEXT = 0.08
SOFT_DEBOUNCE_EXEC = 0.08

# -------------------------
# OLED
# -------------------------
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'
font_big = ImageFont.truetype(font_path, 15)
font_small = ImageFont.truetype(font_path, 10)

font_cache = {}
def get_font(size: int):
    f = font_cache.get(size)
    if f is None:
        f = ImageFont.truetype(font_path, size)
        font_cache[size] = f
    return f

# -------------------------
# 메뉴
# -------------------------
menu_options = ["업데이트 재시도", "기존 상태로 복구", "Wi-Fi 설정"]
current_menu_index = 0

# -------------------------
# 전역 상태(화면/액션)
# -------------------------
status_lock = threading.Lock()
status_message = ""
status_until = 0.0

stop_threads = False

action_lock = threading.Lock()
pending_action = None   # "retry" | "recover" | "wifi"

# ✅ Wi-Fi 액션 상태
wifi_action_lock = threading.Lock()
wifi_action_running = False
wifi_cancel_requested = False

# ✅ Wi-Fi 진행률(첫 코드와 동일 컨셉)
wifi_stage_lock = threading.Lock()
wifi_stage = {
    "active": False,
    "target_percent": 0,
    "display_percent": 0,
    "line1": "",
    "line2": "",
    "spinner": 0,
}

ap_state_lock = threading.Lock()
ap_state = {
    "last_clients": 0,
    "flash_until": 0.0,
    "poll_next": 0.0,
    "spinner": 0,
}

# 버튼 상태(롱프레스 감지용)
last_time_button_next = 0.0
last_time_button_exec = 0.0
next_press_time = None
next_is_down = False
next_long_handled = False

# -------------------------
# 유틸
# -------------------------
def run_quiet(cmd, timeout=3.0, shell=False):
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, shell=shell)
        return True
    except Exception:
        return False

def run_capture(cmd, timeout=4.0, shell=False):
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, shell=shell)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return 999, "", str(e)

def read_ina219_percentage():
    try:
        ina = INA219(SHUNT_OHMS)
        ina.configure()
        voltage = ina.voltage()
        if voltage <= MIN_VOLTAGE:
            return 0
        elif voltage >= MAX_VOLTAGE:
            return 100
        else:
            return int(((voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)) * 100)
    except (OSError, DeviceRangeError):
        return -1

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"

def get_current_time():
    return datetime.now().strftime('%H:%M:%S')

def set_status(msg, seconds=2.0):
    global status_message, status_until
    with status_lock:
        status_message = msg
        status_until = time.time() + seconds

def clear_status_if_expired():
    global status_message
    with status_lock:
        if status_message and time.time() > status_until:
            status_message = ""

# -------------------------
# Wi-Fi 표시/진행률 도우미
# -------------------------
def wifi_stage_set(percent, line1, line2=""):
    with wifi_stage_lock:
        wifi_stage["active"] = True
        wifi_stage["target_percent"] = int(max(0, min(100, percent)))
        if wifi_stage["display_percent"] > wifi_stage["target_percent"]:
            wifi_stage["display_percent"] = wifi_stage["target_percent"]
        wifi_stage["line1"] = line1 or ""
        wifi_stage["line2"] = line2 or ""

def wifi_stage_clear():
    with wifi_stage_lock:
        wifi_stage["active"] = False
        wifi_stage["target_percent"] = 0
        wifi_stage["display_percent"] = 0
        wifi_stage["line1"] = ""
        wifi_stage["line2"] = ""
        wifi_stage["spinner"] = 0

def wifi_stage_tick():
    with wifi_stage_lock:
        if not wifi_stage["active"]:
            wifi_stage["spinner"] = (wifi_stage["spinner"] + 1) % 4
            return
        t = wifi_stage["target_percent"]
        d = wifi_stage["display_percent"]
        if d < t:
            step = 1
            if t - d > 25:
                step = 3
            elif t - d > 12:
                step = 2
            wifi_stage["display_percent"] = min(t, d + step)
        wifi_stage["spinner"] = (wifi_stage["spinner"] + 1) % 4

def get_ap_station_count():
    try:
        r = subprocess.run(["iw", "dev", "wlan0", "station", "dump"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=0.7)
        if r.returncode != 0:
            return 0
        return sum(1 for line in (r.stdout or "").splitlines() if line.strip().startswith("Station "))
    except Exception:
        return 0

def ap_client_tick(wifi_running: bool):
    now = time.time()
    with ap_state_lock:
        ap_state["spinner"] = (ap_state["spinner"] + 1) % 4
        if not wifi_running:
            ap_state["last_clients"] = 0
            ap_state["flash_until"] = 0.0
            ap_state["poll_next"] = 0.0
            return
        if now < ap_state["poll_next"]:
            return
        ap_state["poll_next"] = now + 0.8
        prev = ap_state["last_clients"]

    cnt = get_ap_station_count()

    with ap_state_lock:
        ap_state["last_clients"] = cnt
        if cnt > 0 and prev == 0:
            ap_state["flash_until"] = now + 1.3

# -------------------------
# NetworkManager / Wi-Fi 연결(첫 코드와 동일 계열)
# -------------------------
last_good_wifi_profile = None

def has_real_internet(timeout=1.5):
    try:
        r = subprocess.run(
            ["ping", "-I", "wlan0", "-c", "1", "-W", "1", "8.8.8.8"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout
        )
        return r.returncode == 0
    except Exception:
        return False

def nm_is_active():
    rc, out, _ = run_capture(["systemctl", "is-active", "NetworkManager"], timeout=2.0)
    return (rc == 0) and ("active" in out.strip())

def nm_restart():
    run_quiet(["sudo", "systemctl", "enable", "--now", "NetworkManager"], timeout=6.0)
    run_quiet(["sudo", "systemctl", "restart", "NetworkManager"], timeout=6.0)

def nm_set_managed(managed: bool):
    v = "yes" if managed else "no"
    run_quiet(["sudo", "nmcli", "dev", "set", "wlan0", "managed", v], timeout=4.0)

def nm_disconnect_wlan0():
    run_quiet(["sudo", "nmcli", "dev", "disconnect", "wlan0"], timeout=4.0)

def nm_get_active_wifi_profile():
    rc, out, _ = run_capture(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], timeout=3.0)
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 3:
            name, ctype, dev = parts[0], parts[1], parts[2]
            if ctype == "wifi" and dev == "wlan0" and name:
                return name
    return None

def nm_autoconnect(timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if has_real_internet():
            return True
        time.sleep(0.7)
    return has_real_internet()

def nm_connect(ssid: str, psk: str, timeout=30):
    run_quiet(["sudo", "nmcli", "dev", "wifi", "rescan", "ifname", "wlan0"], timeout=6.0)
    rc, _, _ = run_capture(
        ["sudo", "nmcli", "--wait", str(int(timeout)), "dev", "wifi", "connect", ssid, "password", psk, "ifname", "wlan0"],
        timeout=timeout + 5
    )
    if rc == 0:
        return True
    # 한번 더
    run_quiet(["sudo", "nmcli", "dev", "wifi", "rescan", "ifname", "wlan0"], timeout=6.0)
    rc2, _, _ = run_capture(
        ["sudo", "nmcli", "--wait", str(int(timeout)), "dev", "wifi", "connect", ssid, "password", psk, "ifname", "wlan0"],
        timeout=timeout + 5
    )
    return rc2 == 0

def kill_portal_tmp_procs():
    cmd = r"""sudo bash -lc '
pids=$(pgrep -a hostapd | awk "/\/tmp\/hostapd\.conf/{print \$1}" | xargs)
[ -n "$pids" ] && kill -9 $pids || true
pids=$(pgrep -a dnsmasq | awk "/\/tmp\/dnsmasq\.conf/{print \$1}" | xargs)
[ -n "$pids" ] && kill -9 $pids || true
'"""
    run_quiet(cmd, timeout=6.0, shell=True)

def wlan0_soft_reset():
    run_quiet(["sudo", "ip", "addr", "flush", "dev", "wlan0"], timeout=3.0)
    run_quiet(["sudo", "ip", "link", "set", "wlan0", "down"], timeout=3.0)
    time.sleep(1)
    run_quiet(["sudo", "ip", "link", "set", "wlan0", "up"], timeout=3.0)
    time.sleep(1)

def prepare_for_ap_mode():
    global last_good_wifi_profile
    try:
        prof = nm_get_active_wifi_profile()
        if prof:
            last_good_wifi_profile = prof
    except Exception:
        pass

    if nm_is_active():
        nm_disconnect_wlan0()
        nm_set_managed(False)
        time.sleep(0.3)

def restore_after_ap_mode(timeout=25):
    global last_good_wifi_profile

    wifi_stage_set(5, "WiFi 종료 중", "프로세스 정리")
    kill_portal_tmp_procs()
    run_quiet(["sudo", "systemctl", "stop", "hostapd"], timeout=3.0)
    run_quiet(["sudo", "systemctl", "stop", "dnsmasq"], timeout=3.0)

    wifi_stage_set(25, "WiFi 재시작", "인터페이스 초기화")
    wlan0_soft_reset()

    wifi_stage_set(45, "WiFi 재시작", "NetworkManager")
    nm_set_managed(True)
    nm_restart()
    time.sleep(1.2)

    if last_good_wifi_profile:
        wifi_stage_set(60, "재연결 중", last_good_wifi_profile[:18])
        run_quiet(["sudo", "nmcli", "connection", "up", last_good_wifi_profile], timeout=12.0)

    wifi_stage_set(75, "인터넷 확인", "")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if has_real_internet():
            wifi_stage_set(100, "완료", "")
            time.sleep(0.4)
            wifi_stage_clear()
            return True
        p = 75 + int(25 * ((time.time() - t0) / max(1.0, timeout)))
        wifi_stage_set(min(99, p), "인터넷 확인", "")
        time.sleep(0.35)

    ok = has_real_internet()
    wifi_stage_set(100 if ok else 0, "완료" if ok else "실패", "")
    time.sleep(0.6)
    wifi_stage_clear()
    return ok

def connect_from_portal_nm(ssid: str, psk: str, timeout=35):
    wifi_stage_set(10, "연결 준비", "AP 종료")
    try:
        if hasattr(wifi_portal, "stop_ap"):
            wifi_portal.stop_ap()
    except Exception:
        pass

    kill_portal_tmp_procs()
    run_quiet(["sudo", "systemctl", "stop", "hostapd"], timeout=3.0)
    run_quiet(["sudo", "systemctl", "stop", "dnsmasq"], timeout=3.0)

    wifi_stage_set(30, "연결 준비", "인터페이스 초기화")
    wlan0_soft_reset()

    wifi_stage_set(50, "연결 준비", "NetworkManager")
    nm_set_managed(True)
    nm_restart()
    time.sleep(1.5)

    wifi_stage_set(70, "WiFi 연결 중", ssid[:18])
    ok = nm_connect(ssid, psk, timeout=timeout)
    if not ok:
        wifi_stage_set(0, "연결 실패", "")
        time.sleep(0.8)
        wifi_stage_clear()
        return False

    wifi_stage_set(85, "인터넷 확인", "")
    ok2 = nm_autoconnect(timeout=20)
    wifi_stage_set(100 if ok2 else 0, "완료" if ok2 else "실패", "")
    time.sleep(0.6)
    wifi_stage_clear()
    return ok2

def _portal_loop_until_connected_or_cancel():
    global wifi_cancel_requested

    prepare_for_ap_mode()

    wifi_stage_clear()
    wifi_portal.start_ap()
    if not getattr(wifi_portal, "_state", {}).get("server_started", False):
        wifi_portal.run_portal(block=False)
        wifi_portal._state["server_started"] = True

    t0 = time.time()
    while True:
        if wifi_cancel_requested:
            try:
                if hasattr(wifi_portal, "stop_ap"):
                    wifi_portal.stop_ap()
            except Exception:
                pass
            return "cancel"

        req = getattr(wifi_portal, "_state", {}).get("requested")
        if req:
            ssid = (req.get("ssid") or "").strip()
            psk = (req.get("psk") or "").strip()
            wifi_portal._state["requested"] = None

            ok = False
            if ssid and psk:
                ok = connect_from_portal_nm(ssid, psk, timeout=35)

            if ok:
                return True

            # 실패하면 다시 AP
            prepare_for_ap_mode()
            wifi_stage_clear()
            wifi_portal.start_ap()

        # 10분 제한
        if time.time() - t0 > 600:
            return False

        time.sleep(0.2)

# -------------------------
# OLED 표시
# -------------------------
def display_menu_or_wifi_screen():
    clear_status_if_expired()

    with wifi_action_lock:
        wifi_running = wifi_action_running

    battery_percentage = read_ina219_percentage()
    ip_address = get_ip_address()
    current_time = get_current_time()

    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, fill="black")

        # ✅ Wi-Fi 설정 중이면 Wi-Fi 전용 화면
        if wifi_running:
            with wifi_stage_lock:
                st_active = wifi_stage["active"]
                st_p = wifi_stage["display_percent"]
                st1 = wifi_stage["line1"]
                st2 = wifi_stage["line2"]
                sp = wifi_stage["spinner"]

            with ap_state_lock:
                flash_until = ap_state["flash_until"]
                ap_sp = ap_state["spinner"]

            dots = "." * sp
            dots2 = "." * ap_sp
            now = time.time()

            if st_active:
                draw.text((2, 0), (st1 or "")[:16], font=get_font(13), fill=255)
                line2 = (st2 or "")
                if line2:
                    draw.text((2, 16), (line2 + dots)[:18], font=get_font(11), fill=255)
                else:
                    draw.text((2, 16), ("처리중" + dots)[:18], font=get_font(11), fill=255)

                x1, y1, x2, y2 = 8, 48, 120, 60
                draw.rectangle([(x1, y1), (x2, y2)], outline=255, fill=0)
                fill_w = int((x2 - x1) * (st_p / 100.0))
                if fill_w > 0:
                    draw.rectangle([(x1, y1), (x1 + fill_w, y2)], fill=255)

                draw.text((2, 32), "NEXT 길게: 취소", font=get_font(11), fill=255)
            else:
                # AP 안내(첫 코드와 동일 텍스트)
                if now < flash_until:
                    draw.text((2, 0), ("연결됨!" + dots2)[:16], font=get_font(14), fill=255)
                else:
                    draw.text((2, 0), "WiFi 설정 모드", font=get_font(14), fill=255)

                draw.text((2, 18), "AP: GDSENG-SETUP", font=get_font(12), fill=255)
                draw.text((2, 34), "PW: 12345678", font=get_font(12), fill=255)
                draw.text((2, 50), "IP: 192.168.4.1:8080", font=get_font(12), fill=255)
            return

        # ✅ 평상시 메뉴 화면
        draw.text((10, 0), menu_options[current_menu_index], font=font_big, fill=255)
        draw.text((10, 28), f"Battery: {battery_percentage}%", font=font_small, fill=255)
        draw.text((10, 40), f"IP: {ip_address}", font=font_small, fill=255)
        draw.text((10, 52), f"Time: {current_time}", font=font_small, fill=255)

        with status_lock:
            msg = status_message
        if msg:
            draw.rectangle(device.bounding_box, outline="white", fill="black")
            draw.text((6, 18), msg, font=font_big, fill=255)

# -------------------------
# 버튼 콜백 (Wi-Fi 취소용 롱프레스는 BOTH edge로 처리)
# -------------------------
def button_next_edge(channel):
    global last_time_button_next, next_press_time, next_is_down, next_long_handled
    global current_menu_index

    now = time.time()
    if (now - last_time_button_next) < SOFT_DEBOUNCE_NEXT:
        return
    last_time_button_next = now

    # 눌림(LOW) / 떼짐(HIGH)
    if GPIO.input(BUTTON_PIN_NEXT) == GPIO.LOW:
        next_press_time = now
        next_is_down = True
        next_long_handled = False
    else:
        # 짧은 클릭이면 메뉴 이동(단, Wi-Fi 실행 중엔 메뉴 이동 금지)
        with wifi_action_lock:
            wifi_running = wifi_action_running

        if (not wifi_running) and next_is_down and (not next_long_handled) and (next_press_time is not None):
            # 짧게 눌렀다 떼면 메뉴 다음
            current_menu_index = (current_menu_index + 1) % len(menu_options)
            set_status("", 0.01)

        next_is_down = False
        next_press_time = None

def button_execute_callback(channel):
    global last_time_button_exec, pending_action
    now = time.time()
    if (now - last_time_button_exec) < SOFT_DEBOUNCE_EXEC:
        return
    last_time_button_exec = now

    # Wi-Fi 실행 중에는 EXEC 무시(원하면 여기서도 취소 처리 가능)
    with wifi_action_lock:
        if wifi_action_running:
            return

    option = menu_options[current_menu_index]
    with action_lock:
        if option == "업데이트 재시도":
            pending_action = "retry"
        elif option == "기존 상태로 복구":
            pending_action = "recover"
        elif option == "Wi-Fi 설정":
            pending_action = "wifi"

# -------------------------
# 기존 액션들
# -------------------------
def git_pull():
    shell_script_path = '/home/user/stm32/git-pull.sh'
    if not os.path.isfile(shell_script_path):
        with open(shell_script_path, 'w') as script_file:
            script_file.write("#!/bin/bash\n")
            script_file.write("cd /home/user/stm32\n")
            script_file.write("git remote update\n")
            script_file.write("if git status -uno | grep -q 'Your branch is up to date'; then\n")
            script_file.write("   echo '이미 최신 상태입니다.'\n")
            script_file.write("   exit 0\n")
            script_file.write("fi\n")
            script_file.write("git stash\n")
            script_file.write("git pull\n")
            script_file.write("git stash pop\n")
            script_file.flush()
            os.fsync(script_file.fileno())

    os.chmod(shell_script_path, 0o755)

    set_status("업데이트 중...", 60)

    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)

    try:
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode == 0:
            if "이미 최신 상태" in result.stdout:
                set_status("이미 최신 상태", 2)
                GPIO.output(LED_SUCCESS, True)
                time.sleep(0.8)
                GPIO.output(LED_SUCCESS, False)
            else:
                set_status("업데이트 성공!", 2)
                GPIO.output(LED_SUCCESS, True)
                time.sleep(0.8)
                GPIO.output(LED_SUCCESS, False)
                subprocess.run(["python3", "/home/user/stm32/main.py"])
        else:
            set_status("업데이트 실패", 2)
            GPIO.output(LED_ERROR, True)
            time.sleep(0.8)
            GPIO.output(LED_ERROR, False)

    except Exception:
        set_status("오류 발생", 2)
        GPIO.output(LED_ERROR, True)
        time.sleep(0.8)
        GPIO.output(LED_ERROR, False)

def recover_previous_state():
    set_status("복구 실행", 2)
    subprocess.run(["python3", "/home/user/stm32/main.py"])

# -------------------------
# ✅ 새 Wi-Fi 설정(첫 코드의 로직 이식)
# -------------------------
def wifi_setup():
    global wifi_cancel_requested

    # 이미 인터넷이면 종료
    if has_real_internet():
        set_status("이미 인터넷 연결됨", 2)
        return

    # hostapd/dnsmasq 체크
    r1 = subprocess.run(["which", "hostapd"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    r2 = subprocess.run(["which", "dnsmasq"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if (r1.returncode != 0) or (r2.returncode != 0):
        set_status("AP 구성 불가\nhostapd/dnsmasq\n없음", 3)
        return

    # Wi-Fi 액션 시작 플래그
    with wifi_action_lock:
        wifi_cancel_requested = False
        # stage 화면(AP 안내)로 먼저
        wifi_stage_clear()

    # 실제 포털 루프(블로킹)
    result = _portal_loop_until_connected_or_cancel()

    if result == "cancel":
        wifi_stage_set(10, "취소 처리중", "재연결 준비")
        ok_restore = restore_after_ap_mode(timeout=25)
        set_status("재연결 완료" if ok_restore else "재연결 실패", 2.0)
        wifi_stage_clear()
        return

    if result is True:
        set_status("WiFi 연결 완료", 2)
        wifi_stage_clear()
        return

    set_status("WiFi 연결 실패", 2)
    wifi_stage_clear()

# -------------------------
# 워커/표시/롱프레스 감지 스레드
# -------------------------
def action_worker():
    global pending_action
    global wifi_action_running

    while not stop_threads:
        act = None
        with action_lock:
            if pending_action:
                act = pending_action
                pending_action = None

        if act == "retry":
            git_pull()

        elif act == "recover":
            recover_previous_state()

        elif act == "wifi":
            with wifi_action_lock:
                wifi_action_running = True
                # Wi-Fi 화면으로 전환 (status보다 우선)
                wifi_stage_clear()
                with ap_state_lock:
                    ap_state["last_clients"] = 0
                    ap_state["flash_until"] = 0.0
                    ap_state["poll_next"] = 0.0
                    ap_state["spinner"] = 0

            try:
                wifi_setup()
            finally:
                with wifi_action_lock:
                    wifi_action_running = False
                wifi_stage_clear()

        time.sleep(0.05)

def display_loop():
    # 0.2초 주기로 업데이트 (Wi-Fi 진행률 부드럽게)
    while not stop_threads:
        with wifi_action_lock:
            wifi_running = wifi_action_running
        wifi_stage_tick()
        ap_client_tick(wifi_running)
        display_menu_or_wifi_screen()
        time.sleep(0.2)

def longpress_cancel_loop():
    # Wi-Fi 실행 중 NEXT 길게 누르면 취소 플래그
    global wifi_cancel_requested, next_long_handled
    while not stop_threads:
        with wifi_action_lock:
            wifi_running = wifi_action_running

        if wifi_running and next_is_down and (not next_long_handled) and (next_press_time is not None):
            if time.time() - next_press_time >= LONG_PRESS_CANCEL:
                next_long_handled = True
                wifi_cancel_requested = True
                wifi_stage_set(5, "취소 처리중", "잠시만")

        time.sleep(0.03)

# -------------------------
# main
# -------------------------
def main():
    global stop_threads

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    GPIO.setup(LED_SUCCESS, GPIO.OUT)
    GPIO.setup(LED_ERROR, GPIO.OUT)

    GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # NEXT는 BOTH edge로(롱프레스 + 짧은 클릭 처리)
    GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.BOTH, callback=button_next_edge, bouncetime=60)
    GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING, callback=button_execute_callback, bouncetime=120)

    # 화면 업데이트 스레드
    t_display = threading.Thread(target=display_loop, daemon=True)
    t_display.start()

    # 액션 처리 스레드
    t_worker = threading.Thread(target=action_worker, daemon=True)
    t_worker.start()

    # 롱프레스 취소 감지 스레드
    t_cancel = threading.Thread(target=longpress_cancel_loop, daemon=True)
    t_cancel.start()

    display_menu_or_wifi_screen()

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_threads = True
        GPIO.cleanup()

if __name__ == "__main__":
    main()
