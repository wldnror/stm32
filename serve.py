from datetime import datetime
import os
import sys
import time
import threading
import subprocess
import re

import RPi.GPIO as GPIO
import wifi_portal

import app_state as st
from app_config import *
from app_utils import (
    ensure_menu_config_csv,
    is_fw_extract_mode,
    is_memory_lock_enabled,
    run_capture,
    run_quiet,
    has_real_internet,
    get_ip_address,
)
from power_manager import init_ina219, battery_monitor_thread
from stm32_manager import (
    kill_openocd,
    run_openocd_ok,
    detect_stm32_flash_kb_with_unlock,
    resolve_target_bin_by_gas,
    make_openocd_program_cmd,
    has_recent_unlock,
    mark_recent_unlock,
    check_stm32_connection,
)
from modbus_manager import (
    tftp_upgrade_device,
    modbus_scan_loop,
    modbus_detail_poll_thread,
    scan_compute_prefix,
)
from menu_manager import (
    build_scan_menu,
    build_scan_detail_menu,
    build_menu_for_dir,
    refresh_root_menu,
    is_root_menu_view,
)
from display_manager import (
    update_oled_display,
    wifi_stage_set,
    wifi_stage_clear,
    wifi_stage_tick,
    set_ui_progress,
    set_ui_text,
    clear_ui_override,
)

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

button_state_lock = threading.Lock()
oled_refresh_lock = threading.Lock()

if not hasattr(st, "ui_transition_until"):
    st.ui_transition_until = 0.0
if not hasattr(st, "stm32_disconnected_since"):
    st.stm32_disconnected_since = 0.0
if not hasattr(st, "auto_flash_cooldown_until"):
    st.auto_flash_cooldown_until = 0.0
if not hasattr(st, "last_stm32_poll_ok"):
    st.last_stm32_poll_ok = False


def now_mono():
    return time.monotonic()


def in_ui_transition() -> bool:
    return now_mono() < getattr(st, "ui_transition_until", 0.0)


def enter_ui_transition(sec: float = 0.08, flush_events: bool = True):
    st.ui_transition_until = now_mono() + max(0.0, float(sec))
    if flush_events:
        with button_state_lock:
            st.execute_short_event = False
            st.next_pressed_event = False
            st.execute_long_handled = False
            st.next_long_handled = False


def force_oled_refresh():
    try:
        with oled_refresh_lock:
            update_oled_display()
            st.last_oled_update_time = time.time()
            st.need_update = False
    except Exception:
        pass


def progress_stage(pct: int, line1: str, line2: str = "", pos=(8, 6), font_size=14, immediate=True):
    msg = line1 if not line2 else f"{line1}\n{line2}"
    set_ui_progress(pct, msg, pos=pos, font_size=font_size)
    st.need_update = True
    if immediate:
        force_oled_refresh()


def text_stage(line1: str, line2: str = "", pos=(10, 18), font_size=15, immediate=True):
    set_ui_text(line1, line2, pos=pos, font_size=font_size)
    st.need_update = True
    if immediate:
        force_oled_refresh()


def portal_set_state_safe(**kwargs):
    try:
        if hasattr(wifi_portal, "_set_state"):
            wifi_portal._set_state(**kwargs)
            return
    except Exception:
        pass
    try:
        state_obj = getattr(wifi_portal, "_state", None)
        if isinstance(state_obj, dict):
            state_obj.update(kwargs)
    except Exception:
        pass
    try:
        if hasattr(wifi_portal, "_write_state_file"):
            wifi_portal._write_state_file()
    except Exception:
        pass


def portal_pop_req_safe():
    try:
        if hasattr(wifi_portal, "_pop_req_file"):
            return wifi_portal._pop_req_file()
    except Exception:
        pass
    return None


def portal_clear_req_safe():
    try:
        state_obj = getattr(wifi_portal, "_state", None)
        if isinstance(state_obj, dict):
            state_obj["requested"] = None
    except Exception:
        pass
    portal_set_state_safe(requested=None)


def button_next_edge(channel):
    now = now_mono()
    with button_state_lock:
        if (now - st.last_time_button_next_pressed) < SOFT_DEBOUNCE_NEXT:
            return
        st.last_time_button_next_pressed = now

        if GPIO.input(BUTTON_PIN_NEXT) == GPIO.LOW:
            st.next_press_time = now
            st.next_is_down = True
            st.next_long_handled = False
        else:
            if st.next_is_down and (not st.next_long_handled) and (st.next_press_time is not None):
                dt = now - st.next_press_time
                if dt < NEXT_LONG_CANCEL_THRESHOLD:
                    st.next_pressed_event = True
            st.next_is_down = False
            st.next_press_time = None


def button_execute_edge(channel):
    now = now_mono()
    with button_state_lock:
        if (now - st.last_time_button_execute_pressed) < SOFT_DEBOUNCE_EXEC:
            return
        st.last_time_button_execute_pressed = now

        if GPIO.input(BUTTON_PIN_EXECUTE) == GPIO.LOW:
            st.execute_press_time = now
            st.execute_is_down = True
            st.execute_long_handled = False
        else:
            if st.execute_is_down and (not st.execute_long_handled) and (st.execute_press_time is not None):
                st.execute_short_event = True
            st.execute_is_down = False
            st.execute_press_time = None


GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)

GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.BOTH, callback=button_next_edge, bouncetime=40)
GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.BOTH, callback=button_execute_edge, bouncetime=40)

GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)
GPIO.setup(LED_ERROR1, GPIO.OUT)


def git_head_hash():
    rc, out, _ = run_capture(["git", "-C", GIT_REPO_DIR, "rev-parse", "HEAD"], timeout=1.2)
    if rc != 0:
        return None
    v = (out or "").strip()
    return v if v else None


