"""Pure, unverified scoring for prepared Jev campaign facts.

Step 225 deliberately has no execution authority.  The facts accepted here are
typed offline inputs, and every report remains an offline projection even when
all inputs are complete.  Step 227 owns attachment to verified live runs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal, cast

from jev.campaign import WEIGHT_SCALE, CampaignCase, CampaignManifest, CampaignSchemaError
from jev.contracts import JsonValue

Outcome = Literal["win", "loss", "draw", "timeout", "bot_error", "infrastructure_error"]
ProjectedVerdict = Literal["no_change", "incomplete", "regressed", "inconclusive", "improved"]

_OUTCOMES: Final = frozenset(
    {"win", "loss", "draw", "timeout", "bot_error", "infrastructure_error"}
)
_PROMOTION_DELTA_MILLIONTHS: Final = 100_000


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CampaignSchemaError(message)


def _object(value: object, keys: tuple[str, ...], what: str) -> dict[str, object]:
    _require(isinstance(value, dict), f"{what} must be an object")
    result = cast(dict[str, object], value)
    _require(all(isinstance(key, str) for key in result), f"{what} keys must be strings")
    _require(set(result) == set(keys), f"{what} fields differ")
    return result


def _text(value: object, what: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{what} must be a non-empty string")
    return cast(str, value)


def _boolean(value: object, what: str) -> bool:
    _require(isinstance(value, bool), f"{what} must be a boolean")
    return cast(bool, value)


def _json_list(values: Iterable[JsonValue]) -> list[JsonValue]:
    return list(values)


@dataclass(frozen=True)
class OfflineCaseFact:
    """A caller-supplied outcome with no claim about how it was produced."""

    case_id: str
    source_fingerprint: str
    outcome: Outcome

    @classmethod
    def from_dict(cls, value: object, what: str) -> OfflineCaseFact:
        doc = _object(value, ("case_id", "source_fingerprint", "outcome"), what)
        outcome = _text(doc["outcome"], f"{what}.outcome")
        _require(outcome in _OUTCOMES, f"{what}.outcome is unknown")
        return cls(
            _text(doc["case_id"], f"{what}.case_id"),
            _text(doc["source_fingerprint"], f"{what}.source_fingerprint"),
            cast(Outcome, outcome),
        )


@dataclass(frozen=True)
class OfflineCriticalFact:
    """Offline critical-suite results for the exact parent and candidate test."""

    test_id: str
    parent_passed: bool
    candidate_passed: bool

    @classmethod
    def from_dict(cls, value: object, what: str) -> OfflineCriticalFact:
        doc = _object(value, ("test_id", "parent_passed", "candidate_passed"), what)
        return cls(
            _text(doc["test_id"], f"{what}.test_id"),
            _boolean(doc["parent_passed"], f"{what}.parent_passed"),
            _boolean(doc["candidate_passed"], f"{what}.candidate_passed"),
        )


@dataclass(frozen=True)
class OfflineEvidence:
    case_facts: tuple[OfflineCaseFact, ...]
    critical_facts: tuple[OfflineCriticalFact, ...]

    @classmethod
    def from_dict(cls, value: object) -> OfflineEvidence:
        doc = _object(value, ("case_facts", "critical_facts"), "offline evidence")
        _require(isinstance(doc["case_facts"], list), "offline evidence.case_facts must be a list")
        _require(
            isinstance(doc["critical_facts"], list),
            "offline evidence.critical_facts must be a list",
        )
        return cls(
            tuple(
                OfflineCaseFact.from_dict(item, f"offline evidence.case_facts[{index}]")
                for index, item in enumerate(cast(list[object], doc["case_facts"]))
            ),
            tuple(
                OfflineCriticalFact.from_dict(item, f"offline evidence.critical_facts[{index}]")
                for index, item in enumerate(cast(list[object], doc["critical_facts"]))
            ),
        )


def _wins(facts: list[OfflineCaseFact]) -> int:
    return sum(fact.outcome == "win" for fact in facts)


def _rate_millionths(facts: list[OfflineCaseFact]) -> int:
    _require(bool(facts), "cannot score an empty stratum")
    return (_wins(facts) * WEIGHT_SCALE) // len(facts)


def _case_key(case: CampaignCase) -> tuple[str, int]:
    return case.race, case.difficulty


def evaluate_offline(manifest: CampaignManifest, evidence: OfflineEvidence) -> dict[str, JsonValue]:
    """Apply D2/D3 to offline facts without conferring live authority."""

    manifest.validate()
    case_by_id = {case.case_id: case for case in manifest.cases}
    fact_by_id: dict[str, OfflineCaseFact] = {}
    for fact in evidence.case_facts:
        _require(fact.case_id not in fact_by_id, f"duplicate case fact {fact.case_id}")
        case = case_by_id.get(fact.case_id)
        if case is None:
            raise CampaignSchemaError(f"unknown case fact {fact.case_id}")
        _require(
            fact.source_fingerprint == case.source_fingerprint,
            f"case fact {fact.case_id} names the wrong source",
        )
        fact_by_id[fact.case_id] = fact

    critical_by_id: dict[str, OfflineCriticalFact] = {}
    for critical_fact in evidence.critical_facts:
        _require(
            critical_fact.test_id not in critical_by_id,
            f"duplicate critical fact {critical_fact.test_id}",
        )
        _require(
            critical_fact.test_id in manifest.critical_test_ids,
            f"unknown critical fact {critical_fact.test_id}",
        )
        critical_by_id[critical_fact.test_id] = critical_fact

    base: dict[str, JsonValue] = {
        "schema_version": 1,
        "campaign_id": manifest.campaign_id,
        "evidence_scope": "unverified/offline",
        "authoritative": False,
        "promotion_authorized": False,
        "scoring_rule_version": manifest.scoring_rule_version,
    }
    if manifest.no_change:
        _require(not evidence.case_facts, "an unchanged candidate cannot have case facts")
        base.update(
            {
                "projected_verdict": "no_change",
                "complete": True,
                "completed_cases": 0,
                "required_cases": 0,
                "reason": "candidate fingerprint equals parent fingerprint",
            }
        )
        return base

    missing_cases = [case.case_id for case in manifest.cases if case.case_id not in fact_by_id]
    interrupted = [
        fact.case_id for fact in evidence.case_facts if fact.outcome == "infrastructure_error"
    ]
    missing_critical = [
        test_id for test_id in manifest.critical_test_ids if test_id not in critical_by_id
    ]
    if missing_cases or interrupted or missing_critical:
        base.update(
            {
                "projected_verdict": "incomplete",
                "complete": False,
                "completed_cases": len(manifest.cases) - len(missing_cases) - len(interrupted),
                "required_cases": len(manifest.cases),
                "missing_case_ids": _json_list(missing_cases),
                "interrupted_case_ids": _json_list(interrupted),
                "missing_critical_test_ids": _json_list(missing_critical),
            }
        )
        return base

    def selected(role: str, source: str) -> list[OfflineCaseFact]:
        return [
            fact_by_id[case.case_id]
            for case in manifest.cases
            if case.role == role and case.source_fingerprint == source
        ]

    parent_fp = manifest.parent.fingerprint
    candidate_fp = manifest.candidate.fingerprint
    screen_parent_wins = _wins(selected("screen", parent_fp))
    screen_candidate_wins = _wins(selected("screen", candidate_fp))

    promotion_cases = [case for case in manifest.cases if case.role == "promotion"]
    strata = sorted({_case_key(case) for case in promotion_cases})
    weights = {
        weight.source_fingerprint: weight.weight_millionths for weight in manifest.champion_weights
    }
    candidate_rates: dict[tuple[str, int], int] = {}
    parent_rates: dict[tuple[str, int], int] = {}
    reference_rates: dict[tuple[str, int], int] = {}
    per_stratum: list[JsonValue] = []
    for race, difficulty in strata:
        by_source: dict[str, list[OfflineCaseFact]] = {}
        for case in promotion_cases:
            if _case_key(case) == (race, difficulty):
                by_source.setdefault(case.source_fingerprint, []).append(fact_by_id[case.case_id])
        candidate_rate = _rate_millionths(by_source[candidate_fp])
        parent_rate = _rate_millionths(by_source[parent_fp])
        reference_rate = (
            sum(weights[source] * _rate_millionths(by_source[source]) for source in weights)
            // WEIGHT_SCALE
        )
        candidate_rates[(race, difficulty)] = candidate_rate
        parent_rates[(race, difficulty)] = parent_rate
        reference_rates[(race, difficulty)] = reference_rate
        per_stratum.append(
            cast(
                dict[str, JsonValue],
                {
                    "race": race,
                    "difficulty": difficulty,
                    "candidate_wins": _wins(by_source[candidate_fp]),
                    "parent_wins": _wins(by_source[parent_fp]),
                    "games_per_source": len(by_source[candidate_fp]),
                    "candidate_rate_millionths": candidate_rate,
                    "parent_rate_millionths": parent_rate,
                    "reference_rate_millionths": reference_rate,
                },
            )
        )

    candidate_score = sum(candidate_rates.values()) // len(strata)
    reference_score = sum(reference_rates.values()) // len(strata)
    delta = candidate_score - reference_score
    candidate_promotion = selected("promotion", candidate_fp)
    parent_promotion = selected("promotion", parent_fp)
    candidate_wins = _wins(candidate_promotion)
    parent_wins = _wins(parent_promotion)

    race_regressions: list[str] = []
    for race in sorted({race for race, _ in strata}):
        candidate_race_wins = _wins(
            [
                fact_by_id[case.case_id]
                for case in promotion_cases
                if case.race == race and case.source_fingerprint == candidate_fp
            ]
        )
        parent_race_wins = _wins(
            [
                fact_by_id[case.case_id]
                for case in promotion_cases
                if case.race == race and case.source_fingerprint == parent_fp
            ]
        )
        if candidate_race_wins < parent_race_wins:
            race_regressions.append(race)
    difficulty_regressions: list[int] = []
    for difficulty in sorted({difficulty for _, difficulty in strata}):
        candidate_difficulty_wins = _wins(
            [
                fact_by_id[case.case_id]
                for case in promotion_cases
                if case.difficulty == difficulty and case.source_fingerprint == candidate_fp
            ]
        )
        parent_difficulty_wins = _wins(
            [
                fact_by_id[case.case_id]
                for case in promotion_cases
                if case.difficulty == difficulty and case.source_fingerprint == parent_fp
            ]
        )
        if candidate_difficulty_wins < parent_difficulty_wins:
            difficulty_regressions.append(difficulty)

    critical_failed = [
        test_id
        for test_id in manifest.critical_test_ids
        if not critical_by_id[test_id].candidate_passed
    ]
    parent_critical_failed = [
        test_id
        for test_id in manifest.critical_test_ids
        if not critical_by_id[test_id].parent_passed
    ]
    bot_errors = [fact.case_id for fact in evidence.case_facts if fact.outcome == "bot_error"]
    regressed = bool(
        critical_failed
        or parent_critical_failed
        or bot_errors
        or screen_candidate_wins < screen_parent_wins
        or candidate_wins < parent_wins
        or race_regressions
        or difficulty_regressions
    )
    improved = bool(
        not regressed
        and screen_candidate_wins >= screen_parent_wins
        and candidate_wins > parent_wins
        and delta >= _PROMOTION_DELTA_MILLIONTHS
    )
    verdict: ProjectedVerdict = (
        "regressed" if regressed else "improved" if improved else "inconclusive"
    )
    base.update(
        {
            "projected_verdict": verdict,
            "complete": True,
            "completed_cases": len(manifest.cases),
            "required_cases": len(manifest.cases),
            "screen_candidate_wins": screen_candidate_wins,
            "screen_parent_wins": screen_parent_wins,
            "promotion_candidate_wins": candidate_wins,
            "promotion_parent_wins": parent_wins,
            "candidate_score_millionths": candidate_score,
            "reference_score_millionths": reference_score,
            "candidate_reference_delta_millionths": delta,
            "per_stratum": per_stratum,
            "race_regressions": _json_list(race_regressions),
            "difficulty_regressions": _json_list(difficulty_regressions),
            "critical_failures": _json_list(critical_failed),
            "parent_critical_failures": _json_list(parent_critical_failed),
            "bot_error_case_ids": _json_list(bot_errors),
        }
    )
    return base
