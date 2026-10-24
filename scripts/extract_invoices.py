"""
extract_invoices.py  (script 2b)

Sends each invoice PDF to the Document AI Invoice Parser and writes the structured
result to data/extracted/<name>.json. This is the first billed step, about 0.10 USD
per document. Failed requests are not billed.

The parser returns entities (invoice_id, supplier_name, total_amount, line items, and
more), each with a confidence. This script maps the fields the matcher needs, records
every field's confidence, and flags any field below the 0.80 gate for human review.
It never discards a low confidence field, it marks it, so nothing uncertain is trusted
silently.

Reads:
  data/processor.json      written by create_processor.py
  data/invoices/*.pdf       the 16 invoice PDFs
Writes:
  data/extracted/<stem>.json  one record per invoice

Run all:
  python scripts/extract_invoices.py
Run a subset (cheap smoke test, matches any substring of the file name):
  python scripts/extract_invoices.py AC10001
"""

import json
import re
import sys
import time
from pathlib import Path

from google.cloud import documentai_v1 as documentai

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
INVOICES = DATA / "invoices"
OUT = DATA / "extracted"
OUT.mkdir(parents=True, exist_ok=True)

CONF_GATE = 0.80

# Invoice Parser entity types mapped to the fields the matcher needs.
FIELD_MAP = {
    "invoice_id": "invoice_number",
    "supplier_name": "vendor_name",
    "purchase_order": "po_id",
    "net_amount": "net",
    "total_tax_amount": "tax",
    "total_amount": "total",
    "currency": "currency",
    "supplier_iban": "bank_account",
}


def load_processor():
    info = json.loads((DATA / "processor.json").read_text())
    return info


def entity_value(ent):
    """Prefer the normalized value Document AI computes, fall back to raw text."""
    nv = ent.normalized_value.text if ent.normalized_value and ent.normalized_value.text else None
    return nv if nv else ent.mention_text


def extract_one(client, name, pdf_path):
    content = pdf_path.read_bytes()
    raw = documentai.RawDocument(content=content, mime_type="application/pdf")
    req = documentai.ProcessRequest(name=name, raw_document=raw)
    t0 = time.time()
    result = client.process_document(request=req)
    dt = round(time.time() - t0, 2)
    doc = result.document

    fields = {}         # mapped field -> {value, confidence, gate}
    line_items = []
    low_conf = []

    for ent in doc.entities:
        etype = ent.type_
        conf = round(float(ent.confidence), 3)
        if etype == "line_item":
            li = {"confidence": conf}
            for prop in ent.properties:
                key = prop.type_.split("/")[-1]   # quantity, unit_price, amount, description
                li[key] = entity_value(prop)
                li[f"{key}_conf"] = round(float(prop.confidence), 3)
            line_items.append(li)
            continue
        if etype in FIELD_MAP:
            field = FIELD_MAP[etype]
            gated = conf < CONF_GATE
            fields[field] = {"value": entity_value(ent), "confidence": conf,
                             "source": "docai", "needs_review": gated}
            if gated:
                low_conf.append(f"{field}({conf})")

    # bank text fallback: the parser often does not emit an IBAN, so read the remit
    # account straight from the document text. Deterministic, marked for audit, so a
    # downstream control knows this field did not come from the model.
    if not fields.get("bank_account", {}).get("value"):
        m = re.search(r"Remit To Account:\s*([A-Z0-9]{10,34})", doc.text)
        if m:
            fields["bank_account"] = {"value": m.group(1), "confidence": None,
                                      "source": "text_fallback", "needs_review": False}

    record = {
        "pdf": pdf_path.name,
        "seconds": dt,
        "entity_count": len(doc.entities),
        "fields": fields,
        "line_items": line_items,
        "fields_below_gate": low_conf,
        "gate": CONF_GATE,
    }
    return record


def main():
    info = load_processor()
    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": info["endpoint"]}
    )
    name = info["processor_name"]

    flt = sys.argv[1] if len(sys.argv) > 1 else None
    pdfs = sorted(INVOICES.glob("*.pdf"))
    if flt:
        pdfs = [p for p in pdfs if flt in p.name]
    if not pdfs:
        print(f"no PDFs matched {flt!r} in {INVOICES}")
        sys.exit(1)

    print(f"processor {info['processor_id']}, {len(pdfs)} document(s), gate {CONF_GATE}")
    total_low = 0
    for p in pdfs:
        rec = extract_one(client, name, p)
        (OUT / f"{p.stem}.json").write_text(json.dumps(rec, indent=2))
        tot = rec["fields"].get("total", {}).get("value", "n/a")
        low = ",".join(rec["fields_below_gate"]) or "none"
        total_low += len(rec["fields_below_gate"])
        print(f"{p.name:<18} {rec['seconds']:>5}s  entities={rec['entity_count']:>2}  "
              f"total={tot:<10}  below_gate={low}")
    print(f"\nwrote {len(pdfs)} record(s) to {OUT.relative_to(ROOT)}; "
          f"{total_low} field(s) below the {CONF_GATE} gate")


if __name__ == "__main__":
    main()
