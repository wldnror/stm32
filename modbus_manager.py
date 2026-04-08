import os
import re
import shutil
import socket
import time
from typing import Optional

import RPi.GPIO as GPIO
from pymodbus.client import ModbusTcpClient
from pymodbus.pdu import ExceptionResponse

import app_state as st
from app_config import (
    MODBUS_PORT,
    REG_STATUS_4XXXX,
    REG_GAS_INT_4XXXX,
    REG_FAULT_4XXXX,
    TFTP_REMOTE_DIR,
    TFTP_SERVER_ROOT,
    TFTP_DEVICE_SUBDIR,
    TFTP_DEVICE_FILENAME,
    LED_SUCCESS,
    LED_ERROR,
    LED_ERROR1,
)
from app_utils import get_ip_address, run_quiet
from display_manager import set_ui_progress, set_ui_text, clear_ui_override


def reg_addr(addr_4xxxx: int) -> int:
    return int(addr_4xxxx) - 40001


def encode_ip_to_words(ip: str):
    a, b, c, d = map(int, (ip or "").strip().split("."))
    for x in (a, b, c, d):
        if x < 0 or x > 255:
            raise ValueError("bad ip")
    return ((a << 8) | b, (c << 8) | d)


def quick_modbus_probe(ip: str, timeout=0.22) -> bool:
    try:
        s = socket.create_connection((ip, MODBUS_PORT), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def modbus_connect_with_retries(ip: str, port=502, timeout=1.2, retries=3, delay=0.25) -> Optional[ModbusTcpClient]:
    c = ModbusTcpClient(ip, port=port, timeout=timeout)
    try:
        for _ in range(max(1, int(retries))):
            try:
                if c.connect():
                    return c
            except Exception:
                pass
            time.sleep(delay)
        try:
            c.close()
        except Exception:
            pass
        return None
    except Exception:
        try:
            c.close()
        except Exception:
            pass
        return None


def is_modbus_error(resp) -> bool:
    try:
        if resp is None:
            return True
        if isinstance(resp, ExceptionResponse):
            return True
        if hasattr(resp, "isError") and callable(resp.isError) and resp.isError():
            return True
    except Exception:
        return True
    return False


def treat_as_ok_modbus_write_exception(e: Exception) -> bool:
    msg = str(e or "")
    ok_like = [
        "unpack requires a buffer of 4 bytes",
        "Unable to decode response",
        "No response received",
        "Invalid Message",
        "Socket is closed",
    ]
    return any(k in msg for k in ok_like)


def u16(v):
    try:
        return int(v) & 0xFFFF
    except Exception:
        return 0


def try_read_some_modbus_info(client: ModbusTcpClient) -> Optional[str]:
    try:
        r = client.read_holding_registers(address=reg_addr(40001), count=4, slave=1)
        if is_modbus_error(r):
            return None
        vals = getattr(r, "registers", None)
        if not vals:
            return None
        return "R40001:" + ",".join(str(x) for x in vals[:4])
    except Exception as e:
        return ("READ ERR:" + str(e))[:18]


def read_gas_and_alarm_flags_with_client(c: ModbusTcpClient):
    lo = 40001
    hi = 40008
    start = reg_addr(lo)
    count = (hi - lo) + 1
    r = c.read_holding_registers(address=start, count=count, slave=1)
    if is_modbus_error(r):
        return None, None, "read"
    regs = getattr(r, "registers", None) or []
    if len(regs) < count:
        return None, None, "short"

    def R(addr_4xxxx: int) -> int:
        return u16(regs[addr_4xxxx - lo])

    status_40001 = R(REG_STATUS_4XXXX)
    gas_int = R(REG_GAS_INT_4XXXX)
    fault_40008 = R(REG_FAULT_4XXXX)

    gas = float(gas_int)

    a1 = bool(status_40001 & (1 << 6))
    a2 = bool(status_40001 & (1 << 7))
    pwr_fault = bool(fault_40008 & (1 << 2))
    fut = (fault_40008 != 0)

    flags = {"PWR": pwr_fault, "A1": a1, "A2": a2, "FUT": fut}
    return gas, flags, ""


def read_gas_and_alarm_flags(ip: str):
    c = modbus_connect_with_retries(ip, port=MODBUS_PORT, timeout=0.8, retries=1, delay=0.0)
    if c is None:
        return None, None, "connect"
    try:
        return read_gas_and_alarm_flags_with_client(c)
    except Exception as e:
        return None, None, str(e)[:18]
    finally:
        try:
            c.close()
        except Exception:
            pass


def fmt_gas(v):
    if v is None:
        return "--"
    try:
        v = float(v)
        if abs(v - int(v)) < 1e-6:
            return str(int(v))
        if abs(v) < 100:
            return f"{v:.1f}"
        return f"{v:.0f}"
    except Exception:
        return "--"


def format_scan_summary(gas, flags, err=""):
    if err:
        return f"ERR:{err}"
    if gas is None:
        return "읽는중..."
    parts = [f"가스:{fmt_gas(gas)}"]
    if flags:
        if flags.get("PWR"):
            parts.append("PWR")
        if flags.get("A1"):
            parts.append("A1")
        if flags.get("A2"):
            parts.append("A2")
        if flags.get("FUT") and (not (flags.get("PWR") or flags.get("A1") or flags.get("A2"))):
            parts.append("FUT")
    return " ".join(parts)


def read_device_info_fast(ip: str) -> Optional[str]:
    c = modbus_connect_with_retries(ip, port=MODBUS_PORT, timeout=0.65, retries=1, delay=0.0)
    if c is None:
        return None
    try:
        gas, flags, err = read_gas_and_alarm_flags_with_client(c)
        return format_scan_summary(gas, flags, err)
    except Exception:
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass


def scan_compute_prefix(ip: str):
    parts = (ip or "").split(".")
    if len(parts) != 4:
        return None
    return ".".join(parts[:3]) + "."


def pick_remote_fw_file_for_device(ip: str) -> str:
    if not os.path.isdir(TFTP_REMOTE_DIR):
        return ""
    p = os.path.join(TFTP_REMOTE_DIR, "default.bin")
    if os.path.isfile(p):
        return p
    p2 = os.path.join(TFTP_REMOTE_DIR, "asgd3200.bin")
    if os.path.isfile(p2):
        return p2

    bins = []
    try:
        for fn in os.listdir(TFTP_REMOTE_DIR):
            if fn.lower().endswith(".bin"):
                full = os.path.join(TFTP_REMOTE_DIR, fn)
                if os.path.isfile(full):
                    bins.append(full)
    except Exception:
        return ""

    if not bins:
        return ""

    def score(path: str):
        base = os.path.basename(path)
        stem = os.path.splitext(base)[0].strip()
        m = re.match(r"^(\d+)$", stem)
        mt = 0.0
        try:
            mt = os.path.getmtime(path)
        except Exception:
            mt = 0.0
        if m:
            try:
                n = int(m.group(1))
            except Exception:
                n = -1
            return (2, n, mt)
        return (1, -1, mt)

    bins.sort(key=score, reverse=True)
    return bins[0]


def ensure_tftp_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except PermissionError:
        ok = run_quiet(["sudo", "mkdir", "-p", path], timeout=6.0)
        if ok:
            run_quiet(["sudo", "chmod", "0777", path], timeout=4.0)
        return ok
    except Exception:
        return False


def tftp_upgrade_device(ip: str):
    ip = (ip or "").strip()
    if not ip:
        return

    set_ui_progress(5, "원격 업뎃\n준비...", pos=(18, 0), font_size=15)

    if not quick_modbus_probe(ip, timeout=0.35):
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("포트 응답X", ip, pos=(2, 18), font_size=12)
        time.sleep(1.6)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return

    fw_src = pick_remote_fw_file_for_device(ip)
    if not fw_src:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("FW 없음", "2.TFTP_REMOTE", pos=(4, 18), font_size=13)
        time.sleep(1.6)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return

    set_ui_progress(20, "FW 복사중", pos=(18, 10), font_size=15)

    device_dir = os.path.join(TFTP_SERVER_ROOT, TFTP_DEVICE_SUBDIR)
    if not ensure_tftp_dir(device_dir):
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("권한 오류", "/srv/tftp", pos=(6, 18), font_size=13)
        time.sleep(2.0)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return

    dst_path = os.path.join(device_dir, TFTP_DEVICE_FILENAME)
    try:
        try:
            if os.path.exists(dst_path):
                os.remove(dst_path)
        except PermissionError:
            run_quiet(["sudo", "rm", "-f", dst_path], timeout=4.0)
        except Exception:
            pass

        try:
            shutil.copyfile(fw_src, dst_path)
        except PermissionError:
            run_quiet(["sudo", "cp", "-f", fw_src, dst_path], timeout=6.0)
            run_quiet(["sudo", "chmod", "0644", dst_path], timeout=3.0)
        except Exception:
            shutil.copyfile(fw_src, dst_path)
    except Exception as e:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("복사 실패", str(e)[:16], pos=(2, 18), font_size=12)
        time.sleep(2.0)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return

    tftp_ip = get_ip_address()
    set_ui_progress(45, f"명령 전송\n{ip}", pos=(6, 0), font_size=13)

    client = modbus_connect_with_retries(ip, port=502, timeout=2.2, retries=4, delay=0.35)
    if client is None:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("연결 실패", ip, pos=(2, 18), font_size=12)
        time.sleep(1.6)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return

    try:
        try:
            info = try_read_some_modbus_info(client)
            if info:
                set_ui_text("MODBUS", info[:18], pos=(2, 18), font_size=12)
                time.sleep(0.9)
                clear_ui_override()
        except Exception:
            pass

        ok_final = False
        addr_ip1 = reg_addr(40088)
        addr_ctrl = reg_addr(40091)

        try:
            w1, w2 = encode_ip_to_words(tftp_ip)
            try:
                client.write_registers(address=addr_ip1, values=[w1, w2], slave=1)
            except Exception:
                pass
        except Exception:
            pass

        try:
            r = client.write_register(address=addr_ctrl, value=1, slave=1)
            ok_final = not is_modbus_error(r)
        except Exception as e:
            ok_final = treat_as_ok_modbus_write_exception(e)

        if not ok_final:
            GPIO.output(LED_ERROR, True)
            GPIO.output(LED_ERROR1, True)
            set_ui_text("명령 실패", "40091=1", pos=(10, 18), font_size=13)
            time.sleep(1.8)
            GPIO.output(LED_ERROR, False)
            GPIO.output(LED_ERROR1, False)
            clear_ui_override()
            return

        GPIO.output(LED_SUCCESS, True)
        set_ui_progress(100, "전송 완료\n업뎃 진행", pos=(10, 5), font_size=15)
        time.sleep(1.1)
        GPIO.output(LED_SUCCESS, False)
    finally:
        try:
            client.close()
        except Exception:
            pass

    clear_ui_override()


def modbus_detail_poll_thread():
    client = None
    client_ip = None
    backoff_until = 0.0

    while not st.stop_threads:
        time.sleep(0.2)

        with st.scan_detail_lock:
            active = st.scan_detail_active
            ip = (st.scan_detail_ip or "").strip()

        if (not active) or (not ip):
            if client:
                try:
                    client.close()
                except Exception:
                    pass
            client = None
            client_ip = None
            continue

        now = time.time()
        if now < backoff_until:
            continue

        if client and client_ip != ip:
            try:
                client.close()
            except Exception:
                pass
            client = None
            client_ip = None

        if client is None:
            c = ModbusTcpClient(ip, port=MODBUS_PORT, timeout=0.8)
            try:
                if not c.connect():
                    try:
                        c.close()
                    except Exception:
                        pass
                    st.scan_detail["err"] = "connect"
                    st.scan_detail["ts"] = time.time()
                    st.need_update = True
                    backoff_until = time.time() + 0.2
                    continue
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
                st.scan_detail["err"] = "connect"
                st.scan_detail["ts"] = time.time()
                st.need_update = True
                backoff_until = time.time() + 0.2
                continue
            client = c
            client_ip = ip

        try:
            gas, flags, err = read_gas_and_alarm_flags_with_client(client)
            st.scan_detail["gas"] = gas
            if flags:
                st.scan_detail["flags"] = flags
            st.scan_detail["err"] = err or ""
            st.scan_detail["ts"] = time.time()
            st.need_update = True

            if err in ("connect", "read", "short"):
                try:
                    client.close()
                except Exception:
                    pass
                client = None
                client_ip = None
                backoff_until = time.time() + 0.2

        except Exception as e:
            st.scan_detail["err"] = str(e)[:18]
            st.scan_detail["ts"] = time.time()
            st.need_update = True
            try:
                client.close()
            except Exception:
                pass
            client = None
            client_ip = None
            backoff_until = time.time() + 0.2


def modbus_scan_loop():
    while not st.stop_threads:
        time.sleep(0.08)

        with st.scan_lock:
            active = st.scan_active
            done = st.scan_done
            pref = st.scan_base_prefix

        if (not active) or done:
            continue

        now = time.time()
        if not pref:
            with st.scan_lock:
                st.scan_ips = []
                st.scan_infos.clear()
                st.scan_selected_idx = 0
                st.scan_selected_ip = None
                st.scan_done = True
                st.scan_active = False
                st.scan_menu_dirty = True
                st.scan_menu_dirty_ts = now
                st.scan_last_tick = now
            st.need_update = True
            continue

        found = []
        infos_local = {}
        last_push = 0.0

        for host in range(2, 255):
            if st.stop_threads:
                break

            ip = pref + str(host)
            if quick_modbus_probe(ip, timeout=0.22):
                found.append(ip)
                info = read_device_info_fast(ip)
                if info:
                    infos_local[ip] = info

            tnow = time.time()
            if tnow - last_push >= 0.35:
                last_push = tnow
                new_ips = sorted(found, key=lambda x: tuple(int(p) for p in x.split(".")))
                with st.scan_lock:
                    st.scan_ips = new_ips
                    for k, v in infos_local.items():
                        st.scan_infos[k] = v
                    if st.scan_selected_idx >= len(st.scan_ips):
                        st.scan_selected_idx = 0
                    st.scan_menu_dirty = True
                    st.scan_menu_dirty_ts = tnow
                    st.scan_last_tick = tnow
                st.need_update = True

        new_ips = sorted(found, key=lambda x: tuple(int(p) for p in x.split(".")))
        with st.scan_lock:
            st.scan_ips = new_ips
            st.scan_infos.clear()
            st.scan_infos.update(infos_local)
            if st.scan_selected_idx >= len(st.scan_ips):
                st.scan_selected_idx = 0
            st.scan_done = True
            st.scan_active = False
            st.scan_menu_dirty = True
            st.scan_menu_dirty_ts = time.time()
            st.scan_last_tick = time.time()

        st.need_update = True
