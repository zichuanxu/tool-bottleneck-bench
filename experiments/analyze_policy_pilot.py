"""Exploratory LOIO action selection; refuses an incomplete matched inventory.

Utility is success first, cost only for exact training ties. No cognitive-cause
or confirmatory claim. A deployment policy never receives a grading result.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from policy_pilot import CAMPAIGN, PLAN, ACTIONS, atomic, read

CHOICES = ("retain", *ACTIONS)
META = ("menu_count", "completion_cap", "call_limit", "total_token_limit")
TRACE = (*META, "requests", "tool_calls", "tool_observations", "public_tool_errors",
         "length_finishes", "empty_finishes", "prompt_tokens", "completion_tokens")
CONTROLS = ("metadata_policy", "training_fixed", "bundle", "budget", "switch", "retry")


def best_action(examples):
    return max(CHOICES, key=lambda a: (
        sum(e["success"][a] for e in examples),
        -sum(e["cost"][a] for e in examples), -CHOICES.index(a)))


def fit_stump(train, features):
    """Depth one, >=16 training blocks per leaf; same capacity in both policies."""
    action = best_action(train)
    best = dict(feature=None, threshold=None, left=action, right=action)
    best_score = (sum(e["success"][action] for e in train),
                  -sum(e["cost"][action] for e in train))
    for feature in features:
        values = sorted({e["features"][feature] for e in train})
        thresholds = [(a + b) / 2 for a, b in zip(values, values[1:])]
        # ponytail: at most 16 deterministic training quantiles; no parameter search.
        if len(thresholds) > 16:
            thresholds = [thresholds[round(i * (len(thresholds) - 1) / 15)] for i in range(16)]
        for threshold in thresholds:
            left = [e for e in train if e["features"][feature] <= threshold]
            right = [e for e in train if e["features"][feature] > threshold]
            if min(len(left), len(right)) < 16:
                continue
            a, b = best_action(left), best_action(right)
            score = (sum(e["success"][a] for e in left) + sum(e["success"][b] for e in right),
                     -sum(e["cost"][a] for e in left) - sum(e["cost"][b] for e in right))
            if score > best_score:
                best_score = score
                best = dict(feature=feature, threshold=threshold, left=a, right=b)
    return best


def choose(rule, features):
    if rule["feature"] is None or features[rule["feature"]] <= rule["threshold"]:
        return rule["left"]
    return rule["right"]


def held_out_choices(train, public_test):
    rules = {"metadata_policy": fit_stump(train, META), "trace_policy": fit_stump(train, TRACE)}
    fixed = best_action(train)
    # public_test contains IDs for joining only and the numeric whitelist, no labels.
    decisions = {e["id"]: {**{name: choose(rule, e["features"]) for name, rule in rules.items()},
                            "training_fixed": fixed, **{a: a for a in CHOICES}}
                 for e in public_test}
    return decisions, rules


def uncached_costs(plan):
    costs = defaultdict(float)
    # Transport amendment: retain unknown cash liability, not invented tokens.
    ledger = read(CAMPAIGN / "ledger.json")
    unverified = {r["number"]: r for r in ledger.get("unverified_charge_reservations", [])}
    for path in sorted((CAMPAIGN / "receipts").glob("*.json")):
        receipt = read(path)
        entry = receipt["admission"]
        if entry["episode"] == "transport-preflight":
            continue
        payload = json.loads(receipt["response_body"])
        if receipt["http_status"] != 200:
            saved = unverified.get(entry["number"], {})
            if (receipt["http_status"] != 429 or payload.get("error", {}).get("code") != 429
                    or payload.get("choices") or payload.get("id") or payload.get("usage")
                    or receipt.get("generation_id")
                    or saved.get("receipt_sha256") != sha256(path.read_bytes()).hexdigest()):
                raise ValueError("Unreconciled transport receipt")
            continue
        usage = payload["usage"]
        prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
        pricing = plan["prices"][entry["model"]]["endpoint"]["pricing"]
        applicable = pricing
        for tier in sorted(pricing.get("overrides", []), key=lambda t: t["min_prompt_tokens"]):
            if prompt >= tier["min_prompt_tokens"]:
                applicable = tier
        costs[entry["episode"]] += prompt * float(applicable["prompt"]) + completion * float(applicable["completion"])
    return costs


def load_complete():
    plan = read(PLAN)
    if read(CAMPAIGN / "status.json")["status"] != "complete":
        raise ValueError("Pilot incomplete: no matched policy-utility analysis")
    raw = (CAMPAIGN / "rows.jsonl").read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    if len(rows) != plan["episodes"] or len({r["episode_id"] for r in rows}) != len(rows):
        raise ValueError("Missing or duplicate episodes")
    groups = defaultdict(dict)
    generations = []
    for row in rows:
        if row["action"] in groups[row["block_id"]]:
            raise ValueError("Duplicate block action")
        groups[row["block_id"]][row["action"]] = row
        generations.extend(row["episode"]["response_ids"])
        if row["episode"]["status"] == "provider_failure":
            raise ValueError("Transport missingness requires a separate bounded analysis")
    if len(generations) != len(set(generations)):
        raise ValueError("Duplicate provider generation")
    costs = uncached_costs(plan)
    examples = []
    for block in plan["blocks"]:
        arms = groups[block["block_id"]]
        if set(arms) != {"probe", *ACTIONS}:
            raise ValueError("Incomplete paired block")
        probe = arms["probe"]
        success, cost, tokens = {}, {}, {}
        for choice in CHOICES:
            row = probe if choice == "retain" else arms[choice]
            success[choice] = int(row["episode"]["verdict"]["verified_success"])
            cost[choice] = costs[row["episode_id"]] + (0 if choice == "retain" else costs[probe["episode_id"]])
            tokens[choice] = row["episode"]["usage"]["total_tokens"] + (0 if choice == "retain" else probe["episode"]["usage"]["total_tokens"])
        examples.append(dict(id=block["block_id"], intent=block["intent"], incumbent=block["incumbent"],
                             features={k: probe["public_features"][k] for k in TRACE},
                             success=success, cost=cost, tokens=tokens))
    return examples, sha256(raw).hexdigest()


def interval(values, alpha, draws):
    samples = sorted(statistics.mean(values[i] for i in draw) for draw in draws)
    def quantile(q):
        position = (len(samples) - 1) * q
        lower = int(position)
        return samples[lower] + (samples[min(lower + 1, len(samples) - 1)] - samples[lower]) * (position - lower)
    return [quantile(alpha / 2), quantile(1 - alpha / 2)]


def analyze():
    examples, source_hash = load_complete()
    intents = sorted({e["intent"] for e in examples})
    decisions, folds = {}, {}
    for intent in intents:
        train = [e for e in examples if e["intent"] != intent]
        public = [{"id": e["id"], "features": e["features"]} for e in examples if e["intent"] == intent]
        choices, rules = held_out_choices(train, public)
        decisions.update(choices)
        folds[intent] = rules
    # Seal action choices before evaluating held-out utilities.
    atomic(CAMPAIGN / "policy-decisions.json", dict(evidence_weight="exploratory_development",
           source_sha256=source_hash, analysis_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
           decisions=decisions, folds=folds))
    names = ("trace_policy", "metadata_policy", "training_fixed", *CHOICES)
    def summary(subset, name):
        return dict(blocks=len(subset), success_rate=statistics.mean(e["success"][decisions[e["id"]][name]] for e in subset),
                    mean_uncached_cost_usd=statistics.mean(e["cost"][decisions[e["id"]][name]] for e in subset),
                    mean_total_tokens=statistics.mean(e["tokens"][decisions[e["id"]][name]] for e in subset),
                    choices=dict(Counter(decisions[e["id"]][name] for e in subset)))
    rng = random.Random(20260905)
    draws = [[rng.randrange(len(intents)) for _ in intents] for _ in range(10000)]
    comparisons = {}
    for control in CONTROLS:
        effects, cash = [], []
        for intent in intents:
            group = [e for e in examples if e["intent"] == intent]
            effects.append(100 * statistics.mean(e["success"][decisions[e["id"]]["trace_policy"]] - e["success"][decisions[e["id"]][control]] for e in group))
            cash.append(statistics.mean(e["cost"][decisions[e["id"]]["trace_policy"]] - e["cost"][decisions[e["id"]][control]] for e in group))
        comparisons[control] = dict(success_difference_pp=statistics.mean(effects),
                                    success_conditional_95=interval(effects, .05, draws),
                                    success_conditional_familywise=interval(effects, .05 / len(CONTROLS), draws),
                                    cost_difference_usd=statistics.mean(cash),
                                    cost_conditional_95=interval(cash, .05, draws))
    no_probe = {}
    for action in ACTIONS:
        data = summary(examples, action)
        data["mean_uncached_cost_usd"] -= statistics.mean(e["cost"]["retain"] for e in examples)
        data["mean_total_tokens"] -= statistics.mean(e["tokens"]["retain"] for e in examples)
        no_probe[action] = data
    ledger = read(CAMPAIGN / "ledger.json")
    result = dict(evidence_weight="exploratory_development", blocks=len(examples), intents=len(intents),
        transport_accounting=dict(reported_usd=ledger["reported_usd"],
            conservative_accounted_usd=ledger["accounted_usd"],
            unverified_charge_reservations=ledger.get("unverified_charge_reservations", [])),
        source_sha256=source_hash, summaries={n: summary(examples, n) for n in names},
        no_probe_fixed_controls=no_probe, comparisons=comparisons,
        by_incumbent={m: {n: summary([e for e in examples if e["incumbent"] == m], n) for n in names}
                      for m in sorted({e["incumbent"] for e in examples})},
        caveats=["Not a new-intent confirmation; all semantic intents have been inspected before.",
                 "Depth-one selectors maximize training success; cost breaks exact ties only.",
                 "Conditional intent-bootstrap intervals hold learned out-of-fold rules fixed; they omit retraining uncertainty.",
                 "Six success comparisons use nominal Bonferroni coverage; only twelve intent clusters.",
                 "Cost uses uncached frozen per-request prices; deployment caching and latency may differ.",
                 "Transport-only amendment permits bounded empty-429 retries; unknown-charge reservations are separate from token-priced generation costs.",
                 "All counterfactual arms are acquired; operational cost includes only probe and selected output, not oracle best-of."])
    atomic(CAMPAIGN / "analysis.json", result)
    print(json.dumps({"blocks": len(examples), "summaries": result["summaries"]}, indent=2))


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    analyze()
