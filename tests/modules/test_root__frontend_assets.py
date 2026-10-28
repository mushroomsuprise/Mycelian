# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Front-end assets that aren't Python: JS syntax, rendered overlay scripts, template configs,
the Factorio mod and the Stream Deck plugin manifests.

* every .js under assets/, templates/, sd_plugin/ and streamdeck-plugin/**/bin passes ``node --check``;
* every overlay template renders with Jinja from its own config defaults the way the web engine
  does (``WebEngine._template_variable_map`` + ``_finalize_served_overlay_html``), and every inline
  ``<script>`` in the result compiles in node; Spore Studio sidecars and both boilerplates are compiled
  with ``template_codegen.compile_model`` and checked the same way;
* every template config parses and has the shape ``TemplateConfigParser`` and Source Settings expect;
* the Factorio mod's info.json / changelog / locale agree, and ``luac -p`` passes when luac exists;
* the Stream Deck manifests parse and reference files that exist.

Everything here only reads the repo; node runs ``--check`` or compiles source in ``vm.Script``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import copy  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

import jinja2  # noqa: E402
import pytest  # noqa: E402

from modules import web_engine as we  # noqa: E402
from modules.template_config_parser import TemplateConfigParser  # noqa: E402
from modules.web_engine import WebEngine  # noqa: E402

NODE = shutil.which("node")
LUAC = shutil.which("luac") or shutil.which("luac5.4") or shutil.which("luac5.3")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed; JavaScript syntax checks need it")

TEMPLATES = ROOT / "templates"
CONFIG_DIR = TEMPLATES / "template_configs"
BOILERPLATES = TEMPLATES / "_boilerplates"
SPORE_DIR = TEMPLATES / "_spore"
FACTORIO_MOD = ROOT / "factorio_mod" / "mycelian-stats"
SD_PLUGIN_DIRS = [
    ROOT / "sd_plugin" / "com.mushroomsuprise.mycelian.sdPlugin",
    ROOT / "streamdeck-plugin" / "mycelian" / "com.mushroomsuprise.mycelian.sdPlugin",
]

KNOWN_ELEMENT_TYPES = {"text", "file", "select", "number", "slider", "checkbox", "color", "textarea", "separator"}
JS_SCRIPT_TYPES = {"", "text/javascript", "application/javascript", "module"}


# --------------------------------------------------------------------------- helpers


def _js_files() -> List[Path]:
    roots = [ROOT / "assets", TEMPLATES, ROOT / "sd_plugin"]
    out: List[Path] = []
    for base in roots:
        out.extend(p for p in base.rglob("*.js") if "node_modules" not in p.parts)
    out.extend(p for p in (ROOT / "streamdeck-plugin").glob("**/bin/*.js") if "node_modules" not in p.parts)
    return sorted(set(out))


JS_FILES = _js_files()
TEMPLATE_STEMS = sorted(p.stem for p in TEMPLATES.glob("*.html"))
CONFIG_FILES = sorted(CONFIG_DIR.glob("*.json"))
BOILERPLATE_CONFIGS = sorted(BOILERPLATES.glob("*.json"))
SPORE_SIDECARS = sorted(SPORE_DIR.glob("*.spore.json"))

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
_TYPE_RE = re.compile(r"""\btype\s*=\s*["']?([^"'\s>]+)""", re.I)

# Compiles each item like a browser <script> (vm.Script) or module (SourceTextModule) and prints
# the failures as JSON. One node process per call keeps the suite fast.
_NODE_CHECKER = r"""
const vm = require('vm');
let src = '';
process.stdin.on('data', (d) => { src += d; });
process.stdin.on('end', () => {
  const bad = [];
  for (const it of JSON.parse(src)) {
    try {
      if (it.module) { new vm.SourceTextModule(it.code, { identifier: it.name }); }
      else { new vm.Script(it.code, { filename: it.name }); }
    } catch (e) { bad.push(it.name + ': ' + (e && e.message)); }
  }
  process.stdout.write(JSON.stringify(bad));
});
"""


