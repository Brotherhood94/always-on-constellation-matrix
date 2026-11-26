#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile

REGISTRATION = '/data/telegram-appservice.yaml'
ADMISSION = 'synapse_admission.TelegramAdmission'
DATABASE_ROLES = ('matrix_telegram_bridge', 'matrix_telegram_audit', 'matrix_telegram_space')


def write(path, value, mode=0o600, uid=None):
    stat = path.stat() if path.exists() else None
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(value if isinstance(value, bytes) else (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode())
            os.fchmod(stream.fileno(), stat.st_mode & 0o777 if stat else mode)
            if stat or uid is not None:
                os.fchown(stream.fileno(), stat.st_uid if stat else uid, stat.st_gid if stat else uid)
            stream.flush()
            os.fsync(stream.fileno())
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


def load(path):
    return json.loads(path.read_text())


def credentials(directory):
    path = directory / 'database.json'
    current = load(path) if path.exists() else {}
    missing = [role for role in DATABASE_ROLES if role not in current]
    if missing:
        current.update({role: secrets.token_hex(32) for role in missing})
        write(path, current)
    return current


def generate(root, private):
    api = load(private_file(private))
    api_id, api_hash = api.get('api_id'), api.get('api_hash')
    if type(api_id) is not int or not 0 < api_id < 2**31 or not re.fullmatch(r'[0-9a-f]{32}', str(api_hash)):
        raise SystemExit('Telegram API credentials must contain a positive api_id and a 32-character api_hash.')
    domain, owner = os.environ['MATRIX_SERVER_NAME'], os.environ['MATRIX_OWNER']
    if not re.fullmatch(r'@[a-z0-9._=/-]+:' + re.escape(domain), owner):
        raise SystemExit('The operator must be a local Matrix account.')
    config = json.load(sys.stdin)
    config['network'].update(api_id=api_id, api_hash=api_hash)
    config['network']['sync'].update(create_limit=0, login_sync_limit=0, direct_chats=False)
    config['network']['takeout'].update(dialog_sync=False, forward_backfill=False, backward_backfill=False)
    config['homeserver'].update(address='http://synapse:8008', domain=domain, software='standard')
    config['appservice'].update(address='http://telegram-bridge:29317', hostname='0.0.0.0', port=29317,
                                public_address='', id='telegram', username_template='telegram_{{.}}')
    config['appservice']['bot'].update(username='telegrambot', displayname='Telegram Bridge')
    config['bridge'].update(split_portals=True, bridge_notices=False, deduplicate_matrix_messages=True,
                            personal_filtering_spaces=False,
                            permissions={'*': 'block', owner: 'admin', f'@telegram-agent:{domain}': 'relay'},
                            portal_create_filter={'mode': 'allow', 'list': [], 'always_deny_from_login': []})
    relay = config['bridge']['relay']
    relay.update(enabled=True, admin_only=True, default_relays=[])
    relay.setdefault('message_formats', {})['m.text'] = '{{ .Message }}'
    config['database'].update(type='postgres', uri=database_uri(root, 'matrix_telegram_bridge'))
    config['matrix'].update(federate_rooms=False, message_error_notices=False, message_status_events=True,
                            sync_direct_chat_list=False,
                            delivery_receipts=False, max_download_size=5_000_000)
    config['double_puppet'].update(servers={}, secrets={}, allow_discovery=False)
    config['encryption'].update(allow=True, default=True, require=True)
    config['backfill'].update(enabled=False, max_initial_messages=0, max_catchup_messages=0)
    config['backfill']['queue'].update(enabled=False, manual=False)
    config['logging']['writers'] = [{'type': 'stdout', 'format': 'json'}]
    write(root / 'bridge/config.yaml', config, uid=991)


def private_file(path):
    source = Path(path)
    if source.stat().st_mode & 0o077:
        raise SystemExit('Make the private Telegram API credentials file readable only by its owner.')
    return source


def database_uri(root, role):
    return f'postgres://{role}:{credentials(root)[role]}@postgres/matrix_telegram?sslmode=disable'


def office_provisioning(root):
    config = json.load(sys.stdin)
    provision = config['provisioning']
    key = provision.get('shared_secret', '')
    if not isinstance(key, str) or not re.fullmatch(r'constellation-office-[0-9a-f]{64}', key):
        key = 'constellation-office-' + secrets.token_hex(32)
    desired = {'shared_secret': key, 'allow_matrix_auth': False, 'debug_endpoints': False}
    matrix = config['matrix']
    relay_formats = config['bridge']['relay'].setdefault('message_formats', {})
    changed = relay_formats.get('m.text') != '{{ .Message }}' or any(provision.get(name) != value for name, value in desired.items()) or matrix.get('sync_direct_chat_list') is not False
    if changed:
        provision.update(desired)
        matrix['sync_direct_chat_list'] = False
        relay_formats['m.text'] = '{{ .Message }}'
        write(root / 'bridge/config.yaml', config, uid=991)
    key_file = root / 'guide/office-bridge-key'
    expected = (key + '\n').encode()
    if not key_file.is_file() or key_file.read_bytes() != expected:
        write(key_file, expected, uid=991)
    print('changed' if changed else 'unchanged')


def database_sql(root):
    for role, password in credentials(root).items():
        if not re.fullmatch(r'[0-9a-f]{64}', password) or role not in DATABASE_ROLES:
            raise SystemExit('Unexpected database role or credential format.')
        print(f"SELECT 'CREATE ROLE {role} LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') \\gexec")
        print(f"ALTER ROLE {role} PASSWORD '{password}';")
    print("SELECT 'CREATE DATABASE matrix_telegram OWNER matrix_telegram_bridge ENCODING ''UTF8'' LC_COLLATE ''C'' LC_CTYPE ''C'' TEMPLATE template0' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='matrix_telegram') \\gexec")
    print('REVOKE CONNECT ON DATABASE matrix_telegram FROM PUBLIC;')
    print('GRANT CONNECT ON DATABASE matrix_telegram TO matrix_telegram_audit, matrix_telegram_space;')


def registration(root, action):
    path = Path(os.environ['MATRIX_DATA']) / 'synapse/homeserver.yaml'
    config = load(path)
    paths = config.setdefault('app_service_config_files', [])
    prior = next((module for module in config.get('modules', []) if module.get('module') == ADMISSION), None)
    modules = [module for module in config.get('modules', []) if module.get('module') != ADMISSION]
    admission_path = path.parent / 'synapse_admission.py'
    changed = False
    if action == 'enable':
        write(path.parent / 'telegram-appservice.yaml', (root / 'bridge/registration.yaml').read_bytes(), 0o640, 991)
        owner = os.environ['MATRIX_OWNER']
        extension = {'module': ADMISSION, 'config': {'owner': owner}}
        source = Path(__file__).with_name('synapse_admission.py').read_bytes()
        if not admission_path.is_file() or admission_path.read_bytes() != source:
            write(admission_path, source, 0o640, 991)
            changed = True
        modules.append(extension)
        changed = changed or prior != extension or REGISTRATION not in paths
        if REGISTRATION not in paths:
            paths.append(REGISTRATION)
    else:
        config['app_service_config_files'] = [entry for entry in paths if entry != REGISTRATION]
    config['modules'] = modules
    write(path, config)
    if action == 'enable':
        print('changed' if changed else 'unchanged')


def update_allowlist(root, operation, peer, receiver):
    if not re.fullmatch(r'(?:user|chat|channel):[1-9][0-9]{0,18}', peer) or not re.fullmatch(r'[1-9][0-9]{0,18}', receiver):
        raise SystemExit('Specify the verified peer and Telegram login ID. Forum topic IDs are unsupported.')
    path = root / 'bridge/config.yaml'
    try:
        config = json.load(sys.stdin)
        portal_filter = config['bridge']['portal_create_filter']
        entries = portal_filter['list']
    except (json.JSONDecodeError, KeyError, TypeError):
        raise SystemExit('Unable to read the bridge portal creation filter.') from None
    if portal_filter.get('mode') != 'allow' or not isinstance(entries, list):
        raise SystemExit('The bridge must use an explicit portal allowlist.')
    target = {'id': peer, 'receiver': receiver}
    if operation == 'allow' and target not in entries:
        entries.append(target)
    if operation == 'disallow' and target in entries:
        entries.remove(target)
    write(path, config)


if __name__ == '__main__':
    command, root, *args = sys.argv[1:]
    directory = Path(root)
    if command == 'generate':
        generate(directory, *args)
    elif command == 'database-sql':
        database_sql(directory)
    elif command == 'office-provisioning':
        office_provisioning(directory)
    elif command == 'space-reader':
        write(directory / 'guide/space-db', (database_uri(directory, 'matrix_telegram_space') + '\n').encode(), uid=991)
    elif command in ('enable', 'disable'):
        registration(directory, command)
    elif command in ('allow', 'disallow'):
        update_allowlist(directory, command, *args)
    else:
        raise SystemExit('Unknown bridge configuration operation.')
