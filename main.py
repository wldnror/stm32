import RPi.GPIO as GPIO
import time
import os
import sys
import socket
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1107
import subprocess
from datetime import datetime
from ina219 import INA219, DeviceRangeError

# INA219 설정
SHUNT_OHMS = 0.1
MAX_VOLTAGE = 5.0  # 최대 전압을 5V로 설정

def read_ina219_percentage():
    try:
        ina = INA219(SHUNT_OHMS)
        ina.configure()
        voltage = ina.voltage()

        # 백분율로 변환
        percentage = (voltage / MAX_VOLTAGE) * 100
        return min(percentage, 100)  # 최대값을 100%로 제한
    except DeviceRangeError as e:
        return 0

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        return "0.0.0.0"

# 배터리 아이콘 선택 함수
def select_battery_icon(percentage):
    if percentage < 20:
        return low_battery_icon
    elif percentage < 60:
        return medium_battery_icon
    elif percentage < 100:
        return high_battery_icon
    else:
        return full_battery_icon

# 배터리 아이콘 로드
# 아이콘 파일들은 미리 준비해 놓고 해당 경로에 맞게 로드해야 합니다.
low_battery_icon = Image.open("/home/user/stm32/img/bat.png")
medium_battery_icon = Image.open("/home/user/stm32/img/bat.png")
high_battery_icon = Image.open("/home/user/stm32/img/bat.png")
full_battery_icon = Image.open("/home/user/stm32/img/bat.png")

        
BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17
LED_DEBUGGING = 23
LED_SUCCESS = 24
LED_ERROR = 25

# 명령어 설정
commands = [
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ASGD3000-V353_0X009D2B79.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ASGD3000-V352PNP_0X009D2B7C.bin verify reset exit 0x08000000\"",
    "git_pull",  # 이 함수는 나중에 execute_command 함수에서 호출됩니다.
]

command_names = ["ASGD S", "ASGD S PNP", "시스템 업데이트"]

# OLED 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

# GPIO 설정
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(LED_DEBUGGING, GPIO.OUT)
GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)

# 폰트 및 이미지 설정
font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'
font_big = ImageFont.truetype(font_path, 10)
font_s = ImageFont.truetype(font_path, 11)
font = ImageFont.truetype(font_path, 17)
font_status = ImageFont.truetype(font_path, 13)

current_command_index = 0
status_message = ""

def git_pull():
    # 쉘 스크립트 경로 설정
    shell_script_path = '/home/user/stm32/git-pull.sh'
    
    # 쉘 스크립트 파일이 있는지 확인하고 없으면 생성
    if not os.path.isfile(shell_script_path):
        with open(shell_script_path, 'w') as script_file:
            script_file.write("#!/bin/bash\n")
            script_file.write("cd /home/user/stm32\n")
            script_file.write("git pull\n")
            script_file.flush()  # Python의 내부 버퍼를 비웁니다.
            os.fsync(script_file.fileno())  # 파일 시스템의 버퍼를 디스크에 기록합니다.

    os.chmod(shell_script_path, 0o755)  # 스크립트 파일에 실행 권한 부여
    
    # 업데이트 시작 시 디버깅 LED를 켜고 OLED에 상태 메시지 표시
    GPIO.output(LED_DEBUGGING, True)
    display_status_message("시스템 업데이트 중...")

    try:
        # 쉘 스크립트 실행
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # LED 초기화
        GPIO.output(LED_DEBUGGING, False)
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        
        if result.returncode == 0:
            print("업데이트 성공!")
            time.sleep(1)  # 메시지를 충분히 보여주기 위해 약간 대기합니다.
            restart_script()  # 여기에서 스크립트를 재시작합니다.
        else:
            print("GitHub 업데이트 실패. 오류 코드:", result.returncode)
            print("오류 메시지:", result.stderr)  # 오류 메시지 출력
            GPIO.output(LED_ERROR, True)
            display_status_message("업데이트 실패!")
            time.sleep(1)  # 메시지를 충분히 보여주기 위해 약간 대기합니다.

    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
       
        # 오류 LED를 켜고 OLED에 상태 메시지 표시
        GPIO.output(LED_ERROR, True)
        display_status_message("명령 실행 중 오류 발생")
        time.sleep(1)  # 메시지를 충분히 보여주기 위해 약간 대기합니다.
    finally:
        # LED 상태를 원래대로 복구
        GPIO.output(LED_DEBUGGING, False)
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)


def restart_script():
    print("스크립트를 재시작합니다.")
    os.execv(sys.executable, [sys.executable] + sys.argv)
       
def display_progress_bar(percentage):
    with canvas(device) as draw:
        # 전체 진행 바
        draw.rectangle([(10, 50), (110, 60)], outline="white", fill="black")
        # 현재 진행 상황 표시
        draw.rectangle([(10, 50), (10 + percentage, 60)], outline="white", fill="white")

def display_status_message(message):
    global status_message
    status_message = message
    update_oled_display()
    time.sleep(1)
    status_message = ""
    update_oled_display()

def unlock_memory():
    display_progress_bar(0)
    GPIO.output(LED_DEBUGGING, True)
    display_status_message("메모리 잠금 해제 중...")
    print("메모리 해제 시도...")
    time.sleep(1)
    display_progress_bar(50)
    openocd_command = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "stm32f1x unlock 0",
        "-c", "reset run",
        "-c", "shutdown"
    ]
    result = subprocess.run(openocd_command)
    GPIO.output(LED_DEBUGGING, False)
    if result.returncode == 0:
        print("메모리 잠금 해제 성공!")
        display_progress_bar(100)
    else:
        print("    메모리 잠금 해제 실패!")
    return result.returncode == 0

