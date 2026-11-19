"""LLM layer: GemmaLLM (Gemma 4 via Gemini API, google-genai) and MockLLM (offline, deterministic).

Three operations only: classify (structured JSON), run_tools (manual function-calling loop), generate (free text).
"""
from __future__ import annotations
import hashlib, json, re, threading, time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from pydantic import BaseModel, ValidationError
from .config import settings


class LLMError(Exception):
    pass


class LLMFormatError(LLMError):
    pass


@dataclass
class ToolRunResult:
    calls: list[dict] = field(default_factory=list)   # {name,args,result,duration_ms}
    final_text: str = ""
    steps: int = 0


class LLM(Protocol):
    name: str
    def classify(self, system: str, user: str, schema: type[BaseModel], tier: str = "fast") -> BaseModel: ...
    def run_tools(self, system: str, messages: list[dict], tools: list[dict], tool_executor: Callable[[str, dict], dict], max_steps: int = 6, tier: str = "strong") -> ToolRunResult: ...
    def generate(self, system: str, messages: list[dict], tier: str = "fast") -> str: ...
    def pop_meta(self) -> dict: ...


_meta = threading.local()


def _add_meta(model: str, tokens_in: int, tokens_out: int, ms: int, cached: bool) -> None:
    m = getattr(_meta, "m", None) or {"model": model, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0, "calls": 0, "cached": 0}
    m["model"] = model
    m["tokens_in"] += tokens_in
    m["tokens_out"] += tokens_out
    m["latency_ms"] += ms
    m["calls"] += 1
    m["cached"] += 1 if cached else 0
    _meta.m = m


def pop_meta() -> dict:
    m = getattr(_meta, "m", None) or {}
    _meta.m = None
    return m


