import subprocess

# 프로그래밍할 HEX 파일의 경로
hex_file_path = '/Program/nh3-gn8020-e.hex'

# Pickle 명령어를 사용하여 PIC 프로그래밍
# 여기서는 14-bit PIC 마이크로컨트롤러를 예로 들었습니다.
# 실제 PIC 유형에 따라 명령어를 조정해야 할 수 있습니다 (예: p16, n16 등).
try:
    subprocess.run(['pickle', 'p14', 'lvp', 'program', hex_file_path], check=True)
    print("프로그래밍 성공")
except subprocess.CalledProcessError as e:
    print(f"프로그래밍 실패: {e}")
