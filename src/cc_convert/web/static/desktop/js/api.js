// Desktop UI — API layer.
// Same-origin fetch with the manager's CSRF header; all business logic lives
// behind /api/* (see docs: the desktop UI must not reimplement any of it).

export function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
}

export class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

async function request(method, url, body, options = {}) {
    const headers = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const token = getCsrfToken();
    if (token) headers['X-CCCONVERT-CSRF'] = token;
    if (options.signal) headers['AbortSignal'] = undefined;

    let response;
    try {
        response = await fetch(url, {
            method,
            headers,
            body: body !== undefined ? JSON.stringify(body) : undefined,
            signal: options.signal,
        });
    } catch (e) {
        if (e && e.name === 'AbortError') throw e;
        throw new ApiError(0, '无法连接 Manager（' + e.message + '）');
    }

    let data = null;
    try { data = await response.json(); } catch { /* empty body */ }

    if (!response.ok) {
        // The manager rotates the CSRF token on every restart; a page left
        // open across a restart gets a 403 until it is reloaded.
        if (response.status === 403 && data && String(data.detail || '').includes('CSRF')) {
            throw new ApiError(403, '页面已过期（Manager 重启过），请按 Ctrl+R 刷新后重试');
        }
        const detail = (data && (data.detail || (data.error && data.error.message))) || `HTTP ${response.status}`;
        throw new ApiError(response.status, detail);
    }
    return data;
}

export const api = {
    get: (url, options) => request('GET', url, undefined, options),
    post: (url, body, options) => request('POST', url, body ?? {}, options),
    patch: (url, body, options) => request('PATCH', url, body, options),
    del: (url, options) => request('DELETE', url, undefined, options),
};
