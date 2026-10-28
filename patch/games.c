/* Games (docs/internals.md#games), iPod: the Library's Games, Sudoku, Vortex, Solitaire and Snake.
 * The logic comes first, host-tested (test/peq.py games_check); the page below draws both on a bare
 * sheet (games_paint) and takes a game's keys in ringnav() before stock does (games_key). The state
 * is on the heap only while the page is open; the scratch holds its pointer. */
#include "offsets.inc"
#include "peq.h"
#ifndef PEQ_HOST
#include "playback.h"
#include "stock.h"
#endif
#if IPOD || defined PEQ_HOST

#ifdef PEQ_HOST /* playback.c's and coverflow.c's on the device */
unsigned games_seed = 1;
unsigned rand_below(unsigned n) {
    games_seed ^= games_seed << 13;
    games_seed ^= games_seed >> 17;
    games_seed ^= games_seed << 5;
    return games_seed % n;
}
unsigned hash_bytes(unsigned h, const unsigned char *s, unsigned n) {
    for (unsigned i = 0; i < n; ++i) h = (h ^ s[i]) * 16777619u;
    return h;
}
#endif

/* A save's first word is its magic and its last the FNV-1a of the rest; anything else reads as
 * no save, so a fresh game. */
int games_ok(const void *b, unsigned n, unsigned magic) {
    unsigned head, tail;
    memcpy(&head, b, 4);
    memcpy(&tail, (const char *)b + n - 4, 4);
    return head == magic && tail == hash_bytes(FNV_SEED, b, n - 4);
}
void games_seal(void *b, unsigned n, unsigned magic) {
    memcpy(b, &magic, 4);
    unsigned h = hash_bytes(FNV_SEED, b, n - 4);
    memcpy((char *)b + n - 4, &h, 4);
}

/* 0..n-1 in a random order. */
static void deal(unsigned char *p, int n) {
    for (int i = 0; i < n; ++i) p[i] = (unsigned char)i;
    for (int i = n - 1; i > 0; --i) {
        int j = (int)rand_below((unsigned)i + 1);
        unsigned char t = p[i];
        p[i] = p[j];
        p[j] = t;
    }
}

/* Sudoku: a cell is its digit (0 blank) and CLUE. The bundled puzzles, 4 bits a cell, each with
 * exactly one solution (the host test proves it); a new game is one of them relabelled, its rows
 * and columns shuffled within bands and stacks, the bands and stacks shuffled and maybe transposed,
 * which keeps the solution unique.
 * ponytail: 24 fixed puzzles, no generator, no pencil marks or hints. */
#define SUD_MAGIC 0x31445553u /* "SUD1" */
#define CLUE 16
struct sudoku {
    unsigned magic;
    unsigned char cell[81], cur, pad[2];
    unsigned sum;
};
static const char puzzles[][42] = {
    "\x20\x00\x50\x60\x40\x00\x80\x21\x00\x03\x00\x00\x00\x81\x00\x30\x00\x07\x00\x09\x68\x05\x10"
    "\x00\x00\x10\x63\x00\x02\x40\x70\x04\x00\x69\x00\x10\x35\x00\x80\x00\x00",
    "\x09\x00\x06\x00\x30\x02\x50\x00\x09\x60\x00\x03\x50\x08\x00\x00\x06\x30\x20\x40\x71\x80\x00"
    "\x59\x00\x01\x04\x40\x00\x02\x90\x00\x81\x03\x02\x00\x50\x00\x00\x00\x80",
    "\x00\x00\x30\x00\x06\x00\x47\x90\x05\x30\x02\x86\x00\x00\x00\x00\x05\x23\x00\x00\x51\x00\x60"
    "\x00\x00\x30\x10\x20\x10\x98\x05\x05\x00\x00\x02\x70\x70\x00\x10\x86\x00",
    "\x05\x40\x00\x00\x99\x10\x00\x00\x00\x82\x00\x07\x54\x14\x00\x12\x57\x80\x60\x00\x70\x00\x01"
    "\x02\x00\x60\x00\x00\x03\x00\x02\x80\x00\x04\x00\x05\x20\x08\x00\x60\x00",
    "\x89\x04\x01\x60\x00\x00\x06\x00\x00\x20\x05\x00\x00\x30\x21\x95\x00\x30\x65\x00\x00\x00\x04"
    "\x00\x00\x21\x00\x50\x30\x00\x00\x99\x80\x00\x00\x00\x17\x40\x05\x82\x00",
    "\x49\x00\x00\x20\x02\x00\x08\x01\x00\x71\x03\x29\x80\x60\x60\x00\x10\x00\x87\x30\x02\x50\x00"
    "\x00\x04\x00\x00\x00\x70\x08\x05\x10\x01\x00\x00\x20\x08\x00\x06\x03\x00",
    "\x00\x06\x00\x00\x30\x57\x19\x86\x40\x00\x05\x07\x89\x10\x20\x05\x00\x00\x10\x04\x00\x50\x07"
    "\x80\x00\x00\x14\x03\x00\x10\x90\x05\x00\x80\x00\x30\x06\x00\x00\x00\x00",
    "\x60\x50\x00\x30\x00\x07\x04\x69\x00\x09\x00\x20\x00\x00\x01\x87\x00\x60\x00\x80\x00\x07\x00"
    "\x62\x90\x10\x40\x30\x06\x00\x08\x20\x00\x23\x07\x04\x00\x00\x09\x00\x10",
    "\x40\x07\x02\x00\x80\x07\x05\x80\x42\x60\x00\x00\x00\x09\x06\x00\x08\x00\x00\x00\x80\x20\x00"
    "\x21\x00\x40\x53\x76\x05\x00\x02\x00\x00\x00\x00\x00\x10\x50\x46\x78\x00",
    "\x00\x09\x00\x00\x00\x00\x00\x07\x00\x00\x00\x20\x49\x80\x94\x60\x80\x00\x80\x02\x40\x05\x00"
    "\x00\x37\x96\x04\x03\x50\x60\x10\x20\x00\x00\x58\x00\x00\x64\x90\x03\x00",
    "\x08\x00\x00\x00\x00\x70\x00\x00\x00\x00\x27\x40\x91\x00\x14\x00\x07\x00\x03\x50\x06\x00\x28"
    "\x09\x00\x00\x05\x10\x80\x53\x60\x93\x97\x00\x14\x00\x00\x69\x00\x00\x00",
    "\x09\x00\x06\x80\x06\x70\x00\x40\x00\x40\x80\x19\x70\x30\x00\x10\x05\x00\x13\x50\x60\x04\x00"
    "\x00\x90\x30\x00\x00\x00\x00\x40\x00\x07\x48\x06\x00\x80\x00\x71\x05\x00",
    "\x07\x80\x02\x00\x50\x00\x03\x00\x01\x30\x04\x06\x00\x80\x40\x70\x81\x52\x09\x00\x00\x00\x00"
    "\x07\x00\x00\x00\x05\x00\x07\x00\x00\x21\x00\x03\x07\x00\x02\x00\x08\x00",
    "\x70\x00\x13\x00\x00\x08\x70\x00\x60\x20\x40\x00\x08\x00\x00\x00\x05\x00\x59\x00\x00\x37\x14"
    "\x00\x06\x00\x00\x00\x00\x00\x09\x09\x00\x07\x00\x35\x38\x20\x90\x00\x00",
    "\x00\x45\x00\x00\x87\x00\x00\x09\x65\x00\x00\x00\x00\x09\x00\x43\x00\x02\x40\x80\x09\x00\x31"
    "\x00\x70\x06\x40\x00\x30\x80\x10\x00\x00\x04\x08\x90\x00\x09\x00\x00\x00",
    "\x09\x80\x02\x10\x00\x15\x40\x00\x09\x00\x00\x00\x00\x29\x20\x00\x07\x10\x00\x00\x30\x69\x00"
    "\x00\x00\x60\x08\x00\x06\x20\x00\x00\x00\x30\x00\x80\x08\x40\x00\x07\x60",
    "\x00\x20\x60\x00\x08\x00\x10\x72\x00\x07\x10\x35\x04\x00\x30\x02\x00\x00\x51\x40\x00\x00\x06"
    "\x00\x07\x05\x10\x08\x50\x00\x00\x10\x60\x00\x08\x07\x00\x00\x00\x00\x00",
    "\x14\x70\x90\x00\x60\x90\x00\x30\x01\x60\x00\x51\x04\x00\x28\x00\x00\x05\x00\x00\x00\x40\x20"
    "\x00\x00\x00\x60\x03\x01\x02\x08\x00\x00\x00\x00\x00\x50\x10\x09\x00\x30",
    "\x00\x00\x50\x20\x06\x70\x00\x00\x03\x00\x21\x00\x40\x00\x07\x03\x80\x64\x00\x04\x72\x00\x50"
    "\x30\x00\x07\x00\x80\x60\x00\x05\x00\x00\x20\x00\x80\x00\x00\x05\x00\x10",
    "\x03\x69\x01\x80\x05\x08\x00\x00\x36\x00\x00\x30\x00\x40\x00\x00\x02\x07\x01\x32\x00\x00\x80"
    "\x00\x00\x00\x00\x00\x10\x07\x00\x00\x02\x18\x50\x40\x00\x40\x00\x02\x00",
    "\x05\x02\x00\x08\x00\x00\x01\x00\x00\x90\x30\x06\x02\x00\x05\x00\x00\x30\x00\x90\x01\x50\x06"
    "\x00\x00\x00\x00\x08\x65\x07\x04\x00\x00\x08\x40\x07\x50\x00\x09\x26\x00",
    "\x90\x00\x00\x00\x00\x01\x00\x50\x63\x30\x00\x80\x71\x00\x00\x00\x04\x05\x64\x00\x10\x83\x00"
    "\x30\x40\x00\x00\x01\x00\x02\x05\x00\x02\x00\x79\x01\x00\x40\x00\x00\x00",
    "\x04\x20\x50\x00\x85\x10\x00\x00\x64\x70\x00\x40\x00\x00\x04\x00\x29\x00\x00\x00\x00\x00\x19"
    "\x60\x01\x57\x00\x00\x00\x80\x00\x72\x00\x00\x10\x06\x00\x82\x00\x00\x00",
    "\x70\x80\x00\x01\x00\x41\x06\x07\x00\x00\x00\x00\x00\x09\x06\x00\x03\x00\x00\x00\x03\x10\x70"
    "\x10\x09\x00\x52\x30\x40\x00\x02\x00\x00\x20\x40\x00\x08\x00\x09\x56\x00",
};
#define PUZZLES (int)(sizeof puzzles / sizeof *puzzles)

