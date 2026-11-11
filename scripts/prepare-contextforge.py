#!/usr/bin/env python3
"""Prepare the optional, pinned ContextForge compatibility source outside Git."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path, PurePosixPath

PROJECT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT / "integrations/contextforge/manifest.json"
STAMP = ".agentport-prepared.json"
GENERATOR = "agentport.prepare-contextforge/v1"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def contained(root, value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or not path.parts:
        raise ValueError("Unsafe archive or stamp path")
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Archive or stamp path escapes the prepared directory")
    return target


def extract(archive, output, prefix):
    """Extract regular files first; permit only links to existing internal files."""
    links, seen = [], set()
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            name = PurePosixPath(member.name)
            if not name.parts or name.parts[0] != prefix:
                raise ValueError("Unexpected archive root")
            if len(name.parts) == 1 and member.isdir():
                continue
            relative = str(PurePosixPath(*name.parts[1:]))
            target = contained(output, relative)
            if relative in seen:
                raise ValueError("Duplicate archive path")
            seen.add(relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as content, target.open("xb") as destination:
                    shutil.copyfileobj(content, destination)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            elif member.issym() or member.islnk():
                links.append((target, member))
            else:
                raise ValueError("Special archive files are not supported")
        for target, member in links:
            link = PurePosixPath(member.linkname)
            if link.is_absolute() or "\\" in member.linkname:
                raise ValueError("Unsafe archive link")
            if member.islnk():
                if not link.parts or link.parts[0] != prefix:
                    raise ValueError("Hard link escapes archive root")
                resolved = contained(output, str(PurePosixPath(*link.parts[1:])))
            else:
                resolved = (target.parent / member.linkname).resolve()
            if not resolved.is_relative_to(output.resolve()) or not resolved.is_file() or resolved.is_symlink():
                raise ValueError("Archive links must refer to regular files inside the source tree")
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                target.symlink_to(os.path.relpath(resolved, target.parent))
            else:
                shutil.copyfile(resolved, target)


def file_digest(path):
    return "link:" + os.readlink(path) if path.is_symlink() else digest(path)


def prepare(archive=None, output=None, force=False):
    manifest = json.loads(MANIFEST.read_text())
    patch = contained(MANIFEST.parent, manifest["patch"]["path"])
    if digest(patch) != manifest["patch"]["sha256"]:
        raise RuntimeError("Compatibility patch differs from its pinned manifest hash")
    expected = {"generator": GENERATOR, "manifest_sha256": digest(MANIFEST), "patch_sha256": digest(patch)}
    output = Path(output or PROJECT / "upstream/contextforge").absolute()
    previous = None
    if output.exists():
        stamp = output / STAMP
        if output.is_symlink() or not stamp.is_file():
            raise RuntimeError("Existing source is not stamped as generated; move it aside first. No files changed.")
        previous = json.loads(stamp.read_text())
        if previous.get("generator") != GENERATOR:
            raise RuntimeError("Unknown source directory; refusing to replace it")
        if all(previous.get(key) == value for key, value in expected.items()):
            valid = bool(previous.get("files")) and all(
                (path := contained(output, name)).is_file() and file_digest(path) == value
                for name, value in previous["files"].items())
            if valid:
                print(f"Prepared ContextForge is unchanged: {output}")
                return output
        if not force:
            raise RuntimeError("Generated source changed; --force preserves the previous copy before refreshing it")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".contextforge-", dir=output.parent) as temporary:
        staging = Path(temporary)
        if archive:
            archive = Path(archive)
        else:
            archive = staging / "source.tar.gz"
            with urllib.request.urlopen(manifest["source"]["archive_url"], timeout=60) as response:
                with archive.open("xb") as destination:
                    shutil.copyfileobj(response, destination)
        if digest(archive) != manifest["source"]["sha256"]:
            raise RuntimeError("ContextForge archive SHA256 does not match the pinned source")
        source = staging / "source"
        source.mkdir()
        extract(archive, source, "mcp-context-forge-" + manifest["source"]["commit"])
        result = subprocess.run(["patch", "--batch", "--forward", "--fuzz=0",
                                 "-p" + str(manifest["patch"]["strip_components"]), "-i", str(patch)],
                                cwd=source, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError("Pinned compatibility patch did not apply: " + result.stdout[-2000:])
        files = {str(path.relative_to(source)): file_digest(path)
                 for path in sorted(source.rglob("*")) if path.is_file()}
        (source / STAMP).write_text(json.dumps({**expected, "files": files}, indent=2) + "\n")
        if previous:
            backup = output.with_name(output.name + ".previous-" + uuid.uuid4().hex[:8])
            output.rename(backup)
            print(f"Preserved previous generated source: {backup}")
        if output.exists():
            raise RuntimeError("Output directory appeared during preparation; refusing to overwrite it")
        source.rename(output)
    print(f"Prepared optional ContextForge compatibility source: {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use an existing pinned tar.gz instead of downloading")
    parser.add_argument("--output", type=Path, help="Generated source directory (default upstream/contextforge)")
    parser.add_argument("--force", action="store_true", help="Refresh stamped source, preserving its previous copy")
    args = parser.parse_args()
    prepare(args.archive, args.output, args.force)


if __name__ == "__main__":
    main()
