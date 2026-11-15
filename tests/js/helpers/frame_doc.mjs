// Copyright (c) 2024-2026 Mycelian
// SPDX-License-Identifier: MIT
// A fake same-origin iframe document for container measuring tests. Describe the page as a
// tree; every element gets a fixed rect and computed style (unset properties use the
// browser defaults that matter to container_measure.js).
//
//   frameDoc([{ rect: [0, 0, 400, 300], children: [
//       { rect: [10, 20, 100, 50], style: { backgroundColor: "rgb(255, 0, 0)" } },
//       { text: "hello", rect: [5, 5, 40, 12] },
//       { tag: "img", rect: [0, 100, 64, 64], attrs: { "data-spore-hidden": "true" } },
//   ] }])

import { Document } from "./dom.mjs";

const DEFAULT_STYLE = {
    display: "block",
    visibility: "visible",
    opacity: "1",
    contentVisibility: "visible",
    backgroundColor: "rgba(0, 0, 0, 0)",
    backgroundImage: "none",
    boxShadow: "none",
    overflow: "visible",
    overflowX: "visible",
    overflowY: "visible",
    position: "static",
    borderTopWidth: "0px",
    borderRightWidth: "0px",
    borderBottomWidth: "0px",
    borderLeftWidth: "0px",
    borderTopStyle: "none",
    borderRightStyle: "none",
    borderBottomStyle: "none",
    borderLeftStyle: "none",
    borderTopColor: "rgb(0, 0, 0)",
    borderRightColor: "rgb(0, 0, 0)",
    borderBottomColor: "rgb(0, 0, 0)",
    borderLeftColor: "rgb(0, 0, 0)",
};

export const RED = { backgroundColor: "rgb(255, 0, 0)" };

function rectOf(r) {
    return { left: r[0], top: r[1], width: r[2], height: r[3] };
}

export function frameDoc(tree, opts = {}) {
    const doc = new Document();
    const styles = new Map();
    const textRects = new Map();
    styles.set(doc.body, Object.assign({}, DEFAULT_STYLE, opts.bodyStyle || {}));
    if (opts.bodyRect) { doc.body._rect = rectOf(opts.bodyRect); }

    function build(spec, parent) {
        if (spec.text !== undefined) {
            const node = doc.createTextNode(spec.text);
            textRects.set(node, spec.rect ? rectOf(spec.rect) : { left: 0, top: 0, width: 0, height: 0 });
            parent.appendChild(node);
            return node;
        }
        const el = doc.createElement(spec.tag || "div");
        if (spec.rect) { el._rect = rectOf(spec.rect); }
        for (const [k, v] of Object.entries(spec.attrs || {})) { el.setAttribute(k, v); }
        styles.set(el, Object.assign({}, DEFAULT_STYLE, spec.style || {}));
        parent.appendChild(el);
        for (const child of spec.children || []) { build(child, el); }
        if (spec.id) { el.id = spec.id; }
        return el;
    }
    for (const spec of tree) { build(spec, doc.body); }

    doc.defaultView = { getComputedStyle: (el) => styles.get(el) || Object.assign({}, DEFAULT_STYLE) };
    doc.createRange = () => ({
        node: null,
        selectNodeContents(n) { this.node = n; },
        getBoundingClientRect() {
            const r = textRects.get(this.node) || { left: 0, top: 0, width: 0, height: 0 };
            return { left: r.left, top: r.top, width: r.width, height: r.height, right: r.left + r.width, bottom: r.top + r.height };
        },
    });
    doc.__styles = styles;
    return doc;
}
