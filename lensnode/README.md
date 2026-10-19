# LensNode

LensNode is the standalone execution worker for SourceLens. It connects to the
control plane over WebSocket, reports workspace directories and available tasks,
then executes dispatched runs inside its local workspace.

## Command execution

The default `LENSNODE_EXECUTION_BACKEND=trusted_container` enables the
DeepAgents `execute` tool. Commands run directly inside the LensNode container,
with the container's environment and the current Run workspace as the working
directory.
There is no command blacklist and this mode is not an additional sandbox; do
not expose the container to untrusted users. Set the backend to `filesystem`
to roll back to file operations only while a sandbox provider is prepared.

## Development

```bash
docker compose up --build
```

The standalone compose file only starts the LensNode service. Set
`LENSNODE_SERVER_URL` to the existing SourceLens backend; LensNode derives the
control WebSocket, AI Gateway, deliverable upload, and delegation endpoints.
The individual endpoint variables remain available for non-standard routes.
In the root blue/green stack, use `http://nginx:80`; in the development and
standalone stacks, use `http://backend-api:8000`. See [env.sample](../env.sample)
for deployment inputs and [config.py](lensnode/config.py) for runtime defaults.

## TLS verification

LensNode verifies SourceLens HTTPS and WSS certificates with the system trust
store by default. This applies to the control channel, AI Gateway requests,
Skill package downloads, and deliverable uploads.

For a SourceLens deployment that uses a private CA, mount the CA certificate
into the LensNode container and configure its container path:

```yaml
services:
  lensnode:
    environment:
      LENSNODE_TLS_CA_FILE: /etc/sourcelens/ca.crt
    volumes:
      - ./ca.crt:/etc/sourcelens/ca.crt:ro
```

For local development with a self-signed certificate, verification can be
disabled explicitly:

```env
LENSNODE_TLS_SKIP_VERIFY=true
```

This disables both certificate and hostname verification and must not be used
in production. `LENSNODE_TLS_SKIP_VERIFY` defaults to `false`. When it is
`true`, it takes precedence over `LENSNODE_TLS_CA_FILE` and LensNode logs a
warning at startup.
