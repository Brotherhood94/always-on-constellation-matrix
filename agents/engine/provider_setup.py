"""Run Amplifier's own provider setup against this Instance's private home."""
from importlib import import_module
import json
import os
from pathlib import Path
import re
import sys

import yaml

HOME = Path('/instance/backend/amplifier')
PROVIDERS = {'provider-anthropic', 'provider-openai', 'provider-openai-chatgpt'}
MODEL = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}')


def provider_profiles(home):
    try:
        settings = yaml.safe_load((home / 'settings.yaml').read_text()) or {}
        providers = settings.get('config', {}).get('providers', [])
        disabled = settings.get('configurator', {}).get('disabled', {}).get('providers', [])
        enabled = []
        for row in providers:
            identity = row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-')
            override = settings.get('overrides', {}).get(identity, {})
            if identity not in disabled and row.get('enabled', True) and override.get('enabled', True):
                if row['module'] not in PROVIDERS:
                    raise ValueError
                config = row['config']
                key = config.get('api_key')
                if key and not re.fullmatch(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}', key):
                    raise ValueError
                profile = {'module': row['module'], 'config': config, 'id': identity}
                enabled.append((config.get('priority', 100), identity, profile))
        return [item[2] for item in sorted(enabled, key=lambda item: item[:2])]
    except (AttributeError, KeyError, TypeError, ValueError, OSError, yaml.YAMLError):
        raise SystemExit('Amplifier provider settings require a supported module and private credential references.') from None


def configured_provider(home):
    try:
        selected = provider_profiles(home)[0]
        module, config = selected['module'], selected['config']
        model = config['default_model']
        effort = config.get('reasoning_effort')
        if not MODEL.fullmatch(model):
            raise ValueError
        if effort is not None and not MODEL.fullmatch(effort):
            raise ValueError
        return {'provider': module, 'model': model, 'effort': effort}
    except (IndexError, KeyError, TypeError, ValueError):
        raise SystemExit('Select a supported provider and model in Amplifier before activating this agent.') from None


def setup_console():
    from amplifier_app_cli import provider_config_utils
    from rich.console import Console
    from rich.text import Text

    name = os.environ.get('AGENT_NAME') or os.environ.get('AGENT_USERNAME')
    title = f'Amplifier setup for {name}' if name else 'Amplifier provider setup'

    class AgentSetupConsole(Console):
        def clear(self, home=True):
            super().clear(home=home)
            self.print(Text(title, style='bold'))
            self.print()

    console = AgentSetupConsole()
    provider_config_utils.console = console
    import_module('amplifier_app_cli.commands.provider').console = console
    return console


def main():
    os.umask(0o077)
    if sys.argv[1:] == ['metadata']:
        print(json.dumps(configured_provider(HOME)))
        return
    if sys.argv[1:] or not sys.stdin.isatty():
        raise SystemExit('Run Amplifier provider setup in the operator terminal.')
    os.environ['AMPLIFIER_HOME'] = str(HOME)
    os.chdir(HOME.parent)
    # Only show providers pre-installed from the release's pinned packages.
    from amplifier_app_cli.provider_sources import DEFAULT_PROVIDER_SOURCES

    DEFAULT_PROVIDER_SOURCES.clear()
    from amplifier_app_cli.__main__ import main as amplifier
    sys.argv[:] = ['amplifier', 'provider', 'manage', '--scope', 'global']
    console = setup_console()
    with console.screen(hide_cursor=False):
        console.clear()
        amplifier()


if __name__ == '__main__':
    main()
