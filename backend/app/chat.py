"""Prompt assembly: system prompt + trimmed history, bounded by a character budget."""
from __future__ import annotations


def build_messages(system_prompt: str, history: list[dict], max_chars: int) -> list[dict]:
    """Keep the newest messages that fit in max_chars (the latest message is always kept, truncated if needed)."""
    kept: list[dict] = []
    used = len(system_prompt)
    for m in reversed(history):
        size = len(m["content"])
        if used + size > max_chars:
            if not kept:
                kept.append({"role": m["role"], "content": m["content"][-max(max_chars - used, 200):]})
            break
        kept.append({"role": m["role"], "content": m["content"]})
        used += size
    kept.reverse()
    msgs = [{"role": "system", "content": system_prompt}] if system_prompt.strip() else []
    return msgs + kept


def auto_title(text: str) -> str:
    t = " ".join(text.split())
    return (t[:48] + "…") if len(t) > 48 else (t or "New conversation")
