import RPi.GPIO as GPIO
import subprocess
import time
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.oled.device import sh1107

# GPIO 핀 설정
BUTTON_PIN_RETRY = 17
BUTTON_PIN_RECOVER = 27

# OLED 디스플레이 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)
font = ImageFont.truetype('/usr/share/fonts/truetype/malgun/malgunbd.ttf', 17)

def display_message(message):
    with canvas(device) as draw:
        draw.text((10, 20), message, font=font, fill=255)

def update_retry():
    display_message("업데이트 재시도...")
    time.sleep(2)
    subprocess.run(["python3", "main.py"])

def recover_previous_state():
    display_message("기존 상태로 복구...")
    time.sleep(2)
    # 복구 로직 구현

def main():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN_RETRY, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BUTTON_PIN_RECOVER, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    display_message("1. 업데이트 재시도\n2. 기존 상태 복구")

    while True:
        if GPIO.input(BUTTON_PIN_RETRY) == GPIO.LOW:
            update_retry()
            break
        elif GPIO.input(BUTTON_PIN_RECOVER) == GPIO.LOW:
            recover_previous_state()
            break
        time.sleep(0.1)

if __name__ == "__main__":
    main()
