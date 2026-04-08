import threading
import time
from ina219 import INA219

import app_state as st
from app_config import SHUNT_OHMS, MIN_VOLTAGE, MAX_VOLTAGE

def ina_poll_loop(interval=0.35):
    while not st.stop_threads:
        try:
            sensor = st.ina
            if sensor is None:
                time.sleep(0.6)
                continue
            with st.ina_lock:
                v = sensor.voltage()
                c = None
                p = None
                try:
                    c = sensor.current()
                except Exception:
                    c = None
                try:
                    p = sensor.power()
                except Exception:
                    p = None
            st.ina_last = {"v": v, "c": c, "p": p, "ts": time.time()}
        except Exception:
            pass
        time.sleep(interval)

def init_ina219():
    try:
        st.ina = INA219(SHUNT_OHMS)
        st.ina.configure()
    except Exception:
        st.ina = None

    if not st.ina_poll_started:
        st.ina_poll_started = True
        threading.Thread(target=ina_poll_loop, daemon=True).start()

def _ina_get_voltage(max_age=2.0):
    d = st.ina_last
    ts = d.get("ts", 0.0) or 0.0
    if ts and (time.time() - ts) <= max_age:
        return d.get("v", None)
    return None

def read_ina219_percentage():
    try:
        v = _ina_get_voltage(max_age=2.5)
        if v is None:
            return -1
        voltage = float(v)
        if voltage <= MIN_VOLTAGE:
            return 0
        if voltage >= MAX_VOLTAGE:
            return 100
        return int(((voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)) * 100)
    except Exception:
        return -1

def battery_monitor_thread():
    while not st.stop_threads:
        st.battery_percentage = read_ina219_percentage()
        time.sleep(2)