def lock_memory_procedure():
    display_progress_bar(0)
    GPIO.output(LED_DEBUGGING, True)
    display_status_message("  메모리 잠금 중...")
    openocd_command = [
        "sudo",
        "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "stm32f1x lock 0",
        "-c", "reset run",
        "-c", "shutdown",
    ]
    try:
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        GPIO.output(LED_DEBUGGING, False)
        if result.returncode == 0:
            print("성공적으로 메모리를 잠갔습니다.")
            GPIO.output(LED_SUCCESS, True)
            display_status_message(" 메모리 잠금 성공")
            display_progress_bar(100)
            time.sleep(1)
            GPIO.output(LED_SUCCESS, False)
        else:
            print("메모리 잠금에 실패했습니다. 오류 코드:", result.returncode)
            GPIO.output(LED_ERROR, True)
            display_status_message(" 메모리 잠금 실패")
            display_progress_bar(50)
            time.sleep(1)
            GPIO.output(LED_ERROR, False)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        GPIO.output(LED_ERROR, True)
        display_status_message("오류 발생")
        display_progress_bar(0)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)

def execute_command(command_index):
    print("  업데이트 시도...")
    display_progress_bar(0)
    # LED 초기화
    GPIO.output(LED_DEBUGGING, False)
    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    # GitHub 업데이트 명령어 실행
    if command_index == len(commands) - 1:  # commands 리스트의 마지막 항목이면
        git_pull()
        return
    if command_index == 2:  # 메모리 잠금 명령
        lock_memory_procedure()
        return

    if not unlock_memory():
        GPIO.output(LED_ERROR, True)
        display_status_message("   잠금 해제 실패")
        time.sleep(2)
        GPIO.output(LED_ERROR, False)
        return

    GPIO.output(LED_DEBUGGING, True)
    display_status_message("   업데이트 중...")
    process = subprocess.Popen(commands[command_index], shell=True)
    # 프로세스가 완료될 때까지 반복
    
    while process.poll() is None:
    # "업데이트 중..." 메시지를 계속 표시
        display_status_message("    업데이트 중...")
        time.sleep(1)
    # 프로세스 완료 후 결과 확인
    result = process.returncode
    GPIO.output(LED_DEBUGGING, False)
    display_progress_bar(50)

    if result == 0:
        print(f"'{commands[command_index]}' 업데이트 성공!")
        GPIO.output(LED_SUCCESS, True)
        display_status_message("   업데이트 성공")
        display_progress_bar(100)
        time.sleep(1)
        GPIO.output(LED_SUCCESS, False)
        lock_memory_procedure()
    else:
        print(f"'{commands[command_index]}' 업데이트 실패!")
        GPIO.output(LED_ERROR, True)
        display_status_message("   업데이트 실패")
        display_progress_bar(50)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)

def update_oled_display():
    global current_command_index
    ip_address = get_ip_address()
    current_time = datetime.now().strftime('%H:%M:%S')

    # INA219 센서에서 백분율 데이터 읽기
    voltage_percentage = read_ina219_percentage()

    with canvas(device) as draw:
        # 인터넷 연결 상태를 표시하는 부분을 삭제하거나 주석 처리합니다.
        # draw.text((120, 0), connection_status, font=font_status, fill=255)
        # 배터리 아이콘 및 백분율 표시
        battery_icon = select_battery_icon(voltage_percentage)
        draw.bitmap((0, 0), battery_icon, fill=255)  # 아이콘 위치 조정 필요
        draw.text((10, 10), f"{voltage_percentage:.0f}%", font=font_s, fill=255)  # 텍스트 위치 조정 필요

        # IP 주소를 우측 상단에 표시합니다. 좌표를 적절히 조정하세요.
        draw.text((0, 0), ip_address, font=font_big, fill=255)
        draw.text((85, 0), current_time, font=font_big, fill=255)

        # INA219 데이터 표시
        #draw.text((0, 10), f"전압: {voltage_percentage:.0f}%", font=font_s, fill=255)

        # 기존의 상태 메시지 및 기타 텍스트 표시 코드
        if status_message:
            draw.rectangle(device.bounding_box, outline="white", fill="black")
            draw.text((7, 20), status_message, font=font_status, fill=255)
        else:
            draw.text((0, 51), 'GDSENG', font=font_big, fill=255)
            draw.text((95, 51), 'ver 2.5', font=font_big, fill=255)
            draw.text((42, 15), f'설정 {current_command_index+1}번', font=font_s, fill=255)
            if command_names[current_command_index] == "ASGD S":
                draw.text((32, 28), 'ASGD S', font=font, fill=255)
            elif command_names[current_command_index] == "ASGD S PNP":
                draw.text((18, 28), 'ASGD S PNP', font=font, fill=255)

            elif command_names[current_command_index] == "시스템 업데이트":
                draw.text((1, 28), '시스템 업데이트', font=font, fill=255)


try:
    while True:
        if not GPIO.input(BUTTON_PIN_NEXT):
            current_command_index = (current_command_index + 1) % len(commands)
            time.sleep(0.1)
        elif not GPIO.input(BUTTON_PIN_EXECUTE):
            execute_command(current_command_index)
            time.sleep(0.1)
        update_oled_display()
        time.sleep(0.1)
except KeyboardInterrupt:
    GPIO.cleanup()