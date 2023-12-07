import time
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1107
from PIL import ImageFont

# OLED 디스플레이 설정
serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

# 폰트 설정
font_path = '/usr/share/fonts/truetype/malgun/malgunbd.ttf'
font = ImageFont.truetype(font_path, 14)

def display_loading_bar(duration=5):
    start_time = time.time()
    while time.time() - start_time < duration:
        with canvas(device) as draw:
            draw.text((10, 20), "부팅 중...", font=font, fill=255)
            # 로딩 바
            progress = (time.time() - start_time) / duration
            progress_width = max(5, 125 * progress)  # 최소 너비를 5로 설정
            draw.rectangle([(5, 40), (progress_width, 50)], outline="white", fill="white")
        time.sleep(0.1)

if __name__ == '__main__':
    display_loading_bar(13)
