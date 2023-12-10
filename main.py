import RPi.GPIO as GPIO
import time
import os
import sys
import subprocess
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1107

# GPIO 핀 설정
BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17
LED_DEBUGGING = 23
LED_SUCCESS = 24
LED_ERROR = 25

# 자동 모드와 수동 모드 상태를 추적하는 전역 변수
is_auto_mode = True

# GPIO 핀 번호 모드 설정
GPIO.setmode(GPIO.BCM)

# GPIO 설정
GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(LED_DEBUGGING, GPIO.OUT)
GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)

# OLED 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

# 폰트 및 이미지 설정
font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'
font = ImageFont.truetype(font_path, 17)

# 자동 모드와 수동 모드 아이콘 로드
auto_mode_icon = Image.open("/home/user/stm32/img/A.png")
manual_mode_icon = Image.open("/home/user/stm32/img/X.png")

# 명령어 설정
commands = [
    "sudo openocd ...",  # 예시 명령어
    "sudo openocd ...",  # 예시 명령어
    "git_pull",  # 이 함수는 나중에 execute_command 함수에서 호출됩니다.
]

command_names = ["ASGD S", "ASGD S PNP", "시스템 업데이트"]
current_command_index = 0
status_message = ""

# 명령어 실행 및 기타 함수들은 여기에 추가됩니다.

def update_oled_display():
    # OLED 업데이트 로직을 여기에 추가합니다.

    with canvas(device) as draw:
        # 예시: 디스플레이에 "Hello, World!" 표시
        draw.text((10, 20), "Hello, World!", font=font, fill="white")

try:
    while True:
        # 버튼 처리 및 OLED 업데이트 로직을 여기에 추가합니다.

        # OLED 디스플레이 업데이트
        update_oled_display()

        # 짧은 지연
        time.sleep(0.1)

except KeyboardInterrupt:
    GPIO.cleanup()
