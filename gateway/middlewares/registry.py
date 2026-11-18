"""Build middlewares from their config names (``pipeline.request`` / ``pipeline.response``).

Each factory gets the gateway config and the shared ``Services``; its own settings come from
``middlewares.<name>`` in gateway.yaml.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from gateway.config import GatewayConfig
from gateway.detectors.rules import RuleDetector
from gateway.middlewares.access import AuthMiddleware, PolicyMiddleware
from gateway.middlewares.audit import AuditMiddleware
from gateway.middlewares.injection import InjectionScanMiddleware, InjectionScanSettings
from gateway.middlewares.pii import PiiMaskMiddleware
from gateway.pii import DEFAULT_ENTITIES
from gateway.pipeline.base import Middleware
from gateway.pipeline.runner import Pipeline
from gateway.policy import load_policies
from gateway.services import Services

log = logging.getLogger(__name__)

Factory = Callable[[GatewayConfig, Services], Middleware]


class PiiSettings(BaseModel):
    """``middlewares.pii_mask`` in gateway.yaml."""

    model_config = ConfigDict(extra="forbid")

    entities: list[str] = Field(default_factory=lambda: list(DEFAULT_ENTITIES))
    min_score: float = Field(default=0.5, ge=0.0, le=1.0)
    model: str = "en_core_web_sm"


def _audit(config: GatewayConfig, services: Services) -> Middleware:
    return AuditMiddleware(services.store)


def agent_keys(config: GatewayConfig) -> dict[str, str]:
    """API key of each agent, read from its ``token_env`` variable (missing keys are skipped)."""
    keys: dict[str, str] = {}
    for agent, agent_cfg in config.agents.items():
        key = os.environ.get(agent_cfg.token_env or "", "")
        if key:
            keys[agent] = key
        else:
            log.warning("agent %s has no API key (env %s unset): it cannot authenticate over HTTP",
                        agent, agent_cfg.token_env)  # fmt: skip
    return keys


def _auth(config: GatewayConfig, services: Services) -> Middleware:
    return AuthMiddleware(agent_keys(config))


def _policy(config: GatewayConfig, services: Services) -> Middleware:
    if not config.policies_file:
        raise ValueError("the 'policy' middleware needs 'policies_file' in gateway.yaml")
    policies = load_policies(config.policies_file)
    needs_approvals = any(p.require_approval for p in policies.agents.values())
    return PolicyMiddleware(policies, services.approvals if needs_approvals else None)


def _pii(config: GatewayConfig, services: Services) -> Middleware:
    settings = PiiSettings.model_validate(config.middlewares.get("pii_mask", {}))
    return PiiMaskMiddleware(entities=settings.entities, min_score=settings.min_score, model=settings.model)


def _injection(config: GatewayConfig, services: Services) -> Middleware:
    settings = InjectionScanSettings.model_validate(config.middlewares.get("injection_scan", {}))
    store = services.store if services.has_store else None
    return InjectionScanMiddleware(RuleDetector(), settings, store)


FACTORIES: dict[str, Factory] = {
    "audit": _audit,
    "auth": _auth,
    "policy": _policy,
    "pii_mask": _pii,
    "injection_scan": _injection,
}


def build_pipeline(
    config: GatewayConfig, services: Services, factories: dict[str, Factory] | None = None
) -> Pipeline:
    """Create one instance per middleware name, shared between the request and response lists."""
    table = FACTORIES if factories is None else factories
    instances: dict[str, Middleware] = {}
    for name in [*config.pipeline.request, *config.pipeline.response]:
        if name in instances:
            continue
        if name not in table:
            raise ValueError(f"unknown middleware {name!r}; available: {sorted(table)}")
        instances[name] = table[name](config, services)
    return Pipeline(
        request=[instances[n] for n in config.pipeline.request],
        response=[instances[n] for n in config.pipeline.response],
    )
