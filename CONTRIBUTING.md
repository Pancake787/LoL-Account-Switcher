# Contributing to LoL Account Switcher

Thanks for your interest in contributing! This document covers development
setup, running tests, building a release, and the pull-request workflow.

Please also read our [Code of Conduct](CODE_OF_CONDUCT.md) — participation in
this project means agreeing to abide by it.

## Development Setup

1. **Install Python 3.12** from [python.org](https://www.python.org/downloads/)
   (use the `py` launcher on Windows). **Do not use the Microsoft Store
   version of Python** — it virtualizes `%APPDATA%` writes, which breaks the
   credential/session-file paths this app depends on.
2. Clone your fork and install dependencies:

   ```
   py -m pip install -r requirements.txt
   ```

3. Run the app locally:

   ```
   py main.py
   ```

## Running Tests

```
py -m pytest
```

All tests must pass before a pull request is merged. If you add or change
behavior, add or update tests alongside it.

## Building a Release (`--onedir`)

The project ships as a PyInstaller `--onedir` build (see
[`LoLSwitcher.spec`](LoLSwitcher.spec) — `--noupx` to avoid antivirus false
positives, `console=False` for a windowless GUI start).

```
py -m pip install pyinstaller==6.20.0
pyinstaller LoLSwitcher.spec
```

The built app appears under `dist/LoLSwitcher/`. Smoke-test it before opening
a PR that touches packaging: launch `dist/LoLSwitcher/LoLSwitcher.exe` and
confirm no console window flashes on startup or during an account switch.

## Building the Installer (`Setup.exe`)

The release also ships an Inno Setup installer that wraps the `--onedir`
build above (see [`installer/LoLSwitcher.iss`](installer/LoLSwitcher.iss)).

1. Install Inno Setup 6:

   ```
   winget install --id JRSoftware.InnoSetup
   ```

2. Make sure a fresh `MicrosoftEdgeWebview2Setup.exe` (the WebView2 Evergreen
   Bootstrapper) is present in the project root — the `.iss` bundles it as an
   installer prerequisite. Download the current one from
   `https://developer.microsoft.com/microsoft-edge/webview2/#download-the-webview2-runtime`.
3. Run the PyInstaller build first (previous section), then compile the
   installer:

   ```
   ISCC installer\LoLSwitcher.iss
   ```

   If `ISCC` isn't on `PATH`, it's typically at
   `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` or
   `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`.

This produces `LoLSwitcher-v2.0.0-Setup.exe` at the project root. Like the
portable `.exe`, it is unsigned, so smoke-test it the same way (launch it,
confirm the install completes and the app starts without a console flash).

## Secret Scanning (required before your first commit)

This repository uses [`pre-commit`](https://pre-commit.com/) with the
official [`gitleaks`](https://github.com/gitleaks/gitleaks) hook to catch
accidentally committed secrets (API keys, session tokens, credential files)
before they ever reach git history.

1. Install `pre-commit`:

   ```
   pip install pre-commit
   ```

2. Install the `gitleaks` binary itself (the hook needs it on `PATH`):

   ```
   winget install --id Gitleaks.Gitleaks
   # or: choco install gitleaks
   ```

3. Activate the hook in your local clone (run once per clone):

   ```
   pre-commit install
   ```

4. Optional — run it against the whole tree once, not just staged files:

   ```
   pre-commit run --all-files
   ```

Every commit you make will now be scanned automatically. If the hook flags a
false positive, do not disable it — open an issue instead so the
`.gitleaks.toml` allowlist can be adjusted.

## Pull Request Workflow

1. Fork the repository and create a feature branch off `main`.
2. Make your changes, keeping commits focused and descriptive.
3. Run `py -m pytest` and `pre-commit run --all-files` locally — both must be
   clean.
4. Open a pull request describing what changed and why. Link any related
   issue.
5. Be responsive to review feedback — small, incremental commits addressing
   review comments are preferred over force-pushed rewrites mid-review.

## Project Structure Pointers

- `controller.py` — orchestrates app state; no business logic lives in the GUI
  layer.
- `core.py` — headless switch logic shared by the GUI and the Stream Deck CLI
  path (`lolswitcher switch <username>`).
- `credential_store.py` — Windows Credential Manager (via `keyring`) + DPAPI
  API-key file wrapper.
- `riot_client.py` — process kill/restart + session-file swap logic.
- `rank_service.py` — Riot API calls for rank display.
- `gui/` — pywebview shell, HTML/CSS/JS front end, and the JS↔Python bridge.
- `stream-deck-plugin/` — the optional Elgato Stream Deck plugin (TypeScript).

See [`CLAUDE.md`](CLAUDE.md) for deeper implementation notes (session file
location, process kill order, keyring/PyInstaller quirks).
