// Shared render helpers for the desktop UI pages.

export function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

export const STATUS_LABELS = { running: '运行中', stopped: '已停止', error: '异常', unknown: '未知' };

export function statusLabel(status) {
    return STATUS_LABELS[status] || status;
}

export function statusDot(status) {
    const cls = status === 'running' ? 'running' : status === 'error' ? 'error' : 'stopped';
    return `<span class="desktop-dot desktop-dot--${cls}"></span>`;
}

export function statusBadge(status) {
    const tone = status === 'running' ? 'success' : status === 'error' ? 'danger' : 'default';
    return `<span class="ui-badge ui-badge--${tone}">${esc(statusLabel(status))}</span>`;
}

export function formatUptime(seconds) {
    if (!seconds) return '0s';
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
}

export function truncateUrl(url) {
    if (!url) return '';
    try {
        const u = new URL(url);
        return u.hostname + (u.port ? ':' + u.port : '');
    } catch {
        return url.length > 30 ? url.substring(0, 30) + '…' : url;
    }
}

/** Client config snippets (same content as the old dashboard popover). */
export function modelOverrides(inst) {
    const out = [];
    if (inst.client_model) out.push(`ANTHROPIC_MODEL=${inst.client_model}`);
    if (inst.client_sonnet_model) out.push(`ANTHROPIC_DEFAULT_SONNET_MODEL=${inst.client_sonnet_model}`);
    if (inst.client_opus_model) out.push(`ANTHROPIC_DEFAULT_OPUS_MODEL=${inst.client_opus_model}`);
    if (inst.client_haiku_model) out.push(`ANTHROPIC_DEFAULT_HAIKU_MODEL=${inst.client_haiku_model}`);
    return out;
}

export function psCmd(inst) {
    let s = `$env:ANTHROPIC_BASE_URL="http://127.0.0.1:${inst.port}"\n$env:ANTHROPIC_AUTH_TOKEN="local"`;
    modelOverrides(inst).forEach((m) => { s += `\n$env:` + m.replace('=', '="') + '"'; });
    return s;
}

export function cmdCmd(inst) {
    let s = `set ANTHROPIC_BASE_URL=http://127.0.0.1:${inst.port}\nset ANTHROPIC_AUTH_TOKEN=local`;
    modelOverrides(inst).forEach((m) => { s += '\nset ' + m; });
    return s;
}

export function bashCmd(inst) {
    let s = `export ANTHROPIC_BASE_URL="http://127.0.0.1:${inst.port}"\nexport ANTHROPIC_AUTH_TOKEN="local"`;
    modelOverrides(inst).forEach((m) => {
        const idx = m.indexOf('=');
        s += `\nexport ${m.slice(0, idx)}="${m.slice(idx + 1)}"`;
    });
    return s;
}

export function settingsJson(inst) {
    const map = {
        ANTHROPIC_MODEL: inst.client_model,
        ANTHROPIC_DEFAULT_SONNET_MODEL: inst.client_sonnet_model,
        ANTHROPIC_DEFAULT_OPUS_MODEL: inst.client_opus_model,
        ANTHROPIC_DEFAULT_HAIKU_MODEL: inst.client_haiku_model,
    };
    const entries = Object.entries(map).filter(([, v]) => v);
    if (!entries.length) return '';
    const body = entries.map(([k, v]) => `    "${k}": ${JSON.stringify(v)}`).join(',\n');
    return `{\n${body}\n}`;
}

/** Attach a listener that is removed automatically by the page router. */
export class Cleanups {
    /** Set by the router when the page's DOM has been replaced. Async
     * continuations must check this before touching the (detached) view. */
    dead = false;
    constructor() { this.fns = []; }
    kill() { this.dead = true; this.dispose(); }
    add(fn) { this.fns.push(fn); }
    listen(target, event, handler, options) {
        target.addEventListener(event, handler, options);
        this.fns.push(() => target.removeEventListener(event, handler, options));
    }
    interval(fn, ms) {
        const t = setInterval(fn, ms);
        this.fns.push(() => clearInterval(t));
    }
    dispose() { this.fns.forEach((fn) => { try { fn(); } catch { /* ignore */ } }); this.fns = []; }
}
