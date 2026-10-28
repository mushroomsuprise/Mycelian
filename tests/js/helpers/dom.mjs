// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
//
// Minimal hand-written DOM for running Mycelian browser scripts under node:vm.
// Not a browser: enough of Node/Element/Document/Event, a small selector engine and a
// forgiving HTML parser so the app's scripts can build markup and query it back.
// Layout is fake: set `el._rect = {left, top, width, height}` to control
// getBoundingClientRect / offsetWidth / clientWidth etc.

const VOID = new Set([
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
]);
const RAW_TEXT = new Set(["script", "style", "textarea", "title"]);

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", "#39": "'" };

export function decodeEntities(s) {
    return String(s).replace(/&(#x[0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+[0-9]*);/g, (m, name) => {
        if (name[0] === "#") {
            const code = name[1] === "x" || name[1] === "X"
                ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
            return Number.isFinite(code) ? String.fromCodePoint(code) : m;
        }
        return Object.prototype.hasOwnProperty.call(ENTITIES, name) ? ENTITIES[name] : m;
    });
}

function escText(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escAttr(s) {
    return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

/* ------------------------------------------------------------------ events */

export class Event {
    constructor(type, init = {}) {
        this.type = type;
        this.bubbles = !!init.bubbles;
        this.cancelable = !!init.cancelable;
        this.defaultPrevented = false;
        this.target = null;
        this.currentTarget = null;
        this._stop = false;
        this._stopNow = false;
        this.timeStamp = Date.now();
        for (const k of Object.keys(init)) {
            if (!(k in this) || k === "detail") { this[k] = init[k]; }
        }
    }
    preventDefault() { if (this.cancelable !== false) { this.defaultPrevented = true; } }
    stopPropagation() { this._stop = true; }
    stopImmediatePropagation() { this._stop = true; this._stopNow = true; }
    composedPath() {
        const path = [];
        let n = this.target;
        while (n) { path.push(n); n = n.parentNode; }
        return path;
    }
}
export class CustomEvent extends Event {
    constructor(type, init = {}) { super(type, init); this.detail = init.detail === undefined ? null : init.detail; }
}
export class UIEvent extends Event {}
export class MouseEvent extends Event {
    constructor(type, init = {}) {
        super(type, init);
        for (const k of ["clientX", "clientY", "button", "buttons", "pageX", "pageY", "offsetX", "offsetY"]) {
            if (this[k] === undefined) { this[k] = 0; }
        }
        for (const k of ["shiftKey", "ctrlKey", "altKey", "metaKey"]) {
            if (this[k] === undefined) { this[k] = false; }
        }
    }
}
export class PointerEvent extends MouseEvent {
    constructor(type, init = {}) { super(type, init); if (this.pointerId === undefined) { this.pointerId = 1; } }
}
export class KeyboardEvent extends Event {
    constructor(type, init = {}) {
        super(type, init);
        if (this.key === undefined) { this.key = ""; }
        for (const k of ["shiftKey", "ctrlKey", "altKey", "metaKey", "repeat", "isComposing"]) {
            if (this[k] === undefined) { this[k] = false; }
        }
    }
}
export class FocusEvent extends Event {}
export class InputEvent extends Event {}
export class WheelEvent extends MouseEvent {}
export class DragEvent extends MouseEvent {}
export class ErrorEvent extends Event {}
export class PromiseRejectionEvent extends Event {}

export class EventTarget {
    constructor() { this._listeners = new Map(); }
    addEventListener(type, fn, opts) {
        if (!fn) { return; }
        if (!this._listeners) { this._listeners = new Map(); }
        const list = this._listeners.get(type) || [];
        const capture = typeof opts === "boolean" ? opts : !!(opts && opts.capture);
        if (list.some((l) => l.fn === fn && l.capture === capture)) { return; }
        list.push({ fn, capture, once: !!(opts && opts.once) });
        this._listeners.set(type, list);
    }
    removeEventListener(type, fn, opts) {
        const list = this._listeners && this._listeners.get(type);
        if (!list) { return; }
        const capture = typeof opts === "boolean" ? opts : !!(opts && opts.capture);
        const i = list.findIndex((l) => l.fn === fn && l.capture === capture);
        if (i >= 0) { list.splice(i, 1); }
    }
    _fire(ev, phaseCapture) {
        const list = this._listeners && this._listeners.get(ev.type);
        if (list && list.length) {
            for (const l of list.slice()) {
                if (phaseCapture !== null && l.capture !== phaseCapture) { continue; }
                if (l.once) { this.removeEventListener(ev.type, l.fn, l.capture); }
                ev.currentTarget = this;
                if (typeof l.fn === "function") { l.fn.call(this, ev); } else if (l.fn && l.fn.handleEvent) { l.fn.handleEvent(ev); }
                if (ev._stopNow) { break; }
            }
        }
        const prop = this["on" + ev.type];
        if (phaseCapture !== true && typeof prop === "function" && !ev._stopNow) {
            ev.currentTarget = this;
            prop.call(this, ev);
        }
    }
    dispatchEvent(ev) {
        if (!ev.target) { ev.target = this; }
        const path = [];
        let n = this.parentNode || (this.nodeType === 9 ? this.defaultView : null);
        while (n) {
            path.push(n);
            n = n.parentNode || (n.nodeType === 9 ? n.defaultView : null);
        }
        for (let i = path.length - 1; i >= 0 && !ev._stop; i--) { path[i]._fire(ev, true); }
        if (!ev._stop) { this._fire(ev, null); }
        if (ev.bubbles) {
            for (const p of path) { if (ev._stop) { break; } p._fire(ev, false); }
        }
        ev.currentTarget = null;
        return !ev.defaultPrevented;
    }
}

/* -------------------------------------------------------------------- nodes */

export class Node extends EventTarget {
    constructor(doc) {
        super();
        this.ownerDocument = doc || null;
        this.parentNode = null;
        this.childNodes = [];
    }
    get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
    get firstChild() { return this.childNodes[0] || null; }
    get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
    get nextSibling() {
        if (!this.parentNode) { return null; }
        const s = this.parentNode.childNodes;
        return s[s.indexOf(this) + 1] || null;
    }
    get previousSibling() {
        if (!this.parentNode) { return null; }
        const s = this.parentNode.childNodes;
        const i = s.indexOf(this);
        return i > 0 ? s[i - 1] : null;
    }
    get isConnected() {
        let n = this;
        while (n) { if (n.nodeType === 9) { return true; } n = n.parentNode; }
        return false;
    }
    hasChildNodes() { return this.childNodes.length > 0; }
    _adopt(child) {
        if (child.nodeType === 11) {
            const kids = child.childNodes.slice();
            child.childNodes = [];
            for (const k of kids) { k.parentNode = null; }
            return kids;
        }
        if (child.parentNode) { child.parentNode.removeChild(child); }
        return [child];
    }
    appendChild(child) {
        for (const k of this._adopt(child)) { k.parentNode = this; this.childNodes.push(k); }
        return child;
    }
    insertBefore(child, ref) {
        if (!ref) { return this.appendChild(child); }
        const kids = this._adopt(child);
        const i = this.childNodes.indexOf(ref);
        if (i < 0) { throw new Error("NotFoundError: reference node is not a child"); }
        for (const k of kids) { k.parentNode = this; }
        this.childNodes.splice(i, 0, ...kids);
        return child;
    }
    removeChild(child) {
        const i = this.childNodes.indexOf(child);
        if (i < 0) { throw new Error("NotFoundError: node is not a child"); }
        this.childNodes.splice(i, 1);
        child.parentNode = null;
        const doc = this.ownerDocument || (this.nodeType === 9 ? this : null);
        if (doc && doc.activeElement && (doc.activeElement === child || (child.contains && child.contains(doc.activeElement)))) {
            doc.activeElement = doc.body;
        }
        return child;
    }
    replaceChild(newChild, oldChild) {
        this.insertBefore(newChild, oldChild);
        return this.removeChild(oldChild);
    }
    remove() { if (this.parentNode) { this.parentNode.removeChild(this); } }
    contains(other) {
        let n = other;
        while (n) { if (n === this) { return true; } n = n.parentNode; }
        return false;
    }
    get textContent() { return this.childNodes.map((c) => (c.nodeType === 8 ? "" : c.textContent)).join(""); }
    set textContent(v) {
        for (const c of this.childNodes) { c.parentNode = null; }
        this.childNodes = [];
        const s = v == null ? "" : String(v);
        if (s !== "") { this.appendChild(new Text(this.ownerDocument, s)); }
    }
    get nodeValue() { return null; }
}

export class Text extends Node {
    constructor(doc, data) { super(doc); this.data = String(data); }
    get nodeType() { return 3; }
    get nodeName() { return "#text"; }
    get textContent() { return this.data; }
    set textContent(v) { this.data = String(v == null ? "" : v); }
    get nodeValue() { return this.data; }
    set nodeValue(v) { this.data = String(v); }
    get length() { return this.data.length; }
    cloneNode() { return new Text(this.ownerDocument, this.data); }
    get _html() { return this.parentNode && RAW_TEXT.has((this.parentNode.localName || "")) ? this.data : escText(this.data); }
}

export class Comment extends Node {
    constructor(doc, data) { super(doc); this.data = String(data); }
    get nodeType() { return 8; }
    get nodeName() { return "#comment"; }
    get textContent() { return this.data; }
    set textContent(v) { this.data = String(v); }
    cloneNode() { return new Comment(this.ownerDocument, this.data); }
    get _html() { return "<!--" + this.data + "-->"; }
}

class ClassList {
    constructor(el) { this.el = el; }
    _get() { return (this.el.getAttribute("class") || "").split(/\s+/).filter(Boolean); }
    _set(list) { this.el.setAttribute("class", list.join(" ")); }
    get length() { return this._get().length; }
    item(i) { return this._get()[i] || null; }
    contains(c) { return this._get().includes(c); }
    add(...cs) { const l = this._get(); for (const c of cs) { if (!l.includes(c)) { l.push(c); } } this._set(l); }
    remove(...cs) { this._set(this._get().filter((c) => !cs.includes(c))); }
    toggle(c, force) {
        const has = this.contains(c);
        const want = force === undefined ? !has : !!force;
        if (want && !has) { this.add(c); } else if (!want && has) { this.remove(c); }
        return want;
    }
    replace(a, b) { if (!this.contains(a)) { return false; } this._set(this._get().map((c) => (c === a ? b : c))); return true; }
    forEach(fn) { this._get().forEach(fn); }
    [Symbol.iterator]() { return this._get()[Symbol.iterator](); }
    toString() { return this._get().join(" "); }
    get value() { return this.toString(); }
}

function camelToKebab(s) { return s.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()); }
function kebabToCamel(s) { return s.replace(/-([a-z])/g, (m, c) => c.toUpperCase()); }

function makeStyle() {
    const props = new Map();
    const api = {
        setProperty(name, value) {
            if (value === null || value === undefined || value === "") { props.delete(name); } else { props.set(name, String(value)); }
        },
        getPropertyValue(name) { return props.has(name) ? props.get(name) : ""; },
        removeProperty(name) { const v = props.get(name) || ""; props.delete(name); return v; },
        get cssText() { return Array.from(props.entries()).map(([k, v]) => k + ": " + v + ";").join(" "); },
        set cssText(v) {
            props.clear();
            String(v || "").split(";").forEach((decl) => {
                const i = decl.indexOf(":");
                if (i > 0) { props.set(decl.slice(0, i).trim(), decl.slice(i + 1).trim()); }
            });
        },
        get length() { return props.size; },
        item(i) { return Array.from(props.keys())[i] || ""; },
        _props: props,
    };
    return new Proxy(api, {
        get(t, k) {
            if (typeof k !== "string" || k in t) { return t[k]; }
            const name = k.startsWith("--") ? k : camelToKebab(k);
            return props.has(name) ? props.get(name) : "";
        },
        set(t, k, v) {
            if (k === "cssText") { t.cssText = v; return true; }
            if (typeof k !== "string") { t[k] = v; return true; }
            api.setProperty(k.startsWith("--") ? k : camelToKebab(k), v);
            return true;
        },
        has(t, k) { return typeof k === "string" ? (k in t || props.has(camelToKebab(k))) : k in t; },
    });
}

export class Element extends Node {
    constructor(doc, tagName, ns) {
        super(doc);
        this.localName = String(tagName).toLowerCase();
        this.namespaceURI = ns || "http://www.w3.org/1999/xhtml";
        this._attrs = new Map();
        this.style = makeStyle();
        this._classList = null;
        this.scrollTop = 0;
        this.scrollLeft = 0;
        this._rect = null;
    }
    get nodeType() { return 1; }
    get tagName() { return this.namespaceURI.endsWith("xhtml") ? this.localName.toUpperCase() : this.localName; }
    get nodeName() { return this.tagName; }
    get classList() { if (!this._classList) { this._classList = new ClassList(this); } return this._classList; }
    get attributes() {
        const list = Array.from(this._attrs.entries()).map(([name, value]) => ({ name, value, nodeName: name, nodeValue: value }));
        list.getNamedItem = (n) => list.find((a) => a.name === n) || null;
        return list;
    }
    getAttributeNames() { return Array.from(this._attrs.keys()); }
    getAttribute(n) { n = String(n).toLowerCase(); return this._attrs.has(n) ? this._attrs.get(n) : null; }
    setAttribute(n, v) {
        n = String(n).toLowerCase();
        this._attrs.set(n, String(v));
        if (n === "style") { this.style.cssText = String(v); }
    }
    setAttributeNS(ns, n, v) { this.setAttribute(n, v); }
    getAttributeNS(ns, n) { return this.getAttribute(n); }
    removeAttribute(n) { n = String(n).toLowerCase(); this._attrs.delete(n); if (n === "style") { this.style.cssText = ""; } }
    hasAttribute(n) { return this._attrs.has(String(n).toLowerCase()); }
    hasAttributes() { return this._attrs.size > 0; }
    toggleAttribute(n, force) {
        const has = this.hasAttribute(n);
        const want = force === undefined ? !has : !!force;
        if (want && !has) { this.setAttribute(n, ""); } else if (!want && has) { this.removeAttribute(n); }
        return want;
    }
    get id() { return this.getAttribute("id") || ""; }
    set id(v) { this.setAttribute("id", v); }
    get className() { return this.getAttribute("class") || ""; }
    set className(v) { this.setAttribute("class", v); }
    get title() { return this.getAttribute("title") || ""; }
    set title(v) { this.setAttribute("title", v); }
    get name() { return this.getAttribute("name") || ""; }
    set name(v) { this.setAttribute("name", v); }
    get type() { return this.getAttribute("type") || (this.localName === "input" ? "text" : this.localName === "button" ? "submit" : ""); }
    set type(v) { this.setAttribute("type", v); }
    get href() { return this.getAttribute("href") || ""; }
    set href(v) { this.setAttribute("href", v); }
    get src() { return this.getAttribute("src") || ""; }
    set src(v) { this.setAttribute("src", v); }
    get placeholder() { return this.getAttribute("placeholder") || ""; }
    set placeholder(v) { this.setAttribute("placeholder", v); }
    get htmlFor() { return this.getAttribute("for") || ""; }
    set htmlFor(v) { this.setAttribute("for", v); }
    get tabIndex() { const v = this.getAttribute("tabindex"); return v == null ? -1 : Number(v); }
    set tabIndex(v) { this.setAttribute("tabindex", v); }
    get hidden() { return this.hasAttribute("hidden"); }
    set hidden(v) { this.toggleAttribute("hidden", !!v); }
    get disabled() { return this.hasAttribute("disabled"); }
    set disabled(v) { this.toggleAttribute("disabled", !!v); }
    get readOnly() { return this.hasAttribute("readonly"); }
    set readOnly(v) { this.toggleAttribute("readonly", !!v); }
    get multiple() { return this.hasAttribute("multiple"); }
    set multiple(v) { this.toggleAttribute("multiple", !!v); }
    get draggable() { return this.getAttribute("draggable") === "true"; }
    set draggable(v) { this.setAttribute("draggable", v ? "true" : "false"); }
    get contentEditable() { return this.getAttribute("contenteditable") || "inherit"; }
    set contentEditable(v) { this.setAttribute("contenteditable", v); }
    get isContentEditable() { const v = this.getAttribute("contenteditable"); return v === "" || v === "true" || v === "plaintext-only"; }
    get min() { return this.getAttribute("min") || ""; }
    set min(v) { this.setAttribute("min", v); }
    get max() { return this.getAttribute("max") || ""; }
    set max(v) { this.setAttribute("max", v); }
    get step() { return this.getAttribute("step") || ""; }
    set step(v) { this.setAttribute("step", v); }
    get alt() { return this.getAttribute("alt") || ""; }
    set alt(v) { this.setAttribute("alt", v); }
    get rel() { return this.getAttribute("rel") || ""; }
    set rel(v) { this.setAttribute("rel", v); }
    get target() { return this.getAttribute("target") || ""; }
    set target(v) { this.setAttribute("target", v); }
    get accept() { return this.getAttribute("accept") || ""; }
    set accept(v) { this.setAttribute("accept", v); }
    get maxLength() { const v = this.getAttribute("maxlength"); return v == null ? -1 : Number(v); }
    set maxLength(v) { this.setAttribute("maxlength", v); }
    get width() { return Number(this.getAttribute("width") || (this.localName === "canvas" ? 300 : 0)); }
    set width(v) { this.setAttribute("width", v); }
    get height() { return Number(this.getAttribute("height") || (this.localName === "canvas" ? 150 : 0)); }
    set height(v) { this.setAttribute("height", v); }

    /* form-ish state */
    get value() {
        if (this.localName === "select") {
            if (this._value !== undefined) { return this._value; }
            const opts = this.options;
            const sel = opts.find((o) => o.selected) || opts[0];
            return sel ? sel.value : "";
        }
        if (this.localName === "option") {
            return this._value !== undefined ? this._value : (this.hasAttribute("value") ? this.getAttribute("value") : this.textContent);
        }
        if (this.localName === "textarea") { return this._value !== undefined ? this._value : this.textContent; }
        if (this._value !== undefined) { return this._value; }
        const a = this.getAttribute("value");
        if (a !== null) { return a; }
        return (this.localName === "input" && (this.type === "checkbox" || this.type === "radio")) ? "on" : "";
    }
    set value(v) {
        const s = v == null ? "" : String(v);
        if (this.localName === "select") {
            const opts = this.options;
            const hit = opts.find((o) => o.value === s);
            opts.forEach((o) => { o._selected = o === hit; });
            this._value = hit ? s : "";
            return;
        }
        if (this.localName === "option") { this.setAttribute("value", s); return; }
        this._value = s;
    }
    get defaultValue() { return this.getAttribute("value") || ""; }
    set defaultValue(v) { this.setAttribute("value", v); }
    get checked() { return this._checked !== undefined ? this._checked : this.hasAttribute("checked"); }
    set checked(v) { this._checked = !!v; }
    get indeterminate() { return !!this._indeterminate; }
    set indeterminate(v) { this._indeterminate = !!v; }
    get selected() { return this._selected !== undefined ? this._selected : this.hasAttribute("selected"); }
    set selected(v) {
        const sel = this.closest && this.closest("select");
        if (v && sel && !sel.multiple) { sel.options.forEach((o) => { o._selected = false; }); sel._value = undefined; }
        this._selected = !!v;
        if (sel) { sel._value = undefined; }
    }
    get options() {
        const out = this.querySelectorAll("option");
        out.add = (o) => this.appendChild(o);
        out.remove = (i) => { if (out[i]) { out[i].remove(); } };
        return out;
    }
    get selectedIndex() { return this.options.findIndex((o) => o.value === this.value && (o.selected || this._value !== undefined || true)); }
    set selectedIndex(i) { const o = this.options[i]; this.value = o ? o.value : ""; }
    get selectedOptions() { return this.options.filter((o) => o.selected); }
    get files() { return this._files || []; }
    set files(v) { this._files = v; }
    get valueAsNumber() { const n = parseFloat(this.value); return Number.isFinite(n) ? n : NaN; }
    set valueAsNumber(n) { this.value = String(n); }
    get selectionStart() { return this._selStart !== undefined ? this._selStart : String(this.value).length; }
    set selectionStart(v) { this._selStart = v; }
    get selectionEnd() { return this._selEnd !== undefined ? this._selEnd : String(this.value).length; }
    set selectionEnd(v) { this._selEnd = v; }
    setSelectionRange(a, b) { this._selStart = a; this._selEnd = b; }
    select() { this._selStart = 0; this._selEnd = String(this.value).length; }
    checkValidity() { return true; }
    reportValidity() { return true; }
    setCustomValidity(msg) { this.validationMessage = msg; }

    /* tree helpers */
    get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
    get childElementCount() { return this.children.length; }
    get firstElementChild() { return this.children[0] || null; }
    get lastElementChild() { const c = this.children; return c[c.length - 1] || null; }
    get nextElementSibling() {
        let n = this.nextSibling;
        while (n && n.nodeType !== 1) { n = n.nextSibling; }
        return n;
    }
    get previousElementSibling() {
        let n = this.previousSibling;
        while (n && n.nodeType !== 1) { n = n.previousSibling; }
        return n;
    }
    append(...nodes) { for (const n of nodes) { this.appendChild(typeof n === "string" ? new Text(this.ownerDocument, n) : n); } }
    prepend(...nodes) {
        const first = this.firstChild;
        for (const n of nodes) { this.insertBefore(typeof n === "string" ? new Text(this.ownerDocument, n) : n, first); }
    }
    before(...nodes) { if (this.parentNode) { for (const n of nodes) { this.parentNode.insertBefore(typeof n === "string" ? new Text(this.ownerDocument, n) : n, this); } } }
    after(...nodes) {
        if (!this.parentNode) { return; }
        let ref = this.nextSibling;
        for (const n of nodes) { this.parentNode.insertBefore(typeof n === "string" ? new Text(this.ownerDocument, n) : n, ref); }
    }
    replaceWith(...nodes) { if (this.parentNode) { this.before(...nodes); this.remove(); } }
    replaceChildren(...nodes) { this.textContent = ""; this.append(...nodes); }
    cloneNode(deep) {
        const c = this.ownerDocument.createElementNS(this.namespaceURI, this.localName);
        for (const [k, v] of this._attrs) { c.setAttribute(k, v); }
        if (this._value !== undefined) { c._value = this._value; }
        if (this._checked !== undefined) { c._checked = this._checked; }
        if (deep) { for (const k of this.childNodes) { c.appendChild(k.cloneNode(true)); } }
        if (this.content && this.localName === "template") { c.content = deep ? this.content.cloneNode(true) : new DocumentFragment(this.ownerDocument); }
        return c;
    }
    get innerHTML() {
        const kids = this.content && this.localName === "template" ? this.content.childNodes : this.childNodes;
        return kids.map((c) => (c.nodeType === 1 ? c.outerHTML : c._html)).join("");
    }
    set innerHTML(html) {
        this.textContent = "";
        if (this.localName === "template") {
            this.content = new DocumentFragment(this.ownerDocument);
            for (const n of parseHTML(this.ownerDocument, String(html == null ? "" : html))) { this.content.appendChild(n); }
            return;
        }
        if (RAW_TEXT.has(this.localName)) { if (html) { this.appendChild(new Text(this.ownerDocument, String(html))); } return; }
        for (const n of parseHTML(this.ownerDocument, String(html == null ? "" : html))) { this.appendChild(n); }
    }
    get outerHTML() {
        let s = "<" + this.localName;
        for (const [k, v] of this._attrs) { s += v === "" ? " " + k : " " + k + '="' + escAttr(v) + '"'; }
        s += ">";
        if (VOID.has(this.localName)) { return s; }
        return s + this.innerHTML + "</" + this.localName + ">";
    }
    set outerHTML(html) {
        if (!this.parentNode) { return; }
        const nodes = parseHTML(this.ownerDocument, String(html));
        for (const n of nodes) { this.parentNode.insertBefore(n, this); }
        this.remove();
    }
    get innerText() { return this.textContent; }
    set innerText(v) { this.textContent = v; }
    insertAdjacentHTML(pos, html) {
        const nodes = parseHTML(this.ownerDocument, String(html));
        this._insertAdjacent(pos, nodes);
    }
    insertAdjacentElement(pos, el) { this._insertAdjacent(pos, [el]); return el; }
    insertAdjacentText(pos, text) { this._insertAdjacent(pos, [new Text(this.ownerDocument, text)]); }
    _insertAdjacent(pos, nodes) {
        const p = String(pos).toLowerCase();
        if (p === "beforebegin") { for (const n of nodes) { this.parentNode.insertBefore(n, this); } }
        else if (p === "afterbegin") { const f = this.firstChild; for (const n of nodes) { this.insertBefore(n, f); } }
        else if (p === "beforeend") { for (const n of nodes) { this.appendChild(n); } }
        else if (p === "afterend") { const ref = this.nextSibling; for (const n of nodes) { this.parentNode.insertBefore(n, ref); } }
    }
    querySelector(sel) { return querySelectorFirst(this, sel); }
    querySelectorAll(sel) { return querySelectorAll(this, sel); }
    getElementsByTagName(tag) { return querySelectorAll(this, tag); }
    getElementsByClassName(cls) { return querySelectorAll(this, String(cls).trim().split(/\s+/).map((c) => "." + c).join("")); }
    matches(sel) { return matchesSelector(this, sel, null); }
    closest(sel) {
        let n = this;
        while (n && n.nodeType === 1) { if (n.matches(sel)) { return n; } n = n.parentNode; }
        return null;
    }
    get dataset() {
        const el = this;
        return new Proxy({}, {
            get(t, k) { if (typeof k !== "string") { return undefined; } const v = el.getAttribute("data-" + camelToKebab(k)); return v === null ? undefined : v; },
            set(t, k, v) { el.setAttribute("data-" + camelToKebab(k), v); return true; },
            deleteProperty(t, k) { el.removeAttribute("data-" + camelToKebab(k)); return true; },
            has(t, k) { return el.hasAttribute("data-" + camelToKebab(k)); },
            ownKeys() { return el.getAttributeNames().filter((n) => n.startsWith("data-")).map((n) => kebabToCamel(n.slice(5))); },
            getOwnPropertyDescriptor(t, k) {
                const v = el.getAttribute("data-" + camelToKebab(k));
                return v === null ? undefined : { value: v, enumerable: true, configurable: true, writable: true };
            },
        });
    }

    /* layout (fake) */
    getBoundingClientRect() {
        const r = this._rect || { left: 0, top: 0, width: 0, height: 0 };
        const left = r.left || r.x || 0;
        const top = r.top || r.y || 0;
        const width = r.width || 0;
        const height = r.height || 0;
        return { left, top, width, height, x: left, y: top, right: left + width, bottom: top + height, toJSON() { return this; } };
    }
    getClientRects() { return [this.getBoundingClientRect()]; }
    get offsetWidth() { return this.getBoundingClientRect().width; }
    get offsetHeight() { return this.getBoundingClientRect().height; }
    get offsetLeft() { return this.getBoundingClientRect().left; }
    get offsetTop() { return this.getBoundingClientRect().top; }
    get offsetParent() { return this.parentElement; }
    get clientWidth() { return this._clientWidth !== undefined ? this._clientWidth : this.getBoundingClientRect().width; }
    set clientWidth(v) { this._clientWidth = v; }
    get clientHeight() { return this._clientHeight !== undefined ? this._clientHeight : this.getBoundingClientRect().height; }
    set clientHeight(v) { this._clientHeight = v; }
    get scrollWidth() { return this._scrollWidth !== undefined ? this._scrollWidth : this.clientWidth; }
    set scrollWidth(v) { this._scrollWidth = v; }
    get scrollHeight() { return this._scrollHeight !== undefined ? this._scrollHeight : this.clientHeight; }
    set scrollHeight(v) { this._scrollHeight = v; }
    scrollIntoView() { this._scrolledIntoView = (this._scrolledIntoView || 0) + 1; }
    scrollTo(x, y) { if (typeof x === "object" && x) { this.scrollLeft = x.left || 0; this.scrollTop = x.top || 0; } else { this.scrollLeft = x || 0; this.scrollTop = y || 0; } }
    scrollBy(x, y) { if (typeof x === "object" && x) { this.scrollLeft += x.left || 0; this.scrollTop += x.top || 0; } else { this.scrollLeft += x || 0; this.scrollTop += y || 0; } }
    focus() { if (this.ownerDocument) { const prev = this.ownerDocument.activeElement; if (prev === this) { return; } this.ownerDocument.activeElement = this; this.dispatchEvent(new FocusEvent("focus")); this.dispatchEvent(new FocusEvent("focusin", { bubbles: true })); } }
    blur() { if (this.ownerDocument && this.ownerDocument.activeElement === this) { this.ownerDocument.activeElement = this.ownerDocument.body; this.dispatchEvent(new FocusEvent("blur")); this.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); } }
    click() { if (this.disabled) { return; } this.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })); }
    setPointerCapture(id) { this._pointerCapture = id; }
    releasePointerCapture() { this._pointerCapture = null; }
    hasPointerCapture(id) { return this._pointerCapture === id; }
    animate() { const a = { finished: Promise.resolve(), cancel() {}, onfinish: null, play() {}, pause() {} }; return a; }
    getAnimations() { return []; }
    attachShadow() { this.shadowRoot = new DocumentFragment(this.ownerDocument); return this.shadowRoot; }

    /* media / canvas / dialog stubs */
    play() { this._playing = true; this.paused = false; return Promise.resolve(); }
    pause() { this._playing = false; this.paused = true; }
    load() {}
    showModal() { this.open = true; this.setAttribute("open", ""); }
    show() { this.open = true; this.setAttribute("open", ""); }
    close(rv) { this.open = false; this.removeAttribute("open"); if (rv !== undefined) { this.returnValue = rv; } this.dispatchEvent(new Event("close")); }
    getContext() { return makeCanvasContext(this); }
    toDataURL() { return "data:image/png;base64,"; }
    submit() { this.dispatchEvent(new Event("submit", { cancelable: true })); }
    reset() {}
}

