import json
from pathlib import Path
import sys

import yaml


source, destination, epoch, config = sys.argv[1:]
compose = yaml.safe_load(Path(source).read_text())
names = {"context": ("context-intelligence-data", "context-intelligence-v2-data"),
         "neo4j": ("context-intelligence-neo4j-data", "context-intelligence-v2-neo4j-data")}
for service, (old, new) in names.items():
    items = compose['services'][service]['volumes']
    if items.count(f'{old}:/data') != 1 or new in compose['volumes']:
        raise SystemExit(f'Unexpected data volume for {service}')
    items[items.index(f'{old}:/data')] = f'{new}:/data'
    compose['volumes'][new] = compose['volumes'].pop(old)
engine = compose['services']['context']
if engine.get('image') != 'matrix-context-intelligence:6.13.0-1622b48':
    raise SystemExit('The installed Context Intelligence image differs from the tested base')
engine['image'] = 'matrix-context-intelligence:6.13.0-execution-v2'
engine.pop('build', None)
engine['environment']['CONTEXT_EXECUTION_MIN_EPOCH_MS'] = str(int(epoch))
identity = json.loads(Path(config).read_text())
if identity.get('auth_mode') != 'static' or identity.get('api_keys_store_path') != '/data/identity/api-keys.json':
    raise SystemExit('Installed identity storage differs from the tested layout')
print('Configured contributor grants:', ', '.join(sorted(identity['contributor_grants'])))
Path(destination).write_text(yaml.safe_dump(compose, sort_keys=False))
