"""Pinned preparation rejects unsafe archives and preserves operator files."""

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("prepare_contextforge", Path(__file__).parents[1] / "scripts/prepare-contextforge.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


@pytest.fixture
def source(tmp_path, monkeypatch):
    commit = "1" * 40
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as target:
        member = tarfile.TarInfo("mcp-context-forge-" + commit + "/hello.txt")
        member.size = 7
        target.addfile(member, io.BytesIO(b"before\n"))
        link = tarfile.TarInfo("mcp-context-forge-" + commit + "/alias.txt")
        link.type, link.linkname = tarfile.SYMTYPE, "hello.txt"
        target.addfile(link)
    patch = tmp_path / "compatibility.patch"
    patch.write_text("--- a/upstream/contextforge/hello.txt\n+++ b/upstream/contextforge/hello.txt\n"
                     "@@ -1 +1 @@\n-before\n+after\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source": {"commit": commit, "sha256": prepare.digest(archive)},
        "patch": {"path": patch.name, "sha256": prepare.digest(patch), "strip_components": 3}}))
    monkeypatch.setattr(prepare, "MANIFEST", manifest)
    return archive, tmp_path / "generated", patch


def test_preparation_applies_verified_patch_and_is_idempotent(source):
    archive, output, _ = source
    assert prepare.prepare(archive, output) == output
    assert (output / "hello.txt").read_text() == "after\n"
    assert (output / "alias.txt").is_symlink() and (output / "alias.txt").read_text() == "after\n"
    stamp = (output / prepare.STAMP).read_bytes()
    assert prepare.prepare(archive, output) == output
    assert (output / prepare.STAMP).read_bytes() == stamp


def test_force_refresh_preserves_modified_generated_source(source):
    archive, output, _ = source
    prepare.prepare(archive, output)
    (output / "hello.txt").write_text("operator edit")
    (output / "operator.txt").write_text("preserved")
    with pytest.raises(RuntimeError, match="--force"):
        prepare.prepare(archive, output)
    prepare.prepare(archive, output, force=True)
    previous = list(output.parent.glob(output.name + ".previous-*"))
    assert len(previous) == 1 and (previous[0] / "hello.txt").read_text() == "operator edit"
    assert (previous[0] / "operator.txt").read_text() == "preserved"
    assert (output / "hello.txt").read_text() == "after\n"


def test_unknown_source_is_preserved_even_with_force(source):
    archive, output, _ = source
    output.mkdir()
    (output / "operator.txt").write_text("preserved")
    with pytest.raises(RuntimeError, match="move it aside"):
        prepare.prepare(archive, output, force=True)
    assert (output / "operator.txt").read_text() == "preserved"


@pytest.mark.parametrize("which", ["archive", "patch"])
def test_hash_mismatch_prevents_preparation(source, which):
    archive, output, patch = source
    with (archive if which == "archive" else patch).open("ab") as changed:
        changed.write(b"unexpected")
    with pytest.raises(RuntimeError, match="SHA256|patch differs"):
        prepare.prepare(archive, output)
    assert not output.exists()


@pytest.mark.parametrize("name,kind,link", [
    ("root/../../escape", tarfile.REGTYPE, None),
    ("root/dir/../escape", tarfile.REGTYPE, None),
    ("root/dir\\escape", tarfile.REGTYPE, None),
    ("/root/escape", tarfile.REGTYPE, None),
    ("other/file", tarfile.REGTYPE, None),
    ("root/link", tarfile.SYMTYPE, "../../escape"),
    ("root/link", tarfile.SYMTYPE, "/escape"),
    ("root/link", tarfile.LNKTYPE, "other/file"),
])
def test_unsafe_archive_members_are_rejected(tmp_path, name, kind, link):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as target:
        member = tarfile.TarInfo(name)
        member.type = kind
        if link:
            member.linkname = link
        else:
            member.size = 1
        target.addfile(member, None if link else io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        prepare.extract(archive, tmp_path / "output", "root")
    assert not (tmp_path / "escape").exists()


def test_repository_manifest_pins_source_and_compatibility_patch():
    manifest_path = Path(__file__).parents[1] / "integrations/contextforge/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["source"]["commit"] == "aee91ff6db63b6aafa77522684a9483c418bb2a3"
    assert manifest["source"]["sha256"] == "1b7bf8f3c12621b7d2ac5a36263e0fc767c5b150965b0de8c5975c8e2a1633a5"
    assert manifest["patch"]["sha256"] == prepare.digest(manifest_path.parent / manifest["patch"]["path"])
    assert manifest["compatibility"]["stock_plugin"] is False
