"""Incremental hourly Excel export; safe to run in an isolated process."""
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

log = logging.getLogger(__name__)


def excel_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(',', ':'))
    if isinstance(value, str):
        if any(ord(c) < 32 and c not in '\t\n\r' for c in value):
            value = json.dumps(value)
    return value


def append_literal(sheet, values):
    cells = []
    for value in values:
        if isinstance(value, str) and value.startswith(('=', '+', '-', '@')):
            cell = WriteOnlyCell(sheet, value=value)
            cell.data_type = 's'
            cells.append(cell)
        else:
            cells.append(value)
    sheet.append(cells)


def export_directory(directory):
    started = time.monotonic()
    directory = Path(directory)
    manifest_path = directory / 'excel-progress.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    previous = int(manifest.get('high_water', 0))
    count = 0
    complete = True
    with closing(sqlite3.connect(directory / 'capture.sqlite3', timeout=30)) as conn:
        cutoff = conn.execute('SELECT coalesce(max(id),0) FROM events').fetchone()[0]
        groups = conn.execute(
            'SELECT DISTINCT day,bucket,substr(received_at,12,2) FROM events WHERE id>? AND id<=?',
            (previous, cutoff)).fetchall()
        for day, bucket, hour in groups:
            target = directory / day / hour
            target.mkdir(parents=True, exist_ok=True)
            path = target / f'{bucket}.xlsx'
            temporary = path.with_suffix('.tmp.xlsx')
            where = 'day=? AND bucket=? AND substr(received_at,12,2)=? AND id<=?'
            params = (day, bucket, hour, cutoff)
            widths = {}
            for (raw,) in conn.execute('SELECT payload FROM events WHERE ' + where, params):
                for key, value in json.loads(raw).items():
                    value = excel_value(value)
                    width = max(1, (len(value) + 31999) // 32000) if isinstance(value, str) else 1
                    widths[key] = max(widths.get(key, 1), width)
            keys = sorted(widths)
            headers = [key if widths[key] == 1 else f'{key}__part{part+1}'
                       for key in keys for part in range(widths[key])]
            if len(headers) + 3 > 16384:
                raise ValueError('Snapshot exceeds Excel column capacity; original retained in journal')
            book = Workbook(write_only=True)
            try:
                sheet = None
                for number, (event_id, received, source, raw) in enumerate(conn.execute(
                    'SELECT id,received_at,source,payload FROM events WHERE ' + where + ' ORDER BY id', params
                )):
                    if number % 1_048_575 == 0:
                        sheet = book.create_sheet(f'events_{number // 1_048_575 + 1}')
                        append_literal(sheet, ['event_id', 'received_at_ist', 'source', *headers])
                    values = json.loads(raw)
                    cells = [event_id, received, source]
                    for key in keys:
                        value = excel_value(values.get(key))
                        if widths[key] == 1:
                            cells.append(value)
                        else:
                            value = '' if value is None else str(value)
                            cells.extend(value[part*32000:(part+1)*32000] for part in range(widths[key]))
                    append_literal(sheet, cells)
                book.save(temporary)
                os.replace(temporary, path)
                count += 1
            except PermissionError:
                complete = False
                log.warning('Close workbook to refresh %s; journal retained', path)
            finally:
                book.close()
                temporary.unlink(missing_ok=True)
    if complete:
        temporary_manifest = manifest_path.with_suffix('.tmp.json')
        temporary_manifest.write_text(json.dumps({'high_water': cutoff}))
        os.replace(temporary_manifest, manifest_path)
    return dict(files=count, complete=complete, high_water=cutoff,
                duration_ms=(time.monotonic() - started) * 1000)
