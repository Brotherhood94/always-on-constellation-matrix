import json
from pathlib import Path
import sys

import yaml


def extend(service, catalog, sources):
    existing = service.setdefault('volumes', [])
    targets = {'/agent-catalog', *sources}
    volumes = [item for item in existing if (item['target'] if isinstance(item, dict) else item.split(':')[1]) not in targets]
    for target, source in {'/agent-catalog': catalog, **sources}.items():
        volumes.append(dict(type='bind', source=source, target=target, read_only=True))
    service['volumes'] = volumes
    environment = service.setdefault('environment', {})
    if isinstance(environment, list):
        environment = {name: value if separator else None for name, separator, value in
                       (item.partition('=') for item in environment)}
        service['environment'] = environment
    environment['MATRIX_CATALOG_PATH'] = '/agent-catalog/agents.json'


path, output, dashboard, bindings = sys.argv[1:]
source = Path(path)
config = (yaml.safe_load(source.read_text()) if source.exists() else {}) or {}
for name, mounts in json.loads(Path(bindings).read_text()).items():
    extend(config.setdefault('services', {}).setdefault(name, {}), dashboard, mounts)
Path(output).write_text(yaml.safe_dump(config, sort_keys=False))
