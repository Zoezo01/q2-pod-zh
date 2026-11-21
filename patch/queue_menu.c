/* The queue menu: Play/Pause held on a song, album, artist, composer or genre, folder or playlist
 * row, a Cover Flow cover or Now Playing opens stock's sortselect dialog rebuilt as that row's menu
 * (docs/internals.md#queue-menu): Play Next, Add to Queue, Shuffle, Favourites, Rate, Add to
 * Playlist, Go to, Now Playing's play modes and More…. */
#include "navigation.h"

/* The menu's row (qm_record): its kind (QM_*), the class its list holds, its record's and the
 * browsing state's hashes, Now Playing's queue and its hash; the action picked, the play mode
 * being chosen (qm_choice, 0 none), a playlist cover's name (the covers' own: the live list may be
 * older), the deferred open or run, and g_local_classinfo_save before a Go to. navigation.h's st
 * has the hold's press, the row's place, the dialog and Play Next's forced pick. */
static struct {
    unsigned qm_timer, qm_cls, qm_hash, qm_browse;
    int qm_kind, qm_action, qm_choice;
    char qm_name[96];
    void *qm_queue;
    unsigned qm_queue_hash;
    unsigned char qm_classinfo[912];
} qm __attribute__((section(".scratch")));

/* Play/Pause hold queue menu (docs/internals.md). Stock long press fires once per press, so a hold
 * on a local song, album, artist/composer/genre, folder or playlist row opens the stock sortselect
 * dialog rebuilt as that row's menu. */
enum {
    QM_SONG = 1,
    QM_ALBUM,
    QM_GROUP,
    QM_FOLDER,
    QM_COVERFLOW,
    QM_COVERALBUM,
    QM_PLAYING,
    QM_PLAYLIST
};
enum {
    QA_NEXT = 1,
    QA_ADD,
    QA_SHUFFLE,
    QA_FAV,
    QA_UNFAV,
    QA_PLAYLIST,
    QA_ALBUM,
    QA_ARTIST,
    QA_ADV_SHUFFLE,
    QA_REPEAT,
    QA_GROUP,
    QA_NEXT_GROUP,
    QA_PREV_GROUP,
    QA_RATE,
    QA_RESUME,
    QA_QUEUE,
    QA_FOLDER,
    QA_CHAPTERS,
    QA_MORE,
    QA_NEW_PLAYLIST,
    QA_EDIT_PLAYLISTS
};

/* Everything a row's tracks are resolved from; a change while the menu is open cancels it. */
static unsigned browse_hash(void) { return local_hash(hash_bytes(FNV_SEED, g_folder_path, 1024)); }

/* Coverflow's or Most Played's tracks, when page is theirs. */
static void *page_tracks(void *page) {
    return !page                ? (void *)0
           : page == st.mp_page ? st.mp_list
           : page == st.rt_page ? st.rt_list
                                : coverflow_tracks(page);
}

static void *qm_list(void) {
    if (qm.qm_kind == QM_PLAYING) return P(mcl_pdeqplaylist, 0);
    if (qm.qm_kind == QM_PLAYLIST) return P(p_deque_playlist, 0);
    return qm.qm_kind == QM_COVERFLOW ? page_tracks(window_manager_get_top_window(window_manager()))
                                      : P(p_deque_showlist, 0);
}

void *qm_record(void) {
    if (qm.qm_kind == QM_PLAYING) {
        unsigned at, n;
        void *r = queue_now(&at, &n), *q = P(mcl_pdeqplaylist, 0);
        unsigned type = (unsigned)MCL(MCL_TYPE);
        return r && q == qm.qm_queue && at == st.qm_idx && n == st.qm_rows &&
                       (type == 1 || (type & 0xf000) == 0xf000) && airplayGetFlag() != 2 &&
                       rec_hash(r) == qm.qm_hash && queue_hash(q) == qm.qm_queue_hash
                   ? r
                   : 0;
    }
    if (qm.qm_kind == QM_COVERALBUM) {
        void *r = coverflow_album(window_manager_get_top_window(window_manager()));
        return r && rec_hash(r) == qm.qm_hash ? r : 0;
    }
    void *list = qm_list();
    if (!list || deque_size(list) != st.qm_rows || st.qm_idx >= st.qm_rows ||
        (qm.qm_kind < QM_COVERFLOW && browse_hash() != qm.qm_browse))
        return (void *)0;
    void *r = deque_at(list, st.qm_idx);
    return r && rec_hash(r) == qm.qm_hash ? r : (void *)0;
}

/* The queue menu's album's album_id, 0 when not an album it rates: a Coverflow cover's, a Library
 * Albums row's (split as Coverflow's), an artist's albums tab row's (ringnav_artist_list). */
