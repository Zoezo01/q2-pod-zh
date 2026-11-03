/* q2boot: exits 0 while Play/Pause is held, else 1. S90play runs it at power-on to switch between
 * Q2 Pod and Rockbox (docs/boot.md#rockbox). The keys are "md-gpio-keys", Play/Pause KEY_DOWN (108).
 * No MIPS sysroot: glibc 2.28's declarations, with MIPS o32 ioctl numbers. */
#define KEY_PLAY 108
#define EVIOCGNAME32 0x40204506u /* _IOC(_IOC_READ, 'E', 0x06, 32); MIPS reads are 2 << 29 */
#define EVIOCGKEY32 0x40204518u  /* _IOC(_IOC_READ, 'E', 0x18, 32): 256 key bits */

int open(const char *, int, ...), ioctl(int, unsigned, ...), strcmp(const char *, const char *);

int main(void) {
    char path[] = "/dev/input/event0";
    for (char i = '0'; i < '8'; ++i) {
        unsigned char name[32] = { 0 }, keys[32];
        path[sizeof path - 2] = i;
        int fd = open(path, 0), named = fd >= 0 && ioctl(fd, EVIOCGNAME32, name) >= 0 &&
                                         !strcmp((char *)name, "md-gpio-keys");
        int held = named && ioctl(fd, EVIOCGKEY32, keys) >= 0 && keys[KEY_PLAY / 8] >> KEY_PLAY % 8 & 1;
        if (named) return !held;
    }
    return 1;
}
