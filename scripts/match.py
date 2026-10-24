"""
match.py  (script 3, the deterministic matcher)

The production decision engine for the three way match. Deterministic code owns every
money fact. It reads the extracted invoice fields and the master tables, resolves the
vendor through the purchase order (never the low confidence vendor text), runs arithmetic
verification, then applies the tolerance rules in a fixed precedence to produce one
match_disposition per invoice. The agent never runs here.

Precedence (same order the oracle uses, so the two never disagree):
  bank change hold, duplicate, near duplicate, currency mismatch, missing GR,
  quantity over receipt, price variance (floor wins), tax, approval threshold, else auto.

Reads:
  data/extracted/*.json      the Document AI output
  data/tables/*.csv          vendors (with tax_rate), purchase_orders, goods_receipts
  config/tolerances.yaml      tunable bands and thresholds
Writes:
  data/matched/<stem>.json    disposition, reason, and the evidence behind it

Run:
  python scripts/match.py
"""

import csv
import json
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TABLES = DATA / "tables"
EXTRACTED = DATA / "extracted"
OUT = DATA / "matched"
OUT.mkdir(parents=True, exist_ok=True)


def to_float(s):
    if s is None:
        return None
    m = re.findall(r"[-+]?\d*\.?\d+", str(s).replace(",", ""))
    return float(m[0]) if m else None


def load_csv(name):
    """Load a master table. Backend-agnostic: with ACCEL_DATA_BACKEND=bq the rows come from
    BigQuery, otherwise from the local CSV. Either way the rows are identical dicts of strings, so
    the Matcher below never knows or cares which backend served them. The name keeps the .csv suffix
    for call-site compatibility; the BigQuery adapter strips it to find the table."""
    if os.environ.get("ACCEL_DATA_BACKEND", "csv").lower() == "bq":
        import bq_tables
        return bq_tables.load_table(name)
    with (TABLES / name).open() as f:
        return list(csv.DictReader(f))


def normalize_number(raw):
    return re.sub(r"[\s\-]", "", str(raw)).upper()


