"""
score_pipeline.py  (script 4b)

Scores the deterministic pipeline so far (matcher plus screen) against the frozen key on
final_disposition. After the screen, the two content cases should have moved off the auto
path, so the deterministic layer alone should now reach the final answer on all invoices.

Reads:
  data/routed/*.json    matcher plus screen result
  data/key.json         frozen truth (final_disposition)
Writes:
  nothing (prints a scorecard)

Run:
  python scripts/score_pipeline.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ROUTED = DATA / "routed"

# anything that must never silently reach an auto path
HELD_OR_FLAGGED = {"BANK_CHANGE_HOLD", "DUPLICATE", "NEAR_DUPLICATE", "FLAG_PRICE", "FLAG_QTY",
                   "MISSING_GR", "FLAG_CURRENCY", "FLAG_TAX", "REVIEW_INJECTION", "SUPERSEDED_POLICY"}
AUTO_LIKE = {"AUTO_APPROVE", "NEEDS_SECOND_APPROVER"}


def main():
    key = json.loads((DATA / "key.json").read_text())
    truth = {e["pdf"]: e["final_disposition"] for e in key["invoices"]}

    rows, correct, criticals = [], 0, []
    for jf in sorted(ROUTED.glob("*.json")):
        got = json.loads(jf.read_text())
        pdf = got["pdf"]
        exp = truth.get(pdf)
        d = got["routed_disposition"]
        ok = d == exp
        correct += int(ok)
        rows.append((pdf, exp, d, ok))
        if exp in HELD_OR_FLAGGED and d in AUTO_LIKE:
            criticals.append((pdf, exp, d))

    print("invoice            expected(final)        pipeline               result")
    for pdf, exp, d, ok in rows:
        print(f"{pdf:<18} {exp:<22} {d:<22} {'ok' if ok else 'MISS'}")

    n = len(rows)
    print(f"\ndeterministic pipeline (match + screen): {correct}/{n} on final_disposition")
    if criticals:
        print(f"CRITICAL FAILURES: {len(criticals)}")
        for pdf, exp, d in criticals:
            print(f"  {pdf}: expected {exp}, got {d}")
    else:
        print("critical failures: 0 (nothing held or flagged reached an auto path)")


if __name__ == "__main__":
    main()
