from datetime import datetime
import os
import sys
import time
import threading
import subprocess
import RPi.GPIO as GPIO
from luma.core.render import canvas

import app_state as st
from app_config import *
from app_utils import ensure_menu_config_csv
from power_manager import init_ina219, battery_monitor_thread
from stm32_manager import kill_openocd
from menu_manager import refresh_root_menu

# 기존에 아직 안 옮긴 함수들은 우선 여기 남겨도 됨.
# 예: button_next_edge, button_execute_edge, wifi_worker_thread, execute_button_logic 등
# 그리고 점진적으로 옮기면 됨.

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# 버튼/LED setup은 일단 여기 두거나 hardware.py로 추가 분리 가능
GPIO.setup(BUTTON_PIN_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN_EXECUTE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(LED_SUCCESS, GPIO.OUT)
GPIO.setup(LED_ERROR, GPIO.OUT)
GPIO.setup(LED_ERROR1, GPIO.OUT)

ensure_menu_config_csv()
init_ina219()
refresh_root_menu(reset_index=True)

battery_thread = threading.Thread(target=battery_monitor_thread, daemon=True)
battery_thread.start()

# 아래 thread들도 네가 함수 옮긴 만큼 import해서 연결
# realtime_update_thread = threading.Thread(target=realtime_update_display, daemon=True)
# stm32_thread = threading.Thread(target=stm32_poll_thread, daemon=True)
# wifi_thread = threading.Thread(target=wifi_worker_thread, daemon=True)
# net_thread = threading.Thread(target=net_poll_thread, daemon=True)
# git_thread = threading.Thread(target=git_poll_thread, daemon=True)
# scan_thread = threading.Thread(target=modbus_scan_loop, daemon=True)
# detail_thread = threading.Thread(target=modbus_detail_poll_thread, daemon=True)

st.need_update = True

try:
    execute_button_logic()
except KeyboardInterrupt:
    pass
finally:
    st.stop_threads = True
    try:
        kill_openocd()
    except Exception:
        pass
    GPIO.cleanup()