int sud_puzzles(void) { return PUZZLES; }
void sud_puzzle(int k, unsigned char *out) {
    for (int i = 0; i < 81; ++i) out[i] = (unsigned char)puzzles[k][i / 2] >> (i & 1 ? 0 : 4) & 15;
}

void sud_new(struct sudoku *s) {
    unsigned char clue[81], digit[9], band[3], stack[3], row[3][3], col[3][3];
    sud_puzzle((int)rand_below(PUZZLES), clue);
    deal(digit, 9);
    deal(band, 3);
    deal(stack, 3);
    for (int k = 0; k < 3; ++k) deal(row[k], 3), deal(col[k], 3);
    int flip = (int)rand_below(2);
    memset(s, 0, sizeof *s);
    for (int r = 0; r < 9; ++r)
        for (int c = 0; c < 9; ++c) {
            int sr = 3 * band[r / 3] + row[r / 3][r % 3], sc = 3 * stack[c / 3] + col[c / 3][c % 3];
            int v = clue[flip ? sc * 9 + sr : sr * 9 + sc];
            s->cell[r * 9 + c] = v ? (unsigned char)(digit[v - 1] + 1) | CLUE : 0;
        }
}

/* A clue stays. */
int sud_set(struct sudoku *s, int i, int v) {
    if (i < 0 || i > 80 || v < 0 || v > 9 || s->cell[i] & CLUE) return 0;
    s->cell[i] = (unsigned char)v;
    return 1;
}

/* Cell i's digit is also in its row, column or box. */
int sud_conflict(const struct sudoku *s, int i) {
    int v = s->cell[i] & 15, r = i / 9, c = i % 9, b = r / 3 * 27 + c / 3 * 3;
    for (int k = 0; v && k < 9; ++k) {
        int peer[3] = { r * 9 + k, k * 9 + c, b + k / 3 * 9 + k % 3 };
        for (int j = 0; j < 3; ++j)
            if (peer[j] != i && (s->cell[peer[j]] & 15) == v) return 1;
    }
    return 0;
}

int sud_done(const struct sudoku *s) {
    for (int i = 0; i < 81; ++i)
        if (!(s->cell[i] & 15) || sud_conflict(s, i)) return 0;
    return 1;
}

/* Vortex: rings of bricks round the centre, the paddle an arc on the outer ring, the ball bouncing
 * between. Angles are 1024 a turn, rising clockwise on screen; positions and velocities 16.16 px
 * from the centre, per 1/60 s step. All integer, so the same inputs give the same game.
 * ponytail: three fixed layouts repeating with the level, bricks reflect radially only. */
