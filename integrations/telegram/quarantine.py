import json
from pathlib import Path
import sqlite3
import sys

root = Path(sys.argv[1])
registration = root / 'bridge/registration.yaml'
config = root / 'bridge/config.yaml'
if not registration.is_file() or not config.is_file():
    raise SystemExit('The restored AppService configuration is incomplete.')
connector = root / 'connector'
connector.mkdir(mode=0o700, exist_ok=True)
(connector / 'paused').touch(mode=0o600)
database = connector / 'telegram.sqlite3'
if database.exists():
    with sqlite3.connect(database) as session:
        if session.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise SystemExit('The restored connector database did not pass quick_check.')
        session.execute("INSERT OR REPLACE INTO settings VALUES ('paused','true')")
        session.execute('UPDATE binding SET active=0, revision=revision+1')
        session.execute("UPDATE work SET state='interrupted' WHERE state IN ('queued','running')")
        session.execute("UPDATE draft SET state='cancelled' WHERE state IN ('awaiting_approval','ready')")
        session.execute("UPDATE draft SET state='unknown' WHERE state IN ('publishing','matrix_accepted')")
        assert session.execute('SELECT count(*) FROM binding WHERE active=1').fetchone() == (0,)
        assert session.execute("SELECT value FROM settings WHERE key='paused'").fetchone() == ('true',)
    for filename in ('matrix-token', 'engine-token', 'backend-verified'):
        if not (connector / filename).is_file():
            raise SystemExit('The restored connector is missing persistent state.')
print(json.dumps({'private_registration': True, 'connector_paused': True, 'connector_database_present': database.exists()}))
