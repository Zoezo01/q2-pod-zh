/* Playlists: the editor, New, Delete and More, the hold menu's Add to Playlist picker and Classic's
 * page. */
#include "navigation.h"
#include "playlist_art.h"

/* Entry IDs are stock's order. Keep our records separate from browsing and the live queue. */
static struct {
    void *page, *view, *songs, *picker, *edit;
    void **match;
    int dest, mode, selected, pending, nmatch;
    unsigned key, timer;
    char name[128], query[128];
} ple __attribute__((section(".scratch")));

/* One private selection for demo's lifetime, so a covered stock picker keeps its batch intact. */
static void *ple_add_select __attribute__((section(".scratch")));
struct ple_check {
    void *a, *b;
    int seen;
};
static int ple_checked(void *ctx, int n, char **v, char **names) {
    struct ple_check *c = ctx;
    if (n != 4 || !v[0] || !v[1]) return 1;
    int id = atoi(v[0]);
    void *r = id == I(c->a, REC_ID) ? c->a : c->b;
    if (!r || id != I(r, REC_ID) || tk_strcmp(v[1], P(r, REC_PATH)) ||
        (v[2] ? atoi(v[2]) : 0) != I(r, REC_CUE_START) ||
        (v[3] ? atoi(v[3]) : 0) != I(r, REC_CUE_END))
        return 1;
    c->seen++;
    return 0;
}
static int ple_changed(void *ctx, int n, char **v, char **names) {
    return n != 1 || !v[0] || atoi(v[0]) != 1;
}
static int ple_number(void *ctx, int n, char **v, char **names) {
    if (n != 1 || !v[0]) return 1;
    *(int *)ctx = atoi(v[0]);
    return 0;
}

/* Stock's shared connection, opened lazily and again after any error (0x5c8aa0's jalr
 * sqlite3_open, tools/build.py), and ours (pl_db): the same wait, so Import, Add to Playlist and
 * Rename don't fail (stock's writers drop songs silently) while the art worker reads.
 * ponytail: a stock write that meets Home's or the update dialog's count (a read of the whole
 * songtable) now holds the UI up to 500 ms per statement before it fails as before; a shorter
 * timeout is the knob if that shows. */
int stock_db_open(const char *path, void **db) {
    int bad = sqlite3_open(path, db);
    if (!bad) sqlite3_exec(*db, "PRAGMA busy_timeout=500", 0, 0, 0);
    return bad;
}
/* The database for a write that waits out a reader, playlist_art.c's worker reading the table,
 * instead of failing with SQLITE_BUSY; nonzero when it won't open. */
static int pl_db(void **db) { return stock_db_open("/mnt/data/database.db", db); }

/* Revalidate under the write lock; the temporary ID is above every occupied ID. */
static int ple_mutate(int dest, void *a, void *b) {
    void *db = 0;
    int ok = 0, temp = 0;
    char sql[512];
    struct ple_check check = { a, b, 0 };
    if (dest < 0 || dest >= 100 || !a || I(a, REC_ID) <= 100 ||
        (b && (I(b, REC_ID) <= 100 || I(a, REC_ID) == I(b, REC_ID))))
        return 0;
    if (pl_db(&db)) goto done;
    if (sqlite3_exec(db, "begin immediate", 0, 0, 0)) goto done;
    tk_snprintf(sql, sizeof sql,
                "select id,fileurl,time,time_end from playlist where cd_num=%d and id in (%d,%d)",
                dest, I(a, REC_ID), b ? I(b, REC_ID) : I(a, REC_ID));
    if (sqlite3_exec(db, sql, ple_checked, &check, 0) || check.seen != (b ? 2 : 1)) goto rollback;
    if (b) {
        /* No HAVING: the firmware's SQLite (3.19.3) refuses one without GROUP BY. The top id gives
         * NULL, which ple_number refuses. */
        if (sqlite3_exec(db,
                         "select case when ifnull(max(id),100)<2147483647 "
                         "then max(100,ifnull(max(id),100))+1 end from playlist",
                         ple_number, &temp, 0) ||
            temp <= 100)
            goto rollback;
        tk_snprintf(sql, sizeof sql,
                    "update playlist set id=%d where cd_num=%d and id=%d;select changes();"
                    "update playlist set id=%d where cd_num=%d and id=%d;select changes();"
                    "update playlist set id=%d where cd_num=%d and id=%d;select changes()",
                    temp, dest, I(a, REC_ID), I(a, REC_ID), dest, I(b, REC_ID), I(b, REC_ID), dest,
                    temp);
    } else
        tk_snprintf(sql, sizeof sql,
                    "delete from playlist where cd_num=%d and id=%d;select changes()", dest,
                    I(a, REC_ID));
    if (sqlite3_exec(db, sql, ple_changed, 0, 0) || sqlite3_exec(db, "commit", 0, 0, 0))
        goto rollback;
    ok = 1;
    goto done;
rollback:
    sqlite3_exec(db, "rollback", 0, 0, 0);
done:
    if (db) sqlite3_close(db);
    return ok;
}

/* The stored order (stock V1.34's Time added, V1.32's only one), not getMusicByPlayList's: a move
 * swaps two neighbours' ids, which means nothing in a list sorted by name. */
static int ple_query(void *ctx) { return sort_playlist_addtime(ple.dest); }
/* The editor shows the playlist's songs in that order (mode 0), not the A to Z Add Songs picker. */
int ple_listing(void) { return ple.page && !ple.mode; }
static void ple_reload(void) {
    int n;
    void *songs = staged(ple_query, 0, &n);
    if (ple.songs) deque_destroy(ple.songs);
    ple.songs = songs;
    int size = (int)deque_size(songs);
    if (ple.selected >= size) ple.selected = size ? size - 1 : 0;
}
/* The Add Songs picker's Search row filters by title, artist or album, ASCII case-insensitively.
 * ple.match holds the matching records only; the picker keeps every song, so clearing the query
 * restores the list without re-staging it. */
static int ple_has(const char *text, const char *query) { return text && strcasestr(text, query); }
static void ple_filter(void) {
    free(ple.match);
    ple.match = 0;
    ple.nmatch = 0;
    int n = ple.picker ? (int)deque_size(ple.picker) : 0;
    if (!n || !(ple.match = calloc((unsigned)n, sizeof *ple.match))) return;
    for (int i = 0; i < n; ++i) {
        void *r = deque_at(ple.picker, (unsigned)i);
        char name[512];
        const char *fields[] = { track_name(name, sizeof name, r), P(r, REC_ARTIST),
                                 P(r, REC_ALBUM) };
        for (unsigned f = 0; f < sizeof fields / sizeof *fields; ++f)
            if (ple_has(fields[f], ple.query)) {
                ple.match[ple.nmatch++] = r;
                break;
            }
    }
}
/* Stock's "select" check at the row's end, as the Shuffle and Repeat menus; the caption stops short
 * of it. */
