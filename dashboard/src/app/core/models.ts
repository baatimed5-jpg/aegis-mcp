export interface Stats {
  calls_total: number;
  by_status: Record<string, number>;
  security_events_total: number;
  security_events_by_kind: Record<string, number>;
  blocked_attacks: number;
  tokens_before: number;
  tokens_after: number;
  tokens_saved: number;
  cost_saved_usd: number;
  cache_hits: number;
  pii_entities: number;
}

export interface AuditRow {
  id: string;
  ts: string;
  agent_id: string;
  server: string;
  tool: string;
  status: string;
  reason: string | null;
  injection_score: number | null;
  pii_entities: number | null;
  tokens_before: number | null;
  tokens_after: number | null;
  cost_saved_usd: number | null;
  latency_total_ms: number | null;
  latency_downstream_ms: number | null;
  cache_hit: boolean;
}

export type SecurityKind = 'description' | 'argument' | 'response';

export interface SecurityEvent {
  id: string;
  ts: string;
  kind: SecurityKind;
  server: string;
  tool: string;
  detector: string;
  score: number;
  action: string;
  excerpt: string | null;
}

export interface Approval {
  id: string;
  agent_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  status: string;
  created_at: string;
}

export type Decision = 'approve' | 'deny';

export interface AgentPolicy {
  allow: string[];
  require_approval: string[];
}

export interface Policies {
  agents: Record<string, AgentPolicy>;
}

export interface Pipeline {
  request: string[];
  response: string[];
  servers: string[];
  detector: string;
}

export interface ExperimentSummaryRow {
  profile: string;
  metric: string;
  mean: number;
  std: number;
  runs: number;
}

export interface ExperimentTag {
  tag: string;
  summary: ExperimentSummaryRow[];
  charts: string[];
}

export interface Experiments {
  tags: ExperimentTag[];
}
