from pathlib import Path
_dir = Path(__file__).parent


def load(name: str) -> str:
    """Prompts live in files so they can be replaced without code changes."""
    return (_dir / f"{name}.md").read_text(encoding="utf-8")


def load_split(name: str) -> tuple[str, str]:
    t = load(name)
    a, _, b = t.partition("## PLAN STAGE")
    return a.strip(), ("## PLAN STAGE" + b).strip()
