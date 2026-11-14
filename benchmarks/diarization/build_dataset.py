"""Build the private speaker-diarization dataset described by ``sources.json``.

The dataset lives outside the repository (``--dataset`` or ``RESONA_DIAR_DATASET``):
podcast audio and transcripts are copyrighted and must never be committed.

    uv run python benchmarks/diarization/build_dataset.py fetch --dataset ~/devBench/resona-diar-dataset
    uv run python benchmarks/diarization/build_dataset.py build --dataset ~/devBench/resona-diar-dataset \
        --interview ~/devBench/interview/data

``fetch`` downloads every ``labels.url`` and ``audio.url`` into ``<dataset>/sources/<episode>/``.
``build`` turns each episode into reference speaker turns and cuts evaluation clips:

1. Speaker labels: a published transcript (gold, or a publisher's machine labels: silver),
   the interview data dir's parsed publisher transcripts (``interview-turns``:
   ``<interview>/diarized/<id>.json``, the "platinum" corpus v4) or Deepgram's own word
   speakers (silver).
2. Times: published words are aligned (``resona_eval.metrics.word_alignment``) to timed
   words, Deepgram's (``timing: deepgram``), whisper-large-v3-turbo's on the clip
   (``timing: whisper``, for transcripts with turn times) or on the whole episode
   (``timing: whisper-episode``, for transcripts without times; cached in
   ``<dataset>/sources/<episode>/whisper-words.json``). Every aligned word carries a speaker
   and a time; consecutive words of one speaker form a turn, split at pauses over
   ``PAUSE_SPLIT`` seconds.
3. Windows of ``clip_seconds`` with interesting speaker structure (``multi``: most speakers,
   then most changes; ``short``: most short turns; ``typical``: median changes), cut at pauses,
   16 kHz mono WAV. ``full: true`` adds the whole episode.

Output: ``<dataset>/corpus/`` is a resona-eval corpus (``MANIFEST.json`` loads with
``resona_eval.load_corpus``; each clip is an ``options.diarize`` fixture with ``speakers``),
plus ``clips.json`` (per-clip metadata), ``rttm/<id>.rttm`` and, for Deepgram-timed clips,
``<dataset>/runs/deepgram-nova3/<id>.result.json`` (Deepgram's diarization as a JobResult, a
comparison baseline that ``score.py`` scores like any other run).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import statistics
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from resona_eval.metrics import normalize, word_alignment

HERE = Path(__file__).resolve().parent
PAUSE_SPLIT = 2.0  # seconds of silence that end a reference turn
SHORT_TURN = 1.5  # seconds; a turn below counts as short (backchannel, interjection)
MIN_MATCH = 0.8  # share of published words in a window that must align exactly
MIN_SPEAKER_SECONDS = 2.0  # a speaker counts for window selection with this much speech
STEP = 15.0  # window start grid, seconds
SNAP = 8.0  # look this far around a window edge for a pause to cut at
WHISPER_TIMING_MODEL = "mlx-community/whisper-large-v3-turbo"
LICENCE = ("Copyrighted podcast audio and transcript ({show}); private evaluation copy, "
           "do not commit, publish or redistribute.")


@dataclass
class Tok:
    """One normalized word: who said it and, when aligned, when."""

    text: str
    speaker: str
    start: float | None = None
    end: float | None = None
    exact: bool = False  # aligned to an identical timed word


# ---------------------------------------------------------------- published labels

def _tokens(turns: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(tok, speaker) for speaker, text in turns for tok in normalize(text).split()]


def parse_changelog(text: str) -> list[tuple[str, str]]:
    """``**Name:** text`` paragraphs; unlabelled paragraphs continue the last speaker."""
    turns: list[tuple[str, str]] = []
    for para in re.split(r"\n\s*\n", text):
        para = re.sub(r"\\\[[0-9:.]+\\\]", " ", para).strip()
        m = re.match(r"\*\*([^*]+?):?\*\*:?\s*(.*)", para, re.S)
        if m:
            speaker, body = m.group(1).strip(), m.group(2)
            if speaker.lower() == "break":
                continue
            turns.append((speaker, body))
        elif turns and para:
            turns[-1] = (turns[-1][0], turns[-1][1] + " " + para)
    return turns


def parse_signals(text: str) -> list[tuple[str, str]]:
    """signalsandthreads.com: ``<h1>`` speaker, then ``<p>`` paragraphs, inside the transcript tab."""
    start = text.find("tab-view transcript")
    end = text.find("tab-view", start + 20)
    body = text[start:end if end > 0 else None]
    turns: list[tuple[str, str]] = []
    for tag, content in re.findall(r"<(h1|p)[^>]*>(.*?)</\1>", body, re.S):
        plain = html.unescape(re.sub(r"<[^>]+>", " ", content))
        if tag == "h1":
            turns.append((plain.strip(), ""))
        elif turns:
            turns[-1] = (turns[-1][0], turns[-1][1] + " " + plain)
    return [t for t in turns if t[1].strip()]


def parse_transistor(text: str) -> list[tuple[str, str]]:
    return [(s["speaker"], s["body"]) for s in json.loads(text)["segments"] if s.get("body")]


def _ts(stamp: str) -> float:
    return sum(float(part) * mult for part, mult in zip(reversed(stamp.split(":")), (1, 60, 3600)))


def parse_vtt(text: str) -> list[tuple[float, float, str, str]]:
    """WebVTT cues ``(start, end, speaker, text)``: ``<v Name>`` voice tags or ``Name: text``."""
    cues = []
    for m in re.finditer(r"([\d:.]+) --> ([\d:.]+)[^\n]*\n(.+?)(?:\n\s*\n|\Z)", text, re.S):
        body = m.group(3).strip()
        voice = re.match(r"<v ([^>]+)>(.*)", body, re.S)
        named = re.match(r"([^:\n]{2,40}): (.*)", body, re.S)
        if voice:
            speaker, said = voice.group(1), voice.group(2)
        elif named:
            speaker, said = named.group(1), named.group(2)
        else:
            continue
        said = re.sub(r"<[^>]+>", "", said).replace("\n", " ").strip()
        if said:
            cues.append((_ts(m.group(1)), _ts(m.group(2)), speaker.strip(), said))
    return sorted(cues, key=lambda c: c[0])


# ---------------------------------------------------------------- timing

def deepgram_words(path: Path) -> list[tuple[str, float, float, int]]:
    """Normalized Deepgram tokens ``(token, start, end, speaker)``."""
    words = json.loads(path.read_text())["results"]["channels"][0]["alternatives"][0]["words"]
    return [(tok, float(w["start"]), float(w["end"]), int(w.get("speaker", 0)))
            for w in words for tok in normalize(w.get("punctuated_word") or w["word"]).split()]


def whisper_words(wav: Path, language: str) -> list[tuple[str, float, float]]:
    """Normalized whisper-large-v3-turbo tokens with word times (cached HF weights only)."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import mlx_whisper

    out = mlx_whisper.transcribe(str(wav), path_or_hf_repo=WHISPER_TIMING_MODEL, language=language,
                                 word_timestamps=True, condition_on_previous_text=False)
    return [(tok, float(w["start"]), float(w["end"]))
            for seg in out["segments"] for w in seg.get("words", []) for tok in normalize(w["word"]).split()]


