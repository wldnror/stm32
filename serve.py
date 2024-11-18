import tkinter as tk
from datetime import datetime
import threading
import time
import os
import sys
import socket
import subprocess
import pygame
import ftplib

# DISPLAY 환경 변수 설정 (Linux 환경에서 GUI 사용 시 필요)
os.environ['DISPLAY'] = ':0'

# pygame 초기화
pygame.mixer.init()

# 사운드 파일 경로 설정
script_dir = os.path.dirname(os.path.abspath(__file__))
SUCCESS_SOUND_PATH = os.path.join(script_dir, 'success.mp3')
FAILURE_SOUND_PATH = os.path.join(script_dir, 'failure.mp3')  # 실패 사운드 파일 경로 (별도 파일 권장)

# 사운드 로드
def load_sound(path):
    if os.path.isfile(path):
        try:
            return pygame.mixer.Sound(path)
        except Exception as e:
            print(f"사운드 파일 로드 중 오류 발생 ({path}): {e}")
            return None
    else:
        print(f"사운드 파일을 찾을 수 없습니다: {path}")
        return None

success_sound = load_sound(SUCCESS_SOUND_PATH)
failure_sound = load_sound(FAILURE_SOUND_PATH)

# 사운드 재생 함수 정의
def play_success_sound():
    if success_sound:
        try:
            success_sound.play()
        except Exception as e:
            print(f"성공 사운드 재생 중 오류 발생: {e}")
    else:
        print(f"성공 사운드 파일을 로드하지 못했습니다: {SUCCESS_SOUND_PATH}")

def play_failure_sound():
    if failure_sound:
        try:
            failure_sound.play()
        except Exception as e:
            print(f"실패 사운드 재생 중 오류 발생: {e}")
    else:
        print(f"실패 사운드 파일을 로드하지 못했습니다: {FAILURE_SOUND_PATH}")

# 전역 변수 설정
is_auto_mode = True
current_command_index = 0
commands = [
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ORG.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HMDS.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ARF-T.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HC100.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/SAT4010.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/IPA.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ASGD3000-V352PNP_0X009D2B7C.bin verify reset exit 0x08000000\"",
    "git_pull",  # 이 함수는 나중에 execute_command 함수에서 호출됩니다.
]
command_names = ["ORG","HMDS","ARF-T","HC100", "SAT4010","IPA", "ASGD S PNP", "시스템 업데이트"]

# 상태 메시지 및 실행 상태
status_message = ""
is_executing = False
need_update = False
connection_success = False
connection_failed_since_last_success = False
update_prompt_shown = False  # 업데이트 프롬프트가 이미 표시되었는지 여부

# Tkinter GUI 설정
root = tk.Tk()
root.title("업데이트 관리자")
root.geometry("600x700")  # 필요에 따라 크기 조정
root.attributes("-topmost", True)  # 창을 항상 최상위에 유지
root.lift()  # 창을 최상위로 올리기 (필요한 경우)

# 모드 라벨
mode_label = tk.Label(root, text="", font=("Helvetica", 17))
mode_label.pack(pady=10)
mode_label.config(text=f"모드: {'자동' if is_auto_mode else '수동'}")

# 현재 명령어 라벨
current_command_label = tk.Label(root, text=f"현재 명령어: {command_names[current_command_index]}", font=("Helvetica", 14))
current_command_label.pack(pady=5)

# 상태 라벨
status_label = tk.Label(root, text="상태: 대기 중", font=("Helvetica", 14), fg="blue")
status_label.pack(pady=5)

# IP 주소 라벨
ip_label = tk.Label(root, text=f"IP 주소: 로딩 중...", font=("Helvetica", 12))
ip_label.pack(pady=5)

# LED 상태 표시기 (GUI 내에서 색상으로 대체)
led_frame = tk.Frame(root)
led_frame.pack(pady=10)

led_success = tk.Label(led_frame, text="성공 LED", bg="grey", width=10, height=2)
led_success.grid(row=0, column=0, padx=5)

