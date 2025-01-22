import tkinter as tk
from tkinter import messagebox
from datetime import datetime
import threading
import time
import os
import sys
import socket
import subprocess
import pygame  # pygame 임포트
import ftplib

# ----------------------------
# 기본 환경 설정 및 pygame 초기화
# ----------------------------
os.environ['DISPLAY'] = ':0'
pygame.mixer.init()

# 사운드 파일 경로 설정
script_dir = os.path.dirname(os.path.abspath(__file__))
SUCCESS_SOUND_PATH = os.path.join(script_dir, 'success.mp3')
FAILURE_SOUND_PATH = os.path.join(script_dir, 'failure.mp3')

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

# ----------------------------
# 전역 변수 설정 (메뉴 사용 전 선언)
# ----------------------------
selected_branch = "master"  # 반드시 Tkinter 위젯 생성 전에 선언되어야 함.
is_auto_mode = True
current_command_index = 0
# 시스템 업데이트 기능은 메뉴에서 제외하고, openocd 관련 명령어만 포함함.
commands = [
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg " \
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg " \
    "-c \"program /home/user/stm32/Program/ORG.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg " \
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg " \
    "-c \"program /home/user/stm32/Program/HMDS.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg " \
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg " \
    "-c \"program /home/user/stm32/Program/HMDS-IR.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg " \
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg " \
    "-c \"program /home/user/stm32/Program/ARF-T.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg " \
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg " \
    "-c \"program /home/user/stm32/Program/HC100.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg " \
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg " \
    "-c \"program /home/user/stm32/Program/SAT4010.bin verify reset exit 0x08000000\"",
    "sudo openocd -f /usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg " \
    "-f /usr/local/share/openocd/scripts/target/stm32f1x.cfg " \
    "-c \"program /home/user/stm32/Program/IPA.bin verify reset exit 0x08000000\""
]
command_names = ["ORG", "HMDS", "HMDS-IR", "ARF-T", "HC100", "SAT4010", "IPA"]

status_message = ""
is_executing = False
connection_success = False
connection_failed_since_last_success = False

# 업데이트 알림 관련 전역 변수 (실시간 업데이트 체크)
checking_updates = True
ignore_commit = None
update_notification_frame = None
synced_branches = set()  # 이미 동기화(또는 알림)한 새로운 브랜치 목록

# ----------------------------
# Tkinter GUI 구성
# ----------------------------
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

# LED 상태 표시 (색상)
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

# --- 브랜치 드롭다운 ---
branch_frame = tk.Frame(root)
branch_frame.pack(pady=10)

branch_label = tk.Label(branch_frame, text="브랜치 선택:", font=("Helvetica", 12))
branch_label.grid(row=0, column=0, padx=5)

branch_var = tk.StringVar(value=selected_branch)
branch_menu = tk.OptionMenu(branch_frame, branch_var, ())
branch_menu.config(width=20, font=("Helvetica", 12))
branch_menu.grid(row=0, column=1, padx=5)

