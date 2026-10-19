"""Publish startup milestones before importing the normal hardware application."""
import os
from pathlib import Path

from boot_status import RUNTIME, publish


def main():
    os.chdir(Path(__file__).resolve().parent)
    publish(30, "LOADING")
    try:
        import serve
        publish(65, "CHECKING")
        serve.main()
    except BaseException:
        import traceback
        try:
            (RUNTIME / "startup-error.log").write_text(traceback.format_exc())
        except OSError:
            pass
        publish(20, "RETRYING")
        raise


if __name__ == "__main__":
    main()
