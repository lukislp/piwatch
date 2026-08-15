"""Polls Flux CD resources for GitOps sync status and image-automation status:

- Kustomization (kustomize.toolkit.fluxcd.io) -- sync status, what it manages
- GitRepository (source.toolkit.fluxcd.io) -- is the git source itself healthy
- ImageRepository + ImagePolicy (image.toolkit.fluxcd.io) -- registry scan results
  and the tag each policy resolved to (joined by ImagePolicy.spec.imageRepositoryRef)
- ImageUpdateAutomation (image.toolkit.fluxcd.io) -- when automation last ran/pushed

Optional: Flux is not a hard dependency of PiWatch. Each resource kind is polled and
degraded independently -- e.g. a cluster with kustomize-controller and source-controller
but no image-automation-controller still shows Kustomizations/GitRepositories, instead of
one missing CRD blanking out everything. Each kind logs its unavailability once, then
retries quietly, instead of warning forever for what is a permanent, expected state on
most clusters (most piwatch users don't run Flux at all, let alone image automation).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from ..state import ClusterState

log = logging.getLogger("piwatch.flux")

POLL_INTERVAL = 15
RETRY_INTERVAL = 30
VERSION = "v1"
GROUP_KUSTOMIZE = "kustomize.toolkit.fluxcd.io"
GROUP_SOURCE = "source.toolkit.fluxcd.io"
GROUP_IMAGE = "image.toolkit.fluxcd.io"

_GO_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")
_GO_DURATION_UNIT_SECONDS = {
    "ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600,
}


def _parse_go_duration(s: str | None) -> float | None:
    """spec.interval is a Go time.Duration string (e.g. "5m", "1h30m") -- not
    ISO 8601. Sums each (number, unit) pair found; None if nothing matched."""
    if not s:
        return None
    matches = _GO_DURATION_RE.findall(s)
    if not matches:
        return None
    return sum(float(value) * _GO_DURATION_UNIT_SECONDS[unit] for value, unit in matches)


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _ready_condition(status: dict) -> dict:
    conditions = status.get("conditions") or []
    return next((c for c in conditions if c.get("type") == "Ready"), {})


def _map_kustomization(item: dict) -> dict:
    meta = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    ready_cond = _ready_condition(status)
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")

    # history[0].lastReconciled updates on every successful reconcile attempt (even when
    # nothing changed); the Ready condition's lastTransitionTime only updates when the
    # Ready status itself flips, which under-counts reconciles that stayed healthy.
    history = status.get("history") or []
    last_reconciled = history[0].get("lastReconciled") if history else ready_cond.get("lastTransitionTime")
    last_reconciled_t = _parse_iso(last_reconciled)
    interval_s = _parse_go_duration(spec.get("interval"))
    next_reconcile_t = (
        last_reconciled_t + interval_s if last_reconciled_t is not None and interval_s is not None else None
    )

    last_applied = status.get("lastAppliedRevision")
    last_attempted = status.get("lastAttemptedRevision")
    source_ref = spec.get("sourceRef") or {}
    inventory = status.get("inventory") or {}

    return {
        "key": f"{namespace}/{name}",
        "name": name,
        "namespace": namespace,
        "ready": ready_cond.get("status") == "True",
        "reason": ready_cond.get("reason"),
        "message": ready_cond.get("message"),
        "last_applied_revision": last_applied,
        "last_transition_time": ready_cond.get("lastTransitionTime"),
        "next_reconcile_t": next_reconcile_t,
        "managed_resource_count": len(inventory.get("entries") or []),
        # Both fields get set to the same value on every successful reconcile -- they
        # only differ while an apply attempt is in flight, or stuck failing after a
        # prior success (the Ready condition alone wouldn't catch that second case).
        "apply_pending": bool(last_attempted and last_applied and last_attempted != last_applied),
        "source_kind": source_ref.get("kind"),
        "source_name": source_ref.get("name"),
        "source_namespace": source_ref.get("namespace") or namespace,
    }


def _map_git_repository(item: dict) -> dict:
    meta = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    ready_cond = _ready_condition(status)
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")
    artifact = status.get("artifact") or {}
    ref = spec.get("ref") or {}
    return {
        "key": f"{namespace}/{name}",
        "name": name,
        "namespace": namespace,
        "ready": ready_cond.get("status") == "True",
        "reason": ready_cond.get("reason"),
        "message": ready_cond.get("message"),
        "url": spec.get("url"),
        "ref": ref.get("branch") or ref.get("tag") or ref.get("semver") or ref.get("commit"),
        "revision": artifact.get("revision"),
        "last_update_time": artifact.get("lastUpdateTime"),
    }


def _map_image_policy(item: dict, scan_by_repo: dict[str, dict]) -> dict:
    meta = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    ready_cond = _ready_condition(status)
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")
    latest = status.get("latestRef") or {}
    previous = status.get("observedPreviousRef") or {}
    repo_name = (spec.get("imageRepositoryRef") or {}).get("name")
    # ImagePolicy explicitly references its ImageRepository by name (same namespace,
    # standard Flux convention) -- an actual spec link, not a naming-convention guess.
    scan = scan_by_repo.get(f"{namespace}/{repo_name}", {}) if repo_name else {}
    return {
        "key": f"{namespace}/{name}",
        "name": name,
        "namespace": namespace,
        "ready": ready_cond.get("status") == "True",
        "image": latest.get("name") or repo_name,
        "latest_tag": latest.get("tag"),
        "previous_tag": previous.get("tag"),
        "tag_count": scan.get("tag_count"),
        "last_scan_time": scan.get("scan_time"),
    }


def _map_image_update_automation(item: dict) -> dict:
    meta = item.get("metadata", {})
    status = item.get("status", {})
    ready_cond = _ready_condition(status)
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")
    return {
        "key": f"{namespace}/{name}",
        "name": name,
        "namespace": namespace,
        "ready": ready_cond.get("status") == "True",
        "reason": ready_cond.get("reason"),
        "message": ready_cond.get("message"),
        "last_automation_run_time": status.get("lastAutomationRunTime"),
        "last_push_commit": status.get("lastPushCommit"),
        "last_push_time": status.get("lastPushTime"),
    }


async def _list(custom, group: str, plural: str) -> list[dict]:
    result = await custom.list_cluster_custom_object(group, VERSION, plural)
    return result.get("items", [])


async def run(state: ClusterState):
    from kubernetes_asyncio import client

    warned: dict[str, bool] = {}

    def warn_once(kind: str, exc: Exception) -> None:
        if not warned.get(kind):
            log.info(
                "Flux %s unavailable (%s) -- not installed, or RBAC missing; this is "
                "expected if you don't run Flux (or don't use image automation). "
                "Retrying quietly.",
                kind, exc,
            )
            warned[kind] = True

    while True:
        try:
            async with client.ApiClient() as api_client:
                custom = client.CustomObjectsApi(api_client)
                while True:
                    try:
                        items = await _list(custom, GROUP_KUSTOMIZE, "kustomizations")
                        mapped = {}
                        for item in items:
                            m = _map_kustomization(item)
                            mapped[m["key"]] = m
                        state.set_flux_kustomizations(mapped)
                        warned["kustomizations"] = False
                    except Exception as exc:
                        warn_once("kustomizations", exc)

                    try:
                        items = await _list(custom, GROUP_SOURCE, "gitrepositories")
                        mapped = {}
                        for item in items:
                            m = _map_git_repository(item)
                            mapped[m["key"]] = m
                        state.set_flux_git_repositories(mapped)
                        warned["gitrepositories"] = False
                    except Exception as exc:
                        warn_once("gitrepositories", exc)

                    scan_by_repo: dict[str, dict] = {}
                    try:
                        repo_items = await _list(custom, GROUP_IMAGE, "imagerepositories")
                        for item in repo_items:
                            meta = item.get("metadata", {})
                            key = f"{meta.get('namespace', '')}/{meta.get('name', '')}"
                            scan = (item.get("status") or {}).get("lastScanResult") or {}
                            scan_by_repo[key] = {
                                "tag_count": scan.get("tagCount"),
                                "scan_time": scan.get("scanTime"),
                            }
                        warned["imagerepositories"] = False
                    except Exception as exc:
                        warn_once("imagerepositories", exc)

                    try:
                        items = await _list(custom, GROUP_IMAGE, "imagepolicies")
                        mapped = {}
                        for item in items:
                            m = _map_image_policy(item, scan_by_repo)
                            mapped[m["key"]] = m
                        state.set_flux_image_policies(mapped)
                        warned["imagepolicies"] = False
                    except Exception as exc:
                        warn_once("imagepolicies", exc)

                    try:
                        items = await _list(custom, GROUP_IMAGE, "imageupdateautomations")
                        mapped = {}
                        for item in items:
                            m = _map_image_update_automation(item)
                            mapped[m["key"]] = m
                        state.set_flux_image_automations(mapped)
                        warned["imageupdateautomations"] = False
                    except Exception as exc:
                        warn_once("imageupdateautomations", exc)

                    await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            warn_once("flux", exc)
            await asyncio.sleep(RETRY_INTERVAL)
