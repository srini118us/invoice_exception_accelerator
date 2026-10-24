"""
score_extraction.py  (script 2c)

Measures how well Document AI read the invoices, against the frozen key. Extraction is
only trustworthy if its numbers are right, so this quantifies field accuracy before the
matcher consumes the data. It also reports bank coverage, the known weak spot.

Reads:
  data/extracted/*.json   the extractor output
  data/key.json           the frozen truth
Writes:
  nothing (prints a field accuracy table)

Scope:
  Scores the money and currency fields that the matcher relies on: total, net, tax, currency.
  vendor_name is NOT scored as raw text; it is resolved against the vendor master downstream,
  so a low confidence vendor string is expected, not an error.

Run:
  python scripts/score_extraction.py
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EXTRACTED = DATA / "extracted"

MONEY_TOL = 0.01


def to_float(s):
    if s is None:
        return None
    m = re.findall(r"[-+]?\d*\.?\d+", str(s).replace(",", ""))
    return float(m[0]) if m else None


def norm_ccy(s):
    if not s:
        return None
    s = str(s).upper()
    if "EUR" in s or s == "€":
        return "EUR"
    if "USD" in s or s == "$":
        return "USD"
    return s


def main():
    key = json.loads((DATA / "key.json").read_text())
    by_pdf = {e["pdf"]: e for e in key["invoices"]}

    counts = {"total": [0, 0], "net": [0, 0], "tax": [0, 0], "currency": [0, 0]}
    bank_present = 0
    bank_correct = 0
    rows = []

    for jf in sorted(EXTRACTED.glob("*.json")):
        rec = json.loads(jf.read_text())
        truth = by_pdf.get(rec["pdf"])
        if truth is None:
            continue
        f = rec["fields"]

        def check_money(field, truth_val):
            got = to_float(f.get(field, {}).get("value"))
            ok = got is not None and abs(got - truth_val) <= MONEY_TOL
            counts[field][0] += int(ok)
            counts[field][1] += 1
            return ok, got

        t_ok, t_got = check_money("total", truth["total"])
        n_ok, _ = check_money("net", truth["net"])
        x_ok, _ = check_money("tax", truth["tax_stated"])

        got_ccy = norm_ccy(f.get("currency", {}).get("value"))
        c_ok = got_ccy == truth["currency"]
        counts["currency"][0] += int(c_ok)
        counts["currency"][1] += 1

        bank_val = f.get("bank_account", {}).get("value")
        if bank_val:
            bank_present += 1
            if bank_val.replace(" ", "") == truth["bank_on_invoice"].replace(" ", ""):
                bank_correct += 1

        rows.append((rec["pdf"], t_ok, n_ok, x_ok, c_ok, bool(bank_val)))

    print("invoice            total  net   tax   ccy   bank_seen")
    for pdf, t, n, x, c, b in rows:
        mark = lambda v: "ok " if v else "NO "
        print(f"{pdf:<18} {mark(t)}   {mark(n)}  {mark(x)}  {mark(c)}  {'yes' if b else 'no'}")

    print("\nfield accuracy vs key:")
    for field, (ok, tot) in counts.items():
        print(f"  {field:<9} {ok}/{tot}")
    n = len(rows)
    print(f"  bank      extracted on {bank_present}/{n}; of those {bank_correct} match the key")
    if bank_present < n:
        print(f"  NOTE: bank missing on {n - bank_present}/{n}. A text fallback is needed for the "
              f"bank change check (AC10009).")


if __name__ == "__main__":
    main()
