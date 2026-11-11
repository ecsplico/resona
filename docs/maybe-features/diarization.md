# Studie: Sprechertrennung (Diarisierung) in Resona und Forge

Status: Empfehlung, nicht geplant. Stand: 2026-11-10.

**Frage:** Wie bauen wir „wer hat wann gesprochen“ am besten in Resona und in den Forge-Editor ein?

**Kurzfassung:**

- **Die Sprechertrennung läuft im Resona-Worker, nicht in Forge.** Der Worker hat die Audiodaten, die Wortzeitstempel und das Datenschutzmodell (private Aufträge verlassen den freigegebenen Worker nicht). Forge bekommt fertige Sprecherwechsel und zeigt sie an.
- **Modell: NVIDIA `Nemotron-3-Diarization`**, lokal im Worker. Es ist ohne Freischaltung herunterladbar, kommerziell nutzbar, auf dem Mac schnell (13,4 min Audio in 5–9 s, Abschnitt 2) und kann später auch live trennen. pyannote `community-1` bleibt die Alternative, falls die deutsche Messung (Ticket R3) dagegen spricht oder Aufnahmen mehr als 8 Sprecher haben.
- **Zuordnung pro Wort, nicht pro ASR-Segment.** In der Messung lag die Wortzuordnung bei 96–97 % richtig zugeordneten Wörtern, die Segmentzuordnung nur bei 80–82 % (Abschnitt 2). Der Worker muss dafür die Wortzeitstempel behalten, die er heute verwirft.
- **Ausgabe:** `Segment.speaker` (1, 2, … in Reihenfolge des ersten Auftretens) und Markdown im Format `**Sprecher 1:** …`. Dasselbe Format nutzt der Forge-Editor schon für Redebeiträge (KI-Protokoll, `packages/editor/src/features/ai/protocol/chat.ts`); ob auch der Markdown-Import daraus Sprecherblöcke macht, ist nicht geprüft.
- **Forge:** (1) Sprecher durch die vorhandene Transkriptionskette reichen, (2) einen `resona`-Anbieter über die Job-API, (3) „Sprecher umbenennen/zusammenführen“. Optional Deepgram-Diarisierung für Installationen ohne medizinische Daten.

Markierung: **[Annahme]** = nicht geprüft; **[INFERENCE]** = aus gelesenem Code oder Messungen geschlossen, nicht ausgeführt. Bestandsaufnahme und Marktüberblick mit Quellen: `forge-mono/apps/editor-docs/research/diarization.md` (im Folgenden „Forge-Notiz“).

---

## 1. Ausgangslage (kurz)

- **Resona** kennt keine Sprecher. `Segment` hat nur `start`, `end`, `text` und verbietet weitere Felder (`packages/job-core/src/resona_job_core/results.py`, `Segment`, `_Strict` mit `extra="forbid"`). Der Worker reicht `word_timestamps` an die Engine weiter, `_segment()` behält aber nur `start/end/text` (`apps/resona-worker/src/resona_worker/execute.py`, `_segment`). Die Wortzeiten werden also berechnet und weggeworfen.
- **Forge** hat die Dokumentseite fast fertig: Der Block `transcriptSegment` trägt `speaker`, `start`, `end`; Redebeiträge heißen im KI-Protokoll `**Name:** Text`. Die Transkriptionskette verwirft Sprecher aber an drei Stellen: `timedSegments()` behält nur `{start, end, text}` (`packages/editor/src/server/extract.ts`), `SpeechSegment` hat kein `speaker` (`core/backend-types.ts`), und `renditionBody()` schreibt `speaker: ''` (`integrations/composite-ai/rendition.ts`). Alles geprüft am Stand vom 2026-11-10.

## 2. Messung auf dem Mac (M5 Pro)

Zweck: die beiden offenen Fragen der Forge-Notiz beantworten, nämlich wie schnell die Modelle auf Apple Silicon und CPU sind, und ob Zuordnung pro Wort oder pro Segment nötig ist.

**Aufbau** (Skripte in `.scratch/diarization-bench/`, nicht eingecheckt):

