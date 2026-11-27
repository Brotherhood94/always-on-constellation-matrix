"""Manage only this deployment's private Tailscale Serve routes."""
from dataclasses import asdict, dataclass
import json

from installer import command_output, load, run, save


@dataclass(frozen=True)
class Route:
    port: int
    target: str
    protocol: str = 'https'
    path: str = '/'

    def handler(self, config, host):
        if self.protocol == 'https':
            web = config.get('Web', {}).get(f'{host}:{self.port}', {})
            return web.get('Handlers', {}).get(self.path)
        return config.get('TCP', {}).get(str(self.port))

    def matches(self, config, host):
        handler = self.handler(config, host) or {}
        if self.protocol == 'https':
            field = 'Path' if self.target.startswith('/') else 'Proxy'
            return handler.get(field, '').rstrip('/') == self.target.rstrip('/')
        tls = host if self.protocol == 'tls-terminated-tcp' else ''
        return (handler.get('TCPForward') == self.target.removeprefix('tcp://')
                and handler.get('TerminateTLS', '') == tls and not handler.get('ProxyProtocol'))

    def flags(self):
        flags = [f'--{self.protocol}={self.port}']
        if self.protocol == 'https':
            flags.append(f'--set-path={self.path}')
        return flags


def workspace_routes(settings, data, registry):
    if not settings or not settings['tailscale']:
        return []
    origin = f"http://127.0.0.1:{settings['matrix_port']}"
    paths = ('/_matrix/client', '/_matrix/media', '/_synapse/client', '/.well-known/matrix/client')
    routes = [Route(443, origin + path, path=path) for path in paths]
    routes.extend([
        Route(settings['dashboard_https_port'], str(data / 'dashboard')),
        Route(settings['neo4j_https_port'], f"http://127.0.0.1:{settings['neo4j_port']}"),
        Route(settings['bolt_https_port'], f"tcp://127.0.0.1:{settings['bolt_port']}", 'tls-terminated-tcp'),
    ])
    if 'hiring-manager' in registry['agents']:
        routes.append(Route(settings['dashboard_https_port'],
                            f"http://127.0.0.1:{settings.get('hiring_port', 18792)}/hiring", path='/hiring'))
    remote_pending = any(
        move['destination'] != settings['server_name'] and move['phase'] not in {'complete', 'rolled-back', 'cancelled'}
        for move in registry.get('relocations', {}).values())
    if remote_pending or any(agent['placement'] == 'remote' for agent in registry['agents'].values()):
        routes.append(Route(settings['context_https_port'], f"http://127.0.0.1:{settings['context_port']}"))
    return routes


def status():
    return json.loads(command_output('tailscale', 'serve', 'status', '--json') or '{}') or {}


def remove_matching(host, routes):
    if not routes:
        return
    configured = status()
    for route in routes:
        if route.matches(configured, host):
            run('tailscale', 'serve', '--bg', *route.flags(), 'off')


def configure(settings, data, registry, previous=None):
    host = settings['server_name']
    path = data / 'serve-routes.json'
    current = workspace_routes(settings, data, registry)
    recorded = [Route(**item) for item in load(path)] if path.exists() else []
    prior = list(dict.fromkeys(recorded + workspace_routes(previous, data, registry)))
    if prior != recorded:
        save(path, [asdict(route) for route in prior])
    if current:
        tailnet = json.loads(command_output('tailscale', 'status', '--json'))
        node = (tailnet.get('Self') or {}).get('DNSName') or ''
        if node.rstrip('.') != host or host not in (tailnet.get('CertDomains') or []):
            raise ValueError('The Matrix DNS name must match an HTTPS-enabled Tailscale node')
        configured = status()
        for route in current:
            if configured.get('AllowFunnel', {}).get(f'{host}:{route.port}'):
                raise ValueError('Disable the public Funnel rule on the Constellation ports before setup')
            same_endpoint = [old for old in prior if old.flags() == route.flags()]
            if route.handler(configured, host) and not any(
                candidate.matches(configured, host) for candidate in [route, *same_endpoint]
            ):
                raise ValueError(f'Tailscale Serve already has another target for port {route.port} and path {route.path}')
        save(path, [asdict(route) for route in dict.fromkeys(prior + current)])
        for route in current:
            if not route.matches(configured, host):
                run('tailscale', 'serve', '--bg', *route.flags(), route.target)
    removed = [route for route in prior if route.flags() not in [item.flags() for item in current]]
    remove_matching(host, removed)
    if current or path.exists():
        save(path, [asdict(route) for route in current])


def remove_workspace(settings, data, registry):
    path = data / 'serve-routes.json'
    routes = workspace_routes(settings, data, registry)
    if path.exists():
        routes += [Route(**item) for item in load(path)]
    if (data / 'desktop-compose.yaml').is_file():
        routes.append(Route(2222, 'tcp://127.0.0.1:18222', 'tcp'))
    remove_matching(settings['server_name'], list(dict.fromkeys(routes)))
    path.unlink(missing_ok=True)
