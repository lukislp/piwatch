# 📡 PiWatch – k3s monitoring dashboard for Raspberry Pi

[![CI/CD](https://github.com/lukislp/piwatch/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/lukislp/piwatch/actions/workflows/ci-cd.yml)
[![Release](https://img.shields.io/github/v/release/lukislp/piwatch)](https://github.com/lukislp/piwatch/releases)
[![License: MIT](https://img.shields.io/github/license/lukislp/piwatch)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB)](https://www.python.org/)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lukislp/piwatch/master/.github/badges/coverage.json)](https://github.com/lukislp/piwatch/actions/workflows/ci-cd.yml)

Self-hosted real-time monitoring for a k3s cluster: **FastAPI backend** +
**React frontend** in a single container, live updates over **WebSocket**,
highly available with **2 replicas** (a node goes down ⇒ the second replica
takes over, the frontend reconnects automatically).

**[Live demo](https://piwatch-demo.lktec.org)** — running the actual
`ghcr.io/lukislp/piwatch:latest` image published by this repo's own CI/CD pipeline, in its
built-in demo mode (`PIWATCH_DEMO=1`): a simulated 3-node Raspberry Pi cluster with
live-changing CPU/memory/temperature/disk metrics, pods, deployments, and events - no real
cluster involved.

![PiWatch overview dashboard, live demo screenshot](docs/screenshot.png)

## Features

- **Kubernetes live**: nodes, pods, deployments, events via the watch API (no polling)
- **Per-pod CPU/RAM usage**: live usage per pod/workload via metrics-server, right in the Workloads table
- **Pi hardware**: CPU temperature, load, RAM, disk, uptime per node (DaemonSet agent)
- **NVMe + power health**: dedicated NVMe tab per node -- temperature, model, firmware,
  serial, capacity, wear %/spare capacity, power-on hours, power cycles, unsafe shutdowns,
  total data read/written, host command counts, error counts, and live read/write
  throughput charts. Plus the Pi firmware's under-voltage (bad PSU/PoE) flag, surfaced as a
  plain OK/error indicator on the Overview page
- **Metrics**: CPU/RAM usage via metrics-server (bundled with k3s), ~3h history
- **Network throughput** per node (RX/TX), summed across physical interfaces only
- **GitOps sync status** (optional): if you run [Flux](https://fluxcd.io/), a "GitOps" card on the
  Overview page shows each Kustomization's Ready condition and last applied revision. Not a hard
  dependency -- stays hidden if Flux isn't installed
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

Before deploying, adjust the hostnames in `deploy/httproute.yaml` to point at
your own domains.

1. **Image**: the manifests in `deploy/` reference `registry.example.com/your-namespace/piwatch:latest`
   as a placeholder — for a stock, unmodified deployment, just point them at
   the multi-arch image this repo's own CI/CD pipeline already builds and
   publishes on every release, `ghcr.io/lukislp/piwatch:latest` (or a pinned
   `:X.Y.Z` version tag). You only need to build your own image if you've
   forked or modified the code:
   ```bash
   docker buildx build --platform linux/arm64,linux/amd64 \
     -t your-registry/your-namespace/piwatch:latest --push .
   ```
   Either way, running pods need a restart to pull a newly-pushed `:latest`
   (`imagePullPolicy: Always` picks it up automatically, but won't force a
   restart on its own): `kubectl -n monitoring rollout restart
   deployment/piwatch daemonset/piwatch-node-agent`.

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

## NVMe SMART needs a privileged node-agent

The NVMe temperature, model and capacity fields work from plain, unprivileged sysfs
reads. Full SMART data (wear %, power-on hours, media errors, ...) additionally needs
`nvme-cli` and access to the NVMe admin character device -- that ioctl is gated by the
kernel regardless of file permissions, so `deploy/daemonset-node-agent.yaml` runs the
node-agent container with `privileged: true` and a `/dev` mount for this. That's a
materially bigger privilege footprint than the rest of the agent (everything else is
read-only sysfs/procfs). If you'd rather not grant it, remove `privileged: true` and the
`dev` volume/mount -- temperature, model, capacity and the under-voltage flag keep
working, only the SMART fields go missing.

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
