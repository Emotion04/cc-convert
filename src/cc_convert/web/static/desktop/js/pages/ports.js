// 端口管理：总览、逐实例检测、重绑定。

import { api } from '../api.js';
import { esc, statusDot, statusBadge } from '../helpers.js';

export async function render(view, params, ctx) {
    const { toast, cleanups } = ctx;
    let rows = [];
    const busy = {};

    view.innerHTML = `
        <div class="ui-toolbar">
            <strong style="font-size:13px">端口总览</strong>
            <button type="button" class="ui-button" id="btnRefresh" style="margin-left:auto">刷新</button>
        </div>
        <div class="ui-card desktop-table-wrap">
            <table class="desktop-table">
                <thead><tr><th>实例</th><th>端口</th><th>状态</th><th>占用者</th><th>诊断</th><th>操作</th></tr></thead>
                <tbody id="tbody"></tbody>
            </table>
            <p class="desktop-muted" id="empty" hidden style="text-align:center; padding:24px">暂无实例</p>
        </div>
        <p class="desktop-hint">「检测」会探测该端口当前可绑定性与占用进程；「重绑定」会为实例重新分配一个可用端口并重启。</p>
    `;

    const $ = (sel) => view.querySelector(sel);

    function renderRows() {
        $('#empty').hidden = rows.length > 0;
        $('#tbody').innerHTML = rows.map((row) => {
            const b = busy[row.instance_id];
            return `
            <tr>
                <td><div class="ui-cluster">${statusDot(row.status)}<strong>${esc(row.name)}</strong></div></td>
                <td class="desktop-mono">${row.port}</td>
                <td>${statusBadge(row.status)}</td>
                <td class="desktop-mono desktop-muted">${esc(row.occupier || '—')}</td>
                <td class="desktop-muted" style="white-space:normal; max-width:340px">${esc(row.diagnosis || '—')}</td>
                <td>
                    <div class="ui-cluster">
                        <button type="button" class="ui-button" data-act="check" data-id="${row.instance_id}" ${b ? 'disabled' : ''}>${b === 'check' ? '检测中…' : '检测'}</button>
                        <button type="button" class="ui-button" data-act="rebind" data-id="${row.instance_id}" ${b ? 'disabled' : ''}>${b === 'rebind' ? '重绑中…' : '重绑定'}</button>
                    </div>
                </td>
            </tr>`;
        }).join('');
    }

    async function fetchOverview() {
        try {
            const data = await api.get('/api/ports/overview');
            rows = data.instances || data.rows || [];
            renderRows();
        } catch (e) {
            toast('加载端口总览失败: ' + e.message, 'danger');
        }
    }

    cleanups.listen($('#tbody'), 'click', async (e) => {
        const btn = e.target.closest('[data-act]');
        if (!btn) return;
        const id = btn.dataset.id;
        const act = btn.dataset.act;
        if (busy[id]) return;
        busy[id] = act;
        renderRows();
        try {
            if (act === 'check') {
                const data = await api.post(`/api/ports/${id}/check`);
                const row = rows.find((r) => r.instance_id === id);
                if (row) {
                    row.diagnosis = data.message || data.diagnosis || (data.ok ? '正常' : '异常');
                    row.occupier = data.occupier || row.occupier;
                }
                toast(data.message || '检测完成', (data.ok ?? true) ? 'success' : 'warning');
            } else if (act === 'rebind') {
                const data = await api.post(`/api/ports/${id}/rebind`);
                toast(`已重绑定到端口 ${data.new_port ?? '?'}`, 'success');
            }
        } catch (err) {
            toast(err.message || '操作失败', 'danger');
        } finally {
            busy[id] = null;
            await fetchOverview();
        }
    });

    cleanups.listen($('#btnRefresh'), 'click', fetchOverview);
    await fetchOverview();
}
