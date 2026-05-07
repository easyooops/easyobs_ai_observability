from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


ROOT = Path("c:/project/syyoo/easyobs_ai_observability/apps/web/public/docs/kr")
SKIP_TAGS = {"script", "style", "code", "pre", "svg", "title", "desc"}
ATTRS_TO_TRANSLATE = ("alt", "title", "aria-label")


def should_skip_text(text: str) -> bool:
    s = text.strip()
    if not s:
        return True
    if re.fullmatch(r"[\d\s\W_]+", s):
        return True
    # Keep CLI/path-like tokens as-is.
    if (
        "/" in s
        or "\\" in s
        or "http://" in s
        or "https://" in s
        or "::" in s
        or "<" in s
        or ">" in s
    ):
        return True
    return False


def translate_via_google_public(text: str) -> str:
    # Public endpoint used by many lightweight tooling scripts.
    # Keep timeout short so one bad request cannot stall the whole batch.
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=en&tl=ko&dt=t&q={quote(text)}"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    chunks = payload[0] if payload and payload[0] else []
    translated = "".join(part[0] for part in chunks if part and part[0])
    return translated or text


def translate_text_cached(text: str, cache: dict[str, str]) -> str:
    if text in cache:
        return cache[text]
    try:
        translated = translate_via_google_public(text)
    except Exception:
        translated = text
    cache[text] = translated
    return translated


def translate_file(path: Path, cache: dict[str, str]) -> None:
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")

    if soup.html and soup.html.has_attr("lang"):
        soup.html["lang"] = "ko"

    for node in list(soup.descendants):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if not isinstance(parent, Tag):
            continue
        if parent.name in SKIP_TAGS:
            continue

        raw = str(node)
        if should_skip_text(raw):
            continue

        # Preserve leading/trailing whitespace exactly.
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        core = raw.strip()
        if not core:
            continue
        translated = translate_text_cached(core, cache)
        node.replace_with((" " * leading) + translated + (" " * trailing))

    for tag in soup.find_all(True):
        if tag.name in SKIP_TAGS:
            continue
        for attr in ATTRS_TO_TRANSLATE:
            if not tag.has_attr(attr):
                continue
            value = str(tag.get(attr, ""))
            if should_skip_text(value):
                continue
            tag[attr] = translate_text_cached(value, cache)

    path.write_text(str(soup), encoding="utf-8")


def main() -> None:
    cache: dict[str, str] = {}
    files = sorted(ROOT.glob("*.html"))
    target_names = set(sys.argv[1:]) if len(sys.argv) > 1 else None
    if target_names:
        files = [f for f in files if f.name in target_names]
    for file in files:
        print(f"Translating {file.name} ...", flush=True)
        translate_file(file, cache)
    print(f"Done. translated files: {len(files)}", flush=True)


if __name__ == "__main__":
    main()
