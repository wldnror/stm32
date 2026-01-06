from datetime import datetime
import RPi.GPIO as GPIO
import time
import os
import sys
import socket
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.oled.device import sh1107
from luma.core.render import canvas
import subprocess
from ina219 import INA219
import threading
import re

VISUAL_X_OFFSET = 0
display_lock = threading.Lock()
state_lock = threading.Lock()
stm32_state_lock = threading.Lock()
openocd_lock = threading.Lock()

BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17
LED_SUCCESS = 24
LED_ERROR = 25
LED_ERROR1 = 23

SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1
MAX_VOLTAGE = 4.2

is_auto_mode = True
stm32_poll_enabled = True
auto_flash_done_connection = False

GPIO.setmode(GPIO.BCM)

last_time_button_next_pressed = 0.0
last_time_button_execute_pressed = 0.0
button_press_interval = 0.15
LONG_PRESS_THRESHOLD = 0.7

need_update = False
is_command_executing = False
last_mode_toggle_time = 0.0
mode_toggle_requested = False

execute_press_time = None
execute_is_down = False
execute_long_handled = False
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
last_selected_type = None

font_cache = {}
def get_font(size: int):
    f = font_cache.get(size)
    if f is None:
        f = ImageFont.truetype(font_path, size)
        font_cache[size] = f
    return f

def kill_openocd():
    subprocess.run(["sudo", "pkill", "-f", "openocd"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def run_openocd(args, timeout=None, capture=True):
    with openocd_lock:
        if capture:
            return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return subprocess.run(args, timeout=timeout)

def init_ina219():
    global ina
    try:
        ina = INA219(SHUNT_OHMS)
        ina.configure()
        print("INA219 초기화 성공")
    except Exception as e:
        print("INA219 초기화 실패:", str(e))
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
    except Exception as e:
        print("INA219 모듈 읽기 실패:", str(e))
        return -1

def battery_monitor_thread():
    global battery_percentage
    while not stop_threads:
        battery_percentage = read_ina219_percentage()
        time.sleep(2)

def toggle_mode():
    global is_auto_mode, last_mode_toggle_time, need_update
    global stm32_poll_enabled, connection_success, connection_failed_since_last_success

    with state_lock:
        is_auto_mode = not is_auto_mode
        last_mode_toggle_time = time.time()
        need_update = True
        stm32_poll_enabled = is_auto_mode

    if not is_auto_mode:
        kill_openocd()
        with stm32_state_lock:
            connection_success = False
            connection_failed_since_last_success = False

def button_next_callback(channel):
    global last_time_button_next_pressed, next_pressed_event
    now = time.time()
    last_time_button_next_pressed = now
    next_pressed_event = True

def button_execute_callback(channel):
    global last_time_button_execute_pressed, execute_press_time, execute_is_down, execute_long_handled
    now = time.time()
    last_time_button_execute_pressed = now
    execute_press_time = now
    execute_is_down = True
    execute_long_handled = False

GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.FALLING, callback=button_next_callback, bouncetime=100)
GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING, callback=button_execute_callback, bouncetime=100)
GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)
GPIO.setup(LED_ERROR1, GPIO.OUT)

def check_stm32_connection():
    global connection_success, connection_failed_since_last_success

    with state_lock:
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
        result = run_openocd(command, timeout=1.2, capture=True)
        ok = (result.returncode == 0)
        if not ok:
            if result.stderr:
                print("STM32 연결 실패:", result.stderr)
    except subprocess.TimeoutExpired:
        ok = False
    except Exception as e:
        print(f"STM32 연결 체크 중 오류 발생: {e}")
        ok = False

    with stm32_state_lock:
        if ok:
            if connection_failed_since_last_success:
                print("STM32 재연결 성공")
            else:
                print("STM32 연결 성공")
            connection_success = True
            connection_failed_since_last_success = False
        else:
            connection_failed_since_last_success = True
            connection_success = False

    return ok

def stm32_poll_thread():
    global last_stm32_check_time, auto_flash_done_connection
    while not stop_threads:
        time.sleep(0.1)

        with state_lock:
            poll_ok = stm32_poll_enabled and is_auto_mode and (not is_command_executing)
            idx = current_command_index
            types = command_types[:] if command_types else []
        if not poll_ok:
            continue
        if types and 0 <= idx < len(types) and types[idx] == "system":
            continue

        now = time.time()
        if now - last_stm32_check_time < 0.7:
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
font_s = ImageFont.truetype(font_path, 13)
font_st = ImageFont.truetype(font_path, 11)
font = ImageFont.truetype(font_path, 17)
font_status = ImageFont.truetype(font_path, 13)
font_1 = ImageFont.truetype(font_path, 21)
font_sysupdate = ImageFont.truetype(font_path, 17)
font_time = ImageFont.truetype(font_path, 12)

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

