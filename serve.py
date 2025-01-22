import tkinter as tk
from datetime import datetime
import threading
import time
import os
import sys
import socket
import subprocess
import pygame  # pygame 임포트
import ftplib

# DISPLAY 환경 변수 설정 (Linux 환경에서 GUI 사용 시 필요)
os.environ['DISPLAY'] = ':0'

# pygame 초기화
pygame.mixer.init()

# 사운드 파일 경로 설정
script_dir = os.path.dirname(os.path.abspath(__file__))
SUCCESS_SOUND_PATH = os.path.join(script_dir, 'success.mp3')
FAILURE_SOUND_PATH = os.path.join(script_dir, 'failure.mp3')  # 실패 사운드 파일 경로 (별도 파일 권장)

# 사운드 로드 함수
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

# 사운드 재생 함수
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
selected_branch = "master"  # 기본 브랜치 (드롭다운에서 선택 가능)
commands = [
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ORG.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HMDS.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HMDS-IR.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/ARF-T.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/HC100.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/SAT4010.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg -f /usr/local/share/openocd/scripts/target/stm32f1x.cfg -c \"program /home/user/stm32/Program/IPA.bin verify reset exit 0x08000000\"",
    "git_pull",  # 업데이트 시 git_pull() 함수 호출
]
command_names = ["ORG", "HMDS", "HMDS-IR", "ARF-T", "HC100", "SAT4010", "IPA", "시스템 업데이트"]

# 상태 메시지 및 실행 상태
status_message = ""
is_executing = False
need_update = False
connection_success = False
connection_failed_since_last_success = False

# Tkinter GUI 설정
root = tk.Tk()
root.title("업데이트 관리자")
root.geometry("800x600")
root.attributes("-topmost", True)
root.lift()

mode_label = tk.Label(root, text="", font=("Helvetica", 17))
mode_label.pack(pady=10)
mode_label.config(text=f"모드: {'자동' if is_auto_mode else '수동'}")

current_command_label = tk.Label(root, text=f"현재 명령어: {command_names[current_command_index]}", font=("Helvetica", 14))
current_command_label.pack(pady=5)

status_label = tk.Label(root, text="상태: 대기 중", font=("Helvetica", 14), fg="blue")
status_label.pack(pady=5)

ip_label = tk.Label(root, text="IP 주소: 로딩 중...", font=("Helvetica", 12))
ip_label.pack(pady=5)

# LED 상태 표시 (색상으로 표현)
led_frame = tk.Frame(root)
led_frame.pack(pady=10)

led_success = tk.Label(led_frame, text="성공 LED", bg="grey", width=10, height=2)
led_success.grid(row=0, column=0, padx=5)

led_error = tk.Label(led_frame, text="오류 LED1", bg="grey", width=10, height=2)
led_error.grid(row=0, column=1, padx=5)

led_error1 = tk.Label(led_frame, text="오류 LED2", bg="grey", width=10, height=2)
led_error1.grid(row=0, column=2, padx=5)

# 명령 버튼 프레임
button_frame = tk.Frame(root)
button_frame.pack(pady=20)

def update_led(led_label, status):
    def set_color():
        led_label.config(bg="green" if status else "grey")
    root.after(0, set_color)

def toggle_mode_gui():
    global is_auto_mode
    if is_executing:
        show_notification("현재 명령이 실행 중입니다.", "red")
        return
    is_auto_mode = not is_auto_mode
    mode_label.config(text=f"모드: {'자동' if is_auto_mode else '수동'}")
    show_notification(f"모드가 {'자동' if is_auto_mode else '수동'}으로 변경되었습니다.", "blue")

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
    if is_executing:
        show_notification("이미 명령이 실행 중입니다.", "red")
        return
    threading.Thread(target=execute_command, args=(current_command_index,), daemon=True).start()

previous_button = tk.Button(button_frame, text="이전", command=previous_command_gui, width=10, height=2)
previous_button.grid(row=0, column=0, padx=10)
next_button = tk.Button(button_frame, text="다음", command=next_command_gui, width=10, height=2)
next_button.grid(row=0, column=1, padx=10)
execute_button = tk.Button(button_frame, text="확인", command=execute_command_gui, width=10, height=2)
execute_button.grid(row=0, column=2, padx=10)
toggle_mode_button = tk.Button(button_frame, text="모드 전환", command=toggle_mode_gui, width=10, height=2, bg="orange")
toggle_mode_button.grid(row=0, column=3, padx=10)

# --- 브랜치 드롭다운 기능 (실시간 업데이트) ---
branch_frame = tk.Frame(root)
branch_frame.pack(pady=10)

branch_label = tk.Label(branch_frame, text="브랜치 선택:", font=("Helvetica", 12))
branch_label.grid(row=0, column=0, padx=5)

