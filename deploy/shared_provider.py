"""Select reusable provider grants before showing the Element sign-in page."""
import secrets
import time

from agent_picker import choose_option
from hiring import control, ensure_coordinator, key_for
from installer import commit_registry, compose_config, load, open_existing, operator_lock, run_telegram, save


MODES = {'chatgpt_codex': 'ChatGPT Codex', 'chatgpt_plan': 'ChatGPT Plan',
         'openai_api': 'OpenAI API key', 'anthropic_api': 'Anthropic API key'}


def catalog(data, *, preferred=None):
    preferred = preferred or {}
    accounts = ensure_coordinator(data)['accounts']
    available = {account['id']: account for account in accounts if account['ready']}
    options = {key: value['label'] for key, value in available.items()}
    options['connect'] = 'Connect another account'
    account = choose_option('🤖 Provider connection', options, preferred.get('account') if preferred.get('account') in available
                            else next(iter(options)))
    if account == 'connect':
        mode = choose_option('🤖 Choose a provider', MODES)
        if mode in {'openai_api', 'anthropic_api'}:
            from getpass import getpass
            result = control(data, {'action': 'connect-key', 'mode': mode, 'key': getpass('Provider API key: ')})
            if 'account' not in result:
                raise ValueError('The provider rejected the API key or model catalog.')
        else:
            identifier = secrets.token_hex(12)
            if 'id' not in control(data, {'action': 'begin', 'id': identifier, 'mode': mode}):
                raise ValueError('The connection could not start. Check the private enrollment route and try again.')
            printed = None
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                result = control(data, {'action': 'login-status', 'id': identifier})
                if result.get('status') == 'ready':
                    break
                if result.get('status') != 'pending':
                    raise ValueError('Provider sign-in expired or failed. Resume setup to reconnect.')
                instructions = (result.get('url'), result.get('code'))
                if instructions != printed and instructions[0]:
                    print('Complete the provider sign-in in your browser, then return here.', flush=True)
                    print(instructions[0], flush=True)
                    if instructions[1]:
                        print('Device code ' + instructions[1], flush=True)
                    printed = instructions
                time.sleep(2)
            if result.get('status') != 'ready':
                raise ValueError('Provider sign-in timed out. Resume setup to reconnect.')
        available[result['account']['id']] = result['account']
        account = result['account']['id']
    rows = control(data, {'action': 'catalog', 'account': account}).get('models', [])
    by_id = {row['id']: row for row in rows if row['efforts']}
    if not by_id:
        raise ValueError('The selected connection returned no configurable models.')
    model = choose_option('🤖 Choose the Amplifier model', {key: key for key in by_id},
                          preferred.get('model') if preferred.get('model') in by_id else next(iter(by_id)))
    efforts = {value: value for value in by_id[model]['efforts']}
    effort = choose_option('🤖 Reasoning effort', efforts, preferred.get('effort') if preferred.get('effort') in efforts
                           else next(iter(efforts)))
    return {'account': account, 'mode': available[account]['mode'], 'model': model, 'effort': effort}


def bind(data, key, profile):
    with operator_lock(data):
        settings, registry = open_existing(data)
        entry = registry['agents'].get(key)
        current = data / 'telegram/managed-provider.json' if key == 'telegram-agent' else None
        if current and current.is_file() and load(current) == profile:
            return
        if entry and entry.get('active'):
            return
        grant = control(data, {'action': 'grant', 'agent': key, 'credential': key_for(data, key), **profile})
        if grant.get('status') != 'granted':
            raise ValueError(f'{key} could not use the selected connection. Reconnect its provider and resume setup.')
        if current:
            network = settings['project'] + '_hiring-models'
            save(current, profile)
            save(data / 'telegram/managed-provider.compose.yaml', {
                'services': {'telegram-engine': {'environment': {
                    'HIRING_PROVIDER_MODE': profile['mode'], 'AGENT_USERNAME': key,
                    'AGENT_REASONING': profile['effort'], 'TELEGRAM_MODEL': profile['model']},
                    'networks': ['shared-models'], 'volumes': [str(data / 'state' / key / 'hiring-agent') + ':/hiring-agent:ro']}},
                'networks': {'shared-models': {'name': network, 'external': True}},
            })
        else:
            entry.update(hiring_provider={field: profile[field] for field in ('mode', 'account')},
                         model=profile['model'], effort=profile['effort'])
            commit_registry(data / 'agents.json', registry, actor=settings['owner'], reason='provider setup')
            compose_config(settings, data, registry)


def prepare_telegram(data):
    if not (data / 'telegram/connector/engine-token').is_file():
        run_telegram(data, 'prepare-agent')
    if not (data / 'telegram/connector/backend-verified').is_file():
        if not (data / 'telegram/managed-provider.json').is_file():
            run_telegram(data, 'sign-in')
        run_telegram(data, 'probe')


def configure_team(data, agent_ids):
    if not agent_ids:
        return
    path = data / 'team-provider.json'
    profiles = load(path) if path.exists() else {}
    default = profiles.get('default')
    if not default:
        default = catalog(data)
        profiles = {'default': default, 'agents': {}}
        if len(agent_ids) > 1 and choose_option('🤖 Apply this profile to all agents?',
                                                  {'all': 'Use it for every agent', 'custom': 'Choose exceptions'}, 'all') == 'custom':
            for agent in agent_ids[1:]:
                if choose_option(f'🤖 Provider for {agent}', {'reuse': 'Reuse the shared profile',
                                                              'change': 'Choose a profile'}, 'reuse') == 'change':
                    profiles['agents'][agent] = catalog(data, preferred=default)
        save(path, profiles)
    for agent in agent_ids:
        bind(data, agent, profiles['agents'].get(agent, default))
