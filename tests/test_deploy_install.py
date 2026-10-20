"""Exercise project deployment without root, package downloads, or live services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class ProjectDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        for directory in ['deploy', 'src', 'config', '.venv/bin', '.git']:
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        for name in ['deploy/install.sh', 'src/deploy_check.py']:
            shutil.copy2(REPO / name, self.root / name)
        (self.root / 'main.py').write_text('# source must survive\n')
        (self.root / 'requirements.txt').write_text('')
        (self.root / '.git/keep').write_text('repository metadata')
        (self.root / 'config/URL_config.ini').write_text('https://example.com/live\n')
        self.bin = Path(self.tmp.name) / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}')
        self.script('id', 'if [ "$1" = "-u" ]; then echo 0; else exit 0; fi')
        self.script('git', 'exit 1')  # fixture is not a real repository
        self.script('chown', f'printf "%s\\n" "$*" >> "{self.tmp.name}/chown.log"')
        self.script('systemctl', 'echo "unexpected systemctl" >&2; exit 99')
        python = self.root / '.venv/bin/python'
        python.write_text('#!/bin/sh\n[ "$1" = "-m" ] && [ "$2" = "pip" ]\n')
        python.chmod(0o755)

    def script(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    def run_install(self, *args):
        return subprocess.run(['bash', str(self.root / 'deploy/install.sh'), *args],
                              env=self.env, text=True, capture_output=True)

    def test_default_and_explicit_project_deployment_preserve_source_and_data(self):
        for args in [[], ['--install-dir', str(self.root)]]:
            with self.subTest(args=args):
                result = self.run_install('--upgrade', '--no-systemd', *args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((self.root / 'main.py').read_text(), '# source must survive\n')
                self.assertTrue((self.root / '.git/keep').exists())
                self.assertEqual((self.root / 'config/URL_config.ini').read_text(),
                                 'https://example.com/live\n')
                manifest = json.loads((self.root / '.deploy-manifest.json').read_text())
                self.assertEqual(manifest['source'], str(self.root))
                self.assertNotIn('config/URL_config.ini', manifest['files'])
                self.assertTrue((self.root / '.deploy-installed').exists())
                self.assertEqual(self.run_install('--check').returncode, 0)
        calls = (Path(self.tmp.name) / 'chown.log').read_text().splitlines()
        self.assertNotIn(f'-R ubuntu:ubuntu {self.root}', calls)

    def test_purge_cannot_delete_project(self):
        result = self.run_install('--uninstall', '--purge')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('拒绝 --purge', result.stderr)
        self.assertTrue((self.root / 'main.py').exists())
        self.assertTrue((self.root / '.git/keep').exists())

    def test_protected_copy_target_is_rejected(self):
        for target in ['/home', '/', '/opt', '/etc']:
            with self.subTest(target=target):
                result = self.run_install('--install-dir', target, '--force-dir')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('安装目录', result.stderr)

    def test_project_deployment_still_rejects_uncommitted_code(self):
        self.script('git', '''case "$*" in
          *status*) echo ' M main.py' ;;
          *rev-parse*) echo fixture ;;
        esac''')
        result = self.run_install('--upgrade', '--no-systemd')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('源码目录有未提交改动', result.stderr)
        self.assertFalse((self.root / '.deploy-manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