- Testaudio: deutsche Fallbesprechung mit 4 Stimmen (`say`, Stimmen Anna, Flo, Sandy, Reed), 18 Wortbeiträge, darunter 4 kurze Rückmeldungen („Mhm.“, „Okay.“), die 0,35 s in den vorherigen Beitrag hineinlappen.
  - Kurz: 80,6 s.
  - Lang: dasselbe zehnmal hintereinander, 806 s.
  - Referenz: die exakten Zeiten jedes Clips.
- ASR: `mlx-whisper` large-v3-turbo mit Wortzeitstempeln, 7 s bzw. 20 s Laufzeit.
- Maße:
  - DER: Diarisierungsfehler, ohne Toleranzrand, Überlappung mitgezählt, mit `pyannote.metrics` berechnet.
  - Wortzuordnung: Anteil der ASR-Wörter, die den richtigen Sprecher bekommen.

**Ergebnis:**

| System | Gerät | Laufzeit kurz / lang | DER kurz / lang | Sprecher erkannt (kurz) | Wortzuordnung kurz / lang | Segmentzuordnung lang |
|---|---|---|---|---|---|---|
| Nemotron-3, NeMo-Speech.cpp, `--offline` (volle Aufmerksamkeit, nur ≤ 6,6 min) | Metal | 0,46 s / – | 14,1 % / – | 4/4 | 91,6 % / – | – |
| Nemotron-3, NeMo-Speech.cpp, `v3-offline` | Metal | 0,48 s / 5,3 s | 25,9 % / 11,5 % | 3/4 | 79,2 % / 95,8 % | 80,4 % |
| Nemotron-3, NeMo-Speech.cpp, `v3-offline` | CPU | 11,8 s / 59,3 s | 25,9 % / 11,7 % | 3/4 | 79,2 % / 95,6 % | 80,4 % |
| Nemotron-3, NeMo-Speech.cpp, `v3-streaming` | Metal | 4,6 s / 46,9 s | 21,2 % / 10,2 % | 4/4 | 85,4 % / 97,6 % | 81,8 % |
| Nemotron-3, transformers 5.22.2, offline | MPS (PyTorch) | 11,4 s* / 9,5 s | 22,0 % / 6,6 % | 4/4 | 80,3 % / 97,4 % | 80,9 % |
| sherpa-onnx 2.x, pyannote-seg-3.0 + 3D-Speaker ERes2Net | CPU, 4 Threads | 64,4 s / – | – | – | 82,0 % / – | – |
| sherpa-onnx 2.x, pyannote-seg-3.0 + NeMo TitaNet-small | CPU, 4 Threads | – | – | – | 84,3 % / – | – |
| pyannote `community-1` | – | nicht gemessen: Modell auf Hugging Face freischaltpflichtig, Bedingungen für das Konto nicht akzeptiert | | | | |

\* inklusive einmaligem Aufwärmen von MPS; der zweite Aufruf (lang) zeigt die echte Geschwindigkeit (85-fach Echtzeit).

**Was daraus folgt:**

1. **Geschwindigkeit ist kein Problem.**
   - Nemotron-3 trennt 13,4 min Audio auf dem Mac-GPU in 5–9 s, also schneller als das Whisper-Transkript (20 s).
   - Selbst reine CPU schafft 13,6-fache Echtzeit. Damit passt die Funktion auch auf den CPU-Host (`docker-compose.cpu-standalone.yml`) **[INFERENCE: Messung auf M5-CPU, nicht auf dem Linux-Host]**.
   - sherpa-onnx braucht für 80 s Audio 64 s auf der CPU. Es scheidet als Standard aus, bleibt aber die Option für Geräte ohne PyTorch.
2. **Wortzuordnung ist Pflicht.** Auf der langen Aufnahme liegt die Wortzuordnung 15–17 Punkte über der Segmentzuordnung. Whisper-Segmente laufen über Sprecherwechsel hinweg; das hatte die Forge-Notiz vermutet, die Messung bestätigt es.
   - Den Wechsel zusätzlich auf das nächste Satzzeichen zu schieben (höchstens 3 Wörter Abstand) brachte nur +0 bis +1,7 Punkte, auf einer Variante −0,5. Das lohnt sich nicht als eigene Regel.
