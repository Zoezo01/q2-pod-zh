#!/bin/bash
# Exercise assets/web/q2cgi — the safe Wi-Fi transfer file manager (docs/internals.md#safe-file-
# manager) — under busybox ash with synthetic CGI requests. /mnt/mmc is a tmpfs, so the mount check
# and the filesystem boundary are real. Needs busybox, and that mount:
# - a mount already at /mnt/mmc is used as it is, and emptied, so the suite needs no root itself:
#   `sudo mount -t tmpfs -o uid=$(id -u),gid=$(id -g) tmpfs /mnt/mmc` first, as
#   .github/workflows/firmware.yml does, or a container's `--tmpfs /mnt/mmc`;
# - run as root with none there, it mounts a tmpfs and takes it away again.
# With neither it skips. Under CI (the CI variable, as GitHub sets it) a skip is a failure: a run
# that tested nothing must not pass for one.
# With --fat, /mnt/mmc is a throwaway FAT32 image instead, as a real card is: the whole suite runs
# on it, and so do the 8.3 short-name cases, which only a FAT filesystem can show (a tmpfs has no
# second names, so they are skipped there). The loop mount needs root; in a container, a privileged
# one with bash and busybox:
#   docker run --rm --privileged --network none -v "$PWD:/work:ro" IMAGE bash /work/test/wifi_transfer_cgi.sh --fat
# No network. Every payload is benign (markers under /tmp or the card tmpfs; nothing destructive).
set -u
CGI="$(cd "$(dirname "$0")/.." && pwd)/assets/web/q2cgi"
SH="busybox ash"
skip() { echo "SKIP: $1"; [ -z "${CI:-}" ]; exit; }  # exit 0; 1 under CI
command -v busybox >/dev/null || skip "busybox not found"
if [ "${1:-}" = --fat ]; then
  grep -q ' /mnt/mmc ' /proc/mounts 2>/dev/null && { echo "ERROR: --fat mounts its own image, and /mnt/mmc is already a mount"; exit 2; }
  IMG=$(mktemp /tmp/q2fat.XXXXXX); truncate -s 96M "$IMG"; mkdir -p /mnt/mmc 2>/dev/null
  { busybox mkdosfs "$IMG" >/dev/null 2>&1 && mount -o loop -t vfat "$IMG" /mnt/mmc 2>/dev/null; } || {  # busybox mkdosfs makes FAT32
    rm -f "$IMG"; echo "ERROR: cannot make and mount a FAT32 image at /mnt/mmc (--fat needs a privileged container)"; exit 2; }
  trap 'umount /mnt/mmc 2>/dev/null; rm -f "$IMG"' EXIT
elif ! grep -q ' /mnt/mmc ' /proc/mounts 2>/dev/null; then
  mkdir -p /mnt/mmc 2>/dev/null
  mount -t tmpfs tmpfs /mnt/mmc 2>/dev/null || skip "nothing is mounted at /mnt/mmc, and a tmpfs can't be (need root)"
  trap 'umount /mnt/mmc 2>/dev/null' EXIT
fi
FS=$(awk '$2=="/mnt/mmc"{t=$3} END{print t}' /proc/mounts)
echo "=== /mnt/mmc is a $FS"
rm -rf /mnt/mmc/* /mnt/mmc/.[!.]* 2>/dev/null
# Reproduce the Q2's applet set: a PATH with ONLY the applets the stock rootfs symlinks to busybox
# (notably NO bare `stat`), so a reliance on a tool the device lacks fails here too.
BB=$(command -v busybox)
DEV=/tmp/devbin; rm -rf "$DEV"; mkdir -p "$DEV"
for a in sh ash sed tr grep awk tail head cat ls date mkdir rm rmdir mv cut printf dd wc mountpoint realpath busybox; do ln -sf "$BB" "$DEV/$a"; done
DEVPATH="$DEV"
pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  PASS: $1"; }
bad() { fail=$((fail+1)); echo "  FAIL: $1"; }

# run <name> <method> <query> <contenttype> <bodyfile|-> -> sets $OUT and $RC
run() {
  local nm=$1 meth=$2 q=$3 ct=$4 bf=$5 len=0 src=/dev/null
  [ "$bf" != "-" ] && { len=$(stat -c %s "$bf"); src=$bf; }
  OUT=$(env -i PATH="$DEVPATH" SCRIPT_NAME="/$nm" REQUEST_METHOD="$meth" \
        QUERY_STRING="$q" CONTENT_TYPE="$ct" CONTENT_LENGTH="$len" \
        $SH "$CGI" < "$src" 2>/dev/null); RC=$?
}
# POST a urlencoded path in the body (realistic for create/delete)
post() { local nm=$1 p=$2; printf 'path=%s' "$p" > /tmp/b.$$; run "$nm" POST '' 'application/x-www-form-urlencoded' /tmp/b.$$; rm -f /tmp/b.$$; }
status() { printf '%s' "$OUT" | sed -n 's/^Status: \([0-9]*\).*/\1/p' | head -1; }
body()   { printf '%s' "$OUT" | awk 'b{print} /^\r?$/{b=1}'; }
refused() { [ "$(status)" = 400 ]; }
leaks() { printf '%s' "$OUT" | grep -q "$1"; }
# mp <file> <boundary> <path field> <file name> <content>: a one-file multipart body (printf escapes apply)
mp() { { printf -- "--$2\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n$3\r\n"
         printf -- "--$2\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"$4\"\r\nContent-Type: application/octet-stream\r\n\r\n$5\r\n--$2--\r\n"; } > "$1"; }
# held <name> <method> <query> <body>: as run, but stdin stays open for 8 s after the body, as the
# client's connection does while it waits for the answer; RC is 124 when the CGI was still reading
# after 4 s. No body sends no CONTENT_LENGTH, as thttpd does for a GET.
held() {
  local cl=(); [ -n "$4" ] && cl=("CONTENT_LENGTH=${#4}")
  OUT=$(timeout 4 env -i PATH="$DEVPATH" SCRIPT_NAME="/$1" REQUEST_METHOD="$2" QUERY_STRING="$3" \
        CONTENT_TYPE='application/x-www-form-urlencoded' "${cl[@]}" $SH "$CGI" 2>/dev/null < <(printf '%s' "$4"; sleep 8)); RC=$?
}

