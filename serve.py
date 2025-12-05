from datetime import datetime
import RPi.GPIO as GPIO
import time
import os
import sys
import socket
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1107
import subprocess
from ina219 import INA219, DeviceRangeError
import threading
import re  # ← 번호 파싱용

VISUAL_X_OFFSET = 0  # 필요에 따라 -3, -4 등으로 조절
display_lock = threading.Lock()
# GPIO 핀 설정
BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17
# LED_DEBUGGING = 23
LED_SUCCESS = 24
LED_ERROR = 25
LED_ERROR1 = 23

# INA219 설정
SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1  # 최소 작동 전압
MAX_VOLTAGE = 4.2  # 최대 전압 (완충 시)

# 자동 모드와 수동 모드 상태를 추적하는 전역 변수
is_auto_mode = True

# GPIO 핀 번호 모드 설정 및 초기 상태 설정
GPIO.setmode(GPIO.BCM)

# 전역 변수로 마지막으로 눌린 시간을 추적
last_time_button_next_pressed = 0
last_time_button_execute_pressed = 0
button_press_interval = 0.5  # 두 버튼이 동시에 눌린 것으로 간주되는 최대 시간 차이

need_update = False
is_command_executing = False
is_button_pressed = False

# 전역 변수로 마지막 모드 전환 시간을 추적
last_mode_toggle_time = 0

# 스크립트 시작 부분에 전역 변수 정의
is_executing = False

# ---------------- 메뉴 스택 관련 전역 ----------------
menu_stack = []  # 이전 디렉토리들의 메뉴를 쌓아두는 스택

current_menu = None          # {'dir': ..., 'commands': [...], 'names': [...], 'types': [...], 'extras': [...]}
commands = []
command_names = []
command_types = []           # "bin", "dir", "system", "back"
menu_extras = []             # type이 "dir"일 때 하위 디렉토리 경로 저장

# ---------------- 공통 함수 ----------------

def toggle_mode():
    global is_auto_mode, last_mode_toggle_time
    is_auto_mode = not is_auto_mode
    last_mode_toggle_time = time.time()
    update_oled_display()


def button_next_callback(channel):
    global current_command_index, need_update, last_mode_toggle_time, is_executing, is_button_pressed
    global last_time_button_next_pressed, last_time_button_execute_pressed

    current_time = time.time()
    is_button_pressed = True

    if is_executing or (current_time - last_mode_toggle_time < 10):  # 모드 전환 후 일정 시간 동안는 입력 무시
        is_button_pressed = False
        return

    # EXECUTE 버튼이 최근에 눌렸는지 확인
    if current_time - last_time_button_execute_pressed < button_press_interval:
        toggle_mode()  # 모드 전환
        need_update = True
    else:
        if commands:  # 명령 목록이 비어있지 않을 때만 인덱스 변경
            current_command_index = (current_command_index + 1) % len(commands)
            need_update = True

    last_time_button_next_pressed = current_time  # NEXT 버튼 눌린 시간 갱신
    is_button_pressed = False


def button_execute_callback(channel):
    global current_command_index, need_update, last_mode_toggle_time, is_executing, is_button_pressed
    global last_time_button_next_pressed, last_time_button_execute_pressed

    current_time = time.time()
    is_button_pressed = True

    if is_executing or (current_time - last_mode_toggle_time < 10):  # 모드 전환 후 일정 시간 동안는 입력 무시
        is_button_pressed = False
        return

    # NEXT 버튼이 최근에 눌렸는지 확인
    if current_time - last_time_button_next_pressed < button_press_interval:
        toggle_mode()  # 모드 전환
        need_update = True
    else:
        # EXECUTE 버튼만 눌렸을 때의 로직
        if not is_auto_mode:
            # 수동 모드에서는 현재 메뉴 항목을 실행 (dir/back/system/bin 모두 포함)
            if commands:
                print("[MANUAL] EXECUTE on index", current_command_index,
                      "type:", command_types[current_command_index])
                execute_command(current_command_index)
                need_update = True
        else:
            # 자동 모드
            with display_lock:
                if not commands:
                    is_button_pressed = False
                    return

                item_type = command_types[current_command_index]
                print("[AUTO] EXECUTE on index", current_command_index,
                      "type:", item_type)

                # 자동 모드에서도 폴더/이전/시스템은 선택(실행) 가능하게
                if item_type in ("system", "dir", "back"):
                    execute_command(current_command_index)
                else:
                    # bin 타입일 때는 기존처럼 한 칸 위로 이동
                    # (실제 실행은 메인 루프에서 자동으로)
                    current_command_index = (current_command_index - 1) % len(commands)

                need_update = True

    last_time_button_execute_pressed = current_time  # EXECUTE 버튼 눌린 시간 갱신
    is_button_pressed = False


