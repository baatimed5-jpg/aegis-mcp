"""Fine-tune DeBERTa-v3-small as a prompt-injection classifier (GPU if available, seed 42).

    uv run python -m ml.injection.train [--epochs 3] [--model microsoft/deberta-v3-small]

Plain PyTorch loop (no Trainer) so every step is visible: AdamW, linear warm-up/decay, mixed
precision on CUDA, best epoch chosen by validation F1, decision threshold tuned on validation.
Saves the model + tokenizer + ``aegis_meta.json`` to ``ml/artifacts/injection-deberta`` (gitignored:
too large for git; re-create it with this script). The Colab notebook runs the same code.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from sklearn.metrics import f1_score
from torch.utils.data import DataLoader
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

from ml.injection.data import load_split

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "ml" / "artifacts" / "injection-deberta"
SEED = 42
MAX_LEN = 256


def set_seed(seed: int) -> None:
    """Seed Python, NumPy and PyTorch."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


@torch.no_grad()
def predict(model: Any, tokenizer: Any, texts: list[str], device: str, batch: int = 32) -> np.ndarray:
    """P(injection) for each text."""
    model.eval()
    probs: list[float] = []
    for i in range(0, len(texts), batch):
        enc = tokenizer(
            texts[i : i + batch], truncation=True, max_length=MAX_LEN, padding=True, return_tensors="pt"
        )
        enc = {k: v.to(device) for k, v in enc.items()}
        with torch.autocast(device_type="cuda", enabled=device == "cuda"):
            logits = model(**enc).logits
        probs += torch.softmax(logits.float(), dim=-1)[:, 1].tolist()
    return np.array(probs)


def best_threshold(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    """Threshold in [0.05, 0.95] maximizing F1 on validation."""
    grid = np.round(np.arange(0.05, 0.96, 0.05), 2)
    scores = [f1_score(y, p >= t, zero_division=0) for t in grid]
    k = int(np.argmax(scores))
    return float(grid[k]), float(scores[k])


def main() -> None:
    """Train, select the best epoch on validation, save."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="microsoft/deberta-v3-small")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--lr", type=float, default=3e-5)
    args = parser.parse_args()
    set_seed(SEED)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    train, val = load_split("train"), load_split("val")
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSequenceClassification.from_pretrained(args.model, num_labels=2).to(device)

    def collate(rows: list[dict[str, Any]]) -> dict[str, torch.Tensor]:
        enc = tokenizer(
            [r["text"] for r in rows], truncation=True, max_length=MAX_LEN, padding=True, return_tensors="pt"
        )
        enc["labels"] = torch.tensor([r["label"] for r in rows])
        return dict(enc)

    loader = DataLoader(
        train,
        batch_size=args.batch,
        shuffle=True,
        collate_fn=collate,
        generator=torch.Generator().manual_seed(SEED),
    )  # type: ignore[arg-type]
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps = len(loader) * args.epochs
    sched = get_linear_schedule_with_warmup(opt, int(0.1 * steps), steps)
    scaler = torch.amp.GradScaler("cuda", enabled=device == "cuda")
    y_val = np.array([r["label"] for r in val])
    history = []
    best_f1, best_state = -1.0, None
    t0 = time.time()
    for epoch in range(args.epochs):
        model.train()
        total = 0.0
        for batch in loader:
            batch = {k: v.to(device) for k, v in batch.items()}
            with torch.autocast(device_type="cuda", enabled=device == "cuda"):
                loss = model(**batch).loss
            opt.zero_grad()
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
            total += float(loss)
        p_val = predict(model, tokenizer, [r["text"] for r in val], device)
        f1 = f1_score(y_val, p_val >= 0.5, zero_division=0)
        history.append({"epoch": epoch + 1, "train_loss": total / len(loader), "val_f1@0.5": f1})
        print(history[-1], flush=True)
        if f1 > best_f1:
            best_f1 = f1
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    assert best_state is not None
    model.load_state_dict(best_state)
    p_val = predict(model, tokenizer, [r["text"] for r in val], device)
    threshold, f1_t = best_threshold(y_val, p_val)
    OUT.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(OUT)
    tokenizer.save_pretrained(OUT)
    meta = {"base_model": args.model, "threshold": threshold, "val_f1": f1_t, "history": history,
            "train_n": len(train), "val_n": len(val), "device": device, "train_seconds": round(time.time() - t0, 1),
            "max_len": MAX_LEN, "seed": SEED}  # fmt: skip
    (OUT / "aegis_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
