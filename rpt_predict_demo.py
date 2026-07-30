"""Send a real prediction request to the RPT model and inspect what it returns.

SAP-RPT is a tabular foundation model: it does in-context classification /
regression over rows of structured data. You give it several labeled example
rows plus one (or more) rows where the target column is masked with "?", and
it predicts the masked value(s) with confidence scores + explanations.
"""
import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

AICORE_CLIENT_ID = os.environ["AICORE_CLIENT_ID"]
AICORE_CLIENT_SECRET = os.environ["AICORE_CLIENT_SECRET"]
AICORE_AUTH_URL = os.environ["AICORE_AUTH_URL"]
AICORE_RESOURCE_GROUP = os.environ["AICORE_RESOURCE_GROUP"]
RPT_URL = os.environ["RPT"].strip()
PREDICT_URL = f"{RPT_URL}/predict"


def get_token() -> str:
    resp = requests.post(
        f"{AICORE_AUTH_URL}/oauth/token",
        params={"grant_type": "client_credentials"},
        auth=(AICORE_CLIENT_ID, AICORE_CLIENT_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def predict(token: str, payload: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "AI-Resource-Group": AICORE_RESOURCE_GROUP,
    }
    resp = requests.post(PREDICT_URL, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"{resp.status_code} - {resp.text}")
    return resp.json()


# Example payload: classify "category" for a product whose category is unknown ("?"),
# using 3 labeled rows as in-context examples. Mirrors SAP's official sample.
CLASSIFICATION_PAYLOAD = {
    "index_column": "id",
    "prediction_config": {
        "target_columns": [
            {
                "name": "category",
                "prediction_placeholder": "?",
                "task_type": "classification",
                "top_k": 3,  # return top 3 candidate labels + scores
            }
        ],
        "explanations": {"top_column_scores": 4, "top_relevant_context_rows": 3},
    },
    "parse_data_types": "true",
    "data_schema": {
        "id": {"dtype": "numeric"},
        "product": {"dtype": "string"},
        "price": {"dtype": "numeric"},
        "category": {"dtype": "string"},
        "stock": {"dtype": "numeric"},
        "production_date": {"dtype": "date"},
    },
    "rows": [
        {"id": 1, "product": "Laptop", "price": 899, "category": "Electronics", "stock": "150", "production_date": "2024-01-15"},
        {"id": 2, "product": "Mouse", "price": 25, "category": "Accessories", "stock": "500", "production_date": "2024-02-20"},
        {"id": 3, "product": "Keyboard", "price": 75, "category": "Accessories", "stock": "320", "production_date": "2024-03-10"},
        {"id": 4, "product": "Monitor", "price": 350, "category": "?", "stock": "200", "production_date": "2024-03-25"},
    ],
}


def main() -> None:
    print(f"Predict endpoint: {PREDICT_URL}\n")

    print("Requesting OAuth token...")
    token = get_token()
    print("Token OK.\n")

    print("Sending classification request (predict 'category' for row id=4)...")
    result = predict(token, CLASSIFICATION_PAYLOAD)

    print("\n=== Raw response ===")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
