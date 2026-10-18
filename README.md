# Bracketwise

**Know exactly what each band of a progressive tax costs you.**

Bracketwise is an offline income-tax calculator driven by one plain-text bracket file. Give it a gross income and it shows the tax paid band by band, the effective and marginal rates, the take-home pay, the gross income needed for a target take-home, and the take-home gain from a raise. Exact cents throughout, no network, no accounts, no dependencies beyond the Python standard library.

> The bundled `examples/sample.brk` uses invented numbers for demonstration. It is not real tax law. Write your own bracket file from your jurisdiction's published schedule.

## Features

1. **Plain-text bracket files.** Band, deduction, credit and currency lines in a file you can read, diff and keep in version control.
2. **Band-by-band tax breakdown.** `calc` shows the taxable amount, rate and tax for each band.
3. **Exact cents.** All money is `Decimal`. Each band's tax is rounded half-up to the cent, and totals are sums of those rounded values.
4. **Standard deduction.** Deducted from gross before the bands apply; income below it pays no tax.
5. **Nonrefundable credit.** Reduces tax, never below zero.
6. **Effective and marginal rates.** Effective is total tax over gross; marginal is the rate on the next dollar, with the share you keep.
7. **Bracket table.** `table` lists each band with the tax and effective rate at its upper limit.
8. **Target take-home solver.** `net` finds the smallest gross income, to the cent, that delivers a chosen take-home.
9. **Raise calculator.** `raise` shows the take-home gain from a raise and the share you keep.
10. **Two-file comparison.** `compare` puts two bracket files side by side on the same income, with the tax difference.
11. **Line-numbered linter.** `lint` reports unknown directives, bad amounts, gaps, overlaps, a missing `inf` top band and falling rates, each as `file:line: message`.
12. **JSON output.** `calc --json` and `compare --json` print machine-readable results.
13. **CI-friendly exit codes.** `lint` exits 0 when the file is clean and 1 when it has errors; unreadable files exit 1; bad arguments exit 2.

## Quick start

Bracket file format (`examples/sample.brk`):

```text
# Illustrative bracket file. These numbers are invented for the demo, not real tax law.
currency USD
deduction 12000
credit 150

band 0 10000 10%
band 10000 40000 12%
band 40000 90000 22%
band 90000 inf 32%
```

Check the file, then calculate tax on a gross income of 60,000:

```console
$ python3 bracketwise.py lint examples/sample.brk
OK: examples/sample.brk has 4 bands, USD, deduction 12000, credit 150

$ python3 bracketwise.py calc examples/sample.brk 60000
Gross income      USD 60,000.00
Deduction         USD 12,000.00
Taxable income    USD 48,000.00

Band                                 Taxable     Rate           Tax
0.00 to 10,000.00                  10,000.00   10.00%      1,000.00
10,000.00 to 40,000.00             30,000.00   12.00%      3,600.00
40,000.00 to 90,000.00              8,000.00   22.00%      1,760.00
90,000.00 and over                      0.00   32.00%          0.00

Tax before credits USD 6,360.00
Credit applied     USD 150.00
Total tax          USD 6,210.00
Take-home          USD 53,790.00
Effective rate     10.35%
Marginal rate      22.00%  (keep 78.00% of the next USD 1)
```

Other commands on the same file:

```console
$ python3 bracketwise.py net examples/sample.brk 50000
Gross needed for USD 50,000.00 take-home: USD 55,141.02
Tax at that income: USD 5,141.02 (take-home USD 50,000.00)

$ python3 bracketwise.py raise examples/sample.brk 60000 3000
Raise of USD 3,000.00 on income USD 60,000.00
Take-home gain   USD 2,340.00
Tax on raise     USD 660.00
You keep         78.00% of the raise
```

Run `python3 bracketwise.py table FILE` for the full bracket table, `python3 bracketwise.py compare A B INCOME` to compare two schedules, and add `--json` to `calc` or `compare` for machine-readable output.

## Bracket file reference

| Line | Meaning |
| --- | --- |
| `currency CODE` | Three-letter currency code, default `USD`. |
| `deduction AMOUNT` | Subtracted from gross before the bands. At most once. |
| `credit AMOUNT` | Nonrefundable credit subtracted from tax, floored at zero. At most once. |
| `band FROM TO RATE` | Taxable income from `FROM` up to `TO`, taxed at `RATE` (for example `12%` or `12`). Use `inf` for `TO` on the top band. |

Bands must start at 0, join with no gaps or overlaps, have rates that never fall, and end with `inf`. Comments start with `#`.

## How to run

Requires Python 3. No packages to install for the tool itself.

```console
$ python3 bracketwise.py --help
```

Run from the repository root, or pass the full path to `bracketwise.py`.

## How to test

The tests use pytest, the only third-party dependency.

```console
$ python3 -m pip install pytest
$ python3 -m pytest
```

The tests cover parsing and line-numbered errors, hand-checked tax totals, rounding, the credit floor, boundary marginal rates, the target take-home solver (including the unreachable case), raise and comparison maths, table output, JSON output and CLI exit codes.

## Project layout

```text
bracketwise.py          the tool: parser, tax maths, renderers and CLI
examples/sample.brk     illustrative bracket file used in the quick start
tests/conftest.py       puts the project root on the import path
tests/test_bracketwise.py
README.md
```
