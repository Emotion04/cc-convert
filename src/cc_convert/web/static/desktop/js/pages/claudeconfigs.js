// Claude 配置管理：浏览 ~/.claude 下的所有 settings 文件，标注绑定关系。

import { api } from '../api.js';
import { esc } from '../helpers.js';

export async function render(view, params, ctx) {
    const { toast, cleanups } = ctx;
    let files = [];
    let directory = '';
    const state = { selected: null, content: '' };

    view.innerHTML = `
        <div class="ui-card">
            <div class="desktop-split">
                <div>
                    <strong style="font-size:13px">Claude Code 配置文件</strong>
                    <p class="desktop-muted desktop-mono" id="dirLabel" style="margin:4px 0 0"></p>
                </div>
                <button type="button" class="ui-button" id="btnRefresh">刷新</button>
            </div>
        </div>

        <div class="desktop-inst-grid" style="grid-template-columns: 1.2fr 1fr; align-items:start">
            <div class="ui-card desktop-table-wrap">
                <table class="desktop-table">
                    <thead><tr><th>文件</th><th>BASE_URL</th><th>绑定实例</th><th></th></tr></thead>
                    <tbody id="tbody"></tbody>
                </table>
                <p class="desktop-muted" id="empty" hidden style="text-align:center; padding:24px">
                    该目录下没有配置文件
                </p>
            </div>

            <div class="ui-card">
                <div class="desktop-split">
                    <strong style="font-size:13px" id="previewTitle">选择一个文件查看内容</strong>
                    <button type="button" class="ui-button" id="btnCopy" hidden>复制路径</button>
                </div>
                <pre class="desktop-cmd" id="preview" style="max-height:420px">—</pre>
            </div>
        </div>

        <p class="desktop-hint">
            用法：<code class="desktop-mono">claude --settings &lt;文件名&gt;</code> 即可用对应实例启动。
            绑定关系由面板维护，手动编辑文件不会改变绑定。
        </p>
    `;

    const $ = (sel) => view.querySelector(sel);

    function renderTable() {
        $('#dirLabel').textContent = directory;
        $('#empty').hidden = files.length > 0;
        $('#tbody').innerHTML = files.map((f) => {
            const bound = f.bound_to
                ? `<span class="ui-badge ui-badge--success">${esc(f.bound_to.instance_name)}</span>`
                : (f.is_main_settings ? '<span class="ui-badge">主配置</span>' : '<span class="desktop-muted">—</span>');
            const manageable = !f.is_main_settings && !f.bound_to;
            return `
            <tr data-path="${esc(f.path)}" style="cursor:pointer">
                <td><span class="desktop-mono">${esc(f.name)}</span></td>
                <td class="desktop-mono desktop-muted" style="text-align:left; max-width:260px; overflow:hidden; text-overflow:ellipsis">
                    ${esc(f.base_url || '—')}
                </td>
                <td>${bound}</td>
                <td>
                    <div class="ui-cluster" style="gap:4px">
                        <button type="button" class="ui-button" data-view="${esc(f.path)}">查看</button>
                        ${manageable ? `
                        <button type="button" class="ui-button" data-rename="${esc(f.path)}" data-name="${esc(f.name)}">重命名</button>
                        <button type="button" class="ui-button ui-button--danger" data-fdel="${esc(f.path)}">删除</button>` : ''}
                    </div>
                </td>
            </tr>`;
        }).join('');
    }

    cleanups.listen($('#tbody'), 'click', async (e) => {
        const rn = e.target.closest('[data-rename]');
        if (rn) {
            const oldPath = rn.dataset.rename;
            const oldName = rn.dataset.name;
            const input = window.prompt('新的文件名（不含路径）', oldName);
            if (!input || input === oldName) return;
            try {
                await api.post('/api/claude-configs/rename', { path: oldPath, new_name: input });
                toast('已重命名', 'success');
                await refresh();
            } catch (err) {
                toast(err.message || '重命名失败', 'danger');
            }
            return;
        }
        const fd = e.target.closest('[data-fdel]');
        if (fd) {
            if (fd.dataset.armed) {
                fd.dataset.armed = '';
                try {
                    await api.del('/api/claude-configs?path=' + encodeURIComponent(fd.dataset.fdel));
                    toast('配置文件已删除', 'success');
                    await refresh();
                } catch (err) {
                    toast(err.message || '删除失败', 'danger');
                }
            } else {
                fd.dataset.armed = '1';
                fd.textContent = '确认删除？';
                setTimeout(() => { fd.dataset.armed = ''; fd.textContent = '删除'; }, 4000);
            }
            return;
        }
        const btn = e.target.closest('[data-view]');
        const row = e.target.closest('tr[data-path]');
        const path = btn ? btn.dataset.view : row ? row.dataset.path : null;
        if (!path) return;
        state.selected = path;
        try {
            const data = await api.get('/api/claude-configs/content?path=' + encodeURIComponent(path));
            $('#preview').textContent = data.content || '（空文件）';
            $('#previewTitle').textContent = path.split(/[\\/]/).pop();
            $('#btnCopy').hidden = false;
        } catch (err) {
            $('#preview').textContent = '读取失败：' + (err.message || '未知错误');
        }
    });

    cleanups.listen($('#btnCopy'), 'click', () => {
        if (!state.selected) return;
        navigator.clipboard.writeText(state.selected)
            .then(() => toast('路径已复制', 'success'))
            .catch(() => toast('复制失败', 'danger'));
    });

    async function refresh() {
        try {
            const data = await api.get('/api/claude-configs');
            directory = data.directory || '';
            files = data.files || [];
            renderTable();
        } catch (e) {
            toast('读取配置目录失败：' + e.message, 'danger');
        }
    }

    cleanups.listen($('#btnRefresh'), 'click', refresh);
    await refresh();
}
