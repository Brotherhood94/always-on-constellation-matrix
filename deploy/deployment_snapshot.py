"""Publish a configuration-only input for planning and isolated scenarios."""
from hashlib import sha256
import json


def publish(data, settings, registry, manifest):
    from installer import directory, save
    directory(data / 'architecture', 0o755)
    agents = {}
    for key, entry in registry['agents'].items():
        if entry.get('profile') and not entry.get('retirement'):
            agents[key] = {field: entry[field] for field in ('name', 'model', 'effort', 'profile')}
    body = {'settings': {key: settings.get(key) for key in ('project', 'telegram', 'dot', 'tailscale')},
            'agents': agents,
            'services': {key: {'image': value.get('image'),
                              'checks': {name: value['healthcheck'][name] for name in ('interval', 'timeout', 'start_period', 'retries')
                                         if name in value.get('healthcheck', {})},
                              'memory': value.get('mem_limit')}
                         for key, value in manifest['services'].items()}}
    revision = sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    save(data / 'architecture/snapshot.json', {'revision': revision, **body}, mode=0o444)
