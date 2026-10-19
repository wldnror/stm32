from datetime import datetime
import os
import sys
import time
import threading
import subprocess
import re
import logging

from boot_status import publish, menu_ready
from process_utils import run_command
from network_utils import wifi_link, internet_available
import wifi_backend

import RPi.GPIO as GPIO

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
    initialize_display,
    display_ready,
    update_oled_display,
    wifi_stage_set,
    wifi_stage_clear,
    wifi_stage_tick,
    set_ui_progress,
    set_ui_text,
    clear_ui_override,
)

def portal_module():
    import wifi_portal
    return wifi_portal


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
if not hasattr(st, "physical_disconnect_armed"):
    st.physical_disconnect_armed = False


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
        if hasattr(portal_module(), "_set_state"):
            portal_module()._set_state(**kwargs)
            return
    except Exception:
        pass
    try:
        state_obj = getattr(portal_module(), "_state", None)
        if isinstance(state_obj, dict):
            state_obj.update(kwargs)
    except Exception:
        pass
    try:
        if hasattr(portal_module(), "_write_state_file"):
            portal_module()._write_state_file()
    except Exception:
        pass


def portal_pop_req_safe():
    try:
        if hasattr(portal_module(), "_pop_req_file"):
            return portal_module()._pop_req_file()
    except Exception:
        pass
    return None


def portal_clear_req_safe():
    try:
        state_obj = getattr(portal_module(), "_state", None)
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


def initialize_gpio(timeout=12.0):
    deadline = time.monotonic() + timeout
    while True:
        try:
            GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.setup(LED_SUCCESS, GPIO.OUT)
            GPIO.setup(LED_ERROR, GPIO.OUT)
            GPIO.setup(LED_ERROR1, GPIO.OUT)
            GPIO.add_event_detect(BUTTON_PIN_NEXT, GPIO.BOTH, callback=button_next_edge, bouncetime=40)
            GPIO.add_event_detect(BUTTON_PIN_EXECUTE, GPIO.BOTH, callback=button_execute_edge, bouncetime=40)
            return
        except RuntimeError:
            # sysfs GPIO ownership may still be changing during early udev.
            for pin in (BUTTON_PIN_NEXT, BUTTON_PIN_EXECUTE):
                try:
                    GPIO.remove_event_detect(pin)
                except RuntimeError:
                    pass
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


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


def git_has_remote_updates_light(timeout=10.0) -> bool:
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

    return False


def git_poll_thread():
    prev = None
    while not st.stop_threads:
        try:
            if not st.cached_wifi_connected:
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
                ok = git_has_remote_updates_light(timeout=10.0)
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


def request_wifi_setup():
    with st.wifi_action_lock:
        st.wifi_action_requested = True


def wifi_cancelled():
    return st.stop_threads or st.wifi_cancel_requested


def portal_loop_until_connected_or_cancel():
    portal_set_state_safe(running=True, done=False, last_ok="", last_error="", connecting=False)
    portal_module().start_ap()
    portal_module().run_portal(block=False)
    deadline = time.monotonic() + 600
    while not st.stop_threads and time.monotonic() < deadline:
        if wifi_cancelled():
            return "cancel"
        req = portal_module().pop_request()
        if not req:
            time.sleep(0.1)
            continue
        portal_set_state_safe(connecting=True, last_error="", connect_stage="무선 연결 중…")
        wifi_stage_set(30, "WiFi 연결 중", req["ssid"])
        try:
            portal_module().connect_network(req["ssid"], req["psk"], cancel=wifi_cancelled, hidden=req.get("hidden", False))
            portal_set_state_safe(done=True, running=False, last_ok="연결 완료", last_error="")
            return True
        except Exception as exc:
            if wifi_cancelled():
                return "cancel"
            portal_set_state_safe(last_error=str(exc), connect_stage="설정 AP로 다시 접속하세요.")
            wifi_stage_clear()
            portal_module().start_ap()
        finally:
            portal_set_state_safe(connecting=False)
            req.clear()  # Do not retain the password in pending UI state.
    return False


