// sw.js — PWA Service Worker
// 预缓存全部静态资源，cache-first 策略

const CACHE_NAME = "checkin-helper-v1";

// 预缓存清单：所有静态资源（不含 API 端点）
const CACHE_ASSETS = [
  // HTML 页面
  "/",
  "/login",
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

// 请求阶段：缓存优先，网络回退
self.addEventListener("fetch", (event) => {
  // 不缓存 API 请求（动态内容）
  if (event.request.url.includes("/api/")) {
    return;
  }

  event.respondWith(
    (async () => {
      const cached = await caches.match(event.request);
      if (cached) {
        return cached;
      }
      try {
        const response = await fetch(event.request);
        // 可选：将新请求的响应加入缓存（网络优先资源的渐进增强）
        if (response.ok) {
          const cache = await caches.open(CACHE_NAME);
          cache.put(event.request, response.clone());
        }
        return response;
      } catch {
        // 网络离线且缓存未命中 — 返回离线占位
        return new Response("离线中，请检查网络连接", {
          status: 503,
          statusText: "Service Unavailable",
        });
      }
    })()
  );
});
