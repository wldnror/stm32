import tkinter as tk
from datetime import datetime
import threading
import time
import os
import sys
import socket
import subprocess

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

# Tkinter GUI 설정
root = tk.Tk()
root.title("업데이트 관리자")
root.geometry("500x280")  # 필요에 따라 크기 조정
mode_label = tk.Label(root, text="", font=("Helvetica", 5))
mode_label.pack(pady=10)
current_command_label = tk.Label(root, text=f"현재 명령어: {command_names[current_command_index]}", font=("Helvetica", 14))
current_command_label.pack(pady=5)

status_label = tk.Label(root, text="상태: 대기 중", font=("Helvetica", 14), fg="blue")
status_label.pack(pady=5)

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
        return
    is_auto_mode = not is_auto_mode
    mode_label.config(text=f"모드: {'자동' if is_auto_mode else '수동'}")

def next_command_gui():
    global current_command_index
    if is_executing:
        show_notification("현재 명령이 실행 중입니다.", "red")
        return
    current_command_index = (current_command_index + 1) % len(commands)
    current_command_label.config(text=f"현재 명령어: {command_names[current_command_index]}")

def previous_command_gui():
    global current_command_index
    if is_executing:
        show_notification("현재 명령이 실행 중입니다.", "red")
        return
    current_command_index = (current_command_index - 1) % len(commands)
    current_command_label.config(text=f"현재 명령어: {command_names[current_command_index]}")

def execute_command_gui():
    global is_executing
    if is_executing:
        show_notification("이미 명령이 실행 중입니다.", "red")
        return
    threading.Thread(target=execute_command, args=(current_command_index,), daemon=True).start()

# 버튼 생성 (이전, 다음, 확인)
previous_button = tk.Button(button_frame, text="이전", command=previous_command_gui, width=10, height=2)
previous_button.grid(row=0, column=0, padx=10)

next_button = tk.Button(button_frame, text="다음", command=next_command_gui, width=10, height=2)
next_button.grid(row=0, column=1, padx=10)

execute_button = tk.Button(button_frame, text="확인", command=execute_command_gui, width=10, height=2)
execute_button.grid(row=0, column=2, padx=10)

# IP 주소 업데이트 함수
def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        return "0.0.0.0"

def update_ip_label():
    ip = get_ip_address()
    ip_label.config(text=f"IP 주소: {ip}")
    root.after(5000, update_ip_label)  # 5초마다 업데이트

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

    update_status("시스템 업데이트 중...", "orange")
    try:
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        update_led(led_success, False)
        update_led(led_error, False)
        update_led(led_error1, False)
        
        if result.returncode == 0:
            if "이미 최신 상태" in result.stdout:
                update_status("이미 최신 상태", "blue")
                show_notification("시스템이 이미 최신 상태입니다.", "blue")
            else:
                update_status("업데이트 성공!", "green")
                show_notification("시스템 업데이트에 성공했습니다.", "green")
                restart_script()
        else:
            update_status("업데이트 실패", "red")
            show_notification(f"GitHub 업데이트 실패.\n오류 메시지: {result.stderr}", "red")
            update_led(led_error, True)
            update_led(led_error1, True)
    except Exception as e:
        update_status("업데이트 오류", "red")
        show_notification(f"업데이트 중 오류 발생:\n{str(e)}", "red")
        update_led(led_error, True)
        update_led(led_error1, True)

def restart_script():
    update_status("스크립트 재시작 중...", "orange")
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
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            update_status("메모리 잠금 해제 성공!", "green")
            show_notification("메모리 잠금 해제에 성공했습니다.", "green")
            return True
        else:
            update_status("메모리 잠금 해제 실패", "red")
            show_notification(f"메모리 잠금 해제 실패: {result.stderr}", "red")
            return False
    except Exception as e:
        update_status("오류 발생", "red")
        show_notification(f"메모리 잠금 해제 중 오류 발생: {str(e)}", "red")
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
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            update_status("메모리 잠금 성공", "green")
            show_notification("메모리 잠금에 성공했습니다.", "green")
            update_led(led_success, True)
        else:
            update_status("메모리 잠금 실패", "red")
            show_notification(f"메모리 잠금 실패: {result.stderr}", "red")
            update_led(led_error, True)
            update_led(led_error1, True)
    except Exception as e:
        update_status("오류 발생", "red")
        show_notification(f"메모리 잠금 중 오류 발생: {str(e)}", "red")

# 상태 업데이트 함수
def update_status(message, color):
    status_label.config(text=f"상태: {message}", fg=color)

# 알림 메시지 레이블 (상태 레이블 아래에 추가)
notification_label = tk.Label(root, text="", font=("Helvetica", 12), fg="green")
notification_label.pack(pady=5)

def show_notification(message, color="green", duration=3000):
    notification_label.config(text=message, fg=color)
    root.after(duration, lambda: notification_label.config(text=""))

def execute_command(command_index):
    global is_executing, connection_success, connection_failed_since_last_success
    is_executing = True
    update_status("명령 실행 중...", "orange")
    update_led(led_success, False)
    update_led(led_error, False)
    update_led(led_error1, False)

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
        is_executing = False
        return

    update_status("업데이트 중...", "orange")
    try:
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
        if result == 0:
            update_status("업데이트 성공!", "green")
            show_notification("업데이트에 성공했습니다.", "green")
            update_led(led_success, True)
            lock_memory_procedure()
        else:
            update_status("업데이트 실패", "red")
            show_notification(f"'{commands[command_index]}' 업데이트 실패!", "red")
            update_led(led_error, True)
            update_led(led_error1, True)
    except Exception as e:
        update_status("업데이트 오류", "red")
        show_notification(f"업데이트 중 오류 발생:\n{str(e)}", "red")
    finally:
        is_executing = False

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
                execute_command(current_command_index)
        time.sleep(1)

# 백그라운드 스레드 시작
threading.Thread(target=realtime_update, daemon=True).start()

# IP 주소 초기 업데이트
update_ip_label()

# Tkinter 메인 루프 실행
root.mainloop()