led_error = tk.Label(led_frame, text="오류 LED1", bg="grey", width=10, height=2)
led_error.grid(row=0, column=1, padx=5)

led_error1 = tk.Label(led_frame, text="오류 LED2", bg="grey", width=10, height=2)
led_error1.grid(row=0, column=2, padx=5)

# 업데이트 요청 프레임 (초기에는 숨김)
update_frame = tk.Frame(root, bg='yellow', pady=10)

update_label = tk.Label(update_frame, text="업데이트를 자동으로 확인하고 업데이트 하시겠습니까?", font=("Helvetica", 12), bg='yellow')
update_label.pack(pady=5)

update_buttons_frame = tk.Frame(update_frame, bg='yellow')
update_buttons_frame.pack(pady=5)

def on_update_yes():
    global update_prompt_shown
    print("사용자가 '예'를 선택했습니다. 업데이트를 시작합니다.")
    threading.Thread(target=git_pull, daemon=True).start()
    hide_update_frame()
    update_prompt_shown = False

def on_update_no():
    global update_prompt_shown
    print("사용자가 '아니오'를 선택했습니다. 업데이트를 건너뜁니다.")
    hide_update_frame()
    update_prompt_shown = False

update_yes_button = tk.Button(update_buttons_frame, text="예", command=on_update_yes, width=10, bg="green", fg="white")
update_yes_button.pack(side=tk.LEFT, padx=10)

update_no_button = tk.Button(update_buttons_frame, text="아니오", command=on_update_no, width=10, bg="red", fg="white")
update_no_button.pack(side=tk.LEFT, padx=10)

def show_update_frame():
    global update_prompt_shown
    if not update_prompt_shown:
        print("업데이트 프레임을 표시합니다.")
        update_frame.pack(pady=10)
        update_prompt_shown = True

def hide_update_frame():
    print("업데이트 프레임을 숨깁니다.")
    update_frame.pack_forget()

# 버튼 프레임
button_frame = tk.Frame(root)
button_frame.pack(pady=20)

def update_led(led_label, status):
    if status:
        led_label.config(bg="green")
    else:
        led_label.config(bg="grey")

# 버튼 콜백 함수
def toggle_mode_gui():
    global is_auto_mode
    if is_executing:
        show_notification("현재 명령이 실행 중입니다.", "red")
        print("모드 전환 시도: 명령 실행 중.")
        return
    is_auto_mode = not is_auto_mode
    mode_label.config(text=f"모드: {'자동' if is_auto_mode else '수동'}")
    show_notification(f"모드가 {'자동' if is_auto_mode else '수동'}으로 변경되었습니다.", "blue")
    print(f"모드가 {'자동' if is_auto_mode else '수동'}으로 변경되었습니다.")

def next_command_gui():
    global current_command_index
    if is_executing:
        show_notification("현재 명령이 실행 중입니다.", "red")
        print("명령어 변경 시도: 명령 실행 중.")
        return
    current_command_index = (current_command_index + 1) % len(commands)
    current_command_label.config(text=f"현재 명령어: {command_names[current_command_index]}")
    print(f"다음 명령어로 변경: {command_names[current_command_index]}")

def previous_command_gui():
    global current_command_index
    if is_executing:
        show_notification("현재 명령이 실행 중입니다.", "red")
        print("명령어 변경 시도: 명령 실행 중.")
        return
    current_command_index = (current_command_index - 1) % len(commands)
    current_command_label.config(text=f"현재 명령어: {command_names[current_command_index]}")
    print(f"이전 명령어로 변경: {command_names[current_command_index]}")

def execute_command_gui():
    global is_executing
    if is_executing:
        show_notification("이미 명령이 실행 중입니다.", "red")
        print("명령 실행 시도: 이미 명령이 실행 중입니다.")
        return
    threading.Thread(target=execute_command, args=(current_command_index,), daemon=True).start()

# 버튼 생성 (이전, 다음, 확인, 모드 전환)
previous_button = tk.Button(button_frame, text="이전", command=previous_command_gui, width=10, height=2)
previous_button.grid(row=0, column=0, padx=10)

