import subprocess

def extract_firmware():
    start_address = "0x08000000"  # 플래시 메모리 시작 주소
    length = "0x40000"  # 256KB (STM32F103RCT6 모델 기준)

    openocd_command = [
        "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", f"init; mdw {start_address} {length}; exit"
    ]

    try:
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        if result.stdout:
            with open("firmware_dump.bin", "w") as file:
                file.write(result.stdout)
            print("펌웨어 추출 성공")
        else:
            print("펌웨어 추출 실패: 응답 없음")
    except subprocess.CalledProcessError as e:
        print(f"펌웨어 추출 실패: {e.stderr}")

extract_firmware()
