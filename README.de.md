# LoL Account Switcher

[English](README.md) · [Deutsch](README.de.md)

Ein leichtgewichtiges Windows-Desktop-Tool, mit dem du mit einem Klick
zwischen 2-3 League-of-Legends-Accounts wechseln kannst — ohne Passwörter
erneut einzutippen und ohne den Riot Client manuell zu bedienen. Jede
Account-Karte zeigt den aktuellen Rang (Solo/Duo + Flex, über die Riot API),
und ein optionales Elgato-Stream-Deck-Plugin erlaubt den Wechsel per
Tastendruck, ohne die Desktop-App zu öffnen.

## Screenshots

> 📸 Screenshots und ein Demo-GIF folgen in Kürze.

<!-- Medien ausstehend (D-02): einkommentieren, sobald die Dateien unter docs/ liegen.
![Hauptfenster](docs/screenshots/main-window.png)
![Account-Karten](docs/screenshots/account-cards.png)

![Demo: Account-Wechsel](docs/demo.gif)
-->

## Installation

### Installer (empfohlen)

1. Lade `LoLSwitcher-v2.0.0-Setup.exe` von der
   [Releases](../../releases)-Seite herunter und führe sie aus.
2. Installiert pro Benutzer (kein Admin-/UAC-Prompt nötig), legt einen
   Startmenü-Eintrag an und bietet eine optionale Desktop-Verknüpfung an.
3. Der Installer installiert die Microsoft Edge WebView2 Runtime bei Bedarf
   automatisch im Hintergrund — kein separater Voraussetzungs-Schritt nötig.
4. Starte **LoL Account Switcher** über das Startmenü.

### Portable (ohne Installation)

1. Lade `LoLSwitcher-v2.0.0-win64.zip` von der [Releases](../../releases)-Seite
   herunter und entpacke sie an einen beliebigen Ort (z. B.
   `C:\Tools\LoLSwitcher\`).
2. **Voraussetzung: Microsoft Edge WebView2 Runtime.** Die Oberfläche wird
   über WebView2 gerendert. Auf den meisten Windows-11-Systemen ist die
   Runtime bereits vorhanden. Startet die App mit einem WebView2-Fehler,
   installiere den Evergreen Bootstrapper (liegt auch als Release-Asset bei)
   oder lade die aktuelle Version direkt bei Microsoft herunter:
   `https://developer.microsoft.com/microsoft-edge/webview2/#download-the-webview2-runtime`
3. Starte `LoLSwitcher.exe`.

### Windows-SmartScreen-Warnung

Da weder `LoLSwitcher-v2.0.0-Setup.exe` noch `LoLSwitcher.exe` codesigniert
ist (kein Code-Signing-Zertifikat für dieses private/Open-Source-Projekt
vorhanden), zeigt Windows SmartScreen beim ersten Start voraussichtlich
**„Der Computer wurde durch Windows geschützt"** an — die Warnung gilt
gleichermaßen für den Installer und die portable `.exe`. Das ist bei
unsignierten Programmen normal und kein Hinweis auf Malware.

So geht's weiter:

1. Klicke auf **Weitere Informationen**.
2. Klicke auf **Trotzdem ausführen**.

Dieser Schritt ist einmalig pro Gerät/Build nötig. Wer die App lieber selbst
aus dem Quellcode baut, findet Anleitung in [CONTRIBUTING.md](CONTRIBUTING.md).

## Nutzung

- **Account hinzufügen** — Anzeigename, Riot-Benutzername/Passwort und
  optional die Riot-ID (`GameName#TAG`) für die Rang-Anzeige eingeben.
- **Account wechseln** — Klick auf **Wechseln** auf einer Karte. Riot Client
  und League-Client werden beendet, die Session-Datei wird getauscht, der
  Riot Client startet bereits eingeloggt neu.
- **Umbenennen / löschen / sortieren** — über das Kartenmenü umbenennen oder
  löschen, oder Karten per Drag-and-drop neu anordnen.
- **Passwort kopieren** — kopiert das gespeicherte Passwort für 30 Sekunden in
  die Zwischenablage und löscht es danach automatisch.
- **Rang-Aktualisierung** — Ränge werden automatisch aktualisiert (~alle 15
  Minuten sowie nach Match-Ende) oder manuell über den Refresh-Button in der
  Titelleiste.
- **Session neu aufnehmen** — zeigt eine Karte „Session evtl. abgelaufen" an,
  einmalig manuell einloggen und über **Session neu aufnehmen** einen frischen
  Session-Snapshot für diesen Account erfassen.

## Riot-API-Key

Für die Rang-Anzeige wird ein **persönlicher Riot-Developer-API-Key**
benötigt (https://developer.riotgames.com/) — ein Key pro Nutzer, den du
selbst bereitstellst.

- Riots kostenlose Developer-Keys laufen alle 24 Stunden ab und **können nicht
  mitgeliefert werden** — du musst deinen eigenen Key erzeugen und hinterlegen.
- Key einmalig über die Kommandozeile setzen:

  ```
  LoLSwitcher.exe set-api-key <dein-key>
  ```

  (Key weglassen, um interaktiv danach gefragt zu werden, ohne dass er im
  Terminal angezeigt wird.)
- Der Key wird mit der Windows Data Protection API (DPAPI) verschlüsselt und
  in einer Datei unter `%APPDATA%\LoLSwitcher\` gespeichert. Er wird **nie**
  im Klartext gespeichert und nie in ein Repository committet.

## Sicherheit

`RiotGamesPrivateSettings.yaml` (die Session-Datei des Riot Clients, die diese
App zwischen Accounts austauscht) enthält ein **RSO-Auth-Token** —
behandle sie wie ein Zugangsdatum.

- Pro-Account-Session-Snapshots liegen unter
  `%APPDATA%\LoLSwitcher\sessions\{username}\`.
- Diese Snapshot-Dateien dürfen **niemals** in ein Repository committet,
  geteilt oder irgendwo hochgeladen werden. Wer im Besitz eines gültigen
  Snapshots ist, könnte damit potenziell auf den zugehörigen Riot-Account
  zugreifen, bis das Token rotiert/abgelaufen ist.
- Die App überträgt diese Dateien nirgendwohin außer auf die lokale
  Festplatte deines eigenen Rechners.

## Rechtlicher Hinweis

The following disclaimer text is Riot Games' own required wording and is kept
in English (not translated), per Riot's developer policy:

LoL Account Switcher isn't endorsed by Riot Games and doesn't reflect the
views or opinions of Riot Games or anyone officially involved in producing or
managing Riot Games properties. Riot Games, and all associated properties are
trademarks or registered trademarks of Riot Games, Inc.

## Lizenz

Veröffentlicht unter der [MIT-Lizenz](LICENSE).

## Autorschaft

Der gesamte Code dieses Projekts wurde von **Claude** (dem KI-Assistenten von
Anthropic) mit [Claude Code](https://claude.com/claude-code) geschrieben. Der
Repository-Inhaber ([@Pancake787](https://github.com/Pancake787)) hat die
Anforderungen vorgegeben, die Design-Entscheidungen getroffen und das Ergebnis
getestet, aber keinen Code selbst geschrieben.

## Mitwirken

Beiträge sind willkommen! Siehe [CONTRIBUTING.md](CONTRIBUTING.md) für
Entwicklungs-Setup, Build-Anleitung und Pull-Request-Ablauf (auf Englisch).
Bitte lies auch unseren [Code of Conduct](CODE_OF_CONDUCT.md).
