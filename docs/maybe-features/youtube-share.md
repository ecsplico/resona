# Studie: YouTube-Videos und Shorts in die Resona PWA teilen

Status: Idee, nicht geplant. Stand: 2026-11-08.

**Frage:** Was bedeutet es, ein YouTube-Video oder einen Short per Teilen-Menü (Android/iOS) an die Resona PWA zu schicken und das Transkript in Resona zu erhalten?

**Kurzfassung:** Das Teilen liefert nur einen Link, keine Datei. Resona kann bisher ausschließlich hochgeladene Bytes verarbeiten. Es fehlt also ein Schritt, der aus dem Link Audio (oder vorhandene Untertitel) holt. Technisch ist das mit `yt-dlp` machbar und klein genug, um in den PWA-Projekt-Worker zu passen, ohne den Hub-Vertrag zu ändern. Das eigentliche Risiko liegt nicht im Code, sondern in Nutzungsbedingungen, Urheberrecht, Bot-Erkennung und dauerndem Wartungsaufwand. Empfehlung am Ende: nur als abschaltbare Funktion für selbst betriebene Installationen, nicht auf dem öffentlichen Hub.

Markierung: Aussagen mit **[Annahme]** habe ich nicht geprüft (externes Wissen, kein Zugriff in dieser Studie). Alle rechtlichen Punkte sind keine Rechtsberatung.

---

## 1. Was das Teilen heute liefert

### Ist-Zustand in der PWA

- Das Manifest registriert ein `share_target` (`apps/resona-pwa/nuxt.config.ts`, Zeilen 81–93): `POST /share-target`, `multipart/form-data`, Felder `title`, `text`, `url` und eine Datei `audio` (`audio/*`, `video/webm`, `.webm`, `.m4a`, `.mp3`, `.wav`, `.ogg`).
- Der Service Worker (`apps/resona-pwa/public/share-target-handler.js`) fängt den POST ab:
  - Datei vorhanden → Cache API, Redirect auf `/share-target?kind=audio`; `app/pages/share-target.vue` liest den Blob und öffnet `SaveSheet` → `useUpload().saveRecording`.
  - Keine Datei → Redirect auf `/live?title=<title || text || url>` (Titel-Vorbelegung, wie beim Kalender).
- Folge für YouTube: Ein geteilter Link landet heute **ohne Fehlermeldung in der Live-Aufnahme**, mit der URL als Titel. Das ist falsches Verhalten für diesen Anwendungsfall, unabhängig davon, ob die Funktion je gebaut wird.

### Was die Teilen-Aktion von YouTube mitgibt

- YouTube teilt Text und URL, keine Mediendatei. Android-Chrome-PWAs liefern die URL häufig im Feld `text`, nicht in `url` **[Annahme]**; deshalb muss die URL aus allen drei Feldern (`title`, `text`, `url`) per Regex extrahiert werden.
- URL-Formen **[Annahme]**: `https://youtu.be/<id>`, `https://www.youtube.com/watch?v=<id>`, `https://m.youtube.com/...`, `https://www.youtube.com/shorts/<id>`, mit Tracking-Parametern (`si=`, `feature=share`). Shorts verhalten sich für Download und Untertitel wie normale Videos.
- **iOS:** Das Web Share Target wird von iOS-Safari/Homescreen-Web-Apps meines Wissens nicht unterstützt **[Annahme]**. Auf iOS wäre das nur über einen Kurzbefehl (Shortcuts) lösbar, der `https://<pwa>/share-target?url=<kodierte URL>` öffnet. Dafür braucht die PWA zusätzlich zur bestehenden POST-Route eine **GET-Variante** der Route (heute gibt es nur POST im Service Worker). Auch für Android ist ein GET-Fallback nützlich (Link manuell einfügen, Deep-Link).

---

## 2. Wie kommt man an Audio oder Text?

