# Security

Report security problems privately with **Report a vulnerability** on this repository's Security tab, not in a public issue or on Discord.

Only the latest release gets fixes.

## Card apps are trusted code

A card app (any `.<id>/` folder on the card, from the App Store or copied by hand) runs as root, as the firmware does, and nothing isolates it from the firmware or from other apps: it can read and change anything on the card, including other apps' logins and your Last.fm, ListenBrainz and Spotify logins. Install only apps you trust.
