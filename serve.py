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
from ina219 import INA219, DeviceRangeError
import threading
import re  # ← 번호 파싱용

VISUAL_X_OFFSET = 0  # 필요에 따라 -3, -4 등으로 조절
display_lock = threading.Lock()

# GPIO 핀 설정
BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17
LED_SUCCESS = 24
LED_ERROR = 25
LED_ERROR1 = 23

# INA219 설정
SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1  # 최소 작동 전압
MAX_VOLTAGE = 4.2  # 최대 전압 (완충 시)

# 자동 모드와 수동 모드 상태를 추적하는 전역 변수
is_auto_mode = True

# 이 STM32 "연결 세션"에서 자동 업데이트를 이미 1회 했는지 여부
auto_flash_done_connection = False

# GPIO 핀 번호 모드 설정 및 초기 상태 설정
GPIO.setmode(GPIO.BCM)

# 전역 변수로 마지막으로 눌린 시간을 추적
last_time_button_next_pressed = 0.0
last_time_button_execute_pressed = 0.0
button_press_interval = 0.15  # 두 버튼이 동시에 눌린 것으로 간주되는 최대 시간 차이
LONG_PRESS_THRESHOLD = 0.7   # EXECUTE 길게 누르는 기준 시간(초)

need_update = False
is_command_executing = False

# 전역 변수로 마지막 모드 전환 시간을 추적
last_mode_toggle_time = 0.0

# 모드 토글 요청 플래그 (실제 토글은 메인 루프에서)
mode_toggle_requested = False

# EXECUTE 버튼 길게/짧게 판정용
execute_press_time = None
execute_is_down = False
execute_long_handled = False  # ← 이 눌림에서 long press 처리 여부

# NEXT 버튼 눌림 이벤트 플래그 (메인 루프에서 처리)
next_pressed_event = False

# 스크립트 시작 부분에 전역 변수 정의
is_executing = False

# ---------------- 메뉴 스택 관련 전역 ----------------
# (menu, selected_index) 튜플을 저장
menu_stack = []  # 이전 디렉토리 메뉴와 그때 선택 인덱스를 쌓아두는 스택

current_menu = None          # {'dir': ..., 'commands': [...], 'names': [...], 'types': [...], 'extras': [...]}
commands = []
command_names = []
command_types = []           # "bin", "dir", "system", "back"
menu_extras = []             # type이 "dir"일 때 하위 디렉토리 경로 저장
current_command_index = 0

status_message = ""
message_position = (0, 0)
message_font_size = 17

# ---------------- INA219 / 배터리 전역 ----------------
ina = None
battery_percentage = -1  # 배터리 퍼센트 캐시

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
    """
    배터리 퍼센트 계산 (실제 I2C 읽기)
    -> 전용 쓰레드에서만 호출하고,
       다른 곳에서는 battery_percentage 캐시만 사용하도록 함.
    """
    global ina
    if ina is None:
        return -1
    try:
        voltage = ina.voltage()
        if voltage <= MIN_VOLTAGE:
            return 0
        elif voltage >= MAX_VOLTAGE:
            return 100
        else:
            return int(((voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)) * 100)
    except Exception as e:
        print("INA219 모듈 읽기 실패:", str(e))
        return -1

def battery_monitor_thread():
    """
    2초마다 배터리 퍼센트를 갱신하는 쓰레드.
    """
    global battery_percentage
    while True:
        battery_percentage = read_ina219_percentage()
        time.sleep(2)

# ---------------- 버튼 / 모드 ----------------

def toggle_mode():
    """AUTO <-> MANUAL 모드 전환 (실제 토글은 메인 루프에서 수행)"""
    global is_auto_mode, last_mode_toggle_time, need_update
    is_auto_mode = not is_auto_mode
    last_mode_toggle_time = time.time()
    need_update = True  # OLED는 전용 쓰레드가 그리도록 플래그만 세움

