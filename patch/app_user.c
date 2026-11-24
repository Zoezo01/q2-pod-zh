/* Card apps without root (docs/internals.md#card-apps, after MrRedflag's proposal): a card app,
 * card game or Spotify's librespot runs as APP_UID, in groups video (the screen) and audio (ALSA,
 * and Bluetooth through bluealsa), unless its q2app says root=1 (addon.c). So a bug in an app's
 * server, which anyone on the same Wi-Fi can reach, can't read /mnt/data's secrets or change the
 * system. The card is the apps' (q2mount mounts it for APP_UID: tools/build.py Q2MOUNT), and every
 * app shares it, so this guards demo's and stock's files, not one app's from another. Under a card
 * mounted without that (q2mount's option refused), apps run as root as before, so they can still
 * write it.
 * ponytail: this guards against a buggy app, not a hostile one: root=1 is the app's own word, an
 * app can rewrite what root runs or flashes from the card (.rockbox/rockbox, a root=1 app's run,
 * Network Update's update.tar), and root kills the pid an app's own pid file names (addon.c
 * stop_cmd). An app that ran as root earlier in the same boot leaves /tmp files it can no longer
 * replace until a restart clears /tmp. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h" /* APP_UID (tools/build.py) */

/* o32 numbers (arch/mips/kernel/syscalls/syscall_o32.tbl): demo imports none of these */
#define SYS_CHMOD 4015
#define SYS_LCHOWN 4016
#define SYS_SETUID 4023
#define SYS_SETGID 4046
#define SYS_SETGROUPS 4081
#define SYS_CHOWN 4202
#define GID_VIDEO 28 /* the stock rootfs's /etc/group */
#define GID_AUDIO 29 /* the ALSA nodes', the DAC's */

/* No mdev rule makes these: root:root 660. The DAC's is an audio=1 game's on the jack, its mute
 * and PCM mode cleared after the PCM's open (docs/librespot.md). */
static void dev_group(const char *dev, unsigned gid) {
    cf_syscall(SYS_CHOWN, (long)dev, 0, gid);
    cf_syscall(SYS_CHMOD, (long)dev, 0660, 0);
}

/* Root's alone: stock's Wi-Fi passwords (wifi_connect.sh keeps that one 600 at each save as well:
 * tools/build.py WPA_HOOK), its Tidal and Baidu logins, and q2remote's key.
 * ponytail: set at each non-root start, so a Tidal or Baidu login while an app runs leaves that
 * file 644 until the next one. */
static const char *const secrets[] = { "/mnt/data/wpa_supplicant.conf", "/mnt/data/tidal.conf",
                                       "/mnt/data/baidu_token.conf", "/mnt/data/q2remote.key" };

/* Whether the card is mounted for APP_UID, so an app can drop root and still write it. */
int app_card(void) {
    unsigned st[36] = { 0 }; /* o32 struct stat: st_uid is word 7 (st_mtime 16, addon.c changed) */
    return !__xstat(3, "/mnt/mmc", st) && st[7] == APP_UID;
}

/* In a forked child, still root: the secrets and the screen set, then APP_UID and its groups.
 * Fails closed: a drop that doesn't take ends the child, so nothing runs as root by mistake. */
void app_user(void) {
    static const unsigned groups[] = { GID_VIDEO, GID_AUDIO };
    for (unsigned i = 0; i < sizeof secrets / sizeof *secrets; ++i)
        cf_syscall(SYS_CHMOD, (long)secrets[i], 0600, 0); /* one not there yet is left */
    dev_group("/dev/fb0", GID_VIDEO);
    dev_group("/dev/shanling_dac", GID_AUDIO);
    /* etc/mdev.conf's root:audio rules for these name snd/, which a rule with a / may never match
     * (the subsystem is sound/), and a USB DAC's come at its plug */
    char node[48];
    void *d = opendir("/dev/snd");
    for (struct dirent *e; d && (e = readdir(d));)
        if (e->d_name[0] != '.') {
            tk_snprintf(node, sizeof node, "/dev/snd/%s", e->d_name);
            dev_group(node, GID_AUDIO);
        }
    if (d) closedir(d);
    if (cf_syscall(SYS_SETGROUPS, 2, (long)groups, 0) || cf_syscall(SYS_SETGID, APP_UID, 0, 0) ||
        cf_syscall(SYS_SETUID, APP_UID, 0, 0))
        child_exit(126);
}

/* system(cmd) as APP_UID (a card app's run, Spotify's): none of demo's files, waited for as
 * system() waits (a run backgrounds its loop and ends); as root, system() itself, under a card not
 * mounted for APP_UID. */
void app_system(const char *cmd) {
    if (!app_card()) {
        system(cmd);
        return;
    }
    int status, pid = fork();
    if (!pid) {
        for (int fd = 3; fd < 1024; ++fd)
            close(fd); /* demo's: the watchdog, the database, sockets */
        app_user();
        execl("/bin/sh", "sh", "-c", cmd, (char *)0);
        child_exit(127);
    }
    if (pid > 0) waitpid(pid, &status, 0);
}

/* A reply demo renames over an app's request in /tmp (.view's open, .audio's device line), the
 * app's to replace or remove again: /tmp is sticky, so one of root's would refuse APP_UID. Root
 * replaces it either way. lchown: a link left at the .tmp is renamed there, never followed. */
void app_own(const char *path) { cf_syscall(SYS_LCHOWN, (long)path, APP_UID, APP_UID); }
