"""Copyable setup instructions shared by the wizard and printable guide."""
import os
from shlex import quote
import sys


TITLE = '✨ Finalize your Constellation setup'
WELCOME_TITLE = '👋 Hello Collaboration'
WELCOME_INSTRUCTIONS = ('  Select Finish after the selected agents have joined Matrix and completed setup.\n'
                        '  With an Office agent ready, the wizard opens a welcome task and prints its thread link.')


def interactive_terminal():
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get('TERM') not in (None, '', 'dumb')


def heading(title):
    if sys.stdout.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ:
        return f'\x1b[1;36m{title}\x1b[0m'
    return title


def pages(settings, data, command):
    server = settings['server_name']
    tailnet = '\n     Connect this computer to Tailscale before opening the workspace links.' if settings['tailscale'] else ''
    yield 'element', '💬 Element Desktop', f"""  1. Download and install Element Desktop.
     https://element.io/download{tailnet}
  2. Choose Sign in. Under Account provider, find the current server (usually matrix.org).
     Click Edit to the right of the server name, above Continue.
  3. Paste your Matrix homeserver into Other account provider and select Continue.
     https://{server}
  4. Enter your Matrix credentials, select Sign in, and open Agents → Office.
     Username: {settings['owner']}
     Password: Your Matrix operator password from setup"""

    yield 'dashboard', '📊 Dashboard', f"""  • Open the resource and agent dashboard in your browser.
    {settings['dashboard_url']}
  • Find this link and Context Intelligence in Agents → VPS Alerts → Pinned messages."""

    yield 'context', '🧠 Context Intelligence · Neo4j Browser', f"""  1. Open Neo4j Browser.
     {settings['context_url']}
  2. Enter neo4j as the username.
     Password: Use your dedicated Context Intelligence password from setup.
     To retrieve the stored password, run this command privately on the VPS.
     sudo cat -- {quote(str(data / 'context/neo4j-password'))}
  3. Select Connect."""

    if settings['dot']:
        body = '  • Enable systemd on the deployment to connect the Dot tunnel.'
        if settings['systemd']:
            body = f"""  1. Browser · Open Platform Tunnels and create a tunnel in your organization.
     Associate your ChatGPT workspace and copy the tunnel ID.
     Creating a tunnel requires Tunnels Read + Manage.
     https://platform.openai.com/settings/organization/tunnels
  2. Browser · Create a Restricted runtime API key with Tunnels Read + Use.
     https://platform.openai.com/settings/organization/api-keys
  3. VPS terminal · Replace the placeholder with your tunnel ID.
     {command} dot-connect tunnel_REPLACE_WITH_YOUR_ID
     If prompted, enter the runtime key privately at the hidden prompt.
  4. Browser · Add a private custom MCP server in ChatGPT Plugins.
     Suggested plugin name: Constellation Office
     Choose Tunnel, select the same tunnel ID, and choose No authentication.
     https://chatgpt.com/plugins
  5. Verify Dot can open a small Office task and read its reply."""
        yield 'dot', '🔌 Dot · Secure MCP Tunnel', body + '\n  • Full instructions and connection test in docs/dot.md.'

    if settings['telegram']:
        yield 'telegram', '✈️ Telegram · Bridge and drafting agent', f"""  1. Browser · Sign in with your phone number and the code Telegram sends in the app.
     Create or open an application and copy App api_id and App api_hash.
     https://my.telegram.org/apps
  2. VPS terminal · Reuse a valid private file or create one outside the checkout.
     (umask 077; touch "$HOME/telegram-api.json")
     chmod 600 "$HOME/telegram-api.json"
     nano "$HOME/telegram-api.json"
     In a private editor, replace both example values with your credentials.
     {{"api_id": 1234567, "api_hash": "REPLACE_WITH_YOUR_API_HASH"}}
  3. VPS terminal · Prepare and activate the bridge.
     If you reused a file elsewhere, use that path in the prepare command.
     {command} telegram prepare "$HOME/telegram-api.json"
     {command} telegram activate
  4. Element · Open a private DM with @telegrambot:{server} and send:
     !tg login qr
     Telegram mobile · Open Settings → Devices → Link Desktop Device and scan the code.
  5. Verify and allow one test chat using docs/telegram.md.
  6. VPS terminal · For drafts and updates, run these commands in order.
     {command} telegram prepare-agent
     {command} telegram sign-in
     {command} telegram probe
     {command} telegram start-agent
     At sign-in, open the printed verification page and complete Codex device login.
  7. Bind the verified chat in draft mode and approve one test delivery.
     Follow docs/telegram.md for the binding and delivery check."""


def show_printable(settings, data, command):
    print(f'\n{heading(TITLE)}')
    print('  Sign in to your workspace, then connect the integrations you selected.')
    for _, title, body in pages(settings, data, command):
        print(f'\n{heading(title)}\n{body}')
    print(f'\n{heading(WELCOME_TITLE)}\n{WELCOME_INSTRUCTIONS}')
    print(f'\n  • Resume the guided setup from an interactive terminal.\n    {command} setup-guide\n')


def confirm_page(title, body, action='Continue'):
    print(f'\x1b[2J\x1b[H{heading(TITLE)}\n\n{heading(title)}\n{body}\n')
    try:
        while True:
            response = input(f'  [{action}] Enter · Q then Enter to pause: ').strip().lower()
            if response in ('', 'q'):
                return response == ''
    except (EOFError, KeyboardInterrupt):
        return False
