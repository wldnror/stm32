from datetime import datetime
import RPi.GPIO as GPIO
import time
import os
import sys
import socket
import shutil
from PIL import Image, ImageFont
from luma.core.interface.serial import i2c
from luma.oled.device import sh1107
from luma.core.render import canvas
import subprocess
from ina219 import INA219
import threading
import re
import wifi_portal
from typing import Optional, Tuple
from pymodbus.client import ModbusTcpClient
from pymodbus.pdu import ExceptionResponse

VISUAL_X_OFFSET = 0
display_lock = threading.Lock()
stm32_state_lock = threading.Lock()

wifi_action_lock = threading.Lock()
wifi_action_requested = False
wifi_action_running = False

ui_override_lock = threading.Lock()
ui_override = {
    "active": False,
    "kind": "none",
    "percent": 0,
    "message": "",
    "pos": (0, 0),
    "font_size": 15,
    "line2": "",
}

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

AP_SSID = getattr(wifi_portal, "AP_SSID", "GDSENG-SETUP")
AP_PASS = getattr(wifi_portal, "AP_PASS", "12345678")
AP_IP = getattr(wifi_portal, "AP_IP", "192.168.4.1")
PORTAL_PORT = 8080

def logi(msg: str):
    try:
        print("[AUTOSEL] " + str(msg), flush=True)
    except Exception:
        pass

def _portal_set_state_safe(**kwargs):
    try:
        if hasattr(wifi_portal, "_set_state"):
            wifi_portal._set_state(**kwargs)
            return
    except Exception:
        pass
    try:
        st = getattr(wifi_portal, "_state", None)
        if isinstance(st, dict):
            st.update(kwargs)
    except Exception:
        pass
    try:
        if hasattr(wifi_portal, "_write_state_file"):
            wifi_portal._write_state_file()
    except Exception:
        pass

def _portal_pop_req_safe():
    try:
        if hasattr(wifi_portal, "_pop_req_file"):
            return wifi_portal._pop_req_file()
    except Exception:
        pass
    return None

def _portal_clear_req_safe():
    try:
        st = getattr(wifi_portal, "_state", None)
        if isinstance(st, dict):
            st["requested"] = None
    except Exception:
        pass
    _portal_set_state_safe(requested=None)

def wifi_stage_set(percent, line1, line2=""):
    with wifi_stage_lock:
        wifi_stage["active"] = True
        wifi_stage["target_percent"] = int(max(0, min(100, percent)))
        if wifi_stage["display_percent"] > wifi_stage["target_percent"]:
            wifi_stage["display_percent"] = wifi_stage["target_percent"]
        wifi_stage["line1"] = line1 or ""
        wifi_stage["line2"] = line2 or ""
    _portal_set_state_safe(connect_stage=(line1 or ""))

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

last_time_button_next_pressed = 0.0
last_time_button_execute_pressed = 0.0
SOFT_DEBOUNCE_NEXT = 0.08
SOFT_DEBOUNCE_EXEC = 0.08

button_press_interval = 0.15
LONG_PRESS_THRESHOLD = 0.7
NEXT_LONG_CANCEL_THRESHOLD = 0.7

need_update = False
is_command_executing = False

execute_press_time = None
execute_is_down = False
execute_long_handled = False

next_press_time = None
next_is_down = False
next_long_handled = False
next_pressed_event = False

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

wifi_cancel_requested = False

cached_ip = "0.0.0.0"
cached_wifi_level = 0

last_good_wifi_profile = None

cached_online = False
last_menu_online = None

git_state_lock = threading.Lock()
git_has_update_cached = False
git_last_check = 0.0
git_check_interval = 5.0
GIT_REPO_DIR = "/home/user/stm32"

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

def _iface_exists(name: str) -> bool:
    try:
        return os.path.isdir(f"/sys/class/net/{name}")
    except Exception:
        return False

def has_real_internet(timeout=1.5):
    try:
        targets = ["8.8.8.8", "1.1.1.1"]
        iface = "wlan0" if _iface_exists("wlan0") else ("eth0" if _iface_exists("eth0") else None)
        for t in targets:
            if iface:
                cmd = ["ping", "-I", iface, "-c", "1", "-W", "1", t]
            else:
                cmd = ["ping", "-c", "1", "-W", "1", t]
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
            if r.returncode == 0:
                return True
        return False
    except Exception:
        return False