export class DocumentFragment extends Node {
    constructor(doc) { super(doc); }
    get nodeType() { return 11; }
    get nodeName() { return "#document-fragment"; }
    get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
    get firstElementChild() { return this.children[0] || null; }
    get innerHTML() { return this.childNodes.map((c) => (c.nodeType === 1 ? c.outerHTML : c._html)).join(""); }
    append(...nodes) { for (const n of nodes) { this.appendChild(typeof n === "string" ? new Text(this.ownerDocument, n) : n); } }
    querySelector(sel) { return querySelectorFirst(this, sel); }
    querySelectorAll(sel) { return querySelectorAll(this, sel); }
    getElementById(id) { return findById(this, String(id)); }
    cloneNode(deep) { const f = new DocumentFragment(this.ownerDocument); if (deep) { for (const k of this.childNodes) { f.appendChild(k.cloneNode(true)); } } return f; }
}

export function makeCanvasContext(canvas) {
    const calls = [];
    const ctx = new Proxy({ canvas, calls, fillStyle: "#000", strokeStyle: "#000", lineWidth: 1, font: "10px sans-serif", globalAlpha: 1 }, {
        get(t, k) {
            if (k in t) { return t[k]; }
            if (k === "measureText") { return (s) => ({ width: String(s).length * 6 }); }
            if (k === "getImageData") { return (x, y, w, h) => ({ data: new Uint8ClampedArray(Math.max(1, w * h * 4)), width: w, height: h }); }
            if (k === "createLinearGradient" || k === "createRadialGradient" || k === "createConicGradient") {
                return () => ({ stops: [], addColorStop(o, c) { this.stops.push([o, c]); } });
            }
            if (k === "createPattern") { return () => ({}); }
            return (...args) => { calls.push([k, args]); };
        },
        set(t, k, v) { t[k] = v; return true; },
    });
    return ctx;
}

