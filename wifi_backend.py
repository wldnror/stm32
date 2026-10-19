"""One Wi-Fi owner: NetworkManager handles station mode, AP and DHCP."""
import threading
import time
import uuid
import os
from pathlib import Path

from network_utils import split_nmcli, wifi_link
from process_utils import run_command

IFACE = "wlan0"
AP_PROFILE = "gdseng-setup-ap"
AP_SSID = "GDSENG-SETUP"
AP_PASS = "12345678"
AP_IP = "192.168.4.1"
PORTAL_PORT = 8080
_scan_lock = threading.Lock()
_scan_cache = {"ssids": [], "at": 0.0}
_profile_file = Path.home() / ".config" / "stm32" / "last_wifi_uuid"
try:
    _previous_profile = _profile_file.read_text().strip() or None
except OSError:
    _previous_profile = None


def nmcli(*args, timeout=10, cancel=None, check=True):
    result = run_command(["sudo", "-n", "nmcli", *args], timeout=timeout, cancel=cancel)
    if check and result.returncode != 0:
        if result.returncode == 125:
            raise RuntimeError("연결 취소됨")
        if result.returncode == 124:
            raise RuntimeError("무선 명령 시간 초과")
        # Do not expose command arguments (which can contain the Wi-Fi password).
        raise RuntimeError("무선 설정 실패: " + (result.stderr.strip() or "NetworkManager 오류")[:200])
    return result


def active_profile(cancel=None):
    result = nmcli("-t", "-f", "UUID,TYPE,DEVICE", "connection", "show", "--active", cancel=cancel)
    for line in result.stdout.splitlines():
        parts = split_nmcli(line)
        if len(parts) == 3 and parts[1] in ("wifi", "802-11-wireless") and parts[2] == IFACE:
            return parts[0]
    return None


def ap_active(cancel=None):
    result = nmcli("-t", "-f", "NAME,DEVICE", "connection", "show", "--active", check=False, cancel=cancel)
    return any(split_nmcli(line) == [AP_PROFILE, IFACE] for line in result.stdout.splitlines())


def scan_ssids(force=False, cancel=None):
    with _scan_lock:
        now = time.monotonic()
        if not force and _scan_cache["at"] and now - _scan_cache["at"] < 20:
            return list(_scan_cache["ssids"])
        # The single-radio Pi cannot safely scan while serving the setup AP.
        rescan = "no" if ap_active(cancel=cancel) else "yes"
        result = nmcli("-t", "-f", "SSID,SIGNAL", "device", "wifi", "list",
                       "ifname", IFACE, "--rescan", rescan, timeout=12, check=False, cancel=cancel)
        best = {}
        for line in result.stdout.splitlines():
            fields = split_nmcli(line)
            if len(fields) != 2 or not fields[0] or fields[0] == AP_SSID:
                continue
            try:
                signal = int(fields[1])
            except ValueError:
                continue
            best[fields[0]] = max(signal, best.get(fields[0], -1))
        if best:
            _scan_cache["ssids"] = sorted(best, key=lambda ssid: (-best[ssid], ssid))[:40]
        _scan_cache["at"] = now
        return list(_scan_cache["ssids"])


def list_saved_nm():
    # The device runs NM 1.30; SSID is not a valid field in `connection show`.
    # UUID is unambiguous even when connection names contain colons or duplicates.
    result = nmcli("-t", "-f", "UUID,NAME,TYPE", "connection", "show", check=False)
    items = []
    for line in result.stdout.splitlines():
        fields = split_nmcli(line)
        if len(fields) == 3 and fields[2] in ("wifi", "802-11-wireless") and fields[1] != AP_PROFILE:
            items.append({"nm_id": fields[0], "ssid": fields[1], "display": fields[1]})
    return items