def align(published: list[tuple[str, str]], timed: list[tuple[str, float, float]]) -> list[Tok]:
    """Published tokens in order, with the time of the timed token each aligns to.

    Exact matches keep their time; substitutions only inside short error runs (≤ 3), since
    long runs mean the texts diverge (ads, edits) and their pairing is arbitrary. Times
    that run backwards are dropped, so the timed tokens stay monotonic.
    """
    entries = word_alignment(" ".join(t for t, _ in published), " ".join(t for t, _, _ in timed))
    out: list[Tok] = []
    pending: list[tuple[Tok, bool]] = []  # current run of non-exact entries

    def flush() -> None:
        keep = len(pending) <= 3
        for tok, timed_ok in pending:
            if not (keep and timed_ok):
                tok.start = tok.end = None
        pending.clear()

    i = j = 0
    for entry in entries:
        op = entry["op"]
        if op == "insert":
            pending.append((Tok("", "", None, None), False))
            j += 1
            continue
        tok = Tok(*published[i])
        i += 1
        if op in ("equal", "substitute"):
            tok.start, tok.end = timed[j][1], timed[j][2]
            j += 1
        if op == "equal":
            flush()
            tok.exact = True
        else:
            pending.append((tok, op == "substitute"))
        out.append(tok)
    flush()
    last = -1.0
    for tok in out:
        if tok.start is None:
            continue
        if tok.start < last - 0.05:
            tok.start = tok.end = None
            tok.exact = False
        else:
            last = tok.start
    return out


