"""Rehearse the opt-in conversion with a real homeserver and synthetic accounts."""
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import os
import sqlite3

from installer import (available_profiles, base_environment, directory, load, normalized, prepare_agent_state,
                       prepare_storage, register_entry, save)
from layout import CLIENT, create_layout, encoded, login, room_route
from provision import MatrixError, request
from team_migration import migrate

password = 'synthetic-test-passphrase-88'
server = os.environ['MATRIX_SERVER_NAME']
with TemporaryDirectory() as temp, ExitStack() as stack:
    data = Path(temp)
    presets = available_profiles()
    original = ['manager', 'chief-of-staff', 'coder']
    settings = normalized({'owner':'operator','server_name':server,'project':'starter-fixture',
                           'agents':original, 'systemd':False,'tailscale':False,'matrix_port':8008}, presets)
    save(data / 'settings.json', settings)
    directory(data / 'secrets')
    save(data / 'secrets/MATRIX_REGISTRATION_SECRET', os.environ['MATRIX_REGISTRATION_SECRET'])
    base_environment(settings, data)
    prepare_storage(settings, data)
    directory(data / 'context')
    save(data / 'context/neo4j-password', 'fixture-graph-password')
    owner = login('operator', password)
    rooms = create_layout(server,owner['user_id'],owner['access_token'])
    save(data / 'rooms.json', rooms)
    registry = {'local_server':server,'agents':{},'team_version':1}
    for key in [*original, 'scouter','hiring-manager','coach','vps-manager','helper']:
        matrix = data / 'state' / key / 'matrix'
        directory(matrix)
        save(matrix / 'matrix-password', password)
        if key in original:
            register_entry(settings,registry,key,presets[key])
            prepare_agent_state(data,key,registry['agents'][key],rooms,owner['access_token'],settings)
    registry['agents']['manager']['active'] = True
    save(data / 'agents.json', registry)
    coordinator = data / 'state/manager'
    token = (coordinator / 'matrix/matrix-token').read_text()
    save(coordinator / 'instance/fixture-provider', 'keep-my-sessions')
    chief = data / 'state/chief-of-staff'
    save(chief / 'persona/fixture-project', 'keep-my-workshop')
    old_dm = (chief / 'matrix/room-id').read_text().strip()
    old_access = (chief / 'matrix/matrix-token').read_text().strip()
    old_event = request('PUT',room_route(old_dm)+'/send/m.room.encrypted/legacy-history',
        {'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'history-fixture'},old_access)['event_id']
    receipt = {'welcome':{'event':'$already-welcomed'}}
    save(data / 'onboarding.json',receipt)
    save(data / 'owner-password',password)
    args = SimpleNamespace(apply=False,retire=[],owner_password_file=data/'owner-password')
    # Matrix and persistent state are real. Other service processes have their own integration fixtures.
    for name in ('compose','up','refresh_context','prepare_images','sync_workshop','service_units','configure_network','publish_inventory'):
        stack.enter_context(patch('team_migration.'+name))
    migrate(data,args)
    assert not (data/'team-migration.json').exists()
    queue = coordinator/'matrix/connector.sqlite3'
    with sqlite3.connect(queue) as db:
        db.execute('CREATE TABLE inbox (id TEXT,request TEXT,outgoing TEXT,sent TEXT)')
        db.execute("INSERT INTO inbox VALUES ('pending','pending',NULL,NULL)")
    args.apply = True
    try:
        migrate(data,args)
        raise AssertionError('Open work was ignored')
    except ValueError as exc:
        assert 'open assignments' in str(exc)
    assert load(data/'agents.json')['team_version'] == 1
    with sqlite3.connect(queue) as db:
        db.execute("UPDATE inbox SET sent='settled'")
    migrate(data,args)
    result = load(data/'agents.json')
    assert set(result['agents']) == {'manager','coder','scouter','hiring-manager','coach','vps-manager','helper'}
    assert result['agents']['manager']['starter_role'] == 'task-manager'
    assert (coordinator/'matrix/matrix-token').read_text() == token
    assert (coordinator/'instance/fixture-provider').read_text() == 'keep-my-sessions'
    assert (data/'persona-workshop/fixture-project').read_text() == 'keep-my-workshop'
    assert load(data/'onboarding.json') == receipt
    assert request('GET',CLIENT+'/profile/'+encoded('@manager:'+server)+'/displayname',token=token.strip())['displayname']=='Task Manager'
    route = room_route(rooms['space'])
    children = {r['state_key'] for r in request('GET',route+'/state',token=owner['access_token'])
                if r['type']=='m.space.child' and r['content'].get('via')}
    assert old_dm not in children
    history = request('GET',room_route(old_dm)+'/event/'+encoded(old_event),token=owner['access_token'])
    assert history['sender'] == '@chief-of-staff:'+server
    try:
        request('GET',CLIENT+'/account/whoami',token=old_access)
        raise AssertionError('Retired Chief account is still signed in')
    except MatrixError:
        pass
    dms = {}
    for key in ('hiring-manager','coach','scouter'):
        dm = (data/'state'/key/'matrix/control-room-id').read_text().strip()
        dms[key] = dm
        assert dm in children and dm != old_dm
        assert set(request('GET',room_route(dm)+'/joined_members',token=owner['access_token'])['joined']) == {owner['user_id'],'@'+key+':'+server}
        assert request('GET',room_route(dm)+'/state/m.room.encryption',token=owner['access_token'])['algorithm']=='m.megolm.v1.aes-sha2'
    assert len(set(dms.values())) == 3
    helper_dm = (data/'state/helper/dm/room-id').read_text().strip()
    assert helper_dm in children and helper_dm != rooms['office']
    participant = request('GET', room_route(helper_dm) + '/joined_members', token=owner['access_token'])['joined']
    assert set(participant) == {owner['user_id'], '@helper:'+server}
    direct = request('GET', f"{CLIENT}/user/{encoded(owner['user_id'])}/account_data/m.direct", token=owner['access_token'])
    assert helper_dm in direct['@helper:'+server]
    root_ops = (data/'state/vps-manager/matrix/room-id').read_text().strip()
    assert root_ops in children and all(root_ops not in links for links in direct.values())
    if os.environ.get('MATRIX_HELPER_SMOKE') == '1':
        operator = login('operator', password, 'HELPERSMOKEOWNER', 'Helper Smoke Operator')
        # This file contains only disposable Matrix identities inside the test volume.
        save(Path('/data/helper-smoke.json'), {'operator': operator, 'owner': owner['user_id'],
             'privateRoom': helper_dm, 'office': rooms['office'], 'domain': server,
             'managerToken': token.strip(),
             'coachToken': (data/'state/coach/matrix/matrix-token').read_text().strip(),
             'helperOfficeToken': (data/'state/helper/matrix/matrix-token').read_text().strip(),
             'helperToken': (data/'state/helper/dm/matrix-token').read_text().strip()})
    migrate(data,args)
    assert load(data/'agents.json') == result
    assert all((data/'state'/key/'matrix/control-room-id').read_text().strip() == dm for key,dm in dms.items())
    print('Starter migration preserved the coordinator identity, private history, Workshop store, specialist and welcome receipt in Synapse.')
