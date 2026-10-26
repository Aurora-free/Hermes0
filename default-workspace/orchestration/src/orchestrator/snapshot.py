from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

MAX_TEXT_BYTES = 400_000


@dataclass
class Manifest:
    root: str
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    skipped: List[str] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"root": self.root, "files": self.files, "skipped": self.skipped, "truncated": self.truncated}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan(
    root: Path,
    exclude_dirs: Sequence[str] = (),
    max_files: int = 3000,
    max_bytes: int = 80_000_000,
) -> Manifest:
    root = Path(root).resolve()
    excluded = set(exclude_dirs)
    manifest = Manifest(root=str(root))
    total = 0
    count = 0
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in excluded)
        for name in sorted(files):
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            try:
                stat = path.stat()
            except OSError:
                manifest.skipped.append(rel)
                continue
            if count >= max_files or total + stat.st_size > max_bytes:
                manifest.truncated = True
                manifest.skipped.append(rel)
                continue
            try:
                digest = _sha256(path)
            except OSError:
                manifest.skipped.append(rel)
                continue
            manifest.files[rel] = {"sha256": digest, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            count += 1
            total += stat.st_size
    return manifest


def save(root: Path, dest: Path, manifest: Manifest) -> None:
    root = Path(root).resolve()
    dest = Path(dest)
    files_dir = dest / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for rel in manifest.files:
        source = root / PurePosixPath(rel)
        target = files_dir / PurePosixPath(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
        except OSError:
            manifest.files[rel]["copy_failed"] = True
    (dest / "manifest.json").write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_manifest(path: Path) -> Manifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Manifest(root=data["root"], files=data.get("files", {}), skipped=data.get("skipped", []), truncated=data.get("truncated", False))


def diff_manifests(before: Manifest, after: Manifest) -> Dict[str, List[str]]:
    before_keys = set(before.files)
    after_keys = set(after.files)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    modified = sorted(
        rel for rel in (before_keys & after_keys)
        if before.files[rel]["sha256"] != after.files[rel]["sha256"]
    )
    return {"added": added, "removed": removed, "modified": modified}


def all_changed(diff: Dict[str, List[str]]) -> List[str]:
    return sorted(set(diff["added"]) | set(diff["removed"]) | set(diff["modified"]))


def scope_violations(changed: Iterable[str], root: Path, allowed_paths: Sequence[str], read_only: bool) -> List[str]:
    if read_only:
        return sorted(changed)
    allowed = [Path(item).resolve() for item in allowed_paths]
    root = Path(root).resolve()
    violations = []
    for rel in changed:
        candidate = (root / PurePosixPath(rel)).resolve()
        if not any(candidate == item or item in candidate.parents for item in allowed):
            violations.append(rel)
    return violations


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return "<file too large for textual diff: " + str(path.stat().st_size) + " bytes>\n"
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return "<unreadable: " + str(exc) + ">\n"


def make_patch(
    root: Path,
    before: Manifest,
    after: Manifest,
    before_snapshot_dir: Path,
    patch_path: Path,
) -> Dict[str, Any]:
    root = Path(root).resolve()
    files_dir = Path(before_snapshot_dir) / "files"
    diff = diff_manifests(before, after)
    chunks: List[str] = []
    for rel in diff["modified"]:
        old = _read_text(files_dir / PurePosixPath(rel))
        new = _read_text(root / PurePosixPath(rel))
        chunks.extend(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile="a/" + rel,
                tofile="b/" + rel,
            )
        )
    for rel in diff["added"]:
        new = _read_text(root / PurePosixPath(rel))
        chunks.extend(
            difflib.unified_diff([], new.splitlines(keepends=True), fromfile="/dev/null", tofile="b/" + rel)
        )
    for rel in diff["removed"]:
        old = _read_text(files_dir / PurePosixPath(rel))
        chunks.extend(
            difflib.unified_diff(old.splitlines(keepends=True), [], fromfile="a/" + rel, tofile="/dev/null")
        )
    patch_path = Path(patch_path)
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text("".join(chunks), encoding="utf-8", errors="replace")
    return {"diff": diff, "patch_path": str(patch_path), "patch_bytes": patch_path.stat().st_size}


def restore(root: Path, snapshot_dir: Path, manifest: Manifest, added: Sequence[str] = ()) -> List[str]:
    root = Path(root).resolve()
    files_dir = Path(snapshot_dir) / "files"
    actions: List[str] = []
    for rel in manifest.files:
        target = root / PurePosixPath(rel)
        source = files_dir / PurePosixPath(rel)
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        actions.append("restored " + rel)
    for rel in added:
        target = root / PurePosixPath(rel)
        if target.exists():
            target.unlink()
            actions.append("removed " + rel)
    return actions
