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

VISUAL_X_OFFSET = 0  # 필요에 따라 -3, -4 등으로 조절
display_lock = threading.Lock()
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

# 자동 모드와 수동 모드 상태를 추적하는 전역 변수
is_auto_mode = True

# GPIO 핀 번호 모드 설정 및 초기 상태 설정
GPIO.setmode(GPIO.BCM)

# 전역 변수로 마지막으로 눌린 시간을 추적
last_time_button_next_pressed = 0
last_time_button_execute_pressed = 0
button_press_interval = 0.5  # 두 버튼이 동시에 눌린 것으로 간주되는 최대 시간 차이

need_update = False
is_command_executing = False
is_button_pressed = False

# 전역 변수로 마지막 모드 전환 시간을 추적
last_mode_toggle_time = 0

# 스크립트 시작 부분에 전역 변수 정의
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

    if is_executing or (current_time - last_mode_toggle_time < 10):  # 모드 전환 후 일정 시간 동안는 입력 무시
        is_button_pressed = False
        return

    # EXECUTE 버튼이 최근에 눌렸는지 확인
    if current_time - last_time_button_execute_pressed < button_press_interval:
        toggle_mode()  # 모드 전환
        need_update = True
    else:
        current_command_index = (current_command_index + 1) % len(commands)
        need_update = True

    last_time_button_next_pressed = current_time  # NEXT 버튼 눌린 시간 갱신
    is_button_pressed = False


def button_execute_callback(channel):
    global current_command_index, need_update, last_mode_toggle_time, is_executing, is_button_pressed
    global last_time_button_next_pressed, last_time_button_execute_pressed

    current_time = time.time()
    is_button_pressed = True

    if is_executing or (current_time - last_mode_toggle_time < 10):  # 모드 전환 후 일정 시간 동안는 입력 무시
        is_button_pressed = False
        return

    # NEXT 버튼이 최근에 눌렸는지 확인
    if current_time - last_time_button_next_pressed < button_press_interval:
        toggle_mode()  # 모드 전환
        need_update = True
    else:
        # EXECUTE 버튼만 눌렸을 때의 로직
        if not is_auto_mode:
            execute_command(current_command_index)
            need_update = True
        else:
            with display_lock:
                if current_command_index == command_names.index("시스템 업데이트"):
                    execute_command(current_command_index)
                else:
                    if is_auto_mode:
                        current_command_index = (current_command_index - 1) % len(commands)
                    else:
                        execute_command(current_command_index)
            need_update = True

    last_time_button_execute_pressed = current_time  # EXECUTE 버튼 눌린 시간 갱신
    is_button_pressed = False

# 모드 전환 함수 (위에서 한 번 더 정의되어 있지만, 최종 정의는 이걸로 사용됨)
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
GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.FALLING, callback=button_next_callback, bouncetime=800)
GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING, callback=button_execute_callback, bouncetime=800)
GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)
GPIO.setup(LED_ERROR1, GPIO.OUT)

# 연결 상태를 추적하기 위한 변수
connection_success = False
connection_failed_since_last_success = False

def check_stm32_connection():
    with display_lock:
        global connection_success, connection_failed_since_last_success, is_command_executing
        if is_command_executing:  # 명령 실행 중에는 STM32 연결 확인을 하지 않음
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
            return int(((voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)) * 100)
    except Exception as e:
        # 예외 발생 시 로그 남기기
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
font_1 = ImageFont.truetype(font_path, 21)   # 일반 메뉴(펌웨어 .bin)용
font_sysupdate = ImageFont.truetype(font_path, 17)  # 🔥 시스템 업데이트 전용 더 작은 폰트
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

# -------------------------------
#  펌웨어 폴더 자동 스캔 부분 추가
# -------------------------------
FIRMWARE_DIR = "/home/user/stm32/Program"

