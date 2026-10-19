"""Publish startup milestones before importing the normal hardware application."""
import os
from pathlib import Path

from boot_status import publish


def main():
    os.chdir(Path(__file__).resolve().parent)
    publish(30, "LOADING")
    try:
        import serve
        publish(65, "CHECKING")
        serve.main()
    except BaseException:
        publish(20, "RETRYING")
        raise


if __name__ == "__main__":
    main()
