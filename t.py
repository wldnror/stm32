import telnetlib
import time

def check_connection(host='localhost', port=4444, timeout=10):
    try:
        with telnetlib.Telnet(host, port, timeout) as tn:
            tn.write(b"poll\n")
            response = tn.read_until(b">", timeout=timeout).decode('utf-8')
            if "target state: halted" in response or "target state: running" in response:
                return True
    except Exception as e:
        print(f"Error: {e}")
    return False

while True:
    if check_connection():
        print("STM32 연결됨.")
    else:
        print("STM32 연결 끊김!")
    time.sleep(5)  # 5초마다 체크