echo "=== busybox ash case [[:cntrl:]] support"; busybox ash -c 'case "$(printf a\\tb)" in *[[:cntrl:]]*) echo yes;; *) echo NO;; esac'
rm -rf /tmp/pwned* 2>/dev/null

echo; echo "############ SECURITY: command injection must NOT execute (both POST body and GET query, raw and encoded)"
post create 'path=/mnt/mmc/a$(id>/tmp/pwned1)b' ; # note: this double 'path=' is intentional dup test below; real injection:
printf 'path=/mnt/mmc/a$(id>/tmp/pwned1)b' > /tmp/b1; run create POST '' 'application/x-www-form-urlencoded' /tmp/b1
[ -e /tmp/pwned1 ] && bad "POST dollar-paren executed" || ok "POST \$() injection inert"
run create GET 'path=/mnt/mmc/a`id>/tmp/pwned2`b' '' -
[ -e /tmp/pwned2 ] && bad "GET backtick executed" || ok "GET backtick injection inert"
printf 'path=%%2Fmnt%%2Fmmc%%2Fa%%24%%28id%%3E%%2Ftmp%%2Fpwned3%%29b' > /tmp/b3; run create POST '' 'application/x-www-form-urlencoded' /tmp/b3
[ -e /tmp/pwned3 ] && bad "encoded injection executed" || ok "URL-encoded \$() injection inert (decode-then-argv)"

echo; echo "############ SECURITY: traversal / NUL / scope / duplicate-field must be rejected"
run download GET 'path=/mnt/mmc/../../etc/hostname' '' -
{ [ "$(status)" = 400 ] && ! printf '%s' "$OUT" | grep -q "$(cat /etc/hostname)"; } && ok "download ../ rejected, no leak" || bad "download ../ (status $(status))"
run download GET 'path=/mnt/mmc/..%2f..%2fetc%2fhostname' '' -
[ "$(status)" = 400 ] && ok "download encoded ../ rejected" || bad "download encoded ../ ($(status))"
run list GET 'path=/mnt/mmc/../..' '' -; [ "$(status)" = 400 ] && ok "list ../ rejected" || bad "list ../ ($(status))"
post create '/etc/evil' 2>/dev/null; printf 'path=/etc/evil' > /tmp/be; run create POST '' 'application/x-www-form-urlencoded' /tmp/be
[ "$(status)" = 400 ] && ok "non-/mnt/mmc path rejected" || bad "non-card path ($(status))"
run download GET 'path=/mnt/mmc/a%00.txt' '' -; [ "$(status)" = 400 ] && ok "NUL byte rejected" || bad "NUL ($(status))"
printf 'path=/mnt/mmc/ok&path=/etc/evil' > /tmp/bd; run create POST '' 'application/x-www-form-urlencoded' /tmp/bd
[ "$(status)" = 400 ] && ok "duplicate path field rejected" || bad "duplicate path not rejected ($(status))"

echo; echo "############ FUNCTIONAL: create / list / download / delete / upload (confined to /mnt/mmc)"
printf 'path=/mnt/mmc/My Folder' > /tmp/bf; run create POST '' 'application/x-www-form-urlencoded' /tmp/bf
[ -d "/mnt/mmc/My Folder" ] && ok "create 'My Folder' (POST body, literal space)" || bad "create plain folder ($(status))"
run create GET 'path=/mnt/mmc/From+Query' '' -
[ -d "/mnt/mmc/From Query" ] && ok "create via GET query (+ = space)" || bad "create via GET"
printf 'path=/mnt/mmc/M%%C3%%BAsica %%26 Caf%%C3%%A9 (2024)' > /tmp/bu; run create POST '' 'application/x-www-form-urlencoded' /tmp/bu
[ -d "/mnt/mmc/Música & Café (2024)" ] && ok "create Unicode+&() folder preserved (not mangled)" || bad "create special folder"

B=/tmp/mp_text; BND=----q2t
{ printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"hello world.txt\"\r\nContent-Type: text/plain\r\n\r\nSYNTHETIC-DATA\r\n--$BND--\r\n"; } > "$B"
run upload POST '' "multipart/form-data; boundary=$BND" "$B"
[ "$(cat "/mnt/mmc/hello world.txt" 2>/dev/null)" = "SYNTHETIC-DATA" ] && ok "upload text 'hello world.txt'" || bad "upload text ($(status); file='$(cat "/mnt/mmc/hello world.txt" 2>/dev/null | head -c40)')"

python3 -c "
bnd=b'----q2b'; content=bytes(range(256))*4 + b'\r\n--not-boundary--' + bytes(range(200))
open('/tmp/expect.bin','wb').write(content)
body=(b'--'+bnd+b'\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/sub\r\n'
      b'--'+bnd+b'\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"clip.bin\"\r\nContent-Type: application/octet-stream\r\n\r\n'+content+b'\r\n--'+bnd+b'--\r\n')
open('/tmp/mp_bin','wb').write(body)"
run upload POST '' 'multipart/form-data; boundary=----q2b' /tmp/mp_bin
cmp -s "/mnt/mmc/sub/clip.bin" /tmp/expect.bin && ok "upload BINARY byte-exact (NUL + CRLF + boundary-like content; nested dir)" || bad "upload binary mismatch (size $(stat -c %s /mnt/mmc/sub/clip.bin 2>/dev/null) vs $(stat -c %s /tmp/expect.bin))"

# (a) a $()-bearing filename WITHOUT a slash -> stored literally, no execution. Run with cwd /mnt/mmc
#     so that if $(touch pwned4y) DID execute it would drop /mnt/mmc/pwned4y, which we then check for.
B=/tmp/mp_evil; BND=----q2e
{ printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"x\$(touch pwned4y).mp3\"\r\nContent-Type: audio/mpeg\r\n\r\nAUDIOBYTES\r\n--$BND--\r\n"; } > "$B"
L=$(stat -c %s "$B")
OUT=$( cd /mnt/mmc && env -i PATH="$DEVPATH" SCRIPT_NAME=/upload REQUEST_METHOD=POST \
       CONTENT_TYPE="multipart/form-data; boundary=$BND" CONTENT_LENGTH="$L" busybox ash "$CGI" < "$B" 2>/dev/null )
