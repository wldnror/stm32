import pygame
import time
import os

os.environ['DISPLAY'] = ':0'
# 사운드 파일 경로 설정
script_dir = os.path.dirname(os.path.abspath(__file__))
FAILURE_SOUND_PATH = os.path.join(script_dir, 'gms_k1.mp3')  # 또는 변환된 파일 경로

# Pygame 초기화
pygame.mixer.init()

# 사운드 로드
if os.path.isfile(FAILURE_SOUND_PATH):
    try:
        sound = pygame.mixer.Sound(FAILURE_SOUND_PATH)
        print("사운드 파일 로드 성공.")
    except pygame.error as e:
        print(f"사운드 파일 로드 실패: {e}")
        exit(1)
else:
    print(f"사운드 파일을 찾을 수 없습니다: {FAILURE_SOUND_PATH}")
    exit(1)

# 사운드 재생
try:
    print("사운드 재생 중...")
    sound.play()
    # 사운드가 재생되는 동안 대기
    while pygame.mixer.get_busy():
        time.sleep(0.1)
    print("사운드 재생 완료.")
except Exception as e:
    print(f"사운드 재생 중 오류 발생: {e}")
