"""Remove subdirectories under the input root that no longer contain real videos.

Real = what :func:`titleforge.discover.discover_videos` would return; that means
``Sample/`` folders with only ``*sample*`` videos and ``.txt``/``.md`` junk are
ignored — the same heuristic Phase 1 used to decide what to move. A directory
with only this kind of residue is treated as empty and the whole subtree is
deleted.

Strategy: walk ``input_root`` top-down. For each immediate subdirectory, run
``discover_videos`` against it. If the result is empty, ``shutil.rmtree`` the
whole subtree (including any release notes, .nfo, .srt, .jpg, ``Sample/``).
Otherwise recurse so we still catch smaller empty pockets (e.g. an emptied
``Featurettes/`` inside a show that still has other episodes left to clean up
in a different folder).

The ``input_root`` itself is never removed — it's the user's specified inbox.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from titleforge.discover import discover_videos


def remove_empty_source_dirs(input_root: Path) -> list[Path]:
    """Remove subdirectories under ``input_root`` with no real videos remaining.

    Returns the list of directories actually removed (each entry is the path
    of a top-of-subtree removal — children of those paths aren't listed
    separately). Top-down order: a fully-empty parent is removed first and the
    function does not descend into it.
    """
    input_root = input_root.resolve()
    removed: list[Path] = []
    if not input_root.is_dir():
        return removed
    _walk_and_clean(input_root, removed)
    return removed


def _walk_and_clean(directory: Path, removed: list[Path]) -> None:
    try:
        children = sorted(directory.iterdir())
    except OSError:
        return
    for entry in children:
        if not entry.is_dir():
            continue
        if not discover_videos(entry):
            try:
                shutil.rmtree(entry)
            except OSError:
                continue
            removed.append(entry)
        else:
            _walk_and_clean(entry, removed)