# (주의) 위에서 한 번 정의했지만, 아래 정의가 최종으로 사용됨
def toggle_mode():
    global is_auto_mode
    is_auto_mode = not is_auto_mode
    update_oled_display()  # OLED 화면 업데이트


# 자동 모드와 수동 모드 아이콘 대신 문자열 사용
auto_mode_text = 'A'
manual_mode_text = 'M'

# GPIO 설정
GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.FALLING, callback=button_next_callback, bouncetime=800)
GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING, callback=button_execute_callback, bouncetime=800)
GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)
GPIO.setup(LED_ERROR1, GPIO.OUT)

# 연결 상태를 추적하기 위한 변수
connection_success = False
connection_failed_since_last_success = False


def check_stm32_connection():
    with display_lock:
        global connection_success, connection_failed_since_last_success, is_command_executing
        if is_command_executing:  # 명령 실행 중에는 STM32 연결 확인을 하지 않음
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
                    connection_failed_since_last_success = False  # 성공 후 실패 플래그 초기화
                else:
                    print("STM32 연결 성공")
                    connection_success = False  # 연속적인 성공을 방지
                return True
            else:
                print("STM32 연결 실패:", result.stderr)
                connection_failed_since_last_success = True  # 실패 플래그
                return False
        except Exception as e:
            print(f"오류 발생: {e}")
            connection_failed_since_last_success = True  # 실패 플래그 설정
            return False


# 배터리 상태 확인 함수
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
    except Exception as e:
        # 예외 발생 시 로그 남기기
        print("INA219 모듈 읽기 실패:", str(e))
        return -1


# OLED 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

# 폰트 및 이미지 설정
font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'
font_big = ImageFont.truetype(font_path, 12)
font_s = ImageFont.truetype(font_path, 13)
font_st = ImageFont.truetype(font_path, 11)
font = ImageFont.truetype(font_path, 17)
font_status = ImageFont.truetype(font_path, 13)
font_1 = ImageFont.truetype(font_path, 21)   # 일반 메뉴(펌웨어 .bin)용
font_sysupdate = ImageFont.truetype(font_path, 17)  # 🔥 시스템 업데이트 전용 더 작은 폰트
font_time = ImageFont.truetype(font_path, 12)

# 배터리 아이콘 로드
low_battery_icon = Image.open("/home/user/stm32/img/bat.png")
medium_battery_icon = Image.open("/home/user/stm32/img/bat.png")
high_battery_icon = Image.open("/home/user/stm32/img/bat.png")
full_battery_icon = Image.open("/home/user/stm32/img/bat.png")


# 배터리 아이콘 선택 함수
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
        raw = name  # 예: '2.HMDS' → 그대로 사용
    else:
        # 파일의 경우 .bin 제거 후 사용
        raw = os.path.splitext(name)[0]  # '1.ORG.bin' → '1.ORG'

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
    entries = []  # (order, type_pri, display, type, extra) 로 정렬용 튜플 저장

    try:
        for fname in os.listdir(dir_path):
            full_path = os.path.join(dir_path, fname)

            # 1) 디렉토리인 경우
            if os.path.isdir(full_path):
                order, display_name = parse_order_and_name(fname, is_dir=True)
                # 폴더 아이콘/표시 (이모지 안 나오면 나중에 '▶ ' 로 바꿔도 됨)
                display_name = "📁 " + display_name
                # type_pri = 0 (폴더)
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
                # type_pri = 1 (bin)
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

