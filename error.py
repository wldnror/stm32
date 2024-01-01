import os
import RPi.GPIO as GPIO
import subprocess
import time
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1107

# GPIO 핀 설정
BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17

# OLED 디스플레이 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)
font = ImageFont.truetype('/usr/share/fonts/truetype/malgun/malgunbd.ttf', 17)

# 메뉴 옵션 설정
menu_options = ["업데이트 재시도", "기존 상태로 복구"]
current_menu_index = 0

def display_menu():
    with canvas(device) as draw:
        draw.text((10, 20), menu_options[current_menu_index], font=font, fill=255)

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
