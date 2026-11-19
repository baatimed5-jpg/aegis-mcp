"""Build middlewares from their config names (``pipeline.request`` / ``pipeline.response``).

Each factory gets the gateway config and the shared ``Services``; its own settings come from
``middlewares.<name>`` in gateway.yaml.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from gateway.config import GatewayConfig
from gateway.detectors.base import InjectionDetector
from gateway.detectors.rules import RuleDetector
from gateway.middlewares.access import AuthMiddleware, PolicyMiddleware
from gateway.middlewares.audit import AuditMiddleware
from gateway.middlewares.injection import InjectionScanMiddleware, InjectionScanSettings
from gateway.middlewares.optimizer import (
    MetricsMiddleware,
    MetricsSettings,
    OptimizerMiddleware,
    OptimizerSettings,
    Pruner,
    RulePruner,
)
from gateway.middlewares.pii import PiiMaskMiddleware
from gateway.optimizer.rules import load_rules
from gateway.pii import DEFAULT_ENTITIES
from gateway.pipeline.base import Middleware
from gateway.pipeline.runner import Pipeline
from gateway.policy import load_policies
from gateway.services import Services

log = logging.getLogger(__name__)

Factory = Callable[[GatewayConfig, Services], Middleware]
ROOT = Path(__file__).resolve().parents[2]


class PiiSettings(BaseModel):
    """``middlewares.pii_mask`` in gateway.yaml."""

    model_config = ConfigDict(extra="forbid")

    entities: list[str] = Field(default_factory=lambda: list(DEFAULT_ENTITIES))
    min_score: float = Field(default=0.5, ge=0.0, le=1.0)
    model: str = "en_core_web_sm"


def _audit(config: GatewayConfig, services: Services) -> Middleware:
    return AuditMiddleware(services.store, services.hub)


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


def build_detector(settings: InjectionScanSettings) -> InjectionDetector:
    """The configured injection detector (heavy ML imports only when selected)."""
    if settings.detector == "rules":
        return RuleDetector()
    if settings.detector == "llm":
        from gateway.detectors.llm import LLMDetector

        return LLMDetector(settings.llm_model)
    from gateway.detectors.transformer import TransformerDetector

    transformer = TransformerDetector(ROOT / settings.model_path)
    if settings.detector == "transformer":
        return transformer
    from gateway.detectors.combined import CombinedDetector

    return CombinedDetector.from_file(
        ROOT / settings.combined_file, {"rules": RuleDetector(), "transformer": transformer}
    )


def _injection(config: GatewayConfig, services: Services) -> Middleware:
    settings = InjectionScanSettings.model_validate(config.middlewares.get("injection_scan", {}))
    store = services.store if services.has_store else None
    detector = build_detector(settings)
    description_detector = detector if settings.description_detector == "same" else RuleDetector()
    return InjectionScanMiddleware(detector, settings, store, description_detector)


def _resolve(config: GatewayConfig, name: str) -> Path:
    """A file name relative to the gateway config directory (absolute names unchanged)."""
    path = Path(name)
    if path.is_absolute():
        return path
    return Path(config.config_dir or ROOT / "config") / path


def _optimizer(config: GatewayConfig, services: Services) -> Middleware:
    settings = OptimizerSettings.model_validate(config.middlewares.get("optimizer", {}))
    pruner: Pruner | None = None
    if settings.pruning == "rules":
        pruner = RulePruner(load_rules(_resolve(config, settings.rules_file)))
    elif settings.pruning == "learned":
        from gateway.optimizer.learned import LearnedPruner

        pruner = LearnedPruner.load(
            ROOT / settings.learned_model,
            threshold=settings.learned_threshold,
            rules_file=_resolve(config, settings.rules_file),
        )
    evidence = services.evidence if settings.evidence_recovery else None
    return OptimizerMiddleware(settings, pruner, evidence)


def _metrics(config: GatewayConfig, services: Services) -> Middleware:
    return MetricsMiddleware(MetricsSettings.model_validate(config.middlewares.get("metrics", {})))


FACTORIES: dict[str, Factory] = {
    "optimizer": _optimizer,
    "metrics": _metrics,
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
