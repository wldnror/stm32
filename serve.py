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

BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17
LED_SUCCESS = 24
LED_ERROR = 25
LED_ERROR1 = 23

SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1
MAX_VOLTAGE = 4.2

SOFT_DEBOUNCE_NEXT = 0.05
SOFT_DEBOUNCE_EXEC = 0.05

LONG_PRESS_THRESHOLD = 0.7
NEXT_LONG_CANCEL_THRESHOLD = 0.7

POST_FLASH_WAIT_SEC = 1.2

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

GIT_REPO_DIR = "/home/user/stm32"
MODBUS_PORT = 502

REG_STATUS_4XXXX = 40001
REG_GAS_FLOAT_HI = 40003
REG_GAS_FLOAT_LO = 40004
REG_GAS_INT_4XXXX = 40005
REG_FAULT_4XXXX = 40008

SCAN_PRUNE_SEC = 12.0
SCAN_HOSTS_PER_TICK = 4
SCAN_MENU_REBUILD_MIN_SEC = 0.6
SCAN_PREFIX_STABLE_CNT = 2

SCAN_DETAIL_POLL_SEC = 0.2


auto_flash_done_connection = False
need_update = False
is_command_executing = False
is_executing = False

execute_press_time = None
execute_is_down = False
execute_long_handled = False
execute_short_event = False

next_press_time = None
next_is_down = False
next_long_handled = False
next_pressed_event = False

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

ina_lock = threading.Lock()
ina_last = {"v": None, "c": None, "p": None, "ts": 0.0}
ina_poll_started = False

connection_success = False
connection_failed_since_last_success = False
last_stm32_check_time = 0.0

stop_threads = False
wifi_cancel_requested = False

cached_ip = "0.0.0.0"
cached_wifi_level = 0
cached_online = False
last_menu_online = None
last_good_wifi_profile = None

git_state_lock = threading.Lock()
git_has_update_cached = False
git_last_check = 0.0
git_check_interval = 5.0

scan_lock = threading.Lock()
scan_active = False
scan_done = False
scan_ips = []
scan_infos = {}
scan_selected_idx = 0
scan_selected_ip = None
scan_last_tick = 0.0
scan_base_prefix = None
scan_cursor = 2
scan_seen = {}
scan_menu_dirty = False
scan_menu_dirty_ts = 0.0
scan_menu_rebuild_last = 0.0
scan_prefix_candidate = None
scan_prefix_candidate_cnt = 0

scan_detail_lock = threading.Lock()
scan_detail_active = False
scan_detail_ip = None
scan_detail = {
    "gas": None,
    "flags": {"PWR": False, "A1": False, "A2": False, "FUT": False},
    "ts": 0.0,
    "err": "",
}

_detect_cache_lock = threading.Lock()
_detect_cache = {"ts": 0.0, "flash_kb": None, "dev_id": None}

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

last_time_button_next_pressed = 0.0
last_time_button_execute_pressed = 0.0


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


def ina_poll_loop(interval=0.35):
    global ina_last
    while not stop_threads:
        try:
            sensor = ina
            if sensor is None:
                time.sleep(0.6)
                continue
            with ina_lock:
                v = sensor.voltage()
                c = None
                p = None
                try:
                    c = sensor.current()
                except Exception:
                    c = None
                try:
                    p = sensor.power()
                except Exception:
                    p = None
            ina_last = {"v": v, "c": c, "p": p, "ts": time.time()}
        except Exception:
            pass
        time.sleep(interval)


def init_ina219():
    global ina, ina_poll_started
    try:
        ina = INA219(SHUNT_OHMS)
        ina.configure()
    except Exception:
        ina = None
    if not ina_poll_started:
        ina_poll_started = True
        threading.Thread(target=ina_poll_loop, daemon=True).start()


def _ina_get_voltage(max_age=2.0):
    d = ina_last
    ts = d.get("ts", 0.0) or 0.0
    if ts and (time.time() - ts) <= max_age:
        return d.get("v", None)
    return None


def read_ina219_percentage():
    try:
        v = _ina_get_voltage(max_age=2.5)
        if v is None:
            return -1
        voltage = float(v)
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


def button_execute_edge(channel):
    global last_time_button_execute_pressed
    global execute_press_time, execute_is_down, execute_long_handled, execute_short_event
    now = time.time()
    if (now - last_time_button_execute_pressed) < SOFT_DEBOUNCE_EXEC:
        return
    last_time_button_execute_pressed = now
    if GPIO.input(BUTTON_PIN_EXECUTE) == GPIO.LOW:
        execute_press_time = now
        execute_is_down = True
        execute_long_handled = False
    else:
        if execute_is_down and (not execute_long_handled) and (execute_press_time is not None):
            execute_short_event = True
        execute_is_down = False
        execute_press_time = None
        execute_long_handled = False


GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)

GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.BOTH, callback=button_next_edge, bouncetime=40)
GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.BOTH, callback=button_execute_edge, bouncetime=40)

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


