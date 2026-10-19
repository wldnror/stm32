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
  flex:0 0 120px;
  width:120px;
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
  <form method="post" action="/connect" id="connectForm" onsubmit="return onSubmitConnect(this)">
    <select name="ssid" id="ssidSelect">
      <option value="">와이파이 선택 또는 직접 입력</option>
      {% for s in ssids %}
        <option value="{{s}}">{{s}}</option>
      {% endfor %}
    </select>

    <input name="ssid_manual" maxlength="32" placeholder="SSID 직접 입력 (숨겨진 Wi-Fi 포함)" />
    <div class="pw-wrap">
      <input name="psk" id="psk1" type="password" placeholder="비밀번호 (없으면 빈칸)" autocomplete="current-password" />
      <button class="pw-btn" type="button" aria-label="비밀번호 보기" aria-pressed="false" onclick="togglePw('psk1', this)">
        <span class="icon"></span>
      </button>
    </div>

    <button type="submit" class="primary">연결하기</button>
  </form>
  <button type="button" onclick="manualRefreshScan()">목록 새로고침</button>

  {% if msg %}
    <div class="{{ 'ok' if ok else 'err' }}">{{ msg }}</div>
  {% endif %}
</div>

<div class="card">
  <div class="small">저장된 와이파이</div>

  {% if saved_wpa and saved_wpa|length > 0 %}
    <div class="small" style="margin-top:10px">wpa_supplicant</div>
    {% for s in saved_wpa %}
      <form method="post" action="/delete" class="inline">
        <input name="ssid" value="{{s}}" readonly />
        <input type="hidden" name="src" value="wpa" />
        <button type="submit">삭제</button>
      </form>
    {% endfor %}
    <div class="hr"></div>
  {% endif %}

  {% if saved_nm and saved_nm|length > 0 %}
    <div class="small" style="margin-top:10px">NetworkManager</div>
    {% for item in saved_nm %}
      <form method="post" action="/delete" class="inline">
        <input value="{{ item.display }}" readonly />
        <input type="hidden" name="nm_id" value="{{ item.nm_id }}" />
        <input type="hidden" name="src" value="nm" />
        <button type="submit">삭제</button>
      </form>
    {% endfor %}
    <div class="hr"></div>
  {% endif %}

  {% if (not saved_wpa or saved_wpa|length==0) and (not saved_nm or saved_nm|length==0) %}
    <div class="small" style="margin-top:10px">저장된 와이파이가 없습니다.</div>
    <div class="hr"></div>
  {% endif %}

  <form method="post" action="/reset" onsubmit="if(!confirm('저장된 와이파이를 전부 삭제할까요?')){event.preventDefault();event.stopImmediatePropagation();return false;}">
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
  const ssid = (form.ssid_manual && form.ssid_manual.value) || (form.ssid && form.ssid.value) || "";
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
    if (f.dataset.cancelled === "1") return;
    showLoading("전체 초기화 중입니다…");
    loadingSlowHint();
  });
});

