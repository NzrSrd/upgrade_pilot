"""User-supplied inputs. Validated at the boundary so no node re-checks them."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import RiskLevel
from upgradepilot.models.evidence import NonBlankStr


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


class UserConstraints(HonestModel):
    """Migration constraints. Defaults are permissive so an omitted
    constraint never silently tightens the recommendation."""

    zero_downtime: bool = False
    minimize_effort: bool = False
    deadline: date | None = None
    risk_tolerance: RiskLevel = RiskLevel.MEDIUM
