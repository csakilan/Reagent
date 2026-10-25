"""Glob matching and glob-to-glob subset testing. FROZEN D18.

ONE function decides whether a child pattern is inside a parent scope, and every
caller (actions, resources, models, channels, deny rules) goes through it. The
prototype validator had two algorithms that disagreed; the looser one used
`rstrip("*")` and accepted `nmc2/platform-api*` against `nmc2/platform-api`,
which also matches `nmc2/platform-api-secrets`.

Grammar: `*` matches any run of characters except `/`. Every other character is
literal (no `?`, no `[...]`), so `fnmatch` is deliberately not used.

`is_subset(child, parents)` is exact, not a heuristic: it answers "is every
string `child` can match also matched by at least one parent?" by building a
small automaton for each side and searching their product for a counterexample.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Iterable

# A stand-in for "any character that appears literally in none of the patterns".
_OTHER = "\x00"


@lru_cache(maxsize=4096)
def _tokens(pattern: str) -> tuple[str, ...]:
    """Split into literal characters and '*' tokens, collapsing runs of '*'."""
    out: list[str] = []
    for ch in pattern:
        if ch == "*" and out and out[-1] == "*":
            continue
        out.append(ch)
    return tuple(out)


def _step(tokens: tuple[str, ...], states: frozenset[int], ch: str) -> frozenset[int]:
    nxt: set[int] = set()
    for s in states:
        if s >= len(tokens):
            continue
        t = tokens[s]
        if t == "*":
            if ch != "/":
                nxt.add(s)          # '*' consumes ch and stays
        elif t == ch:
            nxt.add(s + 1)
    return _closure(tokens, frozenset(nxt))


def _closure(tokens: tuple[str, ...], states: frozenset[int]) -> frozenset[int]:
    """'*' may match the empty string, so it can be skipped."""
    out = set(states)
    stack = list(states)
    while stack:
        s = stack.pop()
        if s < len(tokens) and tokens[s] == "*" and s + 1 not in out:
            out.add(s + 1)
            stack.append(s + 1)
    return frozenset(out)


def _accepts(tokens: tuple[str, ...], states: frozenset[int]) -> bool:
    return len(tokens) in states


def match(value: str, pattern: str) -> bool:
    """True when the concrete string `value` matches `pattern`."""
    toks = _tokens(pattern)
    states = _closure(toks, frozenset({0}))
    for ch in value:
        states = _step(toks, states, ch)
        if not states:
            return False
    return _accepts(toks, states)


def match_any(value: str, patterns: Iterable[str]) -> bool:
    return any(match(value, p) for p in patterns)


def is_subset(child: str, parents: Iterable[str]) -> bool:
    """True when every string `child` matches is matched by some parent."""
    parents = tuple(parents)
    if not parents:
        return False
    if "*" not in child:
        return match_any(child, parents)

    ct = _tokens(child)
    pts = [_tokens(p) for p in parents]
    alphabet = {c for t in [ct, *pts] for c in t if c != "*"} | {"/", _OTHER}

    start = (_closure(ct, frozenset({0})),
             tuple(_closure(pt, frozenset({0})) for pt in pts))
    seen = {start}
    frontier = [start]
    while frontier:
        cs, ps = frontier.pop()
        if _accepts(ct, cs) and not any(_accepts(pt, s) for pt, s in zip(pts, ps)):
            return False            # a string the child matches and no parent does
        for ch in alphabet:
            ncs = _step(ct, cs, ch)
            if not ncs:
                continue
            nps = tuple(_step(pt, s, ch) for pt, s in zip(pts, ps))
            node = (ncs, nps)
            if node not in seen:
                seen.add(node)
                frontier.append(node)
    return True


def intersect_scopes(own: list[str], scope: list[str]) -> list[str]:
    """A conservative intersection of two pattern lists, used for FROZEN D1.

    Keeps an own pattern that sits inside the scope, and a scope pattern that
    sits inside the own set. The result is always a subset of both sides.
    """
    out = [p for p in own if is_subset(p, scope)]
    out += [p for p in scope if p not in out and is_subset(p, own)]
    return out
