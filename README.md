# LoL Account Switcher

[English](README.md) · [Deutsch](README.de.md)

[![CI](https://github.com/Pancake787/LoL-Account-Switcher/actions/workflows/ci.yml/badge.svg)](https://github.com/Pancake787/LoL-Account-Switcher/actions/workflows/ci.yml)

A lightweight Windows desktop app that lets you switch between 2-3 League of
Legends accounts with a single click — no re-entering passwords, no manual
Riot Client juggling. Each account card shows the current rank (Solo/Duo +
Flex, via the Riot API), and an optional Elgato Stream Deck plugin lets you
switch accounts with a single button press without opening the desktop app.

## Screenshots

> 📸 Screenshots and a demo GIF are coming soon.

<!-- Media pending (D-02): uncomment once the files are added under docs/.
![Main window](docs/screenshots/main-window.png)
![Account cards](docs/screenshots/account-cards.png)

![Demo: switching accounts](docs/demo.gif)
-->

## Installation

### Installer (recommended)

1. Download `LoLSwitcher-v2.0.0-Setup.exe` from the
   [Releases](../../releases) page and run it.
2. Installs per-user (no admin/UAC prompt needed), adds a Start Menu entry,
   and offers an optional desktop shortcut.
3. The installer silently installs the Microsoft Edge WebView2 Runtime if
   it's missing — no separate prerequisite step needed.
4. Launch **LoL Account Switcher** from the Start Menu.

### Portable (no install)

1. Download `LoLSwitcher-v2.0.0-win64.zip` from the
   [Releases](../../releases) page and extract it anywhere (e.g.
   `C:\Tools\LoLSwitcher\`).
2. **Prerequisite: Microsoft Edge WebView2 Runtime.** The GUI is rendered via
   WebView2. Most Windows 11 systems already have it installed; if the app
   fails to start with a WebView2-related error, install the Evergreen
   Bootstrapper (also attached as a Release asset for convenience) or grab the
   latest one directly from Microsoft:
   `https://developer.microsoft.com/microsoft-edge/webview2/#download-the-webview2-runtime`
3. Run `LoLSwitcher.exe`.

### Windows SmartScreen warning

Because neither `LoLSwitcher-v2.0.0-Setup.exe` nor `LoLSwitcher.exe` is
code-signed (no code-signing certificate has been purchased for this
personal/open-source project), Windows SmartScreen will likely show
**"Windows protected your PC"** the first time you run either one — the
warning applies equally to the installer and the portable `.exe`. This is
expected for unsigned binaries and does not indicate malware.

To proceed:

1. Click **More info**.
2. Click **Run anyway**.

This is a one-time step per machine/build. If you'd rather build from source
yourself, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Usage

- **Add an account** — enter a display name, Riot username/password, and
  (optionally) your Riot ID (`GameName#TAG`) for rank lookups.
- **Switch accounts** — click a card's **Wechseln/Switch** button. The Riot
  Client and League client are closed, the session file is swapped, and the
  Riot Client restarts already logged in.
- **Rename / delete / reorder** — rename or delete a card via its menu, or
  drag-and-drop cards to reorder them.
- **Copy password** — copies the stored password to the clipboard for 30
  seconds, then clears it automatically.
- **Rank refresh** — ranks refresh automatically (~15 min, and after a match
  ends) or on demand via the manual refresh button in the title bar.
- **Session re-capture** — if a card shows "Session evtl. abgelaufen" (session
  possibly expired), use **Session neu aufnehmen** to log in once manually and
  re-capture a fresh session snapshot for that account.

## Riot API Key

Rank display requires a **personal Riot Developer API key**
(https://developer.riotgames.com/) — one key per user, supplied by you.

- Riot's free developer keys expire every 24 hours and **cannot be bundled**
  with this app; you must generate and supply your own.
- Set your key once via the command line:

  ```
  LoLSwitcher.exe set-api-key <your-key>
  ```

  (omit the key to be prompted for it interactively, without echoing it to
  the terminal).
- The key is encrypted with the Windows Data Protection API (DPAPI) and
  written to a file under `%APPDATA%\LoLSwitcher\`. It is **never** stored as
  plaintext and never committed to any repository.

## Security

`RiotGamesPrivateSettings.yaml` (the Riot Client's session file that this app
swaps between accounts) contains an **RSO authentication token** — treat it
exactly like a credential.

- Per-account session snapshots are stored under
  `%APPDATA%\LoLSwitcher\sessions\{username}\`.
- These snapshot files must **never** be committed to a repository, shared, or
  uploaded anywhere. Anyone holding a valid snapshot could potentially use it
  to access the associated Riot account until the token is rotated/expired.
- The app never transmits these files anywhere except onto disk on your own
  machine.

## Legal Disclaimer

LoL Account Switcher isn't endorsed by Riot Games and doesn't reflect the
views or opinions of Riot Games or anyone officially involved in producing or
managing Riot Games properties. Riot Games, and all associated properties are
trademarks or registered trademarks of Riot Games, Inc.

## License

Released under the [MIT License](LICENSE).

## Authorship

The entire codebase of this project was written by **Claude** (Anthropic's AI
assistant) using [Claude Code](https://claude.com/claude-code). The repository
owner ([@Pancake787](https://github.com/Pancake787)) specified the requirements,
made the design decisions, and tested the result, but did not personally
hand-write any of the code.

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for the
development setup, build instructions, and pull-request workflow. Please also
read our [Code of Conduct](CODE_OF_CONDUCT.md).