FIRMWARE_DIR = "/home/user/stm32/Program"

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
                cmd = [
                    "sudo", "openocd",
                    "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
                    "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
                    "-c", f"program {full_path} verify reset exit 0x08000000"
                ]
                entries.append((order, 1, display_name, "bin", cmd))

    except FileNotFoundError:
        print("펌웨어 폴더를 찾을 수 없습니다:", dir_path)
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
        commands_local.append("git_pull")
        names_local.append("시스템 업데이트")
        types_local.append("system")
        extras_local.append(None)
    else:
        commands_local.append(None)
        names_local.append("◀ 이전으로")
        types_local.append("back")
        extras_local.append(None)

    menu = {
        "dir": dir_path,
        "commands": commands_local,
        "names": names_local,
        "types": types_local,
        "extras": extras_local,
    }

    print(f"로딩된 메뉴 ({dir_path}):", names_local)
    return menu

current_menu = build_menu_for_dir(FIRMWARE_DIR, is_root=True)
commands = current_menu["commands"]
command_names = current_menu["names"]
command_types = current_menu["types"]
menu_extras = current_menu["extras"]
current_command_index = 0

def display_progress_and_message(percentage, message, message_position=(0, 0), font_size=17):
    with canvas(device) as draw:
        draw.text(message_position, message, font=font, fill=255)
        draw.rectangle([(10, 50), (110, 60)], outline="white", fill="black")
        draw.rectangle([(10, 50), (10 + percentage, 60)], outline="white", fill="white")

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

    with canvas(device) as draw:
        draw.text((36, 8), "시스템", font=font, fill=255)
        draw.text((17, 27), "업데이트 중", font=font, fill=255)

    try:
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

        if result.returncode == 0:
            if "이미 최신 상태" in result.stdout:
                display_progress_and_message(100, "이미 최신 상태", message_position=(10, 10), font_size=15)
                time.sleep(1)
            else:
                print("업데이트 성공!")
                GPIO.output(LED_SUCCESS, True)
                display_progress_and_message(100, "업데이트 성공!", message_position=(10, 10), font_size=15)
                time.sleep(1)
                GPIO.output(LED_SUCCESS, False)
                restart_script()
        else:
            print("GitHub 업데이트 실패. 오류 코드:", result.returncode)
            print("오류 메시지:", result.stderr)
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            display_progress_and_message(0, "명령 실행 중 오류 발생", message_position=(0, 10), font_size=15)
            time.sleep(1)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0, "명령 실행 중 오류 발생", message_position=(0, 10), font_size=15)
        time.sleep(1)
    finally:
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

def unlock_memory():
    display_progress_and_message(0, "메모리 잠금\n   해제 중", message_position=(18, 0), font_size=15)

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
    try:
        result = run_openocd(openocd_command, timeout=20, capture=False)
        ok = (result.returncode == 0)
    except Exception:
        ok = False

    if ok:
        display_progress_and_message(30, "메모리 잠금\n 해제 성공!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        return True

    display_progress_and_message(0, "메모리 잠금\n 해제 실패!", message_position=(20, 0), font_size=15)
    time.sleep(1)
    global need_update
    with state_lock:
        need_update = True
    return False

def restart_script():
    print("스크립트를 재시작합니다.")
    display_progress_and_message(25, "재시작 중", message_position=(20, 10), font_size=15)

    def restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=restart, daemon=True).start()

def lock_memory_procedure():
    global need_update
    display_progress_and_message(80, "메모리 잠금 중", message_position=(3, 10), font_size=15)
    openocd_command = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "stm32f1x lock 0",
        "-c", "reset run",
        "-c", "shutdown",
    ]
    try:
        result = run_openocd(openocd_command, timeout=20, capture=True)
        ok = (result.returncode == 0)
    except Exception:
        ok = False

    if ok:
        GPIO.output(LED_SUCCESS, True)
        display_progress_and_message(100, "메모리 잠금\n    성공", message_position=(20, 0), font_size=15)
        time.sleep(1)
        GPIO.output(LED_SUCCESS, False)
    else:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0, "메모리 잠금\n    실패", message_position=(20, 0), font_size=15)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

    with state_lock:
        need_update = True

