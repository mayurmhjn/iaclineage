"""Read Terraform's installed-module manifest without fetching or executing code."""

from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath


def _source_identity(source: str) -> str:
    # Terraform expands public registry addresses in the manifest.
    return source.removeprefix("registry.terraform.io/")


class InstalledModules:
    """One root project's default .terraform module cache, read once per scan.

    The manifest is an internal Terraform format. Unknown or invalid records
    fail closed; diagnostics never include manifest contents or source URLs.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.records: dict[str, dict] = {}
        self.problem = ""
        manifest = root / ".terraform" / "modules" / "modules.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("Modules"), list):
                raise ValueError
            for record in data["Modules"]:
                if not isinstance(record, dict) or not all(
                    isinstance(record.get(key), str) for key in ("Key", "Source", "Dir")
                ) or record["Key"] in self.records:
                    raise ValueError
                self.records[record["Key"]] = record
        except FileNotFoundError:
            self.problem = "remote module is not downloaded; run terraform get in this project's root, then scan again"
        except (OSError, ValueError, UnicodeError):
            self.records.clear()
            self.problem = "installed module manifest is unreadable or invalid; refresh module downloads, then scan again"

    def resolve(self, address: str, source: str) -> tuple[Path | None, str]:
        if self.problem:
            return None, self.problem
        # Remove the independent-project namespace, then module path markers.
        key = ".".join(address.split("::")[-1].split(".")[1::2])
        record = self.records.get(key)
        if record is None:
            return None, "remote module has no installed manifest entry; refresh module downloads, then scan again"
        if _source_identity(record["Source"]) != _source_identity(source):
            return None, "installed module source does not match the call; refresh module downloads, then scan again"
        directory = record["Dir"]
        # Default Terraform manifests use root-relative paths. Reject absolute,
        # UNC and drive-relative paths before touching the indicated location.
        if not directory or Path(directory).is_absolute() or PureWindowsPath(directory).drive or directory.startswith(("/", "\\")):
            return None, "installed remote module path is outside the supported local module cache"
        try:
            cache = (self.root / ".terraform" / "modules").resolve()
            child = (self.root / directory).resolve()
            if not child.is_relative_to(cache):
                return None, "installed remote module path is outside the supported local module cache"
            if not child.is_dir() or not any(child.glob("*.tf")):
                return None, "installed remote module source files are missing; refresh module downloads, then scan again"
        except (OSError, ValueError, RuntimeError):
            return None, "installed remote module directory could not be read"
        return child, ""
