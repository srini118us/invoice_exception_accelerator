"""
query_policy.py  (script 5b, readiness check and the agent's retrieval primitive)

Queries the Vertex AI Search policy engine and prints the top matches with their source
document, which is how the agent cites a clause. Also the simplest way to tell when indexing
has finished: an empty result usually means it is still indexing, so wait and retry.

Reads:
  data/policy_search.json   written by create_policy_search.py
Writes:
  nothing (prints results)

Run:
  python scripts/query_policy.py "early payment discount"
  python scripts/query_policy.py "bank account change"
"""

import json
import sys
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as de

ROOT = Path(__file__).resolve().parents[1]
INFO = json.loads((ROOT / "data" / "policy_search.json").read_text())


def search(query, top_k=3):
    client = de.SearchServiceClient(
        client_options=ClientOptions(api_endpoint="discoveryengine.googleapis.com")
    )
    serving_config = (
        f"projects/{INFO['project']}/locations/{INFO['location']}"
        f"/collections/default_collection/engines/{INFO['engine_id']}"
        f"/servingConfigs/default_search"
    )
    req = de.SearchRequest(serving_config=serving_config, query=query, page_size=top_k)
    resp = client.search(req)
    results = []
    for r in resp.results:
        doc = r.document
        data = dict(doc.derived_struct_data) if doc.derived_struct_data else {}
        link = data.get("link", "")
        snippets = data.get("snippets", [])
        snippet = snippets[0].get("snippet", "") if snippets else ""
        results.append({"id": doc.id, "source": link, "snippet": snippet})
    return results


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "three way match price tolerance"
    hits = search(query)
    if not hits:
        print(f"no results for {query!r} (indexing may still be in progress, retry in a few minutes)")
        return
    print(f"query: {query}")
    for i, h in enumerate(hits, 1):
        src = h["source"].split("/")[-1] if h["source"] else h["id"]
        print(f"{i}. {src}")
        if h["snippet"]:
            print(f"   {h['snippet'][:160]}")


if __name__ == "__main__":
    main()
