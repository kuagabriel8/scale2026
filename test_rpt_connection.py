"""Test connectivity to the RPT model deployment on SAP AI Core."""
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

AICORE_CLIENT_ID = os.environ["AICORE_CLIENT_ID"]
AICORE_CLIENT_SECRET = os.environ["AICORE_CLIENT_SECRET"]
AICORE_AUTH_URL = os.environ["AICORE_AUTH_URL"]
AICORE_RESOURCE_GROUP = os.environ["AICORE_RESOURCE_GROUP"]
RPT_URL = os.environ["RPT"].strip()

DEPLOYMENT_ID = RPT_URL.rstrip("/").split("/")[-1]
API_BASE = RPT_URL.split("/v2/inference/")[0]


def get_token() -> str:
    resp = requests.post(
        f"{AICORE_AUTH_URL}/oauth/token",
        params={"grant_type": "client_credentials"},
        auth=(AICORE_CLIENT_ID, AICORE_CLIENT_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def check_deployment_status(token: str) -> dict:
    url = f"{API_BASE}/v2/lm/deployments/{DEPLOYMENT_ID}"
    headers = {
        "Authorization": f"Bearer {token}",
        "AI-Resource-Group": AICORE_RESOURCE_GROUP,
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    print(f"Deployment ID : {DEPLOYMENT_ID}")
    print(f"API base      : {API_BASE}")

    print("\n[1/2] Requesting OAuth token from AI Core...")
    try:
        token = get_token()
        print("      OK - token acquired")
    except requests.RequestException as e:
        print(f"      FAILED - {e}")
        return 1

    print("[2/2] Checking deployment status...")
    try:
        info = check_deployment_status(token)
    except requests.RequestException as e:
        print(f"      FAILED - {e}")
        return 1

    status = info.get("status", "UNKNOWN")
    print(f"      OK - status = {status}")
    print(f"\nDetails: {info}")

    if status != "RUNNING":
        print(f"\nWarning: deployment status is '{status}', not RUNNING.")
        return 2

    print("\nConnectivity to RPT model deployment confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
