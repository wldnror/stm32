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

# 화면 전체를 특정 색으로 채우는 함수
def fill_color(color):
    width, height = 240, 280  # 실제 해상도에 맞추어야 함
    write_command(0x2C)  # 메모리에 쓰기 시작 커맨드

    # RGB 색상을 RGB565 형식으로 변환
    red = (color[0] & 0xF8) << 8
    green = (color[1] & 0xFC) << 3
    blue = color[2] >> 3
    rgb565 = red | green | blue

    pixels = [rgb565 >> 8, rgb565 & 0xFF] * width * height
    for row in range(height):
        write_data(pixels[width*2*row:width*2*(row+1)])

# 메인 함수
def main():
    init_display()
    fill_color([0xFF, 0x00, 0x00])  # 화면을 빨간색으로 채움

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()
