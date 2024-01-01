import subprocess

serve_script = "/home/user/stm32/serve.py"
error_script = "/home/user/stm32/error.py"

try:
    result = subprocess.run(["python3", serve_script], check=True, timeout=10)
except subprocess.CalledProcessError:
    # serve.py 실행 실패
    # error.py 실행
    subprocess.run(["python3", error_script])
except subprocess.TimeoutExpired:
    # serve.py 실행이 10초 동안 완료되지 않았을 때 처리할 내용
    # error.py 실행
    subprocess.run(["python3", error_script])
