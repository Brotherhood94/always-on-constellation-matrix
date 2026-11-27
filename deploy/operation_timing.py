"""Durable metadata-only timing records and their weekly rollup."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import time
import uuid


def record(path, kind, start, outcome, *, actor=None, queue_ms=None, environment=None):
    if not path.is_dir() or not re.fullmatch('[a-z][a-z0-9-]{0,31}', kind):
        return
    identifier = uuid.uuid4().hex
    duration = round((time.monotonic_ns() - start) / 1_000_000)
    details = {'id': identifier, 'kind': kind, 'outcome': outcome, 'total_ms': duration,
               'work_ms': max(0, duration - (queue_ms or 0)),
               **({'queue_ms': queue_ms} if queue_ms is not None else {}),
               **({'actor': actor} if actor else {}), **({'environment': environment} if environment else {})}
    payload = {'event': 'timing:finished', 'workspace': 'agents-team', 'idempotency_key': 'timing-' + identifier,
               'data': {'session_id': 'timing-' + identifier, 'timestamp': datetime.now(timezone.utc).isoformat(),
                        'timing': details}}
    try:
        with NamedTemporaryFile(dir=path, mode='w', delete=False) as stream:
            pending = Path(stream.name)
            try:
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
                pending.replace(path / (identifier + '.json'))
            finally:
                pending.unlink(missing_ok=True)
    except OSError:
        return


def refresh(data, now):
    from connection_history import context_request, replace_json
    for item in sorted((data / 'runtime/timing').glob('*.json'))[:32]:
        payload = json.loads(item.read_text())
        context_request(data, '/events', payload)
        item.unlink()
    timing = context_request(data, '/office/topology/timings', {})
    replace_json(data / 'dashboard/operation-timings.json', {**timing, 'time': int(now.timestamp())}, 0o644)
    if now.weekday() != 0 or now.hour < 10:
        return
    week = now.strftime('%G-W%V')
    identifier = sha256(('timings-week-' + week).encode()).hexdigest()[:32]
    pending = data / 'monitor/outbox' / (identifier + '.json')
    rows = sorted((row for row in timing['ranges']['7'] if row['scope'] == 'workspace'), key=lambda row: -max(row['work'], row['queue'], row['approval']))
    if not rows or pending.is_file() or (data / 'monitor/acks' / identifier).is_file():
        return
    lines = [f'Operation timings · {week}', 'Slowest groups by cumulative time in their longest measured phase']
    for row in rows[:3]:
        phase = max(('work', 'queue', 'approval'), key=row.get)
        minutes = row[phase] / 60_000
        lines.append(f'• {row["kind"]} · {row["actor"]} · {row["count"]} operations · {phase} {minutes:.1f} min')
    lines.append('Open Operation timings on the dashboard to choose a candidate for a twin experiment.')
    replace_json(pending, {'id': identifier, 'time': int(now.timestamp()), 'body': '\n'.join(lines)}, 0o640)
    os.chown(pending, 0, 991)
