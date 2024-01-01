import subprocess
import threading
import time

max_retries = 3  # 최대 재시도 횟수 설정
serve_script = "/home/user/stm32/serve.py"
error_script = "/home/user/stm32/error.py"

# 서버 실행 여부를 감시하기 위한 함수
def watch_server():
    retries = 0
    while retries < max_retries:
        print(f"재시도 횟수: {retries + 1}")
        server_process = subprocess.Popen(["python3", serve_script])
        server_process.wait()  # 서버 실행이 종료될 때까지 대기

        if server_process.returncode == 0:
            # 서버가 정상 종료된 경우
            print("서버가 정상 종료되었습니다.")
            break
        else:
            # 서버가 비정상 종료된 경우
            print("서버가 비정상 종료되었습니다.")
            retries += 1

# 감시 스레드 시작
watcher_thread = threading.Thread(target=watch_server)
watcher_thread.start()

# 메인 스레드에서는 감시 스레드를 계속 실행
while True:
    time.sleep(1)  # 메인 스레드는 여기에서 다른 작업 수행 가능

