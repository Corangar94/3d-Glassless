from tracker.feasibility_gate import (
    GateAssessment,
    GateCheck,
    decide_gate,
    format_assessment,
    main,
    wow_default_checks,
)


def test_decide_gate_returns_go_when_all_required_checks_pass():
    checks = [
        GateCheck("policy_review", required=True, passed=True),
        GateCheck("technical_review", required=True, passed=True),
    ]

    assessment = decide_gate("Friendly Offline Title", checks)

    assert assessment.decision == "GO"
    assert assessment.blockers == []


def test_decide_gate_returns_no_go_when_required_check_fails():
    checks = [
        GateCheck("policy_review", required=True, passed=False, note="process injection prohibited"),
        GateCheck("technical_review", required=True, passed=True),
    ]

    assessment = decide_gate("Protected Game", checks)

    assert assessment.decision == "NO_GO"
    assert assessment.blockers == ["policy_review: process injection prohibited"]


def test_decide_gate_returns_conditional_when_optional_check_fails():
    checks = [
        GateCheck("policy_review", required=True, passed=True),
        GateCheck("depth_access", required=False, passed=False, note="no depth buffer"),
    ]

    assessment = decide_gate("Friendly Offline Title", checks)

    assert assessment.decision == "CONDITIONAL"
    assert assessment.warnings == ["depth_access: no depth buffer"]


def test_wow_default_checks_are_no_go_until_policy_and_technical_reviews_pass():
    assessment = decide_gate("World of Warcraft", wow_default_checks())

    assert isinstance(assessment, GateAssessment)
    assert assessment.decision == "NO_GO"
    assert any("policy_review" in blocker for blocker in assessment.blockers)
    assert any("least_invasive_path" in blocker for blocker in assessment.blockers)


def test_format_assessment_lists_blockers_and_warnings():
    assessment = decide_gate("World of Warcraft", wow_default_checks())

    text = format_assessment(assessment)

    assert "target=World of Warcraft" in text
    assert "decision=NO_GO" in text
    assert "Blockers:" in text
    assert "Warnings:" in text


def test_main_returns_nonzero_for_default_wow_gate(capsys):
    code = main(["wow"])

    assert code == 1
    assert "decision=NO_GO" in capsys.readouterr().out
