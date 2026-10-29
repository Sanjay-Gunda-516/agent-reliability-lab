"""Named agent versions: the unit that regression testing compares.

A version is a fixed AgentConfig (model, effort, prompt version, limits). Versions are
defined here and recorded in `agent_versions` with a hash of the config and the prompt
texts it uses, so a stored run can always be traced to exactly what it ran. Changing what
an existing name means is refused: give the new behaviour a new version name instead.
"""

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.config import AgentConfig
from app.agent.prompts import INTENT_PROMPT, SYSTEM_PROMPTS
from app.db.lab_models import AgentVersionRecord


@dataclass(frozen=True)
class AgentVersion:
    name: str
    description: str
    config: AgentConfig

    def config_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self.config)

    def config_hash(self) -> str:
        return config_hash(self.config_dict())


DEFAULT_AGENT_VERSION = "v1"

AGENT_VERSIONS: dict[str, AgentVersion] = {
    v.name: v
    for v in (
        AgentVersion("v1", "Claude Opus 5, prompt v1.", AgentConfig()),
        AgentVersion(
            "v1-regressed",
            "Demo candidate with a deliberately weaker prompt; expected to regress.",
            AgentConfig(prompt_version="v1-regressed"),
        ),
    )
}


class UnknownAgentVersion(LookupError):
    pass


class AgentVersionConflict(ValueError):
    """A version name already recorded with a different config or prompt."""


def config_hash(config: dict[str, Any]) -> str:
    """Hash of the config plus the prompt texts it selects."""
    prompt = SYSTEM_PROMPTS.get(str(config.get("prompt_version")))
    payload = {"config": config, "system_prompt": prompt, "intent_prompt": INTENT_PROMPT}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def get_version(name: str) -> AgentVersion:
    try:
        return AGENT_VERSIONS[name]
    except KeyError:
        known = ", ".join(sorted(AGENT_VERSIONS))
        raise UnknownAgentVersion(f"unknown agent version {name!r} (known: {known})") from None


def sync_agent_versions(db: Session) -> list[AgentVersionRecord]:
    """Record the versions defined in code; refuse silent changes to existing ones."""
    stored = {r.name: r for r in db.scalars(select(AgentVersionRecord))}
    for version in AGENT_VERSIONS.values():
        record = stored.get(version.name)
        digest = version.config_hash()
        if record is None:
            record = AgentVersionRecord(
                name=version.name,
                description=version.description,
                config=version.config_dict(),
                config_hash=digest,
                model=version.config.model,
                prompt_version=version.config.prompt_version,
                defined_in_code=True,
            )
            db.add(record)
            stored[version.name] = record
        elif record.config_hash != digest and not record.defined_in_code and (
            record.config == version.config_dict()
        ):
            # Backfilled by the migration from old runs: its hash couldn't include the
            # prompt texts, but the config is identical, so it's this version.
            record.config_hash = digest
            record.defined_in_code = True
        elif record.config_hash != digest:
            raise AgentVersionConflict(
                f"agent version {version.name!r} is already recorded with a different config "
                "or prompt; give the changed agent a new version name"
            )
        else:
            record.description = version.description
            record.defined_in_code = True
    for name, record in stored.items():
        if name not in AGENT_VERSIONS:
            record.defined_in_code = False
    db.flush()
    return sorted(stored.values(), key=lambda r: r.name)