next_button = tk.Button(button_frame, text="다음", command=next_command_gui, width=10, height=2)
next_button.grid(row=0, column=1, padx=10)

execute_button = tk.Button(button_frame, text="확인", command=execute_command_gui, width=10, height=2)
execute_button.grid(row=0, column=2, padx=10)

toggle_mode_button = tk.Button(button_frame, text="모드 전환", command=toggle_mode_gui, width=10, height=2, bg="orange")
toggle_mode_button.grid(row=0, column=3, padx=10)

# --- 새로 추가된 부분 시작 ---

# 새로운 버튼 프레임
extra_button_frame = tk.Frame(root)
extra_button_frame.pack(pady=10)

def extract_and_upload_gui():
    if is_executing:
        show_notification("현재 명령이 실행 중입니다.", "red")
        print("파일 추출 시도: 이미 명령이 실행 중입니다.")
        return
    threading.Thread(target=extract_file_from_stm32, daemon=True).start()

extract_button = tk.Button(extra_button_frame, text="파일 추출 및 업로드", command=extract_and_upload_gui, width=20, height=2, bg="purple", fg="white")
extract_button.pack(pady=5)

# --- 새로 추가된 부분 끝 ---

# IP 주소 업데이트 함수
def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        print(f"IP 주소 가져오기 실패: {e}")
        return "0.0.0.0"

def update_ip_label():
    ip = get_ip_address()
    ip_label.config(text=f"IP 주소: {ip}")
    print(f"IP 주소 업데이트: {ip}")
    root.after(5000, update_ip_label)  # 5초마다 업데이트

# Git Pull 함수
def git_pull():
    shell_script_path = '/home/user/stm32/git-pull.sh'
    print("업데이트를 시작합니다.")
    if not os.path.isfile(shell_script_path):
        print(f"{shell_script_path} 파일이 존재하지 않습니다. 스크립트를 생성합니다.")
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
    print("git-pull.sh 스크립트 실행 권한을 설정했습니다.")

    update_status("시스템 업데이트 중...", "orange")
    try:
        print("git-pull.sh 스크립트를 실행합니다.")
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(f"git-pull.sh 출력: {result.stdout}")
        print(f"git-pull.sh 오류 출력: {result.stderr}")

        # 스크립트의 반환값으로 업데이트 필요 여부 판단
        if result.returncode == 1:
            # 업데이트가 수행되었음을 알림
            update_status("업데이트 성공!", "green")
            show_notification("시스템 업데이트에 성공했습니다.", "green")
            play_success_sound()  # 성공 사운드 재생
            print("시스템 업데이트에 성공했습니다.")
            restart_script()
        elif result.returncode == 0:
            # 이미 최신 상태임을 알림
            update_status("이미 최신 상태", "blue")
            show_notification("시스템이 이미 최신 상태입니다.", "blue")
            print("시스템이 이미 최신 상태입니다.")
        else:
            # 기타 오류
            update_status("업데이트 실패", "red")
            show_notification(f"GitHub 업데이트 실패.\n오류 메시지: {result.stderr}", "red")
            play_failure_sound()  # 실패 사운드 재생
            update_led(led_error, True)
            update_led(led_error1, True)
            print(f"업데이트 실패: {result.stderr}")
    except Exception as e:
        update_status("업데이트 오류", "red")
        show_notification(f"업데이트 중 오류 발생:\n{str(e)}", "red")
        play_failure_sound()  # 실패 사운드 재생
        update_led(led_error, True)
        update_led(led_error1, True)
        print(f"업데이트 중 오류 발생: {e}")

def restart_script():
    update_status("스크립트 재시작 중...", "orange")
    print("스크립트를 재시작합니다.")
    def restart():
        time.sleep(3)  # 3초 후 재시작
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart, daemon=True).start()

