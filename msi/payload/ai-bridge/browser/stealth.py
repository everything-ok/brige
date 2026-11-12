"""反检测脚本"""

STEALTH_JS = """
// 隐藏 webdriver
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
});

delete navigator.__proto__.webdriver;

window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {},
};

const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);

Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
        { name: 'Native Client', filename: 'internal-nacl-plugin' },
    ],
});

Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
});

Object.defineProperty(navigator, 'platform', {
    get: () => 'Win32',
});

Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8,
});

Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8,
});

const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) {
        return 'Intel Inc.';
    }
    if (parameter === 37446) {
        return 'Intel Iris OpenGL Engine';
    }
    return getParameter.apply(this, arguments);
};

Date.prototype.getTimezoneOffset = function() { return -480; };

try {
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function() {
            return window;
        }
    });
} catch (e) {}

// ── 真无头（headless=True）补强 ──────────────────────────────
// 新版 Chrome 的 headless 会带 --headless=new 特征，
// 站点 JS 可通过 UA 里的 "HeadlessChrome" / window.outerWidth 等识别。
// 这里把 UA 改回正常 Chrome，并在 navigator 上补齐 headless 缺失的 API。
try {
    const ua = navigator.userAgent;
    if (ua.indexOf('HeadlessChrome') !== -1) {
        Object.defineProperty(navigator, 'userAgent', {
            get: () => ua.replace(/HeadlessChrome/g, 'Chrome'),
            configurable: true,
        });
        Object.defineProperty(navigator, 'appVersion', {
            get: () => navigator.userAgent.replace(/^Mozilla\//, ''),
            configurable: true,
        });
    }
} catch (e) {}

// 无头模式下 window.outerWidth/outerHeight 与 screen.width 都退化成 viewport 尺寸，
// 特征明显（真机屏幕宽 ≠ 视口宽）。这里统一补成合理的桌面尺寸。
try {
    if (!window.outerWidth || window.outerWidth === 0) {
        Object.defineProperty(window, 'outerWidth', {get: () => window.innerWidth, configurable: true});
        Object.defineProperty(window, 'outerHeight', {get: () => window.innerHeight, configurable: true});
    }
    if (!window.screen.width || window.screen.width === 0
            || window.screen.width === window.innerWidth) {
        Object.defineProperty(window.screen, 'width', {get: () => 1920, configurable: true});
        Object.defineProperty(window.screen, 'height', {get: () => 1080, configurable: true});
        Object.defineProperty(window.screen, 'availWidth', {get: () => 1920, configurable: true});
        Object.defineProperty(window.screen, 'availHeight', {get: () => 1040, configurable: true});
        Object.defineProperty(window.screen, 'colorDepth', {get: () => 24, configurable: true});
        Object.defineProperty(window.screen, 'pixelDepth', {get: () => 24, configurable: true});
    }
} catch (e) {}

// 无头模式缺少 window.chrome.csi / loadTimes 之外的 app 细节
try {
    if (!navigator.webdriver) {
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined, configurable: true});
    }
} catch (e) {}
"""
