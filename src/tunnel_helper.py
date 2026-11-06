#!/usr/bin/env python3
"""Expose an HTTP service through an existing frp relay using a persistent URL."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile

ID_PATTERN = r'(?:[a-f0-9]{48}|[a-z0-9][a-z0-9_-]{0,63}-[a-f0-9]{12})'


def emit(event, **values):
    print(json.dumps(dict(event=event, **values)), flush=True)


def valid(pattern, value, label):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f'Invalid value for {label}.')
    return value


def atomic_write(path, values):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.share-')
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(values, stream)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--host', default='127.0.0.1', help='Application IP address or Docker service name')
    parser.add_argument('--port', type=int, help='Internal HTTP port of the application')
    parser.add_argument('--domain', default=os.environ.get('TUNNEL_DOMAIN'),
                        required=not os.environ.get('TUNNEL_DOMAIN'), help='Relay hostname; no built-in default')
    parser.add_argument('--state-dir', default=os.environ.get('TUNNEL_STATE_DIR', '/data'))
    parser.add_argument('--id', help='Existing readable ID or legacy 48-character hexadecimal ID')
    code = parser.add_mutually_exclusive_group()
    code.add_argument('--with-code', action='store_true', help='Generate and persist an optional access code')
    code.add_argument('--without-code', action='store_true', help='Explicitly omit or remove the access code')
    parser.add_argument('--prepare', action='store_true', help='Prepare the ID and URL without connecting')
    return parser.parse_args()


def state(args, directory):
    valid(r'[a-z0-9][a-z0-9_-]{0,63}', args.name, 'name')
    path = directory / f'share-{args.name}.json'
    saved = json.loads(path.read_text()) if path.exists() else {}
    if saved:
        valid(ID_PATTERN, saved.get('Id'), 'saved ID')
        if saved.get('Code'):
            valid(r'[a-f0-9]{32}', saved['Code'], 'saved access code')
    if args.id:
        valid(ID_PATTERN, args.id, 'id')
        if saved and saved['Id'] != args.id:
            raise ValueError('The ID conflicts with saved state. Use the saved ID or choose a new name.')
    port = args.port if args.port is not None else saved.get('Port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('An HTTP port between 1 and 65535 is required.')
    identifier = args.id or saved.get('Id') or f'{args.name}-{secrets.token_hex(6)}'
    code = None if args.without_code else saved.get('Code')
    if args.with_code and not code:
        code = secrets.token_hex(16)
    values = dict(Id=identifier, Port=port, Code=code)
    atomic_write(path, values)
    return values


def token():
    source = os.environ.get('TUNNEL_AUTH_TOKEN_FILE')
    value = Path(source).read_text().strip() if source else os.environ.get('TUNNEL_AUTH_TOKEN', '')
    return valid(r'[a-fA-F0-9]{64}', value, 'TUNNEL_AUTH_TOKEN or TUNNEL_AUTH_TOKEN_FILE')


def configuration(args, share):
    proxy = dict(name=share['Id'], type='http', localIP=args.host, localPort=share['Port'],
                 customDomains=[args.domain], locations=[f"/{share['Id']}/"],
                 hostHeaderRewrite=f"{args.host}:{share['Port']}",
                 requestHeaders=dict(set={'X-Forwarded-Prefix': f"/{share['Id']}"}))
    if share['Code']:
        proxy.update(httpUser='tester', httpPassword=share['Code'])
    return dict(serverAddr=args.domain, serverPort=443, loginFailExit=False,
                auth=dict(method='token', token='{{ .Envs.TUNNEL_AUTH_TOKEN }}',
                          additionalScopes=['HeartBeats', 'NewWorkConns']),
                transport=dict(protocol='wss', tls=dict(enable=True, useSystemCa=True)),
                log=dict(to='console', level='info', disablePrintColor=True), proxies=[proxy])


def run_client(args, share, access_token, result):
    environment = dict(os.environ, TUNNEL_AUTH_TOKEN=access_token)
    client = os.environ.get('FRPC_BIN', '/usr/local/bin/frpc')
    child = None
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True
        if child and child.poll() is None:
            child.send_signal(signum)

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with tempfile.TemporaryDirectory(prefix='frp-tunnel-helper-') as temporary:
            config = Path(temporary) / 'frpc.json'
            config.write_text(json.dumps(configuration(args, share)))
            verified = subprocess.run([client, 'verify', '-c', str(config)], env=environment,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if verified.returncode:
                raise ValueError('frpc rejected the generated configuration.')
            if stopping:
                return 0
            child = subprocess.Popen([client, '-c', str(config)], env=environment, text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if stopping:
                child.terminate()
            for line in child.stdout:
                # frpc v0.72.0 confirms registration, not application readiness.
                if 'start proxy success' in line:
                    emit('registered', **result)
                safe = line.replace(access_token, '[redacted]')
                if share['Code']:
                    safe = safe.replace(share['Code'], '[redacted]')
                print(safe, end='', file=sys.stderr, flush=True)
            returncode = child.wait()
            if not stopping:
                emit('stopped', exit_code=returncode)
            return 0 if stopping else (returncode if returncode > 0 else 1)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    args = arguments()
    valid(r'(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}',
          args.domain, 'domain')
    valid(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,252}', args.host, 'host')
    valid(r'[a-z0-9][a-z0-9_-]{0,63}', args.name, 'name')
    access_token = None if args.prepare else token()
    directory = Path(args.state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # Keep the lock open for the entire session; process exit releases it.
    with (directory / f'.{args.name}.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('This share is already running in the same state directory.') from None
        share = state(args, directory)
        result = dict(name=args.name, id=share['Id'], url=f"https://{args.domain}/{share['Id']}/",
                      base_path=f"/{share['Id']}", host=args.host, port=share['Port'],
                      requires_code=bool(share['Code']))
        emit('prepared', **result)
        if args.prepare:
            return 0
        return run_client(args, share, access_token, result)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f'frp-tunnel-helper: {error}', file=sys.stderr)
        sys.exit(2)
