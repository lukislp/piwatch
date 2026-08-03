"""WebSocket layer.

/ws                      -> full state snapshot, then live delta messages
/ws/logs/{ns}/{pod}      -> live container log stream (follow)

Auth: token as ?token= query parameter (same HMAC token as the REST API).
The frontend reconnects automatically -- after a node failover it lands on
the surviving replica and immediately receives a fresh full_state.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from .auth import verify_token
from .state import state

log = logging.getLogger("piwatch.ws")

router = APIRouter()

HEARTBEAT_S = 20


@router.websocket("/ws")
async def ws_state(websocket: WebSocket, token: str | None = Query(default=None)):
    # accept first, then close with app-level code 4401 -- a pre-accept close
    # would surface as HTTP 403 and the client couldn't distinguish
    # "unauthorized" (-> show login) from a network error (-> retry).
    await websocket.accept()
    if not verify_token(token):
        await websocket.close(code=4401, reason="unauthorized")
        return
    q = state.subscribe()
    try:
        await websocket.send_text(
            json.dumps({"type": "full_state", "data": state.snapshot()})
        )
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                await websocket.send_text(json.dumps(msg))
            except asyncio.TimeoutError:
                # Heartbeat keeps proxies (Traefik) from closing idle streams
                await websocket.send_text(json.dumps({"type": "ping"}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        state.unsubscribe(q)


async def _k8s_log_lines(namespace: str, pod: str, container: str | None):
    """Async generator over live log lines from the Kubernetes API."""
    from kubernetes_asyncio import client

    async with client.ApiClient() as api_client:
        v1 = client.CoreV1Api(api_client)
        resp = await v1.read_namespaced_pod_log(
            name=pod,
            namespace=namespace,
            container=container,
            follow=True,
            tail_lines=200,
            _preload_content=False,
        )
        try:
            async for raw in resp.content:
                yield raw.decode("utf-8", "replace").rstrip("\n")
        finally:
            resp.close()


@router.websocket("/ws/logs/{namespace}/{pod}")
async def ws_logs(
    websocket: WebSocket,
    namespace: str,
    pod: str,
    token: str | None = Query(default=None),
    container: str | None = Query(default=None),
):
    await websocket.accept()
    if not verify_token(token):
        await websocket.close(code=4401, reason="unauthorized")
        return

    if state.demo_mode:
        from .collectors.demo import fake_logs

        source = fake_logs(namespace, pod)
    else:
        source = _k8s_log_lines(namespace, pod, container)

    async def sender():
        try:
            async for line in source:
                await websocket.send_text(json.dumps({"type": "log", "line": line}))
        except Exception as exc:
            with contextlib.suppress(Exception):
                await websocket.send_text(
                    json.dumps({"type": "log_error", "error": str(exc)})
                )

    send_task = asyncio.create_task(sender())
    try:
        # Drain client messages so we notice a disconnect promptly.
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await send_task
