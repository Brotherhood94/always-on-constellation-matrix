"""Copyable setup instructions shared by the wizard and printable guide."""
import os
from select import select
import sys
import termios
import tty


TITLE = '✨ Finalize your Constellation setup'
WELCOME_TITLE = '👋 Welcome to Constellation'
WELCOME_INSTRUCTIONS = ('  The installer configures your selected agents before Element sign-in.\n'
                        '  When they are ready, an agent sends a welcome in Element and the wizard prints its link.')
TELEGRAM_CREDENTIALS = ('1. In your browser, open https://my.telegram.org/apps. Sign in with your Telegram phone number\n'
                        '   and enter the confirmation code Telegram sends to your account.\n\n'
                        '2. Open API development tools. Create an app or open your existing app. Copy App api_id and App api_hash.\n\n'
                        '3. In a second VPS terminal, create and edit a private JSON file outside the checkout.\n'
                        '   If you already have a private Telegram API JSON file, skip to step 4.\n'
                        '     (umask 077; touch ~/telegram-api.json)\n'
                        '     chmod 600 ~/telegram-api.json\n'
                        '     nano ~/telegram-api.json\n'
                        '   Save the following, replacing both sample values. Leave the ID unquoted.\n'
                        '     {"api_id": 123456, "api_hash": "REPLACE_WITH_YOUR_APP_API_HASH"}\n\n'
                        '4. At the Telegram API JSON path prompt, paste the file’s VPS path. Press Enter to use\n'
                        '   the default, or to type the ID and masked hash when there is no default.\n')


def interactive_terminal():
    return sys.stdin.isatty() and sys.stdout.isatty()


def heading(title):
    if sys.stdout.isatty() and os.environ.get('TERM') not in (None, '', 'dumb') and 'NO_COLOR' not in os.environ:
        return f'\x1b[1;36m{title}\x1b[0m'
    return title


def element_locations(settings):
    lines = ['  • Agents → Control Room → Pinned messages → Workspace shortcuts'
             ' has the dashboard and Context Intelligence.']
    if settings['dot']:
        lines.append('  • Agents → Control Room → Pinned messages → Connect Dot has the tunnel setup.')
    if settings['telegram']:
        lines.append('  • Enabling Telegram also installs Telegram Agent automatically.\n'
                     '    In Element, see Telegram → Rooms → Telegram Bridge → Pinned messages → Set up Telegram.')
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


def show_dot_setup(command):
    print(f'''
{heading('🔌 Dot · Secure MCP Tunnel')}

1. In OpenAI Platform, create a Secure MCP Tunnel. Associate it with the ChatGPT workspace that will
   use Dot, then copy the tunnel_… ID. Creating it requires Tunnels Read + Manage.
   https://platform.openai.com/settings/organization/tunnels

2. Create a Restricted runtime API key with Tunnels Read + Use. Use a key owner who has those
   permissions for this tunnel.
   https://platform.openai.com/settings/organization/api-keys

3. On the VPS, run the command below from the Constellation checkout. Replace the placeholder
   with your tunnel ID. Enter the runtime key at the hidden prompt when asked.
   {command} dot-connect tunnel_REPLACE_WITH_YOUR_ID

   Continue when the terminal says “Dot tunnel is ready.”

4. In the associated ChatGPT workspace, open Plugins. Select + → Add custom MCP server and name it
   Constellation Office. Choose Tunnel, select the same tunnel ID, and choose No authentication.
   Review and select Create as a plugin.
   https://chatgpt.com/plugins

5. Open a conversation that uses Constellation Office and ask Dot to run list_agents.
   Compare the returned agents with your Office roster.

   The same guide is pinned in Agents → Control Room → Pinned messages → Connect Dot.
''', flush=True)


def show_printable(settings, command):
    print(f'\n{heading(TITLE)}')
    title, body = element_page(settings)
    print(f'\n{heading(title)}\n{body}')
    if settings['dot']:
        show_dot_setup(command)
    if settings['telegram']:
        print(f'\n{heading("📨 Telegram · API credentials")}\n\n{TELEGRAM_CREDENTIALS}')
        print(f'  On the VPS, start or resume Telegram setup.\n    {command} telegram setup')
        print('  Scan the QR code in Element → Telegram → Rooms → Telegram Bridge.')
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
    page = (f'{heading(TITLE)}\n\n{heading(title)}\n{body}\n\n'
            '  Sign in to Element to finish setup.\n')
    if os.environ.get('TERM') in (None, '', 'dumb'):
        print(page, flush=True)
        try:
            input('I’m signed in. Press Enter to continue. ')
        except (EOFError, KeyboardInterrupt):
            return None
        return 'next'
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        print(f'\x1b[2J\x1b[H{page}\n  → I’m signed in (Enter)', flush=True)
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
