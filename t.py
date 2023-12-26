import subprocess
import os

# OpenOCD를 사용하여 STM32 마이크로컨트롤러에서 펌웨어 추출
def extract_firmware():
    firmware_path = "/home/pi/stm32_firmware.bin"  # 추출된 펌웨어를 저장할 경로
    openocd_command = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "stm32f1x dump_image " + firmware_path + " 0x08000000 0x10000",  # 추출할 메모리 주소와 크기
        "-c", "reset run",
        "-c", "shutdown"
    ]

    try:
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            print("펌웨어 추출 성공:", firmware_path)
        else:
            print("펌웨어 추출 실패:", result.stderr)
    except Exception as e:
        print("펌웨어 추출 중 오류 발생:", str(e))

# 펌웨어 추출 함수 실행
extract_firmware()
