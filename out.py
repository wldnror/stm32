"""Extract firmware on explicit request; keep transfer credentials off GitHub."""
from datetime import datetime
import ftplib
import json
import os
from pathlib import Path
import sys

from app_config import OPENOCD_IFACE, OPENOCD_TARGET
from process_utils import run_command


def upload_to_ftp(file_path, filename):
    config_path = Path.home() / ".config" / "stm32" / "ftp.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    password = os.environ.get("STM32_FTP_PASSWORD", config.get("password", ""))
    if not password:
        raise RuntimeError("FTP 전송 설정이 없습니다. 추출 파일은 장비에 보관했습니다.")
    with ftplib.FTP(timeout=15) as ftp:
        ftp.connect(config.get("server", "79webhard.com"))
        ftp.login(config.get("username", "stm32"), password)
        ftp.cwd(config.get("path", "/home"))
        with open(file_path, "rb") as stream:
            ftp.storbinary("STOR " + filename, stream)


def extract_file_from_stm32():
    directory = Path(__file__).resolve().parent / "Download"
    directory.mkdir(parents=True, exist_ok=True)
    filename = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".bin"
    destination = directory / filename
    command = ["sudo", "-n", "openocd", "-f", OPENOCD_IFACE, "-f", OPENOCD_TARGET,
               "-c", "init", "-c", "reset halt",
               "-c", 'flash read_bank 0 "' + str(destination) + '" 0',
               "-c", "reset run", "-c", "shutdown"]
    result = run_command(command, timeout=75)
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("펌웨어 추출 실패")
    print("파일 추출 성공: " + str(destination), flush=True)
    upload_to_ftp(destination, filename)
    print("파일 FTP 업로드 성공", flush=True)


if __name__ == "__main__":
    try:
        extract_file_from_stm32()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
