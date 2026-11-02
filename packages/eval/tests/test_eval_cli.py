"""`resona eval` exit codes, hub record contents and baseline promotion."""
import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from resona_cli import eval_cli
from resona_cli.main import app
from resona_client.config import EngineConfig
from resona_eval import BaselineFileError, EvaluationError, FixtureRun, build_report, promoted_entry

HUB = "http://hub.test"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _hub_env(monkeypatch):
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setenv("RESONA_API_URL", HUB)
    monkeypatch.setenv("RESONA_API_KEY", "admin-key")


def _args(corpus, tmp_path, *extra):
    return ["eval", "--profile", "cpu-private", "--output-dir", str(tmp_path / "out"),
            "--corpus", str(corpus.root), "--baselines", str(tmp_path / "baselines.json"), *extra]


def _baseline_file(tmp_path, corpus, fixtures, *, corpus_sha=None):
    entry = {"execution_profile": "cpu-private", "platform": "cpu", "revision": 1, "evaluation_id": 1,
             "corpus_sha256": corpus_sha or corpus.sha256, "release_id": "0.1.0", "fixtures": fixtures}
    (tmp_path / "baselines.json").write_text(json.dumps(
        {"schema_version": 1, "baselines": {"cpu-private/cpu": entry}}), encoding="utf-8")


def _perfect_metrics(corpus):
    metrics = {}
    for f in corpus.fixtures:
        m = {k: 0.0 for k in ("wer", "cer") if k in f.checks}
        m.update({k: 0 for k, check in (("silence_insertions", "silence"), ("missing_cells", "table_cells"),
                                        ("order_failures", "reading_order")) if check in f.checks})
        metrics[f.id] = m
    return metrics


def _local(monkeypatch, runs):
    monkeypatch.setattr(eval_cli, "_run_local", lambda corpus, profile: runs)


def test_local_without_baseline_exits_baseline_required(monkeypatch, corpus, perfect_runs, tmp_path):
    _local(monkeypatch, perfect_runs)
    result = runner.invoke(app, _args(corpus, tmp_path))
    assert result.exit_code == 4
    assert "baseline_required" in result.stderr
    report_text = (tmp_path / "out" / "report.json").read_text(encoding="utf-8")
    for fixture in corpus.fixtures:  # metrics and provenance only, never text
        if fixture.reference:
            assert fixture.reference[:25] not in report_text
    report = json.loads(report_text)
    assert report["gate"]["outcome"] == "baseline_required"
    assert report["corpus_sha256"] == corpus.sha256
    assert report["models"][0]["revision"] == "rev-asr"
    for fixture in corpus.fixtures:  # metrics and provenance only, never text
        assert fixture.reference.split(".")[0] not in report_text or not fixture.reference


def test_local_with_matching_baseline_passes(monkeypatch, corpus, perfect_runs, tmp_path):
    _local(monkeypatch, perfect_runs)
    _baseline_file(tmp_path, corpus, _perfect_metrics(corpus))
    result = runner.invoke(app, _args(corpus, tmp_path))
    assert result.exit_code == 0, result.stderr
    assert "gate: passed" in result.stderr


def test_local_wer_regression_fails_gate(monkeypatch, corpus, perfect_runs, tmp_path, result_for):
    speech = next(r for r in perfect_runs if r.fixture.id == "de-speech")
    words = speech.fixture.reference.split()
    degraded = FixtureRun(fixture=speech.fixture, result=result_for(speech.fixture, text=" ".join(words[2:])))
    _local(monkeypatch, [degraded if r is speech else r for r in perfect_runs])
    _baseline_file(tmp_path, corpus, _perfect_metrics(corpus))
    result = runner.invoke(app, _args(corpus, tmp_path))
    assert result.exit_code == 3
    assert "regression:de-speech:wer" in result.stderr


def test_baseline_for_another_corpus_is_not_comparable(monkeypatch, corpus, perfect_runs, tmp_path):
    _local(monkeypatch, perfect_runs)
    _baseline_file(tmp_path, corpus, _perfect_metrics(corpus), corpus_sha="0" * 64)
    result = runner.invoke(app, _args(corpus, tmp_path))
    assert result.exit_code == 4
    assert "baseline_required" in result.stderr


def test_platform_mismatch_is_an_error(monkeypatch, corpus, perfect_runs, tmp_path):
    _local(monkeypatch, perfect_runs)
    result = runner.invoke(app, _args(corpus, tmp_path, "--platform", "mac-metal"))
    assert result.exit_code == 1
    assert "fixtures ran on cpu" in result.stderr


