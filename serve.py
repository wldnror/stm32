import pygame
import time
import os
import sys
import socket
import subprocess
import threading
from datetime import datetime
from PIL import Image
from ina219 import INA219, DeviceRangeError

# Pygame 초기화
pygame.init()

# 화면 설정
scale_factor = 4  # 화면 확대를 위한 스케일 팩터
display_width = 128 * scale_factor
display_height = 64 * scale_factor
screen = pygame.display.set_mode((display_width, display_height))
pygame.display.set_caption("STM32 Controller")

# 색상 정의
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
GRAY = (200, 200, 200)

# 폰트 설정
font = pygame.font.SysFont(None, int(17 * scale_factor))
font_small = pygame.font.SysFont(None, int(12 * scale_factor))
font_medium = pygame.font.SysFont(None, int(15 * scale_factor))
font_large = pygame.font.SysFont(None, int(21 * scale_factor))

# 버튼 정의
button_width = 40 * scale_factor
button_height = 20 * scale_factor
button_margin = 10 * scale_factor

next_button_rect = pygame.Rect(button_margin, display_height - button_height - button_margin, button_width, button_height)
execute_button_rect = pygame.Rect(display_width - button_width - button_margin, display_height - button_height - button_margin, button_width, button_height)

# 배터리 아이콘 로드
low_battery_icon = pygame.image.load("/home/user/stm32/img/bat_low.png").convert_alpha()
medium_battery_icon = pygame.image.load("/home/user/stm32/img/bat_medium.png").convert_alpha()
high_battery_icon = pygame.image.load("/home/user/stm32/img/bat_high.png").convert_alpha()
full_battery_icon = pygame.image.load("/home/user/stm32/img/bat_full.png").convert_alpha()

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

# INA219 설정
SHUNT_OHMS = 0.1
MIN_VOLTAGE = 3.1  # 최소 작동 전압
MAX_VOLTAGE = 4.2  # 최대 전압 (완충 시)

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

# 모드 상태
is_auto_mode = True

# 명령어 설정
commands = [
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/stlink.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ORG.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/stlink.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HMDS.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/stlink.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ARF-T.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/stlink.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HC100.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/stlink.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/SAT4010.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/stlink.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/IPA.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/stlink.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ASGD3000-V352PNP_0X009D2B7C.bin verify reset exit 0x08000000\"",
    "git_pull",  # 이 함수는 나중에 execute_command 함수에서 호출됩니다.
]

command_names = ["ORG", "HMDS", "ARF-T", "HC100", "SAT4010", "IPA", "ASGD S PNP", "시스템 업데이트"]
current_command_index = 0
need_update = False
is_command_executing = False
status_message = ""
message_position = (0, 0)
message_font_size = 17

# 연결 상태를 추적하기 위한 변수
connection_success = False
connection_failed_since_last_success = False

# 스크립트 재시작 함수
def restart_script():
    print("스크립트를 재시작합니다.")
    display_progress_and_message(25, "재시작 중", message_position=(20, 10), font_size=15)
    def restart():
        time.sleep(3)  # 3초 후에 스크립트를 재시작합니다.
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart).start()

# Git Pull 함수
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

    # 화면에 "시스템 업데이트 중" 표시
    display_progress_and_message(0, "시스템 업데이트 중", message_position=(36, 8), font_size=15)
    try:
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        if result.returncode == 0:
            if "이미 최신 상태" in result.stdout:
                display_progress_and_message(100, "이미 최신 상태", message_position=(10, 10), font_size=15)
                time.sleep(1)
            else:
                print("업데이트 성공!")
                display_progress_and_message(100, "업데이트 성공!", message_position=(10, 10), font_size=15)
                time.sleep(1)
                restart_script()
        else:
            print("GitHub 업데이트 실패. 오류 코드:", result.returncode)
            print("오류 메시지:", result.stderr)
            display_progress_and_message(0, "명령 실행 중 오류 발생", message_position=(0, 10), font_size=15)
            time.sleep(1)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        display_progress_and_message(0, "명령 실행 중 오류 발생", message_position=(0, 10), font_size=15)
        time.sleep(1)

