const { createApp, reactive, ref, computed, watch } = Vue;

const API_BASE = ""; // 同域

// ---- HTTP 工具 ----
let _refreshing = false;

async function api(method, path, body) {
    const headers = { "Content-Type": "application/json" };
    const token = localStorage.getItem("token");
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const opts = { method, headers };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(`${API_BASE}${path}`, opts);
    const data = await res.json();
    if (res.ok) return data;

    // 401 时尝试刷新令牌（跳过 auth 接口自身）
    if (res.status === 401 && !["/api/refresh", "/api/login"].includes(path) && !_refreshing) {
        _refreshing = true;
        try {
            const rt = localStorage.getItem("refresh_token");
            if (!rt) throw new Error("no refresh token");
            const rr = await fetch(`${API_BASE}/api/refresh`, {
                method: "POST",
                headers: { Authorization: `Bearer ${rt}`, "Content-Type": "application/json" },
            });
            const rd = await rr.json();
            if (!rr.ok) throw new Error(rd.message || "refresh failed");
            // 更新 localStorage
            localStorage.setItem("token", rd.data.access_token);
            localStorage.setItem("refresh_token", rd.data.refresh_token);
            localStorage.setItem("user", JSON.stringify(rd.data.user));
            _refreshing = false;
            // 重试原始请求
            headers["Authorization"] = `Bearer ${rd.data.access_token}`;
            const retry = await fetch(`${API_BASE}${path}`, { method, headers, body: opts.body });
            const retryData = await retry.json();
            if (!retry.ok) throw new Error(retryData.message || `请求失败 (${retry.status})`);
            return retryData;
        } catch (e) {
            _refreshing = false;
            localStorage.removeItem("token");
            localStorage.removeItem("refresh_token");
            localStorage.removeItem("user");
            throw new Error("登录已过期，请重新登录");
        }
    }
    _refreshing = false;
    throw new Error(data.message || `请求失败 (${res.status})`);
}

