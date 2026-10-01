# Directory Difference

Compares two directories and reports which files were added, removed, or modified, using a content hash rather than modification time. Returns structured `DiffResult` objects you can inspect programmatically.

## Usage

```python
import os
import tempfile

from directory_difference import DirectoryDiff, DiffResult, FileEntry

# Make two temporary directories to compare.
left = tempfile.mkdtemp()
right = tempfile.mkdtemp()
with open(os.path.join(left, "notes.txt"), "wb") as f:
    f.write(b"v1")
with open(os.path.join(right, "notes.txt"), "wb") as f:
    f.write(b"v2")
with open(os.path.join(right, "new.txt"), "wb") as f:
    f.write(b"new")

diff = DirectoryDiff().compare(left, right)
for entry in diff.added:
    print(f"+ {entry.relative_path} ({entry.size} bytes)")
for entry in diff.removed:
    print(f"- {entry.relative_path}")
for entry in diff.modified:
    print(f"~ {entry.relative_path}")
```

Running the snippet above prints:

```
+ new.txt (3 bytes)
~ notes.txt
```

The diff object has three lists — `added`, `removed`, `modified` — each containing `FileEntry` instances with `relative_path`, `size`, and `hash` fields.

## Why this exists

File modification times lie. A deploy tool that copies a tree may reset mtimes, and two builds of the same source can produce byte-identical files with different timestamps. Hashing the content gives a truthful answer to "what actually changed," at the cost of reading every byte of both trees. That trade-off is intentional: this library is for cases where correctness matters more than speed.

## Edge cases

Symlinks are skipped. Only regular files are hashed. If you have a symlink in one tree and a real file with the same name in the other, neither will appear in the diff — the symlink is invisible to the walker, and the real file has no counterpart to compare against. If you need symlink awareness, build a separate comparator rather than relying on this one to guess.

The `relative_path` uses OS-native separators (`\` on Windows, `/` elsewhere). Normalize yourself if you need cross-platform stable keys.