# 메모리 잠금 해제 및 잠금 함수
def unlock_memory():
    update_status("메모리 잠금 해제 중...", "orange")
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
    try:
        print("unlock_memory 명령 실행 중...")
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(f"unlock_memory 명령 출력: {result.stdout}")
        print(f"unlock_memory 명령 오류 출력: {result.stderr}")
        if result.returncode == 0:
            update_status("메모리 잠금 해제 성공!", "green")
            show_notification("메모리 잠금 해제에 성공했습니다.", "green")
            return True
        else:
            update_status("메모리 잠금 해제 실패", "red")
            show_notification(f"메모리 잠금 해제 실패: {result.stderr}", "red")
            play_failure_sound()  # 실패 사운드 재생
            return False
    except Exception as e:
        update_status("오류 발생", "red")
        show_notification(f"메모리 잠금 해제 중 오류 발생: {str(e)}", "red")
        play_failure_sound()  # 실패 사운드 재생
        return False

def lock_memory_procedure():
    update_status("메모리 잠금 중...", "orange")
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
        print("lock_memory_procedure 명령 실행 중...")
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(f"lock_memory_procedure 명령 출력: {result.stdout}")
        print(f"lock_memory_procedure 명령 오류 출력: {result.stderr}")
        if result.returncode == 0:
            update_status("메모리 잠금 성공", "green")
            show_notification("메모리 잠금에 성공했습니다.", "green")
            play_success_sound()  # 최종 성공 사운드 재생
            update_led(led_success, True)
            print("메모리 잠금에 성공했습니다.")
        else:
            update_status("메모리 잠금 실패", "red")
            show_notification(f"메모리 잠금 실패: {result.stderr}", "red")
            play_failure_sound()  # 실패 사운드 재생
            update_led(led_error, True)
            update_led(led_error1, True)
            print(f"메모리 잠금 실패: {result.stderr}")
    except Exception as e:
        update_status("오류 발생", "red")
        show_notification(f"메모리 잠금 중 오류 발생: {str(e)}", "red")
        play_failure_sound()  # 실패 사운드 재생
        print(f"메모리 잠금 중 오류 발생: {e}")

# 상태 업데이트 함수
def update_status(message, color):
    status_label.config(text=f"상태: {message}", fg=color)
    print(f"상태 업데이트: {message} ({color})")

# 알림 메시지 레이블 (상태 레이블 아래에 추가)
notification_label = tk.Label(root, text="", font=("Helvetica", 12), fg="green")
notification_label.pack(pady=5)

def show_notification(message, color="green", duration=3000):
    notification_label.config(text=message, fg=color)
    print(f"알림 표시: {message} ({color})")
    root.after(duration, lambda: notification_label.config(text=""))

