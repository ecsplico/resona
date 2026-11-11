"""Run and score Resona speaker diarization on the private dataset built by ``build_dataset.py``.

    uv run python benchmarks/diarization/score.py run --dataset ~/devBench/resona-diar-dataset --run nemotron
    uv run python benchmarks/diarization/score.py asr --dataset ~/devBench/resona-diar-dataset
    uv run --with 'pyannote.audio>=4' --with torch==2.12.0 python benchmarks/diarization/score.py run \
        --dataset ~/devBench/resona-diar-dataset --run pyannote --diarizer pyannote
    uv run python benchmarks/diarization/score.py score --dataset ~/devBench/resona-diar-dataset \
        --run nemotron --run pyannote --run deepgram-nova3

``run`` without ``--diarizer`` sends every clip of ``<dataset>/corpus`` (a resona-eval corpus,
hashes verified by ``resona_eval.load_corpus``) through the explicit local path, the same
execution subprocess a worker runs (``resona_cli.local_run``: execution profile ``--profile``,
default ``mac-private``, ASR + diarizer, ``options.diarize``). Results land in
``<dataset>/runs/<run>/<clip>.result.json``; clips with a result are skipped unless ``--force``.

``asr`` transcribes every clip once with the profile's first audio candidate and word
timestamps (as diarized jobs do) and caches the engine output in ``<dataset>/asr/``.
``run --diarizer nemotron|pyannote`` then diarizes each clip in-process and assigns speakers
to those cached words with the worker's own ``diarized_transcript``, so diarizers are
compared on identical words. ``pyannote`` = ``pyannote/speaker-diarization-community-1``
(gated on Hugging Face; ``pyannote.audio`` is not a workspace dependency, add it with
``uv run --with``).

``score`` scores each run with ``resona_eval.scoring.score_fixture`` (WER, speaker attribution,
DER, speaker count error: the corpus metrics, nothing re-implemented) and aggregates them by
label source (gold|silver), language, reference speaker count and window kind. It also
classifies speaker errors per clip from the time overlap of reference and hypothesis
speakers (``merged``: two reference speakers mostly under one hypothesis speaker; ``split``:
one reference speaker spread over several hypothesis speakers). Output:
``<dataset>/runs/<run>/scores.json`` and a Markdown summary on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

from resona_eval import load_corpus
from resona_eval.corpus import Fixture
from resona_eval.metrics import normalize
from resona_eval.scoring import score_fixture
from resona_job_core import JobResult

MERGE_SHARE = 0.5  # a reference speaker is "under" the hypothesis speaker holding this share of its speech
SPLIT_SHARE = 0.2  # a second hypothesis speaker with this share of a reference speaker's speech = split


def _dataset(value: str | None) -> Path:
    if not value:
        raise SystemExit("--dataset or RESONA_DIAR_DATASET is required")
    return Path(value).expanduser().resolve()


def run(dataset: Path, name: str, profile: str, only: set[str] | None, force: bool) -> None:
    import dataclasses

    from resona_cli.local_run import load_local_settings, plan_local, run_local

    corpus = load_corpus(dataset / "corpus")
    out = dataset / "runs" / name
    out.mkdir(parents=True, exist_ok=True)
    timing_path = out / "timing.json"
    timing = json.loads(timing_path.read_text()) if timing_path.exists() else {}
    local = load_local_settings()
    for fixture in corpus.fixtures:
        if only and fixture.id not in only:
            continue
        target = out / f"{fixture.id}.result.json"
        if target.exists() and not force:
            continue
        plan = plan_local(local, kind="audio", execution_profile=profile, engine=None, model=None,
                          language=fixture.language, options=fixture.audio_options().model_dump(),
                          private=True, profile=None)
        plan = dataclasses.replace(plan, pipeline=None, postprocess_profile=None)
        started = time.perf_counter()
        try:
            result = run_local(plan, corpus.path(fixture))
        except Exception as exc:  # noqa: BLE001 - one failing clip (e.g. OOM on a full episode) is a finding
            print(f"{fixture.id}: FAILED {exc}", file=sys.stderr)
            timing[fixture.id] = {"error": str(exc), "wall_seconds": round(time.perf_counter() - started, 1)}
            timing_path.write_text(json.dumps(timing, indent=1))
            continue
        wall = time.perf_counter() - started
        target.write_text(result.model_dump_json())
        stages = {s.stage: round(s.seconds, 2) for s in result.provenance.stages}
        timing[fixture.id] = {"wall_seconds": round(wall, 1), "stages": stages}
        timing_path.write_text(json.dumps(timing, indent=1))
        print(f"{fixture.id}: {wall:.0f} s {stages}", file=sys.stderr)


def _audio_candidate(profile: str):
    from resona_cli.local_run import load_local_settings

    settings = load_local_settings().settings
    return settings.execution_profiles[profile].audio[0]


def asr(dataset: Path, profile: str, only: set[str] | None, force: bool) -> None:
    """Cache each clip's ASR output (segments with word timestamps)."""
    from resona_asr_core.audio import SAMPLE_RATE, load_audio_path
    from resona_asr_core.model_cache import configure_model_cache
    from resona_worker.execute import _DEVICE, _load_engine, _resolve_weights

    corpus = load_corpus(dataset / "corpus")
    out = dataset / "asr"
    out.mkdir(exist_ok=True)
    candidate = _audio_candidate(profile)
    configure_model_cache()
    model_arg, revision = _resolve_weights(candidate)
    transcriber = _load_engine(candidate.engine)(device=_DEVICE[candidate.accelerator], modelname=model_arg)
    for fixture in corpus.fixtures:
        target = out / f"{fixture.id}.json"
        if (only and fixture.id not in only) or (target.exists() and not force):
            continue
        audio = load_audio_path(corpus.path(fixture))
        started = time.perf_counter()
        result = transcriber.transcribe(audio, language=fixture.language, task="transcribe",
                                        word_timestamps=True, vad_filter=fixture.audio_options().vad_filter)
        segments = [{"start": float(_get(s, "start")), "end": float(_get(s, "end")), "text": str(_get(s, "text")),
                     "words": [{"start": _get(w, "start"), "end": _get(w, "end"), "word": _get(w, "word")}
                               for w in _get(s, "words") or []]}
                    for s in result.get("segments", []) or []]
        target.write_text(json.dumps({
            "engine": candidate.engine, "model": candidate.model, "revision": revision,
            "seconds": round(time.perf_counter() - started, 2), "duration": len(audio) / SAMPLE_RATE,
            "text": str(result.get("text", "")).strip(), "language": result.get("language") or fixture.language,
            "segments": segments}, ensure_ascii=False))
        print(f"{fixture.id}: asr {time.perf_counter() - started:.0f} s", file=sys.stderr)


