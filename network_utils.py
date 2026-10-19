"""Network helpers shared by the OLED, scanner and setup portal."""
import ipaddress
import re
import socket
import threading
import time

from process_utils import run_command


def split_nmcli(line):
    """Decode nmcli terse fields without splitting escaped colons/backslashes."""
    fields, chars, escaped = [], [], False
    for char in line:
        if escaped:
            chars.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(chars))
            chars = []
        else:
            chars.append(char)
    if escaped:
        chars.append("\\")
    fields.append("".join(chars))
    return fields


def interface_ipv4(iface):
    # Linux ioctl avoids starting a shell/awk/ip pipeline every poll.
    try:
        import fcntl
        import struct
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            data = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack('256s', iface.encode()[:15]))
        ip = socket.inet_ntoa(data[20:24])
        address = ipaddress.IPv4Address(ip)
        return ip if not (address.is_loopback or address.is_link_local or address.is_unspecified) else "0.0.0.0"
    except (ImportError, OSError, ValueError):
        return "0.0.0.0"


def wifi_link():
    result = run_command(["iw", "dev", "wlan0", "link"], timeout=2)
    text = result.stdout
    connected = result.returncode == 0 and "Connected to " in text
    match = re.search(r"signal:\s*(-?\d+)\s*dBm", text)
    dbm = int(match.group(1)) if match else -100
    level = sum(dbm >= threshold for threshold in (-85, -75, -65, -55)) if connected else 0
    ip = interface_ipv4("wlan0")
    return {"connected": connected and ip != "0.0.0.0", "ip": ip, "level": level}


def source_ipv4_for(peer):
    # Use the route to the actual target, not an unrelated default route.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((peer, 502))
            return sock.getsockname()[0]
    except OSError:
        return "0.0.0.0"


internet_cache = {"at": 0.0, "ok": False}
_internet_lock = threading.Lock()


def internet_available(timeout=1.5):
    with _internet_lock:
        now = time.monotonic()
        if internet_cache["at"] and now - internet_cache["at"] < 20:
            return internet_cache["ok"]
        ok = False
        for target in ("1.1.1.1", "8.8.8.8"):
            result = run_command(["ping", "-I", "wlan0", "-c", "1", "-W", "1", target], timeout=timeout)
            if result.returncode == 0:
                ok = True
                break
        internet_cache.update(at=now, ok=ok)
        return ok
