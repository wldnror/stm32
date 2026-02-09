from datetime import datetime
import RPi.GPIO as GPIO
import time
import os
import sys
import socket
from PIL import Image, ImageFont
from luma.core.interface.serial import i2c
from luma.oled.device import sh1107
from luma.core.render import canvas
import subprocess
from ina219 import INA219
import threading
import re

import wifi_portal

VISUAL_X_OFFSET = 0
display_lock = threading.Lock()
stm32_state_lock = threading.Lock()

wifi_action_lock = threading.Lock()
wifi_action_requested = False
wifi_action_running = False

# --------- OLED override(중요: 화면 깨짐/깜빡임 방지) ----------
ui_override_lock = threading.Lock()
ui_override = {
    "active": False,
    "kind": "none",         # "progress" | "text"
    "percent": 0,
    "message": "",
    "pos": (0, 0),
    "font_size": 15,
    "line2": "",
}
# -------------------------------------------------------------

BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17
LED_SUCCESS = 24
LED_ERROR = 25
LED_ERROR1 = 23

SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1
MAX_VOLTAGE = 4.2

auto_flash_done_connection = False

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# --- debounce 튜닝 (버튼 잘 안눌리는 문제 개선) ---
last_time_button_next_pressed = 0.0
last_time_button_execute_pressed = 0.0
SOFT_DEBOUNCE_NEXT = 0.08
SOFT_DEBOUNCE_EXEC = 0.08

button_press_interval = 0.15
LONG_PRESS_THRESHOLD = 0.7              # EXECUTE long
NEXT_LONG_CANCEL_THRESHOLD = 0.7        # NEXT long (wifi cancel)

need_update = False
is_command_executing = False

execute_press_time = None
execute_is_down = False
execute_long_handled = False

# NEXT input state
next_press_time = None
next_is_down = False
next_long_handled = False
next_pressed_event = False  # short press only

is_executing = False

menu_stack = []
current_menu = None
commands = []
command_names = []
command_types = []
menu_extras = []
current_command_index = 0

status_message = ""
message_position = (0, 0)
message_font_size = 17

ina = None
battery_percentage = -1

connection_success = False
connection_failed_since_last_success = False
last_stm32_check_time = 0.0

stop_threads = False

# wifi cancel flag
wifi_cancel_requested = False

# cached network ui
cached_ip = "0.0.0.0"
cached_wifi_level = 0

# 마지막으로 STA에서 잘 붙어있던 NM 프로파일 이름(복구용)
last_good_wifi_profile = None