# ---------------------------------------------------------------- windows and turns

def _timed(toks: list[Tok]) -> list[Tok]:
    return [t for t in toks if t.start is not None]


def turns_of(toks: list[Tok], t0: float, t1: float) -> list[dict]:
    """Reference turns of the tokens inside ``[t0, t1]``, times relative to ``t0``.

    Untimed tokens between timed ones join the current turn when they belong to its speaker.
    """
    idx = [k for k, t in enumerate(toks) if t.start is not None and t.start >= t0 and t.end <= t1]
    if not idx:
        return []
    turns: list[dict] = []
    for tok in toks[idx[0]: idx[-1] + 1]:
        if tok.start is None:
            if turns and turns[-1]["speaker"] == tok.speaker:
                turns[-1]["words"].append(tok.text)
            continue
        if tok.start < t0 or tok.end > t1:
            continue
        cur = turns[-1] if turns else None
        if cur and cur["speaker"] == tok.speaker and tok.start - cur["end"] <= PAUSE_SPLIT:
            cur["words"].append(tok.text)
            cur["end"] = max(cur["end"], tok.end)
        else:
            turns.append({"speaker": tok.speaker, "start": tok.start, "end": tok.end, "words": [tok.text]})
    out = []
    for t in turns:
        start, end = round(t["start"] - t0, 3), round(t["end"] - t0, 3)
        out.append({"speaker": t["speaker"], "start": max(start, 0.0), "end": max(end, start + 0.02),
                    "text": " ".join(t["words"])})
    return sorted(out, key=lambda t: t["start"])


def features(toks: list[Tok], t0: float, t1: float) -> dict:
    turns = turns_of(toks, t0, t1)
    speech: dict[str, float] = {}
    for t in turns:
        speech[t["speaker"]] = speech.get(t["speaker"], 0.0) + t["end"] - t["start"]
    inside = [t for t in toks if t.start is not None and t0 <= t.start <= t1]
    changes = sum(1 for a, b in zip(turns, turns[1:]) if a["speaker"] != b["speaker"])
    return {
        "speakers": sum(1 for s in speech.values() if s >= MIN_SPEAKER_SECONDS),
        "changes": changes,
        "short_turns": sum(1 for t in turns if t["end"] - t["start"] < SHORT_TURN),
        "words": len(inside),
    }


def match_ratio(toks: list[Tok], t0: float, t1: float) -> float:
    """Share of published tokens between the first and last timed token in the window that aligned exactly."""
    idx = [k for k, t in enumerate(toks) if t.start is not None and t0 <= t.start <= t1]
    if not idx:
        return 0.0
    span = toks[idx[0]: idx[-1] + 1]
    return sum(t.exact for t in span) / len(span)


def snap(toks: list[Tok], at: float) -> float:
    """The middle of the longest pause between timed tokens within ``SNAP`` seconds of ``at``."""
    timed = _timed(toks)
    best, gap = at, 0.0
    for a, b in zip(timed, timed[1:]):
        if b.start - a.end > gap and at - SNAP <= (a.end + b.start) / 2 <= at + SNAP:
            best, gap = (a.end + b.start) / 2, b.start - a.end
    return best