/* ----------------------------------------------------------------- document */

export class Document extends Node {
    constructor() {
        super(null);
        this.ownerDocument = null;
        this.defaultView = null;
        this.readyState = "complete";
        this.cookie = "";
        this.visibilityState = "visible";
        this.hidden = false;
        this.documentElement = this.createElement("html");
        this.head = this.createElement("head");
        this.body = this.createElement("body");
        this.documentElement.appendChild(this.head);
        this.documentElement.appendChild(this.body);
        this.appendChild(this.documentElement);
        this.activeElement = this.body;
        this.fonts = { ready: Promise.resolve(), load: () => Promise.resolve([]), check: () => true, add() {} };
        this._execCommands = [];
    }
    get nodeType() { return 9; }
    get nodeName() { return "#document"; }
    get title() { const t = this.head.querySelector("title"); return t ? t.textContent : ""; }
    set title(v) { let t = this.head.querySelector("title"); if (!t) { t = this.createElement("title"); this.head.appendChild(t); } t.textContent = v; }
    createElement(tag) { return new Element(this, tag); }
    createElementNS(ns, tag) { return new Element(this, tag, ns); }
    createTextNode(s) { return new Text(this, s); }
    createComment(s) { return new Comment(this, s); }
    createDocumentFragment() { return new DocumentFragment(this); }
    importNode(node, deep) { return node.cloneNode(!!deep); }
    adoptNode(node) { if (node.parentNode) { node.parentNode.removeChild(node); } return node; }
    createEvent() { const ev = new Event(""); ev.initEvent = (type, bubbles, cancelable) => { ev.type = type; ev.bubbles = !!bubbles; ev.cancelable = !!cancelable; }; ev.initCustomEvent = (type, b, c, detail) => { ev.initEvent(type, b, c); ev.detail = detail; }; return ev; }
    createRange() {
        const doc = this;
        return {
            selectNodeContents(n) { this.node = n; }, setStart() {}, setEnd() {}, collapse() {}, selectNode(n) { this.node = n; },
            getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }; },
            getClientRects() { return []; },
            createContextualFragment(html) { const f = doc.createDocumentFragment(); for (const n of parseHTML(doc, html)) { f.appendChild(n); } return f; },
            deleteContents() {}, insertNode() {}, cloneRange() { return this; }, detach() {},
        };
    }
    getSelection() { return this.defaultView ? this.defaultView.getSelection() : null; }
    getElementById(id) { return findById(this, String(id)); }
    getElementsByTagName(tag) { return querySelectorAll(this, tag); }
    getElementsByClassName(cls) { return querySelectorAll(this, String(cls).trim().split(/\s+/).map((c) => "." + c).join("")); }
    getElementsByName(name) { return querySelectorAll(this, "*").filter((e) => e.getAttribute("name") === name); }
    querySelector(sel) { return querySelectorFirst(this, sel); }
    querySelectorAll(sel) { return querySelectorAll(this, sel); }
    elementFromPoint() { return this._elementFromPoint || null; }
    elementsFromPoint() { return this._elementFromPoint ? [this._elementFromPoint] : []; }
    execCommand(cmd, ui, value) { this._execCommands.push([cmd, value]); return true; }
    queryCommandState() { return false; }
    hasFocus() { return true; }
    get scrollingElement() { return this.documentElement; }
}

