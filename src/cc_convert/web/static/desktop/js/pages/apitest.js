// API 测试：向实例端点发送请求（携带 local token），展示响应。

import { api } from '../api.js';
import { esc } from '../helpers.js';

const PRESETS = {
    health: { method: 'GET', url: '/health', body: '' },
    models: { method: 'GET', url: '/v1/models', body: '' },
    messages: {
        method: 'POST', url: '/v1/messages',
        body: JSON.stringify({ model: 'gpt-3.5-turbo', max_tokens: 1024, messages: [{ role: 'user', content: 'Hello! How are you?' }] }, null, 2),
    },
    count: {
        method: 'POST', url: '/v1/messages/count_tokens',
        body: JSON.stringify({ model: 'gpt-3.5-turbo', messages: [{ role: 'user', content: 'Hello!' }] }, null, 2),
    },
    chat: {
        method: 'POST', url: '/v1/chat/completions',
        body: JSON.stringify({ model: 'gpt-3.5-turbo', max_tokens: 1024, messages: [{ role: 'user', content: 'Hello! How are you?' }] }, null, 2),
    },
};

export async function render(view, params, ctx) {
    const { toast, cleanups } = ctx;
    const state = { instances: [], selected: '', history: [], response: null, sending: false };
    const request = { method: 'GET', url: '/health', body: '' };

    view.innerHTML = `
        <div class="desktop-inst-grid" style="grid-template-columns: 1fr 1fr; align-items:start">
            <div class="ui-card desktop-form" style="max-width:none">
                <div>
                    <label class="desktop-label">实例</label>
                    <select class="ui-select" id="selInstance" style="width:100%"><option value="">选择实例…</option></select>
                    <p class="desktop-hint" id="baseUrlHint">选择实例后自动填充其地址</p>
                </div>
                <div class="desktop-row">
                    <select class="ui-select" id="reqMethod" style="width:100px">
                        ${['GET', 'POST', 'PUT', 'DELETE'].map((m) => `<option>${m}</option>`).join('')}
                    </select>
                    <input type="text" class="ui-input desktop-mono" id="reqUrl" value="/health" style="flex:1">
                    <button type="button" class="ui-button ui-button--primary" id="btnSend">发送</button>
                </div>
                <div>
                    <label class="desktop-label">预设请求</label>
                    <div class="ui-cluster" style="flex-wrap:wrap">
                        ${Object.entries({ health: '健康检查', models: '模型列表', messages: 'Messages', count: 'count_tokens', chat: 'Chat Completions' })
                            .map(([k, l]) => `<button type="button" class="ui-button" data-preset="${k}">${l}</button>`).join('')}
                    </div>
                </div>
                <div id="bodySection">
                    <label class="desktop-label">请求体（JSON）</label>
                    <textarea class="ui-textarea desktop-mono" id="reqBody" rows="8"></textarea>
                </div>
            </div>

            <div class="ui-card">
                <div class="ui-row" style="justify-content:space-between">
                    <strong style="font-size:13px">响应</strong>
                    <span class="ui-badge" id="respStatus" hidden></span>
                </div>
                <pre class="desktop-cmd" id="respBody" style="max-height:380px">尚未发送请求</pre>
                <div class="desktop-muted" id="respMeta"></div>
            </div>
        </div>

        <div class="ui-card" id="historyCard" hidden>
            <strong style="font-size:13px">历史</strong>
            <div class="desktop-table-wrap"><table class="desktop-table">
                <thead><tr><th>时间</th><th>方法</th><th>URL</th><th>状态</th><th></th></tr></thead>
                <tbody id="historyBody"></tbody>
            </table></div>
        </div>
    `;

    const $ = (sel) => view.querySelector(sel);
    const cleanupsLocal = cleanups;

    function baseUrl() {
        const inst = state.instances.find((i) => i.id === state.selected);
        return inst ? `http://127.0.0.1:${inst.port}` : '';
    }

    function syncBodyVisibility() {
        $('#bodySection').hidden = !($('#reqMethod').value === 'POST' || $('#reqMethod').value === 'PUT');
    }

    function formatJson(text) {
        try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text; }
    }

    function renderHistory() {
        $('#historyCard').hidden = state.history.length === 0;
        $('#historyBody').innerHTML = state.history.map((item, idx) => `
            <tr>
                <td class="desktop-muted">${esc(item.time)}</td>
                <td class="desktop-mono">${esc(item.method)}</td>
                <td class="desktop-mono" style="text-align:left; max-width:320px; overflow:hidden; text-overflow:ellipsis">${esc(item.url)}</td>
                <td class="desktop-mono">${item.status ?? '—'}</td>
                <td><button type="button" class="ui-button" data-restore="${idx}">回填</button></td>
            </tr>`).join('');
    }

    cleanupsLocal.listen($('#selInstance'), 'change', () => {
        state.selected = $('#selInstance').value;
        $('#baseUrlHint').textContent = baseUrl() ? `目标：${baseUrl()}` : '选择实例后自动填充其地址';
    });
    cleanupsLocal.listen($('#reqMethod'), 'change', syncBodyVisibility);
    cleanupsLocal.listen(view, 'click', async (e) => {
        const presetBtn = e.target.closest('[data-preset]');
        if (presetBtn) {
            const p = PRESETS[presetBtn.dataset.preset];
            $('#reqMethod').value = p.method;
            $('#reqUrl').value = p.url;
            $('#reqBody').value = p.body;
            syncBodyVisibility();
            return;
        }
        const restoreBtn = e.target.closest('[data-restore]');
        if (restoreBtn) {
            const item = state.history[parseInt(restoreBtn.dataset.restore, 10)];
            if (item) {
                $('#reqMethod').value = item.method;
                $('#reqUrl').value = item.url.replace(baseUrl(), '') || '/';
                $('#reqBody').value = item.body || '';
                syncBodyVisibility();
            }
        }
    });

    cleanupsLocal.listen($('#btnSend'), 'click', async () => {
        if (state.sending) return;
        const target = baseUrl();
        if (!target) { toast('请先选择实例', 'warning'); return; }
        const method = $('#reqMethod').value;
        const path = $('#reqUrl').value;
        const bodyText = $('#reqBody').value;
        const options = { method, headers: { Authorization: 'Bearer local' } };
        if (method === 'POST' || method === 'PUT') {
            options.headers['Content-Type'] = 'application/json';
            options.body = bodyText;
        }
        state.sending = true;
        $('#btnSend').disabled = true;
        const started = performance.now();
        try {
            const res = await fetch(target + path, options);
            const text = await res.text();
            if (cleanupsLocal.dead) return;
            const elapsed = Math.round(performance.now() - started);
            state.response = { status: res.status, body: text, elapsed };
            const badge = $('#respStatus');
            badge.hidden = false;
            badge.textContent = `${res.status} · ${elapsed} ms`;
            badge.className = 'ui-badge ' + (res.ok ? 'ui-badge--success' : 'ui-badge--danger');
            $('#respBody').textContent = formatJson(text);
            $('#respMeta').textContent = `${new Date().toLocaleTimeString('zh-CN')} · ${method} ${target + path}`;
            state.history.unshift({ method, url: target + path, body: bodyText, time: new Date().toLocaleTimeString('zh-CN'), status: res.status });
            state.history = state.history.slice(0, 20);
            renderHistory();
        } catch (e) {
            $('#respStatus').hidden = true;
            $('#respBody').textContent = '请求失败：' + e.message;
        } finally {
            state.sending = false;
            $('#btnSend').disabled = false;
        }
    });

    (async () => {
        try {
            state.instances = await api.get('/api/instances');
            const sel = $('#selInstance');
            state.instances.forEach((inst) => {
                const opt = document.createElement('option');
                opt.value = inst.id;
                opt.textContent = `${inst.name} (${inst.port})`;
                sel.appendChild(opt);
            });
            const running = state.instances.find((i) => i.status === 'running');
            if (running) {
                state.selected = running.id;
                sel.value = running.id;
                $('#baseUrlHint').textContent = `目标：${baseUrl()}`;
            }
        } catch (e) {
            console.error(e);
        }
        syncBodyVisibility();
    })();
}