# 메모리 잠금 해제 함수
def unlock_memory():
    print("메모리 해제 시도!")
    display_progress_and_message(0, "메모리 잠금\n해제 중", message_position=(18, 0), font_size=15)
    # 메모리 잠금 해제 로직 구현...
    openocd_command = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/stlink.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "stm32f1x unlock 0",
        "-c", "reset run",
        "-c", "shutdown"
    ]
    result = subprocess.run(openocd_command)

    if result.returncode == 0:
        display_progress_and_message(30, "메모리 잠금\n해제 성공!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        return True
    else:
        display_progress_and_message(0, "메모리 잠금\n해제 실패!", message_position=(20, 0), font_size=15)
        time.sleep(1)
        update_display()
        return False

# 메모리 잠금 함수
def lock_memory_procedure():
    display_progress_and_message(80, "메모리 잠금 중", message_position=(3, 10), font_size=15)
    openocd_command = [
        "sudo",
        "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/stlink.cfg",
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
            display_progress_and_message(100, "메모리 잠금\n성공", message_position=(20, 0), font_size=15)
            time.sleep(1)
        else:
            print("메모리 잠금에 실패했습니다. 오류 코드:", result.returncode)
            display_progress_and_message(0, "메모리 잠금\n실패", message_position=(20, 0), font_size=15)
            time.sleep(1)
            update_display()
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        display_progress_and_message(0, "오류 발생", message_position=(20, 0), font_size=15)
        time.sleep(1)
        update_display()

# STM32 연결 상태 확인 함수
def check_stm32_connection():
    global connection_success, connection_failed_since_last_success
    if is_command_executing:  # 명령 실행 중에는 STM32 연결 확인을 하지 않음
        return False

    try:
        command = [
            "sudo", "openocd",
            "-f", "/usr/local/share/openocd/scripts/interface/stlink.cfg",
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

# 명령어 실행 함수
def execute_command(command_index):
    global is_command_executing, need_update, status_message, message_font_size, message_position
    is_command_executing = True

    def run_command():
        global is_command_executing, need_update, status_message, message_font_size, message_position
        print("업데이트 시도!")

        if command_index == len(commands) - 1:
            git_pull()
            is_command_executing = False
            need_update = True
            return

        if command_index >= len(commands):
            lock_memory_procedure()
            is_command_executing = False
            need_update = True
            return

        if not unlock_memory():
            # 화면에 오류 메시지 표시
            display_progress_and_message(0, "메모리 잠금\n해제 실패", message_position=(20, 8), font_size=15)
            time.sleep(2)
            is_command_executing = False
            need_update = True
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
            time.sleep(0.5)
            lock_memory_procedure()
        else:
            print(f"'{commands[command_index]}' 업데이트 실패!")
            display_progress_and_message(0, "업데이트 실패", message_position=(7, 10), font_size=15)
            time.sleep(1)

        is_command_executing = False
        need_update = True

    threading.Thread(target=run_command).start()

# 화면에 진행 상태와 메시지 표시 함수
def display_progress_and_message(percentage, message, message_position=(0, 0), font_size=17):
    # 화면 지우기
    screen.fill(BLACK)
    # 메시지 표시
    font_custom = pygame.font.SysFont(None, int(font_size * scale_factor))
    message_lines = message.split('\n')
    for i, line in enumerate(message_lines):
        message_text_surface = font_custom.render(line, True, WHITE)
        pos = (message_position[0] * scale_factor, (message_position[1] + i * font_size) * scale_factor)
        screen.blit(message_text_surface, pos)
    # 진행 상태 바 표시
    progress_bar_outline = pygame.Rect(10 * scale_factor, 50 * scale_factor, 100 * scale_factor, 10 * scale_factor)
    pygame.draw.rect(screen, WHITE, progress_bar_outline, 1)
    progress_bar_filled = pygame.Rect(10 * scale_factor, 50 * scale_factor, (percentage / 100) * 100 * scale_factor, 10 * scale_factor)
    pygame.draw.rect(screen, WHITE, progress_bar_filled)
    pygame.display.flip()

# OLED 디스플레이 업데이트 함수 (Pygame으로 대체)
def update_display():
    global current_command_index, status_message, need_update
    if need_update:
        need_update = False

    ip_address = get_ip_address()
    now = datetime.now()
    current_time = now.strftime('%H시 %M분')
    voltage_percentage = read_ina219_percentage()

    screen.fill(BLACK)  # 화면 지우기

    # 모드 표시
    mode_char = 'A' if is_auto_mode else 'M'
    mode_text_surface = font.render(mode_char, True, WHITE)
    screen.blit(mode_text_surface, (8 * scale_factor, 0))

    # 시간 표시
    time_text_surface = font_small.render(current_time, True, WHITE)
    screen.blit(time_text_surface, (27 * scale_factor, 1 * scale_factor))

    # 배터리 상태 표시
    if voltage_percentage != -1:
        battery_icon = select_battery_icon(voltage_percentage)
        battery_icon = pygame.transform.scale(battery_icon, (20 * scale_factor, 20 * scale_factor))
        screen.blit(battery_icon, (90 * scale_factor, -9 * scale_factor))
        battery_text = f"{voltage_percentage}%"
        battery_text_surface = font_small.render(battery_text, True, WHITE)
        screen.blit(battery_text_surface, (99 * scale_factor, 3 * scale_factor))

    # 현재 명령어 표시
    cmd_name = command_names[current_command_index]
    cmd_text_surface = font_large.render(cmd_name, True, WHITE)
    cmd_text_positions = {
        "ORG": (42 * scale_factor, 27 * scale_factor),
        "HMDS": (33 * scale_factor, 27 * scale_factor),
        "ARF-T": (34 * scale_factor, 27 * scale_factor),
        "HC100": (32 * scale_factor, 27 * scale_factor),
        "SAT4010": (22 * scale_factor, 27 * scale_factor),
        "IPA": (47 * scale_factor, 27 * scale_factor),
        "ASGD S PNP": (2 * scale_factor, 27 * scale_factor),
        "시스템 업데이트": (1 * scale_factor, 20 * scale_factor)
    }
    if cmd_name in cmd_text_positions:
        screen.blit(cmd_text_surface, cmd_text_positions[cmd_name])

    # 상태 메시지 표시
    if status_message:
        font_custom = pygame.font.SysFont(None, int(message_font_size * scale_factor))
        message_lines = status_message.split('\n')
        for i, line in enumerate(message_lines):
            status_text_surface = font_custom.render(line, True, WHITE)
            pos = (message_position[0] * scale_factor, (message_position[1] + i * message_font_size) * scale_factor)
            screen.blit(status_text_surface, pos)

    # 버튼 그리기
    pygame.draw.rect(screen, GRAY, next_button_rect)
    pygame.draw.rect(screen, GRAY, execute_button_rect)

    next_text = font_medium.render("Next", True, BLACK)
    execute_text = font_medium.render("Execute", True, BLACK)
    screen.blit(next_text, (next_button_rect.x + (next_button_rect.width - next_text.get_width()) // 2,
                            next_button_rect.y + (next_button_rect.height - next_text.get_height()) // 2))
    screen.blit(execute_text, (execute_button_rect.x + (execute_button_rect.width - execute_text.get_width()) // 2,
                               execute_button_rect.y + (execute_button_rect.height - execute_text.get_height()) // 2))

    # 성공 및 오류 상태 표시기
    status_color = GREEN if connection_success else RED
    if is_command_executing:
        status_color = WHITE
    pygame.draw.circle(screen, status_color, (display_width - 20 * scale_factor, 20 * scale_factor), 10 * scale_factor)

    pygame.display.flip()

# IP 주소 가져오기 함수
def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        return "0.0.0.0"

# 시스템 종료 함수
def shutdown_system():
    try:
        # 화면에 종료 메시지 표시
        screen.fill(BLACK)
        shutdown_text1 = font_medium.render("시스템 종료 중...", True, WHITE)
        shutdown_text2 = font_small.render("배터리 부족", True, WHITE)
        screen.blit(shutdown_text1, (20 * scale_factor, 25 * scale_factor))
        screen.blit(shutdown_text2, (25 * scale_factor, 50 * scale_factor))
        pygame.display.flip()
        time.sleep(5)
        os.system('sudo shutdown -h now')
    except Exception as e:
        # 예외 발생 시 로그 남기기
        print("시스템 종료 중 오류 발생:", str(e))

# 실시간 업데이트를 위한 스레드 함수
def realtime_update_display():
    global is_command_executing
    while True:
        if not is_command_executing:
            update_display()
        time.sleep(1)

# 스레드 생성 및 시작
realtime_update_thread = threading.Thread(target=realtime_update_display)
realtime_update_thread.daemon = True
realtime_update_thread.start()

# 메인 루프
try:
    update_display()
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_pos = event.pos
                if next_button_rect.collidepoint(mouse_pos):
                    # Next 버튼 클릭
                    current_command_index = (current_command_index + 1) % len(commands)
                    need_update = True

                elif execute_button_rect.collidepoint(mouse_pos):
                    # Execute 버튼 클릭
                    if not is_command_executing:
                        execute_command(current_command_index)

        # 배터리 수준을 확인하고 0%면 시스템 종료
        voltage_percentage = read_ina219_percentage()
        if voltage_percentage == 0:
            print("배터리 수준이 0%입니다. 시스템을 종료합니다.")
            shutdown_system()

        # STM32 연결 상태 확인 및 명령 실행
        if command_names[current_command_index] != "시스템 업데이트":
            if is_auto_mode and check_stm32_connection() and connection_success and not is_command_executing:
                execute_command(current_command_index)

        # 디스플레이 업데이트
        update_display()

        time.sleep(0.03)
except KeyboardInterrupt:
    pygame.quit()
    sys.exit()
except Exception as e:
    print("An error occurred:", e)
    pygame.quit()
    sys.exit()