# 선택된 브랜치를 담는 변수 (기본값: selected_branch)
branch_var = tk.StringVar(value=selected_branch)

# OptionMenu 위젯 (초기 목록은 빈 리스트; 이후 refresh_git_branches()에서 갱신)
branch_menu = tk.OptionMenu(branch_frame, branch_var, ())
branch_menu.config(width=20, font=("Helvetica", 12))
branch_menu.grid(row=0, column=1, padx=5)

def get_git_branches():
    """
    /home/user/stm32 디렉토리에서 로컬 브랜치 목록을 가져와 리스트로 반환합니다.
    """
    try:
        result = subprocess.run(["git", "branch"], cwd="/home/user/stm32",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            branches = []
            for line in result.stdout.splitlines():
                # '*' 표시와 공백 제거
                branch = line.strip().lstrip("*").strip()
                if branch:
                    branches.append(branch)
            return branches
        else:
            print("브랜치 목록 조회 실패:", result.stderr)
            return []
    except Exception as e:
        print("브랜치 목록 조회 중 오류 발생:", e)
        return []

def refresh_git_branches():
    branches = get_git_branches()
    menu = branch_menu["menu"]
    menu.delete(0, "end")
    for br in branches:
        menu.add_command(label=br, command=lambda value=br: branch_var.set(value))
    # 만약 선택된 브랜치가 목록에 없으면 기본값을 첫번째 항목으로 설정
    if branches:
        if branch_var.get() not in branches:
            branch_var.set(branches[0])
    # 10초마다 갱신 (원하는 시간으로 수정 가능)
    root.after(10000, refresh_git_branches)

def change_branch():
    global selected_branch
    new_branch = branch_var.get().strip()
    if new_branch == "":
        show_notification("브랜치가 선택되지 않았습니다.", "red")
        return
    selected_branch = new_branch
    try:
        # 지정한 리포지토리에서 브랜치 체크아웃 수행
        result = subprocess.run(["git", "checkout", selected_branch],
                                cwd="/home/user/stm32",
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True)
        if result.returncode == 0:
            update_status(f"브랜치 변경됨: {selected_branch}", "green")
            show_notification(f"브랜치가 {selected_branch}(으)로 변경되었습니다.", "green")
            play_success_sound()
            restart_script()  # 브랜치 변경 시 스크립트 재시작
        else:
            update_status("브랜치 변경 실패", "red")
            show_notification(f"브랜치 변경 실패:\n{result.stderr}", "red")
            play_failure_sound()
    except Exception as e:
        update_status("브랜치 변경 오류", "red")
        show_notification(f"브랜치 변경 중 오류 발생:\n{str(e)}", "red")
        play_failure_sound()

change_branch_button = tk.Button(branch_frame, text="브랜치 변경", command=change_branch, width=15, height=1, bg="lightblue")
change_branch_button.grid(row=0, column=2, padx=5)

# 초기에 브랜치 목록 갱신 시작
refresh_git_branches()

# --- 추가 기능 : 파일 추출 및 FTP 업로드 ---
extra_button_frame = tk.Frame(root)
extra_button_frame.pack(pady=10)

def extract_and_upload_gui():
    if is_executing:
        show_notification("현재 명령이 실행 중입니다.", "red")
        return
    threading.Thread(target=extract_file_from_stm32, daemon=True).start()

extract_button = tk.Button(extra_button_frame, text="파일 추출 및 업로드", command=extract_and_upload_gui, width=20, height=2, bg="purple", fg="white")
extract_button.pack(pady=5)

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
    root.after(5000, update_ip_label)

# Git Pull 함수 (원격 기준으로 무조건 동기화: 조건부 체크아웃 + 강제 reset 및 로컬 트랙킹 브랜치 생성/삭제 적용)
def git_pull():
    global selected_branch
    shell_script_path = '/home/user/stm32/git-pull.sh'
    with open(shell_script_path, 'w') as script_file:
        script_file.write("#!/bin/bash\n")
        script_file.write("cd /home/user/stm32\n")
        # 선택한 브랜치를 변수에 저장
        script_file.write("branch='{}'\n".format(selected_branch))
        # 현재 브랜치 확인 후, 다른 경우에만 체크아웃
        script_file.write("current_branch=$(git branch --show-current)\n")
        script_file.write("if [ \"$current_branch\" != \"$branch\" ]; then\n")
        script_file.write("    git checkout \"$branch\"\n")
        script_file.write("fi\n")
        # 최신 원격 정보를 가져오고 prune 수행
        script_file.write("git fetch --prune\n")
        # 원격의 모든 브랜치(-> 제외)에서 로컬 트랙킹 브랜치 생성 시도
        script_file.write("for remote in $(git branch -r | grep -v '\\->'); do\n")
        script_file.write("    git branch --track \"${remote#origin/}\" \"$remote\" 2>/dev/null || echo \"Branch ${remote#origin/} already exists.\"\n")
        script_file.write("done\n")
        # 선택한 브랜치로 체크아웃 후 강제 reset
        script_file.write("git checkout $branch\n")
        script_file.write("git reset --hard origin/$branch\n")
        script_file.write("echo '브랜치 업데이트 완료:'$branch\n")
    os.chmod(shell_script_path, 0o755)

    update_status("시스템 업데이트 중...", "orange")
    try:
        result = subprocess.run([shell_script_path],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True)
        stdout = result.stdout.strip()
        if result.returncode == 0:
            update_status("업데이트 완료", "green")
            show_notification("브랜치가 원격(깃허브) 상태로 강제 동기화되었습니다.", "green")
            play_success_sound()
            restart_script()
        else:
            update_status("업데이트 실패", "red")
            show_notification(f"GitHub 업데이트 실패.\n오류 메시지: {result.stderr}", "red")
            play_failure_sound()
            update_led(led_error, True)
            update_led(led_error1, True)
    except Exception as e:
        update_status("업데이트 오류", "red")
        show_notification(f"업데이트 중 오류 발생:\n{str(e)}", "red")
        play_failure_sound()
        update_led(led_error, True)
        update_led(led_error1, True)

def restart_script():
    update_status("스크립트 재시작 중...", "orange")
    def restart():
        time.sleep(3)
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
            show_notification(f"메모리 잠금 해제 실패:\n{result.stderr}", "red")
            play_failure_sound()
            update_led(led_error, True)
            update_led(led_error1, True)
            return False
    except Exception as e:
        update_status("오류 발생", "red")
        show_notification(f"메모리 잠금 해제 중 오류 발생:\n{str(e)}", "red")
        play_failure_sound()
        update_led(led_error, True)
        update_led(led_error1, True)
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
            play_success_sound()
            update_led(led_success, True)
        else:
            update_status("메모리 잠금 실패", "red")
            show_notification(f"메모리 잠금 실패:\n{result.stderr}", "red")
            play_failure_sound()
            update_led(led_error, True)
            update_led(led_error1, True)
    except Exception as e:
        update_status("오류 발생", "red")
        show_notification(f"메모리 잠금 중 오류 발생:\n{str(e)}", "red")
        play_failure_sound()

# 상태 업데이트 및 알림 함수
def update_status(message, color):
    status_label.config(text=f"상태: {message}", fg=color)

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

    if command_index == 7:  # 시스템 업데이트 (시스템 자체의 업데이트 처리)
        lock_memory_procedure()
        is_executing = False
        return

    if not unlock_memory():
        update_status("메모리 잠금 해제 실패", "red")
        show_notification("메모리 잠금 해제 실패", "red")
        play_failure_sound()
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
            play_failure_sound()
            update_led(led_error, True)
            update_led(led_error1, True)
    except Exception as e:
        update_status("업데이트 오류", "red")
        show_notification(f"업데이트 중 오류 발생:\n{str(e)}", "red")
        play_failure_sound()
    finally:
        is_executing = False

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

def realtime_update():
    while True:
        if not is_executing:
            if is_auto_mode and check_stm32_connection() and connection_success:
                execute_command(current_command_index)
        time.sleep(1)

threading.Thread(target=realtime_update, daemon=True).start()

update_ip_label()

def keep_on_top():
    root.attributes("-topmost", True)
    root.lift()
    root.after(1000, keep_on_top)

def on_focus_out(event):
    root.after(100, lambda: root.attributes("-topmost", True))
    root.after(100, lambda: root.lift())

root.bind("<FocusOut>", on_focus_out)
keep_on_top()

# --- 파일 추출 및 FTP 업로드 기능 ---
def extract_file_from_stm32():
    global is_executing
    is_executing = True
    update_status("파일 추출 중...", "orange")
    update_led(led_success, False)
    update_led(led_error, False)
    update_led(led_error1, False)

    memory_address = "0x08000000"  # 예시 주소
    memory_size = "256K"

    now = datetime.now()
    filename = now.strftime("%Y%m%d_%H%M%S") + ".bin"
    save_path = f"/home/user/stm32/Download/{filename}"

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

    try:
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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

def upload_to_ftp(file_path, filename):
    ftp_server = "79webhard.com"
    ftp_user = "stm32"
    ftp_password = "Gds00700@"
    ftp_path = "/home"

    try:
        with ftplib.FTP(ftp_server) as ftp:
            ftp.login(ftp_user, ftp_password)
            ftp.cwd(ftp_path)
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

# Tkinter 메인 루프 실행
root.mainloop()
