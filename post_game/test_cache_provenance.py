"""Unit tests for the Stage-2 checkpoint provenance guard.

Regression cover for the failure this guard exists to catch: the halftime
id-collision fix (`_next_id` carried across the tracker reset) landed while
every cached `tracks_raw.parquet` predated it, so downstream measurement kept
running on data where a large share of tracked time sat in ids that welded two
different children together — and the pipeline never said anything, because
checkpoint reuse tested only `tracks_ckpt.exists()`.
"""

from __future__ import annotations

import json

from post_game import pipeline


def _prov_paths(tmp_path):
    return tmp_path / "tracks_raw.provenance.json", tmp_path / "tracks_raw.parquet"


def test_fingerprint_is_stable_across_calls():
    a = pipeline._tracking_fingerprint()
    b = pipeline._tracking_fingerprint()
    assert a["code_sha256"] == b["code_sha256"]
    assert a["config"] == b["config"]


def test_fingerprint_tracks_config_that_changes_stage2_output():
    fp = pipeline._tracking_fingerprint()
    # These are the knobs that change what Stage 2 emits; if one is dropped from
    # the fingerprint, a cache taken at a different setting mixes in silently.
    for key in ("SAMPLE_RATE", "DETECT_N_TILES", "TRACK_PITCH"):
        assert key in fp["config"]


def test_fresh_cache_is_silent(tmp_path, caplog):
    prov, ckpt = _prov_paths(tmp_path)
    pipeline._write_tracks_provenance(prov)
    with caplog.at_level("WARNING"):
        pipeline._check_tracks_provenance(prov, ckpt)
    assert caplog.text == ""


def test_missing_sidecar_warns(tmp_path, caplog):
    """The state every existing cache was in when the id-carry fix landed."""
    prov, ckpt = _prov_paths(tmp_path)
    with caplog.at_level("WARNING"):
        pipeline._check_tracks_provenance(prov, ckpt)
    assert "NO provenance sidecar" in caplog.text


def test_code_drift_warns(tmp_path, caplog):
    prov, ckpt = _prov_paths(tmp_path)
    pipeline._write_tracks_provenance(prov)
    rec = json.loads(prov.read_text())
    rec["code_sha256"] = "0123456789abcdef"
    prov.write_text(json.dumps(rec))
    with caplog.at_level("WARNING"):
        pipeline._check_tracks_provenance(prov, ckpt)
    assert "DIFFERENT tracking code" in caplog.text


def test_config_drift_warns_and_names_the_key(tmp_path, caplog):
    prov, ckpt = _prov_paths(tmp_path)
    pipeline._write_tracks_provenance(prov)
    rec = json.loads(prov.read_text())
    rec["config"]["SAMPLE_RATE"] = "99"
    prov.write_text(json.dumps(rec))
    with caplog.at_level("WARNING"):
        pipeline._check_tracks_provenance(prov, ckpt)
    assert "config drift" in caplog.text
    assert "SAMPLE_RATE" in caplog.text


def test_corrupt_sidecar_is_treated_as_stale(tmp_path, caplog):
    prov, ckpt = _prov_paths(tmp_path)
    prov.write_text("{ not json")
    with caplog.at_level("WARNING"):
        pipeline._check_tracks_provenance(prov, ckpt)
    assert "unreadable" in caplog.text


def test_guard_never_raises(tmp_path):
    """A provenance problem must never take down a run — it only warns."""
    prov, ckpt = _prov_paths(tmp_path)
    pipeline._check_tracks_provenance(prov, ckpt)          # missing
    prov.write_text("{ not json")
    pipeline._check_tracks_provenance(prov, ckpt)          # corrupt
    prov.write_text(json.dumps({"code_sha256": "x"}))      # no config key
    pipeline._check_tracks_provenance(prov, ckpt)


def test_write_survives_unwritable_path(tmp_path, caplog):
    """A read-only outputs dir costs the warning, not the run."""
    bad = tmp_path / "missing-dir" / "p.json"
    with caplog.at_level("WARNING"):
        pipeline._write_tracks_provenance(bad)
    assert "Could not write cache provenance" in caplog.text
