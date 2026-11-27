"""Native Amplifier module relaying approved requests to the private coordinator."""
import json
import os
from pathlib import Path
import ssl

import httpx
from amplifier_core import llm_errors
from amplifier_core.interfaces import ModelInfo
from amplifier_core.message_models import ChatResponse
from amplifier_module_provider_openai_chatgpt import ChatGPTProvider

NAME = 'constellation-hiring'
PROVIDER_ERRORS = {
    'authorization_required': llm_errors.AuthenticationError,
    'rate_limited': llm_errors.RateLimitError,
    'invalid_request': llm_errors.InvalidRequestError,
    'forbidden': llm_errors.AuthenticationError,
}


class Provider:
    name = NAME

    def __init__(self, config, coordinator):
        self.config, self.coordinator = config, coordinator
        self.default_model = config['default_model']
        self.reasoning_effort = config.get('reasoning_effort')
        self.agent = os.environ.get('HIRING_GRANT_ID') or os.environ['AGENT_USERNAME']
        endpoint = os.environ.get('AGENT_MOBILITY_ENDPOINT')
        if endpoint:
            if not endpoint.startswith('https://'):
                raise ValueError('A worker requires a private HTTPS coordinator')
            self.url = endpoint.rstrip('/') + '/inference'
            self.identity = {'agent': self.agent, 'generation': os.environ['AGENT_MOBILITY_GENERATION']}
            self.token = Path('/mobility/rpc-token')
            verify = ssl.create_default_context(cafile='/mobility/ca.pem')
        else:
            self.url = 'http://hiring-provider:8082/agents/' + self.agent
            self.token = Path('/hiring-agent/token')
            self.identity, verify = {}, True
        self.client = httpx.AsyncClient(verify=verify, trust_env=False, timeout=httpx.Timeout(360, connect=8))

    def get_info(self):
        mode = self.config['mode']
        if mode == 'openai_api':
            from amplifier_module_provider_openai import OpenAIProvider
            upstream = OpenAIProvider(config={'default_model': self.default_model})
        elif mode == 'anthropic_api':
            from amplifier_module_provider_anthropic import AnthropicProvider
            upstream = AnthropicProvider(config={'default_model': self.default_model})
        else:
            upstream = ChatGPTProvider({'auth_mode': mode, 'default_model': self.default_model})
        info = upstream.get_info()
        return info.model_copy(update={'id': NAME, 'display_name': 'Managed ' + info.display_name,
                                       'credential_env_vars': [], 'defaults': {'model': self.default_model}})

    async def exchange(self, payload):
        try:
            token = self.token.read_text().strip()
            async with self.client.stream('POST', self.url, json={**self.identity, **payload},
                                          headers={'Authorization': 'Bearer ' + token}) as response:
                if response.status_code in (401, 403):
                    raise llm_errors.AuthenticationError('The approved provider grant is unavailable', provider=NAME)
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    item = json.loads(line)
                    if 'error' in item:
                        error = PROVIDER_ERRORS.get(item['error'], llm_errors.ProviderUnavailableError)
                        raise error('Managed ChatGPT request failed (' + str(item['error']) + ')', provider=NAME)
                    if 'event' in item:
                        await self.coordinator.hooks.emit(item['event'], item['data'])
                    if 'result' in item:
                        return item['result']
        except (httpx.HTTPError, OSError, ValueError) as error:
            raise llm_errors.ProviderUnavailableError('The managed ChatGPT coordinator is unavailable', provider=NAME) from error
        raise llm_errors.ProviderUnavailableError('The coordinator did not finish the request', provider=NAME)

    async def list_models(self, **_):
        return [ModelInfo.model_validate(model) for model in await self.exchange({'catalog': True})]

    async def complete(self, request, **_):
        return ChatResponse.model_validate(await self.exchange({'request': request.model_dump(mode='json')}))

    def parse_tool_calls(self, response):
        return response.tool_calls

    async def close(self):
        await self.client.aclose()


async def mount(coordinator, config=None):
    provider = Provider(config or {}, coordinator)
    await coordinator.mount('providers', provider, name=NAME)
    return provider.close
