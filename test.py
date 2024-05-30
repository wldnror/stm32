import Adafruit_ADS1x15
import time

# ADS1115 객체 생성, I2C 주소 설정 (0x48로 설정)
adc = Adafruit_ADS1x15.ADS1115(address=0x48)

# Gain 설정 (1은 +/- 4.096V 범위)
GAIN = 1
REFERENCE_VOLTAGE = 4.096  # GAIN=1일 때 참조 전압

def read_current(adc, gain):
    # ADC 값을 읽기
    adc_value = adc.read_adc(0, gain=gain)
    
    # ADC 값을 전압으로 변환
    voltage = (adc_value / 32767.0) * REFERENCE_VOLTAGE
    
    # 전압을 전류(mA)로 변환
    current = (voltage / REFERENCE_VOLTAGE) * 20.0
    
    return adc_value, voltage, current

try:
    while True:
        adc_value, voltage, current = read_current(adc, GAIN)
        print(f'ADC Value: {adc_value}, Voltage: {voltage:.4f} V, Current: {current:.4f} mA')
        time.sleep(1)  # 1초 간격으로 읽기
except KeyboardInterrupt:
    print("Program terminated")