def git_branch_name():
    rc, out, _ = run_capture(["git", "-C", GIT_REPO_DIR, "rev-parse", "--abbrev-ref", "HEAD"], timeout=1.2)
    if rc != 0:
        return None
    v = (out or "").strip()
    if not v or v == "HEAD":
        return None
    return v


def git_has_origin():
    rc, out, _ = run_capture(["git", "-C", GIT_REPO_DIR, "remote"], timeout=1.2)
    if rc != 0:
        return False
    remotes = [x.strip() for x in (out or "").splitlines() if x.strip()]
    return "origin" in remotes


def git_upstream_hash():
    rc, out, _ = run_capture(["git", "-C", GIT_REPO_DIR, "rev-parse", "@{u}"], timeout=1.2)
    if rc != 0:
        return None
    v = (out or "").strip()
    return v if v else None


def git_has_remote_updates_light(timeout=2.2) -> bool:
    if not os.path.isdir(GIT_REPO_DIR):
        return False
    if not git_has_origin():
        return False

    b = git_branch_name()
    lh = git_head_hash()
    if not b or not lh:
        return False

    rc, out, _ = run_capture(["git", "-C", GIT_REPO_DIR, "ls-remote", "origin", f"refs/heads/{b}"], timeout=timeout)
    if rc == 0:
        lines = (out or "").strip().splitlines()
        if lines:
            rh = (lines[0].split() or [""])[0].strip()
            if rh:
                return rh != lh

    run_quiet(["git", "-C", GIT_REPO_DIR, "remote", "update"], timeout=3.8)
    uh = git_upstream_hash()
    if not uh:
        return False
    return uh != lh


def git_poll_thread():
    prev = None
    while not st.stop_threads:
        try:
            if not st.cached_online:
                with st.git_state_lock:
                    st.git_has_update_cached = False
                time.sleep(0.6)
                continue

            now = time.time()
            if now - st.git_last_check < st.git_check_interval:
                time.sleep(0.15)
                continue

            st.git_last_check = now

            with st.wifi_action_lock:
                wifi_running = st.wifi_action_running

            if wifi_running or st.is_executing or st.is_command_executing:
                time.sleep(0.2)
                continue

            ok = False
            try:
                ok = git_has_remote_updates_light(timeout=2.2)
            except Exception:
                ok = False

            with st.git_state_lock:
                st.git_has_update_cached = bool(ok)

            if prev is None or prev != ok:
                prev = ok
                if is_root_menu_view():
                    refresh_root_menu(reset_index=False)
                st.need_update = True

            time.sleep(0.15)
        except Exception:
            time.sleep(0.5)


def nm_is_active():
    rc, out, _ = run_capture(["systemctl", "is-active", "NetworkManager"], timeout=2.0)
    return (rc == 0) and ("active" in out.strip())


def nm_restart():
    run_quiet(["sudo", "systemctl", "enable", "--now", "NetworkManager"], timeout=6.0)
    run_quiet(["sudo", "systemctl", "restart", "NetworkManager"], timeout=6.0)


def nm_set_managed(managed: bool):
    v = "yes" if managed else "no"
    run_quiet(["sudo", "nmcli", "dev", "set", "wlan0", "managed", v], timeout=4.0)


def nm_disconnect_wlan0():
    run_quiet(["sudo", "nmcli", "dev", "disconnect", "wlan0"], timeout=4.0)


def nm_get_active_wifi_profile():
    rc, out, _ = run_capture(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], timeout=3.0)
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 3:
            name, ctype, dev = parts[0], parts[1], parts[2]
            if ctype == "wifi" and dev == "wlan0" and name:
                return name
    return None


