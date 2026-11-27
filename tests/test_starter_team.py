import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
import installer
from agent_picker import select_agents
from agent_catalog import document


class StarterTeamTests(unittest.TestCase):
    def test_management_authority_belongs_to_the_installed_role(self):
        from roster_broker import authorized_actor
        settings = {'roster_management': True}
        registry = {'team_version':2, 'agents':{'coach':{'active':True}}}
        self.assertFalse(authorized_actor(settings,registry,'coach'))
        registry['agents']['coach']['starter_role'] = 'coach'
        self.assertTrue(authorized_actor(settings,registry,'coach'))
        registry['agents']['coach']['retirement'] = 'draining'
        self.assertFalse(authorized_actor(settings,registry,'coach'))
        registry['agents']['chief-of-staff'] = {'active':True}
        self.assertFalse(authorized_actor(settings,registry,'chief-of-staff'))

    def test_new_operator_can_confirm_starter_team_and_see_each_persona(self):
        presets = installer.starter_profiles()
        self.assertEqual(set(presets), {'task-manager', 'hiring-manager', 'coach', 'scouter', 'the-architect', 'helper'})
        with patch('agent_picker.terminal_picker', return_value=None), patch('builtins.input', return_value=''), patch('builtins.print'):
            self.assertEqual(select_agents(presets), list(presets))
        roster = {'local_server': 'office.example', 'team_version': 2, 'agents': {}}
        settings = {'owner': 'operator', 'server_name': 'office.example'}
        for key, definition in presets.items():
            installer.register_entry(settings, roster, key, definition)
        catalog = document(roster, [], 'office.example', 1)
        rows = {row['key']: row for row in catalog['agents']}
        self.assertEqual(set(rows['coach']['contacts']), {'task-manager', 'hiring-manager', 'scouter'})
        self.assertIn('helper', rows['task-manager']['contacts'])
        self.assertEqual(rows['helper']['contacts'], [])
        self.assertNotIn('helper', rows['scouter']['contacts'])
        for row in rows.values():
            self.assertTrue(row['persona']['duties'])
            self.assertTrue(row['persona']['working_style'])
            self.assertEqual(row['status'], 'Sign-in required')

    def test_migration_preview_preserves_identity_and_custom_workers(self):
        from team_migration import preview
        base = installer.available_profiles()
        entries = {k: {**base[k], 'placement':'local'} for k in ('manager','chief-of-staff','coder','reviewer','the-architect')}
        registry = {'agents':entries}
        plan = preview(registry, ['reviewer'])
        self.assertEqual(plan['task_manager'],'manager')
        self.assertEqual(set(plan['add']),{'hiring-manager','coach','scouter','helper'})
        self.assertEqual(plan['retain'],['coder','manager','the-architect'])
        self.assertTrue(plan['archive_chief'])
        self.assertIn('reviewer',entries)
        with self.assertRaises(ValueError):
            preview(registry,['manager'])
        entries['task-manager'] = {**base['coder'],'placement':'local'}
        with self.assertRaisesRegex(ValueError,'identity'):
            preview(registry)

    def test_only_the_approved_tool_is_exposed_after_target_verification(self):
        from tempfile import TemporaryDirectory
        from team_management import apply_tool, tool_spec
        import json
        tool = {'id':'repo-reader','kind':'github','version':'1','source':'https://github.com/cli/cli',
                'permissions':'Read issues in sample/project','verification':'Read repository metadata','repository':'sample/project'}
        operation = {'agent':'coder','tool':tool}
        with TemporaryDirectory() as folder, patch('team_management.publish_inventory'), patch('team_management.graph') as graph:
            data = Path(folder)
            registry = {'agents':{'coder':{'active':True},'other':{'active':True}}}
            first, second = 'a'*64, 'b'*64
            self.assertEqual(apply_tool(data,{},registry,operation,first),'checking')
            self.assertEqual(registry['agents']['coder']['enabled_tools'],{})
            graph.return_value = [{'data':json.dumps({'change':first,'status':'credentials'})}]
            self.assertEqual(apply_tool(data,{},registry,operation,first),'credentials')
            self.assertEqual(apply_tool(data,{},registry,operation,first),'credentials')
            self.assertEqual(apply_tool(data,{},registry,operation,second),'checking')
            graph.return_value = [{'data':json.dumps({'change':second,'status':'verified'})}]
            self.assertEqual(apply_tool(data,{},registry,operation,second),'enabled')
            self.assertEqual(apply_tool(data,{},registry,operation,second),'enabled')
            self.assertEqual(registry['agents']['coder']['enabled_tools'],{'repo-reader':tool})
            self.assertNotIn('enabled_tools',registry['agents']['other'])
            self.assertEqual(graph.call_args.args[-1]['actor'],'coder')
        with self.assertRaises(ValueError):
            tool_spec({**tool,'shell':'arbitrary command'})
        with self.assertRaises(ValueError):
            tool_spec({**tool,'repository':'../other'})

    def test_public_web_purpose_is_reserved_for_scoped_mcp_tools(self):
        from team_management import tool_spec
        tool = {'id':'web-research','kind':'mcp','version':'1','source':'https://publisher.example.org/web',
                'endpoint':'https://publisher.example.org/mcp','methods':['search','read'],
                'permissions':'Read public pages','verification':'Confirm search and read methods',
                'purpose':'public-web'}
        self.assertEqual(tool_spec(tool), tool)
        with self.assertRaises(ValueError):
            tool_spec({**tool,'purpose':'host-shell'})
        with self.assertRaises(ValueError):
            tool_spec({**tool,'kind':'github'})

    def test_protected_edges_and_stale_workshop_edits(self):
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from team_management import coach_change
        registry = {'agents':{k: {**v,'active':True} for k,v in installer.starter_profiles().items()}}
        settings={'server_name':'fixture.test'}
        op={'kind':'coach','agent':'scouter','style':'Include a publisher link.','contacts':['the-architect'],
            'evidence':'$proof','generation':2}
        with TemporaryDirectory() as tmp:
            data=Path(tmp)
            self.assertEqual(coach_change(data,settings,registry,op),'needs_approval')
            (data/'workshop-management').mkdir()
            target='f46311b5-1b4f-480f-a4b5-1f43095e50fc'
            installer.save(data/'workshop-management/targets.json',{'scouter':{'instance':'id','target':target}})
            installer.save(data/'rooms.json',{'office':'!office:test'})
            actual={'target':{'agent_id':'id'},'installation':{'revision':3,'active':None}}
            with patch('team_management.compose',return_value=SimpleNamespace(stdout='workshop')), \
                 patch('team_management.api',return_value=actual), patch('team_management.graph',return_value=[{}]), \
                 patch('team_management.save_style') as publish:
                with self.assertRaisesRegex(ValueError,'changed since review'):
                    coach_change(data,settings,registry,op,approved=True)
                with self.assertRaisesRegex(ValueError,'participate'), patch('team_management.graph',return_value=[]):
                    coach_change(data,settings,registry,{**op,'generation':3},approved=True)
                publish.assert_not_called()

    def test_dashboard_uses_the_applied_revision_and_preserves_copy_for_old_profiles(self):
        registry = {'local_server':'host','team_version':2,'agents':{}}
        for key in ['task-manager','scouter','coder']:
            installer.register_entry({'server_name':'host','owner':'owner'},registry,key,installer.available_profiles()[key])
        profiles = {'scouter':{'revision':8,'working_style':'Review the publisher first.'}}
        rows = {r['key']:r for r in document(registry,[],'host',1,profiles)['agents']}
        self.assertEqual(rows['scouter']['revision'],8)
        self.assertEqual(rows['scouter']['persona']['working_style'],'Review the publisher first.')
        self.assertTrue(rows['coder']['persona']['duties'])
        self.assertEqual(rows['coder']['contacts'],['task-manager'])
        self.assertIn('coder',rows['task-manager']['contacts'])
        registry['agents']['scouter'].update(persona_revision=9, applied_style='Follow the newly applied revision.')
        row = next(r for r in document(registry,[],'host',1,profiles)['agents'] if r['key']=='scouter')
        self.assertEqual(row['persona']['working_style'],'Follow the newly applied revision.')

    def test_interrupted_undo_checks_the_snapshot_and_finishes_without_applying_twice(self):
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from team_management import coach_change, coaching_id
        target = 'f46311b5-1b4f-480f-a4b5-1f43095e50fc'
        first = 'a' * 64
        op = {'kind': 'undo', 'agent': 'scouter', 'evidence': '$proof', 'change': first, 'generation': 2}
        current = coaching_id(op)
        registry = {'team_version': 2, 'dynamic_handoffs': True,
                    'agents': {k: {**v, 'active': True} for k, v in installer.starter_profiles().items()}}
        registry['agents']['scouter']['last_coaching'] = current
        with TemporaryDirectory() as folder:
            data = Path(folder)
            installer.commit_registry(data / 'agents.json', registry)
            (data / 'workshop-management').mkdir()
            journal = data / 'team-changes'
            journal.mkdir()
            installer.save(data / 'workshop-management/targets.json', {'scouter': {'instance': 'id', 'target': target}})
            installer.save(journal / (first + '.json'), {
                'before_contacts': ['task-manager', 'hiring-manager'], 'before_style': 'Describe the publisher.',
                'before_override': None,
            })
            receipt = journal / (current + '.json')
            installer.save(receipt, {
                'target': target, 'generation': 2, 'before_snapshot': 'snapshot', 'restore': 'baseline',
            })
            expected = {'working-style.md': 'old-digest'}
            actual = {'target': {'agent_id': 'id'}, 'installation': {
                'revision': 3, 'active': {'files': {'working-style.md': 'different'}},
                'previous': [{'id': 'baseline', 'files': expected}],
            }}
            with patch('team_management.compose', return_value=SimpleNamespace(stdout='workshop')), \
                 patch('team_management.api', return_value=actual) as api, patch('team_management.publish_inventory'):
                with self.assertRaisesRegex(ValueError, 'files do not match'):
                    coach_change(data, {}, registry, op)
                self.assertEqual(installer.load(receipt)['restore_files'], expected)
                actual['installation'].update(active={'files': expected}, previous=[])
                self.assertEqual(coach_change(data, {}, registry, op), 'applied')
                self.assertTrue(installer.load(receipt)['done'])
                self.assertTrue(all(call.args[1] == 'GET' for call in api.call_args_list))
                self.assertEqual(installer.load(data / 'agents.json')['connection_history']['sequence'], 1)