3. **Kurze Aufnahmen mit vielen Sprechern sind die Schwachstelle.**
   - In 80 s mit 4 Sprechern hat die Standardeinstellung (`v3-offline`) eine Stimme nicht als eigenen Sprecher erkannt.
   - Die volle Aufmerksamkeit (`--offline`) fand alle 4. Sie gilt aber nur bis 6,6 min Audio (NeMo-Speech.cpp-Doku, „Diarize audio“).
   - Folge für den Worker: Aufnahmen bis 6,6 min in einem Durchgang trennen, längere im Streaming-Modus mit großem Puffer **[INFERENCE]**.
4. **Was weder ASR noch Diarisierung retten:** Überlappende Rückmeldungen („Mhm.“, „Okay.“) hat Whisper gar nicht transkribiert. Es gibt also kein Wort, dem ein Sprecher zugeordnet werden könnte. Für lesbare Protokolle ist das hinnehmbar, für Gesprächsanalyse nicht.
5. **Aussagekraft:** Das Testaudio ist synthetisch. `say`-Stimmen sind gleichmäßiger als echte Sprecher, also ist die Messung eher zu günstig. Raumhall, Telefon und Dialekt fehlen. Die Zahlen belegen Geschwindigkeit und das Wort-vor-Segment-Ergebnis. Die deutsche Qualität belegen sie nicht. Dafür ist Ticket R3 da.

## 3. Entscheidungen und Begründung

### 3.1 Wo die Trennung läuft: im Resona-Worker

| Ort | Für | Gegen |
|---|---|---|
| **Resona-Worker (gewählt)** | Datenschutz (private Aufträge bleiben auf dem freigegebenen Worker); Wortzeiten sind dort vorhanden; Gewichte gepinnt und geprüft wie bei den ASR-Modellen (`prepare`, `models.json`); eine Umsetzung für PWA, CLI, MCP und Forge | Vertragsänderung über Hub, Worker und Client |
| Forge-Server mit Cloud-Anbieter | Am kleinsten | Audio geht zum Anbieter, ungeeignet für medizinische Daten; 25-MB-Grenze in Forge |
| Browser (sherpa-onnx WASM) | Kein Serveraufwand | In der Messung 1,25-fache Echtzeit schon nativ auf der CPU; Ergebnis muss trotzdem mit dem Server-Transkript zusammengeführt werden |

Die Zusammenführung von Wörtern und Sprecherwechseln gehört direkt neben den Diarisierer. Der Editor bekommt fertige Sprecherwechsel.

### 3.2 Welches Modell: Nemotron-3-Diarization