[ -e /mnt/mmc/pwned4y ] && bad "upload filename injection executed" || ok "upload filename \$() injection inert"
[ "$(cat "/mnt/mmc/x\$(touch pwned4y).mp3" 2>/dev/null)" = "AUDIOBYTES" ] && ok "upload stored literal \$()-name file safely (preserved, not executed)" || bad "upload literal-name content wrong"
# (b) a filename with a slash (traversal) -> rejected, nothing written outside
B=/tmp/mp_trav; BND=----q2v
{ printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"../../../tmp/escaped\"\r\nContent-Type: application/octet-stream\r\n\r\nX\r\n--$BND--\r\n"; } > "$B"
run upload POST '' "multipart/form-data; boundary=$BND" "$B"
{ [ "$(status)" = 400 ] && [ ! -e /tmp/escaped ]; } && ok "upload traversal filename rejected, no escape" || bad "upload traversal filename ($(status), escaped=$([ -e /tmp/escaped ] && echo yes))"

run list GET 'path=/mnt/mmc' '' -
body | python3 -c "import json,sys; d=json.load(sys.stdin); assert isinstance(d,list) and len(d)>=4; ns=[e['name'] for e in d]; assert 'My Folder' in ns and 'hello world.txt' in ns; assert all('ctime' in e for e in d); assert any('size' in e for e in d); print('list ok:',len(d),'entries')" && ok "list valid JSON array with names/ctime/size" || bad "list JSON/fields wrong"

run download GET 'path=/mnt/mmc/hello world.txt' '' -
[ "$(body)" = "SYNTHETIC-DATA" ] && ok "download returns content" || bad "download content"
printf '%s' "$OUT" | grep -qi 'filename="hello world.txt"' && ok "download Content-Disposition" || bad "download header"

printf 'path=/mnt/mmc/My Folder' > /tmp/bdel; run delete POST '' 'application/x-www-form-urlencoded' /tmp/bdel
[ -d "/mnt/mmc/My Folder" ] && bad "delete left folder" || ok "delete removed folder"
printf 'path=/mnt/mmc' > /tmp/broot; run delete POST '' 'application/x-www-form-urlencoded' /tmp/broot
[ "$(status)" = 400 ] && ok "delete of card root refused" || bad "delete root ($(status))"

# a boundary containing regex metacharacters (+ .) must still parse (RS escaping)
B=/tmp/mp_meta; BND='x+y.z--Boundary9'
{ printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"meta.txt\"\r\nContent-Type: text/plain\r\n\r\nMETABODY\r\n--$BND--\r\n"; } > "$B"
run upload POST '' "multipart/form-data; boundary=$BND" "$B"
[ "$(cat "/mnt/mmc/meta.txt" 2>/dev/null)" = "METABODY" ] && ok "upload with regex-metachar boundary (+ .) parses" || bad "metachar boundary upload ($(status))"

# a large file must stream byte-exact
python3 -c "
import os
bnd=b'----q2big'; content=os.urandom(300000)
open('/tmp/expect.big','wb').write(content)
body=(b'--'+bnd+b'\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n'
      b'--'+bnd+b'\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"big.bin\"\r\nContent-Type: application/octet-stream\r\n\r\n'+content+b'\r\n--'+bnd+b'--\r\n')
open('/tmp/mp_big','wb').write(body)"
run upload POST '' 'multipart/form-data; boundary=----q2big' /tmp/mp_big
cmp -s "/mnt/mmc/big.bin" /tmp/expect.big && ok "upload 300 KB binary byte-exact (content beyond the 64 KB prefix)" || bad "upload large binary mismatch (size $(busybox stat -c %s /mnt/mmc/big.bin 2>/dev/null) vs 300000)"

echo; echo "############ REGRESSION: list emits directory paths WITH a trailing slash (client contract)"
run create GET 'path=/mnt/mmc/SubDir' '' -
run list GET 'path=/mnt/mmc/' '' -        # the real client lists the home path WITH a trailing slash
body | python3 -c "import json,sys
d=json.load(sys.stdin); e=[x for x in d if x['name']=='SubDir'][0]
assert 'size' not in e, 'dir must have no size'
assert e['path']=='/mnt/mmc/SubDir/', e['path']
print('dir path ok:', e['path'])" && ok "list dir path has one clean trailing slash" || bad "list dir trailing slash"
# The client builds the create path as <dirpath>+<name>; with the slash that is /mnt/mmc/SubDir/Child
run create GET 'path=/mnt/mmc/SubDir/Child' '' -
{ [ -d "/mnt/mmc/SubDir/Child" ] && [ ! -e "/mnt/mmc/SubDirChild" ]; } && ok "create inside a subfolder lands there (no mangled top-level sibling)" || bad "subfolder create mangled"

echo; echo "############ SECURITY: a card-root delete (trailing slash / dot / double slash) must NOT wipe the card"
printf keep > /mnt/mmc/keepme
post delete '/mnt/mmc/'
{ [ "$(status)" = 400 ] && [ -e /mnt/mmc/keepme ]; } && ok "delete '/mnt/mmc/' refused, card intact" || bad "delete trailing-slash root ($(status), keepme=$([ -e /mnt/mmc/keepme ] && echo yes))"
post delete '/mnt/mmc//'
{ [ "$(status)" = 400 ] && [ -e /mnt/mmc/keepme ]; } && ok "delete '/mnt/mmc//' refused, card intact" || bad "delete double-slash root ($(status))"
post delete '/mnt/mmc/.'
{ [ "$(status)" = 400 ] && [ -e /mnt/mmc/keepme ]; } && ok "delete '/mnt/mmc/.' refused, card intact" || bad "delete dot root ($(status))"

echo; echo "############ PRIVACY: hidden files are not listed and not directly reachable"
printf 'SECRETKEY' > /mnt/mmc/.scrobble.ini; mkdir -p /mnt/mmc/.spotify
run list GET 'path=/mnt/mmc' '' -
body | python3 -c "import json,sys
ns=[e['name'] for e in json.load(sys.stdin)]
assert '.scrobble.ini' not in ns and '.spotify' not in ns, ns
print('hidden not listed ok')" && ok "dotfiles (.scrobble.ini/.spotify) absent from listing" || bad "dotfiles listed"
run download GET 'path=/mnt/mmc/.scrobble.ini' '' -
{ [ "$(status)" = 400 ] && ! printf '%s' "$OUT" | grep -q SECRETKEY; } && ok "direct download of .scrobble.ini refused, no key leak" || bad "dotfile download ($(status))"
run list GET 'path=/mnt/mmc/.spotify' '' -
[ "$(status)" = 400 ] && ok "direct list of .spotify refused" || bad "dotfile list ($(status))"
post delete '/mnt/mmc/.scrobble.ini'
{ [ "$(status)" = 400 ] && [ -e /mnt/mmc/.scrobble.ini ]; } && ok "direct delete of .scrobble.ini refused" || bad "dotfile delete ($(status))"

