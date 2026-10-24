"""
create_policy_search.py  (script 5a, one time)

Builds the policy grounding layer on Vertex AI Search. The agent retrieves the right policy
clause from here and cites it, so a reviewer sees why a decision was made, not just what.

Steps, all idempotent where the API allows:
  1 create a GCS bucket and upload the policy pack as .txt (plain text is a supported type)
  2 create an unstructured data store (generic, search, content required)
  3 import the uploaded policy files into the data store
  4 create a search engine (app) bound to the data store

Indexing is asynchronous. After this finishes, search may take several minutes to tens of
minutes before it returns results. Use query_policy.py to check readiness.

Reads:
  data/policies/*.md       the policy pack
Writes:
  data/policy_search.json  {project, location, data_store_id, engine_id, bucket}

Run:
  python scripts/create_policy_search.py
"""

import json
import os
import time
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as de
from google.cloud import storage

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "geap-ai-usecases-494501")
LOCATION = "global"                       # Vertex AI Search location
DATA_STORE_ID = "invoice-accel-policies"
ENGINE_ID = "invoice-accel-policy-search"
BUCKET = f"{PROJECT}-invoice-accel"
PREFIX = "policies"

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "data" / "policies"
INFO = ROOT / "data" / "policy_search.json"


def de_client(cls):
    # the global location uses the default endpoint; regional would need a host override
    opts = ClientOptions(api_endpoint="discoveryengine.googleapis.com")
    return cls(client_options=opts)


def upload_policies():
    sc = storage.Client(project=PROJECT)
    bucket = sc.bucket(BUCKET)
    if not bucket.exists():
        bucket = sc.create_bucket(BUCKET, location="us")
        print(f"created bucket gs://{BUCKET}")
    else:
        print(f"bucket gs://{BUCKET} exists")
    uris = []
    for md in sorted(POLICIES.glob("*.md")):
        blob = bucket.blob(f"{PREFIX}/{md.stem}.txt")
        blob.upload_from_string(md.read_text(), content_type="text/plain")
        uris.append(f"gs://{BUCKET}/{PREFIX}/{md.stem}.txt")
    print(f"uploaded {len(uris)} policy files to gs://{BUCKET}/{PREFIX}/")
    return uris


def create_data_store():
    client = de_client(de.DataStoreServiceClient)
    parent = client.collection_path(PROJECT, LOCATION, "default_collection")
    ds = de.DataStore(
        display_name="Invoice Accelerator AP Policies",
        industry_vertical=de.IndustryVertical.GENERIC,
        solution_types=[de.SolutionType.SOLUTION_TYPE_SEARCH],
        content_config=de.DataStore.ContentConfig.CONTENT_REQUIRED,
    )
    try:
        op = client.create_data_store(parent=parent, data_store=ds, data_store_id=DATA_STORE_ID)
        op.result(timeout=300)
        print(f"created data store {DATA_STORE_ID}")
    except Exception as e:
        if "ALREADY_EXISTS" in str(e) or "already exists" in str(e).lower():
            print(f"data store {DATA_STORE_ID} exists")
        else:
            raise


def import_documents():
    client = de_client(de.DocumentServiceClient)
    parent = client.branch_path(PROJECT, LOCATION, DATA_STORE_ID, "default_branch")
    req = de.ImportDocumentsRequest(
        parent=parent,
        gcs_source=de.GcsSource(input_uris=[f"gs://{BUCKET}/{PREFIX}/*.txt"], data_schema="content"),
        reconciliation_mode=de.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
    )
    op = client.import_documents(request=req)
    print("import started, waiting for the operation to finish ...")
    op.result(timeout=600)
    print("import operation complete (indexing continues asynchronously)")


def create_engine():
    client = de_client(de.EngineServiceClient)
    parent = client.collection_path(PROJECT, LOCATION, "default_collection")
    engine = de.Engine(
        display_name="Invoice Accelerator Policy Search",
        solution_type=de.SolutionType.SOLUTION_TYPE_SEARCH,
        industry_vertical=de.IndustryVertical.GENERIC,
        data_store_ids=[DATA_STORE_ID],
        search_engine_config=de.Engine.SearchEngineConfig(
            search_tier=de.SearchTier.SEARCH_TIER_ENTERPRISE,
            search_add_ons=[de.SearchAddOn.SEARCH_ADD_ON_LLM],
        ),
    )
    try:
        op = client.create_engine(parent=parent, engine=engine, engine_id=ENGINE_ID)
        op.result(timeout=300)
        print(f"created engine {ENGINE_ID}")
    except Exception as e:
        if "ALREADY_EXISTS" in str(e) or "already exists" in str(e).lower():
            print(f"engine {ENGINE_ID} exists")
        else:
            raise


def main():
    upload_policies()
    create_data_store()
    import_documents()
    create_engine()
    INFO.write_text(json.dumps({
        "project": PROJECT, "location": LOCATION,
        "data_store_id": DATA_STORE_ID, "engine_id": ENGINE_ID, "bucket": BUCKET,
    }, indent=2))
    print(f"\nsaved {INFO.relative_to(ROOT)}")
    print("next: wait a few minutes, then python scripts/query_policy.py \"early payment discount\"")


if __name__ == "__main__":
    main()