static void *ple_tick(void *item) {
    void *label = item ? widget_get_child(item, 0) : 0;
    if (!label) return 0;
    widget_move_resize(label, I(label, W_X), I(label, W_Y), 330 - I(label, W_X), I(label, W_H));
    void *tick = image_create(item, 340, 0, 24, I(item, W_H));
    image_base_set_image(tick, "select");
    return tick;
}
static void ple_show(void);
static int ple_closed(void *ctx, void *event) {
    if (ple.timer) timer_remove(ple.timer);
    if (ple.songs) deque_destroy(ple.songs);
    if (ple.picker) deque_destroy(ple.picker);
    free(ple.match);
    unsigned key = ple.key; /* swallow the release even if the page closed during the press */
    memset(&ple, 0, sizeof ple);
    ple.key = key;
    return 0;
}
static void ple_move(int at, int dir) {
    int n = (int)deque_size(ple.songs), next = at + dir;
    if (at < 0 || at >= n || next < 0 || next >= n) return;
    if (ple_mutate(ple.dest, deque_at(ple.songs, (unsigned)at),
                   deque_at(ple.songs, (unsigned)next))) {
        ple.selected = next;
        ple_reload();
        ple_show();
    } else
        toast("Could not save playlist");
}
/* phase: press, release, or native delivery/long press. A repeat never moves twice. */
int ple_key(unsigned key, int phase) {
    if (key != KEY_BACK_BTN && key != KEY_FWD_BTN) return 0;
    unsigned bit = key == KEY_FWD_BTN ? 2 : 1;
    int live = ple.page && window_manager_get_top_window(window_manager()) == ple.page;
    if (!ple.page && !(ple.key & bit)) return 0;
    if (phase == 1 && !(ple.key & bit)) {
        ple.key |= bit;
        if (live && !ple.mode && !ple.timer && usable())
            ple_move(widget_get_prop_int(ple.view, SEL, ple.selected + 2) - 2,
                     key == KEY_FWD_BTN ? 1 : -1);
    }
    if (phase == 2) ple.key &= ~bit;
    return 1;
}
static int ple_run(const void *unused);
static int ple_back(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    /* The search box's text is applied by the next loop pass, never under its own key event. */
    if (ple.mode == 4) {
        if (!ple.timer) ple.timer = timer_add(ple_run, 0, 0);
        return STOP;
    }
    stop_timer(&ple.timer);
    if (ple.mode) {
        ple.mode = 0;
        ple_reload();
        ple_show();
    } else
        navigator_back();
    return STOP;
}

/* Rebuilds and closes run after click delivery, as the queue menu does. */
static int ple_run(const void *unused) {
    ple.timer = 0;
    if (!ple.page) return 0;
    int action = ple.pending, mode = ple.mode;
    if (!mode) {
        if (action == 1) {
            navigator_back();
            return 0;
        }
        if (!action) {
            int n;
            if (ple.picker) deque_destroy(ple.picker);
            ple.picker = staged_music(all_songs, (void *)1, &n);
            ple_filter();
            ple.mode = 1;
        } else {
            ple.selected = action - 2;
            ple.mode = 2;
        }
    } else if (mode == 1) {
        if (!action)
            ple.mode = 4;
        else if ((unsigned)(action - 1) >= (unsigned)ple.nmatch)
            return 0;
        else {
            void *r = ple.match[action - 1];
            if (checkPlayListExist(r, ple.dest))
                toast("Already in playlist");
            else {
                int before = (int)deque_size(ple.songs);
                void *one = _create_deque("stSongInfo");
                if (!one) {
                    toast("Could not save playlist");
                    return 0;
                }
                deque_init(one);
                _deque_push_back(one, r); /* stock rewrites the added record's ID and cd_num */
                if (!ple_add_select) {
                    ple_add_select = _create_vector("int");
                    if (ple_add_select) {
                        vector_init(ple_add_select);
                        _vector_push_back(ple_add_select, 1);
                    }
                }
                if (ple_add_select) batch_add_file(0xf001, 0xf00b, one, ple_add_select, ple.dest);
                deque_destroy(one);
                ple_reload();
                int added = (int)deque_size(ple.songs) > before;
                if (added) ple_tick(widget_get_child(ple.view, (unsigned)action));
                toast(added ? "Added to Playlist" : "Could not save playlist");
            }
            return 0; /* picker stays on its selected song */
        }
    } else if (mode == 4) {
        if (!ple.edit || widget_get_text_utf8(ple.edit, ple.query, sizeof ple.query))
            ple.query[0] = 0;
        ple.mode = 1;
        ple_filter();
    } else if (mode == 2) {
        if (action == 2)
            ple.mode = 3;
        else {
            ple.mode = 0;
            ple_move(ple.selected, action ? 1 : -1);
        }
    } else {
        if (!action && (unsigned)ple.selected < deque_size(ple.songs)) {
            if (ple_mutate(ple.dest, deque_at(ple.songs, (unsigned)ple.selected), 0))
                ple_reload();
            else
                toast("Could not save playlist");
        }
        ple.mode = 0;
    }
    ple_show();
    return 0;
}
static int ple_pick(void *ctx, void *event) {
    if (!ple.timer) {
        ple.pending = (int)(long)ctx;
        ple.timer = timer_add(ple_run, 0, 0);
    }
    return 0;
}
/* The T9 box over the title, as Internet Radio's pull-down: page children go so the keyboard has
 * room. Its widget is named search_edit, so navigation.c's search_key closes the keyboard before
 * Return can destroy the edit under it. */
