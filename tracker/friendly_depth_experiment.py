"""Policy-safe offline comparison for friendly-title depth sources."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from tracker.depth_comparison import DepthComparisonResult, compare_depth_stability


@dataclass(frozen=True)
class FriendlyDepthExperimentResult:
    title: str
    decision: str
    allowed: bool
    blockers: list[str]
    comparison: DepthComparisonResult | None


def run_experiment(
    title: str,
    external_depth_dir: str | Path,
    monocular_depth_dir: str | Path,
    policy_approved: bool,
    offline_title: bool,
    max_ratio: float = 2.0,
) -> FriendlyDepthExperimentResult:
    blockers = []
    if not policy_approved:
        blockers.append("policy approval not provided")
    if not offline_title:
        blockers.append("title is not marked as offline/friendly")

    if blockers:
        return FriendlyDepthExperimentResult(
            title=title,
            decision="NO_GO",
            allowed=False,
            blockers=blockers,
            comparison=None,
        )

    comparison = compare_depth_stability(
        captured_dir=external_depth_dir,
        baseline_dir=monocular_depth_dir,
        max_ratio=max_ratio,
    )
    decision = "GO" if not comparison.regressed else "CONDITIONAL"
    return FriendlyDepthExperimentResult(
        title=title,
        decision=decision,
        allowed=True,
        blockers=[],
        comparison=comparison,
    )


def format_experiment_result(result: FriendlyDepthExperimentResult) -> str:
    lines = [
        "Friendly title depth experiment",
        f"title={result.title}",
        f"decision={result.decision}",
        f"allowed={result.allowed}",
        "Blockers:",
    ]
    lines.extend(f"- {blocker}" for blocker in result.blockers) if result.blockers else lines.append("- none")
    if result.comparison is not None:
        lines.extend(
            [
                "Comparison:",
                f"- external_quality={result.comparison.captured.quality}",
                f"- monocular_quality={result.comparison.baseline.quality}",
                f"- mean_delta_ratio={result.comparison.mean_delta_ratio:.2f}",
                f"- regressed={result.comparison.regressed}",
            ]
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare approved friendly-title depth against monocular baseline")
    parser.add_argument("--title", required=True)
    parser.add_argument("--external-depth-dir", required=True)
    parser.add_argument("--monocular-depth-dir", required=True)
    parser.add_argument("--policy-approved", action="store_true")
    parser.add_argument("--offline-title", action="store_true")
    parser.add_argument("--max-ratio", type=float, default=2.0)
    args = parser.parse_args(argv)

    result = run_experiment(
        title=args.title,
        external_depth_dir=args.external_depth_dir,
        monocular_depth_dir=args.monocular_depth_dir,
        policy_approved=args.policy_approved,
        offline_title=args.offline_title,
        max_ratio=args.max_ratio,
    )
    print(format_experiment_result(result))
    return 0 if result.allowed and result.decision == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