unsigned qm_album_id(void *r) {
    if (qm.qm_kind == QM_COVERALBUM || (qm.qm_kind == QM_ALBUM && qm.qm_cls == CLASS_ALBUMS))
        return album_id(r);
    return qm.qm_kind == QM_ALBUM && qm.qm_cls == CLASS_ARTIST_ALBUMS && st.art_ids &&
                   st.art_n == st.qm_rows && st.qm_idx < st.art_n
               ? st.art_ids[st.qm_idx]
               : 0;
}

static void qm_close(void) {
    void *dialog = st.qm_dialog;
    st.qm_dialog = (void *)0;
    if (dialog) window_close(dialog);
}

/* Return and the title bar arrow dismiss without the stock sort-change flag. */
static int qm_back(void *dialog, void *event) {
    if (I(event, EVENT_TYPE) == EVT_KEY_UP && I(event, EVENT_KEY) != KEY_RETURN) return 0;
    qm_close();
    return STOP;
}

static int qm_gone(void *dialog, void *event) {
    if (st.qm_dialog == dialog) st.qm_dialog = (void *)0;
    return 0;
}

/* A playlist's songs, as its row opens them (cd_num, as ple_open). */
static int qm_query(void *r) {
    return qm.qm_kind == QM_PLAYLIST ? getMusicByPlayList(I(r, REC_DISC))
                                     : class_query(qm.qm_cls, r);
}

static int dir_query(void *path) { return toolsLoadDirectory((const char *)path); }
/* A folder's songs and, in stock's listing order, its subfolders' (dot folders such as .tidal
 * left out), so a folder of albums queues them all.
 * ponytail: one toolsLoadDirectory per folder on the UI thread; a big tree takes a while. */
static void folder_tracks(const char *path, void *add, int depth) {
    int n;
    void *rows = staged(dir_query, (void *)path, &n);
    for (unsigned i = 0; i < deque_size(rows); ++i) {
        void *t = deque_at(rows, i);
        const char *name = P(t, REC_NAME);
        if (I(t, REC_TYPE) == 8 && !playback_video(t))
            _deque_push_back(add, t);
        else if (I(t, REC_TYPE) == 4 && depth < 8 && name && *name != '.') {
            char sub[1024];
            if ((unsigned)tk_snprintf(sub, sizeof sub, "%s/%s", path, name) < sizeof sub)
                folder_tracks(sub, add, depth + 1);
        }
    }
    deque_destroy(rows);
}

/* The tracks stock would play for that row, in its order; a folder's take in its subfolders. */
static void qm_tracks(void *r, void *add) {
    int n;
    if (qm.qm_kind == QM_FOLDER) {
        char path[1024];
        if ((unsigned)tk_snprintf(path, sizeof path, "%s/%s", (const char *)g_folder_path,
                                  (const char *)P(r, REC_NAME)) >= sizeof path)
            return; /* cut: a prefix is another folder's path, so nothing, as folder_tracks does */
        ringnav_wdt_long();
        folder_tracks(path, add, 0);
        return;
    }
    void *rows = staged_music(qm_query, r, &n);
    if (qm.qm_kind == QM_PLAYLIST) music_records(rows, 0); /* as its opened list (0xff20) */
    for (unsigned i = 0; i < deque_size(rows); ++i) {
        void *t = deque_at(rows, i);
        if (I(t, REC_TYPE) == 8) _deque_push_back(add, t);
    }
    deque_destroy(rows);
}

/* The row's tracks: the song itself, or what stock would play for the row; an Albums row's own
 * album alone (getMusicByAlbum finds every album of the name), in album order, as Coverflow's. */
void *qm_collect(void *r) {
    if (qm.qm_kind == QM_COVERALBUM || (qm.qm_kind == QM_ALBUM && qm.qm_cls == CLASS_ALBUMS))
        return coverflow_album_tracks(r);
    void *add = song_list();
    if (qm.qm_kind == QM_SONG || qm.qm_kind == QM_COVERFLOW || qm.qm_kind == QM_PLAYING) {
        if (!playback_video(r)) _deque_push_back(add, r);
    } else
        qm_tracks(r, add);
    return add;
}

/* Insert at pos+1 or append; playback_queue_insert keeps every queue index right. Shuffle picks at
 * random, so the first inserted track is forced once (ringnav_shuffle). */
static void qm_insert(void *queue, void *add, int next) {
    unsigned at = playback_queue_insert(queue, add, next);
    if (next) {
        /* stock Shuffle's only: advanced Shuffle Songs (also MCL_MODE 2) plays its own order */
        st.qm_forced = MCL(MCL_MODE) == 2 && !playback_option(0) ? at + 1 : 0;
        st.qm_forced_hash = rec_hash(deque_at(queue, at));
    }
}