def button_next_callback(channel):
    """
    콜백에서는 시간/플래그만 기록.
    실제 메뉴 이동/모드 전환 판단은 메인 루프에서.
    """
    global last_time_button_next_pressed, next_pressed_event
    now = time.time()
    last_time_button_next_pressed = now
    next_pressed_event = True  # 메인 루프에서 처리

def button_execute_callback(channel):
    """
    콜백에서는 '누르기 시작' 시간만 기록.
    - 눌린 시각 기록
    - is_down = True
    실제 길게/짧게/모드 전환 판단은 메인 루프에서,
    버튼이 '떨어지는 시점'을 폴링으로 체크.
    """
    global last_time_button_execute_pressed, execute_press_time, execute_is_down, execute_long_handled
    now = time.time()
    last_time_button_execute_pressed = now
    execute_press_time = now
    execute_is_down = True
    execute_long_handled = False  # 새 눌림 시작 시 long 처리 플래그 리셋

# 자동 모드와 수동 모드 아이콘 대신 문자열 사용
auto_mode_text = 'A'
manual_mode_text = 'M'

# GPIO 설정
GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.FALLING, callback=button_next_callback, bouncetime=100)
GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING, callback=button_execute_callback, bouncetime=100)
GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)
GPIO.setup(LED_ERROR1, GPIO.OUT)

# 연결 상태를 추적하기 위한 변수
connection_success = False
connection_failed_since_last_success = False
last_stm32_check_time = 0.0  # STM32 체크 주기 제한용

# ---------------- STM32 / 배터리 ----------------

def check_stm32_connection():
    """
    STM32 연결 상태 확인.
    display_lock을 잡지 않고 실행하고,
    너무 자주 돌지 않도록 메인 루프에서 주기 제한.
    """
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
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode == 0:
            if connection_failed_since_last_success:
                print("STM32 재연결 성공")
                connection_success = True
                connection_failed_since_last_success = False
            else:
                print("STM32 연결 성공")
                connection_success = True
        else:
            print("STM32 연결 실패:", result.stderr)
            connection_failed_since_last_success = True
            connection_success = False

        return connection_success

    except Exception as e:
        print(f"STM32 연결 체크 중 오류 발생: {e}")
        connection_failed_since_last_success = True
        connection_success = False
        return False

# ---------------- OLED / 폰트 ----------------

serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'
font_big = ImageFont.truetype(font_path, 12)
font_s = ImageFont.truetype(font_path, 13)
font_st = ImageFont.truetype(font_path, 11)
font = ImageFont.truetype(font_path, 17)
font_status = ImageFont.truetype(font_path, 13)
font_1 = ImageFont.truetype(font_path, 21)   # 일반 메뉴(펌웨어 .bin)용
font_sysupdate = ImageFont.truetype(font_path, 17)  # 시스템 업데이트 전용 더 작은 폰트
font_time = ImageFont.truetype(font_path, 12)

# 배터리 아이콘 로드 (지금은 모두 같은 이미지 사용)
low_battery_icon = Image.open("/home/user/stm32/img/bat.png")
medium_battery_icon = Image.open("/home/user/stm32/img/bat.png")
high_battery_icon = Image.open("/home/user/stm32/img/bat.png")
full_battery_icon = Image.open("/home/user/stm32/img/bat.png")

def select_battery_icon(percentage):
    if percentage < 20:
        return low_battery_icon
    elif percentage < 60:
        return medium_battery_icon
    elif percentage < 100:
        return high_battery_icon
    else:
        return full_battery_icon

# -------------------------------
#  펌웨어 폴더 자동 스캔 + 폴더 메뉴
# -------------------------------
FIRMWARE_DIR = "/home/user/stm32/Program"