static int ple_searched(void *ctx, void *event) {
    widget_set_focused(ple.edit, 0);
    input_method_request(input_method(), 0); /* Detach the keyboard before rebuilding its owner. */
    if (!ple.timer) ple.timer = timer_add(ple_run, 0, 0);
    return 0;
}
static void ple_search_show(void) {
    widget_destroy_children(ple.page);
    ple.view = 0;
    ple.edit = edit_create(ple.page, "text", 2, 26);
    widget_set_name(ple.edit, "search_edit");
    t9_box(ple.page, ple.edit, "Search songs");
    if (*ple.query) widget_set_text_utf8(ple.edit, ple.query);
    widget_on(ple.edit, EVT_IM_ACTION, ple_searched, 0);
    widget_set_focused(ple.edit, 1);
}
static void ple_show(void) {
    int mode = ple.mode;
    if (mode == 4) {
        ple_search_show();
        return;
    }
    ple.edit = 0;
    int n = mode == 0   ? (int)deque_size(ple.songs) + 2
            : mode == 1 ? (ple.nmatch ? ple.nmatch + 1 : 2)
            : mode == 2 ? 3
                        : 2;
    char title[180], name[512];
    tk_snprintf(title, sizeof title, "%s: %s",
                mode == 0   ? "Editing"
                : mode == 1 ? "Add Songs"
                : mode == 2 ? "Song Actions"
                            : "Remove song?",
                ple.name);
    /* a bar that holds captions has room for the task's name alone, and no first line for it */
    ple.view = page_list(ple.page, ple.page, 0,
                         mode == 1 && !theme()->page_title_h ? "Add Songs" : title, n, 48);
    static const char *const actions[] = { "Move Up", "Move Down", "Remove from Playlist" };
    if (mode == 1) {
        char search[160];
        tk_snprintf(search, sizeof search, "%s%s", *ple.query ? "Search: " : "Search", ple.query);
        page_row_detail(ple.view, 0, search, 0, ple_pick);
        /* a tick on a song the playlist already holds: same path and CUE segment */
        unsigned mask, *in = ple.nmatch ? rec_index(ple.songs, &mask) : 0;
        if (!ple.nmatch)
            page_row_detail(ple.view, 1, "No songs match", 0, 0);
        else
            for (int i = 0; i < ple.nmatch; i++) {
                void *row = page_row_detail(
                    ple.view, i + 1, track_name(name, sizeof name, ple.match[i]), 0, ple_pick);
                if (in && rec_find(ple.songs, in, mask, ple.match[i])) ple_tick(row);
            }
        free(in);
    } else
        for (int i = 0; i < n; i++) {
            const char *caption =
                mode == 0 && i < 2 ? (i ? "Done" : "Add Songs")
                : mode == 2        ? actions[i]
                : mode == 3 ? (i ? "Cancel" : "Remove from Playlist")
                            : track_name(name, sizeof name,
                                         deque_at(ple.songs, (unsigned)(i - (mode == 0 ? 2 : 0))));
            page_row_detail(ple.view, i, caption, 0, ple_pick);
        }
    /* only the selected row's long title moves, as Songs' */
    for (int i = 0; !geom_val(V_TOOLBAR) && i < (int)widget_count_children(ple.view); ++i)
        hscroll_label_set_only_focus(widget_get_child(widget_get_child(ple.view, i), 0), 1);
    if (n) ringnav_select(ple.view, mode == 0 ? ple.selected + 2 < n ? ple.selected + 2 : 0 : 0, n);
}
static int ple_open(void *ctx, void *event) {
    if (ple.page) return 0;
    unsigned at = (unsigned)(long)ctx;
    void *lists = P(p_deque_playlist, 0);
    if (!lists || at >= deque_size(lists)) return 0;
    void *r = deque_at(lists, at);
    ple.dest = I(r, REC_DISC);
    if (ple.dest < 0 || ple.dest >= 100) return 0;
    tk_snprintf(ple.name, sizeof ple.name, "%s", P(r, REC_TITLE));
    ple.page = page_open("playlistedit_page", ple_closed, ple_back);
    if (ple.page) {
        ple_reload();
        ple_show();
    }
    return 0;
}
extern int stock_playlistsmore_trampoline(void *, void *);
/* Stock's allocator assumes ID order; Library presents headers in name order. Allocate under
 * the database lock instead, and report a failed insert instead of pretending it succeeded. */
static int playlist_used(void *ctx, int n, char **v, char **names) {
    if (n != 1 || !v[0]) return 1;
    int id = atoi(v[0]);
    if (id >= 0 && id < 100) ((unsigned char *)ctx)[id] = 1;
    return 0;
}
int ringnav_playlist_create(void *list, const char *name) {
    void *db = 0;
    unsigned char used[100] = { 0 };
    char title[64], quoted[127], sql[512];
    int result = -1, id = 0;
    if (!list || pl_db(&db)) goto done;
    if (sqlite3_exec(db, "begin immediate", 0, 0, 0)) goto done;
    if (sqlite3_exec(db, "select id from playlist where id <= 100", playlist_used, used, 0))
        goto rollback;
    for (; id < 100 && used[id]; ++id) {}
    if (id == 100) goto rollback;
    if (name && *name)
        tk_snprintf(title, sizeof title, "%s", name);
    else
        tk_snprintf(title, sizeof title, "Playlist%d", id);
    unsigned j = 0;
    for (unsigned i = 0; title[i]; ++i) {
        quoted[j++] = title[i];
        if (title[i] == '\'') quoted[j++] = '\'';
    }
    quoted[j] = 0;
    int exists = 0;
    tk_snprintf(sql, sizeof sql, "select count(*) from playlist where id <= 100 and filename='%s'",
                quoted);
    /* As stock, the whole name: a cut one matching would make importPlayList file its songs
     * under -2. */
    if ((!name || strlen(name) < sizeof title) && sqlite3_exec(db, sql, ple_number, &exists, 0))
        goto rollback;
    if (exists) {
        result = -2;
        goto rollback;
    }
    tk_snprintf(sql, sizeof sql,
                "insert into playlist values (%d,'%s','','','','','%s','','',0,0,%d,0,0,0,0,0,0);"
                "select changes()",
                id, quoted, quoted, id);
    if (sqlite3_exec(db, sql, ple_changed, 0, 0) || sqlite3_exec(db, "commit", 0, 0, 0))
        goto rollback;
    result = id;
    goto done;
rollback:
    sqlite3_exec(db, "rollback", 0, 0, 0);
done:
    if (db) sqlite3_close(db);
    return result;
}

/* A row action's confirm: under Classic its tray, the second cell saying verb. The prompt's
 * own text isn't read. */