echo; echo "############ PARITY: System Volume Information is hidden (as stock does)"
mkdir -p "/mnt/mmc/System Volume Information"
run list GET 'path=/mnt/mmc' '' -
body | python3 -c "import json,sys
ns=[e['name'] for e in json.load(sys.stdin)]
assert 'System Volume Information' not in ns, ns
print('svi hidden ok')" && ok "System Volume Information hidden from listing" || bad "SVI listed"

echo; echo "############ DoS: upload CONTENT_LENGTH must be a positive integer (no unbounded head -c)"
B=/tmp/mp_cl; BND=----q2cl
{ printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"cl.txt\"\r\nContent-Type: text/plain\r\n\r\nX\r\n--$BND--\r\n"; } > "$B"
clbad=0
for badlen in -1 abc ""; do
  O=$(env -i PATH="$DEVPATH" SCRIPT_NAME=/upload REQUEST_METHOD=POST \
      CONTENT_TYPE="multipart/form-data; boundary=$BND" CONTENT_LENGTH="$badlen" \
      busybox ash "$CGI" < "$B" 2>/dev/null)
  st=$(printf '%s' "$O" | sed -n 's/^Status: \([0-9]*\).*/\1/p' | head -1)
  [ "$st" = 400 ] || { bad "upload CONTENT_LENGTH='$badlen' not rejected (status '$st')"; clbad=1; }
done
[ "$clbad" = 0 ] && ok "upload rejects negative / non-numeric / empty CONTENT_LENGTH"

echo; echo "############ ROBUST: a crafted body with two file parts writes only the first file"
# The upload is streamed, so what follows the first file's headers is that file, up to the closing
# boundary: a second part is bytes of the first file, never a second file.
B=/tmp/mp_two; BND=----q2two
{ printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"one.txt\"\r\nContent-Type: text/plain\r\n\r\nAAA\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"two.txt\"\r\nContent-Type: text/plain\r\n\r\nBBB\r\n--$BND--\r\n"; } > "$B"
run upload POST '' "multipart/form-data; boundary=$BND" "$B"
[ ! -e /mnt/mmc/two.txt ] && ok "two-file-part upload writes no second file" || bad "two-part upload wrote two.txt ($(status))"
rm -f /mnt/mmc/one.txt

echo; echo "############ SYMLINK: no op may follow a symlink off the card (NTFS / reformatted card)"
mkdir -p /tmp/offcard; printf SECRET > /tmp/offcard/secret
ln -sf /etc /mnt/mmc/evil 2>/dev/null
ln -sf /tmp/offcard /mnt/mmc/elink 2>/dev/null
if [ -L /mnt/mmc/evil ]; then
  run list GET 'path=/mnt/mmc/evil' '' -
  [ "$(status)" = 400 ] && ok "list via off-card symlink refused" || bad "symlink list ($(status))"
  run download GET 'path=/mnt/mmc/evil/hostname' '' -
  { [ "$(status)" = 400 ] && ! printf '%s' "$OUT" | grep -q "$(cat /etc/hostname)"; } && ok "download through off-card symlink refused, no leak" || bad "symlink download ($(status))"
  post delete '/mnt/mmc/evil/zzz_nonexistent'      # confine resolves the existing parent (/etc) and refuses before rm
  [ "$(status)" = 400 ] && ok "delete through off-card symlink refused (no rm off-card)" || bad "symlink delete ($(status))"
  run create GET 'path=/mnt/mmc/elink/plantdir' '' -
  { [ "$(status)" = 400 ] && [ ! -e /tmp/offcard/plantdir ]; } && ok "create through off-card symlink refused (nothing created off-card)" || bad "symlink create ($(status))"
  B=/tmp/mp_sl; BND=----q2sl
  { printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/elink\r\n"
    printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"planted.mp3\"\r\nContent-Type: audio/mpeg\r\n\r\nX\r\n--$BND--\r\n"; } > "$B"
  run upload POST '' "multipart/form-data; boundary=$BND" "$B"
  { [ "$(status)" = 400 ] && [ ! -e /tmp/offcard/planted.mp3 ]; } && ok "upload through off-card symlink refused, nothing written off-card" || bad "symlink upload ($(status))"
  [ "$(cat /tmp/offcard/secret)" = SECRET ] && ok "off-card data untouched by all symlink ops" || bad "off-card data changed!"
  rm -f /mnt/mmc/evil /mnt/mmc/elink
else
  echo "  SKIP: this filesystem did not allow creating a symlink (cannot exercise confine here)"
fi
rm -rf /tmp/offcard

