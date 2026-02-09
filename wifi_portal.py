import os
import re
import time
import socket
import subprocess
import threading
from flask import Flask, request, render_template_string, redirect

AP_SSID = "GDSENG-SETUP"
AP_PASS = "12345678"
AP_IP   = "192.168.4.1"
IFACE   = "wlan0"

WPA_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"

PAGE = """
<!doctype html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Wi-Fi 설정</title>
<style>
body{font-family:system-ui;margin:16px}
h2{margin:0 0 12px 0}
.card{border:1px solid #ddd;border-radius:12px;padding:14px;margin-bottom:12px}
input,select,button{width:100%;padding:12px;margin-top:10px;font-size:16px;box-sizing:border-box}
button{font-weight:700}
.small{color:#666;font-size:13px}
.err{color:#b00020;font-size:13px;margin-top:10px;white-space:pre-line}
.ok{color:#006400;font-size:13px;margin-top:10px;white-space:pre-line}
.row{display:flex;gap:10px;align-items:center}
.row > *{flex:1}
.pw-wrap{position:relative}
.pw-wrap input{padding-right:44px}
.eye{
  position:absolute;right:10px;top:50%;transform:translateY(-50%);
  width:28px;height:28px;border:none;background:transparent;font-size:18px;cursor:pointer
}
.badge{display:inline-block;padding:6px 10px;border-radius:999px;font-size:12px;background:#f3f3f3;margin-left:8px}
.hr{height:1px;background:#eee;margin:12px 0}
a{color:inherit}
</style>
</head><body>
<div class="row" style="align-items:baseline">
  <h2>라즈베리파이 Wi-Fi 설정</h2>
  <div style="text-align:right">
    <span class="badge">{{ status }}</span>
  </div>
</div>

<div class="card">
  <div class="small">주변 Wi-Fi 목록</div>
  <form method="post" action="/connect" onsubmit="return onSubmitConnect(this)">
    <select name="ssid" required>
      {% for s in ssids %}
        <option value="{{s}}">{{s}}</option>
      {% endfor %}
    </select>

    <div class="pw-wrap">
      <input name="psk" id="psk1" type="password" placeholder="비밀번호 (없으면 빈칸)" autocomplete="current-password" />
      <button class="eye" type="button" onclick="togglePw('psk1', this)">👁</button>
    </div>

    <button type="submit">연결하기</button>
  </form>

  {% if msg %}
    <div class="{{ 'ok' if ok else 'err' }}">{{ msg }}</div>
  {% endif %}
</div>

<div class="card">
  <div class="small">직접 입력</div>
  <form method="post" action="/connect" onsubmit="return onSubmitConnect(this)">
    <input name="ssid" placeholder="SSID" required />

    <div class="pw-wrap">
      <input name="psk" id="psk2" type="password" placeholder="비밀번호 (없으면 빈칸)" autocomplete="current-password" />
      <button class="eye" type="button" onclick="togglePw('psk2', this)">👁</button>
    </div>

    <button type="submit">연결하기</button>
  </form>
</div>

<div class="card">
  <div class="small">저장된 Wi-Fi 관리</div>
  {% if saved and saved|length > 0 %}
    <div class="small" style="margin-top:10px">저장된 SSID</div>
    {% for s in saved %}
      <form method="post" action="/delete" style="margin-top:10px;display:flex;gap:10px">
        <input name="ssid" value="{{s}}" readonly />
        <button type="submit" style="max-width:120px">삭제</button>
      </form>
    {% endfor %}
    <div class="hr"></div>
    <form method="post" action="/reset" onsubmit="return confirm('저장된 Wi-Fi를 전부 삭제할까요?')">
      <button type="submit">전체 초기화</button>
    </form>
  {% else %}
    <div class="small" style="margin-top:10px">저장된 Wi-Fi가 없습니다.</div>
    <div class="hr"></div>
    <form method="post" action="/reset" onsubmit="return confirm('저장된 Wi-Fi를 전부 삭제할까요?')">
      <button type="submit">전체 초기화</button>
    </form>
  {% endif %}
</div>

<div class="small">
AP: <b>{{ap}}</b> / 접속: <b>http://{{ip}}:8080/</b>
</div>

<script>
function togglePw(id, btn){
  const el = document.getElementById(id);
  if(!el) return;
  if(el.type === "password"){ el.type = "text"; btn.textContent = "🙈"; }
  else { el.type = "password"; btn.textContent = "👁"; }
}
function onSubmitConnect(form){
  const ssid = (form.ssid && form.ssid.value || "").trim();
  if(!ssid){ alert("SSID를 입력하세요."); return false; }
  return true;
}
</script>
</body></html>
"""