// ---- Vue 应用 ----
createApp({
    setup() {
        const state = reactive({
            token: localStorage.getItem("token") || null,
            currentUser: JSON.parse(localStorage.getItem("user") || "null"),
        });

        // hash 路由：从 URL 恢复状态，支持刷新保持页面
        function hashFromURL() {
            return window.location.hash.replace(/^#\/?/, "") || "dashboard";
        }
        const route = ref(hashFromURL());
        const isMobile = () => window.innerWidth <= 768;
        const sidebarOpen = ref(!isMobile());
        // 窗口尺寸变化时自动调整侧栏
        window.addEventListener("resize", () => {
            sidebarOpen.value = !isMobile();
        });
        const loading = ref(false);

        // 密码显示切换
        const showLoginPwd = ref(false);
        const showRegPwd = ref(false);
        const showAcctPwd = ref(false);
        const showUserPwd = ref(false);
        const showChangeOldPwd = ref(false);
        const showChangeNewPwd = ref(false);
        const showChangeConfirmPwd = ref(false);

        // 表单
        const form = reactive({ email: "", password: "", invite_code: "" });
        const accountForm = reactive({ email: "", password: "" });
        const bindingForm = reactive({ course_id: "", account_id: "" });
        const userForm = reactive({ email: "", password: "", role: "user", is_active: true });
        const inviteForm = reactive({ code: "", max_uses: null, expires_in: null, note: "" });
        const checkinForm = reactive({ courseid: "", ticketid: "", expire: "", sign: "" });
        const checkinUrl = ref("");
        const gpsCheckinForm = reactive({ courseid: "", id: "" });
        // 自动签到
        const autoEnabled = ref(false);
        const autoTypeDigit = ref(true);
        const autoTypeGps = ref(true);
        const autoTimeWindows = ref([]);
        const autoConfig = ref(null);
        const autoStatus = ref(null);
        const autoSaving = ref(false);
        const logFilter = reactive({
            course_id: "",
            account_email: null,
            status: null,
            date_from: null,
            date_to: null,
        });

        // 列表
        const accounts = ref([]);
        const bindings = ref([]);
        const courses = ref([]); // Course 表缓存
        const logs = ref([]);
        const inviteCodes = ref([]);
        const inviteRequired = ref(false);
        const adminAccounts = ref([]);
        const editingInvite = ref(null);
        const users = ref([]);
        const recentLogs = computed(() => logs.value.slice(0, 10));

        // 分页状态
        const PAGE_SIZE = 20;
        const logPage = ref(1);
        const logTotal = ref(0);
        const userPage = ref(1);
        const userTotal = ref(0);
        const adminAcctPage = ref(1);
        const adminAcctTotal = ref(0);
        const invitePage = ref(1);
        const inviteTotal = ref(0);

        const logTotalPages = computed(() => Math.ceil(logTotal.value / PAGE_SIZE) || 1);
        const userTotalPages = computed(() => Math.ceil(userTotal.value / PAGE_SIZE) || 1);
        const adminAcctTotalPages = computed(() => Math.ceil(adminAcctTotal.value / PAGE_SIZE) || 1);
        const inviteTotalPages = computed(() => Math.ceil(inviteTotal.value / PAGE_SIZE) || 1);

        // 弹窗
        const modals = reactive({
            account: false,
            binding: false,
            user: false,
            invite: false,
            scanner: false,
            changePwd: false,
        });
        const editingAccount = ref(null);
        const editingUser = ref(null);

        // 修改密码
        const changePwdForm = reactive({ old_password: "", new_password: "", confirm: "" });

        // 签到
        const checkinLoading = ref(false);
        const checkinResults = ref([]);
        const gpsCheckinLoading = ref(false);
        const gpsCheckinResults = ref([]);

        // 扫码
        const scanOpening = ref(false);
        const scanLiveMode = ref(false);
        const scanFail = ref("");
        const scanPhotoSrc = ref(null);
        const scanVideo = ref(null);
        const scanCanvas = ref(null);
        const scanPhotoInput = ref(null);
        let scanStream = null;
        let scanTimer = null;
        let lastScannedText = "";
        let lastScannedTime = 0;

        // ---- 计算属性 ----
        const pageTitle = computed(
            () =>
                ({
                    dashboard: "首页",
                    accounts: "账号管理",
                    courses: "课程绑定",
                    checkin: "签到",
                    logs: "签到日志",
                    users: "用户管理",
                })[route.value] || "",
        );

        const pageSubtitle = computed(
            () =>
                ({
                    dashboard: "概览",
                    accounts: "管理课堂派账号",
                    courses: "绑定课程与账号",
                    checkin: "执行批量签到",
                    logs: "查看历史记录",
                    users: "管理平台用户",
                })[route.value] || "",
        );


        const stats = computed(() => {
            const today = new Date();
            return {
                accountCount: accounts.value.length,
                bindingCount: bindings.value.length,
                todayCheckins: logs.value.filter((l) => {
                    if (!l.created_at) return false;
                    const d = new Date(l.created_at);
                    return (
                        d.getDate() === today.getDate() &&
                        d.getMonth() === today.getMonth() &&
                        d.getFullYear() === today.getFullYear()
                    );
                }).length,
                totalLogs: logTotal.value || logs.value.length,
            };
        });

        const checkinSuccessCount = computed(() => checkinResults.value.filter((r) => r.success).length);
        const gpsCheckinSuccessCount = computed(() => gpsCheckinResults.value.filter((r) => r.success).length);

        // ---- Toast (MDUI 2 snackbar) ----
        function showToast(msg, delay) {
            try {
                mdui.snackbar({ message: msg, autoCloseDelay: delay || 1500 });
            } catch {
                /* fallback */
            }
        }

        // ---- 工具 ----
        function copyText(text) {
            if (navigator.clipboard) {
                navigator.clipboard.writeText(text).then(
                    () => showToast("已复制"),
                    () => {},
                );
            }
        }
        // 邀请码三种状态辅助
        function codeIsExpired(c) {
            if (c.max_uses !== null && c.max_uses !== undefined && c.used_count >= c.max_uses) return true;
            if (c.expires_at && new Date(c.expires_at) < new Date()) return true;
            return false;
        }
        function codeStatusClass(c) {
            if (codeIsExpired(c)) return "red";
            if (!c.is_active) return "orange";
            return "green";
        }
        function codeStatusText(c) {
            if (codeIsExpired(c)) return "失效";
            if (!c.is_active) return "停用";
            return "有效";
        }
        function formatTime(ts) {
            if (!ts) return "-";
            try {
                // 服务端时间无时区后缀 → 视为 UTC，补 Z 转换为本地时间
                const src =
                    typeof ts === "string" &&
                    ts.indexOf("T") > 0 &&
                    /[+-]\d{2}:\d{2}$/.test(ts) === false &&
                    ts.slice(-1) !== "Z"
                        ? ts + "Z"
                        : ts;
                const d = new Date(src);
                if (isNaN(d.getTime())) return ts;
                return d.toLocaleString("zh-CN", { hour12: false });
            } catch {
                return ts;
            }
        }

        function getAccountEmail(id) {
            const a = accounts.value.find((a) => a.id === id);
            if (!a) return `#${id}`;
            return a.username || a.email;
        }

        function getCourseName(courseId) {
            const c = courses.value.find((c) => c.id === courseId);
            return c ? c.course_name : courseId;
        }

        function getCourseSemester(courseId) {
            const c = courses.value.find((c) => c.id === courseId);
            return c ? `${c.semester}·${c.term === "1" ? "上学期" : c.term === "2" ? "下学期" : c.term}` : "";
        }

        function getCourseCode(courseId) {
            const c = courses.value.find((c) => c.id === courseId);
            return c ? c.code : "";
        }

        // ---- 导航 ----
        function navigate(to) {
            if (route.value === to) return;
            if (modals.scanner) stopScanner();
            route.value = to;
            window.location.hash = "#" + to;
            if (isMobile()) sidebarOpen.value = false;
            if (to === "register") loadInviteRequired();
            if (to !== "login" && to !== "register") loadPageData(to);
        }

        // ---- 认证 ----
        async function login() {
            if (!form.email || !form.password) {
                showToast("请填写邮箱和密码");
                return;
            }
            loading.value = true;
            try {
                const res = await api("POST", "/api/login", { email: form.email, password: form.password });
                state.token = res.data.access_token;
                state.currentUser = res.data.user;
                localStorage.setItem("token", res.data.access_token);
                localStorage.setItem("refresh_token", res.data.refresh_token);
                localStorage.setItem("user", JSON.stringify(res.data.user));
                route.value = "dashboard";
                window.location.hash = "#dashboard";
                showToast("登录成功");
                loadPageData("dashboard");
            } catch (e) {
                showToast(e.message || "登录失败");
            } finally {
                loading.value = false;
            }
        }

        async function register() {
            if (!form.email || !form.password) {
                showToast("请填写邮箱和密码");
                return;
            }
            loading.value = true;
            try {
                const res = await api("POST", "/api/register", {
                    email: form.email,
                    password: form.password,
                    invite_code: form.invite_code,
                });
                state.token = res.data.access_token;
                state.currentUser = res.data.user;
                localStorage.setItem("token", res.data.access_token);
                localStorage.setItem("refresh_token", res.data.refresh_token);
                localStorage.setItem("user", JSON.stringify(res.data.user));
                route.value = "dashboard";
                window.location.hash = "#dashboard";
                showToast("注册成功");
                loadPageData("dashboard");
            } catch (e) {
                showToast(e.message || "注册失败");
            } finally {
                loading.value = false;
            }
        }

        async function logout() {
            try {
                await api("POST", "/api/logout");
            } catch {}
            state.token = null;
            state.currentUser = null;
            localStorage.removeItem("token");
            localStorage.removeItem("refresh_token");
            localStorage.removeItem("user");
            window.location.reload();
        }

        // ---- 账号 ----
        let _accountsPromise = null;
        function invalidateAccounts() {
            _accountsPromise = null;
        }
        const _LARGE_PAGE = "page=1&page_size=200";
        async function loadAccounts() {
            if (_accountsPromise) return _accountsPromise;
            _accountsPromise = (async () => {
                try {
                    const res = await api("GET", `/api/accounts?${_LARGE_PAGE}`);
                    accounts.value = res.data || [];
                } catch (e) {
                    showToast(e.message);
                }
            })();
            return _accountsPromise;
        }

        function openAccountModal(acct) {
            editingAccount.value = acct || null;
            accountForm.email = acct ? acct.email : "";
            accountForm.password = "";
            modals.account = true;
        }

        async function saveAccount() {
            if (!accountForm.email) {
                showToast("请填写课堂派账号");
                return;
            }
            try {
                if (editingAccount.value) {
                    const body = { email: accountForm.email };
                    if (accountForm.password) body.password = accountForm.password;
                    await api("PUT", `/api/accounts/${editingAccount.value.id}`, body);
                    showToast("账号已更新");
                    invalidateAccounts();
                } else {
                    if (!accountForm.password) {
                        showToast("请填写密码");
                        return;
                    }
                    await api("POST", "/api/accounts", { email: accountForm.email, password: accountForm.password });
                    showToast("账号已添加");
                    invalidateAccounts();
                }
                modals.account = false;
                loadAccounts();
            } catch (e) {
                showToast(e.message);
            }
        }

        async function verifyAccount(acct) {
            try {
                const res = await api("POST", `/api/accounts/${acct.id}/verify`);
                showToast(res.message);
                invalidateAccounts();
                loadAccounts();
            } catch (e) {
                showToast(e.message);
                invalidateAccounts();
                loadAccounts();
            }
        }

        async function deleteAccount(acct) {
            if (!confirm(`确定删除账号 ${acct.email}？`)) return;
            try {
                await api("DELETE", `/api/accounts/${acct.id}`);
                showToast("账号已删除");
                invalidateAccounts();
                loadAccounts();
            } catch (e) {
                showToast(e.message);
            }
        }

        // ---- 课程绑定 ----
        async function loadBindings() {
            try {
                const [bRes, cRes] = await Promise.all([
                    api("GET", `/api/courses/bindings?${_LARGE_PAGE}`),
                    api("GET", `/api/courses?${_LARGE_PAGE}`),
                ]);
                bindings.value = bRes.data || [];
                courses.value = cRes.data || [];
            } catch (e) {
                showToast(e.message);
            }
        }

        function openBindingModal() {
            if (accounts.value.length === 0) {
                showToast("请先添加课堂派账号");
                return;
            }
            bindingForm.course_id = "";
            bindingForm.account_id = accounts.value[0]?.id || "";
            modals.binding = true;
        }

        async function saveBinding() {
            if (!bindingForm.course_id || !bindingForm.account_id) {
                showToast("请填写完整信息");
                return;
            }
            try {
                await api("POST", "/api/courses/bindings", {
                    course_id: bindingForm.course_id,
                    account_id: bindingForm.account_id,
                });
                showToast("课程绑定成功");
                modals.binding = false;
                loadBindings();
            } catch (e) {
                showToast(e.message);
            }
        }

        async function deleteBinding(b) {
            if (!confirm(`确定解绑课程 ${b.course_id}？`)) return;
            try {
                await api("DELETE", `/api/courses/bindings/${b.id}`);
                showToast("课程已解绑");
                loadBindings();
            } catch (e) {
                showToast(e.message);
            }
        }

        async function toggleBinding(b, event) {
            const target = event.target;
            const newState = target.checked;
            try {
                await api("PUT", `/api/courses/bindings/${b.id}`, { is_active: newState });
                b.is_active = newState;
                showToast(newState ? "已启用" : "已禁用");
            } catch (e) {
                target.checked = !newState; // 回滚 DOM 状态
                showToast(e.message);
            }
        }

        // ---- 签到 ----
        function parseCheckinUrl(url) {
            checkinUrl.value = url;
            if (!url) return;
            try {
                const parsed = new URL(url);
                const params = parsed.searchParams;
                const ticketid = params.get("ticketid");
                if (!ticketid) return; // 不是有效的签到链接
                checkinForm.ticketid = ticketid;
                checkinForm.expire = params.get("expire") || "";
                checkinForm.sign = params.get("sign") || "";
                checkinForm.courseid = params.get("courseid") || "";
                showToast("参数已自动填充");
            } catch {
                // 不是合法 URL，忽略
            }
        }

        // 验证签到 URL 并自动执行（不处理 UI 提示，由调用方负责）
        function validateAndExecuteQR(text) {
            try {
                const url = new URL(text);
                if (url.hostname.indexOf("ketangpai.com") === -1 || url.pathname.indexOf("checkIn") === -1) {
                    return false;
                }
                const p = url.searchParams;
                const ticketid = p.get("ticketid");
                const expire = p.get("expire");
                const sign = p.get("sign");
                const courseid = p.get("courseid");
                if (!ticketid || !expire || !sign || !courseid) {
                    return false;
                }
                checkinForm.courseid = courseid;
                checkinForm.ticketid = ticketid;
                checkinForm.expire = expire;
                checkinForm.sign = sign;
                checkinUrl.value = text;
                if (route.value !== "checkin") navigate("checkin");
                Vue.nextTick().then(() => executeCheckin());
                return true;
            } catch {
                return false;
            }
        }

        // ---- 扫码识别 ----
        // 解码管线：OpenCV WeChat QR（主）→ ZXing（备）→ 多尺度 → 分块

        // 微信二维码引擎用的是 opencv_contrib/wechat_qrcode 模块
        // 结合深度学习进行二维码定位和识别，对畸变/模糊/小码/装饰码鲁棒性极强

        // OpenCV / WeChat QR 就绪检查
        // opencv.js + wechat_qrcode_files.js 通过 HTML 中的 <script async> 加载，
        // Module.onRuntimeInitialized 在 WASM 就绪后设 window._cvReady / window._wechatDetector。
        let _cvReady = false;
        let _wechatDetector = null;
        let _checkCount = 0;
        const _cvReadyPromise = new Promise((resolve) => {
            const check = () => {
                // 优先用 Module.onRuntimeInitialized 设置的全局变量
                if (window._cvReady && window._wechatDetector) {
                    _cvReady = true;
                    _wechatDetector = window._wechatDetector;
                    resolve(); return;
                }
                // 兜底：openCV 已加载但 onRuntimeInitialized 已跑过的情况
                if (window.cv && cv.wechat_qrcode_WeChatQRCode && !window._wechatDetector) {
                    try {
                        _wechatDetector = new cv.wechat_qrcode_WeChatQRCode(
                            "/wechat_qrcode/detect.prototxt",
                            "/wechat_qrcode/detect.caffemodel",
                            "/wechat_qrcode/sr.prototxt",
                            "/wechat_qrcode/sr.caffemodel"
                        );
                        window._wechatDetector = _wechatDetector;
                        _cvReady = true;
                        window._cvReady = true;
                        resolve(); return;
                    } catch (e) { console.warn("[WeChatQR] init error:", e); }
                }
                // 放弃：30 秒后停止轮询（opencv.js 可能未加载或初始化失败）
                _checkCount++;
                if (_checkCount > 300) {
                    console.error("[WeChatQR] 引擎加载超时（30s），WeChat QR 不可用");
                    // 不 resolve — 调用方 Promise.race 超时自行降级
                    return;
                }
                setTimeout(check, 100);
            };
            check();
        });

        // 1. WeChat QR 引擎解码（从 canvas 检测并解码 QR 码）
        async function _decodeWithOpenCV(canvas) {
            if (!_cvReady || !_wechatDetector) return null;
            try {
                const src = cv.imread(canvas);
                const pointsVec = new cv.MatVector();
                const results = _wechatDetector.detectAndDecode(src, pointsVec);
                src.delete();
                pointsVec.delete();
                let text = null;
                if (results.size() > 0) text = results.get(0);
                results.delete();
                if (text && text.length > 0) return { data: text };
            } catch (e) { console.warn("[WeChatQR]", e); }
            return null;
        }

        // 2. ZXing 备选（灰度+对比度拉伸+锐化 → Hybrid/GlobalHistogram 二值化）
        function _lumPreprocess(rgba, w, h) {
            const n = w * h;
            const l = new Uint8ClampedArray(n);
            let mn = 255, mx = 0;
            for (let i = 0; i < n; i++) {
                const p = i * 4, v = rgba[p] * 0.2126 + rgba[p + 1] * 0.7152 + rgba[p + 2] * 0.0722;
                l[i] = Math.round(v);
                if (v < mn) mn = v;
                if (v > mx) mx = v;
            }
            const rng = mx - mn;
            if (rng > 15 && rng < 240) { const s = 255 / rng; for (let i = 0; i < n; i++) l[i] = (l[i] - mn) * s; }
            const sh = new Uint8ClampedArray(n);
            for (let y = 1; y < h - 1; y++) {
                const r = y * w, ru = (y - 1) * w, rd = (y + 1) * w;
                for (let x = 1; x < w - 1; x++) {
                    const v = (l[r + x] * 5) - l[ru + x] - l[rd + x] - l[r + x - 1] - l[r + x + 1];
                    sh[r + x] = v < 0 ? 0 : v > 255 ? 255 : v;
                }
                sh[r] = l[r]; sh[r + w - 1] = l[r + w - 1];
            }
            for (let x = 0; x < w; x++) { sh[x] = l[x]; sh[(h - 1) * w + x] = l[(h - 1) * w + x]; }
            return sh;
        }
        function _decodeZXing(lum, w, h, globalBin) {
            if (!window.ZXing) return null;
            try {
                const src = new ZXing.RGBLuminanceSource(lum, w, h);
                const bin = globalBin ? new ZXing.GlobalHistogramBinarizer(src) : new ZXing.HybridBinarizer(src);
                const r = new ZXing.QRCodeReader().decode(new ZXing.BinaryBitmap(bin));
                if (r?.getText) return { data: r.getText() };
            } catch (_) {}
            return null;
        }

        // 3. canvas → OpenCV 优先 → ZXing 备选
        async function _tryDecode(ctx, canvas, img, dw, dh) {
            canvas.width = dw; canvas.height = dh;
            ctx.drawImage(img, 0, 0, dw, dh);
            const r1 = await _decodeWithOpenCV(canvas);
            if (r1) return r1;
            if (!window.ZXing) return null;
            const d = ctx.getImageData(0, 0, dw, dh);
            const lum = _lumPreprocess(d.data, dw, dh);
            return _decodeZXing(lum, dw, dh, 0) || _decodeZXing(lum, dw, dh, 1);
        }

        // 4. 实时循环用（只用 WeChatQR，确保识别精度）
        async function scanQR(canvas) {
            return await _decodeWithOpenCV(canvas);
        }

        // 5. 照片解码 — 多策略：OpenCV → ZXing、多尺度、分块
        async function _decodePhoto(img, canvas) {
            const ctx = canvas.getContext("2d", { willReadFrequently: true });
            const nw = img.naturalWidth, nh = img.naturalHeight;
            if (!nw || !nh) return null;
            // 等待 OpenCV 就绪（最多等 15 秒）
            if (!_cvReady) await Promise.race([_cvReadyPromise, new Promise(r => setTimeout(r, 15000))]);

            // A: ~1000px 快速扫描
            const sA = Math.min(1000 / nw, 1000 / nh, 1);
            const r = await _tryDecode(ctx, canvas, img, Math.round(nw * sA), Math.round(nh * sA));
            if (r) return r;

            // B: 原生分辨率（上限 3000px）— 密集/小 QR 码
            const cap = Math.min(nw, nh, 3000);
            const sB = Math.min(cap / nw, cap / nh, 1);
            if (sB > sA + 0.08) {
                const r2 = await _tryDecode(ctx, canvas, img, Math.round(nw * sB), Math.round(nh * sB));
                if (r2) return r2;
            }
            // C: 0.5x 中分辨率
            if (0.5 > sA + 0.05 && sB > 0.55) {
                const r3 = await _tryDecode(ctx, canvas, img, Math.round(nw * 0.5), Math.round(nh * 0.5));
                if (r3) return r3;
            }
            // D: 分块扫描 — QR 只占画面小部分的大图
            if (nw * nh > 1500 * 1500) {
                const T = 1000, ST = Math.round(T * 0.7);
                const cols = Math.max(1, Math.ceil((nw - T) / ST) + 1);
                const rows = Math.max(1, Math.ceil((nh - T) / ST) + 1);
                for (let r = 0; r < rows; r++) {
                    for (let c = 0; c < cols; c++) {
                        const sx = c === cols - 1 ? nw - T : c * ST;
                        const sy = r === rows - 1 ? nh - T : r * ST;
                        const tw = Math.min(T, nw - sx), th = Math.min(T, nh - sy);
                        if (tw < 150 || th < 150) continue;
                        canvas.width = tw; canvas.height = th;
                        ctx.drawImage(img, sx, sy, tw, th, 0, 0, tw, th);
                        const rr = await _decodeWithOpenCV(canvas);
                        if (rr) return rr;
                        if (window.ZXing) {
                            const d = ctx.getImageData(0, 0, tw, th);
                            const lum = _lumPreprocess(d.data, tw, th);
                            const zr = _decodeZXing(lum, tw, th, 0) || _decodeZXing(lum, tw, th, 1);
                            if (zr) return zr;
                        }
                    }
                }
            }
            return null;
        }

        function startScanner() {
            scanPhotoSrc.value = null;
            scanFail.value = "";
            scanOpening.value = false;
            scanLiveMode.value = false;
            modals.scanner = true;
            Vue.nextTick()
                .then(() => new Promise((r) => setTimeout(r, 300)))
                .then(() => {
                    if (modals.scanner) tryOpenCamera();
                });
        }

        async function tryOpenCamera() {
            if (scanStream || scanLiveMode.value) return;
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                scanFail.value = "当前浏览器不支持摄像头";
                return;
            }
            scanOpening.value = true;
            await Vue.nextTick();
            const tries = [
                { video: { facingMode: { ideal: "environment" } } },
                { video: { facingMode: "user" } },
                { video: true },
            ];
            for (const c of tries) {
                try {
                    scanStream = await navigator.mediaDevices.getUserMedia(c);
                    if (scanStream) break;
                } catch (e) {
                    if (e.name === "NotAllowedError" || e.name === "NotFoundError") break;
                }
            }
            // 用户可能在 getUserMedia 期间关闭了对话框
            if (!modals.scanner) {
                if (scanStream) {
                    scanStream.getTracks().forEach((t) => t.stop());
                    scanStream = null;
                }
                return;
            }
            if (!scanStream) {
                scanOpening.value = false;
                scanFail.value = "无法打开摄像头，可使用拍照扫描";
                return;
            }
            const video = scanVideo.value;
            if (!video) {
                stopStream();
                return;
            }
            video.srcObject = scanStream;
            try {
                await video.play();
            } catch (e) {
                scanOpening.value = false;
                scanFail.value = "摄像头启动失败，可使用拍照扫描";
                stopStream();
                return;
            }
            // play 过程中也可能已关闭
            if (!modals.scanner) {
                stopStream();
                return;
            }
            scanOpening.value = false;
            scanLiveMode.value = true;
            liveLoop();
        }

        async function liveLoop() {
            if (!modals.scanner || !scanLiveMode.value || !scanVideo.value) return;
            const video = scanVideo.value;
            const canvas = scanCanvas.value;
            if (!canvas || video.readyState < 2) {
                scanTimer = setTimeout(liveLoop, 200);
                return;
            }
            // 640px 上限兼顾速度与细节（WeChat QR 内部会处理缩放）
            const rw = video.videoWidth || 640;
            const rh = video.videoHeight || 480;
            const scale = Math.min(640 / rw, 480 / rh, 1);
            const w = Math.round(rw * scale);
            const h = Math.round(rh * scale);
            if (canvas.width !== w || canvas.height !== h) {
                canvas.width = w;
                canvas.height = h;
            }
            const ctx = canvas.getContext("2d");
            ctx.drawImage(video, 0, 0, w, h);
            try {
                const code = await scanQR(canvas);
                if (code && code.data) {
                    const text = code.data;
                    const now = Date.now();
                    if (text === lastScannedText && now - lastScannedTime < 2000) {
                        scanTimer = setTimeout(liveLoop, 150);
                        return;
                    }
                    lastScannedText = text;
                    lastScannedTime = now;
                    if (validateAndExecuteQR(text)) {
                        stopScanner();
                        showToast("二维码识别成功 ✓");
                        return;
                    }
                    showToast("无效二维码");
                }
            } catch (e) {
                console.log("[liveLoop] error:", e);
            }
            scanTimer = setTimeout(liveLoop, 200);
        }

        // ---- 拍照扫描 ----
        function capturePhoto() {
            const input = scanPhotoInput.value;
            if (!input) return;
            input.value = "";
            input.click();
        }

        async function onScanPhoto(event) {
            const files = event.target && event.target.files;
            const file = files && files[0];
            if (!file) return;
            let url;
            try { url = URL.createObjectURL(file); } catch { url = null; }
            if (url) scanPhotoSrc.value = url;
            const img = new Image();
            img.onload = async function () {
                const canvas = scanCanvas.value;
                if (!canvas) { if (url) URL.revokeObjectURL(url); return; }
                try {
                    const code = await _decodePhoto(img, canvas);
                    if (code && code.data) {
                        const text = code.data;
                        stopScanner();
                        if (validateAndExecuteQR(text)) showToast("二维码识别成功 ✓");
                        else showToast("无效二维码");
                        if (url) URL.revokeObjectURL(url);
                        return;
                    }
                    showToast("未识别到签到二维码，请重新拍照");
                } catch (e) {
                    console.error("[onScanPhoto]", e);
                    showToast("图片解析失败，请重试");
                }
                if (url) URL.revokeObjectURL(url);
            };
            img.onerror = function () { showToast("图片加载失败，请重试"); if (url) URL.revokeObjectURL(url); };
            img.src = url || "";
        }

        function stopStream() {
            if (scanStream) {
                scanStream.getTracks().forEach((t) => t.stop());
                scanStream = null;
            }
            if (scanVideo.value) {
                scanVideo.value.srcObject = null;
            }
            if (scanTimer) {
                clearTimeout(scanTimer);
                scanTimer = null;
            }
        }

        function stopScanner() {
            stopStream();
            modals.scanner = false;
            scanOpening.value = false;
            scanLiveMode.value = false;
            scanPhotoSrc.value = null;
        }

        async function executeCheckin() {
            if (
                !checkinForm.courseid ||
                !checkinForm.ticketid ||
                !checkinForm.expire ||
                !checkinForm.sign
            ) {
                showToast("所有参数均为必填项");
                return;
            }
            checkinLoading.value = true;
            checkinResults.value = [];
            try {
                const res = await api("POST", "/api/checkin", {
                    courseid: checkinForm.courseid,
                    ticketid: checkinForm.ticketid,
                    expire: parseInt(checkinForm.expire) || 0,
                    sign: checkinForm.sign || "",
                });
                const data = res.data || {};
                checkinResults.value = data.results || [];
                showToast(data.message || `签到完成 (${checkinSuccessCount.value}/${checkinResults.value.length})`);
            } catch (e) {
                showToast(e.message || "签到失败");
            } finally {
                checkinLoading.value = false;
            }
        }

        async function executeGpsCheckin() {
            if (!gpsCheckinForm.courseid || !gpsCheckinForm.id) {
                showToast("课程 ID 和考勤 ID 为必填项");
                return;
            }
            gpsCheckinLoading.value = true;
            gpsCheckinResults.value = [];
            try {
                const res = await api("POST", "/api/checkin/gps", {
                    id: gpsCheckinForm.id,
                    courseid: gpsCheckinForm.courseid,
                });
                const data = res.data || {};
                gpsCheckinResults.value = data.results || [];
                showToast(data.message || `签到完成 (${gpsCheckinSuccessCount.value}/${gpsCheckinResults.value.length})`);
            } catch (e) {
                showToast(e.message || "签到失败");
            } finally {
                gpsCheckinLoading.value = false;
            }
        }

        // ---- 自动签到 ----
        async function loadAutoConfig() {
          try {
            const res = await api("GET", "/api/auto-checkin/config");
            const d = res.data || {};
            autoConfig.value = d;
            autoEnabled.value = d.enabled === true;
            const types = (d.checkin_types || "1,2").split(",");
            autoTypeDigit.value = types.includes("1");
            autoTypeGps.value = types.includes("2");
            if (d.time_windows && Array.isArray(d.time_windows) && d.time_windows.length) {
              autoTimeWindows.value = d.time_windows.map(w => ({ start: w.start, end: w.end }));
            } else {
              autoTimeWindows.value = [];
            }
          } catch (e) {
            // 首次使用可能无配置
            autoConfig.value = { enabled: false };
            autoEnabled.value = false;
            autoTimeWindows.value = [];
          }
        }

        function isDuplicateWindow(w, index) {
          return autoTimeWindows.value.some((other, i) =>
            i !== index && other.start === w.start && other.end === w.end
          );
        }

        async function saveAutoConfig() {
          const types = [];
          if (autoTypeDigit.value) types.push("1");
          if (autoTypeGps.value) types.push("2");
          if (!types.length) { showToast("请至少选择一种签到类型"); return; }

          // 检查是否有重复时段
          const hasDup = autoTimeWindows.value.some((w, i) => isDuplicateWindow(w, i));
          if (hasDup) { showToast("存在重复时段，请先修改"); return; }

          autoSaving.value = true;
          try {
            const res = await api("PUT", "/api/auto-checkin/config", {
              enabled: autoEnabled.value,
              checkin_types: types.join(","),
              time_windows: JSON.stringify(autoTimeWindows.value),
            });
            showToast(res.message || "配置已保存");
            autoConfig.value = res.data;
          } catch (e) {
            showToast(e.message || "保存失败");
          } finally {
            autoSaving.value = false;
          }
        }

        async function loadAutoStatus() {
          try {
            const res = await api("GET", "/api/auto-checkin/status");
            autoStatus.value = res.data || {};
          } catch {
            autoStatus.value = null;
          }
        }

        async function triggerAutoCheckin() {
          try {
            const res = await api("POST", "/api/auto-checkin/trigger");
            showToast(res.message || "扫描已触发");
            setTimeout(loadAutoStatus, 2000);
          } catch (e) {
            showToast(e.message || "触发失败");
          }
        }

        const MAX_WINDOWS = 16;

        function addTimeWindow() {
          if (autoTimeWindows.value.length >= MAX_WINDOWS) {
            showToast(`时段数量不能超过 ${MAX_WINDOWS} 个`);
            return;
          }
          autoTimeWindows.value.push({ start: 8, end: 12 });
        }

        function removeTimeWindow(index) {
          autoTimeWindows.value.splice(index, 1);
        }

        // ---- 日志 ----
        async function loadLogs() {
            try {
                const params = new URLSearchParams();
                params.set("page", logPage.value);
                params.set("page_size", PAGE_SIZE);
                if (logFilter.course_id) params.set("course_id", logFilter.course_id);
                if (logFilter.account_email) params.set("account_email", logFilter.account_email);
                if (logFilter.status !== null) params.set("status", logFilter.status);
                if (logFilter.date_from) params.set("date_from", logFilter.date_from);
                if (logFilter.date_to) params.set("date_to", logFilter.date_to);
                const res = await api("GET", `/api/logs/checkin?${params}`);
                logs.value = res.data || [];
                logTotal.value = res.total || 0;
            } catch (e) {
                showToast(e.message);
            }
        }

        // 日志筛选防抖 — 任何筛选条件变化后自动重载（300ms 防抖）
        let _logFilterTimer = null;
        watch(
            () => [logFilter.course_id, logFilter.account_email, logFilter.status, logFilter.date_from, logFilter.date_to],
            () => {
                clearTimeout(_logFilterTimer);
                _logFilterTimer = setTimeout(() => {
                    logPage.value = 1;
                    loadLogs();
                }, 300);
            },
        );

        // ---- 用户管理 ----
        async function loadUsers() {
            if (state.currentUser?.role !== "admin") return;
            try {
                const params = new URLSearchParams();
                params.set("page", userPage.value);
                params.set("page_size", PAGE_SIZE);
                const res = await api("GET", `/api/users?${params}`);
                users.value = res.data || [];
                userTotal.value = res.total || 0;
            } catch (e) {
                showToast(e.message);
            }
        }

        function openUserModal(u) {
            editingUser.value = u || null;
            userForm.email = u ? u.email : "";
            userForm.password = "";
            userForm.role = u ? u.role : "user";
            userForm.is_active = u ? u.is_active : true;
            modals.user = true;
        }

        async function saveUser() {
            if (!userForm.email) {
                showToast("请填写邮箱");
                return;
            }
            try {
                if (editingUser.value) {
                    const body = { email: userForm.email, role: userForm.role, is_active: userForm.is_active };
                    if (userForm.password) body.password = userForm.password;
                    await api("PUT", `/api/users/${editingUser.value.id}`, body);
                    showToast("用户已更新");
                } else {
                    if (!userForm.password) {
                        showToast("请填写密码");
                        return;
                    }
                    await api("POST", "/api/users", {
                        email: userForm.email,
                        password: userForm.password,
                        role: userForm.role,
                    });
                    showToast("用户已创建");
                }
                modals.user = false;
                loadUsers();
            } catch (e) {
                showToast(e.message);
            }
        }

        async function deleteUser(u) {
            if (!confirm(`确定删除用户 ${u.email}？`)) return;
            try {
                await api("DELETE", `/api/users/${u.id}`);
                showToast("用户已删除");
                loadUsers();
            } catch (e) {
                showToast(e.message);
            }
        }

        // ---- 修改密码 ----
        async function changePassword() {
            if (!changePwdForm.old_password) {
                showToast("请输入旧密码");
                return;
            }
            if (!changePwdForm.new_password) {
                showToast("请输入新密码");
                return;
            }
            if (changePwdForm.new_password !== changePwdForm.confirm) {
                showToast("两次输入的新密码不一致");
                return;
            }
            try {
                await api("PUT", "/api/user/password", {
                    old_password: changePwdForm.old_password,
                    new_password: changePwdForm.new_password,
                });
                showToast("密码修改成功");
                modals.changePwd = false;
                changePwdForm.old_password = "";
                changePwdForm.new_password = "";
                changePwdForm.confirm = "";
            } catch (e) {
                showToast(e.message);
            }
        }

        // 根据当前路由加载数据
        // ---- 管理员全部账号 ----
        async function loadAdminAccounts() {
            if (state.currentUser?.role !== "admin") return;
            try {
                const params = new URLSearchParams();
                params.set("page", adminAcctPage.value);
                params.set("page_size", PAGE_SIZE);
                const r = await api("GET", `/api/admin/accounts?${params}`);
                adminAccounts.value = r.data || [];
                adminAcctTotal.value = r.total || 0;
            } catch {}
        }

        // ---- 邀请码管理 ----
        async function loadInviteRequired() {
            try {
                const r = await api("GET", "/api/settings/invite-required");
                inviteRequired.value = r.data?.invite_required === true;
            } catch {}
        }
        async function loadInviteCodes() {
            try {
                const params = new URLSearchParams();
                params.set("page", invitePage.value);
                params.set("page_size", PAGE_SIZE);
                const r = await api("GET", `/api/invite-codes?${params}`);
                inviteCodes.value = r.data || [];
                inviteTotal.value = r.total || 0;
            } catch {}
        }
        function openInviteModal(c) {
            editingInvite.value = c || null;
            inviteForm.code = c ? c.code : "";
            inviteForm.max_uses = c ? c.max_uses : null;
            inviteForm.expires_in = null;
            inviteForm.note = c ? c.note : "";
            modals.invite = true;
        }
        async function saveInviteCode() {
            if (editingInvite.value) {
                try {
                    await api("PUT", `/api/invite-codes/${editingInvite.value.id}`, {
                        is_active: editingInvite.value.is_active,
                        max_uses: inviteForm.max_uses,
                        note: inviteForm.note,
                    });
                    showToast("邀请码已更新");
                } catch (e) {
                    showToast(e.message);
                }
            } else {
                try {
                    await api("POST", "/api/invite-codes", {
                        code: inviteForm.code,
                        max_uses: inviteForm.max_uses,
                        expires_in_hours: inviteForm.expires_in,
                        note: inviteForm.note,
                    });
                    showToast("邀请码已生成");
                } catch (e) {
                    showToast(e.message);
                }
            }
            modals.invite = false;
            loadInviteCodes();
        }
        async function toggleInviteActive(c, event) {
            const target = event.target;
            const newVal = target.checked;
            try {
                await api("PUT", `/api/invite-codes/${c.id}`, { is_active: newVal, max_uses: c.max_uses, note: c.note || "" });
                c.is_active = newVal;
                showToast(newVal ? "邀请码已启用" : "邀请码已停用");
            } catch (e) {
                target.checked = !newVal;
                showToast(e.message);
            }
        }
        async function deleteInviteCode(c) {
            if (!confirm(`确定删除邀请码 ${c.code}？`)) return;
            try {
                await api("DELETE", `/api/invite-codes/${c.id}`);
                showToast("邀请码已删除");
                loadInviteCodes();
            } catch (e) {
                showToast(e.message);
            }
        }
        async function toggleInviteRequired(event) {
            const target = event.target;
            const newVal = target.checked;
            try {
                await api("PUT", "/api/settings/invite-required", { invite_required: newVal });
                inviteRequired.value = newVal;
                showToast(newVal ? "已开启邀请码验证" : "已关闭邀请码验证");
            } catch (e) {
                target.checked = !newVal;
                showToast(e.message);
            }
        }

        async function loadPageData(page) {
            switch (page) {
                case "dashboard":
                    logPage.value = 1;
                    await Promise.all([loadAccounts(), loadBindings(), loadLogs()]);
                    break;
                case "accounts":
                    await loadAccounts();
                    break;
                case "courses":
                    await Promise.all([loadAccounts(), loadBindings()]);
                    break;
                case "checkin":
                    await Promise.all([loadAccounts(), loadAutoConfig(), loadAutoStatus()]);
                    break;
                case "logs":
                    logPage.value = 1;
                    await Promise.all([loadAccounts(), loadLogs()]);
                    break;
                case "users":
                    userPage.value = 1;
                    adminAcctPage.value = 1;
                    invitePage.value = 1;
                    await Promise.all([loadUsers(), loadAdminAccounts(), loadInviteCodes()]);
                    break;
            }
        }

        // 监听 hashchange 自动加载数据
        window.addEventListener("hashchange", () => {
            const to = hashFromURL();
            if (route.value === to) return;
            // 未登录时访问需登录页面 → 跳回登录页
            if (!state.token && to !== "login" && to !== "register") {
                window.location.hash = "#login";
                return;
            }
            route.value = to;
            if (to === "register") loadInviteRequired();
            if (to !== "login" && to !== "register") loadPageData(to);
        });

        // JWT 过期检测（仅解码 exp，不验证签名）
        function isJwtExpired(token) {
            try {
                const payload = JSON.parse(atob(token.split(".")[1]));
                return payload.exp * 1000 < Date.now();
            } catch {
                return true;
            }
        }

        // ---- 初始化 ----
        if (!state.token) {
            const hash = window.location.hash.replace(/^#\/?/, "");
            if (hash === "register") {
                route.value = "register";
                loadInviteRequired();
            } else {
                route.value = "login";
                window.location.hash = "#login";
            }
        } else if (isJwtExpired(state.token)) {
            // 本地就能判断 token 已过期 — 直接清掉，无需发起请求
            state.token = null;
            state.currentUser = null;
            localStorage.removeItem("token");
            localStorage.removeItem("refresh_token");
            localStorage.removeItem("user");
            route.value = "login";
            window.location.hash = "#login";
        } else {
            // 获取最新用户信息
            const userId = state.currentUser?.id;
            if (userId) {
                api("GET", `/api/users/${userId}`)
                    .then((res) => {
                        if (res.data) {
                            state.currentUser = res.data;
                            localStorage.setItem("user", JSON.stringify(res.data));
                        }
                    })
                    .catch(() => {
                        // 获取失败时不清除登录状态，保持 localStorage 数据
                    });
            }
            loadPageData(route.value).catch((e) => {
                console.warn("加载页面数据失败:", e);
                showToast("加载数据失败，请刷新重试");
            });
        }

        return {
            state,
            route,
            sidebarOpen,
            loading,
            showLoginPwd,
            showRegPwd,
            showAcctPwd,
            showUserPwd,
            showChangeOldPwd,
            showChangeNewPwd,
            showChangeConfirmPwd,
            form,
            accountForm,
            bindingForm,
            userForm,
            checkinForm,
            gpsCheckinForm,
            logFilter,
            changePwdForm,
            accounts,
            bindings,
            logs,
            users,
            recentLogs,
            modals,
            editingAccount,
            editingUser,
            checkinLoading,
            checkinResults,
            gpsCheckinLoading,
            gpsCheckinResults,
            pageTitle,
            pageSubtitle,
            stats,
            checkinSuccessCount,
            gpsCheckinSuccessCount,
            autoEnabled,
            autoTypeDigit,
            autoTypeGps,
            autoTimeWindows,
            autoConfig,
            autoStatus,
            autoSaving,
            loadAutoConfig,
            saveAutoConfig,
            loadAutoStatus,
            triggerAutoCheckin,
            addTimeWindow,
            removeTimeWindow,
            isDuplicateWindow,
            MAX_WINDOWS,
            formatTime,
            getAccountEmail,
            getCourseName,
            getCourseSemester,
            getCourseCode,
            showToast,
            navigate,
            login,
            register,
            logout,
            loadAccounts,
            openAccountModal,
            saveAccount,
            verifyAccount,
            deleteAccount,
            loadBindings,
            openBindingModal,
            saveBinding,
            deleteBinding,
            toggleBinding,
            executeCheckin,
            executeGpsCheckin,
            parseCheckinUrl,
            checkinUrl,
            loadLogs,
            loadUsers,
            openUserModal,
            saveUser,
            deleteUser,
            changePassword,
            inviteRequired,
            inviteCodes,
            editingInvite,
            inviteForm,
            adminAccounts,
            loadInviteCodes,
            loadInviteRequired,
            openInviteModal,
            saveInviteCode,
            deleteInviteCode,
            toggleInviteActive,
            toggleInviteRequired,
            copyText,
            codeIsExpired,
            codeStatusClass,
            codeStatusText,
            startScanner,
            stopScanner,
            tryOpenCamera,
            capturePhoto,
            onScanPhoto,
            scanOpening,
            scanLiveMode,
            scanFail,
            scanPhotoSrc,
            scanVideo,
            scanCanvas,
            scanPhotoInput,
            // 分页
            PAGE_SIZE,
            logPage,
            logTotal,
            logTotalPages,
            userPage,
            userTotal,
            userTotalPages,
            adminAcctPage,
            adminAcctTotal,
            adminAcctTotalPages,
            invitePage,
            inviteTotal,
            inviteTotalPages,
        };
    },
}).mount("#app");