# ----------------------------
# Utils / System helpers
# ----------------------------
def kill_openocd():
    subprocess.run(["sudo", "pkill", "-f", "openocd"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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


def set_ui_progress(percent, message, pos=(0, 0), font_size=15):
    with ui_override_lock:
        ui_override["active"] = True
        ui_override["kind"] = "progress"
        ui_override["percent"] = int(max(0, min(100, percent)))
        ui_override["message"] = message
        ui_override["pos"] = pos
        ui_override["font_size"] = font_size
        ui_override["line2"] = ""


def set_ui_text(line1, line2="", pos=(0, 0), font_size=15):
    with ui_override_lock:
        ui_override["active"] = True
        ui_override["kind"] = "text"
        ui_override["message"] = line1
        ui_override["line2"] = line2
        ui_override["pos"] = pos
        ui_override["font_size"] = font_size
        ui_override["percent"] = 0


def clear_ui_override():
    with ui_override_lock:
        ui_override["active"] = False
        ui_override["kind"] = "none"
        ui_override["message"] = ""
        ui_override["line2"] = ""
        ui_override["percent"] = 0


def has_real_internet(timeout=1.5):
    # “진짜 인터넷” 판정: wlan0로 ping 8.8.8.8
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


# ----------------------------
# NetworkManager 기반 Wi-Fi 유틸 (JI 저장소 기준)
# ----------------------------
def nm_is_active():
    rc, out, _ = run_capture(["systemctl", "is-active", "NetworkManager"], timeout=2.0)
    return (rc == 0) and ("active" in out.strip())


def nm_restart():
    run_quiet(["sudo", "systemctl", "enable", "--now", "NetworkManager"], timeout=6.0)
    run_quiet(["sudo", "systemctl", "restart", "NetworkManager"], timeout=6.0)


def nm_set_managed(managed: bool):
    # AP 모드 들어갈 때 NM이 간섭하면 꼬이므로 unmanaged로 두고,
    # 나올 때 managed로 복구
    v = "yes" if managed else "no"
    run_quiet(["sudo", "nmcli", "dev", "set", "wlan0", "managed", v], timeout=4.0)


def nm_disconnect_wlan0():
    run_quiet(["sudo", "nmcli", "dev", "disconnect", "wlan0"], timeout=4.0)


def nm_get_active_wifi_profile():
    # wlan0에 붙어있는 활성 connection name 추출
    # 출력 예: NAME:JI  DEVICE:wlan0  TYPE:wifi
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
    # NM 정책(우선순위/최근연결/신호 등)에 따라 자동 연결
    t0 = time.time()
    while time.time() - t0 < timeout:
        if has_real_internet():
            return True
        # 활성 wifi 프로파일이 생기면 DHCP 기다리는 단계일 수 있어 조금 더 기다림
        time.sleep(0.7)
    return has_real_internet()


def nm_connect(ssid: str, psk: str, timeout=30):
    # 스캔 후 연결 (성공 시 NM 프로파일로 저장됨)
    # "오류: SSID를 찾을 수 없습니다" 대비: rescan 한번 더
    run_quiet(["sudo", "nmcli", "dev", "wifi", "rescan", "ifname", "wlan0"], timeout=6.0)
    rc, out, err = run_capture(
        ["sudo", "nmcli", "--wait", str(int(timeout)), "dev", "wifi", "connect", ssid, "password", psk, "ifname", "wlan0"],
        timeout=timeout + 5
    )
    if rc == 0:
        return True

    # 한 번 더(스캔 타이밍 이슈)
    run_quiet(["sudo", "nmcli", "dev", "wifi", "rescan", "ifname", "wlan0"], timeout=6.0)
    rc2, out2, err2 = run_capture(
        ["sudo", "nmcli", "--wait", str(int(timeout)), "dev", "wifi", "connect", ssid, "password", psk, "ifname", "wlan0"],
        timeout=timeout + 5
    )
    return rc2 == 0


def kill_portal_tmp_procs():
    # 포탈이 /tmp/hostapd.conf, /tmp/dnsmasq.conf로 띄운 프로세스만 정확히 kill
    # (서비스 stop으로는 안 내려가는 케이스가 있어서 필수)
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


# ----------------------------
# INA219 / Battery
# ----------------------------
def init_ina219():
    global ina
    try:
        ina = INA219(SHUNT_OHMS)
        ina.configure()
    except Exception:
        ina = None


def read_ina219_percentage():
    global ina
    if ina is None:
        return -1
    try:
        voltage = ina.voltage()
        if voltage <= MIN_VOLTAGE:
            return 0
        if voltage >= MAX_VOLTAGE:
            return 100
        return int(((voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)) * 100)
    except Exception:
        return -1


def battery_monitor_thread():
    global battery_percentage
    while not stop_threads:
        battery_percentage = read_ina219_percentage()
        time.sleep(2)


# ----------------------------
# Button callbacks
# ----------------------------
def button_next_edge(channel):
    """
    NEXT는 BOTH edge로:
    - 눌림: 시간 기록(롱프레스 감지)
    - 떼짐: 짧으면 next_pressed_event=True
    """
    global last_time_button_next_pressed
    global next_press_time, next_is_down, next_long_handled, next_pressed_event

    now = time.time()

    # soft debounce (너무 크면 버튼이 안먹는 느낌이 남)
    if (now - last_time_button_next_pressed) < SOFT_DEBOUNCE_NEXT:
        return
    last_time_button_next_pressed = now

    if GPIO.input(BUTTON_PIN_NEXT) == GPIO.LOW:  # pressed
        next_press_time = now
        next_is_down = True
        next_long_handled = False
    else:  # released
        if next_is_down and (not next_long_handled) and (next_press_time is not None):
            dt = now - next_press_time
            if dt < NEXT_LONG_CANCEL_THRESHOLD:
                next_pressed_event = True
        next_is_down = False
        next_press_time = None


def button_execute_callback(channel):
    global last_time_button_execute_pressed, execute_press_time, execute_is_down, execute_long_handled
    now = time.time()
    if (now - last_time_button_execute_pressed) < SOFT_DEBOUNCE_EXEC:
        return
    last_time_button_execute_pressed = now
    execute_press_time = now
    execute_is_down = True
    execute_long_handled = False


GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)

GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.BOTH, callback=button_next_edge, bouncetime=60)
GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING, callback=button_execute_callback, bouncetime=80)

GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)
GPIO.setup(LED_ERROR1, GPIO.OUT)


# ----------------------------
# STM32 connection polling
# ----------------------------
def check_stm32_connection():
    global connection_success, connection_failed_since_last_success, is_command_executing

    if is_command_executing:
        return False

    try:
        command = [
            "sudo", "openocd",
            "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
            "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
            "-c", "init",
            "-c", "exit"
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1.2
        )

        ok = (result.returncode == 0)

        with stm32_state_lock:
            if ok:
                connection_failed_since_last_success = False
                connection_success = True
            else:
                connection_failed_since_last_success = True
                connection_success = False

        return ok

    except subprocess.TimeoutExpired:
        with stm32_state_lock:
            connection_failed_since_last_success = True
            connection_success = False
        return False
    except Exception:
        with stm32_state_lock:
            connection_failed_since_last_success = True
            connection_success = False
        return False


