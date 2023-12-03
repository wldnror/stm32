from PIL import Image, ImageDraw, ImageFont
import RPi.GPIO as GPIO
import spidev
import time

# 핀 설정
DC_PIN = 24
RST_PIN = 25
SPI_CS_PIN = 8
BLK_PIN = 18  # 백라이트 핀은 선택적입니다.

# SPI 인스턴스 설정
spi = spidev.SpiDev()
spi.open(0, 0)  # SPI 포트 0, 디바이스 0
spi.max_speed_hz = 20000000  # 20 MHz

# GPIO 설정
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(DC_PIN, GPIO.OUT)
GPIO.setup(RST_PIN, GPIO.OUT)
GPIO.setup(SPI_CS_PIN, GPIO.OUT)
GPIO.setup(BLK_PIN, GPIO.OUT)  # 백라이트 핀은 선택적입니다.

# 디스플레이 초기화
def init_display():
    GPIO.output(SPI_CS_PIN, GPIO.HIGH)
    GPIO.output(RST_PIN, GPIO.HIGH)
    time.sleep(0.1)
    GPIO.output(RST_PIN, GPIO.LOW)
    time.sleep(0.1)
    GPIO.output(RST_PIN, GPIO.HIGH)
    time.sleep(0.1)

    # 초기화 명령 (디스플레이에 따라 다름)

# 명령 전송 함수
def write_command(command):
    GPIO.output(DC_PIN, GPIO.LOW)
    GPIO.output(SPI_CS_PIN, GPIO.LOW)
    spi.writebytes([command])
    GPIO.output(SPI_CS_PIN, GPIO.HIGH)

# 데이터 전송 함수
def write_data(data):
    GPIO.output(DC_PIN, GPIO.HIGH)
    GPIO.output(SPI_CS_PIN, GPIO.LOW)
    spi.writebytes(data)
    GPIO.output(SPI_CS_PIN, GPIO.HIGH)

# 이미지를 디스플레이에 전송하는 함수
def display_image(image):
    width, height = 240, 280  # 디스플레이 해상도
    image = image.resize((width, height))
    pixels = list(image.getdata())

    # 여기에서 RGB 데이터를 디스플레이의 색상 포맷에 맞게 변환하고 전송해야 합니다.

def create_image_with_text(width, height, text):
    image = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(0, height, 40):
        for x in range(0, width, 40):
            color = (x % 255, y % 255, (x + y) % 255)
            draw.rectangle((x, y, x+40, y+40), fill=color)

    font = ImageFont.load_default()
    text_width, text_height = draw.textsize(text, font=font)  # 여기를 수정함
    text_x = (width - text_width) / 2
    text_y = (height - text_height) / 2
    draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255))

    return image


def main():
    init_display()
    width, height = 240, 280  # 디스플레이 해상도
    image = create_image_with_text(width, height, "Hello, World!")
    display_image(image)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()
