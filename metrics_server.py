# -*- coding: utf-8 -*-
"""Read-only FastAPI metrics server for supervisor polling.

Port default: 9100
Endpoints are READ ONLY — no mutation endpoints exist.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from shared.keyring_loader import get_credential
from shared.platform.config import load_platform_config
from shared.platform.lifecycle import validate_startup
from shared.platform.metrics_state import MetricsState
from shared.setup_paths import activate

activate()

logger = logging.getLogger("metrics_server")

app = FastAPI(title="RazAgent_Trader Metrics", docs_url=None, redoc_url=None)
_metrics = MetricsState()


def _get_bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _client_allowed(request: Request, allowed: list[str]) -> bool:
    client = request.client.host if request.client else "127.0.0.1"
    for cidr in allowed:
        try:
            if "/" in cidr:
                if ipaddress.ip_address(client) in ipaddress.ip_network(cidr, strict=False):
                    return True
            elif client == cidr:
                return True
        except ValueError:
            continue
    return client in {"127.0.0.1", "::1"}


def require_auth(request: Request) -> None:
    config = load_platform_config()
    if not _client_allowed(request, config.metrics.allowed_ips):
        raise HTTPException(status_code=403, detail="Forbidden")
    expected = get_credential(config.metrics.bearer_token_key) or os.environ.get(
        config.metrics.bearer_token_key, ""
    )
    if expected:
        token = _get_bearer_token(request)
        if token != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")


@app.on_event("startup")
async def on_startup() -> None:
    result = validate_startup()
    if result.config:
        _metrics.set_paper_mode(result.config.is_paper_mode)
    if result.success:
        _metrics.set_health("ok")
        _metrics.set_readiness("ready")
    else:
        _metrics.set_health("degraded")
        _metrics.set_readiness("not_ready")
        for err in result.errors:
            logger.error("Startup validation: %s", err)


@app.get("/healthz")
async def healthz(_: None = Depends(require_auth)) -> JSONResponse:
    snap = _metrics.snapshot()
    return JSONResponse({"status": snap.health, "paper_mode": snap.paper_mode})


@app.get("/readyz")
async def readyz(_: None = Depends(require_auth)) -> JSONResponse:
    snap = _metrics.snapshot()
    code = 200 if snap.readiness == "ready" else 503
    return JSONResponse({"ready": snap.readiness == "ready", "readiness": snap.readiness}, status_code=code)


@app.get("/metrics")
async def metrics(_: None = Depends(require_auth)) -> JSONResponse:
    snap = _metrics.snapshot()
    return JSONResponse({
        "health": snap.health,
        "readiness": snap.readiness,
        "process_state": snap.process_state.value,
        "paper_mode": snap.paper_mode,
        "provider_status": snap.provider_status,
        "exchange_connectivity": snap.exchange_connectivity,
        "last_market_data_ts": snap.last_market_data_ts.isoformat() if snap.last_market_data_ts else None,
        "last_successful_model_call": (
            snap.last_successful_model_call.isoformat() if snap.last_successful_model_call else None
        ),
    })


# Explicitly no POST/PUT/PATCH/DELETE routes — supervisor is read-only


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    config = load_platform_config()
    uvicorn.run(
        "metrics_server:app",
        host=config.metrics.host,
        port=config.metrics.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
