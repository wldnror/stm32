import subprocess
import time
import os

max_retries = 3
serve_script = "/home/user/stm32/serve.py"
error_script = "/home/user/stm32/error.py"

# 설치 완료 플래그(캐시) 파일
INSTALL_FLAG = "/home/user/stm32/.wifi_deps_installed"

# 설치가 필요한 실행파일(명령)들
REQUIRED_BINS = {
    "hostapd": "/usr/sbin/hostapd",
    "dnsmasq": "/usr/sbin/dnsmasq",
}

# 설치가 필요한 파이썬 모듈
REQUIRED_PY_MODULES = [
    "flask",
]

# apt 패키지명 매핑
APT_PACKAGES = [
    "hostapd",
    "dnsmasq",
    "python3-flask",
]

def run_cmd(cmd, check=False):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)

def bin_exists(name, path_hint=None):
    # 1) 지정 경로 힌트가 있으면 먼저 확인
    if path_hint and os.path.isfile(path_hint) and os.access(path_hint, os.X_OK):
        return True
    # 2) which로 확인
    r = run_cmd(["which", name])
    return (r.returncode == 0) and bool(r.stdout.strip())

def python_module_exists(mod_name):
    r = run_cmd(["python3", "-c", f"import {mod_name}"])
    return r.returncode == 0

def deps_ok():
    # 실행파일 체크
    for name, hint in REQUIRED_BINS.items():
        if not bin_exists(name, hint):
            return False
    # 파이썬 모듈 체크
    for m in REQUIRED_PY_MODULES:
        if not python_module_exists(m):
            return False
    return True

def write_flag():
    try:
        with open(INSTALL_FLAG, "w") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass

def install_deps():
    # apt 업데이트 + 설치
    # (sudo 권한 필요)
    print("[main] Installing Wi-Fi portal dependencies...")
    subprocess.run(["sudo", "apt", "update"], check=False)

    # 설치
    subprocess.run(["sudo", "apt", "install", "-y"] + APT_PACKAGES, check=False)

    # 서비스는 자동 실행 충돌을 피하기 위해 disable 권장
    subprocess.run(["sudo", "systemctl", "disable", "--now", "hostapd"], check=False)
    subprocess.run(["sudo", "systemctl", "disable", "--now", "dnsmasq"], check=False)

def ensure_deps_installed():
    """
    - 플래그 파일이 있으면 기본적으로는 설치됐다고 보고,
    - 그래도 실제 바이너리가 없으면(삭제된 경우) 재설치
    - 플래그가 없고 의존성이 없으면 설치
    """
    flag_present = os.path.isfile(INSTALL_FLAG)

    if flag_present and deps_ok():
        return True

    # 플래그가 있어도 실제 deps가 없으면 재설치
    if not deps_ok():
        install_deps()

    # 설치 후 재검증
    if deps_ok():
        write_flag()
        return True

    return False

def run_serve_with_retries():
    for attempt in range(max_retries):
        try:
            subprocess.run(["python3", serve_script], check=True)
            return True
        except subprocess.CalledProcessError:
            if attempt == max_retries - 1:
                subprocess.run(["python3", error_script])
            # 그 외는 재시도
    return False


if __name__ == "__main__":
    # ✅ 1) 의존성 확인/설치
    ok = ensure_deps_installed()
    if not ok:
        # 설치 실패 시 error.py로
        subprocess.run(["python3", error_script])
    else:
        # ✅ 2) serve.py 실행
        run_serve_with_retries()
