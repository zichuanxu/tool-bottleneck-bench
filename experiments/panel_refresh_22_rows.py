"""Read literal, certificate-bound new-panel rows without mutating evidence.

This is an offline analysis adapter, not an admission or publication gate. The
caller must first obtain ``verified_sources`` from the schema-specific model
certificate validator and use the anchored source map. The result retains exact
JSONL bytes and source-line provenance; it does not rewrite or rescore rows.
No files are written, no network calls or acquisition-runner imports are used.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from experiments import build_panel_refresh_22 as selection


@dataclass(frozen=True)
class SelectedBlock:
    rows: tuple[dict, ...]
    literal_lines: tuple[bytes, ...]
    provenance: dict

    @property
    def jsonl_bytes(self) -> bytes:
        """Exact selected lines, in frozen-plan order, with no reserialization."""
        return b"".join(self.literal_lines)


def _require(value: bool, message: str) -> None:
    selection.require(value, message)


def _json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate JSON field")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    _require(isinstance(result, dict), "JSON root must be an object")
    return result


def _binding_map(bindings: list[dict]) -> dict[str, str]:
    result = {}
    _require(isinstance(bindings, list) and bool(bindings), "verified source bindings required")
    for item in bindings:
        path, digest = item.get("path"), item.get("sha256")
        _require(isinstance(path, str) and isinstance(digest, str) and len(digest) == 64
                 and all(c in "0123456789abcdef" for c in digest), "malformed source binding")
        _require(path not in result or result[path] == digest, "conflicting source bindings")
        result[path] = digest
    return result


def _read(root: Path, name: str, bindings: dict[str, str]) -> bytes:
    _require(name in bindings, f"source is not certificate-bound: {name}")
    raw = selection.resolve(root, name).read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == bindings[name], f"source bytes changed: {name}")
    return raw


def _lines(raw: bytes) -> tuple[bytes, ...]:
    # A missing newline may be an unfinished append. It must not be repaired by
    # the adapter, even if a JSON decoder would accept that last object.
    _require(bool(raw) and raw.endswith(b"\n"), "journal lacks completed newline")
    lines = tuple(raw.splitlines(keepends=True))
    _require(all(line.strip() for line in lines), "blank journal line")
    return lines


def _row_matches(row: dict, condition: dict, plan_hash: str, block: str) -> None:
    model = condition["model"]
    _require(row.get("condition_id") == selection.condition_id(condition)
             and row.get("plan_hash") == plan_hash, "row condition or plan identity differs")
    if block == "toolsandbox":
        fields = set(condition) - {"design", "upstream_commit"}
        _require(fields <= set(row), "ToolSandbox condition projection is incomplete")
        _require(selection.canonical({k: row[k] for k in fields}) ==
                 selection.canonical({k: condition[k] for k in fields}),
                 "ToolSandbox condition fields differ")
        _require(row.get("schema") == "candidate-toolsandbox-matched-menu-row-v1",
                 "unexpected ToolSandbox row schema")
    else:
        _require(selection.canonical(row.get("condition")) == selection.canonical(condition),
                 "typed condition differs")
    if "model" in row:
        _require(row["model"] == model, "conflicting top-level row model")
    if block in {"structural", "toolsandbox"}:
        _require(type(row.get("confirmatory_weight")) is int and row["confirmatory_weight"] == 0,
                 "descriptive row weight changed")
    # Do not select rows by correctness or status. A valid independently
    # adjudicated model-error zero must survive literal selection unchanged.


def read_selected_block(root: Path, source: dict, *, verified_sources: list[dict]) -> SelectedBlock:
    """Project one complete block from explicitly bound whole/v5 source bytes.

    ``verified_sources`` is evidence supplied by the independent certificate
    adapter, not an assertion that this function itself performed that audit.
    All results remain ``release_admitted=False``.
    """
    bindings = _binding_map(verified_sources)
    model, block = source["model"], source["block"]
    _require(model in selection.NEW_MODELS and block in selection.BLOCKS,
             "unsupported selected model/block")
    count = selection.BLOCKS[block]
    _require(type(source["planned_conditions"]) is int and source["planned_conditions"] == count,
             "block denominator differs")
    plan_binding = source["plan"]
    _require(bindings.get(plan_binding["path"]) == plan_binding["sha256"],
             "plan is not bound by the independent certificate")
    plan = _json(_read(root, plan_binding["path"], bindings))
    plan_body = dict(plan)
    plan_hash = plan_body.pop("plan_hash", None)
    computed = selection.stable_hash(plan_body)
    _require(source["plan_hash"] == computed and plan_hash in (None, computed), "plan hash differs")
    conditions = plan["conditions"]
    _require(len(conditions) == count and all(c.get("model") == model for c in conditions),
             "frozen plan membership differs")
    ids = [selection.condition_id(c) for c in conditions]
    _require(len(set(ids)) == count and selection.stable_hash(sorted(ids)) == source["condition_ids_sha256"],
             "frozen condition inventory differs")
    row_selection = source["row_selection"]
    picked = []
    journals = {}
    if row_selection["mode"] == "whole_frozen_output":
        name = row_selection["path"]
        lines = _lines(_read(root, name, bindings))
        _require(len(lines) == count, "whole output is partial or has extra rows")
        journals[name] = bindings[name]
        picked = [(name, index, line, _json(line), None) for index, line in enumerate(lines)]
    elif row_selection["mode"] == "literal_v5_selection":
        _require(block == "toolsandbox" and model in {selection.QWEN, selection.GEMINI},
                 "overlay selection is not authorized for this block")
        expected_dispositions = ({"original_complete": 68, "new_complete": 27,
                                  "preserved_prefix_continuation": 1} if model == selection.QWEN else
                                 {"original_complete": 24, "new_complete": 71,
                                  "model_unknown_tool_zero_score": 1})
        _require(Counter(r.get("disposition") for r in row_selection["selected_rows"]) == expected_dispositions,
                 "overlay disposition inventory differs")
        _require(row_selection.get("timing_policy") ==
                 ("segmented_not_uninterrupted_latency" if model == selection.QWEN else "unchanged"),
                 "overlay timing limitation is missing or changed")
        _require(row_selection.get("model_error_policy") ==
                 ("preserve_independently_adjudicated_unknown_tool_zero" if model == selection.GEMINI else "unchanged"),
                 "overlay model-error policy differs")
        for entry in row_selection["journals"]:
            name = entry["path"]
            _require(name not in journals and bindings.get(name) == entry["sha256"],
                     "overlay journal binding differs")
            journals[name] = entry["sha256"]
        lines = {name: _lines(_read(root, name, bindings)) for name in journals}
        source_indices = set()
        for record in row_selection["selected_rows"]:
            name, index = record["journal"], record["row_index"]
            _require(record.get("block") == block and name in lines and type(index) is int
                     and 0 <= index < len(lines[name]) and (name, index) not in source_indices,
                     "invalid or duplicate selected source line")
            line = lines[name][index]
            row = _json(line)
            _require(record["literal_line_sha256"] == hashlib.sha256(line).hexdigest()
                     and record["row_sha256"] == selection.stable_hash(row)
                     and record["condition_id"] == row.get("condition_id"),
                     "literal selected row differs")
            picked.append((name, index, line, row, record.get("disposition")))
            source_indices.add((name, index))
        _require(len(picked) == count, "selected overlay block is incomplete")
        _require(set(name for name, *_ in picked) == set(journals), "unused overlay source")
    else:
        raise ValueError("unsupported row selection mode")

    by_id = {}
    for name, index, line, row, disposition in picked:
        identity = row.get("condition_id")
        _require(identity in ids and identity not in by_id, "foreign or duplicate selected condition")
        by_id[identity] = (name, index, line, row, disposition)
    _require(set(by_id) == set(ids), "selected condition coverage is incomplete")
    rows, literals, provenance_rows = [], [], []
    for condition, identity in zip(conditions, ids):
        name, index, line, row, disposition = by_id[identity]
        _row_matches(row, condition, computed, block)
        rows.append(row)
        literals.append(line)
        provenance_rows.append({"condition_id": identity, "journal": name, "row_index": index,
            "literal_line_sha256": hashlib.sha256(line).hexdigest(),
            "row_sha256": selection.stable_hash(row), "disposition": disposition,
            "duration_scope": "continuation_segment_only" if disposition == "preserved_prefix_continuation" else "unchanged"})
    result = {"schema": "panel-refresh-22-literal-block-projection-v1", "release_admitted": False,
        "model": model, "block": block, "configuration_key": source["configuration_key"],
        "plan": plan_binding, "plan_hash": computed, "rows": count,
        "row_order": "frozen_plan_order", "source_row_bytes_modified": False,
        "projected_jsonl_sha256": hashlib.sha256(b"".join(literals)).hexdigest(),
        "source_journals": [{"path": name, "sha256": digest} for name, digest in sorted(journals.items())],
        "selected_rows": provenance_rows, "source_counts": dict(Counter(item[0] for item in picked)),
        "timing_policy": row_selection.get("timing_policy", "unchanged"),
        "model_error_policy": row_selection.get("model_error_policy", "unchanged")}
    result["artifact_hash"] = selection.stable_hash(result)
    return SelectedBlock(tuple(rows), tuple(literals), result)


def external_plan_metadata(root: Path, source: dict, *, manifest_binding: dict,
                           verified_sources: list[dict]) -> dict:
    """Expose actual freeze provenance without inventing plan slugs/timestamps.

    Structural/ToolSandbox single-model plans omit legacy ``slug`` and
    ``frozen_at``. The manifest's exact plan binding is kept as separate
    provenance; ``prepared_at_utc`` is never relabelled as a plan freeze time.
    """
    _require(source["block"] in {"structural", "toolsandbox"}, "not an external-plan block")
    bindings = _binding_map(verified_sources)
    for binding in (source["plan"], manifest_binding):
        _require(bindings.get(binding["path"]) == binding["sha256"], "freeze source binding differs")
    manifest = _json(_read(root, manifest_binding["path"], bindings))
    selection.verify_self_hash(manifest, "manifest_hash")
    plan = _json(_read(root, source["plan"]["path"], bindings))
    body = dict(plan)
    _require(body.pop("plan_hash", None) == source["plan_hash"] == selection.stable_hash(body),
             "external plan hash differs")
    entries = [e for e in manifest["plans"] if e.get("path") == source["plan"]["path"]]
    _require(len(entries) == 1, "external plan missing or duplicated in freeze manifest")
    entry = entries[0]
    _require(entry["sha256"] == source["plan"]["sha256"] and entry["plan_hash"] == source["plan_hash"]
             and entry["model"] == source["model"] and entry["block"] == source["block"]
             and type(entry["conditions"]) is int and entry["conditions"] == source["planned_conditions"],
             "external manifest plan binding differs")
    _require(type(plan.get("confirmatory_weight")) is int and plan["confirmatory_weight"] == 0,
             "external plan weight differs")
    return {"schema": "panel-refresh-22-external-plan-provenance-v1", "release_admitted": False,
        "plan": source["plan"], "plan_hash": source["plan_hash"], "model": source["model"],
        "block": source["block"], "manifest": manifest_binding,
        "plan_frozen_at": plan.get("frozen_at"), "manifest_prepared_at_utc": manifest.get("prepared_at_utc"),
        "frozen_plan_rewritten": False, "row_locator": source["row_selection"],
        "confirmatory_weight": 0,
        "outcome_access_before_freeze": plan.get("structural_outcomes_access_before_freeze",
                                              plan.get("outcome_access_before_freeze")),
        "core_outcome_selection": plan.get("candidate_core_outcomes_inspected_before_freeze")}