static int qm_apply(void *add, int next) {
    void *queue = P(mcl_pdeqplaylist, 0);
    if (!queue || airplayGetFlag() == 2) return 0;
    unsigned size = deque_size(queue), type = (unsigned)MCL(MCL_TYPE), n = deque_size(add);
    /* Only a local (folder or library) queue grows; streams keep theirs. */
    if (size && type != 1 && (type & 0xf000) != 0xf000) return 0;
    int album = qm.qm_kind == QM_COVERFLOW || qm.qm_kind == QM_COVERALBUM;
    if (n && !size) { /* loads without starting playback; only a real folder is class 1 */
        if (album) album_memory(deque_at(add, 0));
        mclLoadPlayList(add, 0, qm.qm_kind == QM_FOLDER ? 1 : album ? 0xff10 : (int)qm.qm_cls);
    } else if (n)
        qm_insert(queue, add, next);
    return n != 0;
}

/* Go to album/artist reuse g_local_classinfo_save, which the pages underneath reload from when
 * they come back, so it is put back as the page opened here closes. */
static int qm_restore(void *page, void *event) {
    memcpy((void *)g_local_classinfo_save, qm.qm_classinfo, sizeof qm.qm_classinfo);
    return 0;
}

/* Now Playing's More rows, for this record: Album info (0x5397cc) fills the album query and opens
 * playerjumpinfo_page; Artist info opens artistinfo_page with {class, record}. A song played in
 * Coverflow, or with Artist albums: Coverflow, goes to the album's tracks in Coverflow instead, as
 * Go to Artist reaches its covers. A card app's song goes to the app's page when it offers one
 * (addon.c addon_goto). */
static void qm_goto(void *r, int album) {
    unsigned char *info = (unsigned char *)g_local_classinfo_save;
    void *wm = window_manager(), *page = window_manager_get_top_window(wm);
    /* a card app's song (Qobuz's): the app's own page for its album or artist, when it says so */
    if (addon_goto(P(r, REC_PATH), P(r, album ? REC_ALBUM : REC_ARTIST), album)) return;
    if (album && (navigator_window_is_exist("coverflow_page") || pod_value(POD_ARTIST_CF)) &&
        coverflow_goto_album(r))
        return;
    memcpy(qm.qm_classinfo, info, sizeof qm.qm_classinfo);
    if (album) {
        info[9] = 0;
        tk_snprintf((char *)info + 0xd, 0x100, "%s", (const char *)P(r, REC_ALBUM));
        tk_snprintf((char *)info + 0x10d, 0x100, "%s", (const char *)P(r, REC_ARTIST));
        *(int *)info = 0xff10;
        album_open(r);
        navigator_to("playerjumpinfo_page");
    } else {
        struct {
            int cls;
            void *r;
        } context = { (int)qm.qm_cls, r };
        navigator_to_with_context("localmusic/artistinfo_page", &context);
    }
    void *top = window_manager_get_top_window(wm);
    if (top != page) widget_on(top, EVT_WINDOW_CLOSE, qm_restore, top);
}

/* Deferred so the dialog is never closed under its own click dispatch. */
static int qm_open(const void *unused);

/* New Playlist (edit 0) and Select Playlists… run once the dialog is destroyed, so the Playlists
 * page is on top however its close animates (playlists.c's click acts on the top page only), and
 * from the pass after, so the covers never close inside the dialog's destroy. */
static int qm_edit_run(const void *info) {
    playlists_edit((int)(long)P(info, TIMER_CTX));
    return 0;
}
static int qm_edit_due(void *edit, void *event) {
    timer_add(qm_edit_run, edit, 0);
    return 0;
}

