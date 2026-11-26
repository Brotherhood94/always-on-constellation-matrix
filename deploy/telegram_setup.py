"""Interactive bridge and Telegram Agent setup using the existing operator commands."""
import argparse
import os
from pathlib import Path
from pwd import getpwnam
from subprocess import PIPE

from installer import run_telegram, telegram, workspace_command
from setup_guide import heading, interactive_terminal


def setup(data, arguments=()):
    parser = argparse.ArgumentParser(prog='constellation_matrix telegram setup', allow_abbrev=False)
    parser.add_argument('--api-file', type=Path, help='Private Telegram API credentials JSON')
    args = parser.parse_args(arguments)
    command = f'{workspace_command(data, elevated=False)} telegram setup'
    if not interactive_terminal():
        raise ValueError(f'Run {command} from an interactive terminal')
    previous = None
    try:
        while True:
            stage = run_telegram(data, 'setup-state', stdout=PIPE, text=True).stdout.strip()
            if stage == 'managed':
                print('Telegram Agent is managed through host enrollment or an agent move.')
                return
            if stage == 'ready':
                print('Telegram Agent is running. Mention @Telegram Agent in Office or Telegram Bridge.')
                return
            if stage == previous:
                print(f'Telegram setup is still pending. Resume with:\n  {command}')
                return
            if previous is None:
                print(f'\n{heading("📨 Set up Telegram")}\nPress Ctrl+C to pause and resume later.\n', flush=True)
            previous = stage

            if stage == 'credentials':
                path = args.api_file
                if path is None:
                    sudo_user = os.environ.get('SUDO_USER')
                    home = Path(getpwnam(sudo_user).pw_dir) if sudo_user else Path.home()
                    default = home / 'telegram-api.json'
                    if not default.is_file():
                        print('Open https://my.telegram.org/apps and get App api_id and App api_hash.\n'
                              'Save them in a private JSON file with mode 0600. docs/telegram.md has an example.', flush=True)
                    selected = input(f'Path to your private Telegram API file [{default}] ').strip()
                    path = Path(selected).expanduser() if selected else default
                if not path.is_file():
                    print(f'Telegram API credentials file not found. Create it, then run:\n  {command}')
                    return
                telegram(data, ['prepare', str(path.absolute())])
            elif stage == 'bridge':
                print('Activating Telegram Bridge. Use your existing Matrix operator password when prompted.', flush=True)
                telegram(data, ['activate'])
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
                telegram(data, ['probe'])
            elif stage == 'start':
                print('Starting Telegram Agent.', flush=True)
                telegram(data, ['start-agent'])
                run_telegram(data, 'metrics')
            else:
                raise ValueError('Could not determine Telegram setup progress')
    except (KeyboardInterrupt, EOFError):
        print(f'\nResume Telegram setup with:\n  {command}')
