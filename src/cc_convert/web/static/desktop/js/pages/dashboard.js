// 仪表板：Manager 概览 + 实例卡片（启停/重启/编辑/复制配置/删除）+ 全局设置弹窗。

import { api } from '../api.js';
import {
    esc, statusDot, statusBadge, statusLabel, formatUptime, truncateUrl,
    psCmd, cmdCmd, bashCmd, settingsJson,
} from '../helpers.js';

export async function render(view, params, ctx) {
    const { toast, cleanups, navigate } = ctx;
    let instances = [];
    let manager = {};
    const busy = {};
    const probe = {};

    view.innerHTML = `
        <div class="ui-card" id="mgrCard">
            <div class="desktop-split">
                <div class="ui-cluster">
                    <strong>CC Convert Manager</strong>
                    <span class="desktop-muted desktop-mono" id="mgrInfo"></span>
                </div>
                <div class="ui-cluster" id="mgrStats"></div>
            </div>
        </div>

        <div class="ui-toolbar">
            <strong style="font-size:13px">代理实例</strong>
            <div class="ui-cluster" style="margin-left:auto">
                <button type="button" class="ui-button" id="btnSettings">设置</button>
                <button type="button" class="ui-button" id="btnReprobe">重新探测协议</button>
                <button type="button" class="ui-button ui-button--primary" id="btnAdd">添加实例</button>
            </div>
        </div>

        <div id="instGrid" class="desktop-inst-grid"></div>
        <div id="instEmpty" class="ui-empty" hidden>
            <p><strong>暂无代理实例</strong></p>
            <p class="desktop-muted">创建你的第一个 CC Convert 代理实例来开始使用</p>
            <button type="button" class="ui-button ui-button--primary" id="btnAddEmpty">创建实例</button>
        </div>

        <dialog id="settingsDialog" class="ui-dialog">
            <div class="desktop-form" style="padding:20px; min-width:360px">
                <h3 style="margin:0">设置</h3>
                <div>
                    <label class="desktop-label">日志级别</label>
                    <select class="ui-select" id="setLogLevel">
                        <option value="INFO">INFO（默认，只记录必要信息）</option>
                        <option value="DEBUG">DEBUG（详细，含缓存诊断）</option>
                    </select>
                    <p class="desktop-hint">DEBUG 会在每次请求写入缓存前缀与命中信息，仅排查时开启。修改后需要重启实例才生效。</p>
                </div>
                <div class="desktop-split desktop-split--end">
                    <button type="button" class="ui-button" data-ui-close>取消</button>
                    <button type="button" class="ui-button ui-button--primary" id="setSave">保存</button>
                </div>
            </div>
        </dialog>
    `;

    const grid = view.querySelector('#instGrid');
    const emptyBox = view.querySelector('#instEmpty');

    // ── data ──────────────────────────────────────────────────────────────
    async function fetchAll() {
        const [mgr, list] = await Promise.all([
            api.get('/api/manager').catch(() => ({})),
            api.get('/api/instances'),
        ]);
        if (cleanups.dead) return;
        manager = mgr;
        instances = list;
        renderManager();
        renderInstances();
        // Stats in parallel so slow workers don't block the grid.
        await Promise.all(list.filter((i) => i.status === 'running').map(async (inst) => {
            try { inst.stats = await api.get(`/api/instances/${inst.id}/stats`); }
            catch { inst.stats = null; }
            renderInstances();
        }));
    }

    function renderManager() {
        view.querySelector('#mgrInfo').textContent =
            `v${manager.version || '?'} | PID: ${manager.pid || '?'} | ${manager.host || '127.0.0.1'}:${manager.port || '?'}`;
        view.querySelector('#mgrStats').innerHTML = `
            <div style="text-align:right"><div class="desktop-muted">实例</div><strong class="desktop-mono">${manager.instance_count ?? '-'}</strong></div>
            <div style="text-align:right"><div class="desktop-muted">运行中</div><strong class="desktop-mono">${manager.running_count ?? '-'}</strong></div>
            <div style="text-align:right"><div class="desktop-muted">运行时间</div><strong class="desktop-mono">${formatUptime(manager.uptime)}</strong></div>
        `;
    }

    function probeLine(inst) {
        const p = probe[inst.id];
        if (!p) return '';
        const label = p.error ? '探测失败'
            : `推荐: ${p.recommended === 'responses' ? 'responses' : 'chat'}`;
        const tone = p.recommended ? 'success' : 'default';
        const switchBtn = p.recommended === 'responses' && inst.upstream_format !== 'responses'
            ? `<button type="button" class="ui-button" data-act="switch-format" data-id="${inst.id}">切换</button>` : '';
        return `<div class="ui-cluster"><span class="ui-badge ui-badge--${tone}">${esc(label)}</span>${switchBtn}</div>`;
    }

    function copyPopover(inst) {
        const hasModels = inst.client_model || inst.client_sonnet_model || inst.client_opus_model || inst.client_haiku_model;
        return `
        <div class="ui-popover" data-popover-id="${inst.id}" hidden style="min-width:320px">
            <div style="padding:12px">
                <p class="desktop-muted" style="margin:0 0 8px"><strong>客户端配置</strong></p>
                <div class="desktop-muted">PowerShell</div><code class="desktop-cmd">${esc(psCmd(inst))}</code>
                <div class="desktop-muted">CMD</div><code class="desktop-cmd">${esc(cmdCmd(inst))}</code>
                <div class="desktop-muted">Bash</div><code class="desktop-cmd">${esc(bashCmd(inst))}</code>
                ${hasModels ? `<div class="desktop-muted">settings.json 片段</div><code class="desktop-cmd">${esc(settingsJson(inst))}</code>` : ''}
                <button type="button" class="ui-button" style="width:100%" data-act="copy-config" data-id="${inst.id}">复制到剪贴板</button>
            </div>
        </div>`;
    }

    function instCard(inst) {
        const b = busy[inst.id];
        const running = inst.status === 'running';
        return `
        <div class="ui-card desktop-inst-card" data-card-id="${inst.id}">
            <div class="desktop-split" style="align-items:flex-start">
                <div class="ui-cluster">
                    ${statusDot(inst.status)}
                    <div>
                        <strong>${esc(inst.name)}</strong>
                        <div class="desktop-mono desktop-muted">127.0.0.1:${inst.port}</div>
                        ${probeLine(inst)}
                    </div>
                </div>
                ${statusBadge(inst.status)}
            </div>
            <div>
                <div class="desktop-keyval"><span>上游 URL</span><span>${esc(truncateUrl(inst.upstream_base_url))}</span></div>
                <div class="desktop-keyval"><span>目标模型</span><span>${esc(inst.target_model || '透传')}</span></div>
                <div class="desktop-keyval"><span>API 密钥</span><span>${inst.key_configured ? '已配置' : '未设置'}</span></div>
                ${running ? `<div class="desktop-keyval"><span>请求 / 错误</span><span>${inst.stats?.today_requests ?? 0} / ${inst.stats?.today_errors ?? 0}</span></div>` : ''}
            </div>
            <div class="ui-cluster" style="gap:6px">
                <button type="button" class="ui-button ${running ? '' : 'ui-button--primary'}" data-act="toggle" data-id="${inst.id}" ${b ? 'disabled' : ''}>
                    ${b ? (b === 'stop' ? '停止中…' : b === 'restart' ? '重启中…' : '启动中…') : running ? '停止' : '启动'}
                </button>
                ${running ? `<button type="button" class="ui-button" data-act="restart" data-id="${inst.id}" ${b ? 'disabled' : ''}>重启</button>` : ''}
                <button type="button" class="ui-button" data-act="edit" data-id="${inst.id}">编辑</button>
                <div class="ui-cluster" style="position:relative">
                    <button type="button" class="ui-button" data-act="copy" data-id="${inst.id}">复制配置</button>
                    ${copyPopover(inst)}
                </div>
                <button type="button" class="ui-button ui-button--danger" data-act="delete" data-id="${inst.id}" style="margin-left:auto">删除</button>
            </div>
        </div>`;
    }

    function renderInstances() {
        grid.innerHTML = instances.map(instCard).join('');
        emptyBox.hidden = instances.length > 0;
        grid.hidden = instances.length === 0;
    }

    function findInst(id) { return instances.find((i) => i.id === id); }

    // ── actions ───────────────────────────────────────────────────────────
    async function runAction(inst, action, timeoutMs) {
        busy[inst.id] = action;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        const label = action === 'start' ? '启动' : action === 'stop' ? '停止' : '重启';
        try {
            await api.post(`/api/instances/${inst.id}/${action}`, undefined, { signal: controller.signal });
            toast(action === 'start' ? '实例已启动' : action === 'stop' ? '实例已停止' : '实例已重启', 'success');
        } catch (e) {
            const detail = e.name === 'AbortError' ? `超时（${Math.round(timeoutMs / 1000)} 秒）` : e.message;
            toast(`${label}失败：${detail}。可查看实例日志获取原因。`, 'danger');
        } finally {
            clearTimeout(timer);
            busy[inst.id] = null;
            if (!cleanups.dead) await fetchAll();
        }
    }

    async function runReprobe() {
        const btn = view.querySelector('#btnReprobe');
        btn.disabled = true;
        btn.textContent = '探测中…';
        for (const k of Object.keys(probe)) delete probe[k];
        try {
            const data = await api.post('/api/reprobe-formats', {});
            (data.results || []).forEach((item) => {
                probe[item.id] = { recommended: item.recommended, current: item.current, error: item.error };
            });
            const usable = (data.results || []).filter((i) => i.recommended === 'responses').length;
            toast(`探测完成：${usable} 个实例可用 responses`, 'success');
            renderInstances();
        } catch (e) {
            toast('探测失败: ' + e.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.textContent = '重新探测协议';
        }
    }

    async function switchToResponses(inst) {
        busy[inst.id] = 'format';
        try {
            await api.patch(`/api/instances/${inst.id}`, { upstream_format: 'responses' });
            toast('已切换到 responses', 'success');
            await fetchAll();
        } catch (e) {
            toast('切换失败: ' + e.message, 'danger');
        } finally {
            busy[inst.id] = null;
        }
    }

    function copyConfigText(inst) {
        let text = `ANTHROPIC_BASE_URL=http://127.0.0.1:${inst.port}\nANTHROPIC_AUTH_TOKEN=local`;
        const models = [];
        if (inst.client_model) models.push(`ANTHROPIC_MODEL=${inst.client_model}`);
        if (inst.client_sonnet_model) models.push(`ANTHROPIC_DEFAULT_SONNET_MODEL=${inst.client_sonnet_model}`);
        if (inst.client_opus_model) models.push(`ANTHROPIC_DEFAULT_OPUS_MODEL=${inst.client_opus_model}`);
        if (inst.client_haiku_model) models.push(`ANTHROPIC_DEFAULT_HAIKU_MODEL=${inst.client_haiku_model}`);
        if (models.length) text += '\n' + models.join('\n');
        navigator.clipboard.writeText(text).then(() => toast('配置已复制到剪贴板', 'success'));
    }

    async function doDelete(inst) {
        try {
            await api.del(`/api/instances/${inst.id}`);
            toast('实例已删除', 'success');
            await fetchAll();
        } catch (e) {
            toast(e.message || '删除失败', 'danger');
        }
    }

    // Settings dialog
    const settingsDialog = view.querySelector('#settingsDialog');
    view.querySelector('#btnSettings').addEventListener('click', async () => {
        try {
            const s = await api.get('/api/global');
            view.querySelector('#setLogLevel').value = s.log_level || 'INFO';
        } catch { /* keep defaults */ }
        settingsDialog.showModal();
    });
    view.querySelector('#setSave').addEventListener('click', async () => {
        try {
            await api.patch('/api/global', { log_level: view.querySelector('#setLogLevel').value });
            settingsDialog.close();
            toast('设置已保存，重启实例后生效', 'success');
        } catch (e) {
            toast(e.message || '保存失败', 'danger');
        }
    });

    // Global click delegation
    cleanups.listen(grid, 'click', async (e) => {
        const btn = e.target.closest('[data-act]');
        if (!btn) return;
        const act = btn.dataset.act;
        const id = btn.dataset.id;
        const inst = findInst(id);
        if (!inst) return;

        if (act === 'toggle') {
            await runAction(inst, inst.status === 'running' ? 'stop' : 'start', 90000);
        } else if (act === 'restart') {
            await runAction(inst, 'restart', 120000);
        } else if (act === 'edit') {
            window.location.hash = `#/config?id=${id}`;
        } else if (act === 'copy') {
            const pop = btn.parentElement.querySelector('[data-popover-id]');
            if (pop) {
                pop.hidden = !pop.hidden;
                if (!pop.hidden) {
                    const close = (ev) => {
                        if (!pop.contains(ev.target) && ev.target !== btn) { pop.hidden = true; document.removeEventListener('click', close); }
                    };
                    setTimeout(() => document.addEventListener('click', close), 0);
                }
            }
        } else if (act === 'copy-config') {
            copyConfigText(inst);
        } else if (act === 'switch-format') {
            await switchToResponses(inst);
        } else if (act === 'delete') {
            if (inst.status === 'running') {
                toast('请先停止实例再删除', 'warning');
                return;
            }
            // Two-step destructive confirm (universal-ui arm button semantics).
            if (btn.dataset.armed) {
                btn.dataset.armed = '';
                await doDelete(inst);
            } else {
                btn.dataset.armed = '1';
                btn.textContent = '确认删除？';
                setTimeout(() => { btn.dataset.armed = ''; btn.textContent = '删除'; }, 4000);
            }
        }
    });

    view.querySelector('#btnAdd').addEventListener('click', () => { window.location.hash = '#/config'; });
    view.querySelector('#btnAddEmpty').addEventListener('click', () => { window.location.hash = '#/config'; });
    view.querySelector('#btnReprobe').addEventListener('click', runReprobe);

    await fetchAll();
}
