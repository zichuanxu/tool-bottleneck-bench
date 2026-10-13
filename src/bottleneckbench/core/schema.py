"""Shared immutable schemas and content-addressing primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any


JSON = dict[str, Any]


class Track(StrEnum):
    """Independent benchmark tracks."""

    DISCOVERY = "discovery"
    COMPOSITION = "composition"
    DATA_READINESS = "data_readiness"


class Representation(StrEnum):
    """Query-independent connector output representations."""

    RAW = "raw"
    GENERIC_COMPACT = "generic_compact"
    TYPED = "typed"
    LINKED = "linked"


class RunStatus(StrEnum):
    """Mutually exclusive terminal run statuses."""

    VERIFIED_SUCCESS = "verified_success"
    AGENT_FAILURE = "agent_failure"
    BUDGET_FAILURE = "budget_failure"
    CONTEXT_INFEASIBLE = "context_infeasible"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    PROVIDER_FAILURE = "provider_failure"
    HARNESS_FAILURE = "harness_failure"
    VALIDATOR_UNCERTAIN = "validator_uncertain"


def canonical_json(value: object) -> str:
    """Serialize a JSON-compatible value deterministically without ASCII escaping."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def stable_hash(value: object) -> str:
    """Return the SHA-256 hash of a value's canonical UTF-8 JSON encoding."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_floats(value: object, *, digits: int = 12) -> object:
    """Round aggregate floats so canonical JSON is stable across Python runtimes."""

    if isinstance(value, float):
        rounded = round(value, digits)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, (list, tuple)):
        return [normalize_floats(item, digits=digits) for item in value]
    if isinstance(value, dict):
        return {
            key: normalize_floats(item, digits=digits)
            for key, item in value.items()
        }
    return value


@dataclass(frozen=True, slots=True)
class Condition:
    """A fully specified benchmark execution condition."""

    task_id: str
    track: Track
    model: str
    harness: str
    representation: Representation
    registry_size: int
    exposure_size: int
    seed: int

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id must not be empty")
        if not self.model:
            raise ValueError("model must not be empty")
        if not self.harness:
            raise ValueError("harness must not be empty")
        if self.registry_size <= 0:
            raise ValueError("registry_size must be positive")
        if self.exposure_size <= 0:
            raise ValueError("exposure_size must be positive")
        if self.exposure_size > self.registry_size:
            raise ValueError("exposure_size cannot exceed registry_size")

    def to_json(self) -> JSON:
        """Return a manifest-safe JSON object."""

        return {
            "task_id": self.task_id,
            "track": self.track.value,
            "model": self.model,
            "harness": self.harness,
            "representation": self.representation.value,
            "registry_size": self.registry_size,
            "exposure_size": self.exposure_size,
            "seed": self.seed,
        }

    @property
    def condition_id(self) -> str:
        """Bind every experimental field into a stable condition identifier."""

        return stable_hash(self.to_json())
