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
"""
