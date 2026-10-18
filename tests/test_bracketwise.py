import json
from decimal import Decimal
from pathlib import Path

import pytest

import bracketwise as bw

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample.brk"


def plan_from(text):
    plan, errors = bw.parse_plan(text, "test.brk")
    assert errors == []
    return plan


def test_sample_file_lints_clean():
    plan, errors = bw.parse_plan(SAMPLE.read_text(encoding="utf-8"), str(SAMPLE))
    assert errors == []
    assert len(plan.bands) == 4
    assert plan.deduction == Decimal("12000")
    assert plan.credit == Decimal("150")


def test_bad_lines_are_reported_with_line_numbers():
    text = "currency USD\nbogus 5\nband 0 inf ten%\ndeduction -3\n"
    _, errors = bw.parse_plan(text, "x.brk")
    assert "x.brk:2: unknown directive 'bogus'" in errors
    assert any(e.startswith("x.brk:3:") and "not a number" in e for e in errors)
    assert any(e.startswith("x.brk:4:") and "finite amount" in e for e in errors)


def test_gap_and_overlap_are_reported_with_line_numbers():
    gap = "band 0 100 10\nband 150 inf 20\n"
    _, errors = bw.parse_plan(gap, "g.brk")
    assert "g.brk:2: gap between 100 and 150" in errors

    overlap = "band 0 100 10\nband 80 inf 20\n"
    _, errors = bw.parse_plan(overlap, "o.brk")
    assert any(e.startswith("o.brk:2:") and "overlapping" in e for e in errors)


def test_first_band_must_start_at_zero_and_last_must_be_open():
    _, errors = bw.parse_plan("band 5 100 10\nband 100 200 20\n", "s.brk")
    assert any("first band must start at 0" in e for e in errors)
    assert any("last band must end in 'inf'" in e for e in errors)


def test_band_after_open_ended_band_is_an_error():
    _, errors = bw.parse_plan("band 0 inf 10\nband 5 10 20\n", "i.brk")
    assert any("open-ended" in e for e in errors)


def test_rates_must_not_fall_and_must_be_percent_range():
    _, errors = bw.parse_plan("band 0 100 20%\nband 100 inf 10%\n", "r.brk")
    assert any("below the previous band" in e for e in errors)

    _, errors = bw.parse_plan("band 0 inf 150%\n", "r.brk")
    assert any("between 0 and 100" in e for e in errors)


def test_duplicate_single_value_directive_is_an_error():
    _, errors = bw.parse_plan("deduction 1\ndeduction 2\nband 0 inf 10\n", "d.brk")
    assert "d.brk:2: duplicate 'deduction' line" in errors


def test_summarize_matches_hand_calculation():
    # Gross 60000, deduction 12000 -> taxable 48000.
    # 10000 * 10% + 30000 * 12% + 8000 * 22% = 6360.00, minus 150 credit.
    s = bw.summarize(plan_from(SAMPLE.read_text(encoding="utf-8")), Decimal("60000"))
    assert s["taxable"] == Decimal("48000")
    assert s["tax_before_credits"] == Decimal("6360.00")
    assert s["credit_applied"] == Decimal("150")
    assert s["total_tax"] == Decimal("6210.00")
    assert s["take_home"] == Decimal("53790.00")
    assert s["effective_rate"] == Decimal("10.35")
    assert s["marginal_rate"] == Decimal("22")


def test_income_below_deduction_pays_no_tax():
    plan = plan_from("deduction 12000\nband 0 inf 10%\n")
    s = bw.summarize(plan, Decimal("5000"))
    assert s["taxable"] == Decimal(0)
    assert s["total_tax"] == Decimal("0")
    assert s["take_home"] == Decimal("5000")


def test_credit_never_makes_tax_negative():
    plan = plan_from("credit 500\nband 0 inf 10%\n")
    s = bw.summarize(plan, Decimal("1000"))
    assert s["tax_before_credits"] == Decimal("100.00")
    assert s["credit_applied"] == Decimal("100.00")
    assert s["total_tax"] == Decimal("0.00")


