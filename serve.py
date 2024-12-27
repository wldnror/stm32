#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import socket
import subprocess
import threading
from datetime import datetime

import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1107
from ina219 import INA219, DeviceRangeError

# -----------------------------------------------------
# 전역 상수/설정값
# -----------------------------------------------------

SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1    # 배터리 최소 전압
MAX_VOLTAGE = 4.2    # 배터리 최대 전압 (완충)

DISPLAY_POWER_PIN = 26  # 예시로 디스플레이 전원 핀 할당
BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 22

LED_SUCCESS = 24
LED_ERROR = 25
LED_ERROR1 = 23

# 모드 토글 이후 버튼 무시 시간 (초)
MODE_TOGGLE_DEBOUNCE = 0.3

# 버튼 두 번 눌림(동시 입력)으로 판단할 최대 시간 간격(초)
button_press_interval = 0.5

# -----------------------------------------------------
# 전역 변수
# -----------------------------------------------------

is_auto_mode = True
is_executing = False           # 현재 명령 실행 중인지
is_command_executing = False   # STM32 연결 체크 등과 겹치지 않도록
is_button_pressed = False
need_update = False

last_mode_toggle_time = 0      # 마지막 모드 전환 시각
last_time_button_next_pressed = 0
last_time_button_execute_pressed = 0

connection_success = False
connection_failed_since_last_success = False

# 디스플레이/폰트 관련
display_lock = threading.Lock()

# -----------------------------------------------------
# 명령어 리스트 (예시)
# -----------------------------------------------------
# 실제 프로젝트에서는 꼭 필요한 명령들만 알맞게 구성하세요.
commands = [
    # 0
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
    "-c \"program /home/user/stm32/Program/ORG.bin verify reset exit 0x08000000\"",

    # 1
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
    "-c \"program /home/user/stm32/Program/HMDS.bin verify reset exit 0x08000000\"",

    # 2
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
    "-c \"program /home/user/stm32/Program/HMDS-IR.bin verify reset exit 0x08000000\"",

    # 3
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
    "-c \"program /home/user/stm32/Program/ARF-T.bin verify reset exit 0x08000000\"",

    # 4
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
    "-c \"program /home/user/stm32/Program/HC100.bin verify reset exit 0x08000000\"",

    # 5
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
    "-c \"program /home/user/stm32/Program/SAT4010.bin verify reset exit 0x08000000\"",

    # 6
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
    "-c \"program /home/user/stm32/Program/IPA.bin verify reset exit 0x08000000\"",

    # 7
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
    "-c \"program /home/user/stm32/Program/TEST.bin verify reset exit 0x08000000\"",

    # 8 : Git pull 전용 (시스템 업데이트)
    "git_pull"
]

command_names = [
    "ORG","HMDS","HMDS-IR","ARF-T","HC100",
    "SAT4010","IPA","TEST","시스템 업데이트"
]

current_command_index = 0
status_message = ""
message_position = (0, 0)
message_font_size = 17

# -----------------------------------------------------
# GPIO 초기화
# -----------------------------------------------------
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)
GPIO.setup(LED_ERROR1, GPIO.OUT)
GPIO.setup(DISPLAY_POWER_PIN, GPIO.OUT)  # 예: 디스플레이 전원 핀

GPIO.output(DISPLAY_POWER_PIN, GPIO.HIGH)  # 디스플레이 켜두기

# -----------------------------------------------------
# OLED 디스플레이 초기 설정
# -----------------------------------------------------
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

font_path = "/usr/share/fonts/truetype/malgun/malgunbd.ttf"
font_big = ImageFont.truetype(font_path, 12)
font_s = ImageFont.truetype(font_path, 13)
font_st = ImageFont.truetype(font_path, 11)
font = ImageFont.truetype(font_path, 17)
font_status = ImageFont.truetype(font_path, 13)
font_1 = ImageFont.truetype(font_path, 21)
font_time = ImageFont.truetype(font_path, 12)

# -----------------------------------------------------
# 배터리 아이콘 (예시는 동일 아이콘 4개 로드)
# 실제로는 용량별 아이콘 배치를 달리해야 함
# -----------------------------------------------------
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

# -----------------------------------------------------
# 배터리 퍼센트 읽기 (INA219)
# -----------------------------------------------------
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

