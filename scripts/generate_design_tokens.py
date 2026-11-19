#!/usr/bin/env python3
"""Generate the CSS and TypeScript design tokens from apps/web/design/tokens.json.

Usage:  python3 scripts/generate_design_tokens.py          # write the outputs
        python3 scripts/generate_design_tokens.py --check  # exit 1 if an output is stale
Standard library only.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "web" / "design"))

from tokengen import load_tokens, render_css, render_typescript  # noqa: E402

OUTPUTS = {
    ROOT / "apps" / "web" / "src" / "styles" / "tokens.css": render_css,
    ROOT / "packages" / "ui" / "src" / "tokens.ts": render_typescript,
}


def main(argv: list[str]) -> int:
    tokens = load_tokens()
    stale = 0
    for path, render in OUTPUTS.items():
        text = render(tokens)
        rel = path.relative_to(ROOT)
        if "--check" in argv:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                print(f"{rel} is stale; run scripts/generate_design_tokens.py")
                stale += 1
            else:
                print(f"{rel} is up to date")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {rel}")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
