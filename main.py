"""Start immediately; dependency installation belongs to provisioning, not boot."""
import os
import sys

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    os.execv(sys.executable, [sys.executable, "-u", "serve.py"])
