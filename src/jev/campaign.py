"""Immutable, no-network contracts for a Jev campaign.

This module only prepares an exact comparison matrix.  It does not describe or
authenticate executions; Step 227 owns that boundary.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from jev.benchmark import MAP, PINNED_MODEL, RACES, SourceSnapshot, verify_snapshot
from jev.contracts import JsonValue
from jev.policy import load_policy_bundle

SCHEMA_VERSION: Final = 1
SCORING_RULE_VERSION: Final = "jev-campaign-offline-v1"
MANIFEST_FILE: Final = "manifest.json"
MANIFEST_DIGEST_FILE: Final = "manifest.sha256"
WEIGHT_SCALE: Final = 1_000_000
J2_SEEDS: Final = frozenset({11, 101, 202})
PROMOTION_DIFFICULTIES: Final = (3, 4)
PROMOTION_SEED_COUNT: Final = 3
DEFAULT_WINDOW_SECONDS: Final = 6 * 60 * 60
DEFAULT_BUDGET_NANODOLLARS: Final = 5_000_000_000
DEFAULT_SPEED: Final = "2"
DEFAULT_GAME_SECONDS: Final = 900
DEFAULT_WALL_SECONDS: Final = 1200
DEFAULT_REQUESTS: Final = 450

CaseRole = Literal["screen", "promotion", "control"]
WeightRole = Literal["current_parent", "original_anchor", "distinct_champion"]


class CampaignSchemaError(ValueError):
    """A campaign contract is malformed or internally inconsistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CampaignSchemaError(message)


def _sha256(value: str, what: str) -> str:
    _require(
        len(value) == 64 and all(c in "0123456789abcdef" for c in value),
        f"{what} must be lowercase SHA-256",
    )
    return value


def _id(value: str, what: str) -> str:
    try:
        parsed = uuid.UUID(hex=value)
    except ValueError as exc:
        raise CampaignSchemaError(f"{what} must be lowercase UUID4 hex") from exc
    _require(parsed.version == 4 and parsed.hex == value, f"{what} must be lowercase UUID4 hex")
    return value


def _object(value: object, keys: tuple[str, ...], what: str) -> dict[str, object]:
    _require(
        isinstance(value, dict) and all(isinstance(k, str) for k in value),
        f"{what} must be an object",
    )
    result = cast(dict[str, object], value)
    _require(set(result) == set(keys), f"{what} fields differ")
    return result


def _integer(value: object, what: str, minimum: int = 0) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
        f"{what} must be an integer >= {minimum}",
    )
    return cast(int, value)


