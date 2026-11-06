# frp-tunnel-helper

Expose an HTTP service from a Linux container through an existing self-hosted
[frp](https://github.com/fatedier/frp) relay. Get a persistent, readable HTTPS URL
without publishing an application port on the Docker host.

```text
https://tunnel.example.com/my-app-7c92ab48f103/
```

The helper creates the share ID, starts `frpc`, forwards termination signals and
prints JSON events. It is a client helper, not a hosted tunnel service. Bring
your own compatible relay, hostname and authentication token. No provider
address or credentials are built into the image.

## Requirements

- A Linux Docker host. The initial image is published for `linux/amd64`.
- An existing frp v0.72.0 relay accepting WSS on HTTPS port 443. A reverse proxy
  must forward `/~!frp` to the frps control port and other paths to its HTTP
  virtual-host port. HTTPS needs a certificate trusted by the client.
- A shared relay token containing 64 hexadecimal characters.
- An HTTP application that supports a URL base path. The complete `/<share-id>/`
  prefix reaches the application unchanged.

The image contains a minimal Python runtime and frpc. It has no shell, package
manager or pip; use `--entrypoint python` for intentional diagnostics.

No Docker socket, privileged container, TUN device, additional Linux capabilities
or host port mapping is required by the helper. Running Docker on the host
still requires the usual Docker permissions.

## Quick start

Pull the image and create a state volume:

```sh
docker pull oibio/frp-tunnel-helper:0.3.0
docker volume create my-app-tunnel-state
```

Prepare a share before configuring the application's base path:

```sh
docker run --rm --read-only --tmpfs /tmp --cap-drop ALL \
  -v my-app-tunnel-state:/data \
  oibio/frp-tunnel-helper:0.3.0 \
  --domain tunnel.example.com --name my-app --host app --port 8080 --prepare
```

This prints a JSON line with `event: prepared`, `url` and `base_path`. It does
not connect to the relay. Configure the app to serve beneath the returned base
path and test that path locally.

Create a local `tunnel.env` containing your real values:

```dotenv
TUNNEL_DOMAIN=tunnel.example.com
TUNNEL_AUTH_TOKEN=YOUR_EXISTING_64_CHARACTER_HEXADECIMAL_RELAY_TOKEN
```

Keep that file private. Then start the helper in the same Docker network as
your application. Replace the example network, service name and internal port:

```sh
docker run -d --name my-app-tunnel --restart unless-stopped \
  --network myproject_default --read-only --tmpfs /tmp --cap-drop ALL \
  --env-file ./tunnel.env -v my-app-tunnel-state:/data \
  oibio/frp-tunnel-helper:0.3.0 --name my-app --host app --port 8080

docker logs my-app-tunnel
```

Wait for `event: registered`, then open and test the public URL. Registration
confirms the proxy was accepted by frp; it does not check your application.
Docker may display the helper's JSON output and frpc diagnostic output together.

The application must listen on its container network interface for this setup,
usually `0.0.0.0`. The helper connects to the internal container port, not a host
port. Its default host `127.0.0.1` refers to the helper's own network namespace.

## Application listens on localhost

Share the application's network namespace instead:

```sh
docker run -d --name my-app-tunnel \
  --network container:my-app-container --read-only --tmpfs /tmp --cap-drop ALL \
  --env-file ./tunnel.env -v my-app-tunnel-state:/data \
  oibio/frp-tunnel-helper:0.3.0 --name my-app --host 127.0.0.1 --port 8080
```

Recreate the helper against the new application container when replacing that
container. In Compose, the equivalent setting is `network_mode: service:app`.

## Docker Compose

Merge `compose.example.yml` with your application's Compose file. Set
`TUNNEL_DOMAIN` and `TUNNEL_TOKEN_FILE`, where the latter names an absolute path
to a private file containing only the relay token. Both services need a common
network; the example uses the Compose default network and the service name `app`.

```sh
docker compose -f compose.yml -f compose.example.yml \
  run --rm --no-deps tunnel --domain tunnel.example.com --name my-app --host app --port 8080 --prepare
docker compose -f compose.yml -f compose.example.yml up -d tunnel
```

The example uses a file-based Compose secret and a named state volume. Keep the
same Compose project name to retain the same volume and URL.

## IDs, access codes and state

New IDs contain the share name and 12 cryptographically random hexadecimal
characters. A share named `my-app` gets an ID such as `my-app-7c92ab48f103`.
The ID is also the frp proxy name, making it recognizable in the relay dashboard.

The state file `/data/share-my-app.json` stores `Id`, `Port` and `Code`. Preserve
the volume to keep the same URL across restarts. Existing 48-character hexadecimal
IDs remain supported. `--id` imports a known ID; it rejects a conflict with saved
state rather than silently replacing an existing link.

An access code is optional and enabled only with `--with-code`. It is persistent,
shared by all testers of that share and uses HTTP Basic Auth with username
`tester`. It is not a one-time or email code. The browser may remember it.
The code is stored in the private state file and is not printed in logs.

```sh
# Run this with the same volume and other settings used for the share:
docker exec my-app-tunnel python -c \
  'import json; print(json.load(open("/data/share-my-app.json"))["Code"])'
```

Use that command only when intentionally retrieving a code for its recipients.
`--without-code` explicitly removes a stored code. Basic Auth may conflict with
an application that uses the `Authorization` request header itself.

## Configuration

| Setting | Meaning |
| --- | --- |
| `--name` | Required stable share name, up to 64 lowercase letters, digits, `_` or `-`; starts with a letter or digit. |
| `--host` | Application hostname or IPv4 address; defaults to `127.0.0.1`. |
| `--port` | Internal application HTTP port; required on first use, then remembered. |
| `--domain` / `TUNNEL_DOMAIN` | Required relay hostname, without scheme, port or path. No default. |
| `--state-dir` / `TUNNEL_STATE_DIR` | State directory; defaults to `/data`. |
| `TUNNEL_AUTH_TOKEN` | Existing 64-character hexadecimal relay token. |
| `TUNNEL_AUTH_TOKEN_FILE` | Read the token from this file instead of the environment variable. |
| `--prepare` | Save state and print the URL without connecting; no token required. |
| `--with-code` | Generate an optional persistent access code. |
| `--without-code` | Explicitly remove or omit an access code. |
| `--id` | Import an existing readable ID or legacy hexadecimal ID. |

Stdout contains JSON events. Stderr contains frpc diagnostics. An unexpected
client exit also terminates the helper; a container restart policy can restart it.
`docker stop my-app-tunnel` closes the share. `docker start my-app-tunnel` resumes
it. The relay does not reserve an offline ID exclusively.

The image runs as UID/GID `65534:65534`. Named volumes are initialized with the
image's writable `/data` directory. For bind mounts, arrange directory permissions
for the selected user. A writable temporary directory is also required.

## URL base path and trust boundaries

The application receives the full `/<share-id>/...` path and an
`X-Forwarded-Prefix` header. The helper does not strip this path or rewrite HTML,
JavaScript, redirects or WebSocket URLs. Root-absolute URLs such as `/api/items`
escape the share. Configure your framework's base path and test real navigation,
assets, authentication and APIs.

Shares under one hostname have the same browser origin. Use them for trusted
applications and account for cookies, local storage and service workers. Without
an access code or application login, anyone holding the URL can access the app.
Runtime secrets and state must stay outside the image and source repository.
Environment variables can be visible to Docker administrators; mount a token
file when that better fits your deployment.

## Run inside an existing application image

You can copy `/usr/local/bin/tunnel-helper` and `/usr/local/bin/frpc` into a
compatible Linux application image. It needs Python 3, system CA certificates
and a matching CPU architecture. Supply the domain and token at runtime and
provide persistent writable state. When the helper and app run in one container,
use a process supervisor that handles both lifecycles. A separate helper container
usually needs fewer changes to the app image.

## Build and test

```sh
docker build -t oibio/frp-tunnel-helper:0.3.0 .
python tests/test_helper.py
```

Offline Docker tests check IDs, persistence, optional codes, legacy state and
invalid inputs. Set `TUNNEL_TEST_ENV_FILE` to a private environment file to enable
live tests against your own compatible relay. These publish only a short-lived
dummy app and remove their test containers, network and volume afterward.

## License and upstream projects

The helper is licensed under MIT. Bundled components retain their own licenses.
See `LICENSE` and `THIRD_PARTY_NOTICES.md`, also included in the image under
`/usr/share/licenses/frp-tunnel-helper/`.

- [frp](https://github.com/fatedier/frp) — [Apache License 2.0](https://github.com/fatedier/frp/blob/v0.72.0/LICENSE)
- [Python](https://www.python.org/) — [PSF license and incorporated software notices](https://docs.python.org/3.13/license.html)
- [Alpine Linux](https://alpinelinux.org/) — [package licenses and sources](https://pkgs.alpinelinux.org/)
- [Go runtime](https://go.dev/) — [BSD-style license](https://go.dev/LICENSE)

This helper is an independent project and is not an official frp image.
