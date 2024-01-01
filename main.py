import subprocess

serve_script = "/home/user/stm32/serve.py"
error_script = "/home/user/stm32/error.py"

try:
    result = subprocess.run(["python3", serve_script], check=True)
except subprocess.CalledProcessError:
    # serve.py 실행 실패
    # error.py 실행
    subprocess.run(["python3", error_script])