/* ------------------------------------------------------------- html parsing */

export function parseHTML(doc, html) {
    const root = new DocumentFragment(doc);
    const stack = [root];
    const top = () => stack[stack.length - 1];
    let i = 0;
    const n = html.length;
    const pushText = (t) => { if (t) { top().appendChild(new Text(doc, decodeEntities(t))); } };
    while (i < n) {
        const lt = html.indexOf("<", i);
        if (lt < 0) { pushText(html.slice(i)); break; }
        pushText(html.slice(i, lt));
        if (html.startsWith("<!--", lt)) {
            const end = html.indexOf("-->", lt + 4);
            const stop = end < 0 ? n : end;
            top().appendChild(new Comment(doc, html.slice(lt + 4, stop)));
            i = end < 0 ? n : end + 3;
            continue;
        }
        if (html.startsWith("<!", lt) || html.startsWith("<?", lt)) {
            const end = html.indexOf(">", lt);
            i = end < 0 ? n : end + 1;
            continue;
        }
        if (html[lt + 1] === "/") {
            const end = html.indexOf(">", lt);
            const name = html.slice(lt + 2, end < 0 ? n : end).trim().toLowerCase();
            for (let s = stack.length - 1; s > 0; s--) {
                if (stack[s].localName === name) { stack.length = s; break; }
            }
            i = end < 0 ? n : end + 1;
            continue;
        }
        const m = /^<([a-zA-Z][a-zA-Z0-9:-]*)/.exec(html.slice(lt, lt + 64));
        if (!m) { pushText("<"); i = lt + 1; continue; }
        const tag = m[1].toLowerCase();
        let j = lt + m[0].length;
        const attrs = [];
        let selfClose = false;
        while (j < n) {
            while (j < n && /\s/.test(html[j])) { j++; }
            if (html[j] === ">") { j++; break; }
            if (html[j] === "/" && html[j + 1] === ">") { selfClose = true; j += 2; break; }
            if (html[j] === "/") { j++; continue; }
            const am = /^[^\s=>/]+/.exec(html.slice(j, j + 256));
            if (!am) { j++; continue; }
            const aname = am[0].toLowerCase();
            j += am[0].length;
            while (j < n && /\s/.test(html[j])) { j++; }
            let aval = "";
            if (html[j] === "=") {
                j++;
                while (j < n && /\s/.test(html[j])) { j++; }
                const q = html[j];
                if (q === '"' || q === "'") {
                    const end = html.indexOf(q, j + 1);
                    aval = html.slice(j + 1, end < 0 ? n : end);
                    j = end < 0 ? n : end + 1;
                } else {
                    const vm = /^[^\s>]*/.exec(html.slice(j));
                    aval = vm[0];
                    j += aval.length;
                }
            }
            attrs.push([aname, decodeEntities(aval)]);
        }
        const isSvg = tag === "svg" || stack.some((s) => s.localName === "svg");
        const el = isSvg ? doc.createElementNS("http://www.w3.org/2000/svg", tag) : doc.createElement(tag);
        for (const [k, v] of attrs) { el.setAttribute(k, v); }
        top().appendChild(el);
        i = j;
        if (selfClose || VOID.has(tag)) { continue; }
        if (tag === "template") {
            // Like browsers: a <template>'s children live in .content, outside the document tree.
            el.content = new DocumentFragment(doc);
            el.content.localName = "template";
            stack.push(el.content);
            continue;
        }
        if (RAW_TEXT.has(tag)) {
            const close = html.toLowerCase().indexOf("</" + tag, i);
            const stop = close < 0 ? n : close;
            const raw = html.slice(i, stop);
            if (raw) { el.appendChild(new Text(doc, tag === "textarea" || tag === "title" ? decodeEntities(raw) : raw)); }
            const gt = close < 0 ? n : html.indexOf(">", close);
            i = gt < 0 ? n : gt + 1;
            continue;
        }
        stack.push(el);
    }
    const out = root.childNodes.slice();
    for (const c of out) { c.parentNode = null; }
    return out;
}

