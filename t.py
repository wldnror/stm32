import subprocess

def extract_firmware():
    # STM32F103RCT6 플래시 메모리 시작 주소와 크기
    start_address = "0x08000000"  # 플래시 메모리 시작 주소
    length = "65536"  # 256KB를 4바이트 워드로 나눈 값

    # OpenOCD 명령 구성
    openocd_command = [
        "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", f"init; mdw {start_address} {length}; exit"
    ]

    # OpenOCD 실행 및 출력 캡처
    try:
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        if result.stdout:
            # 출력된 메모리 데이터를 파일에 저장
            with open("firmware_dump.bin", "w") as file:
                file.write(result.stdout)
            print("펌웨어 추출 성공")
        else:
            print("펌웨어 추출 실패: 응답 없음")
    except subprocess.CalledProcessError as e:
        print(f"펌웨어 추출 실패: {e.stderr}")

# 펌웨어 추출 함수 실행
extract_firmware()
