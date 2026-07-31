#!/usr/bin/env python3
"""
Team 03 - RPT Investigation-Time Prediction

Improved workflow:
HANA TEAM_03.RISK_ALERTS
 -> retrieve many completed historical cases
 -> calculate actual duration = RESOLVED_AT - ASSIGNED_AT
 -> rank historical cases by similarity to target
 -> send the most similar cases to SAP-RPT
 -> predict ASSESSMENT_DURATION_HOURS

Similarity uses:
  ALERT_TYPE, ALERT_SUBTYPE, ALERT_PRIORITY,
  ALERT_SOURCE, RISK_DRIVERS

This is NOT random selection and does not rely only on risk level.
"""

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from hdbcli import dbapi


BASE_DIR = Path(__file__).resolve().parent
HANA_DIR = BASE_DIR / "hanadb"
CREDENTIALS_FILE = HANA_DIR / "team_03_credentials.json"
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)

HANA_SCHEMA = "TEAM_03"
DEFAULT_CONTEXT_ROWS = 50
DEFAULT_CANDIDATE_ROWS = 500


# ============================================================
# CREDENTIALS
# ============================================================

def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable: {name}\n"
            f"Check: {ENV_FILE}"
        )
    return value.strip()


def load_credentials() -> Dict[str, Any]:
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"Could not find: {CREDENTIALS_FILE}"
        )

    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# HANA
# ============================================================

def get_hana_connection(creds):
    db = creds["database"]

    return dbapi.connect(
        address=db["host"],
        port=db["port"],
        user=db["username"],
        password=db["password"],
        encrypt=True,
        sslValidateCertificate=False,
    )


def test_hana_connection(creds):
    print("\n" + "=" * 70)
    print("1. HANA CONNECTION")
    print("=" * 70)

    conn = get_hana_connection(creds)

    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT CURRENT_USER, CURRENT_SCHEMA FROM DUMMY"
        )
        user, schema = cur.fetchone()

        print(f"Connected as: {user}")
        print(f"Current schema: {schema}")
        print(f"Data schema: {HANA_SCHEMA}")

        cur.execute(
            f"SELECT COUNT(*) FROM {HANA_SCHEMA}.RISK_ALERTS"
        )
        count = cur.fetchone()[0]

        print(f"RISK_ALERTS rows: {count:,}")
        print("HANA connection OK.")

    finally:
        conn.close()


# ============================================================
# DATA
# ============================================================

def duration_hours(assigned, resolved) -> Optional[float]:
    if assigned is None or resolved is None:
        return None

    seconds = (resolved - assigned).total_seconds()

    if seconds < 0:
        return None

    return seconds / 3600.0


def norm(value) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def tokens(value) -> set:
    text = norm(value)
    return {
        x for x in re.findall(r"[a-z0-9_]+", text)
        if len(x) > 1
    }


def similarity(target, historical) -> float:
    """
    Maximum = 100.
    Duration is NOT used because duration is what we predict.
    """

    score = 0.0

    if norm(target.get("ALERT_TYPE")) == norm(
        historical.get("ALERT_TYPE")
    ):
        score += 25

    if norm(target.get("ALERT_SUBTYPE")) == norm(
        historical.get("ALERT_SUBTYPE")
    ):
        score += 25

    if norm(target.get("ALERT_PRIORITY")) == norm(
        historical.get("ALERT_PRIORITY")
    ):
        score += 15

    if norm(target.get("ALERT_SOURCE")) == norm(
        historical.get("ALERT_SOURCE")
    ):
        score += 10

    target_drivers = tokens(target.get("RISK_DRIVERS"))
    historical_drivers = tokens(
        historical.get("RISK_DRIVERS")
    )

    if target_drivers and historical_drivers:
        overlap = target_drivers.intersection(
            historical_drivers
        )

        score += min(
            len(overlap) / max(len(target_drivers), 1) * 25,
            25,
        )

    return round(score, 2)


