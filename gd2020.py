# import subprocess
# import os

# # 프로그래밍할 HEX 파일의 경로
# hex_file_path = '/home/user/stm32/Program/nh3-gn8020-e.hex'

# # 파일의 읽기/쓰기 권한 변경
# os.chmod(hex_file_path, 0o666)

# # Pickle 명령어를 사용하여 PIC 프로그래밍
# try:
#     subprocess.run(['pickle', 'p14', 'lvp', 'program', hex_file_path], check=True)
#     print("프로그래밍 성공")
# except subprocess.CalledProcessError as e:
#     print(f"프로그래밍 실패: {e}")

import RPi.GPIO as GPIO
import time

# GPIO 핀 설정
PGC_PIN = 21  # 예시 GPIO 핀 번호
PGD_PIN = 20  # 예시 GPIO 핀 번호
MCLR_PIN = 16  # 예시 GPIO 핀 번호

GPIO.setmode(GPIO.BCM)
GPIO.setup(PGC_PIN, GPIO.OUT)
GPIO.setup(PGD_PIN, GPIO.OUT)
GPIO.setup(MCLR_PIN, GPIO.OUT)

def enter_programming_mode():
    GPIO.output(MCLR_PIN, GPIO.LOW)
    time.sleep(0.1)  # PIC16F876 프로그래밍 모드 진입 시간

def exit_programming_mode():
    GPIO.output(MCLR_PIN, GPIO.HIGH)
    time.sleep(0.1)  # PIC16F876 일반 모드로 복귀 시간

def send_programming_command(command):
    # GPIO를 사용하여 PIC16F876에 프로그래밍 명령을 전송하는 코드 작성
    pass

def program_hex_file(hex_file_path):
    # HEX 파일을 파싱하고 PIC16F876에 프로그래밍하는 코드 작성
    pass

# 예시 사용법
enter_programming_mode()
program_hex_file("/path/to/your_hex_file.hex")
exit_programming_mode()

GPIO.cleanup()

