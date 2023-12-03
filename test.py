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

    # 초기화 명령을 보내야 합니다.
    # 이 부분은 디스플레이의 데이터시트에 따라 달라집니다.

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
    spi.writebytes([data])
    GPIO.output(SPI_CS_PIN, GPIO.HIGH)

# 메인 함수
def main():
    init_display()
    # 화면을 초기화한 후에 특정 데이터를 보내거나
    # 특정 그래픽을 디스플레이에 그리는 코드를 추가합니다.

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()
