"""
score_match.py  (script 3b, the rules only baseline arm)

Scores the deterministic matcher against the frozen key on match_disposition. This is the
rules only baseline: the number the agent must beat or match. Critical failures are reported
on their own and never averaged into an accuracy percentage, because a single wrong money
decision matters more than many right ones.

Reads:
  data/matched/*.json   the matcher output
  data/key.json         the frozen truth
Writes:
  nothing (prints a scorecard)

Run:
  python scripts/score_match.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MATCHED = DATA / "matched"

# A decision that lets money move when it should have been held. Missing any of these on a
# planted case is a critical failure, not a rounding error.
CRITICAL_IF_AUTO = {"BANK_CHANGE_HOLD", "DUPLICATE", "NEAR_DUPLICATE", "FLAG_PRICE",
                    "FLAG_QTY", "MISSING_GR", "FLAG_CURRENCY", "FLAG_TAX"}
AUTO_LIKE = {"AUTO_APPROVE", "NEEDS_SECOND_APPROVER"}


def main():
    key = json.loads((DATA / "key.json").read_text())
    truth = {e["pdf"]: e["match_disposition"] for e in key["invoices"]}

    rows = []
    correct = 0
    criticals = []
    for jf in sorted(MATCHED.glob("*.json")):
        got = json.loads(jf.read_text())
        pdf = got["pdf"]
        exp = truth.get(pdf)
        d = got["match_disposition"]
        ok = d == exp
        correct += int(ok)
        rows.append((pdf, exp, d, ok))
        # critical: a case that should have been held or flagged was let onto an auto path
        if exp in CRITICAL_IF_AUTO and d in AUTO_LIKE:
            criticals.append((pdf, exp, d))

    print("invoice            expected               matcher                result")
    for pdf, exp, d, ok in rows:
        print(f"{pdf:<18} {exp:<22} {d:<22} {'ok' if ok else 'MISS'}")

    n = len(rows)
    print(f"\nrules baseline: {correct}/{n} disposition match")
    if criticals:
        print(f"CRITICAL FAILURES: {len(criticals)} (a held or flagged case went to an auto path)")
        for pdf, exp, d in criticals:
            print(f"  {pdf}: expected {exp}, got {d}")
    else:
        print("critical failures: 0 (no held or flagged case reached an auto path)")


if __name__ == "__main__":
    main()