class Matcher:
    def __init__(self, cfg, vendors, pos, grs):
        self.cfg = cfg
        self.vendors = {v["vendor_id"]: v for v in vendors}
        self.pos = pos
        self.grs = grs
        self.seen_raw = set()
        self.seen_norm = set()

    def po_rows(self, po_id):
        return [r for r in self.pos if r["po_id"] == po_id]

    def gr_rows(self, po_id):
        return [r for r in self.grs if r["po_id"] == po_id]

    def decide(self, rec):
        f = rec["fields"]
        ev = {}

        po_id = f.get("po_id", {}).get("value")
        inv_no = f.get("invoice_number", {}).get("value")
        currency = (f.get("currency", {}).get("value") or "").upper()
        currency = "EUR" if "EUR" in currency else ("USD" if "USD" in currency else currency)
        net = to_float(f.get("net", {}).get("value"))
        tax = to_float(f.get("tax", {}).get("value"))
        total = to_float(f.get("total", {}).get("value"))
        bank = (f.get("bank_account", {}).get("value") or "").replace(" ", "")

        po = self.po_rows(po_id)
        if not po:
            return "MISSING_PO", "po_not_found", {"po_id": po_id}
        vendor_id = po[0]["vendor_id"]
        vendor = self.vendors.get(vendor_id, {})
        ev["resolved_vendor"] = vendor_id

        # arithmetic verification (controls). A break routes to review, not a silent pass.
        lines = rec.get("line_items", [])
        line_sum = round(sum((to_float(l.get("amount")) or 0.0) for l in lines), 2)
        if net is not None and abs(line_sum - net) > 0.01:
            ev["arith"] = {"line_sum": line_sum, "net": net}
            return "VERIFY_ARITHMETIC", "net_ne_line_sum", ev
        if net is not None and tax is not None and total is not None and abs((net + tax) - total) > 0.01:
            ev["arith_total"] = {"net": net, "tax": tax, "total": total}
            return "VERIFY_ARITHMETIC", "total_ne_net_plus_tax", ev

        raw_key = (vendor_id, str(inv_no))
        norm_key = (vendor_id, normalize_number(inv_no))

        # 1 bank change hold
        master_bank = (vendor.get("bank_account") or "").replace(" ", "")
        if bank and master_bank and bank != master_bank:
            ev["bank"] = {"invoice": bank, "master": master_bank}
            return "BANK_CHANGE_HOLD", "bank_mismatch", ev

        # 2 and 3 duplicates
        if raw_key in self.seen_raw:
            return "DUPLICATE", "exact_duplicate", ev
        if norm_key in self.seen_norm:
            return "NEAR_DUPLICATE", "normalized_number_collision", ev

        # 4 currency
        po_ccy = po[0].get("currency")
        if po_ccy and currency and currency != po_ccy:
            ev["currency"] = {"invoice": currency, "po": po_ccy}
            return "FLAG_CURRENCY", "currency_mismatch", ev

        # map an invoice line to the PO line with the nearest unit price. The extractor
        # returns no line number, so index mapping is unsafe (a partial invoice bills only
        # some PO lines). Production would match by a PO line reference or item description.
        def nearest(up):
            if up is None or not po:
                return None
            return min(po, key=lambda r: abs(float(r["unit_price"]) - up))

        # 5 missing GR
        grs = self.gr_rows(po_id)
        if not grs:
            return "MISSING_GR", "no_gr", ev

        # 6 quantity over receipt
        recv = {}
        for g in grs:
            recv[int(g["line_no"])] = recv.get(int(g["line_no"]), 0) + int(g["qty_received"])
        for li in lines:
            up = to_float(li.get("unit_price"))
            q = to_float(li.get("quantity"))
            pol = nearest(up)
            if pol is None or q is None:
                continue
            ln = int(pol["line_no"])
            if q > recv.get(ln, 0):
                ev["qty"] = {"line": ln, "invoiced": q, "received": recv.get(ln, 0)}
                return "FLAG_QTY", "qty_over_gr", ev

        # 7 price variance, floor wins
        pct = self.cfg["price_variance"]["pct"]
        ceil = self.cfg["price_variance"]["abs_upper"]
        floor = self.cfg["price_variance"]["abs_floor"]
        for li in lines:
            up = to_float(li.get("unit_price"))
            q = to_float(li.get("quantity")) or 0
            pol = nearest(up)
            if pol is None or up is None:
                continue
            base = float(pol["unit_price"])
            unit_delta = abs(up - base)
            line_delta = round(unit_delta * q, 2)
            if line_delta <= floor:
                continue
            if (unit_delta / base if base else 0) > pct or line_delta > ceil:
                ev["price"] = {"line": int(pol["line_no"]), "po": base,
                               "invoice": up, "line_delta": line_delta}
                return "FLAG_PRICE", "price_out_of_tolerance", ev

        # 8 tax, stated vs net times master rate
        rate = float(vendor.get("tax_rate") or 0.0)
        expected_tax = round((net or 0.0) * rate, 2)
        if tax is not None and abs(tax - expected_tax) > self.cfg["tax"]["rounding_abs"]:
            ev["tax"] = {"stated": tax, "expected": expected_tax, "rate": rate}
            return "FLAG_TAX", "tax_mismatch", ev

        # 9 approval threshold on an otherwise clean invoice
        if total is not None and total >= self.cfg["approval"]["second_approver_at"]:
            ev["total"] = total
            return "NEEDS_SECOND_APPROVER", "above_approval_threshold", ev

        return "AUTO_APPROVE", "clean", ev

    def register(self, rec):
        f = rec["fields"]
        po = self.po_rows(f.get("po_id", {}).get("value"))
        if not po:
            return
        vendor_id = po[0]["vendor_id"]
        inv_no = f.get("invoice_number", {}).get("value")
        self.seen_raw.add((vendor_id, str(inv_no)))
        self.seen_norm.add((vendor_id, normalize_number(inv_no)))


def main():
    cfg = yaml.safe_load((ROOT / "config" / "tolerances.yaml").read_text())
    m = Matcher(cfg, load_csv("vendors.csv"), load_csv("purchase_orders.csv"),
                load_csv("goods_receipts.csv"))

    files = sorted(EXTRACTED.glob("*.json"))  # arrival order; production would sort by receipt time
    print(f"matching {len(files)} invoice(s)")
    for jf in files:
        rec = json.loads(jf.read_text())
        disp, reason, ev = m.decide(rec)
        m.register(rec)
        out = {"pdf": rec["pdf"], "match_disposition": disp, "reason": reason, "evidence": ev}
        (OUT / jf.name).write_text(json.dumps(out, indent=2))
        print(f"{rec['pdf']:<18} {disp:<22} {reason}")
    print(f"\nwrote {len(files)} decision(s) to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
