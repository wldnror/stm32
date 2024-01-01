import os
import RPi.GPIO as GPIO
import time
import subprocess
import socket
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

# OLED 디스플레이 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)
font = ImageFont.truetype('/usr/share/fonts/truetype/malgun/malgunbd.ttf')

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
        draw.text((10, 20), message, font=font, fill=255)

def display_menu():
    # 배터리 정보, IP 주소, 현재 시간을 읽어옵니다.
    battery_percentage = read_ina219_percentage()
    ip_address = get_ip_address()
    current_time = get_current_time()

    with canvas(device) as draw:
        # 메뉴 옵션 및 배터리 상태, IP 주소, 현재 시간 표시
        draw.text((10, 0), menu_options[current_menu_index], font=font, font_size=15, fill=255)
        draw.text((10, 30), f"Battery: {battery_percentage}%", font=font,font_size=12, fill=255)
        draw.text((10, 40), f"IP: {ip_address}", font=font, font_size=12, fill=255)
        draw.text((10, 50), f"Time: {current_time}", font=font, font_size=12, fill=255)

def button_next_callback(channel):
    global current_menu_index
    current_menu_index = (current_menu_index + 1) % len(menu_options)
    display_menu()

def button_execute_callback(channel):
    if menu_options[current_menu_index] == "업데이트 재시도":
        git_pull()
    elif menu_options[current_menu_index] == "기존 상태로 복구":
        recover_previous_state()

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
    
    result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if result.returncode == 0:
        if "이미 최신 상태" in result.stdout:
            display_message("이미 최신 상태")
        else:
            display_message("업데이트 성공!")
    else:
        display_message("업데이트 실패")
    time.sleep(2)

def update_retry():
    display_message("업데이트 재시도...")
    time.sleep(2)
    git_pull()

def recover_previous_state():
    display_message("기존 상태로 복구...")
    time.sleep(2)
    # 복구 로직 구현

def main():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.FALLING, callback=button_next_callback, bouncetime=300)
    GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING, callback=button_execute_callback, bouncetime=300)

    display_menu()

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        GPIO.cleanup()

if __name__ == "__main__":
    main()