def stm32_poll_thread():
    global last_stm32_check_time, auto_flash_done_connection
    while not stop_threads:
        time.sleep(0.05)

        if is_command_executing:
            continue

        if commands:
            try:
                if command_types[current_command_index] == "system":
                    continue
            except Exception:
                continue

        now = time.time()
        if now - last_stm32_check_time <= 0.7:
            continue
        last_stm32_check_time = now

        with stm32_state_lock:
            prev_state = connection_success

        check_stm32_connection()

        with stm32_state_lock:
            cur_state = connection_success

        if cur_state and (not prev_state):
            auto_flash_done_connection = False


# ----------------------------
# OLED / Fonts
# ----------------------------
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

font_path = "/usr/share/fonts/truetype/malgun/malgunbd.ttf"
font_big = ImageFont.truetype(font_path, 12)
font_small = ImageFont.truetype(font_path, 11)
font_st = ImageFont.truetype(font_path, 11)
font = ImageFont.truetype(font_path, 17)
font_1 = ImageFont.truetype(font_path, 21)
font_sysupdate = ImageFont.truetype(font_path, 17)
font_time = ImageFont.truetype(font_path, 12)

font_cache = {}
def get_font(size: int):
    f = font_cache.get(size)
    if f is None:
        f = ImageFont.truetype(font_path, size)
        font_cache[size] = f
    return f


low_battery_icon = Image.open("/home/user/stm32/img/bat.png")
medium_battery_icon = Image.open("/home/user/stm32/img/bat.png")
high_battery_icon = Image.open("/home/user/stm32/img/bat.png")
full_battery_icon = Image.open("/home/user/stm32/img/bat.png")


def select_battery_icon(percentage):
    if percentage < 20:
        return low_battery_icon
    if percentage < 60:
        return medium_battery_icon
    if percentage < 100:
        return high_battery_icon
    return full_battery_icon


