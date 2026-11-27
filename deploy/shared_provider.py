"""Select reusable provider grants before showing the Element sign-in page."""
import secrets
import time

from agent_picker import BACK, choose_option
from hiring import control, ensure_coordinator, key_for
from installer import commit_registry, compose_config, load, open_existing, operator_lock, run_telegram, save


MODES = {'chatgpt_codex': 'ChatGPT Codex', 'chatgpt_plan': 'ChatGPT Plan',
         'openai_api': 'OpenAI API key', 'anthropic_api': 'Anthropic API key'}


def connect_account(data, mode):
    if mode in {'openai_api', 'anthropic_api'}:
        from getpass import getpass
        result = control(data, {'action': 'connect-key', 'mode': mode, 'key': getpass('Provider API key: ')})
        if 'account' not in result:
            raise ValueError('The provider rejected the API key or model catalog.')
    else:
        identifier = secrets.token_hex(12)
        result = control(data, {'action': 'begin', 'id': identifier, 'mode': mode})
        if 'id' not in result:
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
    return result['account']


def account_details(account):
    mode = account['mode']
    return f"Provider · {MODES.get(mode, mode)}", f"Account · {account.get('label') or account['id']}"


def select_model(data, account, preferred):
    rows = control(data, {'action': 'catalog', 'account': account['id']}).get('models', [])
    models = {row['id']: row['efforts'] for row in rows if row['efforts']}
    if not models:
        raise ValueError('The selected connection returned no configurable models.')
    model_choices = {key: key for key in models}
    model = preferred.get('model')
    selected_efforts = {model: preferred.get('effort')}
    details = account_details(account)
    while True:
        model = choose_option('🤖 Choose the Amplifier model', model_choices,
                              model if model in models else None, back=True, details=details)
        if model is BACK:
            return BACK
        efforts = {effort: effort for effort in models[model]}
        effort_details = (*details, f'Model · {model}')
        while True:
            current = selected_efforts.get(model)
            effort = choose_option('🤖 Choose reasoning effort', efforts, current if current in efforts else None,
                                    back=True, details=effort_details)
            if effort is BACK:
                break
            selected_efforts[model] = effort
            confirm = choose_option('🤖 Review provider profile', {'use': 'Use this profile'}, back=True,
                                     details=(*effort_details, f'Reasoning effort · {effort}'))
            if confirm is not BACK:
                return {'account': account['id'], 'mode': account['mode'], 'model': model, 'effort': effort}


def catalog(data, *, preferred=None, back=False):
    preferred = preferred or {}
    accounts = ensure_coordinator(data)['accounts']
    available = {account['id']: account for account in accounts if account['ready']}
    selected = preferred.get('account')
    while True:
        options = {key: row['label'] for key, row in available.items()}
        options['connect'] = 'Connect another account'
        choice = choose_option('🤖 Provider connection', options, selected if selected in available else None, back=back)
        if choice is BACK:
            return BACK
        if choice == 'connect':
            mode = choose_option('🤖 Choose a provider', MODES, back=True)
            if mode is BACK:
                continue
            account = connect_account(data, mode)
            available[account['id']] = account
            choice = account['id']
        selected = choice
        profile = select_model(data, available[choice], preferred)
        if profile is not BACK:
            return profile


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


def choose_agent_profiles(data, agent_ids, default, details):
    overrides = {}
    index = 0
    while index < len(agent_ids):
        agent = agent_ids[index]
        choice = choose_option(f'🤖 Provider for {agent}', {'reuse': 'Use the shared settings shown above',
                                                           'change': 'Choose different settings for this agent'},
                               'change' if agent in overrides else 'reuse', back=True, details=details)
        if choice is BACK:
            if index == 0:
                return BACK
            index -= 1
            continue
        if choice == 'change':
            profile = catalog(data, preferred=overrides.get(agent, default), back=True)
            if profile is BACK:
                continue
            overrides[agent] = profile
        else:
            overrides.pop(agent, None)
        index += 1
    return overrides


def configure_team(data, agent_ids):
    if not agent_ids:
        return
    path = data / 'team-provider.json'
    profiles = load(path) if path.exists() else {}
    default = profiles.get('default')
    if not default:
        default = catalog(data)
        overrides = {}
        if len(agent_ids) > 1:
            while True:
                accounts = {account['id']: account for account in ensure_coordinator(data)['accounts']}
                account = accounts.get(default['account'], {'id': default['account'], 'mode': default['mode']})
                shared = (*account_details(account), f"Model · {default['model']}", f"Reasoning effort · {default['effort']}")
                choice = choose_option('🤖 Shared provider profile',
                                       {'all': 'Use these settings for every selected agent',
                                        'custom': 'Choose different settings for individual agents'},
                                       'all', back=True, details=shared)
                if choice is BACK:
                    default = catalog(data, preferred=default)
                    continue
                if choice == 'custom':
                    selected = choose_agent_profiles(data, agent_ids, default, shared)
                    if selected is BACK:
                        continue
                    overrides = selected
                break
        profiles = {'default': default, 'agents': overrides}
        save(path, profiles)
    for agent in agent_ids:
        bind(data, agent, profiles['agents'].get(agent, default))