/* ---------------------------------------------------------------- selectors */

function splitTopLevel(s, sep) {
    const out = [];
    let depth = 0; let quote = null; let cur = "";
    for (const ch of s) {
        if (quote) { cur += ch; if (ch === quote) { quote = null; } continue; }
        if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
        if (ch === "(" || ch === "[") { depth++; }
        if (ch === ")" || ch === "]") { depth--; }
        if (ch === sep && depth === 0) { out.push(cur); cur = ""; continue; }
        cur += ch;
    }
    out.push(cur);
    return out.map((x) => x.trim()).filter((x) => x !== "");
}

// Tokenise a complex selector into [{combinator, compound}] from left to right.
function parseComplex(sel) {
    const parts = [];
    let i = 0; let comb = " "; let cur = ""; let depth = 0; let quote = null;
    const flush = () => { if (cur.trim()) { parts.push({ comb, compound: cur.trim() }); cur = ""; } };
    while (i < sel.length) {
        const ch = sel[i];
        if (quote) { cur += ch; if (ch === quote) { quote = null; } i++; continue; }
        if (ch === '"' || ch === "'") { quote = ch; cur += ch; i++; continue; }
        if (ch === "(" || ch === "[") { depth++; }
        if (ch === ")" || ch === "]") { depth--; }
        if (depth === 0 && (ch === ">" || ch === "+" || ch === "~")) { flush(); comb = ch; i++; continue; }
        if (depth === 0 && /\s/.test(ch)) {
            if (cur.trim()) { flush(); comb = " "; }
            i++;
            continue;
        }
        cur += ch; i++;
    }
    flush();
    return parts;
}

