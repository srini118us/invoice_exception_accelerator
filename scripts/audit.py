"""
audit.py  (script 8, the audit trail)

Stitches every stage into one reconstructable record per invoice, so an auditor or architect can
later see the full story: what was extracted, what the matcher decided and on what evidence, what
the screen routed, what the agent concluded and which clauses it cited, who approved, what posted,
and which incident was raised, each with its acting identity and a correlation id.

In production each stage would append its own record in real time. Here the trail is assembled
from the immutable stage outputs, which is equivalent for review.

Reads:
  data/extracted/*.json, data/matched/*.json, data/routed/*.json, data/agent/*.json,
  data/ledger.json, data/execution_log.json, data/mcp_actions.json
Writes:
  data/audit_trail.jsonl   one JSON line per invoice, every stage with identity and timestamp

Run:
  python scripts/audit.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def by_invoice(records, keyfn):
    out = {}
    for r in records:
        out.setdefault(keyfn(r), []).append(r)
    return out


def main():
    ts = datetime.now(timezone.utc).isoformat()
    ledger = {e["invoice_number"]: e for e in load(DATA / "ledger.json", [])}
    exec_log = {Path(e["pdf"]).stem: e for e in load(DATA / "execution_log.json", [])}
    mcp = {a["invoice"]: a for a in load(DATA / "mcp_actions.json", [])}

    lines = []
    for jf in sorted((DATA / "routed").glob("*.json")):
        stem = jf.stem
        ext = load(DATA / "extracted" / jf.name, {})
        matched = load(DATA / "matched" / jf.name, {})
        routed = load(jf, {})
        agent = load(DATA / "agent" / jf.name, {})
        f = ext.get("fields", {})
        inv = f.get("invoice_number", {}).get("value")

        stages = []
        # extraction
        gate = ext.get("fields_below_gate", [])
        stages.append({"stage": "extraction", "actor": "document_ai",
                       "detail": {"fields_below_gate": gate}})
        # match
        stages.append({"stage": "match", "actor": "rules.engine",
                       "detail": {"disposition": matched.get("match_disposition"),
                                  "reason": matched.get("reason"),
                                  "evidence": matched.get("evidence")}})
        # screen
        stages.append({"stage": "screen", "actor": "rules.engine",
                       "detail": {"screen_reason": routed.get("screen_reason"),
                                  "routed_disposition": routed.get("routed_disposition")}})
        # agent (only if it ran)
        if agent:
            stages.append({"stage": "investigation", "actor": "ap_investigator.gemini",
                           "detail": {"raw": agent.get("raw_disposition"),
                                      "guarded": agent.get("guarded_disposition"),
                                      "cited_clauses": agent.get("cited_clauses"),
                                      "note": agent.get("note"), "guard": agent.get("guard")}})
        # execution
        el = exec_log.get(stem, {})
        led = ledger.get(inv)
        stages.append({"stage": "execution",
                       "actor": (led or {}).get("approver", "ap.clerk"),
                       "detail": {"action": el.get("action"), "reason": el.get("reason"),
                                  "ledger": led}})
        # operations
        if inv in mcp:
            m = mcp[inv]
            stages.append({"stage": "operations", "actor": m.get("actor", "ap.ops.bot"),
                           "detail": {"incident": m.get("incident"),
                                      "notification": m.get("notification")}})

        lines.append({"correlation_id": stem, "invoice_number": inv,
                      "final_disposition": routed.get("routed_disposition"),
                      "assembled_at": ts, "stages": stages})

    out = DATA / "audit_trail.jsonl"
    out.write_text("\n".join(json.dumps(l) for l in lines))
    print(f"wrote {len(lines)} audit record(s) to {out.relative_to(ROOT)}")
    # show one full record so the shape is visible
    sample = next((l for l in lines if l["invoice_number"] == "AC-10009"), lines[0])
    print("\nsample record (AC-10009, the bank hold):")
    print(json.dumps(sample, indent=2))


if __name__ == "__main__":
    main()
