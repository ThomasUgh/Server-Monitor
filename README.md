# Server Monitor

Überwacht deinen Linux-Server dauerhaft und zeigt den aktuellen Zustand in **einer einzigen Discord-Nachricht**, die regelmäßig aktualisiert wird. Bei Fehlern oder Problemen wird sofort gemeldet.

---

## Was macht das Tool?

- Zeigt CPU, RAM, Festplatte, Netzwerk, Uptime live in Discord
- Erkennt Probleme die **längere Zeit andauern** (z. B. CPU seit 10 Min. überlastet)
- Liest das System-Log und meldet Fehler, OOM-Kills und Service-Ausfälle
- Prüft optional Webseiten, Ports und Zertifikate
- Verwendet immer **dieselbe Discord-Nachricht** – kein Spam, nur Updates

---

## Voraussetzungen

- Ubuntu 20.04 / 22.04 / 24.04 oder Debian 11 / 12
- Python 3.11 oder neuer
- `git`, `python3-venv` und `unzip` installiert

Fehlende Pakete installieren:
```bash
apt update && apt install -y git python3-venv python3-pip
```

---

## Installation

### 1. Repo klonen

```bash
git clone https://github.com/ThomasUgh/Server-Monitor.git /opt/server-monitor
cd /opt/server-monitor
```

### 2. Python-Umgebung einrichten

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

> ⚠️ Wichtig: Immer `venv/bin/python` verwenden, nicht das System-Python.  
> Sonst kommt der Fehler `ModuleNotFoundError: No module named 'psutil'`.

### 3. Konfiguration anlegen

```bash
mkdir -p /etc/server-monitor
cp config.yaml /etc/server-monitor/config.yaml
nano /etc/server-monitor/config.yaml
```

**Das Wichtigste:** Webhook-URL ganz oben eintragen:
```yaml
discord_webhook_url: "https://discord.com/api/webhooks/DEINE_ID/DEIN_TOKEN"
```

> Webhook erstellen: Discord → Kanal → Einstellungen → Integrationen → Webhooks → Neuer Webhook

### 4. Ordner und Benutzer anlegen

```bash
useradd -r -s /bin/false server-monitor

mkdir -p /var/lib/server-monitor /var/log/server-monitor

chown -R server-monitor:server-monitor \
  /opt/server-monitor \
  /var/lib/server-monitor \
  /var/log/server-monitor
```

### 5. Einmalig testen

```bash
cd /opt/server-monitor
venv/bin/python main.py --config /etc/server-monitor/config.yaml
```

Wenn in Discord eine Nachricht erscheint → alles gut. Mit `Strg+C` beenden.

### 6. Als dauerhaften Service aktivieren

```bash
cp server-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now server-monitor
systemctl status server-monitor
```

Der Service startet jetzt automatisch nach jedem Reboot.

---

## Updates einspielen

```bash
cd /opt/server-monitor
git pull
venv/bin/pip install -r requirements.txt
systemctl restart server-monitor
```

---

## Konfiguration anpassen

Alle Einstellungen sind in `/etc/server-monitor/config.yaml`.  
Nach jeder Änderung: `systemctl restart server-monitor`

### Update-Intervall

```yaml
update_interval_seconds: 60   # Wie oft Discord aktualisiert wird (Sekunden)
```

### Schwellenwerte

```yaml
thresholds:
  cpu_percent: 85            # Alarm ab 85 % CPU
  cpu_duration_minutes: 10   # ... wenn das 10 Minuten anhält
  ram_percent: 85
  disk_percent: 85
```

### Netzwerk-Interface

```bash
ip link show   # Interface-Namen anzeigen
```

```yaml
network:
  interface: "eth0"        # Deinen Interface-Namen eintragen
  threshold_mbits: 100
```

### Dienste überwachen

```yaml
monitored_services:
  - nginx
  - postgresql
  - docker
```

### Festplatten

```yaml
disk_mountpoints:
  - "/"
  - "/data"    # Weitere Partitionen ergänzen
```

### Ereignisse

```yaml
max_events_displayed: 10
severity_mode: "warning"     # info | warning | error | critical
```

### Journal-Lookback beim Start

Beim Start liest der Monitor rückwirkend ins System-Log.

```yaml
journal:
  lookback_enabled: true       # true = beim Start zurückschauen
  lookback_minutes: 60         # Wie weit zurück (Minuten)
  lookback_min_priority: "error"  # Nur Fehler+, kein Info-Spam
```

---

## Nützliche Befehle

```bash
# Live-Log anzeigen
journalctl -u server-monitor -f

# Log-Datei lesen
tail -f /var/log/server-monitor/monitor.log

# Service neu starten (z. B. nach Config-Änderung)
systemctl restart server-monitor

# Discord-Nachricht zurücksetzen (neue Nachricht erstellen)
systemctl stop server-monitor
rm /var/lib/server-monitor/state.json
systemctl start server-monitor
```

---

## Häufige Probleme

**`ModuleNotFoundError: No module named 'psutil'`**  
→ Das venv wurde noch nicht eingerichtet. Ausführen:
```bash
cd /opt/server-monitor
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

**Die Discord-Nachricht wird nicht erstellt**  
→ Webhook-URL in der Config prüfen. Testen:
```bash
curl -X POST "WEBHOOK_URL" -H "Content-Type: application/json" -d '{"content":"Test"}'
```

**Zu viele Meldungen**  
→ `severity_mode: "error"` setzen oder `cooldown_minutes` unter `dedupe:` erhöhen.

**Falsches Netzwerk-Interface**  
→ `ip link show` ausführen und den richtigen Namen unter `network.interface:` eintragen.

---

## Dateipfade

| Pfad | Inhalt |
|---|---|
| `/opt/server-monitor/` | Programmcode |
| `/etc/server-monitor/config.yaml` | Konfiguration |
| `/var/lib/server-monitor/state.json` | Zustand & Message-ID |
| `/var/log/server-monitor/monitor.log` | Log-Datei |
