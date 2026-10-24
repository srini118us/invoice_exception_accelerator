"""
create_processor.py  (script 2a, one time)

Creates a Document AI Invoice Parser processor, or reuses the existing one, and saves
its id locally so extract_invoices.py can find it. A processor is the hosted model
endpoint that turns an invoice PDF into structured fields.

Idempotent: if a processor with the same display name already exists in the region,
this reuses it rather than creating a duplicate. Safe to run more than once.

Reads:  nothing (uses Application Default Credentials from gcloud)
Writes: data/processor.json  {project, location, processor_id, processor_name, endpoint}

Run:
  python scripts/create_processor.py
"""

import json
import os
from pathlib import Path

from google.cloud import documentai_v1 as documentai

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "geap-ai-usecases-494501")
LOCATION = "us"                     # Document AI region, us or eu
DISPLAY_NAME = "invoice-accel-parser"
PROCESSOR_TYPE = "INVOICE_PROCESSOR"

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data"
OUT.mkdir(parents=True, exist_ok=True)
INFO_PATH = OUT / "processor.json"


def main():
    endpoint = f"{LOCATION}-documentai.googleapis.com"
    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": endpoint}
    )
    parent = client.common_location_path(PROJECT, LOCATION)

    # reuse an existing processor with the same display name
    existing = None
    for p in client.list_processors(parent=parent):
        if p.display_name == DISPLAY_NAME:
            existing = p
            break

    if existing is not None:
        processor = existing
        print(f"reusing processor: {processor.name}")
    else:
        processor = client.create_processor(
            parent=parent,
            processor=documentai.Processor(
                type_=PROCESSOR_TYPE, display_name=DISPLAY_NAME
            ),
        )
        print(f"created processor: {processor.name}")

    processor_id = processor.name.split("/")[-1]
    info = {
        "project": PROJECT,
        "location": LOCATION,
        "processor_id": processor_id,
        "processor_name": processor.name,
        "endpoint": endpoint,
        "default_version": processor.default_processor_version,
    }
    INFO_PATH.write_text(json.dumps(info, indent=2))
    print(f"state: {processor.state.name}")
    print(f"default version: {processor.default_processor_version}")
    print(f"saved: {INFO_PATH.relative_to(ROOT)}")
    print(f"console: https://console.cloud.google.com/ai/document-ai/processors?project={PROJECT}")


if __name__ == "__main__":
    main()