def parse_json(text: str) -> Any:
    """Direct parse, then ```json fence, then first balanced {...}."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            depth += text[i] == "{"
            depth -= text[i] == "}"
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break
        start = text.find("{", start + 1)
    raise json.JSONDecodeError("no JSON object found", text, 0)


class GemmaLLM:
    name = "gemma"

    def __init__(self) -> None:
        from google import genai
        from google.genai import types
        self._types = types
        self.client = genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(timeout=25000))
        self.sem = threading.Semaphore(2)
        self.sys_in_user = False          # fallback when the API rejects system_instruction
        self.struct_mode = "json_schema"  # json_schema -> json_mime -> plain
        settings.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ plumbing
    def _model(self, tier: str) -> str:
        return settings.model_strong if tier == "strong" else settings.model_fast

    def _cache_path(self, key: dict):
        h = hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:32]
        return settings.cache_dir / f"{h}.json"

    def _call(self, model: str, system: str, contents: list, config_kw: dict, cache_key: dict | None) -> dict:
        """Returns {"text": str, "calls": [{name,args,id}], "tokens_in", "tokens_out"}. Retries on 429/5xx; caches."""
        t = self._types
        path = self._cache_path(cache_key) if (cache_key and settings.llm_cache) else None
        if path and path.exists():
            d = json.loads(path.read_text("utf-8"))
            _add_meta(model, d.get("tokens_in", 0), d.get("tokens_out", 0), 0, True)
            return d
        last: Exception | None = None
        for attempt in range(3):
            t0 = time.monotonic()
            try:
                cfg_kw = dict(config_kw)
                cont = contents
                if self.sys_in_user:
                    cont = [t.Content(role="user", parts=[t.Part(text="[SYSTEM INSTRUCTIONS]\n" + system + "\n[END SYSTEM INSTRUCTIONS]")])] + contents
                else:
                    cfg_kw["system_instruction"] = system
                with self.sem:
                    resp = self.client.models.generate_content(model=model, contents=cont, config=t.GenerateContentConfig(**cfg_kw))
                out = self._unpack(resp)
                ms = int((time.monotonic() - t0) * 1000)
                _add_meta(model, out["tokens_in"], out["tokens_out"], ms, False)
                if path:
                    path.write_text(json.dumps(out), "utf-8")
                return out
            except Exception as e:  # noqa: BLE001
                code = getattr(e, "code", None) or getattr(e, "status_code", None)
                msg = str(e)
                if code == 400 and "instruction" in msg.lower() and not self.sys_in_user:
                    self.sys_in_user = True          # fallback flag, retry immediately
                    continue
                last = e
                if code in (429, 500, 502, 503, 504) or "timed out" in msg.lower() or "timeout" in msg.lower():
                    time.sleep(1.5 * (2 ** attempt))
                    continue
                break
        raise LLMError(f"Gemma call failed: {type(last).__name__}: {str(last)[:200]}")

    def _unpack(self, resp) -> dict:
        text, calls = [], []
        cand = (resp.candidates or [None])[0]
        parts = (cand.content.parts if cand and cand.content and cand.content.parts else []) or []
        for p in parts:
            if getattr(p, "thought", False):
                continue                                  # never expose "thinking" parts
            if getattr(p, "function_call", None):
                fc = p.function_call
                calls.append({"name": fc.name, "args": dict(fc.args or {}), "id": getattr(fc, "id", None)})
            elif getattr(p, "text", None):
                text.append(p.text)
        um = getattr(resp, "usage_metadata", None)
        return {"text": "".join(text), "calls": calls,
                "tokens_in": getattr(um, "prompt_token_count", 0) or 0, "tokens_out": getattr(um, "candidates_token_count", 0) or 0}

    def _to_contents(self, messages: list[dict]) -> list:
        t = self._types
        return [t.Content(role="model" if m["role"] in ("assistant", "model") else "user", parts=[t.Part(text=m["content"])]) for m in messages]

    # ------------------------------------------------------------ operations
    def classify(self, system: str, user: str, schema: type[BaseModel], tier: str = "fast") -> BaseModel:
        model = self._model(tier)
        sys_full = system + "\n\nReturn ONLY a JSON object matching this JSON Schema:\n" + json.dumps(schema.model_json_schema())
        err_note = ""
        for attempt in range(2):                          # one retry with the validation error
            cfg: dict = {"temperature": 0, "response_mime_type": "application/json"}
            if self.struct_mode == "json_schema":
                cfg["response_json_schema"] = schema.model_json_schema()
            elif self.struct_mode == "plain":
                cfg.pop("response_mime_type")
            contents = self._to_contents([{"role": "user", "content": user + err_note}])
            try:
                out = self._call(model, sys_full, contents, cfg, {"op": "classify", "m": model, "s": sys_full, "u": user + err_note, "mode": self.struct_mode})
            except LLMError as e:
                if "400" in str(e) and self.struct_mode != "plain":
                    self.struct_mode = "json_mime" if self.struct_mode == "json_schema" else "plain"
                    continue
                raise
            try:
                return schema.model_validate(parse_json(out["text"]))
            except (json.JSONDecodeError, ValidationError) as e:
                err_note = f"\n\nYour previous output was invalid ({str(e)[:300]}). Return ONLY valid JSON matching the schema."
        raise LLMFormatError(f"{schema.__name__}: model did not return valid JSON")

    def generate(self, system: str, messages: list[dict], tier: str = "fast") -> str:
        model = self._model(tier)
        out = self._call(model, system, self._to_contents(messages), {"temperature": 0.2},
                         {"op": "gen", "m": model, "s": system, "u": messages})
        if not out["text"].strip():
            raise LLMFormatError("empty generation")
        return out["text"].strip()

    def run_tools(self, system, messages, tools, tool_executor, max_steps=6, tier="strong") -> ToolRunResult:
        t = self._types
        model = self._model(tier)
        decls = [t.FunctionDeclaration(name=d["name"], description=d["description"], parameters_json_schema=d["parameters"]) for d in tools]
        cfg = {"temperature": 0.2, "tools": [t.Tool(function_declarations=decls)],
               "automatic_function_calling": t.AutomaticFunctionCallingConfig(disable=True)}
        contents = self._to_contents(messages)
        res = ToolRunResult()
        for step in range(max_steps):
            out = self._call(model, system, contents, cfg, {"op": "tools", "m": model, "s": system, "c": [c.model_dump(mode="json", exclude_none=True) for c in contents]})
            res.steps = step + 1
            if not out["calls"]:
                res.final_text = out["text"]
                return res
            contents.append(t.Content(role="model", parts=[t.Part(function_call=t.FunctionCall(name=c["name"], args=c["args"], id=c.get("id"))) for c in out["calls"]]))
            resp_parts = []
            for c in out["calls"]:
                t0 = time.monotonic()
                result = tool_executor(c["name"], c["args"])
                res.calls.append({"name": c["name"], "args": c["args"], "result": result, "duration_ms": int((time.monotonic() - t0) * 1000)})
                resp_parts.append(t.Part(function_response=t.FunctionResponse(name=c["name"], id=c.get("id"), response={"result": json.loads(json.dumps(result, default=str)[:12000]) if len(json.dumps(result, default=str)) <= 12000 else {"truncated": True}})))
            contents.append(t.Content(role="user", parts=resp_parts))
        return res                                        # max_steps reached; pipeline continues with what was gathered

    def pop_meta(self) -> dict:
        return pop_meta()


_instance: LLM | None = None


def get_llm() -> LLM:
    global _instance
    if _instance is None:
        if settings.llm_provider == "gemma":
            _instance = GemmaLLM()
        else:
            from .llm_mock import MockLLM
            _instance = MockLLM()
    return _instance