def load_firmware_commands():
    """
    FIRMWARE_DIR 안의 .bin 파일을 모두 찾아서
    - 파일명(확장자 제외)을 메뉴 이름으로 사용
    - openocd program 명령을 자동 생성
    마지막에는 'git_pull' / '시스템 업데이트'를 추가
    """
    cmds = []
    names = []

    try:
        bin_files = sorted(
            f for f in os.listdir(FIRMWARE_DIR)
            if f.lower().endswith(".bin")
        )
    except FileNotFoundError:
        print("펌웨어 폴더를 찾을 수 없습니다:", FIRMWARE_DIR)
        bin_files = []

    for fname in bin_files:
        base_name = os.path.splitext(fname)[0]  # 확장자 제거 후 메뉴 이름으로 사용
        full_path = os.path.join(FIRMWARE_DIR, fname)

        openocd_cmd = (
            "sudo openocd "
            "-f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg "
            "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg "
            f"-c \"program {full_path} verify reset exit 0x08000000\""
        )

        cmds.append(openocd_cmd)
        names.append(base_name)

    # 마지막 메뉴는 시스템 업데이트(git_pull)
    cmds.append("git_pull")
    names.append("시스템 업데이트")

    print("로딩된 펌웨어 목록:", names)
    return cmds, names
 
# 명령어 자동 로딩
commands, command_names = load_firmware_commands()

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
            script_file.write("git remote update\n")  # 원격 저장소 정보 업데이트
            script_file.write("if git status -uno | grep -q 'Your branch is up to date'; then\n")
            script_file.write("   echo '이미 최신 상태입니다.'\n")
            script_file.write("   exit 0\n")
            script_file.write("fi\n")
            script_file.write("git stash\n")  # 임시로 변경사항을 저장
            script_file.write("git pull\n")  # 원격 저장소의 변경사항을 가져옴
            script_file.write("git stash pop\n")  # 저장했던 변경사항을 다시 적용
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


def display_progress_and_message(percentage, message, message_position=(0, 0), font_size=17):
    with canvas(device) as draw:
        # 메시지 표시
        draw.text(message_position, message, font=font, fill=255)
        
        # 진행 상태 바 표시
        draw.rectangle([(10, 50), (110, 60)], outline="white", fill="black")  # 상태 바의 외곽선
        draw.rectangle([(10, 50), (10 + percentage, 60)], outline="white", fill="white")  # 상태 바의 내용
        

