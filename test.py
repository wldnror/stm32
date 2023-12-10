from luma.core.interface.serial import i2c, spi
from luma.core.render import canvas
from luma.oled.device import sh1107

# Raspberry Pi의 I2C나 SPI 포트를 설정
serial = i2c(port=1, address=0x3C)  # I2C 사용 예제
# serial = spi(device=0, port=0)  # SPI 사용 예제

# SH1107 디바이스 초기화
device = sh1107(serial)

# 간단한 텍스트와 그래픽을 화면에 표시
with canvas(device) as draw:
    draw.rectangle(device.bounding_box, outline="white", fill="black")
    draw.text((30, 40), "Hello World", fill="white")

