"""Collect upstream license/notice files from frp's pinned Go module archives."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'v0.72.0'


def download(url):
    with urllib.request.urlopen(url, timeout=90) as response:
        return response.read()


def escaped(value):
    return ''.join('!' + character.lower() if character.isupper() else character for character in value)


def collect(item):
    module, version = item
    url = f'https://proxy.golang.org/{escaped(module)}/@v/{escaped(version)}.zip'
    content = download(url)
    target = ROOT / 'licenses/modules' / (module.replace('/', '_') + '@' + version)
    count = 0
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for member in archive.infolist():
            basename = member.filename.rsplit('/', 1)[-1]
            if not re.fullmatch(r'(?:LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)(?:\.[A-Za-z0-9_-]+)?', basename, flags=re.I):
                continue
            relative = member.filename.split('/', 1)[1] if '/' in member.filename else basename
            # Module prefixes can contain slashes. Keep the upstream path but flatten separators.
            destination = target / relative.replace('/', '__')
            target.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(member))
            count += 1
    if not count:
        raise RuntimeError(f'No license or notice found for {module}@{version}')
    return dict(module=module, version=version, archive=url,
                archive_sha256=hashlib.sha256(content).hexdigest(), notice_files=count)


def main():
    module_file = download(f'https://raw.githubusercontent.com/fatedier/frp/{VERSION}/go.mod').decode()
    modules = re.findall(r'^\s*([^\s]+)\s+(v[^\s]+)', module_file, flags=re.M)
    modules.append(('github.com/fatedier/frp', VERSION))
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = sorted(executor.map(collect, modules), key=lambda item: item['module'])
    (ROOT / 'licenses/MODULES.json').write_text(json.dumps(results, indent=2) + '\n')
    print(f'Collected license/notice files from {len(results)} pinned upstream modules.')


if __name__ == '__main__':
    main()
