"""Bounded keyset reads of minute analytics from an existing capture journal."""
import json
from pathlib import Path
import sqlite3

SOURCES = {'pcr_time_minute', 'exposure_minute','premium_minute'}
INDEX_SQL = """CREATE INDEX IF NOT EXISTS algorithm_lookup ON events(bucket,source,id)
WHERE source IN ('pcr_time_minute','exposure_minute')"""
PREMIUM_INDEX_SQL = """CREATE INDEX IF NOT EXISTS premium_lookup ON events(bucket,source,id)
WHERE source='premium_minute'"""


def read_algorithm_events(database, *, bucket, source, after_id=0, limit=1000):
    if source not in SOURCES or not isinstance(bucket, str) or not bucket:
        raise ValueError('Algorithm source and nonempty bucket required')
    if type(after_id) is not int or after_id < 0 or type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError('Nonnegative cursor and limit between 1 and 10000 required')
    connection = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        predicate = "source='premium_minute'" if source=='premium_minute' else "source IN ('pcr_time_minute','exposure_minute')"
        rows = connection.execute(f"""SELECT id,received_at,payload FROM events
            WHERE {predicate}
            AND bucket=? AND source=? AND id>? ORDER BY id LIMIT ?""",
            (bucket, source, after_id, limit))
        return [dict(id=identifier, received_at=received, data=json.loads(payload))
                for identifier, received, payload in rows]
    finally:
        connection.close()
