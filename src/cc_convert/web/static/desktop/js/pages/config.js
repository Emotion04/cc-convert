// 实例配置：创建/编辑全字段、密钥展开、协议探测、上游策略探测（20s 倒计时）、获取模型。

import { api } from '../api.js';
import { esc } from '../helpers.js';

const THINKING_OPTIONS = [
    ['auto', '智能（auto，推荐）：内置规则 + 从上游 400 学习'],
    ['upstream', '遵循上游（upstream）：启动时实时探测，多 1~2 次极小请求'],
    ['rules', '严格规则（rules）：不学习、不纠正'],
    ['turn', '仅当前轮（turn）：含整条工具调用链'],
    ['keep', '全部回传（keep）'],
    ['drop', '全部丢弃（drop）'],
];

const THINKING_HELP = [
    ['OpenAI o 系列', 'keep', '官方 API 保留推理内容并在后续请求回传'],
    ['DeepSeek V4', 'turn', '工具调用轮要求回传推理，历史轮丢弃'],
    ['Kimi', 'turn', '工具调用轮要求回传推理'],
    ['GLM', 'turn', '工具调用轮要求回传推理'],
    ['Qwen', 'drop', '不接受历史推理回传'],
    ['vLLM / SGLang', 'auto', '取决于所服务模型，按上游 400 表现学习'],
];

    function thinkingNotes() {
        try {
            const raw = JSON.parse(document.getElementById('thinkingNotes').textContent);
            if (Array.isArray(raw) && raw.length) return raw;
        } catch { /* fall through to builtin */ }
        return THINKING_HELP.map(([provider, label, summary]) => ({ provider, label, summary, source: '' }));
    }

/**
 * Minimal file picker for settings fragments. Built on <dialog> so focus
 * trapping and Escape come from the platform rather than hand-written code.
 */
function showFilePicker(directory, files, onPick) {
    const dialog = document.createElement('dialog');
    dialog.className = 'ui-dialog';
    dialog.innerHTML = `
        <div class="desktop-form" style="padding:20px; min-width:420px; max-width:560px">
            <div>
                <h3 style="margin:0 0 4px">选择配置文件</h3>
                <p class="desktop-hint" style="margin:0">${directory}</p>
            </div>
            <div class="desktop-form" style="gap:6px; max-height:50vh; overflow:auto"></div>
            <div class="desktop-split desktop-split--end">
                <button type="button" class="ui-button" data-cancel>取消</button>
            </div>
        </div>`;
    const list = dialog.querySelector('.desktop-form > .desktop-form');
    for (const f of files) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ui-button';
        btn.style.cssText = 'justify-content:flex-start;text-align:left;width:100%';
        const bound = f.bound_to ? `　· 已绑定「${f.bound_to.instance_name}」` : '';
        btn.innerHTML = `<span class="desktop-mono">${f.name}</span>`
            + `<span class="desktop-muted" style="margin-left:8px">${f.base_url || '未设置 BASE_URL'}${bound}</span>`;
        btn.addEventListener('click', () => { onPick(f); dialog.close(); });
        list.appendChild(btn);
    }
    dialog.querySelector('[data-cancel]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => dialog.remove());
    document.body.appendChild(dialog);
    dialog.showModal();
}

