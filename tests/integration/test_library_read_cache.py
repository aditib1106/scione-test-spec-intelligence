"""Reuse source derivations while reading every mutable review/SQL revision fresh."""

from copy import deepcopy
from uuid import uuid4

import pytest
from test_library_api import _seed

from scione.api.workspace import Workspace
from scione.config import Settings
from scione.library.store import LibraryConflict


@pytest.fixture
def cached_library(tmp_path, monkeypatch):
    workspace = Workspace(
        Settings(
            _env_file=None,
            scione_workspace_dir=tmp_path,
            scione_database_url=None,
            scione_auth_database_url=None,
        )
    )
    source, run = _seed(tmp_path)
    calls = {"normalize": 0, "assess": 0, "review_pdf": 0}
    original_normalize = workspace.comparison_source
    original_pdf = workspace.specs.read_pdf
    import scione.library.workspace as module

    original_assess = module.assess_requirements

    def normalize(*args, **kwargs):
        calls["normalize"] += 1
        return original_normalize(*args, **kwargs)

    def assess(*args, **kwargs):
        calls["assess"] += 1
        return original_assess(*args, **kwargs)

    def read_pdf(*args, **kwargs):
        calls["review_pdf"] += 1
        return original_pdf(*args, **kwargs)

    monkeypatch.setattr(workspace, "comparison_source", normalize)
    monkeypatch.setattr(workspace.specs, "read_pdf", read_pdf)
    monkeypatch.setattr(module, "assess_requirements", assess)
    yield workspace, source, run, calls
    workspace.library.store.close()


def test_repeated_library_reads_reuse_normalization_and_pdf_review(cached_library):
    workspace, source, _run, calls = cached_library
    first = workspace.library.document(source.document_id)
    assert calls == {"normalize": 1, "assess": 1, "review_pdf": 1}
    second = workspace.library.document(source.document_id)
    third = workspace.library.listing()["documents"][0]
    assert first == second == third
    assert calls == {"normalize": 1, "assess": 1, "review_pdf": 1}


def test_human_review_and_sql_fact_edits_invalidate_only_the_source_derivation(cached_library):
    workspace, source, run, calls = cached_library
    initial = workspace.library.document(source.document_id)
    assert initial["review_count"] == 0
    workspace.specs.save_reviews(
        source.document_id,
        run.run_id,
        [run.extraction.requirements[0].id],
        "needs_review",
        "Recheck the source wording.",
        expected_version=workspace.specs.load_review(source.document_id, run.run_id).version,
    )
    reviewed = workspace.library.document(source.document_id)
    assert reviewed["review_count"] == 1
    assert calls == {"normalize": 1, "assess": 2, "review_pdf": 2}
    requirements = deepcopy(run.extraction.requirements)
    requirements[0].original_test_name = "Gloss measurement, corrected label"
    workspace.facts.save(run, requirements, "Verified correction.", expected_version=0)
    corrected = workspace.library.document(source.document_id)
    assert corrected["review_count"] == 0
    assert calls == {"normalize": 1, "assess": 3, "review_pdf": 3}
    assert workspace.specs.load_latest_extraction(source.document_id) == run


def test_sql_metadata_and_archive_versions_are_never_cached(cached_library):
    workspace, source, _run, calls = cached_library
    initial = workspace.library.document(source.document_id)
    workspace.library.store.edit_metadata(
        source.document_id,
        {
            "company_name": "Updated company",
            "title": "Updated title",
            "specification_id": initial["specification_id"],
            "revision": "Issue B",
        },
        initial["version"],
    )
    changed = workspace.library.document(source.document_id)
    assert changed["company_name"] == "Updated company" and changed["revision"] == "Issue B"
    assert changed["version"] > initial["version"]
    assert calls == {"normalize": 1, "assess": 1, "review_pdf": 1}
    with pytest.raises(LibraryConflict):
        workspace.library.store.set_document_archived(
            source.document_id,
            archived=True,
            expected_version=initial["version"],
        )
    archived = workspace.library.store.set_document_archived(
        source.document_id,
        archived=True,
        expected_version=changed["version"],
    )
    assert workspace.library.listing()["documents"] == []
    workspace.library.store.set_document_archived(
        source.document_id,
        archived=False,
        expected_version=archived["version"],
    )
    assert len(workspace.library.listing()["documents"]) == 1


def test_new_extraction_run_invalidates_cached_normalization_and_review(cached_library):
    workspace, source, run, calls = cached_library
    workspace.library.document(source.document_id)
    second = run.model_copy(deep=True, update={"run_id": str(uuid4())})
    workspace.specs.save_extraction(source.document_id, second)
    current = workspace.library.document(source.document_id)
    assert current["run_id"] == second.run_id
    assert calls == {"normalize": 2, "assess": 2, "review_pdf": 2}


def test_equal_source_ids_in_other_workspaces_have_independent_read_caches(
    cached_library,
    tmp_path,
):
    workspace, source, run, calls = cached_library
    workspace.library.document(source.document_id)
    other = Workspace(
        Settings(
            _env_file=None,
            scione_workspace_dir=tmp_path / "other",
            scione_database_url=None,
            scione_auth_database_url=None,
        )
    )
    try:
        imported = other.specs.import_pdf(
            source.original_name, workspace.specs.read_pdf(source.document_id)
        )
        assert imported.document_id == source.document_id
        other.specs.save_extraction(imported.document_id, run)
        other.library.document(imported.document_id)
        assert calls["assess"] == 2
        assert workspace.library._indexed_sources is not other.library._indexed_sources
        assert workspace.library._source_assessments is not other.library._source_assessments
    finally:
        other.library.store.close()
