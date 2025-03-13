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

# 스레드 간 디스플레이 업데이트 충돌 방지를 위한 Lock
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

# GPIO 핀 번호 모드 설정 및 초기화
GPIO.setmode(GPIO.BCM)

# 버튼 마지막 입력 시간
last_time_button_next_pressed = 0
last_time_button_execute_pressed = 0
button_press_interval = 0.5  # 두 버튼이 동시에 눌린 것으로 간주되는 최대 시간 차이

need_update = False
is_command_executing = False
is_button_pressed = False

# 모드 전환 시각 추적
last_mode_toggle_time = 0

# 스크립트 실행 상태 변수
is_executing = False

# -------------------------------------------------------------------
# [네트워크 설정 모드 관련 전역 변수]
network_setup_mode = False       # 네트워크 설정 화면 활성화 여부
available_networks = []          # 스캔한 네트워크 목록 (SSID 리스트)
current_network_index = 0        # 선택 중인 네트워크 인덱스
password_entry_mode = False      # 패스워드 입력 모드 여부
password_input = ""              # 입력된 패스워드 문자열
character_set = "abcdefghijklmnopqrstuvwxyz0123456789@#!"
current_char_index = 0           # 패스워드 입력 시 선택된 문자 인덱스
# -------------------------------------------------------------------

def toggle_mode():
    global is_auto_mode, last_mode_toggle_time
    is_auto_mode = not is_auto_mode
    last_mode_toggle_time = time.time()
    update_oled_display()

def button_next_callback(channel):
    global current_command_index, need_update, last_mode_toggle_time, is_executing, is_button_pressed
    global last_time_button_next_pressed, last_time_button_execute_pressed
    global network_setup_mode, current_network_index, password_entry_mode, current_char_index

    current_time = time.time()
    is_button_pressed = True

    if is_executing or (current_time - last_mode_toggle_time < 10):
        is_button_pressed = False
        return

    # 네트워크 설정 모드일 경우
    if network_setup_mode:
        if password_entry_mode:
            # 패스워드 입력 시: NEXT 버튼을 누르면 character_set 내에서 다음 문자 선택
            current_char_index = (current_char_index + 1) % len(character_set)
            update_oled_display()  # 즉시 업데이트
        else:
            # 네트워크 목록에서 NEXT 버튼을 누르면 다음 네트워크 선택
            if available_networks:
                current_network_index = (current_network_index + 1) % len(available_networks)
                update_oled_display()  # 즉시 업데이트
    else:
        # 기존 버튼 동작: NEXT 버튼 단독 입력 시
        if current_time - last_time_button_execute_pressed < button_press_interval:
            toggle_mode()
            need_update = True
        else:
            current_command_index = (current_command_index + 1) % len(commands)
            need_update = True

    last_time_button_next_pressed = current_time
    is_button_pressed = False

def button_execute_callback(channel):
    global current_command_index, need_update, last_mode_toggle_time, is_executing, is_button_pressed
    global last_time_button_next_pressed, last_time_button_execute_pressed
    global network_setup_mode, password_entry_mode, password_input, current_char_index

    current_time = time.time()
    is_button_pressed = True

    if is_executing or (current_time - last_mode_toggle_time < 10):
        is_button_pressed = False
        return

    # 네트워크 설정 모드일 경우
    if network_setup_mode:
        if not password_entry_mode:
            # 네트워크 선택 완료: 보안 네트워크로 가정하여 패스워드 입력 모드 전환
            password_entry_mode = True
            password_input = ""
            current_char_index = 0
            update_oled_display()  # 즉시 업데이트
        else:
            # 패스워드 입력 모드: SET 버튼 누르면 현재 선택한 문자 추가
            password_input += character_set[current_char_index]
            update_oled_display()  # 즉시 업데이트
            # 예시로 8자리 이상 입력 시 연결 시도 (필요에 따라 조건 수정)
            if len(password_input) >= 8:
                connect_to_network(available_networks[current_network_index], password_input)
                # 연결 시도 후 네트워크 모드 초기화
                network_setup_mode = False
                password_entry_mode = False
                available_networks.clear()
        need_update = True
    else:
        # 기존 버튼 동작: EXECUTE 버튼 단독 입력 시
        if current_time - last_time_button_next_pressed < button_press_interval:
            toggle_mode()
            need_update = True
        else:
            if not is_auto_mode:
                execute_command(current_command_index)
                need_update = True
            else:
                with display_lock:
                    if current_command_index == command_names.index("시스템 업데이트"):
                        execute_command(current_command_index)
                    else:
                        if is_auto_mode:
                            current_command_index = (current_command_index - 1) % len(commands)
                        else:
                            execute_command(current_command_index)
                need_update = True

    last_time_button_execute_pressed = current_time
    is_button_pressed = False

# 모드 전환 함수
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

# STM32 연결 상태 관련 변수
connection_success = False
connection_failed_since_last_success = False