static int confirm_over(void *prompt, const char *verb) {
    st.confirm_verb = verb;
    int answer = navigator_to_with_context("dialog/confirminfo_dialog", prompt);
    st.confirm_verb = (const char *)0;
    st.confirm_win = (void *)0;
    return answer;
}
int ringnav_playlist_delete(void *win, void *event) {
    void *list = P(p_deque_playlist, 0), *db = 0;
    int row = widget_get_prop_int(win, "_playlist", -1), ok = 0;
    if (!list || row < 0 || (unsigned)row >= deque_size(list)) return 0;
    int id = I(deque_at(list, (unsigned)row), REC_ID);
    if (id < 0 || id >= 100) return 0;
    struct {
        int kind, ms;
        char text[0x400];
    } prompt = { 1, 0, "Delete Playlist?" };
    if (confirm_over(&prompt, "Delete") != 10) return 0;
    char sql[256];
    if (pl_db(&db)) goto done;
    if (sqlite3_exec(db, "begin immediate", 0, 0, 0)) goto done;
    tk_snprintf(sql, sizeof sql,
                "delete from playlist where id=%d;select changes();"
                "delete from playlist where id>100 and cd_num=%d",
                id, id);
    if (sqlite3_exec(db, sql, ple_changed, 0, 0) || sqlite3_exec(db, "commit", 0, 0, 0)) {
        sqlite3_exec(db, "rollback", 0, 0, 0);
        goto done;
    }
    ok = 1;
done:
    if (db) sqlite3_close(db);
    if (ok) {
        g_delete_flag = 1;
        navigator_back();
    }
    toast(ok ? "Playlist deleted" : "Could not delete playlist");
    return 0;
}

int ringnav_playlistsmore(void *win, void *ctx) {
    int result = stock_playlistsmore_trampoline(win, ctx);
    if (win) {
        widget_set_prop_int(win, "_playlist", (int)ctx);
        /* Stock's row says Delete Song; this page deletes the whole playlist. Rename only this
         * label, as Now Playing's Delete Song still deletes the song. As its translation key, so a
         * re-translation keeps it (no language has the key, so it reads as given). */
        void *del = widget_lookup(win, "label_del", 1);
        if (del) widget_set_tr_text(del, "Delete Playlist");
        /* A row like this page's stock Rename and Delete: a 375 px list_item, the set's pitch high
         * (SET0 72), holding a 335 px s_btn_listitem, its body high (68), its 52px icon at x 10 and
         * its 20px label at x 72. Edit Playlist follows the Delete row at the same spacing. */
        int h = geom(G_PITCH), inner = geom(G_BODY);
        /* (tools/ipod.py: a set without toolbars has them from the top) */
        void *item = list_item_create(win, 0, (geom_val(V_TOOLBAR) ? 10 : 0) + 2 * h, 375, h);
        widget_use_style(item, "s_listitem_black");
        void *button = button_create(item, 20, 0, 335, inner);
        widget_use_style(button, "s_btn_listitem");
        widget_on(button, EVT_CLICK, ple_open, ctx);
        void *image = image_create(button, 10, 0, 52, inner);
        image_base_set_image(image, "rename");
        image_set_draw_type(image, IMAGE_DRAW_CENTER);
        void *label = hscroll_label_create(button, 72, 0, 253, inner);
        widget_use_style(label, "s_scrlabel_white20l");
        set_hscroll_label_attribute(label);
        widget_set_text_utf8(label, "Edit Playlist");
        /* A set without settings icons: none here either, the text at its column */
        void *const rows[][2] = { { widget_lookup(win, "img_rename", 1),
                                    widget_lookup(win, "label_rename", 1) },
                                  { widget_lookup(win, "img_del", 1), del },
                                  { image, label } };
        for (unsigned i = 0; !geom_val(V_SET_ICON) && i < 3; ++i) {
            void *c = rows[i][1];
            if (rows[i][0]) widget_set_visible(rows[i][0], 0, 0);
            if (!c) continue;
            /* the row's whole width, so its chevron is the theme's: on all but Delete */
            void *row = P(c, W_PARENT), *row_item = row ? P(row, W_PARENT) : (void *)0;
            int x = geom_val(V_TEXT_X) - SET_STOCK_X;
            if (row_item) {
                widget_move_resize(row, 0, 0, 375, h);
                x = geom_val(V_TEXT_X);
                if (i != 1) widget_set_visible(image_create(row_item, 302, 0, 50, 0), 0, 0);
            }
            widget_move_resize(c, x, I(c, W_Y), I(c, W_W), I(c, W_H));
            theme_font(c, ROLE_TITLE, FONT_ROW);
        }
    }
    return result;
}

/* The row alone selected in the stock batch record, as player_addplayList (0x517b78) does. */
void qm_select(void) {
    batch_init_selectrecord((int)st.qm_rows);
    batch_set_selectitem((int)st.qm_idx);
}

/* Stock playlist rows use the queue in add mode 2. Guard their final dispatch as well. */
#define PLAYLIST_PICK ((int (*)(void *, void *))0x4b4850)
int qm_playlist_pick(void *ctx, void *event) {
    char click[0x30];
    if (!qm_record()) {
        toast("Queue unchanged");
        return 0; /* a STOP would leave the list's scroll_view holding its grab */
    }
    qm_select();
    /* Classic's picker has no dots: a tap at their place picks */
    return PLAYLIST_PICK(
        ctx, geom_val(V_TOOLBAR) ? event : pointer_event_init(click, EVT_CLICK, ctx, 0, 0));
}

int qm_playlist_gone(void *ctx, void *event) {
    st.qm_playlist = 0;
    return 0;
}

/* w's live EVT_CLICK handler fn, as its emitter holds it (ctx @0, fn @12), or 0. */
static void *click_bound(void *w, const void *fn) {
    void *emitter = w ? P(w, W_EMITTER) : (void *)0;
    for (void *item = emitter ? P(emitter, 0) : 0; item; item = P(item, EMIT_NEXT))
        if (I(item, EMIT_TYPE) == EVT_CLICK && P(item, 12) == fn && !B(item, EMIT_PENDING_REMOVE))
            return item;
    return (void *)0;
}

static int pl_pick(void *ctx, void *event);
void qm_playlist_bind(void *w, int depth, int (*pick)(void *, void *)) {
    if (!w || depth == 16) return;
    void *item = click_bound(w, (void *)PLAYLIST_PICK) ?: click_bound(w, (void *)pl_pick);
    if (item) {
        void *ctx = P(item, 0), *fn = P(item, 12);
        widget_off_by_func(w, EVT_CLICK, fn, ctx);
        widget_on(w, EVT_CLICK, pick, ctx);
    }
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i)
        qm_playlist_bind(widget_get_child(w, i), depth + 1, pick);
}

