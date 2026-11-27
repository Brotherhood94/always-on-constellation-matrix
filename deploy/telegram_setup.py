"""Interactive bridge and Telegram Agent setup using the existing operator commands."""
import argparse
import getpass
import json
import tempfile
import os
from pathlib import Path
from pwd import getpwnam
from subprocess import CalledProcessError, PIPE

from installer import run_telegram, telegram, workspace_command
from setup_guide import heading, interactive_terminal


PROBE_AUTHENTICATION_ERROR = 42


def prompt_credentials(data, home, compose_runtime, default):
    print('Save a private ~/telegram-api.json on the VPS with mode 0600, or enter a different VPS path.\n'
          'Get your App api_id and App api_hash from https://my.telegram.org/apps.', flush=True)
    prompt = ('Telegram API JSON path [~/telegram-api.json]: ' if default else
              'Telegram API JSON path (Enter to type the ID and hash): ')
    while True:
        supplied = input(prompt).strip()
        if not supplied:
            if default:
                telegram(data, ['prepare', str(default)])
                return
            try:
                api_id = int(input('Telegram App api_id: ').strip())
            except ValueError as error:
                raise ValueError('Enter the numeric Telegram App api_id') from error
            api_hash = getpass.getpass('Telegram App api_hash: ').strip()
            content = json.dumps({'api_id': api_id, 'api_hash': api_hash}).encode()
            break
        path = home / supplied[2:] if supplied.startswith('~/') else Path(supplied).absolute()
        if not compose_runtime:
            if path.is_file():
                telegram(data, ['prepare', str(path)])
                return
            print('The private Telegram API JSON file does not exist.', flush=True)
            continue
        from operator_runtime import read_operator_credentials
        try:
            content = read_operator_credentials(path)
        except ValueError as error:
            print(error, flush=True)
        else:
            break
    with tempfile.TemporaryDirectory(prefix='constellation-telegram-') as directory:
        credentials = Path(directory) / 'api.json'
        with open(credentials, 'xb', opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            stream.write(content)
        telegram(data, ['prepare', str(credentials)])


def setup(data, arguments=(), *, install_agent=True, owner_passphrase=None):
    parser = argparse.ArgumentParser(prog='constellation_matrix telegram setup', allow_abbrev=False)
    parser.add_argument('--api-file', type=Path, help='Private Telegram API credentials JSON')
    args = parser.parse_args(arguments)
    command = f'{workspace_command(data, elevated=False)} telegram setup'
    if not interactive_terminal():
        raise ValueError(f'Run {command} from an interactive terminal')
    previous = None
    mode = 'agent' if install_agent else 'bridge'
    try:
        while True:
            stage = run_telegram(data, 'setup-state', mode, stdout=PIPE, text=True).stdout.strip()
            if stage == 'managed':
                print('Telegram Agent is managed through host enrollment or an agent move.')
                return
            if stage == 'ready':
                run_telegram(data, 'metrics')
                if install_agent:
                    print('Telegram Agent is running. Mention @Telegram Agent in Office or Telegram Bridge.')
                else:
                    print('Your Telegram account is linked in Telegram Bridge.')
                return
            if stage == previous:
                print(f'Telegram setup is still pending. Resume with:\n  {command}')
                return
            if previous is None:
                title = "📨 Telegram Agent" if install_agent else "📨 Connect Telegram Bridge"
                print(f'\n{heading(title)}\nPress Ctrl+C to pause and resume later.\n', flush=True)
            previous = stage

            if stage == 'credentials':
                path = args.api_file
                if path is None:
                    compose_runtime = os.environ.get('CONSTELLATION_RUNTIME') == 'compose'
                    if compose_runtime:
                        home = Path(os.environ.get('CONSTELLATION_OPERATOR_HOME') or Path.home())
                        mounted = os.environ.get('CONSTELLATION_TELEGRAM_API_FILE')
                        default = Path(mounted) if mounted else None
                    else:
                        sudo_user = os.environ.get('SUDO_USER')
                        home = Path(getpwnam(sudo_user).pw_dir) if sudo_user else Path.home()
                        default = home / 'telegram-api.json'
                    prompt_credentials(data, home, compose_runtime, default if default and default.is_file() else None)
                    continue
                if not path.is_file():
                    raise ValueError('The private Telegram API credentials file does not exist')
                telegram(data, ['prepare', str(path.absolute())])
            elif stage == 'bridge':
                print('Activating Telegram Bridge.', flush=True)
                if owner_passphrase is None:
                    print('Enter your existing Matrix operator password when prompted.', flush=True)
                    telegram(data, ['activate'])
                else:
                    telegram(data, ['activate'], owner_passphrase=owner_passphrase)
            elif stage == 'login':
                print('In Element, open Telegram → Rooms → Telegram Bridge and send:\n'
                      '  !tg login qr\n'
                      'On your phone, open Telegram → Settings → Devices → Link Desktop Device and scan the code.\n'
                      'If the bot asks for your two-step verification password, send a new message in Telegram Bridge:\n'
                      '  !tg <your Telegram two-step verification password>\n'
                      'Replace the placeholder, including the brackets. Use your Telegram password.\n'
                      'If the login has expired, send !tg cancel and then !tg login qr for a fresh code.', flush=True)
                input('After the bot confirms that you are logged in, press Enter. ')
            elif stage == 'engine':
                print('Registering Telegram Agent and preparing its dedicated Amplifier Instance.', flush=True)
                telegram(data, ['prepare-agent'])
            elif stage == 'provider':
                print('Telegram Agent · Sign in with ChatGPT Codex and verify the Amplifier connection.', flush=True)
                telegram(data, ['sign-in'])
                try:
                    telegram(data, ['probe'])
                except CalledProcessError as error:
                    if error.returncode != PROBE_AUTHENTICATION_ERROR:
                        raise
                    input('Press Enter to open a fresh Codex device sign-in, or Ctrl+C to pause. ')
                    telegram(data, ['sign-in', '--reauthenticate'])
                    telegram(data, ['probe'])
            elif stage == 'start':
                print('Starting Telegram Agent.', flush=True)
                telegram(data, ['start-agent'])
            else:
                raise ValueError('Could not determine Telegram setup progress')
    except (KeyboardInterrupt, EOFError):
        print(f'\nResume Telegram setup with:\n  {command}')
