import smbus
import time
from gpiozero import LED

# MPU-6050 설정
power_mgmt_1 = 0x6b
bus = smbus.SMBus(1)
address = 0x68  # MPU-6050의 기본 I2C 주소

# LED 설정 (GPIO 17번 핀과 27번 핀 사용)
led_right = LED(17)  # 우측 기울기를 나타내는 LED
led_left = LED(27)   # 좌측 기울기를 나타내는 LED

def init_mpu6050():
    bus.write_byte_data(address, power_mgmt_1, 0)

def read_accelerometer(axis):
    high = bus.read_byte_data(address, axis)
    low = bus.read_byte_data(address, axis + 1)
    value = (high << 8) + low
    if value >= 0x8000:
        return -((65535 - value) + 1)
    else:
        return value

def control_leds():
    init_mpu6050()
    while True:
        accel_x = read_accelerometer(0x3b)  # X축 가속도 값 읽기

        if accel_x > 1500:
            # 우측으로 기울었을 때
            led_right.on()
            led_left.off()
        elif accel_x < -1500:
            # 좌측으로 기울었을 때
            led_left.on()
            led_right.off()
        else:
            # 기울기가 없을 때
            led_left.off()
            led_right.off()

        time.sleep(0.1)

if __name__ == "__main__":
    control_leds()