def get_git_branches():
    try:
        result = subprocess.run(["git", "branch"], cwd="/home/user/stm32",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            branches = []
            for line in result.stdout.splitlines():
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
    if branches and branch_var.get() not in branches:
        branch_var.set(branches[0])
    root.after(10000, refresh_git_branches)

def change_branch():
    global selected_branch
    new_branch = branch_var.get().strip()
    if new_branch == "":
        show_notification("브랜치가 선택되지 않았습니다.", "red")
        return
    selected_branch = new_branch
    try:
        result = subprocess.run(["git", "checkout", selected_branch],
                                cwd="/home/user/stm32",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            update_status(f"브랜치 변경됨: {selected_branch}", "green")
            show_notification(f"브랜치가 {selected_branch}(으)로 변경되었습니다.", "green")
            play_success_sound()
            restart_script()  # 변경 후 재시작
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

refresh_git_branches()

# --- 파일 추출 및 FTP 업로드 ---
extra_button_frame = tk.Frame(root)
extra_button_frame.pack(pady=10)

def extract_and_upload_gui():
    if is_executing:
        show_notification("현재 명령이 실행 중입니다.", "red")
        return
    threading.Thread(target=extract_file_from_stm32, daemon=True).start()

extract_button = tk.Button(extra_button_frame, text="파일 추출 및 업로드", command=extract_and_upload_gui, width=20, height=2, bg="purple", fg="white")
extract_button.pack(pady=5)

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

# ----------------------------
# 실시간 업데이트 체크 및 사용자 알림 기능
# ----------------------------
def update_system(root):
    global checking_updates
    checking_updates = False  # 업데이트 중에는 체크 중지
    try:
        result = subprocess.run(['git', 'pull'], capture_output=True, text=True)
        message = "업데이트 완료. 애플리케이션을 재시작합니다."
        root.after(2000, restart_script)
    except Exception as e:
        message = f"업데이트 중 오류 발생: {e}"
    finally:
        checking_updates = True
    messagebox.showinfo("시스템 업데이트", message)

def sync_branches(root):
    try:
        subprocess.check_call(['git', 'fetch', '--all'])
        subprocess.check_call(['git', 'pull', '--all'])
        subprocess.check_call(['git', 'remote', 'prune', 'origin'])
    except subprocess.CalledProcessError as e:
        messagebox.showerror("브랜치 동기화 오류", f"브랜치 동기화 중 오류가 발생했습니다: {e}")

def prune_deleted_branches(root):
    try:
        subprocess.check_call(['git', 'fetch', '--prune'])
    except subprocess.CalledProcessError as e:
        messagebox.showerror("브랜치 정리 오류", f"브랜치 정리 중 오류가 발생했습니다: {e}")

def show_update_notification(root, remote_commit):
    global update_notification_frame
    if update_notification_frame and update_notification_frame.winfo_exists():
        return

    def on_yes():
        start_update(root, remote_commit)
    def on_no():
        ignore_update(remote_commit)

    update_notification_frame = tk.Frame(root)
    update_notification_frame.place(relx=0.5, rely=0.95, anchor='center')
    update_label = tk.Label(update_notification_frame,
                            text="새로운 버전이 있습니다. 업데이트를 진행하시겠습니까?",
                            font=("Arial", 15), fg="red")
    update_label.pack(side="left", padx=5)
    yes_button = tk.Button(update_notification_frame, text="예", command=on_yes, font=("Arial", 14), fg="red")
    yes_button.pack(side="left", padx=5)
    no_button = tk.Button(update_notification_frame, text="건너뛰기", command=on_no, font=("Arial", 14), fg="red")
    no_button.pack(side="left", padx=5)

def show_temporary_notification(root, message, duration=5000):
    notification_frame = tk.Frame(root, bg="green")
    notification_frame.place(relx=0.5, rely=0.95, anchor='center')
    notification_label_temp = tk.Label(notification_frame, text=message, font=("Arial", 14), fg="white", bg="green")
    notification_label_temp.pack(side="left", padx=5)
    root.after(duration, notification_frame.destroy)

def start_update(root, remote_commit):
    global update_notification_frame, ignore_commit, checking_updates, synced_branches
    ignore_commit = None
    checking_updates = False
    if update_notification_frame and update_notification_frame.winfo_exists():
        update_notification_frame.destroy()
    threading.Thread(target=update_system, args=(root,), daemon=True).start()

def ignore_update(remote_commit):
    global ignore_commit, update_notification_frame
    ignore_commit = remote_commit
    with open("ignore_commit.txt", "w") as file:
        file.write(ignore_commit)
    if update_notification_frame and update_notification_frame.winfo_exists():
        update_notification_frame.destroy()

def check_for_updates(root):
    global synced_branches
    while checking_updates:
        try:
            current_branch = subprocess.check_output(['git', 'branch', '--show-current']).strip().decode()
            remote_info = subprocess.check_output(['git', 'ls-remote', '--heads', 'origin']).strip().decode().splitlines()
            remote_branches = [line.split()[1].split('/')[-1] for line in remote_info]
            local_info = subprocess.check_output(['git', 'branch', '--list']).strip().decode().splitlines()
            local_branches = [line.strip().replace('* ', '') for line in local_info]
            tracked_remote = subprocess.check_output(['git', 'branch', '-r']).strip().decode().splitlines()
            tracked_remote = [line.split('/')[-1].strip() for line in tracked_remote]
            deleted_branches = [b for b in tracked_remote if b not in remote_branches]
            # 새로운 브랜치: 원격에 존재하지만 로컬에 없는 것 중, 이미 동기화한 것은 제외
            new_branches = [b for b in remote_branches if b not in local_branches and b not in synced_branches]
            remote_branch_info = subprocess.check_output(['git', 'ls-remote', '--heads', 'origin', current_branch]).strip().decode()
            remote_commit = remote_branch_info.split()[0] if remote_branch_info else None
            local_commit = subprocess.check_output(['git', 'rev-parse', current_branch]).strip().decode()
            if deleted_branches:
                prune_deleted_branches(root)
                show_temporary_notification(root, "삭제된 브랜치가 정리되었습니다.")
            elif new_branches:
                sync_branches(root)
                for branch in new_branches:
                    synced_branches.add(branch)
                show_temporary_notification(root, "새로운 브랜치가 동기화되었습니다.")
            elif local_commit != remote_commit and remote_commit != ignore_commit:
                show_update_notification(root, remote_commit)
        except Exception as e:
            print(f"업데이트 체크 중 오류 발생: {e}")
        time.sleep(1)

# ----------------------------
# 재시작 함수
# ----------------------------
def restart_script():
    update_status("스크립트 재시작 중...", "orange")
    def restart():
        time.sleep(3)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart, daemon=True).start()

# ----------------------------
# 메모리 잠금 해제 및 잠금 함수 (openocd)
# ----------------------------
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
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "stm32f1x lock 0",
        "-c", "reset run",
        "-c", "shutdown"
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

# ----------------------------
# 상태 업데이트 및 알림 함수
# ----------------------------
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
    try:
        if not unlock_memory():
            update_status("메모리 잠금 해제 실패", "red")
            show_notification("메모리 잠금 해제 실패", "red")
            play_failure_sound()
            is_executing = False
            return
        update_status("업데이트 중...", "orange")
        process = subprocess.Popen(commands[command_index], shell=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
threading.Thread(target=check_for_updates, args=(root,), daemon=True).start()

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

# ----------------------------
# 파일 추출 및 FTP 업로드
# ----------------------------
def extract_file_from_stm32():
    global is_executing
    is_executing = True
    update_status("파일 추출 중...", "orange")
    update_led(led_success, False)
    update_led(led_error, False)
    update_led(led_error1, False)
    memory_address = "0x08000000"
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

# ----------------------------
# git_pull() 함수: 로컬 모든 브랜치를 원격과 완전히 동기화 (강제)
# ----------------------------
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
        script_file.write("git fetch --all --prune\n")
        # 원격의 모든 브랜치에 대해 로컬 트랙킹 브랜치 강제 생성/덮어쓰기 (-B 옵션 사용)
        script_file.write("for remote in $(git branch -r | grep -v '\\->'); do\n")
        script_file.write("    git checkout -B \"${remote#origin/}\" \"$remote\"\n")
        script_file.write("done\n")
        # 선택한 브랜치로 체크아웃 후 강제 동기화
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
            show_notification("로컬 브랜치들이 원격과 완전히 동기화되었습니다.", "green")
            play_success_sound()
            restart_script()
        else:
            update_status("업데이트 실패", "red")
            show_notification(f"업데이트 실패.\n오류 메시지: {result.stderr}", "red")
            play_failure_sound()
            update_led(led_error, True)
            update_led(led_error1, True)
    except Exception as e:
        update_status("업데이트 오류", "red")
        show_notification(f"업데이트 중 오류 발생:\n{str(e)}", "red")
        play_failure_sound()
        update_led(led_error, True)
        update_led(led_error1, True)

# ----------------------------
# 재시작 함수
# ----------------------------
def restart_script():
    update_status("스크립트 재시작 중...", "orange")
    def restart():
        time.sleep(3)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart, daemon=True).start()

# ----------------------------
# 메모리 잠금 해제 및 잠금 함수 (openocd)
# ----------------------------
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
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", "stm32f1x lock 0",
        "-c", "reset run",
        "-c", "shutdown"
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

# ----------------------------
# 상태 업데이트 및 알림 함수
# ----------------------------
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
    try:
        if not unlock_memory():
            update_status("메모리 잠금 해제 실패", "red")
            show_notification("메모리 잠금 해제 실패", "red")
            play_failure_sound()
            is_executing = False
            return
        update_status("업데이트 중...", "orange")
        process = subprocess.Popen(commands[command_index], shell=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
threading.Thread(target=check_for_updates, args=(root,), daemon=True).start()

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

# ----------------------------
# 파일 추출 및 FTP 업로드
# ----------------------------
def extract_file_from_stm32():
    global is_executing
    is_executing = True
    update_status("파일 추출 중...", "orange")
    update_led(led_success, False)
    update_led(led_error, False)
    update_led(led_error1, False)
    memory_address = "0x08000000"
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

# ----------------------------
# Tkinter 메인 루프 실행
# ----------------------------
root.mainloop()