def choose_windows(toks: list[Tok], kinds: list[str], length: float, need_match: bool) -> list[tuple[str, float, float]]:
    timed = _timed(toks)
    first, last = timed[0].start, timed[-1].end
    candidates = []
    t = first
    while t + length <= last:
        if not need_match or match_ratio(toks, t, t + length) >= MIN_MATCH:
            f = features(toks, t, t + length)
            if f["speakers"] >= 2:
                candidates.append((t, f))
        t += STEP
    chosen: list[tuple[str, float, float]] = []

    def free(t0: float) -> bool:
        return all(t0 + length <= a or t0 >= b for _, a, b in chosen)

    for kind in kinds:
        pool = [(t0, f) for t0, f in candidates if free(t0)]
        if not pool:
            break
        if kind == "multi":
            t0, _ = max(pool, key=lambda c: (c[1]["speakers"], c[1]["changes"]))
        elif kind == "short":
            t0, _ = max(pool, key=lambda c: (c[1]["short_turns"], c[1]["changes"]))
        elif kind == "typical":
            median = statistics.median(f["changes"] for _, f in pool)
            t0, _ = min(pool, key=lambda c: (abs(c[1]["changes"] - median), c[0]))
        else:
            raise SystemExit(f"unknown window kind {kind!r}")
        a, b = snap(toks, t0), snap(toks, t0 + length)
        chosen.append((kind, a, b))
    return chosen


def vtt_overlap(cues: list[tuple[float, float, str, str]], t0: float, t1: float) -> float:
    """Seconds inside ``[t0, t1]`` in which cues of two or more different speakers are active."""
    bounds = sorted({max(t0, min(t1, x)) for c in cues if c[1] > t0 and c[0] < t1 for x in c[:2]} | {t0, t1})
    total = 0.0
    for a, b in zip(bounds, bounds[1:]):
        active = {c[2] for c in cues if c[0] <= a and c[1] >= b}
        if len(active) >= 2:
            total += b - a
    return round(total, 2)


# ---------------------------------------------------------------- audio

def cut(src: Path, dst: Path, t0: float, t1: float | None) -> None:
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t0:.3f}"]
    if t1 is not None:
        args += ["-t", f"{t1 - t0:.3f}"]
    args += ["-i", str(src), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
    subprocess.run(args, check=True)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True)
    return float(out.stdout.strip())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- episodes

def _download(url: str, dst: Path) -> None:
    if dst.exists() and dst.stat().st_size > 0:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "resona-diarization-dataset/1"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dst.with_suffix(dst.suffix + ".part"), "wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    dst.with_suffix(dst.suffix + ".part").rename(dst)


_LABEL_SUFFIX = {"changelog-md": ".md", "signals-html": ".html", "transistor-json": ".json", "vtt": ".vtt"}


def label_path(dataset: Path, ep: dict) -> Path:
    return dataset / "sources" / ep["id"] / f"labels{_LABEL_SUFFIX[ep['labels']['format']]}"


def audio_path(dataset: Path, interview: Path | None, ep: dict) -> Path:
    if "interview" in ep["audio"]:
        if interview is None:
            raise SystemExit(f"{ep['id']}: needs --interview for its audio")
        return interview / "raw" / ep["audio"]["interview"] / "audio.m4a"
    return dataset / "sources" / ep["id"] / ("audio" + Path(ep["audio"]["url"].split("?")[0]).suffix)


def fetch(spec: dict, dataset: Path) -> None:
    for ep in spec["episodes"]:
        if "url" in ep["labels"]:
            _download(ep["labels"]["url"], label_path(dataset, ep))
        if "url" in ep["audio"]:
            _download(ep["audio"]["url"], audio_path(dataset, None, ep))
        print(f"fetched {ep['id']}", file=sys.stderr)


def published_turns(dataset: Path, ep: dict) -> list[tuple[str, str]]:
    text = label_path(dataset, ep).read_text(encoding="utf-8")
    fmt = ep["labels"]["format"]
    if fmt == "changelog-md":
        return parse_changelog(text)
    if fmt == "signals-html":
        return parse_signals(text)
    if fmt == "transistor-json":
        return parse_transistor(text)
    raise SystemExit(f"{ep['id']}: no turn parser for {fmt}")