# -----------------------------------------------------
# 모드 토글
# -----------------------------------------------------
def toggle_mode():
    """
    모드를 자동/수동으로 전환하고,
    마지막 모드 전환 시간을 갱신한다.
    """
    global is_auto_mode, last_mode_toggle_time
    is_auto_mode = not is_auto_mode
    last_mode_toggle_time = time.time()
    update_oled_display()

# -----------------------------------------------------
# 버튼 콜백들
# -----------------------------------------------------
def button_next_callback(channel):
    global current_command_index, need_update
    global is_executing, is_button_pressed
    global last_time_button_next_pressed, last_time_button_execute_pressed

    current_time = time.time()
    is_button_pressed = True

    # 모드 전환 막힌 상태(명령 실행 중이거나, 모드 전환 후 일정 시간 이내)
    if is_executing or (current_time - last_mode_toggle_time < MODE_TOGGLE_DEBOUNCE):
        is_button_pressed = False
        return

    # EXECUTE 버튼과 "동시에" 눌렸는지(실제로는 짧은 간격)
    if current_time - last_time_button_execute_pressed < button_press_interval:
        toggle_mode()
        need_update = True
    else:
        # NEXT 버튼만 눌렸을 때
        current_command_index = (current_command_index + 1) % len(commands)
        need_update = True

    last_time_button_next_pressed = current_time
    is_button_pressed = False

def button_execute_callback(channel):
    global current_command_index, need_update
    global is_executing, is_button_pressed
    global last_time_button_next_pressed, last_time_button_execute_pressed

    current_time = time.time()
    is_button_pressed = True

    if is_executing or (current_time - last_mode_toggle_time < MODE_TOGGLE_DEBOUNCE):
        is_button_pressed = False
        return

    # NEXT 버튼과 "동시에" 눌렸는지 판별
    if current_time - last_time_button_next_pressed < button_press_interval:
        toggle_mode()
        need_update = True
    else:
        # EXECUTE 버튼만 단독으로 눌렀을 때
        if not is_auto_mode:
            # 수동 모드일 경우 해당 인덱스 명령 즉시 실행
            execute_command(current_command_index)
            need_update = True
        else:
            # 자동 모드일 경우
            with display_lock:
                if current_command_index == command_names.index("시스템 업데이트"):
                    execute_command(current_command_index)
                else:
                    # 원 코드 로직: auto_mode일 때는 "현재 커맨드를 -1"만 한다는 부분이 있었으나
                    # 사실상 무슨 의도인지 불명확 -> 필요에 따라 맞춰서 수정
                    current_command_index = (current_command_index - 1) % len(commands)
            need_update = True

    last_time_button_execute_pressed = current_time
    is_button_pressed = False

# -----------------------------------------------------
# GPIO 이벤트 설정 (디바운스 300~800ms 정도는 사용 환경에 따라 조절)
# -----------------------------------------------------
GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.FALLING,
                      callback=button_next_callback, bouncetime=300)
GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING,
                      callback=button_execute_callback, bouncetime=300)

# -----------------------------------------------------
# STM32 연결 체크
# -----------------------------------------------------
def check_stm32_connection():
    """
    openocd 명령을 통해 STM32 연결을 간단히 확인.
    """
    global connection_success, connection_failed_since_last_success, is_command_executing

    with display_lock:
        if is_command_executing:
            # 명령 실행 중이면 연결 체크 생략
            return False

    try:
        command = [
            "sudo", "openocd",
            "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
            "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
            "-c", "init",
            "-c", "exit"
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

        if result.returncode == 0:
            print("STM32 연결 성공")
            connection_success = True
            connection_failed_since_last_success = False
            return True
        else:
            print("STM32 연결 실패:", result.stderr)
            connection_failed_since_last_success = True
            connection_success = False
            return False
    except Exception as e:
        print("STM32 연결 체크 중 오류:", e)
        connection_failed_since_last_success = True
        connection_success = False
        return False

# -----------------------------------------------------
# Git pull
# -----------------------------------------------------
def git_pull():
    """
    git-pull 전용 쉘 스크립트 없으면 만들어서 실행.
    """
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
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

        if result.returncode == 0:
            if "이미 최신 상태" in result.stdout:
                display_progress_and_message(100, "이미 최신 상태",
                                             message_position=(10, 10), font_size=15)
                time.sleep(1)
            else:
                print("업데이트 성공!")
                GPIO.output(LED_SUCCESS, True)
                display_progress_and_message(100, "업데이트 성공!",
                                             message_position=(10, 10), font_size=15)
                time.sleep(1)
                GPIO.output(LED_SUCCESS, False)
                restart_script()
        else:
            print("GitHub 업데이트 실패. 오류 코드:", result.returncode)
            print("오류 메시지:", result.stderr)
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            display_progress_and_message(0, "명령 실행 중 오류 발생",
                                         message_position=(0, 10), font_size=15)
            time.sleep(1)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0, "명령 실행 중 오류 발생",
                                     message_position=(0, 10), font_size=15)
        time.sleep(1)
    finally:
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