def check_stm32_connection():
    with display_lock:
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
                    connection_success = False
                return True
            else:
                print("STM32 연결 실패:", result.stderr)
                connection_failed_since_last_success = True
                return False
        except Exception as e:
            print(f"오류 발생: {e}")
            connection_failed_since_last_success = True
            return False

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
        print("INA219 모듈 읽기 실패:", str(e))
        return -1

# OLED 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

# 폰트 및 이미지 설정 (폰트 경로는 환경에 맞게 수정)
font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'
font_big = ImageFont.truetype(font_path, 12)
font_s = ImageFont.truetype(font_path, 13)
font_st = ImageFont.truetype(font_path, 11)
font = ImageFont.truetype(font_path, 17)
font_status = ImageFont.truetype(font_path, 13)
font_1 = ImageFont.truetype(font_path, 21)
font_time = ImageFont.truetype(font_path, 12)

# 배터리 아이콘 로드 (실제 파일 경로에 맞게 수정)
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

# 명령어 목록 (마지막 항목은 시스템 업데이트)
commands = [
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ORG.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HMDS.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HMDS-IR.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ARF-T.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HC100.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/SAT4010.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/IPA.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/V356.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/V356_PNP.bin verify reset exit 0x08000000\"",
    "git_pull",  # 시스템 업데이트 시 git_pull 함수 호출
]

command_names = ["ORG", "HMDS", "HMDS-IR", "ARF-T", "HC100", "SAT4010", "IPA", "V356", "V356_PNP", "시스템 업데이트"]

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

def restart_script():
    print("스크립트를 재시작합니다.")
    display_progress_and_message(25, "재시작 중", message_position=(20, 10), font_size=15)
    def restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart).start()

def display_progress_and_message(percentage, message, message_position=(0, 0), font_size=17):
    with canvas(device) as draw:
        draw.text(message_position, message, font=font, fill=255)
        draw.rectangle([(10, 50), (110, 60)], outline="white", fill="black")
        draw.rectangle([(10, 50), (10 + percentage, 60)], outline="white", fill="white")

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
            display_progress_and_message(100,"메모리 잠금\n    성공", message_position=(20, 0), font_size=15)
            time.sleep(1)
            GPIO.output(LED_SUCCESS, False)
        else:
            print("메모리 잠금에 실패했습니다. 오류 코드:", result.returncode)
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            display_progress_and_message(0,"메모리 잠금\n    실패", message_position=(20, 0), font_size=15)
            time.sleep(1)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        update_oled_display()
        display_progress_and_message(0,"오류 발생", message_position=(20, 0), font_size=15)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

def execute_command(command_index):
    global is_executing, is_command_executing
    is_executing = True
    is_command_executing = True

    print("업데이트 시도...")
    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)

    if command_index == len(commands) - 1:
        git_pull()
        is_executing = False
        is_command_executing = False
        return

    if command_index == 9:
        lock_memory_procedure()
        is_executing = False
        is_command_executing = False
        return
        
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

# 네트워크 스캔 시 SSID만 출력하도록 수정
def scan_networks():
    try:
        # -t: 탭으로 구분, -f: SSID 필드만 출력
        result = subprocess.run(["nmcli", "-t", "-f", "SSID", "device", "wifi", "list"], stdout=subprocess.PIPE, text=True)
        return parse_network_list(result.stdout)
    except Exception as e:
        print("네트워크 스캔 실패:", e)
        return []

def parse_network_list(scan_output):
    networks = []
    lines = scan_output.splitlines()
    for line in lines:
        ssid = line.strip()
        if ssid:  # 빈 문자열이 아니면 추가
            networks.append(ssid)
    return networks

