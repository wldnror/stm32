import os
import re
import shutil
import socket
import ipaddress
import uuid
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from network_utils import source_ipv4_for
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
    try:
        address = ipaddress.IPv4Address(ip)
        if address.is_unspecified or address.is_loopback or address.is_link_local:
            return None
        return str(address).rsplit(".", 1)[0] + "."
    except ipaddress.AddressValueError:
        return None


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


def publish_tftp_firmware(source, destination):
    temporary = destination + ".tmp-" + uuid.uuid4().hex
    try:
        try:
            shutil.copyfile(source, temporary)
            os.chmod(temporary, 0o644)
        except PermissionError:
            if not run_quiet(["sudo", "-n", "cp", source, temporary], timeout=8):
                raise OSError("TFTP firmware copy failed")
            if not run_quiet(["sudo", "-n", "chmod", "0644", temporary], timeout=4):
                raise OSError("TFTP firmware permissions failed")
        try:
            os.replace(temporary, destination)
        except PermissionError:
            if not run_quiet(["sudo", "-n", "mv", "-f", temporary, destination], timeout=4):
                raise OSError("TFTP firmware publication failed")
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except PermissionError:
            run_quiet(["sudo", "-n", "rm", "-f", temporary], timeout=4)


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
        publish_tftp_firmware(fw_src, dst_path)
    except Exception as e:
        GPIO.output(LED_ERROR, True)
        GPIO.output(LED_ERROR1, True)
        set_ui_text("복사 실패", str(e)[:16], pos=(2, 18), font_size=12)
        time.sleep(2.0)
        GPIO.output(LED_ERROR, False)
        GPIO.output(LED_ERROR1, False)
        clear_ui_override()
        return

    tftp_ip = source_ipv4_for(ip)
    if tftp_ip == "0.0.0.0":
        set_ui_text("네트워크 오류", "경로를 확인하세요", font_size=12)
        time.sleep(1)
        clear_ui_override()
        return
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

        w1, w2 = encode_ip_to_words(tftp_ip)
        try:
            address_result = client.write_registers(address=addr_ip1, values=[w1, w2], slave=1)
            address_ok = not is_modbus_error(address_result)
        except Exception:
            address_ok = False
        if not address_ok:
            set_ui_text("서버 IP 전송 실패", "업데이트 중단", pos=(1, 18), font_size=12)
            time.sleep(1.5)
            clear_ui_override()
            return

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
    # Eight bounded workers turn a ~56-second serial /24 probe into a few seconds.
    # A generation prevents a cancelled scan from overwriting a newer scan's menu.
    with ThreadPoolExecutor(max_workers=8) as pool:
        while not st.stop_threads:
            time.sleep(0.1)
            with st.scan_lock:
                active, generation = st.scan_active, st.scan_generation
                prefix = st.scan_base_prefix
            if not active:
                continue
            def cancelled(generation=generation):
                return st.stop_threads or not st.scan_active or generation != st.scan_generation
            found, infos = [], {}
            own_ip = get_ip_address()
            def probe(ip, is_cancelled=cancelled):
                if is_cancelled() or not quick_modbus_probe(ip, timeout=0.22):
                    return ip, None, False
                if is_cancelled():
                    return ip, None, False
                return ip, read_device_info_fast(ip), True
            futures = []
            if prefix:
                futures = [pool.submit(probe, prefix + str(host)) for host in range(1, 255)
                           if prefix + str(host) != own_ip]
            last_push = 0.0
            for future in as_completed(futures):
                if cancelled():
                    for pending in futures:
                        pending.cancel()
                    break
                ip, info, ok = future.result()
                if ok:
                    found.append(ip)
                    if info:
                        infos[ip] = info
                now = time.monotonic()
                if now - last_push >= 0.35:
                    last_push = now
                    with st.scan_lock:
                        if cancelled():
                            break
                        st.scan_ips = sorted(found, key=lambda value: tuple(map(int, value.split("."))))
                        st.scan_infos = dict(infos)
                        st.scan_menu_dirty = True
                    st.need_update = True
            with st.scan_lock:
                if cancelled():
                    continue
                st.scan_ips = sorted(found, key=lambda value: tuple(map(int, value.split("."))))
                st.scan_infos = dict(infos)
                st.scan_selected_idx = min(st.scan_selected_idx, max(0, len(found) - 1))
                st.scan_done, st.scan_active = True, False
                st.scan_menu_dirty = True
            st.need_update = True
