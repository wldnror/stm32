import time
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1107
from PIL import ImageFont

# OLED 디스플레이 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

# 폰트 설정
font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'  # 폰트 경로를 확인하세요
font = ImageFont.truetype(font_path, 14)

def display_boot_message():
    with canvas(device) as draw:
        draw.text((10, 20), "부팅 중...", font=font, fill=255)

if __name__ == '__main__':
    display_boot_message()
    time.sleep(10)  # 10초간 메시지를 표시한 후 종료
