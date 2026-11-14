# Speaker-diarization benchmark (private dataset)

Measures Resona's diarization (`options.diarize`: ASR + Nemotron-3-Diarization on the
worker path) on real podcast conversations. The committed corpus fixtures
(`de-two-speakers`, `de-case-conference`) are synthetic; this benchmark adds 2-6 speaker
conversations in German and English.

**No data lives in this repository.** Podcast audio and transcripts are copyrighted: the
dataset is built into a directory outside the repo (`--dataset` or `RESONA_DIAR_DATASET`,
e.g. `~/devBench/resona-diar-dataset/`). `sources.json` names only public episode ids and
transcript URLs.

## Labels

- **gold**: speaker names from transcripts the publisher wrote or edited (The Changelog,
  Signals and Threads, Logbuch:Netzpolitik, Lage der Nation). Times come from aligning
  those words to Deepgram's word times or to
  whisper-large-v3-turbo word times on the clip, so turn boundaries carry alignment error
  (tens of ms); speaker identity is human.
- **silver**: Deepgram nova-3 diarization of the other interview episodes, and Oxide and
  Friends' published transcripts (Transistor machine diarization, curation unknown). These
  are model output, not ground truth: a disagreement can be either model's error.
- **platinum**: the interview data dir's corpus v4 (`data/published.yaml`, turns parsed
  into `diarized/<id>.json`): transcripts the show publishes itself (feed, website, PDF)
  with speaker names, one episode per show (16 shows not in gold, plus new episodes of
  Logbuch:Netzpolitik and Lage der Nation). Most have no timestamps;
  their words are aligned to whisper-large-v3-turbo over the whole episode
  (`timing: whisper-episode`). Curation is unknown per show: some publishers edit
  machine diarization, so treat it as near-gold.
- **platinum-anon**: corpus v4 shows whose transcripts carry anonymous labels
  (`Speaker 1`, `SPEAKER_00`). These come from the hosting platform's own diarization
  (Podigee, iHeart, Spotify): model output, scored like silver.

`build` freezes each interview episode's turns into `sources/<episode>/labels.turns.json`,
so a later rebuild does not change while the interview data dir does.

Windows where under 80 % of the published words align to timed words are skipped
(Ready for Review was dropped for this reason).

## Commands

```sh
export RESONA_DIAR_DATASET=~/devBench/resona-diar-dataset
uv run python benchmarks/diarization/build_dataset.py fetch
uv run python benchmarks/diarization/build_dataset.py build --interview ~/devBench/interview/data
uv run python benchmarks/diarization/score.py run --run nemotron          # mac-private: mlx-whisper + Nemotron
# diarizer comparison on identical words: cache ASR once (worker options), then diarize in-process
uv run python benchmarks/diarization/score.py asr
uv run python benchmarks/diarization/score.py run --run nemotron-words --diarizer nemotron
uv run --with 'pyannote.audio>=4' --with torch==2.12.0 \
    python benchmarks/diarization/score.py run --run pyannote --diarizer pyannote
uv run python benchmarks/diarization/score.py score --run nemotron --run nemotron-words --run pyannote --run deepgram-nova3
```

`run` uses the explicit local execution path (`resona_cli.local_run`, the worker's
execution subprocess) with the execution profile `--profile` (default `mac-private`); the
diarizer must be prepared (`resona-worker prepare` with the `diarize` extra).
`run --diarizer` reuses the worker's `diarized_transcript` over the words cached by `asr`.
pyannote (`pyannote/speaker-diarization-community-1`) is gated on Hugging Face: accept its
licence with the account of your local token first.
`score` reuses `resona_eval.scoring.score_fixture` (speaker attribution, DER, speaker count
error, WER) and aggregates by label source, language, speaker count and window kind;
it also lists merged and split reference speakers per clip.