def _text(value: object, what: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{what} must be a non-empty string")
    return cast(str, value)


def _canonical(document: dict[str, JsonValue]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


@dataclass(frozen=True)
class SourceReference:
    version: int
    directory: str
    fingerprint: str
    policy_hash: str

    @classmethod
    def from_snapshot(cls, directory: Path) -> SourceReference:
        resolved = directory.resolve(strict=True)
        snapshot: SourceSnapshot = verify_snapshot(resolved)
        _require(snapshot.version == "v2", "campaign sources must be frozen Jev v2 snapshots")
        bundle = load_policy_bundle(
            resolved / "bots" / "jev" / "v2", expected_entrypoint="bots.jev.v2"
        )
        return cls(2, str(resolved), snapshot.fingerprint, bundle.policy_hash)

    def verify(self) -> None:
        _require(
            SourceReference.from_snapshot(Path(self.directory)) == self,
            "source reference no longer matches its snapshot",
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "version": self.version,
            "directory": self.directory,
            "fingerprint": self.fingerprint,
            "policy_hash": self.policy_hash,
        }

    @classmethod
    def from_dict(cls, value: object, what: str) -> SourceReference:
        doc = _object(value, ("version", "directory", "fingerprint", "policy_hash"), what)
        result = cls(
            _integer(doc["version"], f"{what}.version", 1),
            _text(doc["directory"], f"{what}.directory"),
            _sha256(_text(doc["fingerprint"], f"{what}.fingerprint"), f"{what}.fingerprint"),
            _sha256(_text(doc["policy_hash"], f"{what}.policy_hash"), f"{what}.policy_hash"),
        )
        _require(result.version == 2, f"{what}.version must be 2")
        result.verify()
        return result


@dataclass(frozen=True)
class ChampionWeight:
    source_fingerprint: str
    weight_millionths: int
    roles: tuple[WeightRole, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "source_fingerprint": self.source_fingerprint,
            "weight_millionths": self.weight_millionths,
            "roles": list(self.roles),
        }

    @classmethod
    def from_dict(cls, value: object, what: str) -> ChampionWeight:
        doc = _object(value, ("source_fingerprint", "weight_millionths", "roles"), what)
        raw_roles = doc["roles"]
        _require(
            isinstance(raw_roles, list) and bool(raw_roles),
            f"{what}.roles must be a non-empty list",
        )
        role_values = cast(list[object], raw_roles)
        allowed = {"current_parent", "original_anchor", "distinct_champion"}
        _require(
            all(isinstance(r, str) and r in allowed for r in role_values),
            f"{what}.roles contains an unknown role",
        )
        roles = cast(tuple[WeightRole, ...], tuple(role_values))
        _require(len(set(roles)) == len(roles), f"{what}.roles contains duplicates")
        return cls(
            _sha256(
                _text(doc["source_fingerprint"], f"{what}.source_fingerprint"),
                f"{what}.source_fingerprint",
            ),
            _integer(doc["weight_millionths"], f"{what}.weight_millionths", 1),
            roles,
        )


def coalesce_champion_weights(
    parent: SourceReference, original: SourceReference, distinct: SourceReference | None
) -> tuple[ChampionWeight, ...]:
    allocations: list[tuple[SourceReference, int, WeightRole]] = [
        (parent, 500_000, "current_parent"),
        (original, 250_000, "original_anchor"),
        ((distinct or original), 250_000, "distinct_champion"),
    ]
    weights: dict[str, int] = {}
    roles: dict[str, list[WeightRole]] = {}
    for source, weight, role in allocations:
        weights[source.fingerprint] = weights.get(source.fingerprint, 0) + weight
        roles.setdefault(source.fingerprint, []).append(role)
    return tuple(ChampionWeight(fp, weights[fp], tuple(roles[fp])) for fp in sorted(weights))


@dataclass(frozen=True)
class CampaignCase:
    case_id: str
    role: CaseRole
    source_fingerprint: str
    map_name: str
    race: str
    difficulty: int
    seed: int
    order: int

    @property
    def stratum(self) -> tuple[str, int]:
        return self.race, self.difficulty

    @staticmethod
    def make(
        role: CaseRole, source: str, race: str, difficulty: int, seed: int, order: int
    ) -> CampaignCase:
        identity = f"{role}|{source}|{MAP}|{race}|{difficulty}|{seed}|{order}"
        return CampaignCase(
            hashlib.sha256(identity.encode()).hexdigest(),
            role,
            source,
            MAP,
            race,
            difficulty,
            seed,
            order,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "case_id": self.case_id,
            "role": self.role,
            "source_fingerprint": self.source_fingerprint,
            "map": self.map_name,
            "race": self.race,
            "difficulty": self.difficulty,
            "seed": self.seed,
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, value: object, what: str) -> CampaignCase:
        doc = _object(
            value,
            ("case_id", "role", "source_fingerprint", "map", "race", "difficulty", "seed", "order"),
            what,
        )
        role = _text(doc["role"], f"{what}.role")
        _require(role in {"screen", "promotion", "control"}, f"{what}.role is unknown")
        result = cls(
            _sha256(_text(doc["case_id"], f"{what}.case_id"), f"{what}.case_id"),
            cast(CaseRole, role),
            _sha256(
                _text(doc["source_fingerprint"], f"{what}.source_fingerprint"),
                f"{what}.source_fingerprint",
            ),
            _text(doc["map"], f"{what}.map"),
            _text(doc["race"], f"{what}.race"),
            _integer(doc["difficulty"], f"{what}.difficulty", 1),
            _integer(doc["seed"], f"{what}.seed"),
            _integer(doc["order"], f"{what}.order"),
        )
        _require(
            result
            == CampaignCase.make(
                result.role,
                result.source_fingerprint,
                result.race,
                result.difficulty,
                result.seed,
                result.order,
            ),
            f"{what}.case_id does not match its fields",
        )
        return result


@dataclass(frozen=True)
class CampaignManifest:
    schema_version: int
    campaign_id: str
    model: str
    requested_speed: str
    budget_nanodollars: int
    window_seconds: int
    game_seconds: int
    wall_seconds: int
    request_limit: int
    parent: SourceReference
    candidate: SourceReference
    champions: tuple[SourceReference, ...]
    champion_weights: tuple[ChampionWeight, ...]
    cases: tuple[CampaignCase, ...]
    critical_test_ids: tuple[str, ...]
    scoring_rule_version: str = SCORING_RULE_VERSION

    @property
    def no_change(self) -> bool:
        return self.parent.fingerprint == self.candidate.fingerprint

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "unsupported campaign schema")
        _id(self.campaign_id, "campaign_id")
        _require(self.model == PINNED_MODEL, "campaign model must remain pinned")
        _require(self.requested_speed in {"native", "1", "2", "4"}, "requested_speed is invalid")
        _require(
            self.budget_nanodollars == DEFAULT_BUDGET_NANODOLLARS,
            "Step 225 fixes the planned $5 ceiling",
        )
        _require(
            self.window_seconds == DEFAULT_WINDOW_SECONDS,
            "Step 225 fixes the planned six-hour window",
        )
        _require(
            (self.game_seconds, self.wall_seconds, self.request_limit)
            == (DEFAULT_GAME_SECONDS, DEFAULT_WALL_SECONDS, DEFAULT_REQUESTS),
            "match limits differ from the frozen plan",
        )
        for source in (self.parent, self.candidate, *self.champions):
            source.verify()
        source_by_fp = {source.fingerprint: source for source in self.champions}
        _require(
            len(source_by_fp) == len(self.champions),
            "champion sources contain duplicate fingerprints",
        )
        _require(self.parent.fingerprint in source_by_fp, "champions must include current parent")
        _require(
            sum(w.weight_millionths for w in self.champion_weights) == WEIGHT_SCALE,
            "champion weights must sum exactly to one million",
        )
        _require(
            {w.source_fingerprint for w in self.champion_weights} == set(source_by_fp),
            "weights and champion sources differ",
        )
        _require(
            len({case.case_id for case in self.cases}) == len(self.cases), "case IDs must be unique"
        )
        _require(
            tuple(case.order for case in self.cases) == tuple(range(len(self.cases))),
            "case order must be contiguous and immutable",
        )
        _require(
            all(case.map_name == MAP and case.race in RACES for case in self.cases),
            "case map or race is invalid",
        )
        seeds = tuple(case.seed for case in self.cases)
        _require(not (set(seeds) & J2_SEEDS), "campaign cases must exclude J2 panel seeds")
        _require(
            len(set(self.critical_test_ids)) == len(self.critical_test_ids)
            and bool(self.critical_test_ids),
            "critical test IDs must be non-empty and unique",
        )
        if self.no_change:
            _require(not self.cases, "an unchanged candidate must not schedule cases")
            return
        expected_sources = {self.candidate.fingerprint, *source_by_fp}
        _require(
            all(case.source_fingerprint in expected_sources for case in self.cases),
            "case names an unknown source",
        )
        screen = [c for c in self.cases if c.role == "screen"]
        _require(len(screen) == 6, "screen must contain three paired cases per source")
        for race in RACES:
            pair = [c for c in screen if c.race == race]
            _require(
                len(pair) == 2
                and {c.source_fingerprint for c in pair}
                == {self.parent.fingerprint, self.candidate.fingerprint}
                and len({c.seed for c in pair}) == 1
                and all(c.difficulty == 3 for c in pair),
                f"screen stratum {race} is not an exact parent/candidate pair",
            )
        screen_seeds = {c.seed for c in screen}
        _require(len(screen_seeds) == 3, "screen must use one distinct seed per race")
        promotion = [c for c in self.cases if c.role == "promotion"]
        coordinates = {(c.race, c.difficulty, c.seed) for c in promotion}
        expected_coordinates = {
            (race, difficulty, seed)
            for race in RACES
            for difficulty in PROMOTION_DIFFICULTIES
            for seed in {c.seed for c in promotion}
        }
        _require(
            coordinates == expected_coordinates
            and len({c.seed for c in promotion}) == PROMOTION_SEED_COUNT,
            "promotion strata or seed count is incomplete",
        )
        for coordinate in coordinates:
            group = [c for c in promotion if (c.race, c.difficulty, c.seed) == coordinate]
            _require(
                {c.source_fingerprint for c in group} == expected_sources
                and len(group) == len(expected_sources),
                "promotion coordinate lacks an exact source matrix",
            )
        controls = [c for c in self.cases if c.role == "control"]
        _require(
            len(controls) == 3
            and {c.race for c in controls} == set(RACES)
            and all(
                c.source_fingerprint == self.parent.fingerprint and c.difficulty == 3
                for c in controls
            ),
            "control matrix must contain one parent Medium case per race",
        )
        control_seeds = {c.seed for c in controls}
        _require(len(control_seeds) == 3, "controls must use one distinct seed per race")
        _require(
            not (screen_seeds & {c.seed for c in promotion})
            and not (screen_seeds & control_seeds)
            and not ({c.seed for c in promotion} & control_seeds),
            "screen, promotion and control seed lists must be isolated",
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "model": self.model,
            "requested_speed": self.requested_speed,
            "budget_nanodollars": self.budget_nanodollars,
            "window_seconds": self.window_seconds,
            "game_seconds": self.game_seconds,
            "wall_seconds": self.wall_seconds,
            "request_limit": self.request_limit,
            "parent": self.parent.to_dict(),
            "candidate": self.candidate.to_dict(),
            "champions": [x.to_dict() for x in self.champions],
            "champion_weights": [x.to_dict() for x in self.champion_weights],
            "cases": [x.to_dict() for x in self.cases],
            "critical_test_ids": list(self.critical_test_ids),
            "scoring_rule_version": self.scoring_rule_version,
        }

    @classmethod
    def from_dict(cls, value: object) -> CampaignManifest:
        keys = (
            "schema_version",
            "campaign_id",
            "model",
            "requested_speed",
            "budget_nanodollars",
            "window_seconds",
            "game_seconds",
            "wall_seconds",
            "request_limit",
            "parent",
            "candidate",
            "champions",
            "champion_weights",
            "cases",
            "critical_test_ids",
            "scoring_rule_version",
        )
        doc = _object(value, keys, "manifest")
        for field in ("champions", "champion_weights", "cases", "critical_test_ids"):
            _require(isinstance(doc[field], list), f"manifest.{field} must be a list")
        result = cls(
            _integer(doc["schema_version"], "manifest.schema_version", 1),
            _text(doc["campaign_id"], "manifest.campaign_id"),
            _text(doc["model"], "manifest.model"),
            _text(doc["requested_speed"], "manifest.requested_speed"),
            _integer(doc["budget_nanodollars"], "manifest.budget_nanodollars"),
            _integer(doc["window_seconds"], "manifest.window_seconds", 1),
            _integer(doc["game_seconds"], "manifest.game_seconds", 1),
            _integer(doc["wall_seconds"], "manifest.wall_seconds", 1),
            _integer(doc["request_limit"], "manifest.request_limit", 1),
            SourceReference.from_dict(doc["parent"], "manifest.parent"),
            SourceReference.from_dict(doc["candidate"], "manifest.candidate"),
            tuple(
                SourceReference.from_dict(x, f"manifest.champions[{i}]")
                for i, x in enumerate(cast(list[object], doc["champions"]))
            ),
            tuple(
                ChampionWeight.from_dict(x, f"manifest.champion_weights[{i}]")
                for i, x in enumerate(cast(list[object], doc["champion_weights"]))
            ),
            tuple(
                CampaignCase.from_dict(x, f"manifest.cases[{i}]")
                for i, x in enumerate(cast(list[object], doc["cases"]))
            ),
            tuple(
                _text(x, f"manifest.critical_test_ids[{i}]")
                for i, x in enumerate(cast(list[object], doc["critical_test_ids"]))
            ),
            _text(doc["scoring_rule_version"], "manifest.scoring_rule_version"),
        )
        _require(result.scoring_rule_version == SCORING_RULE_VERSION, "unsupported scoring rule")
        result.validate()
        return result


def _fresh_seeds(count: int, excluded: set[int]) -> tuple[int, ...]:
    values: list[int] = []
    while len(values) < count:
        value = secrets.randbelow(2**31)
        if value not in excluded and value not in values:
            values.append(value)
    return tuple(values)


def build_manifest(
    *,
    parent: SourceReference,
    candidate: SourceReference,
    original: SourceReference,
    distinct: SourceReference | None,
    critical_test_ids: tuple[str, ...],
    requested_speed: str = DEFAULT_SPEED,
    screen_seeds: tuple[int, int, int] | None = None,
    promotion_seeds: tuple[int, int, int] | None = None,
    control_seeds: tuple[int, int, int] | None = None,
    campaign_id: str | None = None,
) -> CampaignManifest:
    weights = coalesce_champion_weights(parent, original, distinct)
    by_fp = {s.fingerprint: s for s in (parent, original, *((distinct,) if distinct else ()))}
    champions = tuple(by_fp[weight.source_fingerprint] for weight in weights)
    cases: list[CampaignCase] = []
    if parent.fingerprint != candidate.fingerprint:
        excluded = set(J2_SEEDS)
        screen_values = screen_seeds or cast(tuple[int, int, int], _fresh_seeds(3, excluded))
        excluded.update(screen_values)
        promotion_values = promotion_seeds or cast(tuple[int, int, int], _fresh_seeds(3, excluded))
        excluded.update(promotion_values)
        control_values = control_seeds or cast(tuple[int, int, int], _fresh_seeds(3, excluded))
        _require(
            len(set((*screen_values, *promotion_values, *control_values))) == 9
            and not (set((*screen_values, *promotion_values, *control_values)) & J2_SEEDS),
            "campaign seeds must be unique and exclude J2 seeds",
        )
        order = 0
        for race, seed in zip(RACES, screen_values, strict=True):
            for source in (parent.fingerprint, candidate.fingerprint):
                cases.append(CampaignCase.make("screen", source, race, 3, seed, order))
                order += 1
        promotion_sources = (
            candidate.fingerprint,
            *tuple(weight.source_fingerprint for weight in weights),
        )
        promotion_sources = tuple(dict.fromkeys(promotion_sources))
        for difficulty in PROMOTION_DIFFICULTIES:
            for race in RACES:
                for seed in promotion_values:
                    for source in promotion_sources:
                        cases.append(
                            CampaignCase.make("promotion", source, race, difficulty, seed, order)
                        )
                        order += 1
        for race, seed in zip(RACES, control_values, strict=True):
            cases.append(CampaignCase.make("control", parent.fingerprint, race, 3, seed, order))
            order += 1
    result = CampaignManifest(
        SCHEMA_VERSION,
        campaign_id or uuid.uuid4().hex,
        PINNED_MODEL,
        requested_speed,
        DEFAULT_BUDGET_NANODOLLARS,
        DEFAULT_WINDOW_SECONDS,
        DEFAULT_GAME_SECONDS,
        DEFAULT_WALL_SECONDS,
        DEFAULT_REQUESTS,
        parent,
        candidate,
        champions,
        weights,
        tuple(cases),
        critical_test_ids,
    )
    result.validate()
    return result


def manifest_digest(manifest: CampaignManifest) -> str:
    return hashlib.sha256(_canonical(manifest.to_dict())).hexdigest()


def prepare_campaign(root: Path, manifest: CampaignManifest) -> Path:
    manifest.validate()
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    final = root / manifest.campaign_id
    _require(not final.exists(), "campaign already exists; manifests are write-once")
    staging = root / f".{manifest.campaign_id}.preparing"
    _require(not staging.exists(), "campaign staging path already exists")
    staging.mkdir()
    try:
        (staging / MANIFEST_FILE).write_bytes(_canonical(manifest.to_dict()))
        (staging / MANIFEST_DIGEST_FILE).write_text(
            manifest_digest(manifest) + "\n", encoding="ascii"
        )
        staging.rename(final)
    except BaseException:
        for path in staging.iterdir() if staging.exists() else ():
            path.unlink()
        if staging.exists():
            staging.rmdir()
        raise
    return final


def read_campaign_manifest(campaign_dir: Path) -> CampaignManifest:
    try:
        raw = (campaign_dir / MANIFEST_FILE).read_bytes()
        expected = (campaign_dir / MANIFEST_DIGEST_FILE).read_text(encoding="ascii").strip()
        parsed = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignSchemaError("campaign manifest cannot be read") from exc
    manifest = CampaignManifest.from_dict(parsed)
    _require(raw == _canonical(manifest.to_dict()), "manifest JSON is not canonical")
    _require(expected == manifest_digest(manifest), "campaign manifest changed after preparation")
    _require(
        campaign_dir.resolve().name == manifest.campaign_id,
        "campaign directory does not match campaign_id",
    )
    return manifest


def manifest_summary(manifest: CampaignManifest) -> dict[str, JsonValue]:
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": manifest.campaign_id,
        "manifest_digest": manifest_digest(manifest),
        "evidence_scope": "unverified/offline",
        "authoritative": False,
        "promotion_authorized": False,
        "no_change": manifest.no_change,
        "case_count": len(manifest.cases),
        "planned_budget_nanodollars": manifest.budget_nanodollars,
        "window_seconds": manifest.window_seconds,
    }
