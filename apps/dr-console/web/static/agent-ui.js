/* Agent UI helpers: live activity trace of an agent run, readable evidence, markdown preview.
   Everything shown comes from the server; text is escaped before it is put into the page. */
(function () {
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const human = (k) => String(k).replace(/_/g, ' ').replace(/\b(rto|rpo|tm|sql|asr|id|url|fqdn|ms|db)\b/gi, (m) => m.toUpperCase())
        .replace(/^./, (c) => c.toUpperCase());

    // ------------------------------------------------------------- evidence / JSON as readable HTML
    function renderData(v, depth = 0) {
        if (v === null || v === undefined || v === '') return '<span class="kv-null">—</span>';
        if (typeof v === 'boolean') return `<span class="st ${v ? 'pass' : 'fail'}">${v ? 'Yes' : 'No'}</span>`;
        if (typeof v === 'number') return `<span class="kv-num">${esc(Number.isInteger(v) ? v : v.toFixed(3).replace(/0+$/, '').replace(/\.$/, ''))}</span>`;
        if (typeof v !== 'object') return `<span class="kv-str">${esc(v)}</span>`;
        if (depth > 3) return `<pre class="kv-pre">${esc(JSON.stringify(v, null, 2))}</pre>`;
        if (Array.isArray(v)) {
            if (!v.length) return '<span class="kv-null">none</span>';
            if (v.every((x) => x === null || typeof x !== 'object')) {
                return `<div class="chips">${v.map((x) => `<span class="chip">${esc(x)}</span>`).join('')}</div>`;
            }
            const keys = [...new Set(v.flatMap((x) => (x && typeof x === 'object' && !Array.isArray(x)) ? Object.keys(x) : []))];
            if (keys.length && keys.length <= 8) {
                return `<div class="scroll"><table class="kv-table"><thead><tr>${keys.map((k) => `<th>${esc(human(k))}</th>`).join('')}</tr></thead>
                    <tbody>${v.map((row) => `<tr>${keys.map((k) => `<td>${renderData(row?.[k], depth + 1)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
            }
            return v.map((x) => renderData(x, depth + 1)).join('');
        }
        const entries = Object.entries(v);
        if (!entries.length) return '<span class="kv-null">—</span>';
        return `<dl class="kv">${entries.map(([k, x]) => `<dt>${esc(human(k))}</dt><dd>${renderData(x, depth + 1)}</dd>`).join('')}</dl>`;
    }

    // ------------------------------------------------------------- live activity trace
    const ICONS = {
        tool: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
        input: '<path d="M4 7h16M4 12h16M4 17h10"/>',
        agent: '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
        check: '<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/><path d="M9 12l2 2 4-4"/>',
        fallback: '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 010 10h-3"/>',
        result: '<path d="M5 12l5 5L20 7"/>',
    };
    const KIND_LABEL = { input: 'Input', tool: 'Azure read', agent: 'AI agent', check: 'Code check', fallback: 'Fallback', result: 'Result' };

    function renderTrace(panel, tr, elapsed, done, savedAt) {
        const events = tr.events || [];
        panel.innerHTML = `
            <div class="trace-head">
                <span class="trace-live ${done ? 'done' : ''}"></span>
                <span class="trace-h">Agent activity</span>
                <span class="trace-sub">${savedAt ? `Last run ${new Date(savedAt).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })} · ` : ''}${done ? `Finished in ${(elapsed / 1000).toFixed(1)}s` : `Working · ${(elapsed / 1000).toFixed(1)}s`} · ${events.length} step${events.length === 1 ? '' : 's'}</span>
                <span class="trace-note">${savedAt ? 'Recorded' : 'Live'}: real Azure calls and agent steps</span>
                ${done ? `<button class="link-btn trace-toggle" type="button">${panel.classList.contains('collapsed') ? 'Show steps' : 'Hide steps'}</button>` : ''}
            </div>
            <ol class="trace-list">${events.map((e, i) => `
                <li class="trace-item ${esc(e.status)}">
                    <span class="trace-ic" title="${esc(KIND_LABEL[e.kind] || e.kind)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${ICONS[e.kind] || ICONS.tool}</svg></span>
                    <div class="trace-main">
                        <div class="trace-title"><span class="trace-kind">${esc(KIND_LABEL[e.kind] || e.kind)}</span>${esc(e.title)}</div>
                        ${e.detail ? `<div class="trace-detail">${esc(e.detail)}</div>` : ''}
                        ${e.data != null ? `<details class="trace-data" data-i="${i}"><summary>Details</summary><div class="trace-data-body">${renderData(e.data)}</div></details>` : ''}
                    </div>
                    <span class="trace-ms">${e.status === 'running' ? '<span class="spin"></span>' : (e.ms != null ? (e.ms / 1000).toFixed(2) + 's' : '+' + (e.t_ms / 1000).toFixed(1) + 's')}</span>
                </li>`).join('')}
                ${!done && !events.length ? '<li class="trace-item running"><span class="trace-ic"><span class="spin"></span></span><div class="trace-main"><div class="trace-title">Starting…</div></div></li>' : ''}
            </ol>
            <button class="trace-jump hide" type="button">New steps below ↓</button>`;
    }

    /** POST to an agent endpoint and show its live activity in `panel` while it runs. Returns the JSON reply. */
    async function runWithTrace(url, panel, options = {}) {
        const id = (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
        const started = performance.now();
        panel.classList.remove('hide', 'collapsed');
        let last = { events: [] };
        let finished = false;
        const open = new Set();
        const keepOpen = () => panel.querySelectorAll('details.trace-data[open]').forEach((d) => open.add(d.dataset.i));
        // The list is a fixed-height window: it follows the newest step unless the reader has scrolled up to
        // read older ones, in which case their position is kept and a "new steps" button appears.
        let follow = true;
        let seen = 0;
        const draw = () => {
            keepOpen();
            const prev = panel.querySelector('.trace-list');
            const prevTop = prev ? prev.scrollTop : 0;
            renderTrace(panel, last, performance.now() - started, finished);
            panel.querySelectorAll('details.trace-data').forEach((d) => { if (open.has(d.dataset.i)) d.open = true; });
            const list = panel.querySelector('.trace-list');
            const jump = panel.querySelector('.trace-jump');
            const count = (last.events || []).length;
            if (follow) { list.scrollTop = list.scrollHeight; seen = count; } else { list.scrollTop = prevTop; }
            jump.classList.toggle('hide', follow || count <= seen);
            list.onscroll = () => {
                follow = list.scrollTop + list.clientHeight >= list.scrollHeight - 24;
                if (follow) { seen = count; jump.classList.add('hide'); }
            };
            jump.onclick = () => { follow = true; draw(); };
            const t = panel.querySelector('.trace-toggle');
            if (t) t.onclick = () => { panel.classList.toggle('collapsed'); draw(); };
        };
        const poll = async () => {
            try {
                const r = await fetch(`/api/trace/${id}`, { cache: 'no-store' });
                if (r.ok) last = await r.json();
            } catch (e) { /* keep polling */ }
        };
        draw();
        const timer = setInterval(async () => { await poll(); if (!finished) draw(); }, 500);
        try {
            const res = await fetch(url, { method: 'POST', ...options, headers: { ...(options.headers || {}), 'X-Trace-Id': id } });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
            return data;
        } finally {
            clearInterval(timer);
            await poll();
            finished = true;
            draw();
        }
    }

    /** Show the saved activity of the last run (restored after navigation), collapsed until opened. */
    function showSaved(panel, trace, savedAt) {
        if (!panel || !trace || !(trace.events || []).length) return;
        panel.classList.remove('hide');
        panel.classList.add('collapsed');
        const draw = () => {
            renderTrace(panel, trace, trace.elapsed_ms || 0, true, savedAt);
            panel.querySelector('.trace-jump')?.remove();
            const t = panel.querySelector('.trace-toggle');
            if (t) t.onclick = () => { panel.classList.toggle('collapsed'); draw(); };
        };
        draw();
    }

    /** Remember the approver's name in this browser so it is not retyped at every approval (convenience only). */
    function rememberName(input) {
        if (!input) return;
        try { if (!input.value) input.value = localStorage.getItem('dra-approver') || ''; } catch (e) { /* storage blocked */ }
        input.addEventListener('change', () => { try { localStorage.setItem('dra-approver', input.value.trim()); } catch (e) { /* ignore */ } });
    }

    // ------------------------------------------------------------- markdown preview
    function renderMarkdown(md) {
        if (window.marked && window.DOMPurify) {
            return DOMPurify.sanitize(marked.parse(String(md || ''), { gfm: true, breaks: false }));
        }
        return `<pre class="kv-pre">${esc(md)}</pre>`;
    }

    window.AgentUI = { esc, renderData, runWithTrace, renderMarkdown, showSaved, rememberName };
})();
