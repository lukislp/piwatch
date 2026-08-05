# 📡 PiWatch – k3s monitoring dashboard for Raspberry Pi

[![CI/CD](https://github.com/lukislp/piwatch/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/lukislp/piwatch/actions/workflows/ci-cd.yml)
[![Release](https://img.shields.io/github/v/release/lukislp/piwatch)](https://github.com/lukislp/piwatch/releases)
[![License: MIT](https://img.shields.io/github/license/lukislp/piwatch)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB)](https://www.python.org/)

Self-hosted real-time monitoring for a k3s cluster: **FastAPI backend** +
**React frontend** in a single container, live updates over **WebSocket**,
highly available with **2 replicas** (a node goes down ⇒ the second replica
takes over, the frontend reconnects automatically).

## Features

- **Kubernetes live**: nodes, pods, deployments, events via the watch API (no polling)
- **Pi hardware**: CPU temperature, load, RAM, disk, uptime per node (DaemonSet agent)
- **Metrics**: CPU/RAM usage via metrics-server (bundled with k3s), ~3h history
- **HTTP/TCP healthchecks** for your own services (Home Assistant, MQTT, …) with uptime history
- **Live logs** for any pod, right in the browser
- **Simple auth**: password from a Kubernetes Secret, signed tokens (failover-friendly)
- **Dark/light mode**

## Architecture

```
Browser ⇄ WSS ⇄ Gateway/Ingress ⇄ Service ⇄ 2× piwatch pod (anti-affinity)
                                                │ FastAPI + React static files
                                                │ Watcher/poller/checks (independent per replica)
                              DaemonSet node-agent (1/Pi, /sys + /proc read-only)
```

Each replica keeps its own state in RAM (stateless externally, no shared
storage, no leader election). Tokens from both replicas are interchangeable
because both use the same Secret.

## Try it locally (no cluster needed)

```bash
cd backend && pip install -r requirements.txt
PIWATCH_DEMO=1 uvicorn app.main:app --port 8000
# Frontend dev server (optional, with proxy):
cd ../frontend && npm install && npm run dev
```

Without `PIWATCH_PASSWORD`, login is disabled.

## Deploying to your own cluster

The manifests in `deploy/` are written for a 2-node k3s cluster and route
through the [Gateway API](https://gateway-api.sigs.k8s.io/) (`httproute.yaml`,
tested with NGINX Gateway Fabric). If you're on ingress-nginx or another
controller, swap `httproute.yaml`/`RateLimitPolicy` for an `Ingress` resource
and adjust `deploy/kustomization.yaml` accordingly.

Before deploying, adjust the image references in `deploy/deployment.yaml` and
`deploy/daemonset-node-agent.yaml` (currently `registry.example.com/your-namespace/piwatch:latest`)
and the hostnames in `deploy/httproute.yaml` to point at your own registry and domains.

1. **Build & push the image** (multi-arch, from your PC — no CI job for the
   build, a manual push per change):
   ```bash
   docker buildx build --platform linux/arm64,linux/amd64 \
     -t registry.example.com/your-namespace/piwatch:latest --push .
   ```
   Repeat this command on every code change (the tag stays "latest" —
   `imagePullPolicy: Always` picks it up automatically, but running pods
   still need a manual restart to pull it: `kubectl -n monitoring rollout
   restart deployment/piwatch daemonset/piwatch-node-agent`).

2. **Create the Secret** — `.\create-secret.ps1` (interactively asks only for
   the login password, generates the signing key automatically at random,
   creates the Secret directly on the cluster via `kubectl` — it never
   touches disk as a plaintext file). The alternative, file-based approach
   (`cp deploy/secret.example.yaml deploy/secret.yaml` + uncomment the
   `- secret.yaml` line in `deploy/kustomization.yaml`) still works but is
   **not** recommended, since the Secret then sits unencrypted on disk. For a
   Secret that's safe to commit to Git, seal it with
   [Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets) instead
   (see the comment in `deploy/kustomization.yaml`).

3. **Roll it out** (point `KUBECONFIG` at your cluster's kubeconfig):
   ```bash
   kubectl apply -k deploy/
   kubectl -n monitoring get pods -o wide   # 2× piwatch across 2 nodes + node-agents
   ```

4. **Access**: whichever hostnames you configured in `deploy/httproute.yaml`
   (e.g. an internal `.lan` name plus a public domain behind your reverse proxy).

## Testing failover

```bash
kubectl drain <node-running-a-piwatch-pod> --ignore-daemonsets --delete-emptydir-data
```

The dashboard stays reachable: the browser reconnects automatically and gets
the full state from the surviving replica. Afterwards:
`kubectl uncordon <node>`.

## Tests

```bash
cd backend && python -m pytest tests/
```

## Configuration (environment variables)

| Variable | Meaning | Default |
|---|---|---|
| `PIWATCH_PASSWORD` | Login password (empty = auth disabled) | – |
| `PIWATCH_SECRET` | Token signing key (must match across replicas) | derived |
| `PIWATCH_TOKEN_TTL` | Token validity in seconds | 43200 |
| `PIWATCH_DEMO` | `1` = demo mode with fake data | – |
| `PIWATCH_CHECKS_FILE` | Path to the healthcheck YAML | `/config/healthchecks.yaml` |
| `PIWATCH_AGENT_SERVICE` | Headless service for the node-agents | `piwatch-node-agent.monitoring…` |

## Deliberate simplifications

- History data lives only in RAM (~3h); it resets on pod restart.
  Possible extension: SQLite/PVC or Prometheus.
- No alerting (push/mail) — the pub/sub structure is prepared for it.

## License

[MIT](LICENSE)