def _text_size(draw, text, font):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return (bbox[2] - bbox[0], bbox[3] - bbox[1])
    except Exception:
        try:
            return draw.textsize(text, font=font)
        except Exception:
            return (len(text) * 6, 10)


def _ellipsis_to_width(draw, text, font, max_w):
    s = text or ""
    if max_w <= 0:
        return ""
    w, _ = _text_size(draw, s, font)
    if w <= max_w:
        return s
    ell = "…"
    w_ell, _ = _text_size(draw, ell, font)
    if w_ell > max_w:
        return ""
    lo, hi = 0, len(s)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = s[:mid] + ell
        w_c, _ = _text_size(draw, cand, font)
        if w_c <= max_w:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best if best else ell


def _wrap_lines(draw, text, font, max_w, max_lines=2):
    s = (text or "").strip()
    if not s:
        return []
    words = s.split()
    if not words:
        return [s[:max(1, min(len(s), 18))]]
    lines = []
    cur = ""
    for w in words:
        cand = (cur + " " + w).strip() if cur else w
        ww, _ = _text_size(draw, cand, font)
        if ww <= max_w:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) >= max_lines - 1:
                break
    if len(lines) < max_lines and cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if lines:
        last = lines[-1]
        if _text_size(draw, last, font)[0] > max_w:
            lines[-1] = _ellipsis_to_width(draw, last, font, max_w)
    return lines


def _right_text(draw, x_right, y, text, font, fill=255):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
    except Exception:
        try:
            w, _ = draw.textsize(text, font=font)
        except Exception:
            w = len(text) * 6
    xx = max(0, int(x_right - w))
    draw.text((xx, y), text, font=font, fill=fill)


def draw_center_text_autofit(draw, text, center_x, center_y, max_width, start_size, min_size=10):
    size = start_size
    while size >= min_size:
        f = get_font(size)
        w, _ = _text_size(draw, text, f)
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


def reg_addr(addr_4xxxx: int) -> int:
    return int(addr_4xxxx) - 40001


def encode_ip_to_words(ip: str):
    a, b, c, d = map(int, (ip or "").strip().split("."))
    for x in (a, b, c, d):
        if x < 0 or x > 255:
            raise ValueError("bad ip")
    return ((a << 8) | b, (c << 8) | d)


