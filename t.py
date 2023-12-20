import subprocess
import time

def check_stm32_connection():
    try:
        # OpenOCD를 사용하여 STM32와의 연결을 시도하는 명령
        command = [
            "sudo", "openocd",
            "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
            "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
            "-c", "init",
            "-c", "exit"
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # 결과를 확인하여 연결 상태를 판단
        if result.returncode == 0:
            print("STM32 연결 성공")
            return True
        else:
            print("STM32 연결 실패:", result.stderr)
            return False
    except Exception as e:
        print(f"오류 발생: {e}")
        return False

# 메인 루프
while True:
    if check_stm32_connection():
        print("STM32 연결됨")
    else:
        print("STM32 연결 끊김 또는 응답 없음")
    time.sleep(5)  # 5초 간격으로 확인
