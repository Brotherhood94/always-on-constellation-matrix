"""Exercise the pinned Amplifier UI in a terminal using an offline provider fixture."""
import errno
import os
from pathlib import Path
import pty
import select
import sys
from tempfile import TemporaryDirectory
import time
import traceback
from types import SimpleNamespace

import yaml


def run_amplifier(home):
    sys.path.insert(0, '/opt/office')
    import provider_setup
    from amplifier_app_cli import provider_config_utils
    from amplifier_app_cli.provider_manager import ProviderManager

    class FixtureProvider:
        def auth_status(self):
            return 'unauthenticated'

        def login(self, print_fn):
            print_fn('Fixture browser sign-in complete')
            return True

    provider_setup.HOME = home
    ProviderManager.list_providers = lambda self: [
        ('provider-openai-chatgpt', 'Fixture provider', ''),
    ]
    provider_config_utils.get_provider_info = lambda provider_id: {
        'display_name': 'Fixture provider',
        'capabilities': ['auth:oauth-device-code'],
        'config_fields': [
            {'id': 'auth_mode', 'display_name': 'Fixture authentication mode', 'prompt': 'Enter mode'},
            {'id': 'reasoning_effort', 'display_name': 'Fixture reasoning effort',
             'prompt': 'Enter effort', 'requires_model': True},
        ],
    }
    provider_config_utils.load_provider_class = lambda provider_id: FixtureProvider
    provider_config_utils._try_instantiate_provider = lambda cls, config: cls()
    provider_config_utils.list_models_for_instance = lambda instance: [
        SimpleNamespace(id='fixture-model', display_name='Fixture model', capabilities=[]),
    ]
    provider_setup.main()


def verify(home):
    pid, terminal = pty.fork()
    if pid == 0:
        os.environ.update(TERM='xterm-256color', COLUMNS='100', LINES='30', AGENT_NAME='Analyst Fixture')
        try:
            run_amplifier(home)
        except SystemExit as result:
            os._exit(0 if result.code in (None, 0) else 1)
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    steps = [
        (b'Choice', b'a\r', False),
        (b'Which provider?', b'1\r', True),
        (b'Enter mode', b'chatgpt_codex\r', True),
        (b'Start it now?', b'\r', True),
        (b'Fixture model', b'1\r', True),
        (b'Enter effort', b'medium\r', True),
        (b'Provider added:', b'd\r', True),
    ]
    output = bytearray()
    cursor = 0
    deadline = time.monotonic() + 20
    try:
        while time.monotonic() < deadline:
            if not select.select([terminal], [], [], 0.2)[0]:
                continue
            try:
                chunk = os.read(terminal, 65536)
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                break
            if not chunk:
                break
            output.extend(chunk)
            if steps:
                marker, keys, page_break = steps[0]
                index = output.find(marker, cursor)
                if index >= 0:
                    page = output[cursor:index]
                    if page_break:
                        assert b'\x1b[2J' in page, marker
                    assert b'Amplifier setup for Analyst Fixture' in page, marker
                    cursor = index + len(marker)
                    steps.pop(0)
                    os.write(terminal, keys)
        else:
            os.kill(pid, 15)
    finally:
        os.close(terminal)
    _, status = os.waitpid(pid, 0)
    assert status == 0 and not steps, (status, steps, output[-1500:])
    assert b'\x1b[?1049h' in output and b'\x1b[?1049l' in output
    settings = yaml.safe_load((home / 'settings.yaml').read_text())
    config = settings['config']['providers'][0]['config']
    assert config['auth_mode'] == 'chatgpt_codex'
    assert config['default_model'] == 'fixture-model' and config['reasoning_effort'] == 'medium'


if __name__ == '__main__':
    with TemporaryDirectory() as directory:
        home = Path(directory) / 'amplifier'
        home.mkdir()
        (home / 'settings.yaml').write_text('{"bundle":{"app":[]}}')
        verify(home)
    print('Amplifier provider, credential, sign-in, model, and final settings pages passed.')