def wifi_worker_thread():
    try:
        if wifi_backend.ap_active():
            with st.wifi_action_lock:
                st.wifi_action_running = True
            wifi_stage_set(40, "기존 WiFi 복원", "설정 모드 복구 중")
            wifi_backend.restore_connection(cancel=lambda: st.stop_threads)
    except Exception:
        logging.exception("Stale setup AP recovery failed")
    finally:
        wifi_stage_clear()
        with st.wifi_action_lock:
            st.wifi_action_running = False
    while not st.stop_threads:
        with st.wifi_action_lock:
            do = st.wifi_action_requested and not st.wifi_action_running
            if do:
                st.wifi_action_requested = False
                st.wifi_action_running = True
        if not do:
            time.sleep(0.1)
            continue
        result = False
        st.wifi_cancel_requested = False
        wifi_stage_set(10, "WiFi 설정 준비", "잠시만 기다려주세요")
        try:
            result = portal_loop_until_connected_or_cancel()
        except Exception:
            logging.exception("Wi-Fi setup failed")
            portal_set_state_safe(last_error="Wi-Fi 설정 오류", connect_stage="")
        finally:
            # Includes cancellation, ten-minute timeout, server bind/AP failures.
            # None may leave wlan0 in AP mode or unmanaged after the worker exits.
            try:
                portal_module().stop_portal()
                if result is not True:
                    wifi_stage_set(60, "기존 WiFi 복원", "잠시만 기다려주세요")
                    restored = portal_module().restore_connection(cancel=lambda: st.stop_threads)
                    text_stage("재연결 완료" if restored else "재연결 실패")
                else:
                    text_stage("WiFi 연결 완료")
            except Exception:
                logging.exception("Wi-Fi recovery failed")
                text_stage("WiFi 복원 실패", "설정을 다시 시도하세요", font_size=12)
            finally:
                portal_set_state_safe(running=False, connecting=False, requested=None)
                wifi_stage_clear()
                time.sleep(0.8)
                clear_ui_override()
                with st.wifi_action_lock:
                    st.wifi_action_running = False
                if is_root_menu_view():
                    refresh_root_menu(reset_index=False)
                st.need_update = True
        time.sleep(0.1)


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

        if now - st.last_oled_update_time >= (0.10 if wifi_running or st.is_executing else 0.20):
            with oled_refresh_lock:
                update_oled_display()
                st.last_oled_update_time = now
                st.need_update = False

        time.sleep(0.02)


def net_poll_thread():
    while not st.stop_threads:
        try:
            link = wifi_link()
            st.cached_ip = link["ip"]
            st.cached_wifi_connected = link["connected"]
            st.cached_wifi_level = link["level"]
            online_now = internet_available() if link["connected"] else False
            st.cached_online = online_now
            if st.last_menu_online is None or online_now != st.last_menu_online:
                st.last_menu_online = online_now
                if is_root_menu_view():
                    refresh_root_menu(reset_index=False)
            st.need_update = True
        except Exception:
            logging.exception("Network status polling failed")
        time.sleep(3.0)


def git_pull():
    shell_script_path = "/home/user/stm32/git-pull.sh"
    text_stage("시스템", "업데이트 중", pos=(20, 10))

    try:
        result = run_command([shell_script_path], timeout=120)

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
        progress_stage(32, "업데이트 진행 중", "잠금 해제 생략")
        time.sleep(0.10)
        return True

    progress_stage(32, "업데이트 진행 중", "잠금 해제 중")

    ok = run_openocd_ok(
        ["init", "reset halt", "stm32f1x unlock 0", "reset run", "shutdown"],
        timeout=STM32_UNLOCK_TIMEOUT_SEC,
    )
    mark_recent_unlock(ok)

    if ok:
        progress_stage(40, "업데이트 진행 중", "잠금 해제 완료")
        time.sleep(0.15)
        return True

    progress_stage(0, "업데이트 실패", "잠금 해제 실패")
    time.sleep(0.35)
    return False


def restart_script():
    progress_stage(25, "재시작 중", "")

    def restart():
        time.sleep(1)
        st.stop_threads = True
        GPIO.cleanup()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=restart, daemon=True).start()


def lock_memory_procedure():
    if not is_memory_lock_enabled():
        text_stage("메모리 잠금", "건너뜀", pos=(18, 18))
        time.sleep(0.30)
        clear_ui_override()
        st.need_update = True
        return

    progress_stage(88, "업데이트 진행 중", "마무리 중")
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
            time.sleep(0.45)
    except Exception:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        progress_stage(0, "오류 발생", "")
        time.sleep(0.45)
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
    try:
        _execute_command(command_index)
    except Exception:
        logging.exception("Device command failed")
        text_stage("작업 실패", "다시 시도하세요", font_size=12)
        time.sleep(0.8)
        clear_ui_override()
    finally:
        st.is_executing = False
        st.is_command_executing = False
        st.need_update = True


