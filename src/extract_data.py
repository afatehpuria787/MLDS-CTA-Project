import os
import time
import json            # IMPORT LIBRARIES
import sqlite3
import requests
from datetime import datetime, timezone

API_KEY = os.getenv("CTA_TRAIN_API_KEY") # FETCH API KEY 
if not API_KEY:
    raise SystemExit("CTA_TRAIN_API_KEY is not set. Example (PowerShell): $env:CTA_TRAIN_API_KEY='YOUR_KEY'")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30")) # HOW OFTEN TO REQUEST FROM API
DB_PATH = "cta_trains.db"

BASE_URL = "https://lapi.transitchicago.com/api/1.0/ttpositions.aspx"

ROUTES = { # MAPPING OF ALL ROUTES
    "Red":  "red",
    "Blue": "blue",
    "Brn":  "brn",
    "G":    "g",
    "Org":  "org",
    "P":    "p",
    "Pink": "pink",
    "Y":    "y",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc         TEXT    NOT NULL,   -- ISO timestamp when we polled
    rn             TEXT,               -- run number (train ID)
    next_station   TEXT,               -- Next station name
    lat            REAL,
    lon            REAL,
    heading        INTEGER,            -- degrees (0-359)
    arriving_now   INTEGER,            -- 1/0
    delayed        INTEGER             -- 1/0
);
-- (Optional) Make (ts_utc, rn) unique to avoid duplicates if you re-run inserts
CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_ts_rn ON {table}(ts_utc, rn);
"""

def ensure_db(conn: sqlite3.Connection): # CONNECT TO THE DB AND CREATE THE TABLES
    with conn:
        for t in ROUTES.values():
            conn.executescript(SCHEMA_SQL.format(table=t))

def fetch_route_positions(route_code: str) -> list[dict]: # FETCH THE DATA
    """Fetch live positions for a single CTA route; returns normalized dicts."""
    params = {"key": API_KEY, "rt": route_code, "outputType": "JSON"}
    r = requests.get(BASE_URL, params=params, timeout=12)
    r.raise_for_status()
    ct = r.headers.get("Content-Type", "").lower()
    if "json" not in ct:
        # Not JSON? print head to help debug and return empty
        print(f"[{route_code}] Unexpected content-type={ct}")
        print(r.text[:300])
        return []

    data = r.json()
    ctatt = data.get("ctatt", {})
    if ctatt.get("errCd") not in (None, "0"):
        print(f"[{route_code}] CTA error {ctatt.get('errCd')}: {ctatt.get('errNm')}")
        return []

    out = []
    for block in ctatt.get("route", []):
        for t in block.get("train", []) or []:
            try:
                out.append({
                    "rn": t.get("rn"),
                    "next_station": t.get("nextStaNm"),
                    "lat": float(t["lat"]) if t.get("lat") else None,
                    "lon": float(t["lon"]) if t.get("lon") else None,
                    "heading": int(t["heading"]) if t.get("heading") else None,
                    "arriving_now": 1 if t.get("isApp") == "1" else 0,
                    "delayed": 1 if t.get("isDly") == "1" else 0,
                })
            except (ValueError, TypeError):
                # Skip malformed rows without crashing the whole poll
                continue
    return out
 
def insert_snapshot(conn: sqlite3.Connection, table: str, ts_iso: str, rows: list[dict]):
    if not rows:                     # STICK DATA IN DATABASE
        return
    sql = f"""
        INSERT OR IGNORE INTO {table}
        (ts_utc, rn, next_station, lat, lon, heading, arriving_now, delayed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    vals = [
        (
            ts_iso,
            r.get("rn"),
            r.get("next_station"),
            r.get("lat"),
            r.get("lon"),
            r.get("heading"),
            r.get("arriving_now"),
            r.get("delayed"),
        )
        for r in rows
    ]
    with conn:
        conn.executemany(sql, vals)

def main(): # MAIN FUNCTION TO PULL DATA EVERY LOOP AND POPULATE DATABASE UNTIL INTERUPTED
    print(f"Writing to SQLite DB: {DB_PATH}")
    print(f"Polling every {POLL_SECONDS}s. Press Ctrl+C to stop.")
    conn = sqlite3.connect(DB_PATH)
    ensure_db(conn)

    try:
        while True:
            ts_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            total = 0
            for rt_code, table in ROUTES.items():
                try:
                    rows = fetch_route_positions(rt_code)
                    insert_snapshot(conn, table, ts_iso, rows)
                    print(f"[{ts_iso}] {rt_code:<4} -> {len(rows):2d} rows")
                    total += len(rows)
                    time.sleep(0.2)  # tiny pause between routes (be polite)
                except requests.RequestException as e:
                    print(f"[{rt_code}] request failed: {e}")
            print(f"[{ts_iso}] snapshot complete: {total} trains total\n")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
