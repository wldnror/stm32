import re
import time
import socket
import subprocess
import threading
from flask import Flask, request, render_template_string, redirect

AP_SSID = "GDSENG-SETUP"
AP_PASS = "12345678"
AP_IP = "192.168.4.1"
IFACE = "wlan0"
WPA_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"

app = Flask(__name__)

_state = {"running": False, "requested": None, "last_error": "", "last_ok": "", "server_started": False}

PAGE = r"""
<!doctype html><html lang="ko"><head>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Wi-Fi 설정</title>
<style>
:root{--bd:#e6e6e6;--fg:#111;--mut:#666;--ok:#0a7a2f;--er:#b00020;}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Apple SD Gothic Neo,Noto Sans KR,sans-serif;margin:16px;color:var(--fg);background:#fff}
h2{margin:0 0 10px 0;font-size:18px}
.card{border:1px solid var(--bd);border-radius:14px;padding:14px;margin-bottom:12px;background:#fff}
input,select,button{width:100%;padding:12px;margin-top:10px;font-size:16px;border-radius:12px;border:1px solid var(--bd);background:#fff}
button{font-weight:700;cursor:pointer}
button:active{transform:translateY(1px)}
.small{color:var(--mut);font-size:13px;line-height:1.35}
.err{color:var(--er);font-size:13px;margin-top:10px;white-space:pre-line}
.ok{color:var(--ok);font-size:13px;margin-top:10px;white-space:pre-line}
.row{display:flex;gap:10px;align-items:center}
.row > *{flex:1}
.badge{display:inline-block;padding:6px 10px;border-radius:999px;font-size:12px;background:#f4f4f4}
.hr{height:1px;background:#eee;margin:12px 0}
.pw-wrap{position:relative}
.pw-wrap input{padding-right:46px}
.pw-btn{
  position:absolute;right:10px;top:50%;transform:translateY(-50%);
  width:34px;height:34px;border-radius:10px;border:1px solid var(--bd);
  background:#fff;display:flex;align-items:center;justify-content:center;
  padding:0;margin:0;
}
.pw-btn svg{width:20px;height:20px}
</style>
</head><body>

<div class="row" style="align-items:baseline">
  <h2>라즈베리파이 Wi-Fi 설정</h2>
  <div style="text-align:right"><span class="badge">{{ status }}</span></div>
</div>

<div class="card">
  <div class="small">주변 Wi-Fi 목록</div>
  {% if ssids and ssids|length > 0 %}
  <form method="post" action="/connect" onsubmit="return onSubmitConnect(this)">
    <select name="ssid" required>
      {% for s in ssids %}
        <option value="{{s}}">{{s}}</option>
      {% endfor %}
    </select>
    <div class="pw-wrap">
      <input name="psk" id="psk1" type="password" placeholder="비밀번호 (없으면 빈칸)" autocomplete="current-password" />
      <button class="pw-btn" type="button" aria-label="비밀번호 보기" aria-pressed="false" onclick="togglePw('psk1', this)">
        <span class="icon" data-kind="eye"></span>
      </button>
    </div>
    <button type="submit">연결하기</button>
  </form>
  {% else %}
    <div class="small" style="margin-top:10px">
      목록이 비어있습니다.<br>
      - AP 모드(type AP)에서는 스캔이 제한될 수 있어요.<br>
      - NetworkManager가 꺼져있거나 wlan0가 unmanaged면 스캔이 안돼요.
    </div>
  {% endif %}

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
      <button class="pw-btn" type="button" aria-label="비밀번호 보기" aria-pressed="false" onclick="togglePw('psk2', this)">
        <span class="icon" data-kind="eye"></span>
      </button>
    </div>
    <button type="submit">연결하기</button>
  </form>
</div>

<div class="card">
  <div class="small">저장된 Wi-Fi 관리 (NM + WPA)</div>

  {% if saved_nm and saved_nm|length>0 %}
    <div class="small" style="margin-top:10px">NetworkManager 저장</div>
    {% for s in saved_nm %}
      <form method="post" action="/delete" style="margin-top:10px;display:flex;gap:10px">
        <input name="ssid" value="{{s}}" readonly />
        <input type="hidden" name="src" value="nm" />
        <button type="submit" style="max-width:120px">삭제</button>
      </form>
    {% endfor %}
    <div class="hr"></div>
  {% endif %}

  {% if saved_wpa and saved_wpa|length>0 %}
    <div class="small" style="margin-top:10px">wpa_supplicant 저장</div>
    {% for s in saved_wpa %}
      <form method="post" action="/delete" style="margin-top:10px;display:flex;gap:10px">
        <input name="ssid" value="{{s}}" readonly />
        <input type="hidden" name="src" value="wpa" />
        <button type="submit" style="max-width:120px">삭제</button>
      </form>
    {% endfor %}
    <div class="hr"></div>
  {% endif %}

  {% if (not saved_nm or saved_nm|length==0) and (not saved_wpa or saved_wpa|length==0) %}
    <div class="small" style="margin-top:10px">저장된 Wi-Fi가 없습니다.</div>
    <div class="hr"></div>
  {% endif %}

  <form method="post" action="/reset" onsubmit="return confirm('저장된 Wi-Fi를 전부 삭제할까요?')">
    <button type="submit">전체 초기화</button>
  </form>
</div>

<div class="small">AP: <b>{{ap}}</b> / 접속: <b>http://{{ip}}:8080/</b></div>

<script>
const EYE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/>
  <circle cx="12" cy="12" r="3"/>
</svg>`;
const EYE_OFF = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M3 3l18 18"/>
  <path d="M10.58 10.58A3 3 0 0 0 12 15a3 3 0 0 0 2.42-4.42"/>
  <path d="M9.88 5.08A10.94 10.94 0 0 1 12 5c6.5 0 10 7 10 7a18.3 18.3 0 0 1-3.1 4.28"/>
  <path d="M6.61 6.61A18.3 18.3 0 0 0 2 12s3.5 7 10 7a10.94 10.94 0 0 0 2.12-.08"/>
</svg>`;

function setIcon(btn, on){
  const span = btn.querySelector(".icon");
  span.innerHTML = on ? EYE_OFF : EYE;
}
document.querySelectorAll(".pw-btn").forEach(b=>setIcon(b,false));

function togglePw(id, btn){
  const el = document.getElementById(id);
  if(!el) return;
  const on = (el.type === "password");
  el.type = on ? "text" : "password";
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.setAttribute("aria-label", on ? "비밀번호 숨기기" : "비밀번호 보기");
  setIcon(btn, on);
}

function onSubmitConnect(form){
  const ssid = (form.ssid && form.ssid.value || "").trim();
  if(!ssid){ alert("SSID를 입력하세요."); return false; }
  return true;
}
</script>
</body></html>
"""

