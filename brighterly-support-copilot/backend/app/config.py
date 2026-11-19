"""Central configuration. Secrets come only from the environment / .env file."""
from __future__ import annotations
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent


def _load_env_file() -> None:
    for p in (BACKEND_DIR / ".env", ROOT_DIR / ".env"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file()


class Settings:
    def __init__(self) -> None:
        g = os.environ.get
        self.llm_provider = g("LLM_PROVIDER", "mock").lower()
        self.gemini_api_key = g("GEMINI_API_KEY", "")
        self.model_fast = g("GEMMA_MODEL_FAST", "gemma-4-26b-a4b-it")
        self.model_strong = g("GEMMA_MODEL_STRONG", "gemma-4-31b-it")
        self.llm_cache = g("LLM_CACHE", "1") == "1"
        self.demo_now = g("DEMO_NOW", "2026-10-31T12:00:00+00:00")
        self.session_secret = g("SESSION_SECRET", "dev-secret-change-me")
        self.admin_token = g("ADMIN_TOKEN", "demo-admin-token")
        self.prompt_version = g("PROMPT_VERSION", "2026-10-31.1")
        self.data_dir = Path(g("DATA_DIR", str(ROOT_DIR / "data")))
        self.cache_dir = BACKEND_DIR / "cache" / "llm"
        self.mock_latency_ms = int(g("MOCK_LATENCY_MS", "450"))   # simulated latency so the mock demo streams visibly
        self.max_message_chars = 2000
        self.rate_limit_per_min = int(g("RATE_LIMIT_PER_MIN", "20"))

    def validate(self) -> None:
        if self.llm_provider == "gemma" and not self.gemini_api_key:
            raise SystemExit(
                "GEMINI_API_KEY is not set. Set it in backend/.env, or run with LLM_PROVIDER=mock "
                "for the offline deterministic provider."
            )
        if self.llm_provider not in ("gemma", "mock"):
            raise SystemExit("LLM_PROVIDER must be 'gemma' or 'mock'.")


settings = Settings()