#define RINGS 3
#define SEGS 16
#define R_B 40     /* the innermost ring's inner radius, px */
#define BRICK_T 12 /* a ring's thickness */
#define R_PAD 118  /* the paddle's radius */
#define PAD_HALF 64
#define SPIN 128 /* the turn off the radial at the paddle's ends */
#define MISS (R_PAD + 10)
#define V0 (2 << 16) /* 120 px/s */
#define VSTEP (1 << 14)
#define VMAX (9 << 15) /* 270 px/s */
_Static_assert(VMAX < BRICK_T << 16, "a step never crosses a whole ring");
enum { AIM, PLAY, OVER };
struct vortex {
    int state, lives, level, score, pa, x, y, vx, vy;
    unsigned short brick[RINGS], pad;
};
static const unsigned short layouts[][RINGS] = {
    { 0xffff, 0xffff, 0xffff },
    { 0x5555, 0xffff, 0xaaaa },
    { 0x0f0f, 0xf0f0, 0xffff },
};
static const unsigned short quarter[257] = {
    /* sin, 16.16, a quarter turn */
    0,     402,   804,   1206,  1608,  2010,  2412,  2814,  3216,  3617,  4019,  4420,  4821,
    5222,  5623,  6023,  6424,  6824,  7224,  7623,  8022,  8421,  8820,  9218,  9616,  10014,
    10411, 10808, 11204, 11600, 11996, 12391, 12785, 13180, 13573, 13966, 14359, 14751, 15143,
    15534, 15924, 16314, 16703, 17091, 17479, 17867, 18253, 18639, 19024, 19409, 19792, 20175,
    20557, 20939, 21320, 21699, 22078, 22457, 22834, 23210, 23586, 23961, 24335, 24708, 25080,
    25451, 25821, 26190, 26558, 26925, 27291, 27656, 28020, 28383, 28745, 29106, 29466, 29824,
    30182, 30538, 30893, 31248, 31600, 31952, 32303, 32652, 33000, 33347, 33692, 34037, 34380,
    34721, 35062, 35401, 35738, 36075, 36410, 36744, 37076, 37407, 37736, 38064, 38391, 38716,
    39040, 39362, 39683, 40002, 40320, 40636, 40951, 41264, 41576, 41886, 42194, 42501, 42806,
    43110, 43412, 43713, 44011, 44308, 44604, 44898, 45190, 45480, 45769, 46056, 46341, 46624,
    46906, 47186, 47464, 47741, 48015, 48288, 48559, 48828, 49095, 49361, 49624, 49886, 50146,
    50404, 50660, 50914, 51166, 51417, 51665, 51911, 52156, 52398, 52639, 52878, 53114, 53349,
    53581, 53812, 54040, 54267, 54491, 54714, 54934, 55152, 55368, 55582, 55794, 56004, 56212,
    56418, 56621, 56823, 57022, 57219, 57414, 57607, 57798, 57986, 58172, 58356, 58538, 58718,
    58896, 59071, 59244, 59415, 59583, 59750, 59914, 60075, 60235, 60392, 60547, 60700, 60851,
    60999, 61145, 61288, 61429, 61568, 61705, 61839, 61971, 62101, 62228, 62353, 62476, 62596,
    62714, 62830, 62943, 63054, 63162, 63268, 63372, 63473, 63572, 63668, 63763, 63854, 63944,
    64031, 64115, 64197, 64277, 64354, 64429, 64501, 64571, 64639, 64704, 64766, 64827, 64884,
    64940, 64993, 65043, 65091, 65137, 65180, 65220, 65259, 65294, 65328, 65358, 65387, 65413,
    65436, 65457, 65476, 65492, 65505, 65516, 65525, 65531, 65535, 65535,
};
static const unsigned char atans[257] = {
    /* atan(i / 256), 1024 a turn */
    0,   1,   1,   2,   3,   3,   4,   4,   5,   6,   6,   7,   8,   8,   9,   10,  10,  11,  11,
    12,  13,  13,  14,  15,  15,  16,  16,  17,  18,  18,  19,  20,  20,  21,  22,  22,  23,  23,
    24,  25,  25,  26,  27,  27,  28,  28,  29,  30,  30,  31,  31,  32,  33,  33,  34,  34,  35,
    36,  36,  37,  38,  38,  39,  39,  40,  41,  41,  42,  42,  43,  44,  44,  45,  45,  46,  46,
    47,  48,  48,  49,  49,  50,  51,  51,  52,  52,  53,  53,  54,  55,  55,  56,  56,  57,  57,
    58,  58,  59,  60,  60,  61,  61,  62,  62,  63,  63,  64,  65,  65,  66,  66,  67,  67,  68,
    68,  69,  69,  70,  70,  71,  71,  72,  72,  73,  74,  74,  75,  75,  76,  76,  77,  77,  78,
    78,  79,  79,  80,  80,  81,  81,  82,  82,  83,  83,  84,  84,  84,  85,  85,  86,  86,  87,
    87,  88,  88,  89,  89,  90,  90,  91,  91,  91,  92,  92,  93,  93,  94,  94,  95,  95,  96,
    96,  96,  97,  97,  98,  98,  99,  99,  99,  100, 100, 101, 101, 102, 102, 102, 103, 103, 104,
    104, 104, 105, 105, 106, 106, 106, 107, 107, 108, 108, 108, 109, 109, 110, 110, 110, 111, 111,
    112, 112, 112, 113, 113, 113, 114, 114, 115, 115, 115, 116, 116, 116, 117, 117, 118, 118, 118,
    119, 119, 119, 120, 120, 120, 121, 121, 121, 122, 122, 122, 123, 123, 123, 124, 124, 124, 125,
    125, 125, 126, 126, 126, 127, 127, 127, 128, 128,
};

static int vsin(int a) {
    a &= 1023;
    int q = a & 255, v = a & 256 ? quarter[256 - q] : quarter[q];
    return a & 512 ? -v : v;
}
static int vcos(int a) { return vsin(a + 256); }

/* The angle of x, y (any scale, below 2^23). */
static int vangle(int y, int x) {
    int ax = x < 0 ? -x : x, ay = y < 0 ? -y : y;
    if (!ax && !ay) return 0;
    int a = ax >= ay ? atans[ay * 256 / ax] : 256 - atans[ax * 256 / ay];
    if (x < 0) a = 512 - a;
    if (y < 0) a = -a;
    return a & 1023;
}

static unsigned vsqrt(unsigned long long n) {
    unsigned long long r = 0, b = 1ull << 62;
    while (b > n) b >>= 2;
    for (; b; b >>= 2)
        if (n >= r + b)
            n -= r + b, r = (r >> 1) + b;
        else
            r >>= 1;
    return (unsigned)r;
}

/* x, y's distance from the centre, 1/256 px. */
static int radius(int x, int y) {
    long long a = x >> 8, b = y >> 8;
    return (int)vsqrt((unsigned long long)(a * a + b * b));
}

static void place(struct vortex *v) {
    v->x = (R_PAD - 6) * vcos(v->pa);
    v->y = (R_PAD - 6) * vsin(v->pa);
}

static void head(struct vortex *v, int a) {
    int speed = V0 + VSTEP * v->level;
    if (speed > VMAX) speed = VMAX;
    v->vx = (int)((long long)speed * vcos(a) >> 16);
    v->vy = (int)((long long)speed * vsin(a) >> 16);
}

static void level(struct vortex *v) {
    memcpy(v->brick, layouts[v->level % 3], sizeof v->brick);
    v->state = AIM;
    place(v);
}

void vortex_new(struct vortex *v) {
    memset(v, 0, sizeof *v);
    v->lives = 3;
    v->pa = 768; /* the top */
    level(v);
}

void vortex_launch(struct vortex *v) {
    if (v->state != AIM) return;
    head(v, v->pa + 512);
    v->state = PLAY;
}

/* One step, the paddle turned by dpa first. */
void vortex_step(struct vortex *v, int dpa) {
    v->pa = (v->pa + dpa) & 1023;
    if (v->state == AIM) place(v);
    if (v->state != PLAY) return;
    int ox = v->x, oy = v->y, before = radius(ox, oy);
    v->x += v->vx;
    v->y += v->vy;
    int r = radius(v->x, v->y), X = v->x >> 8, Y = v->y >> 8, ring = (r / 256 - R_B) / BRICK_T;
    if (r >= R_B * 256 && ring < RINGS) {
        int seg = vangle(Y, X) * SEGS / 1024;
        if (v->brick[ring] >> seg & 1) {
            v->brick[ring] &= (unsigned short)~(1u << seg);
            v->score += 10 * (RINGS - ring);
            int nx = X * 4096 / r, ny = Y * 4096 / r; /* the normal, 1/4096 */
            long long dot = ((long long)v->vx * nx + (long long)v->vy * ny) >> 12;
            v->vx -= (int)(2 * dot * nx >> 12);
            v->vy -= (int)(2 * dot * ny >> 12);
            v->x = ox, v->y = oy;
            if (!(v->brick[0] | v->brick[1] | v->brick[2])) {
                v->score += 100;
                ++v->level;
                level(v);
            }
            return;
        }
    }
    int edge = (R_PAD - 4) * 256;
    if (before < edge && r >= edge && (long long)v->vx * X + (long long)v->vy * Y > 0) {
        int a = vangle(Y, X), d = ((a - v->pa + 512) & 1023) - 512;
        if (d >= -PAD_HALF && d <= PAD_HALF) head(v, a + 512 + d * SPIN / PAD_HALF);
    } else if (r >= MISS * 256) {
        v->state = --v->lives ? AIM : OVER;
        place(v);
    }
}

/* Solitaire: Klondike, draw one, unlimited redeals. A card is suit * 13 + rank (Ace 0, King 12;
 * spades, hearts, diamonds, clubs) and UP when face up. The piles in the wheel's order: the stock,
 * the waste, four foundations (any takes an Ace), seven tableau columns.
 * ponytail: no undo or score; a held run drops whatever part of it fits. */
#define SOL_MAGIC 0x314c4f53u /* "SOL1" */
#define UP 64
#define SOL_MAX 24 /* the stock's 24 at most; a column's 6 face down and 13 up */
enum { STOCK, WASTE, FOUND, TABLEAU = FOUND + 4, PILES = TABLEAU + 7 };
struct solitaire {
    unsigned magic;
    unsigned char card[PILES][SOL_MAX], len[PILES], cur, pad[2];
    unsigned sum;
};