| Weg | Was passiert | Vorteile | Nachteile |
|---|---|---|---|
| **A. Audio laden (`yt-dlp`), dann Resona-ASR** | Audio-Stream herunterladen, per ffmpeg auf 16 kHz mono, normaler `audio`-Job | Gleiche Pipeline, gleiche Qualität, Postprocess-Profile, Provenienz, funktioniert auch ohne Untertitel | Rechtlich/ToS heikel, Bot-Erkennung, Wartung, Bandbreite, Rechenzeit |
| **B. Vorhandene Untertitel/Transkript abrufen** | Nur Textspur holen (manuelle oder automatische Untertitel), kein Download von Audio | Schnell, billig, kein ASR, keine Audio-Kopie | Nicht jedes Video hat Untertitel; automatische Untertitel sind schwächer und ohne Satzzeichen; Abruf ebenfalls inoffiziell **[Annahme]** |
| **C. Offizielle YouTube Data API** | `captions.list`/`captions.download` | Offiziell | `captions.download` verlangt OAuth und Rechte am Video (nur eigene Videos) **[Annahme]**; Kontingent (Quota) begrenzt; liefert kein Audio |
| **D. Nutzer lädt selbst Audio hoch** | Heutiger Weg (Datei teilen/hochladen) | Rechtlich Sache des Nutzers, null neuer Code | Umständlich, kein „Teilen aus der YouTube-App" |

### Zu A: `yt-dlp` im Detail **[Annahme]** (alles zu yt-dlp/YouTube ist externes Wissen)

- Braucht Python plus `ffmpeg` auf dem Host und häufige Updates, weil YouTube die Zugriffswege oft ändert. Ein veralteter `yt-dlp` fällt nach Wochen aus. Im Betrieb heißt das: automatisches Update im Container-Build oder per Cron, Gesundheitscheck mit einem festen Testvideo.
- **Bot-Erkennung:** Rechenzentrums-IPs werden oft mit „Sign in to confirm you're not a bot" abgewiesen. Gegenmittel (Cookies aus einem angemeldeten Konto, Proof-of-Origin-Token, Proxys) sind Wartungs- und Risikoquellen. **Cookies eines echten Kontos auf einem gemeinsam genutzten Server sind keine gute Idee** (Kontosperrung, Geheimnis im Worker). In dieser Studie wird keine Cookie-Lösung vorgeschlagen; ohne sie ist die Zuverlässigkeit von einem Server-Standort aus nicht garantiert.
- **Ratenbegrenzung:** pro Worker serialisieren, kleine Wartezeit zwischen Abrufen, kein Massenabruf; Playlists und Kanäle grundsätzlich ablehnen (`--no-playlist`).
- **Live-Streams, Premieren, Altersbeschränkung, Mitglieder-/Privatvideos:** nicht abrufbar ohne Anmeldung; klare Fehlermeldung statt Retry-Schleife.
- **Größe/Dauer:** nur Audio-Format (`bestaudio`), Dauer- und Bytegrenze vor dem Download prüfen (Metadatenabruf liefert Dauer). Vorschlag: max. 2 Stunden und max. 200 MiB, passend zur Obergrenze des Hubs (siehe Abschnitt 3, „Größen- und Dauerlimits“).

### Rechtliches und Nutzungsbedingungen **[Annahme], keine Rechtsberatung**