def connect_to_network(ssid, password):
    try:
        # 기존 연결 삭제 (필요시)
        subprocess.run(["nmcli", "connection", "delete", ssid], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # 새 연결 시도
        result = subprocess.run(["nmcli", "device", "wifi", "connect", ssid, "password", password],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            print(f"{ssid} 연결 성공!")
            display_progress_and_message(100, "네트워크 연결 성공", message_position=(10, 10), font_size=15)
        else:
            print(f"{ssid} 연결 실패!")
            display_progress_and_message(0, "네트워크 연결 실패", message_position=(10, 10), font_size=15)
    except Exception as e:
        print("연결 시도 중 오류 발생:", e)
        display_progress_and_message(0, "연결 오류", message_position=(10, 10), font_size=15)

def update_oled_display():
    global current_command_index, status_message, message_position, message_font_size, is_button_pressed
    global network_setup_mode, available_networks, current_network_index, password_entry_mode, password_input, current_char_index

    with display_lock:
        if is_button_pressed:
            return

        ip_address = get_ip_address()
        now = datetime.now()
        current_time = now.strftime('%H시 %M분')
        voltage_percentage = read_ina219_percentage()

        # 시스템 업데이트 메뉴에서 IP가 0.0.0.0이면 네트워크 설정 모드 활성화
        if command_names[current_command_index] == "시스템 업데이트" and ip_address == "0.0.0.0":
            network_setup_mode = True
        else:
            network_setup_mode = False

        with canvas(device) as draw:
            if network_setup_mode:
                if not available_networks:
                    available_networks.extend(scan_networks())
                if not available_networks:
                    draw.text((0, 10), "네트워크 없음", font=font, fill=255)
                else:
                    draw.text((0, 0), "네트워크 선택", font=font, fill=255)
                    draw.text((0, 20), f"{available_networks[current_network_index]}", font=font, fill=255)
                    if password_entry_mode:
                        draw.text((0, 40), f"PW: {password_input}", font=font, fill=255)
                        draw.text((0, 60), f"현재: {character_set[current_char_index]}", font=font, fill=255)
                    else:
                        draw.text((0, 40), "SET: 선택  NEXT: 스크롤", font=font, fill=255)
            else:
                if command_names[current_command_index] != "시스템 업데이트":
                    mode_char = 'A' if is_auto_mode else 'M'
                    outer_ellipse_box = (2, 0, 22, 20)
                    text_position = {'A': (8, -3), 'M': (5, -3)}
                    draw.ellipse(outer_ellipse_box, outline="white", fill=None)
                    draw.text(text_position[mode_char], mode_char, font=font, fill=255)

                if command_names[current_command_index] in ["ORG", "HMDS", "HMDS-IR", "ARF-T", "HC100", "SAT4010", "IPA", "V356", "V356_PNP"]:
                    battery_icon = select_battery_icon(voltage_percentage)
                    draw.bitmap((90, -9), battery_icon, fill=255)
                    draw.text((99, 3), f"{voltage_percentage:.0f}%", font=font_st, fill=255)
                    draw.text((27, 1), current_time, font=font_time, fill=255)
                elif command_names[current_command_index] == "시스템 업데이트":
                    if ip_address == "0.0.0.0":
                        ip_display = "연결 없음"
                    else:
                        ip_display = ip_address
                    draw.text((0, 51), ip_display, font=font_big, fill=255)
                    draw.text((80, -3), 'GDSENG', font=font_big, fill=255)
                    draw.text((83, 50), 'ver 3.56', font=font_big, fill=255)
                    draw.text((0, -3), current_time, font=font_time, fill=255)

                if status_message:
                    draw.rectangle(device.bounding_box, outline="white", fill="black")
                    font_custom = ImageFont.truetype(font_path, message_font_size)
                    draw.text(message_position, status_message, font=font_custom, fill=255)
                else:
                    if command_names[current_command_index] == "ORG":
                        draw.text((42, 27), 'ORG', font=font_1, fill=255)
                    elif command_names[current_command_index] == "HMDS":
                        draw.text((33, 27), 'HMDS', font=font_1, fill=255)
                    elif command_names[current_command_index] == "HMDS-IR":
                        draw.text((20, 27), 'HMDS-IR', font=font_1, fill=255)
                    elif command_names[current_command_index] == "ARF-T":
                        draw.text((34, 27), 'ARF-T', font=font_1, fill=255)
                    elif command_names[current_command_index] == "HC100":
                        draw.text((32, 27), 'HC100', font=font_1, fill=255)
                    elif command_names[current_command_index] == "SAT4010":
                        draw.text((22, 27), 'SAT4010', font=font_1, fill=255)
                    elif command_names[current_command_index] == "IPA":
                        draw.text((46, 27), 'IPA', font=font_1, fill=255)
                    elif command_names[current_command_index] == "V356":
                        draw.text((33, 27), 'v356', font=font_1, fill=255)
                    elif command_names[current_command_index] == "V356_PNP":
                        draw.text((22, 27), 'v356_PNP', font=font_1, fill=255)
                    elif command_names[current_command_index] == "시스템 업데이트":
                        draw.text((1, 20), '시스템 업데이트', font=font, fill=255)

def realtime_update_display():
    global is_command_executing
    while True:
        if not is_button_pressed and not is_command_executing:
            update_oled_display()
        time.sleep(0.1)  # 갱신 주기를 0.1초로 단축

realtime_update_thread = threading.Thread(target=realtime_update_display)
realtime_update_thread.daemon = True
realtime_update_thread.start()

def shutdown_system():
    try:
        with canvas(device) as draw:
            draw.text((20, 25), "배터리 부족", font=font, fill=255)
            draw.text((25, 50), "시스템 종료 중...", font=font_st, fill=255)
        time.sleep(5)
        # 실제 DISPLAY 전원 핀 제어 필요시 수정
        os.system('sudo shutdown -h now')
    except Exception as e:
        print("시스템 종료 중 오류 발생:", str(e))

# 초기 디스플레이 업데이트
update_oled_display()

try:
    while True:
        # 배터리 0%면 시스템 종료
        if read_ina219_percentage() == 0:
            print("배터리 수준이 0%입니다. 시스템을 종료합니다.")
            shutdown_system()

        # STM32 연결 상태 확인 후 명령 실행 (시스템 업데이트 메뉴 제외)
        if command_names[current_command_index] != "시스템 업데이트":
            if is_auto_mode and check_stm32_connection() and connection_success:
                execute_command(current_command_index)

        if need_update:
            update_oled_display()
            need_update = False

        time.sleep(0.03)
except KeyboardInterrupt:
    GPIO.cleanup()
