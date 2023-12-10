import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import spi
from luma.core.render import canvas
from luma.lcd.device import st7789

try:
    # GPIO 핀 번호 설정 (Raspberry Pi에 따라 변경해야 할 수 있음)
    RST_PIN = 25  # 예시로 25번 핀 사용
    DC_PIN = 24   # 예시로 24번 핀 사용

    # SPI 인터페이스와 ST7789 디스플레이 초기화
    serial = spi(port=0, device=0, gpio_DC=DC_PIN, gpio_RST=RST_PIN)
    device = st7789(serial, rotate=0, width=240, height=280)

    # 화면에 텍스트 출력
    with canvas(device) as draw:
        font = ImageFont.load_default()
        draw.text((10, 10), "Hello, World!", font=font, fill="white")

finally:
    # GPIO 리소스 정리
    GPIO.cleanup()
