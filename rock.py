#깃허브 테스트

import subprocess

# OpenOCD 스크립트와 명령어 설정
openocd_command = [
    "sudo",
    "openocd",
    "-f", "/usr/local/share/openocd/scripts/interface/raspberrypi-native.cfg",
    "-f", "/usr/local/share/openocd/scripts/target/stm32f1x.cfg",  # 실제 타겟 설정 파일로 교체해야 함
    "-c", "init",
    "-c", "reset halt",
    "-c", "stm32f1x lock 0",  # RDP 활성화 명령. 이 명령은 해당 STM32 모델의 문서를 참조하여 수정될 수 있음
    "-c", "reset run",
    "-c", "shutdown",
]

try:
    # OpenOCD 명령어 실행
    result = subprocess.run(openocd_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # 실행 결과 출력
    print("표준 출력:")
    print(result.stdout)
    print("표준 에러:")
    print(result.stderr)
    
    # 프로세스 실행 상태 확인
    if result.returncode == 0:
        print("성공적으로 메모리를 잠갔습니다.")
    else:
        print("메모리 잠금에 실패했습니다. 오류 코드:", result.returncode)
except Exception as e:
    print("명령 실행 중 오류 발생:", str(e))
