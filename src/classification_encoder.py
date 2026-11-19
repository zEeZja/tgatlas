"""Bounded CPU embeddings: pinned safe weights, dynamic INT8, batched disk cache."""

import gc
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import time


def validate_settings(settings):
    for key, low, high in [
        ("dimension", 1, 4096),
        ("max_tokens", 32, 512),
        ("batch_size", 1, 64),
        ("threads", 1, 8),
    ]:
        value = settings.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not low <= value <= high
        ):
            raise ValueError("Некорректные ограничения encoder: " + key)
    if (
        settings.get("precision") not in ("int8", "fp32")
        or settings.get("pooling") not in ("mean", "cls")
        or not isinstance(settings.get("revision"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", settings["revision"])
        or not isinstance(settings.get("name"), str)
        or not settings["name"]
        or not isinstance(settings.get("prefix"), str)
        or len(settings["prefix"]) > 100
    ):
        raise ValueError("Некорректный encoder/pooling/precision/revision")


class CachedEncoder:
    """No model download during classification; all-hit runs do not load torch.

    Cache identity includes preprocessing, precision and token limit. Binary
    vectors are little-endian float32, never pickle/joblib. Model loading is lazy.
    """

    def __init__(self, settings, cache_path, *, model_factory=None):
        validate_settings(settings)
        self.settings = dict(settings)
        self.name = settings["name"]
        self.revision = settings["revision"]
        self.dimension = settings["dimension"]
        if (
            not isinstance(self.dimension, int)
            or isinstance(self.dimension, bool)
            or not 1 <= self.dimension <= 4096
            or settings["precision"] not in ("int8", "fp32")
            or settings["pooling"] not in ("mean", "cls")
            or not 32 <= settings["max_tokens"] <= 512
            or not 1 <= settings["batch_size"] <= 64
            or not 1 <= settings["threads"] <= 8
            or not isinstance(self.revision, str)
            or len(self.revision) != 40
        ):
            raise ValueError("Некорректные ограничения encoder")
        self.identity = hashlib.sha256(
            json.dumps(
                dict(
                    format="atlas-embeddings-v10.1",
                    **{
                        k: v
                        for k, v in self.settings.items()
                        if k not in ("batch_size", "threads")
                    },
                ),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        path = Path(cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.cache = sqlite3.connect(path, timeout=30)
        self.cache.execute(
            "CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector BLOB NOT NULL)"
        )
        self.cache.commit()
        self.model_factory = model_factory
        self.model = self.tokenizer = None
        self.stats = dict(
            cache_hits=0,
            computed_documents=0,
            model_load_seconds=0.0,
            encode_seconds=0.0,
        )

    def _load(self):
        if self.model is not None:
            return
        started = time.perf_counter()
        import torch
        from transformers import AutoModel, AutoTokenizer

        torch.set_num_threads(self.settings["threads"])
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.name,
            revision=self.revision,
            local_files_only=True,
            trust_remote_code=False,
        )
        self.model = (
            AutoModel.from_pretrained(
                self.name,
                revision=self.revision,
                local_files_only=True,
                trust_remote_code=False,
                use_safetensors=True,
            )
            .float()
            .eval()
        )
        if self.model.config.hidden_size != self.dimension:
            raise ValueError("Размерность encoder не соответствует конфигурации")
        if self.settings["precision"] == "int8":
            torch.ao.quantization.quantize_dynamic(
                self.model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True
            )
        self.stats["model_load_seconds"] += time.perf_counter() - started

    def _compute(self, texts):
        if self.model_factory is not None:
            return self.model_factory(texts)
        self._load()
        tokens = self.tokenizer(
            [self.settings["prefix"] + text for text in texts],
            padding=True,
            truncation=True,
            max_length=self.settings["max_tokens"],
            return_tensors="pt",
        )
        with self.torch.inference_mode():
            hidden = self.model(**tokens).last_hidden_state
            if self.settings["pooling"] == "cls":
                vectors = hidden[:, 0]
            else:
                mask = tokens["attention_mask"].unsqueeze(-1)
                vectors = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            return self.torch.nn.functional.normalize(vectors, p=2, dim=1).cpu().numpy()

    def encode(self, texts, batch_size=None):
        import numpy as np

        started = time.perf_counter()
        batch_size = batch_size or self.settings["batch_size"]
        if not isinstance(batch_size, int) or not 1 <= batch_size <= 64:
            raise ValueError("Некорректный размер пакета")
        texts = [str(text)[:6000] for text in texts]
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        keys = [
            hashlib.sha256((self.identity + "\0" + text).encode()).hexdigest()
            for text in texts
        ]
        ready, pending = {}, {}
        for key, text in zip(keys, texts):
            if key in ready or key in pending:
                continue
            row = self.cache.execute(
                "SELECT vector FROM embeddings WHERE key=?", (key,)
            ).fetchone()
            if row and isinstance(row[0], bytes) and len(row[0]) == self.dimension * 4:
                vector = np.frombuffer(row[0], dtype="<f4").copy()
                if (
                    np.isfinite(vector).all()
                    and abs(float(np.linalg.norm(vector)) - 1) < 0.01
                ):
                    ready[key] = vector
                    continue
            pending[key] = text
        self.stats["cache_hits"] += sum(key in ready for key in keys)
        # Length sorting reduces padding; scatter back by content key, not batch order.
        todo = sorted(pending.items(), key=lambda item: (len(item[1]), item[0]))
        for offset in range(0, len(todo), batch_size):
            batch = todo[offset : offset + batch_size]
            vectors = np.asarray(
                self._compute([text for _, text in batch]), dtype=np.float32
            )
            if (
                vectors.shape != (len(batch), self.dimension)
                or not np.isfinite(vectors).all()
            ):
                raise ValueError("Encoder вернул некорректные векторы")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if (norms < 1e-12).any():
                raise ValueError("Encoder вернул нулевой вектор")
            vectors = vectors / norms
            records = []
            for (key, _), vector in zip(batch, vectors):
                ready[key] = vector
                records.append((key, vector.astype("<f4").tobytes()))
            with self.cache:
                self.cache.executemany(
                    "INSERT OR REPLACE INTO embeddings VALUES (?,?)", records
                )
            self.stats["computed_documents"] += len(batch)
        self.stats["encode_seconds"] += time.perf_counter() - started
        return np.asarray([ready[key] for key in keys], dtype=np.float32)

    def close(self):
        self.cache.close()
        self.model = self.tokenizer = None
        gc.collect()


def peak_memory_mb():
    """Peak working set on Windows, max RSS on Unix; no sampling thread."""
    import os

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize / 1024**2
    import resource
    import sys

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024**2 if sys.platform == "darwin" else 1024)