def _run(cmd, timeout=10.0):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)

def _run_ok(cmd, timeout=10.0):
    try:
        r = _run(cmd, timeout=timeout)
        return r.returncode == 0, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return False, "", str(e)

def has_internet(timeout=1.2):
    try:
        r = subprocess.run(["ping", "-I", IFACE, "-c", "1", "-W", "1", "8.8.8.8"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False

def scan_ssids():
    ok, out, err = _run_ok(["sudo", "nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi", "list", "ifname", IFACE], timeout=12.0)
    txt = (out or "") + "\n" + (err or "")
    items = []
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        ssid = (parts[0] or "").strip()
        if not ssid:
            continue
        try:
            sig = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        except Exception:
            sig = 0
        items.append((ssid, sig))
    best = {}
    for s, sig in items:
        if s not in best or sig > best[s]:
            best[s] = sig
    uniq = [(s, best[s]) for s in best]
    uniq.sort(key=lambda x: (-x[1], x[0]))
    return [s for s, _ in uniq[:40]]

def list_saved_nm():
    ok, out, _ = _run_ok(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], timeout=8.0)
    if not ok:
        return []
    saved = []
    for line in out.splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 2 and parts[1] == "wifi":
            name = (parts[0] or "").strip()
            if name and name not in saved:
                saved.append(name)
    return saved

def list_saved_wpa():
    try:
        ok, out, _ = _run_ok(["sudo", "cat", WPA_CONF], timeout=6.0)
        if not ok:
            return []
        ssids = re.findall(r'network=\{[^}]*ssid="([^"]+)"[^}]*\}', out, flags=re.S)
        out_list = []
        for s in ssids:
            s = s.strip()
            if s and s not in out_list:
                out_list.append(s)
        return out_list
    except Exception:
        return []

def delete_saved_nm(ssid):
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "SSID empty"
    ok, _, err = _run_ok(["sudo", "nmcli", "connection", "delete", "id", ssid], timeout=10.0)
    return (ok, "삭제 완료" if ok else ((err or "").strip() or "삭제 실패"))

def delete_saved_wpa(ssid):
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "SSID empty"
    ok, txt, err = _run_ok(["sudo", "cat", WPA_CONF], timeout=8.0)
    if not ok:
        return False, (err or "").strip() or "읽기 실패"
    before = txt
    txt2 = re.sub(r'network=\{[^}]*ssid="'+re.escape(ssid)+r'"[^}]*\}\s*', "", txt, flags=re.S)
    if txt2 == before:
        return False, "해당 SSID를 찾지 못했습니다."
    tmp = "/tmp/wpa_supplicant.conf.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(txt2.rstrip() + "\n")
    ok2, _, err2 = _run_ok(["sudo", "cp", tmp, WPA_CONF], timeout=8.0)
    if not ok2:
        return False, (err2 or "").strip() or "저장 실패"
    _run_ok(["sudo", "chmod", "600", WPA_CONF], timeout=6.0)
    _run_ok(["sudo", "wpa_cli", "-i", IFACE, "reconfigure"], timeout=6.0)
    return True, "삭제 완료"

def reset_wifi_config():
    nm = list_saved_nm()
    wpa = list_saved_wpa()
    failed = []
    for s in nm:
        ok, _ = delete_saved_nm(s)
        if not ok:
            failed.append(f"NM:{s}")
    for s in wpa:
        ok, _ = delete_saved_wpa(s)
        if not ok:
            failed.append(f"WPA:{s}")
    if failed:
        return False, "일부 삭제 실패: " + ", ".join(failed)
    return True, "초기화 완료"

def stop_ap():
    cmd = r"""sudo bash -lc '
pids=$(pgrep -a hostapd | awk "/\/tmp\/hostapd\.conf/{print \$1}" | xargs)
[ -n "$pids" ] && kill -9 $pids || true
pids=$(pgrep -a dnsmasq | awk "/\/tmp\/dnsmasq\.conf/{print \$1}" | xargs)
[ -n "$pids" ] && kill -9 $pids || true
'"""
    try:
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6)
    except Exception:
        pass

