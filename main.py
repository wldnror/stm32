from datetime import datetime
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
from ina219 import INA219, DeviceRangeError
import threading

# GPIO 핀 설정
BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17
# LED_DEBUGGING = 23
LED_SUCCESS = 24
LED_ERROR = 25
LED_ERROR1 = 23


# INA219 설정
SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1  # 최소 작동 전압
MAX_VOLTAGE = 4.2  # 최대 전압 (완충 시)
previous_voltage = None
# voltage_drop_threshold = 0.1  # 전압이 이 값 이상 떨어질 때 반응

# 자동 모드와 수동 모드 상태를 추적하는 전역 변수
is_auto_mode = True

# GPIO 핀 번호 모드 설정 및 초기 상태 설정
GPIO.setmode(GPIO.BCM)

# 모드 전환 함수
def toggle_mode():
    global is_auto_mode
    is_auto_mode = not is_auto_mode
    update_oled_display()  # OLED 화면 업데이트

# 자동 모드와 수동 모드 아이콘 대신 문자열 사용
auto_mode_text = 'A'
manual_mode_text = 'M'

# GPIO 설정
GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
# GPIO.setup(LED_DEBUGGING, GPIO.OUT)
GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)
GPIO.setup(LED_ERROR1, GPIO.OUT)

# 연결 상태를 추적하기 위한 변수
connection_success = False
connection_failed_since_last_success = False

def check_stm32_connection():
    global connection_success, connection_failed_since_last_success
    try:
        command = [
            "sudo", "openocd",
            "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
            "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
            "-c", "init",
            "-c", "exit"
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode == 0:
            if connection_failed_since_last_success:
                print("STM32 재연결 성공")
                connection_success = True
                connection_failed_since_last_success = False  # 성공 후 실패 플래그 초기화
                
            else:
                print("STM32 연결 성공")
                connection_success = False  # 연속적인 성공을 방지
            return True
        else:
            print("STM32 연결 실패:", result.stderr)
            connection_failed_since_last_success = True  # 실패 플래그 
            return False
    except Exception as e:
        print(f"오류 발생: {e}")
        connection_failed_since_last_success = True  # 실패 플래그 설정
        return False

# 배터리 상태 확인 함수
def read_ina219_percentage():
    try:
        ina = INA219(SHUNT_OHMS)
        ina.configure()
        voltage = ina.voltage()
        if voltage <= MIN_VOLTAGE:
            return 0
        elif voltage >= MAX_VOLTAGE:
            return 100
        else:
            percentage = ((voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)) * 100
            return min(max(percentage, 0), 100)
    except DeviceRangeError as e:
        return 0

# OLED 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

# 폰트 및 이미지 설정
font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'
font_big = ImageFont.truetype(font_path, 12)
font_s = ImageFont.truetype(font_path, 13)
font_st = ImageFont.truetype(font_path, 11)
font = ImageFont.truetype(font_path, 17)
font_status = ImageFont.truetype(font_path, 13)
font_1 = ImageFont.truetype(font_path, 21)
font_time = ImageFont.truetype(font_path, 12)

# 배터리 아이콘 로드
low_battery_icon = Image.open("/home/user/stm32/img/bat.png")
medium_battery_icon = Image.open("/home/user/stm32/img/bat.png")
high_battery_icon = Image.open("/home/user/stm32/img/bat.png")
full_battery_icon = Image.open("/home/user/stm32/img/bat.png")

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

# 명령어 설정
commands = [
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ORG.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HMDS.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ARF-T.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HC100.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/IPA.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ASGD3000-V352PNP_0X009D2B7C.bin verify reset exit 0x08000000\"",
    # "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/extracted_file.bin verify reset exit 0x08000000\"",
    "git_pull",  # 이 함수는 나중에 execute_command 함수에서 호출됩니다.
]

command_names = ["ORG","HMDS","ARF-T","HC100","IPA", "ASGD S PNP", "시스템 업데이트"]

current_command_index = 0
status_message = ""

def git_pull():
    shell_script_path = '/home/user/stm32/git-pull.sh'
    if not os.path.isfile(shell_script_path):
        with open(shell_script_path, 'w') as script_file:
            script_file.write("#!/bin/bash\n")
            script_file.write("cd /home/user/stm32\n")
            script_file.write("git pull\n")
            script_file.flush()
            os.fsync(script_file.fileno())

    os.chmod(shell_script_path, 0o755)

    # GPIO.output(LED_DEBUGGING, True)

    with canvas(device) as draw:
        # '시스템' 메시지를 (0, 23) 위치에 표시
        draw.text((36, 8), "시스템", font=font, fill=255)
        # '업데이트 중' 메시지를 (0, 38) 위치에 표시
        draw.text((17, 27), "업데이트 중", font=font, fill=255)

    try:
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # GPIO.output(LED_DEBUGGING, False)
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        
        if result.returncode == 0:
            print("업데이트 성공!")
            GPIO.output(LED_SUCCESS, True)
            display_progress_and_message(100, "업데이트 성공!", message_position=(10, 10), font_size=15)
            
            time.sleep(1)
            GPIO.output(LED_SUCCESS, False)
            restart_script()
        else:
            print("GitHub 업데이트 실패. 오류 코드:", result.returncode)
            print("오류 메시지:", result.stderr)
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            display_progress_and_message(0, "명령 실행 중 오류 발생", message_position=(0, 10), font_size=15)
            time.sleep(1)

    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0, "명령 실행 중 오류 발생", message_position=(0, 10), font_size=15)
        time.sleep(1)
    finally:
        # GPIO.output(LED_DEBUGGING, False)
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

