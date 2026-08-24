"""User-supplied inputs. Validated at the boundary so no node re-checks them."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from upgradepilot.models.enums import RiskLevel
from upgradepilot.models.evidence import NonBlankStr


class RemoteRepoRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["remote"] = "remote"
    url: NonBlankStr


class LocalRepoRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["local"] = "local"
    path: NonBlankStr


RepoRef = Annotated[RemoteRepoRef | LocalRepoRef, Field(discriminator="kind")]


class DependencySpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: NonBlankStr
    current_version: NonBlankStr
    target_version: NonBlankStr

    @model_validator(mode="after")
    def _versions_must_differ(self) -> Self:
        if self.current_version.strip() == self.target_version.strip():
            raise ValueError("current_version and target_version must differ")
        return self


class UserConstraints(BaseModel):
    """Migration constraints. Defaults are permissive so an omitted
    constraint never silently tightens the recommendation."""

    model_config = ConfigDict(frozen=True)

    zero_downtime: bool = False
    minimize_effort: bool = False
    deadline: date | None = None
    risk_tolerance: RiskLevel = RiskLevel.MEDIUM
