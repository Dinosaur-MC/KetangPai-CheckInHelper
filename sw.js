// sw.js — PWA Service Worker
// 预缓存全部静态资源，cache-first 策略

const CACHE_NAME = "checkin-helper-v1";

// 预缓存清单：所有静态资源（不含 API 端点）
const CACHE_ASSETS = [
  // 注意：不预缓存 HTML 页面（/ 和 /login），
  // 因为 / 在未登录时返回 302 重定向，会导致 cache.addAll() 失败。
  // 这两个页面由 fetch 处理器的 cache-first 策略在首次访问时自然缓存。
  // 应用 CSS
  "/static/common.css",
  "/static/index.css",
  "/static/login.css",
  // 应用 JS
  "/static/index.js",
  "/static/login.js",
  // UI 框架
  "/static/mdui.css",
  "/static/mdui.global.js",
  "/static/vue.global.prod.js",
  // 字体
  "/static/material-icons.css",
  "/static/MaterialIcons-Regular.ttf",
  // 二维码识别引擎（大文件）
  "/static/opencv.js",
  "/static/wechat_qrcode_files.js",
  "/static/wechat_qrcode_files.data",
  "/static/zxing.min.js",
  // PWA 图标
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  // 背景图
  "/static/img(32).webp",
  "/static/img(64).webp",
  // 图标
  "/favicon.ico",
];

// 安装阶段：预缓存所有资源
self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      await cache.addAll(CACHE_ASSETS);
      await self.skipWaiting();
    })()
  );
});

// 激活阶段：清理旧缓存，立即接管页面
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      );
      await self.clients.claim();
    })()
  );
});

// ── 文件分类 ──
// 应用自身代码（频繁更新）→ stale-while-revalidate
const _APP_ASSETS = new Set([
  "/static/common.css",
  "/static/index.css",
  "/static/login.css",
  "/static/index.js",
  "/static/login.js",
  "/favicon.ico",
]);

function _isApi(url) { return url.includes("/api/"); }
function _isAppAsset(url) { return _APP_ASSETS.has(new URL(url).pathname); }

function _serverUnavailable() {
  return new Response("离线中，请检查网络连接", {
    status: 503, statusText: "Service Unavailable",
  });
}

// stale-while-revalidate：有缓存立即返回并在后台拉取最新版；无缓存则等待网络
async function _staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  // 不论有无缓存，后台都发起网络请求更新缓存
  const updateCache = fetch(request).then((response) => {
    if (response.ok) cache.put(request, response.clone());
  }).catch(() => {});
  if (cached) return cached;  // 有缓存 → 立即返回
  await updateCache;          // 无缓存 → 等网络
  const now = await cache.match(request);
  if (now) return now;
  // 离线且无缓存 → 返回 503
  try { return await fetch(request); } catch { return _serverUnavailable(); }
}

// cache-first：缓存命中直接返回，未命中则请求网络
async function _cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      await cache.put(request, response.clone());
    }
    return response;
  } catch { return _serverUnavailable(); }
}

// 请求阶段：按文件类型选择策略
self.addEventListener("fetch", (event) => {
  // API 请求不缓存
  if (_isApi(event.request.url)) return;

  // 应用自身代码 → stale-while-revalidate（每次访问都在后台检查更新）
  if (_isAppAsset(event.request.url)) {
    event.respondWith(_staleWhileRevalidate(event.request));
    return;
  }

  // 其他资源（大库/字体/图标/图片）→ cache-first
  event.respondWith(_cacheFirst(event.request));
});