echo; echo "############ SYMLINK: nor to a hidden entry elsewhere on the card"
# hidden() holds a component against the dot-entries of its own folder, so a link in ANOTHER folder
# to a hidden one passed: through it the folder was listed, its files read, a file put in it and
# its contents deleted. confine() now makes the same test of the place a link leads to.
rm -rf /mnt/mmc/.spotify /mnt/mmc/Links /mnt/mmc/Plain
mkdir -p /mnt/mmc/.spotify/cache /mnt/mmc/Links /mnt/mmc/Plain
printf 'SECRETKEY' > /mnt/mmc/.scrobble.ini; printf 'plain' > /mnt/mmc/Plain/plain.txt
printf 'LAUNCHER' > /mnt/mmc/.spotify/run; printf 'LOGIN' > /mnt/mmc/.spotify/cache/credentials.json
ln -s /mnt/mmc/.spotify /mnt/mmc/Links/peek 2>/dev/null       # absolute, to a hidden folder
ln -s ../.scrobble.ini /mnt/mmc/Links/key.txt 2>/dev/null     # relative, to a hidden file
ln -s /mnt/mmc/Links/peek /mnt/mmc/Plain/chain 2>/dev/null    # a link to the first link
ln -s .spotify /mnt/mmc/beside 2>/dev/null                    # in the hidden folder's own folder
ln -s /mnt/mmc/Plain /mnt/mmc/Links/shortcut 2>/dev/null      # to an ordinary folder
if [ -L /mnt/mmc/Links/peek ]; then
  run list GET 'path=/mnt/mmc/Links/peek' '' -
  { refused && ! leaks cache; } && ok "list through a link to a hidden folder refused" || bad "hidden-link list ($(status))"
  run download GET 'path=/mnt/mmc/Links/peek/cache/credentials.json' '' -
  { refused && ! leaks LOGIN; } && ok "download through it refused, no leak" || bad "hidden-link download ($(status))"
  run download GET 'path=/mnt/mmc/Links/key.txt' '' -
  { refused && ! leaks SECRETKEY; } && ok "download of a link to a hidden file refused, no leak" || bad "hidden-file link download ($(status))"
  run list GET 'path=/mnt/mmc/Plain/chain' '' -
  { refused && ! leaks cache; } && ok "a link to that link refused" || bad "hidden-link chain ($(status))"
  run create GET 'path=/mnt/mmc/Links/peek/planted' '' -
  { refused && [ ! -e /mnt/mmc/.spotify/planted ]; } && ok "create through it refused, nothing made" || bad "hidden-link create ($(status))"
  mp /tmp/mp_hl ----q2hl /mnt/mmc/Links/peek run ATTACKER
  run upload POST '' 'multipart/form-data; boundary=----q2hl' /tmp/mp_hl
  { refused && [ "$(cat /mnt/mmc/.spotify/run)" = LAUNCHER ]; } && ok "upload through it refused, the launcher untouched" || bad "hidden-link upload ($(status), run='$(cat /mnt/mmc/.spotify/run)')"
  post delete '/mnt/mmc/Links/peek/cache'
  { refused && [ -f /mnt/mmc/.spotify/cache/credentials.json ]; } && ok "delete through it refused, nothing deleted" || bad "hidden-link delete ($(status))"
  run list GET 'path=/mnt/mmc/beside' '' -
  { refused && ! leaks cache; } && ok "a link beside the hidden folder refused" || bad "hidden-link beside ($(status))"
  run download GET 'path=/mnt/mmc/Links/shortcut/plain.txt' '' -
  [ "$(body)" = plain ] && ok "a link to an ordinary folder on the card still works" || bad "ordinary link ($(status))"
else
  echo "  SKIP: this filesystem did not allow creating a symlink: these 9 cases need one"
fi
rm -rf /mnt/mmc/Links /mnt/mmc/Plain /mnt/mmc/beside

echo; echo "############ HARD LINK: a hidden file's second name in another folder cannot change the hidden file"
# A card with hard links (NTFS) can hold a hidden file under an ordinary name in another folder.
# That name is left to work as the ordinary file its owner made it: the page cannot make such a
# link, and telling one from any other file would take a walk of the whole card at every request.
# What has to hold is that the hidden file cannot be changed or removed through it: an upload
# renames a new file over the name and a delete unlinks the name, and neither touches the file.
mkdir -p /mnt/mmc/HardLinks; printf 'SECRETKEY' > /mnt/mmc/.scrobble.ini
if ln /mnt/mmc/.scrobble.ini /mnt/mmc/HardLinks/key.txt 2>/dev/null; then
  mp /tmp/mp_hd ----q2hd /mnt/mmc/HardLinks key.txt ATTACKER
  run upload POST '' 'multipart/form-data; boundary=----q2hd' /tmp/mp_hd
  { [ "$(cat /mnt/mmc/.scrobble.ini)" = SECRETKEY ] && [ "$(cat /mnt/mmc/HardLinks/key.txt)" = ATTACKER ]; } && ok "upload over a hard link to a hidden file replaces the name, the hidden file untouched" || bad "hard-link upload ($(status), .scrobble.ini='$(cat /mnt/mmc/.scrobble.ini 2>/dev/null)')"
  ln -f /mnt/mmc/.scrobble.ini /mnt/mmc/HardLinks/key.txt
  post delete '/mnt/mmc/HardLinks/key.txt'
  { [ ! -e /mnt/mmc/HardLinks/key.txt ] && [ "$(cat /mnt/mmc/.scrobble.ini 2>/dev/null)" = SECRETKEY ]; } && ok "delete of a hard link to a hidden file removes the name, the hidden file stays" || bad "hard-link delete ($(status), .scrobble.ini='$(cat /mnt/mmc/.scrobble.ini 2>/dev/null)')"
else
  echo "  SKIP: this filesystem has no hard links: these 2 cases need one"
fi
rm -rf /mnt/mmc/HardLinks

echo; echo "############ PRIVACY+: upload must not create a hidden (leading-dot) file nor clobber a dir"
printf 'OLD' > /mnt/mmc/.scrobble.ini        # known value; the earlier direct-delete was refused so it persists
B=/tmp/mp_dot; BND=----q2dot
{ printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\".scrobble.ini\"\r\nContent-Type: text/plain\r\n\r\nATTACKER\r\n--$BND--\r\n"; } > "$B"
run upload POST '' "multipart/form-data; boundary=$BND" "$B"
{ [ "$(status)" = 400 ] && [ "$(cat /mnt/mmc/.scrobble.ini)" = OLD ]; } && ok "upload of a leading-dot filename refused (.scrobble.ini not overwritten)" || bad "upload dotfile overwrite ($(status), now='$(cat /mnt/mmc/.scrobble.ini 2>/dev/null)')"
mkdir -p "/mnt/mmc/ClashDir"
B=/tmp/mp_cd; BND=----q2cd
{ printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"ClashDir\"\r\nContent-Type: application/octet-stream\r\n\r\nX\r\n--$BND--\r\n"; } > "$B"
run upload POST '' "multipart/form-data; boundary=$BND" "$B"
{ [ "$(status)" = 400 ] && [ -d /mnt/mmc/ClashDir ]; } && ok "upload onto an existing directory name refused" || bad "upload dir-clash ($(status))"

echo; echo "############ PATH ROBUSTNESS: sibling-prefix, double-encoding, mid-name dot"
run create GET 'path=/mnt/mmcEVIL' '' -
{ [ "$(status)" = 400 ] && [ ! -e /mnt/mmcEVIL ]; } && ok "sibling-prefix /mnt/mmcEVIL rejected" || bad "sibling-prefix ($(status))"
run create GET 'path=/mnt/mmc/%252e%252e' '' -
[ -d "/mnt/mmc/%2e%2e" ] && ok "double-encoded dots stay literal, no traversal" || bad "double-encoding (dir not literal)"
run create GET 'path=/mnt/mmc/2024.Album' '' -
[ -d "/mnt/mmc/2024.Album" ] && ok "mid-name dot folder created (no dot-rule false positive)" || bad "mid-name dot wrongly rejected"