def test_band_tax_rounds_half_up_to_the_cent():
    plan = plan_from("band 0 inf 10%\n")
    s = bw.summarize(plan, Decimal("5.05"))
    assert s["total_tax"] == Decimal("0.51")


def test_marginal_rate_at_a_boundary_uses_the_next_band():
    plan = plan_from(SAMPLE.read_text(encoding="utf-8"))
    assert bw.marginal_rate(plan, Decimal("0")) == Decimal("0.10")
    assert bw.marginal_rate(plan, Decimal("10000")) == Decimal("0.12")
    assert bw.marginal_rate(plan, Decimal("9999.99")) == Decimal("0.10")


def test_gross_for_net_is_the_smallest_gross_to_the_cent():
    plan = plan_from(SAMPLE.read_text(encoding="utf-8"))
    gross = bw.gross_for_net(plan, Decimal("50000"))
    assert gross == Decimal("55141.02")
    assert bw.summarize(plan, gross)["take_home"] >= Decimal("50000")
    assert bw.summarize(plan, gross - Decimal("0.01"))["take_home"] < Decimal("50000")


def test_gross_for_net_zero_target_needs_no_income():
    plan = plan_from(SAMPLE.read_text(encoding="utf-8"))
    assert bw.gross_for_net(plan, Decimal("0")) == Decimal("0")


def test_gross_for_net_unreachable_when_everything_is_taxed():
    plan = plan_from("band 0 inf 100%\n")
    with pytest.raises(bw.BracketError):
        bw.gross_for_net(plan, Decimal("1"))


def test_raise_effect_keeps_seventy_eight_percent_in_the_22_band():
    plan = plan_from(SAMPLE.read_text(encoding="utf-8"))
    effect = bw.raise_effect(plan, Decimal("60000"), Decimal("3000"))
    assert effect["gain"] == Decimal("2340.00")
    assert effect["tax_on_raise"] == Decimal("660.00")
    assert effect["kept_percent"] == Decimal("78.00")


def test_compare_reports_the_tax_difference():
    a = plan_from("deduction 0\nband 0 inf 10%\n")
    b = plan_from("deduction 0\nband 0 inf 15%\n")
    result = bw.compare_results(Decimal("1000"), a, b)
    assert result["difference_tax"] == Decimal("50.00")
    assert "Lower tax: file A" in bw.render_compare(result)


def test_table_shows_threshold_tax_and_gross():
    plan = plan_from(SAMPLE.read_text(encoding="utf-8"))
    table = bw.render_table(plan)
    # Tax at taxable 90000 is 1000 + 3600 + 11000 = 15600, gross at top is 102000.
    assert "15,600.00" in table
    assert "102,000.00" in table


def test_cli_calc_json_output(capsys):
    code = bw.main(["calc", str(SAMPLE), "60000", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_tax"] == "6210.00"
    assert payload["take_home"] == "53790.00"
    assert len(payload["bands"]) == 4


def test_cli_lint_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.brk"
    good.write_text("band 0 inf 10%\n", encoding="utf-8")
    assert bw.main(["lint", str(good)]) == 0

    bad = tmp_path / "bad.brk"
    bad.write_text("band 0 10 10%\nband 20 inf 12%\n", encoding="utf-8")
    assert bw.main(["lint", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "bad.brk:2: gap between 10 and 20" in out
    assert "1 error(s)" in out


def test_cli_rejects_non_numeric_income():
    with pytest.raises(SystemExit) as excinfo:
        bw.main(["calc", str(SAMPLE), "lots"])
    assert excinfo.value.code == 2


def test_cli_missing_file_returns_one(tmp_path, capsys):
    code = bw.main(["table", str(tmp_path / "nope.brk")])
    assert code == 1
    assert "cannot read" in capsys.readouterr().err
