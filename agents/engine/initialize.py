"""Configure a pinned Amplifier backend for a Constellation Instance."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml

from provider_setup import provider_profiles

HOME = Path('/instance')
BACKEND = HOME / 'backend'
MODULES = '/opt/amplifier-acp/.venv/lib/python3.13/site-packages'


def save(path, value):
    with NamedTemporaryFile(dir=path.parent, mode='w', delete=False) as stream:
        temp = Path(stream.name)
        try:
            json.dump(value, stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def initialize_settings(settings):
    path = settings / 'settings.yaml'
    marker = settings / '.office-provider-settings'
    if marker.exists():
        return
    content = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {'bundle': {'app': []}}
    # Adopt the previous release's private Codex account once on upgrade.
    legacy = BACKEND / 'codex-oauth.json'
    if legacy.is_file() and not content.get('config', {}).get('providers'):
        content.setdefault('config', {})['providers'] = [{
            'module': 'provider-openai-chatgpt',
            'config': {'auth_mode': 'chatgpt_codex', 'token_file_path': str(legacy),
                       'default_model': os.environ['AGENT_MODEL'], 'reasoning_effort': os.environ['AGENT_REASONING']},
        }]
        save(path, content)
    elif not path.exists():
        save(path, content)
    save(marker, {'version': 1})


def configure():
    BACKEND.mkdir(mode=0o700, exist_ok=True)
    settings = BACKEND / 'amplifier'
    settings.mkdir(mode=0o700, exist_ok=True)
    initialize_settings(settings)
    tools = []
    if os.environ.get('AGENT_WORKSPACE_TOOLS') == 'true':
        tools.append({
            'module': 'tool-mcp', 'source': MODULES + '/amplifier_module_tool_mcp',
            'config': {'servers': {'workspace_files': {
                'command': 'node', 'args': ['/opt/workspace-tools/workspace-mcp.mjs'],
                'env': {'MATRIX_WORKSPACE': '/workspace', 'AGENT_USERNAME': os.environ['AGENT_USERNAME']},
            }}},
        })
    bundle = BACKEND / 'office.yaml'
    save(bundle, {
        'bundle': {'name': 'matrix-office', 'version': '1.0.0+amplifier-unified.snapshot.1'},
        'session': {'orchestrator': {'module': 'loop-live'}, 'context': {
            'module': 'context-simple', 'source': MODULES + '/amplifier_module_context_simple',
        }},
        'providers': provider_profiles(settings),
        'tools': tools, 'agents': [],
    })
    module_sources = {
        'provider-' + name: MODULES + '/amplifier_module_provider_' + name.replace('-', '_')
        for name in ('anthropic', 'openai', 'openai-chatgpt')
    }
    save(BACKEND / 'agent.json', {'home': str(settings), 'appHome': str(BACKEND / 'acp'),
                                  'bundle': str(bundle), 'runtimeImmutable': True, 'moduleSources': module_sources})
    path = HOME / 'config.json'
    document = json.loads(path.read_text())
    # Constellation defaults the backend's HOME to /instance/backend. Match
    # the setup CLI's private home so Amplifier can use its saved logins.
    document['config'].update(setup_complete=True, enabled=True, purpose='Agent in the private Matrix deployment', backend={
        'command': '/usr/local/bin/backend', 'args': [], 'env': {'HOME': '${AGENT_HOME}'}, 'secret_env': {},
        'working_directory': '/workspace', 'allowed_working_roots': ['/workspace'],
    })
    save(path, document)


if __name__ == '__main__':
    configure()