echo; echo "############ MULTIPART EDGES: zero-byte, content ending in the boundary token, quoted boundary"
B=/tmp/mp_zero; BND=----q2z
{ printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"zero.bin\"\r\nContent-Type: application/octet-stream\r\n\r\n\r\n--$BND--\r\n"; } > "$B"
run upload POST '' "multipart/form-data; boundary=$BND" "$B"
{ [ -f /mnt/mmc/zero.bin ] && [ ! -s /mnt/mmc/zero.bin ]; } && ok "zero-byte upload stored as an empty file" || bad "zero-byte upload ($(status))"
python3 -c "
bnd=b'----q2end'; content=b'AUDIO--'+bnd        # content literally ENDS with the boundary token
open('/tmp/expect.end','wb').write(content)
body=(b'--'+bnd+b'\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n'
      b'--'+bnd+b'\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"ends.bin\"\r\nContent-Type: application/octet-stream\r\n\r\n'+content+b'\r\n--'+bnd+b'--\r\n')
open('/tmp/mp_end','wb').write(body)"
run upload POST '' 'multipart/form-data; boundary=----q2end' /tmp/mp_end
cmp -s /mnt/mmc/ends.bin /tmp/expect.end && ok "content ending in the boundary token is byte-exact" || bad "content-ends-in-boundary mismatch"
B=/tmp/mp_q; BND=----q2quoted
{ printf -- "--$BND\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n"
  printf -- "--$BND\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"q.txt\"\r\nContent-Type: text/plain\r\n\r\nQBODY\r\n--$BND--\r\n"; } > "$B"
run upload POST '' "multipart/form-data; boundary=\"$BND\"" "$B"
[ "$(cat /mnt/mmc/q.txt 2>/dev/null)" = QBODY ] && ok "quoted boundary accepted (RFC-legal)" || bad "quoted boundary ($(status))"

echo; echo "############ REQUEST BODY: exactly CONTENT_LENGTH bytes, never to end-of-file"
# thttpd gives a CGI the client's socket as stdin unless it already holds part of the body, and the
# socket stays open until the answer: a CGI that reads to end-of-file never answers.
held create POST '' 'path=/mnt/mmc/HeldPost'
{ [ "$RC" != 124 ] && [ -d /mnt/mmc/HeldPost ]; } && ok "POST create answers while the connection stays open" || bad "POST create waited for end-of-file (rc $RC)"
held create GET 'path=/mnt/mmc/HeldGet' ''
{ [ "$RC" != 124 ] && [ -d /mnt/mmc/HeldGet ]; } && ok "GET create (no body, no CONTENT_LENGTH) answers while the connection stays open" || bad "GET create waited for end-of-file (rc $RC)"
held delete POST '' 'path=/mnt/mmc/HeldPost'
{ [ "$RC" != 124 ] && [ ! -e /mnt/mmc/HeldPost ]; } && ok "POST delete answers while the connection stays open" || bad "POST delete waited for end-of-file (rc $RC)"
printf 'path=/mnt/mmc/CutHereNOT-THE-BODY' > /tmp/bcut      # CONTENT_LENGTH covers "path=/mnt/mmc/CutHere" only
OUT=$(env -i PATH="$DEVPATH" SCRIPT_NAME=/create REQUEST_METHOD=POST CONTENT_TYPE='application/x-www-form-urlencoded' CONTENT_LENGTH=21 $SH "$CGI" < /tmp/bcut 2>/dev/null)
{ [ -d /mnt/mmc/CutHere ] && [ ! -e /mnt/mmc/CutHereNOT-THE-BODY ]; } && ok "bytes past CONTENT_LENGTH are not read as the body" || bad "the body was not cut at CONTENT_LENGTH ($(status))"
printf 'path=/mnt/mmc/HugeLength' > /tmp/bhuge
OUT=$(env -i PATH="$DEVPATH" SCRIPT_NAME=/create REQUEST_METHOD=POST CONTENT_TYPE='application/x-www-form-urlencoded' CONTENT_LENGTH=99999999999999999999 $SH "$CGI" < /tmp/bhuge 2>/dev/null)
[ -d /mnt/mmc/HugeLength ] && ok "a CONTENT_LENGTH too long for the shell to compare is capped at 64 KB, not an error" || bad "huge CONTENT_LENGTH ($(status))"

echo; echo "############ MULTIPART NEWLINES: content is kept to the byte whatever newlines it holds"
# busybox awk takes RS="\0" as an empty RS (paragraph mode): a record's leading newlines are dropped,
# so a newline among the content's last two bytes moved the content's end and the file lost bytes.
NL="nl_last_but_one nl_two_at_end nl_one_at_end nl_at_start nl_only blank_lines_then_binary separator_byte"
python3 -c "
import os
cases = {'nl_last_but_one': b'line one\nX', 'nl_two_at_end': b'ends in a blank line\n\n', 'nl_one_at_end': b'ends in a newline\n',
         'nl_at_start': b'\n\nstarts with blank lines', 'nl_only': b'\n\n\n',
         'blank_lines_then_binary': b'a\n\nb\n\n\n' + os.urandom(70000) + b'\n\x00',
         'separator_byte': b'\x1e\nholds the byte the parser splits records at\x1e\x00\n\x1e'}
assert sorted(cases) == sorted('$NL'.split())
for name, content in cases.items():
    bnd = b'----q2nl'
    open('/tmp/expect.' + name, 'wb').write(content)
    open('/tmp/mp_' + name, 'wb').write(
        b'--' + bnd + b'\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n--' + bnd
        + b'\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"' + name.encode() + b'.bin\"\r\n'
        + b'Content-Type: application/octet-stream\r\n\r\n' + content + b'\r\n--' + bnd + b'--\r\n')"
for c in $NL; do
  run upload POST '' 'multipart/form-data; boundary=----q2nl' /tmp/mp_$c
  cmp -s /mnt/mmc/$c.bin /tmp/expect.$c && ok "upload byte-exact: $c" || bad "upload $c differs ($(stat -c %s /mnt/mmc/$c.bin 2>/dev/null) bytes stored, $(stat -c %s /tmp/expect.$c) sent; status $(status))"
done

