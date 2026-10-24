"""
servicenow_mcp_server.py  (script 7a, an MCP server)

Exposes two operational tools over the MCP protocol so the dispatcher (and, later, any agent)
can reach an external system through one standard interface:

  create_incident(short_description, description, severity)
      Creates an incident in ServiceNow via the Table API when instance credentials are set
      (SN_INSTANCE, SN_USER, SN_PASS). Without them it records a mock incident locally, so the
      MCP pattern runs with no external dependency. Same tool, swappable backend.
  send_notification(to, subject, body)
      Records a supplier notification locally (a safe mock, never sends real email).

This is a real MCP server (stdio). The dispatcher launches it and calls these tools over MCP.

Run: launched automatically by dispatch_exceptions.py; not run by hand.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MOCK_INCIDENTS = DATA / "mock_incidents.json"
NOTIFICATIONS = DATA / "notifications.json"

SN_INSTANCE = os.environ.get("SN_INSTANCE")
SN_USER = os.environ.get("SN_USER")
SN_PASS = os.environ.get("SN_PASS")

mcp = FastMCP("servicenow-ap")


def _append(path, record):
    data = json.loads(path.read_text()) if path.exists() else []
    data.append(record)
    path.write_text(json.dumps(data, indent=2))


@mcp.tool()
def create_incident(short_description: str, description: str, severity: str = "3") -> dict:
    """Create an incident for an invoice exception. Uses ServiceNow when credentials are set,
    otherwise records a mock incident locally. Returns the incident number and the backend used."""
    if SN_INSTANCE and SN_USER and SN_PASS:
        url = f"https://{SN_INSTANCE}.service-now.com/api/now/table/incident"
        payload = {"short_description": short_description, "description": description,
                   "urgency": severity, "category": "inquiry"}
        try:
            resp = requests.post(url, auth=(SN_USER, SN_PASS),
                                 headers={"Content-Type": "application/json", "Accept": "application/json"},
                                 data=json.dumps(payload), timeout=30)
        except Exception as e:
            return {"backend": "servicenow_error", "error": f"request failed: {e}"}
        if resp.status_code >= 300:
            return {"backend": "servicenow_error", "status_code": resp.status_code,
                    "body": resp.text[:300]}
        rec = resp.json().get("result", {})
        return {"backend": "servicenow", "number": rec.get("number"), "sys_id": rec.get("sys_id")}
    # mock backend
    num = f"MOCK-INC{str(abs(hash(short_description)) % 100000).zfill(5)}"
    _append(MOCK_INCIDENTS, {"number": num, "short_description": short_description,
                             "description": description, "severity": severity,
                             "created": datetime.now(timezone.utc).isoformat()})
    return {"backend": "mock", "number": num}


@mcp.tool()
def send_notification(to: str, subject: str, body: str) -> dict:
    """Record a supplier notification. Always a local mock, never a real email."""
    _append(NOTIFICATIONS, {"to": to, "subject": subject, "body": body,
                            "sent": datetime.now(timezone.utc).isoformat()})
    return {"backend": "mock_mail", "status": "recorded"}


if __name__ == "__main__":
    mcp.run()