/* Classic's Playlists: under a set without toolbars (toolbar 0) the page is rows, as Songs'. Stock
 * still builds everything: its toolbar, hidden (tools/ipod.py), and its Import and Export tile row,
 * hidden, stay as the click targets; the rows stock can't have are added after each rebuild and
 * click those hidden widgets, so its mode, marks, dialogs and reload stay its own. Stock's own
 * handlers, which the rows' dispatch looks up: */
enum { PL_SELECT_ALL, PL_NEW, PL_EDIT, PL_IMPORT, PL_EXPORT, PL_DELETE, PL_DONE, PL_N };
static const unsigned pl_stock[PL_N] = { 0x4b46b4, 0x4b45b4, 0x4b44e4, 0x4b5cf0,
                                         0x4b5b48, 0x4b59e0, 0x4b45fc };
/* on_common_keyup: the page's own static key-up until stock V1.34, which binds the shared one */
#define PLAYLIST_KEYUP ((int (*)(void *, void *))0x4f21b8)

/* A row's place on the list's two heights: name over second line in the page rows' boxes, which
 * hold their fonts; the dots or a mark at the right edge, centred on the picker's tick's column
 * (x 346), where the selected row's chevron (x 317) shows in their place. The text ends clear of
 * whichever shows. With Row Art its art leads, in an album row's column, the text after it. */
#define PL_MARK_X 333 /* the tick's column, the dots' hit */
void pl_fit(void *button) {
    const theme_t *T = theme();
    void *art = widget_lookup(button, PL_ART, 0);
    int h = I(button, W_H), width = 289, x = T->page_x;
    if (art && widget_get_visible(art)) {
        widget_move_resize(art, T->art_x, (h - geom(G_ART)) / 2, geom(G_ART), geom(G_ART));
        x = T->art_text_x - 2; /* hscroll_label's margin */
    }
    for (unsigned i = 0, n = widget_count_children(button); i < n; ++i) {
        void *c = widget_get_child(button, i);
        const char *name = widget_get_prop_str(c, "name", "");
        if (tk_str_start_with(name, "img_more") && widget_get_visible(c)) {
            widget_move_resize(c, 321, 0, 50, h);
        } else if (tk_str_start_with(name, "img_choice") && widget_get_visible(c)) {
            width = 309;
            widget_move_resize(c, PL_MARK_X, 0, 26, h);
            /* stock's mark is a disc: as a picker's, for paint_radio's tick */
            if (!tk_strcmp(widget_get_prop_str(c, "image", ""), "mulselect"))
                image_base_set_image(c, "select");
        }
    }
    void *info = widget_lookup(button, "view_info", 0);
    if (!info || widget_count_children(info) < 2) return;
    width -= x - T->page_x;
    widget_move_resize(info, x, 0, width, h);
    widget_move_resize(widget_get_child(info, 0), 0, T->page_cap_y, width, T->page_cap_h);
    widget_move_resize(widget_get_child(info, 1), 0, T->page_det_y, width, T->page_det_h);
}

/* Stock's row, as the list's other two-line rows: its art stays under Row Art (not in the
 * picker), its count reads "N Songs". */
static void *pl_art_image(void *button);
static void pl_row(void *item) {
    const theme_t *T = theme();
    char count[32];
    void *button = widget_get_child(item, 0), *info = widget_lookup(button, "view_info", 0);
    void *art = pl_art_image(button);
    int h = T->page_detail, add = widget_get_prop_int(window_of(item), PL_MODE, 0);
    if (!info || widget_count_children(info) < 2) return;
    widget_set_name(item, PAGE_TALL);
    widget_set_prop_int(item, PL_ROW, 1);
    widget_resize(item, I(item, W_W), h);
    widget_move_resize(button, 0, 0, 375, h);
    for (unsigned i = 0, n = widget_count_children(button); i < n; ++i) {
        void *c = widget_get_child(button, i);
        const char *name = widget_get_prop_str(c, "name", "");
        if (tk_str_start_with(name, "img_more")) {
            /* emptied under the theme: paint_radio draws the dots; a picker has none */
            image_base_set_image(c, "unselect");
            if (add) widget_set_visible(c, 0, 0);
        } else if (c == art) {
            widget_set_visible(c, geom(G_ART) && !add, 0);
        } else if (c != info && !tk_str_start_with(name, "img_choice"))
            widget_set_visible(c, 0, 0);
    }
    void *title = widget_get_child(info, 0), *second = widget_get_child(info, 1);
    const unsigned *s = widget_get_text(second);
    unsigned songs = 0;
    while (s && *s && (*s < '0' || *s > '9')) ++s;
    while (s && *s >= '0' && *s <= '9') songs = songs * 10 + (*s++ - '0');
    tk_snprintf(count, sizeof count, "%u %s", songs, songs == 1 ? "Song" : "Songs");
    widget_set_text_utf8(second, count);
    hscroll_label_set_only_focus(title, 1);
    theme_font(title, ROLE_TALL, FONT_ROW);
    row_meta(second);
    pl_fit(button);
}

/* Each row's click runs next pass: stock's handler may rebuild the rows under it (a dialog's close
 * does) and it finds its widget by handler, as nothing stock names. The row is the action, whatever
 * scrolled: no index reaches stock. */
static void pl_rows(void *win);
static int pl_page(void *win) {
    return win && !tk_strcmp(widget_get_prop_str(win, "name", ""), "playlist_page");
}
static void *pl_target(void *parent, unsigned fn) {
    for (unsigned i = 0, n = parent ? widget_count_children(parent) : 0; i < n; ++i)
        if (click_bound(widget_get_child(parent, i), (void *)fn))
            return widget_get_child(parent, i);
    return (void *)0;
}
/* The hidden widget stock binds action's handler to: in its toolbar, or its tile row. */
static void *pl_find(void *win, int action) {
    void *view = widget_lookup(win, "scroll_view", 1);
    return pl_target(widget_lookup(win, "view_navbar", 1), pl_stock[action])
               ?: pl_target(view && widget_count_children(view) ? widget_get_child(view, 0)
                                                                : (void *)0,
                            pl_stock[action]);
}
static unsigned pl_timer
    __attribute__((section(".scratch"))); /* pl_click's pending row, its action the timer's ctx */
