"""Indexed, read-only dashboard queries. No browser-selected database paths."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3


class DashboardStore:
    def __init__(self, directory):
        self.directory=Path(directory).resolve()
        self.database=self.directory/'capture.sqlite3'

    def connect(self):
        return sqlite3.connect(self.database.as_uri()+'?mode=ro',uri=True,timeout=2)

    def contracts(self):
        try:
            value=json.loads((self.directory/'coverage.json').read_text(encoding='utf-8'))
            return value.get('contracts',[])[:5000]
        except (OSError,ValueError,TypeError):
            return []

    def candles(self,limit=1440):
        try:
            with closing(self.connect()) as db:
                rows=db.execute('SELECT * FROM nifty_candles ORDER BY minute DESC LIMIT ?',(limit,)).fetchall()
            return [dict(zip(('time','open','high','low','close','partial','source','updated_epoch'),row)) for row in reversed(rows)]
        except sqlite3.Error:
            return []

    def history(self,targets,points=240):
        result=[]
        try:
            with closing(self.connect()) as db:
                for bucket,source in targets:
                    predicate="source='premium_minute'" if source=='premium_minute' else "source IN ('pcr_time_minute','exposure_minute')"
                    limit=points*(2 if source=='premium_minute' else 1)
                    rows=db.execute(f'''SELECT id,payload FROM events WHERE {predicate}
                        AND bucket=? AND source=? ORDER BY id DESC LIMIT ?''',(bucket,source,limit)).fetchall()
                    for identifier,payload in reversed(rows):
                        result.append(dict(id=identifier,bucket=bucket,source=source,data=json.loads(payload)))
        except (sqlite3.Error,ValueError):
            pass
        return result