def parse_order_and_name(name: str, is_dir: bool):
    """
    '1.부트로더.bin' / '1.ORG.bin' / '2.HMDS' (폴더) 같은 이름에서
    앞의 숫자와 표시 이름을 분리해준다.

    - 파일(bin)  : 확장자(.bin) 제거 후 번호/이름 파싱
    - 폴더(dir)  : 전체 이름 그대로 번호/이름 파싱
    숫자가 없으면 order=9999로 뒤에 정렬.
    """
    if is_dir:
        raw = name          # 예: '2.HMDS' 그대로 사용
    else:
        raw = os.path.splitext(name)[0]  # 파일은 확장자 제거 ('1.ORG.bin' → '1.ORG')

    m = re.match(r'^(\d+)\.(.*)$', raw)
    if m:
        order = int(m.group(1))
        display = m.group(2).lstrip()
    else:
        order = 9999
        display = raw
    return order, display

def build_menu_for_dir(dir_path, is_root=False):
    """
    dir_path 안의 폴더와 .bin 파일을 읽어서 메뉴를 구성한다.
    - 폴더   → type: "dir"
    - .bin  → type: "bin"
    - 루트   → 마지막에 "시스템 업데이트" (type: "system")
    - 서브폴더 → 마지막에 "◀ 이전으로" (type: "back")
    정렬 순서:
    - 번호(order) → 타입(폴더/파일) → 이름
    """
    entries = []  # (order, type_pri, display, type, extra)

    try:
        for fname in os.listdir(dir_path):
            full_path = os.path.join(dir_path, fname)

            # 1) 디렉토리인 경우
            if os.path.isdir(full_path):
                order, display_name = parse_order_and_name(fname, is_dir=True)
                # 폴더 표시: ► 폴더명
                display_name = "▶ " + display_name
                entries.append((order, 0, display_name, "dir", full_path))

            # 2) .bin 파일인 경우
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
        print("펌웨어 폴더를 찾을 수 없습니다:", dir_path)
        entries = []

    # 정렬: 번호(order) → 타입(폴더/파일) → 이름
    entries.sort(key=lambda x: (x[0], x[1], x[2]))

    commands_local = []
    names_local = []
    types_local = []
    extras_local = []

    for order, type_pri, display_name, item_type, extra in entries:
        if item_type == "dir":
            commands_local.append(None)         # 폴더는 실제 실행 명령 없음
            names_local.append(display_name)
            types_local.append("dir")
            extras_local.append(extra)         # extra 에 하위 디렉토리 경로 저장
        elif item_type == "bin":
            commands_local.append(extra)       # openocd_cmd
            names_local.append(display_name)
            types_local.append("bin")
            extras_local.append(None)

    # 루트 / 서브에 따라 마지막 항목 추가
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

# 초기 메뉴 로딩 (루트)
current_menu = build_menu_for_dir(FIRMWARE_DIR, is_root=True)
commands = current_menu["commands"]
command_names = current_menu["names"]
command_types = current_menu["types"]
menu_extras = current_menu["extras"]
current_command_index = 0

# ---------------- git pull / 진행바 ----------------

def display_progress_and_message(percentage, message, message_position=(0, 0), font_size=17):
    with canvas(device) as draw:
        draw.text(message_position, message, font=font, fill=255)
        draw.rectangle([(10, 50), (110, 60)], outline="white", fill="black")
        draw.rectangle([(10, 50), (10 + percentage, 60)], outline="white", fill="white")

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
            print("GitHub 업데이트 실패. 오류 코드:", resultreturncode)
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

# ---------------- 메모리 잠금/해제 ----------------

