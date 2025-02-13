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

display_lock = threading.Lock()
# GPIO 핀 설정
BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17
LED_SUCCESS = 24
LED_ERROR = 25
LED_ERROR1 = 23

# INA219 설정
SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1  # 최소 작동 전압
MAX_VOLTAGE = 4.2  # 최대 전압 (완충 시)

# 자동 모드와 수동 모드 상태
is_auto_mode = True

GPIO.setmode(GPIO.BCM)

last_time_button_next_pressed = 0
last_time_button_execute_pressed = 0
button_press_interval = 0.5

need_update = False
is_command_executing = False
is_button_pressed = False

last_mode_toggle_time = 0
is_executing = False

def toggle_mode():
    global is_auto_mode, last_mode_toggle_time
    is_auto_mode = not is_auto_mode
    last_mode_toggle_time = time.time()
    update_oled_display()

def button_next_callback(channel):
    global current_command_index, need_update, last_mode_toggle_time, is_executing, is_button_pressed
    global last_time_button_next_pressed, last_time_button_execute_pressed

    current_time = time.time()
    is_button_pressed = True

    if is_executing or (current_time - last_mode_toggle_time < 10):
        is_button_pressed = False
        return

    if current_time - last_time_button_execute_pressed < button_press_interval:
        toggle_mode()
        need_update = True
    else:
        current_command_index = (current_command_index + 1) % len(command_names)
        need_update = True

    last_time_button_next_pressed = current_time
    is_button_pressed = False

def button_execute_callback(channel):
    global current_command_index, need_update, last_mode_toggle_time, is_executing, is_button_pressed
    global last_time_button_next_pressed, last_time_button_execute_pressed

    current_time = time.time()
    is_button_pressed = True

    if is_executing or (current_time - last_mode_toggle_time < 10):
        is_button_pressed = False
        return

    if current_time - last_time_button_next_pressed < button_press_interval:
        toggle_mode()
        need_update = True
    else:
        if not is_auto_mode:
            execute_command(current_command_index)
            need_update = True
        else:
            with display_lock:
                if command_names[current_command_index] == "시스템 업데이트":
                    execute_command(current_command_index)
                else:
                    if is_auto_mode:
                        current_command_index = (current_command_index - 1) % len(command_names)
                    else:
                        execute_command(current_command_index)
            need_update = True

    last_time_button_execute_pressed = current_time
    is_button_pressed = False

GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.FALLING, callback=button_next_callback, bouncetime=800)
GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING, callback=button_execute_callback, bouncetime=800)
GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)
GPIO.setup(LED_ERROR1, GPIO.OUT)

connection_success = False
connection_failed_since_last_success = False

def check_stm32_connection():
    with display_lock:
        global connection_success, connection_failed_since_last_success, is_command_executing
        if is_command_executing:
            return False
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
                    connection_failed_since_last_success = False
                else:
                    print("STM32 연결 성공")
                    connection_success = False
                return True
            else:
                print("STM32 연결 실패:", result.stderr)
                connection_failed_since_last_success = True
                return False
        except Exception as e:
            print(f"오류 발생: {e}")
            connection_failed_since_last_success = True
            return False

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
            return int(((voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)) * 100)
    except Exception as e:
        print("INA219 모듈 읽기 실패:", str(e))
        return -1

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

def select_battery_icon(percentage):
    if percentage < 20:
        return low_battery_icon
    elif percentage < 60:
        return medium_battery_icon
    elif percentage < 100:
        return high_battery_icon
    else:
        return full_battery_icon

# ===== 프로그램 폴더 내의 파일들을 동적으로 읽어 명령어/메뉴 항목 생성 =====
def load_program_commands(program_folder="/home/user/stm32/Program"):
    # 지정 폴더 내의 .bin 파일들을 찾습니다.
    files = [f for f in os.listdir(program_folder)
             if os.path.isfile(os.path.join(program_folder, f)) and f.lower().endswith('.bin')]
    files.sort()
    cmds = []
    names = []
    for file in files:
        full_path = os.path.join(program_folder, file)
        # OpenOCD를 통한 프로그램 업로드 명령어 생성
        command = (
            f"sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
            f"-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
            f"-c \"program {full_path} verify reset exit 0x08000000\""
        )
        cmds.append(command)
        # 파일명(확장자 제외)을 메뉴 항목으로 사용
        names.append(os.path.splitext(file)[0])
    return cmds, names