void sol_new(struct solitaire *s) {
    unsigned char deck[52];
    int n = 0;
    deal(deck, 52);
    memset(s, 0, sizeof *s);
    for (int k = 0; k < 7; ++k) {
        for (int i = 0; i <= k; ++i) s->card[TABLEAU + k][i] = deck[n++];
        s->card[TABLEAU + k][k] |= UP;
        s->len[TABLEAU + k] = (unsigned char)(k + 1);
    }
    memcpy(s->card[STOCK], deck + n, 52 - n);
    s->len[STOCK] = (unsigned char)(52 - n);
}

/* The stock's top card face up on the waste; an empty stock takes the waste back, face down. */
int sol_draw(struct solitaire *s) {
    if (s->len[STOCK]) {
        s->card[WASTE][s->len[WASTE]++] = s->card[STOCK][--s->len[STOCK]] | UP;
        return 1;
    }
    for (; s->len[WASTE]; ++s->len[STOCK])
        s->card[STOCK][s->len[STOCK]] = s->card[WASTE][--s->len[WASTE]] & ~UP;
    return s->len[STOCK] != 0;
}

static int sol_fits(const struct solitaire *s, int c, int to) {
    int n = s->len[to], top = n ? s->card[to][n - 1] & ~UP : 0;
    if (to < TABLEAU) return n ? c == top + 1 && c / 13 == top / 13 : c % 13 == 0;
    return n ? c % 13 == top % 13 - 1 && (c / 13 % 3 == 0) != (top / 13 % 3 == 0) : c % 13 == 12;
}

/* From's cards that fit onto to, moved: a foundation takes the top card, a column the part of
 * the face-up run that fits (at most one does). A column left with a face-down top turns it. */
int sol_move(struct solitaire *s, int from, int to) {
    int n = s->len[from], run = 0;
    if (from == STOCK || from == to || to < FOUND) return 0;
    while (run < n && s->card[from][n - 1 - run] & UP && (run < 1 || from >= TABLEAU)) ++run;
    for (int k = 1; k <= run && (k == 1 || to >= TABLEAU); ++k)
        if (sol_fits(s, s->card[from][n - k] & ~UP, to)) {
            memcpy(&s->card[to][s->len[to]], &s->card[from][n - k], (unsigned)k);
            s->len[to] = (unsigned char)(s->len[to] + k);
            s->len[from] = (unsigned char)(n - k);
            if (n > k) s->card[from][n - k - 1] |= UP;
            return 1;
        }
    return 0;
}

/* From's top card onto the foundation that takes it. */
int sol_home(struct solitaire *s, int from) {
    for (int f = FOUND; f < TABLEAU; ++f)
        if (sol_move(s, from, f)) return 1;
    return 0;
}

int sol_won(const struct solitaire *s) {
    return s->len[FOUND] + s->len[FOUND + 1] + s->len[FOUND + 2] + s->len[FOUND + 3] == 52;
}

/* Every card dealt and face up: all of them home, the lowest left always being a column's top. */
void sol_finish(struct solitaire *s) {
    if (s->len[STOCK] || s->len[WASTE]) return;
    for (int p = TABLEAU; p < PILES; ++p)
        if (s->len[p] && !(s->card[p][0] & UP)) return;
    for (int moved = 1; moved;) {
        moved = 0;
        for (int p = TABLEAU; p < PILES; ++p) moved |= sol_home(s, p);
    }
}

/* Snake: SN_N x SN_N cells, a cell y * SN_N + x; the body a ring of cells from the tail, the head
 * last. Walls and the body kill. A turn is relative (+1 right, -1 left), one a step and a second
 * waiting for the next, so quick turns can't fold the snake back into itself. Each apple adds one
 * and a little speed.
 * ponytail: no wrap mode, levels or obstacles. */
#define SN_N 20
#define SN_CELLS (SN_N * SN_N)
#define SN_MS 160 /* the first step's period; SN_FAST at most, SN_FASTER less an apple */
#define SN_FAST 70
#define SN_FASTER 4
struct snake {
    unsigned short body[SN_CELLS], tail, len, food;
    unsigned char dir, turned, state; /* dir 0 up, 1 right, 2 down, 3 left; state PLAY, OVER */
    signed char next;                 /* a turn waiting for the next step, 0 none */
    int score;
};

static void sn_food(struct snake *s) {
    unsigned char taken[SN_CELLS] = { 0 };
    for (int i = 0; i < s->len; ++i) taken[s->body[(s->tail + i) % SN_CELLS]] = 1;
    int k = SN_CELLS - s->len ? (int)rand_below((unsigned)(SN_CELLS - s->len)) : -1;
    for (int c = 0; c < SN_CELLS; ++c)
        if (!taken[c] && !k--) {
            s->food = (unsigned short)c;
            return;
        }
    s->state = OVER; /* the board is full */
}

void snake_new(struct snake *s) {
    memset(s, 0, sizeof *s);
    s->len = 3;
    s->dir = 1;
    for (int i = 0; i < 3; ++i) s->body[i] = (unsigned short)(SN_N / 2 * SN_N + SN_N / 2 - 3 + i);
    s->state = PLAY;
    sn_food(s);
}

void snake_turn(struct snake *s, int d) {
    if (s->state != PLAY) return;
    if (s->turned) {
        if (!s->next) s->next = (signed char)d; /* one waits; any more are dropped */
        return;
    }
    s->dir = (unsigned char)((s->dir + d + 4) & 3);
    s->turned = 1;
}

/* The step's period, ms. */
int snake_ms(const struct snake *s) {
    int ms = SN_MS - SN_FASTER * s->score;
    return ms < SN_FAST ? SN_FAST : ms;
}

/* 0 moved, 1 ate, -1 died (or nothing, after). */
int snake_step(struct snake *s) {
    if (s->state != PLAY) return -1;
    int head = s->body[(s->tail + s->len - 1) % SN_CELLS], x = head % SN_N, y = head / SN_N;
    s->turned = 0;
    x += s->dir == 1 ? 1 : s->dir == 3 ? -1 : 0;
    y += s->dir == 2 ? 1 : s->dir == 0 ? -1 : 0;
    int next = y * SN_N + x, ate = next == s->food;
    if (x < 0 || x >= SN_N || y < 0 || y >= SN_N) {
        s->state = OVER;
        return -1;
    }
    for (int i = !ate; i < s->len; ++i) /* the tail moves off its cell unless the snake grows */
        if (s->body[(s->tail + i) % SN_CELLS] == next) {
            s->state = OVER;
            return -1;
        }
    s->body[(s->tail + s->len) % SN_CELLS] = (unsigned short)next;
    if (s->next) snake_turn(s, s->next), s->next = 0; /* the waiting turn, for the next step */
    if (!ate) {
        s->tail = (unsigned short)((s->tail + 1) % SN_CELLS);
        return 0;
    }
    ++s->len;
    ++s->score;
    sn_food(s);
    return 1;
}

#ifndef PEQ_HOST
#define GAMES_SUDOKU "/mnt/data/ringnav-sudoku"
#define GAMES_VORTEX "/mnt/data/ringnav-vortex"
#define GAMES_SOLITAIRE "/mnt/data/ringnav-solitaire"
#define GAMES_SNAKE "/mnt/data/ringnav-snake"
#define BEST_MAGIC 0x31585456u  /* "VTX1" */
#define SNAKE_MAGIC 0x314b4e53u /* "SNK1": Snake's best, as Vortex's */
/* The paddle's turn for the wheel's, in percent: 100 is a whole turn for a whole turn (200 raw
 * units, wheel_sample). The hardware's calibration knob. */
