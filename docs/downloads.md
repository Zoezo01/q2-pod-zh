# Saved downloads: UX and Q2SPOT/1 contract

This contract describes Spotify's saved collections and gives Deezer and Qobuz maintainers the same user experience to implement in their own integrations. It requires no shared daemon, storage implementation or credentials. Tidal Downloads ends with the same **Clear All Downloads…** and **Remove Downloads?** confirmation for its own cache.

## User experience and storage

**Downloads** is a persistent hub: **Downloaded Playlists**, **Downloaded Tracks**, **Download Progress**, **Storage Management**. Download Playlist and Download Liked Songs retain source titles, source order and every duplicate occurrence. Completion counts entry positions: two occurrences share audio but count twice. Missing/unavailable entries remain visible; collection playback follows the entire manifest across pages, skips missing entries and never fetches audio online.

Updates are manual. **Update Download** fetches the entire source listing before replacing the manifest or title. Listing errors preserve the previous manifest. **Retry Failed** retries unavailable/removed entries. **Resume** continues interrupted work, reusing completed audio. **Pause** and **Cancel Download** stop a job without deleting saved music; canceled jobs can be resumed. Multiple collections queue sequentially. Repeated requests for the same account/source never duplicate or reset a job. After reboot, interrupted and queued jobs are paused and require explicit Resume.

Explicit download audio is protected until removal, separately from the evictable playback cache. Cache-size settings cap only temporary playback storage. Existing complete cached tracks remain browsable; their playlist grouping/order cannot be reconstructed. Re-downloading the source playlist supplies that grouping. Required audio, metadata and key must be complete before an entry counts as downloaded.

Storage Management selects multiple playlists or tracks across pages and shows the affected count before confirmation. Clear All Downloads uses the same confirmation and keeps login/settings. Playlist removal frees only audio no remaining collection or independently saved track needs. Explicit track removal warns how many playlists become incomplete, marks those entries missing and pauses active work so it cannot immediately restore removed music. Choose Retry Failed to restore it deliberately. Selection is bounded by the existing 8 KiB request limit; the firmware asks users to remove the current selection before adding more when it fills.

Stop and **join** download work before deleting audio/references, including sign-out. Serialize deletion with queue changes; a canceled worker must not later commit protected music. Atomic manifest commits sync the temporary file, rename and sync its directory. Ignore interrupted `.tmp` records and incomplete audio copies. Preserve completed audio on network/listing/metadata failures. Clear All also removes account-owned interrupted protected files. Existing sign-out cleanup still removes saved credentials, audio, keys and art.

## Protocol

