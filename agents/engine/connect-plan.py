# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#   "amplifier-module-provider-openai-chatgpt @ git+https://github.com/microsoft/amplifier-module-provider-openai-chatgpt.git@cae3eaa32e16b139b8e889b8cc477c3f2d9dee31",
# ]
# ///
"""Authorize ChatGPT Plan on the computer running the browser."""
import asyncio
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from amplifier_module_provider_openai_chatgpt import plan_auth


class Direct(HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise ValueError('The enrollment address redirected')


def connect():
    pairing = Path(__file__).resolve().with_name('pair.json')
    data = json.loads(pairing.read_text())
    url = urlsplit(data['url'])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment
            or not re.fullmatch(r'/hiring/import/[a-f0-9]{24}', url.path)
            or not re.fullmatch(r'[a-f0-9]{64}', data['ticket'])):
        raise ValueError('Use a valid private enrollment link from Hiring Manager.')
    with TemporaryDirectory() as work:
        token_file = Path(work) / 'oauth.json'
        print('Authorize your ChatGPT plan in the browser. Complete setup in Element afterward.')
        asyncio.run(plan_auth.login(token_file_path=str(token_file), app_name='Constellation Matrix',
                                    request_plan_permission=True, open_browser=True))
        request = Request(data['url'], data=token_file.read_bytes(), method='POST',
                          headers={'Authorization': 'Bearer ' + data['ticket'], 'Content-Type': 'application/json'})
        with build_opener(Direct()).open(request, timeout=35) as response:
            if json.load(response).get('status') != 'ready':
                raise ValueError('The VPS could not verify this connection')
    pairing.unlink()
    print('ChatGPT Plan is connected. Return to Hiring Manager in Element.')


if __name__ == '__main__':
    try:
        connect()
    except Exception:
        print('Sign-in could not be completed. Return to Hiring Manager to start a fresh connection.')
        raise SystemExit(1) from None
