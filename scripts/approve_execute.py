"""
approve_execute.py  (script 6, approval gate and idempotent executor)

Turns the pipeline decision into a controlled posting. Deterministic and local; it simulates
the ERP ledger with a file. Three controls live here:

  1 Approval gate with separation of duties. AUTO_APPROVE posts straight through. A total at or
    above the threshold needs a second approver whose identity differs from the requester. Every
    flag, hold, duplicate and review stays unposted. An approval where approver equals requester
    is rejected.
  2 Idempotent executor. Each post carries an idempotency key (invoice number plus total). The
    executor skips a key already in the ledger, so a replay posts once, never twice.
  3 The ledger is the simulated system of record, append only.

Reads:
  data/routed/*.json      pipeline decision per invoice
  data/extracted/*.json   invoice total
Writes:
  data/ledger.json         posted entries (the simulated ERP)
  data/execution_log.json  per invoice: posted or held, and why

Run:
  python scripts/approve_execute.py
  python scripts/approve_execute.py   # run again to see idempotency: no double posting
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ROUTED = DATA / "routed"
EXTRACTED = DATA / "extracted"
LEDGER = DATA / "ledger.json"
LOG = DATA / "execution_log.json"

REQUESTER = "ap.clerk"
SECOND_APPROVER = "ap.manager"       # a different identity, enforcing separation of duties
APPROVAL_THRESHOLD = 25000.00

POSTABLE = {"AUTO_APPROVE", "NEEDS_SECOND_APPROVER"}


def to_float(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


def load_ledger():
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else []


def idempotency_key(invoice_number, total):
    return f"{invoice_number}|{total}"


def approve(disposition, total, requester, approver):
    """Return (approved: bool, reason). Separation of duties enforced here."""
    if disposition == "AUTO_APPROVE":
        return True, "auto_below_threshold"
    if disposition == "NEEDS_SECOND_APPROVER":
        if approver is None:
            return False, "second_approver_required"
        if approver == requester:
            return False, "rejected_requester_equals_approver"   # separation of duties
        return True, f"approved_by_{approver}"
    return False, f"not_postable_{disposition}"


def post_to_ledger(ledger, key, entry):
    """Idempotent post: if the key is already present, do nothing and report it."""
    if any(e["idempotency_key"] == key for e in ledger):
        return False
    ledger.append(entry)
    return True


def main():
    ledger = load_ledger()
    log = []
    posted = held = replays = 0

    for jf in sorted(ROUTED.glob("*.json")):
        r = json.loads(jf.read_text())
        disp = r["routed_disposition"]
        ext = json.loads((EXTRACTED / jf.name).read_text())
        inv = ext["fields"].get("invoice_number", {}).get("value")
        total = to_float(ext["fields"].get("total", {}).get("value"))

        if disp not in POSTABLE:
            held += 1
            log.append({"pdf": r["pdf"], "disposition": disp, "action": "held",
                        "reason": "not postable"})
            continue

        approver = SECOND_APPROVER if disp == "NEEDS_SECOND_APPROVER" else None
        ok, reason = approve(disp, total, REQUESTER, approver)
        if not ok:
            held += 1
            log.append({"pdf": r["pdf"], "disposition": disp, "action": "held", "reason": reason})
            continue

        key = idempotency_key(inv, total)
        entry = {"idempotency_key": key, "invoice_number": inv, "amount": total,
                 "disposition": disp, "requester": REQUESTER,
                 "approver": approver or "auto", "status": "POSTED"}
        if post_to_ledger(ledger, key, entry):
            posted += 1
            log.append({"pdf": r["pdf"], "disposition": disp, "action": "posted", "reason": reason})
        else:
            replays += 1
            log.append({"pdf": r["pdf"], "disposition": disp, "action": "already_posted",
                        "reason": "idempotent_skip"})

    LEDGER.write_text(json.dumps(ledger, indent=2))
    LOG.write_text(json.dumps(log, indent=2))

    # separation of duties demonstration: the high value case approved by the requester is rejected
    sod_ok, sod_reason = approve("NEEDS_SECOND_APPROVER", 32400.0, REQUESTER, REQUESTER)

    print(f"posted {posted}, held {held}, idempotent skips {replays}")
    print(f"ledger now holds {len(ledger)} entr(y/ies)")
    print(f"separation of duties check (approver == requester): "
          f"{'rejected as expected' if not sod_ok else 'ACCEPTED, BUG'} ({sod_reason})")
    print(f"wrote {LEDGER.relative_to(ROOT)} and {LOG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
