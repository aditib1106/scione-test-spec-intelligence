"""Saved matching outputs must respect the settings of an explicit new request."""

from datetime import UTC, datetime, timedelta

import pytest
from test_semantic_matching import RecordingProvider, proposal, snapshot

from scione.library.store import LibraryStore
from scione.matching.models import MATCHING_PROMPT_VERSION, MATCHING_VALIDATION_VERSION
from scione.matching.service import SemanticMatchingRunner
from scione.matching.store import SQLMatchingStore

SETTINGS = {
    "temperature": 0.0,
    "max_output_tokens": 8192,
    "reasoning_effort": "low",
    "completion_window": "asap",
    "timeout_seconds": 90.0,
    "automatic_retries": 0,
    "response_format": "json_schema",
}


def attempt(identifier, generation, position=0):
    run = SemanticMatchingRunner(RecordingProvider(proposal())).run(snapshot())
    run.run_id = identifier
    run.started_at = datetime(2026, 11, 9, tzinfo=UTC) + timedelta(seconds=position)
    run.requested_provider = "sail"
    run.requested_model = "fixture-model-alias"
    run.generation_settings = dict(generation)
    return run


@pytest.fixture
def store(tmp_path):
    library = LibraryStore(tmp_path)
    yield SQLMatchingStore(library, "workspace_one")
    library.close()


@pytest.mark.parametrize(
    "change",
    [
        {"max_output_tokens": 16384},
        {"reasoning_effort": "high"},
        {"temperature": 0.2},
        {"timeout_seconds": 120.0},
    ],
)
def test_explicit_generation_profile_reuses_only_compatible_saved_attempt(store, change):
    first = attempt("first", SETTINGS)
    changed = {**SETTINGS, **change}
    latest = attempt("latest", changed, 1)
    store.save("document_one", first)
    store.save("document_one", latest)
    expected = {
        "provider": "sail",
        "model": "fixture-model-alias",
        "generation_settings": SETTINGS,
    }
    found = store.latest_completed("document_one", first.input_fingerprint, **expected)
    assert found.run_id == first.run_id
    assert found.generation_settings == SETTINGS
    assert store.latest_completed(
        "document_one",
        first.input_fingerprint,
        **{**expected, "generation_settings": changed},
    ).run_id == latest.run_id
    # The history/report view retains the newest successful attempt regardless
    # of which profile a developer now chooses in the run dialog.
    assert store.latest_completed("document_one", first.input_fingerprint).run_id == latest.run_id
    assert len(store.listing("document_one")) == 2


def test_legacy_unspecified_generation_cannot_satisfy_explicit_nonempty_profile(store):
    legacy = attempt("legacy", {})
    store.save("document_one", legacy)
    assert store.latest_completed(
        "document_one", legacy.input_fingerprint, generation_settings=SETTINGS
    ) is None
    assert store.latest_completed("document_one", legacy.input_fingerprint).run_id == "legacy"
    assert store.latest_completed(
        "document_one", legacy.input_fingerprint, generation_settings={}
    ).run_id == "legacy"


def test_profile_with_an_extra_or_missing_setting_is_not_equal(store):
    run = attempt("saved", SETTINGS)
    store.save("document_one", run)
    missing = {key: value for key, value in SETTINGS.items() if key != "reasoning_effort"}
    for requested in (missing, {**SETTINGS, "different_policy": True}):
        assert store.latest_completed(
            "document_one", run.input_fingerprint, generation_settings=requested
        ) is None


def test_settings_filter_keeps_existing_workspace_model_prompt_and_validation_boundaries(store):
    run = attempt("current", SETTINGS)
    old_prompt = attempt("old_prompt", SETTINGS, 1)
    old_prompt.prompt_version = MATCHING_PROMPT_VERSION + "_old"
    old_validation = attempt("old_validation", SETTINGS, 2)
    old_validation.validation_version = MATCHING_VALIDATION_VERSION + "_old"
    for saved in (run, old_prompt, old_validation):
        store.save("document_one", saved)
    assert store.latest_completed(
        "document_one", run.input_fingerprint, generation_settings=SETTINGS
    ).run_id == "current"
    assert store.latest_completed(
        "document_one", run.input_fingerprint, model="another-model", generation_settings=SETTINGS
    ) is None
    assert store.latest_completed(
        "document_one", run.input_fingerprint, provider="fireworks", generation_settings=SETTINGS
    ) is None
    assert store.latest_completed("other_document", run.input_fingerprint) is None
    other_workspace = SQLMatchingStore(store.library, "workspace_two")
    assert other_workspace.latest_completed("document_one", run.input_fingerprint) is None