def execute_command(command_index):
    global is_executing, connection_success, connection_failed_since_last_success
    is_executing = True
    update_status("명령 실행 중...", "orange")
    update_led(led_success, False)
    update_led(led_error, False)
    update_led(led_error1, False)
    print(f"명령 실행 시작: {command_names[command_index]}")

    if command_index == len(commands) - 1:
        git_pull()
        is_executing = False
        return

    if command_index == 7:   # 시스템 업데이트
        lock_memory_procedure()
        is_executing = False
        return

    if not unlock_memory():
        update_status("메모리 잠금 해제 실패", "red")
        show_notification("메모리 잠금 해제 실패", "red")
        play_failure_sound()  # 실패 사운드 재생
        is_executing = False
        return

    update_status("업데이트 중...", "orange")
    try:
        print(f"업데이트 명령 실행 중: {commands[command_index]}")
        process = subprocess.Popen(commands[command_index], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        start_time = time.time()
        max_duration = 6
        progress_increment = 20 / max_duration

        while process.poll() is None:
            elapsed = time.time() - start_time
            current_progress = 30 + (elapsed * progress_increment)
            current_progress = min(current_progress, 80)
            update_status(f"업데이트 중... {int(current_progress)}%", "orange")
            time.sleep(0.5)

        result = process.returncode
        print(f"명령어 실행 완료: {command_names[command_index]} (Return code: {result})")
        if result == 0:
            update_status("업데이트 성공!", "green")
            show_notification("업데이트에 성공했습니다.", "green")
            play_success_sound()  # 성공 사운드 재생
            update_led(led_success, True)
            lock_memory_procedure()
        else:
            update_status("업데이트 실패", "red")
            show_notification(f"'{commands[command_index]}' 업데이트 실패!", "red")
            play_failure_sound()  # 실패 사운드 재생
            update_led(led_error, True)
            update_led(led_error1, True)
            print(f"업데이트 실패: {commands[command_index]}")
    except Exception as e:
        update_status("업데이트 오류", "red")
        show_notification(f"업데이트 중 오류 발생:\n{str(e)}", "red")
        play_failure_sound()  # 실패 사운드 재생
        print(f"업데이트 중 오류 발생: {e}")
    finally:
        is_executing = False
        print("명령 실행 종료.")

# STM32 연결 상태 확인 함수
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
                connection_failed_since_last_success = False
            else:
                print("STM32 연결 성공")
                connection_success = False  # 연속적인 성공을 방지
            return True
        else:
            print("STM32 연결 실패:", result.stderr)
            connection_failed_since_last_success = True
            return False
    except Exception as e:
        print(f"오류 발생: {e}")
        connection_failed_since_last_success = True
        return False

# 실시간 업데이트를 위한 함수
def realtime_update():
    while True:
        if not is_executing:
            # STM32 연결 상태 확인 및 자동 모드일 때 명령 실행
            if is_auto_mode and check_stm32_connection() and connection_success:
                print("자동 모드: 업데이트를 실행합니다.")
                execute_command(current_command_index)
        time.sleep(1)  # 1초마다 확인

# --- 새로 추가된 부분 시작 ---

def check_for_updates():
    """
    새로운 커밋이 있는지 확인하는 함수.
    """
    try:
        print("업데이트 확인: git-pull.sh 실행 중...")
        # git-pull.sh 스크립트를 실행하여 업데이트 확인 및 적용
        result = subprocess.run(['/home/user/stm32/git-pull.sh'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(f"git-pull.sh 출력: {result.stdout}")
        print(f"git-pull.sh 오류 출력: {result.stderr}")

        # 스크립트의 반환값으로 업데이트 필요 여부 판단
        if result.returncode == 1:
            # 업데이트가 수행되었음을 알림
            print("업데이트가 수행되었습니다.")
            return True
        elif result.returncode == 0:
            # 이미 최신 상태임을 알림
            print("시스템이 이미 최신 상태입니다.")
            return False
        else:
            # 기타 오류
            print(f"업데이트 실패 또는 상태 판단 불가: {result.stderr}")
            return False
    except Exception as e:
        print(f"업데이트 확인 중 오류 발생: {e}")
        return False

def show_update_prompt():
    """
    업데이트가 있을 경우 GUI 내에 업데이트 요청 프레임을 표시하는 함수.
    """
    show_update_frame()

def check_updates_and_prompt():
    """
    업데이트가 있는지 확인하고, 있다면 GUI 내에 업데이트 요청을 표시하는 함수.
    """
    print("업데이트 확인을 시작합니다.")
    updates_available = check_for_updates()
    if updates_available:
        print("업데이트가 감지되었습니다. 업데이트 프레임을 표시합니다.")
        # Tkinter는 스레드 안전하지 않으므로 main thread에서 프레임 표시
        root.after(0, show_update_prompt)
    else:
        print("업데이트가 없습니다.")

# 업데이트 체크 주기 설정 (1초마다 확인)
def periodic_update_check():
    threading.Thread(target=check_updates_and_prompt, daemon=True).start()
    root.after(1000, periodic_update_check)  # 1,000ms = 1초

# --- 새로 추가된 부분 끝 ---

# --- 새로 추가된 부분 시작 ---

def extract_file_from_stm32():
    global is_executing
    is_executing = True
    update_status("파일 추출 중...", "orange")
    update_led(led_success, False)
    update_led(led_error, False)
    update_led(led_error1, False)
    print("파일 추출을 시작합니다.")

    # 추출할 파일의 STM32 메모리 주소 및 크기 설정
    memory_address = "0x08000000"  # 예시 주소
    memory_size = "256K"

    # 현재 날짜와 시간을 기반으로 파일 이름 지정
    now = datetime.now()
    filename = now.strftime("%Y%m%d_%H%M%S") + ".bin"
    save_path = f"/home/user/stm32/Download/{filename}"
    print(f"파일 저장 경로: {save_path}")

    # OpenOCD 명령을 사용하여 STM32의 메모리 덤프
    openocd_command = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", f"flash read_bank 0 {save_path} 0",
        "-c", "reset run",
        "-c", "shutdown",
    ]

    # 명령 실행 및 결과 확인
    try:
        print("OpenOCD 명령 실행 중...")
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(f"OpenOCD 출력: {result.stdout}")
        print(f"OpenOCD 오류 출력: {result.stderr}")
        if result.returncode == 0:
            print("파일 추출 성공!")
            show_notification("파일 추출에 성공했습니다.", "green")
            play_success_sound()
            upload_to_ftp(save_path, filename)
        else:
            print("파일 추출 실패. 오류 코드:", result.returncode)
            print("오류 메시지:", result.stderr)
            update_status("파일 추출 실패", "red")
            show_notification(f"파일 추출 실패.\n오류 메시지: {result.stderr}", "red")
            play_failure_sound()
            update_led(led_error, True)
            update_led(led_error1, True)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))
        update_status("파일 추출 오류", "red")
        show_notification(f"파일 추출 중 오류 발생:\n{str(e)}", "red")
        play_failure_sound()
        update_led(led_error, True)
        update_led(led_error1, True)
    finally:
        is_executing = False
        print("파일 추출 작업 종료.")