- Die YouTube-Nutzungsbedingungen verbieten meines Wissens das Herunterladen von Inhalten außer über ausdrücklich angebotene Funktionen. Ein Server, der das automatisiert tut, verstößt wahrscheinlich dagegen. Das Risiko trägt der Betreiber der Installation (beim öffentlichen Hub also der Betreiber von `resona.ecsplico.de`), nicht der Nutzer allein.
- Urheberrecht: Transkript und Audiokopie sind Vervielfältigungen. Das deutsche Recht kennt private Kopien, aber mit Grenzen (Umgehung technischer Schutzmaßnahmen, offensichtlich rechtswidrige Vorlage). Eigene Videos, Creative-Commons-Videos und Material mit Erlaubnis sind unkritisch.
- Datenschutz/Einwilligung: Gesprochene Inhalte Dritter in einem Video sind personenbezogene Daten. Bei Resona (Schwerpunkt medizinische Diktate) ist der Einsatz mit fremden Videos nicht der Kernfall; der Hub darf daraus aber nichts Unnötiges speichern.
- Folgerung für das Design: **Audiokopie nach dem Job löschen** (nur Transkript und Quellenangabe behalten), Funktion standardmäßig **aus**, Hinweis im Dialog („Nur für eigene oder frei lizenzierte Inhalte"), keine Weitergabe-Funktion.

---

## 3. Wo würde das in der Resona-Architektur laufen?

### Rahmenbedingungen im Repo

- Der Hub kennt nur zwei Job-Arten: `JobKind = Literal["audio", "document"]` (`packages/job-core/src/resona_job_core/settings.py`). Jobs entstehen aus einem **Upload** (`POST /v1/uploads`, Magic-Byte-Prüfung, `audio|document`, Obergrenze `RESONA_MAX_UPLOAD_BYTES`, im Code Standard 200 MiB in `jobs_routes.py`; `CLAUDE.md` nennt 500 MiB, der Code gilt) plus `POST /v1/jobs` mit `Idempotency-Key`. Es gibt **keine URL-Eingabe**.
- Der Hub führt selbst keine Inferenz aus (er verwaltet Uploads, Jobs und Ergebnisse); Worker holen Jobs aktiv ab (outbound-only, kein offener Port, `apps/resona-worker`). Der Ausführungs-Subprozess läuft im privaten Fall mit Allowlist-Umgebung und erzwungenem Offline-Modus (`subprocess_env(private=True)`, `HF_HUB_OFFLINE`). Ein Netzwerk-Download passt nicht in diesen Subprozess.
- Richtlinien: `ExecutionPolicy`/`Stage` kennen nur `asr`, `ocr`, `llm` (`packages/job-core/src/resona_job_core/policy.py`, `StageKind`); Endpunkt-Arten `local_llm`, `cloud_asr`, `cloud_llm`. „Privat" heißt: keine Audio- oder Dokument-Bytes an Dritte. Ein Abruf von YouTube sendet zwar keine Nutzerdaten, aber die **URL und die IP des Workers** an Google; das ist ein neuer Egress-Typ, den die Richtlinie heute nicht beschreibt.
- Aufbewahrung: Ergebnisse 24 h, Metadaten 7 Tage (`maintenance.py`, `METADATA_TTL`), nicht angehängte Uploads verfallen nach 1 h.
- Die PWA hat bereits eine Stelle, die Inhalte holt und an den Hub gibt: den **Projekt-Worker** (`apps/resona-pwa/worker/`, `worker.py`): er liest `pending`-Zeilen aus Directus (`Source("recordings", "audio_file", "audio", "transcribing")`), lädt Audio hoch, legt den Hub-Job an, pollt und schreibt das Transkript zurück. Er hat eine eigene `uv`-Umgebung und keinen Import aus `packages/*`.
- Standalone-Modus (IndexedDB, `localProcessor.ts`) kann das **nicht**: Ein Browser darf `googlevideo.com` nicht per `fetch` lesen (CORS), und `yt-dlp` läuft nicht im Browser. Die Funktion gäbe es nur im Directus-Modus (so läuft `resona.ecsplico.de`).

### Optionen

**Option 1 (Empfehlung für einen Spike): Abruf im PWA-Projekt-Worker, Hub unverändert.**

```
Teilen → /share-target (SW erkennt YouTube-URL) → Bestätigungsdialog
  → Directus: recordings { source: "url", source_url, status: "fetching" }
  → project-worker: Allowlist prüfen → yt-dlp (nur Audio, Limits) → ffmpeg 16 kHz mono
      → Datei in audio_file ODER direkt Upload an den Hub → status "pending"
  → vorhandene Schleife: POST /v1/uploads + /v1/jobs (audio) → Transkript → done
  → Audiokopie löschen, Quelle + Transkript behalten
```

Vorteile: kein Eingriff in Hub-Protokoll, Policy, Settings-Schema oder Worker-Release; Egress liegt in einem eigenen Dienst, den man separat abschalten, drosseln und netzwerkseitig einschränken kann; Fehlermeldungen und Zustandsmaschine (`fetching` → `pending` → `transcribing`) passen in das bestehende Muster (Stale-Recovery in `directus.py`).
Nachteile: Der PWA-Stack bekommt `ffmpeg` und `yt-dlp` im Image; das Transkript hat dann Provenienz „Hub-Job auf hochgeladener Datei", nicht „YouTube-Link" (muss in `source_url` stehen).

**Option 2: Neue Job-Art `url` / Fetch-Stufe auf dem Resona-Worker.**

Der Hub nähme `POST /v1/jobs` mit `kind: "url"` und einem validierten `source_url` an; ein Worker mit Fähigkeit `media-fetch` lädt das Audio im eigenen Netzwerk-Subprozess und führt danach ASR aus. Das erfordert: neue `JobKind`, neuer `StageKind` (z. B. `fetch`) und Endpunkt-Art, Organisations-Einstellung (z. B. `url_fetch_enabled`, Standard `false`; `OrganizationSettings` ist `extra="forbid"`, also Schemaänderung samt Hub-Migration und Worker-Protokoll-/Release-Abstimmung), Worker-Manifest/`prepare`, Evaluations-Gate für den Kandidaten, MCP- und CLI-Oberfläche, Docs. Es lohnt sich nur, wenn mehrere Clients (CLI, MCP) URL-Jobs wollen. Für die PWA allein ist es überdimensioniert.

**Option 3: Nur Untertitel (Weg B) im Projekt-Worker**, ohne Audio, ohne ASR. Sehr klein, keine Audiokopie, aber nur für Videos mit Untertiteln und mit schwächerer Qualität. Als Vorstufe von Option 1 sinnvoll (Untertitel zuerst, Audio nur auf Wunsch oder als Rückfall).

### Datenschutzklassen und Richtlinien

- Der Fetch-Schritt ist kein ASR-Stufe der Hub-Richtlinie. In Option 1 liegt er außerhalb des Hubs; der anschließende `audio`-Job ist ein normaler, **privater** Job (`private=true`, Standard der PWA über `useHub()`), also keine Cloud-ASR, wenn der Nutzer das nicht ausdrücklich wählt.
- Wenn der Nutzer die Cloud-Erlaubnis nicht erteilt hat, bleibt die Auswertung lokal; der Fetch selbst muss im Dialog klar als „Abruf bei YouTube" benannt werden (URL/IP gehen an Google). Eine Prüfung „Org-Einstellung erlaubt URL-Abruf" gibt es in Option 1 nur auf PWA-Seite (Konfiguration `NUXT_PUBLIC_…`/Worker-Env), nicht im Hub. Wer das serverseitig erzwingen will, braucht Option 2.
- Option 2 bräuchte: `check_policy` für die neue Stufe vor Admission (403 `policy_denied`), frische Prüfung vor dem Abruf (`/v1/workers/jobs/{id}/policy`), Abruf nur auf einem Worker mit ausdrücklicher Freigabe.

### Größen- und Dauerlimits

- Vor dem Download Metadaten abfragen (Dauer, Livestatus, Format), abbrechen bei Überschreitung, außerdem `--max-filesize` als zweite Sicherung. Nach ffmpeg-Umwandlung (16 kHz mono PCM16, ca. 2 MB/min, wie `utils/audioMerge.ts` der PWA) ergeben 2 Stunden etwa 230 MB, also über 200 MiB. Entweder Grenze auf ca. 90 Minuten setzen oder in kompaktem Format (Opus/M4A) hochladen und den Worker umwandeln lassen. Das ist vor einer Umsetzung zu entscheiden.
- Die 24 h Aufbewahrung des Hubs betrifft nur den Hub-Job (der Projekt-Worker löscht ihn nach dem Zurückschreiben); Transkript und Quelle liegen danach in Directus.

---

## 4. Sicherheit

- **SSRF:** Der Nutzer liefert eine beliebige Zeichenkette; der Worker darf nie diese Zeichenkette an `yt-dlp` geben. Stattdessen: URL parsen, nur Schema `https`, kein Benutzername/Passwort, kein Port, Host exakt aus Allowlist (`youtube.com`, `www.youtube.com`, `m.youtube.com`, `youtu.be`; optional `music.youtube.com`), Video-ID per Regex `^[A-Za-z0-9_-]{11}$` extrahieren und daraus selbst `https://www.youtube.com/watch?v=<id>` bauen. Shorts-Pfad `/shorts/<id>` auf dieselbe Form normalisieren. Playlist- und Kanal-Parameter verwerfen.
- `yt-dlp` unterstützt viele Extraktoren und `file://`-artige Quellen **[Annahme]**: den YouTube-Extraktor erzwingen, Generic-Extraktor abschalten, Konfigurationsdateien des Hosts nicht laden (`--ignore-config`), keine Plugins.
- Der Worker-Container bekommt **keine** Hub-/Directus-Schreibgeheimnisse mehr als nötig und läuft mit eingeschränktem Netz (Egress nur zu YouTube-/googlevideo-Zielen und zu Directus/Hub; interne Adressbereiche sperren, damit eine Umleitung nicht intern landet). Das Netz-Allowlisting ist unabhängig von der URL-Prüfung nötig, weil `yt-dlp` weitere Hosts selbst auflöst.
- Befehlsausführung nur mit Argumentliste, nie über eine Shell; Ausgabepfade selbst erzeugen (Temp-Verzeichnis pro Auftrag, Aufräumen im `finally`).
- Bei Option 1 darf die PWA selbst nichts abrufen: Der Browser sendet nur die Zeile nach Directus; die Allowlist-Prüfung läuft **serverseitig im Worker**, die Prüfung in der PWA ist nur Komfort.

---

## 5. Änderungen in der PWA

1. **Service Worker** (`public/share-target-handler.js`): Text/URL-Shares auf YouTube-URL prüfen (alle Felder), bei Treffer nach `/share-target?kind=url&u=<kodiert>` umleiten statt nach `/live`. Nicht-YouTube-Text bleibt wie heute (`/live?title=`). Zusätzlich GET-Route `/share-target?url=` für Kurzbefehle/Deep-Links.
2. **`pages/share-target.vue`:** zusätzlicher Zweig `kind=url`: Vorschau (Titel/ID), Sprache und Profil wie `SaveSheet`, Hinweistext zu ToS/Eigengebrauch, Button „Transkribieren". Fehler und „Funktion nicht aktiviert" als eigener Zustand.
3. **Datenmodell** (`directus/bootstrap.md`, `app/types/directus.ts`, `bootstrap.mjs`): siehe Abschnitt 6.
4. **Listen/Detailseite:** Zeilen ohne `audio_file` zeigen Quelle (Link, Kanal, Dauer) statt Player; Status `fetching` mit eigener `StatusChip`-Beschriftung („Wird geladen"). Die Detailseite `recording/[id].vue` setzt heute einen Player voraus; die Anpassung dort gehört in dieselbe Änderung, kollidiert aber mit der laufenden Arbeit an dieser Datei und muss mit deren Bearbeiter abgestimmt werden.
5. **Konfiguration:** Schalter (z. B. `NUXT_PUBLIC_URL_IMPORT_ENABLED`, Standard `false`) analog zu `NUXT_PUBLIC_CALENDAR_ENABLED`; Worker-seitig ein eigener Schalter, damit ein Client allein die Funktion nicht aktiviert.
6. **Standalone-Modus:** Funktion ausblenden (Abschnitt 3).
7. **Tests:** Vitest für URL-Normalisierung (alle Formen, Ablehnungsfälle), SW-Handler-Logik, Worker-pytest für Allowlist, Limits, Zustandsübergänge mit gemocktem `yt-dlp`.

---

## 6. Datenmodell

Erweiterung von `recordings` (statt neue Sammlung, damit Liste, Suche, Export, Bearbeitung und Review-Status weiter greifen):

| Feld | Typ | Zweck |
|---|---|---|
| `source` | Wert `url` ergänzen (heute `batch` \| `live`) | Herkunft |
| `status` | Wert `fetching` ergänzen (heute `pending` \| `transcribing` \| `done` \| `error`) | Abruf läuft |
| `source_url` | string | kanonische URL (`https://www.youtube.com/watch?v=<id>`) |
| `source_meta` | json | `video_id`, Titel, Kanal, Dauer, Abrufweg (`audio`/`captions`), Zeitpunkt |
| `audio_file` | bleibt, aber **nullable** und nach Abschluss geleert | Audiokopie nicht dauerhaft halten |

`transcripts.engine`: bei Untertitelweg ein eigener Stempel wie `youtube-captions`, beim ASR-Weg wie heute der Hub-Stempel. Die Stale-Recovery im Worker (`directus.py`) muss `fetching` ohne Fortschritt auf `pending` zurücksetzen (wie `transcribing` ohne `hub_job_id`). Fehlertexte auf Deutsch wie in `worker.py`.

Hub: keine Änderung in Option 1. In Option 2 zusätzlich `kind: "url"` im Job-Request, `source_url` im `execution_snapshot`, Provenienz-Eintrag der Fetch-Stufe in `JobResult.provenance`.

---

## 7. Aufwand nach Schnitten

Größen: S ≈ bis 1 Tag, M ≈ 2–4 Tage, L ≈ 1–2 Wochen (jeweils eine Person, inklusive Tests).

| # | Schnitt | Größe | Abhängigkeit |
|---|---|---|---|
| 1 | Share-Target erkennt YouTube-URL (SW, GET-Route, URL-Normalisierung, Bestätigungsdialog); behebt nebenbei das „URL als Live-Titel"-Verhalten | S | – |
| 2 | Datenmodell (`bootstrap`, Typen, Feature-Schalter) | S | – |
| 3 | Worker-Abruf mit `yt-dlp`: Allowlist, Limits, ffmpeg, Zustände, Aufräumen, Image-Anpassung, Tests | M | 2 |
| 4 | UI: Status, Quelle statt Player, Fehlerzustände | S–M | 2, Abstimmung mit der Detailseiten-Arbeit |
| 5 | Untertitel-zuerst-Pfad mit Rückfall auf Audio | M | 3 |
| 6 | Betrieb: yt-dlp-Updates, Gesundheitscheck, Netz-Allowlist, Dokumentation, ToS-Hinweis | M (laufend) | 3 |
| 7 | iOS-Kurzbefehl dokumentieren und GET-Route testen | S | 1 |
| 8 | Hub-native Job-Art `url` (Option 2) | L | Entscheidung, 3 gelernt |

Spike (1 + 2 + 3, ohne Untertitel): etwa 1 Woche.

---

## 8. Risiken

1. **ToS/Urheberrecht** (Abschnitt 2): höchstes Risiko, nicht durch Technik lösbar.
2. **Zuverlässigkeit:** Bot-Erkennung und ständige Änderungen bei YouTube; ohne Cookies von Rechenzentrums-IPs oft nicht erreichbar **[Annahme]**. Ein Feature, das „manchmal" geht, erzeugt Support.
3. **Wartung:** `yt-dlp`-Updates sind ein Dauerauftrag.
4. **Verwechslung mit Privatsphäre-Versprechen:** Resona wirbt mit privater Verarbeitung; der Abruf geht ins Internet. Muss im UI und in `docs/guide/privacy.md` stehen.
5. **Speicher/Kosten:** lange Videos → viel ASR-Zeit auf dem Worker; Warteschlange kann für medizinische Diktate blockieren. Mitigation: Dauergrenze; eine Prioritätssteuerung für Jobs habe ich im Hub-Code nicht gefunden **[INFERENCE: nicht gezielt gesucht]**.
6. **Detailseite/Player:** Bestehende UI nimmt Audio an.
7. **Fehlklassifizierung beim Teilen:** Nicht jeder geteilte Link ist YouTube; bei Nicht-Treffer muss das alte Verhalten bleiben.

---

## 9. Empfehlung

- **Jetzt unabhängig von der Funktion beheben:** Beim Teilen eines Links (Text/URL ohne Datei) nicht stillschweigend eine Live-Aufnahme mit der URL als Titel öffnen. Mindestens eine Meldung „Links können noch nicht transkribiert werden" (Schnitt 1, klein).
- **Funktion selbst: zurückstellen, bis die Rechtsfrage für den eigenen Einsatz geklärt ist.** Wenn es gebraucht wird: Option 1 als Spike, **standardmäßig aus**, nur für selbst betriebene Installationen, **nicht auf dem öffentlichen Hub**; Untertitel zuerst (Option 3) als risikoärmerer Einstieg, Audio nur als bewusster Rückfall; Audiokopie nie dauerhaft speichern; keine Cookies.
- **Hub-native Job-Art (Option 2) nicht bauen**, solange nur die PWA den Bedarf hat.
- Offene Entscheidungen vor einem Spike: (a) Dauer-/Größengrenze gegen die 200-MiB-Hubgrenze, (b) Untertitelweg ja/nein, (c) wo der Schalter lebt (nur PWA/Worker-Env), (d) wer die rechtliche Einschätzung gibt.
