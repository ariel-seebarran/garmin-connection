#!/usr/bin/env python3
"""Push local Garmin data to the cloud server.

Usage:
    python scripts/push_to_cloud.py
    python scripts/push_to_cloud.py --server https://coachclaude.mooo.com
    python scripts/push_to_cloud.py --local-user-id 1   # if you registered locally
"""

import argparse
import getpass
import sqlite3
import sys
from pathlib import Path

import httpx


def read_local_db(db_path: Path, user_id: int) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    def fetch(query, params=()):
        cur = conn.execute(query, params)
        return [dict(r) for r in cur.fetchall()]

    data = {
        "activities":        fetch("SELECT * FROM activities WHERE user_id = ?", (user_id,)),
        "sleep":             fetch("SELECT * FROM sleep_data WHERE user_id = ?", (user_id,)),
        "daily_stats":       fetch("SELECT * FROM daily_stats WHERE user_id = ?", (user_id,)),
        "training_metrics":  fetch("SELECT * FROM training_metrics WHERE user_id = ?", (user_id,)),
    }
    conn.close()
    return data


def main():
    parser = argparse.ArgumentParser(description="Push local Garmin data to cloud server")
    parser.add_argument("--server", default="https://coachclaude.mooo.com")
    parser.add_argument("--db", default=None, help="Path to garmin.db (auto-detected if omitted)")
    parser.add_argument("--local-user-id", type=int, default=0,
                        help="Local user_id to export (0 = pre-login data, 1 = first registered user)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else Path(__file__).parent.parent / "backend" / "garmin.db"
    if not db_path.exists():
        print(f"Error: DB not found at {db_path}")
        sys.exit(1)

    print(f"Reading from {db_path} (local user_id={args.local_user_id})...")
    data = read_local_db(db_path, args.local_user_id)
    print(f"  Activities:       {len(data['activities'])}")
    print(f"  Sleep days:       {len(data['sleep'])}")
    print(f"  Daily stats:      {len(data['daily_stats'])}")
    print(f"  Training metrics: {len(data['training_metrics'])}")

    if not any(data.values()):
        print("\nNothing to push — run a Garmin sync first.")
        sys.exit(0)

    print(f"\nPushing to {args.server}")
    username = input("Cloud username: ")
    password = getpass.getpass("Cloud password: ")

    with httpx.Client(base_url=args.server, timeout=120.0) as client:
        r = client.post("/api/auth/login", json={"username": username, "password": password})
        if r.status_code != 200:
            print(f"Login failed: {r.text}")
            sys.exit(1)
        print("Logged in.")

        r = client.post("/api/import", json=data)
        if r.status_code != 200:
            print(f"Import failed: {r.text}")
            sys.exit(1)

        result = r.json()
        print("\nUploaded to cloud:")
        print(f"  Activities:       {result['activities']}")
        print(f"  Sleep days:       {result['sleep']}")
        print(f"  Daily stats:      {result['daily_stats']}")
        print(f"  Training metrics: {result['training_metrics']}")
        print(f"  Indexed in vector store: {result.get('indexed_in_vector_store', 0)}")
        print("\nDone!")


if __name__ == "__main__":
    main()
