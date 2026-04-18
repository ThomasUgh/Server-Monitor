# Server Monitor – Benutzerhandbuch

**Server Monitor** beobachtet deinen Linux-Server und schreibt den aktuellen Zustand dauerhaft in **eine einzige Discord-Nachricht**, die regelmäßig aktualisiert wird. Bei wichtigen Ereignissen (z. B. volle Festplatte, Dienst ausgefallen, OOM-Kill) wird sofort aktualisiert.

---

## Was macht das Tool?

- Es zeigt dir jederzeit den aktuellen Gesundheitszustand deines Servers in Discord:
  CPU-Auslastung, RAM, Festplattenplatz, Netzwerktraffic, laufende Dienste.
- Es erkennt Probleme, die **über einen längeren Zeitraum** andauern (z. B. CPU seit 10 Minuten überlastet).
- Es liest das System-Log und meldet Fehler oder abgestürzte Prozesse.
- Es prüft optional Webseiten, Ports und Zertifikate.
- **Es verwendet immer dieselbe Discord-Nachricht** – kein Spam, nur Updates.

---

## Voraussetzungen

- Ubuntu 20.04 / 22.04 / 24.04 oder Debian 11/12
- Python 3.11 oder neuer
- `pip` und `venv` installiert

---

## Installation (Schritt für Schritt)

### 1. Dateien kopieren

```bash
# Projektordner anlegen
sudo mkdir -p /opt/server-monitor
sudo cp -r . /opt/server-monitor/
```

### 2. Python-Umgebung einrichten

```bash
cd /opt/server-monitor
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 3. Konfigurationsordner und -datei anlegen

```bash
sudo mkdir -p /etc/server-monitor
sudo cp config.yaml /etc/server-monitor/config.yaml

# Ordner für Zustandsdaten und Logs anlegen
sudo mkdir -p /var/lib/server-monitor
sudo mkdir -p /var/log/server-monitor
```

### 4. Benutzer anlegen (empfohlen)

```bash
sudo useradd -r -s /bin/false server-monitor
sudo chown -R server-monitor:server-monitor /opt/server-monitor
sudo chown -R server-monitor:server-monitor /var/lib/server-monitor
sudo chown -R server-monitor:server-monitor /var/log/server-monitor
```

### 5. Konfiguration anpassen

Öffne die Konfigurationsdatei:

```bash
sudo nano /etc/server-monitor/config.yaml
```

**Das Wichtigste zuerst:** Trage deine Discord-Webhook-URL ein (Zeile ganz oben in der Datei):

```yaml
discord_webhook_url: "https://discord.com/api/webhooks/DEINE_ID/DEIN_TOKEN"
```

> Webhook erstellen: Discord → Kanal-Einstellungen → Integrationen → Webhooks → Neuer Webhook

---

## Konfiguration anpassen

### Update-Intervall ändern

```yaml
update_interval_seconds: 60   # Alle 60 Sekunden aktualisieren (Standard)
```

Empfehlung: 60 bis 300 Sekunden. Werte unter 15 Sekunden können zu Discord-Ratenlimits führen.

---

### Schwellenwerte ändern

Unter `thresholds:` in der Config:

```yaml
thresholds:
  cpu_percent: 85          # Alarm ab 85 % CPU-Auslastung
  cpu_duration_minutes: 10 # ... wenn das 10 Minuten anhält
  ram_percent: 85          # Alarm ab 85 % RAM-Auslastung
  ram_duration_minutes: 10
  disk_percent: 85         # Alarm, wenn Festplatte zu 85 % voll
  swap_percent: 80
  iowait_percent: 25
  iowait_duration_minutes: 5
```

---

### Netzwerkinterface einstellen

```yaml
network:
  interface: "eth0"           # Name deines Netzwerk-Interfaces (eth0, ens18, ens3, ...)
  threshold_mbits: 100        # Alarm ab 100 Mbit/s
  duration_minutes: 10
```

Den Interface-Namen findest du mit: `ip link show` oder `ifconfig`

---

### Dienste überwachen

Unter `monitored_services:` trägst du die systemd-Dienste ein, die überwacht werden sollen:

```yaml
monitored_services:
  - nginx
  - postgresql
  - redis
  - docker
  # Weitere Dienste einfach untereinander eintragen (mit - davor)
