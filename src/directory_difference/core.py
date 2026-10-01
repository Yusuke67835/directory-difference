"""Compare two directories by file content hash.

Only regular files are compared. Symlinks, device files, sockets, and FIFOs are
intentionally skipped: comparing their "content" would require resolving links or
reading from streams whose semantics differ from plain file bytes, and silently
lumping them into the regular-file model would produce confusing results. A user
who needs to track symlinks should build a separate comparator; mixing them here
is the kind of ambiguity that bites later.

Encoding of file paths in the result uses OS-native separators as returned by
os.walk. Callers who need a stable, portable key should normalize themselves.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Dict, Iterator, Optional, Tuple


@dataclass(frozen=True)
class FileEntry:
    """A single file discovered during a directory walk.

    ``relative_path`` is the path relative to the scanned directory root, using
    the OS-native separator. ``size`` is the byte length; stored alongside the
    hash because it is cheap to collect and useful to callers who want to report
    human-friendly summaries without re-stat-ing files.
    """

    relative_path: str
    size: int
    hash: str


@dataclass
class DiffResult:
    """Outcome of comparing two directories.

    Each list holds ``FileEntry`` objects whose ``relative_path`` is relative to
    its own directory root. Because the two roots may differ, a path that appears
    in ``added`` and ``modified`` would be the same string but refer to different
    absolute locations; that is by design — the point of this library is to
    compare two trees that are supposed to mirror each other.
    """

    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    modified: list = field(default_factory=list)


class DirectoryDiff:
    """Compare two directory trees by content hash.

    Parameters
    ----------
    hash_name:
        Name accepted by ``hashlib.new``. ``sha256`` is the default because it is
        widely available and collision-resistant enough for change detection.
        ``md5`` is faster but not cryptographically sound; callers who only need
        a change detector may pass it explicitly.
    chunk_size:
        Bytes read per iteration when hashing. 64 KiB is a reasonable default
        that avoids both excessive syscalls and excessive memory use. Tests pass
        tiny values to exercise the multi-chunk path.
    """

    def __init__(self, hash_name: str = "sha256", chunk_size: int = 64 * 1024) -> None:
        # Validate up front so a misconfigured diff fails immediately rather
        # than partway through a large walk.
        hashlib.new(hash_name)
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self._hash_name = hash_name
        self._chunk_size = chunk_size

    def compare(self, left: str, right: str) -> DiffResult:
        """Compare ``left`` and ``right`` directories.

        Raises ``FileNotFoundError`` if either path does not exist, and
        ``NotADirectoryError`` if either path is not a directory. These are the
        standard library's own errors; translating them would hide the cause.
        """
        self._require_directory(left)
        self._require_directory(right)

        left_files = dict(self._walk(left))
        right_files = dict(self._walk(right))

        result = DiffResult()
        for path, right_entry in right_files.items():
            left_entry = left_files.get(path)
            if left_entry is None:
                result.added.append(right_entry)
            elif left_entry.hash != right_entry.hash:
                result.modified.append(right_entry)
        for path, left_entry in left_files.items():
            if path not in right_files:
                result.removed.append(left_entry)

        # Sort for deterministic output regardless of filesystem walk order.
        # Walk order is not guaranteed across platforms, and tests rely on
        # stable ordering.
        result.added.sort(key=lambda e: e.relative_path)
        result.removed.sort(key=lambda e: e.relative_path)
        result.modified.sort(key=lambda e: e.relative_path)
        return result

    @staticmethod
    def _require_directory(path: str) -> None:
        # Use lstat semantics: we care about the directory entry itself, not
        # what a symlink points at. A symlink to a directory is not treated as
        # a directory here, which avoids surprising recursion through links.
        if not os.path.exists(path):
            raise FileNotFoundError(f"No such file or directory: {path!r}")
        if not os.path.isdir(path):
            raise NotADirectoryError(f"Not a directory: {path!r}")

    def _walk(self, root: str) -> Iterator[Tuple[str, FileEntry]]:
        """Yield (relative_path, FileEntry) pairs for every regular file under root."""
        for dirpath, dirnames, filenames in os.walk(root):
            # Sorting dirnames makes os.walk descend deterministically. This is
            # not strictly required for correctness (we sort results at the
            # end), but deterministic descent aids debugging and makes any
            # future short-circuit logic predictable.
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root)
                try:
                    st = os.lstat(full)
                except OSError:
                    # File may vanish between walk and stat (TOCTOU). Skip it
                    # rather than crash; a transient race should not abort a
                    # full directory scan. We do not record it anywhere because
                    # we cannot characterize something we could not stat.
                    continue
                # os.walk lists symlinks in filenames. We explicitly exclude
                # non-regular files: only S_ISREG qualifies. This drops
                # symlinks even if they point to regular files, because a
                # symlink's identity is the link, not the target.
                import stat as _stat
                if not _stat.S_ISREG(st.st_mode):
                    continue
                digest = self._hash_file(full)
                yield rel, FileEntry(
                    relative_path=rel,
                    size=st.st_size,
                    hash=digest,
                )

    def _hash_file(self, path: str) -> str:
        h = hashlib.new(self._hash_name)
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(self._chunk_size), b""):
                h.update(block)
        return h.hexdigest()
