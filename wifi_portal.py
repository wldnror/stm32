import os
import re
import time
import json
import subprocess
import threading
from flask import Flask, request, render_template_string, redirect, jsonify

AP_SSID = "GDSENG-SETUP"
AP_PASS = "12345678"
AP_IP   = "192.168.4.1"
IFACE   = "wlan0"

WPA_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"
NM_CONN_DIR = "/etc/NetworkManager/system-connections"

app = Flask(__name__)

_state = {
    "running": False,
    "requested": None,
    "done": False,
    "last_error": "",
    "server_started": False,
    "last_ok": "",
    "connect_stage": "",
    "connect_started_at": 0.0,
}

PAGE = r"""
<!doctype html><html lang="ko"><head>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>와이파이 설정</title>
<style>
:root{--bd:#e6e6e6;--fg:#111;--mut:#666;--ok:#0a7a2f;--er:#b00020;}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Apple SD Gothic Neo,Noto Sans KR,sans-serif;margin:16px;color:var(--fg);background:#fff}
h2{margin:0 0 10px 0;font-size:18px}
.card{border:1px solid var(--bd);border-radius:14px;padding:14px;margin-bottom:12px;background:#fff}

input,select,button{
  width:100%;
  padding:12px;
  margin-top:10px;
  font-size:16px;
  border-radius:12px;
  border:1px solid var(--bd);
  background:#fff;
  color:var(--fg);
  -webkit-appearance:none;
  appearance:none;
}

button{font-weight:800;cursor:pointer}
button:active{transform:translateY(1px)}
button.primary{background:#111;border-color:#111;color:#fff}
button:disabled{opacity:.6;cursor:default}

.small{color:var(--mut);font-size:13px;line-height:1.35}
.err{color:var(--er);font-size:13px;margin-top:10px;white-space:pre-line}
.ok{color:var(--ok);font-size:13px;margin-top:10px;white-space:pre-line}
.row{display:flex;gap:10px;align-items:center}
.row > *{flex:1}
.badge{display:inline-block;padding:6px 10px;border-radius:999px;font-size:12px;background:#f4f4f4}
.hr{height:1px;background:#eee;margin:12px 0}

.pw-wrap{position:relative}
.pw-wrap input{padding-right:52px}
.pw-btn{
  position:absolute;right:10px;top:50%;transform:translateY(-50%);
  width:36px;height:36px;border-radius:10px;border:1px solid var(--bd);
  background:#fff;display:flex;align-items:center;justify-content:center;
  padding:0;margin:0;
  z-index:2;
}
.pw-btn svg{width:20px;height:20px}

form.inline{
  display:flex;
  gap:10px;
  align-items:center;
  margin-top:10px;
}
form.inline input{
  flex:1;
  min-width:0;
  width:auto;
  margin-top:0;
}
form.inline button{
  flex:0 0 90px;
  width:90px;
  margin-top:0;
}

#loading{
  position:fixed; inset:0;
  background:rgba(0,0,0,.35);
  display:none;
  align-items:center;
  justify-content:center;
  z-index:9999;
}
.loading-box{
  width:min(340px, 92vw);
  background:#fff;
  border-radius:16px;
  padding:18px 16px;
  border:1px solid var(--bd);
  box-shadow:0 10px 30px rgba(0,0,0,.18);
  text-align:center;
}
.spinner{
  width:34px; height:34px;
  border:3px solid #e6e6e6;
  border-top-color:#111;
  border-radius:50%;
  margin:0 auto 10px auto;
  animation:spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.loading-text{ font-weight:900; }
</style>
</head><body>

<div class="row" style="align-items:baseline">
  <h2>와이파이 설정</h2>
  <div style="text-align:right"><span class="badge" id="badge">{{ status }}</span></div>
</div>

<div class="card">
  <div class="small">주변 와이파이</div>
  {% if ssids and ssids|length > 0 %}
  <form method="post" action="/connect" id="connectForm" onsubmit="return onSubmitConnect(this)">
    <select name="ssid" required id="ssidSelect">
      {% for s in ssids %}
        <option value="{{s}}">{{s}}</option>
      {% endfor %}
    </select>

    <div class="pw-wrap">
      <input name="psk" id="psk1" type="password" placeholder="비밀번호 (없으면 빈칸)" autocomplete="current-password" />
      <button class="pw-btn" type="button" aria-label="비밀번호 보기" aria-pressed="false" onclick="togglePw('psk1', this)">
        <span class="icon"></span>
      </button>
    </div>

    <button type="submit" class="primary">연결하기</button>
  </form>
  {% else %}
    <div class="small" style="margin-top:10px">
      주변 와이파이를 찾지 못했습니다.
    </div>
    <button class="primary" style="margin-top:12px" type="button" onclick="manualRefreshScan()">다시 스캔</button>
  {% endif %}

  {% if msg %}
    <div class="{{ 'ok' if ok else 'err' }}">{{ msg }}</div>
  {% endif %}
</div>

<div class="card">
  <div class="small">저장된 와이파이</div>

  {% if saved_wpa and saved_wpa|length > 0 %}
    <div class="small" style="margin-top:10px">wpa_supplicant</div>
    {% for s in saved_wpa %}
      <form method="post" class="inline">
        <input name="ssid" value="{{s}}" readonly />
        <input type="hidden" name="src" value="wpa" />
        <button type="submit" class="primary" formaction="/connect_saved">연결</button>
        <button type="submit" formaction="/delete">삭제</button>
      </form>
    {% endfor %}
    <div class="hr"></div>
  {% endif %}

  {% if saved_nm and saved_nm|length > 0 %}
    <div class="small" style="margin-top:10px">NetworkManager</div>
    {% for item in saved_nm %}
      <form method="post" class="inline">
        <input value="{{ item.display }}" readonly />
        <input type="hidden" name="nm_id" value="{{ item.nm_id }}" />
        <input type="hidden" name="src" value="nm" />
        <button type="submit" class="primary" formaction="/connect_saved">연결</button>
        <button type="submit" formaction="/delete">삭제</button>
      </form>
    {% endfor %}
    <div class="hr"></div>
  {% endif %}

  {% if (not saved_wpa or saved_wpa|length==0) and (not saved_nm or saved_nm|length==0) %}
    <div class="small" style="margin-top:10px">저장된 와이파이가 없습니다.</div>
    <div class="hr"></div>
  {% endif %}

  <form method="post" action="/reset" onsubmit="return confirm('저장된 와이파이를 전부 삭제할까요?')">
    <button type="submit" class="primary">전체 초기화</button>
  </form>
</div>

<div id="loading">
  <div class="loading-box">
    <div class="spinner"></div>
    <div class="loading-text" id="loadingText">처리 중입니다…</div>
    <div class="small" style="margin-top:8px;color:var(--mut)" id="loadingSub">잠시만 기다려주세요.</div>
  </div>
</div>

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

function initEyes(){
  document.querySelectorAll(".pw-btn .icon").forEach(x => x.innerHTML = EYE);
}
initEyes();

function togglePw(id, btn){
  const el = document.getElementById(id);
  if(!el) return;
  const show = (el.type === "password");
  el.type = show ? "text" : "password";
  btn.setAttribute("aria-pressed", show ? "true" : "false");
  btn.setAttribute("aria-label", show ? "비밀번호 숨기기" : "비밀번호 보기");
  const icon = btn.querySelector(".icon");
  icon.innerHTML = show ? EYE_OFF : EYE;
}

function onSubmitConnect(form){
  const ssid = (form.ssid && form.ssid.value || "").trim();
  if(!ssid){ alert("SSID를 선택하세요."); return false; }
  return true;
}

function showLoading(text, sub){
  const el = document.getElementById("loading");
  const tx = document.getElementById("loadingText");
  const sb = document.getElementById("loadingSub");
  if(tx) tx.textContent = text || "처리 중입니다…";
  if(sb) sb.textContent = sub || "잠시만 기다려주세요.";
  if(el) el.style.display = "flex";
}

function loadingSlowHint(){
  setTimeout(() => {
    const el = document.getElementById("loading");
    const tx = document.getElementById("loadingText");
    if(el && el.style.display === "flex" && tx){
      tx.textContent = "조금만 더 기다려주세요…";
    }
  }, 2500);
}

function disableSubmitButtons(form){
  form.querySelectorAll('button[type="submit"]').forEach(b => b.disabled = true);
}

document.querySelectorAll('form[action="/delete"]').forEach(f => {
  f.addEventListener("submit", () => {
    disableSubmitButtons(f);
    showLoading("삭제 중입니다…");
    loadingSlowHint();
  });
});

document.querySelectorAll('form[action="/reset"]').forEach(f => {
  f.addEventListener("submit", () => {
    disableSubmitButtons(f);
    showLoading("전체 초기화 중입니다…");
    loadingSlowHint();
  });
});

document.querySelectorAll('form[action="/connect"]').forEach(f => {
  f.addEventListener("submit", () => {
    disableSubmitButtons(f);
    showLoading("연결 적용 중입니다…", "연결 화면으로 이동합니다.");
    loadingSlowHint();
  });
});

// connect_saved 버튼(저장된 와이파이 연결) 로딩
document.querySelectorAll('form button[formaction="/connect_saved"]').forEach(btn => {
  btn.addEventListener("click", () => {
    const form = btn.closest("form");
    if(form){
      disableSubmitButtons(form);
      showLoading("저장된 와이파이로 연결 중…", "연결 화면으로 이동합니다.");
      loadingSlowHint();
    }
  });
});

let lastScanSig = "";
let scanBusy = false;

function applyScan(ssids){
  const sel = document.getElementById("ssidSelect");
  if(!sel) return;
  const cur = sel.value;
  const sig = JSON.stringify(ssids || []);
  if(sig === lastScanSig) return;
  lastScanSig = sig;

  sel.innerHTML = "";
  (ssids || []).forEach(s => {
    const o = document.createElement("option");
    o.value = s;
    o.textContent = s;
    sel.appendChild(o);
  });

  const exists = (ssids || []).includes(cur);
  if(exists) sel.value = cur;
}

async function refreshScan(){
  if(scanBusy) return;
  scanBusy = true;
  try{
    const r = await fetch("/api/scan", {cache:"no-store"});
    if(!r.ok) return;
    const j = await r.json();
    if(j && Array.isArray(j.ssids)) applyScan(j.ssids);
  }catch(e){}
  finally{
    scanBusy = false;
  }
}

function manualRefreshScan(){
  showLoading("스캔 중입니다…");
  refreshScan().finally(() => location.reload());
}

setInterval(refreshScan, 5000);

async function refreshBadge(){
  try{
    const r = await fetch("/api/state", {cache:"no-store"});
    if(!r.ok) return;
    const j = await r.json();
    const badge = document.getElementById("badge");
    if(badge){
      badge.textContent = j.internet ? "연결됨" : "설정 모드";
    }
  }catch(e){}
}
setInterval(refreshBadge, 3000);
</script>

</body></html>
"""

