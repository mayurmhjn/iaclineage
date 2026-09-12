"""Package a supplied candidate wheel and reviewed tester files without rebuilding."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import zipfile

from test_wheel import supplied_wheel, verify_package_sources


# An explicit inventory prevents accidental inclusion of local reports or repos.
KIT_FILES = {
    "TESTER-GUIDE.md": "docs/tester-guide.md",
    "PLATFORM-SUPPORT.md": "docs/platform-support.md",
    "example/main.tf": "tests/fixtures/ui_details/main.tf",
    "nested-blocks/main.tf": "tests/fixtures/nested_blocks/main.tf",
    "lineage-regressions/main.tf": "tests/fixtures/lineage_regressions/main.tf",
    "compatibility.yml": ".github/workflows/compatibility.yml",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_value(repo: Path, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def atomic_write(path: Path, content: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_kit(repo: Path, wheel_dir: Path, output: Path, *, ci_run_url: str | None = None) -> Path:
    repo = repo.resolve()
    wheel = supplied_wheel(wheel_dir)
    verify_package_sources(wheel, repo)
    version = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    wheel_bytes = wheel.read_bytes()
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
        metadata_paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise ValueError("wheel must contain exactly one package metadata file")
        metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
        if metadata["Name"] != "iaclineage" or metadata["Version"] != version:
            raise ValueError("wheel identity differs from checkout")

    files = {name: (repo / source).read_text(encoding="utf-8").encode("utf-8")
             for name, source in KIT_FILES.items()}
    if not files["TESTER-GUIDE.md"].startswith(f"# IaCLineage beta {version}\n".encode()):
        raise ValueError("tester guide version differs from wheel")
    files["TESTER-GUIDE.md"] = files["TESTER-GUIDE.md"].replace(b"(platform-support.md)", b"(PLATFORM-SUPPORT.md)")
    files["PLATFORM-SUPPORT.md"] = files["PLATFORM-SUPPORT.md"].replace(
        b"(tester-guide.md)", b"(TESTER-GUIDE.md)").replace(
        b"(../.github/workflows/compatibility.yml)", b"(compatibility.yml)")
    files[wheel.name] = wheel_bytes
    status = git_value(repo, "status", "--porcelain")
    manifest = {
        "schema_version": 1,
        "version": version,
        "source_revision": git_value(repo, "rev-parse", "HEAD"),
        "source_dirty": None if status is None else bool(status),
        "ci_run_url": ci_run_url,
        "wheel": {"filename": wheel.name, "sha256": sha256(wheel_bytes)},
        "files": {name: sha256(content) for name, content in sorted(files.items())},
        "verification": "Packaging checks integrity and source match; consult the recorded CI run and pilot evidence for test results.",
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    files["SHA256SUMS"] = "".join(f"{sha256(content)}  {name}\n" for name, content in sorted(files.items())).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3  # Stable permissions/header on Windows and POSIX.
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    content = buffer.getvalue()
    # Create only a fresh destination, preserving every existing candidate.
    output.mkdir(parents=True, exist_ok=False)
    kit = output / "iaclineage-beta-kit.zip"
    atomic_write(kit, content)
    atomic_write(output / "SHA256SUMS", f"{sha256(content)}  {kit.name}\n".encode())
    return kit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path, help="new directory; existing candidates are never overwritten")
    parser.add_argument("--ci-run-url", help="provenance link, not an assertion that CI passed")
    args = parser.parse_args()
    kit = build_kit(Path(__file__).resolve().parents[1], args.wheel_dir, args.output_dir,
                    ci_run_url=args.ci_run_url)
    print(f"Created {kit}")
    print(f"SHA-256: {sha256(kit.read_bytes())}")


if __name__ == "__main__":
    main()
