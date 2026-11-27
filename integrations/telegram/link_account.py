"""Link the operator's Telegram account without sending credentials to Matrix."""
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode
import warnings


def bridge_request(compose, owner, key, route, *, payload=None, transaction=None):
    query = {'user_id': owner}
    if transaction:
        query['txn_id'] = transaction
    address = f'http://127.0.0.1:29317/_matrix/provision/v3/login/{route}?{urlencode(query)}'
    # Pass the header and body through stdin so neither is in the process arguments.
    config = [f'url = {json.dumps(address)}', f'header = {json.dumps("Authorization: Bearer " + key)}']
    if payload is not None:
        config.extend(['header = "Content-Type: application/json"', f'data-binary = {json.dumps(json.dumps(payload))}'])
    timeout = '600' if route.endswith('/display_and_wait') else '60'
    result = subprocess.run([*compose, 'curl', '-q', '--config', '-', '--noproxy', '*', '--silent',
                             '--fail', '--connect-timeout', '5', '--max-time', timeout, '--request', 'POST'],
                            input='\n'.join(config) + '\n', text=True, capture_output=True)
    if result.returncode:
        raise ValueError('The bridge could not complete this Telegram login step. Run telegram setup to try again.')
    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise ValueError('The bridge returned an invalid Telegram login step.') from None
    if not isinstance(response, dict):
        raise ValueError('The bridge returned an invalid Telegram login step.')
    return response


def masked_password():
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        try:
            return getpass.getpass('Telegram two-step verification password (hidden): ')
        except getpass.GetPassWarning:
            raise ValueError('Open an interactive VPS terminal for hidden password entry.') from None


def link_account(request, show_qr):
    step = request('start/qr')
    process = step.get('login_id')
    if not isinstance(process, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', process):
        raise ValueError('The bridge returned an invalid Telegram login ID.')
    try:
        while step.get('type') != 'complete':
            kind, step_id = step.get('type'), step.get('step_id')
            if not isinstance(step_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', step_id):
                raise ValueError('The bridge returned an invalid Telegram login step.')
            if kind == 'display_and_wait':
                display = step.get('display_and_wait', {})
                url = display.get('data', '')
                if display.get('type') != 'qr' or not re.fullmatch(r'tg://login\?token=[A-Za-z0-9_=-]+', url):
                    raise ValueError('The bridge did not provide a Telegram login QR code.')
                print('\nOn your phone, open Telegram → Settings → Devices → Link Desktop Device.', flush=True)
                print('Scan this QR code. It refreshes automatically until you scan it.\n', flush=True)
                show_qr(url)
                payload = None
            elif kind == 'user_input':
                fields = step.get('user_input', {}).get('fields', [])
                if len(fields) != 1 or fields[0].get('type') != 'password' or not fields[0].get('id'):
                    raise ValueError('The bridge requested an unsupported Telegram login step.')
                if step_id == 'fi.mau.telegram.login.password.incorrect':
                    print('Telegram rejected that password. Try again.', flush=True)
                payload = {fields[0]['id']: masked_password()}
            else:
                raise ValueError('The bridge returned an unsupported Telegram login step.')
            step = request(f'step/{process}/{step_id}/{kind}', payload=payload, transaction=step.get('txn_id'))
    finally:
        if step.get('type') != 'complete':
            try:
                request(f'cancel/{process}')
            except ValueError:
                pass
    print('Your Telegram account is linked.', flush=True)


def main(key_file, *compose):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('Run telegram setup in an interactive VPS terminal.')
    owner = os.environ['MATRIX_OWNER']
    key = Path(key_file).read_text().strip()
    bridge = (*compose, 'exec', '-T', 'telegram-bridge')

    def request(route, **options):
        return bridge_request(bridge, owner, key, route, **options)

    def show_qr(url):
        subprocess.run([*bridge, 'qrencode', '-t', 'UTF8', '-m', '4'],
                       input=url, text=True, check=True)

    link_account(request, show_qr)


if __name__ == '__main__':
    try:
        main(*sys.argv[1:])
    except (KeyboardInterrupt, EOFError):
        raise SystemExit(130) from None
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None
