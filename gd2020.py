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
PGC_PIN = 10
PGD_PIN = 11
MCLR_PIN = 5V  # 이 핀은 실제 GPIO 핀이 아니므로, 라즈베리 파이에서 제어할 수 없습니다.

GPIO.setmode(GPIO.BCM)
GPIO.setup(PGC_PIN, GPIO.OUT)
GPIO.setup(PGD_PIN, GPIO.OUT)
# MCLR 핀은 별도의 전원 공급 장치를 통해 제어되어야 합니다.

def enter_programming_mode():
    # MCLR를 낮은 전압으로 설정하여 프로그래밍 모드로 전환
    # 라즈베리 파이에서 직접 제어할 수 없으므로, 외부 회로를 통해 제어해야 합니다.

def exit_programming_mode():
    # MCLR를 높은 전압으로 설정하여 일반 모드로 전환
    # 라즈베리 파이에서 직접 제어할 수 없으므로, 외부 회로를 통해 제어해야 합니다.

def send_programming_command(command):
    # GPIO를 사용하여 PIC16F876에 프로그래밍 명령을 전송하는 코드 구현

def program_hex_file(hex_file_path):
    # HEX 파일을 파싱하고 PIC16F876에 프로그래밍하는 코드 구현

# 프로그래밍 예시
enter_programming_mode()
program_hex_file("/path/to/your_hex_file.hex")
exit_programming_mode()

GPIO.cleanup()


