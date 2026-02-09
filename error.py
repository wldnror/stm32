import os
import RPi.GPIO as GPIO
import time
import subprocess
import socket
import threading
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1107
from ina219 import INA219, DeviceRangeError

# ✅ Wi-Fi 포털 모듈
import wifi_portal

# LED 핀
LED_SUCCESS = 24
LED_ERROR = 25

# 버튼 핀
BUTTON_PIN_NEXT = 27
BUTTON_PIN_EXECUTE = 17

# INA219
SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1
MAX_VOLTAGE = 4.2

# OLED
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'
font_big = ImageFont.truetype(font_path, 15)
font_small = ImageFont.truetype(font_path, 10)

# ✅ 메뉴: Wi-Fi 설정 추가
menu_options = ["업데이트 재시도", "기존 상태로 복구", "Wi-Fi 설정"]
current_menu_index = 0

# 화면/상태
status_lock = threading.Lock()
status_message = ""          # 화면에 잠시 띄울 메시지
status_until = 0.0           # 메시지 유지 시간(타임아웃)

stop_threads = False

# ✅ 버튼 콜백에서 “직접 실행” 금지: 요청만 큐잉
action_lock = threading.Lock()
pending_action = None        # "retry" | "recover" | "wifi"


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
    except (OSError, DeviceRangeError):
        return -1


def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def get_current_time():
    return datetime.now().strftime('%H:%M:%S')


def set_status(msg, seconds=2.0):
    global status_message, status_until
    with status_lock:
        status_message = msg
        status_until = time.time() + seconds


def clear_status_if_expired():
    global status_message
    with status_lock:
        if status_message and time.time() > status_until:
            status_message = ""


def display_menu():
    clear_status_if_expired()

    battery_percentage = read_ina219_percentage()
    ip_address = get_ip_address()
    current_time = get_current_time()

    with canvas(device) as draw:
        # 상단: 현재 메뉴
        draw.text((10, 0), menu_options[current_menu_index], font=font_big, fill=255)

        # 하단 정보
        draw.text((10, 28), f"Battery: {battery_percentage}%", font=font_small, fill=255)
        draw.text((10, 40), f"IP: {ip_address}", font=font_small, fill=255)
        draw.text((10, 52), f"Time: {current_time}", font=font_small, fill=255)

        # ✅ 상태 메시지(있으면 덮어쓰기)
        with status_lock:
            msg = status_message

        if msg:
            draw.rectangle(device.bounding_box, outline="white", fill="black")
            draw.text((6, 18), msg, font=font_big, fill=255)


def button_next_callback(channel):
    global current_menu_index
    current_menu_index = (current_menu_index + 1) % len(menu_options)
    set_status("", 0.01)  # 메시지 빠르게 정리
    # 화면은 주기 업데이트 스레드가 갱신


def button_execute_callback(channel):
    # ✅ 콜백에서 실행하지 말고 “요청”만 넣기
    global pending_action
    option = menu_options[current_menu_index]
    with action_lock:
        if option == "업데이트 재시도":
            pending_action = "retry"
        elif option == "기존 상태로 복구":
            pending_action = "recover"
        elif option == "Wi-Fi 설정":
            pending_action = "wifi"


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

    set_status("업데이트 중...", 60)

    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)

    try:
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode == 0:
            if "이미 최신 상태" in result.stdout:
                set_status("이미 최신 상태", 2)
                GPIO.output(LED_SUCCESS, True)
                time.sleep(0.8)
                GPIO.output(LED_SUCCESS, False)
            else:
                set_status("업데이트 성공!", 2)
                GPIO.output(LED_SUCCESS, True)
                time.sleep(0.8)
                GPIO.output(LED_SUCCESS, False)
                # 업데이트 성공 시 main.py 실행
                subprocess.run(["python3", "/home/user/stm32/main.py"])
        else:
            set_status("업데이트 실패", 2)
            GPIO.output(LED_ERROR, True)
            time.sleep(0.8)
            GPIO.output(LED_ERROR, False)

    except Exception as e:
        set_status("오류 발생", 2)
        GPIO.output(LED_ERROR, True)
        time.sleep(0.8)
        GPIO.output(LED_ERROR, False)


def recover_previous_state():
    set_status("복구 실행", 2)
    subprocess.run(["python3", "/home/user/stm32/main.py"])


def wifi_setup():
    # ✅ Wi-Fi는 선택사항: 포털 실행은 오래 걸림 → 상태 표시 + 백그라운드 처리
    if wifi_portal.has_internet():
        set_status("이미 인터넷 연결됨", 2)
        return

    # hostapd가 없으면 AP 안 뜸 → 안내
    r = subprocess.run(["which", "hostapd"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        set_status("hostapd 없음\n(main.py에서\n설치 후 재시도)", 3)
        return

    set_status("WiFi 설정 모드\nAP: GDSENG-SETUP\n192.168.4.1", 60)

    # ✅ 핵심: 여기서 ensure_wifi_connected는 블로킹이므로
    # action_worker 스레드에서 돌게 되어있음(현재 함수 자체는 worker에서 호출됨)
    wifi_portal.ensure_wifi_connected(auto_start_ap=True)

    if wifi_portal.has_internet():
        set_status("WiFi 연결 완료", 2)
    else:
        set_status("WiFi 연결 실패", 2)


def action_worker():
    global pending_action
    while not stop_threads:
        act = None
        with action_lock:
            if pending_action:
                act = pending_action
                pending_action = None

        if act == "retry":
            git_pull()
        elif act == "recover":
            recover_previous_state()
        elif act == "wifi":
            wifi_setup()

        time.sleep(0.05)


def update_display_every_second():
    while not stop_threads:
        display_menu()
        time.sleep(1)


def main():
    global stop_threads

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    GPIO.setup(LED_SUCCESS, GPIO.OUT)
    GPIO.setup(LED_ERROR, GPIO.OUT)

    GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.FALLING, callback=button_next_callback, bouncetime=250)
    GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.FALLING, callback=button_execute_callback, bouncetime=250)

    # 화면 업데이트 스레드
    update_thread = threading.Thread(target=update_display_every_second, daemon=True)
    update_thread.start()

    # 액션 처리 스레드
    worker_thread = threading.Thread(target=action_worker, daemon=True)
    worker_thread.start()

    display_menu()

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_threads = True
        GPIO.cleanup()


if __name__ == "__main__":
    main()