def _git_head_hash():
    rc, out, _ = run_capture(["git", "-C", GIT_REPO_DIR, "rev-parse", "HEAD"], timeout=1.2)
    if rc != 0:
        return None
    v = (out or "").strip()
    return v if v else None

def _git_branch_name():
    rc, out, _ = run_capture(["git", "-C", GIT_REPO_DIR, "rev-parse", "--abbrev-ref", "HEAD"], timeout=1.2)
    if rc != 0:
        return None
    v = (out or "").strip()
    if not v or v == "HEAD":
        return None
    return v

def _git_has_origin():
    rc, out, _ = run_capture(["git", "-C", GIT_REPO_DIR, "remote"], timeout=1.2)
    if rc != 0:
        return False
    remotes = [x.strip() for x in (out or "").splitlines() if x.strip()]
    return "origin" in remotes

def _git_upstream_hash():
    rc, out, _ = run_capture(["git", "-C", GIT_REPO_DIR, "rev-parse", "@{u}"], timeout=1.2)
    if rc != 0:
        return None
    v = (out or "").strip()
    return v if v else None

def git_has_remote_updates_light(timeout=2.2) -> bool:
    if not os.path.isdir(GIT_REPO_DIR):
        return False
    if not _git_has_origin():
        return False
    b = _git_branch_name()
    lh = _git_head_hash()
    if not b or not lh:
        return False
    rc, out, _ = run_capture(["git", "-C", GIT_REPO_DIR, "ls-remote", "origin", f"refs/heads/{b}"], timeout=timeout)
    if rc == 0:
        line = (out or "").strip().splitlines()
        if line:
            rh = (line[0].split() or [""])[0].strip()
            if rh:
                return rh != lh
    run_quiet(["git", "-C", GIT_REPO_DIR, "remote", "update"], timeout=3.8)
    uh = _git_upstream_hash()
    if not uh:
        return False
    return uh != lh

def git_poll_thread():
    global git_has_update_cached, git_last_check, need_update
    prev = None
    while not stop_threads:
        try:
            if not cached_online:
                with git_state_lock:
                    git_has_update_cached = False
                time.sleep(0.6)
                continue
            now = time.time()
            if now - git_last_check < git_check_interval:
                time.sleep(0.15)
                continue
            git_last_check = now
            with wifi_action_lock:
                wifi_running = wifi_action_running
            if wifi_running or is_executing or is_command_executing:
                time.sleep(0.2)
                continue
            ok = False
            try:
                ok = git_has_remote_updates_light(timeout=2.2)
            except Exception:
                ok = False
            with git_state_lock:
                git_has_update_cached = bool(ok)
            if prev is None or prev != ok:
                prev = ok
                refresh_root_menu(reset_index=False)
                need_update = True
            time.sleep(0.15)
        except Exception:
            time.sleep(0.5)

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
    if psk:
        cmd = ["sudo", "nmcli", "--wait", str(int(timeout)), "dev", "wifi", "connect", ssid, "password", psk, "ifname", "wlan0"]
    else:
        cmd = ["sudo", "nmcli", "--wait", str(int(timeout)), "dev", "wifi", "connect", ssid, "ifname", "wlan0"]
    rc, _, _ = run_capture(cmd, timeout=timeout + 5)
    if rc == 0:
        return True
    run_quiet(["sudo", "nmcli", "dev", "wifi", "rescan", "ifname", "wlan0"], timeout=6.0)
    rc2, _, _ = run_capture(cmd, timeout=timeout + 5)
    return rc2 == 0

def nm_up_profile(nm_id: str, timeout=20) -> bool:
    if not nm_id:
        return False
    rc, _, _ = run_capture(["sudo", "nmcli", "--wait", str(int(timeout)), "connection", "up", "id", nm_id], timeout=timeout + 2)
    return rc == 0

def wpa_select_saved_ssid(ssid: str) -> bool:
    if not ssid:
        return False
    rc, out, _ = run_capture(["sudo", "wpa_cli", "-i", "wlan0", "list_networks"], timeout=4.0)
    if rc != 0:
        return False
    net_id = None
    for line in (out or "").splitlines():
        line = line.strip()
        if not line or line.startswith("network id"):
            continue
        parts = re.split(r"\t+", line)
        if len(parts) >= 2 and parts[1] == ssid:
            net_id = parts[0]
            break
    if net_id is None:
        return False
    run_quiet(["sudo", "wpa_cli", "-i", "wlan0", "select_network", net_id], timeout=3.0)
    run_quiet(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", net_id], timeout=3.0)
    run_quiet(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], timeout=4.0)
    run_quiet(["sudo", "dhclient", "-r", "wlan0"], timeout=6.0)
    run_quiet(["sudo", "dhclient", "wlan0"], timeout=10.0)
    return True

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

