# Studie: Forge-Editor in die Resona PWA integrieren (Diktat an beliebiger Stelle)

Status: Idee, nicht geplant. Stand: 2026-11-08.

**Frage:** Lässt sich der Editor des Projekts skaile-forge als einfacher Editor in die Resona PWA einbauen, damit Diktieren und Textbearbeitung besser werden, insbesondere: an beliebiger Stelle im Text per Tastendruck, Mausklick oder Telefonfunktion hineindiktieren, auch bei einfachen Editoren mit Sprachunterstützung?

**Kurzfassung:**

- **Der Forge-Editor kann das heute nicht.** Weder die Bibliothek `forge-editor` noch die Host-App `skaile-forge` diktieren an die Stelle, an der das Diktat begann (Belege in Abschnitt 1).
- Die Bibliothek hat aber genau den Baustein, der dafür fehlt: eine änderungsfeste Bereichsmarke (`captureRange`/`resolveRange`, Abschnitt 1.3).
- Dafür ist ein **Ticket im Forge-Repo angelegt**: [skaile-ai/forge-editor#150](https://github.com/skaile-ai/forge-editor/issues/150).
- Die Resona PWA hat bereits einen TipTap-Editor und Push-to-Talk mit Platzhalter, aber **ohne** Positionsverfolgung (Abschnitt 2). Der kleinste Weg zu „überall hineindiktieren" ist, dieses Verfahren in der PWA nachzubauen. Das Einbetten des kompletten Forge-Editors lohnt erst, wenn auch dessen übrige Funktionen gewünscht sind.

Markierung: **[Annahme]** = nicht geprüft; **[INFERENCE]** = aus gelesenem Code geschlossen, nicht ausgeführt.

---

## 1. Kann der Forge-Editor schon „hineindiktieren"? Befund

### 1.1 Wo der „Forge-Editor" liegt

- Bibliothek: Repo `skaile-ai/forge-editor` (lokal `~/devBench/SKAILEdev/forge/forge-editor`, privat, Issues aktiv). Paket `@skaile/forge-editor` 7.1.0, Vue **und** React, auf TipTap 3 (`packages/editor/package.json`; Peer-Abhängigkeit `@tiptap/core ^3.31.3`).
- Host-App: `skaile-forge` (lokal `~/devBench/SKAILEdev/forge/skaile-forge`), bindet `@skaile/forge-editor` ein (`CLAUDE.md`, Abschnitt Shared modules). Dass die Coolify-Anwendung `skaile-forge` (https://skaile-forge.ecsplico.de) genau dieses Repo ausliefert, habe ich nicht geprüft **[Annahme]**; die Zuordnung ergibt sich aus dem Namen.

### 1.2 Suchergebnis (Quelltext ohne `node_modules`, `.scratch`, `research`, `dist`)

- `rg -i "dictat|SpeechRecognition|webkitSpeech"` in **forge-editor**: **keine Treffer** außerhalb der ausgeschlossenen Ordner.
- Aufnahme existiert, aber nur für Audio-Blöcke: `packages/editor/src/features/media/audio-recorder.ts` (`createAudioRecorder`, `MediaRecorder`, Ergebnis ist eine `File`); eingebunden als Slash-Befehl „aufnahme" mit Berechtigung `ABILITY.record` = `insert.record` (`core/schema/abilities.ts`) und im `AudioRecorderModal` (Vue und React). Es fügt einen Medienblock ein, keinen Text an der Cursorposition.
- Transkription existiert für **Assets**: `packages/editor/src/server/extract.ts` (`transcribe()`, Zeile ~135; OpenAI-kompatibel oder Deepgram, bis 25 MB-Grenze laut Kommentar im Code) über `POST /api/ai/extract`, Methode `transcription`, als Ableitung (Rendition) eines importierten Audios.
- Sprachausgabe (TTS) existiert: `packages/editor/src/adapters/speech/*`, Add-on `editor-audio`.
- In **skaile-forge** (Host-App) gibt es eine einfache Diktierfunktion: `app/editor/audio/feature.ts` (`forgeAudioFeature`, Befehl `dictate`, Werkzeugleiste) mit `app/composables/useAudio.ts` und `server/api/transcribe.post.ts`:
  - Ablauf: Klick → `getUserMedia` + `MediaRecorder` → zweiter Klick beendet → `POST /api/transcribe` → `editor.chain().focus().insertContent(transcript).run()`.
  - **Eingefügt wird dort, wo die Auswahl bei Eintreffen des Transkripts steht**, nicht dort, wo das Diktat begann. Keine Marke, kein Platzhalter, keine Positionsverfolgung.
  - Ein `MediaRecorder` pro Seite; während `isTranscribing` ist der Befehl gesperrt (`can: () => !audio.isTranscribing.value`), also kein zweites Diktat parallel.
  - Kein Tastenkürzel, keine Auswahlleiste, kein Bedienelement für Touchgeräte außer dem Werkzeugleisten-Knopf.
  - Die Server-Route nimmt rohe Audiodaten oder `multipart` mit `audio` und `language` und antwortet `{ transcript }`; STT-Anbieter kommt aus den Einstellungen (`kind: stt`).

**Ergebnis:** Teilweise vorhanden (Diktat an der Cursorposition **zum Zeitpunkt des Einfügens**), die gewünschte Funktion (Diktat an beliebiger, über Änderungen hinweg verfolgter Stelle; Auslösung per Tastatur, Maus und Telefon) **nicht**. Auch keine bestehenden Issues dazu: Suche in `skaile-ai/forge-editor` nach `dictat OR diktier OR speech OR voice OR Spracheingabe` lieferte nur die geschlossenen Tickets #56/#57/#70/#66 (Sprachausgabe/Audio-Add-on, Kitchen-Sink).

### 1.3 Was die Bibliothek schon mitbringt (Grundlage für das Ticket)

- `packages/editor/src/core/doc-anchors.ts`: Erweiterung `DocAnchors`, Funktionen `captureRange(editor, {from,to}, {startInside})`, `resolveRange(state, anchor)`, `releaseRanges(editor, anchors)`. Bereiche wandern durch lokale Transaktionen (`tr.mapping`) und werden mit einem `AnchorResolver` (Yjs, `features/collab/anchor-resolver.ts`) auch über Fremdänderungen neu aufgelöst. Gedacht für KI-Abläufe (Auswahlmenü, Reviews).
- Grenzverhalten dort: Text, der genau an der Grenze eingefügt wird, bleibt außerhalb; `startInside` bindet den Anfang an das erste eigene Zeichen. Genau das braucht eine Diktiermarke.
- Rahmen für Funktionen: `EditorFeature` (`core/feature.ts`) mit Befehlen, Werkzeugleiste, Auswahlleiste (`selectionActions`), Berechtigungen (`ABILITY`); Vue- und React-Bindungen pro Funktion.

---

## 2. Was die Resona PWA heute hat

- `apps/resona-pwa/app/components/TranscriptEditor.vue`: TipTap 3 (`@tiptap/vue-3 ^3.27.1`, StarterKit, Placeholder); Text ist **einfacher Text** (Directus-Feld `text`, ein `\n` = ein Absatz). Methoden: `insertText`, `insertPlaceholder`, `replaceBetween`, `appendParagraph`, `focus`.
- Push-to-Talk in `app/pages/recording/[id].vue` (Abschnitt „Push-to-talk dictation"): Taste links-Strg gehalten oder Knopf; beim Start wird `insertPlaceholder()` aufgerufen, das **die Zeichen „[…]" in den Text schreibt** und die Rohpositionen `{from, to}` zurückgibt; nach dem Hub-Aufruf ersetzt `replaceBetween(from, to, text)` diese Rohpositionen. Es ist immer nur ein Diktat gleichzeitig möglich (`pttState` muss `idle` sein).
- **Schwäche (aus dem Code gelesen, nicht ausgeführt) [INFERENCE]:** Die gespeicherten Positionen werden bei Änderungen des Textes zwischen Start und Antwort nicht mitgeführt. Schreibt der Nutzer davor etwas, trifft `replaceBetween` die falsche Stelle; der Platzhalter „[…]" ist echter Text und könnte (bei Auto-Speichern während des Wartens) im gespeicherten Transkript landen.
- Das Konzept existiert bereits im CLI: `apps/resona-cli/src/resona_cli/dictate_anchors.py` (`AnchorTracker`, `adjust_location`: Anfang rechts-haftend, Ende links-haftend) und `dictate.py` (`DictationTextArea` meldet jede Änderung, unterscheidet Nutzeraktion von eigener Buchführung über `internal_edit()`; `Segment` mit Status `recording|queued|transcribing|inserted|error`, `disturbed`-Flag; `TranscriptionWorker` serialisiert Modellzugriffe). Die PWA hat nichts davon.
- Mobil: `useRecorder` (MediaRecorder), Hub-Sync-Aufruf über `useHub()`; Live-Pfad über `/v1/listen` und Gerätesprache (`useDeviceSpeech`). Auf dem Telefon gibt es keine Tastatur-Taste „Strg", der Knopf ist das einzige Bedienelement.

---

## 3. Integrationsoptionen

| Option | Inhalt | Aufwand | Passt, wenn … |
|---|---|---|---|
| **A. Paket einbetten** (`@skaile/forge-editor` Vue) | Editor, Werkzeugleiste, Markdown, Kommentare, KI-Review, Block-UI | L | die PWA mehr als Diktieren will (Kommentare, KI-Bearbeitung, strukturierte Dokumente) |
| **B. iframe** der Host-App `skaile-forge` | Fertiger Editor mit Login der Host-App | M, aber schlechte Passung | nur als Notlösung |
| **C. Mechanismus nachbauen** in `TranscriptEditor.vue` | Eigene kleine ProseMirror-Erweiterung für verfolgte Bereiche, Diktier-Zustandsmaschine, Bedienelemente | S–M | die Frage „überall hineindiktieren" im Vordergrund steht |

### A. Paket einbetten

- Vorteile: Ein Editor für beide Welten; Anker, Kollaboration, Auswahlleiste, Markdown und KI vorhanden; die geplante Diktierfunktion (Ticket #150) kommt dort generisch an.
- Hindernisse:
  - Die Vue-Oberfläche ist auf **Nuxt UI** ausgelegt (`./vue/kit/nuxt-ui`; Einstellung über `--ui-*`-Variablen, Tailwind 4 plus Icon-Sammlung `@iconify-json/lucide`, README des Pakets). Die PWA nutzt das eigene **Hearth**-Design (reines Vue + Tailwind-Tokens, ohne Komponentenbibliothek, `apps/resona-pwa/CLAUDE.md`). Ob sich die neutrale `./vue/kit`-Ebene ohne Nuxt UI sinnvoll stylen lässt, ist offen und im Spike zu klären **[Annahme: nicht geprüft]**.
  - TipTap-Versionen: Bibliothek verlangt `^3.31.3`, PWA hat `^3.27.1` (Anhebung nötig, geringes Risiko).
  - Datenmodell: Die PWA speichert **reinen Text**. Forge arbeitet mit Dokumenten (Markdown/Yjs/Blöcke). Entweder Forge im Einfach-Modus (`./vue/simple`) mit Text↔Absatz-Abbildung wie heute, oder Datenmodell der Transkripte ändern (Directus-Feld, Export, Live- und Batch-Schreiber). Das Zweite ist ein großer Eingriff.
  - Veröffentlichung: Das Paket hat `publishConfig.access: public`; ob es auf npm liegt und aus dem PWA-Docker-Build erreichbar ist, habe ich nicht geprüft **[Annahme]**. Das Repo selbst ist privat.
  - Umfang: Das Paket ist groß (viele Einstiegspunkte, Add-ons). Tree-Shaking auf `./core` + `./vue/simple` + Diktierfunktion ist Pflicht, sonst wächst das PWA-Bundle spürbar.
- Offline: Die PWA ist installierbar und arbeitet offline in der Warteschlange; KI-/Kollaborationsfunktionen des Forge-Editors setzen einen Server voraus und müssten abgeschaltet werden.

### B. iframe

Nachteile: anderes Origin (Login/Keycloak-SSO, Cookies, CORS, Content-Security-Policy), mobile Tastatur- und Fokusprobleme in iframes, Daten müssten über `postMessage` hin und her (Text, Cursor, Diktat-Ereignisse), Offline unmöglich, zwei Deployments gekoppelt. Nicht empfohlen.

### C. Nachbau in der PWA

Inhalt (alles in der PWA, `TranscriptEditor.vue` bleibt die einzige Editor-Stelle):

1. Kleine TipTap-Erweiterung `DictationAnchors`: Plugin-Zustand mit Karten `id → {from, to}`, Fortschreibung über `tr.mapping.map(pos, assoc)` (Anfang `assoc = 1`, Ende `assoc = -1`, wie `AnchorTracker` und `doc-anchors.ts`), ohne `addToHistory`. Ohne Kollaboration braucht man keinen `AnchorResolver`.
2. Platzhalter als **Decoration** (Widget am Anker), nicht als Text. Damit kann „[…]" nie im Speicherwert landen.
3. Diktier-Zustandsmaschine (Composable, z. B. `useDictation`) mit mehreren gleichzeitigen Segmenten (nur eines nimmt auf), Status `recording|transcribing|inserted|error|cancelled`, Wiederholen bei Fehler.
4. Bedienelemente: siehe Abschnitt 4.

Ein Teil der betroffenen Dateien (`recording/[id].vue`, `TranscriptEditor.vue`, `useTranscriptEdit.ts`) wird gerade von anderer Seite bearbeitet; die Umsetzung muss danach oder in Abstimmung geschehen.

---

## 4. Bedienung und Mobilzugang

Gewünscht: per Tastendruck, Maus oder Telefonfunktion an beliebiger Stelle hineindiktieren.

- **Tastatur (Desktop):** wählbares Tastenkürzel zum Umschalten; zusätzlich Halten-zum-Sprechen (heute links-Strg in der PWA, `ControlLeft`; auf macOS erreicht die Fn-/Globus-Taste den Browser nicht, siehe Kommentar in `recording/[id].vue`). Kürzel dürfen nicht mit Browser-/TipTap-Tastenbelegungen kollidieren.
- **Maus:** Werkzeugleistenknopf; Knopf in der Auswahlleiste bei markiertem Text („durch Diktat ersetzen"); Rechtsklick/Kontextmenü optional.
- **Telefon:**
  - Schwebender Mikrofonknopf, sichtbar bei fokussiertem Editor und grobem Zeigegerät (`(pointer: coarse)`), oberhalb der Bildschirmtastatur positioniert (`visualViewport`). Antippen = Start/Stop.
  - Lang drücken = halten zum Sprechen, Loslassen beendet.
  - Wichtig: Der Knopf darf dem Editor Fokus und Auswahl nicht nehmen (`pointerdown` mit `preventDefault()`), sonst geht die Einfügestelle verloren. Die jetzige PWA verlässt sich darauf, dass `focus()` die letzte Auswahl wiederherstellt (Kommentar in `TranscriptEditor.vue`).
  - Mikrofon-Berechtigung und Verfügbarkeit (`getUserMedia` nur im sicheren Kontext) sichtbar melden, nicht still scheitern.
- **Tastatur-Diktat des Betriebssystems als Rückfall:** Das Mikrofon der Bildschirmtastatur (iOS/Android) schreibt über normale Eingabeereignisse (`beforeinput` mit `inputType: "insertFromDictation"` **[Annahme]**) direkt an die Cursorposition und funktioniert in jedem Editor, auch ohne Resona. Es hat keine Resona-Nachbearbeitung (Profile, Ersetzungen) und keine Provenienz, ist aber der Maßstab: Der Editor darf es nicht stören (keine Eingabe abfangen, die Anker dürfen es nicht als Fremdänderung missdeuten). „Auch bei einfachen Editoren mit Sprachunterstützung" lässt sich damit ohne Eingriff erfüllen; der Mehrwert eigener Diktierfunktion ist Qualität (Hub-Engines) und Nachvollziehbarkeit.

---

## 5. Abbildung Text ↔ Diktiersegmente (Provenienz)

Ziel: Für Korrekturen und Trainingsdaten wissen, welcher Textabschnitt aus welchem Audioclip stammt.

- Pro Segment: `segment_id`, Audioclip (Blob/Datei), Engine/Modell (Hub-Provenienz, siehe `JobResult.provenance`, in der PWA bei Expertenmodus sichtbar), Start-/Endzeit, rohes Transkript, **Bereich beim Einfügen**, Bereich nach späteren Änderungen (über denselben Anker, solange er lebt).
- Drei Speicherformen, aufsteigend nach Aufwand:
  1. **Nur Ereignisse:** Beim Einfügen wird `onSegment({id, range, text, audio, engine, …})` ausgelöst; der Host speichert das in einer eigenen Sammlung (Directus, z. B. `dictation_segments`: `recording`, `audio_file`, `raw_text`, `engine`, `offset`, `length` zum Einfügezeitpunkt). Keine Änderung am Dokumentformat. Das ist auch das Vorgehen im Forge-Ticket.
  2. **Markierung im Dokument** (TipTap-Mark `dictation` mit `segmentId`): bleibt bei Bearbeitung am Text haften, erlaubt später „Diff zwischen rohem und korrigiertem Text je Segment". Preis: Persistenzformat muss Marks aufnehmen; die PWA speichert heute reinen Text, also bräuchte sie ein Zusatzfeld (z. B. `structured`/JSON in `transcripts`, das es als Feld gibt).
  3. **Korrekturspur:** je Segment der letzte Stand des Bereichs vs. `raw_text`; ergibt direkt Paare (Audio, korrigierter Text) für Training. Nur sinnvoll mit 2.
- Datenschutz: Medizinische Audioclips dauerhaft für „Training" zu speichern ist eine eigene Entscheidung (Einwilligung, Aufbewahrung, Löschung); der Hub speichert Ergebnisse nur 24 h (`CLAUDE.md`, Job-Ablauf). Die Studie nimmt keine Speicherung an; sie macht sie nur technisch möglich.

---

## 6. Konflikte bei Änderungen während der Transkription

Szenarien und Soll-Verhalten (jeweils mit Test):

| Fall | Verhalten |
|---|---|
| Nutzer tippt **vor** der Marke | Marke wandert mit; Ergebnis landet an der neuen Position |
| Nutzer tippt **genau an** der Markengrenze | Anfangs-Grenze rechts-haftend, End-Grenze links-haftend: Neu getippter Text bleibt vor bzw. nach dem Ergebnis (CLI-Vorbild: `adjust_location(inclusive=…)`) |
| Absatz um die Marke wird gelöscht | Anker kollabiert; Ergebnis an kollabierter Stelle, bei verlorenem Anker an den Cursor mit Hinweis, nie an Dokumentende/Position 0 |
| Mehrere Diktate nacheinander, Antworten in anderer Reihenfolge | Jedes an eigener Marke; Reihenfolge der Antworten egal |
| Rückgängig (Undo) | Einfügen ein Schritt; Undo anderer Edits darf Marken nicht verschieben (Plugin-Zustand mit `addToHistory: false`; im CLI sperrt man Undo, in TipTap geht Mapping über Transaktionen sauber, **[INFERENCE]**) |
| Datei wird parallel gespeichert/neu geladen (`useTranscriptEdit`, Auto-Speichern) | Vor einem externen `setContent` (die PWA setzt den Editor bei Wertänderung von außen neu, `watch(modelValue)`) gingen alle Anker verloren. Lösung: bei ausstehenden Segmenten Setzen von außen blockieren oder Anker vorher aus- und danach wieder einlesen |
| Kollaboration (nur Forge) | `AnchorResolver` löst Anker nach Yjs-Transaktionen neu auf; vorhanden in der Bibliothek |
| Seite wird verlassen / Offline | Aufnahme kann nicht fortgesetzt werden; Clip lokal halten und Wiederholen anbieten |

Hinweis zum aktuellen PWA-Code: Der Wertefluss `v-model` → `setContent` bei Abweichung (`TranscriptEditor.vue`, `watch`) ist für Anker kritisch (siehe Tabelle).

---

## 7. Aufwand

Größen: S ≈ bis 1 Tag, M ≈ 2–4 Tage, L ≈ 1–2 Wochen.

**Weg C (empfohlen als erster Schritt)**

| Schnitt | Größe |
|---|---|
| `DictationAnchors`-Erweiterung + Tests (Randfälle aus Abschnitt 6) | M |
| Platzhalter als Decoration; `[…]`-Text entfernen | S |
| `useDictation` (mehrere Segmente, Status, Wiederholen), Anbindung an bestehenden Hub-Aufruf | M |
| Bedienelemente: Kürzel, Knopf, Auswahlleiste, schwebender Mobil-Knopf, Lang-Drücken | M |
| `onSegment`-Ereignis + Directus-Sammlung für Provenienz (optional) | S–M |
| Echtgeräte-Test (iOS Safari/Chrome Android, Bildschirmtastatur, Fokus) | M |

Gesamt etwa 1,5–2 Wochen.

**Weg A (Einbetten)**: Spike (Bundle-Größe, Styling ohne Nuxt UI, Text↔Dokument-Abbildung) M; Umsetzung L; zuzüglich Abhängigkeit vom Forge-Ticket #150 (Diktierfunktion in der Bibliothek, Größe nach Ticket L).

---

## 8. Risiken

1. **Fokus und Auswahl auf Mobilgeräten:** Schwebende Knöpfe verlieren die Auswahl leicht (iOS Safari besonders) **[Annahme]**. Früh auf echten Geräten prüfen.
2. **Mikrofon im Hintergrund:** Mobile Browser stoppen Aufnahmen bei Bildschirmsperre/App-Wechsel **[Annahme]**; die PWA muss das erkennen und den Clip sichern.
3. **Anker gehen bei externem `setContent` verloren** (Abschnitt 6).
4. **Paketkopplung bei Weg A:** Nuxt-UI-Annahmen, Versionssprünge (TipTap), Bundle-Größe, privates Repo/Registry.
5. **Datenmodell:** Wechsel von Text auf Dokument rührt Export, Live-Pfad, Batch-Schreiber und Worker an.
6. **Doppelte Arbeit:** Baut die PWA Weg C und Forge das Ticket #150 getrennt, entstehen zwei Implementierungen derselben Idee. Gegenmittel: dieselbe Semantik (Grenzverhalten, Status, Ereignis `onSegment`), damit ein späterer Wechsel einfach bleibt.
7. **Datenschutz** der Provenienz-Audios (Abschnitt 5).

---

## 9. Empfehlung

1. **Forge-Ticket #150 stehen lassen** und dort priorisieren, was Forge selbst braucht. Es beschreibt Auslöser (Kürzel, Werkzeugleiste, mobiler Knopf), Einfügen an Cursor/Auswahl, Anker unter Änderungen (inklusive Kollaboration), austauschbare Transkriptions-Schnittstelle, Touch-Betrieb und Tests.
2. **Für die PWA zuerst Weg C:** Anker-Erweiterung und Decoration-Platzhalter in `TranscriptEditor.vue`, mehrere Diktate, Bedienung inklusive mobilem Knopf. Das behebt die Positions- und Platzhalter-Schwäche des bestehenden Push-to-Talk und liefert „überall hineindiktieren" ohne neue Abhängigkeit.
3. **Weg A erst entscheiden**, wenn Kommentare, KI-Bearbeitung oder strukturierte Dokumente in der PWA gewünscht sind; vorher einen kurzen Spike (Styling, Bundle, Datenmodell). Dann die Diktierfunktion aus der Bibliothek verwenden und den PWA-Nachbau entfernen.
4. **Weg B (iframe) nicht verfolgen.**
5. Betriebssystem-Diktat der Tastatur als gleichwertigen Rückfall weiterhin zulassen und in Tests absichern.

---

## Anhang: Forge-Ticket

Angelegt in `skaile-ai/forge-editor` (privates Repo, Konventionen aus `docs/agents/issue-tracker.md`: Titel `<KÜRZEL>: i-<kürzel>-<slug> - …`, Labels `enhancement`, `needs-triage`):

- [#150 DIK: i-dik-dictate-at-position - Dictate into the document at any caret/selection, anchored under concurrent edits](https://github.com/skaile-ai/forge-editor/issues/150)

Der Text enthält keine Hostnamen, Zugangsdaten oder Patientendaten.
