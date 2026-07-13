#!/usr/bin/env python3
"""
Migrate FailsDB SQLite records to Meggan REST API.

This script extracts failure records from a SQLite database, transforms them
to match the Meggan API schema, and submits them. It supports dry-run mode
and local state tracking for resumable execution.

Updated Flow:
1. Retrieves records from SQLite and deduplicates them based on Felbeskrivning & Kommentar.
2. Saves unique records to a .jsonl file.
3. Submits records one-by-one from the .jsonl file to the API.
"""

import argparse
import httpx
import json
import os
import sqlite3
import sys
import time
from typing import Dict, List, Optional, Set, Tuple

# Configuration Constants
DEFAULT_API_BASE_URL = "http://localhost:8030/api/v1"
STATE_FILE_NAME = ".migrate_failsdb_state.json"
DRY_RUN_OUTPUT_FILE = "fetched_records.jsonl"
REQUEST_DELAY_SECONDS = 0.5


def load_processed_ids(state_file: str) -> Set[int]:
    """Load previously processed record IDs from the state file."""
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("processed_ids", []))
        except (json.JSONDecodeError, IOError):
            print(f"⚠️ Warning: Could not read state file {state_file}. Starting fresh.")
    return set()


def save_processed_ids(state_file: str, processed_ids: Set[int]) -> None:
    """Save processed record IDs to the state file."""
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"processed_ids": list(processed_ids)}, f, indent=2)
    except IOError as e:
        print(f"❌ Error saving state file: {e}")
        sys.exit(1)


def normalize_utf8(text: Optional[str]) -> Optional[str]:
    """Ensure text is valid UTF-8 by encoding and decoding."""
    if text is None:
        return None
    try:
        return text.encode("utf-8", errors="ignore").decode("utf-8")
    except UnicodeError:
        return text


def transform_record(row: Dict[str, Optional[str]]) -> Dict[str, str]:
    """
    Transform a SQLite row into the Meggan API payload schema.
    """
    felbeskrivning = normalize_utf8(row.get("Felbeskrivning"))
    kommentar = normalize_utf8(row.get("Kommentar"))
    artnr = normalize_utf8(row.get("Artnr"))

    internal_id = artnr if artnr and artnr.strip() else "Other"

    return {
        "equipment_type": "Other",
        "equipment_name": "Other",
        "failure_description": felbeskrivning or "",
        "solution_description": kommentar or "",
        "internal_ID": internal_id
    }


def submit_to_api(client: httpx.Client, payload: Dict[str, str], record_id: int) -> bool:
    """
    Submit a record to the Meggan API.
    """
    endpoint = "/records/"

    try:
        response = client.post(
            endpoint,
            data=payload
        )

        if response.status_code == 200:
            print(f"✅ Successfully submitted record ID {record_id}")
            return True
        elif response.status_code == 422:
            errors = response.json().get("detail", "Unknown validation failure")
            print(f"❌ Validation Error for ID {record_id}: {errors}")
            return False
        else:
            print(f"❌ Server Error ({response.status_code}) for ID {record_id}: {response.text}")
            return False

    except httpx.RequestError as e:
        print(f"🌐 Network Error submitting ID {record_id}: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate FailsDB SQLite records to Meggan REST API",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "db_path",
        help="Path to the FailsDB_SQLite database file"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not submit to API; only extract and deduplicate records to fetched_records.jsonl"
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_BASE_URL,
        help=f"Base URL for Meggan API (default: {DEFAULT_API_BASE_URL})"
    )

    args = parser.parse_args()

    if not os.path.exists(args.db_path):
        print(f"❌ Database file not found: {args.db_path}")
        sys.exit(1)

    # --- PHASE 1: Retrieve & Deduplicate ---
    print("🔄 Phase 1: Retrieving and deduplicating records...")

    try:
        conn = sqlite3.connect(args.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
    except sqlite3.Error as e:
        print(f"❌ Database connection error: {e}")
        sys.exit(1)

    query = """
        SELECT ID, Felbeskrivning, Kommentar, Artnr
        FROM FelTabell
        WHERE Felbeskrivning IS NOT NULL AND Felbeskrivning != ''
          AND Kommentar IS NOT NULL AND Kommentar != ''
    """

    try:
        cursor.execute(query)
        rows = cursor.fetchall()
    except sqlite3.Error as e:
        print(f"❌ Database query error: {e}")
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    print(f"📊 Found {len(rows)} records matching criteria.")

    unique_records: List[Dict] = []
    seen_pairs: Set[Tuple[str, str]] = set()
    duplicate_count = 0

    for row in rows:
        record_id = row["ID"]
        felbeskrivning = normalize_utf8(row["Felbeskrivning"]) or ""
        kommentar = normalize_utf8(row["Kommentar"]) or ""

        # Deduplication key based on normalized description and comment
        dedup_key = (felbeskrivning.strip().lower(), kommentar.strip().lower())

        if dedup_key not in seen_pairs:
            seen_pairs.add(dedup_key)
            payload = transform_record(dict(row))
            # Store original ID alongside payload for state tracking
            unique_records.append({"id": record_id, **payload})
        else:
            duplicate_count += 1

    # Save unique records to JSONL
    try:
        with open(DRY_RUN_OUTPUT_FILE, "w", encoding="utf-8") as f:
            for rec in unique_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"💾 Saved {len(unique_records)} unique records to {DRY_RUN_OUTPUT_FILE}.")
        print(f"🗑️ Skipped {duplicate_count} duplicate records.")
    except IOError as e:
        print(f"❌ Error writing to {DRY_RUN_OUTPUT_FILE}: {e}")
        sys.exit(1)

    if args.dry_run:
        print("📝 Dry-run mode enabled. Exiting after extraction.")
        return

    # --- PHASE 2: Submit from JSONL ---
    print("\n🔄 Phase 2: Submitting records to API...")

    processed_ids = load_processed_ids(STATE_FILE_NAME)
    print(f"📂 Loaded state: {len(processed_ids)} previously processed records.")

    try:
        client = httpx.Client(
            base_url=args.api_url,
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=20)
        )
    except Exception as e:
        print(f"❌ Failed to initialize HTTP client: {e}")
        sys.exit(1)

    success_count = 0
    skipped_count = 0

    try:
        with open(DRY_RUN_OUTPUT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                rec = json.loads(line)
                record_id = rec["id"]
                """
                if record_id in processed_ids:
                    print(f"⏭️ Skipping already processed record ID {record_id}")
                    skipped_count += 1
                    continue
                """
                # Extract payload without the internal 'id' field to preserve original schema
                payload = {k: v for k, v in rec.items() if k != "id"}

                if submit_to_api(client, payload, record_id):
                    processed_ids.add(record_id)
                    save_processed_ids(STATE_FILE_NAME, processed_ids)
                    success_count += 1
                    time.sleep(REQUEST_DELAY_SECONDS)
                else:
                    print(f"❌ Failed to submit record ID {record_id}. Stopping execution.")
                    sys.exit(1)
    except FileNotFoundError:
        print(f"❌ JSONL file {DRY_RUN_OUTPUT_FILE} not found. Run extraction first.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing JSONL file: {e}")
        sys.exit(1)
    finally:
        client.close()

    print(f"\n🎉 Migration complete. {success_count} new records processed, {skipped_count} skipped.")


if __name__ == "__main__":
    main()