def upload_to_ftp(file_path, filename):
    ftp_server = "79webhard.com"
    ftp_user = "stm32"
    ftp_password = "Gds00700@"
    ftp_path = "/home"

    try:
        print(f"FTP 서버에 연결 중: {ftp_server}")
        with ftplib.FTP(ftp_server) as ftp:
            ftp.login(ftp_user, ftp_password)
            ftp.cwd(ftp_path)
            print(f"FTP 경로 변경: {ftp_path}")

            with open(file_path, 'rb') as file:
                ftp.storbinary(f'STOR {filename}', file)
            
            print("파일 FTP 업로드 성공!")
            show_notification("파일 FTP 업로드에 성공했습니다.", "green")
            play_success_sound()
            update_led(led_success, True)
    except ftplib.all_errors as e:
        print("FTP 업로드 실패:", str(e))
        update_status("FTP 업로드 실패", "red")
        show_notification(f"FTP 업로드 실패:\n{str(e)}", "red")
        play_failure_sound()
        update_led(led_error, True)
        update_led(led_error1, True)

# --- 새로 추가된 부분 끝 ---

# --- 새로 추가된 부분 시작 ---

# 업데이트 체크 및 프롬프트 실행 (GUI 초기화 후)
periodic_update_check()

# --- 새로 추가된 부분 끝 ---

# 백그라운드 스레드 시작
threading.Thread(target=realtime_update, daemon=True).start()

# IP 주소 초기 업데이트
update_ip_label()

# 창이 다른 창에 의해 가려졌을 때 다시 최상위로 올리는 함수
def keep_on_top():
    root.attributes("-topmost", True)
    root.lift()
    root.after(1000, keep_on_top)  # 1초마다 이 함수 재실행
    #print("keep_on_top 실행")

# 포커스 이벤트 핸들러
def on_focus_out(event):
    # 창이 포커스를 잃었을 때 최상위로 다시 올리기
    root.after(100, lambda: root.attributes("-topmost", True))
    root.after(100, lambda: root.lift())
    print("포커스 아웃 이벤트 발생: 창을 최상위로 올립니다.")

root.bind("<FocusOut>", on_focus_out)

# 최상위 유지 함수 시작
keep_on_top()

# Tkinter 메인 루프 실행
root.mainloop()