def restart_script():
    print("스크립트를 재시작합니다.")
    display_status_message("재시작 중",position=(20, 20), font_size=15)
    def restart():
        time.sleep(3)  # 1초 후에 스크립트를 재시작합니다.
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart).start()

def display_progress_and_message(percentage, message, message_position=(0, 0), font_size=17):
    with canvas(device) as draw:
        # 메시지 표시
        draw.text(message_position, message, font=font, fill=255)
        
        # 진행 상태 바 표시
        draw.rectangle([(10, 50), (110, 60)], outline="white", fill="black")  # 상태 바의 외곽선
        draw.rectangle([(10, 50), (10 + percentage, 60)], outline="white", fill="white")  # 상태 바의 내용
# 함수 사용 예시
# display_progress_and_message(0, "여기에 상태 메시지 입력", message_position=(20, 20), font_size=17)

def unlock_memory():
    print("메모리 해제 시도...")
    # GPIO.output(LED_DEBUGGING, True)

    # '메모리 잠금' 및 '해제 중' 메시지와 함께 초기 진행 상태 바 표시
    display_progress_and_message(0, "메모리 잠금\n   해제 중", message_position=(18, 0), font_size=15)

    # 메모리 잠금 해제 로직 구현...
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

    # GPIO.output(LED_DEBUGGING, False)

    if result.returncode == 0:
        display_progress_and_message(30, "메모리 잠금\n 해제 성공!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        return True
    else:
        display_progress_and_message(0, "메모리 잠금\n 해제 실패!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        return False

def restart_script():
    print("스크립트를 재시작합니다.")
    display_progress_and_message(25, "재시작 중", message_position=(20, 10), font_size=15)
    def restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart).start()   


def lock_memory_procedure():
    # display_progress_bar(0)
    # GPIO.output(LED_DEBUGGING, True)
    display_progress_and_message(90, "메모리 잠금 중", message_position=(3, 10), font_size=15)
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
        # GPIO.output(LED_DEBUGGING, False)
        if result.returncode == 0:
            print("성공적으로 메모리를 잠갔습니다.")
            GPIO.output(LED_SUCCESS, True)
            display_progress_and_message(100,"메모리 잠금\n    성공", message_position=(20, 0), font_size=15)
            # display_progress_bar(100)
            time.sleep(1)
            GPIO.output(LED_SUCCESS, False)
        else:
            print("메모리 잠금에 실패했습니다. 오류 코드:", result.returncode)
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            display_progress_and_message(0,"메모리 잠금\n    실패", message_position=(20, 0), font_size=15)
            # display_progress_bar(50)
            time.sleep(1)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0,"오류 발생")
        # display_progress_bar(0)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

