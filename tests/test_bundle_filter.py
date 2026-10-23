#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The build's unused-data filter only drops files nothing reads at runtime."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_filter():
    spec = importlib.util.spec_from_file_location(
        "mycelian_bundle_filter_test", ROOT / "packaging" / "bundle_filter.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bf = _load_filter()

# Anything NiceGUI or the browser loads from the package directory.
_SERVED_SUFFIXES = (".js", ".mjs", ".css", ".vue", ".html", ".svg", ".ico", ".woff",
                    ".woff2", ".ttf", ".png", ".json", ".txt")


def _package_dests(package: str):
    spec = importlib.util.find_spec(package)
    base = Path(spec.origin).parent
    for path in base.rglob("*"):
        if path.is_file():
            yield f"{package}/{path.relative_to(base).as_posix()}", path


class RuleTests(unittest.TestCase):
    def test_rules_match_only_the_intended_files(self) -> None:
        drop = {
            "nicegui/static/socket.io.min.js.map": "source-map",
            "nicegui/elements/echart/dist/index.js.map": "source-map",
            "nicegui\\elements\\codemirror\\dist\\css-C4GSMmGA.js.map": "source-map",
            "pycountry/locales/de/LC_MESSAGES/iso3166-1.mo": "pycountry-locales",
            "nicegui/elements/__pycache__/button.cpython-314.pyc": "nicegui-pycache",
            "nicegui/__pycache__/ui.cpython-314.pyc": "nicegui-pycache",
            "nicegui/elements/aggrid/package.json": "nicegui-npm-build-input",
            "nicegui/elements/aggrid/package-lock.json": "nicegui-npm-build-input",
            "nicegui/elements/aggrid/rollup.config.mjs": "nicegui-npm-build-input",
            "nicegui/elements/aggrid/src/index.mjs": "nicegui-npm-build-input",
        }
        for dest, rule in drop.items():
            self.assertEqual(bf.unused_data_rule(dest), rule, dest)
        keep = [
            "nicegui/static/socket.io.min.js",
            "nicegui/static/dompurify.mjs",
            "nicegui/static/nicegui.js",
            "nicegui/elements/echart/dist/index.js",
            "nicegui/elements/markdown.js",
            "nicegui/elements/button.py",
            "nicegui/templates/index.html",
            "nicegui/static/lang/de.umd.prod.js",
            "pycountry/databases/iso3166-1.json",
            "pycountry/locales/README",
            "PIL/__pycache__/Image.cpython-314.pyc",
            "webview/js/api.js.map",
            "certifi/cacert.pem",
            "modules/help_system/assets/help.js",
            "factorio_mod/package.json",
            "nicegui/package.json",
            "nicegui/static/src/x.js",
        ]
        for dest in keep:
            self.assertIsNone(bf.unused_data_rule(dest), dest)

    def test_required_data_is_never_matched(self) -> None:
        for dest in bf.REQUIRED_DATA:
            self.assertIsNone(bf.unused_data_rule(dest), dest)

    def test_verify_fails_when_required_data_is_missing(self) -> None:
        with self.assertRaises(RuntimeError):
            bf.verify_filtered([("nicegui/static/nicegui.js", "x", "DATA")])


class InstalledPackageTests(unittest.TestCase):
    """Apply the rules to the installed NiceGUI/pycountry trees."""

    def test_every_served_nicegui_file_except_maps_is_kept(self) -> None:
        dropped_rules = set()
        for dest, _path in _package_dests("nicegui"):
            rule = bf.unused_data_rule(dest)
            if rule:
                dropped_rules.add(rule)
            served = "/dist/" in dest or dest.startswith(
                ("nicegui/static/", "nicegui/templates/")
            )
            if dest.endswith(_SERVED_SUFFIXES) and served:
                if not dest.endswith(".js.map"):
                    self.assertIsNone(rule, dest)
            if dest.endswith(".py"):
                self.assertIsNone(rule, dest)
        self.assertIn("source-map", dropped_rules)

    def test_nicegui_rules_only_drop_maps_caches_and_npm_inputs(self) -> None:
        for dest, path in _package_dests("nicegui"):
            rule = bf.unused_data_rule(dest)
            if rule == "source-map":
                self.assertTrue(path.name.endswith(".js.map"), dest)
            elif rule == "nicegui-pycache":
                self.assertEqual(path.parent.name, "__pycache__", dest)
            elif rule == "nicegui-npm-build-input":
                parts = dest.split("/")
                self.assertEqual(parts[1], "elements", dest)
                self.assertTrue(
                    parts[3] in ("package.json", "package-lock.json", "rollup.config.mjs")
                    or parts[3] == "src",
                    dest,
                )
            else:
                self.assertIsNone(rule, dest)

    def test_pycountry_keeps_databases_and_drops_only_mo(self) -> None:
        for dest, _path in _package_dests("pycountry"):
            rule = bf.unused_data_rule(dest)
            if "/databases/" in dest or dest.endswith(".py"):
                self.assertIsNone(rule, dest)
            if rule:
                self.assertTrue(dest.endswith(".mo") and "/locales/" in dest, dest)

    def test_shipped_windows_exe_drops_only_expected_entries(self) -> None:
        exe = ROOT / "packaged" / "windows" / "Mycelian.exe"
        try:
            from PyInstaller.archive.readers import CArchiveReader
        except ImportError:
            self.skipTest("PyInstaller not installed")
        if not exe.is_file():
            self.skipTest("no packaged Windows build")
        toc = CArchiveReader(str(exe)).toc
        datas = [(name, "", "DATA") for name in toc]
        kept, _removed = bf.filter_unused_data(datas)  # raises if REQUIRED_DATA lost
        kept_names = {bf.normalize_dest(e[0]) for e in kept}
        for name in toc:
            dest = bf.normalize_dest(name)
            if dest not in kept_names:
                self.assertTrue(
                    dest.endswith((".js.map", ".mo", ".pyc", "package.json",
                                   "package-lock.json", "rollup.config.mjs"))
                    or "/src/" in dest,
                    dest,
                )
            elif dest.startswith("nicegui/") and "/dist/" in dest:
                self.assertFalse(dest.endswith(".map"), dest)


_NO_LOCALES_PROBE = textwrap.dedent(
    """
    import sys
    opened = []
    sys.addaudithook(lambda ev, args: opened.append(str(args[0])) if ev == "open" else None)
    import base64
    from psnawp_api.utils.misc import extract_region_from_npid
    country = extract_region_from_npid(base64.b64encode(b"VaultTec-Co@b7.us").decode())
    assert country is not None and country.alpha_2 == "US", country
    print("COUNTRY", country.name)
    import pycountry
    assert pycountry.countries.get(alpha_2="JP").name == "Japan"
    hits = [p for p in opened if "locales" in p and "pycountry" in p]
    print("LOCALE_OPENS", len(hits))
    print("DB_OPENS", sum(1 for p in opened if p.endswith("iso3166-1.json")))
    """
)

_NO_AVIF_PROBE = textwrap.dedent(
    """
    import io, sys
    sys.modules["PIL._avif"] = None  # behave as if the extension is not bundled
    from PIL import Image
    for path in sys.argv[1:]:
        with Image.open(path) as im:
            im.load()
            buf = io.BytesIO()
            im.save(buf, format="PNG")
    Image.new("RGB", (4, 4)).save(io.BytesIO(), "PNG")
    Image.init()
    from PIL import AvifImagePlugin
    print("AVIF_SUPPORTED", AvifImagePlugin.SUPPORTED)
    print("OK")
    """
)


class RuntimeEvidenceTests(unittest.TestCase):
    def _run(self, code: str, *args: str) -> str:
        result = subprocess.run(
            [sys.executable, "-c", code, *args],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return result.stdout

    def test_psn_country_lookup_never_opens_pycountry_locales(self) -> None:
        if importlib.util.find_spec("psnawp_api") is None:
            self.skipTest("psnawp not installed")
        out = self._run(_NO_LOCALES_PROBE)
        self.assertIn("LOCALE_OPENS 0", out)
        self.assertNotIn("DB_OPENS 0", out)  # the audit hook did see the real read

    def test_app_images_open_without_the_avif_codec(self) -> None:
        assets = ROOT / "assets" / "default_assets"
        paths = [
            assets / "icons" / "Mycelian.png",
            assets / "icons" / "Mycelian.ico",
            assets / "images" / "Mycelian.png",
        ]
        paths = [str(p) for p in paths if p.is_file()]
        if not paths:
            self.skipTest("assets missing")
        out = self._run(_NO_AVIF_PROBE, *paths)
        self.assertIn("AVIF_SUPPORTED False", out)
        self.assertIn("OK", out)


if __name__ == "__main__":
    unittest.main()
