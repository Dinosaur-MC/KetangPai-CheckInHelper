const { createApp, reactive, ref, onMounted } = Vue;

const API_BASE = "";

async function api(method, path, body) {
    const headers = { "Content-Type": "application/json" };
    const opts = { method, headers };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(`${API_BASE}${path}`, opts);
    const data = await res.json();
    if (res.ok) return data;
    throw new Error(data.message || `请求失败 (${res.status})`);
}

function showToast(msg) {
    const el = document.createElement("mdui-snackbar");
    el.message = msg;
    document.body.appendChild(el);
    el.open = true;
    el.addEventListener("closed", () => el.remove());
}

createApp({
    setup() {
        const page = ref("login");
        const showPwd = ref(false);
        const loading = ref(false);
        const inviteRequired = ref(false);
        const form = reactive({ email: "", password: "", invite_code: "" });

        // 已登录用户直接跳转回主页
        if (localStorage.getItem("token")) {
            const token = localStorage.getItem("token");
            try {
                const payload = JSON.parse(atob(token.split(".")[1]));
                if (payload.exp * 1000 > Date.now()) {
                    window.location.replace("/");
                    return;
                }
            } catch {
                // token 解析失败，忽略
            }
            // token 过期，清理
            localStorage.removeItem("token");
            localStorage.removeItem("refresh_token");
            localStorage.removeItem("user");
        }

        onMounted(async () => {
            try {
                const res = await api("GET", "/api/settings/invite-required");
                inviteRequired.value = res.data?.invite_required === true;
            } catch {
                // 忽略
            }
        });

        async function doLogin() {
            if (!form.email || !form.password) {
                showToast("请填写邮箱和密码");
                return;
            }
            loading.value = true;
            try {
                const res = await api("POST", "/api/login", {
                    email: form.email,
                    password: form.password,
                });
                const data = res.data || {};
                localStorage.setItem("token", data.access_token);
                localStorage.setItem("refresh_token", data.refresh_token);
                localStorage.setItem("user", JSON.stringify(data.user));
                window.location.replace("/");
            } catch (e) {
                showToast(e.message || "登录失败");
            } finally {
                loading.value = false;
            }
        }

        async function doRegister() {
            if (!form.email || !form.password) {
                showToast("请填写邮箱和密码");
                return;
            }
            loading.value = true;
            try {
                const body = { email: form.email, password: form.password };
                if (form.invite_code) body.invite_code = form.invite_code;
                const res = await api("POST", "/api/register", body);
                const data = res.data || {};
                localStorage.setItem("token", data.access_token);
                localStorage.setItem("refresh_token", data.refresh_token);
                localStorage.setItem("user", JSON.stringify(data.user));
                window.location.replace("/");
            } catch (e) {
                showToast(e.message || "注册失败");
            } finally {
                loading.value = false;
            }
        }

        return { page, showPwd, loading, inviteRequired, form, doLogin, doRegister };
    },
}).mount("#app");
