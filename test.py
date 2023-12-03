import RPi.GPIO as GPIO
import spidev
import time
from PIL import Image

# 핀 설정
DC_PIN = 24
RST_PIN = 25
SPI_CS_PIN = 8

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

# 디스플레이 초기화
def init_display():
    GPIO.output(SPI_CS_PIN, GPIO.HIGH)
    GPIO.output(RST_PIN, GPIO.HIGH)
    time.sleep(0.1)
    GPIO.output(RST_PIN, GPIO.LOW)
    time.sleep(0.1)
    GPIO.output(RST_PIN, GPIO.HIGH)
    time.sleep(0.1)

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
    image = image.rotate(180)  # 이미지를 회전시킵니다.
    image = image.transpose(Image.FLIP_LEFT_RIGHT)  # 이미지를 좌우 반전시킵니다.
    image_data = list(image.tobytes())
    width, height = image.size

    # 디스플레이에 이미지 표시
    write_command(0x2C)
    write_data(image_data)

# 메인 함수
def main():
    init_display()
    GPIO.output(DC_PIN, GPIO.HIGH)  # 데이터 모드로 설정

    # 이미지 파일을 열어서 디스플레이에 표시
    image = Image.open("sample.jpg")  # 표시할 이미지 파일 이름
    display_image(image)

    time.sleep(5)  # 5초 동안 표시 유지

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()