| Kriterium | Nemotron-3-Diarization | pyannote `community-1` |
|---|---|---|
| Lizenz | OpenMDW-1.1, „ready for commercial or non-commercial use“ ([Modellkarte](https://huggingface.co/nvidia/Nemotron-3-Diarization)) | Gewichte CC-BY-4.0 (Namensnennung im Produkt), Code MIT |
| Download | Frei, ohne Konto (geprüft: `gated=False`) | **Freischaltpflichtig** (geprüft: `gated=auto`, 403 ohne Zustimmung). Jeder Worker-Host bräuchte bei `prepare` ein HF-Token mit akzeptierten Bedingungen |
| Geschwindigkeit Mac | Gemessen 85–150-fache Echtzeit (MPS/Metal) | Keine veröffentlichten Zahlen für CPU/MPS; nicht gemessen |
| Sprecherzahl | Höchstens 8 | Unbegrenzt, Vorgaben `num/min/max_speakers` |
| Live | Gleiches Modell kann streamen (ab 0,32 s Puffer) | Nur Stapelverarbeitung |
| Veröffentlichte Fehlerquote | DIHARD III 12,73, CALLHOME 9,10 (NeMo-Protokoll) | DIHARD3 20,2, AMI-IHM 17,0 (pyannote-Protokoll; nicht direkt vergleichbar) |
| Deutsch | Nicht unter den Trainingssprachen genannt | Keine deutsche Messung veröffentlicht |

**Entscheidung:** Nemotron-3 als Standard. Den Ausschlag geben der freie Download, der zum Offline-/Pin-Modell des Workers passt, die gemessene Geschwindigkeit auf Mac und CPU und der Live-Pfad mit demselben Modell. Die Grenze von 8 Sprechern ist für Diktat, Visite und Teambesprechung unkritisch. pyannote kommt nur dann hinzu, wenn die deutsche Messung (R3) Nemotron klar schlechter zeigt oder Aufnahmen mit mehr als 8 Sprechern ein echter Anwendungsfall werden.

DiariZen (beste veröffentlichte Werte) scheidet aus, weil seine Gewichte nur nichtkommerziell nutzbar sind (CC BY-NC 4.0). OpenAIs Diarisierungsmodell wird am 2027-02-26 abgeschaltet (beides Forge-Notiz, Abschnitt 3).

### 3.3 Laufzeit im Worker: transformers, mit einer Abhängigkeitsprüfung vorweg

- **Gewählt: transformers in-process** (`AutoModelForAudioFrameClassification`, seit transformers 5.22.2 enthalten; geprüft in `src/transformers/models/__init__.py` des Tags `v5.22.2`).
  - Passt zu allem, was der Worker schon tut: HF-Revision in `models.json` pinnen, in `prepare` laden, offline ausführen, Gerät über PyTorch prüfen, Provenienz mit `model_revision`.
  - Gemessen auf MPS: 9,5 s für 806 s Audio.
- **Hindernis:** Der Lock hat transformers 5.8.1 (macOS) und **4.57.6 unter Linux**. Linux ist durch `nemo-toolkit` 2.7.3 der Parakeet-Engine gebunden (`uv.lock`, Abhängigkeiten von `nemo-toolkit`). Auf beiden Plattformen muss transformers auf ≥ 5.22 angehoben werden. Unter Linux heißt das: NeMo anheben, oder prüfen, ob NeMos eigenes `SortformerEncLabelModel` in 2.7.3 die Nemotron-3-Datei lädt **[Annahme: nicht geprüft]**. Das ist der erste Schritt von Ticket R2.
- **Ausweichweg:** NeMo-Speech.cpp (Apache-2.0), ein Binärprogramm für Metal, CUDA und CPU mit GGUF-Gewichten aus demselben HF-Repo. In der Messung war es der schnellste Weg. Dagegen spricht: Es gibt kein Python-Paket (PyPI: 404 für `nemo-speech`), das Programm ist Version 0.3.0 und müsste neben den Wheels im Release-Manifest gepinnt und verteilt werden. Nur nehmen, wenn die Abhängigkeitsfrage unter Linux nicht lösbar ist.

### 3.4 Datenvertrag

- `AudioOptions.diarize: bool = False` (`packages/job-core/src/resona_job_core/options.py`). Keine Vorgabe der Sprecherzahl in der ersten Fassung: Nemotron erwartet keine **[Annahme: in der transformers-Schnittstelle nicht gesehen]**. Eine Vorgabe kommt erst mit pyannote.
- `Segment.speaker: int | None = None`, 1-basiert in Reihenfolge des ersten Auftretens. So nummerieren auch Nemotron und die Ausgabe von NeMo-Speech.cpp. Eine Zahl statt eines Namens hält Übersetzung und Umbenennung aus dem Vertrag heraus.
- Die Segmente sind bei `diarize` die **Sprecherabschnitte**: ASR-Segmente werden an jedem Sprecherwechsel geteilt. Wortlisten kommen nicht in den Vertrag. Die Zusammenführung passiert im Worker, und ohne Wortlisten bleibt das Ergebnis klein. Wörter erst aufnehmen, wenn Forge Wort-für-Wort-Hervorhebung baut.
- `StageProvenance.stage` bekommt `"diarize"`, mit Modell, Revision, Gerät und Sekunden.
- `StageKind` in `policy.py` bleibt unverändert. Der Diarisierer ist eine In-Process-Stufe ohne Endpunkt, und `check_policy` prüft nur Stufen mit Endpunkt (`policy.py`, `check_policy`: `if ep is None: continue`). Cloud-Diarisierung wird nicht gebaut.
- **Markdown:** ein Absatz pro Sprecherabschnitt, beginnend mit `**Sprecher N:** ` (bei `language` ≠ `de`: `**Speaker N:** `). Lange Abschnitte werden weiter nach `audio_markdown` umbrochen (Pause, Satzende, Länge). Die Folgeabsätze tragen keine Marke [INFERENCE: so übernimmt Forge sie als Fließtext unter dem Sprecher; prüfen beim Bau].
- **Reihenfolge der Auslieferung:** Wegen `extra="forbid"` lehnt ein alter Hub Ergebnisse mit `speaker` ab. Der Hub muss also zuerst ausgeliefert werden.

### 3.5 Wer `diarize` ausführen darf: ein Merkmal im Heartbeat

- Ein Worker ohne vorbereitetes Modell darf keinen `diarize`-Auftrag annehmen. Dafür gibt es schon ein Vorbild: `text_jobs`.
  - Der Worker meldet das Merkmal im Heartbeat und beim Claim (`apps/resona-worker/src/resona_worker/hub.py`).
  - Der Hub speichert es pro Worker (`Worker.text_jobs`, Migration 6, `db/schema.py`).
  - `try_claim` filtert danach (`broker.py`).
- Für die Diarisierung genauso: Feld `diarize` im Heartbeat und Claim, neue Spalte `Worker.diarize` (Migration 7), `try_claim` bietet `diarize`-Aufträge nur solchen Workern an. `queue_reason` ist `no_capable_worker`, solange keiner online ist.
- Der Worker setzt das Merkmal nur, wenn `prepare` den Diarisierer erfolgreich geprüft hat (siehe 3.6). Das Ergebnis steht im Capabilities-Manifest.

### 3.6 Prüfen und Freigeben

- `prepare`: lädt die gepinnte Nemotron-Revision und trennt eine Prüfdatei mit 2 Sprechern (`fixtures/de_two_speakers.wav`, mit `say` erzeugt wie die bestehenden Prüfdateien).
  - Verlangt: 2 Sprecher, Wechsel an der richtigen Stelle (±0,5 s), richtige Revision in der Provenienz.
  - Schlägt das fehl, bleibt das Merkmal aus. Das vorherige Manifest bleibt erhalten wie heute.
- `resona-eval`:
  - Neue Maße: Wortzuordnung (wie in Abschnitt 2) und DER. `pyannote-metrics` ist schon im Linux-Lock, die Maße lassen sich aber auch ohne pyannote-Abhängigkeit berechnen.
  - Neue Korpusdateien: 2–3 deutsche Gespräche aus `make_corpus.py`.
  - Die Gate-Regel gilt wie für WER: Verschlechterung um mehr als 0,02 absolut lässt das Gate durchfallen.
- Ab der Einführung des Merkmals laufen Produktionsaufträge mit `diarize` nur auf Releases, deren Evaluation diese Maße enthält **[INFERENCE: Erweiterung von `releases.may_run`; Detailentscheidung beim Bau]**.

### 3.7 Postprocess und Oberflächen

- **LLM-Politur:** Der Markup-Schutz (`markup_signature` in `packages/postprocess/src/resona_postprocess/chunking.py`) kennt Überschriften, Tabellen und HTML-Kommentare, aber keine Sprechermarken. Er muss die Folge der `**Sprecher N:**`-Marken einschließen. Sonst kann eine Politur Sprecher zusammenlegen, ohne dass ein Prüfschritt anschlägt.
- **CLI:** `resona transcribe --diarize`.
- **MCP:** `transcribe_audio(diarize=…)`.
- **OpenAI-kompatibler Weg:** `verbose_json` gibt `speaker` ohne Änderung mit aus, weil `audio_routes.py` das ganze Segment ausgibt (Forge-Notiz 2.3). Anfordern lässt sich die Diarisierung dort über ein zusätzliches Formularfeld `diarize=true`.
- **PWA:** zeigt das Markdown wie heute. Eine eigene Sprecheransicht ist nicht Teil dieses Vorhabens.

## 4. Forge

| # | Schritt | Wozu |
|---|---|---|
| F1 | Sprecher durch die Kette reichen: `speaker?: string` an `SpeechSegment`, `ExtractResponse.segments`, `TranscriptionResult`; `timedSegments()` liest `speaker` (Zahl oder Text) und macht daraus „Sprecher N“; `renditionBody()` schreibt ihn | Voraussetzung für jeden Anbieter |
| F2 | Anbieter `resona` in `TranscriptionConfig`: Upload → `POST /v1/jobs` (`Idempotency-Key`, `private: true`, `options.diarize`) → Status abfragen → Ergebnis; liest `segments[].speaker` | Lange Aufnahmen ohne die 60-s-Grenze des synchronen Wegs; medizintauglich. Für diesen Anbieter die 25-MB-Grenze von Forge auf die Hub-Grenze anheben |
| F3 | „Sprecher umbenennen…“ am Sprecherfeld: ändert alle Segmente der umgebenden Lesefassung in einer Transaktion; Auswahl eines vorhandenen Namens führt zusammen. Vorschläge aus `participants` der Aufnahme und der Sprecherliste des Hosts | Diarisierung liefert „Sprecher 1/2“; ohne Umbenennen ist das Ergebnis mühsam |
| F4 | Optional: Deepgram `diarize_model=latest` als Schalter in `TranscriptionConfig` | Für Forge-Installationen ohne Resona und ohne medizinische Daten; nutzt F1 |

Nicht jetzt:

- Hervorheben des gerade abgespielten Segments (unabhängig davon sinnvoll).
- Sprecherregister mit Kennung und Farbe.
- Erneutes Trennen mit Übernahme der Namen.
- Live-Diarisierung im Diktat.

Alles Weitere steht in der Forge-Notiz, Abschnitt 4.3 und 5.

## 5. Risiken

- **Deutsche Qualität ungemessen.** Die eigene Messung ist synthetisch. Vor einer Qualitätsaussage braucht es echte deutsche Aufnahmen mit Einwilligung. Synthetische Korpusdateien sichern nur gegen Rückschritte.
- **transformers-Anhebung unter Linux** kann Parakeet/NeMo brechen (3.3). Ausweichweg: NeMo-Speech.cpp.
- **Kurze Aufnahmen mit vielen Sprechern** werden zusammengelegt (gemessen). Gegenmittel: voller Durchgang bis 6,6 min; die Messung in R3 entscheidet über Voreinstellungen.
- **Überlappungen und Rückmeldungen** gehen verloren (gemessen: Whisper transkribiert sie nicht).
- **Datenschutz:** Sprechertrennung speichert keine Stimmprofile. Wiedererkennung über Aufnahmen hinweg wäre ein eigenes Vorhaben mit biometrischen Daten und ist hier ausdrücklich ausgeschlossen.

## 6. Offene Punkte

- Ab welcher Länge vom vollen Durchgang auf Streaming umschalten: fest 6,6 min oder kürzer? Entscheidet R3.
- Sollen Folgeabsätze eines langen Sprecherabschnitts die Marke wiederholen? Für Forge-Import und LLM-Politur ist eine Marke pro Absatz robuster, für das Lesen störender.
- Soll die Diarisierung pro Organisation Standard sein (Einstellung), oder immer pro Auftrag angefordert werden? Vorschlag: pro Auftrag, Standard aus.
- Die Begriffe **Sprecher**, **Sprecherabschnitt** und **Diarisierung** in `CONTEXT.md` aufnehmen, sobald gebaut.

## Ticket breakdown

| # | Ticket | Repo | Größe | Blockiert durch |
|---|---|---|---|---|
| R1 | Vertrag: `AudioOptions.diarize`, `Segment.speaker`, Provenienzstufe `diarize`; Hub nimmt die Option an und gibt `speaker` in `verbose_json` und Markdown-Abschnitten weiter; Client-Typen. Hub zuerst ausliefern | resona | M | – |
| R2 | Worker-Stufe: transformers ≥ 5.22 auflösen (Linux/NeMo, sonst NeMo-Speech.cpp); Nemotron-Revision in `models.json`; `prepare` mit 2-Sprecher-Prüfdatei; Wortzeiten behalten, Wortzuordnung, Sprecherabschnitte, Markdown; Heartbeat-Merkmal `diarize`, Migration 7, `try_claim`-Filter | resona | L | R1 |
| R3 | Evaluation: Wortzuordnung + DER in `resona-eval`, deutsche Mehrsprecher-Korpusdateien, Gate-Regel; danach Entscheidung Nemotron-Voreinstellung bzw. pyannote | resona | M | R1 |
| R4 | Oberflächen: `resona transcribe --diarize`, MCP-Parameter, Formularfeld im OpenAI-Weg, Sprechermarken im `markup_signature` | resona | S | R2 |
| F1 | Sprecher durch die Forge-Transkriptionskette | forge-mono | S | – |
| F2 | Forge-Anbieter `resona` über die Job-API | forge-mono | M | F1, R2 |
| F3 | Sprecher umbenennen/zusammenführen | forge-mono | S–M | – |
| F4 | Optional: Deepgram-Diarisierung | forge-mono | S | F1 |