def execute_command(command_index):
    print("업데이트 시도...")
    # display_progress_bar(0)
    # GPIO.output(LED_DEBUGGING, False)
    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)

    if command_index == len(commands) - 1:
        git_pull()
        return

    if command_index == 6:
        lock_memory_procedure()
        return

    if not unlock_memory():
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        with canvas(device) as draw:
            # '메모리 잠금' 메시지를 (0, 10) 위치에 표시
            draw.text((20, 8), "메모리 잠금", font=font, fill=255)
            # '해제 실패' 메시지를 (0, 25) 위치에 표시
            draw.text((28, 27), "해제 실패", font=font, fill=255)

        time.sleep(2)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        return

    # GPIO.output(LED_DEBUGGING, True)
    display_progress_and_message(30, "업데이트 중...", message_position=(12, 10), font_size=15)
    process = subprocess.Popen(commands[command_index], shell=True)
    
    start_time = time.time()
    max_duration = 30  # 최대 지속 시간을 초 단위로 설정 (이 값은 조정 가능)
    progress_increment = 20 / max_duration  # 50%에서 70%까지 증가
    
    while process.poll() is None:
        elapsed = time.time() - start_time
        current_progress = 50 + (elapsed * progress_increment)
        current_progress = min(current_progress, 70)  # 70%를 초과하지 않도록 제한
        display_progress_and_message(current_progress, "업데이트 중...", message_position=(12, 10), font_size=15)
        time.sleep(0.5)
        
    result = process.returncode# GPIO.output(LED_DEBUGGING, False)
    if result == 0:
        print(f"'{commands[command_index]}'업데이트 성공!")# GPIO.output(LED_SUCCESS, True)
        display_progress_and_message(90, "업데이트 성공!", message_position=(7, 10), font_size=15)# display_progress_bar(100)
        time.sleep(0.5)# GPIO.output(LED_SUCCESS, False)
        lock_memory_procedure()
    else:
        print(f"'{commands[command_index]}' 업데이트 실패!")
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0,"업데이트 실패", message_position=(7, 10), font_size=15)# display_progress_bar(50)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

def update_oled_display():
    global current_command_index, status_message, message_position, message_font_size
    ip_address = get_ip_address()
    now = datetime.now()  # 시스템 시간을 사용
    current_time = now.strftime('%H시 %M분')  # '시:분' 형식으로 변환 (24시간 형식)
    # current_time = now.strftime('%I시 %M분')  # '시:분' 형식으로 변환 (12시간 형식
    voltage_percentage = read_ina219_percentage()

    with canvas(device) as draw:
        if command_names[current_command_index] != "시스템 업데이트":
            # "시스템 업데이트"가 아닌 다른 메뉴에서는 오전/오후를 표시
            # am_pm = "오전" if now.hour < 12 else "오후"
            # current_time = f"{am_pm} {current_time}"

            # 모드에 따라 'A' 또는 'M' 선택
            mode_char = 'A' if is_auto_mode else 'M'
            outer_ellipse_box = (2, 0, 22, 20)  # 외부 동그라미 좌표 (크기 조정)
            # inner_ellipse_box = (8, 19, 16, 27)  # 내부 동그라미 좌표 (두께 조정)
            text_position = {
                'A': (8, -3),
                'M': (5, -3)
            }
            draw.ellipse(outer_ellipse_box, outline="white", fill=None)    # 외부 동그라미 그리기 (두께 조정)
            # draw.ellipse(inner_ellipse_box, outline="black", fill=None) # 내부 동그라미 그리기
            draw.text(text_position[mode_char], mode_char, font=font, fill=255)  # 글자 그리기

        if command_names[current_command_index] in ["ORG","HMDS","ARF-T","HC100","IPA", "ASGD S PNP"]:
            battery_icon = select_battery_icon(voltage_percentage)
            draw.bitmap((90, -9), battery_icon, fill=255)
            draw.text((99, 3), f"{voltage_percentage:.0f}%", font=font_st, fill=255)
            draw.text((27, 1), current_time, font=font_time, fill=255)
        elif command_names[current_command_index] == "시스템 업데이트":
            draw.text((0, 51), ip_address, font=font_big, fill=255)
            draw.text((80, -3), 'GDSENG', font=font_big, fill=255)
            draw.text((90, 50), 'ver 3.4', font=font_big, fill=255)
            draw.text((0, -3), current_time, font=font_time, fill=255)


        # 사용자 지정 위치와 폰트 크기로 메시지 표시
        if status_message:
            draw.rectangle(device.bounding_box, outline="white", fill="black")
            font_custom = ImageFont.truetype(font_path, message_font_size)
            draw.text(message_position, status_message, font=font_custom, fill=255)
        else:
            # if command_names[current_command_index] != "시스템 업데이트":
                # draw.text((40, 20), f'설정 {current_command_index+1}번', font=font_s, fill=255)  
            if command_names[current_command_index] == "ORG":
                draw.text((42, 27), 'ORG', font=font_1, fill=255)
            elif command_names[current_command_index] == "HMDS":
                draw.text((33, 27), 'HMDS', font=font_1, fill=255)
            elif command_names[current_command_index] == "ARF-T":
                draw.text((34, 27), 'ARF-T', font=font_1, fill=255)
            elif command_names[current_command_index] == "HC100":
                draw.text((32, 27), 'HC100', font=font_1, fill=255)
            elif command_names[current_command_index] == "IPA":
                draw.text((47, 27), 'IPA', font=font_1, fill=255)
            elif command_names[current_command_index] == "ASGD S PNP":
                draw.text((1, 27), 'ASGD S PNP', font=font_1, fill=255)
            elif command_names[current_command_index] == "시스템 업데이트":
                draw.text((1, 20), '시스템 업데이트', font=font, fill=255)

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        return "0.0.0.0"

