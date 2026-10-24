"""
lifecycle.py  (closed loop exception lifecycle, reusable module)

Adds state to the one shot pipeline so a held exception can be resolved and closed. It reuses the
existing deterministic matcher and screen; it adds no AI agent and no cloud infrastructure. All
intake here is simulated and labeled; in production the correction events arrive over Pub/Sub or a
webhook. It keeps its own case store and its own ledger, so the existing baseline, ledger and
scores are untouched.

Four rules, matching the design distinctions:
  1 PO or GR correction revalidates the SAME invoice; it is not superseded.
  2 A replacement invoice supersedes the old one; the old case closes as SUPERSEDED and never pays,
    and the replacement is linked to the same case.
  3 Closing a ServiceNow incident is history only; it NEVER releases a hold.
  4 A revalidated invoice still runs approval (separation of duties), idempotent execution, and
    independent verification; the case closes only after verification passes.

State: OPEN -> REVALIDATED_CLEAN | REVALIDATED_EXCEPTION | SUPERSEDED -> APPROVED -> POSTED -> CLOSED

Reads: data/routed/*.json, data/extracted/*.json, data/tables/*.csv, config/tolerances.yaml
Writes: data/cases.json, data/lifecycle_ledger.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

# reuse the existing deterministic components (same directory on sys.path)
from match import Matcher, load_csv
from screen import screen_text, pdf_text
from approve_execute import approve, idempotency_key, REQUESTER, SECOND_APPROVER

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EXTRACTED = DATA / "extracted"
CASES = DATA / "cases.json"
LC_LEDGER = DATA / "lifecycle_ledger.json"

POSTABLE = {"AUTO_APPROVE", "NEEDS_SECOND_APPROVER"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _save(path, obj):
    path.write_text(json.dumps(obj, indent=2))


def _cfg():
    return yaml.safe_load((ROOT / "config" / "tolerances.yaml").read_text())


def _tables():
    return load_csv("vendors.csv"), load_csv("purchase_orders.csv"), load_csv("goods_receipts.csv")


# ---------------------------------------------------------------- case store --- #
def open_cases_from_flagged():
    """One OPEN case per flagged invoice (the non auto routed dispositions)."""
    cases = {}
    for jf in sorted((DATA / "routed").glob("*.json")):
        r = json.loads(jf.read_text())
        if r["routed_disposition"] in POSTABLE:
            continue
        ext = json.loads((EXTRACTED / jf.name).read_text())
        inv = ext["fields"].get("invoice_number", {}).get("value")
        po = ext["fields"].get("po_id", {}).get("value")
        cid = f"CASE-{Path(r['pdf']).stem}"
        cases[cid] = {"case_id": cid, "invoice_number": inv, "po_id": po, "source_pdf": r["pdf"],
                      "status": "OPEN", "original_disposition": r["routed_disposition"],
                      "superseded_by": None,
                      "history": [{"ts": _now(), "event": "opened", "detail": r["routed_disposition"]}]}
    _save(CASES, cases)
    return cases


def _log(case, event, detail):
    case["history"].append({"ts": _now(), "event": event, "detail": detail})


def close_incident(cases, case_id, note="incident closed in ServiceNow"):
    """Rule 3: incident closure is history only. It must NEVER change hold or approval state."""
    case = cases[case_id]
    _log(case, "incident_closed", note)
    # deliberately no status change
    _save(CASES, cases)
    return case["status"]


# ------------------------------------------------------------- revalidation --- #
def _revalidate_disposition(extracted, pos_override=None):
    """Re run the SAME matcher and screen on possibly corrected inputs. No new logic."""
    vendors, pos, grs = _tables()
    if pos_override:
        pos = pos_override
    disp, reason, ev = Matcher(_cfg(), vendors, pos, grs).decide(extracted)
    if disp in POSTABLE:
        stem = Path(extracted["pdf"]).stem
        new_disp, screen_reason = screen_text(pdf_text(stem))
        if new_disp:
            return new_disp, screen_reason, ev
    return disp, reason, ev


def apply_po_correction(cases, case_id, line_no, new_unit_price, authorizer):
    """Rule 1: an authorized PO correction. Revalidate the SAME invoice against corrected master
    data, applied as an in memory override so the frozen tables and baseline stay untouched."""
    case = cases[case_id]
    _, pos, _ = _tables()
    corrected = [dict(r) for r in pos]
    for r in corrected:
        if r["po_id"] == case["po_id"] and int(r["line_no"]) == int(line_no):
            r["unit_price"] = str(new_unit_price)
    _log(case, "po_correction", {"po_id": case["po_id"], "line_no": line_no,
                                 "new_unit_price": new_unit_price, "authorizer": authorizer,
                                 "source": "SIMULATED correction intake"})
    ext = json.loads((EXTRACTED / f"{Path(case['source_pdf']).stem}.json").read_text())
    disp, reason, _ = _revalidate_disposition(ext, pos_override=corrected)
    case["status"] = "REVALIDATED_CLEAN" if disp in POSTABLE else "REVALIDATED_EXCEPTION"
    case["revalidated_disposition"] = disp
    _log(case, "revalidated", {"disposition": disp, "reason": reason})
    _save(CASES, cases)
    return disp


def supersede_with_replacement(cases, case_id, replacement_extracted, authorizer):
    """Rule 2: a replacement invoice supersedes the old one. The old case closes as SUPERSEDED and
    never pays; the replacement is linked to the same case and revalidated on its own."""
    case = cases[case_id]
    repl_inv = replacement_extracted["fields"]["invoice_number"]["value"]
    case["status"] = "SUPERSEDED"
    case["superseded_by"] = repl_inv
    _log(case, "superseded", {"replacement_invoice": repl_inv, "authorizer": authorizer,
                              "source": "SIMULATED replacement intake",
                              "note": "old invoice closed, never pays"})
    # the replacement rides the same case id; revalidate it fresh
    disp, reason, _ = _revalidate_disposition(replacement_extracted)
    case["replacement_disposition"] = disp
    _log(case, "replacement_revalidated", {"invoice": repl_inv, "disposition": disp, "reason": reason})
    _save(CASES, cases)
    return disp


# ------------------------------------------------- approval, execution, verify --- #
def approve_and_post(cases, case_id, invoice_number, total, disposition):
    """Rule 4: the revalidated invoice still runs approval (SoD) and idempotent execution."""
    case = cases[case_id]
    if disposition not in POSTABLE:
        _log(case, "approval_blocked", {"disposition": disposition})
        _save(CASES, cases)
        return False, f"not postable: {disposition}"
    approver = SECOND_APPROVER if disposition == "NEEDS_SECOND_APPROVER" else None
    ok, reason = approve(disposition, total, REQUESTER, approver)
    if not ok:
        _log(case, "approval_rejected", reason)
        _save(CASES, cases)
        return False, reason
    ledger = _load(LC_LEDGER, [])
    key = idempotency_key(invoice_number, total)
    if any(e["idempotency_key"] == key for e in ledger):
        _log(case, "post_idempotent_skip", key)
        case["status"] = "POSTED"
        _save(CASES, cases); _save(LC_LEDGER, ledger)
        return True, "idempotent_skip"
    ledger.append({"idempotency_key": key, "invoice_number": invoice_number, "amount": total,
                   "case_id": case_id, "approver": approver or "auto", "status": "POSTED"})
    case["status"] = "POSTED"
    _log(case, "posted", {"invoice": invoice_number, "amount": total, "approver": approver or "auto"})
    _save(CASES, cases); _save(LC_LEDGER, ledger)
    return True, "posted"


def verify_and_close(cases, case_id, expected_invoice, expected_total):
    """Rule 4: independent verification gates closure. Re reads the lifecycle ledger. Confirms the
    expected invoice posted once at the right amount, and a superseded original is NOT in the ledger."""
    case = cases[case_id]
    ledger = _load(LC_LEDGER, [])
    rows = [e for e in ledger if e["invoice_number"] == expected_invoice]
    checks = {
        "posted_once": len(rows) == 1,
        "amount_matches": bool(rows) and abs(rows[0]["amount"] - expected_total) <= 0.01,
        "superseded_original_not_posted": (case.get("superseded_by") is None
                                           or not any(e["invoice_number"] == case["invoice_number"]
                                                      for e in ledger)),
    }
    ok = all(checks.values())
    _log(case, "verification", checks)
    if ok:
        case["status"] = "CLOSED"
        _log(case, "closed", "verification passed")
    else:
        _log(case, "close_blocked", checks)
    _save(CASES, cases)
    return ok, checks
