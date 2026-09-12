"""Prebuilt candidate identity and cross-platform checkout comparisons."""

import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest


spec = importlib.util.spec_from_file_location("wheel_validation", Path(__file__).parents[1] / "scripts/test_wheel.py")
wheel_validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wheel_validation)


@pytest.fixture
def candidate(tmp_path):
    wheel = tmp_path / "iaclineage-0.1.0b7-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("iaclineage/__init__.py", '__version__ = "0.1.0b7"\n')
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (tmp_path / "SHA256SUMS").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    return wheel


def test_supplied_wheel_preserves_candidate_bytes(candidate):
    before = candidate.read_bytes()
    assert wheel_validation.supplied_wheel(candidate.parent) == candidate.resolve()
    assert candidate.read_bytes() == before


@pytest.mark.parametrize("problem", ["changed-wheel", "missing-entry", "duplicate-entry", "missing-checksum"])
def test_invalid_candidate_identity_is_rejected(candidate, problem):
    checksum = candidate.parent / "SHA256SUMS"
    if problem == "changed-wheel":
        candidate.write_bytes(candidate.read_bytes() + b"changed")
    elif problem == "missing-entry":
        checksum.write_text("", encoding="utf-8")
    elif problem == "duplicate-entry":
        checksum.write_text(checksum.read_text(encoding="utf-8") * 2, encoding="utf-8")
    else:
        checksum.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        wheel_validation.supplied_wheel(candidate.parent)


def test_candidate_directory_must_have_exactly_one_wheel(candidate):
    second = candidate.with_name("other.whl")
    second.write_bytes(candidate.read_bytes())
    with pytest.raises(ValueError, match="exactly one"):
        wheel_validation.supplied_wheel(candidate.parent)
    second.unlink()
    candidate.unlink()
    with pytest.raises(ValueError, match="exactly one"):
        wheel_validation.supplied_wheel(candidate.parent)


def test_checkout_line_endings_may_differ_but_source_may_not(candidate, tmp_path):
    repo = tmp_path / "repo"
    source = repo / "src/iaclineage/__init__.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b'__version__ = "0.1.0b7"\r\n')
    wheel_validation.verify_package_sources(candidate, repo)
    source.write_bytes(b'__version__ = "different"\r\n')
    with pytest.raises(ValueError, match="differs from checkout"):
        wheel_validation.verify_package_sources(candidate, repo)


def test_missing_packaged_source_is_rejected(candidate, tmp_path):
    repo = tmp_path / "repo"
    source = repo / "src/iaclineage/missing.py"
    source.parent.mkdir(parents=True)
    source.write_text("", encoding="utf-8")
    with pytest.raises(KeyError):
        wheel_validation.verify_package_sources(candidate, repo)
