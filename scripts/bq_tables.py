"""bq_tables.py  (BigQuery data access adapter)"""
import os
from google.cloud import bigquery

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "geap-ai-usecases-494501")
DATASET = os.environ.get("ACCEL_BQ_DATASET", "invoice_accel")
_client = None

def _bq():
    global _client
    if _client is None:
        _client = bigquery.Client(project=PROJECT)
    return _client

def load_table(name):
    table = name.replace(".csv", "")
    rows = _bq().query(f"SELECT * FROM `{PROJECT}.{DATASET}.{table}`").result()
    return [{k: ("" if v is None else str(v)) for k, v in dict(r).items()} for r in rows]
