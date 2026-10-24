"""
oracle_validate.py  (script 1b)

An independent check on the frozen key. NOT part of the production pipeline.

The oracle re derives the match_disposition for every invoice straight from the
tables and the invoice line facts, using its own logic, and compares the result to
key.json. It deliberately shares no code with the production matcher (match.py),
which does not exist yet. If the oracle and the key disagree, the key has a bug and
every later score is untrustworthy, so extraction must not start until this passes.

What it reads:
  data/tables/vendors.csv, purchase_orders.csv, goods_receipts.csv
  data/key.json          (reads the invoice FACTS, never the expected answer, for deriving)

What it produces:
  a pass or fail line per invoice on match_disposition
  a mutation check that corrupts one PO price and confirms a disposition flips
  exit code 0 only if every invoice agrees and the mutation is detected

Scope note:
  The oracle validates match_disposition only, the deterministically derivable layer.
  Two invoices (prompt injection, superseded policy) pass the three way match cleanly;
  their final_disposition is decided later by the agent and policy layer, so the oracle
  checks only that their match_disposition is AUTO_APPROVE, not the content decision.

Run:
  python scripts/oracle_validate.py
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "data" / "tables"
KEY_PATH = ROOT / "data" / "key.json"

# ---- tolerance values re stated here on purpose --------------------------------
# The oracle encodes the policy independently. It does NOT import tolerances.yaml or
# the matcher. If a maintainer changes the config, the oracle must be updated by hand
# and the agreement re checked. That friction is the control working as intended.
PCT_BAND = 0.05
ABS_CEILING = 100.00      # on line value delta
ABS_FLOOR = 15.00         # on line value delta
TAX_ROUNDING = 1.00
APPROVAL_AT = 25000.00


def load_csv(name):
    with (TABLES / name).open() as f:
        return list(csv.DictReader(f))


def derive_match_disposition(inv, vendors, po_lines, receipts, seen_raw, seen_norm):
    """Independent derivation. Order of checks is the oracle's own precedence, hard
    holds first, then the three way match, then value checks. match.py must adopt the
    same precedence or it will disagree with the oracle, which is the point of 1b."""
    vendor = next((v for v in vendors if v["vendor_id"] == inv["vendor_id"]), None)
    po = [r for r in po_lines if r["po_id"] == inv["po_id"]]
    grs = [r for r in receipts if r["po_id"] == inv["po_id"]]

    raw = inv["invoice_number"]
    norm = inv["normalized_number"]

    # 1 bank detail change, hardest hold, money safety first
    if vendor is not None and inv["bank_on_invoice"] != vendor["bank_account"]:
        return "BANK_CHANGE_HOLD"

    # 2 and 3 duplicates, by vendor
    key_raw = (inv["vendor_id"], raw)
    key_norm = (inv["vendor_id"], norm)
    if key_raw in seen_raw:
        return "DUPLICATE"
    if key_norm in seen_norm:
        return "NEAR_DUPLICATE"

    # 4 currency must equal the po currency
    po_ccy = po[0]["currency"] if po else None
    if po_ccy is not None and inv["currency"] != po_ccy:
        return "FLAG_CURRENCY"

    # 5 missing goods receipt
    if not grs:
        return "MISSING_GR"

    # 6 quantity over receipt, per line
    recv_by_line = {}
    for g in grs:
        recv_by_line[int(g["line_no"])] = recv_by_line.get(int(g["line_no"]), 0) + int(g["qty_received"])
    for ln in inv["lines"]:
        received = recv_by_line.get(ln["line_no"], 0)
        if ln["qty"] > received:
            return "FLAG_QTY"

    # 7 price variance, pct on unit price OR absolute on line value delta
    po_price = {int(r["line_no"]): float(r["unit_price"]) for r in po}
    for ln in inv["lines"]:
        base = po_price.get(ln["line_no"])
        if base is None:
            continue
        unit_delta = abs(ln["unit_price"] - base)
        line_value_delta = unit_delta * ln["qty"]
        if line_value_delta <= ABS_FLOOR:
            continue
        pct = unit_delta / base if base else 0.0
        if pct > PCT_BAND or line_value_delta > ABS_CEILING:
            return "FLAG_PRICE"

    # 8 tax check: the invoice's own stated tax amount must equal net times its stated
    # rate, within rounding. This is an on invoice arithmetic check, no external rate needed.
    net = round(sum(l["qty"] * l["unit_price"] for l in inv["lines"]), 2)
    expected_tax = round(net * float(inv.get("tax_rate_stated", 0.0)), 2)
    if abs(inv["tax_stated"] - expected_tax) > TAX_ROUNDING:
        return "FLAG_TAX"

    # 9 approval threshold on an otherwise clean invoice
    if inv["total"] >= APPROVAL_AT:
        return "NEEDS_SECOND_APPROVER"

    return "AUTO_APPROVE"


def run(po_lines_override=None):
    vendors = load_csv("vendors.csv")
    po_lines = po_lines_override if po_lines_override is not None else load_csv("purchase_orders.csv")
    receipts = load_csv("goods_receipts.csv")
    key = json.loads(KEY_PATH.read_text())

    seen_raw, seen_norm = set(), set()
    results = []
    for inv in key["invoices"]:
        derived = derive_match_disposition(inv, vendors, po_lines, receipts, seen_raw, seen_norm)
        expected = inv["match_disposition"]
        results.append((inv["pdf"], expected, derived, derived == expected))
        # register AFTER deciding, so a duplicate sees the earlier one, not itself
        seen_raw.add((inv["vendor_id"], inv["invoice_number"]))
        seen_norm.add((inv["vendor_id"], inv["normalized_number"]))
    return results


def main():
    results = run()
    ok = all(r[3] for r in results)
    print("invoice            expected(match)        derived(match)         result")
    for pdf, exp, der, good in results:
        print(f"{pdf:<18} {exp:<22} {der:<22} {'ok' if good else 'MISMATCH'}")
    agree = sum(1 for r in results if r[3])
    print(f"\nagreement: {agree}/{len(results)} on match_disposition")

    # mutation check: corrupt one PO price, confirm at least one disposition changes
    po_lines = load_csv("purchase_orders.csv")
    mutated = [dict(r) for r in po_lines]
    for r in mutated:
        if r["po_id"] == "PO-5001" and r["line_no"] == "1":
            r["unit_price"] = str(float(r["unit_price"]) + 25.0)   # clean invoice's PO, forces a flip
    mut_results = run(po_lines_override=mutated)
    changed = sum(1 for a, b in zip(results, mut_results) if a[2] != b[2])
    mutation_ok = changed >= 1
    print(f"mutation check: corrupted one PO price, {changed} disposition(s) changed -> "
          f"{'detected' if mutation_ok else 'NOT DETECTED'}")

    if ok and mutation_ok:
        print("\nORACLE PASS: key is consistent and the oracle computes independently.")
        sys.exit(0)
    print("\nORACLE FAIL: do not proceed to extraction until resolved.")
    sys.exit(1)


if __name__ == "__main__":
    main()
