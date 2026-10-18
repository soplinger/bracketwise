#!/usr/bin/env python3
"""Bracketwise: offline progressive income-tax calculator driven by a plain-text bracket file."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

CENT = Decimal("0.01")
HUNDRED = Decimal(100)
DIRECTIVES = {"currency", "deduction", "credit", "band"}


class BracketError(Exception):
    """Raised when a bracket file cannot be read or is invalid."""


@dataclass(frozen=True)
class Band:
    lo: Decimal
    hi: Decimal | None  # None means open-ended ("inf")
    rate: Decimal  # fraction, e.g. Decimal("0.12") for 12%
    line: int


@dataclass
class Plan:
    source: str = "<string>"
    currency: str = "USD"
    deduction: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)
    bands: list[Band] = field(default_factory=list)


# ---------------------------------------------------------------- parsing


def _amount(token: str) -> Decimal:
    try:
        value = Decimal(token)
    except InvalidOperation:
        raise ValueError(f"'{token}' is not a number") from None
    if not value.is_finite() or value < 0:
        raise ValueError(f"'{token}' must be a finite amount of zero or more")
    return value


def _single_amount(args: list[str]) -> Decimal:
    if len(args) != 1:
        raise ValueError("expects exactly one amount")
    return _amount(args[0])


def _rate(token: str) -> Decimal:
    try:
        percent = Decimal(token.rstrip("%"))
    except InvalidOperation:
        raise ValueError(f"rate '{token}' is not a number") from None
    if not percent.is_finite() or not 0 <= percent <= 100:
        raise ValueError(f"rate '{token}' must be between 0 and 100 percent")
    return percent / HUNDRED


def _band(args: list[str], line: int) -> Band:
    if len(args) != 3:
        raise ValueError("band needs three values: band FROM TO RATE")
    lo = _amount(args[0])
    hi = None if args[1].lower() == "inf" else _amount(args[1])
    if hi is not None and hi <= lo:
        raise ValueError(f"band upper limit {args[1]} must be above lower limit {args[0]}")
    return Band(lo=lo, hi=hi, rate=_rate(args[2]), line=line)


def _currency(args: list[str]) -> str:
    if len(args) != 1 or len(args[0]) != 3 or not args[0].isalpha():
        raise ValueError("currency must be a three-letter code")
    return args[0].upper()


def check_bands(plan: Plan) -> list[str]:
    """Cross-band checks: contiguous from 0, open-ended top band, rates that rise."""
    where = plan.source
    if not plan.bands:
        return [f"{where}: no band lines found"]
    errors = []
    first = plan.bands[0]
    if first.lo != 0:
        errors.append(f"{where}:{first.line}: first band must start at 0, not {first.lo}")
    previous: Band | None = None
    for band in plan.bands:
        if previous is not None:
            if previous.hi is None:
                errors.append(f"{where}:{band.line}: band follows an open-ended band ('inf' must be last)")
            elif band.lo < previous.hi:
                errors.append(f"{where}:{band.line}: band starts at {band.lo}, overlapping the band below it (ends {previous.hi})")
            elif band.lo > previous.hi:
                errors.append(f"{where}:{band.line}: gap between {previous.hi} and {band.lo}")
            if band.rate < previous.rate:
                errors.append(
                    f"{where}:{band.line}: rate {_pct(band.rate)} is below the previous band's {_pct(previous.rate)}"
                )
        previous = band
    if previous is not None and previous.hi is not None:
        errors.append(f"{where}:{previous.line}: last band must end in 'inf'")
    return errors


def parse_plan(text: str, source: str = "<string>") -> tuple[Plan, list[str]]:
    """Parse bracket-file text. Returns the plan and every error as 'source:line: message'."""
    plan = Plan(source=source)
    errors: list[str] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        where = f"{source}:{number}"
        word, *args = body.split()
        directive = word.lower()
        if directive not in DIRECTIVES:
            errors.append(f"{where}: unknown directive '{word}'")
            continue
        if directive != "band" and directive in seen:
            errors.append(f"{where}: duplicate '{directive}' line")
            continue
        seen.add(directive)
        try:
            if directive == "currency":
                plan.currency = _currency(args)
            elif directive == "deduction":
                plan.deduction = _single_amount(args)
            elif directive == "credit":
                plan.credit = _single_amount(args)
            else:
                plan.bands.append(_band(args, number))
        except ValueError as exc:
            errors.append(f"{where}: {exc}")
    errors.extend(check_bands(plan))
    return plan, errors


def _read(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise BracketError(f"cannot read {path}: {exc.strerror}") from None


def load_plan(path: str | Path) -> Plan:
    plan, errors = parse_plan(_read(path), source=str(path))
    if errors:
        raise BracketError("\n".join(errors))
    return plan


# ---------------------------------------------------------------- tax maths


def _pct(rate: Decimal) -> str:
    return f"{rate * HUNDRED:.2f}%"


def _band_tax(band: Band, taxable: Decimal) -> Decimal:
    top = taxable if band.hi is None else min(taxable, band.hi)
    span = top - band.lo
    if span <= 0:
        return Decimal(0)
    return (span * band.rate).quantize(CENT, ROUND_HALF_UP)


def _bands_for(plan: Plan, taxable: Decimal) -> list[dict]:
    rows = []
    for band in plan.bands:
        top = taxable if band.hi is None else min(taxable, band.hi)
        rows.append(
            {
                "lo": band.lo,
                "hi": band.hi,
                "rate": band.rate * HUNDRED,
                "taxable": max(Decimal(0), top - band.lo),
                "tax": _band_tax(band, taxable),
            }
        )
    return rows


def marginal_rate(plan: Plan, taxable: Decimal) -> Decimal:
    """Rate on the next dollar of taxable income (fraction)."""
    for band in plan.bands:
        if taxable >= band.lo and (band.hi is None or taxable < band.hi):
            return band.rate
    return plan.bands[-1].rate


def summarize(plan: Plan, gross: Decimal) -> dict:
    """Full tax picture for one gross income. Rates in the result are percentages."""
    if gross < 0:
        raise ValueError("gross income must not be negative")
    taxable = max(Decimal(0), gross - plan.deduction)
    rows = _bands_for(plan, taxable)
    before = sum((row["tax"] for row in rows), Decimal(0))
    credit_applied = min(plan.credit, before)
    total = before - credit_applied
    effective = (total / gross * HUNDRED).quantize(CENT, ROUND_HALF_UP) if gross else Decimal(0)
    return {
        "currency": plan.currency,
        "gross": gross,
        "deduction": plan.deduction,
        "taxable": taxable,
        "bands": rows,
        "tax_before_credits": before,
        "credit_applied": credit_applied,
        "total_tax": total,
        "take_home": gross - total,
        "effective_rate": effective,
        "marginal_rate": marginal_rate(plan, taxable) * HUNDRED,
    }


def gross_tax_at_taxable(plan: Plan, taxable: Decimal) -> Decimal:
    return sum((_band_tax(band, taxable) for band in plan.bands), Decimal(0))


def _to_cents(amount: Decimal) -> int:
    return int((amount / CENT).to_integral_value(rounding=ROUND_HALF_UP))


def gross_for_net(plan: Plan, target: Decimal) -> Decimal:
    """Smallest gross income (to the cent) whose take-home is at least target."""
    if target < 0:
        raise ValueError("target take-home must not be negative")
    goal = _to_cents(target)

    def net_cents(cents: int) -> int:
        return _to_cents(summarize(plan, Decimal(cents) * CENT)["take_home"])

    if net_cents(0) >= goal:
        return Decimal(0)
    high = max(goal, 1)
    for _ in range(64):
        if net_cents(high) >= goal:
            break
        high *= 2
    else:
        raise BracketError("target take-home cannot be reached with this bracket file")
    low = 0
    while high - low > 1:
        mid = (low + high) // 2
        if net_cents(mid) >= goal:
            high = mid
        else:
            low = mid
    return Decimal(high) * CENT


def raise_effect(plan: Plan, gross: Decimal, raise_by: Decimal) -> dict:
    before = summarize(plan, gross)["take_home"]
    after = summarize(plan, gross + raise_by)["take_home"]
    gain = after - before
    kept = (gain / raise_by * HUNDRED).quantize(CENT, ROUND_HALF_UP) if raise_by else Decimal(0)
    return {"gain": gain, "tax_on_raise": raise_by - gain, "kept_percent": kept}


# ---------------------------------------------------------------- rendering


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _band_label(row: dict) -> str:
    if row["hi"] is None:
        return f"{row['lo']:,.2f} and over"
    return f"{row['lo']:,.2f} to {row['hi']:,.2f}"


def render_summary(s: dict) -> str:
    cur = s["currency"]
    lines = [
        f"Gross income      {cur} {s['gross']:,.2f}",
        f"Deduction         {cur} {s['deduction']:,.2f}",
        f"Taxable income    {cur} {s['taxable']:,.2f}",
        "",
        f"{'Band':<30}{'Taxable':>14}{'Rate':>9}{'Tax':>14}",
    ]
    for row in s["bands"]:
        lines.append(
            f"{_band_label(row):<30}{row['taxable']:>14,.2f}{row['rate']:>8.2f}%{row['tax']:>14,.2f}"
        )
    lines += [
        "",
        f"Tax before credits {cur} {s['tax_before_credits']:,.2f}",
        f"Credit applied     {cur} {s['credit_applied']:,.2f}",
        f"Total tax          {cur} {s['total_tax']:,.2f}",
        f"Take-home          {cur} {s['take_home']:,.2f}",
        f"Effective rate     {s['effective_rate']:.2f}%",
        f"Marginal rate      {s['marginal_rate']:.2f}%  (keep {100 - s['marginal_rate']:.2f}% of the next {cur} 1)",
    ]
    return "\n".join(lines)


def render_table(plan: Plan) -> str:
    cur = plan.currency
    lines = [
        f"Brackets in {cur} (tax shown before credits; gross = taxable + deduction)",
        f"{'From':>14} {'To':>14} {'Rate':>8} {'Tax at top':>14} {'Gross at top':>14} {'Eff. rate':>10}",
    ]
    for band in plan.bands:
        rate = f"{band.rate * HUNDRED:.2f}%"
        if band.hi is None:
            lines.append(f"{band.lo:>14,.2f} {'and over':>14} {rate:>8} {'':>14} {'':>14} {'':>10}")
            continue
        tax_at = gross_tax_at_taxable(plan, band.hi)
        gross_at = band.hi + plan.deduction
        effective = (tax_at / gross_at * HUNDRED) if gross_at else Decimal(0)
        lines.append(
            f"{band.lo:>14,.2f} {band.hi:>14,.2f} {rate:>8} {tax_at:>14,.2f} {gross_at:>14,.2f} {effective:>9.2f}%"
        )
    return "\n".join(lines)


def compare_results(gross: Decimal, plan_a: Plan, plan_b: Plan) -> dict:
    a = {"name": plan_a.source, "summary": summarize(plan_a, gross)}
    b = {"name": plan_b.source, "summary": summarize(plan_b, gross)}
    diff = b["summary"]["total_tax"] - a["summary"]["total_tax"]
    return {"gross": gross, "a": a, "b": b, "difference_tax": diff}


def render_compare(result: dict) -> str:
    cur = result["a"]["summary"]["currency"]
    diff = result["difference_tax"]
    if diff > 0:
        lower = "file A"
    elif diff < 0:
        lower = "file B"
    else:
        lower = "tie"
    lines = [
        f"Gross income {cur} {result['gross']:,.2f}",
        f"{'':<4}{'File':<28}{'Total tax':>14}{'Take-home':>14}{'Effective':>11}",
    ]
    for label, key in (("A", "a"), ("B", "b")):
        item = result[key]
        s = item["summary"]
        lines.append(
            f"{label:<4}{item['name'][:27]:<28}{s['total_tax']:>14,.2f}{s['take_home']:>14,.2f}{s['effective_rate']:>10.2f}%"
        )
    lines.append(f"Tax difference (B - A): {cur} {diff:,.2f}")
    lines.append(f"Lower tax: {lower}")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI


def _cli_amount(token: str) -> Decimal:
    try:
        return _amount(token)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bracketwise", description="Progressive income-tax calculator.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lint", help="check a bracket file for errors")
    p.add_argument("file")

    p = sub.add_parser("calc", help="tax on one income, band by band")
    p.add_argument("file")
    p.add_argument("income", type=_cli_amount)
    p.add_argument("--json", action="store_true", help="print JSON")

    p = sub.add_parser("table", help="print the bracket table with thresholds")
    p.add_argument("file")

    p = sub.add_parser("net", help="gross income needed for a target take-home")
    p.add_argument("file")
    p.add_argument("target", type=_cli_amount)

    p = sub.add_parser("raise", help="take-home gain from a raise")
    p.add_argument("file")
    p.add_argument("income", type=_cli_amount)
    p.add_argument("amount", type=_cli_amount)

    p = sub.add_parser("compare", help="compare two bracket files on one income")
    p.add_argument("file_a")
    p.add_argument("file_b")
    p.add_argument("income", type=_cli_amount)
    p.add_argument("--json", action="store_true", help="print JSON")
    return parser


def _lint(path: str) -> int:
    plan, errors = parse_plan(_read(path), source=path)
    if errors:
        for message in errors:
            print(message)
        print(f"{len(errors)} error(s) in {path}")
        return 1
    print(f"OK: {path} has {len(plan.bands)} bands, {plan.currency}, deduction {plan.deduction}, credit {plan.credit}")
    return 0


def _dump(payload) -> None:
    print(json.dumps(_jsonable(payload), indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "lint":
            return _lint(args.file)

        if args.command == "table":
            print(render_table(load_plan(args.file)))
            return 0

        if args.command == "calc":
            summary = summarize(load_plan(args.file), args.income)
            if args.json:
                _dump(summary)
            else:
                print(render_summary(summary))
            return 0

        if args.command == "net":
            plan = load_plan(args.file)
            gross = gross_for_net(plan, args.target)
            s = summarize(plan, gross)
            print(f"Gross needed for {plan.currency} {args.target:,.2f} take-home: {plan.currency} {gross:,.2f}")
            print(f"Tax at that income: {plan.currency} {s['total_tax']:,.2f} (take-home {plan.currency} {s['take_home']:,.2f})")
            return 0

        if args.command == "raise":
            plan = load_plan(args.file)
            effect = raise_effect(plan, args.income, args.amount)
            cur = plan.currency
            print(f"Raise of {cur} {args.amount:,.2f} on income {cur} {args.income:,.2f}")
            print(f"Take-home gain   {cur} {effect['gain']:,.2f}")
            print(f"Tax on raise     {cur} {effect['tax_on_raise']:,.2f}")
            print(f"You keep         {effect['kept_percent']:.2f}% of the raise")
            return 0

        if args.command == "compare":
            result = compare_results(args.income, load_plan(args.file_a), load_plan(args.file_b))
            if args.json:
                _dump(result)
            else:
                print(render_compare(result))
            return 0
    except BracketError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
