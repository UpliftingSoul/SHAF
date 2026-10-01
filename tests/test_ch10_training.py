"""可选训练依赖下的真实回归：full/LoRA 训练、合并、评测、ONNX 和服务分批一致性。

使用随机初始化的小型本地 BERT，不下载真实权重、不写项目模型或历史报告。
缺少 ml 组时跳过；升级验收时必须安装 ml/cpu 并实际运行这些用例。
"""
import importlib
import json
import os
import sys

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("peft")
pytest.importorskip("onnxruntime")
from transformers import AutoModelForSequenceClassification, AutoTokenizer, BertConfig, BertForSequenceClassification

from app.core.taxonomy import ID2LABEL, LABEL2ID, NUM_CLASSES, TOPIC_NAMES
from scripts.ch10 import evaluate, export_onnx, train


@pytest.fixture
def tiny_model(tmp_path, monkeypatch):
    """真实架构与标签不变，仅缩小隐藏维度和层数以便快速离线验证。"""
    monkeypatch.setattr(train, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    for key in list(os.environ):
        if key.startswith("CH10_"):
            monkeypatch.delenv(key)
    base, data = tmp_path / "base", tmp_path / "dataset"
    base.mkdir()
    data.mkdir()
    torch.manual_seed(42)
    config = BertConfig(vocab_size=16, hidden_size=16, intermediate_size=32,
                        num_hidden_layers=1, num_attention_heads=2,
                        max_position_embeddings=128, num_labels=NUM_CLASSES,
                        id2label=ID2LABEL, label2id=LABEL2ID,
                        problem_type="multi_label_classification")
    BertForSequenceClassification(config).save_pretrained(base)
    (base / "vocab.txt").write_text(
        "\n".join(["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "买", "大", "了",
                   "想", "退", "快", "递", "到", "哪", "好", "吗"]), encoding="utf-8")
    samples = [{"text": text, "labels": [TOPIC_NAMES[i % 2]]}
               for i, text in enumerate(["买大了想退", "快递到哪了", "好", "好吗"])]
    for name in ("train", "val", "test"):
        (data / f"{name}.jsonl").write_text("\n".join(map(json.dumps, samples)), encoding="utf-8")
    torch.set_num_threads(1)
    return base, data


def test_defaults_and_environment_cli_precedence(monkeypatch):
    monkeypatch.setattr(train, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("CH10_TRAIN_MODE", "full")
    monkeypatch.delenv("CH10_LORA_RANK", raising=False)
    assert train.parse_args([]).lora_rank == 64
    monkeypatch.setenv("CH10_TRAIN_MODE", "lora")
    assert train.parse_args(["--mode", "full"]).mode == "full"
    with pytest.raises(SystemExit):
        train.parse_args(["--lora-rank", "0"])


def test_best_snapshot_only_copies_trainable_weights():
    config = BertConfig(vocab_size=16, hidden_size=16, intermediate_size=32,
                        num_hidden_layers=1, num_attention_heads=2, num_labels=NUM_CLASSES)
    model = train.adapt_model(BertForSequenceClassification(config), rank=4, alpha=8)
    best = train.BestInMemory(model)
    best.on_evaluate(None, None, None, metrics={"eval_micro_f1": 0.5})
    snapshot = {key: value.clone() for key, value in best.best_state.items()}
    assert any("classifier" in name for name in snapshot)
    assert any("pooler" in name for name in snapshot)
    assert all("lora_" in name or "modules_to_save" in name for name in snapshot)
    with torch.no_grad():
        for param in model.parameters():
            if param.requires_grad:
                param.add_(1)
    best.on_evaluate(None, None, None, metrics={"eval_micro_f1": 0.4})
    best.restore()
    assert all(torch.equal(model.state_dict()[key], value) for key, value in snapshot.items())


@pytest.mark.parametrize("mode", ["full", "lora"])
def test_training_to_onnx_and_batched_service(mode, tiny_model, tmp_path, monkeypatch):
    base, data = tiny_model
    output, onnx_dir, reports = tmp_path / "model", tmp_path / "onnx", tmp_path / "reports"
    train.main(["--base-model", str(base), "--data-dir", str(data), "--output-dir", str(output),
                "--mode", mode, "--lora-rank", "4", "--lora-alpha", "8", "--epochs", "1",
                "--batch-size", "2", "--eval-batch-size", "2", "--cpu-threads", "1"])
    trained = AutoModelForSequenceClassification.from_pretrained(output)
    tokenizer = AutoTokenizer.from_pretrained(output)
    metadata = json.loads((output / "training_config.json").read_text())
    assert metadata["mode"] == mode
    assert trained.config.problem_type == "multi_label_classification"
    if mode == "lora":
        assert (output / "adapter" / "adapter_config.json").exists()
        assert not (output / "adapter_config.json").exists()
        assert metadata["trainable_parameters"] < metadata["total_parameters"]
    for module in (evaluate, export_onnx):
        monkeypatch.setattr(module, "MODEL_DIR", output)
        monkeypatch.setattr(module, "TEST", data / "test.jsonl")
        monkeypatch.setattr(module, "REPORTS", reports)
    monkeypatch.setattr(export_onnx, "OUT", onnx_dir)
    evaluate.main()
    export_onnx.main()
    assert json.loads((reports / "export_report.json").read_text())["mismatch"] == 0
    assert json.loads((reports / "eval_report.json").read_text())["test_size"] == 4

    # 服务仍按固定 data/ch10/onnx 读取；只在临时工作目录制造同结构，完全隔离真实权重。
    service_dir = tmp_path / "data" / "ch10" / "onnx"
    service_dir.parent.mkdir(parents=True)
    onnx_dir.rename(service_dir)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CH10_INFERENCE_BATCH_SIZE", "2")
    monkeypatch.setenv("CH10_INFERENCE_THREADS", "1")
    sys.modules.pop("scripts.ch10.serve", None)
    service = importlib.import_module("scripts.ch10.serve")
    texts = ["买大了想退", "好", "快递到哪了", "好吗", ""]
    batched = service.classify(service.ClassifyIn(texts=texts))
    from fastapi.testclient import TestClient
    with TestClient(service.app) as client:
        assert client.get("/healthz").json() == {"ok": True}
        assert client.post("/classify", json={"texts": texts}).json() == batched
        assert client.post("/classify", json={"texts": 123}).status_code == 422
    monkeypatch.setattr(service, "_batch_size", 100)
    whole = service.classify(service.ClassifyIn(texts=texts))
    assert batched == whole
    assert service.classify(service.ClassifyIn(texts=[])) == {"results": []}
    # 同时核对训练产物重载后的 Torch 概率，避免仅验证两次 ONNX 自洽。
    trained.eval()
    with torch.no_grad():
        probs = torch.sigmoid(trained(**tokenizer(texts, padding=True, return_tensors="pt")).logits).numpy()
    onnx_probs = np.array([list(row["scores"].values()) for row in batched["results"]])
    assert np.allclose(probs, onnx_probs, atol=1e-3)
    sys.modules.pop("scripts.ch10.serve", None)
