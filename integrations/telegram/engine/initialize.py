import json
import os
from pathlib import Path
import re
import tempfile

home = Path('/instance')
workspace = home / 'workspace'
backend = home / 'backend'
models = re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{2,79}', os.environ['TELEGRAM_MODEL'])
if not models:
    raise SystemExit('Specify a model available to the dedicated provider account.')


def write(path, text):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
        pending = Path(file.name)
        try:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


bundle = f'''bundle:
  name: telegram-communication
  version: 0.1.0+amplifier-unified.snapshot.1
  description: Conversation-scoped draft writing without tools
session:
  orchestrator:
    module: loop-live
  context:
    module: context-simple
    source: /opt/amplifier-acp/.venv/lib/python3.13/site-packages/amplifier_module_context_simple
providers:
  - module: provider-openai
    source: /opt/amplifier-acp/.venv/lib/python3.13/site-packages/amplifier_module_provider_openai
    config:
      default_model: {models.group()}
      max_tokens: 700
      reasoning_effort: low
tools: []
agents: []
'''
fixed_bundle = backend / 'telegram-communication.yaml'
if fixed_bundle.exists() and fixed_bundle.read_text() != bundle:
    raise SystemExit('The backend configuration changed. Reconcile and rotate session generations first.')
write(fixed_bundle, bundle)
local_settings = backend / 'amplifier'
local_settings.mkdir(mode=0o700, exist_ok=True)
write(local_settings / 'settings.yaml', 'bundle:\n  app: []\n')
write(backend / 'agent.json', json.dumps({
    'home': str(backend / 'amplifier'), 'appHome': str(backend / 'acp'), 'bundle': str(fixed_bundle),
}))
config = home / 'config.json'
document = json.loads(config.read_text())
if not document['config']['setup_complete']:
    document['config'].update(setup_complete=True, enabled=True, purpose='Draft authorized Telegram replies', backend={
        'command': '/usr/local/bin/backend', 'args': [], 'env': {}, 'secret_env': {},
        'working_directory': str(workspace), 'allowed_working_roots': [str(workspace)],
    })
    write(config, json.dumps(document))
