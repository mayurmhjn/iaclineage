"""Build and smoke-test an installed wheel outside the checkout on any OS."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def supplied_wheel(directory: Path) -> Path:
    """Select one immutable candidate and verify its recorded content hash."""
    wheels = list(directory.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("wheel directory must contain exactly one .whl file")
    wheel = wheels[0]
    records = [line.split() for line in (wheel.parent / "SHA256SUMS").read_text(encoding="utf-8").splitlines()]
    hashes = [record[0] for record in records if len(record) == 2 and record[1] == wheel.name]
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if hashes != [actual]:
        raise ValueError("wheel SHA256SUMS entry is missing, duplicated, or mismatched")
    return wheel


def verify_package_sources(wheel: Path, repo: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for source in (repo / "src" / "iaclineage").rglob("*"):
            if source.suffix in {".py", ".js", ".css", ".html"}:
                name = source.relative_to(repo / "src").as_posix()
                # Git may check out text as CRLF on Windows. The wheel's
                # actual bytes stay unchanged and are identified by SHA-256.
                if archive.read(name).replace(b"\r\n", b"\n") != source.read_bytes().replace(b"\r\n", b"\n"):
                    raise ValueError(f"wheel package source differs from checkout: {name}")
        package_roots = {name.split("/", 1)[0] for name in archive.namelist() if name.endswith(".py")}
        if package_roots != {"iaclineage"}:
            raise ValueError("wheel unexpectedly contains the retired compatibility package")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="also run installed-report browser checks")
    parser.add_argument("--wheel-dir", type=Path,
                        help="test one prebuilt wheel with SHA256SUMS instead of building")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    wheel = supplied_wheel(args.wheel_dir) if args.wheel_dir else None
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    with tempfile.TemporaryDirectory(prefix="iaclineage-wheel-") as temporary:
        root = Path(temporary).resolve()
        def run(*command: str | Path) -> None:
            subprocess.run([str(part) for part in command], cwd=root, env=environment, check=True)

        if wheel is None:
            source = root / "source"
            source.mkdir()
            for name in ("pyproject.toml", "README.md", "uv.lock", "LICENSE", "NOTICE"):
                shutil.copyfile(repo / name, source / name)
            shutil.copytree(repo / "src" / "iaclineage", source / "src" / "iaclineage")
            run("uv", "build", "--wheel", "--out-dir", root / "dist", source)
            wheel, = (root / "dist").glob("*.whl")
        print(f"Testing {wheel.name}; SHA-256: {hashlib.sha256(wheel.read_bytes()).hexdigest()}", flush=True)
        verify_package_sources(wheel, repo)
        run("uv", "venv", "--python", sys.executable, root / "env")
        binaries = root / "env" / ("Scripts" if os.name == "nt" else "bin")
        python = binaries / ("python.exe" if os.name == "nt" else "python")
        cli = binaries / ("iaclineage.exe" if os.name == "nt" else "iaclineage")
        run("uv", "pip", "install", "--python", python, wheel)
        run(python, "-I", "-c", """
import importlib, sys
from importlib.metadata import version
from pathlib import Path
module = importlib.import_module('iaclineage')
assert module.__version__ == version('iaclineage')
for name in ('cli', 'diagnostics', 'hcl_source', 'installed_modules', 'model', 'report', 'scan'):
    loaded = importlib.import_module('iaclineage.' + name)
    assert Path(loaded.__file__).is_relative_to(Path(sys.prefix))
print('Canonical imports resolve inside the wheel environment.')
""")
        run(cli, "--version")
        fixture = repo / "tests" / "fixtures" / "references"
        run(cli, "scan", fixture)
        for fmt in ("text", "json"):
            run(cli, "find", fixture, "api", "--format", fmt)
        for fmt in ("html", "json", "interactive-html"):
            run(cli, "report", fixture, "--format", fmt, "--output", root / (fmt + ".html"))
        run(cli, "report", fixture, "--format", "interactive-html", "--include-source-values",
            "--output", root / "opted.html")
        if args.browser:
            environment["IACLINEAGE_CLI"] = str(cli)
            run("node", repo / "scripts" / "test_browser.cjs")
    print("Installed-wheel checks passed.")


if __name__ == "__main__":
    main()