app = Flask(__name__)

_state = {
    "running": False,
    "requested": None,
    "done": False,
    "last_error": "",
    "server_started": False,
    "last_ok": "",
}

def _run(cmd, check=False):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)

def has_internet(timeout=2.0):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.connect(("1.1.1.1", 53))
        s.close()
        return True
    except Exception:
        return False

def scan_ssids():
    try:
        p = _run(["sudo", "iwlist", IFACE, "scan"])
        txt = (p.stdout or "") + "\n" + (p.stderr or "")
        ssids = re.findall(r'ESSID:"(.*?)"', txt)
        ssids = [s.strip() for s in ssids if s and s.strip()]
        out = []
        for s in ssids:
            if s not in out:
                out.append(s)
        return out[:40]
    except Exception:
        return []

def list_saved_ssids():
    try:
        txt = _run(["sudo", "cat", WPA_CONF]).stdout
        ssids = re.findall(r'network=\{[^}]*ssid="([^"]+)"[^}]*\}', txt, flags=re.S)
        out = []
        for s in ssids:
            s = s.strip()
            if s and s not in out:
                out.append(s)
        return out
    except Exception:
        return []

def delete_saved_ssid(ssid):
    if not ssid:
        return False, "SSID empty"
    try:
        txt = _run(["sudo", "cat", WPA_CONF]).stdout
        before = txt
        txt2 = re.sub(r'network=\{[^}]*ssid="'+re.escape(ssid)+r'"[^}]*\}\s*', "", txt, flags=re.S)
        if txt2 == before:
            return False, "해당 SSID를 찾지 못했습니다."
        tmp = "/tmp/wpa_supplicant.conf.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(txt2.rstrip() + "\n")
        _run(["sudo", "cp", tmp, WPA_CONF], check=True)
        _run(["sudo", "chmod", "600", WPA_CONF], check=False)
        _run(["sudo", "wpa_cli", "-i", IFACE, "reconfigure"], check=False)
        return True, "삭제 완료"
    except Exception as e:
        return False, f"삭제 실패: {e}"

def reset_wifi_config():
    try:
        txt = _run(["sudo", "cat", WPA_CONF]).stdout
        txt2 = re.sub(r'network=\{.*?\}\s*', "", txt, flags=re.S)
        if "ctrl_interface" not in txt2:
            txt2 = "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1\ncountry=KR\n\n"
        tmp = "/tmp/wpa_supplicant.conf.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(txt2.rstrip() + "\n")
        _run(["sudo", "cp", tmp, WPA_CONF], check=True)
        _run(["sudo", "chmod", "600", WPA_CONF], check=False)
        _run(["sudo", "wpa_cli", "-i", IFACE, "reconfigure"], check=False)
        return True, "초기화 완료"
    except Exception as e:
        return False, f"초기화 실패: {e}"

def _write_wpa_network(ssid, psk):
    if not ssid:
        raise ValueError("SSID empty")

    if psk:
        gen = _run(["wpa_passphrase", ssid, psk], check=True).stdout
        m = re.search(r"network=\{.*?\}\s*", gen, flags=re.S)
        block = m.group(0) if m else gen
    else:
        block = f'network={{\n    ssid="{ssid}"\n    key_mgmt=NONE\n}}\n'

    _run(["sudo", "cp", WPA_CONF, WPA_CONF + ".bak"], check=False)

    tmp = "/tmp/wpa_supplicant.conf.tmp"
    existing = _run(["sudo", "cat", WPA_CONF]).stdout

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
    _run(["sudo", "chmod", "600", WPA_CONF], check=False)

def _kill_wifi_owners():
    _run(["sudo", "pkill", "-f", "hostapd"], check=False)
    _run(["sudo", "pkill", "-f", "dnsmasq"], check=False)
    _run(["sudo", "pkill", "-f", f"wpa_supplicant.*{IFACE}"], check=False)
    _run(["sudo", "dhclient", "-r", IFACE], check=False)
    _run(["sudo", "rfkill", "unblock", "wifi"], check=False)

