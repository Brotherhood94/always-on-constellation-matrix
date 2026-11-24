import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
from urllib.parse import quote

from provision_stack import MatrixError, request

deployment = Path('/opt/matrix-root-operations')
legacy = Path('/root/matrix-root')
credentials = deployment / 'gateway-credentials'
uid = int(os.environ['ROOT_GATEWAY_UID'])
gid = int(os.environ['ROOT_GATEWAY_GID'])
server = os.environ['MATRIX_SERVER_NAME']
owner = os.environ['MATRIX_OWNER']
root = f'@root:{server}'
device = 'ROOTOPS2'
client = '/_matrix/client/v3'
user_admin = '/_synapse/admin/v2/users/'


def encoded(value):
    return quote(value, safe='')


def save(path, value, mode=0o640, group=gid):
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), 0, group)
            os.fchmod(stream.fileno(), mode)
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


def temporary_admin():
    name, password = 'root-setup-' + secrets.token_hex(8), secrets.token_urlsafe(40)
    path = '/_synapse/admin/v1/register'
    nonce = request('GET', path)['nonce']
    fields = '\0'.join((nonce, name, password, 'admin')).encode()
    key = os.environ['MATRIX_REGISTRATION_SECRET'].encode()
    session = request('POST', path, {'nonce': nonce, 'username': name, 'password': password,
                      'mac': hmac.new(key, fields, hashlib.sha1).hexdigest(), 'admin': True})
    return session['user_id'], session['access_token']


def get_or_missing(path, token=None):
    try:
        return request('GET', path, token=token)
    except MatrixError as error:
        if error.code == 'M_NOT_FOUND':
            return None
        raise


def login(password):
    return request('POST', client + '/login', {
        'type': 'm.login.password', 'identifier': {'type': 'm.id.user', 'user': root},
        'password': password, 'device_id': device, 'initial_device_display_name': 'Root Operations Gateway',
    })['access_token']


def legacy_file(name, forbidden_mode):
    try:
        directory = legacy.lstat()
        path = legacy / name
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (directory.st_uid != os.geteuid() or not stat.S_ISDIR(directory.st_mode) or directory.st_mode & 0o077 or
            metadata.st_uid != os.geteuid() or not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & forbidden_mode):
        raise SystemExit('The retired Root credentials are not in their expected protected files.')
    return path.read_text().strip()


def recover_legacy(agents):
    document = legacy_file('config.json', 0o022)
    if not document:
        raise SystemExit('The Root Matrix ID is already active under a different credential. The retired installation could not be verified.')
    config = json.loads(document)
    if (config.get('root'), config.get('owner'), config.get('agents'), config.get('url')) != (
            root, owner, agents, 'http://synapse:8008'):
        raise SystemExit('The retired installation does not match this Root account and Agents room.')
    password = legacy_file('matrix-password', 0o077)
    if password:
        try:
            return login(password)
        except MatrixError as error:
            if error.code != 'M_FORBIDDEN':
                raise
    return None


def identity(agents):
    token_file = credentials / 'matrix-token'
    if token_file.exists():
        token = token_file.read_text().strip()
    else:
        password_file = deployment / 'matrix-password'
        if not password_file.exists():
            save(password_file, secrets.token_urlsafe(40) + '\n', 0o600, 0)
        password = password_file.read_text().strip()
        try:
            token = login(password)
        except MatrixError as error:
            if error.code not in ('M_FORBIDDEN', 'M_USER_DEACTIVATED'):
                raise
            temporary, admin = temporary_admin()
            try:
                path = user_admin + encoded(root)
                prior = get_or_missing(path, admin)
                if prior and (prior.get('name') != root or prior.get('admin') is not False):
                    raise SystemExit('Root resolves to an unexpected administrative identity in Synapse.')
                token = recover_legacy(agents) if prior and not prior.get('deactivated') else None
                if token:
                    print('Reused the retired Root Matrix credential with a fresh device session.')
                else:
                    request('PUT', path, {
                        'password': password, 'admin': False, 'deactivated': False, 'displayname': 'Root',
                    }, admin)
                    token = login(password)
                    if prior and not prior.get('deactivated'):
                        print('Rotated the verified retired Root bot credential and opened a fresh Matrix device session.')
            finally:
                try:
                    request('POST', '/_synapse/admin/v1/deactivate/' + encoded(temporary), {'erase': False}, admin)
                except MatrixError:
                    request('POST', client + '/logout', {}, admin)
                    raise SystemExit('Temporary setup administrator session logged out. Review account ' + temporary) from None
        save(token_file, token + '\n')
    who = request('GET', client + '/account/whoami', token=token)
    if who.get('user_id') != root or who.get('device_id') != device:
        raise SystemExit('Unexpected Root Matrix device.')
    return token