echo; echo "############ BINARY INTEGRITY: an upload neither changes, drops nor adds a byte of the content"
# The header lines before the content are read with ash's read and the closing boundary is cut off the
# end, so the bytes that could move either end (NUL, \1, tab, LF, CR, \036, space, '-', DEL, 0x80,
# 0xff) are put alone, at the content's start, and as every pair at its end. Then random bytes.
MX=/tmp/mx; rm -rf "$MX"; mkdir -p "$MX"
python3 -c "
import random
special = [0x00, 0x01, 0x09, 0x0a, 0x0d, 0x1e, 0x20, 0x2d, 0x7f, 0x80, 0xff]
bnd = b'----q2mx'
cases = {'whole_closing_line_inside': b'before\r\n--' + bnd + b'--\r\nafter'}
for a in special:
    cases['one_%02x' % a] = bytes([a])
    cases['start_%02x' % a] = bytes([a, a]) + b'then ordinary text'
    for b in special:
        cases['end_%02x_%02x' % (a, b)] = b'ordinary text then' + bytes([a, b])
rnd = random.Random(20261112)
for i in range(40):
    cases['random_%02d' % i] = bytes(rnd.getrandbits(8) for _ in range(rnd.choice([1, 2, 3, 7, 64, 500, 3000])))
for name, content in cases.items():
    open('$MX/' + name + '.expect', 'wb').write(content)
    open('$MX/' + name + '.body', 'wb').write(
        b'--' + bnd + b'\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/mx\r\n--' + bnd
        + b'\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"' + name.encode() + b'\"\r\n'
        + b'Content-Type: application/octet-stream\r\n\r\n' + content + b'\r\n--' + bnd + b'--\r\n')"
