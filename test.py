from luma.core.interface.serial import i2c, spi
from luma.core.render import canvas
from luma.oled.device import sh1107
import time

# Raspberry Pi의 I2C나 SPI 포트를 설정
serial = i2c(port=1, address=0x3C)  # I2C 사용 예제
# serial = spi(device=0, port=0)  # SPI 사용 예제

# SH1107 디바이스 초기화
device = sh1107(serial)

while True:
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, outline="white", fill="black")
        draw.text((30, 40), "Hello World", fill="white")
    
    # 화면을 일정 시간동안 유지
    time.sleep(1)
