import subprocess
import re
import time

# main.py 파일의 경로를 정확하게 지정하세요.
path_to_main_py = "/home/user/stm32/main.py"

# # main.py 파일 실행 및 재시도 로직
# def execute_main_py():
#     while True:
#         result = subprocess.run(["python3", path_to_main_py], capture_output=True, text=True)
#         if result.returncode == 0:
#             print("main.py 실행 성공")
#             break
#         else:
#             print(f"main.py 실행 실패: {result.stderr}, 재시도 중...")
#             time.sleep(10)  # 재시도 전에 10초간 대기

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
    execute_main_py()  # main.py 실행 및 재시도
    while True:
        if not is_connected():
            print("Network disconnected. Scanning for open networks...")
            open_networks = scan_wifi_networks()
            if open_networks:
                print("Open networks found:", open_networks)
                for ssid in open_networks:
                    connect_to_open_network(ssid)
            else:
                print("No open networks found.")
        else:
            print("Already connected to a network.")
        
        # 3분 동안 대기
        time.sleep(180)
