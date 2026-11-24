import json
from pathlib import Path
import sys

sys.path.insert(0, '/opt/matrix-stack')
from agent_catalog import document, modify, register


def update(agent, release):
    definition = json.loads((release / f'{agent}.json').read_text()) if agent == 'epyc100' else json.loads(
        (release / 'root-profile.json').read_text())

    def change(registry):
        if agent == 'epyc100':
            register(registry, agent, definition, 'Epyc100', 'epyc100')
        else:
            host = registry['local_server']
            if host != 'macai':
                raise ValueError(f'Unexpected Root host {host}.')
            register(registry, agent, definition, 'Root', host)
            registry['agents'][agent].update(model='gpt-6.2-sol', effort='xhigh', local_compose={
                'project': 'matrix-root-operations',
                'service': 'gateway',
                'bundle': '/opt/matrix-root-operations/instance/backend/root-maintenance.yaml',
            })
        document(registry, [], 'validation.invalid', 0)

    modify(change)
    print(f'Updated the agent catalog for {agent}.')


if __name__ == '__main__':
    agent, release = sys.argv[1:]
    if agent not in {'epyc100', 'root'}:
        raise SystemExit('Unexpected agent ID.')
    update(agent, Path(release))
