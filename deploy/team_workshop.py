"""Publish bounded Office working-style revisions through Persona Workshop's versioned API."""
from uuid import UUID
import json


def active_claims(request, installation):
    active = installation.get('active') or {}
    source = active.get('source')
    if not source:
        return []
    path = '/api/v1/projects/{}/revisions/{}'.format(UUID(source['subject_key']), UUID(source['revision']))
    revision = request('GET', path)
    if not revision.get('valid') or any(revision['manifest']['files'].get(k) != digest for k, digest in active['files'].items()):
        raise ValueError('The installed Workshop files differ from the cited revision')
    return [claim for claim in json.loads(revision['files']['claims.json']) if claim.get('status') == 'accepted']


def save_style(request, alias, purpose, communication, working_style, previous=()):
    root = '/api/v1/projects'
    matches = [row for row in request('GET', root)['items'] if row['alias'] == alias]
    if len(matches) > 1:
        raise ValueError('Workshop has conflicting projects for this change')
    project = (request('GET', f"{root}/{UUID(matches[0]['id'])}") if matches else
               request('POST', root, {'alias': alias, 'purpose': purpose, 'subject_type': 'fictional', 'aliases': []}))
    path = f"{root}/{UUID(project['id'])}"
    fields = ('collection', 'section', 'statement', 'scope')
    statements = [tuple(claim[k] for k in fields) for claim in previous
                  if (claim['collection'], claim['section']) != ('working', 'collaboration')]
    if not any(row[0] == 'communication' for row in statements):
        statements.append(('communication', 'structure', communication, 'Office working agreement'))
    statements.append(('working', 'collaboration', working_style, 'Office working agreement'))
    for collection, section, statement, scope in statements:
        if not any(row.get('valid') and tuple(row[k] for k in fields) == (collection, section, statement, scope)
                   for row in project['observations']):
            project = request('POST', path + '/observations', {'expected_revision': project['revision'],
                              'collection': collection, 'section': section, 'statement': statement,
                              'scope': scope})
    revisions = [row for row in project.get('revisions', []) if row.get('valid')]
    revision = revisions[-1] if revisions else request('POST', path + '/revisions', {'expected_revision': project['revision']})
    if not revision.get('valid'):
        raise ValueError('Workshop could not compile the proposed profile')
    return path + '/revisions/' + str(UUID(revision['id']))


def apply_style(request, target, revision, expected):
    return request('POST', revision + '/apply', {'target_id': str(UUID(target)), 'expected_revision': expected})['installation']