def _execute_command(command_index):
    if not st.command_types or command_index < 0 or command_index >= len(st.command_types):
        return

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
            st.scan_generation += 1
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
            time.sleep(1.0)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
            clear_ui_override()
            st.need_update = True
            st.is_executing = False
            st.is_command_executing = False
            return

        progress_stage(10, "실행 중", "")
        try:
            result = run_command([sys.executable, OUT_SCRIPT_PATH], timeout=120)
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

    progress_stage(2, "업데이트 진행 중", "장치 확인 중")
    progress_stage(8, "업데이트 진행 중", "용량 확인 중")
    dev_id, flash_kb = detect_stm32_flash_kb_with_unlock(timeout=STM32_DETECT_TIMEOUT_SEC)

    progress_stage(18, "업데이트 진행 중", "파일 선택 중")
    resolved_path, chosen_kind = resolve_target_bin_by_gas(selected_path, flash_kb)

    progress_stage(30, "업데이트 진행 중", "잠금 해제 준비")
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
    flash_result = {}
    def program_target():
        try:
            with st.openocd_lock:
                flash_result["code"] = run_command(openocd_cmd, timeout=90, capture=False).returncode
        except Exception:
            logging.exception("OpenOCD programming failed")
            flash_result["code"] = 1
    flash_thread = threading.Thread(target=program_target, daemon=True)
    flash_thread.start()
    start_time = time.monotonic()
    while flash_thread.is_alive():
        elapsed = time.monotonic() - start_time
        current_progress = min(45 + elapsed * 35 / PROGRAM_PROGRESS_MAX_DURATION_SEC, 80)
        set_ui_progress(current_progress, progress_msg, pos=(6, 0), font_size=13)
        st.need_update = True
        time.sleep(PROGRAM_PROGRESS_POLL_SEC)
    result = flash_result.get("code", 1)
    if result == 0:
        set_ui_progress(82, f"업데이트 진행 중\n{info_line}", pos=(6, 0), font_size=13)
        st.need_update = True
        force_oled_refresh()
        st.auto_flash_cooldown_until = time.time() + 4.0
        st.auto_flash_done_connection = True
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
        st.auto_flash_done_connection = True
        progress_stage(0, "업데이트 실패", "")
        time.sleep(0.6)

    GPIO.output(LED_SUCCESS, False)
    GPIO.output(LED_ERROR, False)
    GPIO.output(LED_ERROR1, False)
    clear_ui_override()
    st.need_update = True
    st.is_executing = False
    st.is_command_executing = False


def stm32_poll_thread():
    while not st.stop_threads:
        with st.wifi_action_lock:
            wifi_running = st.wifi_action_running
        menu = st.current_menu or {}
        idx = st.current_command_index
        types = menu.get("types", [])
        extras = menu.get("extras", [])
        path = extras[idx] if 0 <= idx < len(extras) else None
        should_probe = (0 <= idx < len(types) and types[idx] == "bin" and path
                        and not wifi_running and not st.is_executing and not st.is_command_executing)
        if should_probe:
            check_stm32_connection()
            with st.stm32_state_lock:
                st.stm32_probe_path = path
                st.stm32_probe_at = time.monotonic()
            time.sleep(STM32_POLL_INTERVAL_SEC)
        else:
            if not st.is_executing and not st.is_command_executing:
                with st.stm32_state_lock:
                    st.connection_success = False
                    st.stm32_probe_path = None
            time.sleep(0.1)


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

        # 과거 코드 스타일: 업데이트 중에는 연결 판정 무시
        if (not st.is_executing) and (not st.is_command_executing):
            now_wall = time.time()
            if now_wall - st.last_stm32_check_time >= STM32_POLL_INTERVAL_SEC:
                st.last_stm32_check_time = now_wall

                prev_ok = st.last_stm32_poll_ok
                with st.stm32_state_lock:
                    path = st.menu_extras[st.current_command_index] if st.menu_extras else None
                    cur_ok = (st.connection_success and st.stm32_probe_path == path
                              and time.monotonic() - st.stm32_probe_at < 3.0)
                st.last_stm32_poll_ok = cur_ok

                if not cur_ok:
                    if st.stm32_disconnected_since == 0.0:
                        st.stm32_disconnected_since = now_wall
                    elif (now_wall - st.stm32_disconnected_since) >= 2.0:
                        st.physical_disconnect_armed = True
                else:
                    # 실제로 2초 이상 분리되었다가 다시 연결된 경우만 재허용
                    if (not prev_ok) and st.physical_disconnect_armed:
                        st.auto_flash_done_connection = False
                        st.physical_disconnect_armed = False

                    # 계속 연결 유지 중이면 아무 것도 하지 않음
                    st.stm32_disconnected_since = 0.0

                with st.stm32_state_lock:
                    cs = cur_ok

                if st.commands:
                    if (
                        cs
                        and (not st.auto_flash_done_connection)
                        and (time.time() >= getattr(st, "auto_flash_cooldown_until", 0.0))
                        and st.command_types[st.current_command_index] == "bin"
                    ):
                        st.auto_flash_done_connection = True
                        st.stm32_disconnected_since = 0.0
                        st.physical_disconnect_armed = False
                        st.auto_flash_cooldown_until = time.time() + 4.0
                        progress_stage(1, "업데이트 진행 중", "장치 확인 중")
                        execute_command(st.current_command_index)

        time.sleep(0.01)


def main():
    publish(70, "PREPARING")
    initialize_gpio()
    ensure_menu_config_csv()
    init_ina219()
    refresh_root_menu(reset_index=True)
    publish(85, "ALMOST READY")
    initialize_display()
    force_oled_refresh()
    if not display_ready():
        raise RuntimeError("First OLED menu frame failed")
    menu_ready()

    probe_thread = threading.Thread(target=stm32_poll_thread, daemon=True)
    probe_thread.start()

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


if __name__ == "__main__":
    main()