def inline_scripts(html: str, label: str) -> List[Dict[str, Any]]:
    """Inline JS blocks of a page (src= scripts and non-JS types such as JSON data are skipped)."""
    items = []
    for index, match in enumerate(_SCRIPT_RE.finditer(html)):
        attrs, body = match.group(1), match.group(2)
        if re.search(r"\bsrc\s*=", attrs, re.I):
            continue
        type_match = _TYPE_RE.search(attrs)
        script_type = type_match.group(1).lower() if type_match else ""
        if script_type not in JS_SCRIPT_TYPES:
            continue
        items.append({"name": f"{label}#script{index}", "code": body, "module": script_type == "module"})
    return items


def node_compile_errors(items: List[Dict[str, Any]]) -> List[str]:
    if not items:
        return []
    proc = subprocess.run(
        [NODE, "--experimental-vm-modules", "--no-warnings", "-e", _NODE_CHECKER],
        input=json.dumps(items),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def jinja_env(undefined=jinja2.Undefined) -> jinja2.Environment:
    """The parts of Flask's Jinja environment overlays rely on: file loader + .html autoescape."""
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        autoescape=jinja2.select_autoescape(["html", "htm", "xml"]),
        undefined=undefined,
    )


def repo_parser() -> TemplateConfigParser:
    return TemplateConfigParser(config_dir=str(CONFIG_DIR))


def render_overlay(stem: str, preview: bool = False, undefined=jinja2.Undefined) -> str:
    """Render templates/<stem>.html like WebEngine's template route does."""
    config = repo_parser().load_config(stem)
    variables = WebEngine._template_variable_map(config)
    html = jinja_env(undefined).get_template(f"{stem}.html").render(
        **variables, mycelian_html_stem=stem, mycelian_preview_mode=preview
    )
    return we._finalize_served_overlay_html(html)


def render_spore_model(model: Dict[str, Any], stem: str) -> str:
    from modules.spore_studio import template_codegen as tc

    html, config = tc.compile_model(copy.deepcopy(model))
    variables = WebEngine._template_variable_map(config)
    out = jinja2.Environment(autoescape=True).from_string(html).render(
        **variables, mycelian_html_stem=stem, mycelian_preview_mode=False
    )
    return we._finalize_served_overlay_html(out)


def minimal_spore_model(alert_system: str) -> Dict[str, Any]:
    text = {"id": "greeting", "type": "text", "category": "Main", "position": {"x": 0, "y": 0},
            "size": {"w": 200, "h": 40}, "props": {"text": "Hi"}, "source_settings_expose": {"text": True},
            "bindings": [{"event": "next_alert", "action": "set_text", "args": {"literal": "{{ user }} '</script>"}}]}
    return {"template_name": f"zz_{alert_system}_check", "alert_system": alert_system,
            "design": {"width": 800, "height": 200}, "duration_seconds": 5, "elements": [text],
            "advanced_js": "var answer = 42;", "streamdeck_options": {"description": "", "actions": {}},
            "dynamic_controls": {"elements": []}}


# --------------------------------------------------------------------------- node --check on .js files


def test_js_file_discovery_finds_the_shipped_scripts():
    rel = {p.relative_to(ROOT).as_posix() for p in JS_FILES}
    for expected in (
        "assets/default_assets/spore_studio/editor.js",
        "assets/default_assets/alert_queue.js",
        "templates/_boilerplates/spore_data_runtime.js",
        "sd_plugin/com.mushroomsuprise.mycelian.sdPlugin/bin/plugin.js",
        "streamdeck-plugin/mycelian/com.mushroomsuprise.mycelian.sdPlugin/bin/plugin.js",
    ):
        assert expected in rel
    assert not any("node_modules" in r for r in rel)
    assert not any(r.startswith("streamdeck-plugin/mycelian/src") for r in rel)


@needs_node
@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_js_file_passes_node_check(path):
    proc = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, f"node --check {path.relative_to(ROOT)} failed:\n{proc.stderr}"