def start_ap():
    _state["running"] = True
    _state["done"] = False
    _state["last_error"] = ""
    _state["last_ok"] = ""
    _state["requested"] = None

    _kill_wifi_owners()

    _run(["sudo", "ip", "link", "set", IFACE, "down"], check=False)
    _run(["sudo", "ip", "addr", "flush", "dev", IFACE], check=False)
    _run(["sudo", "ip", "addr", "add", f"{AP_IP}/24", "dev", IFACE], check=False)
    _run(["sudo", "ip", "link", "set", IFACE, "up"], check=False)

    hostapd_conf = f"""
country_code=KR
interface={IFACE}
driver=nl80211
ssid={AP_SSID}
hw_mode=g
channel=6
ieee80211n=1
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

    subprocess.Popen(["sudo", "dnsmasq", "-C", "/tmp/dnsmasq.conf", "-d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.Popen(["sudo", "hostapd", "/tmp/hostapd.conf"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def stop_ap_and_connect(ssid, psk, wait_sec=35):
    try:
        _write_wpa_network(ssid, psk)
    except Exception as e:
        _state["last_error"] = f"WPA 저장 실패: {e}"
        return False

    _run(["sudo", "pkill", "-f", "hostapd"], check=False)
    _run(["sudo", "pkill", "-f", "dnsmasq"], check=False)

    _run(["sudo", "ip", "addr", "flush", "dev", IFACE], check=False)
    _run(["sudo", "ip", "link", "set", IFACE, "down"], check=False)
    _run(["sudo", "ip", "link", "set", IFACE, "up"], check=False)

    _run(["sudo", "pkill", "-f", f"wpa_supplicant.*{IFACE}"], check=False)
    _run(["sudo", "wpa_supplicant", "-B", "-i", IFACE, "-c", WPA_CONF], check=False)
    _run(["sudo", "wpa_cli", "-i", IFACE, "reconfigure"], check=False)

    _run(["sudo", "dhclient", "-r", IFACE], check=False)
    _run(["sudo", "dhclient", IFACE], check=False)

    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if has_internet():
            _state["done"] = True
            _state["running"] = False
            _state["last_ok"] = f"연결 성공: {ssid}"
            _state["last_error"] = ""
            return True
        time.sleep(1)

    _state["last_error"] = "연결 시간 초과(인터넷 확인 실패)"
    return False

@app.route("/", methods=["GET"])
def index():
    ssids = scan_ssids()
    saved = list_saved_ssids()
    msg = _state["last_error"] or _state["last_ok"] or ""
    ok = bool(_state["last_ok"]) and not _state["last_error"]
    status = "인터넷 연결됨" if has_internet() else "설정 모드"
    return render_template_string(PAGE, ssids=ssids, saved=saved, ap=AP_SSID, ip=AP_IP, msg=msg, ok=ok, status=status)

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
    <br><br>
    <a href="/">돌아가기</a>
    """

@app.route("/delete", methods=["POST"])
def delete():
    ssid = (request.form.get("ssid") or "").strip()
    ok, msg = delete_saved_ssid(ssid)
    _state["last_ok"] = msg if ok else ""
    _state["last_error"] = "" if ok else msg
    return redirect("/")

@app.route("/reset", methods=["POST"])
def reset():
    ok, msg = reset_wifi_config()
    _state["last_ok"] = msg if ok else ""
    _state["last_error"] = "" if ok else msg
    return redirect("/")

def run_portal(block=True, host="0.0.0.0", port=8080):
    if block:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    else:
        th = threading.Thread(target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False), daemon=True)
        th.start()
        return th

def ensure_wifi_connected(auto_start_ap=True):
    if has_internet():
        return True
    if not auto_start_ap:
        return False

    start_ap()
    if not _state["server_started"]:
        run_portal(block=False)
        _state["server_started"] = True

    while _state["running"]:
        req = _state.get("requested")
        if req:
            ok = stop_ap_and_connect(req["ssid"], req["psk"])
            _state["requested"] = None
            if ok:
                return True
            else:
                start_ap()
        time.sleep(0.5)

    return has_internet()
