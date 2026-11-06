# Third-party software

The MIT license at the root applies to the helper code, documentation and examples.
It does not replace the licenses of the bundled software or its dependencies.

## frp

The image includes the unmodified `frpc` v0.72.0 executable from the official
`ghcr.io/fatedier/frpc` image, pinned by digest in the Dockerfile.

- Project: https://github.com/fatedier/frp
- Source release: https://github.com/fatedier/frp/tree/v0.72.0
- License: Apache License 2.0, https://github.com/fatedier/frp/blob/v0.72.0/LICENSE
- The Go modules declared by this release, their archive hashes and collected
  license/notice files are listed under `licenses/`. The collection includes
  declared modules which may not be used by a particular platform build.
- Go runtime license: https://go.dev/LICENSE

`tools/collect_licenses.py` refreshes the collected upstream documents for the
fixed release. Review its output when changing the frp version.

## Python, musl, OpenSSL, zlib and CA certificates

The image copies a minimal runtime from the official Python 3.13 Alpine image,
pinned by digest. The final scratch-based image contains Python, the musl loader,
OpenSSL and zlib libraries and the CA bundle. It does not redistribute Alpine's shell,
package manager, readline, gdbm or other unused distribution tools.

- Python license and incorporated software notices:
  https://docs.python.org/3.13/license.html
- Python source: https://www.python.org/downloads/source/
- Python container source: https://github.com/docker-library/python
- Alpine Linux packages, licenses and source/build recipes:
  https://pkgs.alpinelinux.org/ and https://gitlab.alpinelinux.org/alpine/aports
- musl libc: MIT, https://git.musl-libc.org/cgit/musl/tree/COPYRIGHT
- OpenSSL: Apache License 2.0, https://github.com/openssl/openssl/blob/openssl-3.5.9/LICENSE.txt
- zlib: zlib license, https://github.com/madler/zlib/blob/v1.3.2/LICENSE
- Mozilla CA data: MPL 2.0, https://www.mozilla.org/en-US/MPL/2.0/

These components retain their original copyright and license terms. Their
collected notices are under `licenses/runtime/`. Python's license is also present at
`/usr/local/lib/python3.13/LICENSE.txt`. The helper's collected notices are
available at `/usr/share/licenses/frp-tunnel-helper/` in the image.
