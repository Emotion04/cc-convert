// 用量统计：时间范围 + 粒度 + 分组，KPI、堆叠趋势、构成占比、分组条形、明细表。

import { api } from '../api.js';
import { esc } from '../helpers.js';

const COUNTERS = ['input_tokens', 'output_tokens', 'cache_read', 'cache_write'];

export async function render(view, params, ctx) {
    const { toast, cleanups } = ctx;
    const state = {
        presetValue: '7d', from: '', to: '', granularity: 'day', groupBy: 'instance',
        barMetric: 'output_tokens', loading: false, totals: {}, series: [], groups: [],
    };
    const charts = {};

    view.innerHTML = `
        <div class="ui-card">
            <div class="ui-cluster" style="gap:10px">
                <div class="ui-segmented" id="presets" role="group" aria-label="时间范围">
                    ${[['today', '今天'], ['7d', '近 7 天'], ['30d', '近 30 天'], ['90d', '近 90 天'], ['180d', '近 180 天']]
                        .map(([v, l]) => `<button type="button" class="ui-segmented__item" data-value="${v}" aria-pressed="${v === '7d'}">${l}</button>`).join('')}
                </div>
                <div class="ui-cluster">
                    <input type="date" class="ui-input" id="dateFrom">
                    <span class="desktop-muted">至</span>
                    <input type="date" class="ui-input" id="dateTo">
                </div>
                <div class="ui-cluster">
                    <span class="desktop-muted">粒度</span>
                    <select class="ui-select" id="granularity"><option value="day">按天</option><option value="hour">按小时</option></select>
                </div>
                <div class="ui-cluster">
                    <span class="desktop-muted">分组</span>
                    <select class="ui-select" id="groupBy">
                        <option value="">不分组</option>
                        <option value="instance">按实例</option>
                        <option value="model">按模型</option>
                        <option value="client_model">按请求模型</option>
                        <option value="endpoint">按入口</option>
                    </select>
                </div>
                <span class="desktop-muted" id="updatedAt" style="margin-left:auto"></span>
            </div>
        </div>

        <div class="desktop-kpis" id="kpis"></div>

        <div class="ui-alert ui-alert--warning" id="unreportedBox" hidden>
            有 <strong id="unreportedN"></strong> 次请求未上报用量（常见于流式请求或中转不支持 include_usage）。这些请求没有被记入下面的 token 数字，避免低估。
        </div>

        <div class="desktop-inst-grid" style="grid-template-columns: 2fr 1fr">
            <div class="ui-card"><strong style="font-size:13px">用量趋势（堆叠）</strong><div class="desktop-chart-box"><canvas id="trendChart"></canvas></div></div>
            <div class="ui-card"><strong style="font-size:13px">构成占比</strong><div class="desktop-chart-box"><canvas id="shareChart"></canvas></div></div>
        </div>

        <div class="ui-card">
            <div class="desktop-split">
                <strong style="font-size:13px">分组对比</strong>
                <div class="ui-segmented" id="barMetrics" role="group" aria-label="指标">
                    ${[['input_tokens', '输入'], ['output_tokens', '输出'], ['cache_read', '缓存读'], ['requests', '请求']]
                        .map(([v, l]) => `<button type="button" class="ui-segmented__item" data-value="${v}" aria-pressed="${v === 'output_tokens'}">${l}</button>`).join('')}
                </div>
            </div>
            <div class="desktop-chart-box"><canvas id="groupChart"></canvas></div>
            <p class="desktop-muted" id="groupEmpty" hidden style="text-align:center; padding:24px">选择上方「分组」后这里显示各实例 / 模型的对比</p>
        </div>

        <div class="ui-card" id="tableCard" hidden>
            <strong style="font-size:13px">明细</strong>
            <div class="desktop-table-wrap">
                <table class="desktop-table">
                    <thead><tr><th>分组</th><th>请求</th><th>输入</th><th>输出</th><th>缓存读</th><th>缓存写</th><th>缓存率</th><th>未上报</th><th>错误</th><th>平均耗时</th></tr></thead>
                    <tbody id="tableBody"></tbody>
                </table>
            </div>
        </div>
    `;

    const $ = (sel) => view.querySelector(sel);

    function setPresetDates(value) {
        const fmt = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
        const now = new Date();
        if (value === 'today') {
            state.from = state.to = fmt(now);
        } else {
            const days = parseInt(value);
            state.from = fmt(new Date(now.getTime() - (days - 1) * 86400000));
            state.to = fmt(now);
        }
        $('#dateFrom').value = state.from;
        $('#dateTo').value = state.to;
    }

    function fmtNum(n) {
        if (n == null) return '—';
        if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + ' 亿';
        if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + ' 万';
        return String(n);
    }

    function renderKpis() {
        const t = state.totals;
        const kpis = [
            ['请求数', fmtNum(t.requests ?? 0), '成功 + 失败'],
            ['输入', fmtNum(t.input_tokens ?? 0), '未命中缓存部分'],
            ['输出', fmtNum(t.output_tokens ?? 0), '含推理输出'],
            ['缓存读取', fmtNum(t.cache_read ?? 0), '命中前缀缓存'],
            ['缓存写入', fmtNum((t.cache_write ?? 0) + (t.cache_write_1h ?? 0)), '含估算标记项'],
            ['缓存率', t.cache_rate == null ? '—' : (t.cache_rate * 100).toFixed(1) + '%', '缓存读 / 全部输入'],
            ['未上报', fmtNum(t.unreported ?? 0), '上游未返回 usage'],
        ];
        $('#kpis').innerHTML = kpis.map(([label, value, hint]) => `
            <div class="ui-stat">
                <span class="ui-stat__label">${label}</span>
                <span class="ui-stat__value">${value}</span>
                <span class="ui-stat__delta">${hint}</span>
            </div>`).join('');
        $('#unreportedBox').hidden = !(t.unreported > 0);
        $('#unreportedN').textContent = t.unreported ?? 0;
    }

    function renderCharts() {
        const destroy = (key) => { if (charts[key]) { charts[key].destroy(); delete charts[key]; } };
        destroy('trend'); destroy('share'); destroy('group');
        if (!window.Chart) return;

        const labels = state.series.map((s) => s.label);
        const mk = (field, label, color) => ({
            label, data: state.series.map((s) => s[field]),
            backgroundColor: color, borderWidth: 0, stack: 'tokens',
        });
        if (state.series.length) {
            charts.trend = new Chart($('#trendChart'), {
                type: 'line',
                data: { labels, datasets: [
                    mk('input_tokens', '输入', 'rgba(249,115,22,.55)'),
                    mk('output_tokens', '输出', 'rgba(20,184,166,.55)'),
                    mk('cache_read', '缓存读', 'rgba(99,102,241,.5)'),
                    mk('cache_write', '缓存写', 'rgba(234,179,8,.45)'),
                ] },
                options: chartOpts(true),
            });
        }
        const t = state.totals;
        const values = [t.input_tokens ?? 0, t.output_tokens ?? 0, t.cache_read ?? 0, (t.cache_write ?? 0) + (t.cache_write_1h ?? 0)];
        if (values.some((v) => v > 0)) {
            charts.share = new Chart($('#shareChart'), {
                type: 'doughnut',
                data: { labels: ['输入', '输出', '缓存读', '缓存写'], datasets: [{ data: values, backgroundColor: ['#F97316', '#14B8A6', '#6366F1', '#EAB308'], borderWidth: 0 }] },
                options: { maintainAspectRatio: false, cutout: '62%', plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 11 } } } } },
            });
        }
        if (state.groups.length) {
            const metricLabel = { input_tokens: '输入', output_tokens: '输出', cache_read: '缓存读', requests: '请求' }[state.barMetric] || state.barMetric;
            charts.group = new Chart($('#groupChart'), {
                type: 'bar',
                data: { labels: state.groups.map((g) => g.key), datasets: [{ label: metricLabel, data: state.groups.map((g) => g[state.barMetric] || 0), backgroundColor: 'rgba(249,115,22,.7)', borderRadius: 6 }] },
                options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { font: { size: 10 } } }, y: { beginAtZero: true, ticks: { font: { size: 10 } } } } },
            });
        }
        $('#groupEmpty').hidden = state.groups.length > 0;
    }

    function chartOpts(stacked) {
        return {
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 11 } } } },
            scales: {
                x: { stacked, grid: { display: false }, ticks: { font: { size: 10 } } },
                y: { stacked, beginAtZero: true, ticks: { font: { size: 10 } } },
            },
        };
    }

    function renderTable() {
        $('#tableCard').hidden = state.groups.length === 0;
        $('#tableBody').innerHTML = state.groups.map((g) => `
            <tr>
                <td><strong>${esc(g.key)}</strong></td>
                <td>${fmtNum(g.requests)}</td>
                <td>${fmtNum(g.input_tokens)}</td>
                <td>${fmtNum(g.output_tokens)}</td>
                <td>${fmtNum(g.cache_read)}</td>
                <td>${fmtNum((g.cache_write ?? 0) + (g.cache_write_1h ?? 0))}</td>
                <td>${g.cache_rate == null ? '—' : (g.cache_rate * 100).toFixed(1) + '%'}</td>
                <td>${g.unreported || '—'}</td>
                <td>${g.errors || '—'}</td>
                <td>${g.requests ? Math.round(g.duration_ms / g.requests) + ' ms' : '—'}</td>
            </tr>`).join('');
    }

    async function refresh() {
        state.loading = true;
        try {
            const params = new URLSearchParams({ from: state.from, to: state.to, granularity: state.granularity });
            if (state.groupBy) params.set('group_by', state.groupBy);
            const data = await api.get('/api/usage/summary?' + params.toString());
            if (cleanups.dead) return;
            state.totals = data.totals;
            state.series = data.series;
            state.groups = data.groups;
            $('#updatedAt').textContent = '更新于 ' + new Date().toLocaleTimeString();
            renderKpis();
            renderCharts();
            renderTable();
        } catch (e) {
            toast('加载用量数据失败: ' + e.message, 'danger');
        } finally {
            state.loading = false;
        }
    }

    // Wiring
    setPresetDates('7d');
    cleanups.listen($('#presets'), 'click', (e) => {
        const btn = e.target.closest('[data-value]');
        if (!btn) return;
        state.presetValue = btn.dataset.value;
        view.querySelectorAll('#presets [data-value]').forEach((b) => b.setAttribute('aria-pressed', b === btn));
        setPresetDates(state.presetValue);
        refresh();
    });
    cleanups.listen($('#barMetrics'), 'click', (e) => {
        const btn = e.target.closest('[data-value]');
        if (!btn) return;
        state.barMetric = btn.dataset.value;
        view.querySelectorAll('#barMetrics [data-value]').forEach((b) => b.setAttribute('aria-pressed', b === btn));
        renderCharts();
    });
    for (const [sel, prop] of [['#dateFrom', 'from'], ['#dateTo', 'to'], ['#granularity', 'granularity'], ['#groupBy', 'groupBy']]) {
        cleanups.listen($(sel), 'change', () => { state[prop] = $(sel).value; refresh(); });
    }

    await refresh();
}
