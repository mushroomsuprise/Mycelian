/*
 * Measures what a template inside a container is showing right now.
 * Shared by the live container page (container_shell.js) and the Spore Studio
 * canvas (container_editor.js) so both size boxes the same way.
 *
 * It works from the outside on any same-origin template (built-in, user or
 * Spore Studio) with no changes to the template:
 *   - only things that paint count: text, images/video/canvas/svg, and boxes
 *     with a background, border or shadow. Transparent wrappers of any size
 *     (a 100%-tall chat column, #sporeRoot, title layers) don't;
 *   - a hidden or faded-out parent hides everything inside it;
 *   - content clipped by an overflow:hidden parent or outside the viewport
 *     doesn't count.
 * A template can opt in to measuring only part of the page with a
 * [data-mycelian-content] element, or report its size itself:
 *   parent.postMessage({type: "mycelian-content-size", width: 400, height: 120}, "*")
 *   parent.postMessage({type: "mycelian-content-size", auto: true}, "*")   // back to measuring
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.MycelianContainerMeasure = api;
    }
})(typeof window !== "undefined" ? window : globalThis, function () {
    var SKIP_TAGS = {
        SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1, LINK: 1, META: 1, TITLE: 1, HEAD: 1, BR: 1, WBR: 1
    };
    var MEDIA_TAGS = {
        IMG: 1, VIDEO: 1, CANVAS: 1, SVG: 1, IFRAME: 1, OBJECT: 1, EMBED: 1, PICTURE: 1, INPUT: 1,
        TEXTAREA: 1, SELECT: 1, BUTTON: 1, PROGRESS: 1, METER: 1
    };
    var MESSAGE_TYPE = "mycelian-content-size";
    var POLL_MS = 1000;

    function isTransparentColor(value) {
        if (!value || value === "transparent") {
            return true;
        }
        var m = /^rgba?\(([^)]*)\)$/.exec(String(value).replace(/\s+/g, ""));
        if (!m) {
            return false;
        }
        var inner = m[1];
        var raw;
        if (inner.indexOf("/") >= 0) {
            raw = inner.split("/")[1];
        } else {
            var parts = inner.split(",");
            if (parts.length < 4) {
                return false;
            }
            raw = parts[3];
        }
        var alpha = raw.indexOf("%") >= 0 ? parseFloat(raw) / 100 : parseFloat(raw);
        return !(alpha > 0);
    }

    function hasBorder(cs) {
        var sides = ["Top", "Right", "Bottom", "Left"];
        for (var i = 0; i < sides.length; i++) {
            var width = parseFloat(cs["border" + sides[i] + "Width"]) || 0;
            var style = cs["border" + sides[i] + "Style"];
            if (width > 0 && style && style !== "none" && style !== "hidden" &&
                !isTransparentColor(cs["border" + sides[i] + "Color"])) {
                return true;
            }
        }
        return false;
    }

    /** True when the element's own box puts pixels on screen. */
    function paintsBox(el, cs) {
        if (MEDIA_TAGS[String(el.tagName).toUpperCase()]) {
            return true;
        }
        if (!isTransparentColor(cs.backgroundColor)) {
            return true;
        }
        if (cs.backgroundImage && cs.backgroundImage !== "none") {
            return true;
        }
        if (cs.boxShadow && cs.boxShadow !== "none") {
            return true;
        }
        return hasBorder(cs);
    }

    /** Hidden here means nothing inside can show either. */
    function hidesSubtree(el, cs) {
        if (cs.display === "none" || cs.contentVisibility === "hidden") {
            return true;
        }
        if (Number(cs.opacity) === 0) {
            return true;
        }
        return el.getAttribute && el.getAttribute("data-spore-hidden") === "true";
    }

    function clipsChildren(cs) {
        var values = [cs.overflow, cs.overflowX, cs.overflowY];
        for (var i = 0; i < values.length; i++) {
            if (values[i] && values[i] !== "visible") {
                return true;
            }
        }
        return false;
    }

    function intersect(a, b) {
        var left = Math.max(a.left, b.left);
        var top = Math.max(a.top, b.top);
        var right = Math.min(a.right, b.right);
        var bottom = Math.min(a.bottom, b.bottom);
        if (right - left < 1 || bottom - top < 1) {
            return null;
        }
        return { left: left, top: top, right: right, bottom: bottom };
    }

    function rectOf(r) {
        return { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    }

    /**
     * Visible content bounds inside ``doc`` for a viewWidth x viewHeight viewport.
     * Returns {w, h, x, y}, {empty: true} when nothing shows, or null when the
     * document can't be read yet.
     */
    function measureDocument(doc, viewWidth, viewHeight) {
        if (!doc || !doc.body || !doc.defaultView || viewWidth < 2 || viewHeight < 2) {
            return null;
        }
        var view = doc.defaultView;
        var viewport = { left: 0, top: 0, right: viewWidth, bottom: viewHeight };
        var box = null;

        function add(rect, clip) {
            var hit = intersect(rectOf(rect), clip);
            if (!hit) {
                return;
            }
            if (!box) {
                box = hit;
                return;
            }
            box.left = Math.min(box.left, hit.left);
            box.top = Math.min(box.top, hit.top);
            box.right = Math.max(box.right, hit.right);
            box.bottom = Math.max(box.bottom, hit.bottom);
        }

        function visit(node, clip, parentShown) {
            if (node.nodeType === 3) {
                if (parentShown && /\S/.test(node.nodeValue || "")) {
                    var range = doc.createRange();
                    range.selectNodeContents(node);
                    add(range.getBoundingClientRect(), clip);
                }
                return;
            }
            if (node.nodeType !== 1 || SKIP_TAGS[String(node.tagName).toUpperCase()]) {
                return;
            }
            var cs = view.getComputedStyle(node);
            if (hidesSubtree(node, cs)) {
                return;
            }
            // Computed visibility already includes inheritance; a child can turn itself back on.
            var shown = cs.visibility !== "hidden" && cs.visibility !== "collapse";
            var rect = node.getBoundingClientRect();
            var own = cs.position === "fixed" ? viewport : clip;
            if (shown && rect.width >= 1 && rect.height >= 1 && paintsBox(node, cs)) {
                add(rect, own);
            }
            if (MEDIA_TAGS[String(node.tagName).toUpperCase()]) {
                return;
            }
            var inner = own;
            if (clipsChildren(cs)) {
                inner = intersect(own, rectOf(rect));
                if (!inner) {
                    return;
                }
            }
            for (var child = node.firstChild; child; child = child.nextSibling) {
                visit(child, inner, shown);
            }
        }

        var start = doc.querySelector("[data-mycelian-content]");
        if (start) {
            // Opt-in root: measure only inside it, but a hidden ancestor still hides it.
            for (var up = start.parentElement; up && up !== doc.documentElement; up = up.parentElement) {
                if (hidesSubtree(up, view.getComputedStyle(up))) {
                    return { empty: true };
                }
            }
            visit(start, viewport, true);
        } else {
            // body's own background is the page backdrop, not content.
            var bodyStyle = view.getComputedStyle(doc.body);
            if (hidesSubtree(doc.body, bodyStyle)) {
                return { empty: true };
            }
            var bodyClip = clipsChildren(bodyStyle) ? intersect(viewport, rectOf(doc.body.getBoundingClientRect())) : viewport;
            if (!bodyClip) {
                return { empty: true };
            }
            for (var node = doc.body.firstChild; node; node = node.nextSibling) {
                visit(node, bodyClip, bodyStyle.visibility !== "hidden" && bodyStyle.visibility !== "collapse");
            }
        }
        if (!box) {
            return { empty: true };
        }
        return {
            w: Math.ceil(box.right - box.left),
            h: Math.ceil(box.bottom - box.top),
            x: box.left,
            y: box.top
        };
    }

    function measure(iframe) {
        try {
            var doc = iframe.contentDocument;
            var width = iframe.clientWidth || iframe.offsetWidth;
            var height = iframe.clientHeight || iframe.offsetHeight;
            return measureDocument(doc, width, height);
        } catch (err) {
            return null;
        }
    }

    /**
     * Sizes templates report themselves. A report wins over measuring until the
     * template sends {auto: true}.
     */
    function createExplicitSizes() {
        var sizes = {};
        return {
            /** Handle a window message. Returns the slot id it changed, or null. */
            handle: function (ev, frames) {
                var data = ev && ev.data;
                if (!data || data.type !== MESSAGE_TYPE) {
                    return null;
                }
                for (var i = 0; i < frames.length; i++) {
                    if (frames[i].contentWindow !== ev.source) {
                        continue;
                    }
                    var id = frames[i].getAttribute("data-slot");
                    if (data.auto) {
                        delete sizes[id];
                        return id;
                    }
                    var width = Number(data.width);
                    var height = Number(data.height);
                    if (!isFinite(width) || !isFinite(height)) {
                        return null;
                    }
                    sizes[id] = width <= 0 || height <= 0
                        ? { empty: true }
                        : { w: width, h: height, x: 0, y: 0 };
                    return id;
                }
                return null;
            },
            get: function (id) {
                return sizes[id] || null;
            },
            clear: function (id) {
                delete sizes[id];
            }
        };
    }

    function frameScheduler(callback) {
        var pending = false;
        var raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : function (fn) {
            return setTimeout(fn, 16);
        };
        return function () {
            if (pending) {
                return;
            }
            pending = true;
            raf(function () {
                pending = false;
                callback();
            });
        };
    }

    /**
     * Call ``onChange`` whenever what the template shows might have changed.
     * Re-attaches after every reload of the iframe. Returns {stop}.
     */
    function watch(iframe, onChange) {
        var notify = frameScheduler(onChange);
        var detach = null;
        var stopped = false;

        function attach() {
            if (detach) {
                detach();
                detach = null;
            }
            var doc;
            try {
                doc = iframe.contentDocument;
            } catch (err) {
                return;
            }
            if (!doc || !doc.documentElement) {
                return;
            }
            var cleanups = [];
            var view = doc.defaultView || {};
            var MO = view.MutationObserver || (typeof MutationObserver === "function" ? MutationObserver : null);
            if (MO) {
                var mo = new MO(notify);
                mo.observe(doc.documentElement, {
                    childList: true,
                    subtree: true,
                    characterData: true,
                    attributes: true,
                    attributeFilter: ["class", "style", "hidden", "data-spore-hidden", "src", "open"]
                });
                cleanups.push(function () { mo.disconnect(); });
            }
            var RO = view.ResizeObserver || (typeof ResizeObserver === "function" ? ResizeObserver : null);
            if (RO) {
                var ro = new RO(notify);
                ro.observe(doc.documentElement);
                if (doc.body) {
                    ro.observe(doc.body);
                }
                cleanups.push(function () { ro.disconnect(); });
            }
            ["transitionend", "animationend", "load"].forEach(function (type) {
                doc.addEventListener(type, notify, true);
                cleanups.push(function () { doc.removeEventListener(type, notify, true); });
            });
            if (doc.fonts && doc.fonts.ready && typeof doc.fonts.ready.then === "function") {
                doc.fonts.ready.then(notify, function () {});
            }
            detach = function () {
                cleanups.forEach(function (fn) {
                    try { fn(); } catch (err) { /* page already gone */ }
                });
            };
            notify();
        }

        function onLoad() {
            if (!stopped) {
                attach();
            }
        }

        iframe.addEventListener("load", onLoad);
        attach();
        var poll = setInterval(function () {
            if (!stopped) {
                notify();
            }
        }, POLL_MS);
        return {
            stop: function () {
                stopped = true;
                clearInterval(poll);
                iframe.removeEventListener("load", onLoad);
                if (detach) {
                    detach();
                    detach = null;
                }
            }
        };
    }

    return {
        MESSAGE_TYPE: MESSAGE_TYPE,
        measure: measure,
        measureDocument: measureDocument,
        createExplicitSizes: createExplicitSizes,
        watch: watch
    };
});