function matchCompound(el, compound, scope) {
    if (el.nodeType !== 1) { return false; }
    let rest = compound;
    const tm = /^(\*|[a-zA-Z][a-zA-Z0-9-]*)/.exec(rest);
    if (tm) {
        if (tm[1] !== "*" && el.localName !== tm[1].toLowerCase()) { return false; }
        rest = rest.slice(tm[1].length);
    }
    while (rest) {
        let m;
        if ((m = /^#((?:\\.|[\w-])+)/.exec(rest))) {
            if (el.getAttribute("id") !== m[1].replace(/\\(.)/g, "$1")) { return false; }
        } else if ((m = /^\.((?:\\.|[\w-])+)/.exec(rest))) {
            if (!el.classList.contains(m[1].replace(/\\(.)/g, "$1"))) { return false; }
        } else if ((m = /^\[\s*([^\]~|^$*=\s]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]*))\s*(i)?)?\s*\]/.exec(rest))) {
            const name = m[1].toLowerCase();
            const op = m[2];
            let want = m[3] !== undefined ? m[3] : m[4] !== undefined ? m[4] : m[5];
            let have = el.getAttribute(name);
            if (have === null) { return false; }
            if (m[6]) { have = have.toLowerCase(); want = (want || "").toLowerCase(); }
            if (op === "=" && have !== want) { return false; }
            if (op === "~=" && !have.split(/\s+/).includes(want)) { return false; }
            if (op === "|=" && !(have === want || have.startsWith(want + "-"))) { return false; }
            if (op === "^=" && !(want && have.startsWith(want))) { return false; }
            if (op === "$=" && !(want && have.endsWith(want))) { return false; }
            if (op === "*=" && !(want && have.includes(want))) { return false; }
        } else if ((m = /^:(not|is|where|has)\(/.exec(rest))) {
            let depth = 1; let k = m[0].length;
            while (k < rest.length && depth) { if (rest[k] === "(") { depth++; } else if (rest[k] === ")") { depth--; } k++; }
            const inner = rest.slice(m[0].length, k - 1);
            const fn = m[1];
            if (fn === "not" && matchesSelector(el, inner, scope)) { return false; }
            if ((fn === "is" || fn === "where") && !matchesSelector(el, inner, scope)) { return false; }
            if (fn === "has" && querySelectorAll(el, inner.replace(/^\s*>/, ":scope >")).length === 0) { return false; }
            rest = rest.slice(k);
            continue;
        } else if ((m = /^:nth-(child|of-type)\(([^)]*)\)/.exec(rest))) {
            const sibs = el.parentNode ? el.parentNode.childNodes.filter((c) => c.nodeType === 1 && (m[1] === "child" || c.localName === el.localName)) : [el];
            const idx = sibs.indexOf(el) + 1;
            const expr = m[2].trim();
            let ok;
            if (expr === "odd") { ok = idx % 2 === 1; } else if (expr === "even") { ok = idx % 2 === 0; } else if (/^\d+$/.test(expr)) { ok = idx === Number(expr); } else {
                const am = /^(-?\d*)n\s*(?:([+-])\s*(\d+))?$/.exec(expr);
                if (!am) { ok = false; } else {
                    const a = am[1] === "" ? 1 : am[1] === "-" ? -1 : Number(am[1]);
                    const b = am[2] ? (am[2] === "-" ? -1 : 1) * Number(am[3]) : 0;
                    ok = a === 0 ? idx === b : (idx - b) / a >= 0 && (idx - b) % a === 0;
                }
            }
            if (!ok) { return false; }
        } else if ((m = /^:([a-z-]+)/.exec(rest))) {
            const p = m[1];
            const parent = el.parentNode;
            const sibs = parent ? parent.childNodes.filter((c) => c.nodeType === 1) : [el];
            if (p === "first-child" && sibs[0] !== el) { return false; }
            if (p === "last-child" && sibs[sibs.length - 1] !== el) { return false; }
            if (p === "only-child" && sibs.length !== 1) { return false; }
            if (p === "first-of-type" && sibs.filter((s) => s.localName === el.localName)[0] !== el) { return false; }
            if (p === "last-of-type") { const t = sibs.filter((s) => s.localName === el.localName); if (t[t.length - 1] !== el) { return false; } }
            if (p === "checked" && !(el.checked || (el.localName === "option" && el.selected))) { return false; }
            if (p === "disabled" && !el.disabled) { return false; }
            if (p === "enabled" && el.disabled) { return false; }
            if (p === "empty" && el.childNodes.length) { return false; }
            if (p === "scope" && el !== scope) { return false; }
            if (p === "root" && el !== (el.ownerDocument && el.ownerDocument.documentElement)) { return false; }
            if (p === "focus" && !(el.ownerDocument && el.ownerDocument.activeElement === el)) { return false; }
            if (p === "focus-within" && !(el.ownerDocument && el.contains(el.ownerDocument.activeElement))) { return false; }
            if (p === "hover" || p === "active" || p === "focus-visible") { return false; }
            // unknown pseudo-classes (e.g. :visible) are ignored
        } else {
            throw new SyntaxError("dom stub: unsupported selector part '" + rest + "' in '" + compound + "'");
        }
        rest = rest.slice(m[0].length);
    }
    return true;
}

