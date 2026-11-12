/* Stock's delete of a folder (Folder view's multi-select Delete; docs/internals.md, "Folder
 * delete"). */
#include "navigation.h"

/* remove_file (0x4ffff0) removes a file with remove() but a folder with rm -rf "<path>" for
 * system(), where the shell still expands $ and backticks: a folder named $NOT removed its parent,
 * and a name could run commands as root. That snprintf (FOLDER_RM_CALL) jumps here: the path goes
 * inside single quotes, each ' as '\''. A path that does not fit n removes nothing. */
int ringnav_folder_rm(char *cmd, unsigned n, const char *fmt, const char *path) {
    unsigned at = 8; /* rm -rf ' */
    (void)fmt;
    memcpy(cmd, "rm -rf '", at);
    for (; *path; ++path) {
        unsigned k = *path == '\'' ? 4 : 1;
        if (at + k + 2 > n) return snprintf(cmd, n, ":"); /* the closing ' and NUL */
        memcpy(cmd + at, k == 4 ? "'\\''" : path, k);
        at += k;
    }
    cmd[at++] = '\'';
    cmd[at] = 0;
    return (int)at;
}
