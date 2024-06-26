import subprocess
from datetime import datetime
import ftplib

def extract_file_from_stm32():
    # 추출할 파일의 STM32 메모리 주소 및 크기 설정
    memory_address = "0x08000000"  # 예시 주소
    memory_size = "256K"

    # 현재 날짜와 시간을 기반으로 파일 이름 지정
    now = datetime.now()
    filename = now.strftime("%Y%m%d_%H%M%S") + ".bin"
    save_path = f"/home/user/stm32/Download/{filename}"

    # OpenOCD 명령을 사용하여 STM32의 메모리 덤프
    openocd_command = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", "init",
        "-c", "reset halt",
        "-c", f"flash read_bank 0 {save_path} 0",
        "-c", "reset run",
        "-c", "shutdown",
    ]

    # 명령 실행 및 결과 확인
    try:
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            print("파일 추출 성공!")
            upload_to_ftp(save_path, filename)
        else:
            print("파일 추출 실패. 오류 코드:", result.returncode)
            print("오류 메시지:", result.stderr)
    except Exception as e:
        print("명령 실행 중 오류 발생:", str(e))

def upload_to_ftp(file_path, filename):
    ftp_server = "79webhard.com"
    ftp_user = "stm32"
    ftp_password = "Gds00700@"
    ftp_path = "/home"

    try:
        with ftplib.FTP(ftp_server) as ftp:
            ftp.login(ftp_user, ftp_password)
            ftp.cwd(ftp_path)

            with open(file_path, 'rb') as file:
                ftp.storbinary(f'STOR {filename}', file)
            
            print("파일 FTP 업로드 성공!")
    except ftplib.all_errors as e:
        print("FTP 업로드 실패:", str(e))

# 함수 호출
extract_file_from_stm32()