function matchesComplex(el, parts, scope) {
    // match right-to-left
    const last = parts.length - 1;
    if (!matchCompound(el, parts[last].compound, scope)) { return false; }
    let cur = el;
    for (let k = last; k > 0; k--) {
        const comb = parts[k].comb;
        const want = parts[k - 1].compound;
        if (comb === ">") {
            cur = cur.parentNode;
            if (!cur || !matchCompound(cur, want, scope)) { return false; }
        } else if (comb === " ") {
            let p = cur.parentNode; let found = null;
            while (p && p.nodeType === 1) {
                if (matchCompound(p, want, scope) && matchesComplex(p, parts.slice(0, k), scope)) { found = p; break; }
                p = p.parentNode;
            }
            return !!found;
        } else if (comb === "+") {
            cur = cur.previousElementSibling;
            if (!cur || !matchCompound(cur, want, scope)) { return false; }
        } else if (comb === "~") {
            let p = cur.previousElementSibling; let found = null;
            while (p) {
                if (matchCompound(p, want, scope) && matchesComplex(p, parts.slice(0, k), scope)) { found = p; break; }
                p = p.previousElementSibling;
            }
            return !!found;
        }
    }
    return true;
}

export function matchesSelector(el, sel, scope) {
    return splitTopLevel(String(sel), ",").some((one) => {
        const parts = parseComplex(one);
        return parts.length > 0 && matchesComplex(el, parts, scope);
    });
}

