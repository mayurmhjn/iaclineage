"""Tester kits preserve candidate bytes and include only reviewed inputs."""

import hashlib
import importlib
import json
import zipfile

import pytest


@pytest.fixture
def kit_inputs(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    builder = importlib.import_module("build_beta_kit")
    monkeypatch.setattr(builder, "git_value", lambda repo, *args: "" if args[0] == "status" else "a" * 40)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nversion = "0.1.0b1"\n', encoding="utf-8")
    for path in builder.KIT_FILES.values():
        destination = repo / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("reviewed fixture\n", encoding="utf-8")
    (repo / "docs/tester-guide.md").write_text(
        '# IaCLineage beta 0.1.0b1\n[Platform](platform-support.md)\n', encoding="utf-8")
    (repo / "docs/platform-support.md").write_text(
        '[Guide](tester-guide.md)\n[Workflow](../.github/workflows/compatibility.yml)\n', encoding="utf-8")
    source = repo / "src/iaclineage/__init__.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b'__version__ = "0.1.0b1"\r\n')
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    wheel = wheel_dir / "iaclineage-0.1.0b1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("iaclineage/__init__.py", source.read_bytes().replace(b"\r\n", b"\n"))
        archive.writestr("iaclineage-0.1.0b1.dist-info/METADATA", 'Name: iaclineage\nVersion: 0.1.0b1\n')
    (wheel_dir / "SHA256SUMS").write_text(f"{builder.sha256(wheel.read_bytes())}  {wheel.name}\n", encoding="utf-8")
    return builder, repo, wheel_dir, wheel


def test_kit_inventory_hashes_provenance_and_wheel_are_preserved(kit_inputs, tmp_path):
    builder, repo, wheel_dir, wheel = kit_inputs
    (repo / "private-report.html").write_text("DO_NOT_PACKAGE", encoding="utf-8")
    (wheel_dir / "secret.txt").write_text("DO_NOT_PACKAGE", encoding="utf-8")
    before = wheel.read_bytes()
    kit = builder.build_kit(repo, wheel_dir, tmp_path / "out", ci_run_url="https://example.test/runs/1")
    with zipfile.ZipFile(kit) as archive:
        assert set(archive.namelist()) == set(builder.KIT_FILES) | {wheel.name, "manifest.json", "SHA256SUMS"}
        assert archive.read(wheel.name) == before == wheel.read_bytes()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["source_revision"] == "a" * 40
        assert manifest["source_dirty"] is False
        assert manifest["ci_run_url"] == "https://example.test/runs/1"
        assert manifest["wheel"]["sha256"] == hashlib.sha256(before).hexdigest()
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        for line in archive.read("SHA256SUMS").decode().splitlines():
            digest, name = line.split()
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        assert b"(PLATFORM-SUPPORT.md)" in archive.read("TESTER-GUIDE.md")
        assert b"(TESTER-GUIDE.md)" in archive.read("PLATFORM-SUPPORT.md")
        assert b"(compatibility.yml)" in archive.read("PLATFORM-SUPPORT.md")
    assert (kit.parent / "SHA256SUMS").read_text().split()[0] == hashlib.sha256(kit.read_bytes()).hexdigest()


def test_repeated_packaging_is_identical_and_existing_kit_is_not_overwritten(kit_inputs, tmp_path):
    builder, repo, wheel_dir, _ = kit_inputs
    kit = builder.build_kit(repo, wheel_dir, tmp_path / "first")
    before = kit.read_bytes()
    second = builder.build_kit(repo, wheel_dir, tmp_path / "second")
    assert second.read_bytes() == before
    with pytest.raises(FileExistsError):
        builder.build_kit(repo, wheel_dir, kit.parent)
    assert kit.read_bytes() == before


@pytest.mark.parametrize("problem", ["checksum", "source", "version", "guide-version", "missing-guide"])
def test_invalid_inputs_fail_before_creating_output(kit_inputs, tmp_path, problem):
    builder, repo, wheel_dir, wheel = kit_inputs
    if problem == "checksum":
        wheel.write_bytes(wheel.read_bytes() + b"changed")
    elif problem == "source":
        (repo / "src/iaclineage/__init__.py").write_text("changed", encoding="utf-8")
    elif problem == "version":
        (repo / "pyproject.toml").write_text('[project]\nversion = "0.1.0b8"\n', encoding="utf-8")
    elif problem == "guide-version":
        (repo / "docs/tester-guide.md").write_text("Old guide", encoding="utf-8")
    else:
        (repo / "docs/tester-guide.md").unlink()
    output = tmp_path / "out"
    with pytest.raises((ValueError, FileNotFoundError)):
        builder.build_kit(repo, wheel_dir, output)
    assert not output.exists()
