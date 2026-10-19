import csv
import os
import re
import socket
import subprocess

from app_config import MENU_CONFIG_CSV
from process_utils import run_command
from network_utils import interface_ipv4


def logi(msg: str):
    try:
        print("[AUTOSEL] " + str(msg), flush=True)
    except Exception:
        pass


def run_quiet(cmd, timeout=3.0, shell=False):
    try:
        return run_command(cmd, timeout=timeout, shell=shell, capture=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_capture(cmd, timeout=4.0, shell=False):
    try:
        result = run_command(cmd, timeout=timeout, shell=shell)
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 999, "", type(exc).__name__


def ensure_menu_config_csv():
    try:
        cfg_dir = os.path.dirname(MENU_CONFIG_CSV)
        if cfg_dir:
            os.makedirs(cfg_dir, exist_ok=True)
        if not os.path.isfile(MENU_CONFIG_CSV):
            with open(MENU_CONFIG_CSV, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["key", "value"])
                writer.writerow(["fw_extract_enabled", "0"])
    except Exception as e:
        logi(f"menu_config.csv 생성 실패: {e}")


def get_csv_flag(key: str, default: int = 0) -> int:
    try:
        ensure_menu_config_csv()
        with open(MENU_CONFIG_CSV, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                k = str(row.get("key", "")).strip()
                v = str(row.get("value", "")).strip()
                if k == key:
                    return 1 if v == "1" else 0
    except Exception:
        pass
    return int(default)


def is_fw_extract_mode() -> bool:
    return get_csv_flag("fw_extract_enabled", 0) == 1


def is_memory_lock_enabled() -> bool:
    return not is_fw_extract_mode()


def iface_exists(name: str) -> bool:
    try:
        return os.path.isdir(f"/sys/class/net/{name}")
    except Exception:
        return False


def has_real_internet(timeout=1.5):
    from network_utils import internet_available
    return internet_available(timeout=timeout)


def ip_from_ip_cmd(ifname: str) -> str:
    return interface_ipv4(ifname)


def get_ip_address():
    for iface in ("wlan0", "eth0"):
        ip = interface_ipv4(iface)
        if ip != "0.0.0.0":
            return ip
    return "0.0.0.0"


def parse_order_and_name(name: str, is_dir: bool):
    raw = name if is_dir else os.path.splitext(name)[0]
    m = re.match(r"^(\d+)\.(.*)$", raw)
    if m:
        order = int(m.group(1))
        display = m.group(2).lstrip()
    else:
        order = 9999
        display = raw
    return order, display


def strip_order_prefix(name: str) -> str:
    s = (name or "").strip()
    m = re.match(r"^\d+\.(.*)$", s)
    if m:
        s = (m.group(1) or "").strip()
    return s


def canon_name(name: str) -> str:
    return strip_order_prefix(name).strip().lower()