def start_ap(cancel=None, progress=None):
    global _previous_profile
    deadline = time.monotonic() + 45
    def interrupted():
        return (cancel is not None and cancel()) or time.monotonic() >= deadline
    def command(*args, **kwargs):
        if interrupted():
            raise RuntimeError("연결 취소됨" if cancel and cancel() else "설정 준비 시간 초과")
        return nmcli(*args, cancel=interrupted, **kwargs)
    if ap_active(cancel=interrupted):
        return
    profile = active_profile(cancel=interrupted)
    if profile:
        _previous_profile = profile
        _profile_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(_profile_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(profile)
    if progress:
        progress(12, "주변 WiFi 검색", "NEXT 길게: 취소")
    scan_ssids(force=True, cancel=interrupted)
    result = command("connection", "show", "id", AP_PROFILE, check=False)
    if result.returncode != 0:
        command("connection", "add", "type", "wifi", "ifname", IFACE,
              "con-name", AP_PROFILE, "ssid", AP_SSID, "connection.autoconnect", "no")
    command("connection", "modify", "id", AP_PROFILE,
          "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
          "802-11-wireless.channel", "6", "802-11-wireless.powersave", "2",
          "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", AP_PASS,
          "ipv4.method", "shared", "ipv4.addresses", AP_IP + "/24",
          "ipv6.method", "disabled", "connection.autoconnect", "no")
    # Never kill system-wide wpa_supplicant/dnsmasq or restart NetworkManager.
    if progress:
        progress(20, "설정 AP 시작", "NEXT 길게: 취소")
    command("--wait", "20", "connection", "up", "id", AP_PROFILE, timeout=23)


def stop_ap():
    # Also cancel an activation whose nmcli client was interrupted. NetworkManager
    # owns that async operation and can continue it after its client exits.
    nmcli("--wait", "8", "connection", "down", "id", AP_PROFILE, timeout=10, check=False)


def restore_connection(cancel=None):
    stop_ap()
    # Cancelling before the radio switches (or NM's own auto-reconnect) may
    # already leave the original link healthy. Do not tear it down again.
    if (_previous_profile and wifi_link()["connected"]
            and active_profile(cancel=cancel) == _previous_profile):
        return True
    nmcli("device", "set", IFACE, "managed", "yes")
    if _previous_profile:
        result = nmcli("--wait", "20", "connection", "up", "uuid", _previous_profile,
                       timeout=23, cancel=cancel, check=False)
        return result.returncode == 0 and wifi_link()["connected"]
    nmcli("--wait", "20", "device", "connect", IFACE, timeout=23, cancel=cancel, check=False)
    return wifi_link()["connected"]


def validate_credentials(ssid, psk):
    if not ssid or len(ssid.encode("utf-8")) > 32 or "\x00" in ssid:
        raise ValueError("SSID는 UTF-8 1~32바이트여야 합니다.")
    if psk and not (8 <= len(psk.encode("utf-8")) <= 63 or
                    (len(psk) == 64 and all(c in "0123456789abcdefABCDEF" for c in psk))):
        raise ValueError("비밀번호는 8~63바이트 또는 64자리 16진수여야 합니다.")
    if "\x00" in psk or "\n" in psk or "\r" in psk:
        raise ValueError("비밀번호에 잘못된 문자가 있습니다.")


def connect_network(ssid, psk, cancel=None, hidden=False):
    validate_credentials(ssid, psk)
    stop_ap()
    nmcli("device", "set", IFACE, "managed", "yes")
    # A failed password attempt must not overwrite the last working profile.
    candidate = "gdseng-candidate-" + uuid.uuid4().hex[:12]
    args = ["--wait", "25", "device", "wifi", "connect", ssid,
            "ifname", IFACE, "name", candidate]
    if psk:
        args.extend(["password", psk])
    if hidden:
        args.extend(["hidden", "yes"])
    try:
        nmcli(*args, timeout=28, cancel=cancel)
        if not wifi_link()["connected"]:
            raise RuntimeError("무선 연결 후 IP 주소를 받지 못했습니다.")
        nmcli("connection", "modify", "id", candidate, "connection.id", ssid,
              "802-11-wireless.powersave", "2", "connection.autoconnect", "yes",
              "connection.autoconnect-priority", "100")
        return True
    except Exception:
        nmcli("connection", "delete", "id", candidate, check=False)
        raise


def delete_saved_nm(uuid):
    known = {item["nm_id"] for item in list_saved_nm()}
    if uuid not in known:
        return False, "저장된 Wi-Fi를 찾지 못했습니다."
    nmcli("connection", "delete", "uuid", uuid)
    return True, "삭제 완료"


def reset_wifi_config():
    # Excludes our active AP, so the user can continue entering new credentials.
    for item in list_saved_nm():
        nmcli("connection", "delete", "uuid", item["nm_id"])
    return True, "초기화 완료"
