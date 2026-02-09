# /home/user/stm32/wifi_portal.py
import os
import re
import time
import json
import socket
import subprocess
import threading
from flask import Flask, request, render_template_string

AP_SSID = "GDSENG-SETUP"
AP_PASS = "12345678"          # 8자 이상
AP_IP   = "192.168.4.1"
AP_NET  = "192.168.4.0/24"
IFACE   = "wlan0"

WPA_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"

# 간단 HTML (모바일용)
PAGE = """
<!doctype html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Wi-Fi 설정</title>
<style>
body{font-family:system-ui;margin:16px}
.card{border:1px solid #ddd;border-radius:12px;padding:14px;margin-bottom:12px}
input,select,button{width:100%;padding:12px;margin-top:10px;font-size:16px}
button{font-weight:700}
.small{color:#666;font-size:13px}
</style></head><body>
<h2>라즈베리파이 Wi-Fi 설정</h2>
<div class="card">
  <div class="small">주변 Wi-Fi 목록</div>
  <form method="post" action="/connect">
    <select name="ssid" required>
      {% for s in ssids %}
        <option value="{{s}}">{{s}}</option>
      {% endfor %}
    </select>
    <input name="psk" type="password" placeholder="비밀번호 (없으면 빈칸)" />
    <button type="submit">연결하기</button>
  </form>
</div>

<div class="card">
  <div class="small">직접 입력</div>
  <form method="post" action="/connect">
    <input name="ssid" placeholder="SSID" required />
    <input name="psk" type="password" placeholder="비밀번호 (없으면 빈칸)" />
    <button type="submit">연결하기</button>
  </form>
</div>

<div class="small">
AP: <b>{{ap}}</b> / 접속 주소: <b>http://{{ip}}/</b>
</div>
</body></html>
"""

app = Flask(__name__)

_state = {
    "running": False,
    "requested": None,   # {"ssid":..., "psk":...}
    "done": False,
    "last_error": ""
}

def _run(cmd, check=False):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)

def has_internet(timeout=2.0):
    # DNS/UDP로 빠르게 체크
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.connect(("1.1.1.1", 53))
        s.close()
        return True
    except Exception:
        return False

def scan_ssids():
    # iwlist scan 기반 (파이 제로에서 잘 동작)
    try:
        p = _run(["sudo", "iwlist", IFACE, "scan"])
        txt = p.stdout + "\n" + p.stderr
        ssids = re.findall(r'ESSID:"(.*?)"', txt)
        ssids = [s for s in ssids if s and s.strip()]
        # 중복 제거 (순서 유지)
        out = []
        for s in ssids:
            if s not in out:
                out.append(s)
        return out[:30]
    except Exception:
        return []

def _write_wpa_network(ssid, psk):
    # wpa_passphrase로 PSK 해시 생성해서 안전하게 저장
    if not ssid:
        raise ValueError("SSID empty")

    if psk:
        gen = _run(["wpa_passphrase", ssid, psk], check=True).stdout
        # network 블록만 추출
        m = re.search(r"network=\{.*?\}\s*", gen, flags=re.S)
        block = m.group(0) if m else gen
    else:
        # 오픈 네트워크
        block = f'network={{\n    ssid="{ssid}"\n    key_mgmt=NONE\n}}\n'

    # 기존 파일 백업
    _run(["sudo", "cp", WPA_CONF, WPA_CONF + ".bak"])

    # country / ctrl_interface / update_config 유지하면서 network만 추가 (맨 아래 append)
    tmp = "/tmp/wpa_supplicant.conf.tmp"
    existing = _run(["sudo", "cat", WPA_CONF]).stdout

    # 이미 같은 ssid가 있으면 제거 후 append
    existing = re.sub(r'network=\{[^}]*ssid="'+re.escape(ssid)+r'"[^}]*\}\s*', "", existing, flags=re.S)

    new_content = existing.rstrip() + "\n\n" + block + "\n"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_content)

    _run(["sudo", "cp", tmp, WPA_CONF], check=True)
    _run(["sudo", "chmod", "600", WPA_CONF])