def get_target_alert(creds, alert_id=None):
    conn = get_hana_connection(creds)

    try:
        cur = conn.cursor()

        columns_sql = """
            ALERT_ID,
            ALERT_TYPE,
            ALERT_SUBTYPE,
            ALERT_PRIORITY,
            ALERT_SOURCE,
            RISK_DRIVERS,
            ASSIGNED_AT,
            RESOLVED_AT
        """

        if alert_id is not None:
            query = f"""
                SELECT {columns_sql}
                FROM {HANA_SCHEMA}.RISK_ALERTS
                WHERE ALERT_ID = ?
            """
            cur.execute(query, (alert_id,))
        else:
            query = f"""
                SELECT {columns_sql}
                FROM {HANA_SCHEMA}.RISK_ALERTS
                WHERE ALERT_TYPE IS NOT NULL
                ORDER BY CREATED_AT DESC
                LIMIT 1
            """
            cur.execute(query)

        row = cur.fetchone()

        if row is None:
            raise RuntimeError("No target alert found.")

        names = [x[0] for x in cur.description]
        return dict(zip(names, row))

    finally:
        conn.close()


def get_candidates(
    creds,
    target_alert_id,
    candidate_limit=DEFAULT_CANDIDATE_ROWS,
):
    """
    Get a large pool of completed investigations first.
    We rank them later instead of blindly taking the newest 50.
    """

    conn = get_hana_connection(creds)

    try:
        cur = conn.cursor()

        query = f"""
            SELECT
                ALERT_ID,
                ALERT_TYPE,
                ALERT_SUBTYPE,
                ALERT_PRIORITY,
                ALERT_SOURCE,
                RISK_DRIVERS,
                ASSIGNED_AT,
                RESOLVED_AT
            FROM {HANA_SCHEMA}.RISK_ALERTS
            WHERE ASSIGNED_AT IS NOT NULL
              AND RESOLVED_AT IS NOT NULL
              AND RESOLVED_AT >= ASSIGNED_AT
              AND ALERT_ID <> ?
            ORDER BY RESOLVED_AT DESC
            LIMIT {int(candidate_limit)}
        """

        cur.execute(query, (target_alert_id,))

        names = [x[0] for x in cur.description]
        result = []

        for raw in cur.fetchall():
            row = dict(zip(names, raw))

            duration = duration_hours(
                row["ASSIGNED_AT"],
                row["RESOLVED_AT"],
            )

            if duration is None:
                continue

            row["ASSESSMENT_DURATION_HOURS"] = round(
                duration, 4
            )

            result.append(row)

        return result

    finally:
        conn.close()


def select_similar(target, candidates, limit):
    scored = []

    for row in candidates:
        copy = row.copy()
        copy["_similarity_score"] = similarity(
            target,
            row,
        )
        scored.append(copy)

    scored.sort(
        key=lambda x: (
            x["_similarity_score"],
            x.get("RESOLVED_AT") or datetime.min,
        ),
        reverse=True,
    )

    return scored[:limit]


# ============================================================
# RPT PAYLOAD
# ============================================================

def rpt_value(value):
    if value is None:
        return "UNKNOWN"
    return str(value)


def historical_to_rpt(row):
    return {
        "ALERT_ID": int(row["ALERT_ID"]),
        "ALERT_TYPE": rpt_value(row["ALERT_TYPE"]),
        "ALERT_SUBTYPE": rpt_value(row["ALERT_SUBTYPE"]),
        "ALERT_PRIORITY": rpt_value(row["ALERT_PRIORITY"]),
        "ALERT_SOURCE": rpt_value(row["ALERT_SOURCE"]),
        "RISK_DRIVERS": rpt_value(row["RISK_DRIVERS"]),
        "ASSESSMENT_DURATION_HOURS": float(
            row["ASSESSMENT_DURATION_HOURS"]
        ),
    }


def target_to_rpt(row):
    return {
        "ALERT_ID": int(row["ALERT_ID"]),
        "ALERT_TYPE": rpt_value(row["ALERT_TYPE"]),
        "ALERT_SUBTYPE": rpt_value(row["ALERT_SUBTYPE"]),
        "ALERT_PRIORITY": rpt_value(row["ALERT_PRIORITY"]),
        "ALERT_SOURCE": rpt_value(row["ALERT_SOURCE"]),
        "RISK_DRIVERS": rpt_value(row["RISK_DRIVERS"]),
        "ASSESSMENT_DURATION_HOURS": "?",
    }