static int qm_run(const void *unused) {
    qm.qm_timer = 0;
    if (!st.qm_dialog) return 0;
    if (qm.qm_action == QA_NEW_PLAYLIST || qm.qm_action == QA_EDIT_PLAYLISTS)
        widget_on(st.qm_dialog, EVT_DESTROY, qm_edit_due,
                  (void *)(long)(qm.qm_action == QA_EDIT_PLAYLISTS));
    qm_close();
    void *r = qm_record();
    int a = qm.qm_action, cls = qm.qm_kind == QM_COVERFLOW ? 0xf001 : (int)qm.qm_cls;
    unsigned aid = r ? qm_album_id(r) : 0; /* an album's own Favourites and Rate */
    if (r && a >= 100) {
        if (!playback_set(qm.qm_choice - 1, a - 100)) toast("Queue unchanged");
        qm.qm_choice = 0;
    } else if (r && a == QA_CHAPTERS) {
        chapters_open(r);
    } else if (r && a == QA_QUEUE) {
        /* A shuffle's play order is not the list's: ours (Up Next). A Play Next stock's Shuffle was
         * holding (ringnav_shuffle) goes first in it, as advanced play has no forced pick. */
        if (playback_listable()) {
            void *queue = P(mcl_pdeqplaylist, 0);
            unsigned at = st.qm_forced - 1;
            st.qm_forced = 0;
            if (queue && at < deque_size(queue) &&
                rec_hash(deque_at(queue, at)) == st.qm_forced_hash)
                playback_move(at);
            up_next(page_keyup);
        } else
            navigator_to("playerqueue_page");
    } else if (a == QA_MORE && qm.qm_kind == QM_PLAYLIST) {
        if (r) navigator_to_with_context("localmusic/playlistsmore_page", (void *)(long)st.qm_idx);
    } else if (a == QA_NEW_PLAYLIST || a == QA_EDIT_PLAYLISTS) {
        /* the page's own, not the playlist's: qm_edit_due */
    } else if (a == QA_MORE) {
        void *more = np_more(); /* clicked as a tap would, so stock's own handler opens it */
        char click[0x30];
        if (more && !addon_more())
            stock_dispatch_trampoline(more, pointer_event_init(click, EVT_CLICK, more, 0, 0));
    } else if (r && (a == QA_NEXT_GROUP || a == QA_PREV_GROUP)) {
        if (!playback_group_skip(a == QA_NEXT_GROUP)) toast("No other group");
    } else if (aid && a == QA_RATE) {
        rate_show(window_manager_get_top_window(window_manager()), rate_kept(1, aid), aid);
    } else if (aid && (a == QA_FAV || a == QA_UNFAV)) {
        if (rate_saved(rate_keep(1, aid, a == QA_FAV ? 5 : 0))) rate_album_favs(r, a == QA_FAV);
    } else if (r && a == QA_RATE) {
        rate_open();
    } else if (r && a >= QA_ADV_SHUFFLE && a <= QA_GROUP) {
        qm.qm_choice = a - QA_ADV_SHUFFLE + 1;
        qm_open(0);
    } else if (r && a == QA_FAV) { /* tags a folder file as batch-select's Add to My Fav does */
        qm_select();
        batch_add_file(cls, 0xf00a, qm_list(), P(p_vector_select_record, 0), 0);
        ratings_copy_due();
        fav_told(r, 1);
    } else if (r && a == QA_UNFAV) {
        deleteMusicFromFav(r);
        ratings_copy_due();
        fav_told(r, 0);
        if (cls == 0xf00a)
            g_delete_flag = 1;          /* My Fav reloads, as Now Playing's heart (0x53cafc) */
    } else if (r && a == QA_PLAYLIST) { /* the playlist page's add mode adds the selected row */
        qm_select();
        navigator_to_with_context(
            "localmusic/playlist_page",
            (void *)(long)((qm.qm_kind == QM_PLAYING ? 0x20000 : 0x10000) | cls));
        if (qm.qm_kind == QM_PLAYING) {
            void *top = window_manager_get_top_window(window_manager());
            if (!tk_strcmp(widget_get_prop_str(top, "name", ""), "playlist_page")) {
                st.qm_playlist = top;
                qm_playlist_bind(top, 0, qm_playlist_pick);
                widget_on(top, EVT_DESTROY, qm_playlist_gone, top);
            }
        }
    } else if (r && a == QA_RESUME) { /* the list's songs from the chapter; the poll seeks */
        int at = 0, off = 0;
        void *list = qm_list(), *t = book_place(list, r, &at, &off), *songs;
        if (t && (songs = _create_deque("stSongInfo"))) {
            deque_init(songs);
            for (unsigned i = 0; i < deque_size(list); i++)
                if (I(deque_at(list, i), REC_TYPE) == 8) _deque_push_back(songs, deque_at(list, i));
            st.rs_go_key = listen_key(t);
            st.rs_go = off;
            st.rs_go_at = (unsigned)time_now_ms();
            play_folder(songs, at, 0xf001);
            deque_destroy(songs);
        } else
            toast("Queue unchanged");
    } else if (r && a == QA_FOLDER) {
        st.go_layer = song_folder(r, st.go_folder);
        if (st.go_layer)
            navigator_to("folder_page"); /* its init, ringnav_folder, opens there */
        else
            toast("Folder not found");
        st.go_layer = 0; /* never left for a later Folder visit */
    } else if (r && a >= QA_ALBUM)
        qm_goto(r, a == QA_ALBUM);
    else {
        void *add = r ? qm_collect(r) : (void *)0;
        if (!add || !(a == QA_SHUFFLE ? shuffle_play(add) : qm_apply(add, a == QA_NEXT)))
            toast("Queue unchanged");
        if (add) deque_destroy(add);
    }
    return 0;
}

/* Touch and centre both arrive as the row's click; the first one wins. */
static int qm_pick(void *action, void *event) {
    if (st.qm_dialog && !qm.qm_timer) {
        qm.qm_action = (int)(long)action;
        qm.qm_timer = timer_add(qm_run, (void *)0, 0);
    }
    return 0;
}

/* Deferred out of the long-press dispatch. The sortselect init is synchronous and non-modal
 * (it clears to_modal), so the dialog is the top window once navigator_to returns. */
/* Hold menu rows: stock sortselect's list is 210, so three whole rows and the reading margin
 * (SCROLL_MARGIN) show at every scroll, never a half row under the title. */
