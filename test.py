import Adafruit_ADS1x15

# ADS1115 객체 생성, I2C 주소 설정 (예: 0x49)
adc = Adafruit_ADS1x15.ADS1115(address=0x49)

# Gain 설정 (1은 +/- 4.096V 범위)
GAIN = 1

# 채널 0에서 데이터 읽기 (단일 종료 모드)
value = adc.read_adc(0, gain=GAIN)
print('Channel 0: {}'.format(value))
