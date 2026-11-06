# Testbench

The testbench compares transcription and scan engines on **your own** recordings and documents. You
contribute a file, correct the first result into a reference, run the item through several
candidates and compare accuracy, time and device per candidate.

It is a hub feature, **off by default**, and only an organization admin can read what it stores.

## Flow

1. **Contribute.** Any `client` or `org_admin` credential adds a recording (audio) or scan
   (PDF/PNG/JPEG/TIFF): `resona testbench add memo.m4a`. The hub stores the original.
2. **Draft.** The hub immediately queues a *draft run*: one job on the first available candidate for
   the file's kind. Its output is the first transcription or scan.
3. **Correct.** An org admin opens the item in the [admin UI](#admin-ui), listens to or views the
   original, fixes the draft text and saves it as the **reference** (ground truth). The item is now
   `corrected`.
4. **Bench runs.** The admin picks candidates (engine, model, accelerator) and runs the item through
   them. Each finished run is scored against the reference.
5. **Compare.** The UI shows per-run metrics, a word-level diff against the reference, timing and
   device, and a leaderboard across all corrected items.

Item status: `awaiting_draft` (draft run not completed) → `needs_correction` (draft done, no
reference) → `corrected`.

Correcting references, starting bench runs and the leaderboard are **admin UI only**; the CLI
contributes and lists.

## Enabling it

The testbench is controlled by the organization setting `testbench_enabled` (default `false`). While
it is off, every `/v1/testbench/*` route except `/status` answers `403 testbench_disabled`. An org
admin turns it on with the **Enable testbench** button on the admin UI Testbench tab, or from the CLI by editing the settings:

```bash
resona settings get | jq '.settings.testbench_enabled = true | .settings' > settings.json
resona settings set --file settings.json
resona testbench status
```

## CLI

```bash
resona testbench add memo.m4a scans/ --language de --label "Arzt-Brief"   # contribute files / directories
resona testbench list --status needs_correction                           # org admin
resona testbench status                                                   # enabled, item/corrected/run counts
```

`add` prints `<item id> <status> <kind> <file>` per file (or `--json`). `list` accepts `--kind
audio|document`, `--status`, `--limit`, `--cursor`, `--json`. The Python equivalents are the
`testbench_*` methods of [`ResonaClient`](../reference/client.md); the HTTP routes are in the
[API reference](../reference/api.md#testbench).

## Admin UI

The hub's admin UI at `/admin` has a **Testbench** tab (org admin token): item list, original
player/viewer, reference editor, candidate picker, run table with word diff, and the leaderboard.

## Candidates

Bench runs can only use candidates that at least one **approved, unrevoked, private** worker of your
organization has both been approved for and reported as *tested* in its capabilities manifest (the
same tuples `GET /v1/engines` lists). Each candidate shows how many such workers are online. A run
needs one of those workers to be online to start; otherwise it stays queued.

Testbench jobs need **no promoted release tuple**: they are not production jobs, so the
evaluation/promotion gate does not apply. The worker must still be approved, match the job's privacy
class and have the candidate in its approved and tested set, and the execution policy is checked as
for any job. Cloud candidates are out of scope: testbench jobs are always private.

## Metrics

Runs are scored against the reference with `resona-eval`:

| Item kind | Scored text | Normalization | Metrics |
|---|---|---|---|
| audio | `raw_text` | `text-v1` | WER, CER |
| document | `markdown` | `markdown-v1` | WER, CER |

When the reference changes, all completed runs of the item are re-scored. The word diff aligns the
normalized words (`equal`, `substitute`, `insert`, `delete`). See
[Evaluation & Baselines](evaluation.md) for the normalizations.

!!! warning "Draft bias"
    The reference is typically a correction of the draft run's output, so the draft run is biased in
    its own favour. Draft runs are shown on the item but **excluded from the leaderboard**, which
    averages only completed bench runs on corrected items per candidate.

## Privacy and retention

- Originals are **persisted until deleted**, unlike normal jobs (24 h results). They live under
  `<DATA_PATH>/organizations/<storage_id>/testbench/` with run results; the retention and orphan
  sweeps never remove them. Back up and size that volume accordingly.
- Contributing needs consent to store the file: audio and scans may contain personal or patient
  data. Only contribute material you may keep. Nothing is sent to cloud services.
- Only **org admins** can read items, originals, references and results. Clients can only contribute
  and see the status counters. The hub operator has no access.
- Deleting an item removes its row, original, runs and result files, and cancels its active jobs.
