import subprocess

# 메모리 지우기를 수행할 PIC의 유형에 따라 적절한 명령어를 선택하세요.
# 예시에서는 14-bit PIC 마이크로컨트롤러를 사용합니다.
try:
    subprocess.run(['pickle', 'p14', 'erase'], check=True)
    print("메모리 지우기 성공")
except subprocess.CalledProcessError as e:
    print(f"메모리 지우기 실패: {e}")
