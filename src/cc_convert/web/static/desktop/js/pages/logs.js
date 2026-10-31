// 日志查看：实例选择、server/api 日志、级别过滤、行数、自动刷新、下载。

import { api } from '../api.js';
import { esc } from '../helpers.js';

export async function render(view, params, ctx) {
    const { toast, cleanups } = ctx;
    const state = {
        instances: [], selectedInstance: '', logType: 'server', logLevel: 'all',
        lines: '100', rawLogs: [], loading: false, autoRefresh: false,
    };

    view.innerHTML = `
        <div class="ui-card">
            <div class="ui-row" style="flex-wrap:wrap; gap:10px">
                <select class="ui-select" id="selInstance">
                    <option value="">选择实例…</option>
                </select>
                <select class="ui-select" id="selType">
                    <option value="server">服务器日志</option>
                    <option value="api">API 日志</option>
                </select>
                <select class="ui-select" id="selLevel">
                    <option value="all">全部</option>
                    <option value="DEBUG">DEBUG</option>
                    <option value="INFO">INFO</option>
                    <option value="WARNING">WARNING</option>
                    <option value="ERROR">ERROR</option>
                </select>
                <select class="ui-select" id="selLines">
                    <option value="50">50</option><option value="100" selected>100</option>
                    <option value="200">200</option><option value="500">500</option>
                </select>
                <label class="ui-cluster desktop-row" style="font-size:12px"><input type="checkbox" id="autoRefresh"> 自动刷新</label>
                <div class="ui-cluster" style="margin-left:auto">
                    <button type="button" class="ui-button" id="btnDownload" disabled>下载</button>
                    <button type="button" class="ui-button ui-button--primary" id="btnRefresh" disabled>刷新</button>
                </div>
            </div>
            <p class="desktop-mono desktop-muted" id="logTitle" style="margin:8px 0 0">选择实例</p>
        </div>

        <div class="ui-card" id="logCard" hidden>
            <div class="desktop-log" id="logBox"></div>
        </div>
        <div class="ui-empty" id="emptyHint"><p><strong>未选择实例</strong></p><p class="desktop-muted">从上方选择要查看日志的实例</p></div>
    `;

    const $ = (sel) => view.querySelector(sel);

    function filteredLogs() {
        if (state.logLevel === 'all') return state.rawLogs;
        return state.rawLogs.filter((line) => line.includes(` - ${state.logLevel} `));
    }

    function renderLogs() {
        const lines = filteredLogs();
        // Level keywords get colored inside the pre block.
        const html = lines.map((line) => {
            const safe = esc(line);
            if (line.includes(' - ERROR ')) return `<span class="log-ERROR">${safe}</span>`;
            if (line.includes(' - WARNING ')) return `<span class="log-WARNING">${safe}</span>`;
            if (line.includes(' - INFO ')) return `<span class="log-INFO">${safe}</span>`;
            if (line.includes(' - DEBUG ')) return `<span class="log-DEBUG">${safe}</span>`;
            return safe;
        }).join('\n');
        $('#logBox').innerHTML = html || '<span style="opacity:.5">（无日志）</span>';
        $('#logBox').scrollTop = $('#logBox').scrollHeight;
    }

    async function fetchLogs() {
        if (!state.selectedInstance) return;
        state.loading = true;
        try {
            let data;
            data = await api.get(`/api/instances/${state.selectedInstance}/logs?type=${state.logType}&lines=${state.lines}`);
            if (cleanups.dead) return;
            const text = typeof data === 'string' ? data : (data.logs ?? data.content ?? '');
            state.rawLogs = String(text).split('\n').filter((l) => l.length > 0);
            renderLogs();
        } catch (e) {
            toast('加载日志失败: ' + e.message, 'danger');
            state.rawLogs = [];
            renderLogs();
        } finally {
            state.loading = false;
        }
    }

    async function fetchInstances() {
        try {
            state.instances = await api.get('/api/instances');
            const sel = $('#selInstance');
            state.instances.forEach((inst) => {
                const opt = document.createElement('option');
                opt.value = inst.id;
                opt.textContent = `${inst.name} (${inst.port})`;
                sel.appendChild(opt);
            });
        } catch (e) {
            console.error('Failed to fetch instances:', e);
        }
    }

    function updateTitle() {
        const inst = state.instances.find((i) => i.id === state.selectedInstance);
        const label = inst ? inst.name : '';
        $('#logTitle').textContent = label
            ? `${label} / ${state.logType}.log（级别过滤：${state.logLevel}）`
            : '选择实例';
    }

    function updateEnabled() {
        const on = !!state.selectedInstance;
        $('#btnRefresh').disabled = !on;
        $('#btnDownload').disabled = !on;
        $('#logCard').hidden = !on;
        $('#emptyHint').hidden = on;
    }

    cleanups.listen($('#selInstance'), 'change', () => {
        state.selectedInstance = $('#selInstance').value;
        updateEnabled(); updateTitle(); fetchLogs();
    });
    cleanups.listen($('#selType'), 'change', () => { state.logType = $('#selType').value; updateTitle(); fetchLogs(); });
    cleanups.listen($('#selLevel'), 'change', () => { state.logLevel = $('#selLevel').value; renderLogs(); });
    cleanups.listen($('#selLines'), 'change', () => { state.lines = $('#selLines').value; fetchLogs(); });
    cleanups.listen($('#btnRefresh'), 'click', fetchLogs);
    cleanups.listen($('#autoRefresh'), 'change', () => {
        state.autoRefresh = $('#autoRefresh').checked;
        if (state.autoRefresh) {
            const t = setInterval(() => { if (state.selectedInstance) fetchLogs(); }, 3000);
            cleanups.add(() => clearInterval(t));
        }
    });
    cleanups.listen($('#btnDownload'), 'click', () => {
        const blob = new Blob([filteredLogs().join('\n')], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const inst = state.instances.find((i) => i.id === state.selectedInstance);
        a.download = `${inst ? inst.name : 'instance'}-${state.logType}.log`;
        a.click();
        URL.revokeObjectURL(url);
    });

    await fetchInstances();
    updateEnabled();
}
