"""A messy library's playlist table, for playlist_art.c's bounds: stock's 18-column `playlist` table with
101 containers and about 55k songs, ids shuffled across the playlists.

cd 1: 20,000 untagged singles, each in its own folder (20,000 albums); cd 2: 5,000 songs of 500 albums
with 4 KB album tags and 800-byte fileurls holding non-UTF-8 bytes; cd 3: 300 songs, 280 of them
gone from the card; cd 0 (Q2 Pod's first playlist) and cd 4-100: 300 songs each; and 50 junk rows (cd_num NULL, -1 or 999, fileurl NULL
or empty, duplicates)."""
import random
import sqlite3

DDL = ('create table playlist(id integer primary key,filename,fileurl,album,songer,style,name,composer,'
       'albumsonger,year,track,cd_num,time,time_end,time_create,Initial,bitdepth,samprate)')
NAMES = {1: 'Mix', 2: 'Road Trip', 3: 'Ünïcødé/', 4: 'L' * 255}
LIVE = 20  # cd 3's songs still on the card, at /mnt/mmc/L/<i>/t.flac beside a cover.jpg


def songs():
    """[(cd_num, fileurl, album, songer, albumsonger)], before the shuffle."""
    rows: list = [(1, f'/mnt/mmc/S/{i}/t.flac', None, None, None) for i in range(20000)]
    tag = 'T' * 4096
    for i in range(5000):
        url = (f'/mnt/mmc/B/{i % 500}/'.encode() + b'\xff\xfe' * 390 + f'{i}.flac'.encode())[:800]
        rows.append((2, url, f'{tag}{i % 500}', 'Artist', ''))
    rows += [(3, f'/mnt/mmc/{"L" if i < LIVE else "Gone"}/{i}/t.flac', f'Album {i}', 'Artist', '')
             for i in range(300)]
    rows += [(cd, f'/mnt/mmc/P/{cd}/{i}.flac', f'Album {cd}.{i % 7}', 'Artist', '')
             for cd in (0, *range(4, 101)) for i in range(300)]
    junk = [(None, '/mnt/mmc/j.flac'), (-1, '/mnt/mmc/j.flac'), (999, '/mnt/mmc/j.flac'), (5, None), (5, '')]
    rows += [(cd, url, None, None, None) for cd, url in junk * 10]
    return rows


def build(path):
    """The table at path (journal_mode=delete, as stock's); returns {cd_num: [(id, fileurl, album, songer,
    albumsonger)]} in id order, the rows PL_SONGS gives each playlist."""
    rows = songs()
    random.Random(2026).shuffle(rows)
    db = sqlite3.connect(path)
    db.execute('pragma journal_mode=delete')
    db.execute(DDL)
    for cd in range(101):
        name = NAMES.get(cd, f'Playlist{cd}')
        db.execute("insert into playlist values (?,?,'','','','',?,'','',0,0,?,0,0,0,0,0,0)", (cd, name, name, cd))
    by_cd = {}
    for i, (cd, url, album, songer, aa) in enumerate(rows):
        db.execute("insert into playlist(id,fileurl,album,songer,albumsonger,cd_num) values (?,cast(? as text),?,?,?,?)",
                   (101 + i, url, album, songer, aa, cd))
        by_cd.setdefault(cd, []).append((str(101 + i), url, album, songer, aa))
    db.commit()
    db.close()
    return by_cd
