import RPi.GPIO as GPIO
from luma.core.interface.serial import spi
from luma.lcd.device import st7789
from luma.core.render import canvas
from PIL import ImageDraw, ImageFont

# SPI 설정 및 ST7789 디스플레이 초기화
# gpio_DC와 gpio_RST는 Raspberry Pi의 해당 GPIO 핀 번호로 변경하세요.
serial = spi(port=0, device=0, gpio_DC=23, gpio_RST=24)
device = st7789(serial, rotate=0, width=240, height=240)

# 화면에 텍스트 출력
with canvas(device) as draw:
    # 폰트 설정 (기본 폰트 사용)
    font = ImageFont.load_default()
    
    # "Hello, World!" 텍스트 출력
    draw.text((10, 10), "Hello, World!", font=font, fill="white")

# GPIO 정리
GPIO.cleanup()