```

---

### Festplattenpartitionen festlegen

```yaml
disk_mountpoints:
  - "/"           # Hauptpartition (immer sinnvoll)
  - "/data"       # Weitere Partition
```

Leer lassen = automatisch alle Partitionen erkennen.

---

### Ereignisse und Anzeige

```yaml
max_events_displayed: 10       # Wie viele Ereignisse im Embed stehen
severity_mode: "warning"       # warning = Warnungen + Fehler + Kritisches anzeigen
```

`severity_mode` Optionen:
| Wert | Bedeutung |
|------|-----------|
| `info` | Alles anzeigen (sehr viele Meldungen) |
| `warning` | Warnungen und schlimmer ← **Empfehlung** |
| `error` | Nur Fehler und Kritisches |
| `critical` | Nur kritische Fehler |

---

### HTTP-Checks (Webseiten prüfen)

```yaml
http_checks:
  - url: "https://meine-seite.de"
    name: "Meine Webseite"
    expected_status: 200
    timeout: 10
```

---

### Port-Checks

```yaml
port_checks:
  - host: "localhost"
    port: 5432
    name: "PostgreSQL"
```

---

### Zertifikat-Checks

```yaml
cert_checks:
  - host: "meine-seite.de"
    port: 443
    warning_days: 30     # Warnung 30 Tage vor Ablauf
    critical_days: 7     # Kritisch 7 Tage vor Ablauf
```

---

### Duplikate und Spam vermeiden

```yaml
dedupe:
  resource_cooldown_minutes: 15   # Ressourcen-Alarme max. alle 15 Min.
  service_cooldown_minutes: 5     # Dienst-Alarme max. alle 5 Min.
  journal_cooldown_minutes: 5
```

---

## Einmalig testen (ohne Service)

```bash
cd /opt/server-monitor
venv/bin/python main.py --config /etc/server-monitor/config.yaml
```

Das Tool startet und schreibt sofort eine Nachricht in Discord.  
Beende es mit `Strg+C`.

---

## Als systemd-Service einrichten

```bash
# Service-Datei kopieren
sudo cp server-monitor.service /etc/systemd/system/

# systemd neu laden
sudo systemctl daemon-reload

# Service aktivieren (startet automatisch beim Booten)
sudo systemctl enable server-monitor

# Service jetzt starten
sudo systemctl start server-monitor

# Status prüfen
sudo systemctl status server-monitor
```

---

## Log-Datei lesen

```bash
# Log-Datei direkt lesen:
tail -f /var/log/server-monitor/monitor.log

# Oder über systemd journal:
journalctl -u server-monitor -f
```

---

## Service neu starten / stoppen

```bash
sudo systemctl restart server-monitor
sudo systemctl stop server-monitor
```

---

## Häufige Fragen

**Die Discord-Nachricht wird nicht erstellt.**
→ Überprüfe die Webhook-URL in der Config. Teste sie mit:
`curl -X POST "DEINE_WEBHOOK_URL" -H "Content-Type: application/json" -d '{"content":"Test"}'`

**Die Nachricht zeigt immer denselben Stand.**
→ Prüfe ob der Service läuft: `sudo systemctl status server-monitor`

**Ich bekomme zu viele Meldungen.**
→ Setze `severity_mode: "error"` oder erhöhe die `cooldown_minutes` unter `dedupe:`.

**Das Tool erkennt mein Netzwerk-Interface nicht.**
→ Führe `ip link show` aus und trage den korrekten Interface-Namen unter `network.interface:` ein.

**Ich möchte die gespeicherte Discord-Nachricht zurücksetzen.**
→ Lösche die Zustandsdatei: `sudo rm /var/lib/server-monitor/state.json`  
  Das Tool erstellt beim nächsten Start eine neue Nachricht.

---

## Verzeichnisübersicht

| Pfad | Inhalt |
|------|--------|
| `/opt/server-monitor/` | Programmcode |
| `/etc/server-monitor/config.yaml` | Konfigurationsdatei |
| `/var/lib/server-monitor/state.json` | Zustand (Message-ID, letzte Ereignisse) |
| `/var/log/server-monitor/monitor.log` | Log-Datei |
