# /home/user/stm32/wifi_portal.py
import os
import re
import time
import socket
import subprocess
import threading
from flask import Flask, request, render_template_string

AP_SSID = "GDSENG-SETUP"
AP_PASS = "12345678"          # 8자 이상
AP_IP   = "192.168.4.1"
IFACE   = "wlan0"

WPA_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"

# 모바일용 간단 페이지
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
.err{color:#b00020;font-size:13px;margin-top:8px}
.ok{color:#006400;font-size:13px;margin-top:8px}
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

  {% if msg %}
    <div class="{{ 'ok' if ok else 'err' }}">{{ msg }}</div>
  {% endif %}
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
    # 빠른 연결 확인 (UDP DNS)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.connect(("1.1.1.1", 53))
        s.close()
        return True
    except Exception:
        return False

def scan_ssids():
    # iwlist scan (Pi Zero에서도 흔히 사용)
    try:
        p = _run(["sudo", "iwlist", IFACE, "scan"])
        txt = p.stdout + "\n" + p.stderr
        ssids = re.findall(r'ESSID:"(.*?)"', txt)
        ssids = [s.strip() for s in ssids if s and s.strip()]
        out = []
        for s in ssids:
            if s not in out:
                out.append(s)
        return out[:40]
    except Exception:
        return []

def _write_wpa_network(ssid, psk):
    if not ssid:
        raise ValueError("SSID empty")

    if psk:
        gen = _run(["wpa_passphrase", ssid, psk], check=True).stdout
        m = re.search(r"network=\{.*?\}\s*", gen, flags=re.S)
        block = m.group(0) if m else gen
    else:
        block = f'network={{\n    ssid="{ssid}"\n    key_mgmt=NONE\n}}\n'

    _run(["sudo", "cp", WPA_CONF, WPA_CONF + ".bak"])

    tmp = "/tmp/wpa_supplicant.conf.tmp"
    existing = _run(["sudo", "cat", WPA_CONF]).stdout

    # 동일 SSID가 이미 있으면 제거 후 append
    existing = re.sub(
        r'network=\{[^}]*ssid="'+re.escape(ssid)+r'"[^}]*\}\s*',
        "",
        existing,
        flags=re.S
    )

    new_content = existing.rstrip() + "\n\n" + block + "\n"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_content)

    _run(["sudo", "cp", tmp, WPA_CONF], check=True)
    _run(["sudo", "chmod", "600", WPA_CONF])

def start_ap():
    _state["running"] = True
    _state["done"] = False
    _state["last_error"] = ""
    _state["requested"] = None

    # 기존 AP 프로세스 정리
    _run(["sudo", "pkill", "-f", "hostapd"])
    _run(["sudo", "pkill", "-f", "dnsmasq"])

    # wlan0 IP 설정
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

    # dnsmasq (캡티브 포털 느낌: 모든 도메인을 AP_IP로)
    subprocess.Popen(["sudo", "dnsmasq", "-C", "/tmp/dnsmasq.conf", "-d"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # hostapd
    subprocess.Popen(["sudo", "hostapd", "/tmp/hostapd.conf"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def stop_ap_and_connect(ssid, psk, wait_sec=30):
    try:
        _write_wpa_network(ssid, psk)
    except Exception as e:
        _state["last_error"] = f"WPA 저장 실패: {e}"
        return False

    # AP 종료
    _run(["sudo", "pkill", "-f", "hostapd"])
    _run(["sudo", "pkill", "-f", "dnsmasq"])

    # STA 모드 복귀
    _run(["sudo", "ip", "addr", "flush", "dev", IFACE])
    _run(["sudo", "ip", "link", "set", IFACE, "down"])
    _run(["sudo", "ip", "link", "set", IFACE, "up"])

    # 재연결 트리거 (wpa_supplicant 기반)
    _run(["sudo", "wpa_cli", "-i", IFACE, "reconfigure"], check=False)
    _run(["sudo", "dhclient", "-r", IFACE], check=False)
    _run(["sudo", "dhclient", IFACE], check=False)

    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if has_internet():
            _state["done"] = True
            _state["running"] = False
            return True
        time.sleep(1)

    _state["last_error"] = "연결 시간 초과(인터넷 확인 실패)"
    return False

@app.route("/", methods=["GET"])
def index():
    ssids = scan_ssids()
    msg = _state["last_error"] if _state["last_error"] else ""
    ok = False
    return render_template_string(PAGE, ssids=ssids, ap=AP_SSID, ip=AP_IP, msg=msg, ok=ok)

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
    if block:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    else:
        th = threading.Thread(
            target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
            daemon=True
        )
        th.start()
        return th

def ensure_wifi_connected(auto_start_ap=True):
    """
    인터넷이 없으면 AP+포털을 켜고,
    사용자가 SSID/PSK 제출하면 연결 시도 후 종료.
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
                # 실패하면 다시 AP 켜서 재시도
                start_ap()
                run_portal(block=False)
        time.sleep(0.5)

    return has_internet()
