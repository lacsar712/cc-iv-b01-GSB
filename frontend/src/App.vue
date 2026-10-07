<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <nav v-if="session" class="topbar">
      <button :class="{ active: page === 'logs' }" @click="page = 'logs'">扫描总表</button>
      <button :class="{ active: page === 'compare' }" @click="goCompare">昼夜差专页</button>
      <span class="who">已登录：{{ session.username }}（{{ isWriter ? "扫描员·可改窗可报送" : "巡视·只读" }}）</span>
      <button class="secondary" @click="logout">退出</button>
    </nav>

    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456；巡视账号 watcher / watch123456 只读，不能改窗也不能报送。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>

    <!-- 扫描总表 -->
    <div v-else-if="page === 'logs'">
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
      <!-- 上格：选对照窗 -->
      <section class="pane">
        <h2>① 对照窗</h2>
        <p class="hint" v-if="!isWriter">巡视账号只读：可查看对照窗，但不能改窗，也不能报送。</p>
        <div class="winrow">
          <label>组串</label>
          <select v-model="selectedCode" @change="syncFormFromSelection">
            <option v-for="s in cmp.strings" :key="s.string_code" :value="s.string_code">{{ s.string_code }}</option>
          </select>
          <label>昼窗起</label><input type="number" step="0.5" min="0" max="24" v-model="form.ds" :disabled="!isWriter" />
          <label>昼窗止</label><input type="number" step="0.5" min="0" max="24" v-model="form.de" :disabled="!isWriter" />
          <label>夜窗起</label><input type="number" step="0.5" min="0" max="24" v-model="form.ns" :disabled="!isWriter" />
          <label>夜窗止<small>（跨零填到30）</small></label><input type="number" step="0.5" min="0" max="30" v-model="form.ne" :disabled="!isWriter" />
          <button v-if="isWriter" :disabled="loading || !selectedCode" @click="saveWindow">套用对照窗</button>
          <button class="secondary" @click="resetForm">恢复当前</button>
        </div>
        <p class="hint">未手设的串用默认窗：昼 [{{ cmp.default_window.day_start }}:00, {{ cmp.default_window.day_end }}:00)，夜 [{{ cmp.default_window.night_start }}:00, 次日{{ cmp.default_window.night_end - 24 }}:00)。窗为左闭右开；每串只保留一笔窗宽，后套的覆盖先套的。</p>
        <p v-if="winMsg" :class="winOk ? 'oktext' : 'err'">{{ winMsg }}</p>
      </section>

      <!-- 中格：按组串列昼夜差与样本点 -->
      <section class="pane">
        <h2>② 各组串昼夜填充因子差（按当前窗即时重算）</h2>
        <table>
          <thead>
            <tr>
              <th>组串</th><th>昼窗</th><th>夜窗</th>
              <th>昼样本</th><th>昼均FF</th>
              <th>夜样本</th><th>夜均FF</th>
              <th>窗外点</th><th>昼夜差</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="s in cmp.strings" :key="s.string_code">
              <tr
                :class="{ pick: s.string_code === selectedCode }"
                @click="selectedCode = s.string_code; syncFormFromSelection()"
              >
                <td>{{ s.string_code }}</td>
                <td>{{ fmtWin(s.window.day_start, s.window.day_end) }}</td>
                <td>{{ fmtNight(s.window.night_start, s.window.night_end) }}</td>
                <td>{{ s.day.count }}</td>
                <td>{{ s.day.mean_ff ?? '—' }}</td>
                <td>{{ s.night.count }}</td>
                <td>{{ s.night.mean_ff ?? '—' }}</td>
                <td>{{ s.outside_count }}</td>
                <td>
                  <span v-if="s.diff === null" class="tag pending">留空（单侧无样本）</span>
                  <span v-else class="tag" :class="Math.abs(s.diff) >= BIG_DIFF ? 'bad' : 'ok'">
                    {{ s.diff > 0 ? '+' : '' }}{{ s.diff }}
                  </span>
                </td>
              </tr>
              <tr class="samples">
                <td></td>
                <td colspan="8">
                  <span class="samp-label">昼样本点：</span>
                  <span v-if="!s.day.samples.length" class="dim">无</span>
                  <span v-for="p in s.day.samples" :key="'d'+p.id" class="pt daypt">#{{ p.id }} FF={{ p.fill_factor }}（{{ fmtTime(p.at) }}） </span>
                  <br />
                  <span class="samp-label">夜样本点：</span>
                  <span v-if="!s.night.samples.length" class="dim">无</span>
                  <span v-for="p in s.night.samples" :key="'n'+p.id" class="pt nightpt">#{{ p.id }} FF={{ p.fill_factor }}（{{ fmtTime(p.at) }}） </span>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </section>

      <!-- 下格：只读口径 -->
      <section class="pane criteria">
        <h2>③ 统计口径（只读）</h2>
        <ul>
          <li v-for="(v, k) in cmp.criteria" :key="k"><b>{{ criteriaName[k] || k }}</b>：{{ v }}</li>
        </ul>
      </section>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
