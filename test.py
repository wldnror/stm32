import pygame
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

# 이미지를 디스플레이에 전송하는 함수 (구현 필요)
def display_image(image):
    # 변환된 이미지를 디스플레이에 전송하는 로직을 여기에 구현합니다.

def create_image_with_text(width, height, text):
    pygame.init()
    surface = pygame.Surface((width, height))

    # 색상 패턴 그리기
    for y in range(0, height, 40):
        for x in range(0, width, 40):
            color = (x % 255, y % 255, (x + y) % 255)
            pygame.draw.rect(surface, color, (x, y, 40, 40))

    # 텍스트 추가
    font = pygame.font.Font(None, 36)
    text_surface = font.render(text, True, (255, 255, 255))
    text_rect = text_surface.get_rect(center=(width/2, height/2))
    surface.blit(text_surface, text_rect)

    return pygame.image.tostring(surface, 'RGB')

def main():
    init_display()
    width, height = 240, 280  # 디스플레이 해상도
    image_data = create_image_with_text(width, height, "Hello, World!")
    display_image(image_data)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()
        pygame.quit()