def unlock_memory():
    with display_lock:
        print("메모리 해제 시도...")

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
    result = subprocess.run(openocd_command)

    if result.returncode == 0:
        display_progress_and_message(30, "메모리 잠금\n 해제 성공!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        return True
    else:
        display_progress_and_message(0, "메모리 잠금\n 해제 실패!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        global need_update
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
            print("성공적으로 메모리를 잠갔습니다.")
            GPIO.output(LED_SUCCESS, True)
            display_progress_and_message(100, "메모리 잠금\n    성공", message_position=(20, 0), font_size=15)
            time.sleep(1)
            GPIO.output(LED_SUCCESS, False)
        else:
            print("메모리 잠금에 실패했습니다. 오류 코드:", resultreturncode)
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            display_progress_and_message(0, "메모리 잠금\n    실패", message_position=(20, 0), font_size=15)
            time.sleep(1)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0, "오류 발생")
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
    finally:
        need_update = True

# ---------------- 메뉴 실행 ----------------

def execute_command(command_index):
    global is_executing, is_command_executing
    global current_menu, commands, command_names, command_types, menu_extras
    global current_command_index, menu_stack, need_update

    is_executing = True
    is_command_executing = True

    if not commands:
        is_executing = False
        is_command_executing = False
        return

    item_type = command_types[command_index]
    print("[EXECUTE] index:", command_index, "type:", item_type,
          "name:", command_names[command_index])

    # 1) 폴더 진입
    if item_type == "dir":
        subdir = menu_extras[command_index]
        if subdir and os.path.isdir(subdir):
            # 현재 메뉴와 선택 인덱스를 함께 스택에 저장
            menu_stack.append((current_menu, current_command_index))

            current_menu = build_menu_for_dir(subdir, is_root=False)
            commands = current_menu["commands"]
            command_names = current_menu["names"]
            command_types = current_menu["types"]
            menu_extras = current_menu["extras"]
            # 서브 폴더 안에서는 맨 위부터 시작
            current_command_index = 0
            need_update = True

        is_executing = False
        is_command_executing = False
        return

    # 2) 이전으로 (back)
    if item_type == "back":
        if menu_stack:
            # 저장해 둔 (메뉴, 인덱스) 튜플을 꺼냄
            prev_menu, prev_index = menu_stack.pop()

            current_menu = prev_menu
            commands = current_menu["commands"]
            command_names = current_menu["names"]
            command_types = current_menu["types"]
            menu_extras = current_menu["extras"]

            # 원래 선택하던 인덱스로 복원 (범위 체크 포함)
            if 0 <= prev_index < len(commands):
                current_command_index = prev_index
            else:
                current_command_index = 0

            need_update = True

        is_executing = False
        is_command_executing = False
        return

    # 3) 시스템 업데이트
    if item_type == "system":
        git_pull()
        need_update = True
        is_executing = False
        is_command_executing = False
        return

    # 4) 일반 bin 실행
    print("업데이트 시도...")
    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)

    if not unlock_memory():
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        with canvas(device) as draw:
            draw.text((20, 8), "메모리 잠금", font=font, fill=255)
            draw.text((28, 27), "해제 실패", font=font, fill=255)
        time.sleep(2)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        is_executing = False
        is_command_executing = False
        need_update = True
        return

    display_progress_and_message(30, "업데이트 중...", message_position=(12, 10), font_size=15)
    process = subprocess.Popen(commands[command_index], shell=True)

    start_time = time.time()
    max_duration = 6
    progress_increment = 20 / max_duration

    while process.poll() is None:
        elapsed = time.time() - start_time
        current_progress = 30 + (elapsed * progress_increment)
        current_progress = min(current_progress, 80)
        display_progress_and_message(current_progress, "업데이트 중...", message_position=(12, 10), font_size=15)
        time.sleep(0.5)

    result = process.returncode
    if result == 0:
        print(f"'{commands[command_index]}' 업데이트 성공!")
        display_progress_and_message(80, "업데이트 성공!", message_position=(7, 10), font_size=15)
        time.sleep(0.5)
        lock_memory_procedure()
    else:
        print(f"'{commands[command_index]}' 업데이트 실패!")
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0, "업데이트 실패", message_position=(7, 10), font_size=15)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

    need_update = True
    is_executing = False
    is_command_executing = False

