"""Prepare and inspect Step 225 Jev campaign contracts without network access.

Live execution and authoritative reporting are intentionally unavailable until
Step 227 supplies verified execution facts.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from jev.campaign import (  # noqa: E402
    CampaignSchemaError,
    SourceReference,
    build_manifest,
    manifest_summary,
    prepare_campaign,
    read_campaign_manifest,
)
from jev.campaign_evaluation import OfflineEvidence, evaluate_offline  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare or inspect an unverified/offline Jev campaign contract"
    )
    subparsers = parser.add_subparsers(dest="command")
    prepare = subparsers.add_parser("prepare", help="write an immutable no-network manifest")
    prepare.add_argument("--parent", type=Path, required=True)
    prepare.add_argument("--candidate", type=Path, required=True)
    prepare.add_argument("--original", type=Path, required=True)
    prepare.add_argument("--distinct-champion", type=Path)
    prepare.add_argument("--root", type=Path, required=True)
    prepare.add_argument("--critical-test", action="append", required=True)
    prepare.add_argument("--speed", choices=("native", "1", "2", "4"), default="2")
    prepare.add_argument(
        "--dry-run",
        action="store_true",
        help="compatibility label; preparation is already offline and writes only the manifest",
    )

    report = subparsers.add_parser("report", help="read an offline manifest or score offline facts")
    report.add_argument("--campaign", type=Path, required=True)
    report.add_argument("--evidence", type=Path)

    for name in ("run", "resume"):
        refused = subparsers.add_parser(name, help="reserved for the Step 227 controller")
        refused.add_argument("--campaign", type=Path, required=True)
        refused.add_argument("--authoritative", action="store_true", help=argparse.SUPPRESS)
        refused.add_argument("--promote", action="store_true", help=argparse.SUPPRESS)
    return parser


def _print(document: object) -> None:
    print(json.dumps(document, sort_keys=True, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage()
        return 0
    try:
        if args.command == "prepare":
            parent = SourceReference.from_snapshot(args.parent)
            candidate = SourceReference.from_snapshot(args.candidate)
            original = SourceReference.from_snapshot(args.original)
            distinct = (
                SourceReference.from_snapshot(args.distinct_champion)
                if args.distinct_champion is not None
                else None
            )
            manifest = build_manifest(
                parent=parent,
                candidate=candidate,
                original=original,
                distinct=distinct,
                critical_test_ids=tuple(args.critical_test),
                requested_speed=args.speed,
            )
            campaign_dir = prepare_campaign(args.root, manifest)
            summary = manifest_summary(manifest)
            summary["campaign_directory"] = str(campaign_dir)
            summary["operation"] = "prepared offline; no network or process launch"
            _print(summary)
            return 0
        if args.command == "report":
            manifest = read_campaign_manifest(args.campaign)
            if args.evidence is None:
                _print(manifest_summary(manifest))
                return 0
            try:
                raw_evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CampaignSchemaError("offline evidence cannot be read") from exc
            _print(evaluate_offline(manifest, OfflineEvidence.from_dict(raw_evidence)))
            return 0
        print(
            "live campaign execution and authoritative promotion are unavailable in Step 225; "
            "Step 227 must supply verified execution facts",
            file=sys.stderr,
        )
        return 2
    except CampaignSchemaError as exc:
        print(f"invalid campaign input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
