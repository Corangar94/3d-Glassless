"""Policy and technical feasibility gates for protected game integrations."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

GateDecision = Literal["GO", "CONDITIONAL", "NO_GO"]


@dataclass(frozen=True)
class GateCheck:
    id: str
    required: bool
    passed: bool
    note: str = ""


@dataclass(frozen=True)
class GateAssessment:
    target: str
    decision: GateDecision
    blockers: list[str]
    warnings: list[str]


def decide_gate(target: str, checks: Sequence[GateCheck]) -> GateAssessment:
    blockers = [_format_check(check) for check in checks if check.required and not check.passed]
    warnings = [_format_check(check) for check in checks if not check.required and not check.passed]

    if blockers:
        decision: GateDecision = "NO_GO"
    elif warnings:
        decision = "CONDITIONAL"
    else:
        decision = "GO"

    return GateAssessment(
        target=target,
        decision=decision,
        blockers=blockers,
        warnings=warnings,
    )


def wow_default_checks() -> list[GateCheck]:
    """Default WoW gate starts closed until explicit reviews are completed."""
    return [
        GateCheck(
            "policy_review",
            required=True,
            passed=False,
            note="Blizzard/protected-title policy review not completed",
        ),
        GateCheck(
            "least_invasive_path",
            required=True,
            passed=False,
            note="least-invasive technical path not selected",
        ),
        GateCheck(
            "multiplayer_depth_access",
            required=False,
            passed=False,
            note="game-depth access may be disabled or unsafe in multiplayer",
        ),
    ]


def format_assessment(assessment: GateAssessment) -> str:
    lines = [
        "Glassless3D Feasibility Gate",
        f"target={assessment.target}",
        f"decision={assessment.decision}",
        "",
        "Blockers:",
    ]
    lines.extend(f"- {blocker}" for blocker in assessment.blockers) if assessment.blockers else lines.append("- none")
    lines.append("")
    lines.append("Warnings:")
    lines.extend(f"- {warning}" for warning in assessment.warnings) if assessment.warnings else lines.append("- none")
    return "\n".join(lines)


def format_assessment_json(assessment: GateAssessment) -> str:
    return json.dumps(
        {
            "target": assessment.target,
            "decision": assessment.decision,
            "blockers": assessment.blockers,
            "warnings": assessment.warnings,
        },
        indent=2,
        sort_keys=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Glassless3D feasibility gates")
    parser.add_argument("target", choices=["wow"], help="Gate target to evaluate")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", help="Optional report output path")
    args = parser.parse_args(argv)

    if args.target == "wow":
        assessment = decide_gate("World of Warcraft", wow_default_checks())
    else:  # pragma: no cover - argparse choices prevent this path
        raise ValueError(args.target)

    report = (
        format_assessment_json(assessment)
        if args.format == "json"
        else format_assessment(assessment)
    )
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
    else:
        print(report)
    return 0 if assessment.decision == "GO" else 1


def _format_check(check: GateCheck) -> str:
    return f"{check.id}: {check.note}" if check.note else check.id


if __name__ == "__main__":
    raise SystemExit(main())
