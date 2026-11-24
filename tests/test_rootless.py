"""Security and command checks for the rootless installer launcher."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from rootless import docker_socket, launch


class RootlessDockerTest(unittest.TestCase):
    def test_daemon_must_be_local_owned_and_rootless(self):
        with TemporaryDirectory() as work:
            path = Path(work).resolve() / 'docker.sock'
            path.touch()
            with patch.dict(os.environ, {'DOCKER_HOST': f'unix://{path}'}, clear=True):
                with self.assertRaisesRegex(ValueError, 'belongs to your account'):
                    docker_socket(os.geteuid())
            with patch.dict(os.environ, {'DOCKER_HOST': f'unix://{path}'}, clear=True), \
                 patch('rootless.stat.S_ISSOCK', return_value=True):
                with self.assertRaisesRegex(ValueError, 'belongs to your account'):
                    docker_socket(os.geteuid() + 1)
                with patch('rootless.subprocess.check_output', return_value='["name=userns"]'):
                    with self.assertRaisesRegex(ValueError, 'rootless mode'):
                        docker_socket(os.geteuid())
                with patch('rootless.subprocess.check_output', return_value='["name=rootless"]'):
                    selected, cli, _ = docker_socket(os.geteuid())
                    self.assertEqual(selected, path)
                    self.assertEqual(cli, ('docker', '--host', f'unix://{path}'))
            with patch.dict(os.environ, {'DOCKER_HOST': 'ssh://example.test'}, clear=True):
                with self.assertRaisesRegex(ValueError, 'local rootless Docker Unix socket'):
                    docker_socket(os.geteuid())

    def test_launch_has_scoped_mounts_and_keeps_standard_input(self):
        with TemporaryDirectory() as work:
            workspace = Path(work)
            source, data, config = (workspace / name for name in ('source', 'data', 'install.json'))
            config.touch()
            cli = ('docker', '--host', 'unix:///run/user/1000/docker.sock')
            with patch('rootless.docker_socket', return_value=(Path('/run/user/1000/docker.sock'), cli, {})), \
                 patch('rootless.subprocess.run') as run:
                run.return_value.returncode = 0
                launch(source, data, ['install', '--config', str(config)], [config])
            command = run.call_args.args[0]
            self.assertTrue(data.is_dir())
            self.assertIn(f'type=bind,source={source},target={source},readonly', command)
            self.assertIn(f'type=bind,source={data},target={data}', command)
            self.assertIn(f'type=bind,source={config},target={config},readonly', command)
            self.assertIn('-i', command)
            self.assertNotIn('--privileged', command)