def _get(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


class _Pyannote:
    MODEL = "pyannote/speaker-diarization-community-1"

    def __init__(self, device: str) -> None:
        import torch
        from pyannote.audio import Pipeline

        self._torch = torch
        self.pipeline = Pipeline.from_pretrained(self.MODEL).to(torch.device(device))

    def __call__(self, audio):
        from resona_diarize import Turn, renumber

        waveform = self._torch.from_numpy(audio).float().unsqueeze(0)
        output = self.pipeline({"waveform": waveform, "sample_rate": 16000})
        annotation = getattr(output, "speaker_diarization", output)
        labels: dict[str, int] = {}
        return renumber(Turn(float(seg.start), float(seg.end), labels.setdefault(label, len(labels)))
                        for seg, _, label in annotation.itertracks(yield_label=True) if seg.end > seg.start)


def run_diarizer(dataset: Path, name: str, diarizer: str, profile: str, only: set[str] | None, force: bool) -> None:
    """Diarize each clip in-process over the cached ASR words of ``asr``."""
    from resona_asr_core.audio import load_audio_path
    from resona_job_core import Provenance, StageProvenance
    from resona_worker.execute import _DEVICE, _diarizer_weights, diarized_transcript

    corpus = load_corpus(dataset / "corpus")
    out = dataset / "runs" / name
    out.mkdir(parents=True, exist_ok=True)
    timing_path = out / "timing.json"
    timing = json.loads(timing_path.read_text()) if timing_path.exists() else {}
    device = _DEVICE[_audio_candidate(profile).accelerator]
    if diarizer == "nemotron":
        from resona_diarize.model import Diarizer

        engine, model, weights = _diarizer_weights()
        model_fn, revision = Diarizer(str(weights), device), weights.name
    else:
        model_fn, engine, model, revision = _Pyannote(device), "pyannote", _Pyannote.MODEL, None
    for fixture in corpus.fixtures:
        target = out / f"{fixture.id}.result.json"
        cached = dataset / "asr" / f"{fixture.id}.json"
        if (only and fixture.id not in only) or (target.exists() and not force):
            continue
        if not cached.exists():
            raise SystemExit(f"{fixture.id}: no cached ASR; run `score.py asr` first")
        words = json.loads(cached.read_text())
        audio = load_audio_path(corpus.path(fixture))
        started = time.perf_counter()
        turns = model_fn(audio)
        seconds = time.perf_counter() - started
        diarized = diarized_transcript(words["segments"], words["text"], words["duration"], turns, fixture.language)
        segments, markdown = diarized if diarized is not None else ([], words["text"])
        result = JobResult(kind="audio", raw_text=words["text"], markdown=markdown, language=words["language"],
                           segments=segments, provenance=Provenance(stages=[
                               StageProvenance(stage="asr", engine=words["engine"], model=words["model"],
                                               model_revision=words["revision"], accelerator="metal",
                                               seconds=words["seconds"]),
                               StageProvenance(stage="diarize", engine=engine, model=model, model_revision=revision,
                                               accelerator="metal" if device == "mps" else "cpu", seconds=seconds)]))
        target.write_text(result.model_dump_json())
        timing[fixture.id] = {"wall_seconds": round(seconds, 1), "stages": {"diarize": round(seconds, 2)}}
        timing_path.write_text(json.dumps(timing, indent=1))
        print(f"{fixture.id}: {diarizer} {seconds:.1f} s, {len({t.speaker for t in turns})} speakers", file=sys.stderr)


def speaker_errors(fixture: Fixture, result: JobResult) -> dict:
    """Merged and split reference speakers, from the time overlap of reference turns and
    hypothesis segments (seconds per (reference, hypothesis) speaker pair)."""
    overlap: dict[tuple[str, int], float] = {}
    speech: dict[str, float] = {}
    for t in fixture.speakers:
        speech[t.speaker] = speech.get(t.speaker, 0.0) + t.end - t.start
        for s in result.segments:
            if s.speaker is None:
                continue
            o = min(t.end, s.end) - max(t.start, s.start)
            if o > 0:
                overlap[(t.speaker, s.speaker)] = overlap.get((t.speaker, s.speaker), 0.0) + o
    main: dict[str, int] = {}
    split: list[str] = []
    for ref, total in speech.items():
        shares = sorted(((v / total, hyp) for (r, hyp), v in overlap.items() if r == ref), reverse=True)
        if shares and shares[0][0] >= MERGE_SHARE:
            main[ref] = shares[0][1]
        if len(shares) > 1 and shares[1][0] >= SPLIT_SHARE:
            split.append(ref)
    by_hyp: dict[int, list[str]] = {}
    for ref, hyp in main.items():
        by_hyp.setdefault(hyp, []).append(ref)
    merged = [sorted(refs) for refs in by_hyp.values() if len(refs) > 1]
    return {
        "hyp_speakers": len({s.speaker for s in result.segments if s.speaker is not None}),
        "merged": merged,
        "split": sorted(split),
        "share": {ref: round(v / speech[ref], 3) for ref, v in
                  ((r, max((v for (rr, _), v in overlap.items() if rr == r), default=0.0)) for r in speech)},
    }


def _bucket(n: int) -> str:
    return "4+" if n >= 4 else str(n)


def aggregate(rows: list[dict]) -> dict:
    """Macro means plus word-weighted attribution and speech-weighted DER."""
    if not rows:
        return {}
    words = sum(r["ref_words"] for r in rows)
    speech = sum(r["speech_seconds"] for r in rows)
    m = [r["metrics"] for r in rows]
    return {
        "clips": len(rows),
        "attribution_mean": round(statistics.mean(x["speaker_attribution"] for x in m), 4),
        "attribution_weighted": round(sum(r["metrics"]["speaker_attribution"] * r["ref_words"] for r in rows) / words, 4),
        "attribution_min": round(min(x["speaker_attribution"] for x in m), 4),
        "der_mean": round(statistics.mean(x["der"] for x in m), 4),
        "der_weighted": round(sum(r["metrics"]["der"] * r["speech_seconds"] for r in rows) / speech, 4),
        "der_max": round(max(x["der"] for x in m), 4),
        "count_exact": round(sum(x["speaker_count_error"] == 0 for x in m) / len(m), 3),
        "count_error_mean": round(statistics.mean(x["speaker_count_error"] for x in m), 3),
        "wer_mean": round(statistics.mean(x["wer"] for x in m), 4),
        "merged_clips": sum(bool(r["errors"]["merged"]) for r in rows),
        "split_clips": sum(bool(r["errors"]["split"]) for r in rows),
    }


def score(dataset: Path, name: str) -> dict:
    corpus = load_corpus(dataset / "corpus")
    meta = json.loads((dataset / "corpus" / "clips.json").read_text())
    out = dataset / "runs" / name
    timing = json.loads((out / "timing.json").read_text()) if (out / "timing.json").exists() else {}
    rows = []
    for fixture in corpus.fixtures:
        path = out / f"{fixture.id}.result.json"
        if not path.exists():
            continue
        result = JobResult.model_validate_json(path.read_text())
        info = meta[fixture.id]
        rows.append({
            "id": fixture.id, "episode": info["episode"], "show": info["show"], "language": fixture.language,
            "label_source": info["label_source"], "window": info["window"], "speakers": info["speakers"],
            "seconds": info["seconds"], "speech_seconds": sum(t.end - t.start for t in fixture.speakers),
            "ref_words": len(normalize(fixture.reference).split()),
            "metrics": score_fixture(fixture, result), "errors": speaker_errors(fixture, result),
            "timing": timing.get(fixture.id),
        })
    groups: dict[str, dict] = {"all": aggregate(rows)}
    for key, label in (("label_source", "labels"), ("language", "lang"), ("window", "window")):
        for value in sorted({r[key] for r in rows}):
            groups[f"{label}={value}"] = aggregate([r for r in rows if r[key] == value])
    for source in ("gold", "silver"):
        for bucket in ("2", "3", "4+"):
            sel = [r for r in rows if r["label_source"] == source and _bucket(r["speakers"]) == bucket]
            if sel:
                groups[f"labels={source} speakers={bucket}"] = aggregate(sel)
    report = {"run": name, "corpus_sha256": corpus.sha256, "clips": rows, "groups": groups,
              "failed": {k: v["error"] for k, v in timing.items() if "error" in v}}
    (out / "scores.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


def markdown(report: dict) -> str:
    lines = [f"### Run `{report['run']}`", "",
             "| Group | Clips | Attribution (mean / word-weighted / min) | DER (mean / speech-weighted / max) "
             "| Count exact | Merged clips | Split clips | WER |",
             "|---|---|---|---|---|---|---|---|"]
    for group, a in report["groups"].items():
        if not a:
            continue
        lines.append(f"| {group} | {a['clips']} | {a['attribution_mean']:.3f} / {a['attribution_weighted']:.3f} / "
                     f"{a['attribution_min']:.3f} | {a['der_mean']:.3f} / {a['der_weighted']:.3f} / {a['der_max']:.3f} | "
                     f"{a['count_exact']:.0%} | {a['merged_clips']} | {a['split_clips']} | {a['wer_mean']:.3f} |")
    lines += ["", "| Clip | Labels | Ref spk | Hyp spk | Attribution | DER | WER | Merged | Split |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in report["clips"]:
        m, e = r["metrics"], r["errors"]
        lines.append(f"| {r['id']} | {r['label_source']} | {r['speakers']} | {e['hyp_speakers']} | "
                     f"{m['speaker_attribution']:.3f} | {m['der']:.3f} | {m['wer']:.3f} | "
                     f"{'; '.join('+'.join(x) for x in e['merged'])} | {', '.join(e['split'])} |")
    for clip, error in report["failed"].items():
        lines.append(f"| {clip} | failed: {error} | | | | | | | |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run", "asr", "score"])
    parser.add_argument("--dataset", default=os.environ.get("RESONA_DIAR_DATASET"),
                        help="dataset directory (env RESONA_DIAR_DATASET)")
    parser.add_argument("--run", action="append", default=[], help="run name (score: repeatable)")
    parser.add_argument("--diarizer", choices=["nemotron", "pyannote"], default=None,
                        help="`run`: diarize in-process over the cached ASR words instead of the worker path")
    parser.add_argument("--profile", default="mac-private", help="execution profile for `run` and `asr`")
    parser.add_argument("--only", default=None, help="comma-separated clip ids")
    parser.add_argument("--force", action="store_true", help="re-run clips that already have a result")
    args = parser.parse_args()
    dataset = _dataset(args.dataset)
    only = set(args.only.split(",")) if args.only else None
    if args.command == "asr":
        asr(dataset, args.profile, only, args.force)
    elif args.command == "run":
        if len(args.run) != 1:
            parser.error("run takes one --run")
        if args.diarizer:
            run_diarizer(dataset, args.run[0], args.diarizer, args.profile, only, args.force)
        else:
            run(dataset, args.run[0], args.profile, only, args.force)
    else:
        if not args.run:
            parser.error("score needs --run")
        for name in args.run:
            print(markdown(score(dataset, name)) + "\n")


if __name__ == "__main__":
    main()
