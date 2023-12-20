import spidev
import time

spi = spidev.SpiDev()
spi.open(0, 0)  # 0: 첫 번째 SPI 버스, 0: 첫 번째 장치
spi.max_speed_hz = 1000000  # 1 MHz

def check_spi_device():
    try:
        # 예시: 단순한 데이터 전송 및 응답 받기
        # 이 부분은 연결된 장치의 특성에 맞게 수정되어야 합니다.
        to_send = [0x00]  # 더미 데이터
        response = spi.xfer(to_send)

        # 예시: 응답을 검증하여 장치의 연결 상태 확인
        # 실제 연결된 장치에 따라 응답 검증 로직은 달라져야 합니다.
        if response[0] == 0x00:  # 예상 응답
            return True
        else:
            return False
    except Exception as e:
        print(f"SPI 통신 오류: {e}")
        return False

# 메인 루프
while True:
    if check_spi_device():
        print("장치 연결됨.")
    else:
        print("장치 연결 끊김 또는 응답 없음.")
    time.sleep(1)  # 1초 대기
