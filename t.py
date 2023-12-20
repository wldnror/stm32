import subprocess
import time

def check_stm32_connection():
    try:
        # OpenOCD를 통해 STM32와의 연결을 시도하는 명령
        command = ["openocd", "-f", "/path/to/openocd.cfg", "-c", "init", "-c", "exit"]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # 결과를 확인하여 연결 상태를 판단
        if result.returncode == 0:
            print("STM32 연결 성공")
            return True
        else:
            print("STM32 연결 실패")
            return False
    except Exception as e:
        print(f"오류 발생: {e}")
        return False

# 주기적으로 연결 확인
while True:
    if check_stm32_connection():
        print("STM32 연결됨")
    else:
        print("STM32 연결 끊김")
    time.sleep(5)  # 5초 간격으로 확인
