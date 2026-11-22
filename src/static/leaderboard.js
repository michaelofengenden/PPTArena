/* Leaderboard page: split switch, sorting, linked highlight, score bars, two scatter plots, heat table.
   All numbers come from the server-rendered ranking table, so the table is the single source. */
(() => {
    "use strict";

    const NS = "http://www.w3.org/2000/svg";
    const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
    const panels = all("[data-split-panel]");
    const tabs = all(".switch [data-split]");
    const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

    function svg(name, attrs, text) {
        const el = document.createElementNS(NS, name);
        for (const key in attrs) el.setAttribute(key, attrs[key]);
        if (text !== undefined) el.textContent = text;
        return el;
    }

    const one = (value) => value.toFixed(1);

    // ---------- Split switch ----------

    function showSplit(key) {
        tabs.forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.split === key)));
        panels.forEach((panel) => { panel.hidden = panel.dataset.splitPanel !== key; });
        drawCharts();
    }

    tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => showSplit(tab.dataset.split));
        tab.addEventListener("keydown", (event) => {
            if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
            const next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
            next.focus();
            showSplit(next.dataset.split);
        });
    });

    // ---------- Sorting: best first, systems without a value last ----------

    panels.forEach((panel) => {
        const body = panel.querySelector(".rank tbody");
        const buttons = all(".rank thead [data-sort]", panel);
        buttons.forEach((button) => button.addEventListener("click", () => {
            const key = button.dataset.sort;
            const lowerIsBetter = key === "time";
            const value = (row) => (row.dataset[key] === "" ? null : parseFloat(row.dataset[key]));
            all("tr", body)
                .sort((a, b) => {
                    const x = value(a), y = value(b);
                    if (x === null || y === null) return (x === null) - (y === null);
                    return lowerIsBetter ? x - y : y - x;
                })
                .forEach((row) => body.appendChild(row));
            buttons.forEach((other) => other.setAttribute("aria-pressed", String(other === button)));
        }));
    });

    // ---------- Linked highlight ----------

    function pick(panel, id) {
        panel.classList.toggle("has-pick", Boolean(id));
        all("[data-id]", panel).forEach((el) => el.classList.toggle("is-pick", el.dataset.id === id));
    }

    panels.forEach((panel) => {
        all(".rank tbody, .heat tbody", panel).forEach((body) => {
            body.addEventListener("pointerover", (event) => {
                const row = event.target.closest("tr[data-id]");
                if (row) pick(panel, row.dataset.id);
            });
            body.addEventListener("pointerleave", () => pick(panel, null));
        });
    });

    // ---------- Score bars: point at or tab to a column for the full name and both parts of the score ----------

    panels.forEach((panel) => {
        const figure = panel.querySelector(".bars");
        if (!figure) return;
        const tip = document.createElement("div");
        tip.className = "tip";
        tip.hidden = true;
        figure.append(tip);

        function show(column) {
            const row = panel.querySelector(`.rank tr[data-id="${column.dataset.id}"]`);
            pick(panel, column.dataset.id);
            const name = document.createElement("b"), detail = document.createElement("span");
            name.textContent = row.dataset.name;
            detail.textContent = `Instruction ${one(+row.dataset.instruction)}, visual ${one(+row.dataset.visual)}`;
            tip.replaceChildren(name, detail);
            tip.hidden = false;
            const frame = figure.getBoundingClientRect(), box = column.querySelector("b").getBoundingClientRect();
            const middle = box.left + box.width / 2 - frame.left;
            tip.style.left = `${Math.min(Math.max(middle - tip.offsetWidth / 2, 0), frame.width - tip.offsetWidth)}px`;
            tip.style.top = `${box.top - frame.top - tip.offsetHeight - 6}px`;
        }

        function hide() {
            tip.hidden = true;
            pick(panel, null);
        }

        all("li", figure).forEach((column) => {
            column.addEventListener("pointerenter", () => show(column));
            column.addEventListener("focus", () => show(column));
            column.addEventListener("blur", hide);
        });
        figure.querySelector("ol").addEventListener("pointerleave", hide);
    });

    // ---------- Heat table: one quiet blue, darker means a higher score (lighter in dark mode) ----------

    const RAMP = ["#d9e5f9", "#c4d5ef", "#b0c4e6", "#9bb4dc", "#87a4d2", "#7494c7", "#6184bd",
        "#4d74b3", "#4064a1", "#34568e", "#28477b", "#1d3a69", "#122c58"]
        .map((hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)));

    function rampColor(share, dark) {
        const stops = dark ? RAMP.slice().reverse() : RAMP;
        const at = Math.min(1, Math.max(0, share)) * (stops.length - 1);
        const low = Math.floor(at), high = Math.min(low + 1, stops.length - 1);
        return stops[low].map((channel, i) => Math.round(channel + (stops[high][i] - channel) * (at - low)));
    }

    function luminance(rgb) {
        const [r, g, b] = rgb.map((c) => {
            const s = c / 255;
            return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }

    function paintHeat() {
        all(".heat td[data-v]").forEach((cell) => {
            const rgb = rampColor(parseFloat(cell.dataset.v) / 100, darkQuery.matches);
            const light = luminance(rgb);
            cell.style.background = `rgb(${rgb.join(" ")})`;
            cell.style.color = 1.05 / (light + 0.05) >= (light + 0.05) / 0.05 ? "#fff" : "#000";
        });
    }

    // ---------- Scatter plots ----------

    const CHARTS = {
        quality: {
            x: (d) => d.instruction, y: (d) => d.visual,
            xTitle: "Instruction following", yTitle: "Visual quality", equalLine: true,
            tip: (d) => `Instruction ${one(d.instruction)}, visual ${one(d.visual)}`,
        },
        speed: {
            x: (d) => d.time, y: (d) => d.score, log: true,
            xTitle: "Minutes per edit", yTitle: "Score",
            tip: (d) => `Score ${one(d.score)}, ${one(d.time)} min per edit`,
        },
    };

    function readRows(panel) {
        return all(".rank tbody tr", panel).map((row) => ({
            id: row.dataset.id,
            name: row.dataset.name,
            short: row.dataset.short,
            family: row.dataset.family,
            score: parseFloat(row.dataset.score),
            instruction: parseFloat(row.dataset.instruction),
            visual: parseFloat(row.dataset.visual),
            time: row.dataset.time === "" ? null : parseFloat(row.dataset.time),
        })).sort((a, b) => b.score - a.score);
    }

    function linearScale(values, pixels) {
        const step = Math.max(...values) - Math.min(...values) > 45 ? 20 : 10;
        let low = Math.floor(Math.min(...values) / step) * step;
        let high = Math.ceil(Math.max(...values) / step) * step;
        if (Math.min(...values) - low < step * 0.15) low -= step;
        if (high - Math.max(...values) < step * 0.3) high += step;
        low = Math.max(0, low);
        high = Math.min(100, high);
        const ticks = [];
        for (let t = low; t <= high; t += step) ticks.push(t);
        return { ticks, label: String, at: (v) => pixels[0] + ((v - low) / (high - low)) * (pixels[1] - pixels[0]), low, high };
    }

    function logScale(values, pixels) {
        const low = Math.min(...values) / 1.35, high = Math.max(...values) * 1.35;
        const ticks = [0.5, 1, 2, 3, 5, 10, 15, 20, 30, 60].filter((t) => t >= low && t <= high);
        const span = Math.log(high) - Math.log(low);
        return { ticks, label: String, at: (v) => pixels[0] + ((Math.log(v) - Math.log(low)) / span) * (pixels[1] - pixels[0]), low, high };
    }

    const ruler = document.createElement("canvas").getContext("2d");

    function textWidth(text, font) {
        ruler.font = font;
        return ruler.measureText(text).width;
    }

    const overlaps = (a, b) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;

    /* Put each name next to its dot where it collides with nothing. A name that fits nowhere is
       left out: the ranking above and the hover readout still carry it. */
    function placeInOrder(order, points, frame, font, obstacles, placed = new Map()) {
        const taken = points.map((p) => ({ x0: p.px - 7, x1: p.px + 7, y0: p.py - 7, y1: p.py + 7 })).concat(obstacles);
        const height = 15;
        const labels = new Map(placed);
        placed.forEach((spot) => taken.push(...spot.boxes));
        order.forEach((p) => {
            if (labels.has(p)) return;
            const width = textWidth(p.short, font);
            const spots = [
                { x: p.px + 10, y: p.py, anchor: "start" },
                { x: p.px - 10, y: p.py, anchor: "end" },
                { x: p.px, y: p.py - 14, anchor: "middle" },
                { x: p.px, y: p.py + 15, anchor: "middle" },
                { x: p.px + 8, y: p.py - 12, anchor: "start" },
                { x: p.px + 8, y: p.py + 13, anchor: "start" },
                { x: p.px - 8, y: p.py - 12, anchor: "end" },
                { x: p.px - 8, y: p.py + 13, anchor: "end" },
            ];
            // Second ring: further out, joined to the dot by a hairline, for dots in a cluster.
            [[1, -1], [1, 1], [-1, -1], [-1, 1], [0, -1], [0, 1], [1.4, 0], [-1.4, 0]].forEach(([dx, dy]) => {
                [26, 40].forEach((reach) => spots.push({
                    x: p.px + dx * reach * 0.8, y: p.py + dy * reach,
                    anchor: dx > 0 ? "start" : dx < 0 ? "end" : "middle", leader: true,
                }));
            });
            for (const spot of spots) {
                const x0 = spot.anchor === "start" ? spot.x : spot.anchor === "end" ? spot.x - width : spot.x - width / 2;
                const box = { x0: x0 - 2, x1: x0 + width + 2, y0: spot.y - height / 2, y1: spot.y + height / 2 };
                if (box.x0 < frame.x0 || box.x1 > frame.x1 || box.y0 < frame.y0 || box.y1 > frame.y1) continue;
                if (taken.some((other) => overlaps(box, other))) continue;
                // A leader may not run through another dot or name, and later names keep off it.
                const trail = [];
                if (spot.leader) {
                    for (let step = 2; step <= 9; step++) {
                        const x = p.px + (spot.x - p.px) * (step / 10), y = p.py + (spot.y - p.py) * (step / 10);
                        trail.push({ x0: x - 2, x1: x + 2, y0: y - 2, y1: y + 2 });
                    }
                    const own = taken[points.indexOf(p)];
                    if (trail.some((bit) => taken.some((other) => other !== own && overlaps(bit, other)))) continue;
                }
                taken.push(box, ...trail);
                spot.boxes = [box, ...trail];
                labels.set(p, spot);
                break;
            }
        });
        return labels;
    }

    /* The order names are placed in decides who gets squeezed out, so try a few orders and keep
       the one that names the most dots with the fewest leader lines. */
    function placeLabels(points, frame, font, obstacles) {
        const crowd = (p) => points.filter((q) => Math.hypot(p.px - q.px, p.py - q.py) < 70).length;
        const orders = [
            points,
            points.slice().sort((a, b) => crowd(b) - crowd(a)),
            points.slice().sort((a, b) => a.px - b.px),
            points.slice().sort((a, b) => b.px - a.px),
            points.slice().reverse(),
        ];
        const quality = (labels) => labels.size * 100 - Array.from(labels.values()).filter((spot) => spot.leader).length;
        let best = null;
        orders.forEach((order) => {
            const labels = placeInOrder(order, points, frame, font, obstacles);
            if (!best || quality(labels) > quality(best)) best = labels;
        });
        // Last resort for a dot still without a name: let the name cross the guide line.
        if (best.size < points.length) best = placeInOrder(points, points, frame, font, [], best);
        points.forEach((p) => { p.label = best.get(p); });
    }

    function drawChart(panel, figure) {
        const spec = CHARTS[figure.dataset.chart];
        const plot = figure.querySelector(".plot");
        const width = plot.clientWidth, height = plot.clientHeight;
        if (!width || !height) return;

        const rows = readRows(panel).filter((d) => spec.x(d) !== null && spec.y(d) !== null);
        plot.replaceChildren();
        if (!rows.length) return;

        const frame = { x0: 34, x1: width - 6, y0: 26, y1: height - 42 };
        const xs = (spec.log ? logScale : linearScale)(rows.map(spec.x), [frame.x0, frame.x1]);
        const ys = linearScale(rows.map(spec.y), [frame.y1, frame.y0]);
        const font = `12.5px ${getComputedStyle(document.body).fontFamily}`;
        const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "group", "aria-label": figure.querySelector("h3").textContent });

        ys.ticks.forEach((tick) => {
            const y = Math.round(ys.at(tick)) + 0.5;
            root.append(svg("line", { class: tick === ys.low ? "axis" : "grid", x1: frame.x0, x2: frame.x1, y1: y, y2: y }));
            root.append(svg("text", { x: frame.x0 - 8, y: y + 4, "text-anchor": "end" }, ys.label(tick)));
        });
        xs.ticks.forEach((tick) => {
            const x = Math.round(xs.at(tick)) + 0.5;
            root.append(svg("line", { class: "grid", x1: x, x2: x, y1: frame.y0, y2: frame.y1 }));
            root.append(svg("text", { x, y: frame.y1 + 18, "text-anchor": "middle" }, xs.label(tick)));
        });
        root.append(svg("text", { class: "axis-title", x: 0, y: 11 }, spec.yTitle));
        root.append(svg("text", { class: "axis-title", x: frame.x1, y: height - 4, "text-anchor": "end" }, spec.xTitle));

        const obstacles = [];      // things a system's name must not sit on
        if (spec.equalLine) {
            const low = Math.max(xs.low, ys.low), high = Math.min(xs.high, ys.high);
            if (high > low) {
                const x1 = xs.at(low), y1 = ys.at(low), x2 = xs.at(high), y2 = ys.at(high);
                root.append(svg("line", { class: "guide", x1, y1, x2, y2 }));
                root.append(svg("text", { x: x2 + 6, y: y2 + 14 }, "equal"));
                obstacles.push({ x0: x2 + 4, x1: x2 + 10 + textWidth("equal", font), y0: y2 + 2, y1: y2 + 20 });
                const steps = Math.ceil(Math.hypot(x2 - x1, y2 - y1) / 10);
                for (let i = 0; i <= steps; i++) {
                    const x = x1 + (x2 - x1) * (i / steps), y = y1 + (y2 - y1) * (i / steps);
                    obstacles.push({ x0: x - 3, x1: x + 3, y0: y - 3, y1: y + 3 });
                }
            }
        }

        const points = rows.map((d) => ({ ...d, px: xs.at(spec.x(d)), py: ys.at(spec.y(d)) }));
        placeLabels(points, { x0: frame.x0, x1: width, y0: 14, y1: frame.y1 + 2 }, font, obstacles);

        const tip = document.createElement("div");
        tip.className = "tip";
        tip.hidden = true;

        function show(point) {
            pick(panel, point.id);
            const name = document.createElement("b"), detail = document.createElement("span");
            name.textContent = point.name;
            detail.textContent = spec.tip(point);
            tip.replaceChildren(name, detail);
            tip.hidden = false;
            const left = Math.min(Math.max(point.px - tip.offsetWidth / 2, 0), width - tip.offsetWidth);
            const above = point.py - tip.offsetHeight - 12;
            tip.style.left = `${left}px`;
            tip.style.top = `${above < 0 ? point.py + 14 : above}px`;
        }

        function hide() {
            tip.hidden = true;
            pick(panel, null);
        }

        points.forEach((point) => {
            const group = svg("g", { "data-id": point.id, "data-family": point.family, tabindex: 0, role: "img", "aria-label": `${point.name}: ${spec.tip(point)}` });
            if (point.label && point.label.leader) {
                const toward = point.label.anchor === "start" ? -3 : point.label.anchor === "end" ? 3 : 0;
                const edge = point.label.y > point.py ? -7 : point.label.y < point.py ? 7 : 0;
                group.append(svg("line", { class: "leader", x1: point.px, y1: point.py, x2: point.label.x + toward, y2: point.label.y + edge }));
            }
            if (point.label) {
                group.append(svg("text", { class: "label", x: point.label.x, y: point.label.y + 4, "text-anchor": point.label.anchor }, point.short));
            }
            group.append(svg("circle", { class: "dot", cx: point.px, cy: point.py, r: 5 }));
            group.addEventListener("focus", () => show(point));
            group.addEventListener("blur", hide);
            root.append(group);
        });

        // The pointer only has to be nearest to a dot, not on it.
        plot.addEventListener("pointermove", (event) => {
            const box = plot.getBoundingClientRect();
            const x = event.clientX - box.left, y = event.clientY - box.top;
            let best = null, bestDistance = 40;
            points.forEach((point) => {
                const distance = Math.hypot(point.px - x, point.py - y);
                if (distance < bestDistance) { best = point; bestDistance = distance; }
            });
            if (best) show(best); else hide();
        });
        plot.addEventListener("pointerleave", hide);

        plot.append(root, tip);
    }

    // ---------- Case strip: one row per system, one column per case ----------

    function drawStrip(panel, figure) {
        const holder = figure.querySelector(".strip");
        const data = JSON.parse(figure.querySelector("script").textContent);
        const width = holder.clientWidth;
        holder.replaceChildren();
        if (!width || !data.cases.length) return;

        const narrow = width < 560;
        const names = narrow ? 0 : Math.round(width * 0.3) + 2;       // lines up with the heat table above
        const rowHeight = narrow ? 14 : 22, rowGap = 2;
        const gap = data.cases.length > 40 ? 1 : 2;
        const cell = (width - names - gap * (data.cases.length - 1)) / data.cases.length;
        const height = data.rows.length * (rowHeight + rowGap) - rowGap;
        const dark = darkQuery.matches;
        const root = svg("svg", { viewBox: `0 0 ${width} ${height + 22}`, width, height: height + 22, role: "img",
            "aria-label": `Scores of ${data.rows.length} systems on each of ${data.cases.length} cases, cases ordered from easiest to hardest` });

        data.rows.forEach((row, r) => {
            const y = r * (rowHeight + rowGap);
            const group = svg("g", { "data-id": row.id });
            if (names) group.append(svg("text", { class: "strip-name", x: 0, y: y + rowHeight / 2 + 4.5 }, row.name));
            row.scores.forEach((score, c) => {
                const fill = score === null ? "var(--bg-subtle)" : `rgb(${rampColor(score / 100, dark).join(" ")})`;
                group.append(svg("rect", { x: names + c * (cell + gap), y, width: cell, height: rowHeight, rx: cell > 12 ? 2 : 0, fill }));
            });
            root.append(group);
        });
        root.append(svg("text", { x: names, y: height + 17 }, "Easiest case"));
        root.append(svg("text", { x: width, y: height + 17, "text-anchor": "end" }, "Hardest case"));
        const mark = svg("rect", { class: "strip-mark", width: cell + 2, height: rowHeight + 2, visibility: "hidden" });
        root.append(mark);

        const tip = document.createElement("div");
        tip.className = "tip";
        tip.hidden = true;

        function hide() {
            tip.hidden = true;
            mark.setAttribute("visibility", "hidden");
            pick(panel, null);
        }

        holder.addEventListener("pointermove", (event) => {
            const box = holder.getBoundingClientRect();
            const c = Math.floor((event.clientX - box.left - names) / (cell + gap));
            const r = Math.floor((event.clientY - box.top) / (rowHeight + rowGap));
            if (c < 0 || c >= data.cases.length || r < 0 || r >= data.rows.length) { hide(); return; }
            const row = data.rows[r], score = row.scores[c];
            pick(panel, row.id);
            mark.setAttribute("x", names + c * (cell + gap) - 1);
            mark.setAttribute("y", r * (rowHeight + rowGap) - 1);
            mark.setAttribute("visibility", "visible");
            const title = document.createElement("b"), detail = document.createElement("span");
            title.textContent = data.cases[c];
            detail.textContent = `${row.name}: ${score === null ? "no scored deck" : Math.round(score)}`;
            tip.replaceChildren(title, detail);
            tip.hidden = false;
            const x = names + c * (cell + gap) + cell / 2;
            tip.style.left = `${Math.min(Math.max(x - tip.offsetWidth / 2, 0), width - tip.offsetWidth)}px`;
            const above = r * (rowHeight + rowGap) - tip.offsetHeight - 8;
            tip.style.top = `${above < -60 ? (r + 1) * (rowHeight + rowGap) + 6 : above}px`;
        });
        holder.addEventListener("pointerleave", hide);
        holder.append(root, tip);
    }

    function drawCharts() {
        panels.filter((panel) => !panel.hidden).forEach((panel) => {
            all("[data-strip]", panel).forEach((figure) => {
                const holder = figure.querySelector(".strip");
                holder.replaceWith(holder.cloneNode(false));
                drawStrip(panel, figure);
            });
            all("[data-chart]", panel).forEach((figure) => {
                // Rebuild the plot container so listeners from an earlier draw do not pile up.
                const plot = figure.querySelector(".plot");
                const fresh = plot.cloneNode(false);
                plot.replaceWith(fresh);
                drawChart(panel, figure);
            });
        });
    }

    // ---------- Citation ----------

    const copyButton = document.getElementById("copyCitation");
    if (copyButton) {
        copyButton.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(document.getElementById("citationText").textContent);
                copyButton.textContent = "Copied";
                setTimeout(() => { copyButton.textContent = "Copy"; }, 1600);
            } catch (_) { /* clipboard blocked: the text is selectable */ }
        });
    }

    // ---------- Start ----------

    paintHeat();
    drawCharts();
    darkQuery.addEventListener("change", () => { paintHeat(); drawCharts(); });
    let resizeTimer = 0;
    window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(drawCharts, 120);
    });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(drawCharts);
})();