# ---------------- IP / OLED 출력 ----------------

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
    global current_command_index, status_message, message_position, message_font_size
    with display_lock:
        if not commands:
            return

        ip_address = get_ip_address()
        now = datetime.now()
        current_time = now.strftime('%H시 %M분')

        # 배터리 퍼센트는 모니터 쓰레드에서 갱신된 캐시 사용
        voltage_percentage = battery_percentage

        with canvas(device) as draw:
            item_type = command_types[current_command_index]
            title = command_names[current_command_index]

            # 모드 표시 (시스템 업데이트 메뉴가 아닐 때만)
            if item_type != "system":
                mode_char = 'A' if is_auto_mode else 'M'
                outer_ellipse_box = (2, 0, 22, 20)
                text_position = {'A': (8, -3), 'M': (5, -3)}
                draw.ellipse(outer_ellipse_box, outline="white", fill=None)
                draw.text(text_position[mode_char], mode_char, font=font, fill=255)

            # 상단 정보 (배터리/시간 or IP/버전)
            if item_type != "system":
                battery_icon = select_battery_icon(voltage_percentage if voltage_percentage >= 0 else 0)
                draw.bitmap((90, -9), battery_icon, fill=255)
                # 퍼센티지 표시 (음수면 오류이므로 '--%')
                if voltage_percentage is not None and voltage_percentage >= 0:
                    perc_text = f"{voltage_percentage:.0f}%"
                else:
                    perc_text = "--%"
                draw.text((99, 3), perc_text, font=font_st, fill=255)
                draw.text((27, 1), current_time, font=font_time, fill=255)
            else:
                if ip_address == "0.0.0.0":
                    ip_display = "연결 없음"
                else:
                    ip_display = ip_address
                draw.text((0, 51), ip_display, font=font_big, fill=255)
                draw.text((80, -3), 'GDSENG', font=font_big, fill=255)
                draw.text((83, 50), 'ver 3.71', font=font_big, fill=255)
                draw.text((0, -3), current_time, font=font_time, fill=255)

            # 상태 메시지가 있을 때 전체 메시지 화면
            if status_message:
                draw.rectangle(device.bounding_box, outline="white", fill="black")
                font_custom = ImageFont.truetype(font_path, message_font_size)
                draw.text(message_position, status_message, font=font_custom, fill=255)
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

# ---------------- 실시간 업데이트 스레드 ----------------

last_oled_update_time = 0.0

def realtime_update_display():
    """
    OLED는 이 쓰레드만 직접 그리게 한다.
    - need_update 플래그가 True이거나
    - 마지막 업데이트 이후 1초 이상 지났을 때 갱신
    """
    global is_command_executing, need_update, last_oled_update_time
    while True:
        if not is_command_executing:
            now = time.time()
            if need_update or (now - last_oled_update_time >= 1.0):
                update_oled_display()
                last_oled_update_time = now
                need_update = False
        time.sleep(0.05)

# ---------------- 종료 처리 / 메인 루프 ----------------

def shutdown_system():
    try:
        with canvas(device) as draw:
            draw.text((20, 25), "배터리 부족", font=font, fill=255)
            draw.text((25, 50), "시스템 종료 중...", font=font_st, fill=255)
        time.sleep(5)
        os.system('sudo shutdown -h now')
    except Exception as e:
        print("시스템 종료 중 오류 발생:", str(e))

# 초기 INA219 셋업 & 배터리 모니터 시작
init_ina219()
battery_thread = threading.Thread(target=battery_monitor_thread, daemon=True)
battery_thread.start()

# OLED 갱신 스레드 시작
realtime_update_thread = threading.Thread(target=realtime_update_display, daemon=True)
realtime_update_thread.start()

# 초기 화면
need_update = True