# 동적으로 명령어/메뉴 항목을 로드합니다.
commands, command_names = load_program_commands()

# 마지막 메뉴 항목으로 시스템 업데이트( git pull )를 추가합니다.
commands.append("git_pull")
command_names.append("시스템 업데이트")
# ============================================================================

current_command_index = 0
status_message = ""
message_position = (0, 0)
message_font_size = 17

def git_pull():
    shell_script_path = '/home/user/stm32/git-pull.sh'
    if not os.path.isfile(shell_script_path):
        with open(shell_script_path, 'w') as script_file:
            script_file.write("#!/bin/bash\n")
            script_file.write("cd /home/user/stm32\n")
            script_file.write("git remote update\n")
            script_file.write("if git status -uno | grep -q 'Your branch is up to date'; then\n")
            script_file.write("   echo '이미 최신 상태입니다.'\n")
            script_file.write("   exit 0\n")
            script_file.write("fi\n")
            script_file.write("git stash\n")
            script_file.write("git pull\n")
            script_file.write("git stash pop\n")
            script_file.flush()
            os.fsync(script_file.fileno())

    os.chmod(shell_script_path, 0o755)
    
    with canvas(device) as draw:
        draw.text((36, 8), "시스템", font=font, fill=255)
        draw.text((17, 27), "업데이트 중", font=font, fill=255)

    try:
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        
        if result.returncode == 0:
            if "이미 최신 상태" in result.stdout:
                display_progress_and_message(100, "이미 최신 상태", message_position=(10, 10), font_size=15)
                time.sleep(1)
            else:
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
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

def restart_script():
    print("스크립트를 재시작합니다.")
    display_progress_and_message(25, "재시작 중", message_position=(20, 10), font_size=15)
    def restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart).start()

def display_progress_and_message(percentage, message, message_position=(0, 0), font_size=17):
    with canvas(device) as draw:
        draw.text(message_position, message, font=font, fill=255)
        draw.rectangle([(10, 50), (110, 60)], outline="white", fill="black")
        draw.rectangle([(10, 50), (10 + percentage, 60)], outline="white", fill="white")
        
