"""Early boot OLED service. It never waits for network access or image libraries."""
import fcntl
import os
import time

from boot_display import SH1107, render
from boot_status import RUNTIME, read_state, record_first_frame


def main():
    lock = os.open(RUNTIME / "display.lock", os.O_RDWR | os.O_CREAT, 0o664)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # The running application owns the display.
        started = time.monotonic()
        display, tick = None, 0
        try:
            while True:
                state = read_state()
                if state.get("handoff"):
                    return
                if display is None:
                    try:
                        display = SH1107()
                    except OSError:
                        if time.monotonic() - started > 30:
                            raise
                        time.sleep(0.1)
                        continue
                elapsed = time.monotonic() - started
                stage = state.get("stage", "STARTING")
                progress = state.get("progress", 10)
                if elapsed > 90:
                    stage = "PLEASE WAIT"
                display.display(render(progress, stage, elapsed, tick))
                if tick == 0:
                    record_first_frame()
                    print("OLED first boot frame at %.3fs since kernel start" % time.monotonic(), flush=True)
                    # Prioritize the first visible frame, then yield to boot/app work.
                    os.nice(10)
                tick += 1
                time.sleep(0.5)
        finally:
            if display:
                display.close()
    finally:
        os.close(lock)


if __name__ == "__main__":
    main()