def interview_turns(dataset: Path, interview: Path | None, ep: dict) -> list[dict]:
    """Parsed publisher turns ``{speaker, start, end, text}``, frozen into
    ``<dataset>/sources/<episode>/labels.turns.json`` on first use (the interview data dir keeps changing)."""
    frozen = dataset / "sources" / ep["id"] / "labels.turns.json"
    if not frozen.exists():
        if interview is None:
            raise SystemExit(f"{ep['id']}: needs --interview for its labels")
        frozen.parent.mkdir(parents=True, exist_ok=True)
        frozen.write_text((interview / "diarized" / f"{ep['labels']['interview']}.json").read_text())
    return json.loads(frozen.read_text())


def turn_cues(turns: list[dict]) -> list[tuple[float, float, str, str]] | None:
    """Timed turns as cues; a missing end becomes the next turn's start. None when a turn has no start."""
    if any(t.get("start") is None for t in turns):
        return None
    cues = []
    for k, t in enumerate(turns):
        start = float(t["start"])
        end = t.get("end")
        if end is None:
            end = turns[k + 1]["start"] if k + 1 < len(turns) else start + 0.4 * len(t["text"].split())
        cues.append((start, max(float(end), start), str(t["speaker"]), t["text"]))
    return cues


def cue_tokens(cues: list[tuple[float, float, str, str]]) -> list[Tok]:
    """Provisional times spread evenly over each cue (window selection only)."""
    toks = []
    for start, end, speaker, said in cues:
        words = normalize(said).split()
        step = (end - start) / max(len(words), 1)
        toks += [Tok(w, speaker, start + k * step, start + (k + 1) * step, True) for k, w in enumerate(words)]
    toks.sort(key=lambda t: t.start)
    return toks


def episode_whisper(dataset: Path, audio: Path, ep: dict) -> list[tuple[str, float, float]]:
    """whisper-large-v3-turbo word times over the whole episode, cached next to its sources."""
    cache = dataset / "sources" / ep["id"] / "whisper-words.json"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(whisper_words(audio, ep["language"])))
    return [tuple(w) for w in json.loads(cache.read_text())]


def episode_tokens(dataset: Path, interview: Path | None, ep: dict) -> tuple[list[Tok], list | None]:
    """Episode-wide tokens for window selection (and, for Deepgram or episode-wide whisper
    timing, the reference). Cue episodes (VTT, timed publisher turns) get provisional times;
    their reference times come from whisper per clip (:func:`whisper_reference`)."""
    fmt = ep["labels"]["format"]
    if fmt == "vtt":
        cues = parse_vtt(label_path(dataset, ep).read_text(encoding="utf-8"))
        return cue_tokens(cues), cues
    if fmt == "interview-turns":
        turns = interview_turns(dataset, interview, ep)
        if ep["timing"] == "whisper":
            cues = turn_cues(turns)
            if cues is None:
                raise SystemExit(f"{ep['id']}: timing 'whisper' needs timed turns; use 'whisper-episode'")
            return cue_tokens(cues), cues
        published = _tokens([(str(t["speaker"]), t["text"]) for t in turns])
        return align(published, episode_whisper(dataset, audio_path(dataset, interview, ep), ep)), None
    dg = deepgram_words(interview / "raw" / ep["audio"]["interview"] / "deepgram.json")
    if fmt == "deepgram":
        return [Tok(t, f"S{s}", a, b, True) for t, a, b, s in dg], None
    return align(_tokens(published_turns(dataset, ep)), [(t, a, b) for t, a, b, _ in dg]), None


def whisper_reference(cues: list, wav: Path, t0: float, t1: float, language: str) -> tuple[list[Tok], float]:
    """Cue tokens of the clip aligned to whisper word times on the clip (times relative to the clip)."""
    published = []
    for start, end, speaker, said in cues:
        if end >= t0 - 3 and start <= t1 + 3:
            published += [(w, speaker) for w in normalize(said).split()]
    timed = whisper_words(wav, language)
    toks = align(published, timed)
    # Cue words outside the clip align to nothing; trim them so the match ratio counts the clip only.
    timed_idx = [k for k, t in enumerate(toks) if t.start is not None]
    toks = toks[timed_idx[0]: timed_idx[-1] + 1] if timed_idx else []
    ratio = sum(t.exact for t in toks) / len(toks) if toks else 0.0
    return toks, ratio