def button_next_edge(channel):
    global last_time_button_next_pressed
    global next_press_time, next_is_down, next_long_handled, next_pressed_event
    now = time.time()
    if (now - last_time_button_next_pressed) < SOFT_DEBOUNCE_NEXT:
        return
    last_time_button_next_pressed = now
    if GPIO.input(BUTTON_PIN_NEXT) == GPIO.LOW:
        next_press_time = now
        next_is_down = True
        next_long_handled = False
    else:
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
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.2)
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

serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

font_path = "/usr/share/fonts/truetype/malgun/malgunbd.ttf"
font_big = ImageFont.truetype(font_path, 12)
font_st = ImageFont.truetype(font_path, 11)
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

def draw_wifi_bars(draw, x, y, level):
    bar_w = 3
    gap = 2
    base_h = 3
    max_h = base_h + 3 * 3
    for i in range(4):
        h = base_h + i * 3
        xx = x + i * (bar_w + gap)
        yy = y + (max_h - h)
        if level >= (i + 1):
            draw.rectangle([xx, yy, xx + bar_w, y + max_h], fill=255)
        else:
            draw.rectangle([xx, y + max_h - 1, xx + bar_w, y + max_h], fill=255)

FIRMWARE_DIR = "/home/user/stm32/Program"
OUT_SCRIPT_PATH = "/home/user/stm32/out.py"

GENERAL_DIRNAME = "1.일반"
TFTP_DIRNAME = "2.TFTP"
GENERAL_ROOT = os.path.join(FIRMWARE_DIR, GENERAL_DIRNAME)
TFTP_ROOT = os.path.join(FIRMWARE_DIR, TFTP_DIRNAME)
FLASH_KB_THRESHOLD = 300

TFTP_REMOTE_DIR = "/home/user/stm32/Program/2.TFTP_REMOTE"
TFTP_SERVER_ROOT = "/srv/tftp"
TFTP_DEVICE_SUBDIR = os.path.join("GDS", "ASGD-3200")
TFTP_DEVICE_FILENAME = "asgd3200.bin"
MODBUS_PORT = 502

_detect_cache_lock = threading.Lock()
_detect_cache = {"ts": 0.0, "flash_kb": None, "dev_id": None}

def make_openocd_program_cmd(bin_path: str) -> str:
    return (
        "sudo openocd "
        "-f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
        "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
        f"-c \"program {bin_path} verify reset exit 0x08000000\""
    )

def _parse_openocd_flash_kb(text: str) -> Optional[int]:
    m = re.search(r"flash size\s*=\s*(\d+)\s*KiB", text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    m2 = re.search(r"flash size\s*=\s*(\d+)\s*kB", text, re.IGNORECASE)
    if m2:
        try:
            return int(m2.group(1))
        except Exception:
            return None
    return None

def _detect_flash_kb_by_probe(timeout=3.5) -> Optional[int]:
    cmd = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "flash probe 0",
        "-c", "shutdown",
    ]
    rc, out, err = run_capture(cmd, timeout=timeout)
    txt = (out or "") + "\n" + (err or "")
    kb = _parse_openocd_flash_kb(txt)
    return kb