#define QM_ROW 66
/* Under Classic (classic_dialogs) the hold menu is Cover Flow's flip panel
 * (docs/ipod.md#cover-flow) centred under the bar, opaque: QM_HEAD rows of header holding the
 * navbar's title, then the theme's page rows, QM_ROWS at most (past them it scrolls), all clear of
 * the glass. No dim; hold_paint draws the panel. */
#define QM_HEAD 44
#define QM_ROWS 6
static void hold_open(void *dialog, int n) {
    void *nav = widget_lookup(dialog, "view_navbar", 1),
         *list = widget_lookup(dialog, "list_view", 1);
    if (!nav || !list) return;
    int row = theme()->page_row, rows = n < QM_ROWS ? n : QM_ROWS, h = QM_HEAD + rows * row,
        y = BAR_ROWS + (320 - BAR_ROWS - h) / 2;
    void *title = widget_lookup(nav, "scrlabel_title", 1);
    widget_set_prop_str(dialog, "highlight", "");
    widget_set_prop_int(dialog, "style:normal:bg_color", 0);
    widget_set_prop_int(nav, "style:normal:bg_color", 0);
    widget_move_resize(nav, PANEL_X, y, BACK_W, QM_HEAD);
    widget_set_visible(widget_lookup(nav, "img_return", 1), 0, 0);
    widget_move_resize(title, 12, (QM_HEAD - 33) / 2, BACK_W - 24, 33);
    panel_font(title, 31, 1);
    text_ink(title, 0xffffffffu, INK_LABEL); /* white on the header in either half */
    widget_set_prop_int(list, "style:normal:bg_color", 0);
    widget_move_resize(list, PANEL_X, y + QM_HEAD, BACK_W, rows * row);
    widget_set_prop_str(widget_lookup(list, "scroll_view", 1), "self_layout",
                        "default(x=0,y=0,w=100%,h=100%)");
}
/* A row's label as the flip panel's track: the theme's face, left; Light Mode flips its white. */
static void hold_label(void *item, void *label) {
    widget_set_prop_int(item, "style:normal:bg_color", 0);
    widget_move_resize(label, 12, 0, BACK_W - 24, theme()->page_row);
    panel_font(label, 25, 1);
}
/* The title loops, as Now Playing's; stock's sortselect title doesn't. Its holds at its start and
 * end are every label's (ringnav_hscroll_start, ringnav_hscroll_lull). */
