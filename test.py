import Adafruit_ADS1x15
import time
import matplotlib.pyplot as plt

# ADS1115 객체 생성, I2C 주소 설정 (0x48로 설정)
adc = Adafruit_ADS1x15.ADS1115(address=0x48)

# Gain 설정 (1은 +/- 4.096V 범위)
GAIN = 1
REFERENCE_VOLTAGE = 4.096  # GAIN=1일 때 참조 전압

# 데이터 저장을 위한 리스트
times = []
currents = []

def read_current(adc, gain):
    # ADC 값을 읽기
    adc_value = adc.read_adc(0, gain=gain)
    
    # ADC 값을 전압으로 변환
    voltage = (adc_value / 32767.0) * REFERENCE_VOLTAGE
    
    # 전압을 전류(mA)로 변환
    current = (voltage / REFERENCE_VOLTAGE) * 20.0
    
    return adc_value, voltage, current

# 실시간 플로팅 설정
plt.ion()
fig, ax = plt.subplots()
line, = ax.plot(times, currents, '-o')
ax.set_ylim(0, 20)
ax.set_xlim(0, 10)
plt.xlabel('Time (s)')
plt.ylabel('Current (mA)')

try:
    start_time = time.time()
    while True:
        adc_value, voltage, current = read_current(adc, GAIN)
        elapsed_time = time.time() - start_time
        
        # 데이터 업데이트
        times.append(elapsed_time)
        currents.append(current)
        
        # 그래프 업데이트
        line.set_xdata(times)
        line.set_ydata(currents)
        ax.relim()
        ax.autoscale_view()
        
        plt.draw()
        plt.pause(1)  # 1초 간격으로 읽기
        print(f'ADC Value: {adc_value}, Voltage: {voltage:.4f} V, Current: {current:.4f} mA')
except KeyboardInterrupt:
    print("Program terminated")
    plt.ioff()
    plt.show()
