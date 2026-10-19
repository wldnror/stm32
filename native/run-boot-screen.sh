#!/bin/sh
# Keep existing installations functional until the optional native helper is installed.
if [ -x /usr/local/sbin/stm32-early-init ]; then
    exec /usr/local/sbin/stm32-early-init
fi
/sbin/modprobe -a i2c_bcm2835 i2c_dev || true
exec /usr/bin/python3 -u /home/user/stm32/boot_screen.py
