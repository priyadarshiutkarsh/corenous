"""Shared application context — opened once per CLI invocation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


def _load_cfg(config_path: Path) -> dict:
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


@dataclass
class AppContext:
    config_path: Path
    cfg: dict = field(default_factory=dict)
    _data_dir_override: Path | None = field(default=None, repr=False)

    _store: object = field(default=None, repr=False)
    _cache: object = field(default=None, repr=False)
    _vault: object = field(default=None, repr=False)

    @classmethod
    def load(cls, root: Path | None = None) -> "AppContext":
        """Resolve the config path and load the YAML.

        Inside a frozen ``Corenous.app`` we deliberately ignore ``root``
        and ask :mod:`src.paths` for the canonical bundle paths so the
        same code base behaves correctly both from source and packaged."""
        from ..paths import IS_BUNDLED, default_config_path, default_data_dir

        if IS_BUNDLED:
            config_path = default_config_path()
            cfg = _load_cfg(config_path)
            ctx = cls(
                config_path=config_path,
                cfg=cfg,
                _data_dir_override=default_data_dir(),
            )
        else:
            root = root or Path.cwd()
            config_path = root / "config" / "settings.yaml"
            cfg = _load_cfg(config_path)
            ctx = cls(config_path=config_path, cfg=cfg)
        from ..ai.llm import configure_local_llm

        configure_local_llm(config_path=ctx.config_path, cfg=ctx.cfg)
        return ctx

    @property
    def data_dir(self) -> Path:
        if self._data_dir_override is not None:
            self._data_dir_override.mkdir(parents=True, exist_ok=True)
            return self._data_dir_override
        d = Path(self.cfg.get("memory", {}).get("data_dir", "data"))
        if not d.is_absolute():
            d = self.config_path.parent.parent / d
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def store(self):
        if self._store is None:
            from ..memory.store import MemoryStore
            db_name = self.cfg.get("memory", {}).get("db_filename", "memories.db")
            self._store = MemoryStore(self.data_dir / db_name)
        return self._store

    @property
    def cache(self):
        if self._cache is None:
            from ..memory.vector_cache import VectorCache
            vec_name = self.cfg.get("memory", {}).get("vectors_filename", "vectors.npy")
            self._cache = VectorCache(self.data_dir / vec_name)
            self._cache.load_from_store(self.store.get_all_compressed_vectors())
        return self._cache

    @property
    def vault(self):
        if self._vault is None:
            from ..privacy.vault import Vault
            self._vault = Vault(self.store)
        return self._vault
