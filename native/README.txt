Native early animation (Pi Zero W, ARMv6)

Generate assets and build on the Pi:
  python3 native/build_assets.py
  gcc -Os -static -Wall -Wextra -Werror -o /tmp/stm32-early-init native/boot_screen.c
  sudo install -m 0755 /tmp/stm32-early-init /usr/local/sbin/stm32-early-init
  sudo python3 native/install_modules.py

Systemd fallback: run-boot-screen.sh uses the native binary when installed;
otherwise it retains the existing Python renderer.
Optional earlier entry: append init=/usr/local/sbin/stm32-early-init to a COPY
of cmdline.txt. Select that copy only in a tryboot.txt one-shot trial using
cmdline=gds-native-cmdline.txt. Do not change the normal cmdline before validation.
The helper mounts the normal API filesystems, forks only the display worker and
immediately execs the original /sbin/init. OLED/module failures happen in the
child and never delay the real init. Existing Python/main display locking and
handoff are preserved. No framebuffer, MCU pins or network are touched.

The installed device uses numeric UID/GID 1000 for user; /run/stm32-boot and its
lock are assigned to that account without slow NSS lookups. Do not deploy this
unchanged to a different account ID. Native first-frame timestamp is recorded
in /run/stm32-boot/native-first-frame.json and is relative to Linux kernel start.

Initial firmware output is still static: the firmware interpreter executes
commands synchronously. Adding delays/animation there would delay Linux itself.
The native helper bridges to real stage progress with moving activity dots.

Fast module loading uses unpacked copies of the running kernel's original
I2C modules, under a kernel-release-specific directory. No kernel/module
updates are performed. Missing copies or a kernel change fall back to modprobe.