class _FakeHub:
    def __init__(self, corpus, result_for):
        self.by_id = {f.id: f for f in corpus.fixtures}
        self.result_for = result_for
        self.jobs: dict[int, dict] = {}
        self.evaluations: list[dict] = []
        self.promotions: list[dict] = []

    def install(self, mock: respx.MockRouter, *, promote_status=200):
        mock.get(f"{HUB}/v1/version").respond(json={"release_id": "0.1.0", "hub_version": "0.1.0",
                                                     "worker_protocol": 1})
        mock.post(f"{HUB}/v1/uploads").respond(json={"upload_id": "u", "sha256": "s", "size": 1})
        mock.post(f"{HUB}/v1/jobs").mock(side_effect=self._create_job)
        mock.get(url__regex=rf"{HUB}/v1/jobs/\d+$").mock(side_effect=self._job)
        mock.get(url__regex=rf"{HUB}/v1/jobs/\d+/result$").mock(side_effect=self._result)
        mock.get(f"{HUB}/v1/baselines").respond(404, json={"error": "baseline_required", "message": "none"})
        mock.post(f"{HUB}/v1/evaluations").mock(side_effect=self._evaluation)
        mock.post(f"{HUB}/v1/evaluations/7/promote").mock(
            side_effect=lambda request: self._promote(request, promote_status))

    def _create_job(self, request):
        body = json.loads(request.content)
        job_id = len(self.jobs) + 1
        self.jobs[job_id] = body
        return httpx.Response(202, json={"job_id": job_id, "status": "pending"})

    def _job(self, request):
        job_id = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json={"job_id": job_id, "kind": self.jobs[job_id]["kind"],
                                         "status": "completed", "private": True})

    def _result(self, request):
        job_id = int(request.url.path.split("/")[3])
        fixture = self.by_id[self.jobs[job_id]["provenance"]["source"].rsplit(":", 1)[1]]
        return httpx.Response(200, content=self.result_for(fixture).model_dump_json())

    def _evaluation(self, request):
        body = json.loads(request.content)
        self.evaluations.append(body)
        return httpx.Response(201, json={"evaluation_id": 7, "status": "recorded", **body})

    def _promote(self, request, status):
        self.promotions.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, json={"error": "baseline_revision_mismatch", "message": "moved"})
        return httpx.Response(200, json={"baseline_revision": 1})


def test_remote_records_metrics_only_and_promote_pins_baseline(corpus, tmp_path, result_for, monkeypatch,
                                                              perfect_runs):
    hub = _FakeHub(corpus, result_for)
    with respx.mock(assert_all_called=False) as mock:
        hub.install(mock)
        result = runner.invoke(app, _args(corpus, tmp_path, "--remote"))
        assert result.exit_code == 4, result.stderr

        assert len(hub.jobs) == len(corpus.fixtures)
        for body in hub.jobs.values():
            assert body["purpose"] == "evaluation" and body["release_id"] == "0.1.0"
            assert body["private"] is True and body["execution_profile"] == "cpu-private"
        (record,) = hub.evaluations
        assert set(record) == {"profile", "execution_profile", "platform", "release_id", "corpus_sha256",
                               "baseline_revision", "fixtures", "fixture_checks", "gate"}
        assert record["fixture_checks"] == {
            f.id: {"kind": f.kind, "checks": list(f.checks)} for f in corpus.fixtures
        }
        assert record["platform"] == "cpu" and record["corpus_sha256"] == corpus.sha256
        assert record["gate"] == {"passed": True, "failures": []}
        assert all(isinstance(v, (int, float)) for m in record["fixtures"].values() for v in m.values())

        report_path = tmp_path / "out" / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["evaluation_id"] == 7 and report["hub_baseline_revision"] is None

        promoted = runner.invoke(app, ["eval", "promote", "7", "--report", str(report_path),
                                       "--baselines", str(tmp_path / "baselines.json")])
        assert promoted.exit_code == 0, promoted.stderr
        assert hub.promotions == [{"expected_baseline_revision": None, "rebaseline": False}]

    pinned = json.loads((tmp_path / "baselines.json").read_text(encoding="utf-8"))
    entry = pinned["baselines"]["cpu-private/cpu"]
    assert entry["revision"] == 1 and entry["evaluation_id"] == 7 and entry["release_id"] == "0.1.0"
    assert entry["corpus_sha256"] == corpus.sha256
    assert {m["revision"] for m in entry["models"]} == {"rev-asr", "rev-layout"}
    assert entry["fixtures"]["silence"] == {"silence_insertions": 0}

    _local(monkeypatch, perfect_runs)  # the pinned baseline now gates local runs
    assert runner.invoke(app, _args(corpus, tmp_path)).exit_code == 0


def test_promote_conflict_leaves_baselines_untouched(corpus, tmp_path, result_for):
    hub = _FakeHub(corpus, result_for)
    with respx.mock(assert_all_called=False) as mock:
        hub.install(mock, promote_status=409)
        assert runner.invoke(app, _args(corpus, tmp_path, "--remote")).exit_code == 4
        result = runner.invoke(app, ["eval", "promote", "7", "--report", str(tmp_path / "out" / "report.json"),
                                     "--baselines", str(tmp_path / "baselines.json")])
    assert result.exit_code == 1
    assert "baseline_revision_mismatch" in result.stderr
    assert not (tmp_path / "baselines.json").exists()