#define VORTEX_GAIN 100
#define FRAME_MS 33
#define SAVE_MS 2000 /* a move's save waits this long; a power-off loses at most this */
#define CELL 30
#define TURN (6.2831853f / 1024)
#define POP_MS 240 /* Vortex: a broken brick's flash fading out */
#define TRAIL 4    /* Vortex: the ball's last frames, fading behind it */
#define HUD_X 24   /* Vortex: the score, best, level and lives, clear of the glass's corners */
#define SN_CELL 11 /* Snake: the board, under the HUD and clear of the glass's corners */
#define SN_X ((375 - SN_N * SN_CELL) / 2)
#define SN_Y 36
enum { CHOOSER, SUDOKU, VORTEX, SOLITAIRE, SNAKE };
/* Vortex, Snake: playing, paused by Play/Pause or the screen, Return's menu */
enum { RUN, HELD, MENU };
struct best {
    unsigned magic, score, sum;
};
static struct game {
    void *page, *list, *view, *sheet;
    unsigned timer, frame, at, acc; /* frame: Vortex's timer, at its last run, acc 1/60000 s due */
    int h, screen, target, picking, pick, confirm, paused, choice, carry;
    int dirty; /* unsaved: 1 Sudoku, 2 Solitaire */
    struct sudoku sud;
    struct vortex vx;
    struct best best;
    struct solitaire sol;
    unsigned save;
    unsigned pop_at; /* Vortex: when the last brick broke, which pop_ring, pop_seg */
    int pop_ring, pop_seg, trail[TRAIL][2], record; /* record: the last game set Best */
    struct best sn_best;
    struct snake sn;
} *g __attribute__((section(".scratch")));

static int sud_started(const struct sudoku *s) {
    for (int i = 0; i < 81; ++i)
        if (s->cell[i] && !(s->cell[i] & CLUE)) return 1;
    return 0;
}

/* The dirty games written whole (blob_io: a temporary, synced, renamed over). */
static void flush(int loud) {
    int ok = 1;
    stop_timer(&g->save);
    if (g->dirty & 1)
        games_seal(&g->sud, sizeof g->sud, SUD_MAGIC), ok &= BLOB_IO(GAMES_SUDOKU, g->sud, 1);
    if (g->dirty & 2)
        games_seal(&g->sol, sizeof g->sol, SOL_MAGIC), ok &= BLOB_IO(GAMES_SOLITAIRE, g->sol, 1);
    g->dirty = 0;
    if (!ok && loud) toast("Couldn't save");
}

static int save_due(const void *unused) {
    g->save = 0;
    flush(1);
    return 0;
}

/* A move: saved SAVE_MS after the first unsaved one, or on leaving the game, not per move. */
static void changed(int game) {
    g->dirty |= game;
    if (!g->save) g->save = timer_add(save_due, (void *)0, SAVE_MS);
}

/* The frame timer off and the wheel back to ticks. */
static void hold(int paused) {
    g->paused = paused;
    wheel_raw = 0;
    stop_timer(&g->frame);
}

/* A record score saved; an unfinished game never is. */
static void game_over(void) {
    int snake = g->screen == SNAKE, score = snake ? g->sn.score : g->vx.score;
    struct best *b = snake ? &g->sn_best : &g->best;
    if (!(g->record = score > (int)b->score)) return;
    b->score = (unsigned)score;
    games_seal(b, sizeof *b, snake ? SNAKE_MAGIC : BEST_MAGIC);
    if (!(snake ? BLOB_IO(GAMES_SNAKE, g->sn_best, 1) : BLOB_IO(GAMES_VORTEX, g->best, 1)))
        toast("Couldn't save");
}

/* Snake's steps, each its period of the time passed (2 a frame at most); 1 when it ended. */
static int snake_frame(unsigned elapsed) {
    struct snake *s = &g->sn;
    int moved = 0;
    g->acc += elapsed;
    for (int n = 0; n < 2 && s->state == PLAY && g->acc >= (unsigned)snake_ms(s); ++n, moved = 1)
        g->acc -= (unsigned)snake_ms(s), snake_step(s);
    if (g->acc >= (unsigned)snake_ms(s)) g->acc = 0;
    if (moved) widget_invalidate_force(g->page, 0);
    return s->state == OVER;
}

/* 60 steps a second, at most 4 a frame; the screen going off or another window on top pauses. */
static int frame(const void *unused) {
    if (!g_backlight_status || window_manager_get_top_window(window_manager()) != g->page) {
        g->frame = 0; /* ending: no timer to remove */
        hold(HELD);
        widget_invalidate_force(g->page, 0);
        return 0;
    }
    unsigned now = (unsigned)time_now_ms();
    if (g->screen == SNAKE) {
        unsigned elapsed = now - g->at;
        g->at = now;
        if (!snake_frame(elapsed)) return 8; /* RET_REPEAT */
        g->frame = 0;
        hold(RUN);
        game_over();
        return 0;
    }
    int travel = __sync_lock_test_and_set(&wheel_travel, 0), n = 0;
    struct vortex was = g->vx;
    g->acc += (now - g->at) * 60;
    g->at = now;
    g->carry -= travel * 1024 * VORTEX_GAIN; /* the wheel's x falls clockwise */
    int dpa = g->carry / (200 * 100);
    for (; g->acc >= 1000 && n < 4; ++n, g->acc -= 1000) vortex_step(&g->vx, n ? 0 : dpa);
    if (n) g->carry -= dpa * 200 * 100;
    if (g->acc >= 1000) g->acc = 0;
    if (travel) reset_poweroptions_timer(1, 1, 1); /* the ball alone lets the screen time out */
    for (int k = 0; k < RINGS; ++k) { /* a cleared level refills: its last brick is was's */
        unsigned gone = was.brick[k] & ~(g->vx.level == was.level ? g->vx.brick[k] : 0u);
        if (gone) g->pop_at = now, g->pop_ring = k, g->pop_seg = __builtin_ctz(gone);
    }
    for (int k = TRAIL - 1; k; --k) memcpy(g->trail[k], g->trail[k - 1], sizeof *g->trail);
    g->trail[0][0] = was.x, g->trail[0][1] = was.y;
    for (int k = 0; g->vx.state != PLAY && k < TRAIL; ++k) /* a launch starts with no tail */
        g->trail[k][0] = g->vx.x, g->trail[k][1] = g->vx.y;
    if (memcmp(&was, &g->vx, sizeof was) || now - g->pop_at < POP_MS)
        widget_invalidate_force(g->page, 0);
    if (g->vx.state != OVER) return 8; /* RET_REPEAT */
    g->frame = 0;
    hold(RUN);
    game_over();
    return 0;
}

/* A frame timer runs: ringnav_sleep keeps stock's pace. */
int games_live(void) { return g && g->frame; }

static void run(void) {
    g->paused = RUN;
    g->at = (unsigned)time_now_ms();
    g->acc = 0;
    g->carry = 0;
    __sync_lock_test_and_set(&wheel_travel, 0);
    wheel_raw = g->screen == VORTEX;
    if (!g->frame) g->frame = timer_add(frame, 0, FRAME_MS);
}

/* The chooser or the game target, from the next loop pass. */
static int show(const void *unused) {
    g->timer = 0;
    int from = g->screen;
    hold(RUN);
    g->screen = g->target;
    g->picking = g->confirm = 0;
    widget_set_visible(g->list, g->screen == CHOOSER, 0);
    widget_set_visible(g->sheet, g->screen != CHOOSER, 0);
    if (g->screen == CHOOSER) {
        flush(1);
        ringnav_select(g->view, from - 1, 4);
    } else if (g->screen == VORTEX)
        vortex_new(&g->vx), run();
    else if (g->screen == SNAKE)
        snake_new(&g->sn), run();
    widget_invalidate_force(g->page, 0);
    return 0;
}

static void to(int screen) {
    g->target = screen;
    rearm(&g->timer, show, 0);
}

static int pick_game(void *ctx, void *event) {
    to((int)(long)ctx + 1);
    return 0;
}

static int leave(const void *unused) {
    g->timer = 0;
    if (g->screen == CHOOSER)
        navigator_back();
    else
        to(CHOOSER);
    return 0;
}

/* Return in the chooser (a game's goes to games_key): back to the Library. */
static int keyup(void *ctx, void *event) { return return_key(event, &g->timer, leave); }

