<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <nav v-if="session" class="topbar">
      <button :class="{ active: page === 'scan' }" @click="page = 'scan'">扫描总表</button>
      <button :class="{ active: page === 'daynight' }" @click="goDayNight">昼夜差专页</button>
      <span class="spacer"></span>
      <button class="secondary" @click="logout">退出</button>
    </nav>

    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>

    <!-- 扫描总表 -->
    <div v-else-if="page === 'scan'">
      <p class="sub">已登录：{{ session.username }}（{{ isWriter ? "扫描员，可提交/可改对照窗" : "巡视，只读" }}）</p>
      <section>
        <button class="secondary" @click="refresh">刷新列表</button>
      </section>
      <section v-if="isWriter">
        <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
        <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
        <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
        <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
        <button :disabled="loading" @click="submit">提交扫描</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
      <section>
        <table>
          <thead>
            <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in logs" :key="row.id">
              <td>{{ row.id }}</td>
              <td>{{ row.string_code }}</td>
              <td>{{ row.voc_v }}</td>
              <td>{{ row.isc_a }}</td>
              <td>{{ row.fill_factor }}</td>
              <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
              <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- 昼夜差专页 -->
    <div v-else>
      <p class="sub">交班核对：组串白天与夜里填充因子差得离不离谱。已登录：{{ session.username }}（{{ isWriter ? "扫描员，可调对照窗" : "巡视，只读" }}）</p>

      <!-- 上格：对照窗 -->
      <section>
        <h2>上格 · 对照窗</h2>
        <p v-if="!isWriter" class="err">巡视口令只能看，不能改窗，也不能报送。</p>
        <div class="window-form">
          <div class="field">
            <label>组串编号</label>
            <input v-model="winCode" :disabled="!isWriter" placeholder="例如 阵列C-串05" />
          </div>
          <div class="field">
            <label>起始日期（北京时间）</label>
            <input type="date" v-model="winStart" :disabled="!isWriter" />
          </div>
          <div class="field">
            <label>窗宽（天）</label>
            <input type="number" min="1" max="366" v-model="winDays" :disabled="!isWriter" />
          </div>
          <div class="field btn-field">
            <button :disabled="!isWriter || loading" @click="saveWindow">保存对照窗</button>
          </div>
        </div>
        <p v-if="winError" class="err">{{ winError }}</p>
      </section>

      <!-- 中格：按组串列出昼夜差和样本点 -->
      <section>
        <h2>中格 · 各组串昼夜差（按当前窗实时重算）</h2>
        <button class="secondary" @click="loadDayNight">刷新</button>
        <table>
          <thead>
            <tr>
              <th>组串</th><th>对照窗</th><th>白天点</th><th>白天均值</th>
              <th>夜里点</th><th>夜里均值</th><th>昼夜差</th><th>窗由谁调</th><th v-if="isWriter">选窗</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in diffs" :key="row.string_code">
              <td>{{ row.string_code }}</td>
              <td>{{ row.start_date }} 起 {{ row.days }} 天</td>
              <td>{{ row.day_points }}</td>
              <td>{{ fmt(row.day_avg) }}</td>
              <td>{{ row.night_points }}</td>
              <td>{{ fmt(row.night_avg) }}</td>
              <td>
                <span v-if="row.diff === null" class="tag empty">样本不足 · 留空</span>
                <span v-else class="tag" :class="diffClass(row.diff)">{{ fmt(row.diff) }}</span>
              </td>
              <td>{{ row.updated_by }}</td>
              <td v-if="isWriter">
                <button class="secondary small" @click="pickWindow(row)">选入上格</button>
              </td>
            </tr>
            <tr v-if="diffs.length === 0">
              <td :colspan="isWriter ? 9 : 8" class="muted">还没有任何对照窗。</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- 下格：只读口径 -->
      <section class="readonly">
        <h2>下格 · 口径（只读）</h2>
        <ul>
          <li>样本只取自扫描总表中<strong>已办结（done）</strong>的填充因子；待处理读数不进样本。</li>
          <li>白天为北京时间 06:00（含）至 18:00（不含），其余时段为夜里。</li>
          <li>对照窗为 [起始日期, 起始日期 + 窗宽天) 的北京时间日期区间。</li>
          <li>昼夜差 = 窗内白天均值 − 窗内夜里均值，随对照窗调整<strong>实时重算</strong>，总表不设手填列。</li>
          <li>窗内白天或夜里任一侧没有样本点时，该串昼夜差<strong>留空</strong>（把窗收到数据落窗外即变平/留空）。</li>
          <li>每个组串只保存一笔对照窗，两人同时改也只留下一笔窗宽。</li>
          <li>扫描员可调对照窗与报送；巡视口令只读，不能改窗也不能报送。</li>
        </ul>
      </section>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const page = ref("scan");