def draw_center_text_autofit(draw, text, center_x, center_y, max_width, start_size, min_size=10):
    size = start_size
    while size >= min_size:
        f = get_font(size)
        try:
            bbox = draw.textbbox((0, 0), text, font=f)
            w = bbox[2] - bbox[0]
        except Exception:
            try:
                w, _ = draw.textsize(text, font=f)
            except Exception:
                w = len(text) * (size // 2)
        if w <= max_width:
            try:
                draw.text((center_x, center_y), text, font=f, fill=255, anchor="mm")
            except TypeError:
                draw.text((center_x, center_y), text, font=f, fill=255)
            return
        size -= 1

    f = get_font(min_size)
    try:
        draw.text((center_x, center_y), text, font=f, fill=255, anchor="mm")
    except TypeError:
        draw.text((center_x, center_y), text, font=f, fill=255)


def draw_wifi_bars(draw, x, y, level):  # level 0~4
    bar_w = 3
    gap = 2
    base_h = 3
    max_h = base_h + 3 * 3  # 12

    for i in range(4):
        h = base_h + i * 3
        xx = x + i * (bar_w + gap)
        yy = y + (max_h - h)
        if level >= (i + 1):
            draw.rectangle([xx, yy, xx + bar_w, y + max_h], fill=255)
        else:
            # 테두리(네모)는 없애고, 비어있는 바는 얇게라도 보이게 "점"으로 최소 표현
            # (완전 없애면 아이콘이 아예 사라져서 UX가 나빠짐)
            draw.rectangle([xx, y + max_h - 1, xx + bar_w, y + max_h], fill=255)


# ----------------------------
# Menu build
# ----------------------------
FIRMWARE_DIR = "/home/user/stm32/Program"
OUT_SCRIPT_PATH = "/home/user/stm32/out.py"


def parse_order_and_name(name: str, is_dir: bool):
    raw = name if is_dir else os.path.splitext(name)[0]
    m = re.match(r"^(\d+)\.(.*)$", raw)
    if m:
        order = int(m.group(1))
        display = m.group(2).lstrip()
    else:
        order = 9999
        display = raw
    return order, display


def build_menu_for_dir(dir_path, is_root=False):
    entries = []
    try:
        for fname in os.listdir(dir_path):
            full_path = os.path.join(dir_path, fname)

            if os.path.isdir(full_path):
                order, display_name = parse_order_and_name(fname, is_dir=True)
                display_name = "▶ " + display_name
                entries.append((order, 0, display_name, "dir", full_path))

            elif fname.lower().endswith(".bin"):
                order, display_name = parse_order_and_name(fname, is_dir=False)
                openocd_cmd = (
                    "sudo openocd "
                    "-f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
                    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
                    f"-c \"program {full_path} verify reset exit 0x08000000\""
                )
                entries.append((order, 1, display_name, "bin", openocd_cmd))

    except FileNotFoundError:
        entries = []

    entries.sort(key=lambda x: (x[0], x[1], x[2]))

    commands_local = []
    names_local = []
    types_local = []
    extras_local = []

    for order, type_pri, display_name, item_type, extra in entries:
        if item_type == "dir":
            commands_local.append(None)
            names_local.append(display_name)
            types_local.append("dir")
            extras_local.append(extra)
        elif item_type == "bin":
            commands_local.append(extra)
            names_local.append(display_name)
            types_local.append("bin")
            extras_local.append(None)

    if is_root:
        # 기존 wifi_portal.has_internet() 대신 "진짜 인터넷(ping)" 기준으로 메뉴 노출
        online = has_real_internet()

        if online:
            commands_local.append(f"python3 {OUT_SCRIPT_PATH}")
            names_local.append("FW 추출(OUT)")
            types_local.append("script")
            extras_local.append(None)

            commands_local.append("git_pull")
            names_local.append("시스템 업데이트")
            types_local.append("system")
            extras_local.append(None)

        commands_local.append("wifi_setup")
        names_local.append("Wi-Fi 설정")
        types_local.append("wifi")
        extras_local.append(None)

    else:
        commands_local.append(None)
        names_local.append("◀ 이전으로")
        types_local.append("back")
        extras_local.append(None)

    return {
        "dir": dir_path,
        "commands": commands_local,
        "names": names_local,
        "types": types_local,
        "extras": extras_local,
    }


def refresh_root_menu(reset_index=False):
    global current_menu, commands, command_names, command_types, menu_extras, current_command_index
    current_menu = build_menu_for_dir(FIRMWARE_DIR, is_root=True)
    commands = current_menu["commands"]
    command_names = current_menu["names"]
    command_types = current_menu["types"]
    menu_extras = current_menu["extras"]
    if reset_index or (current_command_index >= len(commands)):
        current_command_index = 0


refresh_root_menu(reset_index=True)


# ----------------------------
# Git pull / System update
# ----------------------------
def git_pull():
    shell_script_path = "/home/user/stm32/git-pull.sh"
    if not os.path.isfile(shell_script_path):
        with open(shell_script_path, "w") as script_file:
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

    set_ui_text("시스템", "업데이트 중", pos=(20, 10), font_size=15)

    try:
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

        if result.returncode == 0:
            if "이미 최신 상태" in result.stdout:
                set_ui_text("이미 최신 상태", "", pos=(10, 18), font_size=15)
                time.sleep(1.0)
            else:
                GPIO.output(LED_SUCCESS, True)
                set_ui_text("업데이트 성공!", "", pos=(10, 18), font_size=15)
                time.sleep(1.0)
                GPIO.output(LED_SUCCESS, False)
                restart_script()
        else:
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            set_ui_text("업데이트 실패", "", pos=(10, 18), font_size=15)
            time.sleep(1.2)

    except Exception:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("오류 발생", "", pos=(20, 18), font_size=15)
        time.sleep(1.2)

    finally:
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()


# ----------------------------
# OpenOCD lock/unlock
# ----------------------------
def unlock_memory():
    set_ui_progress(0, "메모리 잠금\n   해제 중", pos=(18, 0), font_size=15)

    openocd_command = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "stm32f1x unlock 0",
        "-c", "reset run",
        "-c", "shutdown"
    ]
    result = subprocess.run(openocd_command)

    if result.returncode == 0:
        set_ui_progress(30, "메모리 잠금\n 해제 성공!", pos=(20, 0), font_size=15)
        time.sleep(1)
        return True

    set_ui_progress(0, "메모리 잠금\n 해제 실패!", pos=(20, 0), font_size=15)
    time.sleep(1)

    global need_update
    need_update = True
    return False


def restart_script():
    set_ui_progress(25, "재시작 중", pos=(20, 10), font_size=15)

    def restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=restart, daemon=True).start()


def lock_memory_procedure():
    global need_update
    set_ui_progress(80, "메모리 잠금 중", pos=(3, 10), font_size=15)

    openocd_command = [
        "sudo",
        "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "stm32f1x lock 0",
        "-c", "reset run",
        "-c", "shutdown",
    ]
    try:
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            GPIO.output(LED_SUCCESS, True)
            set_ui_progress(100, "메모리 잠금\n    성공", pos=(20, 0), font_size=15)
            time.sleep(1)
            GPIO.output(LED_SUCCESS, False)
        else:
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            set_ui_progress(0, "메모리 잠금\n    실패", pos=(20, 0), font_size=15)
            time.sleep(1)
    except Exception:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_progress(0, "오류 발생", pos=(20, 10), font_size=15)
        time.sleep(1)
    finally:
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        need_update = True


# ----------------------------
# Wi-Fi: setup / cancel restore  (NetworkManager 기준으로 통일)
# ----------------------------
def request_wifi_setup():
    global wifi_action_requested
    with wifi_action_lock:
        wifi_action_requested = True