status_message = ""
message_position = (0, 0)
message_font_size = 17


def git_pull():
    shell_script_path = '/home/user/stm32/git-pull.sh'
    if not os.path.isfile(shell_script_path):
        with open(shell_script_path, 'w') as script_file:
            script_file.write("#!/bin/bash\n")
            script_file.write("cd /home/user/stm32\n")
            script_file.write("git remote update\n")  # 원격 저장소 정보 업데이트
            script_file.write("if git status -uno | grep -q 'Your branch is up to date'; then\n")
            script_file.write("   echo '이미 최신 상태입니다.'\n")
            script_file.write("   exit 0\n")
            script_file.write("fi\n")
            script_file.write("git stash\n")  # 임시로 변경사항을 저장
            script_file.write("git pull\n")  # 원격 저장소의 변경사항을 가져옴
            script_file.write("git stash pop\n")  # 저장했던 변경사항을 다시 적용
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


def display_progress_and_message(percentage, message, message_position=(0, 0), font_size=17):
    with canvas(device) as draw:
        # 메시지 표시
        draw.text(message_position, message, font=font, fill=255)

        # 진행 상태 바 표시
        draw.rectangle([(10, 50), (110, 60)], outline="white", fill="black")  # 상태 바의 외곽선
        draw.rectangle([(10, 50), (10 + percentage, 60)], outline="white", fill="white")  # 상태 바의 내용


def unlock_memory():
    with display_lock:
        print("메모리 해제 시도...")

    # '메모리 잠금' 및 '해제 중' 메시지와 함께 초기 진행 상태 바 표시
    display_progress_and_message(0, "메모리 잠금\n   해제 중", message_position=(18, 0), font_size=15)

    # 메모리 잠금 해제 로직 구현...
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
        update_oled_display()
        return False


def restart_script():
    print("스크립트를 재시작합니다.")
    display_progress_and_message(25, "재시작 중", message_position=(20, 10), font_size=15)

    def restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=restart).start()


def lock_memory_procedure():
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
            print("메모리 잠금에 실패했습니다. 오류 코드:", result.returncode)
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            display_progress_and_message(0, "메모리 잠금\n    실패", message_position=(20, 0), font_size=15)
            time.sleep(1)
            update_oled_display()
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        update_oled_display()
        display_progress_and_message(0, "오류 발생")
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)


def execute_command(command_index):
    global is_executing, is_command_executing
    global current_menu, commands, command_names, command_types, menu_extras
    global current_command_index, menu_stack

    is_executing = True  # 작업 시작 전에 상태를 실행 중으로 설정
    is_command_executing = True  # 명령 실행 중 상태 활성화

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
            # 현재 메뉴를 스택에 저장
            menu_stack.append(current_menu)

            # 하위 디렉토리 메뉴 생성
            current_menu = build_menu_for_dir(subdir, is_root=False)
            commands = current_menu["commands"]
            command_names = current_menu["names"]
            command_types = current_menu["types"]
            menu_extras = current_menu["extras"]
            current_command_index = 0
            update_oled_display()

        is_executing = False
        is_command_executing = False
        return

    # 2) 이전으로 (back)
    if item_type == "back":
        if menu_stack:
            current_menu = menu_stack.pop()
            commands = current_menu["commands"]
            command_names = current_menu["names"]
            command_types = current_menu["types"]
            menu_extras = current_menu["extras"]
            current_command_index = 0
            update_oled_display()

        is_executing = False
        is_command_executing = False
        return

    # 3) 시스템 업데이트
    if item_type == "system":
        git_pull()
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

    is_executing = False
    is_command_executing = False


def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        return "0.0.0.0"


