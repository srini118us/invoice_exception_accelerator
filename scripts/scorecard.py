"""
scorecard.py  (script 9, the consolidated evaluation)

Pulls every arm into one board against the frozen key, with critical failures reported on their
own. This is the single number sheet for the README.

Reads:
  data/key.json, data/extracted/*.json, data/matched/*.json, data/routed/*.json,
  data/agent/*.json, data/ledger.json, data/mcp_actions.json, data/model_armor_probe.json
Writes:
  data/scorecard.json   the numbers, for the README

Run:
  python scripts/scorecard.py
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

POSTABLE = {"AUTO_APPROVE", "NEEDS_SECOND_APPROVER"}
HELD_OR_FLAGGED = {"BANK_CHANGE_HOLD", "DUPLICATE", "NEAR_DUPLICATE", "FLAG_PRICE", "FLAG_QTY",
                   "MISSING_GR", "FLAG_CURRENCY", "FLAG_TAX", "REVIEW_INJECTION", "SUPERSEDED_POLICY"}


def load(p, d):
    return json.loads(p.read_text()) if p.exists() else d


def to_float(s):
    m = re.findall(r"[-+]?\d*\.?\d+", str(s).replace(",", ""))
    return float(m[0]) if m else None


def main():
    key = json.loads((DATA / "key.json").read_text())
    by_pdf = {e["pdf"]: e for e in key["invoices"]}
    n = len(by_pdf)

    # extraction field accuracy
    fields = {"total": 0, "net": 0, "tax": 0, "currency": 0, "bank": 0}
    for jf in sorted((DATA / "extracted").glob("*.json")):
        rec = json.loads(jf.read_text())
        t = by_pdf.get(rec["pdf"])
        if not t:
            continue
        f = rec["fields"]
        if to_float(f.get("total", {}).get("value")) == t["total"]:
            fields["total"] += 1
        if to_float(f.get("net", {}).get("value")) == t["net"]:
            fields["net"] += 1
        if to_float(f.get("tax", {}).get("value")) == t["tax_stated"]:
            fields["tax"] += 1
        cur = (f.get("currency", {}).get("value") or "").upper()
        if ("EUR" if "EUR" in cur else "USD" if "USD" in cur else cur) == t["currency"]:
            fields["currency"] += 1
        if (f.get("bank_account", {}).get("value") or "").replace(" ", "") == t["bank_on_invoice"].replace(" ", ""):
            fields["bank"] += 1

    # rules baseline (match)
    match_ok = 0
    for jf in sorted((DATA / "matched").glob("*.json")):
        m = json.loads(jf.read_text())
        if m["match_disposition"] == by_pdf.get(m["pdf"], {}).get("match_disposition"):
            match_ok += 1

    # pipeline (match + screen) vs final
    pipe_ok, pipe_crit = 0, 0
    for jf in sorted((DATA / "routed").glob("*.json")):
        r = json.loads(jf.read_text())
        exp = by_pdf.get(r["pdf"], {}).get("final_disposition")
        if r["routed_disposition"] == exp:
            pipe_ok += 1
        if exp in HELD_OR_FLAGGED and r["routed_disposition"] in POSTABLE:
            pipe_crit += 1

    # agent arm (flagged queue)
    agent_files = sorted((DATA / "agent").glob("*.json"))
    agent_ok = cite_ok = 0
    for jf in agent_files:
        a = json.loads(jf.read_text())
        exp = by_pdf.get(a["pdf"], {}).get("final_disposition")
        if a.get("raw_disposition") == exp:
            agent_ok += 1
        if a.get("cited_clauses"):
            cite_ok += 1

    # execution
    ledger = load(DATA / "ledger.json", [])
    mcp = load(DATA / "mcp_actions.json", [])
    armor = load(DATA / "model_armor_probe.json", {})

    sc = {
        "invoices": n,
        "extraction_field_accuracy": {k: f"{v}/{n}" for k, v in fields.items()},
        "rules_baseline_match": f"{match_ok}/{n}",
        "pipeline_final": f"{pipe_ok}/{n}",
        "pipeline_critical_failures": pipe_crit,
        "agent_arm": {"flagged": len(agent_files), "correct": f"{agent_ok}/{len(agent_files)}",
                      "citation_coverage": f"{cite_ok}/{len(agent_files)}"},
        "execution": {"posted": len(ledger), "incidents": len(mcp)},
        "model_armor": {"status": armor.get("status"),
                        "default_flags_injection": armor.get("detail", {}).get("injected_flagged_default"),
                        "sensitive_flags_injection": armor.get("detail", {}).get("injected_flagged_sensitive")},
    }
    (DATA / "scorecard.json").write_text(json.dumps(sc, indent=2))

    print("=== INVOICE EXCEPTION ACCELERATOR SCORECARD ===")
    print(f"invoices: {n}")
    print("extraction field accuracy:", sc["extraction_field_accuracy"])
    print(f"rules baseline (match):   {sc['rules_baseline_match']}")
    print(f"pipeline final (match+screen): {sc['pipeline_final']}  critical failures: {pipe_crit}")
    print(f"agent arm (flagged {len(agent_files)}): correct {agent_ok}/{len(agent_files)}, "
          f"citations {cite_ok}/{len(agent_files)}")
    print(f"execution: posted {len(ledger)}, incidents {len(mcp)}")
    print(f"model armor: default flags injection = {sc['model_armor']['default_flags_injection']}, "
          f"sensitive = {sc['model_armor']['sensitive_flags_injection']}")
    print(f"\nwrote {(DATA/'scorecard.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