# -----------------------------------------------------
# 스크립트 재시작
# -----------------------------------------------------
def restart_script():
    print("스크립트를 재시작합니다.")
    display_progress_and_message(25, "재시작 중", message_position=(20, 10), font_size=15)

    def restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=restart).start()

# -----------------------------------------------------
# 디스플레이에 진행 바 + 메시지 표시
# -----------------------------------------------------
def display_progress_and_message(percentage, message,
                                 message_position=(0, 0),
                                 font_size=17):
    with canvas(device) as draw:
        font_custom = ImageFont.truetype(font_path, font_size)
        draw.text(message_position, message, font=font_custom, fill=255)

        # 진행 상태 바
        draw.rectangle([(10, 50), (110, 60)], outline="white", fill="black")
        # 현재 진행 퍼센트만큼 채움
        bar_width = int((110 - 10) * (percentage / 100.0))
        draw.rectangle([(10, 50), (10 + bar_width, 60)], outline="white", fill="white")

# -----------------------------------------------------
# 메모리 잠금 해제
# -----------------------------------------------------
def unlock_memory():
    with display_lock:
        print("메모리 해제 시도...")

    display_progress_and_message(0, "메모리 잠금\n   해제 중", (18, 0), 15)

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
        display_progress_and_message(30, "메모리 잠금\n 해제 성공!", (20, 0), 15)
        time.sleep(1)
        return True
    else:
        display_progress_and_message(0, "메모리 잠금\n 해제 실패!", (20, 0), 15)
        time.sleep(1)
        update_oled_display()
        return False

# -----------------------------------------------------
# 메모리 잠금
# -----------------------------------------------------
def lock_memory_procedure():
    display_progress_and_message(80, "메모리 잠금 중", (3, 10), 15)
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
        result = subprocess.run(openocd_command,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True)
        if result.returncode == 0:
            print("성공적으로 메모리를 잠갔습니다.")
            GPIO.output(LED_SUCCESS, True)
            display_progress_and_message(100, "메모리 잠금\n    성공", (20, 0), 15)
            time.sleep(1)
            GPIO.output(LED_SUCCESS, False)
        else:
            print("메모리 잠금에 실패했습니다. 오류 코드:", result.returncode)
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            display_progress_and_message(0, "메모리 잠금\n    실패", (20, 0), 15)
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

# -----------------------------------------------------
# 명령 실행 로직
# -----------------------------------------------------
def execute_command(command_index):
    global is_executing, is_command_executing
    is_executing = True
    is_command_executing = True

    print("명령 실행 시도:", command_names[command_index])

    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)

    # 만약 '시스템 업데이트' (git_pull)라면
    if command_names[command_index] == "시스템 업데이트":
        git_pull()
        is_executing = False
        is_command_executing = False
        return

    # 1) 메모리 잠금 해제
    if not unlock_memory():
        # 해제 실패
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

    # 2) 실제 펌웨어 업데이트(혹은 프로그래밍) 실행
    display_progress_and_message(30, "업데이트 중...", (12, 10), 15)
    process = subprocess.Popen(commands[command_index], shell=True)

    start_time = time.time()
    max_duration = 6.0
    progress_increment = 20.0 / max_duration  # 30~50 구간 차지

    while process.poll() is None:
        elapsed = time.time() - start_time
        current_progress = 30 + (elapsed * progress_increment)
        current_progress = min(current_progress, 80)
        display_progress_and_message(current_progress,
                                     "업데이트 중...", (12, 10), 15)
        time.sleep(0.5)

    result = process.returncode
    if result == 0:
        print(f"'{command_names[command_index]}' 업데이트 성공!")
        display_progress_and_message(80, "업데이트 성공!", (7, 10), 15)
        time.sleep(0.5)
        # 3) 메모리 잠금
        lock_memory_procedure()
    else:
        print(f"'{command_names[command_index]}' 업데이트 실패!")
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0, "업데이트 실패", (7, 10), 15)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

    is_executing = False
    is_command_executing = False

