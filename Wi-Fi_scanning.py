import subprocess
import re

# Wi-Fi 네트워크를 스캔하는 함수
def scan_wifi_networks(interface="wlan0"):
    cmd = ["sudo", "iwlist", interface, "scan"]
    scan_result = subprocess.run(cmd, capture_output=True, text=True)
    networks = scan_result.stdout

    # 정규 표현식을 사용하여 SSID를 추출
    pattern = re.compile(r'ESSID:"(.*?)"')
    available_networks = pattern.findall(networks)

    return available_networks

# 암호화되지 않은 네트워크에 연결하는 함수
def connect_to_open_network(ssid):
    # NetworkManager를 사용하여 공개 네트워크에 연결 시도
    cmd = ["sudo", "nmcli", "dev", "wifi", "connect", ssid]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"Connected to {ssid}")
    else:
        print(f"Failed to connect to {ssid}")

# 메인 로직
if __name__ == "__main__":
    open_networks = scan_wifi_networks()
    for ssid in open_networks:
        connect_to_open_network(ssid)
