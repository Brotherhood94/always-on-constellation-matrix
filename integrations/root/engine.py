import asyncio
import json
import os
from pathlib import Path
import sys


home = Path('/instance')
backend = home / 'backend'
token = backend / 'codex-oauth.json'


def configure():
    os.umask(0o077)
    backend.mkdir(exist_ok=True)
    (backend / 'amplifier').mkdir(exist_ok=True)
    workspace = home / 'workspace'
    workspace.mkdir(exist_ok=True)
    bundle = backend / 'root-maintenance.yaml'
    bundle.write_text('''bundle:
  name: root-maintenance
  version: 1.0.0+amplifier-unified.snapshot.1
  description: Prepare privileged maintenance proposals for explicit approval
session:
  orchestrator:
    module: loop-live
  context:
    module: context-simple
    source: /opt/amplifier-acp/.venv/lib/python3.13/site-packages/amplifier_module_context_simple
providers:
  - module: provider-openai-chatgpt
    source: /opt/amplifier-acp/.venv/lib/python3.13/site-packages/amplifier_module_provider_openai_chatgpt
    config:
      auth_mode: chatgpt_codex
      token_file_path: /instance/backend/codex-oauth.json
      default_model: gpt-6.2-sol
      reasoning_effort: xhigh
tools: []
agents: []
''')
    (backend / 'amplifier/settings.yaml').write_text('bundle:\n  app: []\n')
    (backend / 'agent.json').write_text(json.dumps({
        'home': str(backend / 'amplifier'), 'appHome': str(backend / 'acp'), 'bundle': str(bundle),
        'runtimeImmutable': True,
    }))
    path = home / 'config.json'
    instance = json.loads(path.read_text())
    instance['config'].update(setup_complete=True, enabled=True, purpose='Propose VPS maintenance for review', backend={
        'command': '/usr/local/bin/backend', 'args': [], 'env': {}, 'secret_env': {},
        'working_directory': str(workspace), 'allowed_working_roots': [str(workspace)],
    })
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(instance))
    temporary.replace(path)


async def sign_in():
    from amplifier_module_provider_openai_chatgpt import ChatGPTProvider
    provider = ChatGPTProvider({'auth_mode': 'chatgpt_codex', 'token_file_path': str(token)})
    try:
        async with asyncio.timeout(360):
            if provider.auth_status() != 'authenticated':
                await provider.login()
            if provider.auth_status() != 'authenticated':
                raise SystemExit('Root Codex sign-in did not complete.')
    finally:
        await provider.close()
    print('Root is signed in with its dedicated Codex credentials.')


if __name__ == '__main__':
    if sys.argv[1] == 'configure':
        configure()
    elif sys.argv[1] == 'sign-in' and sys.stdin.isatty():
        asyncio.run(sign_in())
    else:
        raise SystemExit('Run sign-in from an interactive root terminal.')