try:
    while True:
        now = time.time()

        # 1) 배터리 부족 종료
        if battery_percentage == 0:
            print("배터리 수준이 0%입니다. 시스템을 종료합니다.")
            shutdown_system()

        # 2-0) EXECUTE 길게 누르는 중인지 감시 (버튼이 아직 내려가 있는 상태에서 long 처리)
        if execute_is_down and not execute_long_handled and execute_press_time is not None:
            if now - execute_press_time >= LONG_PRESS_THRESHOLD:
                execute_long_handled = True
                if is_auto_mode and commands and not is_executing:
                    item_type = command_types[current_command_index]
                    print(f"[AUTO] EXECUTE long press detected (hold), type={item_type}")
                    if item_type in ("system", "dir", "back"):
                        execute_command(current_command_index)
                        need_update = True
                # MANUAL 모드는 길이 구분 없이 release에서만 실행 처리

        # 2) EXECUTE 버튼 릴리즈 감지 (길게/짧게/모드 전환 판단)
        if execute_is_down and GPIO.input(BUTTON_PIN_EXECUTE) == GPIO.HIGH:
            # 버튼이 올라감 → 눌렀던 시간으로 동작 판단 (단, long은 위에서 이미 처리될 수 있음)
            duration = now - execute_press_time if execute_press_time else 0
            execute_is_down = False

            # 두 버튼 동시에 눌린 경우 → 모드 전환
            if abs(last_time_button_next_pressed - last_time_button_execute_pressed) < button_press_interval:
                mode_toggle_requested = True
                # 이 경우 NEXT 이벤트는 취소
                next_pressed_event = False

            else:
                # AUTO / MANUAL 모드별 동작
                if is_auto_mode:
                    if execute_long_handled:
                        # 이미 long press 로직 처리됨 → 여기서는 아무 것도 하지 않음
                        print(f"[AUTO] EXECUTE release after long press ({duration:.3f}s)")
                    else:
                        # long 처리 안 됐으면 무조건 short로 처리
                        if commands and not is_executing:
                            print(f"[AUTO] EXECUTE short press ({duration:.3f}s)")
                            current_command_index = (current_command_index - 1) % len(commands)
                            need_update = True
                else:
                    # MANUAL 모드: 길이 상관 없이 실행
                    if commands and not is_executing:
                        print("[MANUAL] EXECUTE pressed, run command")
                        execute_command(current_command_index)
                        need_update = True

            # 이 눌림에 대한 상태 리셋
            execute_press_time = None
            execute_long_handled = False

        # 3) NEXT 버튼 단독 이벤트 처리 (모드 전환 조합이 아닌 경우)
        if next_pressed_event:
            # EXECUTE를 함께 누른 케이스는 위에서 처리
            if not execute_is_down and (now - last_time_button_next_pressed) >= 0:
                # 모드 전환 직후 10초 동안은 입력 무시 (기존 로직 유지)
                if not is_executing and (now - last_mode_toggle_time >= 1):
                    if commands:
                        current_command_index = (current_command_index + 1) % len(commands)
                        need_update = True
                next_pressed_event = False

        # 4) 모드 토글 요청 처리
        if mode_toggle_requested:
            # 모드 전환 직후 0.5초 동안은 중복 전환 방지
            if now - last_mode_toggle_time >= 0.5:
                toggle_mode()
            mode_toggle_requested = False

        # 4.5) STM32 연결 상태 주기적 확인 (3초마다)
        if now - last_stm32_check_time > 0.7:
            last_stm32_check_time = now
            prev_state = connection_success
            check_stm32_connection()
            if connection_success and not prev_state:
                print("=> 새 STM32 연결 감지: 자동 업데이트 1회 허용 상태로 리셋")
                auto_flash_done_connection = False
            elif (not connection_success) and prev_state:
                print("=> STM32 케이블 제거 감지")

        # 5) 자동 모드에서 bin 타입 자동 실행 (연결당 1회)
        if commands:
            if (
                is_auto_mode
                and command_types[current_command_index] == "bin"
                and not is_executing
                and connection_success             # 실제 STM32가 연결돼 있을 때만
                and not auto_flash_done_connection # 이 연결 세션에서 아직 자동 업데이트 안 했을 때만
            ):
                print("[AUTO] STM32 연결 상태에서 자동 업데이트 1회 실행")
                execute_command(current_command_index)
                auto_flash_done_connection = True

        time.sleep(0.03)

except KeyboardInterrupt:
    GPIO.cleanup()
