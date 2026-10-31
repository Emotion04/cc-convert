// Desktop UI — hash router + app shell wiring.
// Pages are lazy-imported modules that render into #view and return a cleanup.

import { createToastStack, bindDialog, bindSheet, setTheme } from '/static/desktop/vendor/universal-ui/vanilla.js';
import { api } from './api.js';

const toasts = createToastStack({ container: document.getElementById('toastStack') });
export function toast(message, tone = 'default') {
    toasts.push(message, { tone });
}

const routes = {
    '/': { title: '仪表板', subtitle: '监控和管理你的 CC Convert 代理实例', load: () => import('./pages/dashboard.js') },
    '/config': { title: '实例配置', subtitle: '创建或编辑一个代理实例', load: () => import('./pages/config.js') },
    '/usage': { title: '用量统计', subtitle: 'Token 用量与缓存命中', load: () => import('./pages/usage.js') },
    '/ports': { title: '端口管理', subtitle: '诊断与修复端口占用', load: () => import('./pages/ports.js') },
    '/logs': { title: '日志查看', subtitle: '查看 Manager 与实例日志', load: () => import('./pages/logs.js') },
    '/api-test': { title: 'API 测试', subtitle: '向实例端点发送测试请求', load: () => import('./pages/apitest.js') },
    '/global': { title: '全局设置', subtitle: '日志、搜索服务与代理参数', load: () => import('./pages/global.js') },
};

let cleanup = null;
let currentPath = null;

export async function navigate() {
    const hash = window.location.hash.replace(/^#/, '') || '/';
    const [path, query] = hash.split('?');
    const route = routes[path] || routes['/'];
    const params = new URLSearchParams(query || '');

    // Rerunning the same route (e.g. clicking the active nav item) is a no-op.
    if (path === currentPath && path !== '/config') return;
    currentPath = path;

    document.getElementById('pageTitle').textContent = route.title;
    document.getElementById('pageSubtitle').textContent = route.subtitle;
    document.getElementById('pageActions').innerHTML = '';

    document.querySelectorAll('#sideNav .ui-nav-item, #sheetNav .ui-nav-item').forEach((btn) => {
        if (btn.dataset.route === path) btn.setAttribute('aria-current', 'page');
        else btn.removeAttribute('aria-current');
    });

    if (cleanup) { cleanup.kill(); cleanup = null; }
    const view = document.getElementById('view');
    view.innerHTML = '<div class="ui-spinner" role="status" aria-label="加载中"></div>';

    const mod = await route.load();
    const c = new (await import('./helpers.js')).Cleanups();
    try {
        cleanup = c;
        await mod.render(view, params, { toast, cleanups: c, navigate });
    } catch (e) {
        view.innerHTML = `<div class="ui-alert ui-alert--danger">页面加载失败：${e.message}</div>`;
    }
    document.getElementById('navSheet').close?.();
}

function wireShell() {
    for (const nav of [document.getElementById('sideNav'), document.getElementById('sheetNav')]) {
        nav.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-route]');
            if (!btn) return;
            window.location.hash = '#' + btn.dataset.route;
        });
    }
    window.addEventListener('hashchange', navigate);

    const themeToggle = document.getElementById('themeToggle');
    themeToggle.addEventListener('click', () => {
        const dark = document.documentElement.dataset.theme === 'dark';
        setTheme(dark ? 'light' : 'dark');
    });

    bindSheet(document.getElementById('navSheet'), {
        trigger: document.getElementById('navTrigger'),
    });

    updateManagerStatus();
    setInterval(updateManagerStatus, 30000);
}

async function updateManagerStatus() {
    const el = document.getElementById('managerStatus');
    try {
        const data = await api.get('/api/manager');
        el.innerHTML = `<span class="ui-status__dot" style="background:#10b981"></span>Manager ${data.instance_count} 实例 · ${data.host}:${data.port}`;
    } catch {
        el.innerHTML = '<span class="ui-status__dot" style="background:#ef4444"></span>Manager 离线';
    }
}

wireShell();
navigate();