static int pl_due(const void *info) {
    void *wm = window_manager(), *win = window_manager_get_top_window(wm);
    int action = (int)(long)P(info, TIMER_CTX);
    char click[0x30];
    pl_timer = 0;
    if (!pl_page(win)) return 0;
    void *w = pl_find(win, action);
    /* Import writes every song of every list it reads: the art's build steps aside (its reads
     * would make each insert wait), and the rebuild that follows asks again */
    if (w && action == PL_IMPORT) playlist_art_stop();
    if (w) stock_dispatch_trampoline(w, pointer_event_init(click, EVT_CLICK, w, 0, 0));
    /* a dialog's up, or gone: its close rebuilds the rows (Classic's: a toolbar's page has none) */
    if (window_manager_get_top_window(wm) == win && !geom_val(V_TOOLBAR)) pl_rows(win);
    return 0;
}
/* The latest row clicked in a pass is the one that runs. */
static int pl_click(void *action, void *event) {
    (void)event;
    stop_timer(&pl_timer);
    pl_timer = timer_add(pl_due, action, 0);
    return 0;
}
/* The row goes to index at, or after the rest when at < 0. */
static void pl_action(void *view, const char *caption, int action, int into, int at) {
    int h = geom(G_PITCH);
    void *item = list_item_create(view, 0, 0, 0, h);
    widget_use_style(item, "s_listitem_black");
    void *button = button_create(item, 0, 0, 375, h);
    widget_use_style(button, "s_btn_listitem");
    widget_set_name(button, PL_ACTION);
    widget_set_prop_int(item, PL_ACT, action + 1);
    widget_on(button, EVT_CLICK, pl_click, (void *)(long)action);
    void *label = hscroll_label_create(button, theme()->page_x, 0, 289, h);
    widget_use_style(label, "s_scrlabel_white20l");
    set_hscroll_label_attribute(label);
    hscroll_label_set_only_focus(label, 1);
    widget_set_text_utf8(label, caption);
    theme_font(label, ROLE_TITLE, FONT_ROW);
    if (into) widget_set_visible(image_create(button, 302, 0, 50, 0), 0, 0); /* page_into's */
    if (at >= 0) widget_restack(item, at);
}

/* Which rows the mode shows, their words, the bar's: on every rebuild, and after a click or mark.
 * Moving between normal and select mode the selection stays on its playlist; marks only add rows
 * after it, so the count that load() would reset it on follows. */
static void pl_rows(void *win) {
    void *view = widget_lookup(win, "scroll_view", 1), *nav = widget_lookup(win, "view_navbar", 1);
    void *lists = P(p_deque_playlist, 0);
    if (!view) return;
    int add = widget_get_prop_int(win, PL_MODE, 0), marking = !add && g_navbar_status == 1,
        marks = 0, was = widget_get_prop_int(win, PL_MARKING, 0),
        n = lists ? (int)deque_size(lists) : 0, count = (int)widget_count_children(view);
    for (int i = 0; marking && i < n; ++i) marks += batch_get_selectitem(i) == 1;
    char words[48];
    void *row[PL_N] = { 0 };
    int show[PL_N] = { marking,   !marking, !marking && n > 0, marking || !n, marks > 0,
                       marks > 0, marking };
    for (int i = 0; i < count; ++i) {
        void *item = widget_get_child(view, i);
        int action = widget_get_prop_int(item, PL_ACT, 0) - 1;
        if (action < 0 || action >= PL_N) continue;
        row[action] = item;
        widget_set_visible(item, show[action], 0);
        if (!show[action]) widget_move_resize(item, 0, 0, 375, geom(G_PITCH));
    }
    /* PAGE_TALL's the separators': every row shown but the list's last, a playlist's or ours */
    for (int i = count - 1, last = 1; i >= 0; --i) {
        void *item = widget_get_child(view, i);
        if (!widget_get_prop_int(item, PL_ACT, 0) && !widget_get_prop_int(item, PL_ROW, 0))
            continue;
        int shown = widget_get_visible(item);
        widget_set_name(item, shown && !last ? PAGE_TALL : "");
        if (shown) last = 0;
    }
    if (row[PL_SELECT_ALL])
        widget_set_text_utf8(widget_get_child(widget_get_child(row[PL_SELECT_ALL], 0), 0),
                             B(G_ALLSELECT_FLAG, 0) ? "Deselect All" : "Select All");
    for (int i = PL_EXPORT; marks && i <= PL_DELETE && row[i]; ++i) {
        tk_snprintf(words, sizeof words, "%s %d Playlist%s%s", i == PL_EXPORT ? "Export" : "Delete",
                    marks, marks == 1 ? "" : "s", i == PL_EXPORT ? "" : "\xe2\x80\xa6");
        widget_set_text_utf8(widget_get_child(widget_get_child(row[i], 0), 0), words);
    }
    /* the bar reads the toolbar's first label, which stock writes with the last list opened */
    tk_snprintf(words, sizeof words,
                marks     ? "%d Selected"
                : marking ? "Edit Playlists"
                          : "Playlists",
                marks);
    if (nav && widget_count_children(nav)) widget_set_text_utf8(widget_get_child(nav, 0), words);
    bar_title_sync(win);
    void *none = widget_lookup(win, PL_NONE, 0);
    if (!none && !n) {
        none = text(win, 0, 124, 375, 32);
        widget_set_name(none, PL_NONE);
        widget_set_text_utf8(none, "No Playlists");
        theme_font(none, ROLE_TITLE, FONT_ROW);
        widget_set_prop_int(none, "style:normal:text_color", (int)SPOT_GREY);
    }
    if (none) widget_set_visible(none, !n, 0);
    widget_set_prop_int(win, PL_MARKING, marking);
    if (!marking && marking == was) return;
    /* Stock showed its marks or the dots: the rows go to their places, the list to its new height.
     */
    for (int i = 1; i < count; ++i) {
        void *item = widget_get_child(view, i);
        if (widget_get_prop_int(item, PL_ROW, 0)) pl_fit(widget_get_child(item, 0));
    }
    widget_layout(view);
    if (load_rows(&g_menu, view)) {
        int id = widget_get_prop_int(view, SEL, -1);
        /* select mode's rows: Select All, the playlists, its actions; the list's: New, Edit (with
         * playlists), the playlists. Leaving, Select All goes to the first playlist, an action to
         * Edit (Import on an empty list). */
        if (marking != was)
            id = marking ? (n ? 1 : 0) : id <= 0 ? (n ? 2 : 0) : id <= n ? id + 1 : 1;
        prop(view, COUNT, g_menu.rows);
        prop(view, SEL, id);
        if (marking != was) reveal(&g_menu, id, 1);
    }
    widget_invalidate_force(win, (void *)0);
}
/* With nothing remembered (a first open, or a count changed) Classic's Playlists starts on its
 * first playlist, below New and Edit, as stock's and the iPod's lists of playlists alone do; an
 * empty list on New. */
