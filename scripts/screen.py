"""
screen.py  (script 4, the deterministic content screen)

Runs only on invoices the matcher marked clean (AUTO_APPROVE or NEEDS_SECOND_APPROVER).
Two problems hide in document content and policy, not in the numbers, so the three way
match passes them. Before any clean invoice reaches the auto path, this screen reads the
invoice text and routes two kinds off it:

  1 injected instruction: text that tells the processor what to do (approve, ignore checks).
    Treated as data, never obeyed. Routed to REVIEW_INJECTION.
  2 unsupported claim: a discount or deduction the invoice asserts. Current policy does not
    apply early payment discounts automatically, so any such claim is routed for a policy
    check (SUPERSEDED_POLICY candidate). The agent later cites the exact clause.

This is deterministic and local. It decides nothing about money; it only diverts suspicious
clean invoices into the flagged queue the agent investigates.

Reads:
  data/matched/*.json     matcher decisions
  data/invoices/*.pdf      invoice text, read locally with pypdf (no GCP)
Writes:
  data/routed/<stem>.json  {match_disposition, screen_reason, routed_disposition}

Run:
  python scripts/screen.py
"""

import json
import re
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MATCHED = DATA / "matched"
INVOICES = DATA / "invoices"
OUT = DATA / "routed"
OUT.mkdir(parents=True, exist_ok=True)

AUTO_PATH = {"AUTO_APPROVE", "NEEDS_SECOND_APPROVER"}

# instruction like content that does not belong on an invoice. Case insensitive.
INJECTION_PATTERNS = [
    r"ignore (all|any|prior|previous|the)",
    r"disregard (all|any|prior|previous|the)",
    r"mark (this )?invoice (as )?approved",
    r"approve(d)? for immediate payment",
    r"system note",
    r"\boverride\b",
    r"do not (check|verify|review)",
]

# a claim of a discount or deduction the invoice asserts for itself.
CLAIM_PATTERNS = [
    r"early[ -]?payment discount",
    r"\bdiscount applied\b",
    r"\bdeduction\b",
    r"per (prior|previous|old) .*policy",
]


def pdf_text(stem):
    path = INVOICES / f"{stem}.pdf"
    if not path.exists():
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:
        return ""


def screen_text(text):
    low = text.lower()
    for pat in INJECTION_PATTERNS:
        if re.search(pat, low):
            return "REVIEW_INJECTION", "prompt_injection"
    for pat in CLAIM_PATTERNS:
        if re.search(pat, low):
            return "SUPERSEDED_POLICY", "relies_on_superseded_clause"
    return None, None


def main():
    files = sorted(MATCHED.glob("*.json"))
    print(f"screening {len(files)} invoice(s); clean ones get a content check")
    routed_count = 0
    for jf in files:
        m = json.loads(jf.read_text())
        disp = m["match_disposition"]
        stem = Path(m["pdf"]).stem
        screen_reason = None
        routed = disp
        if disp in AUTO_PATH:
            new_disp, reason = screen_text(pdf_text(stem))
            if new_disp:
                routed = new_disp
                screen_reason = reason
                routed_count += 1
        out = {"pdf": m["pdf"], "match_disposition": disp,
               "screen_reason": screen_reason, "routed_disposition": routed}
        (OUT / jf.name).write_text(json.dumps(out, indent=2))
        flag = f"  <- screen: {screen_reason}" if screen_reason else ""
        print(f"{m['pdf']:<18} {disp:<22} -> {routed:<22}{flag}")
    print(f"\nrouted {routed_count} clean invoice(s) off the auto path into the flagged queue")


if __name__ == "__main__":
    main()
