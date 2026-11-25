"""Copyable setup instructions shared by the wizard and printable guide."""
import os
from select import select
import sys
import termios
import tty


TITLE = '✨ Finalize your Constellation setup'
WELCOME_TITLE = '👋 Welcome to Constellation'
WELCOME_INSTRUCTIONS = ('  After signing in to Element, choose and configure the agents to install.\n'
                        '  When they are ready, an agent sends a welcome in Element and the wizard prints its link.')


def interactive_terminal():
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get('TERM') not in (None, '', 'dumb')


def heading(title):
    if sys.stdout.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ:
        return f'\x1b[1;36m{title}\x1b[0m'
    return title


def element_locations(settings):
    lines = ['  • Agents → VPS Alerts → Pinned messages → Workspace shortcuts'
             ' has the dashboard and Context Intelligence.']
    if settings['dot']:
        lines.append('  • Agents → VPS Alerts → Pinned messages → Connect Dot has the tunnel setup.')
    if settings['telegram']:
        lines.append('  • Open Telegram → Telegram Control → Pinned messages → Set up Telegram.')
    return '\n'.join(lines)


def element_page(settings):
    server = settings['server_name']
    tailnet = '\n     Connect this computer to Tailscale before opening the workspace links.' if settings['tailscale'] else ''
    return '💬 Element Desktop', f"""  1. Download and install Element Desktop.
     https://element.io/download{tailnet}
  2. Choose Sign in. Under Account provider, find the current server (usually matrix.org).
     Click Edit to the right of the server name, above Continue.
  3. Paste your Matrix homeserver into Other account provider and select Continue.
     https://{server}
  4. Enter your Matrix credentials, select Sign in, and open Agents → Office.
     Username: {settings['owner']}
     Password: Your Matrix operator password from setup

{element_locations(settings)}"""


def show_printable(settings, command):
    print(f'\n{heading(TITLE)}')
    title, body = element_page(settings)
    print(f'\n{heading(title)}\n{body}')
    print(f'\n{heading(WELCOME_TITLE)}\n{WELCOME_INSTRUCTIONS}')
    print(f'\n  • Resume the guided setup from an interactive terminal.\n    {command} setup-guide\n')


def read_page_key(fd):
    key = os.read(fd, 1)
    if key != b'\x1b' or not select([fd], [], [], 0.1)[0]:
        return key
    key += os.read(fd, 1)
    if key in (b'\x1b[', b'\x1bO') and select([fd], [], [], 0.1)[0]:
        key += os.read(fd, 1)
    return key


def confirm_element(title, body):
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        print(f'\x1b[2J\x1b[H{heading(TITLE)}\n\n{heading(title)}\n{body}\n\n'
              '  → I’m signed in (Enter)', flush=True)
        while True:
            key = read_page_key(fd)
            if key in (b'\r', b'\n', b'\x1b[C', b'\x1bOC'):
                return 'next'
            if key in (b'', b'\x04'):
                return None
    except KeyboardInterrupt:
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
