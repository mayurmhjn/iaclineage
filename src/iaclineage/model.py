"""Small, source-safe data structures for the beta index."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceRange:
    """A source span with portable display positions and exact byte offsets."""

    path: Path
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    start_byte: int
    end_byte: int

    def display(self) -> str:
        """Render a compact editor-friendly location."""
        return (
            f"{self.path.as_posix()}:{self.start_line}:{self.start_column}"
            f"-{self.end_line}:{self.end_column}"
        )


@dataclass(frozen=True)
class ConditionalRole:
    """One enclosing conditional and the role occupied by a reference."""

    conditional_range: SourceRange
    role: str


@dataclass(frozen=True)
class ConditionalPart:
    role: str
    source_range: SourceRange
    value: str | None = None


@dataclass(frozen=True)
class ConditionalExpression:
    source_range: SourceRange
    parts: tuple[ConditionalPart, ...]
    context: tuple[ConditionalRole, ...] = ()


@dataclass(frozen=True)
class ContextReference:
    """An unevaluated contextual traversal, separate from dependency edges."""

    traversal: str
    source_range: SourceRange


@dataclass(frozen=True)
class TerraformField:
    """A source-defined field or container; values are exported only by opt-in."""

    path: str
    kind: str
    source_range: SourceRange
    value: str | None = None
    children: tuple[TerraformField, ...] = ()
    conditionals: tuple[ConditionalExpression, ...] = ()
    provisioner_type: str | None = None
    context_references: tuple[ContextReference, ...] = ()


@dataclass(frozen=True)
class TerraformEntity:
    """A searchable Terraform declaration with optional source evidence."""

    kind: str
    address: str
    source_range: SourceRange
    source_text: str | None = None
    fields: tuple[TerraformField, ...] = ()

    def snippet(self) -> str:
        """Return a structural snippet that cannot reveal body values.

        The label(s) are taken from the trailing address segments so that
        namespaced child-module addresses (for example
        ``module.app.variable.name``) render the local declaration name rather
        than a module-path segment.

        Handles block kinds with zero, one, or two labels:
        - ``terraform`` (zero labels)
        - ``moved`` / ``import`` (zero labels, but synthetic address like moved.1)
        - ``resource`` / ``data`` (two labels: type and name)
        - ``var``, ``output``, ``module``, ``provider``, ``check``,
          ``provider_meta``, and individual ``local`` values (one label)
        """
        # Special zero‑label blocks: terraform, moved, import
        if self.kind == "terraform":
            return "terraform { ... }"
        if self.kind in {"moved", "import"}:
            return f"{self.kind} {{ ... }}"

        if self.kind == "provider":
            name = re.sub(
                r"^(?:module\.[^.]+\.)*provider\.", "", self.address.rpartition("::")[2]
            ).split(".")[0]
            return f'provider "{name}" {{ ... }}'

        parts = self.address.split(".")
        # Blocks with two labels: resource and data (use the two trailing labels)
        if self.kind in {"resource", "data"} and len(parts) >= 3:
            return f'{self.kind} "{parts[-2]}" "{parts[-1]}" {{ ... }}'
        # One‑label blocks and local values (use the trailing label)
        if len(parts) >= 2:
            return f'{self.kind} "{parts[-1]}" {{ ... }}'
        # Fallback for unexpected addresses
        return f"{self.address} {{ ... }}"


@dataclass(frozen=True)
class StaticReference:
    """An explicit Terraform traversal seen in one entity's expression."""

    source_address: str
    target_address: str
    source_range: SourceRange
    resolution: str
    usage: str | None = None
    source_field: str = ""
    target_field: str = ""
    traversal: str = ""
    kind: str = "reference"
    certainty: str = "direct"
    conditional_roles: tuple[ConditionalRole, ...] = ()


@dataclass(frozen=True)
class ParseDiagnostic:
    """A parser condition stated without retaining original source text."""

    code: str
    message: str
    source_range: SourceRange


@dataclass(frozen=True)
class RepositoryIndex:
    """The non-sensitive, in-memory output of indexing one repository."""

    entities: tuple[TerraformEntity, ...]
    references: tuple[StaticReference, ...]
    diagnostics: tuple[ParseDiagnostic, ...]