document.querySelectorAll('form[action="/connect"]').forEach(f => {
  f.addEventListener("submit", (event) => {
    if(event.defaultPrevented) return;
    disableSubmitButtons(f);
    showLoading("연결 적용 중입니다…", "연결 화면으로 이동합니다.");
    loadingSlowHint();
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

  const opts = new Set();
  Array.from(sel.options).forEach(o => opts.add(o.value));

  sel.innerHTML = '<option value="">와이파이 선택 또는 직접 입력</option>';
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

setInterval(refreshScan, 20000);

async function refreshBadge(){
  try{
    const r = await fetch("/api/state", {cache:"no-store"});
    if(!r.ok) return;
    const j = await r.json();
    const badge = document.getElementById("badge");
    if(badge){
      badge.textContent = j.connected ? "연결됨" : "설정 모드";
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

    if(j.connected){
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

# Shared state is accessed by Flask requests and the device Wi-Fi worker.
_state_lock = threading.RLock()
_server = None

from wifi_backend import (
    AP_SSID, AP_PASS, AP_IP, PORTAL_PORT, start_ap, stop_ap, scan_ssids,
    list_saved_nm, delete_saved_nm, reset_wifi_config, connect_network,
    restore_connection, validate_credentials, ap_active,
)
from network_utils import wifi_link, internet_available as has_internet, internet_cache as _internet_cache
from process_utils import run_command


def _set_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)


def pop_request():
    with _state_lock:
        return _state.pop("requested", None)


@app.before_request
def setup_mode_only():
    # Configuration endpoints exist only during physical-button setup mode.
    with _state_lock:
        running = _state.get("running", False)
    if not running:
        return "Wi-Fi 설정 모드가 아닙니다.", 403
    import ipaddress
    from urllib.parse import urlsplit
    try:
        remote = ipaddress.ip_address(request.remote_addr or "")
        if not (remote.is_loopback or remote in ipaddress.ip_network(AP_IP + "/24", strict=False)):
            return "설정 AP에서 접속해 주세요.", 403
    except ValueError:
        return "접속 주소 오류", 403
    if request.method == "POST":
        with _state_lock:
            if _state.get("connecting") or _state.get("requested"):
                return "이미 연결을 처리하고 있습니다.", 409
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            return "잘못된 요청 출처입니다.", 403


@app.route("/", methods=["GET"])
def index():
    with _state_lock:
        state = dict(_state)
    return render_template_string(PAGE, ssids=scan_ssids(), saved_wpa=[],
        saved_nm=list_saved_nm(), msg=state["last_error"] or state["last_ok"],
        ok=bool(state["last_ok"]) and not state["last_error"], status="설정 모드")


@app.route("/connecting")
def connecting():
    return render_template_string(CONNECTING)


@app.route("/api/scan")
def api_scan():
    return jsonify({"ssids": scan_ssids()})


@app.route("/api/state")
def api_state():
    with _state_lock:
        state = {key: _state.get(key) for key in
            ("running", "done", "last_ok", "last_error", "connect_stage", "connect_started_at")}
    # Association + DHCP is success even on isolated company networks.
    state["connected"] = bool(state["done"])
    state["internet"] = bool(_internet_cache["ok"])
    return jsonify(state)


@app.route("/connect", methods=["POST"])
def connect():
    manual = request.form.get("ssid_manual") or ""
    ssid = manual or request.form.get("ssid") or ""
    psk = request.form.get("psk") or ""
    try:
        validate_credentials(ssid, psk)
    except ValueError as exc:
        return str(exc), 400
    with _state_lock:
        if _state.get("requested") or _state.get("connecting"):
            return "이미 연결을 처리하고 있습니다.", 409
        _state.update(requested={"ssid": ssid, "psk": psk, "hidden": bool(manual)}, last_error="", last_ok="",
                      done=False, connect_stage="연결 준비 중…", connect_started_at=time.time())
    return redirect("/connecting")


@app.route("/delete", methods=["POST"])
def delete():
    try:
        ok, msg = delete_saved_nm(request.form.get("nm_id") or "")
    except Exception as exc:
        ok, msg = False, str(exc)
    _set_state(last_ok=msg if ok else "", last_error="" if ok else msg)
    return redirect("/")


@app.route("/reset", methods=["POST"])
def reset():
    try:
        ok, msg = reset_wifi_config()
    except Exception as exc:
        ok, msg = False, str(exc)
    _set_state(last_ok=msg if ok else "", last_error="" if ok else msg)
    return redirect("/")


def run_portal(block=True, host=AP_IP, port=PORTAL_PORT):
    global _server
    from werkzeug.serving import make_server
    if _server is None:
        _server = make_server(host, port, app, threaded=True)
        _set_state(server_started=True)
    if block:
        _server.serve_forever()
    else:
        thread = threading.Thread(target=_server.serve_forever, daemon=True)
        thread.start()
        return thread


def stop_portal():
    global _server
    server, _server = _server, None
    if server is not None:
        server.shutdown()
        server.server_close()
    _set_state(server_started=False)


if __name__ == "__main__":
    raise SystemExit("Use the device Wi-Fi setup button to start the portal.")