def test_promote_refuses_a_report_of_another_evaluation(corpus, tmp_path, monkeypatch, perfect_runs):
    _local(monkeypatch, perfect_runs)
    runner.invoke(app, _args(corpus, tmp_path))
    result = runner.invoke(app, ["eval", "promote", "7", "--report", str(tmp_path / "out" / "report.json")])
    assert result.exit_code == 1
    assert "not the report of hub evaluation 7" in result.stderr


def test_fixture_options_reach_the_submitted_jobs(corpus, tmp_path, result_for):
    hub = _FakeHub(corpus, result_for)
    with respx.mock(assert_all_called=False) as mock:
        hub.install(mock)
        assert runner.invoke(app, _args(corpus, tmp_path, "--remote")).exit_code == 4
    by_fixture = {body["provenance"]["source"].rsplit(":", 1)[1]: body for body in hub.jobs.values()}
    assert by_fixture["bad-text-layer-force"]["options"] == {"languages": ["de"], "ocr": "force"}
    assert by_fixture["bad-text-layer-auto"]["options"] == {"languages": ["de"]}
    assert by_fixture["de-speech"]["options"] == {"translate": False, "word_timestamps": False, "vad_filter": True}


def test_postprocess_profile_applies_to_document_jobs_only_and_is_measurement_only(corpus, tmp_path, result_for):
    hub = _FakeHub(corpus, result_for)
    with respx.mock(assert_all_called=False) as mock:
        hub.install(mock)
        result = runner.invoke(app, _args(corpus, tmp_path, "--remote", "--postprocess-profile", "polish-de"))
        baseline_calls = [c for c in mock.calls if c.request.url.path == "/v1/baselines"]
    assert result.exit_code == 0, result.stderr
    for body in hub.jobs.values():
        if body["kind"] == "document":
            assert body["profile"] == "polish-de"
        else:
            assert "profile" not in body
    assert baseline_calls == [] and hub.evaluations == []  # never compared, never recorded as promotable
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["postprocess_profile"] == "polish-de"
    assert report["baseline"] is None and report["evaluation_id"] is None
    assert report["gate"]["outcome"] == "measurement_only"


def test_postprocess_run_still_fails_absolute_checks(corpus, tmp_path, result_for):
    def degraded(fixture):
        if fixture.id == "table-columns-pdf":
            return result_for(fixture, markdown=result_for(fixture).markdown.replace("| Fuß 9 |", "|  |"))
        return result_for(fixture)

    hub = _FakeHub(corpus, degraded)
    with respx.mock(assert_all_called=False) as mock:
        hub.install(mock)
        result = runner.invoke(app, _args(corpus, tmp_path, "--remote", "--postprocess-profile", "polish-de"))
    assert result.exit_code == 3
    assert "missing_cells:table-columns-pdf" in result.stderr


def test_postprocess_profile_is_rejected_in_local_mode(monkeypatch, corpus, perfect_runs, tmp_path):
    ran = []
    monkeypatch.setattr(eval_cli, "_run_local", lambda corpus, profile: ran.append(profile) or perfect_runs)
    result = runner.invoke(app, _args(corpus, tmp_path, "--postprocess-profile", "polish-de"))
    assert result.exit_code == 1
    assert "--remote" in result.stderr
    assert ran == [] and not (tmp_path / "out" / "report.json").exists()


def test_postprocess_report_cannot_be_promoted(corpus, tmp_path, result_for):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"mode": "remote", "evaluation_id": 7, "postprocess_profile": "polish-de",
                                       "hub_baseline_revision": None}), encoding="utf-8")
    hub = _FakeHub(corpus, result_for)
    with respx.mock(assert_all_called=False) as mock:
        hub.install(mock)
        result = runner.invoke(app, ["eval", "promote", "7", "--report", str(report_path),
                                     "--baselines", str(tmp_path / "baselines.json")])
    assert result.exit_code == 1
    assert hub.promotions == [] and not (tmp_path / "baselines.json").exists()


def test_postprocess_report_is_never_gated_against_or_pinned_as_a_baseline(corpus, perfect_runs):
    with pytest.raises(EvaluationError):
        build_report(corpus=corpus, runs=perfect_runs, execution_profile="cpu-private", platform="cpu",
                     mode="remote", release_id="0.1.0", baseline={"fixtures": {}}, baseline_source="hub",
                     postprocess_profile="polish-de")
    report, _ = build_report(corpus=corpus, runs=perfect_runs, execution_profile="cpu-private", platform="cpu",
                             mode="remote", release_id="0.1.0", baseline=None, baseline_source=None,
                             postprocess_profile="polish-de")
    with pytest.raises(BaselineFileError):
        promoted_entry(report, evaluation_id=7, revision=1, promoted_at="2026-11-02T00:00:00+00:00")
