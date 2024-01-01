import subprocess
import re
import time
import sys

# main.py 파일의 경로를 정확하게 지정하세요.
path_to_main_py = "/home/user/stm32/main.py"

def is_system_ready():
    # 'cron' 서비스가 활성화되었는지 확인합니다.
    status = subprocess.run(['systemctl', 'is-active', 'cron'], capture_output=True, text=True)
    return status.returncode == 0

def execute_main_py():
    # 시스템이 완전히 준비될 때까지 대기합니다.
    while not is_system_ready():
        print("시스템 준비 대기 중...")
        time.sleep(5)

    # 시스템이 준비되면 main.py를 실행합니다.
    while True:
        result = subprocess.run(["python3", path_to_main_py], capture_output=True, text=True)
        if result.returncode == 0:
            print("main.py 실행 성공")
            break
        else:
            print(f"main.py 실행 실패: {result.stderr}, 재시도 중...")
            time.sleep(5)  # 재시도 전에 5초간 대기

# Wi-Fi 네트워크를 스캔하는 함수
def scan_wifi_networks(interface="wlan0"):
    cmd = ["sudo", "iwlist", interface, "scan"]
    scan_result = subprocess.run(cmd, capture_output=True, text=True)
    scan_output = scan_result.stdout

    # 정규 표현식을 사용하여 SSID와 보안 설정을 추출
    pattern = re.compile(r'Encryption key:off\n\s*ESSID:"([^"]*)"')

    # 보안이 해제된 네트워크의 SSID 추출
    open_networks = pattern.findall(scan_output)

    return open_networks

# 암호화되지 않은 네트워크에 연결하는 함수
def connect_to_open_network(ssid):
    cmd = ["sudo", "nmcli", "dev", "wifi", "connect", ssid]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"Connected to {ssid}")
    else:
        print(f"Failed to connect to {ssid}. Error: {result.stderr}")

# 네트워크 연결 상태를 확인하는 함수
def is_connected(interface="wlan0"):
    try:
        cmd = ["iwconfig", interface]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return "Access Point" in result.stdout
    except Exception as e:
        print(f"Error checking network status: {e}")
        return False

# 메인 로직
if __name__ == "__main__":
    try:
        execute_main_py()  # main.py 실행 및 재시도
        while True:
            if not is_connected():
                print("네트워크 연결이 끊어졌습니다. 열린 네트워크를 스캔 중...")
                open_networks = scan_wifi_networks()
                if open_networks:
                    print("열린 네트워크를 찾았습니다:", open_networks)
                    for ssid in open_networks:
                        connect_to_open_network(ssid)
                else:
                    print("열린 네트워크를 찾지 못했습니다.")
            else:
                print("이미 네트워크에 연결되어 있습니다.")
            
            # 3분 동안 대기
            time.sleep(180)
    except KeyboardInterrupt:
        print("사용자에 의해 스크립트가 종료되었습니다.")
        sys.exit(1)
