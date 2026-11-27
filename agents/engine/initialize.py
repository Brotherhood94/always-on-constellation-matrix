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
    workspace_tools = os.environ.get('AGENT_WORKSPACE_TOOLS') == 'true'
    repo = os.environ.get('MATRIX_REPOSITORY_DRAFTS') == 'true'
    if workspace_tools or repo or os.environ.get('MATRIX_CATALOG_PATH'):
        name, script = ('workspace_files', 'workspace-mcp') if workspace_tools or repo else ('approved_tools', 'scoped-mcp')
        tools.append({
            'module': 'tool-mcp', 'source': MODULES + '/amplifier_module_tool_mcp',
            'config': {'servers': {name: {
                'command': 'node', 'args': [f'/opt/workspace-tools/{script}.mjs'],
                'env': {'MATRIX_WORKSPACE': '/workspace/constellation-matrix' if repo else '/workspace',
                        'AGENT_USERNAME': os.environ['AGENT_USERNAME'],
                        **({'MATRIX_REPOSITORY_DRAFTS': 'true'} if repo else
                           {key: os.environ[key] for key in ('MATRIX_CATALOG_PATH', 'MATRIX_SERVER_NAME', 'TEAM_ROLE')
                            if key in os.environ})},
            }}},
        })
    providers = provider_profiles(settings)
    managed_mode = os.environ.get('HIRING_PROVIDER_MODE')
    if managed_mode:
        providers = [{'module': 'provider-constellation-hiring',
                      'source': MODULES + '/amplifier_module_provider_constellation_hiring',
                      'config': {'mode': managed_mode, 'default_model': os.environ['AGENT_MODEL'],
                                 'reasoning_effort': os.environ['AGENT_REASONING']}}]
    bundle = BACKEND / 'office.yaml'
    save(bundle, {
        'bundle': {'name': 'matrix-office', 'version': '1.0.0+amplifier-unified.snapshot.1'},
        'session': {'orchestrator': {'module': 'loop-live'}, 'context': {
            'module': 'context-simple', 'source': MODULES + '/amplifier_module_context_simple',
        }},
        'providers': providers,
        'tools': tools, 'agents': [],
    })
    module_sources = {
        'provider-' + name: MODULES + '/amplifier_module_provider_' + name.replace('-', '_')
        for name in ('anthropic', 'openai', 'openai-chatgpt')
    }
    if managed_mode:
        module_sources['provider-constellation-hiring'] = MODULES + '/amplifier_module_provider_constellation_hiring'
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