CONNECTING = r"""
<!doctype html><html lang="ko"><head>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>와이파이 설정</title>
<style>
:root{--bd:#e6e6e6;--fg:#111;--mut:#666;--er:#b00020;}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Apple SD Gothic Neo,Noto Sans KR,sans-serif;margin:16px;color:var(--fg);background:#fff}
.card{border:1px solid var(--bd);border-radius:14px;padding:16px;margin-bottom:12px;background:#fff}
h2{margin:0 0 10px 0;font-size:18px}
.small{color:var(--mut);font-size:13px;line-height:1.35}
.err{color:var(--er);font-size:13px;margin-top:10px;white-space:pre-line}
button{
  width:100%;
  padding:12px;
  margin-top:12px;
  font-size:16px;
  border-radius:12px;
  border:1px solid var(--bd);
  background:#111;
  color:#fff;
  font-weight:900;
  cursor:pointer;
}
.spinner{
  width:36px;height:36px;
  border:3px solid #e6e6e6;
  border-top-color:#111;
  border-radius:50%;
  animation:spin .8s linear infinite;
  margin:8px auto 12px auto;
}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head><body>
<div class="card">
  <h2>연결 중</h2>
  <div class="spinner"></div>
  <div class="small" id="stage">잠시만 기다려주세요.</div>
  <div class="err" id="err" style="display:none"></div>
  <button id="back" style="display:none" onclick="location.href='/'">돌아가기</button>
</div>

<script>
async function tick(){
  try{
    const r = await fetch("/api/state", {cache:"no-store"});
    if(!r.ok) return;
    const j = await r.json();

    const stage = document.getElementById("stage");
    const err = document.getElementById("err");
    const back = document.getElementById("back");

    if(stage){
      const s = j.connect_stage || "";
      stage.textContent = s ? s : "연결 확인 중…";
    }

    if(j.internet){
      location.href = "/";
      return;
    }

    if(j.last_error){
      err.style.display = "block";
      err.textContent = j.last_error;
      back.style.display = "block";
      return;
    }
  }catch(e){}
}
setInterval(tick, 1000);
tick();
</script>
</body></html>
"""

