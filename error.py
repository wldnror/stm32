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

# GPIO 핀 설정
BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17

# INA219 설정
SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1
MAX_VOLTAGE = 4.2

# 화면 업데이트 제어를 위한 전역 변수
updating_display = True

# OLED 디스플레이 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

# 폰트 경로 및 폰트 객체 생성
font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'
font_big = ImageFont.truetype(font_path, 15)
font_small = ImageFont.truetype(font_path, 10)

# 메뉴 옵션 설정
menu_options = ["업데이트 재시도", "기존 상태로 복구"]
current_menu_index = 0

# 배터리 상태 읽기 함수
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
    except DeviceRangeError:
        return 0

# IP 주소 얻기 함수
def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "0.0.0.0"

# 현재 시간 얻기 함수
def get_current_time():
    return datetime.now().strftime('%H:%M:%S')

# 메시지 표시 함수
def display_message(message):
    with canvas(device) as draw:
        draw.text((10, 20), message, font=font_big, fill=255)

def display_menu():
    global updating_display
    if updating_display:
        battery_percentage = read_ina219_percentage()
        ip_address = get_ip_address()
        current_time = get_current_time()

        with canvas(device) as draw:
            draw.text((10, 0), menu_options[current_menu_index], font=font_big, fill=255)
            draw.text((10, 30), f"Battery: {battery_percentage}%", font=font_small, fill=255)
            draw.text((10, 40), f"IP: {ip_address}", font=font_small, fill=255)
            draw.text((10, 50), f"Time: {current_time}", font=font_small, fill=255)

def button_next_callback(channel):
    global current_menu_index, updating_display
    updating_display = False
    current_menu_index = (current_menu_index + 1) % len(menu_options)
    display_menu()
    updating_display = True

def button_execute_callback(channel):
    global updating_display
    updating_display = False
    if menu_options[current_menu_index] == "업데이트 재시도":
        git_pull()
    elif menu_options[current_menu_index] == "기존 상태로 복구":
        recover_previous_state()
    updating_display = True

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
        draw.text((36, 8), "시스템", font=font_big, fill=255)  # 폰트 변경
        draw.text((17, 27), "업데이트 중", font=font_big, fill=255)  # 폰트 변경

    try:
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        
        if result.returncode == 0:
            if "이미 최신 상태" in result.stdout:
                display_message("이미 최신 상태")
                GPIO.output(LED_SUCCESS, True)
                time.sleep(1)
                GPIO.output(LED_SUCCESS, False)
            else:
                display_message("업데이트 성공!")
                GPIO.output(LED_SUCCESS, True)
                time.sleep(1)
                GPIO.output(LED_SUCCESS, False)
                # 스크립트 재시작 또는 다른 조치를 취할 수 있습니다.
        else:
            display_message("업데이트 실패")
            GPIO.output(LED_ERROR, True)
            time.sleep(1)
            GPIO.output(LED_ERROR, False)
    except Exception as e:
        display_message("오류 발생: " + str(e))
        GPIO.output(LED_ERROR, True)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)


def update_retry():
    display_message("업데이트 재시도...")
    time.sleep(2)
    git_pull()

def recover_previous_state():
    display_message("기존 상태로 복구...")
    time.sleep(2)
    # 복구 로직 구현

def update_display_every_second():
    while True:
        display_menu()
        time.sleep(1)

def main():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.FALLING, callback=button_next_callback, bouncetime=300)
    GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING, callback=button_execute_callback, bouncetime=300)

    update_thread = threading.Thread(target=update_display_every_second)
    update_thread.start()

    display_menu()

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        GPIO.cleanup()

if __name__ == "__main__":
    main()