def prepare_for_ap_mode():
    """
    AP(포탈) 모드 들어가기 전에:
    - 현재 STA에서 붙어있던 NM 프로파일 이름 기억
    - NM이 wlan0 잡고 있으면 간섭하므로 wlan0 unmanaged + disconnect
    """
    global last_good_wifi_profile
    try:
        prof = nm_get_active_wifi_profile()
        if prof:
            last_good_wifi_profile = prof
    except Exception:
        pass

    # NM이 살아있으면 간섭 방지
    if nm_is_active():
        nm_disconnect_wlan0()
        nm_set_managed(False)
        time.sleep(0.3)


def restore_after_ap_mode(timeout=25):
    """
    포탈 취소/실패/종료 후 STA로 복귀:
    - /tmp conf로 떠 있는 hostapd/dnsmasq 프로세스까지 확실히 kill
    - wlan0 초기화
    - NM managed 복구 + 재시작
    - 마지막 프로파일(있으면) 우선으로 up, 아니면 NM 자동연결
    """
    global last_good_wifi_profile

    # 1) 포탈이 띄운 AP 프로세스 먼저 정리(가장 중요)
    kill_portal_tmp_procs()
    run_quiet(["sudo", "systemctl", "stop", "hostapd"], timeout=3.0)
    run_quiet(["sudo", "systemctl", "stop", "dnsmasq"], timeout=3.0)

    # 2) wlan0 초기화
    wlan0_soft_reset()

    # 3) NM 복구
    nm_set_managed(True)
    nm_restart()
    time.sleep(1.2)

    # 4) 마지막으로 붙었던 프로파일 우선
    if last_good_wifi_profile:
        run_quiet(["sudo", "nmcli", "connection", "up", last_good_wifi_profile], timeout=12.0)

    # 5) 최종: 자동연결/인터넷 확인
    t0 = time.time()
    while time.time() - t0 < timeout:
        if has_real_internet():
            return True
        time.sleep(0.7)

    return has_real_internet()


def connect_from_portal_nm(ssid: str, psk: str, timeout=35):
    """
    포탈에서 SSID/PSK 제출되면:
    - AP 완전 종료
    - NM managed 복구 + 재시작
    - nmcli로 SSID/PSK 직접 연결(=NM 프로파일로 저장)
    """
    # 1) AP 프로세스 정리
    try:
        if hasattr(wifi_portal, "stop_ap"):
            wifi_portal.stop_ap()
    except Exception:
        pass

    kill_portal_tmp_procs()
    run_quiet(["sudo", "systemctl", "stop", "hostapd"], timeout=3.0)
    run_quiet(["sudo", "systemctl", "stop", "dnsmasq"], timeout=3.0)

    # 2) wlan0 초기화
    wlan0_soft_reset()

    # 3) NM 복구
    nm_set_managed(True)
    nm_restart()
    time.sleep(1.5)

    # 4) 연결
    ok = nm_connect(ssid, psk, timeout=timeout)
    if not ok:
        return False

    # 5) 인터넷 확인(조금 기다림)
    return nm_autoconnect(timeout=20)


def _portal_loop_until_connected_or_cancel():
    """
    반환값:
      True      = 새 연결 성공
      False     = 실패/타임아웃
      "cancel"  = 사용자 취소(NEXT long)
    """
    global wifi_cancel_requested

    # AP 모드 준비 (NM 간섭 제거 + last profile 기억)
    prepare_for_ap_mode()

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

        # 사용자가 포탈에서 SSID/PSK 제출했을 때만 처리
        req = getattr(wifi_portal, "_state", {}).get("requested")
        if req:
            ssid = req.get("ssid", "").strip()
            psk = req.get("psk", "").strip()
            wifi_portal._state["requested"] = None

            # ★ NM 기준으로 실제 연결 수행(성공해야만 True)
            ok = False
            if ssid and psk:
                ok = connect_from_portal_nm(ssid, psk, timeout=35)

            if ok:
                return True

            # 실패면 다시 AP 띄움
            prepare_for_ap_mode()
            wifi_portal.start_ap()

        if time.time() - t0 > 600:
            return False

        time.sleep(0.2)


