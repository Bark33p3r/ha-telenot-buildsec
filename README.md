# Telenot BuildSec für Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/Bark33p3r/ha-telenot-buildsec/actions/workflows/validate.yml/badge.svg)](https://github.com/Bark33p3r/ha-telenot-buildsec/actions/workflows/validate.yml)

> **English:** Unofficial local integration for Telenot complex 200H/400H intrusion alarm panels with IP module.
> It talks to the panel the same way the BuildSec app does (VdS 2465 S2, operator access with activation code)
> and emulates a keypad: arm away / arm home / disarm, display text, LEDs, zones and switch functions.
> Not affiliated with or endorsed by Telenot Electronic GmbH.

Inoffizielle Integration für Telenot-Einbruchmeldezentralen (complex 200H/400H) mit IP-Modul.
Sie verbindet sich **lokal** genauso wie die BuildSec-App und bildet ein **virtuelles Bedienteil** nach:
Display-Text, LEDs, Meldebereiche und Tasten.

> ⚠️ Inoffiziell, ohne Gewähr, kein Produkt der Telenot Electronic GmbH. Die erste Inbetriebnahme bitte vor Ort machen und einen
> aufgeschalteten Wachdienst vorab informieren.

## Was du brauchst (aus der BuildSec-App, Gerät → Bearbeiten)

| Feld | Beispiel |
|---|---|
| IP-Adresse / Port der Anlage | `192.168.1.50` / Port aus der App |
| Freischaltcode | 16 Hex-Zeichen |
| Bedienteilcode | dein Code (1–6 Ziffern) |
| Bedienteiladresse | meist `0` |

Die Integration nutzt die Betreiber-Berechtigung, also Freischaltcode plus Code, über eine direkte LAN-Verbindung.

## Installation

### Über HACS (empfohlen)

1. *HACS → ⋮ → Benutzerdefinierte Repositories*: `https://github.com/Bark33p3r/ha-telenot-buildsec`, Typ **Integration**.
2. „Telenot BuildSec“ suchen, herunterladen und Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Telenot BuildSec*.

[![Repository in HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bark33p3r&repository=ha-telenot-buildsec&category=integration)

### Manuell

1. Den Ordner `custom_components/telenot_buildsec` nach `/config/custom_components/` kopieren,
   z. B. mit dem File-Editor- oder SSH-Add-on.
2. Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Telenot BuildSec*.

**Wichtig:** Die Anlage erlaubt nur **eine** Fernverbindung. Solange HA verbunden ist, meldet die App
„belegt“. Mit dem Schalter **„Verbindung halten“** gibst du die Anlage für die App frei.

## Entitäten

- `alarm_control_panel`: Scharf/Unscharf schalten. Der Status kommt von den Bedienteil-LEDs oder vom Display.
- Sensoren: Display Zeile 1/2, Summer, Verbindung (mit `last_error`).
- Binärsensoren: Alarm, Störung, Betrieb, Gehtest, LED 1–4 mit Beschriftung aus der Anlage,
  dazu alle beschrifteten Meldebereiche.
- Buttons: beschriftete Schaltfunktionen der Anlage.
- Aktion `telenot_buildsec.send_keys`: beliebige Tastenfolge, z. B. `SA1234B` (Esc, Unscharf, Code, OK).

## Tastenfolgen einstellen (Optionen)

Die Bedienteil-Tasten heißen `0–9`, `A`, `B`, `E`, `I`, `S`, `+`, `-`. Das sind die Codes, die die App sendet.
Laut den Tastenbildern der App gilt: `E` = Extern scharf, `I` = Intern scharf, `A` = Unscharf, `B` = OK, `S` = Esc, `+`/`-` = runter/hoch. Die Kreis-Taste (Notruf) sendet die Integration absichtlich nie.
An einer complex 400H geprüft: Funktionstaste → Anzeige „Code:“ → Code → ✓ (`B`). Die Standardwerte sind deshalb
`SE{code}B` (extern scharf), `SI{code}B` (intern scharf) und `SA{code}B` (unscharf). Das `S` (Esc) vorne bricht eine noch offene Eingabe ab.
Falls deine Anlage anders reagiert, probiere die Folge mit `telenot_buildsec.send_keys` aus, beobachte dabei die
Display-Sensoren und trag die passende Folge in den Optionen ein.

Bei den LEDs wählt „Automatisch“ die LED mit der Beschriftung „extern scharf“ bzw. „intern scharf“. LEDs mit „bereit“ zählen nur als Bereit-Anzeige.

## Tests

`python tests/test_protocol.py` testet Verschlüsselung und Ablauf gegen eine simulierte Anlage.

## Hinweise

- Die Anlage erlaubt nur **eine** Fernverbindung gleichzeitig (HA *oder* BuildSec-App).
- Getestet mit einer complex 400H (Firmware 38.88) und direkter LAN-Verbindung zum IP-Modul. hiXserver/Cloud wird nicht unterstützt.
- Probleme bitte als [Issue](https://github.com/Bark33p3r/ha-telenot-buildsec/issues) melden, mit Debug-Log, **aber ohne Codes**:
  Debug-Logs enthalten den Bedienteilcode als Tastencodes.

## Haftungsausschluss

Dieses Projekt steht in keiner Verbindung zur Telenot Electronic GmbH. „Telenot“, „complex“ und „BuildSec“ sind Marken
der jeweiligen Inhaber und werden nur zur Beschreibung der Kompatibilität genannt. Nutzung auf eigenes Risiko.