@needs_node
def test_node_check_rejects_broken_js(tmp_path):
    bad = tmp_path / "broken.js"
    bad.write_text("function (", encoding="utf-8")
    proc = subprocess.run([NODE, "--check", str(bad)], capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode != 0
    assert "SyntaxError" in proc.stderr


# --------------------------------------------------------------------------- inline script checker


def test_inline_scripts_skips_external_and_data_blocks():
    html = (
        '<script src="/x.js"></script>'
        '<script>var a = 1;</script>'
        '<script type="application/json">{"a": 1}</script>'
        "<script type='text/javascript'>var b = 2;</script>"
        '<script type="module">export const c = 3;</script>'
        '<SCRIPT TYPE="text/template"><div>{{x}}</div></SCRIPT>'
    )
    items = inline_scripts(html, "page")
    assert [i["code"] for i in items] == ["var a = 1;", "var b = 2;", "export const c = 3;"]
    assert [i["module"] for i in items] == [False, False, True]
    assert items[0]["name"] == "page#script1"


@needs_node
def test_node_compile_errors_reports_only_broken_blocks():
    items = [
        {"name": "ok", "code": "var x = 1; function f() { return x; }", "module": False},
        {"name": "bad", "code": "var = ;", "module": False},
        {"name": "mod", "code": "import x from './x.js'; export default x;", "module": True},
        {"name": "script-import", "code": "import x from './x.js';", "module": False},
    ]
    errors = node_compile_errors(items)
    assert [e.split(":")[0] for e in errors] == ["bad", "script-import"]
    assert node_compile_errors([]) == []


# --------------------------------------------------------------------------- overlay templates


def test_every_overlay_template_has_a_config_and_vice_versa_for_templates():
    configs = {p.stem for p in CONFIG_FILES}
    assert TEMPLATE_STEMS, "no overlay templates found"
    assert set(TEMPLATE_STEMS) - configs == set()


@pytest.mark.xfail(strict=True, reason="BUG: templates/template_configs/save_overwrite_test.json is a test "
                                       "leftover with no template; it ships and is listed in Source Settings")
def test_every_config_has_an_overlay_template():
    stems = set(TEMPLATE_STEMS)
    assert [p.stem for p in CONFIG_FILES if p.stem not in stems] == []


@pytest.mark.parametrize("preview", [False, True], ids=["live", "preview"])
@pytest.mark.parametrize("stem", TEMPLATE_STEMS)
def test_overlay_template_renders_from_its_config_defaults(stem, preview):
    html = render_overlay(stem, preview=preview)
    assert "<html" in html.lower()
    assert "</html>" in html.lower()
    # _finalize_served_overlay_html makes sure the shared overlay scripts are present
    assert "overlay_recovery.js" in html
    # Jinja syntax is gone: no unrendered tags survive
    assert "{%" not in html


@needs_node
@pytest.mark.parametrize("preview", [False, True], ids=["live", "preview"])
@pytest.mark.parametrize("stem", TEMPLATE_STEMS)
def test_overlay_template_inline_scripts_compile(stem, preview):
    items = inline_scripts(render_overlay(stem, preview=preview), stem)
    assert items, f"{stem} has no inline script"
    assert node_compile_errors(items) == []


def _strict_param(stem):
    if stem == "follow":
        return pytest.param(stem, marks=pytest.mark.xfail(
            strict=True, raises=jinja2.UndefinedError,
            reason="BUG: templates/follow.html:268 JS fallback 'Thanks for the follow {{FolName}}!' is "
                   "read as Jinja, so it renders as 'Thanks for the follow !' and the name is never filled in"))
    return stem


@pytest.mark.parametrize("stem", [_strict_param(s) for s in TEMPLATE_STEMS])
def test_overlay_template_only_uses_variables_its_config_defines(stem):
    render_overlay(stem, undefined=jinja2.StrictUndefined)


def test_follow_fallback_message_loses_the_name_placeholder():
    """The concrete effect of the follow.html Jinja clash (documented by the xfail above)."""
    html = render_overlay("follow")
    assert "'Thanks for the follow !'" in html
    assert "/\\{\\{FolName\\}\\}/g" in html  # the replace() regex survives because its braces are escaped


def test_overlay_values_are_html_escaped_like_flask():
    env = jinja_env()
    out = env.from_string("{{ v }}").render(v="<b>")
    assert out == "&lt;b&gt;"  # like Flask, string templates autoescape
    assert env.autoescape("counter.html") is True
    assert env.autoescape("notes.txt") is False


@needs_node
def test_preview_helper_html_scripts_compile():
    items = inline_scripts(we.MYCELIAN_PREVIEW_HELPER_HTML, "preview_helper")
    assert items
    assert node_compile_errors(items) == []


# --------------------------------------------------------------------------- Spore Studio output


@needs_node
@pytest.mark.parametrize("sidecar", SPORE_SIDECARS, ids=lambda p: p.name)
def test_spore_sidecar_compiles_renders_and_scripts_parse(sidecar):
    model = json.loads(sidecar.read_text(encoding="utf-8"))
    stem = sidecar.name[: -len(".spore.json")]
    html = render_spore_model(model, stem)
    assert "</html>" in html.lower()
    items = inline_scripts(html, sidecar.name)
    assert items
    assert node_compile_errors(items) == []


@needs_node
@pytest.mark.parametrize("alert_system", ["queue", "instant"])
def test_boilerplate_compiles_renders_and_scripts_parse(alert_system):
    html = render_spore_model(minimal_spore_model(alert_system), f"zz_{alert_system}_check")
    assert "var answer = 42;" in html
    assert "</script><b>" not in html
    items = inline_scripts(html, f"{alert_system}_alert")
    assert len(items) >= 2
    assert node_compile_errors(items) == []


def test_spore_sidecars_name_their_template():
    stems = set(TEMPLATE_STEMS)
    for sidecar in SPORE_SIDECARS:
        model = json.loads(sidecar.read_text(encoding="utf-8"))
        stem = sidecar.name[: -len(".spore.json")]
        assert model.get("template_name") == stem
        assert stem in stems
        assert isinstance(model.get("elements"), list) and model["elements"]
        assert model.get("alert_system") in {"queue", "instant"}


# --------------------------------------------------------------------------- template configs


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", CONFIG_FILES + BOILERPLATE_CONFIGS + SPORE_SIDECARS,
                         ids=lambda p: p.relative_to(TEMPLATES).as_posix())
