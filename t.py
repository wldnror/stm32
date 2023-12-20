import spidev
import time

# SPI 인스턴스 생성
spi = spidev.SpiDev()

# SPI 통신 시작
spi.open(0, 0)  # 첫 번째 숫자는 SPI 버스 번호, 두 번째 숫자는 장치 번호

# SPI 통신 설정
spi.max_speed_hz = 500000  # 통신 속도 설정
spi.mode = 0  # SPI 모드 설정

def check_device():
    try:
        # 테스트를 위한 데이터 전송 (예: 0x00)
        response = spi.xfer([0x00])
        # 응답 확인 (장치에 따라 응답이 다를 수 있음)
        if response[0] == 0x00:  # 예상 응답값 확인
            return True
        else:
            return False
    except Exception as e:
        print(f"SPI 통신 오류: {e}")
        return False

# 주기적인 연결 상태 확인
while True:
    if check_device():
        print("장치 연결됨.")
    else:
        print("장치 연결 끊김!")
    time.sleep(5)  # 5초마다 체크
