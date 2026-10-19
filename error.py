"""Compatibility entry point; recovery uses the same tested app and Wi-Fi code."""
import os
from pathlib import Path
import sys

if __name__ == "__main__":
    entry = Path(__file__).resolve().with_name("main.py")
    os.execv(sys.executable, [sys.executable, str(entry)])