Use [existing Q2SPOT/1 framing and escaping](librespot.md#browsing-protocol-q2spot1): one request/reply per Unix stream connection, 8 KiB requests, 64 KiB replies, at most 50 six-field rows per page. Source IDs/cursors are opaque; titles never identify collections. Positions are zero-based absolute manifest positions. Spotify uses `liked` for account-scoped Liked Songs and stable playlist URIs for playlists.

Advertise each operation through `caps` as an `op` row. The firmware enables the new hub with `collections`, `collection`, `playcollection`, `downloadliked`, `jobs`, `job` and `remove`; otherwise it exposes supported legacy actions (with `download`, Downloads keeps a **Download Status** row sending `downloadstatus`) and an update explanation. Older firmware keeps using `download`, `downloadstatus`, `downloaded` and `playdownload` unchanged. Firmware polls `jobs` only while Download Progress is on screen with the display on, and a failed poll keeps the list.

| Operation        | Arguments            | Reply                                                                                                                                                                     |
| ---------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `download`       | `source`             | Idempotently queue a playlist; message, no rows.                                                                                                                          |
| `downloadliked`  | none                 | Idempotently queue Liked Songs; message, no rows.                                                                                                                         |
| `savetrack`      | `track-uri`          | Save an independent track through the same queue, protecting it from playlist removal.                                                                                    |
| `collections`    | `[cursor]`           | `collection` rows: source ID, title, human completion summary, empty context/position. Includes partial collections.                                                      |
| `collection`     | `source [cursor]`    | Ordered `track` or `missing` rows: URI, title, availability/error text, source ID, absolute entry position.                                                               |
| `playcollection` | `source position`    | Play that occurrence or the next available one, then follow the whole manifest; no rows.                                                                                  |
| `jobs`           | `[cursor]`           | `job` rows: source ID, state, human counts/message, collection title, completed entry count. Firmware polls every two seconds.                                            |
| `jobstatus`      | `source`             | Structured `value` rows: key as URI, value as title, empty subtitle, source as context, empty position. Keys: `state`, `completed`, `total`, `failed`, `listing_pending`. |
| `job`            | `source action`      | Action: `pause`, `resume`, `cancel`, `retry`, `update`; no rows.                                                                                                          |
| `remove`         | `kind ids [confirm]` | Without confirmation: count/warning message, no deletion. Literal `confirm` performs removal; no rows.                                                                    |

Removal kinds are `collections`, `tracks`, `all`. IDs are comma-separated stable IDs, empty for `all`; IDs in this contract cannot contain commas. Preview reports selection counts and playlists affected by track removal. Confirmation validates current records again; cancel sends nothing. A filesystem error may leave a partially completed removal: report its cause, and permit safe repetition. Jobs interrupted by storage management require Resume.

`downloaded` continues to include complete legacy cache tracks and protected tracks, newest first. `playdownload URI` stays cache-only. `downloadstatus` retains its message-only reply for older clients. Human messages must not be parsed for structured state; use `jobstatus`.

## Examples

These examples write literal `\t` for tab boundaries; actual requests use tab bytes. Empty trailing fields matter.

```
Q2SPOT/1
17\tdownloadliked

Q2SPOT/1
17\tok\tDownload saved. See Downloads for progress or Resume\t
```

A duplicate and an unavailable occurrence across a page:

```
Q2SPOT/1
18\tcollection\tliked\t50

Q2SPOT/1
18\tok\tSome tracks unavailable\t100
track\tspotify:track:0000000000000000000001\tOne\tDownloaded\tliked\t50
missing\tspotify:local:unavailable\tUnavailable\tNot available\tliked\t51
track\tspotify:track:0000000000000000000001\tOne\tDownloaded\tliked\t52
```

`19\tplaycollection\tliked\t52` starts at the second occurrence and continues beyond this UI page. Previous skips missing position 51 to position 50.

```
Q2SPOT/1
20\tjobstatus\tliked

Q2SPOT/1
20\tok\tSpotify cooldown: 30 s\t
value\tstate\twaiting\t\tliked\t
value\tcompleted\t12\t\tliked\t
value\ttotal\t30\t\tliked\t
value\tfailed\t0\t\tliked\t
value\tlisting_pending\tfalse\t\tliked\t
```

```
Q2SPOT/1
21\tremove\ttracks\tspotify:track:0000000000000000000001

Q2SPOT/1
21\tok\tRemove 0 playlists, 1 tracks? 2 playlists may become incomplete\t
```

`22\tremove\ttracks\tspotify:track:0000000000000000000001\tconfirm` performs that removal. `23\tremove\tall\t` previews Clear All; append `\tconfirm` only after confirmation.

## States and failures

```
queued -> listing -> downloading -> complete
                   downloading -> partial (unavailable entries)
listing/downloading -> waiting -> listing/downloading (cooldown/backoff)
active -> paused (interruption, storage/authentication error, exhausted retries)
any -> paused/canceled (explicit controls)
paused/canceled/partial -> queued (Resume/Retry Failed)
complete/partial -> queued + listing_pending (Update Download)
restart: queued/listing/downloading/waiting -> paused
```

`completed`/`failed` count entry positions, not unique files. `listing_pending` means the old manifest applies until a full refresh commits. Never mix source revisions. Rename a collection only on successful listing commit.

Retain HTTP status and filesystem causes in errors/logs. `storage` means check card space/write access and Resume; distinguish `refused`/`login`, `unavailable`, `offline`, and `throttled`. Honor shared cooldowns including Retry-After and provider reset headers. Waiting never adds failed tracks. Temporary network/server errors receive at most three retries (2, 4, 8 seconds), then retain the job paused for manual recovery. Waits are cancelable and release keep-awake. Spotify's atomic `/tmp/q2-librespot.download` uses `downloading=1` for active work, `0` during cooldown/backoff/pause/completion. Shutdown may interrupt a wait; the next start offers Resume.

## Deezer and Qobuz mapping

Use each service's existing daemon/browser, account identity, metadata and offline decoder. Add stable playlist IDs and an account-scoped favorites/Liked Songs source. Keep provider order and duplicate positions independently of metadata hydration; local-only/unavailable entries still occupy positions. Follow all provider pages and check consistent source revisions where available. Expiring stream URLs belong only in the transfer path, never in manifests.

For Deezer, put manifests/jobs beside its account/download records, exclude explicitly saved audio from cache eviction, and feed manifest entry positions to its offline queue. Map favorite tracks to Download Liked Songs and existing authentication/API errors to the shared states. Advertise capabilities only after implementing the controls and storage semantics.

For Qobuz, reuse complete-file FLAC metadata, ffmpeg/output and quality settings. Add manifests/protected references beside its cache. Playlist/favorite-track APIs supply ordering; keep fresh `track/getFileUrl` and Range recovery in transfers. Exclude protected referenced audio from eviction rather than treating every played song as an explicit download. These are maintainer instructions, not a claim either integration already implements this extension.

## Conformance

- More than 100 entries, duplicates at page boundaries, Liked Songs embedded/lazy/next pages, renamed playlists, unavailable entries and offline next/previous playback. No missing entry may trigger online playback.
- Two queued collections; repeated requests during/after transfer; pause/resume/cancel; restart requires Resume and reuses completed audio.
- 429 with numeric/date Retry-After, provider reset headers and no header; shared cooldown, no inflated failure counts, cancelable waits and released keep-awake. Network/5xx interruption gets bounded retries then manual recovery.
- Full/read-only/unwritable card; interrupted audio/metadata/manifest writes; failed later listing page or source revision change. Preserve causes, previous manifest and successful audio; never count incomplete data.
- Evict all temporary cache; protected music survives. Remove shared playlists, independent tracks and multiple selections across pages; clear/sign out while work waits. No resurrection or unrelated account/login/settings loss.
- New firmware with older backends; old operations against the new backend. Build/validate stock and iPod plus matching backend artifacts, without publishing or installing.

Automated tests cover storage/manifest/queue logic, synthetic retry errors and emulated firmware UI/protocol. Live Spotify authentication, actual provider throttling/lazy-page responses and card power-loss durability require device validation before release.