def update_oled_display():
    global current_command_index, status_message, message_position, message_font_size, is_button_pressed
    with display_lock:  # 스레드 간 충돌 방지를 위해 display_lock 사용
        if is_button_pressed:
            return  # 버튼 입력 모드에서는 화면 업데이트 무시

        if not commands:
            return

        ip_address = get_ip_address()
        now = datetime.now()
        current_time = now.strftime('%H시 %M분')
        voltage_percentage = read_ina219_percentage()

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
                battery_icon = select_battery_icon(voltage_percentage)
                draw.bitmap((90, -9), battery_icon, fill=255)
                draw.text((99, 3), f"{voltage_percentage:.0f}%", font=font_st, fill=255)
                draw.text((27, 1), current_time, font=font_time, fill=255)
            else:
                # IP 주소가 "0.0.0.0"이면 "연결 없음"으로 표시
                if ip_address == "0.0.0.0":
                    ip_display = "연결 없음"
                else:
                    ip_display = ip_address
                draw.text((0, 51), ip_display, font=font_big, fill=255)
                draw.text((80, -3), 'GDSENG', font=font_big, fill=255)
                draw.text((83, 50), 'ver 3.56', font=font_big, fill=255)
                draw.text((0, -3), current_time, font=font_time, fill=255)

            # 상태 메시지가 있을 때 전체 메시지 화면
            if status_message:
                draw.rectangle(device.bounding_box, outline="white", fill="black")
                font_custom = ImageFont.truetype(font_path, message_font_size)
                draw.text(message_position, status_message, font=font_custom, fill=255)
            else:
                # ✅ 메뉴 이름을 가운데 정렬로 표시 (anchor="mm" 사용)
                center_x = device.width // 2 + VISUAL_X_OFFSET

                # 시스템 업데이트만 약간 위로 + 작은 폰트
                if item_type == "system":
                    center_y = 33  # 🔥 업데이트만 위로
                    use_font = font_sysupdate
                else:
                    center_y = 42  # 일반 메뉴는 조금 아래로 중앙 근처
                    use_font = font_1

                try:
                    # Pillow에서 anchor 지원될 때
                    draw.text((center_x, center_y), title, font=use_font, fill=255, anchor="mm")
                except TypeError:
                    # anchor 없으면 수동으로 중앙 계산
                    try:
                        w, h = draw.textsize(title, font=use_font)
                    except Exception:
                        w, h = (len(title) * 8, 16)
                    x = int(center_x - w / 2)
                    y = int(center_y - h / 2)
                    draw.text((x, y), title, font=use_font, fill=255)


# 실시간 업데이트를 위한 스레드 함수
def realtime_update_display():
    global is_command_executing
    while True:
        if not is_button_pressed and not is_command_executing:
            update_oled_display()
        time.sleep(1)


# 스레드 생성 및 시작
realtime_update_thread = threading.Thread(target=realtime_update_display)
realtime_update_thread.daemon = True
realtime_update_thread.start()


def shutdown_system():
    try:
        with canvas(device) as draw:
            draw.text((20, 25), "배터리 부족", font=font, fill=255)
            draw.text((25, 50), "시스템 종료 중...", font=font_st, fill=255)
        time.sleep(5)
        # DISPLAY_POWER_PIN 정의되어 있으면 사용, 아니면 제거하거나 주석 처리
        # GPIO.output(DISPLAY_POWER_PIN, GPIO.LOW)
        os.system('sudo shutdown -h now')
    except Exception as e:
        # 예외 발생 시 로그 남기기
        print("시스템 종료 중 오류 발생:", str(e))


# 초기 디스플레이 업데이트
update_oled_display()

# 메인 루프
try:
    while True:
        # 배터리 수준을 확인하고 0%면 시스템 종료
        if read_ina219_percentage() == 0:
            print("배터리 수준이 0%입니다. 시스템을 종료합니다.")
            shutdown_system()

        # STM32 연결 상태 확인 및 명령 실행
        if commands:
            # 자동 모드에서 bin 타입만 자동 실행
            if is_auto_mode and command_types[current_command_index] == "bin" \
               and check_stm32_connection() and connection_success:
                execute_command(current_command_index)

        # OLED 디스플레이 업데이트
        if need_update:
            update_oled_display()
            need_update = False

        time.sleep(0.03)
except KeyboardInterrupt:
    GPIO.cleanup()