static void sudoku_key(unsigned key) {
    struct sudoku *s = &g->sud;
    int wheel = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0;
    if (g->confirm) {
        if (key == KEY_CENTER) sud_new(s), changed(1);
        if (key == KEY_CENTER || key == KEY_RETURN) g->confirm = 0;
    } else if (g->picking) {
        if (wheel) g->pick = (g->pick + wheel + 10) % 10;
        if (key == KEY_CENTER) sud_set(s, s->cur, g->pick), changed(1);
        if (key == KEY_CENTER || key == KEY_RETURN) g->picking = 0;
    } else if (wheel)
        s->cur = (unsigned char)((s->cur + wheel + 81) % 81);
    else if (key == KEY_FWD_BTN || key == KEY_BACK_BTN)
        s->cur = (unsigned char)((s->cur + (key == KEY_FWD_BTN ? 9 : 72)) % 81);
    else if (key == KEY_CENTER && !(s->cell[s->cur] & CLUE) && !sud_done(s))
        g->picking = 1, g->pick = s->cell[s->cur];
    else if (key == KEY_PLAY && sud_started(s) && !sud_done(s))
        g->confirm = 1;
    else if (key == KEY_PLAY)
        sud_new(s), changed(1);
    else if (key == KEY_RETURN)
        to(CHOOSER);
}

/* Vortex's and Snake's shared keys: Return's Continue/Quit menu, Play/Pause's pause, Centre again
 * after game over. 0 for a key left to the game. */
static int arcade_key(unsigned key, int over) {
    if (g->paused == MENU) {
        if (key == KEY_NEXT || key == KEY_PREV)
            g->choice = !g->choice;
        else if (key == KEY_CENTER && g->choice)
            to(CHOOSER);
        else if (key == KEY_CENTER || key == KEY_RETURN || key == KEY_PLAY)
            run();
    } else if (over) {
        if (key == KEY_CENTER && g->screen == SNAKE)
            snake_new(&g->sn), run();
        else if (key == KEY_CENTER)
            vortex_new(&g->vx), run();
        else if (key == KEY_RETURN)
            to(CHOOSER);
    } else if (key == KEY_RETURN)
        hold(MENU), g->choice = 0;
    else if (g->paused && (key == KEY_PLAY || key == KEY_CENTER))
        run();
    else if (key == KEY_PLAY)
        hold(HELD);
    else
        return 0;
    return 1;
}

static void vortex_key(unsigned key) {
    if (!arcade_key(key, g->vx.state == OVER) && key == KEY_CENTER) vortex_launch(&g->vx);
}

/* The side keys steer Snake; the wheel picks Continue/Quit in the menu and is ignored otherwise. */
static void snake_key(unsigned key) {
    int wheel = key == KEY_NEXT || key == KEY_PREV;
    if (key == KEY_FWD_BTN) key = KEY_NEXT;
    if (key == KEY_BACK_BTN) key = KEY_PREV;
    if (arcade_key(key, g->sn.state == OVER) || g->paused || wheel) return;
    if (key == KEY_NEXT) snake_turn(&g->sn, 1);
    if (key == KEY_PREV) snake_turn(&g->sn, -1);
}

/* A pile's column on screen: the stock, the waste, a gap and the foundations over the tableau. */
static const unsigned char col[PILES] = { 0, 1, 3, 4, 5, 6, 0, 1, 2, 3, 4, 5, 6 };

/* Centre picks a pile's cards up (the stock draws instead) and drops them where they fit; Centre
 * again on the same pile sends its top card home, or puts it back. */
static void solitaire_key(unsigned key) {
    struct solitaire *s = &g->sol;
    int wheel = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0, cur = s->cur, moved = 0;
    if (g->confirm) {
        if (key == KEY_CENTER) sol_new(s), changed(2);
        if (key == KEY_CENTER || key == KEY_RETURN) g->confirm = g->picking = 0;
    } else if (wheel)
        s->cur = (unsigned char)((cur + wheel + PILES) % PILES);
    else if (key == KEY_FWD_BTN || key == KEY_BACK_BTN)
        s->cur = (unsigned char)(cur < TABLEAU       ? TABLEAU + col[cur]
                                 : cur < TABLEAU + 2 ? cur - TABLEAU
                                                     : cur - TABLEAU - 1); /* col 2: the waste */
    else if (key == KEY_CENTER && g->picking) {
        moved = cur == g->pick
                    ? (cur < FOUND || cur >= TABLEAU) && sol_home(s, cur) /* a foundation's stays */
                    : sol_move(s, g->pick, cur);
        if (moved || cur == g->pick) g->picking = 0;
    } else if (key == KEY_CENTER && cur == STOCK)
        moved = sol_draw(s);
    else if (key == KEY_CENTER && s->len[cur] && !sol_won(s))
        g->picking = 1, g->pick = cur;
    else if (key == KEY_PLAY && !sol_won(s))
        g->confirm = 1;
    else if (key == KEY_PLAY)
        sol_new(s), changed(2);
    else if (key == KEY_RETURN && g->picking)
        g->picking = 0;
    else if (key == KEY_RETURN)
        to(CHOOSER); /* show() saves, the cursor too */
    if (moved) sol_finish(s), changed(2);
}

/* ringnav(), before stock: a game takes every key but Power and a held Return (Home), so the
 * wheel, Centre, Play/Pause and the side buttons never reach the volume, the media or the screen.
 */
int games_key(void *top, unsigned key) {
    if (!g || top != g->page || g->screen == CHOOSER) return 0;
    if (key == KEY_RETURN && *(volatile unsigned char *)RETURN_RELEASE_LATCH) return 0;
    struct game was = *g;
    if (g->screen == SUDOKU)
        sudoku_key(key);
    else if (g->screen == SOLITAIRE)
        solitaire_key(key);
    else if (g->screen == SNAKE)
        snake_key(key);
    else
        vortex_key(key);
    /* a moved cursor is kept on leaving; a key the game ignored paints nothing */
    g->dirty |= (g->sud.cur != was.sud.cur) | (g->sol.cur != was.sol.cur) << 1;
    if (memcmp(&was, g, sizeof was)) widget_invalidate_force(g->page, 0);
    return 1;
}

static void box(void *canvas, int x, int y, int w, int h, unsigned rgb) {
    canvas_set_fill_color(canvas, rgba(rgb, 255));
    canvas_fill_rect(canvas, x, y, w, h);
}

/* A box with its corners rounded r, square when the canvas declines to round it. */
static void round_box(void *canvas, int x, int y, int w, int h, unsigned rgb, unsigned r) {
    int rect[4] = { x, y, w, h };
    unsigned c = rgba(rgb, 255);
    if (canvas_fill_rounded_rect(canvas, rect, (void *)0, &c, r)) box(canvas, x, y, w, h, rgb);
}

/* A two-pixel frame, its corners rounded r (4 or more) or, at 0 or when the canvas declines,
 * square. */
static void frame_rect(void *canvas, int x, int y, int w, int h, unsigned rgb, unsigned r) {
    int out[4] = { x, y, w, h }, in[4] = { x + 1, y + 1, w - 2, h - 2 };
    unsigned c = rgba(rgb, 255);
    if (r && !canvas_stroke_rounded_rect(canvas, out, (void *)0, &c, r, 1) &&
        !canvas_stroke_rounded_rect(canvas, in, (void *)0, &c, r - 1, 1))
        return;
    box(canvas, x, y, w, 2, rgb);
    box(canvas, x, y + h - 2, w, 2, rgb);
    box(canvas, x, y, 2, h, rgb);
    box(canvas, x + w - 2, y, 2, h, rgb);
}

/* A framed box mid-screen, two lines. */
static void banner(void *canvas, unsigned tone, const char *a, unsigned ca, const char *b,
                   unsigned cb) {
    int w = 281, h = 80, x = (375 - w) / 2, y = (g->h - h) / 2;
    round_box(canvas, x, y, w, h, 0x202020, 12);
    frame_rect(canvas, x, y, w, h, tone, 12);
    caption(canvas, a, x, y + 8, w, 32, 20, rgba(ca, 255));
    caption(canvas, b, x, y + 40, w, 32, 20, rgba(cb, 255));
}