def shutdown_system():
    with canvas(device) as draw:
        # 첫 번째 메시지를 (0, 0) 위치에 표시
        draw.text((20, 25), "배터리 부족", font=font, fill=255)
        # 두 번째 메시지를 (0, 25) 위치에 표시
        draw.text((25, 50), "시스템 종료 중...", font=font_st, fill=255)

    time.sleep(5)  # 메시지를 5초 동안 표시

    # 디스플레이 전원을 끄는 코드 추가
    GPIO.output(DISPLAY_POWER_PIN, GPIO.LOW)

    os.system('sudo shutdown -h now')  # 시스템을 안전하게 종료합니다.

# def button_handler():
#     global current_command_index  # 전역 변수 사용을 위한 global 선언
#     while True:  # 무한 루프 추가
#         if not GPIO.input(BUTTON_PIN_NEXT) and not GPIO.input(BUTTON_PIN_EXECUTE):
#             toggle_mode()
#             time.sleep(0.03)  # 디바운싱을 위한 지연

#         elif not GPIO.input(BUTTON_PIN_NEXT):
#             current_command_index = (current_command_index + 1) % len(commands)
#             time.sleep(0.03)

#         elif not GPIO.input(BUTTON_PIN_EXECUTE):
#             execute_command(current_command_index)
#             time.sleep(0.03)
            
# 스레드 시작
# button_thread = threading.Thread(target=button_handler)
# button_thread.daemon = True  # 프로그램 종료 시 스레드도 함께 종료되도록 설정
# button_thread.start()
try:
    while True:
        # 배터리 수준을 확인하고 0%면 시스템 종료
        if read_ina219_percentage() == 0:
            print("배터리 수준이 0%입니다. 시스템을 종료합니다.")
            shutdown_system()

        # STM32 연결 상태 확인 및 명령 실행
        if command_names[current_command_index] != "시스템 업데이트":
            if is_auto_mode and check_stm32_connection() and connection_success:
                execute_command(current_command_index)

        # 두 버튼을 동시에 눌렀을 때 모드 전환
        if not GPIO.input(BUTTON_PIN_NEXT) and not GPIO.input(BUTTON_PIN_EXECUTE):
            toggle_mode()
            time.sleep(0.01)  # 디바운싱을 위한 지연

        # NEXT 버튼 처리
        elif not GPIO.input(BUTTON_PIN_NEXT):
            current_command_index = (current_command_index + 1) % len(commands)
            time.sleep(0.01)

        # EXECUTE 버튼 처리
        elif not GPIO.input(BUTTON_PIN_EXECUTE):
            execute_command(current_command_index)
            time.sleep(0.01)

        # OLED 디스플레이 업데이트
        update_oled_display()

        # 짧은 지연
        time.sleep(0.01)

except KeyboardInterrupt:
    GPIO.cleanup()