def _run(cmd, check=False, timeout=15.0):
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=check,
        timeout=timeout
    )

def has_internet(timeout=1.2):
    try:
        r = subprocess.run(
            ["ping", "-I", IFACE, "-c", "1", "-W", "1", "8.8.8.8"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout
        )
        return r.returncode == 0
    except Exception:
        return False

def _scan_iwlist():
    p = _run(["sudo", "iwlist", IFACE, "scan"], timeout=18.0)
    txt = (p.stdout or "") + "\n" + (p.stderr or "")
    ssids = re.findall(r'ESSID:"(.*?)"', txt)
    ssids = [s.strip() for s in ssids if s and s.strip()]
    out = []
    for s in ssids:
        if s not in out:
            out.append(s)
    return out[:40]

def _scan_nmcli():
    p = _run(["sudo", "nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi", "list", "ifname", IFACE], timeout=12.0)
    items = []
    for line in (p.stdout or "").splitlines():
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

def scan_ssids():
    try:
        nm = _scan_nmcli()
        if nm:
            return nm
    except Exception:
        pass
    try:
        iw = _scan_iwlist()
        if iw:
            return iw
    except Exception:
        pass
    return []

def list_saved_wpa():
    try:
        txt = _run(["sudo", "cat", WPA_CONF], timeout=6.0).stdout
        ssids = re.findall(r'network=\{[^}]*ssid="([^"]+)"[^}]*\}', txt, flags=re.S)
        out = []
        for s in ssids:
            s = s.strip()
            if s and s not in out:
                out.append(s)
        return out
    except Exception:
        return []

def _parse_nmconnection_files():
    items = []
    try:
        if not os.path.isdir(NM_CONN_DIR):
            return items
        for fn in os.listdir(NM_CONN_DIR):
            if not fn.endswith(".nmconnection"):
                continue
            path = os.path.join(NM_CONN_DIR, fn)
            try:
                txt = _run(["sudo", "cat", path], timeout=3.5).stdout
            except Exception:
                continue
            nm_id = os.path.splitext(fn)[0].strip()
            m = re.search(r"(?m)^\s*ssid\s*=\s*(.+?)\s*$", txt)
            ssid = m.group(1).strip() if m else ""
            if ssid:
                items.append({"ssid": ssid, "nm_id": nm_id, "display": f"{ssid} ({nm_id})"})
    except Exception:
        pass
    return items

def list_saved_nm():
    items = []
    try:
        p = _run(["sudo", "nmcli", "-t", "-f", "NAME,TYPE,802-11-wireless.ssid", "connection", "show"], timeout=6.0)
        for line in (p.stdout or "").splitlines():
            parts = line.strip().split(":")
            if len(parts) < 2:
                continue
            name = (parts[0] or "").strip()
            ctype = (parts[1] or "").strip()
            if ctype != "wifi" or not name:
                continue
            ssid = (parts[2] if len(parts) >= 3 else "").strip()
            display = ssid if ssid else name
            if ssid and ssid != name:
                display = f"{ssid} ({name})"
            items.append({"ssid": ssid or name, "nm_id": name, "display": display})
    except Exception:
        items = []

    if not items:
        items = _parse_nmconnection_files()
    else:
        has_real_ssid = any(("(" in it["display"]) or (it["ssid"] and it["ssid"] != it["nm_id"]) for it in items)
        if not has_real_ssid:
            fb = _parse_nmconnection_files()
            known = set([it["nm_id"] for it in items if it.get("nm_id")])
            for it in fb:
                if it["nm_id"] not in known:
                    items.append(it)

    uniq = []
    seen = set()
    for it in items:
        key = (it.get("nm_id",""), it.get("display",""))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq

def delete_saved_wpa(ssid):
    if not ssid:
        return False, "SSID empty"
    try:
        txt = _run(["sudo", "cat", WPA_CONF], timeout=6.0).stdout
        before = txt
        txt2 = re.sub(r'network=\{[^}]*ssid="'+re.escape(ssid)+r'"[^}]*\}\s*', "", txt, flags=re.S)
        if txt2 == before:
            return False, "해당 SSID를 찾지 못했습니다."
        tmp = "/tmp/wpa_supplicant.conf.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(txt2.rstrip() + "\n")
        _run(["sudo", "cp", tmp, WPA_CONF], timeout=6.0)
        _run(["sudo", "chmod", "600", WPA_CONF], timeout=6.0)
        _run(["sudo", "wpa_cli", "-i", IFACE, "reconfigure"], timeout=6.0)
        return True, "삭제 완료"
    except Exception as e:
        return False, f"삭제 실패: {e}"

def delete_saved_nm(nm_id: str):
    if not nm_id:
        return False, "NM id empty"
    try:
        p = _run(["sudo", "nmcli", "connection", "delete", "id", nm_id], timeout=8.0)
        if p.returncode == 0:
            return True, "삭제 완료"
        msg = (p.stderr or "").strip() or (p.stdout or "").strip() or "삭제 실패"
        return False, msg
    except Exception as e:
        return False, f"삭제 실패: {e}"

def reset_wifi_config():
    errs = []
    for it in list_saved_nm():
        nm_id = it.get("nm_id", "")
        ok, _ = delete_saved_nm(nm_id)
        if not ok:
            errs.append(f"NM:{nm_id}")
    for s in list_saved_wpa():
        ok, _ = delete_saved_wpa(s)
        if not ok:
            errs.append(f"WPA:{s}")
    if errs:
        return False, "일부 삭제 실패: " + ", ".join(errs)
    return True, "초기화 완료"

def _write_wpa_network(ssid, psk):
    if not ssid:
        raise ValueError("SSID empty")

    if psk:
        gen = _run(["wpa_passphrase", ssid, psk], check=True, timeout=6.0).stdout
        m = re.search(r"network=\{.*?\}\s*", gen, flags=re.S)
        block = m.group(0) if m else gen
    else:
        block = f'network={{\n    ssid="{ssid}"\n    key_mgmt=NONE\n}}\n'

    _run(["sudo", "cp", WPA_CONF, WPA_CONF + ".bak"], timeout=6.0)

    existing = _run(["sudo", "cat", WPA_CONF], timeout=6.0).stdout
    existing = re.sub(r'network=\{[^}]*ssid="'+re.escape(ssid)+r'"[^}]*\}\s*', "", existing, flags=re.S)

    tmp = "/tmp/wpa_supplicant.conf.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(existing.rstrip() + "\n\n" + block + "\n")

    _run(["sudo", "cp", tmp, WPA_CONF], timeout=6.0)
    _run(["sudo", "chmod", "600", WPA_CONF], timeout=6.0)

def _kill_wifi_owners():
    _run(["sudo", "pkill", "-f", "hostapd"], timeout=4.0)
    _run(["sudo", "pkill", "-f", "dnsmasq"], timeout=4.0)
    _run(["sudo", "pkill", "-f", f"wpa_supplicant.*{IFACE}"], timeout=4.0)
    _run(["sudo", "dhclient", "-r", IFACE], timeout=6.0)
    _run(["sudo", "rfkill", "unblock", "wifi"], timeout=4.0)

def start_ap():
    _state["running"] = True
    _state["done"] = False
    _state["last_error"] = ""
    _state["last_ok"] = ""
    _state["requested"] = None
    _state["connect_stage"] = ""
    _state["connect_started_at"] = 0.0

    _kill_wifi_owners()

    _run(["sudo", "ip", "link", "set", IFACE, "down"], timeout=4.0)
    _run(["sudo", "ip", "addr", "flush", "dev", IFACE], timeout=4.0)
    _run(["sudo", "ip", "addr", "add", f"{AP_IP}/24", "dev", IFACE], timeout=4.0)
    _run(["sudo", "ip", "link", "set", IFACE, "up"], timeout=4.0)

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

    subprocess.Popen(["sudo", "dnsmasq", "-C", "/tmp/dnsmasq.conf", "-d"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.Popen(["sudo", "hostapd", "/tmp/hostapd.conf"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def stop_ap_and_connect(ssid, psk, wait_sec=35):
    _state["connect_started_at"] = time.time()
    _state["connect_stage"] = "설정 저장 중…"
    try:
        _write_wpa_network(ssid, psk)
    except Exception as e:
        _state["last_error"] = f"설정 저장 실패: {e}"
        return False

    _state["connect_stage"] = "설정 모드 종료 중…"
    _run(["sudo", "pkill", "-f", "hostapd"], timeout=4.0)
    _run(["sudo", "pkill", "-f", "dnsmasq"], timeout=4.0)

    _state["connect_stage"] = "인터페이스 초기화 중…"
    _run(["sudo", "ip", "addr", "flush", "dev", IFACE], timeout=4.0)
    _run(["sudo", "ip", "link", "set", IFACE, "down"], timeout=4.0)
    _run(["sudo", "ip", "link", "set", IFACE, "up"], timeout=4.0)

    _state["connect_stage"] = "무선 연결 시도 중…"
    _run(["sudo", "pkill", "-f", f"wpa_supplicant.*{IFACE}"], timeout=4.0)
    _run(["sudo", "wpa_supplicant", "-B", "-i", IFACE, "-c", WPA_CONF], timeout=10.0)
    _run(["sudo", "wpa_cli", "-i", IFACE, "reconfigure"], timeout=6.0)

    _state["connect_stage"] = "IP 받는 중…"
    _run(["sudo", "dhclient", "-r", IFACE], timeout=8.0)
    _run(["sudo", "dhclient", IFACE], timeout=12.0)

    _state["connect_stage"] = "인터넷 확인 중…"
    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if has_internet():
            _state["done"] = True
            _state["running"] = False
            _state["last_ok"] = "연결 완료"
            _state["last_error"] = ""
            _state["connect_stage"] = "연결 완료"
            return True
        time.sleep(1)

    _state["last_error"] = "연결 시간 초과"
    _state["connect_stage"] = ""
    return False

# --------------------------
# 저장된 와이파이 연결(추가)
# --------------------------

def _nm_up(nm_id: str):
    p = _run(["sudo", "nmcli", "connection", "up", "id", nm_id], timeout=25.0)
    ok = (p.returncode == 0)
    msg = (p.stdout or "").strip() or (p.stderr or "").strip()
    return ok, (msg or ("연결 실패" if not ok else "연결 요청 완료"))

def _wpa_select_network_by_ssid(ssid: str):
    p = _run(["sudo", "wpa_cli", "-i", IFACE, "list_networks"], timeout=6.0)
    txt = (p.stdout or "")
    net_id = None
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("network id"):
            continue
        parts = re.split(r"\t+", line)
        if len(parts) >= 2 and parts[1] == ssid:
            net_id = parts[0]
            break
    if net_id is None:
        return False, "wpa_supplicant에 해당 SSID가 없습니다."

    _run(["sudo", "wpa_cli", "-i", IFACE, "select_network", net_id], timeout=6.0)
    _run(["sudo", "wpa_cli", "-i", IFACE, "enable_network", net_id], timeout=6.0)
    _run(["sudo", "wpa_cli", "-i", IFACE, "reconfigure"], timeout=6.0)
    return True, "선택 완료"

def stop_ap_and_connect_saved(req, wait_sec=35):
    _state["connect_started_at"] = time.time()

    _state["connect_stage"] = "설정 모드 종료 중…"
    _run(["sudo", "pkill", "-f", "hostapd"], timeout=4.0)
    _run(["sudo", "pkill", "-f", "dnsmasq"], timeout=4.0)

    _state["connect_stage"] = "인터페이스 초기화 중…"
    _run(["sudo", "ip", "addr", "flush", "dev", IFACE], timeout=4.0)
    _run(["sudo", "ip", "link", "set", IFACE, "down"], timeout=4.0)
    _run(["sudo", "ip", "link", "set", IFACE, "up"], timeout=4.0)

    src = (req.get("src") or "").strip().lower()

    if src == "nm":
        nm_id = (req.get("nm_id") or "").strip()
        if not nm_id:
            _state["last_error"] = "nm_id가 비어있습니다."
            _state["connect_stage"] = ""
            return False

        _state["connect_stage"] = "NetworkManager 연결 시도 중…"
        ok, msg = _nm_up(nm_id)
        if not ok:
            _state["last_error"] = msg or "NM 연결 실패"
            _state["connect_stage"] = ""
            return False

    else:
        ssid = (req.get("ssid") or "").strip()
        if not ssid:
            _state["last_error"] = "SSID가 비어있습니다."
            _state["connect_stage"] = ""
            return False

        _state["connect_stage"] = "무선 연결 시도 중…"
        _run(["sudo", "pkill", "-f", f"wpa_supplicant.*{IFACE}"], timeout=4.0)
        _run(["sudo", "wpa_supplicant", "-B", "-i", IFACE, "-c", WPA_CONF], timeout=10.0)

        ok, msg = _wpa_select_network_by_ssid(ssid)
        if not ok:
            _state["last_error"] = msg
            _state["connect_stage"] = ""
            return False

    _state["connect_stage"] = "IP 받는 중…"
    _run(["sudo", "dhclient", "-r", IFACE], timeout=8.0)
    _run(["sudo", "dhclient", IFACE], timeout=12.0)

    _state["connect_stage"] = "인터넷 확인 중…"
    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if has_internet():
            _state["done"] = True
            _state["running"] = False
            _state["last_ok"] = "연결 완료"
            _state["last_error"] = ""
            _state["connect_stage"] = "연결 완료"
            return True
        time.sleep(1)

    _state["last_error"] = "연결 시간 초과"
    _state["connect_stage"] = ""
    return False

# --------------------------
# Flask routes
# --------------------------

@app.route("/", methods=["GET"])
def index():
    ssids = scan_ssids()
    saved_wpa = list_saved_wpa()
    saved_nm = list_saved_nm()

    msg = _state["last_error"] or _state["last_ok"] or ""
    ok = bool(_state["last_ok"]) and not _state["last_error"]
    status = "연결됨" if has_internet() else "설정 모드"

    return render_template_string(
        PAGE,
        ssids=ssids,
        saved_wpa=saved_wpa,
        saved_nm=saved_nm,
        msg=msg,
        ok=ok,
        status=status,
    )

@app.route("/connecting", methods=["GET"])
def connecting():
    return render_template_string(CONNECTING)

@app.route("/api/scan", methods=["GET"])
def api_scan():
    return jsonify({"ssids": scan_ssids()})

@app.route("/api/state", methods=["GET"])
def api_state():
    return jsonify({
        "internet": has_internet(),
        "running": bool(_state.get("running")),
        "requested": bool(_state.get("requested")),
        "last_ok": _state.get("last_ok",""),
        "last_error": _state.get("last_error",""),
        "connect_stage": _state.get("connect_stage",""),
        "connect_started_at": _state.get("connect_started_at", 0.0),
    })

@app.route("/connect", methods=["POST"])
def connect():
    ssid = (request.form.get("ssid") or "").strip()
    psk  = (request.form.get("psk") or "").strip()
    if not ssid:
        return "SSID가 비어있습니다.", 400
    _state["requested"] = {"mode": "new", "ssid": ssid, "psk": psk}
    _state["last_error"] = ""
    _state["last_ok"] = ""
    _state["connect_stage"] = "연결 준비 중…"
    _state["connect_started_at"] = time.time()
    return redirect("/connecting")

# 저장된 와이파이 연결(추가 라우트)
@app.route("/connect_saved", methods=["POST"])
def connect_saved():
    src = (request.form.get("src") or "").strip().lower()
    req = {"mode": "saved", "src": src}

    if src == "nm":
        nm_id = (request.form.get("nm_id") or "").strip()
        if not nm_id:
            return "nm_id가 비어있습니다.", 400
        req["nm_id"] = nm_id
    else:
        ssid = (request.form.get("ssid") or "").strip()
        if not ssid:
            return "SSID가 비어있습니다.", 400
        req["ssid"] = ssid

    _state["requested"] = req
    _state["last_error"] = ""
    _state["last_ok"] = ""
    _state["connect_stage"] = "저장된 설정으로 연결 준비 중…"
    _state["connect_started_at"] = time.time()
    return redirect("/connecting")

@app.route("/delete", methods=["POST"])
def delete():
    src = (request.form.get("src") or "").strip().lower()

    if src == "nm":
        nm_id = (request.form.get("nm_id") or "").strip()
        ok, msg = delete_saved_nm(nm_id)
    else:
        ssid = (request.form.get("ssid") or "").strip()
        ok, msg = delete_saved_wpa(ssid)

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
        th = threading.Thread(
            target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
            daemon=True
        )
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
            if req.get("mode") == "saved":
                ok = stop_ap_and_connect_saved(req)
            else:
                ok = stop_ap_and_connect(req["ssid"], req.get("psk", ""))

            _state["requested"] = None
            if ok:
                return True
            start_ap()
        time.sleep(0.5)

    return has_internet()

if __name__ == "__main__":
    run_portal(block=True)
