/* Early SH1107 animation. No network, fonts, Python or MCU GPIO access.
 * As init=...: mount the usual API filesystems, fork the optional display
 * helper, and immediately exec the original init. Display failure never
 * prevents Linux from booting. Normal invocation is the systemd fallback.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <linux/magic.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/types.h>
#include <sys/syscall.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include "boot_assets.h"

#define RUNTIME "/run/stm32-boot"
static volatile sig_atomic_t stopping;
static unsigned char frame[8192], cache[16][64];
static int cached;
static double monotime(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void on_signal(int sig) { (void)sig; stopping = 1; }
static void api_mount(const char *type, const char *path, long magic, const char *data) {
    struct statfs fs;
    if (statfs(path, &fs) == 0 && fs.f_type == magic) return;
    mount(type, path, type, MS_NOSUID | (strcmp(path, "/dev") ? MS_NODEV : 0), data);
}
static void modules(void) {
    /* Pre-unpacked copies of this exact kernel's own modules avoid a cold
     * dynamic modprobe + xz startup. Kernel changes fall back to modprobe. */
    struct utsname uts;
    int loaded = 1;
    const char *names[] = {"i2c-bcm2835.ko", "i2c-dev.ko"};
    if (uname(&uts) == 0) {
        for (unsigned i = 0; i < sizeof names / sizeof *names; i++) {
            char path[256];
            snprintf(path, sizeof path, "/usr/local/lib/stm32-boot/%s/%s", uts.release, names[i]);
            int fd = open(path, O_RDONLY | O_CLOEXEC);
            if (fd < 0) { loaded = 0; break; }
            int result = syscall(SYS_finit_module, fd, "", 0);
            int error = errno;
            close(fd);
            if (result != 0 && error != EEXIST) { loaded = 0; break; }
        }
    } else loaded = 0;
    if (loaded) return;
    pid_t pid = fork();
    if (pid == 0) {
        int null = open("/dev/null", O_WRONLY);
        if (null >= 0) { dup2(null, 1); dup2(null, 2); close(null); }
        execl("/sbin/modprobe", "modprobe", "-a", "i2c_bcm2835", "i2c_dev", (char *)0);
        _exit(127);
    }
    /* The helper has a short deadline; it must not wait on a stuck modprobe. */
    double deadline = monotime() + 2;
    int status;
    while (pid > 0 && waitpid(pid, &status, WNOHANG) == 0) {
        if (monotime() >= deadline) { kill(pid, SIGKILL); waitpid(pid, &status, 0); break; }
        usleep(20000);
    }
}
static void text(const char *s, int y) {
    size_t len = strlen(s);
    if (len > 21) len = 21;
    int x0 = (128 - (int)(len * 6 - 1)) / 2;
    for (size_t i = 0; i < len; i++)
        for (int x = 0; x < 5; x++)
            for (int b = 0; b < 7; b++)
                if (glyphs[(unsigned char)s[i] & 127][x] & (1 << b))
                    frame[(y + b) * 128 + x0 + i * 6 + x] = 1;
}
static void draw(int progress, const char *stage, int elapsed, int tick) {
    memcpy(frame, base_frame, sizeof frame);
    text(stage, 28);
    for (int i = 0; i <= tick % 4; i++)
        frame[38 * 128 + 57 + i * 4] = frame[38 * 128 + 58 + i * 4] = 1;
    if (progress < 0) progress = 0;
    if (progress > 100) progress = 100;
    for (int y = 46; y <= 49; y++)
        for (int x = 14; x < 14 + progress; x++) frame[y * 128 + x] = 1;
    if (progress <= 90) {
        int span = 100 - progress - 8;
        if (span < 1) span = 1;
        int left = 16 + progress + (tick * 5) % span;
        for (int y = 47; y <= 48; y++)
            for (int x = left; x < left + 6; x++) frame[y * 128 + x] = 1;
    }
    char buf[22];
    snprintf(buf, sizeof buf, "%dS", elapsed);
    text(buf, 56);
}
static int send_frame(int fd) {
    unsigned char pages[16][64] = {{0}};
    for (int y = 0; y < 64; y++)
        for (int x = 0; x < 128; x++)
            pages[x / 8][63 - y] |= frame[y * 128 + x] << (x % 8);
    for (int p = 0; p < 16; p++) {
        int first = 0, last = 63;
        if (cached) {
            while (first < 64 && pages[p][first] == cache[p][first]) first++;
            if (first == 64) continue;
            while (last > first && pages[p][last] == cache[p][last]) last--;
        }
        unsigned char cmd[] = {0, 0x10 | (first >> 4), first & 15, 0xb0 | p};
        unsigned char data[65] = {0x40};
        int length = last - first + 1;
        memcpy(data + 1, pages[p] + first, length);
        if (write(fd, cmd, sizeof cmd) != sizeof cmd || write(fd, data, length + 1) != length + 1)
            return -1;
    }
    unsigned char show[] = {0, 0xaf};
    if (write(fd, show, sizeof show) != sizeof show) return -1;
    memcpy(cache, pages, sizeof cache);
    cached = 1;
    return 0;
}
static char *value(char *json, const char *key) {
    char *p = strstr(json, key);
    if (!p || !(p = strchr(p, ':'))) return NULL;
    do { p++; } while (*p == ' ' || *p == '\t');
    return p;
}
static int state(int *progress, char *stage, size_t size) {
    char buf[1024];
    int fd = open(RUNTIME "/state.json", O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return 0;
    ssize_t n = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (n <= 0) return 0;
    buf[n] = 0;
    char *pid = value(buf, "\"pid\"");
    if (!pid || atoi(pid) <= 1 || kill(atoi(pid), 0) != 0) return 0;
    char *p = value(buf, "\"handoff\"");
    if (p && strncmp(p, "true", 4) == 0) return 1;
    p = value(buf, "\"progress\"");
    if (p) *progress = atoi(p);
    p = value(buf, "\"stage\"");
    if (p && *p == '"') {
        p++;
        size_t i = 0;
        while (i + 1 < size && p[i] && p[i] != '"') { stage[i] = p[i]; i++; }
        stage[i] = 0;
    }
    return 0;
}
static void record_frame(void) {
    char tmp[] = RUNTIME "/.native-frame-XXXXXX";
    int fd = mkstemp(tmp);
    if (fd < 0) return;
    fchmod(fd, 0644);
    dprintf(fd, "{\"first_frame_at\":%.6f,\"pid\":%d,\"source\":\"native\"}\n", monotime(), getpid());
    close(fd);
    /* Keep the boot's earliest frame when the fallback runs on app restart. */
    link(tmp, RUNTIME "/native-first-frame.json");
    unlink(tmp);
}
static int animation(void) {
    signal(SIGTERM, on_signal); signal(SIGINT, on_signal);
    mkdir(RUNTIME, 0775); chown(RUNTIME, 1000, 1000); chmod(RUNTIME, 0775);
    int lock = open(RUNTIME "/display.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0664);
    if (lock < 0) return 0;
    fchown(lock, 1000, 1000); fchmod(lock, 0664);
    if (flock(lock, LOCK_EX | LOCK_NB) != 0) { close(lock); return 0; }
    modules();
    int fd = -1, tick = 0, progress = 10;
    double start = monotime(), deadline = start + 120;
    char stage[22] = "SYSTEM STARTING";
    while (!stopping && monotime() < deadline) {
        if (state(&progress, stage, sizeof stage)) break;
        if (fd < 0) {
            fd = open("/dev/i2c-1", O_RDWR);
            if (fd < 0) { usleep(50000); continue; }
            if (ioctl(fd, I2C_SLAVE, 0x3c) < 0 || write(fd, oled_init, sizeof oled_init) != sizeof oled_init) {
                close(fd); fd = -1; usleep(100000); continue;
            }
            cached = 0;
        }
        draw(progress, stage, (int)(monotime() - start), tick);
        if (send_frame(fd) < 0) { close(fd); fd = -1; usleep(100000); continue; }
        if (tick == 0) { record_frame(); nice(10); }
        tick++;
        usleep(250000);
    }
    if (fd >= 0) close(fd);
    close(lock); /* Preserve the image; main takes this same lock before luma. */
    return 0;
}
int main(int argc, char **argv) {
    /* Raw logical pixels for parity checks and previews; never opens I2C. */
    if (argc == 6 && strcmp(argv[1], "--render") == 0) {
        draw(atoi(argv[2]), argv[3], atoi(argv[4]), atoi(argv[5]));
        return fwrite(frame, 1, sizeof frame, stdout) == sizeof frame ? 0 : 1;
    }
    if (getpid() == 1) {
        api_mount("proc", "/proc", PROC_SUPER_MAGIC, "");
        api_mount("sysfs", "/sys", SYSFS_MAGIC, "");
        api_mount("devtmpfs", "/dev", TMPFS_MAGIC, "mode=0755");
        api_mount("tmpfs", "/run", TMPFS_MAGIC, "mode=0755");
        pid_t pid = fork();
        if (pid == 0) { setsid(); return animation(); }
        /* No waits for OLED, modules or animation on the boot-critical path. */
        argv[0] = "/sbin/init";
        execv(argv[0], argv);
        execl("/lib/systemd/systemd", "systemd", (char *)0);
        return 127;
    }
    return animation();
}
