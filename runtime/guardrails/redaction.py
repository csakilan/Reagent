"""Secret and PII redaction. WF1.

Wraps FOUR paths, not one: model output, tool results, trace payloads, logs.
Regex rules plus an exact-match list of the known demo secrets, because a regex
miss on camera loses the round. Hits are reported by rule name and count,
never by the matched text.

Pure Python on purpose: no LangChain import, so it is testable in CI and usable
from a logging filter.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

Location = str  # "model_output" | "tool_result" | "trace" | "log"

#: Order matters: specific rules first, so the hit names the real kind.
SECRET_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    # The 40-char secret. Keyword-anchored form first, then the bare shape when
    # it has the mixed case + slash/plus texture of a real key.
    ("aws_secret_access_key", re.compile(
        r"(?i)(?<=aws_secret_access_key)(\s*[:=]\s*[\"']?)[A-Za-z0-9/+=]{40}")),
    ("aws_secret_access_key", re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("openrouter_key", re.compile(r"\bsk-or-(?:v1-)?[A-Za-z0-9]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("connection_string", re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:/@]+:[^\s@]+@[^\s\"']+")),
    ("password_assignment", re.compile(
        r"(?i)(?<=password)(\s*[:=]\s*[\"']?)[^\s\"']{6,}")),
]

PII_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("us_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
]

def _looks_like_aws_secret(s: str) -> bool:
    """The bare 40-char shape also matches hashes and paths. A real secret key
    mixes upper, lower and digits, and has at most a few / or +."""
    return (any(c.islower() for c in s) and any(c.isupper() for c in s)
            and any(c.isdigit() for c in s) and s.count("/") + s.count("+") <= 4
            and not re.fullmatch(r"[0-9a-f]{40}", s))


def _looks_like_card(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if not 13 <= len(digits) <= 16:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):  # Luhn, so PR numbers and ids survive
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


_VALIDATORS = {"aws_secret_access_key": _looks_like_aws_secret, "credit_card": _looks_like_card}

#: Known demo secrets. Exact match beats any regex: these are the values that
#: are planted in fixtures and will be on screen.
DEMO_SECRETS: list[str] = [
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "wJalrXUtnFEMI",
    "AKIAIOSFODNN7EXAMPLE",
]

#: Env vars whose live values are redacted wherever they appear.
SECRET_ENV_VARS = (
    "OPENROUTER_API_KEY", "DATABASE_URL", "GITHUB_BOT_TOKEN", "GITHUB_USER_TOKEN",
    "GITHUB_WEBHOOK_SECRET", "GITHUB_APP_TOKEN", "CONNECTOR_SHARED_SECRET", "SLACK_BOT_TOKEN", "SLACK_USER_TOKEN", "SLACK_SIGNING_SECRET",
)


def _mask(rule: str) -> str:
    return f"[REDACTED:{rule}]"


@dataclass
class Redactor:
    secrets: bool = True
    pii: bool = False
    exact: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        values = list(DEMO_SECRETS) + list(self.exact)
        for var in SECRET_ENV_VARS:
            v = os.environ.get(var, "")
            if len(v) >= 8:
                values.append(v)
        # Longest first so a full key is masked before its prefix is.
        self._exact = sorted({v for v in values if v}, key=len, reverse=True)

    @classmethod
    def from_effective(cls, effective: dict[str, Any] | None) -> "Redactor":
        """Build from effective.guardrails.redaction. Secrets redaction is never
        switched off at runtime: it is an enterprise safety flag, and a resolver
        bug must not be what leaks a key on screen."""
        red = ((effective or {}).get("guardrails") or {}).get("redaction") or {}
        return cls(secrets=True, pii=bool(red.get("pii", False)))

    def redact_text(self, text: str) -> tuple[str, dict[str, int]]:
        hits: dict[str, int] = {}
        if not text:
            return text, hits
        for value in self._exact:
            if value in text:
                n = text.count(value)
                text = text.replace(value, _mask("known_secret"))
                hits["known_secret"] = hits.get("known_secret", 0) + n
        rules: list[tuple[str, re.Pattern[str]]] = list(SECRET_RULES)
        if self.pii:
            rules += PII_RULES
        for name, rx in rules:
            def sub(m: re.Match[str], _name: str = name) -> str:
                check = _VALIDATORS.get(_name)
                if check and not rx.groups and not check(m.group(0)):
                    return m.group(0)
                hits[_name] = hits.get(_name, 0) + 1
                # Keep a captured `key = ` prefix so the context stays readable.
                prefix = m.group(1) if rx.groups and m.group(1) else ""
                return prefix + _mask(_name)
            text = rx.sub(sub, text)
        return text, hits

    def redact(self, obj: Any) -> tuple[Any, dict[str, int]]:
        """Recursively redact strings inside dicts/lists. Keys are left alone."""
        total: dict[str, int] = {}

        def walk(o: Any) -> Any:
            if isinstance(o, str):
                out, h = self.redact_text(o)
                for k, v in h.items():
                    total[k] = total.get(k, 0) + v
                return out
            if isinstance(o, dict):
                return {k: walk(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [walk(v) for v in o]
            return o

        return walk(obj), total


_default = Redactor()


def redact_text(text: str) -> str:
    return _default.redact_text(text)[0]


def redact(obj: Any) -> Any:
    return _default.redact(obj)[0]


class RedactingFilter(logging.Filter):
    """The fourth path: logs. Install on a handler or logger."""

    def __init__(self, redactor: Redactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor or _default

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — a bad format string must not drop the record
            msg = str(record.msg)
        record.msg = self.redactor.redact_text(msg)[0]
        record.args = None
        return True


def install_log_redaction(loggers: Iterable[str] = ("", "runtime", "uvicorn", "httpx")) -> None:
    f = RedactingFilter()
    for name in loggers:
        lg = logging.getLogger(name)
        if not any(isinstance(x, RedactingFilter) for x in lg.filters):
            lg.addFilter(f)
        for h in lg.handlers:
            if not any(isinstance(x, RedactingFilter) for x in h.filters):
                h.addFilter(f)