def detect_stm32_flash_kb_with_unlock(timeout=4.0) -> Tuple[Optional[int], Optional[int]]:
    now = time.time()
    with _detect_cache_lock:
        if (_detect_cache["flash_kb"] is not None) and (now - _detect_cache["ts"] < 1.5):
            return _detect_cache["dev_id"], _detect_cache["flash_kb"]
    cmd = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "stm32f1x unlock 0",
        "-c", "reset halt",
        "-c", "mdw 0xE0042000 1",
        "-c", "mdh 0x1FFFF7E0 1",
        "-c", "shutdown",
    ]
    rc, out, err = run_capture(cmd, timeout=timeout)
    text = (out or "") + "\n" + (err or "")
    m_id = re.search(r"0xE0042000:\s+(0x[0-9a-fA-F]+)", text, re.IGNORECASE)
    m_fs = re.search(r"0x1FFFF7E0:\s+(0x[0-9a-fA-F]+)", text, re.IGNORECASE)
    if (rc != 0) or (not m_id) or (not m_fs):
        kb = _detect_flash_kb_by_probe(timeout=3.5)
        if kb is not None:
            with _detect_cache_lock:
                _detect_cache["ts"] = time.time()
                _detect_cache["flash_kb"] = kb
                _detect_cache["dev_id"] = None
            return None, kb
        return None, None
    try:
        id_val = int(m_id.group(1), 16)
        fs_val = int(m_fs.group(1), 16)
        dev_id = id_val % 4096
        flash_kb = fs_val
        with _detect_cache_lock:
            _detect_cache["ts"] = time.time()
            _detect_cache["flash_kb"] = flash_kb
            _detect_cache["dev_id"] = dev_id
        return dev_id, flash_kb
    except Exception:
        kb = _detect_flash_kb_by_probe(timeout=3.5)
        if kb is not None:
            with _detect_cache_lock:
                _detect_cache["ts"] = time.time()
                _detect_cache["flash_kb"] = kb
                _detect_cache["dev_id"] = None
            return None, kb
        return None, None

def _strip_order_prefix(name: str) -> str:
    s = (name or "").strip()
    m = re.match(r"^\d+\.(.*)$", s)
    if m:
        s = (m.group(1) or "").strip()
    return s

def _canon(name: str) -> str:
    return _strip_order_prefix(name).strip().lower()

def _is_ir_variant(selected_bin_path: str) -> bool:
    fn = os.path.basename(selected_bin_path).upper()
    return fn.startswith("IR_") or fn.startswith("IR")

def _key_from_filename(path_or_name: str) -> str:
    base = os.path.basename(path_or_name or "")
    m = re.match(r"^\s*\d+\.\s*([^.]+)\.bin\s*$", base, re.IGNORECASE)
    if m:
        return (m.group(1) or "").strip()
    stem = os.path.splitext(base)[0]
    m2 = re.match(r"^\s*\d+\.\s*(.+)\s*$", stem)
    if m2:
        return (m2.group(1) or "").strip()
    return _strip_order_prefix(stem).strip()

def _gas_key_from_selected_path(selected_bin_path: str) -> str:
    sp = os.path.abspath(selected_bin_path)
    return _key_from_filename(sp)