static void qm_title(void *dialog, const char *s) {
    void *title = widget_lookup(dialog, "scrlabel_title", 1);
    widget_set_prop_int(title, "loop", 1);
    widget_set_text_utf8(title, s);
}
static int qm_open(const void *unused) {
    qm.qm_timer = 0;
    void *wm = window_manager(), *page = window_manager_get_top_window(wm);
    void *r = usable() && !window_manager_is_animating(wm) ? qm_record() : (void *)0;
    if (!r) return 0;
    navigator_to("dialog/sortselect_dialog");
    void *dialog = window_manager_get_top_window(wm);
    if (dialog == page) return 0;
    if (tk_strcmp(widget_get_prop_str(dialog, "name", ""), "sortselect_dialog")) {
        window_close(dialog);
        return 0;
    }
    /* The Play hold is a button, as Centre and Return: it ends a click's touch hiding
     * (hide_outline). A choice opens from a row's click, which may be a tap. */
    if (!qm.qm_choice) st.touch_mode = 0;
    void *back = widget_lookup(dialog, "img_return", 1),
         *view = widget_lookup(dialog, "scroll_view", 1);
    widget_off_by_func(dialog, EVT_KEY_UP, (void *)SORTSELECT_KEYUP, dialog);
    widget_off_by_func(back, EVT_CLICK, (void *)SORTSELECT_CLOSE, dialog);
    widget_on(dialog, EVT_KEY_UP, qm_back, dialog);
    widget_on(back, EVT_CLICK, qm_back, dialog);
    widget_on(dialog, EVT_DESTROY, qm_gone, dialog);
    if (qm.qm_choice) {
        /* stock's play modes, its category named as Group by sets it (album or folder) */
        static const char *const shuffle[2][5] = {
            { "Off", "All Songs", "Songs in Album", "Albums", "Albums and Songs" },
            { "Off", "All Songs", "Songs in Folder", "Folders", "Folders and Songs" }
        };
        static const char *const repeat[2][6] = { { "Play One Song", "Play One Album", "Play All",
                                                    "Repeat Song", "Repeat Album", "Repeat All" },
                                                  { "Play One Song", "Play One Folder", "Play All",
                                                    "Repeat Song", "Repeat Folder",
                                                    "Repeat All" } };
        static const char *const groups[] = { "Album", "Folder" };
        int kind = qm.qm_choice - 1,
            count = kind == 0   ? 5
                    : kind == 1 ? 6
                                : 2,
            folder = playback_option(2);
        const char *const *names = kind == 0   ? shuffle[folder]
                                   : kind == 1 ? repeat[folder]
                                               : groups;
        qm_title(dialog, kind == 0 ? "Shuffle" : kind == 1 ? "Repeat" : "Group by");
        widget_destroy_children(view);
        int painted = classic_dialogs(), row = painted ? theme()->page_row : QM_ROW,
            w = painted ? BACK_W : 375;
        for (int i = 0; i < count; ++i) {
            void *item = list_item_create(view, 0, i * row, w, row);
            widget_use_style(item, "s_listitem_black");
            void *label = hscroll_label_create(item, 30, 0, 310, row);
            widget_use_style(label, "s_scrlabel_white24l");
            widget_set_text_utf8(label, names[i]);
            if (painted) hold_label(item, label);
            if (i == playback_option(kind))
                image_base_set_image(image_create(item, w - 35, 0, 24, row), "select");
            widget_on(item, EVT_CLICK, qm_pick, (void *)(long)(100 + i));
        }
        widget_set_prop_int(view, "virtual_h", count * row);
        if (painted) hold_open(dialog, count);
        st.qm_dialog = dialog;
        ringnav_select(view, playback_option(kind), count);
        return 0;
    }
    unsigned cls = qm.qm_cls;
    int song = qm.qm_kind == QM_SONG || qm.qm_kind == QM_COVERFLOW || qm.qm_kind == QM_PLAYING;
    const char *album = P(r, REC_ALBUM), *artist = P(r, REC_ARTIST);
    /* a song by its title: the player's parse names a folder play's untagged record */
    char name[512];
    const char *tag = song ? now_tag(r, REC_TITLE) : (void *)0;
    const char *title = (qm.qm_kind == QM_ALBUM || qm.qm_kind == QM_COVERALBUM) ? album
                        : qm.qm_kind == QM_GROUP ? P(r, qm_by[(cls & 0xf) - 4])
                        : tag && *tag            ? tag
                        : song                   ? track_name(name, sizeof name, r)
                                                 : P(r, REC_NAME);
    if (qm.qm_kind == QM_PLAYLIST && *qm.qm_name) title = qm.qm_name;
    qm_title(dialog, title ? title : "");
    widget_destroy_children(view);
    /* Songs favourite, as stock's heart, and go to their album and artist; collections shuffle.
     * Go to is left out where its single-instance page is already open, or the album is the page.
     */
    unsigned char acts[20], n = 0;
    /* Shuffle Albums/Folders only: skipping the group comes first */
    int group_first = qm.qm_kind == QM_PLAYING && playback_option(0) >= 3;
    /* Rate (the heart's hold) follows Next/Previous album */
    if (group_first) acts[n++] = QA_NEXT_GROUP, acts[n++] = QA_PREV_GROUP;
    if (group_first && st.rate_win) acts[n++] = QA_RATE;
    int at, off; /* a spoken CUE book's saved place, from any of its tracks */
    if (qm.qm_kind == QM_SONG && book_place(qm_list(), r, &at, &off)) acts[n++] = QA_RESUME;
    acts[n++] = QA_NEXT;
    acts[n++] = QA_ADD;
    if (qm.qm_kind == QM_PLAYING) {
        if (chapter_file(r)) acts[n++] = QA_CHAPTERS;
        acts[n++] = QA_QUEUE;
    }
    if (!song) acts[n++] = QA_SHUFFLE;
    if (song) acts[n++] = checkFavExist(r) ? QA_UNFAV : QA_FAV;
    /* an album's own hearts, 5 its favourite */
    unsigned aid = song ? 0 : qm_album_id(r);
    int album_hearts = aid ? rate_kept(1, aid) : 0;
    if (aid) acts[n++] = album_hearts == 5 ? QA_UNFAV : QA_FAV, acts[n++] = QA_RATE;
    if (qm.qm_kind < QM_COVERFLOW || qm.qm_kind == QM_PLAYING) acts[n++] = QA_PLAYLIST;
    /* a card app's song isn't in the Library: Go to only to the app's own page */
    int hidden = song && !addon_goes(P(r, REC_PATH));
    if ((qm.qm_kind == QM_SONG || qm.qm_kind == QM_PLAYING) && album && *album && !hidden &&
        (cls & 0xfff0) != 0xff10 && !navigator_window_is_exist("playerjumpinfo_page") &&
        !coverflow_shows_album(r))
        acts[n++] = QA_ALBUM;
    if ((song || cls == CLASS_ALBUMS) && artist && *artist && !hidden &&
        !navigator_window_is_exist("artistinfo_page"))
        acts[n++] = QA_ARTIST;
    /* ponytail: hidden while a folder_page is open (as Go to's single-instance pages), so a folder
     * play's Now Playing has none; Back reaches that folder_page instead. */
    if (song && !navigator_window_is_exist("folder_page") && song_folder(r, st.go_folder))
        acts[n++] = QA_FOLDER;
    if (qm.qm_kind == QM_PLAYING) {
        acts[n++] = QA_ADV_SHUFFLE;
        acts[n++] = QA_REPEAT;
        acts[n++] = QA_GROUP;
        if (!group_first && st.rate_win) acts[n++] = QA_RATE;
        if (np_more()) acts[n++] = QA_MORE; /* last: Now Playing's own menu */
    }
    if (qm.qm_kind == QM_PLAYLIST) acts[n++] = QA_MORE; /* its dots' page */
    /* the list's own rows, which its covers hide: Select Playlists… is Classic's select mode; a
     * toolbar's page edits from its toolbar */
    if (qm.qm_kind == QM_PLAYLIST && coverflow_playlist(page)) {
        acts[n++] = QA_NEW_PLAYLIST;
        if (!geom_val(V_TOOLBAR)) acts[n++] = QA_EDIT_PLAYLISTS;
    }
    static const char *const rows[] = { "Play Next",
                                        "Add to Queue",
                                        "Shuffle",
                                        "Add to Favourites",
                                        "Remove from Favourites",
                                        "Add to Playlist",
                                        "Go to Album",
                                        "Go to Artist",
                                        "Shuffle",
                                        "Repeat",
                                        "Group by",
                                        "Next Album",
                                        "Previous Album",
                                        "Rate",
                                        "Resume",
                                        "View Queue",
                                        "Go to Folder",
                                        "Chapters",
                                        "More…",
                                        "New Playlist",
                                        "Select Playlists…" };
    char rated[32];
    /* stock sortselect row styles */
    int painted = classic_dialogs(), pitch = painted ? theme()->page_row : QM_ROW;
    for (int i = 0; i < n; ++i) {
        void *item = list_item_create(view, 0, i * pitch, painted ? BACK_W : 375, pitch);
        widget_use_style(item, "s_listitem_black");
        void *label = hscroll_label_create(item, 30, 0, 266, pitch);
        widget_use_style(label, "s_scrlabel_white24l");
        if (acts[i] == QA_RATE) {
            int h = aid ? album_hearts : checkFavExist(r) ? 5 : rate_kept(0, listen_key(r));
            const char *row = aid ? "Rate Album" : "Rate";
            char hs[16];
            tk_snprintf(rated, sizeof rated, h ? "%s  %s" : "%s", row, hearts_text(hs, h));
        }
        widget_set_text_utf8(label,
                             acts[i] == QA_RATE ? rated
                             : acts[i] == QA_GROUP
                                 ? (playback_option(2) ? "Group by: Folder" : "Group by: Album")
                             : acts[i] == QA_NEXT_GROUP && playback_option(2) ? "Next Folder"
                             : acts[i] == QA_PREV_GROUP && playback_option(2) ? "Previous Folder"
                                                                              : rows[acts[i] - 1]);
        if (painted) hold_label(item, label);
        widget_on(item, EVT_CLICK, qm_pick, (void *)(long)acts[i]);
    }
    widget_set_prop_int(view, "virtual_h", n * pitch);
    if (painted) hold_open(dialog, n);
    st.qm_dialog = dialog;
    return 0;
}

