#!/bin/sh
# Read CCF accounting and IRQ activity. Never writes a clock/rate/gate control.
set -eu
p=$(mktemp -d /tmp/q2-clock-XXXXXX)
mounted=0
cleanup() {
    if [ "$mounted" = 1 ]; then
        umount "$p" || return
    fi
    rmdir "$p"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mount -t debugfs debugfs "$p"
mounted=1
printf '\n--- kernel ---\n'
uname -a
printf '\n--- boot ---\n'
cat /proc/sys/kernel/random/boot_id /proc/uptime
printf '\n--- clock framework ---\n'
cat "$p/clk/clk_summary"
printf '\n--- rotator hardware descriptor (when available) ---\n'
find "$p/clk" -path '*/gate_rot/hwdesc' -exec cat {} \;
find "$p/clk" -path '*/gate_rot/clk_flags' -exec cat {} \;
printf '\n--- interrupts ---\n'
cat /proc/interrupts
printf '\n--- rotator module ---\n'
sha256sum /module_driver/soc_rotator.ko
cat /sys/module/soc_rotator/refcnt
printf '\n--- loaded module build ID ---\n'
od -An -tx1 /sys/module/soc_rotator/notes/.note.gnu.build-id
printf '\n--- framebuffer rotation ---\n'
for f in /sys/module/soc_fb/parameters/is_rotated /sys/module/soc_fb/parameters/rotator_angle; do
    if [ -r "$f" ]; then printf '%s: ' "$f"; cat "$f"; fi
done