def unlock_memory():
    with display_lock:
        print("메모리 해제 시도...")

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

    if result.returncode == 0:
        display_progress_and_message(30, "메모리 잠금\n 해제 성공!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        return True
    else:
        display_progress_and_message(0, "메모리 잠금\n 해제 실패!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        update_oled_display()
        return False

def restart_script():
    print("스크립트를 재시작합니다.")
    display_progress_and_message(25, "재시작 중", message_position=(20, 10), font_size=15)
    def restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart).start()   


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
    is_executing = True  # 작업 시작 전에 상태를 실행 중으로 설정
    is_command_executing = True  # 명령 실행 중 상태 활성화

    print("업데이트 시도...")
    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)

    # 마지막 메뉴는 항상 '시스템 업데이트'
    if command_index == len(commands) - 1:
        git_pull()
        is_executing = False
        is_command_executing = False
        return

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
        print(f"'{commands[command_index]}' 업데이트 성공!")
        display_progress_and_message(80, "업데이트 성공!", message_position=(7, 10), font_size=15)
        time.sleep(10)
        lock_memory_procedure()
    else:
        print(f"'{commands[command_index]}' 업데이트 실패!")
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
    with display_lock:  # 스레드 간 충돌 방지를 위해 display_lock 사용
        if is_button_pressed:
            return  # 버튼 입력 모드에서는 화면 업데이트 무시

        ip_address = get_ip_address()
        now = datetime.now()
        current_time = now.strftime('%H시 %M분')
        voltage_percentage = read_ina219_percentage()

        with canvas(device) as draw:
            # 모드 표시 (시스템 업데이트 메뉴가 아닐 때만)
            if command_names[current_command_index] != "시스템 업데이트":
                mode_char = 'A' if is_auto_mode else 'M'
                outer_ellipse_box = (2, 0, 22, 20)
                text_position = {'A': (8, -3), 'M': (5, -3)}
                draw.ellipse(outer_ellipse_box, outline="white", fill=None)
                draw.text(text_position[mode_char], mode_char, font=font, fill=255)

            # 상단 정보 (배터리/시간 or IP/버전)
            if command_names[current_command_index] != "시스템 업데이트":
                battery_icon = select_battery_icon(voltage_percentage)
                draw.bitmap((90, -9), battery_icon, fill=255)
                draw.text((99, 3), f"{voltage_percentage:.0f}%", font=font_st, fill=255)
                draw.text((27, 1), current_time, font=font_time, fill=255)
            else:
                # IP 주소가 "0.0.0.0"이면 "연결 없음"으로 표시
                if ip_address == "0.0.0.0":
                    ip_display = "연결 없음"
                else:
                    ip_display = ip_address
                draw.text((0, 51), ip_display, font=font_big, fill=255)
                draw.text((80, -3), 'GDSENG', font=font_big, fill=255)
                draw.text((83, 50), 'ver 3.56', font=font_big, fill=255)
                draw.text((0, -3), current_time, font=font_time, fill=255)

            # 상태 메시지가 있을 때 전체 메시지 화면
            if status_message:
                draw.rectangle(device.bounding_box, outline="white", fill="black")
                font_custom = ImageFont.truetype(font_path, message_font_size)
                draw.text(message_position, status_message, font=font_custom, fill=255)
            else:
                # ✅ 메뉴 이름을 가운데 정렬로 표시 (anchor="mm" 사용)
                title = command_names[current_command_index]
                center_x = device.width // 2 + VISUAL_X_OFFSET

                # 시스템 업데이트만 약간 위로 + 작은 폰트
                if title == "시스템 업데이트":
                    center_y = 33  # 🔥 업데이트만 위로
                    use_font = font_sysupdate
                else:
                    center_y = 42  # 일반 메뉴는 조금 아래로 중앙 근처
                    use_font = font_1

                try:
                    # Pillow에서 anchor 지원될 때
                    draw.text((center_x, center_y), title, font=use_font, fill=255, anchor="mm")
                except TypeError:
                    # anchor 없으면 수동으로 중앙 계산
                    try:
                        w, h = draw.textsize(title, font=use_font)
                    except Exception:
                        w, h = (len(title) * 8, 16)
                    x = int(center_x - w / 2)
                    y = int(center_y - h / 2)
                    draw.text((x, y), title, font=use_font, fill=255)


# 실시간 업데이트를 위한 스레드 함수
def realtime_update_display():
    global is_command_executing
    while True:
        if not is_button_pressed and not is_command_executing:
            update_oled_display()
        time.sleep(1)

# 스레드 생성 및 시작
realtime_update_thread = threading.Thread(target=realtime_update_display)
realtime_update_thread.daemon = True
realtime_update_thread.start()

def shutdown_system():
    try:
        with canvas(device) as draw:
            draw.text((20, 25), "배터리 부족", font=font, fill=255)
            draw.text((25, 50), "시스템 종료 중...", font=font_st, fill=255)
        time.sleep(5)
        # DISPLAY_POWER_PIN 정의되어 있으면 사용, 아니면 제거하거나 주석 처리
        # GPIO.output(DISPLAY_POWER_PIN, GPIO.LOW)
        os.system('sudo shutdown -h now')
    except Exception as e:
        # 예외 발생 시 로그 남기기
        print("시스템 종료 중 오류 발생:", str(e))

# 초기 디스플레이 업데이트
update_oled_display()

# 메인 루프
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

        # OLED 디스플레이 업데이트
        if need_update:
            update_oled_display()
            need_update = False

        time.sleep(0.03)
except KeyboardInterrupt:
    GPIO.cleanup()
