import subprocess

def extract_firmware():
    firmware_path = "/home/pi/stm32_firmware.bin"  # 추출된 펌웨어를 저장할 경로
    address = "0x08000000"  # 시작 메모리 주소
    width = "32"  # 읽을 데이터의 너비 (32비트)
    count = "0x10000"  # 읽을 데이터의 양

    openocd_command = [
        "sudo", "openocd",
        "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
        "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",
        "-c", f"init; reset halt; mdw {address} {count} {width}; reset run; shutdown"
    ]

    try:
        result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            with open(firmware_path, 'w') as file:
                file.write(result.stdout)  # 추출된 데이터를 파일로 저장
            print("펌웨어 추출 성공:", firmware_path)
        else:
            print("펌웨어 추출 실패:", result.stderr)
    except Exception as e:
        print("펌웨어 추출 중 오류 발생:", str(e))

extract_firmware()

