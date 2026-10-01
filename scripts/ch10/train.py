"""ch10 分类训练：默认全参数，可选 LoRA；保持 17 类多标签、早停和阈值校准。

make ch10-train 保留原路径；make ch10-train TRAIN_ARGS="--mode lora" 启用 LoRA。
CLI 优先于 CH10_* 环境配置（支持 .env）。LoRA 训练结束合并为标准模型，评测与 ONNX 导出无需改动。
"""
import argparse
import json
import os
import pathlib

import numpy as np
import torch
from dotenv import load_dotenv
from sklearn.metrics import f1_score
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          EarlyStoppingCallback, Trainer, TrainerCallback,
                          TrainingArguments, default_data_collator)

from app.core.taxonomy import ID2LABEL, LABEL2ID, NUM_CLASSES

BASE = "hfl/chinese-roberta-wwm-ext"
DATA = pathlib.Path("data/ch10/dataset")
OUT = pathlib.Path("data/ch10/model")


def positive_int(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """训练配置集中在入口；不改变评测、服务端的模型产物契约。"""
    load_dotenv(override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("full", "lora"),
                        default=os.getenv("CH10_TRAIN_MODE", "full"))
    for flag, env, default in (
        ("lora-rank", "LORA_RANK", 64), ("lora-alpha", "LORA_ALPHA", 128),
        ("batch-size", "TRAIN_BATCH_SIZE", 16), ("eval-batch-size", "EVAL_BATCH_SIZE", 64),
        ("gradient-accumulation-steps", "GRADIENT_ACCUMULATION_STEPS", 1),
    ):
        parser.add_argument(f"--{flag}", type=positive_int,
                            default=os.getenv(f"CH10_{env}", str(default)))
    parser.add_argument("--cpu-threads", type=positive_int,
                        default=os.getenv("CH10_CPU_THREADS"))
    parser.add_argument("--epochs", type=float, default=8)
    parser.add_argument("--learning-rate", type=float, default=None)
    parser.add_argument("--base-model", default=BASE)
    parser.add_argument("--data-dir", type=pathlib.Path, default=DATA)
    parser.add_argument("--output-dir", type=pathlib.Path, default=OUT)
    options = parser.parse_args(argv)
    # argparse 不校验来自环境的 choices 默认值，显式拒绝拼写错误。
    if options.mode not in ("full", "lora"):
        parser.error("CH10_TRAIN_MODE 必须为 full 或 lora")
    if options.epochs <= 0 or (options.learning_rate is not None and options.learning_rate <= 0):
        parser.error("训练轮数和学习率必须大于零")
    return options


def adapt_model(model, rank: int, alpha: int):
    """冻结基座，训练 query/value 的 LoRA、分类头和可能新初始化的 pooler。"""
    from peft import LoraConfig, TaskType, get_peft_model

    return get_peft_model(model, LoraConfig(
        task_type=TaskType.SEQ_CLS, r=rank, lora_alpha=alpha, lora_dropout=0.05,
        target_modules=["query", "value"], modules_to_save=["classifier", "pooler"], bias="none",
    ))


def load_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def encode(samples: list[dict], tokenizer) -> list[dict]:
    enc = tokenizer([s["text"] for s in samples], truncation=True,
                    padding="max_length", max_length=128)
    items = []
    for i, s in enumerate(samples):
        vec = [0.0] * NUM_CLASSES                 # float 向量:BCEWithLogitsLoss 要求
        for lb in s["labels"]:
            vec[LABEL2ID[lb]] = 1.0
        items.append({"input_ids": enc["input_ids"][i],
                      "attention_mask": enc["attention_mask"][i],
                      "token_type_ids": enc["token_type_ids"][i],
                      "labels": vec})
    return items


def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = (1 / (1 + np.exp(-logits)) >= 0.5).astype(int)
    return {"micro_f1": f1_score(labels, preds, average="micro", zero_division=0),
            "macro_f1": f1_score(labels, preds, average="macro", zero_division=0)}


class BestInMemory(TrainerCallback):
    """只快照可训练参数；LoRA 不再复制整份冻结基座，最优状态仍按验证集 F1 选择。"""

    def __init__(self, model):
        self.model = model
        self.best_metric = -1.0
        self.best_state = None
        self.trainable_names = {name for name, param in model.named_parameters() if param.requires_grad}

    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        score = (metrics or {}).get("eval_micro_f1", -1.0)
        if score > self.best_metric:
            self.best_metric = score
            self.best_state = {k: v.detach().to("cpu", copy=True)
                               for k, v in self.model.state_dict().items() if k in self.trainable_names}

    def restore(self) -> None:
        if self.best_state is not None:
            # 冻结参数未参与训练，无需回填；标准模式仍恢复所有可训练参数。
            self.model.load_state_dict(self.best_state, strict=False)


def main(argv: list[str] | None = None) -> None:
    options = parse_args(argv)
    if options.cpu_threads is not None:
        torch.set_num_threads(options.cpu_threads)
    tokenizer = AutoTokenizer.from_pretrained(options.base_model)
    model = AutoModelForSequenceClassification.from_pretrained(
        options.base_model, num_labels=NUM_CLASSES, problem_type="multi_label_classification",
        id2label=ID2LABEL, label2id=LABEL2ID)
    if options.mode == "lora":
        model = adapt_model(model, options.lora_rank, options.lora_alpha)
        model.print_trainable_parameters()
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    train_ds = encode(load_jsonl(options.data_dir / "train.jsonl"), tokenizer)
    val_ds = encode(load_jsonl(options.data_dir / "val.jsonl"), tokenizer)
    # 默认 full 的学习率、批量与轮数保持原值；LoRA 可单独覆盖学习率。
    args = TrainingArguments(
        output_dir=str(options.output_dir.parent / "checkpoints"),
        eval_strategy="epoch",                    # v5 参数名,不是 evaluation_strategy
        save_strategy="no",                       # 零磁盘 checkpoint,最优权重走 BestInMemory
        learning_rate=options.learning_rate or (2e-4 if options.mode == "lora" else 2e-5),
        per_device_train_batch_size=options.batch_size,
        per_device_eval_batch_size=options.eval_batch_size,
        gradient_accumulation_steps=options.gradient_accumulation_steps,
        num_train_epochs=options.epochs,
        label_names=["labels"],                    # PEFT 包装后仍让 Trainer 识别多标签与评测指标
        weight_decay=0.01,                        # 正则化防过拟合
        metric_for_best_model="micro_f1",         # EarlyStopping 盯它
        greater_is_better=True,
        logging_steps=20,
        report_to="none",
    )
    best_cb = BestInMemory(model)
    trainer = Trainer(
        model=model, args=args,
        train_dataset=train_ds, eval_dataset=val_ds,
        processing_class=tokenizer,               # v5:tokenizer= 已改名
        data_collator=default_data_collator,
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=2), best_cb],
    )
    trainer.train()
    if best_cb.best_state is not None:            # 回填验证集最优权重(替代 load_best_model_at_end)
        best_cb.restore()
        print(f"回填最优权重:验证集 micro-F1 {best_cb.best_metric:.4f}")
    # 验证集上扫全局最优阈值(0.30~0.70 步进 0.05)
    logits = trainer.predict(val_ds).predictions
    probs = 1 / (1 + np.exp(-logits))
    gold = np.array([d["labels"] for d in val_ds])
    best_t, best_f1 = 0.5, -1.0
    for t in np.arange(0.30, 0.71, 0.05):
        f1 = f1_score(gold, (probs >= t).astype(int), average="micro", zero_division=0)
        if f1 > best_f1:
            best_t, best_f1 = round(float(t), 2), float(f1)
    options.output_dir.mkdir(parents=True, exist_ok=True)
    if options.mode == "lora":
        # 适配器单独留档；根目录保存合并后的标准模型，兼容旧评测/导出/权重目录。
        model.save_pretrained(options.output_dir / "adapter")
        model = model.merge_and_unload(safe_merge=True)
        model.save_pretrained(options.output_dir)
    else:
        trainer.save_model(options.output_dir)
    tokenizer.save_pretrained(options.output_dir)
    (options.output_dir / "threshold.json").write_text(
        json.dumps({"threshold": best_t, "val_micro_f1": best_f1}))
    (options.output_dir / "training_config.json").write_text(json.dumps({
        "mode": options.mode, "base_model": options.base_model,
        "lora_rank": options.lora_rank if options.mode == "lora" else None,
        "lora_alpha": options.lora_alpha if options.mode == "lora" else None,
        "trainable_parameters": trainable, "total_parameters": total,
        "learning_rate": args.learning_rate, "batch_size": options.batch_size,
        "gradient_accumulation_steps": options.gradient_accumulation_steps,
        "best_val_micro_f1": best_cb.best_metric,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"最优阈值 {best_t},验证集 micro-F1 {best_f1:.4f};模型已存 {options.output_dir}")


if __name__ == "__main__":
    main()
