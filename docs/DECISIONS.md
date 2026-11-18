# Design Decisions

Format: **Decision**, then *Alternatives*, then *Reason*. Numbering follows ARCHITECTURE.md §2 where it applies.

## D1: Middleware interface with separate request and response orders
- **Decision**: one base class with optional hooks `on_list_tools`, `on_request`, `on_response`, and
  `on_complete`. Config holds two ordered lists, `pipeline.request` and `pipeline.response`.
- *Alternatives*: an onion/ASGI-style `__call__(ctx, next)` chain.
- *Reason*: in an onion chain the response order is always the reverse of the request order, so
  experiment (6), filter→compress vs compress→filter, could not be expressed. Two lists turn that
  experiment into a config change.

## D2: Denials are exceptions, and `on_complete` always runs
- **Decision**: `PipelineDenied(reason, code)` becomes an MCP tool error (`isError=true`). The
  completion hooks run for every call, whatever its outcome.
- *Alternatives*: return-value flags; JSON-RPC protocol errors.
- *Reason*: denied calls must still be audited. A tool-level error is readable by the agent's LLM.
  A protocol error could crash some clients.

## D3: Cache raw downstream results
- **Decision**: the cache sits at the downstream boundary, and all response middlewares run on cache hits.
  Tools with side effects are never cached.
- *Alternatives*: cache the final optimized response.
- *Reason*: pruning depends on the question, and security must apply to every response that is served.
  Caching `transfer_funds` would be a correctness bug.

## D4: User question passed in `_meta` (fallback: HTTP header)
- **Decision**: the assistant sends `aegis/question` in the request `_meta`, or `X-Aegis-Question` as a fallback.
  If the question is missing, learned pruning falls back to rule-based pruning.
- *Alternatives*: add a `question` argument to every tool (pollutes the tool schemas); infer the
  question from the arguments (weak signal).
- *Reason*: `_meta` is the protocol's own extension point. **To verify** in Phase 4/7 that the
  Agents SDK client can set it.

## D5: API keys rather than JWT
- **Decision**: one key per agent in `.env`. YAML holds only the env-var name. Comparison is
  constant-time. Over stdio, the agent id comes from a CLI flag.
- *Alternatives*: PyJWT with signed tokens.
- *Reason*: one issuer, no third-party identity, no token expiry needed in the demo. It is simpler
  and still meets the requirement ("PyJWT or API keys").

## D6: tiktoken in-path, official count API offline
- *Alternatives*: provider API on every call.
- *Reason*: a network round trip per call would distort the latency-overhead metric. The reported
  numbers still come from the official API.

## D7: Approvals use in-memory futures + a Postgres row
- *Alternatives*: Redis pub/sub; polling the database.
- *Reason*: simplest correct design for a single gateway instance. The multi-instance limitation
  is documented.

## D8: Evidence-recovery store in Redis with TTL
- *Alternatives*: a Postgres table.
- *Reason*: the data is short-lived, per call, and read by key. A TTL cleans it up automatically.

## D9: Wealth data generated in memory (Faker, seed 42)
- *Alternatives*: seed a Postgres schema for the demo server.
- *Reason*: deterministic, no extra infrastructure, and the demo server stays independent of the gateway DB.

## D10: Injection scanning per JSON string value, max over chunks
- *Alternatives*: scan the whole serialized response at once.
- *Reason*: lower latency on large responses, and the offending field can be sanitized precisely.

## D11: Single uv project with dependency groups
- *Alternatives*: a uv workspace with one package per component.
- *Reason*: fewer moving parts for a single developer. The groups keep the runtime image lean.