def _quick_modbus_probe(ip: str, timeout=0.22) -> bool:
    try:
        s = socket.create_connection((ip, MODBUS_PORT), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _modbus_connect_with_retries(ip: str, port=502, timeout=1.2, retries=3, delay=0.25) -> Optional[ModbusTcpClient]:
    c = ModbusTcpClient(ip, port=port, timeout=timeout)
    try:
        for _ in range(max(1, int(retries))):
            try:
                if c.connect():
                    return c
            except Exception:
                pass
            time.sleep(delay)
        try:
            c.close()
        except Exception:
            pass
        return None
    except Exception:
        try:
            c.close()
        except Exception:
            pass
        return None


def _is_modbus_error(resp) -> bool:
    try:
        if resp is None:
            return True
        if isinstance(resp, ExceptionResponse):
            return True
        if hasattr(resp, "isError") and callable(resp.isError) and resp.isError():
            return True
    except Exception:
        return True
    return False


def _treat_as_ok_modbus_write_exception(e: Exception) -> bool:
    msg = str(e or "")
    ok_like = [
        "unpack requires a buffer of 4 bytes",
        "Unable to decode response",
        "No response received",
        "Invalid Message",
        "Socket is closed",
    ]
    return any(k in msg for k in ok_like)


def _try_read_some_modbus_info(client: ModbusTcpClient) -> Optional[str]:
    try:
        r = client.read_holding_registers(address=reg_addr(40001), count=4, slave=1)
        if _is_modbus_error(r):
            return None
        vals = getattr(r, "registers", None)
        if not vals:
            return None
        return "R40001:" + ",".join(str(x) for x in vals[:4])
    except Exception as e:
        return ("READ ERR:" + str(e))[:18]


def _read_device_info_fast(ip: str) -> Optional[str]:
    c = _modbus_connect_with_retries(ip, port=MODBUS_PORT, timeout=0.8, retries=1, delay=0.0)
    if c is None:
        return None
    try:
        r = c.read_holding_registers(address=reg_addr(40001), count=4, slave=1)
        if _is_modbus_error(r):
            return None
        vals = getattr(r, "registers", None)
        if not vals:
            return None
        return "R40001:" + ",".join(str(x) for x in vals[:4])
    except Exception:
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass


def _u16(v):
    try:
        return int(v) & 0xFFFF
    except Exception:
        return 0


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


def _scan_compute_prefix(ip: str) -> Optional[str]:
    parts = (ip or "").split(".")
    if len(parts) != 4:
        return None
    return ".".join(parts[:3]) + "."


def pick_remote_fw_file_for_device(ip: str) -> str:
    if not os.path.isdir(TFTP_REMOTE_DIR):
        return ""
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
                full = os.path.join(TFTP_REMOTE_DIR, fn)
                if os.path.isfile(full):
                    bins.append(full)
    except Exception:
        return ""
    if not bins:
        return ""
    def _score(path: str):
        base = os.path.basename(path)
        stem = os.path.splitext(base)[0].strip()
        m = re.match(r"^(\d+)$", stem)
        mt = 0.0
        try:
            mt = os.path.getmtime(path)
        except Exception:
            mt = 0.0
        if m:
            try:
                n = int(m.group(1))
            except Exception:
                n = -1
            return (2, n, mt)
        return (1, -1, mt)
    bins.sort(key=_score, reverse=True)
    return bins[0]


def _ensure_tftp_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except PermissionError:
        ok = run_quiet(["sudo", "mkdir", "-p", path], timeout=6.0)
        if ok:
            run_quiet(["sudo", "chmod", "0777", path], timeout=4.0)
        return ok
    except Exception:
        return False


def tftp_upgrade_device(ip: str):
    ip = (ip or "").strip()
    if not ip:
        return
    set_ui_progress(5, "원격 업뎃\n준비...", pos=(18, 0), font_size=15)
    if not _quick_modbus_probe(ip, timeout=0.35):
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("포트 응답X", ip, pos=(2, 18), font_size=12)
        time.sleep(1.6)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return
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
    if not _ensure_tftp_dir(device_dir):
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("권한 오류", "/srv/tftp", pos=(6, 18), font_size=13)
        time.sleep(2.0)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return
    dst_path = os.path.join(device_dir, TFTP_DEVICE_FILENAME)
    try:
        try:
            if os.path.exists(dst_path):
                os.remove(dst_path)
        except PermissionError:
            run_quiet(["sudo", "rm", "-f", dst_path], timeout=4.0)
        except Exception:
            pass
        try:
            shutil.copyfile(fw_src, dst_path)
        except PermissionError:
            run_quiet(["sudo", "cp", "-f", fw_src, dst_path], timeout=6.0)
            run_quiet(["sudo", "chmod", "0644", dst_path], timeout=3.0)
        except Exception:
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
    client = _modbus_connect_with_retries(ip, port=502, timeout=2.2, retries=4, delay=0.35)
    if client is None:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("연결 실패", ip, pos=(2, 18), font_size=12)
        time.sleep(1.6)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return
    try:
        try:
            info = _try_read_some_modbus_info(client)
            if info:
                set_ui_text("MODBUS", info[:18], pos=(2, 18), font_size=12)
                time.sleep(0.9)
                clear_ui_override()
        except Exception:
            pass

        ok_final = False
        addr_ip1 = reg_addr(40088)
        addr_ctrl = reg_addr(40091)

        try:
            w1, w2 = encode_ip_to_words(tftp_ip)
            try:
                client.write_registers(address=addr_ip1, values=[w1, w2], slave=1)
            except Exception:
                pass
        except Exception:
            pass

        try:
            r = client.write_register(address=addr_ctrl, value=1, slave=1)
            if _is_modbus_error(r):
                ok_final = False
            else:
                ok_final = True
        except Exception as e:
            ok_final = _treat_as_ok_modbus_write_exception(e)

        if not ok_final:
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
        time.sleep(1.1)
        GPIO.output(LED_SUCCESS, False)
    finally:
        try:
            client.close()
        except Exception:
            pass
    clear_ui_override()


def build_scan_menu():
    with scan_lock:
        ips = list(scan_ips)
    commands_local, names_local, types_local, extras_local = [], [], [], []
    for ip in ips:
        commands_local.append(None)
        names_local.append(f"▶ {ip}")
        types_local.append("scan_item")
        extras_local.append(ip)
    commands_local.append(None)
    names_local.append("◀ 이전으로")
    types_local.append("back_from_scan")
    extras_local.append(None)
    return {"dir": "__scan__", "commands": commands_local, "names": names_local, "types": types_local, "extras": extras_local}


def build_scan_detail_menu(ip: str):
    return {"dir": "__scan_detail__", "commands": [None], "names": [f"{ip}"], "types": ["scan_detail"], "extras": [ip]}


def _draw_box_label(draw, x, y, w, h, label, active):
    f = get_font(11)
    if active:
        draw.rectangle([x, y, x + w, y + h], fill=255)
        draw.text((x + 3, y + 1), label, font=f, fill=0)
    else:
        draw.rectangle([x, y, x + w, y + h], outline=255, fill=0)
        draw.text((x + 3, y + 1), label, font=f, fill=255)


def _fmt_gas(v):
    if v is None:
        return "--"
    try:
        v = float(v)
        if abs(v - int(v)) < 1e-6:
            return str(int(v))
        if abs(v) < 100:
            return f"{v:.1f}"
        return f"{v:.0f}"
    except Exception:
        return "--"


def _read_gas_and_alarm_flags_with_client(c: ModbusTcpClient):
    lo = 40001
    hi = 40008
    start = reg_addr(lo)
    count = (hi - lo) + 1
    r = c.read_holding_registers(address=start, count=count, slave=1)
    if _is_modbus_error(r):
        return None, None, "read"
    regs = getattr(r, "registers", None) or []
    if len(regs) < count:
        return None, None, "short"

    def R(addr_4xxxx: int) -> int:
        return _u16(regs[addr_4xxxx - lo])

    status_40001 = R(REG_STATUS_4XXXX)
    gas_int = R(REG_GAS_INT_4XXXX)
    fault_40008 = R(REG_FAULT_4XXXX)

    gas = float(gas_int)

    a1 = bool(status_40001 & (1 << 6))
    a2 = bool(status_40001 & (1 << 7))
    pwr_fault = bool(fault_40008 & (1 << 2))
    fut = (fault_40008 != 0)

    flags = {"PWR": pwr_fault, "A1": a1, "A2": a2, "FUT": fut}
    return gas, flags, ""


def read_gas_and_alarm_flags(ip: str):
    c = _modbus_connect_with_retries(ip, port=MODBUS_PORT, timeout=0.8, retries=1, delay=0.0)
    if c is None:
        return None, None, "connect"
    try:
        return _read_gas_and_alarm_flags_with_client(c)
    except Exception as e:
        return None, None, str(e)[:18]
    finally:
        try:
            c.close()
        except Exception:
            pass


def draw_scan_detail_screen(draw):
    draw.rectangle(device.bounding_box, fill="black")

    W, H = device.width, device.height
    TOP_H = 16
    BOT_H = 12
    MID_Y0 = TOP_H
    MID_Y1 = H - BOT_H
    MID_CY = (MID_Y0 + MID_Y1) // 2

    f = scan_detail.get("flags", {}) or {}
    _draw_box_label(draw, 2, 1, 24, 14, "PWR", bool(f.get("PWR")))
    _draw_box_label(draw, 28, 1, 20, 14, "A1", bool(f.get("A1")))
    _draw_box_label(draw, 50, 1, 20, 14, "A2", bool(f.get("A2")))
    _draw_box_label(draw, 72, 1, 26, 14, "FUT", bool(f.get("FUT")))

    ip_txt = (scan_detail_ip or "").strip()
    if ip_txt:
        _right_text(draw, W - 2, 3, ip_txt, get_font(10), fill=255)

    gas_txt = _fmt_gas(scan_detail.get("gas", None))
    draw_center_text_autofit(
        draw,
        gas_txt,
        (W // 2 + VISUAL_X_OFFSET),
        MID_CY,
        max_width=W - 6,
        start_size=30,
        min_size=18
    )

    err = (scan_detail.get("err") or "").strip()
    fbot = get_font(10)
    max_w = W - 4
    y0 = H - BOT_H

    if err:
        msg = _ellipsis_to_width(draw, "ERR " + err, fbot, max_w)
        draw.text((2, y0 + 1), msg, font=fbot, fill=255)
    else:
        msg = _ellipsis_to_width(draw, "NEXT:뒤로  EXEC길게:TFTP", fbot, max_w)
        draw.text((2, y0 + 1), msg, font=fbot, fill=255)


def modbus_detail_poll_thread():
    global need_update
    client = None
    client_ip = None
    backoff_until = 0.0
    while not stop_threads:
        time.sleep(SCAN_DETAIL_POLL_SEC)

        with scan_detail_lock:
            active = scan_detail_active
            ip = (scan_detail_ip or "").strip()

        if (not active) or (not ip):
            if client:
                try:
                    client.close()
                except Exception:
                    pass
            client = None
            client_ip = None
            continue

        now = time.time()
        if now < backoff_until:
            continue

        if client and client_ip != ip:
            try:
                client.close()
            except Exception:
                pass
            client = None
            client_ip = None

        if client is None:
            c = ModbusTcpClient(ip, port=MODBUS_PORT, timeout=0.8)
            try:
                if not c.connect():
                    try:
                        c.close()
                    except Exception:
                        pass
                    scan_detail["err"] = "connect"
                    scan_detail["ts"] = time.time()
                    need_update = True
                    backoff_until = time.time() + 0.2
                    continue
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
                scan_detail["err"] = "connect"
                scan_detail["ts"] = time.time()
                need_update = True
                backoff_until = time.time() + 0.2
                continue
            client = c
            client_ip = ip

        try:
            gas, flags, err = _read_gas_and_alarm_flags_with_client(client)
            scan_detail["gas"] = gas
            if flags:
                scan_detail["flags"] = flags
            scan_detail["err"] = err or ""
            scan_detail["ts"] = time.time()
            need_update = True

            if err in ("connect", "read", "short"):
                try:
                    client.close()
                except Exception:
                    pass
                client = None
                client_ip = None
                backoff_until = time.time() + 0.2

        except Exception as e:
            scan_detail["err"] = str(e)[:18]
            scan_detail["ts"] = time.time()
            need_update = True
            try:
                client.close()
            except Exception:
                pass
            client = None
            client_ip = None
            backoff_until = time.time() + 0.2


def modbus_scan_loop():
    global scan_ips, scan_infos, scan_selected_idx, scan_selected_ip, need_update, scan_last_tick
    global scan_cursor, scan_base_prefix, scan_seen, scan_menu_dirty, scan_menu_dirty_ts
    global scan_done, scan_active

    while not stop_threads:
        time.sleep(0.08)
        with scan_lock:
            active = scan_active
            done = scan_done
            pref = scan_base_prefix

        if (not active) or done:
            continue

        now = time.time()
        if not pref:
            with scan_lock:
                scan_ips = []
                scan_infos.clear()
                scan_selected_idx = 0
                scan_selected_ip = None
                scan_done = True
                scan_active = False
                scan_menu_dirty = True
                scan_menu_dirty_ts = now
                scan_last_tick = now
            need_update = True
            continue

        found = []
        infos_local = {}
        last_push = 0.0

        for host in range(2, 255):
            if stop_threads:
                break
            ip = pref + str(host)
            if _quick_modbus_probe(ip, timeout=0.22):
                found.append(ip)
                info = _read_device_info_fast(ip)
                if info:
                    infos_local[ip] = info

            tnow = time.time()
            if tnow - last_push >= 0.35:
                last_push = tnow
                new_ips = sorted(found, key=lambda x: tuple(int(p) for p in x.split(".")))
                with scan_lock:
                    scan_ips = new_ips
                    for k, v in infos_local.items():
                        scan_infos[k] = v
                    if scan_selected_idx >= len(scan_ips):
                        scan_selected_idx = 0
                    scan_menu_dirty = True
                    scan_menu_dirty_ts = tnow
                    scan_last_tick = tnow
                need_update = True

        new_ips = sorted(found, key=lambda x: tuple(int(p) for p in x.split(".")))
        with scan_lock:
            scan_ips = new_ips
            scan_infos.clear()
            scan_infos.update(infos_local)
            if scan_selected_idx >= len(scan_ips):
                scan_selected_idx = 0
            scan_done = True
            scan_active = False
            scan_menu_dirty = True
            scan_menu_dirty_ts = time.time()
            scan_last_tick = time.time()

        need_update = True


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
        commands_local.append("device_scan")
        names_local.append("감지기 연결(스캔)")
        types_local.append("device_scan")
        extras_local.append(None)
    else:
        commands_local.append(None)
        names_local.append("◀ 이전으로")
        types_local.append("back")
        extras_local.append(None)

    return {"dir": dir_path, "commands": commands_local, "names": names_local, "types": types_local, "extras": extras_local}


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
    global git_last_check, git_has_update_cached
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
            if "이미 최신 상태" in (result.stdout or ""):
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
        with git_state_lock:
            git_has_update_cached = False
        git_last_check = 0.0
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()


def unlock_memory():
    global need_update
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
    draw.rectangle(device.bounding_box, fill="black")
    if kind == "progress":
        draw.text(pos, msg, font=get_font(fs), fill=255)
        x1, y1, x2, y2 = 10, 50, 110, 60
        draw.rectangle([(x1, y1), (x2, y2)], fill=0)
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


last_oled_update_time = 0.0


def _draw_scan_screen(draw):
    global scan_selected_ip
    with scan_lock:
        ips = list(scan_ips)
        idx = scan_selected_idx
        infos = dict(scan_infos)
        done = scan_done
    draw.rectangle(device.bounding_box, fill="black")
    now_dt = datetime.now()
    current_time = now_dt.strftime("%H:%M")
    draw.text((2, 1), current_time, font=get_font(12), fill=255)

    if ips:
        if idx < 0:
            idx = 0
        if idx >= len(ips):
            idx = len(ips) - 1
        sel_ip = ips[idx]
        scan_selected_ip = sel_ip
        title = f"{sel_ip}"
        info = infos.get(sel_ip, "")
        if not info:
            info = "읽는중..."
        cx = device.width // 2 + VISUAL_X_OFFSET
        draw_center_text_autofit(draw, title, cx, 28, device.width - 4, 18, min_size=12)
        draw.text((2, 46), _ellipsis_to_width(draw, (info or ""), get_font(11), device.width - 4), font=get_font(11), fill=255)
    else:
        cx = device.width // 2 + VISUAL_X_OFFSET
        if done:
            draw_center_text_autofit(draw, "장치 없음", cx, 28, device.width - 4, 18, min_size=12)
            draw.text((2, 46), _ellipsis_to_width(draw, "◀ 이전으로", get_font(11), device.width - 4), font=get_font(11), fill=255)
        else:
            draw_center_text_autofit(draw, "장치 검색중...", cx, 28, device.width - 4, 18, min_size=12)
            draw.text((2, 46), _ellipsis_to_width(draw, "잠시만...", get_font(11), device.width - 4), font=get_font(11), fill=255)


def update_oled_display():
    global current_command_index, status_message, message_position, message_font_size
    global current_menu, commands, command_names, command_types, menu_extras
    if not display_lock.acquire(timeout=0.2):
        return
    try:
        if not commands:
            return

        with wifi_action_lock:
            wifi_running = wifi_action_running

        now_dt = datetime.now()
        current_time = now_dt.strftime("%H시 %M분")
        voltage_percentage = battery_percentage
        ip_address = cached_ip
        wifi_level = cached_wifi_level

        try:
            with canvas(device) as draw:
                if _draw_override(draw):
                    return

                if current_menu and current_menu.get("dir") == "__scan_detail__":
                    draw_scan_detail_screen(draw)
                    return

                if current_menu and current_menu.get("dir") == "__scan__":
                    _draw_scan_screen(draw)
                    return

                title = command_names[current_command_index]
                item_type = command_types[current_command_index]

                if item_type in ("system", "wifi"):
                    ip_display = "연결 없음" if ip_address == "0.0.0.0" else ip_address
                    draw.text((0, 51), _ellipsis_to_width(draw, ip_display, font_big, device.width - 2), font=font_big, fill=255)
                    draw.text((80, -3), "GDSENG", font=font_big, fill=255)
                    draw.text((83, 50), "ver 3.72", font=font_big, fill=255)
                    draw.text((0, -3), current_time, font=font_time, fill=255)
                    if not cached_online:
                        draw.text((0, 38), "WiFi(옵션)", font=font_big, fill=255)
                else:
                    battery_icon = select_battery_icon(voltage_percentage if voltage_percentage >= 0 else 0)
                    draw.bitmap((90, -11), battery_icon, fill=255)
                    perc_text = f"{voltage_percentage:.0f}%" if (voltage_percentage is not None and voltage_percentage >= 0) else "--%"
                    draw.text((99, 1), perc_text, font=font_st, fill=255)
                    draw.text((2, 1), current_time, font=font_time, fill=255)
                    draw_wifi_bars(draw, 70, 3, wifi_level)

                if status_message:
                    draw.rectangle(device.bounding_box, fill="black")
                    draw.text(message_position, status_message, font=get_font(message_font_size), fill=255)
                    return

                if wifi_running:
                    draw.rectangle(device.bounding_box, fill="black")
                    x = 2
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
                        draw.text((x, 0), (st1 or "")[:16], font=get_font(13), fill=255)
                        line2 = (st2 or "")
                        if line2:
                            draw.text((x, 16), _ellipsis_to_width(draw, (line2 + dots), get_font(11), device.width - 4), font=get_font(11), fill=255)
                        else:
                            draw.text((x, 16), _ellipsis_to_width(draw, ("처리중" + dots), get_font(11), device.width - 4), font=get_font(11), fill=255)
                        x1, y1, x2, y2 = 8, 48, 120, 60
                        draw.rectangle([(x1, y1), (x2, y2)], outline=255, fill=0)
                        fill_w = int((x2 - x1) * (st_p / 100.0))
                        if fill_w > 0:
                            draw.rectangle([(x1, y1), (x1 + fill_w, y2)], fill=255)
                        draw.text((x, 32), "NEXT 길게: 취소", font=get_font(11), fill=255)
                    else:
                        if now < flash_until:
                            draw.text((x, 0), ("연결됨!" + dots2)[:16], font=get_font(14), fill=255)
                        else:
                            draw.text((x, 0), "WiFi 설정 모드", font=get_font(14), fill=255)
                        draw.text((x, 18), f"AP: {AP_SSID}"[:18], font=get_font(12), fill=255)
                        draw.text((x, 34), f"PW: {AP_PASS}"[:18], font=get_font(12), fill=255)
                        draw.text((x, 50), f"IP: {AP_IP}:{PORTAL_PORT}"[:18], font=get_font(12), fill=255)
                    return

                center_x = device.width // 2 + VISUAL_X_OFFSET
                if item_type in ("system", "wifi"):
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


def realtime_update_display():
    global need_update, last_oled_update_time
    global scan_menu_dirty, scan_menu_rebuild_last
    global current_menu, commands, command_names, command_types, menu_extras, current_command_index
    while not stop_threads:
        with wifi_action_lock:
            wifi_running = wifi_action_running
        wifi_stage_tick()
        ap_client_tick(wifi_running)

        now = time.time()

        if scan_menu_dirty and current_menu and current_menu.get("dir") == "__scan__":
            if (not next_is_down) and (not execute_is_down):
                if now - scan_menu_rebuild_last >= SCAN_MENU_REBUILD_MIN_SEC:
                    scan_menu_rebuild_last = now
                    with scan_lock:
                        scan_menu_dirty = False
                        ips_now = list(scan_ips)
                        sel_ip = scan_selected_ip
                    nm = build_scan_menu()
                    current_menu = nm
                    commands = nm["commands"]
                    command_names = nm["names"]
                    command_types = nm["types"]
                    menu_extras = nm["extras"]

                    if sel_ip and (sel_ip in ips_now):
                        new_idx = ips_now.index(sel_ip)
                        current_command_index = new_idx
                        with scan_lock:
                            scan_selected_idx = new_idx
                    else:
                        if current_command_index >= len(commands):
                            current_command_index = max(0, len(commands) - 1)
                    need_update = True

        if need_update or (now - last_oled_update_time >= 0.22):
            update_oled_display()
            last_oled_update_time = now
            need_update = False
        time.sleep(0.02)


def shutdown_system():
    set_ui_text("배터리 부족", "시스템 종료 중...", pos=(10, 18), font_size=15)
    time.sleep(2)
    try:
        os.system("sudo shutdown -h now")
    except Exception:
        pass


def execute_command(command_index):
    global is_executing, is_command_executing
    global current_menu, commands, command_names, command_types, menu_extras
    global current_command_index, menu_stack, need_update
    global connection_success, connection_failed_since_last_success
    global scan_active, scan_selected_idx, scan_selected_ip, scan_infos, scan_seen, scan_base_prefix, scan_done
    global scan_detail_active, scan_detail_ip
    global scan_menu_dirty, scan_menu_dirty_ts

    if not command_types or command_index < 0 or command_index >= len(command_types):
        return

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

    if item_type == "device_scan":
        myip = get_ip_address()
        with scan_lock:
            scan_active = True
            scan_done = False
            scan_selected_idx = 0
            scan_selected_ip = None
            scan_infos.clear()
            scan_seen.clear()
            scan_ips = []
            scan_base_prefix = _scan_compute_prefix(myip)
            scan_menu_dirty = True
            scan_menu_dirty_ts = time.time()
        with scan_detail_lock:
            scan_detail_active = False
            scan_detail_ip = None
        menu_stack.append((current_menu, current_command_index))
        current_menu = build_scan_menu()
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

    if item_type == "back_from_scan":
        with scan_lock:
            scan_active = False
            scan_done = True
            scan_selected_ip = None
        with scan_detail_lock:
            scan_detail_active = False
            scan_detail_ip = None
        if menu_stack:
            prev_menu, prev_index = menu_stack.pop()
            current_menu = prev_menu
            commands = current_menu["commands"]
            command_names = current_menu["names"]
            command_types = current_menu["types"]
            menu_extras = current_menu["extras"]
            current_command_index = prev_index if (0 <= prev_index < len(commands)) else 0
        clear_ui_override()
        need_update = True
        is_executing = False
        is_command_executing = False
        return

    if item_type == "scan_item":
        target_ip = menu_extras[command_index]
        if not target_ip:
            is_executing = False
            is_command_executing = False
            return
        clear_ui_override()
        with scan_lock:
            scan_active = False
            scan_done = True
        with scan_detail_lock:
            scan_detail_active = True
            scan_detail_ip = target_ip
        scan_detail["gas"] = None
        scan_detail["flags"] = {"PWR": False, "A1": False, "A2": False, "FUT": False}
        scan_detail["err"] = ""
        scan_detail["ts"] = time.time()
        menu_stack.append((current_menu, current_command_index))
        current_menu = build_scan_detail_menu(target_ip)
        commands = current_menu["commands"]
        command_names = current_menu["names"]
        command_types = current_menu["types"]
        menu_extras = current_menu["extras"]
        current_command_index = 0
        need_update = True
        is_executing = False
        is_command_executing = False
        return

    if item_type == "scan_detail":
        ip = None
        try:
            ip = menu_extras[command_index]
        except Exception:
            ip = None
        if not ip:
            is_executing = False
            is_command_executing = False
            return
        clear_ui_override()
        tftp_upgrade_device(ip)
        with scan_lock:
            scan_active = False
            scan_done = True
        with scan_detail_lock:
            scan_detail_active = True
            scan_detail_ip = ip
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
        set_ui_text("메모리 잠금", "해제 실패", pos=(20, 12), font_size=15)
        time.sleep(2)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        is_executing = False
        is_command_executing = False
        need_update = True
        return

    info_line = chosen_kind
    if flash_kb is not None:
        info_line = f"{chosen_kind} ({flash_kb}KB)"
    progress_msg = f"업데이트 중...\n{info_line}"
    set_ui_progress(30, progress_msg, pos=(6, 0), font_size=13)

    openocd_cmd = make_openocd_program_cmd(resolved_path)
    process = subprocess.Popen(openocd_cmd, shell=True)
    start_time = time.time()
    max_duration = 6
    progress_increment = 20 / max_duration

    while process.poll() is None:
        elapsed = time.time() - start_time
        current_progress = 30 + (elapsed * progress_increment)
        current_progress = min(current_progress, 80)
        set_ui_progress(current_progress, progress_msg, pos=(6, 0), font_size=13)
        time.sleep(0.2)

    result = process.returncode
    if result == 0:
        set_ui_progress(80, f"업데이트 성공!\n{info_line}", pos=(6, 0), font_size=13)
        time.sleep(POST_FLASH_WAIT_SEC)
        lock_memory_procedure()
    else:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_progress(0, f"업데이트 실패\n{info_line}", pos=(6, 0), font_size=13)
        time.sleep(1)

    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)
    clear_ui_override()
    need_update = True
    is_executing = False
    is_command_executing = False


def get_wifi_level():
    try:
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
    global cached_ip, cached_wifi_level, cached_online, last_menu_online, need_update
    while not stop_threads:
        try:
            cached_ip = get_ip_address()
            online_now = has_real_internet()
            cached_online = online_now
            cached_wifi_level = get_wifi_level() if online_now else 0
            if last_menu_online is None or online_now != last_menu_online:
                last_menu_online = online_now
                refresh_root_menu(reset_index=False)
                need_update = True
            time.sleep(1.5)
        except Exception:
            time.sleep(0.8)


def execute_button_logic():
    global current_command_index, need_update
    global execute_is_down, execute_long_handled, execute_press_time, execute_short_event
    global next_pressed_event, auto_flash_done_connection
    global next_long_handled, wifi_cancel_requested
    global scan_selected_idx, scan_selected_ip
    global scan_detail_active, scan_detail_ip
    global current_menu, commands, command_names, command_types, menu_extras, menu_stack
    global is_executing

    while not stop_threads:
        now = time.time()
        if battery_percentage == 0:
            shutdown_system()

        with wifi_action_lock:
            wifi_running = wifi_action_running

        if wifi_running and next_is_down and (not next_long_handled) and (next_press_time is not None):
            if now - next_press_time >= NEXT_LONG_CANCEL_THRESHOLD:
                next_long_handled = True
                wifi_cancel_requested = True
                wifi_stage_set(5, "취소 처리중", "잠시만")
                need_update = True

        if execute_is_down and (not execute_long_handled) and (execute_press_time is not None):
            if now - execute_press_time >= LONG_PRESS_THRESHOLD:
                execute_long_handled = True
                if commands and (not is_executing):
                    item_type = command_types[current_command_index]
                    if item_type in ("system", "dir", "back", "script", "wifi", "bin", "device_scan", "scan_item", "back_from_scan", "scan_detail"):
                        execute_command(current_command_index)
                        need_update = True

        if execute_short_event:
            execute_short_event = False
            if not execute_long_handled:
                if commands and (not is_executing):
                    current_command_index = (current_command_index - 1) % len(commands)
                    if current_menu and current_menu.get("dir") == "__scan__":
                        with scan_lock:
                            if scan_ips and command_types[current_command_index] == "scan_item":
                                scan_selected_idx = min(current_command_index, len(scan_ips) - 1)
                                scan_selected_ip = scan_ips[scan_selected_idx]
                    need_update = True
            execute_long_handled = False

        if next_pressed_event:
            if (current_menu and current_menu.get("dir") == "__scan_detail__") and (not is_executing):
                with scan_detail_lock:
                    scan_detail_active = False
                    scan_detail_ip = None
                if menu_stack:
                    prev_menu, prev_index = menu_stack.pop()
                    current_menu = prev_menu
                    commands = current_menu["commands"]
                    command_names = current_menu["names"]
                    command_types = current_menu["types"]
                    menu_extras = current_menu["extras"]
                    current_command_index = prev_index if (0 <= prev_index < len(commands)) else 0
                clear_ui_override()
                need_update = True
                next_pressed_event = False
                time.sleep(0.02)
                continue

            if (not execute_is_down) and (not is_executing):
                if commands:
                    current_command_index = (current_command_index + 1) % len(commands)
                    if current_menu and current_menu.get("dir") == "__scan__":
                        with scan_lock:
                            if scan_ips and command_types[current_command_index] == "scan_item":
                                scan_selected_idx = min(current_command_index, len(scan_ips) - 1)
                                scan_selected_ip = scan_ips[scan_selected_idx]
                    need_update = True
            next_pressed_event = False

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

        time.sleep(0.02)


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

git_thread = threading.Thread(target=git_poll_thread, daemon=True)
git_thread.start()

scan_thread = threading.Thread(target=modbus_scan_loop, daemon=True)
scan_thread.start()

detail_thread = threading.Thread(target=modbus_detail_poll_thread, daemon=True)
detail_thread.start()

need_update = True

try:
    execute_button_logic()
except KeyboardInterrupt:
    pass
finally:
    stop_threads = True
    try:
        kill_openocd()
    except Exception:
        pass
    GPIO.cleanup()
