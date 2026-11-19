# Datasets

| Dataset | Use | License | How we get it |
|---|---|---|---|
| `deepset/prompt-injections` (Hugging Face) | Phase 6 detector: train/val (its train split, 85/15) and test (its test split) | Apache-2.0 | `datasets.load_dataset`, cached locally, never committed |
| Synthetic financial injections (`ml/injection/synth.py`) | Phase 6 detector: injections hidden in tool results + benign finance texts with "risky" words | ours (project license) | generated, seed 42 |
| Wealth demo data (`servers/wealth/data.py`) | MCP server data, benign real strings (comments/notes), benchmark answers | ours (Faker, seed 42) | generated in memory |
| Benchmark tasks (`evaluation/benchmark/tasks.json`) | 50 assistant tasks with computed answers | ours | `python -m evaluation.build_tasks` |
| Red-team attacks (`evaluation/attacks/attacks.jsonl`) | Phase 8: 100 LLM-written indirect injections + adaptive rewrites | ours (generated with Gemini, test data only) | `python -m aegis_agents.redteam.agent` |
| Pruning labels (`ml/data/pruning/labels.jsonl`) | Phase 7: which fields of each reference tool result are needed | ours (LLM-as-labeler) | `python -m ml.pruning.label` |

## Split and leakage rules (Phase 6)
- deepset test split stays test; deepset train is cut 85/15 (seed 42) into train/val.
- Synthetic data is split **by template group** (70/15/15): test injections come from templates never
  seen in training, so the test measures generalization, not memorization of a template.
- Texts are normalized (NFKC, lowercase, invisible characters removed) and any text already in an
  earlier split is dropped from later splits. `ml/data/injection/summary.json` records the counts;
  the leakage check (shared texts / shared synthetic templates between splits) must be 0 everywhere.

## Notes
- deepset contains German and English samples; we keep both (attackers use other languages).
- Generated attack texts use only fictional ids and example.com / example.net domains.
- `ml/data/` is gitignored; every file in it is re-created by the scripts above.