def execute_command(command_index):
    global is_executing, is_command_executing
    global current_menu, commands, command_names, command_types, menu_extras
    global current_command_index, menu_stack, need_update

    with state_lock:
        is_executing = True
        is_command_executing = True

    if not commands:
        with state_lock:
            is_executing = False
            is_command_executing = False
        return

    item_type = command_types[command_index]

    if item_type == "dir":
        subdir = menu_extras[command_index]
        if subdir and os.path.isdir(subdir):
            with state_lock:
                menu_stack.append((current_menu, current_command_index))
            new_menu = build_menu_for_dir(subdir, is_root=False)
            with state_lock:
                current_menu = new_menu
                commands = current_menu["commands"]
                command_names = current_menu["names"]
                command_types = current_menu["types"]
                menu_extras = current_menu["extras"]
                current_command_index = 0
                need_update = True

        with state_lock:
            is_executing = False
            is_command_executing = False
        return

    if item_type == "back":
        with state_lock:
            has_stack = bool(menu_stack)
        if has_stack:
            with state_lock:
                prev_menu, prev_index = menu_stack.pop()
                current_menu = prev_menu
                commands = current_menu["commands"]
                command_names = current_menu["names"]
                command_types = current_menu["types"]
                menu_extras = current_menu["extras"]
                current_command_index = prev_index if 0 <= prev_index < len(commands) else 0
                need_update = True

        with state_lock:
            is_executing = False
            is_command_executing = False
        return

    if item_type == "system":
        kill_openocd()
        git_pull()
        with state_lock:
            need_update = True
            is_executing = False
            is_command_executing = False
        return

    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)

    with openocd_lock:
        if not unlock_memory():
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            with canvas(device) as draw:
                draw.text((20, 8), "메모리 잠금", font=font, fill=255)
                draw.text((28, 27), "해제 실패", font=font, fill=255)
            time.sleep(2)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
            with state_lock:
                is_executing = False
                is_command_executing = False
                need_update = True
            return

        display_progress_and_message(30, "업데이트 중...", message_position=(12, 10), font_size=15)

        cmd = commands[command_index]
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            process = None

        start_time = time.time()
        max_duration = 6
        progress_increment = 20 / max_duration if max_duration > 0 else 0

        if process is not None:
            while process.poll() is None:
                elapsed = time.time() - start_time
                current_progress = 30 + (elapsed * progress_increment)
                current_progress = min(current_progress, 80)
                display_progress_and_message(current_progress, "업데이트 중...", message_position=(12, 10), font_size=15)
                time.sleep(0.5)

            result = process.returncode
        else:
            result = 1

    if result == 0:
        display_progress_and_message(80, "업데이트 성공!", message_position=(7, 10), font_size=15)
        time.sleep(0.5)
        # lock_memory_procedure()
    else:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0, "업데이트 실패", message_position=(7, 10), font_size=15)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

    with state_lock:
        need_update = True
        is_executing = False
        is_command_executing = False

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

def update_oled_display():
    global status_message, message_position, message_font_size
    with display_lock:
        with state_lock:
            if not commands:
                return
            idx = current_command_index
            types = command_types[:]
            names = command_names[:]
            auto_mode = is_auto_mode
        if not (0 <= idx < len(names) and 0 <= idx < len(types)):
            return

        ip_address = get_ip_address()
        current_time = datetime.now().strftime("%H시 %M분")
        voltage_percentage = battery_percentage

        with canvas(device) as draw:
            item_type = types[idx]
            title = names[idx]

            if item_type != "system":
                mode_char = "A" if auto_mode else "M"
                outer_ellipse_box = (2, 0, 22, 20)
                text_position = {"A": (8, -3), "M": (5, -3)}
                draw.ellipse(outer_ellipse_box, outline="white", fill=None)
                draw.text(text_position[mode_char], mode_char, font=font, fill=255)

            if item_type != "system":
                battery_icon = select_battery_icon(voltage_percentage if voltage_percentage >= 0 else 0)
                draw.bitmap((90, -9), battery_icon, fill=255)
                if voltage_percentage is not None and voltage_percentage >= 0:
                    perc_text = f"{voltage_percentage:.0f}%"
                else:
                    perc_text = "--%"
                draw.text((99, 3), perc_text, font=font_st, fill=255)
                draw.text((27, 1), current_time, font=font_time, fill=255)
            else:
                ip_display = "연결 없음" if ip_address == "0.0.0.0" else ip_address
                draw.text((0, 51), ip_display, font=font_big, fill=255)
                draw.text((80, -3), "GDSENG", font=font_big, fill=255)
                draw.text((83, 50), "ver 3.71", font=font_big, fill=255)
                draw.text((0, -3), current_time, font=font_time, fill=255)

            with state_lock:
                sm = status_message
                mp = message_position
                mfs = message_font_size

            if sm:
                draw.rectangle(device.bounding_box, outline="white", fill="black")
                draw.text(mp, sm, font=get_font(mfs), fill=255)
            else:
                center_x = device.width // 2 + VISUAL_X_OFFSET
                if item_type == "system":
                    center_y = 33
                    use_font = font_sysupdate
                else:
                    center_y = 42
                    use_font = font_1

                try:
                    draw.text((center_x, center_y), title, font=use_font, fill=255, anchor="mm")
                except TypeError:
                    try:
                        w, h = draw.textsize(title, font=use_font)
                    except Exception:
                        w, h = (len(title) * 8, 16)
                    x = int(center_x - w / 2)
                    y = int(center_y - h / 2)
                    draw.text((x, y), title, font=use_font, fill=255)

