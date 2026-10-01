"""ch10 ONNX 分类服务：分批推理限制峰值内存，保留标签、阈值与返回顺序。

独立进程 :8110；CH10_INFERENCE_BATCH_SIZE / CH10_INFERENCE_THREADS 控制 CPU 资源。
不加载 Torch/PEFT；LoRA 已在训练完成时合并成普通模型并通过原导出程序转换。
"""
import json
import os
import pathlib

import numpy as np
import onnxruntime as ort
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv
from tokenizers import Tokenizer

from app.core.taxonomy import TOPIC_NAMES
from scripts.ch10.inference_lib import apply_threshold

DIR = pathlib.Path("data/ch10/onnx")

load_dotenv(override=False)
_batch_size = int(os.getenv("CH10_INFERENCE_BATCH_SIZE", "32"))
_threads = int(os.getenv("CH10_INFERENCE_THREADS", "4"))
if _batch_size < 1 or _threads < 1:
    raise ValueError("分类器批量与线程数必须大于零")
_options = ort.SessionOptions()
_options.intra_op_num_threads = _threads

app = FastAPI(title="mewhelp ch10 topic classifier")
_sess = ort.InferenceSession(str(DIR / "model.onnx"), sess_options=_options,
                             providers=["CPUExecutionProvider"])
_tok = Tokenizer.from_file(str(DIR / "tokenizer.json"))
_tok.enable_truncation(max_length=128)
_tok.enable_padding(pad_id=0, pad_token="[PAD]")
_threshold: float = json.loads((DIR / "threshold.json").read_text())["threshold"]


class ClassifyIn(BaseModel):
    texts: list[str]


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/classify")
def classify(body: ClassifyIn):
    results = []
    # 大请求按小批处理，每批独立 padding；结果按原输入顺序串回。
    for start in range(0, len(body.texts), _batch_size):
        encs = _tok.encode_batch(body.texts[start:start + _batch_size])
        feed = {
            "input_ids": np.array([e.ids for e in encs], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encs], dtype=np.int64),
            "token_type_ids": np.array([e.type_ids for e in encs], dtype=np.int64),
        }
        (logits,) = _sess.run(["logits"], feed)
        probs = 1 / (1 + np.exp(-logits))
        preds = apply_threshold(probs, _threshold)   # 与 evaluate 共用同一套阈值+兜底
        for row, pred in zip(probs, preds):
            labels = [TOPIC_NAMES[i] for i in range(len(pred)) if pred[i]]
            results.append({"labels": labels,
                            "scores": {TOPIC_NAMES[i]: round(float(p), 4)
                                       for i, p in enumerate(row)}})
    return {"results": results}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8110)
