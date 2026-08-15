"""PiWatch backend entry point.

Serves the REST API + WebSockets and the built React frontend as static
files. On startup, the lifespan launches the collectors:

- real mode: Kubernetes watchers, metrics-server poller, hardware poller,
  Flux Kustomization poller (optional -- degrades quietly if Flux isn't installed),
  PVC poller (usage % additionally needs PIWATCH_PROMETHEUS_URL), Gateway API poller,
  auto-healthchecks from discovered HTTPRoutes/Services (opt-in via
  PIWATCH_AUTO_HEALTHCHECKS), node-history persistence to survive a restart (opt-in via
  PIWATCH_HISTORY_DB)
- demo mode (PIWATCH_DEMO=1 or no cluster reachable): simulator

Run locally:  PIWATCH_DEMO=1 uvicorn app.main:app --reload
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, ws
from .collectors import (
    autochecks,
    demo,
    flux,
    gateway,
    hardware,
    healthcheck,
    history,
    k8s_watch,
    metrics,
    pvc,
)
from .state import state

logging.basicConfig(level=os.environ.get("PIWATCH_LOG_LEVEL", "INFO"))
log = logging.getLogger("piwatch")

STATIC_DIR = os.environ.get("PIWATCH_STATIC_DIR", "/app/static")


def _demo_requested() -> bool:
    return os.environ.get("PIWATCH_DEMO", "") in ("1", "true", "yes")


async def _cluster_reachable() -> bool:
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    try:
        from kubernetes_asyncio import config

        await config.load_kube_config()
        return True
    except Exception:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks: list[asyncio.Task] = []
    use_demo = _demo_requested() or not await _cluster_reachable()
    if use_demo:
        log.info("Starting in DEMO mode (simulated cluster data)")
        state.demo_mode = True
        tasks.append(asyncio.create_task(demo.run(state)))
    else:
        log.info("Starting in cluster mode")
        history.load_startup_history(state)
        tasks.append(asyncio.create_task(k8s_watch.run(state)))
        tasks.append(asyncio.create_task(metrics.run(state)))
        tasks.append(asyncio.create_task(hardware.run(state)))
        tasks.append(asyncio.create_task(flux.run(state)))
        tasks.append(asyncio.create_task(pvc.run(state)))
        tasks.append(asyncio.create_task(gateway.run(state)))
        tasks.append(asyncio.create_task(autochecks.run(state)))
        tasks.append(asyncio.create_task(history.run(state)))
    tasks.append(asyncio.create_task(healthcheck.run(state)))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


app = FastAPI(title="PiWatch", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(ws.router)


# ---------------- REST ----------------

@app.get("/api/state", dependencies=[Depends(auth.require_auth)])
def get_state():
    return JSONResponse(state.snapshot())


@app.get("/healthz")
def healthz():
    """Liveness: process is up."""
    return {"ok": True}


@app.get("/readyz")
def readyz():
    """Readiness: collectors have produced at least one node."""
    ready = bool(state.nodes) or state.demo_mode
    return JSONResponse({"ready": ready}, status_code=200 if ready else 503)


# ---------------- static frontend ----------------

if os.path.isdir(STATIC_DIR):
    app.mount(
        "/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets"
    )

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """SPA fallback: everything that is not /api or /ws gets index.html.

        The candidate path is resolved and checked against STATIC_DIR so a
        crafted '../..'-path can never escape the static directory.
        """
        base = os.path.realpath(STATIC_DIR)
        candidate = os.path.realpath(os.path.join(base, full_path))
        inside = candidate.startswith(base + os.sep)
        if full_path and inside and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(base, "index.html"))
else:
    log.warning("Static directory %s missing -- API only", STATIC_DIR)
