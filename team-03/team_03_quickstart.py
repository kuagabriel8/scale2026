#!/usr/bin/env python3
"""
Team 03 - Quick Start Script
================================

Test both HANA and AI Core connections.

Usage:
    pip install hdbcli sap-ai-sdk-core
    python team_03_quickstart.py
"""

import json
from hdbcli import dbapi

# Load credentials
with open("team_03_credentials.json") as f:
    creds = json.load(f)

def test_hana_connection():
    """Test HANA Cloud connection."""
    print("\n" + "="*60)
    print("Testing HANA Cloud Connection")
    print("="*60)

    db = creds['database']

    try:
        conn = dbapi.connect(
            address=db['host'],
            port=db['port'],
            user=db['username'],
            password=db['password'],
            encrypt=True,
            sslValidateCertificate=False
        )

        cursor = conn.cursor()

        # Test query
        cursor.execute("SELECT CURRENT_USER, CURRENT_SCHEMA FROM DUMMY")
        user, schema = cursor.fetchone()
        print(f"✅ Connected as: {user}")
        print(f"✅ Current schema: {schema}")

        # Count transactions
        cursor.execute("SELECT COUNT(*) FROM TRUSTSPHERE_REFERENCE.TRANSACTIONS")
        count = cursor.fetchone()[0]
        print(f"✅ Transactions in reference schema: {count:,}")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

def test_ai_core_connection():
    """Test AI Core auth by fetching an OAuth token."""
    print("\n" + "="*60)
    print("Testing AI Core Connection")
    print("="*60)

    ai = creds['ai_core']

    try:
        import urllib.request, urllib.parse
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": ai['client_id'],
            "client_secret": ai['client_secret']
        }).encode()
        req = urllib.request.Request(ai['auth_url'] + "/oauth/token", data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            import json as _json
            token_data = _json.loads(resp.read())
        print(f"✅ Auth token obtained")
        print(f"✅ Resource Group: {ai['resource_group']}")
        print(f"✅ Token type: {token_data.get('token_type', 'bearer')}")
        return True
    except Exception as e:
        print(f"❌ Auth failed: {e}")
        return False


if __name__ == "__main__":
    print("="*60)
    print(f"Team 03 - Connection Test")
    print("="*60)

    hana_ok = test_hana_connection()
    ai_ok = test_ai_core_connection()

    print("\n" + "="*60)
    print("Summary")
    print("="*60)
    print(f"HANA Cloud: {'✅ Connected' if hana_ok else '❌ Failed'}")
    print(f"AI Core: {'✅ Connected' if ai_ok else '❌ Failed'}")

    if hana_ok and ai_ok:
        print("\n🎉 All systems ready! You're good to go!")
    else:
        print("\n⚠️  Check your credentials and network connection.")
