"""Bounded subprocesses, including children of timed-out sudo/shell commands."""
import os
import signal
import subprocess
import time


def _kill_group(proc, privileged=False):
    # sudo may run root-owned grandchildren that the caller cannot signal.
    if privileged and os.geteuid() != 0:
        try:
            subprocess.run(["sudo", "-n", "/bin/kill", "-KILL", "--", "-" + str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_command(cmd, timeout=4.0, shell=False, cancel=None, capture=True):
    """Return a CompletedProcess; 124 means timeout, 125 means cancellation.

    Every command owns a fresh process group. Timeout cleanup kills and reaps
    that group, rather than leaving sudo/git/OpenOCD children holding pipes.
    """
    proc = subprocess.Popen(cmd, shell=shell, start_new_session=True,
                            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
                            text=True)
    deadline = time.monotonic() + timeout
    privileged = shell or (not isinstance(cmd, str) and os.path.basename(cmd[0]) == "sudo")
    try:
        while True:
            cancelled = cancel is not None and cancel()
            remaining = deadline - time.monotonic()
            if cancelled or remaining <= 0:
                _kill_group(proc, privileged)
                try:
                    out, err = proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    # A detached child must never keep the UI waiting on a pipe.
                    proc.stdout and proc.stdout.close()
                    proc.stderr and proc.stderr.close()
                    proc.wait(timeout=2)
                    out, err = "", ""
                return subprocess.CompletedProcess(cmd, 125 if cancelled else 124,
                                                   out or "", "cancelled" if cancelled else "command timed out")
            try:
                out, err = proc.communicate(timeout=min(0.2, remaining))
                return subprocess.CompletedProcess(cmd, proc.returncode, out or "", err or "")
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        _kill_group(proc, privileged)
        proc.wait(timeout=3)
        raise
