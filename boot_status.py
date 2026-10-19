"""Small startup handshake; intentionally independent of Flask, PIL and GPIO."""
import fcntl
import json
import os
from pathlib import Path
import socket
import tempfile
import time

RUNTIME = Path(os.environ.get("STM32_BOOT_RUNTIME", "/run/stm32-boot"))
_display_lock = None


def notify(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    try:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), address)
    except OSError:
        pass


def publish(progress, stage, handoff=False):
    state = {"progress": max(0, min(100, int(progress))), "stage": stage,
             "handoff": handoff, "pid": os.getpid(), "at": time.monotonic()}
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(dir=RUNTIME, prefix=".state-")
        with os.fdopen(descriptor, "w") as stream:
            json.dump(state, stream)
        os.replace(temporary, RUNTIME / "state.json")
    except OSError:
        # A legacy installation without the early service must still start.
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    notify("STATUS=" + stage)
    return state


def read_state():
    try:
        state = json.loads((RUNTIME / "state.json").read_text())
        os.kill(int(state["pid"]), 0)
        return state
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def claim_display(timeout=4.0):
    """Ask the splash to leave, then take its lock before initializing luma."""
    global _display_lock
    if _display_lock is not None:
        return
    publish(95, "ALMOST READY", handoff=True)
    try:
        descriptor = os.open(RUNTIME / "display.lock", os.O_RDWR | os.O_CREAT, 0o664)
    except OSError:
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _display_lock = descriptor
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                raise RuntimeError("Startup display handoff timed out")
            time.sleep(0.05)


def menu_ready():
    publish(100, "READY", handoff=True)
    notify("READY=1\nSTATUS=Menu ready")
    print("STM32 menu ready at %.3fs since kernel start" % time.monotonic(), flush=True)
