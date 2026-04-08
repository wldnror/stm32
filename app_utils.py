import csv
import os
import re
import socket
import subprocess

from app_config import MENU_CONFIG_CSV


def logi(msg: str):
    try:
        print("[AUTOSEL] " + str(msg), flush=True)
    except Exception:
        pass


def run_quiet(cmd, timeout=3.0, shell=False):
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            shell=shell,
        )
        return True
    except Exception:
        return False


def run_capture(cmd, timeout=4.0, shell=False):
    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=shell,
        )
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return 999, "", str(e)


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
    try:
        targets = ["8.8.8.8", "1.1.1.1"]
        iface = "wlan0" if iface_exists("wlan0") else ("eth0" if iface_exists("eth0") else None)
        for t in targets:
            if iface:
                cmd = ["ping", "-I", iface, "-c", "1", "-W", "1", t]
            else:
                cmd = ["ping", "-c", "1", "-W", "1", t]
            r = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            if r.returncode == 0:
                return True
        return False
    except Exception:
        return False


def ip_from_ip_cmd(ifname: str) -> str:
    rc, out, _ = run_capture(
        ["bash", "-lc", f"ip -4 addr show {ifname} | awk '/inet /{{print $2}}' | head -n1"],
        timeout=0.9,
    )
    if rc != 0:
        return "0.0.0.0"
    v = (out or "").strip()
    if not v:
        return "0.0.0.0"
    ip = v.split("/")[0].strip()
    return ip if ip else "0.0.0.0"


def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and ip != "0.0.0.0" and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    try:
        for ifn in ["wlan0", "eth0"]:
            if iface_exists(ifn):
                ip2 = ip_from_ip_cmd(ifn)
                if ip2 != "0.0.0.0" and not ip2.startswith("127."):
                    return ip2
        rc, out, _ = run_capture(
            ["bash", "-lc", "ip -4 addr show | awk '/inet /{print $2}' | head -n1"],
            timeout=0.9,
        )
        if rc == 0:
            v = (out or "").strip()
            if v:
                ip3 = v.split("/")[0].strip()
                if ip3 and ip3 != "0.0.0.0" and not ip3.startswith("127."):
                    return ip3
    except Exception:
        pass

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