int pl_first(menu_t *m, int cur) {
    for (int i = cur; i < m->n && widget_get_prop_int(P(m->at[cur], W_PARENT), PL_ACT, 0); ++i)
        if (widget_get_prop_int(P(m->at[i], W_PARENT), PL_ROW, 0)) return i;
    return cur;
}
/* The second handler of a playlist row, after stock's marked it. */
static int pl_marked(void *win, void *event) {
    (void)event;
    if (g_navbar_status == 1) pl_rows(win);
    return 0;
}
/* Stock's row click opens More from x 286 outside select mode (0x4b48b8, 0x4b4998): a picker has
 * no More, so a tap anywhere picks; in the list the dots' column at the edge (PL_MARK_X on) stays
 * More, and a tap left of it, the chevron's column (from x 317) too, opens the playlist. The band's
 * row draws its chevron and no dots, so it opens to the edge; its More is the hold's. A pick or an
 * open is a fresh click at x 0. */
static int pl_pick(void *ctx, void *event) {
    char click[0x30];
    int lit = st.sel_accent && (ctx == st.sel_accent || P(ctx, W_PARENT) == st.sel_accent);
    if (!widget_get_prop_int(window_of(ctx), PL_MODE, 0) &&
        (g_navbar_status == 1 || (!lit && I(event, EVENT_X) >= PL_MARK_X)))
        return PLAYLIST_PICK(ctx, event);
    return PLAYLIST_PICK(ctx, pointer_event_init(click, EVT_CLICK, ctx, 0, 0));
}
/* Return in select mode is Done, not the page; without Done's target it is stock's. */
static int pl_keyup(void *win, void *event) {
    if (I(event, EVENT_KEY) == KEY_RETURN && g_navbar_status == 1 && pl_find(win, PL_DONE)) {
        pl_click((void *)PL_DONE, event);
        return STOP;
    }
    return PLAYLIST_KEYUP(win, event);
}
/* Each playlist row's art (playlist_art.c): stock's 52 px image, its placeholder till the art's
 * file is made, then that file by name. The page whose rows hear of each one made: */
static void *pl_art_page __attribute__((section(".scratch")));
/* A row's art image: stock's unnamed one, named once found by its placeholder. */
static void *pl_art_image(void *button) {
    void *art = widget_lookup(button, PL_ART, 0);
    for (unsigned i = 0, n = widget_count_children(button); !art && i < n; ++i) {
        void *c = widget_get_child(button, i);
        if (!tk_strcmp(widget_get_prop_str(c, "image", ""), "playlist_default"))
            widget_set_name(art = c, PL_ART);
    }
    return art;
}
/* The row's index in p_deque_playlist: stock names its button by it (our action rows and its
 * tile row by none, -1: AWTK gives an unnamed widget's name as NULL, not the default). */
static int pl_at(void *button) {
    const char *name = widget_get_prop_str(button, "name", "");
    return name && *name >= '0' && *name <= '9' ? atoi(name) : -1;
}
/* The row's playlist */
static void *pl_record(void *button) {
    void *lists = P(p_deque_playlist, 0);
    int at = pl_at(button);
    return lists && at >= 0 && at < (int)deque_size(lists) ? deque_at(lists, (unsigned)at) : 0;
}
/* Playlist cd_num's index in the live list (p_deque_playlist), its size when it is gone: the list
 * may have been read again since Cover Flow copied it. */
unsigned playlist_index(int cd_num) {
    void *lists = P(p_deque_playlist, 0);
    unsigned at = 0, n = lists ? deque_size(lists) : 0;
    while (at < n && I(deque_at(lists, at), REC_DISC) != cd_num) ++at;
    return at;
}
/* A shown row's art, when its file is made; else its build is queued. The songs are read again
 * once, by the worker, at the list's first ask: none when no row shows art. */
static void pl_art_row(void *button, int *asked) {
    char url[600];
    void *art = pl_art_image(button), *r = art && widget_get_visible(art) ? pl_record(button) : 0;
    if (!r) return;
    if (!*asked) playlist_art_refresh();
    *asked = 1;
    image_base_set_image(art, playlist_art(I(r, REC_DISC), P(r, REC_TITLE), 1, url, sizeof url)
                                  ? url
                                  : "playlist_default"); /* none, or not made yet: never the last */
}
/* One playlist's art made: its rows alone ask again, as an ask may queue a failed one anew. */
static void pl_art_ready(int cd_num) {
    void *view = pl_art_page ? widget_lookup(pl_art_page, "scroll_view", 1) : 0;
    int asked = 1;
    for (unsigned i = 0, n = view ? widget_count_children(view) : 0; i < n; ++i) {
        void *item = widget_get_child(view, i), *r;
        void *button = widget_count_children(item) ? widget_get_child(item, 0) : 0;
        if (button && pl_art_image(button) && (r = pl_record(button)) && I(r, REC_DISC) == cd_num)
            pl_art_row(button, &asked);
    }
}
/* 1 when a row asked, the songs' read asked for */
static int pl_arts(void *win) {
    void *view = widget_lookup(win, "scroll_view", 1);
    int asked = 0;
    pl_art_page = win;
    playlist_art_ready = pl_art_ready;
    for (unsigned i = 0, n = view ? widget_count_children(view) : 0; i < n; ++i) {
        void *item = widget_get_child(view, i);
        if (widget_count_children(item)) pl_art_row(widget_get_child(item, 0), &asked);
    }
    return asked;
}

/* Display's Playlist View: Cover Flow: the page, opened unseen, gets the playlists' covers over it
 * on the next pass (coverflow_playlists), then shows under them for their Return (navigation.c's
 * Artist View, cf_veil). The page pending them: */