function descendants(root) {
    const out = [];
    const walk = (n) => { for (const c of n.childNodes) { if (c.nodeType === 1) { out.push(c); walk(c); } } };
    walk(root);
    return out;
}

const selectorCache = new Map();
function compileSelector(sel) {
    let groups = selectorCache.get(sel);
    if (!groups) {
        groups = splitTopLevel(sel, ",").map((g) => {
            let g2 = g;
            if (/^[>+~]/.test(g2)) { g2 = ":scope " + g2; }
            return parseComplex(g2);
        });
        if (selectorCache.size > 5000) { selectorCache.clear(); }
        selectorCache.set(sel, groups);
    }
    return groups;
}

function simpleTest(sel) {
    let m;
    if ((m = /^#([\w-]+)$/.exec(sel))) { const id = m[1]; return (el) => el._attrs.get("id") === id; }
    if ((m = /^\.([\w-]+)$/.exec(sel))) { const c = m[1]; return (el) => { const v = el._attrs.get("class"); return !!v && (" " + v + " ").replace(/\s+/g, " ").includes(" " + c + " "); }; }
    if ((m = /^([a-zA-Z][\w-]*)$/.exec(sel))) { const t = m[1].toLowerCase(); return (el) => el.localName === t; }
    if (sel === "*") { return () => true; }
    return null;
}

function findAll(root, sel, firstOnly) {
    const s = String(sel).trim();
    const scope = root.nodeType === 1 ? root : null;
    const fast = simpleTest(s);
    const test = fast || ((el) => {
        const groups = compileSelector(s);
        return groups.some((parts) => parts.length && matchesComplex(el, parts, scope));
    });
    if (!fast) { compileSelector(s); }
    const out = [];
    const stack = [];
    for (let i = root.childNodes.length - 1; i >= 0; i--) { stack.push(root.childNodes[i]); }
    while (stack.length) {
        const n = stack.pop();
        if (n.nodeType !== 1) { continue; }
        if (test(n)) {
            out.push(n);
            if (firstOnly) { return out; }
        }
        const kids = n.childNodes;
        for (let i = kids.length - 1; i >= 0; i--) { stack.push(kids[i]); }
    }
    return out;
}

export function querySelectorAll(root, sel) { return findAll(root, sel, false); }
export function querySelectorFirst(root, sel) { return findAll(root, sel, true)[0] || null; }

export function findById(root, id) {
    const stack = [];
    for (let i = root.childNodes.length - 1; i >= 0; i--) { stack.push(root.childNodes[i]); }
    while (stack.length) {
        const n = stack.pop();
        if (n.nodeType !== 1) { continue; }
        if (n._attrs.get("id") === id) { return n; }
        const kids = n.childNodes;
        for (let i = kids.length - 1; i >= 0; i--) { stack.push(kids[i]); }
    }
    return null;
}
