"""User-supplied inputs. Validated at the boundary so no node re-checks them."""

import re
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, computed_field, model_validator

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import RiskLevel
from upgradepilot.models.evidence import NonBlankStr

_PEP503_SEPARATORS = re.compile(r"[-_.]+")


def canonicalize_name(name: str) -> str:
    """PEP 503 normalisation. The corpus's exact-match key.

    `PLANNING.md` carried this in with the reason that matters: the corpus
    is filtered with Chroma's `$contains`, which is exact-element, so a
    document ingested under `pydantic` is invisible to a query for
    `Pydantic` or `py_dantic`. Normalising at the boundary means every
    producer and every consumer agrees without either remembering to.

    Public and singular on purpose: `services/analysis/manifests.py` needs
    this exact transform to match manifest entries against the corpus's key,
    and `services/` is permitted to import `models/` (CLAUDE.md rule 16).
    A second, private copy in `manifests.py` was the earlier shape and nothing
    kept the two in agreement -- moving the one implementation here means
    there is nothing left to drift.
    """
    return _PEP503_SEPARATORS.sub("-", name.strip()).lower()


class RemoteRepoRef(HonestModel):
    kind: Literal["remote"] = "remote"
    url: NonBlankStr


class LocalRepoRef(HonestModel):
    kind: Literal["local"] = "local"
    path: NonBlankStr


RepoRef = Annotated[RemoteRepoRef | LocalRepoRef, Field(discriminator="kind")]


class DependencySpec(HonestModel):
    name: NonBlankStr
    current_version: NonBlankStr
    target_version: NonBlankStr

    @model_validator(mode="after")
    def _versions_must_differ(self) -> Self:
        if self.current_version.strip() == self.target_version.strip():
            raise ValueError("current_version and target_version must differ")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def canonical_name(self) -> str:
        """PEP 503 normalised name. See `canonicalize_name` above for why.

        Derived, never stored (CLAUDE.md rule 21): a stored copy could
        disagree with `name`.
        """
        return canonicalize_name(self.name)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def import_root(self) -> str:
        """The top-level module name this distribution is expected to provide.

        A GUESS, and the honest name for it is a guess: the mapping from
        distribution name to import name lives in installed package metadata,
        which a static analysis of a cloned repository does not have.
        `pydantic` -> `pydantic` is right; `python-dateutil` -> `dateutil` and
        `PyYAML` -> `yaml` are the well-known cases where it is wrong.

        `services/analysis/analyzer.py` records an explicit confidence reducer
        whenever this guess yields no candidate files, so a wrong guess reads
        as "we could not find it" rather than as "this dependency is unused".
        """
        return self.canonical_name.replace("-", "_")

    @property
    def target_major(self) -> int | None:
        """The leading integer of `target_version`, or `None` when it has none.

        This is the scalar Chroma narrows on (`to_version_major`), so getting
        it wrong is not a cosmetic error: a query filtered to the wrong major
        returns documents about a different release, and every citation built
        from them resolves to a real document that describes the wrong
        upgrade.

        A plain property rather than a `@computed_field`: it is an internal
        retrieval detail, and adding it to the serialised shape would put it
        in the API contract Phase 9 publishes, where nothing needs it.

        Only the leading component is read, and only its leading digits, so
        ordinary release spellings work (`2.9.0`, `2.0b1`, `2`). Anything
        else -- a target of `latest`, or a date-based scheme -- yields `None`,
        and `plan_retrieval` then omits the filter rather than guessing a
        major. An omitted filter retrieves more broadly, which is recoverable;
        a guessed one retrieves confidently from the wrong release, which is
        not.

        Mirrors `CorpusDocument._major_must_agree_with_the_version_string`,
        which parses the corpus side of the same comparison. The two must
        agree on what "the major" means or the filter silently matches
        nothing.
        """
        leading = self.target_version.split(".", 1)[0]
        digits = ""
        for character in leading:
            if not character.isdigit():
                break
            digits += character
        return int(digits) if digits else None


class UserConstraints(HonestModel):
    """Migration constraints. Defaults are permissive so an omitted
    constraint never silently tightens the recommendation."""

    zero_downtime: bool = False
    minimize_effort: bool = False
    deadline: date | None = None
    risk_tolerance: RiskLevel = RiskLevel.MEDIUM
