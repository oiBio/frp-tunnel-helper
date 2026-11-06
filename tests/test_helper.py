"""Docker tests; TUNNEL_TEST_ENV_FILE enables live tests against your own relay."""
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import time
import unittest
import urllib.error
import urllib.request

IMAGE = os.environ.get('TUNNEL_TEST_IMAGE', 'oibio/frp-tunnel-helper:0.3.0')
DOMAIN = 'tunnel.example.test'
if os.environ.get('TUNNEL_TEST_ENV_FILE'):
    values = dict(line.split('=', 1) for line in Path(os.environ['TUNNEL_TEST_ENV_FILE']).read_text().splitlines() if '=' in line)
    DOMAIN = values['TUNNEL_DOMAIN']
FIXTURE = '''
import base64, hashlib, http.server, json, struct
class App(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('Upgrade', '').lower() == 'websocket':
            accept = base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()
            head = self.rfile.read(2)
            length = head[1] & 127
            mask = self.rfile.read(4)
            data = self.rfile.read(length)
            body = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
            self.wfile.write(bytes([129, len(body)]) + body)
            self.wfile.flush()
            return
        body = json.dumps(dict(path=self.path, prefix=self.headers.get('X-Forwarded-Prefix'))).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
http.server.ThreadingHTTPServer(('0.0.0.0', 8080), App).serve_forever()
'''


def docker(*args, check=True):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, encoding='utf-8')
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result


def events(container):
    result = docker('logs', container)
    return [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]


class HelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prefix = 'frp-helper-test-' + secrets.token_hex(5)
        cls.volume = cls.prefix + '-state'
        cls.network = cls.prefix + '-net'
        cls.containers = []
        docker('volume', 'create', cls.volume)
        docker('network', 'create', cls.network)

    @classmethod
    def tearDownClass(cls):
        for name in reversed(cls.containers):
            docker('rm', '-fv', name, check=False)
        docker('network', 'rm', cls.network, check=False)
        docker('volume', 'rm', cls.volume, check=False)

    def prepare(self, *args, check=True):
        return docker('run', '--rm', '--read-only', '--tmpfs', '/tmp', '--cap-drop', 'ALL',
                      '-v', f'{self.volume}:/data', IMAGE, '--domain', DOMAIN, *args, '--prepare', check=check)

    def test_persistent_id_and_code(self):
        first = json.loads(self.prepare('--name', 'persistent', '--port', '8080').stdout)
        second = json.loads(self.prepare('--name', 'persistent').stdout)
        self.assertEqual(first['id'], second['id'])
        self.assertRegex(first['id'], r'^persistent-[a-f0-9]{12}$')
        self.assertFalse(second['requires_code'])
        coded = json.loads(self.prepare('--name', 'persistent', '--with-code').stdout)
        self.assertTrue(coded['requires_code'])
        self.assertEqual(coded['id'], first['id'])
        self.assertTrue(json.loads(self.prepare('--name', 'persistent').stdout)['requires_code'])
        self.assertFalse(json.loads(self.prepare('--name', 'persistent', '--without-code').stdout)['requires_code'])
        self.assertNotIn('code', first)
        changed = json.loads(self.prepare('--name', 'persistent', '--port', '8081').stdout)
        self.assertEqual(changed['id'], first['id'])
        self.assertEqual(changed['port'], 8081)
        conflict = self.prepare('--name', 'persistent', '--id', '0' * 48, check=False)
        self.assertNotEqual(conflict.returncode, 0)

    def test_legacy_links_and_imported_codes_remain_unchanged(self):
        legacy = dict(Id='a' * 48, Port=8080, Code=None)
        coded = dict(Id='b' * 48, Port=8080, Code='c' * 32)
        imported = dict(Id='windows-tool-012345abcdef', Port=8080, Code=None)
        for name, values in [('legacy', legacy), ('legacy-coded', coded), ('imported', imported)]:
            docker('run', '--rm', '-v', f'{self.volume}:/data', '--entrypoint', 'python', IMAGE,
                   '-c', 'import json,sys; json.dump(json.loads(sys.argv[2]),open("/data/share-"+sys.argv[1]+".json","w"))',
                   name, json.dumps(values))
            resumed = json.loads(self.prepare('--name', name).stdout)
            self.assertEqual(resumed['id'], values['Id'])
            self.assertEqual(resumed['requires_code'], bool(values['Code']))
            saved = docker('run', '--rm', '-v', f'{self.volume}:/data', '--entrypoint', 'python', IMAGE,
                           '-c', 'import pathlib,sys; print(pathlib.Path("/data/share-"+sys.argv[1]+".json").read_text())', name)
            self.assertEqual(json.loads(saved.stdout), values)
        explicit = json.loads(self.prepare('--name', 'explicit', '--port', '8080', '--id', 'd' * 48).stdout)
        self.assertEqual(explicit['id'], 'd' * 48)
        self.assertFalse(explicit['requires_code'])

    def test_relay_domain_has_no_default(self):
        result = docker('run', '--rm', IMAGE, '--name', 'no-domain', '--port', '8080', '--prepare', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--domain', result.stderr)

    def test_invalid_arguments_and_missing_token(self):
        for args in [('--name', '../bad', '--port', '80'), ('--name', 'bad', '--port', '65536'),
                     ('--name', 'bad', '--port', '80', '--host', 'app\nmalicious')]:
            self.assertNotEqual(self.prepare(*args, check=False).returncode, 0)
        missing = docker('run', '--rm', IMAGE, '--domain', DOMAIN, '--name', 'missing', '--port', '80', check=False)
        self.assertNotEqual(missing.returncode, 0)

    @unittest.skipUnless(os.environ.get('TUNNEL_TEST_ENV_FILE'), 'No live relay environment file supplied')
    def test_live_relay_sidecar_and_shared_namespace(self):
        env_file = str(Path(os.environ['TUNNEL_TEST_ENV_FILE']).resolve())
        app = self.prefix + '-app'
        self.containers.append(app)
        docker('run', '-d', '--name', app, '--network', self.network, '--network-alias', 'app',
               '--read-only', '--entrypoint', 'python', IMAGE, '-c', FIXTURE)
        for variant, network, host in [('sidecar', self.network, 'app'),
                                       ('namespace', 'container:' + app, '127.0.0.1'),
                                       ('coded', self.network, 'app')]:
            with self.subTest(variant=variant):
                helper = self.prefix + '-' + variant
                self.containers.append(helper)
                command = ['run', '-d', '--name', helper, '--network', network,
                           '--read-only', '--tmpfs', '/tmp', '--cap-drop', 'ALL',
                           '--env-file', env_file, '-v', f'{self.volume}:/data', IMAGE,
                           '--name', variant, '--host', host, '--port', '8080']
                if variant == 'coded':
                    command.append('--with-code')
                docker(*command)
                registered = None
                for attempt in range(100):
                    registered = next((item for item in events(helper) if item['event'] == 'registered'), None)
                    if registered:
                        break
                    self.assertEqual(docker('inspect', '--format', '{{.State.Running}}', helper).stdout.strip(), 'true')
                    time.sleep(.2)
                self.assertIsNotNone(registered, 'No proxy registration received from the relay')
                url = registered['url']
                headers = {}
                if variant == 'coded':
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(url, timeout=15)
                    self.assertEqual(error.exception.code, 401)
                    code = docker('run', '--rm', '-v', f'{self.volume}:/data', '--entrypoint', 'python',
                                  IMAGE, '-c', "import json; print(json.load(open('/data/share-coded.json'))['Code'])").stdout.strip()
                    headers['Authorization'] = 'Basic ' + base64.b64encode(('tester:' + code).encode()).decode()
                    self.assertNotIn(code, docker('logs', helper).stdout)
                    self.assertNotIn(code, docker('logs', helper).stderr)
                with urllib.request.urlopen(urllib.request.Request(url + 'nested?q=1', headers=headers), timeout=15) as response:
                    body = json.load(response)
                self.assertEqual(body['path'], registered['base_path'] + '/nested?q=1')
                self.assertEqual(body['prefix'], registered['base_path'])
                payload = secrets.token_bytes(131072)
                with urllib.request.urlopen(urllib.request.Request(url, data=payload, headers=headers), timeout=15) as response:
                    self.assertEqual(response.read(), payload)
                self.websocket(url, headers)
                duplicate = docker('run', '--rm', '--env-file', env_file, '-v', f'{self.volume}:/data',
                                   IMAGE, '--name', variant, '--port', '8080', check=False)
                self.assertNotEqual(duplicate.returncode, 0)
                docker('stop', '--time', '10', helper)
                self.assertEqual(docker('inspect', '--format', '{{.State.ExitCode}}', helper).stdout.strip(), '0')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url, timeout=15)
                self.assertEqual(error.exception.code, 404)
                resumed = json.loads(self.prepare('--name', variant).stdout)
                self.assertEqual(resumed['url'], url)

    def websocket(self, url, headers):
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        with ssl.create_default_context().wrap_socket(socket.create_connection((parsed.hostname, 443), timeout=15), server_hostname=parsed.hostname) as connection:
            extra = ''.join(f'{name}: {value}\r\n' for name, value in headers.items())
            connection.sendall((f'GET {parsed.path}socket HTTP/1.1\r\nHost: {parsed.hostname}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n{extra}\r\n').encode())
            stream = connection.makefile('rb')
            self.assertIn(b' 101 ', stream.readline())
            while stream.readline() != b'\r\n':
                pass
            payload, mask = b'container-websocket-ok', secrets.token_bytes(4)
            connection.sendall(bytes([129, 128 + len(payload)]) + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))
            head = stream.read(2)
            self.assertEqual(head[0], 129)
            self.assertEqual(stream.read(head[1]), payload)


if __name__ == '__main__':
    unittest.main(verbosity=2)
