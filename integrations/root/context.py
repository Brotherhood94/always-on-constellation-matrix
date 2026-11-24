import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from tempfile import NamedTemporaryFile


def configure(server, token, gid):
    descriptor = os.open(server, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        root = json.load(stream)
    if root.get('auth_mode') != 'static' or root.get('access_control_mode') != 'scoped' or root.get('allow_unauthenticated'):
        raise SystemExit('The existing Context Intelligence authorization is not scoped static authentication.')
    if not token.exists():
        with token.open('x') as stream:
            stream.write(secrets.token_urlsafe(32) + '\n')
    os.chown(token, 0, gid)
    token.chmod(0o440)
    digest = hashlib.sha256(token.read_text().strip().encode()).hexdigest()
    keys = root.setdefault('api_keys', {})
    if any(entry.get('id') == 'root-operations-agent' and key != digest for key, entry in keys.items()):
        raise SystemExit('Root already has another Context Intelligence writer identity.')
    keys[digest] = {'id': 'root-operations-agent'}
    root.setdefault('contributor_grants', {})['root-operations-agent'] = {
        'workspaces': ['root-operations'], 'capabilities': ['live:write'],
    }
    with NamedTemporaryFile('w', dir=server.parent, prefix='.root-server.', delete=False) as stream:
        pending = Path(stream.name)
        try:
            json.dump(root, stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), 991, 991)
            os.fchmod(stream.fileno(), 0o400)
            pending.replace(server)
        finally:
            pending.unlink(missing_ok=True)
    print(digest)


def seed(digest):
    from context_intelligence_server.identity_store import IdentityStore
    store = IdentityStore(Path('/data/identity/api-keys.json'))
    store.load()
    if any(identity == 'root-operations-agent' and key != digest for key, identity in store.flat_dict.items()):
        raise SystemExit('The persisted Root writer identity differs.')
    store.put(digest, {'id': 'root-operations-agent'})


if __name__ == '__main__':
    if sys.argv[1] == 'configure':
        configure(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
    elif sys.argv[1] == 'seed':
        seed(sys.argv[2])