static void *pl_cf_win __attribute__((section(".scratch")));
static unsigned pl_cf_timer __attribute__((section(".scratch")));
static int pl_cf_open(const void *unused) {
    void *win = pl_cf_win;
    pl_cf_timer = 0;
    pl_cf_win = 0;
    if (win == window_manager_get_top_window(window_manager()))
        coverflow_playlists(P(p_deque_playlist, 0));
    if (win) cf_veil(win, 0);
    return 0;
}
/* Cover Flow's Centre on playlist cd_num, under Minimal and Modern (and Classic's longer ones):
 * its row's open, a tap at x 0, on this page under the covers, its list opening over them. Stock's
 * click reads its one add mode global, which an Add to Playlist picker over the covers leaves at 1
 * or 2 (it would add to the playlist, not open it): this page's own goes back first, as
 * ringnav_playlist does. Stock finds the row by its place in the live list, in name order, read
 * again by any Playlists page since (the picker's too), and this page's rows are the list as it
 * was built: a playlist whose place is past them (one added since sorts in by name and pushes the
 * last ones past) has no row, and the covers close for the page, which builds its rows again as
 * it comes to the front. */
void playlist_open(void *win, int cd_num) {
    void *view = widget_lookup(win, "scroll_view", 1);
    int at = (int)playlist_index(cd_num);
    char click[0x30];
    I(PLAYLIST_ADD_MODE, 0) = widget_get_prop_int(win, PL_MODE, 0);
    for (unsigned i = 0, n = view ? widget_count_children(view) : 0; i < n; ++i) {
        void *item = widget_get_child(view, i);
        void *button = widget_count_children(item) ? widget_get_child(item, 0) : 0;
        if (button && pl_at(button) == at) {
            stock_dispatch_trampoline(button, pointer_event_init(click, EVT_CLICK, button, 0, 0));
            return;
        }
    }
    coverflow_playlists_close();
}

/* A playlist cover's hold: New Playlist (edit 0) or Edit Playlists… (Classic's alone: its select
 * mode) on this page, the covers closed over it. New is the toolbar's or Classic's row's target. */
void playlists_edit(int edit) {
    if (coverflow_playlists_close()) pl_click((void *)(long)(edit ? PL_EDIT : PL_NEW), 0);
}

/* Stock leaves its mode set for a page gone in it (held Return, to Home); its art's builds stop. */
static int pl_gone(void *win, void *event) {
    (void)event;
    if (win == pl_cf_win) {
        stop_timer(&pl_cf_timer);
        pl_cf_win = 0;
    }
    if (widget_get_prop_int(win, PL_MARKING, 0)) g_navbar_status = 0;
    if (win == pl_art_page) {
        playlist_art_stop();
        pl_art_page = 0;
    }
    return 0;
}
static void pl_dress(void *win, int first) {
    void *view = widget_lookup(win, "scroll_view", 1);
    int add = widget_get_prop_int(win, PL_MODE, 0);
    if (!view) return;
    /* the rows' click goes through pl_pick first (qm's guarded picker keeps its own), then select
     * mode's second handler, pl_marked: both are appended, and to stock's fresh rows, as
     * ringnav_playlist dresses only after stock's rebuild, whose row button has PLAYLIST_PICK
     * alone, so the rebind leaves pl_pick its one handler and pl_marked goes on after it */
    if (win != st.qm_playlist) qm_playlist_bind(win, 0, pl_pick);
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i);
        if (!i && !add) {
            widget_set_visible(item, 0, 0);
            continue;
        }
        pl_row(item);
        if (!add) widget_on(widget_get_child(item, 0), EVT_CLICK, pl_marked, win);
    }
    /* New and Edit head the list, after the hidden tile row (a picker has none); Select All, select
     * mode's first row, sits with them, as the two modes never show both */
    if (add) {
        pl_action(view, "New Playlist\xe2\x80\xa6", PL_NEW, 1, 0);
    } else {
        pl_action(view, "Select All", PL_SELECT_ALL, 0, 1);
        pl_action(view, "New Playlist\xe2\x80\xa6", PL_NEW, 1, 2);
        pl_action(view, "Edit Playlists\xe2\x80\xa6", PL_EDIT, 1, 3);
        pl_action(view, "Import Playlists", PL_IMPORT, 0, -1);
        pl_action(view, "Export", PL_EXPORT, 0, -1);
        pl_action(view, "Delete", PL_DELETE, 0, -1);
        pl_action(view, "Done", PL_DONE, 0, -1);
    }
    pl_rows(win);
    if (first && !add) { /* stock binds it with ctx win (0x4b5efc) */
        widget_off_by_func(win, EVT_KEY_UP, (void *)PLAYLIST_KEYUP, win);
        widget_on(win, EVT_KEY_UP, pl_keyup, win);
        widget_on(win, EVT_DESTROY, pl_gone, win);
    }
}

/* After stock rebuilds its rows: the guarded Now Playing picker's rebind, Classic's rows, under
 * Row Art (Classic's alone) the rows' art, not the Add to Playlist picker's, a list as it was, and
 * Playlist View's covers. Stock's add mode is one global, left by any picker that closes, which it
 * reads as this page comes back: each page keeps the one it opened with. */
int ringnav_playlist(void *win) {
    int mode = widget_get_prop_int(win, PL_MODE, -1), first = mode < 0;
    if (first)
        widget_set_prop_int(win, PL_MODE, I(PLAYLIST_ADD_MODE, 0));
    else
        I(PLAYLIST_ADD_MODE, 0) = mode;
    int result = stock_playlist_trampoline(win);
    if (win == st.qm_playlist) qm_playlist_bind(win, 0, qm_playlist_pick);
    void *lists = P(p_deque_playlist, 0);
    int list = !widget_get_prop_int(win, PL_MODE, 0), toolbar = geom_val(V_TOOLBAR), read = 0;
    /* Playlist View: Cover Flow, once, as the page opens (from Library, Home's list or its
     * shortcut): coming back to it shows the list; with no playlists the list, New Playlist its way
     * on. The covers are the playlists' art, so they build it in every theme, Row Art or not. */
    int covers = first && list && lists && deque_size(lists) && ringnav_playlist_coverflow();
    if (toolbar && !covers) return result; /* Minimal, Modern: stock's rows, no art built */
    if (toolbar) { /* the rows stay stock's; the covers' builds stop with the page */
        widget_on(win, EVT_DESTROY, pl_gone, win);
        pl_art_page = win, playlist_art_ready = 0;
    } else {
        pl_dress(win, first);
        if (list && (covers || geom(G_ART))) read = pl_arts(win);
    }
    if (!covers) return result;
    if (!read) playlist_art_refresh(); /* the covers' counts and art: the songs read, once */
    cf_veil(win, 1);
    pl_cf_win = win;
    rearm(&pl_cf_timer, pl_cf_open, 0);
    return result;
}
