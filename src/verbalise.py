"""Scored feature row -> one English sentence.

Python formats every number into a string first. Gemma (gemma3:4b via a local
Ollama) only rewords a fixed template; it is told not to compute, compare,
rank or round, and it never receives free-text registry fields (only the
package name, which is checked to be a plain npm name). If Ollama is not
reachable, or Gemma's output contains a number that was not passed in, the
pure-Python template sentence is returned instead.
"""
import re

import httpx

OLLAMA = "http://127.0.0.1:11434/api/generate"
MODEL = "gemma3:4b"
_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def slots(name: str, row: dict, p: float) -> dict:
    if not _NAME.match(name):
        name = "this package"
    return {
        "name": name,
        "p": f"{p:.2f}",
        "days": f"{row['days_since_last_release']:.0f}",
        "r12": f"{int(row['releases_12m'])}",
        "total": f"{int(row['total_versions'])}",
        "years": f"{row['package_age_days'] / 365.25:.1f}",
    }


TEMPLATE = ("{name} has a model probability of {p} of publishing no new version in the next 12 months; "
            "its last release was {days} days ago, it published {r12} releases in the past 12 months, "
            "and it has {total} releases over {years} years.")


def template_sentence(s: dict) -> str:
    return TEMPLATE.format(**s)


def numbers(text: str) -> set[str]:
    return set(_NUM.findall(text))


def verbalise(name: str, row: dict, p: float, timeout: float = 60) -> tuple[str, str]:
    """Return (sentence, source) where source is 'gemma' or 'template'."""
    s = slots(name, row, p)
    base = template_sentence(s)
    prompt = (
        "Rewrite the sentence below as one plain, friendly English sentence for a developer. "
        "Copy every number exactly as written, character for character. Do not add, remove, compute, "
        "compare, rank or round any number. Do not add facts. Output only the sentence.\n\n" + base)
    try:
        r = httpx.post(OLLAMA, json={"model": MODEL, "prompt": prompt, "stream": False,
                                     "options": {"temperature": 0, "seed": 20261031}}, timeout=timeout)
        r.raise_for_status()
        out = " ".join(r.json()["response"].split())
    except (httpx.HTTPError, KeyError, ValueError):
        return base, "template"
    if not out or not numbers(out) <= numbers(base):
        return base, "template"
    return out, "gemma"
