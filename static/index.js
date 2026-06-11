const { createApp, reactive, ref, computed } = Vue;

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
        const checkinForm = reactive({ courseid: "", ticketid: "", expire: "", sign: "", randNum: "" });
        const checkinUrl = ref("");
        const logFilter = reactive({ course_id: "" });

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
        const recentLogs = computed(() => logs.value.slice(-10).reverse());

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

        const filteredLogs = computed(() =>
            logFilter.course_id ? logs.value.filter((l) => l.course_id.includes(logFilter.course_id)) : logs.value,
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
                totalLogs: logs.value.length,
            };
        });

        const checkinSuccessCount = computed(() => checkinResults.value.filter((r) => r.success).length);

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
            return a ? a.email : `#${id}`;
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
        async function loadAccounts() {
            if (_accountsPromise) return _accountsPromise;
            _accountsPromise = (async () => {
                try {
                    const res = await api("GET", "/api/accounts");
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

        async function deleteAccount(acct) {
            if (!confirm(`确定删除账号 ${acct.email}？`)) return;
            try {
                await api("DELETE", `/api/accounts/${acct.id}`);
                showToast("账号已删除");
                loadAccounts();
            } catch (e) {
                showToast(e.message);
            }
        }

        // ---- 课程绑定 ----
        async function loadBindings() {
            try {
                const [bRes, cRes] = await Promise.all([api("GET", "/api/courses/bindings"), api("GET", "/api/courses")]);
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
                checkinForm.randNum = params.get("randNum") || "";
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
                const type = p.get("type");
                const ticketid = p.get("ticketid");
                const expire = p.get("expire");
                const sign = p.get("sign");
                const courseid = p.get("courseid");
                const randNum = p.get("randNum");
                if (!ticketid || !expire || !sign || !courseid || !randNum || !type) {
                    return false;
                }
                checkinForm.courseid = courseid;
                checkinForm.ticketid = ticketid;
                checkinForm.expire = expire;
                checkinForm.sign = sign;
                checkinForm.randNum = randNum;
                checkinUrl.value = text;
                if (route.value !== "checkin") navigate("checkin");
                Vue.nextTick().then(() => executeCheckin());
                return true;
            } catch {
                return false;
            }
        }

        // ---- 扫码识别 (jsQR 实时视频 + 拍照降级) ----
        // 图像预处理 — 对比度增强，帮助 jsQR 识别边缘模糊/带装饰的二维码
        function preprocessQR(imageData) {
            const d = imageData.data;
            // 转灰度 + 自动对比度拉伸
            let min = 255, max = 0;
            for (let i = 0; i < d.length; i += 4) {
                const gray = d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114;
                d[i] = d[i + 1] = d[i + 2] = gray;
                if (gray < min) min = gray;
                if (gray > max) max = gray;
            }
            const range = max - min;
            if (range > 10) {
                const scale = 255 / range;
                for (let i = 0; i < d.length; i += 4) {
                    const v = (d[i] - min) * scale;
                    d[i] = d[i + 1] = d[i + 2] = Math.max(0, Math.min(255, v));
                }
            }
            return imageData;
        }

        // 原生 BarcodeDetector 降级 — 处理能力远强于 jsQR
        async function tryBarcodeDetector(imageData, w, h) {
            if (!window.BarcodeDetector) return null;
            try {
                const detector = new BarcodeDetector({ formats: ["qr_code"] });
                let bitmap;
                try { bitmap = await createImageBitmap(imageData); } catch { return null; }
                const results = await detector.detect(bitmap);
                if (bitmap.close) bitmap.close();
                if (results && results.length > 0 && results[0].rawValue) {
                    return { data: results[0].rawValue };
                }
            } catch (e) {
                // BarcodeDetector 可能不支持 QR
            }
            return null;
        }

        // 多策略扫描: BarcodeDetector(原生) → jsQR 原图 → jsQR 增强
        async function scanQR(imageData, w, h) {
            // 策略 1: 原生 BarcodeDetector — 更快、更准，优先使用
            let code = await tryBarcodeDetector(imageData, w, h);
            if (code && code.data) return code;

            // 策略 2: jsQR 原图 — dontInvert + attemptBoth
            code = jsQR(imageData.data, w, h, { inversionAttempts: "dontInvert" });
            if (!code) code = jsQR(imageData.data, w, h, { inversionAttempts: "attemptBoth" });
            if (code && code.data) return code;

            // 策略 3: jsQR 增强对比度后
            const enhanced = new ImageData(new Uint8ClampedArray(imageData.data), w, h);
            preprocessQR(enhanced);
            code = jsQR(enhanced.data, w, h, { inversionAttempts: "dontInvert" });
            if (!code) code = jsQR(enhanced.data, w, h, { inversionAttempts: "attemptBoth" });
            return code;
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
            // 用更高分辨率扫描，提升带装饰/裁剪二维码的识别率
            const w = Math.min(video.videoWidth || 640, 960);
            const h = Math.min(video.videoHeight || 480, 720);
            if (canvas.width !== w || canvas.height !== h) {
                canvas.width = w;
                canvas.height = h;
            }
            const ctx = canvas.getContext("2d", { willReadFrequently: true });
            ctx.drawImage(video, 0, 0, w, h);
            const imageData = ctx.getImageData(0, 0, w, h);
            try {
                const code = await scanQR(imageData, w, h);
                if (code && code.data) {
                    const text = code.data;
                    const now = Date.now();
                    // 同一二维码 2 秒内不重复提示
                    if (text === lastScannedText && now - lastScannedTime < 2000) {
                        scanTimer = setTimeout(liveLoop, 100);
                        return;
                    }
                    lastScannedText = text;
                    lastScannedTime = now;
                    if (validateAndExecuteQR(text)) {
                        stopScanner();
                        showToast("二维码识别成功 ✓");
                        return;
                    }
                    // 无效二维码 → 短暂提示后继续扫描
                    showToast("无效二维码");
                }
            } catch (e) {
                console.log("[liveLoop] jsQR error:", e);
            }
            scanTimer = setTimeout(liveLoop, 100);
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
            try {
                url = URL.createObjectURL(file);
            } catch {
                url = null;
            }
            if (url) scanPhotoSrc.value = url;
            const img = new Image();
            img.onload = async function () {
                const canvas = scanCanvas.value;
                if (!canvas) {
                    if (url) URL.revokeObjectURL(url);
                    return;
                }
                const w = Math.min(img.naturalWidth || 1200, 1200);
                const h = Math.min(img.naturalHeight || 1200, 1200);
                if (w === 0 || h === 0) {
                    showToast("图片无效，请重试");
                    if (url) URL.revokeObjectURL(url);
                    return;
                }
                canvas.width = w;
                canvas.height = h;
                const ctx = canvas.getContext("2d", { willReadFrequently: true });
                ctx.drawImage(img, 0, 0, w, h);
                let imageData;
                try {
                    imageData = ctx.getImageData(0, 0, w, h);
                } catch {
                    imageData = null;
                }
                if (!imageData) {
                    showToast("图片解析失败，请重试");
                    if (url) URL.revokeObjectURL(url);
                    return;
                }
                try {
                    // 先尝试 jsQR（原图 + 增强 + BarcodeDetector）
                    const code = await scanQR(imageData, w, h);
                    console.log("[onScanPhoto] scanQR:", code);
                    if (code && code.data) {
                        const text = code.data;
                        stopScanner();
                        if (validateAndExecuteQR(text)) showToast("二维码识别成功 ✓");
                        else showToast("无效二维码");
                        if (url) URL.revokeObjectURL(url);
                        return;
                    }
                    showToast("未识别到签到二维码，请重新拍照");
                } catch {
                    showToast("图片解析失败，请重试");
                }
                if (url) URL.revokeObjectURL(url);
            };
            img.onerror = function () {
                showToast("图片加载失败，请重试");
                if (url) URL.revokeObjectURL(url);
            };
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
                !checkinForm.sign ||
                !checkinForm.randNum
            ) {
                showToast("所有参数均为必填项");
                return;
            }
            checkinLoading.value = true;
            checkinResults.value = [];
            try {
                const res = await api("POST", "/api/checkin", {
                    type: 2,
                    courseid: checkinForm.courseid,
                    ticketid: checkinForm.ticketid,
                    expire: parseInt(checkinForm.expire) || 0,
                    sign: checkinForm.sign || "",
                    randNum: checkinForm.randNum || "",
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

        // ---- 日志 ----
        async function loadLogs() {
            try {
                const res = await api("GET", "/api/checkin/logs");
                logs.value = res.data || [];
            } catch (e) {
                showToast(e.message);
            }
        }

        // ---- 用户管理 ----
        async function loadUsers() {
            if (state.currentUser?.role !== "admin") return;
            try {
                const res = await api("GET", "/api/users");
                users.value = res.data || [];
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
                const r = await api("GET", "/api/admin/accounts");
                adminAccounts.value = r.data || [];
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
                const r = await api("GET", "/api/invite-codes");
                inviteCodes.value = r.data || [];
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
                    await Promise.all([loadAccounts(), loadBindings(), loadLogs()]);
                    break;
                case "accounts":
                    await loadAccounts();
                    break;
                case "courses":
                    await Promise.all([loadAccounts(), loadBindings()]);
                    break;
                case "logs":
                    await loadLogs();
                    break;
                case "users":
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
            } catch { return true; }
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
                api("GET", `/api/users/${userId}`).then((res) => {
                    if (res.data) {
                        state.currentUser = res.data;
                        localStorage.setItem("user", JSON.stringify(res.data));
                    }
                }).catch(() => {
                    // 获取失败时不清除登录状态，保持 localStorage 数据
                });
            }
            loadPageData(route.value).catch(() => {
                state.token = null;
                state.currentUser = null;
                localStorage.removeItem("token");
                localStorage.removeItem("refresh_token");
                localStorage.removeItem("user");
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
            pageTitle,
            pageSubtitle,
            filteredLogs,
            stats,
            checkinSuccessCount,
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
            deleteAccount,
            loadBindings,
            openBindingModal,
            saveBinding,
            deleteBinding,
            toggleBinding,
            executeCheckin,
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
        };
    },
}).mount("#app");
