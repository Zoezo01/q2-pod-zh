/* Audited top windows: the name is the navigation allowlist, the class decides whether and how
 * position memory restores, and DRILL (iPod)
 * one whose rows open another list but draw no stock chevron (list_into). BUTTONS (iPod) marks a
 * dialog or page with no list: its own buttons are the rows. CHOICE marks a picker, which opens on
 * its checked option. MORE marks a list that loads more at its end, which stops there, where every
 * other list carries over (ring_list): stock's Tidal pages whose scroll-end handler fetches the
 * next page (View All 0x4ef274, an album's tracks 0x4e17f4, an artist's albums 0x4e324c, Add to
 * Playlist 0x4df568, search 0x478adc), and three whose requests page by offset though no fetch at
 * the end was found (a music class, My Collection, Baidu folders): stopping is what they did, and
 * carrying over where stock pages some other way would hide their later rows.
 * The builder rejects any name that is not a window name in the stock rootfs. CTX_DYNAMIC pages
 * never import another page's row; CTX_FIXED menus are never remembered: opened forward they
 * start at their top; CTX_FOLDER and CTX_LOCAL add a content fingerprint. */
{ "audiosetting_page", CTX_FIXED, 0 },
{ "netdiskdownload_page", CTX_DYNAMIC, 0 },
{ "netdiskfolder_page", CTX_DYNAMIC, MORE },
{ "sortselect_dialog", CTX_DYNAMIC, CHOICE }, /* also the hold menu */
{ "folder_page", CTX_FOLDER, 0 },
{ "home_page", CTX_DYNAMIC, DRILL },
{ "coverflow_page", CTX_DYNAMIC, 0 }, /* runtime window (patch/coverflow.c) */
{ "photos_page", CTX_DYNAMIC, 0 },    /* runtime window (patch/photos.c) */
{ "games_page", CTX_DYNAMIC, 0 }, /* runtime window (patch/games.c) */
{ "cardapps_page", CTX_DYNAMIC, 0 }, /* runtime window (patch/addon.c): Library's Apps */
{ "mostplayed_page", CTX_FIXED, 0 }, /* runtime window (patch/library_lists.c); fixed: the hold menu */
{ "shuffle_page", CTX_DYNAMIC, 0 },   /* runtime window (patch/library_lists.c) */
{ "playlistedit_page", CTX_DYNAMIC, 0 },
{ "chapters_page", CTX_DYNAMIC, 0 }, /* runtime chapter list */
{ "upnext_page", CTX_DYNAMIC, 0 },   /* runtime window (patch/navigation.c) */
{ "ratings_page", CTX_DYNAMIC, 0 },   /* runtime window (patch/ratings.c) */
{ "era_page", CTX_DYNAMIC, 0 },       /* runtime window (patch/library_lists.c) */
{ "era_albums_page", CTX_DYNAMIC, 0 }, /* runtime window (patch/library_lists.c) */
{ "rated_page", CTX_FIXED, 0 },      /* runtime window (patch/ratings.c); fixed: the hold menu */
{ "longsongs_page", CTX_FIXED, 0 },  /* runtime window (patch/library_lists.c); fixed: the hold menu */
{ "radio_page", CTX_DYNAMIC, 0 },     /* runtime window (patch/radio.c) */
{ "spotbrowse_page", CTX_DYNAMIC, 0 }, /* runtime window (patch/spotify.c) */
{ "streamconf_page", CTX_DYNAMIC, 0 }, /* runtime window (patch/navigation.c) */
{ "apps_page", CTX_DYNAMIC, 0 },       /* runtime window (patch/netupdate.c) */
{ "tidal_downloads", CTX_DYNAMIC, 0 }, /* runtime window (patch/library_lists.c) */
{ "app_songs", CTX_DYNAMIC, 0 },       /* runtime window (patch/library_lists.c) */
{ "addon_page", CTX_DYNAMIC, 0 },      /* runtime window (patch/addon.c) */
{ "battery_page", CTX_DYNAMIC, 0 },    /* runtime window (patch/battery.c) */
{ "album_page", CTX_LOCAL, 0 },
{ "albuminfo_page", CTX_LOCAL, 0 },
{ "allmusic_page", CTX_LOCAL, 0 },
{ "artistinfo_page", CTX_LOCAL, 0 },
{ "localclass_page", CTX_LOCAL, 0 },
{ "playlist_page", CTX_LOCAL, DRILL },
{ "localmusic_page", CTX_FIXED, 0 },
{ "playerjumpinfo_page", CTX_DYNAMIC, 0 },
{ "playermore_page", CTX_DYNAMIC, 0 },
{ "playerqueue_page", CTX_DYNAMIC, 0 },
{ "dsdoutput_page", CTX_FIXED, CHOICE },
{ "filter_page", CTX_FIXED, CHOICE },
{ "memplay_page", CTX_FIXED, CHOICE },
{ "playmode_page", CTX_FIXED, CHOICE },
{ "playset_page", CTX_FIXED, 0 },
{ "equalizer_page", CTX_DYNAMIC, 0 }, /* PEQ list editor */
{ "replaygain_page", CTX_FIXED, CHOICE },
{ "usbvol_page", CTX_FIXED, CHOICE },
{ "specfolder_page", CTX_DYNAMIC, 0 },
{ "stream_page", CTX_DYNAMIC, 0 },
{ "about_page", CTX_FIXED, 0 },
{ "autotime_page", CTX_FIXED, CHOICE },
{ "bluetooth_page", CTX_DYNAMIC, 0 },
{ "btquality_page", CTX_FIXED, CHOICE },
{ "datetime_page", CTX_FIXED, CHOICE },
{ "display_page", CTX_FIXED, 0 },
{ "fwupdate_page", CTX_FIXED, 0 },
{ "updatemusic_page", CTX_FIXED, 0 }, /* Update Library */
{ "keylock_page", CTX_FIXED, CHOICE },
{ "language_page", CTX_FIXED, CHOICE },
{ "lighttime_page", CTX_FIXED, CHOICE },
{ "netservice_page", CTX_FIXED, 0 },
{ "powermanager_page", CTX_FIXED, 0 },
{ "reset_page", CTX_FIXED, 0 },
{ "screensaver_page", CTX_FIXED, CHOICE },
{ "standby_page", CTX_FIXED, CHOICE },
{ "synclink_page", CTX_FIXED, 0 },
{ "sysset_page", CTX_FIXED, 0 },
{ "wifi_page", CTX_DYNAMIC, 0 },
{ "wifiset_page", CTX_DYNAMIC, 0 },
{ "tidal_add_playlist_page", CTX_DYNAMIC, MORE },
{ "tidal_albuminfo_page", CTX_DYNAMIC, MORE },
{ "tidal_artists_info_page", CTX_DYNAMIC, MORE },
{ "tidal_biography_page", CTX_DYNAMIC, 0 },
{ "tidal_genre_page", CTX_DYNAMIC, 0 },
{ "tidal_main_page", CTX_DYNAMIC, 0 },
{ "tidal_music_class_page", CTX_DYNAMIC, MORE },
{ "tidal_mycollection_page", CTX_DYNAMIC, MORE },
{ "tidal_playmore_page", CTX_DYNAMIC, 0 },
{ "tidal_user_loginout_page", CTX_DYNAMIC, 0 },
{ "tidal_viewall_page", CTX_DYNAMIC, MORE },
{ "search_dialog", CTX_DYNAMIC, 0 },
{ "tidal_search_dialog", CTX_DYNAMIC, MORE },
{ "confirminfo_dialog", CTX_DYNAMIC, BUTTONS },
/* A BUTTONS window must not contain a list: the window is the surface, so a list inside never is. */
{ "tidal_confirminfo_dialog", CTX_DYNAMIC, BUTTONS },
{ "autoshutdown_dialog", CTX_DYNAMIC, BUTTONS },
{ "playlistsmore_page", CTX_DYNAMIC, BUTTONS }, /* a playlist's Rename, Delete and Edit */
{ "tidal_quality_select_dialog", CTX_DYNAMIC, BUTTONS },
{ "tidal_sortmode_dialog", CTX_DYNAMIC, BUTTONS },