def nm_autoconnect(timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if has_real_internet():
            return True
        time.sleep(0.7)
    return has_real_internet()


def nm_connect(ssid: str, psk: str, timeout=30):
    run_quiet(["sudo", "nmcli", "dev", "wifi", "rescan", "ifname", "wlan0"], timeout=6.0)
    if psk:
        cmd = ["sudo", "nmcli", "--wait", str(int(timeout)), "dev", "wifi", "connect", ssid, "password", psk, "ifname", "wlan0"]
    else:
        cmd = ["sudo", "nmcli", "--wait", str(int(timeout)), "dev", "wifi", "connect", ssid, "ifname", "wlan0"]
    rc, _, _ = run_capture(cmd, timeout=timeout + 5)
    if rc == 0:
        return True
    run_quiet(["sudo", "nmcli", "dev", "wifi", "rescan", "ifname", "wlan0"], timeout=6.0)
    rc2, _, _ = run_capture(cmd, timeout=timeout + 5)
    return rc2 == 0


def nm_up_profile(nm_id: str, timeout=20) -> bool:
    if not nm_id:
        return False
    rc, _, _ = run_capture(["sudo", "nmcli", "--wait", str(int(timeout)), "connection", "up", "id", nm_id], timeout=timeout + 2)
    return rc == 0


def wpa_select_saved_ssid(ssid: str) -> bool:
    if not ssid:
        return False
    rc, out, _ = run_capture(["sudo", "wpa_cli", "-i", "wlan0", "list_networks"], timeout=4.0)
    if rc != 0:
        return False

    net_id = None
    for line in (out or "").splitlines():
        line = line.strip()
        if not line or line.startswith("network id"):
            continue
        parts = re.split(r"\t+", line)
        if len(parts) >= 2 and parts[1] == ssid:
            net_id = parts[0]
            break

    if net_id is None:
        return False

    run_quiet(["sudo", "wpa_cli", "-i", "wlan0", "select_network", net_id], timeout=3.0)
    run_quiet(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", net_id], timeout=3.0)
    run_quiet(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], timeout=4.0)
    run_quiet(["sudo", "dhclient", "-r", "wlan0"], timeout=6.0)
    run_quiet(["sudo", "dhclient", "wlan0"], timeout=10.0)
    return True


def kill_portal_tmp_procs():
    cmd = r"""sudo bash -lc '
pids=$(pgrep -a hostapd | awk "/\/tmp\/hostapd\.conf/{print \$1}" | xargs)
[ -n "$pids" ] && kill -9 $pids || true
pids=$(pgrep -a dnsmasq | awk "/\/tmp\/dnsmasq\.conf/{print \$1}" | xargs)
[ -n "$pids" ] && kill -9 $pids || true
' """
    run_quiet(cmd, timeout=6.0, shell=True)


def wlan0_soft_reset():
    run_quiet(["sudo", "ip", "addr", "flush", "dev", "wlan0"], timeout=3.0)
    run_quiet(["sudo", "ip", "link", "set", "wlan0", "down"], timeout=3.0)
    time.sleep(1)
    run_quiet(["sudo", "ip", "link", "set", "wlan0", "up"], timeout=3.0)
    time.sleep(1)


def request_wifi_setup():
    with st.wifi_action_lock:
        st.wifi_action_requested = True


def prepare_for_ap_mode():
    try:
        prof = nm_get_active_wifi_profile()
        if prof:
            st.last_good_wifi_profile = prof
    except Exception:
        pass

    if nm_is_active():
        nm_disconnect_wlan0()
        nm_set_managed(False)
        time.sleep(0.3)


def restore_after_ap_mode(timeout=25):
    wifi_stage_set(5, "WiFi 종료 중", "프로세스 정리")
    kill_portal_tmp_procs()
    run_quiet(["sudo", "systemctl", "stop", "hostapd"], timeout=3.0)
    run_quiet(["sudo", "systemctl", "stop", "dnsmasq"], timeout=3.0)
    wifi_stage_set(25, "WiFi 재시작", "인터페이스 초기화")
    wlan0_soft_reset()
    wifi_stage_set(45, "WiFi 재시작", "NetworkManager")
    nm_set_managed(True)
    nm_restart()
    time.sleep(1.2)

    if st.last_good_wifi_profile:
        wifi_stage_set(60, "재연결 중", st.last_good_wifi_profile[:18])
        run_quiet(["sudo", "nmcli", "connection", "up", st.last_good_wifi_profile], timeout=12.0)

    wifi_stage_set(75, "인터넷 확인", "")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if has_real_internet():
            wifi_stage_set(100, "완료", "")
            time.sleep(0.4)
            wifi_stage_clear()
            return True
        p = 75 + int(25 * ((time.time() - t0) / max(1.0, timeout)))
        wifi_stage_set(min(99, p), "인터넷 확인", "")
        time.sleep(0.35)

    ok = has_real_internet()
    wifi_stage_set(100 if ok else 0, "완료" if ok else "실패", "")
    time.sleep(0.6)
    wifi_stage_clear()
    return ok


def connect_from_portal_nm(ssid: str, psk: str, timeout=35):
    wifi_stage_set(10, "연결 준비", "AP 종료")
    portal_set_state_safe(connect_stage="연결 준비 중…", last_error="", last_ok="")

    try:
        if hasattr(wifi_portal, "stop_ap"):
            wifi_portal.stop_ap()
    except Exception:
        pass

    kill_portal_tmp_procs()
    run_quiet(["sudo", "systemctl", "stop", "hostapd"], timeout=3.0)
    run_quiet(["sudo", "systemctl", "stop", "dnsmasq"], timeout=3.0)

    wifi_stage_set(30, "연결 준비", "인터페이스 초기화")
    portal_set_state_safe(connect_stage="인터페이스 초기화 중…")
    wlan0_soft_reset()

    wifi_stage_set(50, "연결 준비", "NetworkManager")
    portal_set_state_safe(connect_stage="NetworkManager 준비 중…")
    nm_set_managed(True)
    nm_restart()
    time.sleep(1.5)

    wifi_stage_set(70, "WiFi 연결 중", ssid[:18])
    portal_set_state_safe(connect_stage="무선 연결 시도 중…")
    ok = nm_connect(ssid, psk, timeout=timeout)
    if not ok:
        portal_set_state_safe(last_error="연결 실패", connect_stage="")
        wifi_stage_set(0, "연결 실패", "")
        time.sleep(0.8)
        wifi_stage_clear()
        return False

    wifi_stage_set(85, "인터넷 확인", "")
    portal_set_state_safe(connect_stage="인터넷 확인 중…")
    ok2 = nm_autoconnect(timeout=20)

    if ok2:
        portal_set_state_safe(last_ok="연결 완료", last_error="", connect_stage="연결 완료")
    else:
        portal_set_state_safe(last_error="인터넷 확인 실패", connect_stage="")

    wifi_stage_set(100 if ok2 else 0, "완료" if ok2 else "실패", "")
    time.sleep(0.6)
    wifi_stage_clear()
    return ok2


def portal_loop_until_connected_or_cancel():
    prepare_for_ap_mode()
    wifi_stage_clear()
    portal_set_state_safe(last_error="", last_ok="", connect_stage="설정 모드 시작", running=True, done=False)
    wifi_portal.start_ap()

    try:
        state_obj = getattr(wifi_portal, "_state", {})
        if isinstance(state_obj, dict) and (not state_obj.get("server_started", False)):
            wifi_portal.run_portal(block=False)
            state_obj["server_started"] = True
            portal_set_state_safe(server_started=True)
    except Exception:
        try:
            wifi_portal.run_portal(block=False)
        except Exception:
            pass

    t0 = time.time()
    while True:
        if st.wifi_cancel_requested:
            portal_set_state_safe(connect_stage="취소 처리 중…")
            try:
                if hasattr(wifi_portal, "stop_ap"):
                    wifi_portal.stop_ap()
            except Exception:
                pass
            return "cancel"

        req = None
        try:
            state_obj = getattr(wifi_portal, "_state", {})
            if isinstance(state_obj, dict):
                req = state_obj.get("requested")
        except Exception:
            req = None

        if not req:
            req = portal_pop_req_safe()
            if req and isinstance(getattr(wifi_portal, "_state", None), dict):
                try:
                    wifi_portal._state["requested"] = req
                except Exception:
                    pass

        if req:
            portal_clear_req_safe()

            mode = (req.get("mode") or "new").strip().lower()
            src = (req.get("src") or "").strip().lower()
            ssid = (req.get("ssid") or "").strip()
            psk = (req.get("psk") or "").strip()
            nm_id = (req.get("nm_id") or "").strip()

            ok = False
            if mode == "saved":
                wifi_stage_set(60, "저장된 WiFi", "연결 시도")
                portal_set_state_safe(connect_stage="저장된 설정으로 연결 중…", last_error="", last_ok="")
                if src == "nm" and nm_id:
                    wlan0_soft_reset()
                    nm_set_managed(True)
                    nm_restart()
                    time.sleep(1.0)
                    portal_set_state_safe(connect_stage="NetworkManager 연결 시도 중…")
                    ok = nm_up_profile(nm_id, timeout=20) and nm_autoconnect(timeout=20)
                elif src == "wpa" and ssid:
                    wlan0_soft_reset()
                    portal_set_state_safe(connect_stage="wpa_supplicant 연결 시도 중…")
                    ok = wpa_select_saved_ssid(ssid) and nm_autoconnect(timeout=20)
                else:
                    ok = False
            else:
                if ssid:
                    ok = connect_from_portal_nm(ssid, psk, timeout=35)

            if ok:
                portal_set_state_safe(last_ok="연결 완료", last_error="", connect_stage="연결 완료", running=False, done=True)
                return True

            portal_set_state_safe(last_error="연결 실패", connect_stage="", running=True, done=False)
            prepare_for_ap_mode()
            wifi_stage_clear()
            wifi_portal.start_ap()

        if time.time() - t0 > 600:
            portal_set_state_safe(last_error="시간 초과", connect_stage="", running=False)
            return False

        time.sleep(0.2)


def wifi_worker_thread():
    while not st.stop_threads:
        do = False
        with st.wifi_action_lock:
            if st.wifi_action_requested and (not st.wifi_action_running):
                st.wifi_action_requested = False
                st.wifi_action_running = True
                do = True

        if do:
            try:
                st.wifi_cancel_requested = False
                wifi_stage_clear()
                with st.ap_state_lock:
                    st.ap_state["last_clients"] = 0
                    st.ap_state["flash_until"] = 0.0
                    st.ap_state["poll_next"] = 0.0
                    st.ap_state["spinner"] = 0

                portal_set_state_safe(last_error="", last_ok="", connect_stage="설정 시작 준비…", running=True, done=False)

                r1 = subprocess.run(["which", "hostapd"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                r2 = subprocess.run(["which", "dnsmasq"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                if (r1.returncode != 0) or (r2.returncode != 0):
                    st.status_message = "AP 구성 불가"
                    st.message_position = (12, 10)
                    st.message_font_size = 15
                    st.need_update = True
                    portal_set_state_safe(last_error="hostapd/dnsmasq 없음", connect_stage="", running=False)
                    time.sleep(2.0)
                else:
                    st.status_message = ""
                    st.need_update = True

                    result = portal_loop_until_connected_or_cancel()
                    refresh_root_menu(reset_index=True)
                    enter_ui_transition(0.10)
                    st.need_update = True

                    if result == "cancel":
                        wifi_stage_set(10, "취소 처리중", "재연결 준비")
                        portal_set_state_safe(connect_stage="취소 처리 중…", last_error="취소됨", last_ok="")
                        ok_restore = restore_after_ap_mode(timeout=25)
                        text_stage("재연결 완료" if ok_restore else "재연결 실패", "")
                        time.sleep(1.0)
                        clear_ui_override()
                        wifi_stage_clear()
                        portal_set_state_safe(connect_stage="", running=False)
                    elif result is True:
                        text_stage("WiFi 연결 완료", "")
                        time.sleep(1.1)
                        clear_ui_override()
                        wifi_stage_clear()
                        portal_set_state_safe(last_ok="연결 완료", last_error="", connect_stage="연결 완료", running=False, done=True)
                    else:
                        text_stage("WiFi 연결 실패", "")
                        time.sleep(1.1)
                        clear_ui_override()
                        wifi_stage_clear()
                        portal_set_state_safe(last_error="연결 실패", connect_stage="", running=False)

                st.status_message = ""
                st.need_update = True
            finally:
                with st.wifi_action_lock:
                    st.wifi_action_running = False

        time.sleep(0.05)


def get_ap_station_count():
    try:
        r = subprocess.run(["iw", "dev", "wlan0", "station", "dump"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=0.7)
        if r.returncode != 0:
            return 0
        return sum(1 for line in (r.stdout or "").splitlines() if line.strip().startswith("Station "))
    except Exception:
        return 0


def ap_client_tick(wifi_running: bool):
    now = time.time()
    with st.ap_state_lock:
        st.ap_state["spinner"] = (st.ap_state["spinner"] + 1) % 4
        if not wifi_running:
            st.ap_state["last_clients"] = 0
            st.ap_state["flash_until"] = 0.0
            st.ap_state["poll_next"] = 0.0
            return
        if now < st.ap_state["poll_next"]:
            return
        st.ap_state["poll_next"] = now + 0.8
        prev = st.ap_state["last_clients"]

    cnt = get_ap_station_count()

    with st.ap_state_lock:
        st.ap_state["last_clients"] = cnt
        if cnt > 0 and prev == 0:
            st.ap_state["flash_until"] = now + 1.3


def realtime_update_display():
    while not st.stop_threads:
        with st.wifi_action_lock:
            wifi_running = st.wifi_action_running

        wifi_stage_tick()
        ap_client_tick(wifi_running)

        now = time.time()

        with button_state_lock:
            next_is_down_now = st.next_is_down
            execute_is_down_now = st.execute_is_down

        if st.scan_menu_dirty and st.current_menu and st.current_menu.get("dir") == "__scan__":
            if (not next_is_down_now) and (not execute_is_down_now):
                if now - st.scan_menu_rebuild_last >= SCAN_MENU_REBUILD_MIN_SEC:
                    st.scan_menu_rebuild_last = now
                    with st.scan_lock:
                        st.scan_menu_dirty = False
                        ips_now = list(st.scan_ips)
                        sel_ip = st.scan_selected_ip

                    nm = build_scan_menu()
                    st.current_menu = nm
                    st.commands = nm["commands"]
                    st.command_names = nm["names"]
                    st.command_types = nm["types"]
                    st.menu_extras = nm["extras"]

                    if sel_ip and (sel_ip in ips_now):
                        new_idx = ips_now.index(sel_ip)
                        st.current_command_index = new_idx
                        with st.scan_lock:
                            st.scan_selected_idx = new_idx
                    else:
                        if st.current_command_index >= len(st.commands):
                            st.current_command_index = max(0, len(st.commands) - 1)

                    enter_ui_transition(0.05)
                    st.need_update = True

        if st.need_update or (now - st.last_oled_update_time >= 0.05):
            with oled_refresh_lock:
                update_oled_display()
                st.last_oled_update_time = now
                st.need_update = False

        time.sleep(0.005)


def get_wifi_level():
    try:
        rc, out, _ = run_capture(["iw", "dev", "wlan0", "link"], timeout=0.6)
        if rc != 0 or "Not connected" in out:
            return 0
        r = subprocess.run(["iwconfig", "wlan0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=0.6)
        m = re.search(r"Signal level=(-?\d+)\s*dBm", r.stdout)
        if not m:
            return 0
        dbm = int(m.group(1))
        if dbm >= -55:
            return 4
        if dbm >= -65:
            return 3
        if dbm >= -75:
            return 2
        if dbm >= -85:
            return 1
        return 0
    except Exception:
        return 0


def net_poll_thread():
    while not st.stop_threads:
        try:
            st.cached_ip = get_ip_address()
            online_now = has_real_internet()
            st.cached_online = online_now
            st.cached_wifi_level = get_wifi_level() if online_now else 0

            if st.last_menu_online is None or online_now != st.last_menu_online:
                st.last_menu_online = online_now
                if is_root_menu_view():
                    refresh_root_menu(reset_index=False)
                st.need_update = True

            time.sleep(1.5)
        except Exception:
            time.sleep(0.8)


def git_pull():
    shell_script_path = "/home/user/stm32/git-pull.sh"
    if not os.path.isfile(shell_script_path):
        with open(shell_script_path, "w") as script_file:
            script_file.write("#!/bin/bash\n")
            script_file.write("cd /home/user/stm32\n")
            script_file.write("git remote update\n")
            script_file.write("if git status -uno | grep -q 'Your branch is up to date'; then\n")
            script_file.write("   echo '이미 최신 상태입니다.'\n")
            script_file.write("   exit 0\n")
            script_file.write("fi\n")
            script_file.write("git stash\n")
            script_file.write("git pull\n")
            script_file.write("git stash pop\n")
            script_file.flush()
            os.fsync(script_file.fileno())

    os.chmod(shell_script_path, 0o755)
    text_stage("시스템", "업데이트 중", pos=(20, 10))

    try:
        result = subprocess.run([shell_script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

        if result.returncode == 0:
            if "이미 최신 상태" in (result.stdout or ""):
                text_stage("이미 최신 상태", "")
                time.sleep(1.0)
            else:
                GPIO.output(LED_SUCCESS, True)
                text_stage("업데이트 성공!", "")
                time.sleep(1.0)
                GPIO.output(LED_SUCCESS, False)
                restart_script()
        else:
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            text_stage("업데이트 실패", "")
            time.sleep(1.2)
    except Exception:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        text_stage("오류 발생", "")
        time.sleep(1.2)
    finally:
        with st.git_state_lock:
            st.git_has_update_cached = False
        st.git_last_check = 0.0
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()


def unlock_memory():
    if has_recent_unlock():
        progress_stage(35, "업데이트 진행 중", "잠금 해제 생략")
        time.sleep(0.12)
        return True

    progress_stage(35, "업데이트 진행 중", "잠금 해제 중")

    ok = run_openocd_ok(
        ["init", "reset halt", "stm32f1x unlock 0", "reset run", "shutdown"],
        timeout=STM32_UNLOCK_TIMEOUT_SEC,
    )
    mark_recent_unlock(ok)

    if ok:
        progress_stage(40, "업데이트 진행 중", "잠금 해제 완료")
        time.sleep(0.18)
        return True

    progress_stage(0, "업데이트 실패", "잠금 해제 실패")
    time.sleep(0.4)
    return False


def restart_script():
    progress_stage(25, "재시작 중", "")

    def restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=restart, daemon=True).start()


def lock_memory_procedure():
    if not is_memory_lock_enabled():
        text_stage("메모리 잠금", "건너뜀", pos=(18, 18))
        time.sleep(0.35)
        clear_ui_override()
        st.need_update = True
        return

    progress_stage(85, "업데이트 진행 중", "마무리 중")
    try:
        ok = run_openocd_ok(
            ["init", "reset halt", "stm32f1x lock 0", "reset run", "shutdown"],
            timeout=STM32_LOCK_TIMEOUT_SEC,
        )
        if ok:
            GPIO.output(LED_SUCCESS, True)
            progress_stage(100, "업데이트 완료", "")
            time.sleep(0.35)
            GPIO.output(LED_SUCCESS, False)
        else:
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            progress_stage(0, "업데이트 실패", "잠금 실패")
            time.sleep(0.5)
    except Exception:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        progress_stage(0, "오류 발생", "")
        time.sleep(0.5)
    finally:
        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        st.need_update = True


def shutdown_system():
    text_stage("배터리 부족", "시스템 종료 중...", pos=(10, 18))
    time.sleep(2)
    try:
        os.system("sudo shutdown -h now")
    except Exception:
        pass


def execute_command(command_index):
    if not st.command_types or command_index < 0 or command_index >= len(st.command_types):
        return

    st.stm32_disconnected_since = 0.0

    item_type = st.command_types[command_index]

    if item_type == "wifi":
        request_wifi_setup()
        enter_ui_transition(0.08)
        st.need_update = True
        return

    st.is_executing = True
    st.is_command_executing = True

    if not st.commands:
        st.is_executing = False
        st.is_command_executing = False
        return

    if item_type == "device_scan":
        myip = get_ip_address()
        with st.scan_lock:
            st.scan_active = True
            st.scan_done = False
            st.scan_selected_idx = 0
            st.scan_selected_ip = None
            st.scan_infos.clear()
            st.scan_seen.clear()
            st.scan_ips = []
            st.scan_base_prefix = scan_compute_prefix(myip)
            st.scan_menu_dirty = True
            st.scan_menu_dirty_ts = time.time()
        with st.scan_detail_lock:
            st.scan_detail_active = False
            st.scan_detail_ip = None

        st.menu_stack.append((st.current_menu, st.current_command_index))
        st.current_menu = build_scan_menu()
        st.commands = st.current_menu["commands"]
        st.command_names = st.current_menu["names"]
        st.command_types = st.current_menu["types"]
        st.menu_extras = st.current_menu["extras"]
        st.current_command_index = 0
        enter_ui_transition(0.08)
        clear_ui_override()
        st.need_update = True
        st.is_executing = False
        st.is_command_executing = False
        return

    if item_type == "back_from_scan":
        with st.scan_lock:
            st.scan_active = False
            st.scan_done = True
            st.scan_selected_ip = None
        with st.scan_detail_lock:
            st.scan_detail_active = False
            st.scan_detail_ip = None

        if st.menu_stack:
            prev_menu, prev_index = st.menu_stack.pop()
            st.current_menu = prev_menu
            st.commands = st.current_menu["commands"]
            st.command_names = st.current_menu["names"]
            st.command_types = st.current_menu["types"]
            st.menu_extras = st.current_menu["extras"]
            st.current_command_index = prev_index if (0 <= prev_index < len(st.commands)) else 0

        enter_ui_transition(0.08)
        clear_ui_override()
        st.need_update = True
        st.is_executing = False
        st.is_command_executing = False
        return

    if item_type == "scan_item":
        target_ip = st.menu_extras[command_index]
        if not target_ip:
            st.is_executing = False
            st.is_command_executing = False
            return

        clear_ui_override()
        with st.scan_lock:
            st.scan_active = False
            st.scan_done = True
        with st.scan_detail_lock:
            st.scan_detail_active = True
            st.scan_detail_ip = target_ip

        st.scan_detail["gas"] = None
        st.scan_detail["flags"] = {"PWR": False, "A1": False, "A2": False, "FUT": False}
        st.scan_detail["err"] = ""
        st.scan_detail["ts"] = time.time()

        st.menu_stack.append((st.current_menu, st.current_command_index))
        st.current_menu = build_scan_detail_menu(target_ip)
        st.commands = st.current_menu["commands"]
        st.command_names = st.current_menu["names"]
        st.command_types = st.current_menu["types"]
        st.menu_extras = st.current_menu["extras"]
        st.current_command_index = 0

        enter_ui_transition(0.08)
        st.need_update = True
        st.is_executing = False
        st.is_command_executing = False
        return

    if item_type == "scan_detail":
        ip = None
        try:
            ip = st.menu_extras[command_index]
        except Exception:
            ip = None
        if not ip:
            st.is_executing = False
            st.is_command_executing = False
            return

        clear_ui_override()
        tftp_upgrade_device(ip)
        with st.scan_lock:
            st.scan_active = False
            st.scan_done = True
        with st.scan_detail_lock:
            st.scan_detail_active = True
            st.scan_detail_ip = ip

        st.need_update = True
        st.is_executing = False
        st.is_command_executing = False
        return

    if item_type == "dir":
        subdir = st.menu_extras[command_index]
        if subdir and os.path.isdir(subdir):
            st.menu_stack.append((st.current_menu, st.current_command_index))
            st.current_menu = build_menu_for_dir(subdir, is_root=False)
            st.commands = st.current_menu["commands"]
            st.command_names = st.current_menu["names"]
            st.command_types = st.current_menu["types"]
            st.menu_extras = st.current_menu["extras"]
            st.current_command_index = 0
            enter_ui_transition(0.08)
            st.need_update = True
        st.is_executing = False
        st.is_command_executing = False
        return

    if item_type == "back":
        if st.menu_stack:
            prev_menu, prev_index = st.menu_stack.pop()
            st.current_menu = prev_menu
            st.commands = st.current_menu["commands"]
            st.command_names = st.current_menu["names"]
            st.command_types = st.current_menu["types"]
            st.menu_extras = st.current_menu["extras"]
            st.current_command_index = prev_index if (0 <= prev_index < len(st.commands)) else 0
            enter_ui_transition(0.08)
            st.need_update = True
        st.is_executing = False
        st.is_command_executing = False
        return

    if item_type == "system":
        kill_openocd()
        with st.stm32_state_lock:
            st.connection_success = False
            st.connection_failed_since_last_success = False
        git_pull()
        refresh_root_menu(reset_index=True)
        enter_ui_transition(0.08)
        st.need_update = True
        st.is_executing = False
        st.is_command_executing = False
        return

    if item_type == "script":
        if not is_fw_extract_mode():
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            text_stage("FW 추출", "비활성화", pos=(15, 18))
            time.sleep(1.2)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
            clear_ui_override()
            if is_root_menu_view():
                refresh_root_menu(reset_index=False)
            st.need_update = True
            st.is_executing = False
            st.is_command_executing = False
            return

        kill_openocd()
        with st.stm32_state_lock:
            st.connection_success = False
            st.connection_failed_since_last_success = False

        GPIO.output(LED_SUCCESS, False)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)

        if not os.path.isfile(OUT_SCRIPT_PATH):
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            text_stage("out.py 없음", "")
            time.sleep(1.2)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
            clear_ui_override()
            st.need_update = True
            st.is_executing = False
            st.is_command_executing = False
            return

        progress_stage(10, "실행 중", "")
        try:
            result = subprocess.run(st.commands[command_index], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode == 0:
                GPIO.output(LED_SUCCESS, True)
                progress_stage(100, "완료", "")
                time.sleep(0.8)
                GPIO.output(LED_SUCCESS, False)
            else:
                GPIO.output(LED_ERROR, True)
                GPIO.output(LED_ERROR1, True)
                progress_stage(0, "실패", "")
                time.sleep(1.0)
                GPIO.output(LED_ERROR, False)
                GPIO.output(LED_ERROR1, False)
        except Exception:
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            progress_stage(0, "오류 발생", "")
            time.sleep(1.0)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)

        clear_ui_override()
        if is_root_menu_view():
            refresh_root_menu(reset_index=False)

        st.need_update = True
        st.is_executing = False
        st.is_command_executing = False
        return

    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)

    selected_path = None
    try:
        selected_path = st.menu_extras[command_index]
    except Exception:
        selected_path = None

    if not selected_path:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        text_stage("BIN 경로", "없음", pos=(20, 12))
        time.sleep(0.8)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        st.auto_flash_cooldown_until = time.time() + 4.0
        st.is_executing = False
        st.is_command_executing = False
        st.need_update = True
        return

    progress_stage(5, "업데이트 진행 중", "대상 확인")
    dev_id, flash_kb = detect_stm32_flash_kb_with_unlock(timeout=STM32_DETECT_TIMEOUT_SEC)

    progress_stage(20, "업데이트 진행 중", "파일 선택")
    resolved_path, chosen_kind = resolve_target_bin_by_gas(selected_path, flash_kb)

    if not unlock_memory():
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        text_stage("메모리 잠금", "해제 실패", pos=(20, 12))
        time.sleep(0.8)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        st.auto_flash_cooldown_until = time.time() + 4.0
        st.is_executing = False
        st.is_command_executing = False
        st.need_update = True
        return

    info_line = chosen_kind
    if flash_kb is not None:
        info_line = f"{chosen_kind} ({flash_kb}KB)"

    progress_msg = f"업데이트 진행 중\n{info_line}"
    set_ui_progress(45, progress_msg, pos=(6, 0), font_size=13)
    st.need_update = True
    force_oled_refresh()

    openocd_cmd = make_openocd_program_cmd(resolved_path)
    process = subprocess.Popen(openocd_cmd, shell=True)
    start_time = time.time()
    progress_increment = 35 / PROGRAM_PROGRESS_MAX_DURATION_SEC

    while process.poll() is None:
        elapsed = time.time() - start_time
        current_progress = 45 + (elapsed * progress_increment)
        current_progress = min(current_progress, 80)
        set_ui_progress(current_progress, progress_msg, pos=(6, 0), font_size=13)
        st.need_update = True
        force_oled_refresh()
        time.sleep(PROGRAM_PROGRESS_POLL_SEC)

    result = process.returncode
    if result == 0:
        set_ui_progress(80, f"업데이트 진행 중\n{info_line}", pos=(6, 0), font_size=13)
        st.need_update = True
        force_oled_refresh()
        st.auto_flash_cooldown_until = time.time() + 4.0
        if is_memory_lock_enabled():
            time.sleep(POST_FLASH_WAIT_SEC)
            lock_memory_procedure()
        else:
            progress_stage(100, "업데이트 완료", "")
            time.sleep(0.4)
    else:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        st.auto_flash_cooldown_until = time.time() + 4.0
        progress_stage(0, "업데이트 실패", "")
        time.sleep(0.6)

    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)
    clear_ui_override()
    st.need_update = True
    st.is_executing = False
    st.is_command_executing = False


def execute_button_logic():
    while not st.stop_threads:
        now = now_mono()

        if st.battery_percentage == 0:
            shutdown_system()

        if in_ui_transition():
            time.sleep(0.01)
            continue

        with st.wifi_action_lock:
            wifi_running = st.wifi_action_running

        with button_state_lock:
            next_is_down = st.next_is_down
            next_long_handled = st.next_long_handled
            next_press_time = st.next_press_time

            execute_is_down = st.execute_is_down
            execute_long_handled = st.execute_long_handled
            execute_press_time = st.execute_press_time

            execute_short_event = st.execute_short_event
            next_pressed_event = st.next_pressed_event

        if wifi_running and next_is_down and (not next_long_handled) and (next_press_time is not None):
            if now - next_press_time >= NEXT_LONG_CANCEL_THRESHOLD:
                with button_state_lock:
                    st.next_long_handled = True
                st.wifi_cancel_requested = True
                wifi_stage_set(5, "취소 처리중", "잠시만")
                st.need_update = True

        if (not wifi_running) and next_is_down and (not next_long_handled) and (next_press_time is not None):
            if now - next_press_time >= NEXT_LONG_CANCEL_THRESHOLD:
                if st.current_menu and st.current_menu.get("dir") == "__scan__":
                    with button_state_lock:
                        st.next_long_handled = True
                    execute_command(len(st.command_types) - 1)
                    st.need_update = True

        if execute_is_down and (not execute_long_handled) and (execute_press_time is not None):
            if now - execute_press_time >= LONG_PRESS_THRESHOLD:
                with button_state_lock:
                    st.execute_long_handled = True
                    st.execute_short_event = False
                if st.commands and (not st.is_executing):
                    item_type = st.command_types[st.current_command_index]
                    if item_type in ("system", "dir", "back", "script", "wifi", "bin", "device_scan", "scan_item", "back_from_scan", "scan_detail"):
                        execute_command(st.current_command_index)
                        st.need_update = True

        if execute_short_event:
            with button_state_lock:
                st.execute_short_event = False

            with button_state_lock:
                long_handled_after = st.execute_long_handled

            if not long_handled_after:
                if st.commands and (not st.is_executing):
                    st.current_command_index = (st.current_command_index - 1) % len(st.commands)
                    if st.current_menu and st.current_menu.get("dir") == "__scan__":
                        with st.scan_lock:
                            if st.scan_ips and st.command_types[st.current_command_index] == "scan_item":
                                st.scan_selected_idx = min(st.current_command_index, len(st.scan_ips) - 1)
                                st.scan_selected_ip = st.scan_ips[st.scan_selected_idx]
                    st.need_update = True

            with button_state_lock:
                st.execute_long_handled = False

        if next_pressed_event:
            if (st.current_menu and st.current_menu.get("dir") == "__scan_detail__") and (not st.is_executing):
                with st.scan_detail_lock:
                    st.scan_detail_active = False
                    st.scan_detail_ip = None
                if st.menu_stack:
                    prev_menu, prev_index = st.menu_stack.pop()
                    st.current_menu = prev_menu
                    st.commands = st.current_menu["commands"]
                    st.command_names = st.current_menu["names"]
                    st.command_types = st.current_menu["types"]
                    st.menu_extras = st.current_menu["extras"]
                    st.current_command_index = prev_index if (0 <= prev_index < len(st.commands)) else 0
                enter_ui_transition(0.08)
                clear_ui_override()
                st.need_update = True
                with button_state_lock:
                    st.next_pressed_event = False
                time.sleep(0.01)
                continue

            with button_state_lock:
                exec_down_now = st.execute_is_down

            if (not exec_down_now) and (not st.is_executing):
                if st.commands:
                    st.current_command_index = (st.current_command_index + 1) % len(st.commands)
                    if st.current_menu and st.current_menu.get("dir") == "__scan__":
                        with st.scan_lock:
                            if st.scan_ips and st.command_types[st.current_command_index] == "scan_item":
                                st.scan_selected_idx = min(st.current_command_index, len(st.scan_ips) - 1)
                                st.scan_selected_ip = st.scan_ips[st.scan_selected_idx]
                    st.need_update = True

            with button_state_lock:
                st.next_pressed_event = False

        # 옛날 방식처럼 메인 루프에서 직접 STM32 연결 감지 + 자동실행
        if (not st.is_executing) and (not st.is_command_executing):
            now_wall = time.time()
            if now_wall - st.last_stm32_check_time >= STM32_POLL_INTERVAL_SEC:
                st.last_stm32_check_time = now_wall

                prev_ok = st.last_stm32_poll_ok
                cur_ok = check_stm32_connection()
                st.last_stm32_poll_ok = cur_ok

                if not cur_ok:
                    if st.stm32_disconnected_since == 0.0:
                        st.stm32_disconnected_since = now_wall
                else:
                    new_attach = False

                    if not prev_ok:
                        if st.stm32_disconnected_since == 0.0:
                            # 첫 연결 또는 부팅 직후 연결
                            new_attach = True
                        else:
                            # 실제 분리 후 재연결
                            if (now_wall - st.stm32_disconnected_since) >= 2.0:
                                new_attach = True

                    if new_attach:
                        st.auto_flash_done_connection = False

                    st.stm32_disconnected_since = 0.0

                with st.stm32_state_lock:
                    cs = st.connection_success

                if st.commands:
                    if (
                        cs
                        and (not st.auto_flash_done_connection)
                        and (time.time() >= getattr(st, "auto_flash_cooldown_until", 0.0))
                        and st.command_types[st.current_command_index] == "bin"
                    ):
                        st.auto_flash_done_connection = True
                        st.stm32_disconnected_since = 0.0
                        st.auto_flash_cooldown_until = time.time() + 4.0
                        progress_stage(1, "업데이트 진행 중", "시작 준비")
                        execute_command(st.current_command_index)

        time.sleep(0.01)


ensure_menu_config_csv()
init_ina219()
refresh_root_menu(reset_index=True)

battery_thread = threading.Thread(target=battery_monitor_thread, daemon=True)
battery_thread.start()

realtime_update_thread = threading.Thread(target=realtime_update_display, daemon=True)
realtime_update_thread.start()

wifi_thread = threading.Thread(target=wifi_worker_thread, daemon=True)
wifi_thread.start()

net_thread = threading.Thread(target=net_poll_thread, daemon=True)
net_thread.start()

git_thread = threading.Thread(target=git_poll_thread, daemon=True)
git_thread.start()

scan_thread = threading.Thread(target=modbus_scan_loop, daemon=True)
scan_thread.start()

detail_thread = threading.Thread(target=modbus_detail_poll_thread, daemon=True)
detail_thread.start()

st.need_update = True

try:
    execute_button_logic()
except KeyboardInterrupt:
    pass
finally:
    st.stop_threads = True
    try:
        kill_openocd()
    except Exception:
        pass
    GPIO.cleanup()