def build_payload(historical, target):
    rows = [
        historical_to_rpt(x)
        for x in historical
    ]

    rows.append(target_to_rpt(target))

    return {
        "index_column": "ALERT_ID",

        "prediction_config": {
            "target_columns": [
                {
                    "name": "ASSESSMENT_DURATION_HOURS",
                    "prediction_placeholder": "?",
                    "task_type": "regression",
                }
            ],
            "explanations": {
                "top_column_scores": 6,
                "top_relevant_context_rows": 5,
            },
        },

        "parse_data_types": "true",

        "data_schema": {
            "ALERT_ID": {"dtype": "numeric"},
            "ALERT_TYPE": {"dtype": "string"},
            "ALERT_SUBTYPE": {"dtype": "string"},
            "ALERT_PRIORITY": {"dtype": "string"},
            "ALERT_SOURCE": {"dtype": "string"},
            "RISK_DRIVERS": {"dtype": "string"},
            "ASSESSMENT_DURATION_HOURS": {"dtype": "numeric"},
        },

        "rows": rows,
    }


# ============================================================
# AI CORE / RPT
# ============================================================

def ai_settings():
    return {
        "client_id": require_env("AICORE_CLIENT_ID"),
        "client_secret": require_env("AICORE_CLIENT_SECRET"),
        "auth_url": require_env("AICORE_AUTH_URL").rstrip("/"),
        "resource_group": require_env(
            "AICORE_RESOURCE_GROUP"
        ),
        "rpt_url": require_env("RPT").rstrip("/"),
    }


def get_token(ai):
    response = requests.post(
        f"{ai['auth_url']}/oauth/token",
        params={"grant_type": "client_credentials"},
        auth=(
            ai["client_id"],
            ai["client_secret"],
        ),
        timeout=15,
    )

    if not response.ok:
        raise RuntimeError(
            f"OAuth failed: {response.status_code}\n"
            f"{response.text}"
        )

    data = response.json()

    if "access_token" not in data:
        raise RuntimeError(
            "No access_token in OAuth response:\n"
            + json.dumps(data, indent=2)
        )

    return data["access_token"]


