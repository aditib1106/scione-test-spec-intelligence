"""Legacy SQL source defaults stay compatible with current library/report reads."""

from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select, update
from test_library_api import _seed
from test_library_api import client as client

from scione.library.store import LibraryStore, runs
from scione.specifications.store import FileSpecStore
from scione.structured.models import NORMALIZER_VERSION, AtomicClaim


def test_legacy_atomic_source_default_does_not_block_library_or_comparison(client):
    http, root = client
    document, previous = _seed(root)
    run = previous.model_copy(deep=True, update={"run_id": str(uuid4())})
    run.extraction.requirements[0].atomic_claims = [
        AtomicClaim(
            id="gloss_atom",
            parameter_id="gloss",
            label="Initial gloss",
            kind="criterion",
            stage="initial",
            operator="range",
            minimum="82",
            maximum="92",
            unit="GU",
            raw_text="Initial gloss 82-92 GU",
            source_pages=[1],
        )
    ]
    specs = FileSpecStore(root)
    specs.save_extraction(document.document_id, run)
    assert http.get(f"/api/library/{document.document_id}").status_code == 200

    legacy = deepcopy(run.extraction.model_dump(mode="json"))
    legacy["requirements"][0]["atomic_claims"][0].pop("source_context")
    archive = LibraryStore(root)
    try:
        with archive.engine.begin() as connection:
            connection.execute(
                update(runs)
                .where(runs.c.run_id == run.run_id)
                .values(source=legacy, normalizer_version="atomic_requirements_v0_4")
            )

        # These are the two routes that returned 409 for the saved pilot data.
        assert http.get("/api/library").status_code == 200
        response = http.get(f"/api/comparisons/{document.document_id}")
        assert response.status_code == 200
        assert len(response.json()["requirements"]) == 2

        with archive.engine.connect() as connection:
            stored = connection.execute(
                select(runs.c.source, runs.c.normalizer_version).where(runs.c.run_id == run.run_id)
            ).one()
        assert stored.source == legacy
        assert stored.normalizer_version == f"{NORMALIZER_VERSION}_native_first"
        assert specs.load_extraction(document.document_id, run.run_id) == run
        assert specs.load_extraction(document.document_id, run.run_id).raw_response == (
            "Untouched model-response fixture\n"
        )
    finally:
        archive.close()