mx() { # <group> <what it is>: every body of the group uploaded and compared with what was sent
  local n=0 wrong="" f c
  for f in "$MX/$1"*.body; do
    c=${f##*/}; c=${c%.body}; n=$((n+1))
    run upload POST '' 'multipart/form-data; boundary=----q2mx' "$f"
    cmp -s "/mnt/mmc/mx/$c" "$MX/$c.expect" || wrong="$wrong $c($(status))"
  done
  [ -z "$wrong" ] && ok "byte-exact: $2 ($n uploads)" || bad "not byte-exact: $2:$wrong"
}
mx one_ "a one-byte file of each special byte"
mx start_ "content starting with each special byte, twice"
mx end_ "content ending in every pair of special bytes"
mx whole_ "content holding a whole closing-boundary line"
mx random_ "random bytes, 1 to 3000 long"
# The ending must be exactly CRLF and the closing boundary, or the upload is refused. Here the closing boundary follows a bare LF, not CRLF: taking
# the two bytes before the boundary for a CRLF would store "AB" for "ABC".
printf -- "------q2lf\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n/mnt/mmc/\r\n------q2lf\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"lf.txt\"\r\n\r\nABC\n------q2lf--\r\n" > /tmp/mp_lf
run upload POST '' 'multipart/form-data; boundary=----q2lf' /tmp/mp_lf
{ [ "$(status)" = 400 ] && [ ! -e /mnt/mmc/lf.txt ]; } && ok "a closing boundary after a bare LF is refused, not stored a byte short" || bad "bare-LF closing boundary ($(status), stored '$(cat /mnt/mmc/lf.txt 2>/dev/null)')"

echo; echo "############ MULTIPART VALUES: a bare LF in a value cannot pass for the request's framing"
# Each field is a line of its own, so a bare LF in a value ends the line early and the next line is
# not the boundary. In #342's awk parser, which handed the shell a line per value, in the path field,
# "F -0" passed for the offset and the sender's whole envelope was stored as the file; an offset
# with a leading zero was checked as decimal and cut as octal; "F" and "N" lines stood in for a file
# part that was not there. In the file name, a "P" line named the folder. Only the sender's own
# bytes each time, but not the file that was sent.
python3 -c "
bnd = b'----q2fv'
hp = b'--' + bnd + b'\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n'
hf = lambda n: b'--' + bnd + b'\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"' + n + b'\"\r\nContent-Type: application/octet-stream\r\n\r\n'
end = b'--' + bnd + b'--\r\n'
open('/tmp/fv_offset_minus_zero', 'wb').write(hp + b'/mnt/mmc/fv\nF -0\r\n' + hf(b'a.txt') + b'CONTENT\r\n' + end + b'\r\n')
open('/tmp/fv_no_file_part', 'wb').write(hp + b'/mnt/mmc/fv\nF %d\nN b.txt' % len(hp) + b'\r\n' + end)
for k in range(400):                        # pad the path until the content's offset reads differently as octal
    off = 0
    for _ in range(8):
        pre = hp + b'/mnt/mmc/fv' + b'/' * k + b'\nF 0%d\r\n' % off + hf(b'c.txt')
        if len(pre) == off: break
        off = len(pre)
    if len(pre) == off and set(str(off)) <= set('01234567') and int(str(off), 8) != off: break
else: raise SystemExit('no offset that reads as octal')
open('/tmp/fv_offset_leading_zero', 'wb').write(pre + b'CONTENT\r\n' + end)
open('/tmp/fv_path_from_file_name', 'wb').write(hf(b'd.txt\nP /mnt/mmc/fv') + b'CONTENT\r\n' + end)"
for c in offset_minus_zero no_file_part offset_leading_zero path_from_file_name; do
  rm -rf /mnt/mmc/fv
  run upload POST '' 'multipart/form-data; boundary=----q2fv' /tmp/fv_$c
  { refused && [ ! -e /mnt/mmc/fv ]; } && ok "a line written through a value is refused, nothing stored: $c" || bad "a value wrote a parser line ($c): status $(status), stored: $(ls /mnt/mmc/fv 2>/dev/null | tr '\n' ' ')"
done

echo; echo "############ FAT 8.3 SHORT NAMES: a hidden entry is refused by its second name too"
# FAT32 keeps an 8.3 short name beside every long name and the kernel resolves either, so the dot
# rule alone let .scrobble.ini be read, replaced and deleted as SCROBB~1.INI. q2cgi's hidden()
# compares files, not names. Only a FAT mount has such names: run with --fat (see the header).
if [ "$FS" = vfat ]; then
  rm -rf /mnt/mmc/.scrobble.ini /mnt/mmc/.spotify
  printf 'SECRETKEY' > /mnt/mmc/.scrobble.ini; printf 'LISTENS' > /mnt/mmc/.scrobbler.log
  mkdir -p /mnt/mmc/.spotify/cache /mnt/mmc/Albums/.private
  printf 'LAUNCHER' > /mnt/mmc/.spotify/run; printf 'LOGIN' > /mnt/mmc/.spotify/cache/credentials.json
  printf 'NOTES' > /mnt/mmc/Albums/.private/notes.txt; printf 'song' > "/mnt/mmc/Albums/A Long Song Name.flac"
  short() { # <long path> <base> <extension>: the short name the kernel resolves to that entry, by trying the numeric tails
    local i; for i in 1 2 3 4 5 6 7 8 9; do [ "${1%/*}/$2~$i$3" -ef "$1" ] && { echo "$2~$i$3"; return; }; done
  }
  A_INI=$(short /mnt/mmc/.scrobble.ini SCROBB .INI); A_LOG=$(short /mnt/mmc/.scrobbler.log SCROBB .LOG)
  A_SPOT=$(short /mnt/mmc/.spotify SPOTIF ''); A_PRIV=$(short /mnt/mmc/Albums/.private PRIVAT '')
  A_SONG=$(short "/mnt/mmc/Albums/A Long Song Name.flac" ALONGS .FLA)
  if [ -n "$A_INI" ] && [ -n "$A_LOG" ] && [ -n "$A_SPOT" ] && [ -n "$A_PRIV" ] && [ -n "$A_SONG" ]; then
    ok "the kernel resolves short names here ($A_INI, $A_LOG, $A_SPOT, $A_PRIV, $A_SONG)"
  else bad "no short name found for a long-named entry: the cases below prove nothing"; fi
  run download GET "path=/mnt/mmc/$A_INI" '' -
  { refused && ! leaks SECRETKEY; } && ok "download of .scrobble.ini by its short name refused, no leak" || bad "short-name download ($(status))"
  run download GET "path=/mnt/mmc/$(printf '%s' "$A_INI" | tr 'A-Z' 'a-z')" '' -
  { refused && ! leaks SECRETKEY; } && ok "the same in lower case refused" || bad "lower-case short-name download ($(status))"
  run download GET "path=/mnt/mmc/$A_INI." '' -              # FAT drops a name's trailing dots
  { refused && ! leaks SECRETKEY; } && ok "the same with a trailing dot refused" || bad "trailing-dot short-name download ($(status))"
  run list GET "path=/mnt/mmc/$A_SPOT" '' -
  { refused && ! leaks cache; } && ok "list of .spotify by its short name refused" || bad "short-name list ($(status))"
  run download GET "path=/mnt/mmc/$A_SPOT/cache/credentials.json" '' -
  { refused && ! leaks LOGIN; } && ok "download from inside .spotify by its short name refused, no leak" || bad "short-name folder download ($(status))"
  run download GET "path=/mnt/mmc/Albums/$A_PRIV/notes.txt" '' -
  { refused && ! leaks NOTES; } && ok "a hidden folder inside a folder refused by its short name" || bad "nested short-name download ($(status))"
  run create GET "path=/mnt/mmc/$A_SPOT/planted" '' -
  { refused && [ ! -e /mnt/mmc/.spotify/planted ]; } && ok "create inside .spotify by its short name refused, nothing made" || bad "short-name create ($(status))"
  post delete "/mnt/mmc/$A_LOG"
  { refused && [ "$(cat /mnt/mmc/.scrobbler.log)" = LISTENS ]; } && ok "delete of .scrobbler.log by its short name refused" || bad "short-name delete ($(status))"
  post delete "/mnt/mmc/$A_SPOT"
  { refused && [ -f /mnt/mmc/.spotify/cache/credentials.json ]; } && ok "delete of .spotify by its short name refused" || bad "short-name folder delete ($(status))"
  mp /tmp/mp_a1 ----q2a1 "/mnt/mmc/$A_SPOT" run ATTACKER
  run upload POST '' 'multipart/form-data; boundary=----q2a1' /tmp/mp_a1
  { refused && [ "$(cat /mnt/mmc/.spotify/run)" = LAUNCHER ]; } && ok "upload into .spotify by its short name refused, its launcher untouched" || bad "short-name upload ($(status), run='$(cat /mnt/mmc/.spotify/run)')"
  mp /tmp/mp_a2 ----q2a2 /mnt/mmc/ "$A_INI" ATTACKER
  run upload POST '' 'multipart/form-data; boundary=----q2a2' /tmp/mp_a2
  { refused && [ "$(cat /mnt/mmc/.scrobble.ini)" = SECRETKEY ]; } && ok "upload of a file named as .scrobble.ini's short name refused, the file untouched" || bad "short-name file upload ($(status), now='$(cat /mnt/mmc/.scrobble.ini)')"
  # files are compared, not names: ordinary entries keep working by their short name and in another case
  run download GET "path=/mnt/mmc/Albums/$A_SONG" '' -
  [ "$(body)" = song ] && ok "an ordinary file still downloads by its own short name" || bad "ordinary short name ($(status))"
  run download GET 'path=/mnt/mmc/ALBUMS/a long song name.FLAC' '' -
  [ "$(body)" = song ] && ok "an ordinary file still downloads in another case" || bad "another case ($(status))"
  run create GET 'path=/mnt/mmc/albums/New+Sub' '' -
  [ -d "/mnt/mmc/Albums/New Sub" ] && ok "create under another case of a folder lands in that folder" || bad "create under another case ($(status))"
  mp /tmp/mp_a3 ----q2a3 /mnt/mmc/ALBUMS 'a long song name.flac' newer
  run upload POST '' 'multipart/form-data; boundary=----q2a3' /tmp/mp_a3
  [ "$(cat "/mnt/mmc/Albums/A Long Song Name.flac")" = newer ] && ok "upload over an ordinary file in another case replaces it" || bad "upload in another case ($(status))"
else
  echo "  SKIP: /mnt/mmc is a $FS, which keeps no short names: run with --fat (see the header) for these 16 cases"
fi

echo; echo "############ DEVICE APPLET: no dependency on 'stat' (absent on device busybox 1.31.1)"
grep -qE 'stat -c|busybox stat' "$CGI" && bad "q2cgi invokes the 'stat' applet, which the device busybox lacks" || ok "no 'stat' applet dependency (size via ls -ln, mtime via date -r)"

echo; echo "############ FILESYSTEM BOUNDARY"
ls /tmp/pwned* 2>/dev/null && bad "markers leaked" || ok "no injection markers anywhere"

echo; echo "============ RESULT: $pass passed, $fail failed"; [ "$fail" = 0 ]