export async function render(view, params, ctx) {
    const { toast, cleanups, navigate } = ctx;
    const instanceId = params.get('id') || null;
    const isEdit = !!instanceId;

    let formData = {
        name: '',
        port: 0,
        upstream_base_url: 'https://api.openai.com/v1',
        upstream_api_key: '',
        target_model: '',
        probe_model: '',
        client_model: '',
        client_sonnet_model: '',
        client_opus_model: '',
        client_haiku_model: '',
        client_fable_model: '',
        auto_start: true,
        upstream_format: 'auto',
        thinking_history: 'auto',
        thinking_history_auto_correct: true,
        thinking_history_force_manual: false,
        claude_config_path: '',
        context_window: 0,
        compact_window: 0,
        prompt_cache_ttl: '',
        subagent_cache_ttl: '',
        claude_config_use_upstream_key: true,
    };
    let originalData = null;
    let thinkingCorrected = false;
    let thinkingCorrectedTo = '';
    const saveWarnings = [];
    const state = {
        showKey: false, clearKey: false, saving: false, loading: isEdit,
        formatStatus: '', detectedFormat: '', modelsLoading: false,
        probe: { status: 'idle', ok: false, label: '', evidence: '', applied: false, elapsed: 0, remaining: 20 },
        availableModels: [], showModelOverrides: false, showHelp: false,
    };

    view.innerHTML = `
        <div class="ui-card" style="max-width:720px">
            <div id="loadingBox" style="text-align:center; padding:40px"><div class="ui-spinner"></div><p class="desktop-muted">加载配置中…</p></div>

            <div id="formBox" class="desktop-form" hidden>
                <div>
                    <label class="desktop-label">名称</label>
                    <input type="text" class="ui-input" id="f-name" placeholder="例如: 我的代理">
                </div>
                <div>
                    <label class="desktop-label">端口</label>
                    <div class="desktop-row">
                        <input type="number" class="ui-input desktop-mono" id="f-port" placeholder="自动分配">
                        <button type="button" class="ui-button" id="btnPort">自动</button>
                    </div>
                    <p class="desktop-hint">Claude Code 中请使用完整地址（含协议）：<code class="desktop-mono">http://127.0.0.1:<span id="portEcho"></span></code></p>
                </div>
                <div>
                    <label class="desktop-label">上游 Base URL</label>
                    <input type="text" class="ui-input desktop-mono" id="f-url" placeholder="https://api.openai.com/v1">
                </div>
                <div>
                    <label class="desktop-label">上游协议 <span class="desktop-muted" style="font-weight:400">(自动)</span></label>
                    <div class="desktop-row">
                        <select class="ui-select" id="f-format" style="flex:1">
                            <option value="auto">自动检测</option>
                            <option value="chat_completions">OpenAI Chat Completions</option>
                            <option value="responses">OpenAI Responses</option>
                        </select>
                        <button type="button" class="ui-button" id="btnFormat">探测协议</button>
                    </div>
                    <p class="desktop-hint" id="formatHint"></p>
                    <p class="desktop-hint" id="responsesNote" hidden>上游为 Responses 时，代理会把 Claude Code 的请求体自动转换为 Responses 格式（含工具调用与推理项），响应再转回 Anthropic。两种协议都能用；该中转的缓存统计不稳定（同一请求时而命中、时而报 0），不要据此判断优劣，按上游实际支持的能力选择即可。</p>
                </div>
                <div>
                    <label class="desktop-label">思考历史回传</label>
                    <div class="desktop-row">
                        <select class="ui-select" id="f-thinking" style="flex:1">
                            ${THINKING_OPTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}
                        </select>
                        <button type="button" class="ui-button" id="btnProbe">探测上游策略</button>
                    </div>
                    <p class="desktop-hint" id="probeHint"></p>
                    <p class="desktop-hint">「仅当前轮」回传最后一次用户输入之后的全部 assistant 推理（包括多步工具调用），更早轮次全部丢弃：满足需要工具轮回传的上游，同时避免重发历史推理。</p>
                    <label class="desktop-row" style="margin-top:6px; font-size:12px"><input type="checkbox" id="f-autocorrect"> 自动纠正：上游返回相关 400 时自动调整后续请求（不会重试当前请求）</label>
                    <label class="desktop-row" style="font-size:12px"><input type="checkbox" id="f-forcemanual"> 强制手动优先：忽略自动纠正，始终使用我选择的模式</label>
                    <div class="ui-alert ui-alert--warning" id="correctedBox" hidden style="margin-top:8px">
                        <p style="margin:0; font-size:12px">该实例曾被自动纠正为 <strong id="correctedTo"></strong>。修改 URL / 目标模型 / 协议后会自动失效并重新判断。</p>
                    </div>
                </div>
                <div>
                    <label class="desktop-label">上游 API 密钥</label>
                    <div class="desktop-row">
                        <input type="password" class="ui-input desktop-mono" id="f-key" placeholder="sk-...">
                        <button type="button" class="ui-icon-button" id="btnEye" aria-label="显示/隐藏密钥">
                            <svg class="ui-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/></svg>
                        </button>
                    </div>
                    <p class="desktop-hint" id="keyHint"></p>
                    <label class="desktop-row" id="clearKeyRow" hidden style="font-size:12px"><input type="checkbox" id="f-clearkey"> 清除密钥</label>
                </div>
                <div>
                    <label class="desktop-label">目标模型 <span class="desktop-muted" style="font-weight:400">(可选)</span></label>
                    <div class="desktop-row">
                        <input type="text" class="ui-input desktop-mono" id="f-target" list="modelOptions" placeholder="留空则透传 Claude Code 的模型名">
                        <button type="button" class="ui-button" id="btn1MSuffix" title="在模型名后拼接 [1m]，选择 1M 上下文变体">1M</button>
                        <button type="button" class="ui-button" id="btnModels">获取模型</button>
                    </div>
                    <datalist id="modelOptions"></datalist>
                    <p class="desktop-hint">不填则原样透传，不修改 Claude Code 发来的模型名。此字段覆盖发送给上游的模型名。</p>
                </div>
                <div>
                    <label class="desktop-label">探测用模型 <span class="desktop-muted" style="font-weight:400">(可选)</span></label>
                    <input type="text" class="ui-input desktop-mono" id="f-probemodel" list="modelOptions" placeholder="目标模型为空时，探测与获取模型列表使用这个模型名">
                    <p class="desktop-hint">仅用于面板的「探测协议 / 探测上游策略」，不会发送给 Worker。</p>
                </div>
                <div>
                    <div class="desktop-row" style="justify-content:space-between">
                        <label class="desktop-label" style="margin:0">Claude Code 模型注入 <span class="desktop-muted" style="font-weight:400">(可选)</span></label>
                        <button type="button" class="ui-button" id="btnOverrides">展开</button>
                    </div>
                    <div id="overridesBox" class="desktop-form" hidden style="border-left:2px solid var(--ui-line); padding-left:12px; margin-top:10px">
                        <p class="desktop-hint" style="margin:0">这些设置会显示在"复制配置"弹出框中，你需要手动设置到 Claude Code 的环境变量或 settings.json 中。CC Convert 不会自动注入。</p>
                        ${[['client_model', 'ANTHROPIC_MODEL'], ['client_sonnet_model', 'ANTHROPIC_DEFAULT_SONNET_MODEL'], ['client_opus_model', 'ANTHROPIC_DEFAULT_OPUS_MODEL'], ['client_haiku_model', 'ANTHROPIC_DEFAULT_HAIKU_MODEL'], ['client_fable_model', 'ANTHROPIC_DEFAULT_FABLE_MODEL']].map(([id, label]) => `
                        <div>
                            <label class="desktop-label desktop-mono" style="font-size:11px">${label}</label>
                            <input type="text" class="ui-input desktop-mono" id="f-${id}" placeholder="留空则不注入">
                        </div>`).join('')}
                    </div>
                </div>
                <label class="desktop-row" style="font-size:13px"><input type="checkbox" id="f-autostart" checked> <strong>自动启动</strong>&nbsp;<span class="desktop-muted">创建后自动启动实例</span></label>

                <!-- Claude Code settings binding -->
                <fieldset class="desktop-fieldset">
                    <legend>Claude Code 配置</legend>
                    <p class="desktop-hint" style="margin-top:0">
                        生成一个最小 settings 片段，用 <code class="desktop-mono">claude --settings &lt;文件&gt;</code> 启动即可。
                        未写入的项自动继承主 <code class="desktop-mono">settings.json</code>。
                    </p>

                    <div class="desktop-split">
                        <label class="desktop-label" style="margin:0">上下文窗口</label>
                        <div class="ui-cluster">
                            <button type="button" class="ui-button" id="btnProbeCtx">探测</button>
                            <button type="button" class="ui-button" id="btn1M">1M</button>
                        </div>
                    </div>
                    <div class="desktop-row">
                        <input type="number" class="ui-input" id="f-context" placeholder="留空则不写入（继承主配置）">
                    </div>
                    <p class="desktop-hint" id="ctxHint"></p>

                    <div class="desktop-row" style="margin-top:14px">
                        <div style="flex:1">
                            <label class="desktop-label">压缩阈值</label>
                            <input type="number" class="ui-input" id="f-compact" placeholder="留空则按上下文 97% 自动计算">
                        </div>
                    </div>
                    <p class="desktop-hint" id="compactHint"></p>

                    <div class="desktop-row" style="margin-top:14px">
                        <div style="flex:1">
                            <label class="desktop-label">主缓存 TTL</label>
                            <select class="ui-select" id="f-ttl" style="width:100%">
                                <option value="">不写入（继承）</option>
                                <option value="5m">5 分钟</option>
                                <option value="1h">1 小时</option>
                            </select>
                        </div>
                        <div style="flex:1">
                            <label class="desktop-label">子 agent 缓存 TTL</label>
                            <select class="ui-select" id="f-subttl" style="width:100%">
                                <option value="">不写入（继承）</option>
                                <option value="5m">5 分钟</option>
                                <option value="1h">1 小时</option>
                            </select>
                        </div>
                    </div>
                    <p class="desktop-hint">仅 Claude 系列模型使用缓存 TTL；其他上游会忽略这两项，因此默认不写入。</p>

                    <div style="margin-top:14px">
                        <label class="desktop-label">配置文件</label>
                        <div class="desktop-row">
                            <input type="text" class="ui-input desktop-mono" id="f-ccpath" placeholder="例如 gguuai-claude.json（相对 ~/.claude）或绝对路径">
                            <button type="button" class="ui-button" id="btnPickFile">选择已有</button>
                        </div>
                        <p class="desktop-hint" id="ccBindHint"></p>
                        <label class="desktop-row" style="font-size:12px; margin-top:8px">
                            <input type="checkbox" id="f-ccupstream" checked> 在配置文件中写入上游真实密钥（便于直接查看与管理；代理本身不校验该值）
                        </label>
                    </div>

                    <div class="desktop-split" style="margin-top:12px">
                        <button type="button" class="ui-button" id="btnPreviewCC">预览生成的 JSON</button>
                        <button type="button" class="ui-button" id="btnUnbindCC" hidden>解除绑定</button>
                    </div>
                    <pre class="desktop-cmd" id="ccPreview" hidden style="max-height:240px"></pre>
                </fieldset>

                <div class="desktop-split" style="border-top:1px solid var(--ui-line); padding-top:14px">
                    <button type="button" class="ui-button" id="btnBack">返回仪表板</button>
                    <div class="ui-cluster">
                        <button type="button" class="ui-button" id="btnReset">重置</button>
                        <button type="button" class="ui-button ui-button--primary" id="btnSave">保存</button>
                    </div>
                </div>
            </div>
        </div>

        <dialog id="helpDialog" class="ui-dialog">
            <div style="padding:20px; max-width:560px">
                <h3 style="margin:0 0 10px">各厂商对历史推理的处理</h3>
                <p class="desktop-hint">智能（auto）：使用内置规则，并在收到推理相关的 400 时自动纠正后续请求。遵循上游（upstream）：Worker 启动时真实探测上游（1~2 次极小请求），探测失败则回退到内置规则。严格规则（rules）：不探测、不学习、不纠正。仅当前轮（turn）：只回传最后一次用户输入之后的全部 assistant 消息（含多步工具调用），更早轮次丢弃。</p>
                ${thinkingNotes().map((note) => `
                <div class="ui-card" style="margin-bottom:8px; padding:10px">
                    <div class="desktop-split">
                        <strong style="font-size:13px">${esc(note.provider)}</strong>
                        <span class="ui-badge">${esc(note.label)}</span>
                    </div>
                    <p class="desktop-hint" style="margin:4px 0 0">${esc(note.summary)}</p>
                    ${note.source ? `<a class="ui-link desktop-hint" href="${esc(note.source)}" target="_blank" rel="noopener">来源</a>` : ''}
                </div>`).join('')}
                <p class="desktop-hint">整理自各厂商公开文档（2026-10）。上游策略可能变化，以实际返回为准。</p>
                <div class="desktop-split desktop-split--end"><button type="button" class="ui-button" data-ui-close>关闭</button></div>
            </div>
        </dialog>
    `;

    const $ = (sel) => view.querySelector(sel);
    const formBox = $('#formBox'), loadingBox = $('#loadingBox');

    function bindForm() {
        $('#f-name').value = formData.name;
        $('#f-port').value = formData.port || '';
        $('#f-url').value = formData.upstream_base_url;
        $('#f-key').value = formData.upstream_api_key;
        $('#f-target').value = formData.target_model;
        if (typeof sync1MSuffix === 'function') sync1MSuffix();
        $('#f-probemodel').value = formData.probe_model;
        $('#f-client_model').value = formData.client_model;
        $('#f-client_sonnet_model').value = formData.client_sonnet_model;
        $('#f-client_opus_model').value = formData.client_opus_model;
        $('#f-client_haiku_model').value = formData.client_haiku_model;
        $('#f-client_fable_model').value = formData.client_fable_model;
        $('#f-autostart').checked = formData.auto_start;
        $('#f-format').value = formData.upstream_format;
        $('#f-thinking').value = formData.thinking_history;
        $('#f-ccpath').value = formData.claude_config_path || '';
        $('#f-context').value = formData.context_window || '';
        $('#f-compact').value = formData.compact_window || '';
        $('#f-ttl').value = formData.prompt_cache_ttl || '';
        $('#f-subttl').value = formData.subagent_cache_ttl || '';
        $('#f-ccupstream').checked = formData.claude_config_use_upstream_key !== false;
        $('#f-autocorrect').checked = formData.thinking_history_auto_correct;
        $('#f-forcemanual').checked = formData.thinking_history_force_manual;
        renderDerived();
    }

    function readForm() {
        formData = {
            name: ($('#f-name').value || '').trim(),
            port: parseInt($('#f-port').value, 10) || 0,
            upstream_base_url: ($('#f-url').value || '').trim(),
            upstream_api_key: ($('#f-key').value || '').trim(),
            target_model: ($('#f-target').value || '').trim(),
            probe_model: ($('#f-probemodel').value || '').trim(),
            client_model: $('#f-client_model').value.trim(),
            client_sonnet_model: $('#f-client_sonnet_model').value.trim(),
            client_opus_model: $('#f-client_opus_model').value.trim(),
            client_haiku_model: $('#f-client_haiku_model').value.trim(),
            client_fable_model: $('#f-client_fable_model').value.trim(),
            auto_start: $('#f-autostart').checked,
            upstream_format: $('#f-format').value,
            thinking_history: $('#f-thinking').value,
            thinking_history_auto_correct: $('#f-autocorrect').checked,
            thinking_history_force_manual: $('#f-forcemanual').checked,
            claude_config_path: ($('#f-ccpath').value || '').trim(),
            context_window: parseInt($('#f-context').value, 10) || 0,
            compact_window: parseInt($('#f-compact').value, 10) || 0,
            prompt_cache_ttl: $('#f-ttl').value,
            subagent_cache_ttl: $('#f-subttl').value,
            claude_config_use_upstream_key: $('#f-ccupstream').checked,
        };
    }

    function isDirty() {
        if (!isEdit) {
            // New instance: the button is only meaningful once required
            // fields exist, so enable as soon as they are filled.
            return !!($('#f-name').value || '').trim()
                && !!($('#f-url').value || '').trim()
                && !!($('#f-key').value || '').trim();
        }
        readForm();
        return JSON.stringify(formData) !== JSON.stringify(originalData);
    }

    function syncSaveState() {
        const btn = $('#btnSave');
        if (!btn || state.saving) return;
        btn.disabled = !isDirty();
    }

    function renderDerived() {
        $('#portEcho').textContent = formData.port || '端口';
        $('#formatHint').textContent =
            state.formatStatus === 'detecting' ? '探测中…'
            : state.formatStatus === 'ok' ? `已检测到: ${state.detectedFormat === 'responses' ? 'Responses' : 'Chat Completions'}`
            : state.formatStatus === 'fail' ? '探测失败，已默认 Chat Completions' : '';
        $('#responsesNote').hidden = $('#f-format').value !== 'responses';
        $('#clearKeyRow').hidden = !isEdit;
        $('#keyHint').textContent = isEdit
            ? (formData.upstream_api_key ? '当前已配置，点击右侧眼睛可展开查看。输入新值可替换。' : '当前未配置密钥。')
            : '你的 OpenAI 或兼容 API 密钥';
        const p = state.probe;
        $('#probeHint').textContent =
            p.status === 'probing' ? `探测中… 已用 ${(p.elapsed || 0).toFixed(1)}s / 20s，剩余 ${(p.remaining || 0).toFixed(1)}s`
            : p.status === 'done' ? `${p.ok ? '建议：' + p.label : '未得出结论'}${p.evidence ? ' — ' + p.evidence : ''}${p.applied ? '（已应用，下一次请求生效）' : ''}`
            : p.status === 'fail' ? p.evidence : '';
        const ctx = parseInt($('#f-context').value, 10) || 0;
        const compactInput = parseInt($('#f-compact').value, 10) || 0;
        $('#ctxHint').textContent = ctx > 0
            ? `将写入 CLAUDE_CODE_MAX_CONTEXT_TOKENS = ${ctx}`
            : '';
        $('#compactHint').textContent = ctx > 0
            ? (compactInput > 0
                ? `将写入 ${compactInput}（上下文 ${(compactInput / ctx * 100).toFixed(1)}%）`
                : `未填则按 97% 自动写入 ${Math.floor(ctx * 0.97)}`)
            : (compactInput > 0 ? '需先填写上下文窗口' : '');
        $('#ccBindHint').textContent = formData.claude_config_path
            ? `当前绑定：${formData.claude_config_path}`
            : '留空则不生成配置文件';
        $('#btnUnbindCC').hidden = !formData.claude_config_path;
        $('#correctedBox').hidden = !thinkingCorrected;
        $('#correctedTo').textContent = thinkingCorrectedTo;
        $('#modelOptions').innerHTML = state.availableModels.map((m) => `<option value="${esc(m)}"></option>`).join('');
    }

    // ── edit mode: load existing ──────────────────────────────────────────
    if (isEdit) {
        try {
            const data = await api.get(`/api/instances/${instanceId}`);
            formData = {
                name: data.name || '',
                port: data.port || 0,
                upstream_base_url: data.upstream_base_url || '',
                upstream_api_key: '',
                target_model: data.target_model || '',
                probe_model: data.probe_model || '',
                client_model: data.client_model || '',
                client_sonnet_model: data.client_sonnet_model || '',
                client_opus_model: data.client_opus_model || '',
                client_haiku_model: data.client_haiku_model || '',
                client_fable_model: data.client_fable_model || '',
                auto_start: data.auto_start !== false,
                upstream_format: data.upstream_format || 'auto',
                thinking_history: data.thinking_history || 'auto',
                thinking_history_auto_correct: data.thinking_history_auto_correct !== false,
                thinking_history_force_manual: data.thinking_history_force_manual === true,
            };
            thinkingCorrected = !!data.thinking_history_corrected;
            thinkingCorrectedTo = data.thinking_history_effective_label || data.thinking_history_effective || '';
            // The instance response masks the key; fetch the real one so the
            // eye reveal shows what is actually configured.
            try {
                const kd = await api.get(`/api/instances/${instanceId}/key`);
                formData.upstream_api_key = kd.key || '';
            } catch { /* keep empty */ }
        } catch (e) {
            toast('加载实例失败: ' + e.message, 'danger');
        }
    }
    if (cleanups.dead) return;
    originalData = JSON.parse(JSON.stringify(formData));
    bindForm();
    loadingBox.hidden = true;
    formBox.hidden = false;
    if (isEdit) $('#btnEye').title = '显示/隐藏密钥';

    // ── actions ───────────────────────────────────────────────────────────
    // 1M 开关：目标模型名后拼接/剥离 [1m]。后缀必须落在目标模型上——
    // 代理把一切请求映射到它，只写在 JSON 里会被模型映射剥掉。
    function sync1MSuffix() {
        const value = $('#f-target').value || '';
        $('#btn1MSuffix').classList.toggle('ui-button--primary', value.endsWith('[1m]'));
    }
    cleanups.listen($('#f-target'), 'input', sync1MSuffix);
    $('#btn1MSuffix').addEventListener('click', () => {
        const value = ($('#f-target').value || '').trim();
        $('#f-target').value = value.endsWith('[1m]')
            ? value.slice(0, -4)
            : value + '[1m]';
        sync1MSuffix();
        renderDerived();
    });

    cleanups.listen($('#f-port'), 'input', () => { formData.port = parseInt($('#f-port').value, 10) || 0; renderDerived(); });
    // Any edit re-evaluates whether saving is meaningful.
    cleanups.listen(formBox, 'input', syncSaveState);
    cleanups.listen(formBox, 'change', syncSaveState);
    cleanups.listen($('#f-format'), 'change', renderDerived);

    $('#btnEye').addEventListener('click', () => {
        state.showKey = !state.showKey;
        $('#f-key').type = state.showKey ? 'text' : 'password';
    });
    $('#btnOverrides').addEventListener('click', () => {
        state.showModelOverrides = !state.showModelOverrides;
        $('#overridesBox').hidden = !state.showModelOverrides;
        $('#btnOverrides').textContent = state.showModelOverrides ? '收起' : '展开';
    });

    // ── Claude Code 配置 ──────────────────────────────────────────────
    for (const sel of ['#f-context', '#f-compact']) {
        cleanups.listen($(sel), 'input', () => {
            formData.context_window = parseInt($('#f-context').value, 10) || 0;
            formData.compact_window = parseInt($('#f-compact').value, 10) || 0;
            renderDerived();
        });
    }

    // 1M 快捷按钮：选中后高亮，再点取消。默认值就是 1000000。
    $('#btn1M').addEventListener('click', () => {
        const active = $('#f-context').value === '1000000';
        if (active) {
            $('#f-context').value = '';
            $('#f-compact').value = '';
        } else {
            $('#f-context').value = '1000000';
            $('#f-compact').value = '';
        }
        $('#btn1M').classList.toggle('ui-button--primary', !active);
        formData.context_window = active ? 0 : 1_000_000;
        formData.compact_window = 0;
        renderDerived();
    });

    $('#btnProbeCtx').addEventListener('click', async () => {
        const btn = $('#btnProbeCtx');
        btn.disabled = true;
        btn.textContent = '探测中…';
        try {
            const data = await api.post('/api/probe-context', {
                url: ($('#f-url').value || '').trim(),
                key: ($('#f-key').value || '').trim(),
                model: ($('#f-target').value || $('#f-probemodel').value || '').trim(),
                instance_id: instanceId,
            });
            if (data.found && data.context_length) {
                $('#f-context').value = data.context_length;
                $('#f-compact').value = '';
                formData.context_window = data.context_length;
                formData.compact_window = 0;
                $('#btn1M').classList.toggle('ui-button--primary', data.context_length === 1_000_000);
                toast(`上游返回上下文长度：${data.context_length}（字段 ${data.source}）`, 'success');
            } else {
                toast('上游未提供上下文长度，请手动填写或点「1M」', 'warning');
            }
        } catch (e) {
            toast('探测失败：' + e.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.textContent = '探测';
            renderDerived();
        }
    });

    async function refreshPreview() {
        if (!instanceId) { toast('请先保存实例，再预览配置文件', 'warning'); return; }
        readForm();
        try {
            const data = await api.get(`/api/instances/${instanceId}/claude-config`);
            const doc = { ...data.document };
            if (formData.claude_config_use_upstream_key) {
                toast('预览使用已保存的实例设置，密钥开关在保存后生效', 'warning');
            }
            $('#ccPreview').hidden = false;
            $('#ccPreview').textContent = JSON.stringify(doc, null, 2);
        } catch (e) {
            toast(e.message || '预览失败', 'danger');
        }
    }
    $('#btnPreviewCC').addEventListener('click', refreshPreview);

    $('#btnPickFile').addEventListener('click', async () => {
        try {
            const data = await api.get('/api/claude-configs');
            const available = (data.files || []).filter((f) => f.name !== 'settings.json');
            if (!available.length) {
                toast(`${data.directory} 下没有可选的配置文件`, 'warning');
                return;
            }
            showFilePicker(data.directory, available, (picked) => {
                $('#f-ccpath').value = picked.name;
                formData.claude_config_path = picked.name;
                renderDerived();
            });
        } catch (e) {
            toast(e.message || '读取配置目录失败', 'danger');
        }
    });

    $('#btnUnbindCC').addEventListener('click', async () => {
        if (!instanceId) { $('#f-ccpath').value = ''; renderDerived(); return; }
        try {
            await api.del(`/api/instances/${instanceId}/claude-config`);
            formData.claude_config_path = '';
            $('#f-ccpath').value = '';
            renderDerived();
            toast('已解除绑定（配置文件中的相关字段已清理）', 'success');
        } catch (e) {
            toast(e.message || '解除绑定失败', 'danger');
        }
    });

    $('#btnPort').addEventListener('click', async () => {
        try {
            const data = await api.post('/api/ports/suggest');
            $('#f-port').value = data.port;
            formData.port = data.port;
            renderDerived();
        } catch (e) { toast('获取端口失败: ' + e.message, 'danger'); }
    });

    $('#btnFormat').addEventListener('click', async () => {
        state.formatStatus = 'detecting';
        renderDerived();
        try {
            const data = await api.post('/api/detect-format', {
                url: ($('#f-url').value || '').trim(),
                key: ($('#f-key').value || '').trim(),
                model: ($('#f-target').value || $('#f-probemodel').value || '').trim(),
                instance_id: instanceId,
            });
            $('#f-format').value = data.format;
            state.detectedFormat = data.format;
            state.formatStatus = 'ok';
        } catch {
            state.formatStatus = 'fail';
            $('#f-format').value = 'chat_completions';
        }
        renderDerived();
    });

    $('#btnModels').addEventListener('click', async () => {
        if (state.modelsLoading) return;
        state.modelsLoading = true;
        try {
            const data = await api.post('/api/upstream-models', {
                url: ($('#f-url').value || '').trim(),
                key: ($('#f-key').value || '').trim(),
                instance_id: instanceId,
            });
            state.availableModels = data.models || [];
            toast(`获取到 ${state.availableModels.length} 个模型，可在输入框下拉选择`, 'success');
        } catch (e) {
            toast(e.message || '获取模型失败', 'danger');
        } finally {
            state.modelsLoading = false;
            renderDerived();
        }
    });

    $('#btnProbe').addEventListener('click', async () => {
        if (state.probe.status === 'probing') return;
        const BUDGET = 20, WARN_AT = 5;
        const startedAt = Date.now();
        let warned = false;
        const timer = setInterval(() => {
            const elapsed = (Date.now() - startedAt) / 1000;
            state.probe.elapsed = elapsed;
            state.probe.remaining = Math.max(0, BUDGET - elapsed);
            if (!warned && elapsed >= WARN_AT) {
                warned = true;
                toast(`上游响应较慢（已 ${WARN_AT} 秒），继续等待，最长 ${BUDGET} 秒`, 'warning');
            }
            renderDerived();
        }, 200);
        const controller = new AbortController();
        const abortTimer = setTimeout(() => controller.abort(), (BUDGET + 5) * 1000);

        state.probe = { status: 'probing', ok: false, label: '', evidence: '', applied: false, elapsed: 0, remaining: BUDGET };
        renderDerived();
        try {
            const data = await api.post('/api/probe-thinking', {
                url: ($('#f-url').value || '').trim(),
                key: ($('#f-key').value || '').trim(),
                model: ($('#f-target').value || $('#f-probemodel').value || '').trim(),
                instance_id: instanceId,
                upstream_format: $('#f-format').value,
                apply: isEdit,
            }, { signal: controller.signal });
            state.probe = {
                status: 'done', ok: !!data.ok,
                label: data.label || data.policy || '',
                evidence: data.evidence || '', applied: !!data.applied,
            };
            // Only a definite answer may change the form.
            if (data.ok && data.policy) $('#f-thinking').value = data.policy;
        } catch (e) {
            state.probe = {
                status: 'fail', ok: false, label: '', applied: false,
                evidence: e && e.name === 'AbortError' ? `探测超时（${BUDGET} 秒内没有返回）` : '探测失败: ' + e.message,
            };
        } finally {
            clearInterval(timer);
            clearTimeout(abortTimer);
            if (!cleanups.dead) renderDerived();
        }
    });

    $('#btnSave').addEventListener('click', async () => {
        // Guard synchronously: a fast double-click queues two click events
        // before the button is disabled, and each would create an instance.
        if (state.saving) return;
        readForm();
        if (!formData.name) { toast('请输入实例名称', 'danger'); return; }
        if (!isEdit && !formData.upstream_api_key) { toast('请填写 API 密钥', 'danger'); return; }
        if (!formData.upstream_base_url) { toast('请填写上游 Base URL', 'danger'); return; }

        state.saving = true;
        $('#btnSave').disabled = true;
        $('#btnSave').textContent = '保存中…';
        try {
            const body = { ...formData };
            if (body.upstream_format === 'auto') body.upstream_format = 'chat_completions'; // safe default
            if (isEdit) {
                if ($('#f-clearkey').checked) {
                    body.clear_upstream_api_key = true;
                    body.upstream_api_key = '';
                } else if (!body.upstream_api_key) {
                    delete body.upstream_api_key; // keep existing
                }
            }
            const data = isEdit
                ? await api.patch(`/api/instances/${instanceId}`, body)
                : await api.post('/api/instances', body);
            // 绑定/更新配置文件（失败不应影响实例本身的保存结果）
            const wantedPath = ($('#f-ccpath').value || '').trim();
            let ccError = '';
            if (wantedPath && data && data.id) {
                try {
                    await api.post(`/api/instances/${data.id}/claude-config`, {
                        path: wantedPath,
                        create: true,
                    });
                } catch (e) {
                    ccError = e.message || '写入配置文件失败';
                }
            }
            const warnings = Array.isArray(data?.warnings) ? data.warnings : [];
            // 无论有无告警都回到主界面；告警以悬浮 toast 呈现（右下角，长驻留），
            // 不再用常驻内嵌横幅——保存后的注意力应该落在列表页，而不是表单顶部。
            if (ccError) {
                toast('实例已保存，但配置文件写入失败：' + ccError, 'warning', 10000);
            } else if (warnings.length) {
                toast('已保存，但有需要注意的问题：' + warnings.join('；'), 'warning', 10000);
            } else {
                toast(isEdit ? '实例已更新' : '实例已创建', 'success');
            }
            setTimeout(() => { window.location.hash = '#/'; }, 400);
        } catch (e) {
            toast(e.message || '保存失败', 'danger');
        } finally {
            state.saving = false;
            $('#btnSave').disabled = false;
            $('#btnSave').textContent = '保存';
        }
    });

    $('#btnReset').addEventListener('click', () => {
        formData = JSON.parse(JSON.stringify(originalData));
        bindForm();
        toast('已重置', 'default');
    });
    $('#btnBack').addEventListener('click', () => { window.location.hash = '#/'; });
}
