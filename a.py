import RPi.GPIO as GPIO

GPIO.setmode(GPIO.BCM)  # BCM 모드 사용
GPIO.setup(21, GPIO.OUT)  # GPIO21을 출력 모드로 설정
GPIO.output(21, GPIO.LOW)  # GPIO21을 LOW 상태로 설정
