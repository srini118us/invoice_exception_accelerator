"""
dispatch_exceptions.py  (script 7b, the operational dispatcher over MCP)

After decisions are made, the operational layer turns each exception into work: a ServiceNow
incident, and a supplier notification where the vendor must act. This dispatcher connects to the
MCP server over the MCP protocol and calls its tools. It runs under its own identity (ap.ops.bot),
separate from the decider, and records every action to the audit trail.

Which exceptions get what:
  every held or flagged invoice  -> an incident (the AP team work queue)
  bank change, price, quantity, missing GR -> also a supplier notification (vendor must act)

Reads:
  data/routed/*.json, data/agent/*.json (for the cited note), data/extracted/*.json
Writes:
  data/mcp_actions.json   what was created, the backend, and the acting identity

Run:
  python scripts/dispatch_exceptions.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ROUTED = DATA / "routed"
AGENT = DATA / "agent"
EXTRACTED = DATA / "extracted"
OUT = DATA / "mcp_actions.json"
SERVER = ROOT / "scripts" / "servicenow_mcp_server.py"

ACTOR = "ap.ops.bot"          # operational identity, distinct from the decider
POSTABLE = {"AUTO_APPROVE", "NEEDS_SECOND_APPROVER"}
NOTIFY = {"BANK_CHANGE_HOLD", "FLAG_PRICE", "FLAG_QTY", "MISSING_GR"}


def tool_result_text(result):
    # FastMCP returns content blocks; pull the JSON text from the first one
    try:
        for c in result.content:
            if getattr(c, "text", None):
                return json.loads(c.text)
    except Exception:
        pass
    return {}


async def main():
    flagged = []
    for jf in sorted(ROUTED.glob("*.json")):
        r = json.loads(jf.read_text())
        if r["routed_disposition"] not in POSTABLE:
            flagged.append((jf.name, r))

    # forward the environment so the server subprocess sees SN_* and GCP credentials;
    # the MCP SDK passes only a minimal env by default, which is why backend stayed mock
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=dict(os.environ))
    actions = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            print(f"connected to MCP server, tools: {tools}")
            print(f"acting identity: {ACTOR}; dispatching {len(flagged)} exception(s)")

            for name, r in flagged:
                disp = r["routed_disposition"]
                ext = json.loads((EXTRACTED / name).read_text())
                inv = ext["fields"].get("invoice_number", {}).get("value")
                note = ""
                ap = AGENT / name
                if ap.exists():
                    a = json.loads(ap.read_text())
                    cites = ", ".join(a.get("cited_clauses", []))
                    note = f"{a.get('note','')} (clauses: {cites})" if cites else a.get("note", "")

                inc = await session.call_tool("create_incident", {
                    "short_description": f"Invoice {inv}: {disp}",
                    "description": note or f"Exception {disp} on invoice {inv}.",
                    "severity": "2" if disp == "BANK_CHANGE_HOLD" else "3",
                })
                inc_res = tool_result_text(inc)
                rec = {"invoice": inv, "disposition": disp, "actor": ACTOR,
                       "incident": inc_res}

                if disp in NOTIFY:
                    notif = await session.call_tool("send_notification", {
                        "to": f"supplier+{inv}@example.com",
                        "subject": f"Action needed on invoice {inv}",
                        "body": note or f"Invoice {inv} is on hold: {disp}.",
                    })
                    rec["notification"] = tool_result_text(notif)

                actions.append(rec)
                extra = " +notify" if "notification" in rec else ""
                if inc_res.get("backend") in (None, "servicenow_error"):
                    print(f"{inv:<12} {disp:<20} -> ERROR {json.dumps(inc_res)}")
                else:
                    print(f"{inv:<12} {disp:<20} -> incident {inc_res.get('number')} "
                          f"[{inc_res.get('backend')}]{extra}")

    OUT.write_text(json.dumps(actions, indent=2))
    backends = {a["incident"].get("backend") for a in actions}
    print(f"\nwrote {len(actions)} action(s) to {OUT.relative_to(ROOT)}; incident backend(s): {backends}")


if __name__ == "__main__":
    asyncio.run(main())
