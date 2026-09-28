"""Render the setup progress page shown by the double-click launchers.

The page lives at ``.mucaro-runtime/ui/progress.html``. ``Mucaro AI.app`` and ``Mucaro AI.exe`` open it in
the default browser before setup starts; each setup step rewrites it. While setup runs it reloads itself
every second (meta refresh), and once the app is ready it redirects to the app. It has no JavaScript,
so it behaves the same for ``file://`` pages in every browser.

``scripts/progress_page.sh`` and ``scripts/one_click_windows.ps1`` render the same template the same way;
``backend/tests/test_desktop_launchers.py`` checks the bash copy against this one. Standard library only.
"""

from __future__ import annotations

import html
import os
from pathlib import Path

TEMPLATE = Path(__file__).resolve().with_name("one_click_progress.html")
STATES = ("running", "ready", "error", "stopped")
TOTAL_STEPS = 6


def refresh_tag(state: str, url: str) -> str:
    if state == "running":
        return '<meta http-equiv="refresh" content="1">'
    if state == "ready" and url:
        return f'<meta http-equiv="refresh" content="0; url={html.escape(url, quote=True)}">'
    return ""


def percent(state: str, step: int, total: int) -> int:
    if state in ("ready", "stopped"):
        return 100
    if total <= 0:
        return 0
    return max(0, min(100, int(step * 100 / total)))


def render(
    state: str,
    *,
    step: int = 0,
    total: int = TOTAL_STEPS,
    title: str = "",
    detail: str = "",
    log: str = "",
    url: str = "",
    template: str | None = None,
) -> str:
    if state not in STATES:
        raise ValueError(f"unknown progress state {state!r}")
    page = TEMPLATE.read_text(encoding="utf-8") if template is None else template
    values = {
        "{{STATE}}": state,
        "{{STEP}}": str(step),
        "{{TOTAL}}": str(total),
        "{{PERCENT}}": str(percent(state, step, total)),
        "{{TITLE}}": html.escape(title, quote=True),
        "{{DETAIL}}": html.escape(detail, quote=True),
        "{{LOG}}": html.escape(log, quote=True),
        "{{URL}}": html.escape(url, quote=True),
    }
    # REFRESH first: it is already-escaped markup and must not be escaped again.
    page = page.replace("{{REFRESH}}", refresh_tag(state, url))
    for placeholder, value in values.items():
        page = page.replace(placeholder, value)
    return page


def write(path: Path | str, state: str, **fields: object) -> None:
    """Render and replace the page atomically, so a reload never sees a half-written file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(render(state, **fields), encoding="utf-8")  # type: ignore[arg-type]
    os.replace(tmp, target)


def try_write(path: Path | str | None, state: str, **fields: object) -> None:
    """Best-effort update: a progress page that cannot be written must never stop setup or the app."""
    if not path:
        return
    try:
        write(path, state, **fields)
    except (OSError, ValueError):
        pass
