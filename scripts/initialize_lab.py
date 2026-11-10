"""Explicitly initialize a development lab from the team's synthetic declarations.

Never runs at application startup. Existing declarations require an explicit
replacement flag and remain available as a previous SQL revision.
"""

import argparse

from scione.api.workspace import Workspace
from scione.catalogs.baseline import development_lab_baseline
from scione.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()
    workspace = Workspace(Settings())
    try:
        lab = workspace.labs.load_or_create()
        baseline = development_lab_baseline()
        if lab.capabilities and not args.replace_existing:
            parser.error("Lab already contains declarations; use --replace-existing deliberately.")
        workspace.labs.save(lab.model_copy(update={"capabilities": baseline.capabilities}))
        saved = workspace.labs.load_or_create()
        print(
            f"Saved {len(saved.capabilities)} synthetic lab declarations, revision {saved.version}."
        )
        print(
            "Original profile details, earlier revisions and uploaded specifications are retained."
        )
    finally:
        workspace.library.store.close()


if __name__ == "__main__":
    main()