/* Clues on grey, entries plain in the tone, a conflict red and underlined, the cursor framed, and
 * every cell holding the cursor's digit (the one being picked) dark on the tone. */
static void sudoku_paint(void *canvas, unsigned tone) {
    const struct sudoku *s = &g->sud;
    int x0 = (375 - 9 * CELL) / 2, y0 = (g->h - 9 * CELL) / 2;
    int same = g->picking ? g->pick : s->cell[s->cur] & 15;
    char d[2] = { 0, 0 };
    box(canvas, x0, y0, 9 * CELL, 9 * CELL, 0x555555);
    for (int i = 0; i < 81; ++i) {
        int x = x0 + i % 9 * CELL, y = y0 + i / 9 * CELL, v = s->cell[i] & 15;
        int picked = g->picking && i == s->cur, bad = !picked && sud_conflict(s, i);
        if (picked) v = g->pick;
        int lit = v && v == same; /* the picked cell too */
        box(canvas, x + 1, y + 1, CELL - 2, CELL - 2,
            lit                 ? tone
            : s->cell[i] & CLUE ? 0x333333
                                : 0);
        if (!v) continue;
        d[0] = (char)('0' + v);
        caption(canvas, d, x, y, CELL, CELL, 20,
                rgba(bad                 ? 0xff5050
                     : lit               ? 0
                     : s->cell[i] & CLUE ? 0xffffff
                                         : tone,
                     255));
        if (bad) box(canvas, x + 9, y + CELL - 6, CELL - 18, 2, 0xff5050);
    }
    for (int k = 0; k <= 9; k += 3) {
        box(canvas, x0 + k * CELL - 1, y0 - 1, 2, 9 * CELL + 2, 0xaaaaaa);
        box(canvas, x0 - 1, y0 + k * CELL - 1, 9 * CELL + 2, 2, 0xaaaaaa);
    }
    frame_rect(canvas, x0 + s->cur % 9 * CELL, y0 + s->cur / 9 * CELL, CELL, CELL,
               g->picking ? 0xffffff : tone, 0);
    if (g->confirm)
        banner(canvas, tone, "New game?", 0xffffff, "Centre: yes   Return: no", 0xaaaaaa);
    else if (sud_done(s))
        banner(canvas, tone, "Solved!", tone, "Play/Pause: new game", 0xaaaaaa);
}

static void arc(void *vg, float cx, float cy, int r, int a0, int a1, int width, unsigned color) {
    vgcanvas_begin_path(vg);
    vgcanvas_arc(vg, cx, cy, (float)r, (float)a0 * TURN, (float)a1 * TURN, 0);
    vgcanvas_set_line_width(vg, (float)width);
    vgcanvas_set_stroke_color(vg, color);
    vgcanvas_stroke(vg);
}

static void dot(void *vg, float x, float y, float r, unsigned color) {
    vgcanvas_begin_path(vg);
    vgcanvas_arc(vg, x, y, r, 0, 1024 * TURN, 0);
    vgcanvas_set_fill_color(vg, color);
    vgcanvas_fill(vg);
}

/* The rings shaded from the tone inwards, a broken brick flashing white as it goes, the paddle
 * round-ended, the ball with a fading trail in the tone, and the lives as balls under Best. */
static void vortex_paint(void *canvas, unsigned tone) {
    const struct vortex *v = &g->vx;
    void *vg = canvas_get_vgcanvas(canvas);
    float cx = 187.5f, cy = (float)g->h / 2;
    unsigned age = (unsigned)time_now_ms() - g->pop_at;
    char line[24];
    if (vg) {
        vgcanvas_save(vg);
        vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
        arc(vg, cx, cy, R_PAD, 0, 1024, 1, rgba(0x333333, 255));
        for (int k = 0; k < RINGS; ++k)
            for (int j = 0; j < SEGS; ++j)
                if (v->brick[k] >> j & 1)
                    arc(vg, cx, cy, R_B + k * BRICK_T + BRICK_T / 2, j * 64 + 2, j * 64 + 62,
                        BRICK_T - 2, rgba(mix(tone, 0, k, 4), 255));
        if (age < POP_MS && !g->paused && v->state != OVER)
            arc(vg, cx, cy, R_B + g->pop_ring * BRICK_T + BRICK_T / 2, g->pop_seg * 64 + 2,
                g->pop_seg * 64 + 62, BRICK_T - 2, rgba(0xffffff, 255 - 255 * age / POP_MS));
        vgcanvas_set_line_cap(vg, "round");
        arc(vg, cx, cy, R_PAD, v->pa - PAD_HALF, v->pa + PAD_HALF, 6, rgba(0xffffff, 255));
        for (int k = TRAIL - 1; v->state == PLAY && k >= 0; --k)
            dot(vg, cx + (float)g->trail[k][0] / 65536, cy + (float)g->trail[k][1] / 65536,
                3.5f - k * 0.5f, rgba(tone, 160 - 36 * k));
        dot(vg, cx + (float)v->x / 65536, cy + (float)v->y / 65536, 4, rgba(0xffffff, 255));
        for (int k = 0; k < v->lives; ++k)
            dot(vg, 375 - HUD_X - 55 + (k - (v->lives - 1) / 2.0f) * 14, (float)g->h - 18, 4,
                rgba(0xaaaaaa, 255));
        vgcanvas_restore(vg);
    }
    tk_snprintf(line, sizeof line, "Score %d", v->score);
    caption(canvas, line, HUD_X, 6, 110, 24, 16, SPOT_GREY);
    tk_snprintf(line, sizeof line, "Best %u", g->best.score);
    caption(canvas, line, 375 - HUD_X - 110, 6, 110, 24, 16, SPOT_GREY);
    tk_snprintf(line, sizeof line, "Level %d", v->level + 1);
    caption(canvas, line, HUD_X, g->h - 30, 110, 24, 16, SPOT_GREY);
    if (g->paused == MENU)
        banner(canvas, tone, "Continue", g->choice ? 0xaaaaaa : tone, "Quit",
               g->choice ? tone : 0xaaaaaa);
    else if (v->state == OVER)
        banner(canvas, tone, g->record ? "New best!" : "Game over", 0xffffff, "Centre: play again",
               0xaaaaaa);
    else if (g->paused)
        banner(canvas, tone, "Paused", 0xffffff, "Play/Pause: resume", 0xaaaaaa);
    else if (v->state == AIM)
        caption(canvas, "Centre", (int)cx - 40, (int)cy - 12, 80, 24, 16, SPOT_GREY);
}

#define CARD_W 45
#define CARD_H 46
#define PITCH 49   /* 7 columns, 4 px apart */
#define SOL_X 18   /* the first column's left: the top row clears the glass's corners */
#define SOL_Y 6    /* the top row's top */
#define SOL_LOW 35 /* the outer columns end this far above the bottom, clear of the corners */
#define FAN 20     /* a face-up card's label showing under the next */
#define FAN_DOWN 6

/* A grey v (0 to 255) that Light Mode's flip (ui_ink) leaves at v: a card is paper-white in either
 * mode, its black suits black. */
static unsigned unflipped(unsigned v) {
#if IPOD
    if (ipod_light()) v = v < LIGHT_INK ? 255 : (LIGHT_PAPER - v) * 255 / (LIGHT_PAPER - LIGHT_INK);
#endif
    return v * 0x010101u;
}

/* A rounded card: face down in the tone, darker inside a border when nothing covers it, or rank and
 * suit at the top in red or black, its suit large below when nothing covers it. */