def deepgram_result(dg: list[tuple[str, float, float, int]], t0: float, t1: float) -> dict:
    """Deepgram's own diarization of ``[t0, t1]`` as a JobResult (speaker sections, renumbered 1, 2, …)."""
    words = [(t, a - t0, b - t0, s) for t, a, b, s in dg if a >= t0 and b <= t1]
    numbers: dict[int, int] = {}
    segments: list[dict] = []
    for tok, a, b, s in words:
        n = numbers.setdefault(s, len(numbers) + 1)
        if segments and segments[-1]["speaker"] == n:
            segments[-1]["text"] += " " + tok
            segments[-1]["end"] = round(b, 3)
        else:
            segments.append({"start": round(a, 3), "end": round(max(b, a + 0.02), 3), "text": tok, "speaker": n})
    text = " ".join(t for t, *_ in words)
    return {"schema_version": 1, "kind": "audio", "raw_text": text, "markdown": text, "language": None,
            "segments": segments, "provenance": {"stages": [
                {"stage": "diarize", "engine": "deepgram", "model": "nova-3", "accelerator": "external", "seconds": 0.0}]}}


def rttm(clip_id: str, turns: list[dict]) -> str:
    return "".join(f"SPEAKER {clip_id} 1 {t['start']:.3f} {t['end'] - t['start']:.3f} <NA> <NA> "
                   f"{t['speaker'].replace(' ', '_')} <NA> <NA>\n" for t in turns)


