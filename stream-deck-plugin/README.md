# LoL Account Switcher — Stream Deck Plugin

Ein Stream Deck Plugin zum schnellen Account-Wechsel in League of Legends.

## Voraussetzungen

- Stream Deck App >= 7.1
- Windows 10/11
- `lolswitcher.exe` (oder ein Wrapper-Script auf `py main.py`) — aus Plan 02 gebaut
- Node.js >= 24 (nur fuer den Build)

## Build

```bash
cd stream-deck-plugin
npm install
npm run build
```

## Plugin packen und installieren

```bash
# Plugin als .streamDeckPlugin packen
npm run pack
# oder direkt:
npm run build
streamdeck pack com.lolswitcher.plugin.sdPlugin
```

Die erzeugte Datei `com.lolswitcher.plugin.streamDeckPlugin` in der Stream Deck App installieren
(Doppelklick auf die Datei).

## Property Inspector konfigurieren

Jeder Button benoetigt zwei manuelle Eintraege im Property Inspector:

| Feld | Beschreibung | Beispiel |
|------|-------------|---------|
| **Riot Username** | Der Riot-Benutzername des Accounts | `Maik123` |
| **Pfad zur lolswitcher.exe** | Absoluter Pfad zur ausfuehrbaren Datei | `C:\Program Files\LoLSwitcher\lolswitcher.exe` |

> **Hinweis (D-33):** Es gibt kein automatisches Dropdown aus `accounts.json` —
> der Username und der Pfad werden manuell eingegeben.

## Benoetigte Icon-Dateien

Vor dem finalen `streamdeck pack` muessen folgende PNG-Dateien erstellt werden
(andernfalls fehlen die Icons, aber das Plugin funktioniert technisch):

```
com.lolswitcher.plugin.sdPlugin/
  imgs/
    plugin/
      icon.png        # 72x72 px  (Plugin-Icon in der Stream Deck App)
      icon@2x.png     # 144x144 px
    actions/
      switch-inactive.png   # 72x72 px  (State 0 — inaktiver Account)
      switch-inactive@2x.png
      switch-active.png     # 72x72 px  (State 1 — aktiver Account, visuell abgehoben)
      switch-active@2x.png
```

## Architektur

Jeder Button ruft beim Druck `lolswitcher.exe switch <username>` via
`child_process.execFile` auf (kein Shell-Spawning — Command-Injection-Schutz).

- **Exit 0** → gruenem Haken (`showOk()`)
- **Exit != 0** → Warndreieck (`showAlert()`) — auch bei Match-Block und fehlendem Snapshot
- **Aktiv-Markierung** → `setState(1)` wenn `active_username` in `accounts.json` mit dem
  konfigurierten Username uebereinstimmt (3s-Polling)

## Sicherheitshinweise

- `exePath` wird vom User manuell konfiguriert; das Plugin fuehrt nur diesen Pfad aus
- `username` wird als literales Array-Element uebergeben (keine Shell-Expansion moeglich)
- `accounts.json` enthaelt keine Credentials (Passwoerter nur im Windows Credential Manager)
