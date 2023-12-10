import digitalio
import board
from PIL import Image, ImageDraw
from adafruit_rgb_display import color565
import adafruit_rgb_display.st7789 as st7789

# 디스플레이 설정
cs_pin = digitalio.DigitalInOut(board.CE0)  # Chip select (CS) 핀
dc_pin = digitalio.DigitalInOut(board.D25)  # Data/Command (DC) 핀
reset_pin = digitalio.DigitalInOut(board.D24)  # Reset (RST) 핀

BAUDRATE = 24000000  # SPI 통신 속도

spi = board.SPI()
disp = st7789.ST7789(spi, height=180, y_offset=80, rotation=280,
                     cs=cs_pin, dc=dc_pin, rst=reset_pin, baudrate=16000000)

# 디스플레이 크기에 맞는 이미지 생성
if disp.rotation % 180 == 90:
    height = disp.width
    width = disp.height
else:
    width = disp.width
    height = disp.height

image = Image.new('RGB', (width, height))

# 이미지에 그리기
draw = ImageDraw.Draw(image)
draw.rectangle((0, 0, width, height), fill=(0, 0, 0))
draw.text((10, 10), "Hello World", fill=(255, 255, 255))

# 디스플레이에 이미지 표시
disp.image(image)
