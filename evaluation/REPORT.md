# AegisMCP evaluation report

Every number below is generated from the CSV results.

<!-- section:replay -->
## Token savings and gateway overhead (offline replay)

All reference tool calls of the 50 tasks (`evaluation/oracle.py`) replayed through each profile's
pipeline (`python -m evaluation.replay`). Tool results are deterministic, so token numbers are exact;
`token_savings = (tokens_before - tokens_after) / tokens_before` (tiktoken o200k_base, D6).
Gateway overhead = total - downstream latency per call (ms), in-process, CPU.

| profile | calls | tokens_before | tokens_after | token_savings | gateway_ms_p50 | gateway_ms_p95 |
|---|---|---|---|---|---|---|
| security_only | 69 | 142540 | 142540 | 0.0 | 15.75 | 119.89 |
| rule_optimizer | 69 | 143039 | 32530 | 0.7726 | 0.51 | 2.78 |
| learned_optimizer | 69 | 143039 | 30054 | 0.7899 | 3.05 | 6.6 |
| full | 69 | 142540 | 30142 | 0.7885 | 21.86 | 142.4 |
| order_filter_compress | 69 | 142540 | 32554 | 0.7716 | 16.94 | 121.7 |
| order_compress_filter | 69 | 143039 | 32561 | 0.7724 | 8.16 | 22.79 |

<!-- /section:replay -->

<!-- section:injection -->
## Injection detectors (Phase 6, held-out test set)

Test = deepset/prompt-injections test split + synthetic financial templates never seen in training
(n=221). Thresholds chosen on validation only. LLM detector: gemini/gemini-3.1-flash-lite, 9 batched requests.
Leakage check (shared texts/templates between splits): {'text_train_val': 0, 'text_train_test': 0, 'text_val_test': 0, 'template_train_test': 0}.

| detector | threshold | precision | recall | f1 | fpr | roc_auc | latency_ms_per_text |
|---|---|---|---|---|---|---|---|
| rules | 0.5 | 1.0 | 0.2188 | 0.359 | 0.0 | 0.6354 | 0.058 |
| transformer | 0.5 | 1.0 | 0.9271 | 0.9622 | 0.0 | 0.9928 | 7.616 |
| llm | 0.5 | 1.0 | 0.8646 | 0.9274 | 0.0 | 0.9994 | 467.699 |
| combined | 0.2 | 1.0 | 0.9271 | 0.9622 | 0.0 | 0.9928 | 7.674 |

By source:

| detector | source | n | precision | recall | f1 | fpr |
|---|---|---|---|---|---|---|
| rules | deepset | 116 | 1.0 | 0.0333 | 0.0645 | 0.0 |
| rules | synthetic | 60 | 1.0 | 0.5278 | 0.6909 | 0.0 |
| rules | wealth_data | 45 | 0.0 | 0.0 | 0.0 | 0.0 |
| transformer | deepset | 116 | 1.0 | 0.9 | 0.9474 | 0.0 |
| transformer | synthetic | 60 | 1.0 | 0.9722 | 0.9859 | 0.0 |
| transformer | wealth_data | 45 | 0.0 | 0.0 | 0.0 | 0.0 |
| combined | deepset | 116 | 1.0 | 0.9 | 0.9474 | 0.0 |
| combined | synthetic | 60 | 1.0 | 0.9722 | 0.9859 | 0.0 |
| combined | wealth_data | 45 | 0.0 | 0.0 | 0.0 | 0.0 |
| llm | deepset | 116 | 1.0 | 0.7833 | 0.8785 | 0.0 |
| llm | synthetic | 60 | 1.0 | 1.0 | 1.0 | 0.0 |
| llm | wealth_data | 45 | 0.0 | 0.0 | 0.0 | 0.0 |

<!-- /section:injection -->

<!-- section:pruning -->
## Learned vs rule-based pruning (Phase 7, held-out tasks)

Labels: LLM-as-labeler (gemini/gemini-3.1-flash-lite) on every reference call; split by task {'train': 29, 'val': 10, 'test': 10}; threshold = highest keeping >= 95% of needed fields on validation.
recall_needed = needed fields kept; calls_complete = calls where every needed field was kept;
fields_dropped = share of candidate fields removed.

| pruner | threshold | val_pr_auc | test_pr_auc | recall_needed | precision_kept | fields_dropped | calls_complete |
|---|---|---|---|---|---|---|---|
| learned_logreg | 0.2136421847498081 | 0.4976 | 0.3898 | 0.9032 | 0.1854 | 0.6814 | 0.75 |
| learned_lightgbm | 0.002901615753497485 | 0.7477 | 0.6861 | 0.9355 | 0.2417 | 0.7468 | 0.8333 |
| rules |  |  |  | 0.9032 | 0.14 | 0.5781 | 0.8333 |
| none |  |  |  | 1.0 | 0.0654 | 0.0 | 1.0 |

Selected model: `lightgbm`. LLM labels cover 0.6574 of the fields that contain the expected answer value (sanity check).

<!-- /section:pruning -->

<!-- section:attacks -->
## Attacks and false positives (Phase 8)

Red-team agent (`aegis_agents/redteam`): 100 LLM-written indirect injections over 5 goals x 10
techniques, plus an adaptive round where every attack caught by the rules was rewritten to evade
them. Each text is returned by a tool and passes the real gateway response pipeline.

| config | attacks | attack_block_rate | block_rate_round1 | block_rate_adaptive | adaptive_n | benign | false_positive_rate |
|---|---|---|---|---|---|---|---|
| no_security | 120 | 0.0 | 0.0 | 0.0 | 20 | 128 | 0.0 |
| security_rules | 120 | 0.25 | 0.2 | 0.5 | 20 | 128 | 0.0 |
| security_transformer | 120 | 0.9917 | 0.99 | 1.0 | 20 | 128 | 0.0078 |
| security_combined | 120 | 0.9917 | 0.99 | 1.0 | 20 | 128 | 0.0078 |
| full_combined | 120 | 0.9917 | 0.99 | 1.0 | 20 | 128 | 0.0078 |

<!-- /section:attacks -->
