/* Copyright (c) 2024-2026 Mycelian
 * SPDX-License-Identifier: MIT
 *
 * Spore Studio Game panel: the card's styles, markup and auto-sizing, shared by overlays
 * (spore_data_runtime.js paints, rotates and keeps it live) and the editor's canvas preview,
 * so both draw exactly the same thing.
 *
 *   SporeGamePanel.config(get)          options from get(key) (data-spore-game-* attributes or block props)
 *   SporeGamePanel.pageHtml(game, cfg)  one game's card
 *   SporeGamePanel.fit(el, host, games, cfg)  sets el's font size so the fullest card fills the box
 *
 * Every size in the card is in em, so one font size scales the whole card.
 */
(function (root) {
    if (!root || root.SporeGamePanel) { return; }

    var CSS = [
        ".spore-game_panel { --gp-bar: #a855f7; --gp-goal: #38bdf8; --gp-done: #22c55e; --gp-muted: #94a3b8;",
        "  --gp-track: rgba(255,255,255,0.15); display: flex; flex-direction: column; box-sizing: border-box;",
        "  overflow: hidden; padding: 0.6em 0.75em; line-height: 1.2; }",
        ".spore-gp-host { position: relative; flex: 1 1 auto; min-height: 0; overflow: hidden; }",
        ".spore-gp-page { display: flex; flex-direction: column; gap: 0.5em; width: 100%; }",
        ".spore-gp-page.is-measure { position: absolute; left: 0; right: 0; top: 0; visibility: hidden; pointer-events: none; }",
        ".spore-gp-page.is-exiting { position: absolute; left: 0; right: 0; top: 0; pointer-events: none; }",
        ".spore-gp-page.is-slide_horizontal { animation: sporeGpSlideXIn ease both; }",
        ".spore-gp-page.is-slide_vertical { animation: sporeGpSlideYIn ease both; }",
        ".spore-gp-page.is-fade { animation: sporeGpFadeIn ease both; }",
        ".spore-gp-page.is-exit-slide_horizontal { animation: sporeGpSlideXOut ease both; }",
        ".spore-gp-page.is-exit-slide_vertical { animation: sporeGpSlideYOut ease both; }",
        ".spore-gp-page.is-exit-fade { animation: sporeGpFadeOut ease both; }",
        "@keyframes sporeGpSlideXIn { from { transform: translateX(100%); } to { transform: translateX(0); } }",
        "@keyframes sporeGpSlideXOut { from { transform: translateX(0); } to { transform: translateX(-100%); } }",
        "@keyframes sporeGpSlideYIn { from { transform: translateY(100%); } to { transform: translateY(0); } }",
        "@keyframes sporeGpSlideYOut { from { transform: translateY(0); } to { transform: translateY(-100%); } }",
        "@keyframes sporeGpFadeIn { from { opacity: 0; } to { opacity: 1; } }",
        "@keyframes sporeGpFadeOut { from { opacity: 1; } to { opacity: 0; } }",
        ".spore-gp-head { display: flex; align-items: center; gap: 0.7em; min-width: 0; }",
        ".spore-gp-cover { position: relative; flex: 0 0 auto; width: 3.4em; height: 3.4em; border-radius: 0.45em; overflow: hidden;",
        "  display: flex; align-items: center; justify-content: center; font-weight: 800; background: #334155; color: #fff; }",
        ".spore-gp-cover img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }",
        ".spore-gp-main { flex: 1 1 auto; min-width: 0; }",
        ".spore-gp-title { font-weight: 750; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }",
        ".spore-gp-chips { display: flex; flex-wrap: wrap; gap: 0.35em; align-items: center; margin-top: 0.3em; font-size: 0.62em; color: var(--gp-muted); }",
        ".spore-gp-chip { padding: 0.1em 0.55em; border-radius: 999px; background: rgba(255,255,255,0.1); font-weight: 650; }",
        ".spore-gp-chip.is-ok { background: color-mix(in srgb, var(--gp-done) 25%, transparent); color: var(--gp-done); }",
        ".spore-gp-bar, .spore-gp-goal-bar { display: block; height: 0.3em; border-radius: 999px; background: var(--gp-track); overflow: hidden; }",
        ".spore-gp-bar { margin-top: 0.45em; }",
        ".spore-gp-bar > span, .spore-gp-goal-bar > span { display: block; height: 100%; border-radius: 999px; background: var(--gp-bar);",
        "  transition: width .6s ease; }",
        ".spore-gp-pct { flex: 0 0 auto; font-size: 1.3em; font-weight: 800; font-variant-numeric: tabular-nums; }",
        ".spore-gp-pct small { font-size: 0.5em; color: var(--gp-muted); margin-left: 0.05em; }",
        ".spore-gp-grades { display: flex; flex-wrap: wrap; gap: 0.3em 0.9em; font-size: 0.7em; font-weight: 650; color: var(--gp-muted);",
        "  font-variant-numeric: tabular-nums; }",
        ".spore-gp-grade { display: inline-flex; align-items: center; gap: 0.3em; }",
        ".spore-gp-grade svg { width: 1.3em; height: 1.3em; }",
        ".spore-gp-goals { display: flex; flex-direction: column; gap: 0.35em; }",
        ".spore-gp-goal { display: grid; grid-template-columns: minmax(0, 1fr) auto 6em 2.8em; align-items: center; gap: 0.6em;",
        "  font-size: 0.72em; font-variant-numeric: tabular-nums; }",
        ".spore-gp-goal-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }",
        ".spore-gp-goal-count { color: var(--gp-muted); font-size: 0.9em; }",
        ".spore-gp-goal-pct { text-align: right; font-weight: 700; }",
        ".spore-gp-goal-bar > span { background: var(--gp-goal); }",
        ".spore-gp-goal.is-done .spore-gp-goal-bar > span { background: var(--gp-done); }",
        ".spore-gp-goal.is-done .spore-gp-goal-pct { color: var(--gp-done); }",
        ".spore-gp-goal-more { font-size: 0.62em; color: var(--gp-muted); }",
        ".spore-gp-runs { display: flex; flex-direction: column; gap: 0.3em; }",
        ".spore-gp-run { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; align-items: center; gap: 0.5em;",
        "  font-size: 0.72em; font-variant-numeric: tabular-nums; }",
        ".spore-gp-run svg { width: 1.15em; height: 1.15em; color: var(--gp-muted); }",
        ".spore-gp-run-cat { font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }",
        ".spore-gp-run-pb { font-weight: 800; }",
        ".spore-gp-run-pb small { font-weight: 600; color: var(--gp-muted); margin-right: 0.3em; }",
        ".spore-gp-run-n { color: var(--gp-muted); font-size: 0.9em; text-align: right; min-width: 6.5em; }",
        ".spore-gp-empty { font-size: 0.8em; color: var(--gp-muted); padding: 0.5em 0; }",
        ".spore-gp-dots { display: flex; gap: 0.3em; justify-content: center; }",
        ".spore-gp-dots span { width: 0.32em; height: 0.32em; border-radius: 50%; background: var(--gp-track); }",
        ".spore-gp-dots span.is-on { background: var(--gp-bar); }"
    ].join("\n");

    var TROPHY_TONES = {
        bronze: ["#F2C29A", "#C47A45", "#6E3C1C"],
        silver: ["#FFFFFF", "#BAC4CD", "#68737E"],
        gold: ["#FFF1B0", "#E2B23A", "#83600E"],
        platinum: ["#EEF8FF", "#9CC7F0", "#466FA6"]
    };

    /* Auto size: the card is about this many em wide at its narrowest comfortable layout. */
    var MIN_EMS_WIDE = 16;
    var MIN_FONT = 8;
    var MAX_FONT = 160;

    function ensureStyles(doc) {
        doc = doc || root.document;
        if (!doc || doc.getElementById("spore-game-panel-css")) { return; }
        var style = doc.createElement("style");
        style.id = "spore-game-panel-css";
        style.textContent = CSS;
        (doc.head || doc.documentElement || doc.body).appendChild(style);
    }

    function esc(value) {
        return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c];
        });
    }

    function isOn(raw, fallback) {
        if (raw === undefined || raw === null || raw === "") { return fallback; }
        if (typeof raw === "boolean") { return raw; }
        var v = String(raw).toLowerCase();
        return v !== "false" && v !== "0" && v !== "no" && v !== "off";
    }

    function animName(raw) {
        var name = String(raw || "fade").toLowerCase().replace(/\s+/g, "_");
        if (name === "slide_vertical" || name === "vertical") { return "slide_vertical"; }
        if (name === "slide_horizontal" || name === "horizontal") { return "slide_horizontal"; }
        if (name === "none") { return "none"; }
        return "fade";
    }

    /* get(key) returns a prop value or attribute string (undefined when unset). Keys are the block's prop names. */
    function config(get) {
        var seconds = parseFloat(get("rotate_seconds"));
        var maxGoals = parseInt(get("max_goals"), 10);
        var keys = get("game_keys");
        keys = Array.isArray(keys) ? keys : String(keys || "").split(/[,\n]/);
        return {
            source: String(get("game_source") || "selected").toLowerCase(),
            keys: keys.map(function (k) { return String(k).trim(); }).filter(Boolean),
            seconds: isFinite(seconds) && seconds >= 2 ? seconds : 10,
            anim: animName(get("rotate_animation")),
            showCover: isOn(get("show_cover"), true),
            showTrophies: isOn(get("show_trophies"), true),
            showGoals: isOn(get("show_goals"), true),
            showSpeedrun: isOn(get("show_speedrun"), true),
            showPlayed: isOn(get("show_last_played"), true),
            showDots: isOn(get("show_dots"), true),
            autoSize: isOn(get("auto_size"), true),
            maxGoals: isFinite(maxGoals) && maxGoals >= 0 ? maxGoals : 4,
            titleColor: get("title_color") || "#ffffff",
            colors: {
                "--gp-bar": get("bar_color") || "#a855f7",
                "--gp-goal": get("goal_bar_color") || "#38bdf8",
                "--gp-done": get("done_color") || "#22c55e",
                "--gp-muted": get("muted_color") || "#94a3b8"
            }
        };
    }

    function applyColors(el, cfg) {
        Object.keys(cfg.colors).forEach(function (name) { el.style.setProperty(name, cfg.colors[name]); });
    }

    function trophy(grade) {
        var t = TROPHY_TONES[grade] || TROPHY_TONES.bronze;
        return "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M6.8 3h10.4v4.2c0 3.3-2 5.7-4.4 6.3V16h2.6l.9 2.2H7.7l.9-2.2h2.6v-2.5" +
            "C8.8 12.9 6.8 10.5 6.8 7.2V3z\" fill=\"" + t[1] + "\" stroke=\"" + t[2] + "\" stroke-width=\".6\"/>" +
            "<rect x=\"6.2\" y=\"18.6\" width=\"11.6\" height=\"2.6\" rx=\".9\" fill=\"" + t[1] + "\" stroke=\"" + t[2] + "\" stroke-width=\".6\"/>" +
            "<path d=\"M7.2 5.2H4.4v1.3c0 2.1 1.4 3.6 3.4 4M16.8 5.2h2.8v1.3c0 2.1-1.4 3.6-3.4 4\" fill=\"none\" stroke=\"" + t[0] +
            "\" stroke-width=\"1.6\" stroke-linecap=\"round\"/></svg>";
    }

    function ago(ts) {
        ts = Number(ts) || 0;
        if (!ts) { return ""; }
        var secs = Math.max(0, Date.now() / 1000 - ts);
        if (secs < 90) { return "just now"; }
        var steps = [["minute", 60, 60], ["hour", 3600, 24], ["day", 86400, 7], ["week", 604800, 5], ["month", 2592000, 12]];
        for (var i = 0; i < steps.length; i++) {
            var n = Math.floor(secs / steps[i][1]);
            if (n < steps[i][2]) {
                if (steps[i][0] === "day" && n === 1) { return "yesterday"; }
                n = Math.max(1, n);
                return n + " " + steps[i][0] + (n === 1 ? "" : "s") + " ago";
            }
        }
        var years = Math.max(1, Math.floor(secs / 31536000));
        return years + " year" + (years === 1 ? "" : "s") + " ago";
    }

    function initials(title) {
        var words = String(title || "").replace(/:/g, " ").split(/\s+/).filter(function (w) { return /^[A-Za-z0-9]/.test(w); });
        return (words.slice(0, 2).map(function (w) { return w.charAt(0); }).join("") || "?").toUpperCase();
    }

    function num(n) { return Number(n || 0).toLocaleString(); }

    function goalCount(goal) {
        if (goal.kind === "manual" || goal.kind === "count") { return num(goal.current) + " / " + num(goal.target); }
        if (goal.kind === "percent") { return (goal.current || 0) + "% / " + (goal.target || 0) + "%"; }
        return "";
    }

    function shownRuns(game, cfg) {
        var runs = Array.isArray(game && game.speedruns) ? game.speedruns : [];
        return cfg.showSpeedrun ? runs : [];
    }

    var STOPWATCH = "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"" +
        " stroke-linecap=\"round\"><circle cx=\"12\" cy=\"13\" r=\"8\"/><path d=\"M12 9v4l2.5 2.5M10 2h4M12 2v3\"/></svg>";

    function runsHtml(game, cfg) {
        var runs = shownRuns(game, cfg);
        if (!runs.length) { return ""; }
        return "<div class=\"spore-gp-runs\">" + runs.map(function (r) {
            var best = r.pb ? "<small>PB</small>" + esc(r.pb) : (r.read ? "<small>No finished run</small>" : "");
            var n = Number(r.attempts || 0);
            return "<div class=\"spore-gp-run\">" + STOPWATCH +
                "<span class=\"spore-gp-run-cat\">" + esc(r.category || "") + "</span>" +
                "<span class=\"spore-gp-run-pb\">" + best + "</span>" +
                "<span class=\"spore-gp-run-n\">" + (r.read ? num(n) + (n === 1 ? " attempt" : " attempts") : "") + "</span></div>";
        }).join("") + "</div>";
    }

    function shownGoals(game, cfg) {
        var goals = Array.isArray(game && game.goals) ? game.goals : [];
        return cfg.showGoals ? goals.slice(0, cfg.maxGoals) : [];
    }

    /* How tall a card is, in rows: used to size every rotated card by the fullest one. */
    function weight(game, cfg) {
        var goals = Array.isArray(game && game.goals) ? game.goals : [];
        var shown = shownGoals(game, cfg).length;
        /* The PlayStation trophy row (cups) is a touch taller than Steam's achievements line. */
        var trophies = !cfg.showTrophies ? 0 : game.platform === "psn" ? 1.1 : game.total ? 1 : 0;
        return shown + (cfg.showGoals && goals.length > shown ? 0.6 : 0) + trophies + shownRuns(game, cfg).length;
    }

    function pageHtml(game, cfg, dots) {
        var psn = game.platform === "psn";
        var progress = Math.max(0, Math.min(100, Math.round(Number(game.progress) || 0)));
        var html = "<div class=\"spore-gp-head\">";
        if (cfg.showCover) {
            html += "<div class=\"spore-gp-cover\">" + esc(initials(game.title)) +
                (game.cover ? "<img src=\"" + esc(game.cover) + "\" alt=\"\" onerror=\"this.remove()\">" : "") + "</div>";
        }
        var chips = "<span class=\"spore-gp-chip\">" + esc(game.platform_label || (psn ? "PlayStation" : "Steam")) + "</span>";
        if (game.completed) { chips += "<span class=\"spore-gp-chip is-ok\">" + (psn ? "Platinum" : "Perfect") + "</span>"; }
        if (game.goals_total) {
            chips += game.goals_complete
                ? "<span class=\"spore-gp-chip is-ok\">Goals done</span>"
                : "<span class=\"spore-gp-chip\">" + game.goals_done + " / " + game.goals_total + " goals</span>";
        }
        if (cfg.showPlayed && game.last_played) { chips += "<span>Played " + esc(ago(game.last_played)) + "</span>"; }
        html += "<div class=\"spore-gp-main\"><div class=\"spore-gp-title\" style=\"color:" + esc(cfg.titleColor) + "\">" +
            esc(game.title || "") + "</div><div class=\"spore-gp-chips\">" + chips + "</div>" +
            "<div class=\"spore-gp-bar\"><span style=\"width:" + progress + "%\"></span></div></div>" +
            "<div class=\"spore-gp-pct\">" + progress + "<small>%</small></div></div>";
        if (cfg.showTrophies) {
            if (psn) {
                var earned = game.grades_earned || {};
                html += "<div class=\"spore-gp-grades\">" + ["platinum", "gold", "silver", "bronze"].map(function (g) {
                    return "<span class=\"spore-gp-grade\">" + trophy(g) + num(earned[g]) + "</span>";
                }).join("") + "</div>";
            } else if (game.total) {
                html += "<div class=\"spore-gp-grades\"><span class=\"spore-gp-grade\">" + num(game.earned) + " / " +
                    num(game.total) + " achievements</span></div>";
            }
        }
        html += runsHtml(game, cfg);
        var all = Array.isArray(game.goals) ? game.goals : [];
        var shown = shownGoals(game, cfg);
        if (shown.length) {
            html += "<div class=\"spore-gp-goals\">" + shown.map(function (goal) {
                var pct = Math.max(0, Math.min(100, Number(goal.progress) || 0));
                return "<div class=\"spore-gp-goal" + (goal.completed ? " is-done" : "") + "\">" +
                    "<span class=\"spore-gp-goal-name\">" + (goal.completed ? "✓ " : "") + esc(goal.name) + "</span>" +
                    "<span class=\"spore-gp-goal-count\">" + esc(goalCount(goal)) + "</span>" +
                    "<span class=\"spore-gp-goal-bar\"><span style=\"width:" + pct + "%\"></span></span>" +
                    "<span class=\"spore-gp-goal-pct\">" + pct + "%</span></div>";
            }).join("");
            if (all.length > shown.length) {
                var more = all.length - shown.length;
                html += "<div class=\"spore-gp-goal-more\">+" + more + " more goal" + (more === 1 ? "" : "s") + "</div>";
            }
            html += "</div>";
        }
        if (dots && dots.count > 1) {
            html += "<div class=\"spore-gp-dots\">";
            for (var d = 0; d < dots.count; d++) { html += "<span" + (d === dots.index ? " class=\"is-on\"" : "") + "></span>"; }
            html += "</div>";
        }
        return html;
    }

    function px(value) { return parseFloat(value) || 0; }

    /* Auto size: the font size at which the fullest card fills the box's height, capped by its width.
       Measured once at a reference size; everything is in em, so height scales with the font size. */
    function fit(el, host, games, cfg) {
        if (!cfg.autoSize || !el || !host) { return null; }
        var w = el.clientWidth;
        var h = el.clientHeight;
        if (!w || !h) { return null; }
        var fullest = null;
        (games || []).forEach(function (g) { if (!fullest || weight(g, cfg) > weight(fullest, cfg)) { fullest = g; } });
        var probe = el.ownerDocument.createElement("div");
        probe.className = "spore-gp-page is-measure";
        probe.innerHTML = fullest ? pageHtml(fullest, cfg, { count: (games || []).length, index: 0 })
            : "<div class=\"spore-gp-empty\">No games</div>";
        host.appendChild(probe);
        var heightAt = function (size) {
            el.style.fontSize = size + "px";
            var cs = root.getComputedStyle ? root.getComputedStyle(el) : null;
            return probe.offsetHeight + (cs ? px(cs.paddingTop) + px(cs.paddingBottom) : 1.2 * size);
        };
        var clamp = function (size) { return Math.max(MIN_FONT, Math.min(MAX_FONT, Math.floor(size * 10) / 10)); };
        var REF = 20;
        var needed = heightAt(REF);
        var size = needed > 0 ? REF * h / needed : REF;
        size = clamp(Math.min(size, w / (cfg.showCover ? MIN_EMS_WIDE + 3 : MIN_EMS_WIDE)));
        /* Wrapping chips and rounding don't scale exactly: shrink until it fits (a few steps at most). */
        for (var tries = 0; tries < 4 && size > MIN_FONT; tries++) {
            needed = heightAt(size);
            if (needed <= h + 0.5) { break; }
            size = clamp(Math.min(size * h / needed, size - 0.1));
        }
        host.removeChild(probe);
        el.style.fontSize = size + "px";
        return size;
    }

    root.SporeGamePanel = {
        css: CSS,
        ensureStyles: ensureStyles,
        config: config,
        applyColors: applyColors,
        pageHtml: pageHtml,
        weight: weight,
        fit: fit,
        ago: ago,
        esc: esc
    };
    ensureStyles(root.document);
})(typeof window !== "undefined" ? window : null);
