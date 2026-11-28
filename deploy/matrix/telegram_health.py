"""Publish read-only Telegram health for the dashboard and the Office roster."""
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3


FRESHNESS_SECONDS = 180
SERVICES = {'connector', 'telegram-engine', 'telegram-bridge', 'telegram-guide'}


def epoch(value):
    try:
        stamp = datetime.fromisoformat(value)
        return int(stamp.timestamp()) if stamp.tzinfo else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def telegram_status(report, now, *, remote_engine=False):
    checked = epoch(report.get('generatedAt'))
    if checked is None:
        return 'Status unavailable', None
    if not -60 <= now - checked <= FRESHNESS_SECONDS:
        return 'Status stale', checked
    if report.get('paused') is True:
        return 'Paused', checked
    services = report.get('services', {})
    required = ['connector', 'telegram-bridge'] + ([] if remote_engine else ['telegram-engine'])
    if any(name not in services for name in required):
        return 'Setup required', checked
    for name in required:
        service = services[name]
        running = service.get('state') == 'running'
        health = service.get('health')
        if running and health == 'starting':
            return 'Starting', checked
        if not running or (health != 'healthy' and (name != 'connector' or health is not None)):
            return 'Unavailable', checked
    sync = epoch(report.get('lastSync'))
    if sync is None or not -60 <= now - sync <= FRESHNESS_SECONDS:
        return 'Status stale', min(checked, sync) if sync is not None else checked
    return 'Available', min(checked, sync)


def publish_telegram_health(data, services, now):
    from connection_history import replace_json

    connector = data / 'telegram' / 'connector'
    report = {'generatedAt': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'services': services,
              'pendingDrafts': 0, 'unconfirmedDeliveries': 0, 'oldestQueuedAt': None,
              'lastSync': None, 'paused': (connector / 'paused').is_file()}
    database = connector / 'telegram.sqlite3'
    if database.is_file():
        with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True, timeout=2)) as session:
            session.execute('BEGIN')
            report['pendingDrafts'] = session.execute("SELECT COUNT(*) FROM draft WHERE state='awaiting_approval'").fetchone()[0]
            report['unconfirmedDeliveries'] = session.execute(
                "SELECT COUNT(*) FROM draft WHERE state IN ('publishing','matrix_accepted','unknown')").fetchone()[0]
            oldest = session.execute("SELECT MIN(queued) FROM work WHERE state IN ('queued','running')").fetchone()[0]
            if oldest is not None:
                report['oldestQueuedAt'] = datetime.fromtimestamp(oldest / 1000, timezone.utc).isoformat()
            settings = dict(session.execute("SELECT key, value FROM settings WHERE key IN ('last_sync','paused')"))
            report['lastSync'] = settings.get('last_sync')
            report['paused'] = report['paused'] or settings.get('paused') == 'true'
    config = connector / 'config.json'
    if config.is_file():
        report['agent'] = {'name': 'Telegram', 'model': json.loads(config.read_text()).get('model'),
                           'effort': 'low', 'connector': services.get('connector', {}).get('state', 'stopped')}
    replace_json(data / 'dashboard/telegram.json', report, 0o644)