const logs = ref([]);
const diffs = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const winCode = ref("");
const winStart = ref("");
const winDays = ref(7);
const error = ref("");
const winError = ref("");
const loading = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(v) {
  return v === null || v === undefined ? "—" : Number(v).toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}
function diffClass(d) {
  const abs = Math.abs(d);
  if (abs >= 0.1) return "bad";
  if (abs >= 0.05) return "warn";
  return "ok";
}
async function refresh() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function loadDayNight() {
  if (!session.value) return;
  const res = await fetch("/api/day-night-diff", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) diffs.value = await res.json();
}
function goDayNight() {
  page.value = "daynight";
  loadDayNight();
}
function pickWindow(row) {
  winCode.value = row.string_code;
  winStart.value = row.start_date;
  winDays.value = row.days;
  winError.value = "";
}
async function saveWindow() {
  winError.value = "";
  if (!winCode.value.trim()) { winError.value = "组串编号不能为空"; return; }
  if (!winStart.value) { winError.value = "请选择起始日期"; return; }
  const days = Number(winDays.value);
  if (!Number.isInteger(days) || days < 1 || days > 366) {
    winError.value = "窗宽须为 1 至 366 的整数"; return;
  }
  loading.value = true;
  try {
    const res = await fetch("/api/compare-windows", {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        string_code: winCode.value.trim(),
        start_date: winStart.value,
        days,
      }),
    });
    const data = await res.json();
    if (!res.ok) { winError.value = data.detail || "保存失败"; return; }
    await loadDayNight();
  } catch { winError.value = "保存时网络异常"; }
  finally { loading.value = false; }
}
async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refresh();
    timer = setInterval(tick, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function tick() {
  refresh();
  if (page.value === "daynight") loadDayNight();
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  diffs.value = [];
  page.value = "scan";
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "提交失败"; return; }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refresh();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refresh();
      timer = setInterval(tick, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1080px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.75rem; }
h2 { font-size: 1rem; margin: 0 0 0.75rem; color: #bbf7d0; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
.topbar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; }
.topbar .spacer { flex: 1; }
.topbar button.active { background: #15803d; outline: 1px solid #86efac; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
section.readonly { background: #0f3d24; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
input:disabled { opacity: 0.55; cursor: not-allowed; }
.window-form { display: flex; gap: 1rem; align-items: flex-end; flex-wrap: wrap; }
.window-form .field { flex: 1 1 180px; }
.window-form .btn-field { flex: 0 0 auto; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button:disabled { opacity: 0.55; cursor: not-allowed; }
button.secondary { background: #365314; }
button.small { padding: 0.25rem 0.6rem; font-size: 0.8rem; margin: 0; }
.err { color: #fecaca; }
.muted { color: #a7f3d0; }
ul { margin: 0; padding-left: 1.25rem; line-height: 1.8; font-size: 0.9rem; color: #d1fae5; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.warn { background: #854d0e; color: #fde68a; }
.empty { background: #1f2937; color: #9ca3af; }
.pending { background: #854d0e; color: #fde68a; }
</style>
