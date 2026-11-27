"""Exercise the real relay and provider adapter with deterministic provider replies."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from amplifier_core import ModelInfo, llm_errors
from amplifier_core.message_models import ChatRequest, ChatResponse, Message
from amplifier_module_provider_constellation_hiring import Provider
from hiring_gateway import Connections, handler, save


class FixtureProvider:
    calls = []
    simultaneous = 0
    max_simultaneous = 0
    offline = False

    def __init__(self, config, coordinator=None):
        self.config, self.coordinator = config, coordinator

    async def list_models(self, **_):
        return [ModelInfo(id='fixture-model', display_name='Fixture', context_window=8192, max_output_tokens=400,
                          capabilities=['tools', 'streaming'], defaults={'reasoning_efforts': ['low', 'high']})]

    async def complete(self, request):
        if self.offline:
            raise llm_errors.AuthenticationError('synthetic expired login')
        type(self).simultaneous += 1
        type(self).max_simultaneous = max(self.max_simultaneous, self.simultaneous)
        try:
            self.calls.append((self.config['auth_mode'], request.model, request.reasoning_effort))
            await self.coordinator.hooks.emit('llm:stream_block_delta', {'index': 0, 'delta': {'text': 'Evidence'}})
            await asyncio.sleep(0.04)
            return ChatResponse.model_validate({'content':[{'type': 'text', 'text': 'Evidence'},
                {'type': 'tool_call', 'id': 'first', 'name': 'search', 'input': {'query': 'fixture'}}],
                'tool_calls': [{'id': 'first', 'name': 'search', 'arguments': {'query': 'fixture'}}]})
        finally:
            type(self).simultaneous -= 1

    async def close(self):
        pass


class Tests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.gateway = Connections(self.path, 'https://fixture.test/hiring', provider=FixtureProvider)
        self.account = 'a' * 24
        base = self.gateway.profile(self.account)
        base.mkdir()
        save(base / 'account.json', {'id': self.account, 'mode': 'chatgpt_codex', 'ready': True,
                                    'identity': 'fixture-digest', 'label': 'Fixture account'})
        self.credential = 'b' * 64
        self.grant = {'agent': 'fixture', 'account': self.account, 'mode': 'chatgpt_codex',
                      'model': 'fixture-model', 'effort': 'high', 'credential': self.credential}
        self.request = ChatRequest(messages=[Message(role='user', content=[{'type': 'text', 'text': 'Find evidence'}])],
                                   model='unapproved-model', reasoning_effort='low')
        FixtureProvider.calls.clear()
        FixtureProvider.max_simultaneous = 0

    def test_real_adapter_streams_tool_call_and_enforces_the_approved_grant(self):
        self.assertEqual(self.gateway.grant(self.grant), {'status': 'granted'})
        token = self.path / 'agent-token'
        token.write_text(self.credential)
        events = []

        async def hook(event, payload):
            events.append((event, payload))

        async def exercise():
            with patch.dict(os.environ, {'AGENT_USERNAME': 'fixture'}):
                module = Provider({'mode': 'chatgpt_codex', 'default_model': 'fixture-model',
                                   'reasoning_effort': 'high'}, SimpleNamespace(hooks=SimpleNamespace(emit=hook)))
            module.url = f'http://127.0.0.1:{server.server_port}/agents/fixture'
            module.token = token
            try:
                self.assertEqual(module.get_info().id, module.name)
                self.assertEqual([model.id for model in await module.list_models()], ['fixture-model'])
                reply = await module.complete(self.request)
                self.assertEqual(module.parse_tool_calls(reply)[0].name, 'search')
                self.assertEqual(FixtureProvider.calls, [('chatgpt_codex', 'fixture-model', 'high')])
                self.assertEqual(events[0][0], 'llm:stream_block_delta')
                token.write_text('c' * 64)
                with self.assertRaises(llm_errors.AuthenticationError):
                    await module.complete(self.request)
            finally:
                await module.close()

        with ThreadingHTTPServer(('127.0.0.1', 0), handler(self.gateway, 'model')) as server:
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                asyncio.run(exercise())
            finally:
                server.shutdown()
                worker.join()

    def test_native_amplifier_loads_the_selected_provider_without_an_oauth_file(self):
        from uuid import uuid4
        from amplifier_acp.worker import NativeWorker
        import initialize

        self.gateway.grant(self.grant)
        Path('/hiring-agent/token').write_text(self.credential)
        Path('/instance/config.json').write_text('{"config":{}}')

        async def observe(_event):
            pass

        async def startup():
            settings = json.loads((Path('/instance/backend') / 'agent.json').read_text())
            worker = NativeWorker(str(uuid4()), '/workspace', settings, observe, None)
            try:
                report = await asyncio.wait_for(worker.start(), 25)
                choices = report['provider_choices']
                self.assertEqual([choice['model'] for choice in choices], ['fixture-model'])
            finally:
                await worker.close()

        environment = {'AGENT_USERNAME': 'fixture-worker', 'HIRING_GRANT_ID': 'fixture', 'AGENT_MODEL': 'fixture-model',
                       'AGENT_REASONING': 'high', 'HIRING_PROVIDER_MODE': 'chatgpt_codex',
                       'OPENAI_API_KEY': 'fixture-secret-must-not-reach-backend'}
        with patch.dict(os.environ, environment):
            initialize.configure()
        # Agent Instance starts its backend with the explicit environment from
        # config.json rather than passing the engine's Compose environment through.
        forwarded = json.loads(Path('/instance/config.json').read_text())['config']['backend']['env']
        self.assertEqual(forwarded, {'HOME': '${AGENT_HOME}', 'AGENT_USERNAME': 'fixture-worker', 'HIRING_GRANT_ID': 'fixture'})
        worker_environment = {key: value for key, value in os.environ.items() if key not in environment}
        worker_environment.update({key: value for key, value in forwarded.items() if key != 'HOME'})
        with patch.dict(os.environ, worker_environment, clear=True):
            with ThreadingHTTPServer(('127.0.0.1', 8082), handler(self.gateway, 'model')) as server:
                thread = Thread(target=server.serve_forever)
                thread.start()
                try:
                    asyncio.run(startup())
                finally:
                    server.shutdown()
                    thread.join()
        self.assertFalse(any(Path('/instance').rglob('oauth.json')))

    def test_shared_profile_serializes_refresh_and_revoked_access_is_reported(self):
        self.gateway.grant(self.grant)
        self.gateway.grant({**self.grant, 'agent': 'other', 'credential': 'c' * 64})
        work = {'request': self.request.model_dump(mode='json')}
        with ThreadPoolExecutor() as workers:
            results = [workers.submit(self.gateway.generation, agent, key, work, lambda _: None)
                       for agent, key in [('fixture', self.credential), ('other', 'c' * 64)]]
            for item in results:
                item.result()
        self.assertEqual(FixtureProvider.max_simultaneous, 1)
        with patch.object(FixtureProvider, 'offline', True):
            with self.assertRaises(llm_errors.AuthenticationError):
                self.gateway.generation('fixture', self.credential, work, lambda _: None)
        self.assertFalse(self.gateway.public_accounts()[0]['ready'])
        self.assertEqual(self.gateway.admin({'action': 'revoke', 'agent': 'fixture'}, None), {'status': 'revoked'})
        with self.assertRaises(FileNotFoundError):
            self.gateway.generation('fixture', self.credential, work, lambda _: None)

    def test_plan_desktop_ticket_is_one_time_and_keeps_tokens_out_of_public_accounts(self):
        import io
        from hiring_gateway import plan_auth
        key = 'd' * 24
        self.gateway.begin(key, 'chatgpt_plan')
        url = self.gateway.status(key)['url']
        ticket = url.rsplit('/', 1)[1]
        bundle = self.gateway.desktop_bundle(key, ticket)
        with zipfile.ZipFile(io.BytesIO(bundle)) as files:
            self.assertIn('Constellation ChatGPT Plan/Connect.command', files.namelist())
            pair = json.loads(files.read('Constellation ChatGPT Plan/pair.json'))
            self.assertEqual(pair['ticket'], ticket)
        private = {'access_token': 'fixture-secret', 'auth_mode': 'chatgpt_plan'}

        async def import_fixture(source, *, token_file_path, host_file_path):
            save(Path(token_file_path), json.loads(Path(source).read_text()))
            save(Path(host_file_path), {})

        with patch.object(plan_auth, 'import_credentials', side_effect=import_fixture), \
                patch.object(plan_auth, 'auth_status', return_value={'subject': 'fixture-account',
                    'email': 'fixture@example.test', 'plan_enabled': True}):
            self.assertEqual(self.gateway.import_plan(key, ticket, private), {'status': 'ready'})
            with self.assertRaises(PermissionError):
                self.gateway.import_plan(key, ticket, private)
        connections = self.gateway.public_accounts()
        plan = next(item for item in connections if item['mode'] == 'chatgpt_plan')
        self.gateway.grant({**self.grant, 'agent': 'plan-worker', 'mode': 'chatgpt_plan', 'account': plan['id']})
        self.gateway.generation('plan-worker', self.credential, {'request': self.request.model_dump(mode='json')}, lambda _: None)
        self.assertEqual(FixtureProvider.calls[-1], ('chatgpt_plan', 'fixture-model', 'high'))
        self.assertEqual({account['mode'] for account in connections}, {'chatgpt_plan', 'chatgpt_codex'})
        self.assertNotIn('fixture-secret', json.dumps(connections))


if __name__ == '__main__':
    unittest.main()
