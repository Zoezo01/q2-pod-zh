# Security

Report security problems privately with **Report a vulnerability** on this repository's Security tab, not in a public issue or on Discord.

Only the latest release gets fixes.

## Card apps are trusted code

A card app (any app in a `.<id>/` folder on the card, from the App Store or copied by hand) runs fully trusted: its launcher, or a game itself, is started by the firmware's own process, with the same rights as Q2 Pod, and nothing isolates it from the firmware or from other apps. It can read and change anything on the card and in the Q2's storage, including other apps' saved logins and your scrobbling and Spotify logins. Install only apps you trust, as you would any program on a computer.
