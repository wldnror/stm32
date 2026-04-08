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
    MODBUS_PORT, REG_STATUS_4XXXX, REG_GAS_INT_4XXXX, REG_FAULT_4XXXX,
    TFTP_REMOTE_DIR, TFTP_SERVER_ROOT, TFTP_DEVICE_SUBDIR, TFTP_DEVICE_FILENAME,
    LED_SUCCESS, LED_ERROR, LED_ERROR1,
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

def _quick_modbus_probe(ip: str, timeout=0.22) -> bool:
    try:
        s = socket.create_connection((ip, MODBUS_PORT), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False

def _modbus_connect_with_retries(ip: str, port=502, timeout=1.2, retries=3, delay=0.25) -> Optional[ModbusTcpClient]:
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

def _is_modbus_error(resp) -> bool:
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

def _u16(v):
    try:
        return int(v) & 0xFFFF
    except Exception:
        return 0

def _read_gas_and_alarm_flags_with_client(c: ModbusTcpClient):
    lo = 40001
    hi = 40008
    start = reg_addr(lo)
    count = (hi - lo) + 1
    r = c.read_holding_registers(address=start, count=count, slave=1)
    if _is_modbus_error(r):
        return None, None, "read"
    regs = getattr(r, "registers", None) or []
    if len(regs) < count:
        return None, None, "short"

    def R(addr_4xxxx: int) -> int:
        return _u16(regs[addr_4xxxx - lo])

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

def _format_scan_summary(gas, flags, err=""):
    if err:
        return f"ERR:{err}"
    if gas is None:
        return "읽는중..."
    parts = [f"가스:{int(gas) if abs(gas-int(gas)) < 1e-6 else gas}"]
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

def _read_device_info_fast(ip: str) -> Optional[str]:
    c = _modbus_connect_with_retries(ip, port=MODBUS_PORT, timeout=0.65, retries=1, delay=0.0)
    if c is None:
        return None
    try:
        gas, flags, err = _read_gas_and_alarm_flags_with_client(c)
        return _format_scan_summary(gas, flags, err)
    except Exception:
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass

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

    def _score(path: str):
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

    bins.sort(key=_score, reverse=True)
    return bins[0]

def _ensure_tftp_dir(path: str) -> bool:
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

    if not _quick_modbus_probe(ip, timeout=0.35):
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
    if not _ensure_tftp_dir(device_dir):
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

    client = _modbus_connect_with_retries(ip, port=502, timeout=2.2, retries=4, delay=0.35)
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
        addr_ip1 = reg_addr(40088)
        addr_ctrl = reg_addr(40091)

        try:
            w1, w2 = encode_ip_to_words(tftp_ip)
            client.write_registers(address=addr_ip1, values=[w1, w2], slave=1)
        except Exception:
            pass

        ok_final = False
        try:
            r = client.write_register(address=addr_ctrl, value=1, slave=1)
            ok_final = not _is_modbus_error(r)
        except Exception:
            ok_final = False

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
