"""Early boot OLED service. It never waits for network access or image libraries."""
import fcntl
import os
import time

from boot_display import SH1107, render


def main():
    runtime = os.environ.get("STM32_BOOT_RUNTIME", "/run/stm32-boot")
    lock = os.open(os.path.join(runtime, "display.lock"), os.O_RDWR | os.O_CREAT, 0o664)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # The running application owns the display.
        started = time.monotonic()
        display, tick = None, 0
        read_state = None
        try:
            while True:
                state = read_state() if read_state else {}
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
                    first_frame_at = time.monotonic()
                    print("OLED first boot frame at %.3fs since kernel start" % first_frame_at, flush=True)
                    # Prioritize the first visible frame, then yield to boot/app work.
                    os.nice(10)
                    # JSON, pathlib, tempfile and socket imports can wait until
                    # the user has a visible screen on this single-core Pi.
                    from boot_status import read_state, record_first_frame
                    record_first_frame(first_frame_at)
                tick += 1
                time.sleep(0.5)
        finally:
            if display:
                display.close()
    finally:
        os.close(lock)


if __name__ == "__main__":
    main()
