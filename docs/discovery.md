# Hide folders from library browsing

Put an empty file named `database.ignore` inside any folder on the microSD card, then update
the music library. That folder and its subfolders are hidden from normal music browsing.
Folder names and locations are unrestricted: `/Singles/database.ignore` and
`/Needledrop/database.ignore` can keep the two collections separate. Use the exact lowercase
marker filename and no `.txt` extension. Hidden names work too: `.database.ignore` and
`.database.unignore` count the same, so other players don't show the markers.

For an exception, put an empty `database.unignore` in a subfolder. Its files and descendants
are visible again, unless a deeper folder contains another `database.ignore`. The nearest
marker wins; if both markers are in the same folder, `database.unignore` wins.

These tracks remain indexed, but do not appear in the normal music library's Songs,
Albums, Artists, Album Artists, Genres, Composers, Coverflow or library shuffle.
They are also hidden from the displayed Favourites, Recent and Frequent lists.
Existing favourite/history records are retained.

Use an explicit playlist containing those tracks, or browse their folders, to play
them. Mixed playlists retain both regular and discovery tracks in their original order.
This feature does not remove tracks from the playing queue, change tags, or delete files.

The Era browser and its decade counts also exclude marked tracks, before grouping albums.

Remove the marker and update the library to restore visibility, without moving or retagging
tracks or changing playlist paths. Marker changes take effect after a library update (Update
Local Music), not while browsing and not on a restart. After updating to this firmware, the
markers apply once the library is updated. A marker at the card root applies to the whole
card; nested exceptions still work. Only regular marker files count, not folders with those
names.

Unlike Rockbox's scanner exclusion, the Q2 keeps these tracks indexed: only general browsing
is filtered, so imported and mixed playlists retain their entries and order.

Each library update first searches the card for markers and saves the marked folders to
`/mnt/data/q2-library-scope`. Browsing reads that list, never the card, and it lasts across
restarts. Directory symlinks and folders whose names start with a dot (such as `.rockbox` or
the art caches) are not searched. The search is bounded to 64 nested levels and 1,024-byte
paths; the generated predicate is bounded to 128 KiB. Past these limits no list is saved, and
a damaged list, or one there is no memory to read, is ignored. Either way the whole library
shows until the next library update.