const BIG_DIFF = 0.08;
const session = ref(null);
const logs = ref([]);
const page = ref("logs");
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const loading = ref(false);
const cmp = ref({ criteria: {}, default_window: { day_start: 6, day_end: 18, night_start: 18, night_end: 30 }, strings: [] });
const selectedCode = ref("");
const form = reactive({ ds: 6, de: 18, ns: 18, ne: 30 });
const winMsg = ref("");
const winOk = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");
const criteriaName = {
  tz: "时区", day_window: "默认昼窗", night_window: "默认夜窗",
  sample_rule: "样本口径", outside_rule: "窗外口径", diff_rule: "差值口径",
  recalc_rule: "重算口径", window_rule: "改窗口径",
};
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
async function refresh() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function loadCompare() {
  if (!session.value) return;
  const res = await fetch("/api/compare", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (!res.ok) return;
  cmp.value = await res.json();
  if (!selectedCode.value && cmp.value.strings.length) {
    selectedCode.value = cmp.value.strings[0].string_code;
  }
  syncFormFromSelection();
}
function goCompare() {
  page.value = "compare";
  loadCompare();
}
function currentWindow(code) {
  const s = cmp.value.strings.find(x => x.string_code === code);
  return s ? s.window : cmp.value.default_window;
}
function syncFormFromSelection() {
  const w = currentWindow(selectedCode.value);
  form.ds = w.day_start; form.de = w.day_end; form.ns = w.night_start; form.ne = w.night_end;
  winMsg.value = "";
}
function resetForm() { syncFormFromSelection(); }
async function saveWindow() {
  winMsg.value = "";
  const res = await fetch("/api/compare-windows/" + encodeURIComponent(selectedCode.value), {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...headers() },
    body: JSON.stringify({
      day_start: Number(form.ds), day_end: Number(form.de),
      night_start: Number(form.ns), night_end: Number(form.ne),
    }),
  });
  const data = await res.json();
  if (!res.ok) { winOk.value = false; winMsg.value = data.detail || "对照窗保存失败"; return; }
  winOk.value = true;
  winMsg.value = `已套用 ${selectedCode.value} 的对照窗（${data.updated_by} @ ${fmtTime(data.updated_at)}），昼夜差已按新窗重算`;
  await loadCompare();
}
function fmtWin(a, b) { return `[${fmtHour(a)}, ${fmtHour(b)})`; }
function fmtNight(a, b) {
  return b > 24 ? `[${fmtHour(a)}, 次日${fmtHour(b - 24)})` : `[${fmtHour(a)}, ${fmtHour(b)})`;
}
function fmtHour(h) { return `${String(Math.floor(h)).padStart(2, "0")}:${String(Math.round((h % 1) * 60)).padStart(2, "0")}`; }
function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
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
  if (page.value === "compare") loadCompare();
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  page.value = "logs";
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
main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.5rem; }
h2 { font-size: 1rem; color: #bbf7d0; margin: 0 0 0.75rem; }
.topbar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; }
.topbar .who { margin-left: auto; color: #a7f3d0; font-size: 0.85rem; }
.topbar button.active { background: #15803d; outline: 1px solid #86efac; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section, .pane { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.pane.criteria { background: #0f3d24; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
input:disabled { opacity: 0.55; }
.winrow { display: grid; grid-template-columns: repeat(9, auto); gap: 0.5rem; align-items: end; }
.winrow label { white-space: nowrap; }
.winrow small { color: #a7f3d0; }
.winrow button { margin-bottom: 0.75rem; white-space: nowrap; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
.hint { color: #a7f3d0; font-size: 0.82rem; margin: 0.4rem 0 0; }
.err { color: #fecaca; }
.oktext { color: #bbf7d0; }
.dim { color: #86c5a3; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
tbody tr:not(.samples) { cursor: pointer; }
tr.pick td { background: #166534; }
tr.samples td { font-size: 0.8rem; color: #a7f3d0; background: #0c3a22; }
.pt { margin-right: 0.75rem; white-space: nowrap; }
.daypt { color: #fde68a; }
.nightpt { color: #93c5fd; }
.samp-label { color: #ecfdf5; font-weight: 600; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.criteria ul { margin: 0; padding-left: 1.2rem; }
.criteria li { font-size: 0.85rem; color: #a7f3d0; margin-bottom: 0.3rem; }
</style>