/* A playlist's hold, from its row or its cover: the list's own menu, at its index in the live list
 * (p_deque_playlist), which its More page takes. A cover's title is the name the covers show: the
 * live list keeps a renamed one's old name until its page is on top again. */
static int qm_list_hold(unsigned at, char *key, void *cover) {
    void *lists = P(p_deque_playlist, 0);
    void *r = lists && at < deque_size(lists) ? deque_at(lists, at) : 0;
    if (!key || !r || !(qm.qm_timer = timer_add(qm_open, (void *)0, 0))) return 0;
    qm.qm_kind = QM_PLAYLIST;
    const char *name = cover ? P(cover, REC_TITLE) : 0;
    tk_snprintf(qm.qm_name, sizeof qm.qm_name, "%s", name ? name : "");
    qm.qm_cls = 0xf001; /* its songs load as a song list, as Shuffle's (play_folder) */
    st.qm_idx = at;
    st.qm_rows = deque_size(lists);
    qm.qm_hash = rec_hash(r);
    st.qm_press = *(unsigned long long *)(key + INPUT_KEY_TIME);
    drop_input();
    return 1;
}

/* The hold: the same gates and row as a centre press, over the stock showlist or Coverflow's
 * own tracks (row count checked), or the album at the centre of Coverflow's covers. Everything
 * else stays stock. */