def wifi_worker_thread():
    global wifi_action_requested, wifi_action_running
    global status_message, message_position, message_font_size, need_update, wifi_cancel_requested

    while not stop_threads:
        do = False
        with wifi_action_lock:
            if wifi_action_requested and (not wifi_action_running):
                wifi_action_requested = False
                wifi_action_running = True
                do = True

        if do:
            try:
                wifi_cancel_requested = False

                r1 = subprocess.run(["which", "hostapd"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                r2 = subprocess.run(["which", "dnsmasq"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if (r1.returncode != 0) or (r2.returncode != 0):
                    status_message = "AP 구성 불가"
                    message_position = (12, 10)
                    message_font_size = 15
                    need_update = True
                    time.sleep(2.0)
                else:
                    status_message = ""
                    need_update = True

                    result = _portal_loop_until_connected_or_cancel()

                    # 메뉴는 "실제 인터넷(ping)" 기준으로 재구성
                    refresh_root_menu(reset_index=True)
                    need_update = True

                    if result == "cancel":
                        set_ui_text("WiFi 설정 취소", "재연결 중...", pos=(0, 10), font_size=13)
                        ok_restore = restore_after_ap_mode(timeout=25)
                        set_ui_text("재연결 완료" if ok_restore else "재연결 실패", "", pos=(15, 18), font_size=15)
                        time.sleep(1.4)
                        clear_ui_override()

                    elif result is True:
                        set_ui_text("WiFi 연결 완료", "", pos=(12, 18), font_size=15)
                        time.sleep(1.5)
                        clear_ui_override()
                    else:
                        set_ui_text("WiFi 연결 실패", "", pos=(12, 18), font_size=15)
                        time.sleep(1.5)
                        clear_ui_override()

                status_message = ""
                need_update = True

            finally:
                with wifi_action_lock:
                    wifi_action_running = False

        time.sleep(0.05)


# ----------------------------
# Command execution
# ----------------------------
def execute_command(command_index):
    global is_executing, is_command_executing
    global current_menu, commands, command_names, command_types, menu_extras
    global current_command_index, menu_stack, need_update
    global connection_success, connection_failed_since_last_success

    item_type = command_types[command_index]
    if item_type == "wifi":
        request_wifi_setup()
        need_update = True
        return

    is_executing = True
    is_command_executing = True

    if not commands:
        is_executing = False
        is_command_executing = False
        return

    if item_type == "dir":
        subdir = menu_extras[command_index]
        if subdir and os.path.isdir(subdir):
            menu_stack.append((current_menu, current_command_index))
            current_menu = build_menu_for_dir(subdir, is_root=False)
            commands = current_menu["commands"]
            command_names = current_menu["names"]
            command_types = current_menu["types"]
            menu_extras = current_menu["extras"]
            current_command_index = 0
            need_update = True
        is_executing = False
        is_command_executing = False
        return

    if item_type == "back":
        if menu_stack:
            prev_menu, prev_index = menu_stack.pop()
            current_menu = prev_menu
            commands = current_menu["commands"]
            command_names = current_menu["names"]
            command_types = current_menu["types"]
            menu_extras = current_menu["extras"]
            current_command_index = prev_index if (0 <= prev_index < len(commands)) else 0
            need_update = True
        is_executing = False
        is_command_executing = False
        return

    if item_type == "system":
        kill_openocd()
        with stm32_state_lock:
            connection_success = False
            connection_failed_since_last_success = False
        git_pull()
        refresh_root_menu(reset_index=True)
        need_update = True
        is_executing = False
        is_command_executing = False
        return

    if item_type == "script":
        kill_openocd()
        with stm32_state_lock:
            connection_success = False
            connection_failed_since_last_success = False

        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

        if not os.path.isfile(OUT_SCRIPT_PATH):
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            set_ui_text("out.py 없음", "", pos=(15, 18), font_size=15)
            time.sleep(1.5)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
            clear_ui_override()
            need_update = True
            is_executing = False
            is_command_executing = False
            return

        set_ui_progress(10, "추출/업로드\n 실행 중...", pos=(10, 5), font_size=15)

        try:
            result = subprocess.run(
                commands[command_index],
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            if result.returncode == 0:
                GPIO.output(LED_SUCCESS, True)
                set_ui_progress(100, "완료!", pos=(35, 10), font_size=15)
                time.sleep(1)
                GPIO.output(LED_SUCCESS, False)
            else:
                GPIO.output(LED_ERROR, True)
                GPIO.output(LED_ERROR1, True)
                set_ui_progress(0, "실패!", pos=(35, 10), font_size=15)
                time.sleep(1.2)
                GPIO.output(LED_ERROR, False)
                GPIO.output(LED_ERROR1, False)

        except Exception:
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            set_ui_progress(0, "오류 발생", pos=(25, 10), font_size=15)
            time.sleep(1.2)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)

        clear_ui_override()
        refresh_root_menu(reset_index=True)
        need_update = True
        is_executing = False
        is_command_executing = False
        return

    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)

    if not unlock_memory():
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("메모리 잠금", "해제 실패", pos=(20, 12), font_size=15)
        time.sleep(2)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        is_executing = False
        is_command_executing = False
        need_update = True
        return

    # ---- 업데이트 진행바는 "override 화면"으로 계속 유지됨 (깨짐 방지)
    set_ui_progress(30, "업데이트 중...", pos=(12, 10), font_size=15)
    process = subprocess.Popen(commands[command_index], shell=True)

    start_time = time.time()
    max_duration = 6
    progress_increment = 20 / max_duration

    while process.poll() is None:
        elapsed = time.time() - start_time
        current_progress = 30 + (elapsed * progress_increment)
        current_progress = min(current_progress, 80)
        set_ui_progress(current_progress, "업데이트 중...", pos=(12, 10), font_size=15)
        time.sleep(0.2)

    result = process.returncode
    if result == 0:
        set_ui_progress(80, "업데이트 성공!", pos=(7, 10), font_size=15)
        time.sleep(1.0)
        lock_memory_procedure()
    else:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_progress(0, "업데이트 실패", pos=(7, 10), font_size=15)
        time.sleep(1)

    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)

    clear_ui_override()
    need_update = True
    is_executing = False
    is_command_executing = False


# ----------------------------
# Network info for UI
# ----------------------------
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


def get_wifi_level():
    try:
        # 연결 안된 상태면 0으로
        rc, out, _ = run_capture(["iw", "dev", "wlan0", "link"], timeout=0.6)
        if rc != 0 or "Not connected" in out:
            return 0

        r = subprocess.run(["iwconfig", "wlan0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=0.6)
        m = re.search(r"Signal level=(-?\d+)\s*dBm", r.stdout)
        if not m:
            return 0
        dbm = int(m.group(1))
        if dbm >= -55:
            return 4
        if dbm >= -65:
            return 3
        if dbm >= -75:
            return 2
        if dbm >= -85:
            return 1
        return 0
    except Exception:
        return 0


def net_poll_thread():
    global cached_ip, cached_wifi_level
    while not stop_threads:
        cached_ip = get_ip_address()
        # 인터넷 기준도 ping 기준으로(메뉴 노출과 동일)
        cached_wifi_level = get_wifi_level() if has_real_internet() else 0
        time.sleep(1.5)


# ----------------------------
# OLED render
# ----------------------------
def _draw_override(draw):
    with ui_override_lock:
        active = ui_override["active"]
        kind = ui_override["kind"]
        percent = ui_override["percent"]
        msg = ui_override["message"]
        pos = ui_override["pos"]
        fs = ui_override["font_size"]
        line2 = ui_override["line2"]

    if not active:
        return False

    # 네모 테두리 전부 제거: outline 사용 안함
    draw.rectangle(device.bounding_box, fill="black")

    if kind == "progress":
        draw.text(pos, msg, font=get_font(fs), fill=255)

        # progress bar (테두리 없이)
        x1, y1, x2, y2 = 10, 50, 110, 60
        draw.rectangle([(x1, y1), (x2, y2)], fill=0)  # background
        fill_w = int((x2 - x1) * (percent / 100.0))
        fill_w = int(max(0, min((x2 - x1), fill_w)))
        if fill_w > 0:
            draw.rectangle([(x1, y1), (x1 + fill_w, y2)], fill=255)
        return True

    if kind == "text":
        draw.text(pos, msg, font=get_font(fs), fill=255)
        if line2:
            draw.text((pos[0], pos[1] + 18), line2, font=get_font(fs), fill=255)
        return True

    return False


def update_oled_display():
    global current_command_index, status_message, message_position, message_font_size

    if not display_lock.acquire(timeout=0.2):
        return

    try:
        if not commands:
            return

        with wifi_action_lock:
            wifi_running = wifi_action_running

        now = datetime.now()
        current_time = now.strftime("%H시 %M분")
        voltage_percentage = battery_percentage
        ip_address = cached_ip
        wifi_level = cached_wifi_level

        try:
            with canvas(device) as draw:
                if _draw_override(draw):
                    return

                item_type = command_types[current_command_index]
                title = command_names[current_command_index]

                if item_type != "system":
                    battery_icon = select_battery_icon(voltage_percentage if voltage_percentage >= 0 else 0)
                    draw.bitmap((90, -9), battery_icon, fill=255)
                    perc_text = f"{voltage_percentage:.0f}%" if (voltage_percentage is not None and voltage_percentage >= 0) else "--%"
                    draw.text((99, 3), perc_text, font=font_st, fill=255)
                    draw.text((2, 1), current_time, font=font_time, fill=255)
                    draw_wifi_bars(draw, 70, 0, wifi_level)
                else:
                    ip_display = "연결 없음" if ip_address == "0.0.0.0" else ip_address
                    draw.text((0, 51), ip_display, font=font_big, fill=255)
                    draw.text((80, -3), "GDSENG", font=font_big, fill=255)
                    draw.text((83, 50), "ver 3.71", font=font_big, fill=255)
                    draw.text((0, -3), current_time, font=font_time, fill=255)
                    if not has_real_internet():
                        draw.text((0, 38), "WiFi(옵션)", font=font_big, fill=255)

                # status_message overlay (테두리 제거)
                if status_message:
                    draw.rectangle(device.bounding_box, fill="black")
                    draw.text(message_position, status_message, font=get_font(message_font_size), fill=255)
                    return

                # WiFi setup screen (테두리 제거)
                if wifi_running:
                    draw.rectangle(device.bounding_box, fill="black")
                    draw.text((0, 0), "WiFi 설정 모드", font=get_font(13), fill=255)

                    body_font = get_font(11)
                    y0 = 14
                    line = 12

                    draw.text((0, y0 + line * 0), "AP: GDSENG-SETUP",  font=body_font, fill=255)
                    draw.text((0, y0 + line * 1), "PW: 12345678",      font=body_font, fill=255)
                    draw.text((0, y0 + line * 2), "192.168.4.1:8080",  font=body_font, fill=255)
                    draw.text((0, 52), "NEXT 길게: 취소", font=body_font, fill=255)
                    return

                # Normal menu title
                center_x = device.width // 2 + VISUAL_X_OFFSET
                if item_type == "system":
                    center_y = 33
                    start_size = 17
                else:
                    center_y = 42
                    start_size = 21

                max_w = device.width - 4
                draw_center_text_autofit(draw, title, center_x, center_y, max_w, start_size, min_size=11)

        except Exception:
            return

    finally:
        display_lock.release()


last_oled_update_time = 0.0


def realtime_update_display():
    global need_update, last_oled_update_time
    while not stop_threads:
        now = time.time()
        if need_update or (now - last_oled_update_time >= 0.2):
            update_oled_display()
            last_oled_update_time = now
            need_update = False
        time.sleep(0.03)


# ----------------------------
# Power
# ----------------------------
def shutdown_system():
    set_ui_text("배터리 부족", "시스템 종료 중...", pos=(10, 18), font_size=15)
    time.sleep(2)
    try:
        os.system("sudo shutdown -h now")
    except Exception:
        pass


# ----------------------------
# Start threads
# ----------------------------
init_ina219()

battery_thread = threading.Thread(target=battery_monitor_thread, daemon=True)
battery_thread.start()

realtime_update_thread = threading.Thread(target=realtime_update_display, daemon=True)
realtime_update_thread.start()

stm32_thread = threading.Thread(target=stm32_poll_thread, daemon=True)
stm32_thread.start()

wifi_thread = threading.Thread(target=wifi_worker_thread, daemon=True)
wifi_thread.start()

net_thread = threading.Thread(target=net_poll_thread, daemon=True)
net_thread.start()

need_update = True


# ----------------------------
# Main loop
# ----------------------------
try:
    while True:
        now = time.time()

        if battery_percentage == 0:
            shutdown_system()

        # wifi 모드에서 NEXT long => cancel
        with wifi_action_lock:
            wifi_running = wifi_action_running
        if wifi_running and next_is_down and (not next_long_handled) and (next_press_time is not None):
            if now - next_press_time >= NEXT_LONG_CANCEL_THRESHOLD:
                next_long_handled = True
                wifi_cancel_requested = True
                need_update = True

        # EXECUTE long press => execute current
        if execute_is_down and (not execute_long_handled) and (execute_press_time is not None):
            if now - execute_press_time >= LONG_PRESS_THRESHOLD:
                execute_long_handled = True
                if commands and (not is_executing):
                    item_type = command_types[current_command_index]
                    if item_type in ("system", "dir", "back", "script", "wifi", "bin"):
                        execute_command(current_command_index)
                        need_update = True

        if execute_is_down and GPIO.input(BUTTON_PIN_EXECUTE) == GPIO.HIGH:
            execute_is_down = False

            if abs(last_time_button_next_pressed - last_time_button_execute_pressed) < button_press_interval:
                next_pressed_event = False
            else:
                if not execute_long_handled:
                    if commands and (not is_executing):
                        current_command_index = (current_command_index - 1) % len(commands)
                        need_update = True

            execute_press_time = None
            execute_long_handled = False

        # NEXT short press => next menu
        if next_pressed_event:
            if (not execute_is_down) and (not is_executing):
                if commands:
                    current_command_index = (current_command_index + 1) % len(commands)
                    need_update = True
            next_pressed_event = False

        # auto flash when bin selected + stm32 connected
        with stm32_state_lock:
            cs = connection_success

        if commands:
            if (
                command_types[current_command_index] == "bin"
                and (not is_executing)
                and cs
                and (not auto_flash_done_connection)
            ):
                execute_command(current_command_index)
                auto_flash_done_connection = True

        # 온라인/오프라인 메뉴 갱신(너무 자주 하면 깜빡임이 생길 수 있어 2초에 한 번만)
        # 필요하면 주석 해제해서 "연결되면 바로 시스템 업데이트 메뉴가 나타나는" UX로 만들 수 있음
        # if int(now) % 2 == 0:
        #     refresh_root_menu(reset_index=False)

        time.sleep(0.03)

except KeyboardInterrupt:
    pass
finally:
    stop_threads = True
    try:
        kill_openocd()
    except Exception:
        pass
    GPIO.cleanup()