def start_ap():
    # AP 모드로 전환 (hostapd + dnsmasq 즉석 설정)
    _state["running"] = True
    _state["done"] = False
    _state["last_error"] = ""

    # 네트워크/프로세스 정리
    _run(["sudo", "pkill", "-f", "hostapd"])
    _run(["sudo", "pkill", "-f", "dnsmasq"])

    _run(["sudo", "ip", "link", "set", IFACE, "down"])
    _run(["sudo", "ip", "addr", "flush", "dev", IFACE])
    _run(["sudo", "ip", "addr", "add", f"{AP_IP}/24", "dev", IFACE])
    _run(["sudo", "ip", "link", "set", IFACE, "up"])

    hostapd_conf = f"""
interface={IFACE}
driver=nl80211
ssid={AP_SSID}
hw_mode=g
channel=6
wmm_enabled=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={AP_PASS}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
"""
    dnsmasq_conf = f"""
interface={IFACE}
dhcp-range=192.168.4.10,192.168.4.200,255.255.255.0,12h
address=/#/{AP_IP}
"""
    with open("/tmp/hostapd.conf", "w") as f:
        f.write(hostapd_conf.strip() + "\n")
    with open("/tmp/dnsmasq.conf", "w") as f:
        f.write(dnsmasq_conf.strip() + "\n")

    # dnsmasq 실행
    _run(["sudo", "dnsmasq", "-C", "/tmp/dnsmasq.conf", "-d"], check=False)

    # hostapd 실행 (백그라운드)
    subprocess.Popen(["sudo", "hostapd", "/tmp/hostapd.conf"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def stop_ap_and_connect(ssid, psk, wait_sec=25):
    try:
        _write_wpa_network(ssid, psk)
    except Exception as e:
        _state["last_error"] = f"WPA 저장 실패: {e}"
        return False

    # AP 종료
    _run(["sudo", "pkill", "-f", "hostapd"])
    _run(["sudo", "pkill", "-f", "dnsmasq"])

    # STA 모드 복귀: wpa_supplicant 재시작
    _run(["sudo", "ip", "addr", "flush", "dev", IFACE])
    _run(["sudo", "ip", "link", "set", IFACE, "down"])
    _run(["sudo", "ip", "link", "set", IFACE, "up"])

    # Raspberry Pi OS 기본 구성에 맞춰 재연결 트리거
    _run(["sudo", "wpa_cli", "-i", IFACE, "reconfigure"], check=False)
    _run(["sudo", "dhclient", "-r", IFACE], check=False)
    _run(["sudo", "dhclient", IFACE], check=False)

    # 인터넷 확인 대기
    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if has_internet():
            _state["done"] = True
            _state["running"] = False
            return True
        time.sleep(1)

    _state["last_error"] = "연결 시간 초과 (인터넷 확인 실패)"
    return False

@app.route("/", methods=["GET"])
def index():
    ssids = scan_ssids()
    return render_template_string(PAGE, ssids=ssids, ap=AP_SSID, ip=AP_IP)

@app.route("/connect", methods=["POST"])
def connect():
    ssid = (request.form.get("ssid") or "").strip()
    psk  = (request.form.get("psk") or "").strip()
    if not ssid:
        return "SSID가 비어있습니다.", 400

    _state["requested"] = {"ssid": ssid, "psk": psk}
    return f"""
    연결 요청을 받았습니다.<br>
    SSID: <b>{ssid}</b><br>
    잠시 후 자동으로 재연결됩니다. (AP가 꺼질 수 있어요)
    """

def run_portal(block=True, host="0.0.0.0", port=80):
    # Flask 서버 실행 (thread로도 가능)
    if block:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    else:
        th = threading.Thread(target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
                              daemon=True)
        th.start()
        return th

def ensure_wifi_connected(auto_start_ap=True):
    """
    인터넷이 없으면 AP+포털을 켜고,
    사용자가 SSID/PSK 제출하면 연결 시도 후 종료.
    return: True(인터넷 OK) / False(실패)
    """
    if has_internet():
        return True

    if not auto_start_ap:
        return False

    start_ap()
    run_portal(block=False)

    # 사용자가 제출할 때까지 대기
    while _state["running"]:
        req = _state.get("requested")
        if req:
            ok = stop_ap_and_connect(req["ssid"], req["psk"])
            _state["requested"] = None
            if ok:
                return True
            else:
                # 실패하면 다시 AP 켜서 재시도 가능하게
                start_ap()
                run_portal(block=False)
        time.sleep(0.5)

    return has_internet()