def test_config_json_parses_to_an_object(path):
    data = _load_json(path)
    assert isinstance(data, dict)


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: p.stem)
def test_parser_loads_config_exactly_as_written(path):
    raw = _load_json(path)
    loaded = repo_parser().load_config(path.stem, include_dynamic_controls=True, include_streamdeck_options=True)
    assert loaded == raw
    trimmed = repo_parser().load_config(path.stem)
    assert "dynamic_controls" not in trimmed and "streamdeck_options" not in trimmed
    assert trimmed["elements"] == raw["elements"]


def test_parser_lists_every_config_file():
    assert sorted(repo_parser().get_config_files()) == sorted(p.stem for p in CONFIG_FILES)


def _element_problems(data: Dict[str, Any], *, check_ranges: bool = True) -> List[str]:
    problems: List[str] = []
    elements = data.get("elements")
    if not isinstance(elements, list):
        return ["elements is not a list"]
    for index, el in enumerate(elements):
        where = f"elements[{index}]"
        if not isinstance(el, dict):
            problems.append(f"{where} is not an object")
            continue
        etype = el.get("type")
        if etype not in KNOWN_ELEMENT_TYPES:
            problems.append(f"{where} unknown type {etype!r}")
            continue
        if not isinstance(el.get("label"), str):
            problems.append(f"{where} has no label")
        if etype == "separator":
            continue
        eid, value = el.get("id"), el.get("value", ...)
        where = f"{eid!r}"
        if not isinstance(eid, str) or not eid:
            problems.append(f"elements[{index}] has no id")
        if value is ...:
            problems.append(f"{where} has no value")
            continue
        if "description" in el and not isinstance(el["description"], str):
            problems.append(f"{where} description is not text")
        if etype in ("number", "slider"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(f"{where} {etype} value {value!r} is not a number")
            lo, hi = el.get("min"), el.get("max")
            if lo is not None and hi is not None and lo > hi:
                problems.append(f"{where} min {lo} > max {hi}")
            if check_ranges and isinstance(value, (int, float)):
                if (lo is not None and value < lo) or (hi is not None and value > hi):
                    problems.append(f"{where} value {value} outside [{lo}, {hi}]")
            if "step" in el and not (isinstance(el["step"], (int, float)) and el["step"] > 0):
                problems.append(f"{where} step {el['step']!r} is not positive")
        elif etype == "checkbox":
            if not isinstance(value, bool):
                problems.append(f"{where} checkbox value {value!r} is not a bool")
        elif etype == "select":
            options = el.get("options", [])
            if not isinstance(options, list):
                problems.append(f"{where} options is not a list")
            elif el.get("options_from") == "fonts":
                pass
            elif not options:
                problems.append(f"{where} select has no options")
            elif value not in options:
                problems.append(f"{where} value {value!r} not among options")
        elif etype in ("text", "textarea", "file", "color"):
            if not isinstance(value, str):
                problems.append(f"{where} {etype} value {value!r} is not text")
        if etype == "color" and "options" in el and not isinstance(el["options"], list):
            problems.append(f"{where} color presets are not a list")
    return problems


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: p.stem)
def test_template_config_matches_parser_expectations(path):
    data = _load_json(path)
    assert data.get("template_name") == path.stem
    assert _element_problems(data) == []
    if "hidden" in data:
        assert isinstance(data["hidden"], bool)
    if "alert_system" in data:
        assert data["alert_system"] in {"queue", "instant"}
    if "spore_studio" in data:
        assert isinstance(data["spore_studio"], bool)
    dynamic = data.get("dynamic_controls")
    if dynamic is not None:
        assert isinstance(dynamic, dict) and isinstance(dynamic.get("elements"), list)
        for control in dynamic["elements"]:
            assert isinstance(control, dict) and control.get("id") and control.get("type")
    for key in ("streamdeck_options", "preview_mocks", "outputs"):
        if key in data:
            assert isinstance(data[key], (dict, list))
    if "connector_actions" in data:
        assert isinstance(data["connector_actions"], (dict, list))


