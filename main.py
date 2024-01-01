import subprocess

max_retries = 3  # 최대 재시도 횟수 설정
serve_script = "/home/user/stm32/serve.py"
error_script = "/home/user/stm32/error.py"

for attempt in range(max_retries):
    try:
        # serve.py 실행
        result = subprocess.Popen(["python3", serve_script], check=True)
        break  # 성공 시 루프 종료
    except subprocess.CalledProcessError:
        # serve.py 실행 실패
        if attempt == max_retries - 1:  # 마지막 시도에서도 실패한 경우
            # error.py 실행
            subprocess.run(["python3", error_script])
        # 그 외 경우는 다시 시도