def resolve_target_bin_by_gas(selected_bin_path: str, flash_kb: Optional[int]) -> Tuple[str, str]:
    if flash_kb is None:
        return selected_bin_path, "원본"
    want_tftp = flash_kb > FLASH_KB_THRESHOLD
    base_root = TFTP_ROOT if want_tftp else GENERAL_ROOT
    chosen_kind = "TFTP" if want_tftp else "일반"
    sp = os.path.abspath(selected_bin_path)
    gas_key = _gas_key_from_selected_path(sp)
    is_ir = _is_ir_variant(sp)
    fname = os.path.basename(sp)
    m = re.match(r"^\d+\.(.*)$", fname)
    fname_no_order = (m.group(1) if m else fname)
    stem_base = _strip_order_prefix(os.path.splitext(fname)[0]).strip()
    parent = os.path.basename(os.path.dirname(sp))
    parent_stripped = _strip_order_prefix(parent).strip()
    candidates = []
    if want_tftp:
        if stem_base:
            candidates.append(os.path.join(base_root, f"{stem_base}.bin"))
        if is_ir:
            candidates += [
                os.path.join(base_root, f"IR_{gas_key}.bin"),
                os.path.join(base_root, f"IR{gas_key}.bin"),
                os.path.join(base_root, f"{gas_key}_IR.bin"),
                os.path.join(base_root, f"{gas_key}.bin"),
            ]
        candidates += [
            os.path.join(base_root, f"{gas_key}.bin"),
            os.path.join(base_root, fname_no_order),
            os.path.join(base_root, fname),
        ]
        if _canon(parent) not in (_canon(GENERAL_DIRNAME), _canon(TFTP_DIRNAME)):
            candidates += [
                os.path.join(base_root, parent, fname),
                os.path.join(base_root, parent, fname_no_order),
            ]
            if parent_stripped and parent_stripped != parent:
                candidates += [
                    os.path.join(base_root, parent_stripped, fname),
                    os.path.join(base_root, parent_stripped, fname_no_order),
                ]
    else:
        candidates += [
            os.path.join(base_root, parent, fname),
            os.path.join(base_root, parent, fname_no_order),
            os.path.join(base_root, parent_stripped, fname),
            os.path.join(base_root, parent_stripped, fname_no_order),
            os.path.join(base_root, fname),
            os.path.join(base_root, fname_no_order),
            sp,
        ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c, chosen_kind
    return selected_bin_path, "원본"

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

def _ip_from_ip_cmd(ifname: str) -> str:
    rc, out, _ = run_capture(["bash", "-lc", f"ip -4 addr show {ifname} | awk '/inet /{{print $2}}' | head -n1"], timeout=0.9)
    if rc != 0:
        return "0.0.0.0"
    v = (out or "").strip()
    if not v:
        return "0.0.0.0"
    ip = v.split("/")[0].strip()
    return ip if ip else "0.0.0.0"

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and ip != "0.0.0.0" and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        for ifn in ["wlan0", "eth0"]:
            if _iface_exists(ifn):
                ip2 = _ip_from_ip_cmd(ifn)
                if ip2 != "0.0.0.0" and not ip2.startswith("127."):
                    return ip2
        rc, out, _ = run_capture(["bash", "-lc", "ip -4 addr show | awk '/inet /{print $2}' | head -n1"], timeout=0.9)
        if rc == 0:
            v = (out or "").strip()
            if v:
                ip3 = v.split("/")[0].strip()
                if ip3 and ip3 != "0.0.0.0" and not ip3.startswith("127."):
                    return ip3
    except Exception:
        pass
    return "0.0.0.0"

def encode_ip_to_words(ip: str):
    a, b, c, d = map(int, ip.split("."))
    for x in (a, b, c, d):
        if x < 0 or x > 255:
            raise ValueError("bad ip")
    return ((a << 8) | b, (c << 8) | d)

def _quick_modbus_probe(ip: str, timeout=0.25) -> bool:
    try:
        s = socket.create_connection((ip, MODBUS_PORT), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False

def scan_modbus_devices_same_subnet(limit=48):
    my_ip = get_ip_address()
    if my_ip == "0.0.0.0":
        return []
    parts = my_ip.split(".")
    if len(parts) != 4:
        return []
    base = ".".join(parts[:3]) + "."
    candidates = []
    start = 2
    end = min(254, start + max(8, int(limit)))
    for host in range(start, end + 1):
        ip = base + str(host)
        if ip == my_ip:
            continue
        if _quick_modbus_probe(ip):
            candidates.append(ip)
    return candidates

def build_tftp_device_menu(ip_list):
    commands_local = []
    names_local = []
    types_local = []
    extras_local = []
    for ip in ip_list:
        commands_local.append("tftp_dev")
        names_local.append(f"▶ {ip}")
        types_local.append("tftp_dev")
        extras_local.append(ip)
    commands_local.append(None)
    names_local.append("◀ 이전으로")
    types_local.append("back")
    extras_local.append(None)
    return {
        "dir": "__tftp_devices__",
        "commands": commands_local,
        "names": names_local,
        "types": types_local,
        "extras": extras_local,
    }

def pick_remote_fw_file_for_device(ip: str) -> str:
    p = os.path.join(TFTP_REMOTE_DIR, "default.bin")
    if os.path.isfile(p):
        return p
    p2 = os.path.join(TFTP_REMOTE_DIR, "asgd3200.bin")
    if os.path.isfile(p2):
        return p2
    bins = []
    try:
        for fn in os.listdir(TFTP_REMOTE_DIR):
            if fn.lower().endswith(".bin"):
                bins.append(os.path.join(TFTP_REMOTE_DIR, fn))
    except Exception:
        pass
    return bins[0] if bins else ""

def tftp_upgrade_device(ip: str):
    set_ui_progress(5, "TFTP 업뎃\n준비...", pos=(18, 0), font_size=15)
    fw_src = pick_remote_fw_file_for_device(ip)
    if not fw_src:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("FW 없음", "2.TFTP_REMOTE", pos=(4, 18), font_size=13)
        time.sleep(1.6)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return
    set_ui_progress(20, "FW 복사중", pos=(18, 10), font_size=15)
    device_dir = os.path.join(TFTP_SERVER_ROOT, TFTP_DEVICE_SUBDIR)
    os.makedirs(device_dir, exist_ok=True)
    dst_path = os.path.join(device_dir, TFTP_DEVICE_FILENAME)
    try:
        try:
            if os.path.exists(dst_path):
                os.remove(dst_path)
        except Exception:
            pass
        shutil.copyfile(fw_src, dst_path)
    except Exception as e:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("복사 실패", str(e)[:16], pos=(2, 18), font_size=12)
        time.sleep(2.0)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return
    tftp_ip = get_ip_address()
    set_ui_progress(45, f"명령 전송\n{ip}", pos=(6, 0), font_size=13)
    client = ModbusTcpClient(ip, port=502, timeout=2)
    if not client.connect():
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("연결 실패", ip, pos=(2, 18), font_size=12)
        time.sleep(1.6)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return
    try:
        addr_ip1 = 40088 - 40001
        addr_ctrl = 40091 - 40001
        try:
            w1, w2 = encode_ip_to_words(tftp_ip)
            try:
                client.write_registers(addr_ip1, [w1, w2])
            except Exception:
                pass
        except Exception:
            pass
        r = client.write_register(addr_ctrl, 1)
        if isinstance(r, ExceptionResponse) or getattr(r, "isError", lambda: False)():
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            set_ui_text("명령 실패", "40091=1", pos=(10, 18), font_size=13)
            time.sleep(1.8)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
            clear_ui_override()
            return
        GPIO.output(LED_SUCCESS, True)
        set_ui_progress(100, "전송 완료\n업뎃 진행", pos=(10, 5), font_size=15)
        time.sleep(1.2)
        GPIO.output(LED_SUCCESS, False)
    finally:
        try:
            client.close()
        except Exception:
            pass
    clear_ui_override()

def build_menu_for_dir(dir_path, is_root=False):
    entries = []
    try:
        if is_root:
            gas_dirs = {}
            root_bins = {}
            for base_root in (TFTP_ROOT, GENERAL_ROOT):
                if not os.path.isdir(base_root):
                    continue
                for name in os.listdir(base_root):
                    full = os.path.join(base_root, name)
                    if os.path.isdir(full):
                        gas_dirs[name] = full
                        continue
                    if name.lower().endswith(".bin"):
                        root_bins[name] = full
            for dname in sorted(gas_dirs.keys()):
                order, display_name = parse_order_and_name(dname, is_dir=True)
                entries.append((order, 0, "▶ " + display_name, "dir", gas_dirs[dname]))
            for bname in sorted(root_bins.keys()):
                order, display_name = parse_order_and_name(bname, is_dir=False)
                entries.append((order, 1, display_name, "bin", root_bins[bname]))
        else:
            for fname in os.listdir(dir_path):
                full_path = os.path.join(dir_path, fname)
                if os.path.isdir(full_path):
                    order, display_name = parse_order_and_name(fname, is_dir=True)
                    entries.append((order, 0, "▶ " + display_name, "dir", full_path))
                elif fname.lower().endswith(".bin"):
                    order, display_name = parse_order_and_name(fname, is_dir=False)
                    entries.append((order, 1, display_name, "bin", full_path))
    except FileNotFoundError:
        entries = []
    entries.sort(key=lambda x: (x[0], x[1], x[2]))
    commands_local = []
    names_local = []
    types_local = []
    extras_local = []
    for order, type_pri, display_name, item_type, extra in entries:
        commands_local.append(None)
        names_local.append(display_name)
        types_local.append(item_type)
        extras_local.append(extra)
    if is_root:
        online = cached_online
        if online:
            commands_local.append(f"python3 {OUT_SCRIPT_PATH}")
            names_local.append("FW 추출(OUT)")
            types_local.append("script")
            extras_local.append(None)
            with git_state_lock:
                has_update = git_has_update_cached
            if has_update:
                commands_local.append("git_pull")
                names_local.append("시스템 업데이트")
                types_local.append("system")
                extras_local.append(None)
        commands_local.append("wifi_setup")
        names_local.append("Wi-Fi 설정")
        types_local.append("wifi")
        extras_local.append(None)
        commands_local.append("tftp_scan")
        names_local.append("원격(TFTP) 업데이트")
        types_local.append("tftp_scan")
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

def git_pull():
    global git_last_check
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
        global git_has_update_cached
        with git_state_lock:
            git_has_update_cached = False
        git_last_check = 0.0
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()

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

def request_wifi_setup():
    global wifi_action_requested
    with wifi_action_lock:
        wifi_action_requested = True

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
    _portal_set_state_safe(connect_stage="연결 준비 중…", last_error="", last_ok="")
    try:
        if hasattr(wifi_portal, "stop_ap"):
            wifi_portal.stop_ap()
    except Exception:
        pass
    kill_portal_tmp_procs()
    run_quiet(["sudo", "systemctl", "stop", "hostapd"], timeout=3.0)
    run_quiet(["sudo", "systemctl", "stop", "dnsmasq"], timeout=3.0)
    wifi_stage_set(30, "연결 준비", "인터페이스 초기화")
    _portal_set_state_safe(connect_stage="인터페이스 초기화 중…")
    wlan0_soft_reset()
    wifi_stage_set(50, "연결 준비", "NetworkManager")
    _portal_set_state_safe(connect_stage="NetworkManager 준비 중…")
    nm_set_managed(True)
    nm_restart()
    time.sleep(1.5)
    wifi_stage_set(70, "WiFi 연결 중", ssid[:18])
    _portal_set_state_safe(connect_stage="무선 연결 시도 중…")
    ok = nm_connect(ssid, psk, timeout=timeout)
    if not ok:
        _portal_set_state_safe(last_error="연결 실패", connect_stage="")
        wifi_stage_set(0, "연결 실패", "")
        time.sleep(0.8)
        wifi_stage_clear()
        return False
    wifi_stage_set(85, "인터넷 확인", "")
    _portal_set_state_safe(connect_stage="인터넷 확인 중…")
    ok2 = nm_autoconnect(timeout=20)
    if ok2:
        _portal_set_state_safe(last_ok="연결 완료", last_error="", connect_stage="연결 완료")
    else:
        _portal_set_state_safe(last_error="인터넷 확인 실패", connect_stage="")
    wifi_stage_set(100 if ok2 else 0, "완료" if ok2 else "실패", "")
    time.sleep(0.6)
    wifi_stage_clear()
    return ok2

def _portal_loop_until_connected_or_cancel():
    global wifi_cancel_requested
    prepare_for_ap_mode()
    wifi_stage_clear()
    _portal_set_state_safe(last_error="", last_ok="", connect_stage="설정 모드 시작", running=True, done=False)

    wifi_portal.start_ap()

    try:
        st = getattr(wifi_portal, "_state", {})
        if isinstance(st, dict) and (not st.get("server_started", False)):
            wifi_portal.run_portal(block=False)
            st["server_started"] = True
            _portal_set_state_safe(server_started=True)
    except Exception:
        try:
            wifi_portal.run_portal(block=False)
        except Exception:
            pass

    t0 = time.time()
    while True:
        if wifi_cancel_requested:
            _portal_set_state_safe(connect_stage="취소 처리 중…")
            try:
                if hasattr(wifi_portal, "stop_ap"):
                    wifi_portal.stop_ap()
            except Exception:
                pass
            return "cancel"

        req = None
        try:
            st = getattr(wifi_portal, "_state", {})
            if isinstance(st, dict):
                req = st.get("requested")
        except Exception:
            req = None

        if not req:
            req = _portal_pop_req_safe()
            if req and isinstance(getattr(wifi_portal, "_state", None), dict):
                try:
                    wifi_portal._state["requested"] = req
                except Exception:
                    pass

        if req:
            _portal_clear_req_safe()
            mode = (req.get("mode") or "new").strip().lower()
            src = (req.get("src") or "").strip().lower()
            ssid = (req.get("ssid") or "").strip()
            psk = (req.get("psk") or "").strip()
            nm_id = (req.get("nm_id") or "").strip()
            ok = False

            if mode == "saved":
                wifi_stage_set(60, "저장된 WiFi", "연결 시도")
                _portal_set_state_safe(connect_stage="저장된 설정으로 연결 중…", last_error="", last_ok="")
                if src == "nm" and nm_id:
                    wlan0_soft_reset()
                    nm_set_managed(True)
                    nm_restart()
                    time.sleep(1.0)
                    _portal_set_state_safe(connect_stage="NetworkManager 연결 시도 중…")
                    ok = nm_up_profile(nm_id, timeout=20) and nm_autoconnect(timeout=20)
                elif src == "wpa" and ssid:
                    wlan0_soft_reset()
                    _portal_set_state_safe(connect_stage="wpa_supplicant 연결 시도 중…")
                    ok = wpa_select_saved_ssid(ssid) and nm_autoconnect(timeout=20)
                else:
                    ok = False
            else:
                if ssid:
                    ok = connect_from_portal_nm(ssid, psk, timeout=35)

            if ok:
                _portal_set_state_safe(last_ok="연결 완료", last_error="", connect_stage="연결 완료", running=False, done=True)
                return True

            _portal_set_state_safe(last_error="연결 실패", connect_stage="", running=True, done=False)
            prepare_for_ap_mode()
            wifi_stage_clear()
            wifi_portal.start_ap()

        if time.time() - t0 > 600:
            _portal_set_state_safe(last_error="시간 초과", connect_stage="", running=False)
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
                wifi_stage_clear()
                with ap_state_lock:
                    ap_state["last_clients"] = 0
                    ap_state["flash_until"] = 0.0
                    ap_state["poll_next"] = 0.0
                    ap_state["spinner"] = 0

                _portal_set_state_safe(last_error="", last_ok="", connect_stage="설정 시작 준비…", running=True, done=False)

                r1 = subprocess.run(["which", "hostapd"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                r2 = subprocess.run(["which", "dnsmasq"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if (r1.returncode != 0) or (r2.returncode != 0):
                    status_message = "AP 구성 불가"
                    message_position = (12, 10)
                    message_font_size = 15
                    need_update = True
                    _portal_set_state_safe(last_error="hostapd/dnsmasq 없음", connect_stage="", running=False)
                    time.sleep(2.0)
                else:
                    status_message = ""
                    need_update = True
                    result = _portal_loop_until_connected_or_cancel()
                    refresh_root_menu(reset_index=True)
                    need_update = True
                    if result == "cancel":
                        wifi_stage_set(10, "취소 처리중", "재연결 준비")
                        _portal_set_state_safe(connect_stage="취소 처리 중…", last_error="취소됨", last_ok="")
                        ok_restore = restore_after_ap_mode(timeout=25)
                        set_ui_text("재연결 완료" if ok_restore else "재연결 실패", "", pos=(15, 18), font_size=15)
                        time.sleep(1.0)
                        clear_ui_override()
                        wifi_stage_clear()
                        _portal_set_state_safe(connect_stage="", running=False)
                    elif result is True:
                        set_ui_text("WiFi 연결 완료", "", pos=(12, 18), font_size=15)
                        time.sleep(1.1)
                        clear_ui_override()
                        wifi_stage_clear()
                        _portal_set_state_safe(last_ok="연결 완료", last_error="", connect_stage="연결 완료", running=False, done=True)
                    else:
                        set_ui_text("WiFi 연결 실패", "", pos=(12, 18), font_size=15)
                        time.sleep(1.1)
                        clear_ui_override()
                        wifi_stage_clear()
                        _portal_set_state_safe(last_error="연결 실패", connect_stage="", running=False)
                status_message = ""
                need_update = True
            finally:
                with wifi_action_lock:
                    wifi_action_running = False
        time.sleep(0.05)

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

    if item_type == "tftp_scan":
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        set_ui_progress(5, "주변 장치\n스캔 중...", pos=(10, 10), font_size=15)
        ips = scan_modbus_devices_same_subnet(limit=64)
        if not ips:
            GPIO.output(LED_ERROR, True)
            set_ui_text("장치 없음", "같은 대역", pos=(12, 18), font_size=15)
            time.sleep(1.3)
            GPIO.output(LED_ERROR, False)
            clear_ui_override()
            need_update = True
            is_executing = False
            is_command_executing = False
            return
        menu_stack.append((current_menu, current_command_index))
        current_menu = build_tftp_device_menu(ips)
        commands = current_menu["commands"]
        command_names = current_menu["names"]
        command_types = current_menu["types"]
        menu_extras = current_menu["extras"]
        current_command_index = 0
        clear_ui_override()
        need_update = True
        is_executing = False
        is_command_executing = False
        return

    if item_type == "tftp_dev":
        target_ip = menu_extras[command_index]
        tftp_upgrade_device(target_ip if target_ip else "")
        need_update = True
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

    selected_path = None
    try:
        selected_path = menu_extras[command_index]
    except Exception:
        selected_path = None

    dev_id, flash_kb = detect_stm32_flash_kb_with_unlock(timeout=4.0)

    if not selected_path:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("BIN 경로", "없음", pos=(20, 12), font_size=15)
        time.sleep(1.5)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        is_executing = False
        is_command_executing = False
        need_update = True
        return

    resolved_path, chosen_kind = resolve_target_bin_by_gas(selected_path, flash_kb)

    if not unlock_memory():
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("메모리 잠금", "해제