def build(spec: dict, dataset: Path, interview: Path | None, only: set[str] | None) -> None:
    corpus = dataset / "corpus"
    (corpus / "rttm").mkdir(parents=True, exist_ok=True)
    dg_runs = dataset / "runs" / "deepgram-nova3"
    dg_runs.mkdir(parents=True, exist_ok=True)
    manifest_path, meta_path = corpus / "MANIFEST.json", corpus / "clips.json"
    fixtures = {f["id"]: f for f in json.loads(manifest_path.read_text())["fixtures"]} if manifest_path.exists() else {}
    metas = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    length = float(spec["clip_seconds"])

    def drop(clip_id: str) -> None:
        metas.pop(clip_id, None)
        fixtures.pop(clip_id, None)
        (corpus / f"{clip_id}.wav").unlink(missing_ok=True)
        (corpus / "rttm" / f"{clip_id}.rttm").unlink(missing_ok=True)

    known = {ep["id"] for ep in spec["episodes"]}
    for gone in [k for k, m in metas.items() if m["episode"] not in known]:
        print(f"drop {gone}: episode not in sources", file=sys.stderr)
        drop(gone)

    for ep in spec["episodes"]:
        if only and ep["id"] not in only:
            continue
        for stale in [k for k, m in metas.items() if m["episode"] == ep["id"]]:
            drop(stale)
        audio = audio_path(dataset, interview, ep)
        toks, cues = episode_tokens(dataset, interview, ep)
        need_match = ep["labels"]["format"] != "deepgram" and ep["timing"] != "whisper"
        windows = choose_windows(toks, ep["windows"], length, need_match)
        if ep.get("full"):
            windows.append(("full", 0.0, duration(audio)))
        dg = (deepgram_words(interview / "raw" / ep["audio"]["interview"] / "deepgram.json")
              if ep["timing"] == "deepgram" else None)
        for kind, t0, t1 in windows:
            clip_id = f"{ep['id']}-{kind}" if kind == "full" else f"{ep['id']}-{kind}-{int(t0)}"
            wav = corpus / f"{clip_id}.wav"
            cut(audio, wav, t0, None if kind == "full" else t1)
            if ep["timing"] == "whisper":
                clip_toks, ratio = whisper_reference(cues, wav, t0, t1, ep["language"])
                turns = turns_of(clip_toks, 0.0, t1 - t0)
            else:
                turns = turns_of(toks, t0, t1)
                ratio = 1.0 if not need_match else match_ratio(toks, t0, t1)
            if ratio < MIN_MATCH:
                print(f"skip {clip_id}: only {ratio:.2f} of the published words align", file=sys.stderr)
                wav.unlink()
                continue
            if len({t["speaker"] for t in turns}) < 2:
                print(f"skip {clip_id}: fewer than two speakers", file=sys.stderr)
                wav.unlink()
                continue
            f = {
                "speakers": len({t["speaker"] for t in turns}),
                "changes": sum(1 for a, b in zip(turns, turns[1:]) if a["speaker"] != b["speaker"]),
                "short_turns": sum(1 for t in turns if t["end"] - t["start"] < SHORT_TURN),
            }
            fixtures[clip_id] = {
                "id": clip_id, "kind": "audio", "file": wav.name, "language": ep["language"],
                "source": f"{ep['show']} ({ep['id']}), {t0:.1f}-{t1:.1f} s; labels: {ep['label_source']}",
                "license": LICENCE.format(show=ep["show"]), "sha256": sha256(wav),
                "reference": " ".join(t["text"] for t in turns),
                "checks": ["wer", "speaker_attribution", "der", "speaker_count"],
                "options": {"diarize": True}, "speakers": turns,
            }
            metas[clip_id] = {
                "episode": ep["id"], "show": ep["show"], "language": ep["language"], "window": kind,
                "start": round(t0, 3), "end": round(t1, 3), "seconds": round(t1 - t0, 3),
                "label_source": ep["label_source"], "label_origin": ep["label_origin"],
                "source_audio": ep["audio"].get("interview") and f"youtube:{ep['audio']['interview']}" or ep["audio"]["url"],
                "labels_url": ep["labels"].get("url"), "match_ratio": round(ratio, 3),
                "speech_seconds": round(sum(t["end"] - t["start"] for t in turns), 1),
                "overlap_seconds": vtt_overlap(cues, t0, t1) if cues else None,
                **f,
            }
            (corpus / "rttm" / f"{clip_id}.rttm").write_text(rttm(clip_id, turns))
            if dg is not None:
                (dg_runs / f"{clip_id}.result.json").write_text(json.dumps(deepgram_result(dg, t0, t1)))
            print(f"{clip_id}: {f['speakers']} speakers, {f['changes']} changes, {f['short_turns']} short, "
                  f"match {ratio:.2f}", file=sys.stderr)

    ordered = sorted(fixtures.values(), key=lambda f: f["id"])
    manifest_path.write_text(json.dumps({"schema_version": 1, "generator": "benchmarks/diarization/build_dataset.py",
                                         "fixtures": ordered}, ensure_ascii=False, indent=1) + "\n")
    meta_path.write_text(json.dumps(dict(sorted(metas.items())), ensure_ascii=False, indent=1) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["fetch", "build"])
    parser.add_argument("--dataset", type=Path, default=os.environ.get("RESONA_DIAR_DATASET"),
                        help="dataset directory outside the repository (env RESONA_DIAR_DATASET)")
    parser.add_argument("--interview", type=Path, default=None,
                        help="data dir with raw/<youtube-id>/{audio.m4a,deepgram.json} and diarized/<id>.json")
    parser.add_argument("--sources", type=Path, default=HERE / "sources.json")
    parser.add_argument("--only", default=None, help="comma-separated episode ids")
    args = parser.parse_args()
    if args.dataset is None:
        parser.error("--dataset or RESONA_DIAR_DATASET is required")
    dataset = args.dataset.expanduser().resolve()
    if (Path(__file__).resolve().parents[2] / ".git").exists() and dataset.is_relative_to(Path(__file__).resolve().parents[2]):
        parser.error("the dataset must live outside the repository")
    spec = json.loads(args.sources.read_text())
    if args.command == "fetch":
        fetch(spec, dataset)
    else:
        only = set(args.only.split(",")) if args.only else None
        build(spec, dataset, args.interview.expanduser() if args.interview else None, only)


if __name__ == "__main__":
    main()