int qm_hold(void) {
    qm.qm_choice = 0;
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    int playing = !tk_strcmp(name, "playing_page");
    if (st.rate_on && top == st.rate_on) { /* the picker's: the hold and its release do nothing */
        char *key = play_key();
        if (key) st.qm_press = *(unsigned long long *)(key + INPUT_KEY_TIME);
        drop_input();
        return 1;
    }
    if (st.qm_dialog || qm.qm_timer || st.qm_playlist || !usable() ||
        (!playing && !allowed_top(top)) || window_manager_is_animating(wm) || pointer_down() ||
        airplayGetFlag() == 2 || g_navbar_status)
        return 0;
    if (playing) {
        unsigned at, n, type = (unsigned)MCL(MCL_TYPE);
        void *r = queue_now(&at, &n), *q = P(mcl_pdeqplaylist, 0);
        char *key = play_key();
        if (!r || !key || (type != 1 && (type & 0xf000) != 0xf000) ||
            !(qm.qm_timer = timer_add(qm_open, 0, 0)))
            return 0;
        qm.qm_kind = QM_PLAYING;
        qm.qm_cls = 0xf001;
        qm.qm_queue = q;
        st.qm_idx = at;
        st.qm_rows = n;
        qm.qm_hash = rec_hash(r);
        qm.qm_queue_hash = queue_hash(q);
        st.qm_press = *(unsigned long long *)(key + INPUT_KEY_TIME);
        drop_input();
        return 1;
    }
    int kind = contexts[context_id(name)].kind;
    void *album = coverflow_album(top), *cover = coverflow_playlist(top);
    char *key = play_key();
    if (cover)
        return qm_list_hold(playlist_index(I(cover, REC_DISC)), key, cover); /* by its cd_num */
    if (album) {
        if (!key || !(qm.qm_timer = timer_add(qm_open, (void *)0, 0))) return 0;
        qm.qm_kind = QM_COVERALBUM;
        qm.qm_cls = CLASS_ALBUMS;
        qm.qm_hash = rec_hash(album);
        st.qm_press = *(unsigned long long *)((char *)key + INPUT_KEY_TIME);
        drop_input();
        return 1;
    }
    void *list = page_tracks(top);
    int coverflow = list != 0;
    void *w = surface_under(top, (void *)0, (void *)0, 0);
    if ((!coverflow && kind < CTX_FOLDER) || !w || !load(&g_menu, w, 1) || g_menu.ctx < 0 ||
        g_menu.kind == 3)
        return 0;
    int cur = reconcile(&g_menu, !moving(&g_menu));
    if (cur < 0) return 0;
    /* A playlist row's hold queues its songs; More… opens the page its trailing dots do. The Add
     * to Playlist picker shares playlist_page's name and keeps stock's hold: its add mode tells
     * the two apart, as the showlist under it is another page's. */
    if (!tk_strcmp(name, "playlist_page") && widget_get_prop_int(top, PL_MODE, 0)) return 0;
    if (!tk_strcmp(name, "playlist_page")) {
        /* The row's index counts Import and Export; its dots' name carries the playlist's. */
        void *more = more_image(g_menu.at[cur]);
        return qm_list_hold((unsigned)atoi(more ? widget_get_prop_str(more, "name", "") + 8 : "-1"),
                            key, 0);
    }
    if (!coverflow) list = P(p_deque_showlist, 0);
    if (!list || deque_size(list) != (unsigned)g_menu.rows) return 0;
    void *r = deque_at(list, g_menu.id[cur]);
    /* An artist's tabs reload the showlist with load_localartist_list, which sets classinfo +0
     * (0xff07 songs, 0xff01 albums) but not g_class_type. */
    unsigned cls = (unsigned)(tk_strcmp(name, "artistinfo_page") ? I(g_class_type, 0)
                                                                 : I(g_local_classinfo_save, 0));
    if (!r || !key || playback_video(r)) return 0;
    qm.qm_kind = I(r, REC_TYPE) == 8 ? QM_SONG : 0;
    if (coverflow) {
        if (qm.qm_kind) qm.qm_kind = QM_COVERFLOW;
    } else if (kind == CTX_FOLDER) {
        if (I(r, REC_TYPE) == 4) qm.qm_kind = QM_FOLDER;
    } else if (cls == CLASS_ALBUMS || (cls >= CLASS_ARTIST_ALBUMS && cls <= 0xff03))
        qm.qm_kind = QM_ALBUM; /* all albums, and an artist's, composer's or genre's */
    else if (cls >= 0xf004 && cls <= 0xf006)
        qm.qm_kind = QM_GROUP; /* artist, composer and genre lists */
    if (!qm.qm_kind || !(qm.qm_timer = timer_add(qm_open, (void *)0, 0))) return 0;
    qm.qm_cls = cls;
    st.qm_idx = (unsigned)g_menu.id[cur];
    st.qm_rows = (unsigned)g_menu.rows;
    qm.qm_hash = rec_hash(r);
    qm.qm_browse = browse_hash();
    st.qm_press = *(unsigned long long *)((char *)key + INPUT_KEY_TIME);
    drop_input();
    return 1;
}
