"""Compose topology generated from the operator's configuration and registry."""
import json
import os
from pathlib import Path
from shlex import quote
from urllib.parse import urlsplit

SOURCE = Path(__file__).resolve().parent.parent
CONNECTOR = 'constellation-office-connector:0.22.17'
ENGINE = 'constellation-office-engine:0.22.17'
BASE = 'constellation-amplifier-base:0.22.17'
SYNAPSE = 'ghcr.io/element-hq/synapse:v1.165.0'


def volume(source, target, read_only=False):
    return {'type': 'bind', 'source': str(source), 'target': target, 'read_only': read_only}


def port(local, target):
    return f'127.0.0.1:{local}:{target}'


def telegram_office_agent(registry):
    agents = registry['agents']
    for key in ('task-manager', 'manager'):
        if agents.get(key, {}).get('placement') == 'local':
            return key
    return None


def definition(settings, data, registry, secrets, *, workshop_internal=True):
    engine_image = ENGINE
    command = ('./constellation_matrix' if settings.get('control_runtime') else 'sudo ./constellation_matrix') + ' --data-dir ' + quote(str(data))
    slice_name = {'cgroup_parent': 'matrix-workload.slice'} if settings['systemd'] else {}
    common = {'restart': 'unless-stopped', **slice_name,
              'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}}
    restricted = {**common, 'init': True, 'user': '991:991', 'read_only': True, 'tmpfs': ['/tmp'],
                  'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true']}
    network = {'clients': {}, 'database': {'internal': True}, 'memory': {'internal': True}, 'edge': {}}
    server = settings['server_name']
    matrix = {'MATRIX_URL': 'http://synapse:8008', 'MATRIX_SERVER_NAME': server,
              'MATRIX_OWNER': f"@{settings['owner']}:{server}"}
    context = {'CONTEXT_INTELLIGENCE_URL': 'http://context:8000'}
    catalog = {'MATRIX_CATALOG_PATH': '/catalog/agents.json'}
    state = data / 'state'
    dashboard = data / 'dashboard'
    services = {
        'postgres': {**common, 'image': 'postgres:17.12-bookworm', 'networks': ['database'],
                     'environment': {'POSTGRES_USER': 'synapse', 'POSTGRES_DB': 'synapse',
                                     'POSTGRES_PASSWORD': secrets['POSTGRES_PASSWORD'],
                                     'POSTGRES_INITDB_ARGS': '--encoding=UTF8 --locale=C'},
                     'volumes': [volume(data / 'postgres', '/var/lib/postgresql/data')],
                     'healthcheck': health('pg_isready', '-U', 'synapse', '-d', 'synapse')},
        'synapse': {**common, 'image': SYNAPSE, 'networks': ['database', 'clients'],
                    'environment': {'SYNAPSE_SERVER_NAME': server, 'SYNAPSE_REPORT_STATS': 'no', 'PYTHONPATH': '/data'},
                    'ports': [port(settings['matrix_port'], 8008)],
                    'depends_on': {'postgres': {'condition': 'service_healthy'}},
                    'volumes': [volume(data / 'synapse', '/data')],
                    'healthcheck': health('curl', '-fsS', 'http://127.0.0.1:8008/_matrix/client/versions')},
        'monitor': {**restricted, 'build': str(SOURCE / 'deploy/matrix/bot'), 'networks': ['clients'],
                    'environment': {**matrix, 'STATE_DIR': '/state', 'DATA_DIR': '/monitor',
                                    'WORKSPACE_LINKS': '/dashboard/links.json',
                                    'GUIDE_COMMAND': command,
                                    'GRAPH_PASSWORD_COMMAND': command + ' context password',
                                    'DOT_ENABLED': str(settings['dot']).lower(),
                                    'SYSTEMD_ENABLED': str(settings['systemd']).lower(),
                                    'DOT_TUNNEL_SUPPORTED': str(bool(settings['systemd'] or settings.get('control_runtime'))).lower()},
                    'volumes': [volume(data / 'bot', '/state'), volume(data / 'monitor/outbox', '/monitor/outbox', True),
                                volume(data / 'monitor/acks', '/monitor/acks'), volume(dashboard, '/dashboard', True)]},
        'context': {**restricted, 'image': 'constellation-context:6.13.0-1622b48-topology1',
                    'build': {'context': str(SOURCE), 'dockerfile': 'deploy/context.Dockerfile'},
                    'networks': ['memory'], 'depends_on': {'neo4j': {'condition': 'service_healthy'}},
                    'environment': {'AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_CONFIG_FILE': '/etc/context/server.json'},
                    'volumes': [volume(data / 'context/data', '/data'), volume(data / 'context/config', '/etc/context', True)],
                    'healthcheck': health('python', '-c', 'import json,urllib.request; s=json.load(urllib.request.urlopen("http://127.0.0.1:8000/status",timeout=5)); assert s["neo4j_connected"] and s["neo4j_query_connected"]')},
        'neo4j': {**common, 'image': 'neo4j:5.26.22-community', 'networks': ['memory'],
                  'environment': {'NEO4J_AUTH_FILE': '/run/secrets/neo4j-auth',
                                  'NEO4J_server_memory_heap_initial__size': '256m',
                                  'NEO4J_server_memory_heap_max__size': '512m',
                                  'NEO4J_server_memory_pagecache_size': '256m',
                                  'NEO4J_server_bolt_advertised__address': f"{server}:{settings['bolt_https_port']}"},
                  'volumes': [volume(data / 'context/graph', '/data'),
                              volume(data / 'context/neo4j-auth', '/run/secrets/neo4j-auth', True)],
                  'healthcheck': {'test': ['CMD-SHELL', 'auth=$$(cat /run/secrets/neo4j-auth) && '
                                           'NEO4J_PASSWORD="$${auth#neo4j/}" cypher-shell -u neo4j "RETURN 1" >/dev/null'],
                                  'interval': '8s', 'timeout': '7s', 'retries': 20}},
    }
    if settings.get('control_runtime') and settings['dot'] and (data / 'dot/tunnel-id').is_file():
        from operator_runtime import image
        services['dot-tunnel'] = {
            **common, 'image': image(), 'init': True, 'network_mode': 'host',
            'read_only': True, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'entrypoint': ['/dot/tunnel-client'],
            'command': ['run', '--control-plane.tunnel-id', (data / 'dot/tunnel-id').read_text().strip(),
                        '--control-plane.api-key', 'file:/dot/runtime-key', '--mcp.server-url',
                        'url=http://localhost/mcp,unix-socket=/bridge/bridge.sock', '--mcp.extra-headers',
                        'X-Dot-Bridge-Token: file:/bridge/bridge-token', '--mcp.oauth-discovery', 'disabled',
                        '--health.listen-addr', '127.0.0.1:18786'],
            'volumes': [volume(data / 'dot', '/dot', True), volume(data / 'state/dot/matrix', '/bridge', True)],
            'healthcheck': health('curl', '-fsS', 'http://127.0.0.1:18786/readyz'),
        }
    if settings.get('control_runtime'):
        from operator_runtime import image
        services['control-runtime'] = {
            **common, 'image': image(), 'init': True, 'network_mode': 'host',
            'environment': {'CONSTELLATION_RUNTIME': 'compose', 'PYTHONUNBUFFERED': '1',
                            'PYTHONPATH': str(SOURCE / 'deploy'),
                            **({'CONSTELLATION_TAILSCALE_BIN': os.environ['CONSTELLATION_TAILSCALE_BIN']}
                               if settings['tailscale'] and os.environ.get('CONSTELLATION_TAILSCALE_BIN') else {})},
            'env_file': [str(data / '.env')],
            'volumes': [volume(SOURCE, str(SOURCE), True), volume(data, str(data)),
                        volume('/var/run/docker.sock', '/var/run/docker.sock'),
                        *([volume(os.environ['CONSTELLATION_TAILSCALE_BIN'], '/usr/bin/tailscale', True),
                            volume('/var/run/tailscale/tailscaled.sock', '/var/run/tailscale/tailscaled.sock')]
                           if settings['tailscale'] and os.environ.get('CONSTELLATION_TAILSCALE_BIN') else [])],
            'command': ['python3', '-B', str(SOURCE / 'deploy/control_runtime.py'), str(data)],
            'healthcheck': health('python3', '-c', 'import socket,sys; s=socket.socket(socket.AF_UNIX); '
                                  's.settimeout(2); s.connect(sys.argv[1])', str(data / 'runtime/roster.sock')),
        }
    services['private-edge'] = {
        **restricted,
        'image': 'haproxy:3.2-alpine@sha256:7816a9801a6846b912bc37e48529808e2fa87c3c47992441940f68e393ebcfeb',
        'networks': ['memory', 'edge'],
        'ports': [port(settings['neo4j_port'], 7474), port(settings['bolt_port'], 7687),
                  port(settings['context_port'], 8000)],
        'volumes': [volume(SOURCE / 'deploy/private-edge.cfg', '/usr/local/etc/haproxy/haproxy.cfg', True)],
        'depends_on': {'context': {'condition': 'service_healthy'}, 'neo4j': {'condition': 'service_healthy'}},
    }
    if settings['dot']:
        services['dot'] = {**restricted, 'image': CONNECTOR, 'networks': ['clients'],
                           'command': ['node', '/app/dot-bridge.mjs'],
                           'environment': {**matrix, **catalog, 'DOT_STATE': '/state'},
                           'volumes': [volume(state / 'dot/matrix', '/state'), volume(dashboard, '/catalog', True)]}
        if settings['telegram']:
            services['dot']['volumes'].append(volume(data / 'telegram/dot-requests', '/telegram-dot', True))
    if not settings.get('twin') and (settings.get('control_runtime') or 'hiring-manager' in registry['agents'] or
                                    any(entry.get('hiring_provider') for entry in registry['agents'].values())):
        network['hiring-models'] = {'internal': True}
        network.setdefault('provider', {})
        dashboard_url = urlsplit(settings.get('dashboard_url') or
                                f"https://{server}:{settings['dashboard_https_port']}")
        public_url = settings.get('hiring_url') or (f'{dashboard_url.scheme}://{dashboard_url.netloc}/hiring' if settings['tailscale'] else '')
        services['hiring-provider'] = {
            **restricted, 'image': engine_image, 'profiles': ['agents'], 'networks': ['hiring-models', 'provider'],
            'entrypoint': ['/opt/amplifier-acp/.venv/bin/python', '-B', '/opt/office/hiring_gateway.py'],
            'environment': {'HIRING_PUBLIC_URL': public_url, **({'OPERATION_TIMING_OUTBOX': '/timing',
                            'PYTHONPATH': '/runtime-support'} if settings.get('control_runtime') else {})},
            'ports': [port(settings.get('hiring_port', 18792), 8081)],
            'volumes': [volume(data / 'hiring', '/hiring'), *([volume(data / 'runtime/timing/provider', '/timing'),
                        volume(SOURCE / 'deploy/operation_timing.py', '/runtime-support/operation_timing.py', True)]
                        if settings.get('control_runtime') else [])],
            'healthcheck': health('curl', '-fsS', 'http://127.0.0.1:8082/health'),
        }
    telegram_coordinator = telegram_office_agent(registry) if settings['telegram'] else None
    for key, entry in sorted(registry['agents'].items()):
        if entry.get('placement') != 'local' or entry.get('mobility_fenced'):
            continue
        instance = f'engine-{key}'
        private = f'acp-{key}'
        network[private] = {'internal': True}
        network['provider'] = {}
        directory = state / key
        workspace = [volume(directory / 'work', '/workspace')]
        office_worker = key not in {'the-architect', 'chief-of-staff', 'helper'}
        if office_worker:
            workspace.append(volume(data / 'desktop', '/workspace/desktop'))
        metadata = {'AGENT_USERNAME': key, 'AGENT_NAME': entry['name'], 'AGENT_MODEL': entry['model'],
                    'AGENT_REASONING': entry['effort']}
        services[instance] = {
            **restricted, 'image': engine_image, 'networks': [private, 'provider'], 'profiles': ['agents'],
            'environment': {**metadata, 'AGENT_WORKSPACE_TOOLS': str(office_worker).lower(),
                            'AGENT_INSTANCE_CERTIFICATE_NAMES': f'["{instance}"]'},
            'volumes': [*workspace, volume(directory / 'instance', '/instance'),
                        volume(directory / 'exchange', '/exchange')],
            'healthcheck': health('curl', '-fsS', '--cacert', '/exchange/ca.pem', f'https://{instance}:19443/'),
        }
        if entry.get('hiring_provider'):
            if entry.get('twin_grant'):
                services[instance]['environment']['HIRING_GRANT_ID'] = entry['twin_grant']
            services[instance]['environment']['HIRING_PROVIDER_MODE'] = entry['hiring_provider']['mode']
            services[instance]['networks'].append('hiring-models')
            grant = volume(directory / 'hiring-agent', '/hiring-agent', True)
            grant['bind'] = {'create_host_path': False}
            services[instance]['volumes'].append(grant)
        if key == 'the-architect' and not settings.get('twin'):
            services[instance]['volumes'].append(volume(data / 'architecture', '/workspace/deployment-config', True))
        if key == 'the-architect' and settings.get('maintenance'):
            services[instance]['environment']['MATRIX_REPOSITORY_DRAFTS'] = 'true'
            drafts = volume(data / 'maintenance/drafts', '/workspace/constellation-matrix')
            drafts['bind'] = {'create_host_path': False}
            services[instance]['volumes'].append(drafts)
        worker = {
            **restricted, 'image': CONNECTOR, 'profiles': ['agents'], 'networks': ['clients', 'memory', private],
            'environment': {**matrix, **metadata, **context, **catalog, 'AGENT_STATE': '/state',
                            'AGENT_WORKSPACE': '/workspace', 'AGENT_INSTRUCTIONS': entry['profile']['limits'],
                            'MATRIX_PLANNER': f'@dot:{server}' if settings['dot'] else '',
                            'INSTANCE_ACP_URL': f'wss://{instance}:19443/api/v1/acp'},
            'volumes': [volume(directory / 'matrix', '/state'), volume(directory / 'exchange', '/acp', True),
                        volume(dashboard, '/catalog', True),
                        volume(data / 'context' / f'{key}.token', '/context-token', True)],
            'depends_on': {instance: {'condition': 'service_healthy'}},
        }
        if key == telegram_coordinator:
            worker['environment']['TELEGRAM_OFFICE_SOCKET'] = '/telegram-office/actions.sock'
            worker['environment']['TELEGRAM_HANDOFF_SOCKET'] = '/telegram-handoffs/requests.sock'
            mount = volume(data / 'telegram/office', '/telegram-office', True)
            mount['bind'] = {'create_host_path': False}
            worker['volumes'].append(mount)
            handoffs = volume(data / 'telegram/handoffs', '/telegram-handoffs', True)
            handoffs['bind'] = {'create_host_path': False}
            worker['volumes'].append(handoffs)
        if key == 'chief-of-staff':
            worker.update(command=['node', '/app/chief-of-staff/direct/bridge.mjs'],
                          healthcheck=health('test', '-s', '/tmp/direct-ready'))
            worker['volumes'] += [
                volume(SOURCE / 'integrations/chief-of-staff', '/app/chief-of-staff', True),
                volume(directory / 'matrix', '/publisher'), volume(directory / 'observer', '/direct'),
                volume(data / 'context' / f'{key}.token', '/writer-token', True),
                volume(data / 'context/observation-reader.token', '/reader-token', True),
            ]
            if settings.get('roster_management') and 'hiring-manager' not in registry['agents']:
                socket = (volume(data / 'runtime', '/run/constellation', True) if settings.get('control_runtime') else
                          volume(f"/run/{settings['project']}-roster.sock", '/run/office-roster.sock', True))
                if settings.get('control_runtime'):
                    worker['environment']['ROSTER_SOCKET'] = '/run/constellation/roster.sock'
                socket['bind'] = {'create_host_path': False}
                worker['volumes'] += [socket, volume(data / 'roster/bridge-key', '/roster-key', True)]
                worker['environment']['ROSTER_MANAGEMENT'] = 'true'
                worker['environment']['ROSTER_CONFIGURE_COMMAND'] = (
                    command + ' agent configure')
        elif key == 'the-architect' and not settings.get('twin'):
            worker.update(user='992:992', command=['node', '/app/root/agent.mjs'],
                          healthcheck=health('test', '-s', '/state/synced'))
            worker['environment']['ROOT_HOST'] = server
            if settings.get('maintenance'):
                worker['environment']['VPS_MAINTENANCE'] = 'true'
            worker['volumes'] = [item for item in worker['volumes'] if item['target'] != '/context-token']
            socket = (volume(data / 'runtime', '/run/constellation', True) if settings.get('control_runtime') else
                      volume(f"/run/{settings['project']}-root.sock", '/run/executor.sock', True))
            if settings.get('control_runtime'):
                worker['environment']['ARCHITECT_EXECUTOR_SOCKET'] = '/run/constellation/executor.sock'
            socket['bind'] = {'create_host_path': False}
            worker['volumes'] += [
                volume(SOURCE / 'integrations/root', '/app/root', True),
                volume(directory / 'config.json', '/config.json', True),
                volume(directory / 'operations.json', '/operations.json', True),
                volume(directory / 'gateway-credentials', '/credentials', True),
                volume(data / 'context/the-architect-ops.token', '/context-token', True),
                volume(data / 'context/the-architect.token', '/office-token', True),
                socket,
            ]
        else:
            worker.update(healthcheck=health('test', '-s', '/tmp/matrix-agent-ready'))
            worker['volumes'] += workspace
        if key in {'coach', 'hiring-manager', 'scouter'} and entry.get('starter_role') == key:
            worker['environment']['TEAM_ROLE'] = key
            worker['environment']['SCOUT_WEEKLY_UTC'] = settings.get('scout_weekly_utc', 'Mon 09:00')
            worker['environment']['ROSTER_CONFIGURE_COMMAND'] = './constellation_matrix --data-dir ' + quote(str(data)) + ' agent configure'
            worker['volumes'].append(volume(SOURCE / 'integrations/chief-of-staff', '/app/chief-of-staff', True))
            credential = data / 'roster' / (key + '.key')
            if settings.get('roster_management') and credential.is_file():
                socket = (volume(data / 'runtime', '/run/constellation', True) if settings.get('control_runtime') else
                          volume(f"/run/{settings['project']}-roster.sock", '/run/office-roster.sock', True))
                if settings.get('control_runtime'):
                    worker['environment']['ROSTER_SOCKET'] = '/run/constellation/roster.sock'
                socket['bind'] = {'create_host_path': False}
                worker['volumes'] += [socket, volume(credential, '/roster-key', True)]
                worker['environment']['ROSTER_MANAGEMENT'] = 'true'
        if key == 'scouter' and entry.get('starter_role') == key:
            worker['networks'].append('provider')
            services[instance]['environment']['TEAM_ROLE'] = 'scouter'
        if key == 'helper':
            support = volume(data / 'support', '/support', True)
            worker['volumes'].append(support)
            dm_volumes = [volume(directory / 'dm', '/state') if item['target'] == '/state' else item
                          for item in worker['volumes'] if item['target'] != '/context-token']
            services['helper-dm'] = {**worker, 'command': ['node', '/app/helper-dm.mjs'],
                                     'environment': {**worker['environment'],
                                         'HELPER_HINTS_TIMEZONE': settings.get('helper_hints_timezone', 'UTC'),
                                         'HELPER_HINTS_FEATURES': ','.join(key for key in ('tailscale', 'dot', 'telegram')
                                             if settings[key]).replace('tailscale', 'tailnet')},
                                     'volumes': dm_volumes, 'networks': ['clients', private]}
        if registry.get('team_version') == 2 and key != 'helper':
            grant_mount = volume(directory / 'tool-credentials', '/tool-credentials', True)
            services[instance]['environment'].update(catalog, MATRIX_SERVER_NAME=server)
            services[instance]['volumes'] += [volume(dashboard, '/catalog', True), grant_mount]
            worker['volumes'].append(grant_mount)
        services[key] = worker
    if registry['agents'] or settings['telegram']:
        network['workshop-targets'] = {'internal': workshop_internal}
        workshop_networks = ['workshop-targets', *(name for name in network if name.startswith('acp-'))]
        if workshop_internal and (any(entry['placement'] == 'remote' for entry in registry['agents'].values()) or
                                  registry.get('telegram_placement', {}).get('placement') == 'remote'):
            # Telegram may already be attached to workshop-targets when a remote worker is selected.
            network['workshop-egress'] = {}
            workshop_networks.append('workshop-egress')
        services['persona-workshop'] = {
            **restricted, 'image': 'constellation-persona-workshop:0.7.4',
            'build': str(SOURCE / 'integrations/chief-of-staff/persona'),
            'networks': workshop_networks,
            'profiles': ['agents'], 'volumes': [volume(data / ('persona-workshop' if registry.get('team_version') == 2 else 'state/chief-of-staff/persona'), '/data')],
            'healthcheck': health('persona-workshop', '--home', '/data', 'api', 'GET', '/api/v1/status'),
        }
    if twin := settings.get('twin'):
        from operator_runtime import image
        for key, config in twin.get('service_config', {}).items():
            if key not in services:
                continue
            service = services[key]
            if config.get('memory'):
                service['mem_limit'] = config['memory']
            if 'healthcheck' in service:
                service['healthcheck'].update({name: value for name, value in config.get('checks', {}).items()
                                               if name in ('interval', 'timeout', 'start_period', 'retries')})
        for link in network.values():
            link['internal'] = True
        # Host CLI and SSH forwarding reach only loopback bindings on these two services.
        network['twin-ingress'] = {}
        for service in ('synapse', 'private-edge'):
            services[service]['networks'].append('twin-ingress')
        network['hiring-models'] = {'internal': True}
        network['provider-relay'] = {'external': True, 'name': twin['provider_network']}
        services['hiring-provider'] = {
            **restricted, 'image': image(), 'networks': ['hiring-models', 'provider-relay'],
            'command': ['python3', '-B', '/relay.py'],
            'environment': {'TWIN_PROVIDER_IP': twin['upstream'], 'TWIN_GRANTS': json.dumps(twin['grants'])},
            'volumes': [volume(SOURCE / 'deploy/twin_proxy.py', '/relay.py', True)],
        }
    return {'name': settings['project'], 'services': services, 'networks': network,
            'volumes': {}}


def health(*command):
    return {'test': ['CMD', *command], 'interval': '5s', 'timeout': '4s', 'retries': 24}
