import subprocess

try:
    # serve.py 실행
    result = subprocess.run(["python3", "serve.py"], check=True)
except subprocess.CalledProcessError:
    # serve.py 실행 중 오류 발생 시 error.py 실행
    subprocess.run(["python3", "error.py"])