# -----------------------------------------------------
# IP 주소 얻기
# -----------------------------------------------------
def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        return "0.0.0.0"

# -----------------------------------------------------
# OLED 디스플레이 업데이트
# -----------------------------------------------------
def update_oled_display():
    global current_command_index, status_message
    global message_position, message_font_size, is_button_pressed

    with display_lock:
        if is_button_pressed:
            return  # 버튼 누르는 순간에는 업데이트 무시(충돌 방지)

        ip_address = get_ip_address()
        now = datetime.now()
        current_time = now.strftime('%H시 %M분')
        voltage_percentage = read_ina219_percentage()

        with canvas(device) as draw:
            # 좌측 상단에 자동/수동 모드 표시 (동그라미 안에 A/M)
            mode_char = 'A' if is_auto_mode else 'M'
            # 동그라미
            outer_ellipse_box = (2, 0, 22, 20)
            draw.ellipse(outer_ellipse_box, outline="white", fill=None)
            if mode_char == 'A':
                draw.text((8, -3), mode_char, font=font, fill=255)
            else:
                draw.text((5, -3), mode_char, font=font, fill=255)

            # 현재 선택된 명령 이름
            cmd_name = command_names[current_command_index]

            # 배터리 표시/시간/IP
            if cmd_name != "시스템 업데이트":
                battery_icon = select_battery_icon(voltage_percentage)
                draw.bitmap((90, -9), battery_icon, fill=255)
                draw.text((99, 3), f"{voltage_percentage}%", font=font_st, fill=255)
                draw.text((27, 1), current_time, font=font_time, fill=255)
            else:
                draw.text((0, 51), ip_address, font=font_big, fill=255)
                draw.text((80, -3), 'GDSENG', font=font_big, fill=255)
                draw.text((83, 50), 'ver 3.54', font=font_big, fill=255)
                draw.text((0, -3), current_time, font=font_time, fill=255)

            # status_message가 있을 경우 화면에 문구 표시
            if status_message:
                draw.rectangle(device.bounding_box, outline="white", fill="black")
                font_custom = ImageFont.truetype(font_path, message_font_size)
                draw.text(message_position, status_message, font=font_custom, fill=255)
            else:
                # 명령 이름에 맞춰 중앙에 출력
                if cmd_name in ["ORG","HMDS","HMDS-IR","ARF-T",
                                "HC100","SAT4010","IPA","TEST"]:
                    draw.text((40, 27), cmd_name, font=font_1, fill=255)
                else:
                    # 시스템 업데이트
                    draw.text((1, 20), '시스템 업데이트', font=font, fill=255)

# -----------------------------------------------------
# 주기적으로 OLED 업데이트하는 스레드
# -----------------------------------------------------
def realtime_update_display():
    global is_command_executing
    while True:
        if (not is_button_pressed) and (not is_command_executing):
            update_oled_display()
        time.sleep(1)

realtime_update_thread = threading.Thread(target=realtime_update_display)
realtime_update_thread.daemon = True
realtime_update_thread.start()

# -----------------------------------------------------
# 배터리 부족 시 시스템 종료
# -----------------------------------------------------
def shutdown_system():
    try:
        with canvas(device) as draw:
            draw.text((20, 25), "배터리 부족", font=font, fill=255)
            draw.text((10, 45), "시스템 종료 중...", font=font_st, fill=255)
        time.sleep(5)

        # 디스플레이 전원 끄기
        GPIO.output(DISPLAY_POWER_PIN, GPIO.LOW)
        os.system('sudo shutdown -h now')
    except Exception as e:
        print("시스템 종료 중 오류 발생:", str(e))

# -----------------------------------------------------
# 메인
# -----------------------------------------------------
def main():
    update_oled_display()  # 초기 디스플레이 갱신

    try:
        while True:
            # 배터리 0%면 시스템 종료
            if read_ina219_percentage() == 0:
                print("배터리 수준 0% → 시스템 종료")
                shutdown_system()

            # 자동 모드 & STM32 연결 성공 시 → 명령 자동 실행
            if is_auto_mode:
                if check_stm32_connection() and connection_success:
                    execute_command(current_command_index)

            if need_update:
                update_oled_display()
                need_update = False

            time.sleep(0.03)
    except KeyboardInterrupt:
        print("사용자 종료")
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main()