def unlock_memory():
    with display_lock:
        print("메모리 해제 시도...")

    display_progress_and_message(0, "메모리 잠금\n   해제 중", message_position=(18, 0), font_size=15)

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
    if result.returncode == 0:
        display_progress_and_message(30, "메모리 잠금\n 해제 성공!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        return True
    else:
        display_progress_and_message(0, "메모리 잠금\n 해제 실패!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        update_oled_display()
        return False

def lock_memory_procedure():
    display_progress_and_message(80, "메모리 잠금 중", message_position=(3, 10), font_size=15)
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
        if result.returncode == 0:
            print("성공적으로 메모리를 잠갔습니다.")
            GPIO.output(LED_SUCCESS, True)
            display_progress_and_message(100,"메모리 잠금\n    성공", message_position=(20, 0), font_size=15)
            time.sleep(1)
            GPIO.output(LED_SUCCESS, False)
        else:
            print("메모리 잠금에 실패했습니다. 오류 코드:", result.returncode)
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            display_progress_and_message(0,"메모리 잠금\n    실패", message_position=(20, 0), font_size=15)
            time.sleep(1)
            update_oled_display()
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        update_oled_display()
        display_progress_and_message(0,"오류 발생")
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

def execute_command(command_index):
    global is_executing, is_command_executing
    is_executing = True
    is_command_executing = True

    print("업데이트 시도...")
    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)

    # 선택된 메뉴 항목이 시스템 업데이트라면
    if command_names[command_index] == "시스템 업데이트":
        git_pull()
        is_executing = False
        is_command_executing = False
        return

    # 프로그램 파일 플래싱 시, 메모리 해제 후 진행
    if not unlock_memory():
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        with canvas(device) as draw:
            draw.text((20, 8), "메모리 잠금", font=font, fill=255)
            draw.text((28, 27), "해제 실패", font=font, fill=255)
        time.sleep(2)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        is_executing = False
        is_command_executing = False
        return

    display_progress_and_message(30, "업데이트 중...", message_position=(12, 10), font_size=15)
    process = subprocess.Popen(commands[command_index], shell=True)
    
    start_time = time.time()
    max_duration = 6
    progress_increment = 20 / max_duration
    
    while process.poll() is None:
        elapsed = time.time() - start_time
        current_progress = 30 + (elapsed * progress_increment)
        current_progress = min(current_progress, 80)
        display_progress_and_message(current_progress, "업데이트 중...", message_position=(12, 10), font_size=15)
        time.sleep(0.5)

    result = process.returncode
    if result == 0:
        print(f"'{command_names[command_index]}' 업데이트 성공!")
        display_progress_and_message(80, "업데이트 성공!", message_position=(7, 10), font_size=15)
        time.sleep(0.5)
        lock_memory_procedure()
    else:
        print(f"'{command_names[command_index]}' 업데이트 실패!")
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        display_progress_and_message(0, "업데이트 실패", message_position=(7, 10), font_size=15)
        time.sleep(1)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

    is_executing = False
    is_command_executing = False

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        return "0.0.0.0"
        
def update_oled_display():
    global current_command_index, status_message, message_position, message_font_size, is_button_pressed
    with canvas(device) as draw:
        ip_address = get_ip_address()
        now = datetime.now()
        current_time = now.strftime('%H시 %M분')
        voltage_percentage = read_ina219_percentage()

        if command_names[current_command_index] != "시스템 업데이트":
            mode_char = 'A' if is_auto_mode else 'M'
            outer_ellipse_box = (2, 0, 22, 20)
            text_position = {'A': (8, -3), 'M': (5, -3)}
            draw.ellipse(outer_ellipse_box, outline="white", fill=None)
            draw.text(text_position[mode_char], mode_char, font=font, fill=255)
            
            battery_icon = select_battery_icon(voltage_percentage)
            draw.bitmap((90, -9), battery_icon, fill=255)
            draw.text((99, 3), f"{voltage_percentage:.0f}%", font=font_st, fill=255)
            draw.text((27, 1), current_time, font=font_time, fill=255)
            # 파일명(메뉴 항목) 중앙 배치
            w, h = draw.textsize(command_names[current_command_index], font=font_1)
            x = (device.width - w) // 2
            y = (device.height - h) // 2
            draw.text((x, y), command_names[current_command_index], font=font_1, fill=255)
        else:
            draw.text((0, 51), ip_address, font=font_big, fill=255)
            draw.text((80, -3), 'GDSENG', font=font_big, fill=255)
            draw.text((83, 50), 'ver 3.55', font=font_big, fill=255)
            draw.text((0, -3), current_time, font=font_time, fill=255)
            draw.text((1, 20), '시스템 업데이트', font=font, fill=255)

        if status_message:
            draw.rectangle(device.bounding_box, outline="white", fill="black")
            font_custom = ImageFont.truetype(font_path, message_font_size)
            draw.text(message_position, status_message, font=font_custom, fill=255)

def realtime_update_display():
    global is_command_executing
    while True:
        if not is_button_pressed and not is_command_executing:
            update_oled_display()
        time.sleep(1)

realtime_update_thread = threading.Thread(target=realtime_update_display)
realtime_update_thread.daemon = True
realtime_update_thread.start()

def shutdown_system():
    try:
        with canvas(device) as draw:
            draw.text((20, 25), "배터리 부족", font=font, fill=255)
            draw.text((25, 50), "시스템 종료 중...", font=font_st, fill=255)
        time.sleep(5)
        os.system('sudo shutdown -h now')
    except Exception as e:
        print("시스템 종료 중 오류 발생:", str(e))

update_oled_display()

try:
    while True:
        if read_ina219_percentage() == 0:
            print("배터리 수준이 0%입니다. 시스템을 종료합니다.")
            shutdown_system()

        if command_names[current_command_index] != "시스템 업데이트":
            if is_auto_mode and check_stm32_connection() and connection_success:
                execute_command(current_command_index)

        if need_update:
            update_oled_display()
            need_update = False

        time.sleep(0.03)
except KeyboardInterrupt:
    GPIO.cleanup()
