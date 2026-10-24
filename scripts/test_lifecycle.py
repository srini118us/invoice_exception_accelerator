"""
test_lifecycle.py  (the one additive lifecycle evaluation)

Demonstrates the closed loop on top of the frozen baseline, which it does not touch. Two scenarios:

  A  PO correction (the requested demo): a held price variance invoice is resolved by an authorized
     PO correction, revalidated to clean, approved, posted once, and closed only after verification.
     Also proves that closing the incident alone does NOT release the hold.
  B  Replacement invoice (supersession): a held quantity exception is resolved by a replacement
     invoice that supersedes the original. The original closes as SUPERSEDED and never pays; the
     replacement is approved, posted once, and verified. No duplicate payment.

All intake here is SIMULATED and labeled. The baseline 16 invoice key and scores are untouched;
this uses its own case store and its own ledger.

Run:
  python scripts/test_lifecycle.py
"""

import json
from pathlib import Path

import lifecycle as lc

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def reset_lifecycle_ledger():
    (DATA / "lifecycle_ledger.json").write_text("[]")


def main():
    reset_lifecycle_ledger()
    cases = lc.open_cases_from_flagged()
    results = {}

    # ---------------- Scenario A: PO correction on the held price variance -------- #
    a_id = "CASE-AC10004"
    # rule 3: closing the incident must not release the hold
    status_after_incident = lc.close_incident(cases, a_id)
    results["A_incident_close_does_not_release"] = (status_after_incident == "OPEN")

    disp_a = lc.apply_po_correction(cases, a_id, line_no=1, new_unit_price=50.00,
                                    authorizer="buyer.jane")
    results["A_revalidated_clean"] = (disp_a == "AUTO_APPROVE")

    ok1, _ = lc.approve_and_post(cases, a_id, "AC-10004", 1500.00, disp_a)
    ok2, reason2 = lc.approve_and_post(cases, a_id, "AC-10004", 1500.00, disp_a)  # replay
    results["A_posted_once"] = (ok1 and ok2 and reason2 == "idempotent_skip")

    vok_a, checks_a = lc.verify_and_close(cases, a_id, "AC-10004", 1500.00)
    results["A_verified_and_closed"] = (vok_a and cases[a_id]["status"] == "CLOSED")

    # ---------------- Scenario B: replacement invoice supersedes the old ---------- #
    b_id = "CASE-AC10005"
    replacement = {"pdf": "AC10005R.pdf", "fields": {
        "invoice_number": {"value": "AC-10005R"}, "po_id": {"value": "PO-5008"},
        "currency": {"value": "USD"}, "net": {"value": "5580"}, "tax": {"value": "0"},
        "total": {"value": "5580"}, "bank_account": {"value": "US64SVBK00000012345678"}},
        "line_items": [{"quantity": "18", "unit_price": "310", "amount": "5580"}]}
    disp_b = lc.supersede_with_replacement(cases, b_id, replacement, authorizer="buyer.jane")
    results["B_old_superseded"] = (cases[b_id]["status"] == "SUPERSEDED"
                                   and cases[b_id]["superseded_by"] == "AC-10005R")
    results["B_replacement_clean"] = (disp_b == "AUTO_APPROVE")

    lc.approve_and_post(cases, b_id, "AC-10005R", 5580.00, disp_b)
    vok_b, checks_b = lc.verify_and_close(cases, b_id, "AC-10005R", 5580.00)
    results["B_no_duplicate_payment"] = checks_b["superseded_original_not_posted"]
    results["B_verified_and_closed"] = (vok_b and cases[b_id]["status"] == "CLOSED")

    # ---------------- report ------------------------------------------------------ #
    print("=== LIFECYCLE EVALUATION (additive; baseline untouched) ===")
    for k, v in results.items():
        print(f"  {'ok ' if v else 'FAIL'} {k}")
    ledger = json.loads((DATA / 'lifecycle_ledger.json').read_text())
    print(f"\nlifecycle ledger: {[ (e['invoice_number'], e['amount']) for e in ledger ]}")
    print(f"AC10004 case status: {cases['CASE-AC10004']['status']}")
    print(f"AC10005 case status: {cases['CASE-AC10005']['status']} "
          f"(superseded_by {cases['CASE-AC10005']['superseded_by']})")
    allok = all(results.values())
    print(f"\n{'ALL LIFECYCLE CHECKS PASS' if allok else 'LIFECYCLE CHECKS FAILED'}")
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())