def members(token, room):
    events = request('GET', client + '/rooms/' + encoded(room) + '/state', token=token)
    data = {e['type']: e['content'] for e in events if e['state_key'] == ''}
    active = {e['state_key']: e['content']['membership'] for e in events if e['type'] == 'm.room.member'
              and e['content'].get('membership') in ('invite', 'join')}
    if (data.get('m.room.encryption', {}).get('algorithm') != 'm.megolm.v1.aes-sha2' or
            data.get('m.room.create', {}).get('m.federate') is not False or
            data.get('m.room.join_rules', {}).get('join_rule') != 'invite' or
            data.get('m.room.history_visibility', {}).get('history_visibility') != 'joined' or
            active.keys() - {root, owner} or active.get(root) != 'join'):
        raise SystemExit('Root Operations is not the expected private encrypted room.')
    return active


def ensure_agents_membership(token, agents):
    joined = request('GET', client + '/joined_rooms', token=token)['joined_rooms']
    if agents in joined:
        return True
    dot_path = Path('/srv/agents/dot/state/matrix-token')
    if dot_path.is_file():
        dot_token = dot_path.read_text().strip()
        dot = request('GET', client + '/account/whoami', token=dot_token)
        dot_rooms = request('GET', client + '/joined_rooms', token=dot_token)['joined_rooms']
        if dot.get('user_id') == f'@dot:{server}' and agents in dot_rooms:
            try:
                request('POST', client + '/rooms/' + encoded(agents) + '/invite', {'user_id': root}, dot_token)
            except MatrixError as error:
                if error.code != 'M_FORBIDDEN':
                    raise
    try:
        result = request('POST', client + '/join/' + encoded(agents), {}, token)
    except MatrixError as error:
        if error.code == 'M_FORBIDDEN':
            return False
        raise
    return result.get('room_id') == agents


def provision():
    if not owner.endswith(':' + server) or owner == root:
        raise SystemExit('Invalid approver for Root Operations.')
    prefix = client + '/directory/room/'
    agents = request('GET', prefix + encoded(f'#agents:{server}'))['room_id']
    token = identity(agents)
    alias = 'root-operations-v2'
    prior = get_or_missing(prefix + encoded(f'#{alias}:{server}'))
    if prior:
        audit = prior['room_id']
    else:
        audit = request('POST', client + '/createRoom', {
            'name': 'Root Operations', 'room_alias_name': alias, 'visibility': 'private',
            'preset': 'private_chat', 'creation_content': {'m.federate': False},
            'power_level_content_override': {'invite': 100, 'kick': 100, 'ban': 100, 'redact': 100},
            'initial_state': [
                {'type': 'm.room.encryption', 'state_key': '', 'content': {'algorithm': 'm.megolm.v1.aes-sha2'}},
                {'type': 'm.room.history_visibility', 'state_key': '', 'content': {'history_visibility': 'joined'}},
            ],
        }, token)['room_id']
    config_path = deployment / 'config.json'
    if audit == agents or (config_path.exists() and json.loads(config_path.read_text())['audit'] != audit):
        raise SystemExit('Unexpected Root Operations room identity.')
    active = members(token, audit)
    if owner not in active:
        request('POST', client + '/rooms/' + encoded(audit) + '/invite', {'user_id': owner}, token)
    if ensure_agents_membership(token, agents):
        print('Root is a member of Agents.')
    else:
        print('Invite ' + root + ' to Agents from Element, then run activate.')
    save(config_path, json.dumps({'url': 'http://synapse:8008', 'owner': owner, 'root': root,
                                 'agents': agents, 'audit': audit, 'gateway_uid': uid}) + '\n')
    if sys.argv[1:] == ['ready'] and (owner not in active or active[owner] != 'join'
            or agents not in request('GET', client + '/joined_rooms', token=token)['joined_rooms']):
        raise SystemExit('Accept Root Operations and confirm Root has joined Agents before activation.')


if __name__ == '__main__':
    provision()