def _dup_param(path):
    if path.stem == "poll":
        return pytest.param(path, id=path.stem, marks=pytest.mark.xfail(
            strict=True, reason="BUG: templates/template_configs/poll.json repeats six poll1_* settings "
                                "(elements 43-50); Source Settings shows them twice and the last copy wins"))
    return pytest.param(path, id=path.stem)


@pytest.mark.parametrize("path", [_dup_param(p) for p in CONFIG_FILES])
def test_template_config_element_ids_are_unique(path):
    ids = [el["id"] for el in _load_json(path)["elements"] if el.get("type") != "separator"]
    assert sorted({i for i in ids if ids.count(i) > 1}) == []


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: p.stem)
def test_variable_map_exposes_every_setting(path):
    data = _load_json(path)
    variables = WebEngine._template_variable_map(data)
    for el in data["elements"]:
        if el.get("type") == "separator":
            continue
        assert el["id"] in variables
        if not el["id"].endswith("_font_family"):
            # last value wins for duplicated ids (see the poll.json xfail)
            last = [e for e in data["elements"] if e.get("id") == el["id"]][-1]["value"]
            assert variables[el["id"]] == last
    if "BGColor" in variables and "BGOpacity" in variables:
        assert variables["BGColorWithOpacity"] == WebEngine._color_with_opacity(
            variables["BGColor"], variables["BGOpacity"])


@pytest.mark.parametrize("path", BOILERPLATE_CONFIGS, ids=lambda p: p.name)
def test_boilerplate_config_shape(path):
    data = _load_json(path)
    assert data["template_name"] == "__TEMPLATE_NAME__"
    # Value ranges are not checked: the codegen writes DesignWidth/DesignHeight itself
    # (template_codegen.py) and nothing reads these JSON files at runtime.
    assert _element_problems(data, check_ranges=False) == []


def test_element_problem_checker_flags_bad_configs():
    bad = {"elements": [
        "x",
        {"type": "wat", "label": "x"},
        {"type": "number", "id": "n", "label": "N", "value": "3", "min": 5, "max": 1, "step": 0},
        {"type": "number", "id": "r", "label": "R", "value": 9, "min": 0, "max": 5},
        {"type": "checkbox", "id": "c", "label": "C", "value": "yes"},
        {"type": "select", "id": "s", "label": "S", "value": "z", "options": ["a"]},
        {"type": "select", "id": "e", "label": "E", "value": "z", "options": []},
        {"type": "select", "id": "o", "label": "O", "value": "z", "options": "a"},
        {"type": "color", "id": "col", "label": "Col", "value": 1, "options": "x"},
        {"type": "text", "label": "T", "value": "v"},
        {"type": "text", "id": "nv", "label": "NV"},
        {"type": "text", "id": "d", "label": 3, "value": "", "description": 4},
    ]}
    problems = _element_problems(bad)
    joined = "\n".join(problems)
    for needle in ("elements[0] is not an object", "unknown type 'wat'", "is not a number", "min 5 > max 1",
                   "step 0", "outside [0, 5]", "is not a bool", "not among options", "has no options",
                   "options is not a list", "is not text", "presets are not a list", "elements[9] has no id",
                   "'nv' has no value", "elements[11] has no label", "description is not text"):
        assert needle in joined, needle
    assert _element_problems({"elements": {}}) == ["elements is not a list"]
    assert _element_problems({"elements": [{"type": "select", "id": "f", "label": "F", "value": "x",
                                            "options": [], "options_from": "fonts"}]}) == []