def start_ap():
    _state["running"] = True
    _state["last_error"] = ""
    _state["last_ok"] = ""
    _state["requested"] = None

    stop_ap()
    _run_ok(["sudo", "rfkill", "unblock", "wifi"], timeout=3.0)
    _run_ok(["sudo", "ip", "link", "set", IFACE, "down"], timeout=3.0)
    _run_ok(["sudo", "ip", "addr", "flush", "dev", IFACE], timeout=3.0)
    _run_ok(["sudo", "ip", "addr", "add", f"{AP_IP}/24", "dev", IFACE], timeout=3.0)
    _run_ok(["sudo", "ip", "link", "set", IFACE, "up"], timeout=3.0)

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
bind-interfaces
dhcp-range=192.168.4.10,192.168.4.200,255.255.255.0,12h
address=/#/{AP_IP}
"""

    with open("/tmp/hostapd.conf", "w", encoding="utf-8") as f:
        f.write(hostapd_conf.strip() + "\n")
    with open("/tmp/dnsmasq.conf", "w", encoding="utf-8") as f:
        f.write(dnsmasq_conf.strip() + "\n")

    subprocess.Popen(["sudo", "dnsmasq", "-C", "/tmp/dnsmasq.conf", "-d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.Popen(["sudo", "hostapd", "/tmp/hostapd.conf"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def run_portal(block=True, host="0.0.0.0", port=8080):
    if block:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    else:
        th = threading.Thread(target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False), daemon=True)
        th.start()
        return th

@app.route("/", methods=["GET"])
def index():
    ssids = scan_ssids()
    saved_nm = list_saved_nm()
    saved_wpa = list_saved_wpa()
    msg = _state["last_error"] or _state["last_ok"] or ""
    ok = bool(_state["last_ok"]) and not _state["last_error"]
    status = "인터넷 연결됨" if has_internet() else "설정 모드"
    return render_template_string(
        PAGE,
        ssids=ssids,
        saved_nm=saved_nm,
        saved_wpa=saved_wpa,
        ap=AP_SSID,
        ip=AP_IP,
        msg=msg,
        ok=ok,
        status=status
    )

@app.route("/connect", methods=["POST"])
def connect():
    ssid = (request.form.get("ssid") or "").strip()
    psk = (request.form.get("psk") or "").strip()
    if not ssid:
        return "SSID가 비어있습니다.", 400
    _state["requested"] = {"ssid": ssid, "psk": psk}
    _state["last_ok"] = f"연결 요청: {ssid}"
    _state["last_error"] = ""
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
    src = (request.form.get("src") or "").strip().lower()
    if src == "wpa":
        ok, msg = delete_saved_wpa(ssid)
    else:
        ok, msg = delete_saved_nm(ssid)
    _state["last_ok"] = msg if ok else ""
    _state["last_error"] = "" if ok else msg
    return redirect("/")

@app.route("/reset", methods=["POST"])
def reset():
    ok, msg = reset_wifi_config()
    _state["last_ok"] = msg if ok else ""
    _state["last_error"] = "" if ok else msg
    return redirect("/")

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
            _state["requested"] = None
            _state["running"] = False
            return True
        time.sleep(0.3)
    return has_internet()