last_oled_update_time = 0.0

def realtime_update_display():
    global last_oled_update_time
    while not stop_threads:
        with state_lock:
            busy = is_command_executing
            nu = need_update
        if not busy:
            now = time.time()
            if nu or (now - last_oled_update_time >= 1.0):
                update_oled_display()
                last_oled_update_time = now
                with state_lock:
                    global need_update
                    need_update = False
        time.sleep(0.05)

def shutdown_system():
    try:
        with canvas(device) as draw:
            draw.text((20, 25), "배터리 부족", font=font, fill=255)
            draw.text((25, 50), "시스템 종료 중...", font=font_st, fill=255)
        time.sleep(5)
        os.system("sudo shutdown -h now")
    except Exception as e:
        print("시스템 종료 중 오류 발생:", str(e))

init_ina219()

battery_thread = threading.Thread(target=battery_monitor_thread, daemon=True)
battery_thread.start()

realtime_update_thread = threading.Thread(target=realtime_update_display, daemon=True)
realtime_update_thread.start()

stm32_thread = threading.Thread(target=stm32_poll_thread, daemon=True)
stm32_thread.start()

with state_lock:
    need_update = True

try:
    while True:
        now = time.time()

        if battery_percentage == 0:
            shutdown_system()

        if execute_is_down and (not execute_long_handled) and (execute_press_time is not None):
            if now - execute_press_time >= LONG_PRESS_THRESHOLD:
                execute_long_handled = True
                with state_lock:
                    auto_mode = is_auto_mode
                    can = bool(commands) and (not is_executing)
                    idx = current_command_index
                    it = command_types[idx] if can and 0 <= idx < len(command_types) else None
                if auto_mode and can and it in ("system", "dir", "back"):
                    execute_command(idx)
                    with state_lock:
                        need_update = True

        if execute_is_down and GPIO.input(BUTTON_PIN_EXECUTE) == GPIO.HIGH:
            execute_is_down = False

            if abs(last_time_button_next_pressed - last_time_button_execute_pressed) < button_press_interval:
                mode_toggle_requested = True
                next_pressed_event = False
            else:
                with state_lock:
                    auto_mode = is_auto_mode
                    can = bool(commands) and (not is_executing)
                    idx = current_command_index
                if auto_mode:
                    if not execute_long_handled:
                        if can:
                            with state_lock:
                                current_command_index = (current_command_index - 1) % len(commands)
                                need_update = True
                else:
                    if can:
                        execute_command(idx)
                        with state_lock:
                            need_update = True

            execute_press_time = None
            execute_long_handled = False

        if next_pressed_event:
            if (not execute_is_down) and (now - last_time_button_next_pressed) >= 0:
                with state_lock:
                    if (not is_executing) and (now - last_mode_toggle_time >= 1) and commands:
                        current_command_index = (current_command_index + 1) % len(commands)
                        need_update = True
                next_pressed_event = False

        if mode_toggle_requested:
            with state_lock:
                ok = (now - last_mode_toggle_time >= 0.5)
            if ok:
                toggle_mode()
            mode_toggle_requested = False

        with state_lock:
            st = command_types[current_command_index] if commands else None

        if st != last_selected_type:
            last_selected_type = st
            if st == "system":
                kill_openocd()
                with stm32_state_lock:
                    connection_success = False
                    connection_failed_since_last_success = False

        with state_lock:
            auto_mode = is_auto_mode
            can = bool(commands) and (not is_executing)
            idx = current_command_index
            t = command_types[idx] if can and 0 <= idx < len(command_types) else None
            done = auto_flash_done_connection

        if can and auto_mode and t == "bin":
            with stm32_state_lock:
                cs = connection_success
            if cs and (not done):
                execute_command(idx)
                auto_flash_done_connection = True

        time.sleep(0.03)

except KeyboardInterrupt:
    pass
finally:
    stop_threads = True
    try:
        kill_openocd()
    except Exception:
        pass
    try:
        GPIO.cleanup()
    except Exception:
        pass