# --------------------------------------------------------------------------- Factorio mod


def _changelog_versions() -> List[str]:
    text = (FACTORIO_MOD / "changelog.txt").read_text(encoding="utf-8")
    return re.findall(r"^Version: (\S+)$", text, re.M)


def test_factorio_info_json_fields():
    info = _load_json(FACTORIO_MOD / "info.json")
    assert info["name"] == FACTORIO_MOD.name == "mycelian-stats"
    assert re.fullmatch(r"\d+\.\d+\.\d+", info["version"])
    assert all(0 <= int(part) <= 65535 for part in info["version"].split("."))
    assert re.fullmatch(r"\d+\.\d+", info["factorio_version"])
    for key in ("title", "author", "description"):
        assert isinstance(info[key], str) and info[key].strip()
    assert set(info) <= {"name", "version", "title", "author", "factorio_version", "description",
                         "dependencies", "contact", "homepage"}
    if "dependencies" in info:
        assert all(isinstance(d, str) for d in info["dependencies"])


def test_factorio_versions_agree():
    from modules.game_hooks import factorio_mod_installer as fmi

    info = _load_json(FACTORIO_MOD / "info.json")
    control = (FACTORIO_MOD / "control.lua").read_text(encoding="utf-8")
    assert re.search(r'^local MOD_VERSION = "([^"]+)"', control, re.M).group(1) == info["version"]
    versions = _changelog_versions()
    assert versions[0] == info["version"]
    assert len(versions) == len(set(versions))
    as_tuples = [tuple(int(x) for x in v.split(".")) for v in versions]
    assert as_tuples == sorted(as_tuples, reverse=True)
    assert fmi.MOD_NAME == info["name"]
    assert fmi.read_info_version(FACTORIO_MOD / "info.json") == info["version"]


def test_factorio_changelog_format():
    lines = (FACTORIO_MOD / "changelog.txt").read_text(encoding="utf-8").splitlines()
    separators = [i for i, line in enumerate(lines) if line.startswith("-")]
    assert separators and separators[0] == 0
    for i in separators:
        assert lines[i] == "-" * 99, f"line {i + 1}: Factorio wants exactly 99 dashes"
        assert lines[i + 1].startswith("Version: ")
    for line in lines:
        if line and not line.startswith(("-", "Version: ", "Date: ")):
            assert re.match(r"^  [A-Z][A-Za-z ]+:$|^    - \S", line), f"bad changelog line {line!r}"


def test_factorio_settings_have_locale_names():
    settings = (FACTORIO_MOD / "settings.lua").read_text(encoding="utf-8")
    names = re.findall(r'name\s*=\s*"([^"]+)"', settings)
    assert names
    locale = (FACTORIO_MOD / "locale" / "en" / "mycelian-stats.cfg").read_text(encoding="utf-8")
    sections: Dict[str, Dict[str, str]] = {}
    current = None
    for line in locale.splitlines():
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
        elif "=" in line and current is not None:
            key, value = line.split("=", 1)
            current[key] = value
    assert sections["mod-name"]["mycelian-stats"]
    for name in names:
        assert name in sections["mod-setting-name"], name
        assert name in sections["mod-setting-description"], name
    control = (FACTORIO_MOD / "control.lua").read_text(encoding="utf-8")
    for used in re.findall(r'settings\.global\[\s*"([^"]+)"\s*\]', control):
        assert used in names, f"control.lua reads unknown setting {used}"


