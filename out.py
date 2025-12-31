import subprocess
from datetime import datetime
import os
import ftplib

def extract_file_from_stm32():
    memory_address = "0x08000000"
    dump_size = "0x1000"  # 4KB만 먼저 테스트

    now = datetime.now()
    filename = now.strftime("%Y%m%d_%H%M%S") + ".bin"
    save_path = f"/home/user/stm32/Download/{filename}"

    openocd_command = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "transport select swd",
        "-c", "adapter speed 100",
        "-c", "init",
        "-c", "reset halt",
        "-c", f"dump_image {save_path} {memory_address} {dump_size}",
        "-c", "reset run",
        "-c", "shutdown",
    ]

    result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode == 0:
        print("파일 추출 성공!")
        # upload_to_ftp(save_path, filename)  # 테스트 성공 후 켜세요
    else:
        print("파일 추출 실패. 오류 코드:", result.returncode)
        # OpenOCD는 출력이 stderr로도 많이 나옵니다
        print("STDERR:\n", result.stderr)
        print("STDOUT:\n", result.stdout)

def upload_to_ftp(file_path, filename):
    ftp_server = os.environ.get("FTP_SERVER", "")
    ftp_user = os.environ.get("FTP_USER", "")
    ftp_password = os.environ.get("FTP_PASS", "")
    ftp_path = os.environ.get("FTP_PATH", "/home")

    with ftplib.FTP(ftp_server) as ftp:
        ftp.login(ftp_user, ftp_password)
        ftp.cwd(ftp_path)
        with open(file_path, 'rb') as f:
            ftp.storbinary(f"STOR {filename}", f)

if __name__ == "__main__":
    extract_file_from_stm32()
