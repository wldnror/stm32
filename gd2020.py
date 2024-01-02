import subprocess
import os

# 프로그래밍할 HEX 파일의 경로
hex_file_path = '/home/user/stm32/Program/nh3-gn8020-e.hex'

# 파일의 읽기/쓰기 권한 변경
os.chmod(hex_file_path, 0o666)

# Pickle 명령어를 사용하여 PIC 프로그래밍
try:
    subprocess.run(['pickle', 'p14', 'lvp', 'program', hex_file_path], check=True)
    print("프로그래밍 성공")
except subprocess.CalledProcessError as e:
    print(f"프로그래밍 실패: {e}")