def predict(token, ai, payload):
    response = requests.post(
        f"{ai['rpt_url']}/predict",
        headers={
            "Authorization": f"Bearer {token}",
            "AI-Resource-Group": ai["resource_group"],
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )

    if not response.ok:
        raise RuntimeError(
            f"RPT prediction failed: {response.status_code}\n"
            f"{response.text}"
        )

    return response.json()


# ============================================================
# PRINTING
# ============================================================

def print_target(target):
    print("\nTarget alert")
    print("-" * 70)
    print(f"Alert ID:       {target['ALERT_ID']}")
    print(f"Alert type:     {target['ALERT_TYPE']}")
    print(f"Alert subtype:  {target['ALERT_SUBTYPE']}")
    print(f"Priority:       {target['ALERT_PRIORITY']}")
    print(f"Source:         {target['ALERT_SOURCE']}")
    print(f"Risk drivers:   {target['RISK_DRIVERS']}")


def print_selected(alerts):
    print("\n" + "=" * 100)
    print("HISTORICAL CASES SELECTED FOR RPT")
    print("=" * 100)

    print(
        f"{'ID':<10}"
        f"{'TYPE':<18}"
        f"{'SUBTYPE':<20}"
        f"{'PRIORITY':<10}"
        f"{'SOURCE':<12}"
        f"{'DURATION(h)':<14}"
        f"{'SIMILARITY':<12}"
    )

    print("-" * 100)

    for x in alerts:
        print(
            f"{str(x['ALERT_ID']):<10}"
            f"{str(x['ALERT_TYPE'])[:17]:<18}"
            f"{str(x['ALERT_SUBTYPE'])[:19]:<20}"
            f"{str(x['ALERT_PRIORITY'])[:9]:<10}"
            f"{str(x['ALERT_SOURCE'])[:11]:<12}"
            f"{float(x['ASSESSMENT_DURATION_HOURS']):<14.2f}"
            f"{x['_similarity_score']:<12.2f}"
        )


# ============================================================
# MAIN
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--alert-id",
        type=int,
        default=None,
        help="Target alert ID. Otherwise newest alert is used.",
    )

    parser.add_argument(
        "--context",
        type=int,
        default=DEFAULT_CONTEXT_ROWS,
        help="Number of similar cases sent to RPT.",
    )

    parser.add_argument(
        "--candidates",
        type=int,
        default=DEFAULT_CANDIDATE_ROWS,
        help="Number of completed cases considered before ranking.",
    )

    parser.add_argument(
        "--print-payload",
        action="store_true",
        help="Print full RPT JSON payload.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.context < 3:
        raise ValueError("--context must be at least 3.")

    if args.candidates < args.context:
        raise ValueError(
            "--candidates must be >= --context."
        )

    print("=" * 70)
    print("TEAM 03 - INVESTIGATION TIME PREDICTION")
    print("=" * 70)

    # 1. Credentials
    print("\nLoading credentials...")
    creds = load_credentials()
    ai = ai_settings()

    print(f"HANA schema:   {HANA_SCHEMA}")
    print(f"RPT endpoint:  {ai['rpt_url']}")
    print(f"Resource group:{ai['resource_group']}")

    # 2. HANA
    test_hana_connection(creds)

    # 3. Target
    print("\n" + "=" * 70)
    print("2. SELECTING TARGET ALERT")
    print("=" * 70)

    def load_new_case():
        path = BASE_DIR / "data/time_prediction_sample.json"

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    target = load_new_case()
    print_target(target)

    # 4. Historical candidate pool
    print("\n" + "=" * 70)
    print("3. RETRIEVING HISTORICAL CASE CANDIDATES")
    print("=" * 70)

    candidates = get_candidates(
        creds,
        int(target["ALERT_ID"]),
        args.candidates,
    )

    print(
        f"Completed historical candidates: {len(candidates)}"
    )

    if len(candidates) < 3:
        raise RuntimeError(
            "Not enough completed historical cases."
        )

    # 5. Similarity ranking
    print("\n" + "=" * 70)
    print("4. RANKING CASES BY SIMILARITY")
    print("=" * 70)

    historical = select_similar(
        target,
        candidates,
        args.context,
    )

    print(
        f"Selected {len(historical)} similar cases "
        f"from {len(candidates)} candidates."
    )

    print_selected(historical)

    durations = [
        float(x["ASSESSMENT_DURATION_HOURS"])
        for x in historical
    ]

    print("\nSelected-case duration statistics:")
    print(f"  Minimum: {min(durations):.2f} hours")
    print(f"  Maximum: {max(durations):.2f} hours")
    print(
        f"  Average: "
        f"{sum(durations) / len(durations):.2f} hours"
    )

    # 6. RPT payload
    print("\n" + "=" * 70)
    print("5. BUILDING RPT REGRESSION REQUEST")
    print("=" * 70)

    payload = build_payload(
        historical,
        target,
    )

    print("Target: ASSESSMENT_DURATION_HOURS")
    print("Task:   regression")
    print(f"Rows:   {len(payload['rows'])}")

    if args.print_payload:
        print("\n=== RPT PAYLOAD ===")
        print(
            json.dumps(
                payload,
                indent=2,
                default=str,
            )
        )

    # 7. AI Core
    print("\n" + "=" * 70)
    print("6. AI CORE / RPT")
    print("=" * 70)

    print("Requesting OAuth token...")
    token = get_token(ai)

    print("Token OK. Sending prediction...")

    # 8. Prediction
    result = predict(
        token,
        ai,
        payload,
    )

    print("RPT request successful.")

    print("\n" + "=" * 70)
    print("RPT RESPONSE")
    print("=" * 70)

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )

    print("\nDone.")


if __name__ == "__main__":
    main()