"""Configure a pinned Amplifier backend for a Constellation Instance."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

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


def configure():
    BACKEND.mkdir(mode=0o700, exist_ok=True)
    settings = BACKEND / 'amplifier'
    settings.mkdir(mode=0o700, exist_ok=True)
    save(settings / 'settings.yaml', {'bundle': {'app': []}})
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
        'providers': [{
            'module': 'provider-openai-chatgpt', 'source': MODULES + '/amplifier_module_provider_openai_chatgpt',
            'config': {'auth_mode': 'chatgpt_codex', 'token_file_path': '/instance/backend/codex-oauth.json',
                       'default_model': os.environ['AGENT_MODEL'], 'reasoning_effort': os.environ['AGENT_REASONING']},
        }],
        'tools': tools, 'agents': [],
    })
    save(BACKEND / 'agent.json', {'home': str(settings), 'appHome': str(BACKEND / 'acp'),
                                  'bundle': str(bundle), 'runtimeImmutable': True})
    path = HOME / 'config.json'
    document = json.loads(path.read_text())
    document['config'].update(setup_complete=True, enabled=True, purpose='Agent in the private Matrix deployment', backend={
        'command': '/usr/local/bin/backend', 'args': [], 'env': {}, 'secret_env': {},
        'working_directory': '/workspace', 'allowed_working_roots': ['/workspace'],
    })
    save(path, document)


if __name__ == '__main__':
    configure()