static void card_paint(void *canvas, int x, int y, int c, int whole, unsigned tone) {
    static const unsigned short suit[] = { 0x2660, 0x2665, 0x2666, 0x2663 }; /* ♠♥♦♣ */
    unsigned text[3], n = 0;
    round_box(canvas, x, y, CARD_W, CARD_H, 0x777777, 5);
    round_box(canvas, x + 1, y + 1, CARD_W - 2, CARD_H - 2,
              c & UP ? unflipped(0xf2) : mix(tone, 0, 1, 2), 4);
    if (!(c & UP)) {
        if (whole) round_box(canvas, x + 4, y + 4, CARD_W - 8, CARD_H - 8, mix(tone, 0, 3, 4), 3);
        return;
    }
    c &= ~UP;
    unsigned ink = rgba(c / 13 % 3 ? 0xd01818 : unflipped(0x10), 255);
    if (c % 13 == 9) text[n++] = '1';
    text[n++] = (unsigned char)"A234567890JQK"[c % 13];
    text[n++] = suit[c / 13];
    int r[4] = { x, y + 1, CARD_W, FAN - 2 }, big[4] = { x, y + FAN - 2, CARD_W, CARD_H - FAN + 2 };
    draw_centred(canvas, text, n, r, 16, ink);
    if (whole) draw_centred(canvas, &text[n - 1], 1, big, 26, ink);
}

/* The top row's piles show their top card, a column all of it, its face-up fan closed up to fit
 * the sheet. The cursor's pile is framed round its top card, white over a held run framed in the
 * tone. */
static void solitaire_paint(void *canvas, unsigned tone) {
    const struct solitaire *s = &g->sol;
    for (int p = 0; p < PILES; ++p) {
        int n = s->len[p], i = p >= TABLEAU || !n ? 0 : n - 1, down = 0, fan = FAN;
        int x = SOL_X + col[p] * PITCH, y = p < TABLEAU ? SOL_Y : SOL_Y + CARD_H + 8, first = -1;
        int room = (col[p] % 6 ? g->h - 4 : g->h - SOL_LOW) - y; /* the column's height */
        while (down < n && !(s->card[p][down] & UP)) ++down;
        if (n - down > 1 && down * FAN_DOWN + (n - down - 1) * FAN + CARD_H > room)
            fan = (room - CARD_H - down * FAN_DOWN) / (n - down - 1);
        if (!n) frame_rect(canvas, x, y, CARD_W, CARD_H, 0x333333, 5);
        for (; i < n; y += s->card[p][i++] & UP ? fan : FAN_DOWN) {
            if (s->card[p][i] & UP && first < 0) first = y;
            card_paint(canvas, x, y, s->card[p][i], i == n - 1, tone);
            if (i == n - 1) break;
        }
        if (g->picking && p == g->pick)
            frame_rect(canvas, x - 2, first - 2, CARD_W + 4, y - first + CARD_H + 4, tone, 7);
        if (p == s->cur)
            frame_rect(canvas, x - 2, y - 2, CARD_W + 4, CARD_H + 4, g->picking ? 0xffffff : tone,
                       7);
    }
    if (g->confirm)
        banner(canvas, tone, "New game?", 0xffffff, "Centre: yes   Return: no", 0xaaaaaa);
    else if (sol_won(s))
        banner(canvas, tone, "You win!", tone, "Play/Pause: new game", 0xaaaaaa);
}

/* The board dark in a grey frame, the body rounded in the tone, the head brighter, the apple round
 * and red, the score and best over it. */
static void snake_paint(void *canvas, unsigned tone) {
    const struct snake *s = &g->sn;
    void *vg = canvas_get_vgcanvas(canvas);
    int w = SN_N * SN_CELL;
    char line[24];
    round_box(canvas, SN_X - 4, SN_Y - 4, w + 8, w + 8, 0x111111, 8);
    frame_rect(canvas, SN_X - 4, SN_Y - 4, w + 8, w + 8, 0x333333, 8);
    for (int i = 0; i < s->len; ++i) {
        int c = s->body[(s->tail + i) % SN_CELLS], head = i == s->len - 1;
        round_box(canvas, SN_X + c % SN_N * SN_CELL + 1, SN_Y + c / SN_N * SN_CELL + 1, SN_CELL - 2,
                  SN_CELL - 2, head ? mix(tone, 0xffffff, 1, 3) : tone, 3);
    }
    if (vg && s->state == PLAY) {
        vgcanvas_save(vg);
        vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
        dot(vg, SN_X + (s->food % SN_N + 0.5f) * SN_CELL, SN_Y + (s->food / SN_N + 0.5f) * SN_CELL,
            SN_CELL / 2.0f - 1, rgba(0xe04848, 255));
        vgcanvas_restore(vg);
    }
    tk_snprintf(line, sizeof line, "Score %d", s->score);
    caption(canvas, line, HUD_X, 6, 110, 24, 16, SPOT_GREY);
    tk_snprintf(line, sizeof line, "Best %u", g->sn_best.score);
    caption(canvas, line, 375 - HUD_X - 110, 6, 110, 24, 16, SPOT_GREY);
    if (g->paused == MENU)
        banner(canvas, tone, "Continue", g->choice ? 0xaaaaaa : tone, "Quit",
               g->choice ? tone : 0xaaaaaa);
    else if (s->state == OVER)
        banner(canvas, tone, g->record ? "New best!" : "Game over", 0xffffff, "Centre: play again",
               0xaaaaaa);
    else if (g->paused)
        banner(canvas, tone, "Paused", 0xffffff, "Play/Pause: resume", 0xaaaaaa);
}

/* ringnav_paint: a game's sheet, after stock painted it. */
void games_paint(void *w, void *canvas) {
    if (!g || w != g->sheet || g->screen == CHOOSER || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), tone = accent_tone(2);
    if (g->screen == SUDOKU)
        sudoku_paint(canvas, tone);
    else if (g->screen == SOLITAIRE)
        solitaire_paint(canvas, tone);
    else if (g->screen == SNAKE)
        snake_paint(canvas, tone);
    else
        vortex_paint(canvas, tone);
    stock_canvas_set_fill_color(canvas, fill);
}

static int closed(void *ctx, void *event) {
    hold(RUN);
    stop_timer(&g->timer);
    flush(0);
    free(g);
    g = 0;
    return 0;
}

/* The Library's Games row: the chooser, the Sudoku in progress and Vortex's and Snake's best scores
 * read. */
int games_open(void *ctx, void *event) {
    if (g || !(g = calloc(1, sizeof *g))) return 0;
    void *page = g->page = page_open("games_page", closed, keyup);
    if (!page) {
        free(g);
        g = 0;
        return 0;
    }
    void *f = widget_factory();
    g->h = widget_get_prop_int(page, "h", 290);
    /* The sheet first, under the list: hidden, it still took the chooser's taps above it. */
    g->sheet = widget_factory_create_widget(f, "view", page, 0, 0, 375, g->h);
    g->list = widget_factory_create_widget(f, "view", page, 0, 0, 375, g->h);
    widget_set_visible(g->sheet, 0, 0);
    g->view = page_list(page, g->list, 0, "Games", 4, 48);
    page_row_into(g->view, 0, "Sudoku", pick_game);
    page_row_into(g->view, 1, "Vortex", pick_game);
    page_row_into(g->view, 2, "Solitaire", pick_game);
    page_row_into(g->view, 3, "Snake", pick_game);
    if (!BLOB_IO(GAMES_SUDOKU, g->sud, 0) || !games_ok(&g->sud, sizeof g->sud, SUD_MAGIC) ||
        g->sud.cur > 80)
        sud_new(&g->sud);
    if (!BLOB_IO(GAMES_VORTEX, g->best, 0) || !games_ok(&g->best, sizeof g->best, BEST_MAGIC))
        g->best.score = 0;
    if (!BLOB_IO(GAMES_SNAKE, g->sn_best, 0) ||
        !games_ok(&g->sn_best, sizeof g->sn_best, SNAKE_MAGIC))
        g->sn_best.score = 0;
    int bad = !BLOB_IO(GAMES_SOLITAIRE, g->sol, 0) ||
              !games_ok(&g->sol, sizeof g->sol, SOL_MAGIC) || g->sol.cur >= PILES;
    for (int p = 0; p < PILES; ++p) bad |= g->sol.len[p] > SOL_MAX;
    if (bad) sol_new(&g->sol);
    return 0;
}
#endif
#endif