@pytest.mark.skipif(LUAC is None, reason="luac is not installed; Lua syntax of the Factorio mod is not checked")
@pytest.mark.parametrize("path", sorted(FACTORIO_MOD.rglob("*.lua")), ids=lambda p: p.name)
def test_factorio_lua_parses(path):
    proc = subprocess.run([LUAC, "-p", str(path)], capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr


def test_factorio_lua_files_are_balanced_without_luac():
    """A cheap structural check that runs everywhere: block openers match `end`s."""
    for path in sorted(FACTORIO_MOD.rglob("*.lua")):
        code = path.read_text(encoding="utf-8")
        code = re.sub(r"--\[(=*)\[.*?\]\1\]", "", code, flags=re.S)
        code = re.sub(r"--[^\n]*", "", code)
        code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', code)
        code = re.sub(r"\[(=*)\[.*?\]\1\]", '""', code, flags=re.S)
        opens = len(re.findall(r"\b(function|if|do|repeat)\b", code))
        # `while`/`for` bodies open with `do`; `repeat` closes with `until`
        closes = len(re.findall(r"\bend\b", code)) + len(re.findall(r"\buntil\b", code))
        assert opens == closes, f"{path.name}: {opens} block openers vs {closes} closers"
        assert code.count("(") == code.count(")"), path.name
        assert code.count("{") == code.count("}"), path.name


# --------------------------------------------------------------------------- Stream Deck plugin


def _icon_exists(base: Path, rel: str) -> bool:
    target = base / rel
    if target.suffix:
        return target.is_file()
    return any(target.with_suffix(ext).is_file() for ext in (".png", ".svg", ".gif"))


@pytest.mark.parametrize("plugin", SD_PLUGIN_DIRS, ids=lambda p: p.parent.name)
def test_streamdeck_manifest_references_existing_files(plugin):
    manifest = _load_json(plugin / "manifest.json")
    assert manifest["UUID"] == plugin.name.removesuffix(".sdPlugin")
    assert (plugin / manifest["CodePath"]).is_file()
    assert _icon_exists(plugin, manifest["Icon"])
    assert _icon_exists(plugin, manifest["CategoryIcon"])
    for key in ("Name", "Version", "Author", "Description", "Category"):
        assert isinstance(manifest[key], str) and manifest[key]
    assert re.fullmatch(r"\d+(\.\d+){3}", manifest["Version"])
    assert manifest["SDKVersion"] in (2, 3)
    assert {o["Platform"] for o in manifest["OS"]} <= {"mac", "windows"}
    uuids = [a["UUID"] for a in manifest["Actions"]]
    assert len(uuids) == len(set(uuids))
    for action in manifest["Actions"]:
        assert action["UUID"].startswith(manifest["UUID"] + ".")
        assert _icon_exists(plugin, action["Icon"]), action["Icon"]
        assert (plugin / action["PropertyInspectorPath"]).is_file()
        assert action["States"]
        for state in action["States"]:
            assert _icon_exists(plugin, state["Image"]), state["Image"]
        assert set(action["Controllers"]) <= {"Keypad", "Encoder"}


@pytest.mark.parametrize("plugin", SD_PLUGIN_DIRS, ids=lambda p: p.parent.name)
def test_streamdeck_bundle_registers_every_manifest_action(plugin):
    manifest = _load_json(plugin / "manifest.json")
    bundle = (plugin / manifest["CodePath"]).read_text(encoding="utf-8")
    for action in manifest["Actions"]:
        assert action["UUID"] in bundle, action["UUID"]
    package = _load_json(plugin / "bin" / "package.json")
    assert isinstance(package, dict)


def test_streamdeck_deployed_copy_matches_build_output():
    built, deployed = SD_PLUGIN_DIRS[1], SD_PLUGIN_DIRS[0]
    assert _load_json(built / "manifest.json") == _load_json(deployed / "manifest.json")
    for rel in ("ui/toggle-alerts.html", "ui/template-action.html", "ui/connector-action.html"):
        assert (built / rel).read_text(encoding="utf-8") == (deployed / rel).read_text(encoding="utf-8"), rel


@needs_node
@pytest.mark.parametrize("page", sorted(SD_PLUGIN_DIRS[0].glob("ui/*.html")), ids=lambda p: p.name)
def test_streamdeck_property_inspector_scripts_compile(page):
    items = inline_scripts(page.read_text(encoding="utf-8"), page.name)
    assert items
    assert node_compile_errors(items) == []


def test_streamdeck_source_actions_match_manifest():
    src = ROOT / "streamdeck-plugin" / "mycelian" / "src"
    declared = set()
    for ts in src.rglob("*.ts"):
        declared.update(re.findall(r'@action\(\{\s*UUID:\s*"([^"]+)"', ts.read_text(encoding="utf-8")))
    manifest = _load_json(SD_PLUGIN_DIRS[1] / "manifest.json")
    assert declared == {a["UUID"] for a in manifest["Actions"]}


TSC = ROOT / "streamdeck-plugin" / "mycelian" / "node_modules" / ".bin" / "tsc"


@pytest.mark.skipif(NODE is None or not TSC.exists(),
                    reason="streamdeck-plugin/mycelian/node_modules (local tsc) is not installed")
def test_streamdeck_plugin_type_checks():
    proc = subprocess.run([str(TSC), "--noEmit", "-p", str(ROOT / "streamdeck-plugin" / "mycelian")],
                          capture_output=True, text=True, timeout=120, check=False, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- container solver parity

_LAYOUT_RUNNER = r"""
const fs = require('fs'); const vm = require('vm');
const ctx = { console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
let src = '';
process.stdin.on('data', (d) => { src += d; });
process.stdin.on('end', () => {
  const out = JSON.parse(src).map((c) => ctx.MycelianContainerLayout.solve(c.container, c.used));
  process.stdout.write(JSON.stringify(out));
});
"""


def _random_container(rng, percents: bool = True) -> Dict[str, Any]:
    links = [f"L{i}" for i in range(rng.randint(1, 3))]
    axes = {name: rng.choice(["vertical", "horizontal"]) for name in links}
    slots = []
    for i in range(rng.randint(1, 7)):
        slot: Dict[str, Any] = {
            "id": f"s{i}", "template": f"t{i}", "x": rng.randint(-50, 1500), "y": rng.randint(-50, 900),
            "w": rng.randint(1, 1200), "h": rng.randint(1, 900), "fit": rng.choice(["native", "native", "scale"]),
            "padding": {k: rng.choice([0, 0, rng.randint(0, 40)]) for k in "trbl"},
        }
        if rng.random() < 0.3:
            slot["design_width"], slot["design_height"] = rng.randint(1, 1920), rng.randint(1, 1080)
        if rng.random() < 0.8:
            name = rng.choice(links)
            link: Dict[str, Any] = {"id": name, "axis": axes[name], "role": rng.choice(["locked", "flexible"])}
            if rng.random() < 0.4:
                link["min_px"] = rng.randint(0, 600)
            if percents and rng.random() < 0.3:
                link["min_pct"] = rng.randint(0, 60)
            if rng.random() < 0.3:
                link["max_px"] = rng.randint(0, 1500)
            if percents and rng.random() < 0.3:
                link["max_pct"] = rng.randint(0, 100)
            slot["link"] = link
        slots.append(slot)
    return {"name": "c", "route": "c", "width": rng.randint(320, 3840), "height": rng.randint(240, 2160),
            "spacing": rng.choice([0, 0, rng.randint(0, 60)]), "slots": slots}


def _js_solve(cases):
    proc = subprocess.run(
        [NODE, "-e", _LAYOUT_RUNNER, str(ROOT / "assets" / "default_assets" / "spore_studio" / "container_layout.js")],
        input=json.dumps(cases), capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@needs_node
@pytest.mark.parametrize("percents", [
    False,
    pytest.param(True, marks=pytest.mark.xfail(strict=True, reason=(
        "BUG: container_layout.js:28/38 computes span * (pct / 100) while containers.py:504 computes "
        "span * pct / 100.0; e.g. 55% of 1390 is 764.5000000000001 in JS (rounds to 765) but 764.5 in "
        "Python (rounds to 764), so the live page and the server disagree by 1px"))),
], ids=["px-only", "with-percents"])
def test_container_layout_js_matches_python_solver(percents):
    """assets/.../container_layout.js mirrors containers.solve_layout: same input, same output."""
    import random

    from modules.spore_studio import containers

    rng = random.Random(20261027)
    cases = []
    for _ in range(300):
        raw = _random_container(rng, percents)
        model = containers.normalize_container(raw)
        used = {s["id"]: {"w": rng.uniform(-20, 1300), "h": rng.choice([rng.uniform(0, 1000), None, "x"])}
                for s in model["slots"] if rng.random() < 0.5}
        cases.append({"container": model, "used": used or None})
    js_results = _js_solve(cases)
    for case, js in zip(cases, js_results):
        py = containers.solve_layout(case["container"], case["used"])
        assert json.loads(json.dumps(py)) == js, json.dumps(case)[:2000]
