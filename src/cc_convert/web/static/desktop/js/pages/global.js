// 全局设置：日志级别、超时、搜索服务（Tavily/通晓）与 CORS。

import { api } from '../api.js';

export async function render(view, params, ctx) {
    const { toast, cleanups } = ctx;
    let settings = {};

    view.innerHTML = `
        <div class="ui-card desktop-form" style="max-width:680px">
            <div>
                <label class="desktop-label">日志级别</label>
                <select class="ui-select" id="g-log_level" style="width:100%">
                    <option value="INFO">INFO（默认，只记录必要信息）</option>
                    <option value="DEBUG">DEBUG（详细，含缓存诊断）</option>
                </select>
                <p class="desktop-hint">修改后需要重启实例才生效。DEBUG 会在每次请求写入缓存前缀与命中信息，仅排查时开启。</p>
            </div>
            <div>
                <label class="desktop-label">上游请求超时（秒）</label>
                <input type="number" class="ui-input" id="g-timeout" min="30" max="3600">
            </div>
            <fieldset style="border:1px solid var(--ui-line); border-radius:10px; padding:14px; display:grid; gap:12px">
                <legend style="font-size:12px; color:var(--ui-fg-muted)">Web 搜索服务（服务端工具）</legend>
                <div>
                    <label class="desktop-label">Tavily API Key</label>
                    <input type="text" class="ui-input desktop-mono" id="g-tavily_api_key" placeholder="tvly-...">
                    <p class="desktop-hint">已保存的 key 会以 *** 脱敏显示；留空保持不变。</p>
                </div>
                <div class="desktop-row">
                    <div style="flex:1"><label class="desktop-label">超时（秒）</label><input type="number" class="ui-input" id="g-tavily_timeout"></div>
                    <div style="flex:1"><label class="desktop-label">最大结果数</label><input type="number" class="ui-input" id="g-tavily_max_results"></div>
                </div>
                <div>
                    <label class="desktop-label">通晓 API Key</label>
                    <input type="text" class="ui-input desktop-mono" id="g-tongxiao_api_key">
                </div>
                <div class="desktop-row">
                    <div style="flex:1"><label class="desktop-label">超时（秒）</label><input type="number" class="ui-input" id="g-tongxiao_timeout"></div>
                    <div style="flex:1"><label class="desktop-label">最大结果数</label><input type="number" class="ui-input" id="g-tongxiao_max_results"></div>
                </div>
            </fieldset>
            <fieldset style="border:1px solid var(--ui-line); border-radius:10px; padding:14px; display:grid; gap:12px">
                <legend style="font-size:12px; color:var(--ui-fg-muted)">CORS（Worker 的浏览器跨源策略）</legend>
                <div>
                    <label class="desktop-label">允许来源（逗号分隔）</label>
                    <input type="text" class="ui-input desktop-mono" id="g-cors_origins" placeholder="*">
                </div>
                <label class="desktop-row" style="font-size:12px"><input type="checkbox" id="g-cors_credentials"> 允许携带凭据</label>
            </fieldset>
            <div class="desktop-split desktop-split--end" style="border-top:1px solid var(--ui-line); padding-top:14px">
                <button type="button" class="ui-button ui-button--primary" id="btnSave">保存</button>
            </div>
        </div>
    `;

    const $ = (sel) => view.querySelector(sel);

    async function load() {
        settings = await api.get('/api/global');
        if (cleanups.dead) return;
        const map = {
            'g-log_level': 'log_level', 'g-timeout': 'timeout',
            'g-tavily_api_key': 'tavily_api_key', 'g-tavily_timeout': 'tavily_timeout', 'g-tavily_max_results': 'tavily_max_results',
            'g-tongxiao_api_key': 'tongxiao_api_key', 'g-tongxiao_timeout': 'tongxiao_timeout', 'g-tongxiao_max_results': 'tongxiao_max_results',
        };
        for (const [sel, key] of Object.entries(map)) {
            if (settings[key] != null) $(sel).value = settings[key];
        }
        $('#g-cors_origins').value = Array.isArray(settings.cors_origins)
            ? settings.cors_origins.join(', ') : (settings.cors_origins ?? '');
        $('#g-cors_credentials').checked = settings.cors_credentials !== false;
    }

    cleanups.listen($('#btnSave'), 'click', async () => {
        const body = {
            log_level: $('#g-log_level').value,
            timeout: Number($('#g-timeout').value) || undefined,
            tavily_api_key: $('#g-tavily_api_key').value.trim() || undefined,
            tavily_timeout: Number($('#g-tavily_timeout').value) || undefined,
            tavily_max_results: Number($('#g-tavily_max_results').value) || undefined,
            tongxiao_api_key: $('#g-tongxiao_api_key').value.trim() || undefined,
            tongxiao_timeout: Number($('#g-tongxiao_timeout').value) || undefined,
            tongxiao_max_results: Number($('#g-tongxiao_max_results').value) || undefined,
            cors_origins: $('#g-cors_origins').value.split(',').map((s) => s.trim()).filter(Boolean),
            cors_credentials: $('#g-cors_credentials').checked,
        };
        // Drop undefined so masked/blank fields keep their stored values.
        Object.keys(body).forEach((k) => body[k] === undefined && delete body[k]);
        try {
            settings = await api.patch('/api/global', body);
            if (cleanups.dead) return;
            toast('全局设置已保存（部分项需重启实例生效）', 'success');
            await load();
        } catch (e) {
            toast(e.message || '保存失败', 'danger');
        }
    });

    try {
        await load();
    } catch (e) {
        if (!cleanups.dead) toast('加载全局设置失败: ' + e.message, 'danger');
    }
}
