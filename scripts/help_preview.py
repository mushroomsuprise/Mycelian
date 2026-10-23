# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Preview help pages outside the app.

Builds standalone HTML files that load the same help CSS/JS and theme
variables the app uses, so pages and their mock UIs can be checked under any
theme without launching Mycelian. On macOS, ``--png`` also renders each page to
a screenshot with WebKit (needs PyObjC, which pywebview already installs).

Examples:
    python scripts/help_preview.py twitch_setup
    python scripts/help_preview.py twitch_setup --theme light --png
    python scripts/help_preview.py --all --out /tmp/help-preview
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.help_system.help_content import HELP_TOPICS  # noqa: E402
from modules.help_system.help_render import (  # noqa: E402
    clear_caches,
    help_page_css,
    help_page_js,
    render_topic_html,
)
from modules.theme_manager import ThemeColors, generate_css_variables  # noqa: E402
from modules.ui_styles import get_full_theme_css  # noqa: E402


def _nicegui_static() -> Path:
    import nicegui

    return Path(nicegui.__file__).resolve().parent / "static"


def build_page(topic_id: str, theme: str, width: int) -> str:
    topic = HELP_TOPICS[topic_id]
    theme_file = ROOT / "themes" / f"{theme}_theme.json"
    colors = ThemeColors(**json.loads(theme_file.read_text(encoding="utf-8")))
    body_html, toc = render_topic_html(topic_id)
    fonts = (_nicegui_static() / "fonts.css").as_uri()
    toc_html = "".join(
        f'<div class="pv-toc-{lvl}">{html.escape(text)}</div>' for lvl, text, _ in toc
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="{fonts}">
<style>{generate_css_variables(colors)}</style>
<style>{get_full_theme_css()}</style>
<style>
body {{ margin: 0 !important; background: var(--color-bg-elevated); color: var(--color-text-primary);
       font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }}
.pv {{ display: flex; gap: 24px; padding: 24px; width: {width}px; box-sizing: border-box; }}
.pv-main {{ flex: 1; min-width: 0; }}
.pv-toc {{ width: 200px; font-size: 12px; color: var(--color-text-muted); }}
.pv-toc-h3 {{ padding-left: 12px; }}
.pv-badge {{ display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 999px;
            background: var(--color-primary-light); color: var(--color-primary); margin-bottom: 8px; }}
.pv-title {{ font-size: 1.5rem; font-weight: 700; margin: 0 0 6px; }}
.pv-summary {{ font-style: italic; color: var(--color-text-secondary); margin: 0 0 18px; }}
</style>
<style>{help_page_css()}</style>
</head><body class="{'body--dark' if colors.theme_type == 'dark' else ''}">
<div class="pv"><div class="pv-main">
<div class="pv-badge">{html.escape(topic.category.value.replace("_", " ").title())} · {html.escape(theme)}</div>
<h1 class="pv-title">{html.escape(topic.title)}</h1>
<p class="pv-summary">{html.escape(topic.summary)}</p>
<div class="help-doc">{body_html}</div>
</div><div class="pv-toc"><b>On this page</b>{toc_html}</div></div>
<script>{help_page_js()}</script>
<script>window.MycelianHelp.enhance(document.querySelector('.help-doc'));</script>
</body></html>
"""


_NavDelegate = None


def snapshot(html_path: Path, png_path: Path, width: int) -> None:
    """Render an HTML file to PNG with an offscreen WKWebView (macOS only)."""
    import objc
    from AppKit import NSApplication, NSBitmapImageRep, NSPNGFileType
    from Foundation import NSURL, NSDate, NSMakeRect, NSObject, NSRunLoop
    from WebKit import WKSnapshotConfiguration, WKWebView, WKWebViewConfiguration

    NSApplication.sharedApplication()
    state = {"loaded": False, "height": None, "image": None}

    global _NavDelegate
    if _NavDelegate is None:
        # Objective-C classes can only be registered once per process.
        class HelpPreviewNavDelegate(NSObject):
            def webView_didFinishNavigation_(self, webview, nav):  # noqa: N802
                self.on_loaded()

        _NavDelegate = HelpPreviewNavDelegate

    def pump(cond, timeout=15.0):
        end = NSDate.dateWithTimeIntervalSinceNow_(timeout)
        while not cond() and NSDate.date().compare_(end) < 0:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))

    view = WKWebView.alloc().initWithFrame_configuration_(
        NSMakeRect(0, 0, width, 900), WKWebViewConfiguration.alloc().init()
    )
    delegate = _NavDelegate.alloc().init()
    delegate.on_loaded = lambda: state.__setitem__("loaded", True)
    view.setNavigationDelegate_(delegate)
    view.loadFileURL_allowingReadAccessToURL_(
        NSURL.fileURLWithPath_(str(html_path)), NSURL.fileURLWithPath_("/")
    )
    pump(lambda: state["loaded"])
    pump(lambda: False, 0.6)  # fonts + enhance()

    def got_height(result, error):
        state["height"] = int(result or 900)

    view.evaluateJavaScript_completionHandler_("document.documentElement.scrollHeight", got_height)
    pump(lambda: state["height"] is not None)
    height = min(state["height"] or 900, 16000)
    view.setFrame_(NSMakeRect(0, 0, width, height))
    pump(lambda: False, 0.4)

    cfg = WKSnapshotConfiguration.alloc().init()
    cfg.setRect_(NSMakeRect(0, 0, width, height))

    def got_image(image, error):
        state["image"] = image if image is not None else False

    view.takeSnapshotWithConfiguration_completionHandler_(cfg, got_image)
    pump(lambda: state["image"] is not None)
    image = state["image"]
    if not image:
        raise RuntimeError(f"snapshot failed for {html_path}")
    rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    data = rep.representationUsingType_properties_(NSPNGFileType, None)
    data.writeToFile_atomically_(str(png_path), True)
    del objc


def slice_png(png_path: Path, slice_height: int) -> list:
    """Split a tall screenshot into readable chunks (<name>.part<N>.png)."""
    from PIL import Image

    parts = []
    with Image.open(png_path) as im:
        w, h = im.size
        step = slice_height * 2  # snapshots are @2x
        for i, top in enumerate(range(0, h, step), start=1):
            part = png_path.with_name(f"{png_path.stem}.part{i}.png")
            im.crop((0, top, w, min(h, top + step))).save(part)
            parts.append(part)
    return parts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("topics", nargs="*", help="topic ids (default: --all)")
    parser.add_argument("--all", action="store_true", help="every topic")
    parser.add_argument("--theme", default="dark", help="theme file stem, e.g. dark, light, nord_dark")
    parser.add_argument("--out", default=str(ROOT / "logs" / "help_preview"), help="output directory")
    parser.add_argument("--width", type=int, default=1180)
    parser.add_argument("--png", action="store_true", help="also save a PNG screenshot (macOS)")
    parser.add_argument("--slice", type=int, default=0, metavar="PX",
                        help="with --png, also split the screenshot into chunks PX tall (e.g. 1400)")
    args = parser.parse_args()

    clear_caches()
    topics = list(HELP_TOPICS) if args.all or not args.topics else args.topics
    unknown = [t for t in topics if t not in HELP_TOPICS]
    if unknown:
        parser.error(f"unknown topic(s): {', '.join(unknown)}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for topic_id in topics:
        page = out / f"{topic_id}.{args.theme}.html"
        page.write_text(build_page(topic_id, args.theme, args.width), encoding="utf-8")
        line = str(page)
        if args.png:
            png = page.with_suffix(".png")
            snapshot(page, png, args.width)
            line += f"\n  -> {png}"
            if args.slice:
                for part in slice_png(png, args.slice):
                    line += f"\n  -> {part}"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
