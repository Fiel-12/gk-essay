/* 高考满分作文智能应用 —— 前端逻辑（无构建、无 CDN、原生 ES2020）。
 *
 * 设计要点：
 * 1. SSE 必须支持 POST + Authorization 头，EventSource 做不到，所以用 fetch +
 *    ReadableStream 手动按 "\n\n" 分帧；跨 chunk 的半条 data 行放进 buffer 攒齐再解析。
 * 2. 所有动态文本一律经过 esc() 或 textContent，避免模型输出里的 < > 破坏 DOM / 注入。
 * 3. 状态集中在 state 对象里，视图渲染成独立函数，便于单页三视图切换与局部刷新。
 */
"use strict";

const state = {
  token: localStorage.getItem("gaokao_token") || "",
  user: null,
  meta: null,
  prompts: null,
  currentView: "workbench",
  generating: false,
  stageMap: {},        // stage key -> { el, bodyEl, content }
  currentStage: null,
  typewriter: { queue: "", timer: null },
  lastEssayId: null,
  selectedEssayId: null,
  currentEssay: null,
};

const $ = (id) => document.getElementById(id);

/* ---------------- 基础工具 ---------------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(state._toastTimer);
  state._toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
}

async function request(path, { method = "GET", body } = {}) {
  const headers = {};
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  let payload;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload });
  // 统一处理会话失效：清 token、回到未登录态、弹登录框
  if (res.status === 401) {
    state.token = "";
    localStorage.removeItem("gaokao_token");
    state.user = null;
    renderUser();
    showAuth("login");
  }
  return res;
}

function ensureAuth() {
  if (state.user) return true;
  showAuth("login");
  return false;
}

/* ---------------- 初始化 / 元信息 / 用户 ---------------- */

async function init() {
  bindEvents();
  renderUser();
  await Promise.all([loadMeta(), loadMe()]);
  // 没有有效会话时，进入页面先弹登录框（可关闭，公开接口仍可浏览）
  if (!state.user) showAuth("login");
}

async function loadMeta() {
  try {
    const res = await fetch("/api/meta");
    const data = await res.json();
    state.meta = data;
    $("app-title").textContent = data.app_title || "高考满分作文智能应用";
    $("meta-class").textContent = data.student_class || "—";
    $("meta-name").textContent = data.student_name || "—";
    $("meta-id").textContent = data.student_id || "—";
    $("meta-model").textContent = data.model || "—";
    $("meta-count").textContent = data.corpus_count ?? "—";
    document.title = data.app_title || document.title;
  } catch (err) {
    console.error("加载元信息失败", err);
  }
}

async function loadMe() {
  if (!state.token) { state.user = null; renderUser(); return; }
  try {
    const res = await request("/api/me");
    if (!res.ok) return;
    const data = await res.json();
    state.user = data.user;
  } catch (err) {
    state.user = null;
  }
  renderUser();
}

function renderUser() {
  const name = state.user ? (state.user.nickname || state.user.username) : "未登录";
  $("user-name").textContent = name;
  $("auth-btn").textContent = state.user ? "退出登录" : "登录 / 注册";
}

/* ---------------- 视图切换 ---------------- */

function switchView(view) {
  state.currentView = view;
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  $("view-" + view).classList.add("active");
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  if (view === "history") loadEssays();
  if (view === "prompts") loadPrompts();
}

/* ---------------- 认证浮层 ---------------- */

let authMode = "login";

function showAuth(mode) {
  authMode = mode || "login";
  $("auth-modal").classList.remove("hidden");
  $("auth-title").textContent = mode === "login" ? "登录" : "注册";
  $("tab-login").classList.toggle("active", mode === "login");
  $("tab-register").classList.toggle("active", mode === "register");
  $("nickname-field").style.display = mode === "register" ? "flex" : "none";
  $("auth-error").textContent = "";
}

function hideAuth() {
  $("auth-modal").classList.add("hidden");
}

async function submitAuth(e) {
  e.preventDefault();
  const username = $("auth-username").value.trim();
  const password = $("auth-password").value;
  const nickname = $("auth-nickname").value.trim();
  if (!username || !password) { $("auth-error").textContent = "请填写用户名与密码"; return; }
  const path = authMode === "login" ? "/api/auth/login" : "/api/auth/register";
  const body = { username, password };
  if (authMode === "register") body.nickname = nickname || null;
  try {
    const res = await request(path, { method: "POST", body });
    const data = await res.json();
    if (!res.ok) { $("auth-error").textContent = data.detail || "操作失败"; return; }
    state.token = data.token;
    localStorage.setItem("gaokao_token", data.token);
    state.user = data.user;
    renderUser();
    hideAuth();
    toast(authMode === "login" ? "登录成功" : "注册成功");
  } catch (err) {
    $("auth-error").textContent = "网络错误，请稍后再试";
  }
}

function logout() {
  state.token = "";
  localStorage.removeItem("gaokao_token");
  state.user = null;
  renderUser();
  $("essay-list").innerHTML = "";
  $("essay-detail").innerHTML = "";
  $("essay-detail").classList.add("empty");
  toast("已退出登录");
}

/* ---------------- 生成（SSE 流式） ---------------- */

async function startGenerate(e) {
  e.preventDefault();
  if (state.generating) return;
  if (!ensureAuth()) return;

  const topic = $("topic").value.trim();
  if (!topic || topic.length < 2) { toast("请先填写作文题目"); return; }

  const body = {
    topic,
    material: $("material").value.trim(),
    requirement: $("requirement").value.trim(),
    genre: $("genre").value,
    word_count: Number($("word-count").value),
    angle_count: Number($("angle-count").value),
    do_critique: $("do-critique").checked,
  };

  state.generating = true;
  $("start-gen").disabled = true;
  $("start-gen").textContent = "生成中…";
  setStatus("running", "生成中");
  resetPanel();

  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + state.token,
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || ("生成请求失败：" + res.status));
    }
    await consumeSse(res.body, handleEvent);
  } catch (err) {
    setStatus("error", "失败");
    showPanelError(err.message || String(err));
  } finally {
    state.generating = false;
    $("start-gen").disabled = false;
    $("start-gen").textContent = "开始生成";
    if (state.statusText !== "error") setStatus("muted", "完成");
  }
}

function resetPanel() {
  $("timeline").innerHTML = "";
  $("essay-title").textContent = "";
  const body = $("essay-body");
  body.textContent = "生成的正文会在这里流式出现。";
  body.classList.add("empty");
  $("score-card").classList.add("hidden");
  $("score-card").innerHTML = "";
  state.stageMap = {};
  state.currentStage = null;
  state.lastEssayId = null;
  state.typewriter.queue = "";
  if (state.typewriter.timer) { clearInterval(state.typewriter.timer); state.typewriter.timer = null; }
}

function setStatus(kind, text) {
  const el = $("gen-status");
  el.textContent = text;
  el.className = "status " + kind;
  state.statusText = kind;
}

function showPanelError(msg) {
  const body = $("essay-body");
  body.textContent = "⚠ " + msg;
  body.classList.remove("empty");
  body.classList.add("error");
}

/* 手动解析 SSE：按 "\n\n" 分帧，跨 chunk 的半条 data 行攒在 buffer 里 */
async function consumeSse(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      parseSseLine(raw, onEvent);
    }
  }
  // 流结束时 flush 剩余的半条帧（正常协议末尾应有 \n\n，这里做兜底）
  if (buffer.trim()) parseSseLine(buffer, onEvent);
}

function parseSseLine(raw, onEvent) {
  const line = raw.trim();
  if (!line.startsWith("data:")) return;
  const payload = line.slice(5).trim();
  if (!payload) return;
  let evt;
  try { evt = JSON.parse(payload); } catch (err) { return; }
  onEvent(evt);
}

/* ---------------- SSE 事件处理 ---------------- */

function handleEvent(evt) {
  switch (evt.type) {
    case "stage": handleStageEvent(evt); break;
    case "delta": handleDelta(evt); break;
    case "thinking": handleThinking(evt); break;
    case "meta": handleMeta(evt); break;
    case "saved": state.lastEssayId = evt.essay_id; toast("作文已保存到历史记录"); break;
    case "error": setStatus("error", "失败"); showPanelError(evt.message || "生成失败"); break;
    // 出错后仍会收到 done 收尾事件，此时保留「失败」状态而不是覆盖成「完成」
    case "done": if (state.statusText !== "error") setStatus("muted", "完成"); break;
  }
}

function handleStageEvent(evt) {
  if (evt.status === "start") {
    state.currentStage = evt.stage;
    ensureStageEl(evt.stage, evt.title);
    setStageState(evt.stage, "进行中");
    // 正文与润色两阶段都会输出整篇，切到润色时清空正文面板重新打字
    if (evt.stage === "essay" || evt.stage === "polish") resetEssayBody();
  } else if (evt.status === "done") {
    const rec = state.stageMap[evt.stage];
    if (rec) {
      rec.content = evt.content || rec.content;
      setStageBody(evt.stage, rec.content);
      setStageState(evt.stage, "完成");
    }
  }
}

/* 推理模型会先“想”再“写”：思考阶段只报进度，避免页面长时间空白 */
function handleThinking(evt) {
  const rec = state.stageMap[evt.stage];
  if (rec) {
    const el = rec.el.querySelector(".stage-state");
    if (el) el.textContent = "思考 " + evt.chars + " 字";
  }
  setStatus("running", "模型思考中…");
}

function handleDelta(evt) {
  const rec = state.stageMap[evt.stage] || ensureStageEl(evt.stage, evt.title || evt.stage);
  rec.content += evt.text || "";
  // 正文阶段才走打字机，审题/提纲/评分先攒着，等 done 一次性展示
  if (evt.stage === "essay" || evt.stage === "polish") {
    typewriterPush(evt.text || "");
  }
}

function handleMeta(evt) {
  if (evt.title) $("essay-title").textContent = evt.title;
  if (evt.content) {
    typewriterFlush();
    const body = $("essay-body");
    body.textContent = evt.content;
    body.classList.remove("empty", "error");
    body.scrollTop = 0;
  }
  // 只有真正跑过评分（有总分或解析失败标记）才展示评分卡片，避免 do_critique=false 时空卡片
  if (evt.score && typeof evt.score === "object" && (evt.score.total != null || evt.score.error)) {
    $("score-card").innerHTML = scoreHTML(evt.score);
    $("score-card").classList.remove("hidden");
  }
}

/* ---------------- 时间轴与打字机 ---------------- */

function ensureStageEl(stage, title) {
  if (state.stageMap[stage]) return state.stageMap[stage];
  const item = document.createElement("div");
  item.className = "stage-item active";
  item.innerHTML =
    '<div class="stage-head">' +
      '<span class="stage-dot"></span>' +
      '<span class="stage-title">' + esc(title || stage) + '</span>' +
      '<span class="stage-state">等待</span>' +
    '</div>' +
    '<div class="stage-body"></div>';
  // 点击阶段头部可展开/收起该阶段的完整输出
  item.querySelector(".stage-head").addEventListener("click", () => item.classList.toggle("open"));
  $("timeline").appendChild(item);
  state.stageMap[stage] = { el: item, bodyEl: item.querySelector(".stage-body"), content: "" };
  return state.stageMap[stage];
}

function setStageState(stage, text) {
  const rec = state.stageMap[stage];
  if (!rec) return;
  rec.el.querySelector(".stage-state").textContent = text;
  if (text === "完成") { rec.el.classList.remove("active"); rec.el.classList.add("done"); }
  if (text === "失败") { rec.el.classList.remove("active"); rec.el.classList.add("error"); }
}

function setStageBody(stage, content) {
  const rec = state.stageMap[stage];
  if (!rec) return;
  // 输出默认收起，点击阶段头部再展开，保持时间轴紧凑
  rec.bodyEl.textContent = content || "（本阶段无输出）";
}

function resetEssayBody() {
  const body = $("essay-body");
  body.textContent = "";
  body.classList.remove("empty", "error");
  typewriterFlush();
}

function typewriterPush(text) {
  state.typewriter.queue += text;
  if (state.typewriter.timer) return;
  // 12ms 每拍出 2 个字符，快慢均衡，既像打字机又不会让用户等太久
  state.typewriter.timer = setInterval(() => {
    const queue = state.typewriter.queue;
    if (!queue.length) {
      clearInterval(state.typewriter.timer);
      state.typewriter.timer = null;
      return;
    }
    const chunk = queue.slice(0, 2);
    state.typewriter.queue = queue.slice(2);
    const body = $("essay-body");
    body.textContent += chunk;
    body.scrollTop = body.scrollHeight;
  }, 12);
}

function typewriterFlush() {
  if (state.typewriter.queue) {
    const body = $("essay-body");
    body.textContent += state.typewriter.queue;
    state.typewriter.queue = "";
  }
  if (state.typewriter.timer) { clearInterval(state.typewriter.timer); state.typewriter.timer = null; }
}

/* ---------------- 评分可视化 ---------------- */

function scoreHTML(score) {
  if (!score || score.error) {
    return '<p class="hint">评分未生成：' + esc(score && score.error ? score.error : "未知") + "</p>";
  }
  const base = score.base || {};
  const dev = score.development || {};
  const bars = [
    ["内容", base.content, 20],
    ["表达", base.expression, 20],
    ["深刻", dev.deep, 5],
    ["丰富", dev.rich, 5],
    ["有文采", dev.literary, 5],
    ["有创意", dev.creative, 5],
  ];
  const barRows = bars.map(([label, val, max]) => {
    const v = Number(val) || 0;
    const pct = Math.max(0, Math.min(100, (v / max) * 100));
    return (
      '<div class="bar-row"><span>' + label + '</span>' +
      '<div class="bar-track"><div class="bar-fill ' + (max <= 5 ? "gold" : "") + '" style="width:' + pct + '%"></div></div>' +
      '<span class="bar-value">' + v + "/" + max + "</span></div>"
    );
  }).join("");

  const list = (items, cls, title) =>
    items && items.length
      ? '<div class="score-list ' + cls + '"><h4>' + title + "</h4><ul>" +
        items.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul></div>"
      : "";

  return (
    '<div class="score-head">' +
      '<div class="score-total">' + esc(score.total ?? "—") + '<small> / 60</small></div>' +
      (score.verdict ? '<span class="verdict">' + esc(score.verdict) + "</span>" : "") +
    "</div>" +
    (score.comment ? '<p class="score-comment">' + esc(score.comment) + "</p>" : "") +
    '<div class="bars">' + barRows + "</div>" +
    '<div class="score-lists">' +
      list(score.highlights, "highlights", "亮点") +
      list(score.flaws, "flaws", "硬伤") +
      list(score.suggestions, "suggestions", "修改建议") +
    "</div>"
  );
}

/* ---------------- 范文素材检索 ---------------- */

function openDrawer() {
  $("corpus-drawer").classList.add("open");
  $("drawer-mask").classList.remove("hidden");
}

function closeDrawer() {
  $("corpus-drawer").classList.remove("open");
  $("drawer-mask").classList.add("hidden");
}

async function searchCorpus() {
  const query = $("corpus-query").value.trim();
  if (!query) { toast("请输入检索关键词"); return; }
  const box = $("corpus-results");
  box.innerHTML = '<p class="hint">检索中…</p>';
  try {
    const res = await request("/api/corpus/search", { method: "POST", body: { query, top_k: 5 } });
    const data = await res.json();
    renderCorpus(data.results || []);
  } catch (err) {
    box.innerHTML = '<p class="hint">检索失败</p>';
  }
}

function renderCorpus(results) {
  const box = $("corpus-results");
  if (!results.length) { box.innerHTML = '<p class="hint">没有匹配的素材，换个关键词试试。</p>'; return; }
  box.innerHTML = results.map((r, i) =>
    '<div class="corpus-item">' +
      '<div class="corpus-item-title">' + esc(r.doc_title) + "</div>" +
      '<div class="corpus-item-score">相关度 ' + esc(r.score) + "</div>" +
      '<div class="corpus-item-text">' + esc(r.text) + "</div>" +
      '<div class="corpus-item-actions">' +
        '<button class="btn btn-primary btn-sm" data-insert="' + i + '">插入参考素材</button>' +
      "</div>" +
    "</div>"
  ).join("");
  box.querySelectorAll("[data-insert]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const r = results[Number(btn.dataset.insert)];
      const ta = $("material");
      ta.value = (ta.value.trim() ? ta.value + "\n\n" : "") + "[参考素材] " + r.text;
      toast("已插入素材，可继续编辑材料框");
    });
  });
}

/* ---------------- 历史记录 ---------------- */

async function loadEssays() {
  if (!ensureAuth()) return;
  const list = $("essay-list");
  list.innerHTML = '<li class="list-empty">加载中…</li>';
  try {
    const res = await request("/api/essays?limit=100");
    if (!res.ok) { list.innerHTML = '<li class="list-empty">登录后查看历史记录</li>'; return; }
    const data = await res.json();
    renderEssayList(data.items || []);
  } catch (err) {
    list.innerHTML = '<li class="list-empty">加载失败</li>';
  }
}

function renderEssayList(items) {
  const list = $("essay-list");
  if (!items.length) { list.innerHTML = '<li class="list-empty">还没有生成过作文，去工作台试试。</li>'; return; }
  list.innerHTML = items.map((it) =>
    '<li class="essay-item' + (it.id === state.selectedEssayId ? " selected" : "") + '" data-id="' + it.id + '">' +
      '<div class="essay-item-title">' + (it.favorite ? '<span class="fav-star">★</span> ' : "") + esc(it.title) + "</div>" +
      '<div class="essay-item-meta">' +
        "<span>" + esc(it.genre) + "</span>" +
        "<span>" + esc(it.word_count) + " 字</span>" +
        (it.score != null ? "<span>评分 " + esc(it.score) + "</span>" : "") +
        "<span>" + esc(it.created_at) + "</span>" +
      "</div>" +
    "</li>"
  ).join("");
  list.querySelectorAll(".essay-item").forEach((li) => {
    li.addEventListener("click", () => loadEssayDetail(Number(li.dataset.id)));
  });
}

async function loadEssayDetail(id) {
  state.selectedEssayId = id;
  document.querySelectorAll(".essay-item").forEach((li) =>
    li.classList.toggle("selected", Number(li.dataset.id) === id)
  );
  const box = $("essay-detail");
  box.classList.remove("empty");
  box.innerHTML = '<p class="hint">加载中…</p>';
  try {
    const res = await request("/api/essays/" + id);
    if (!res.ok) return;
    const essay = await res.json();
    state.currentEssay = essay;
    renderEssayDetail(essay);
  } catch (err) {
    box.innerHTML = '<p class="hint">加载失败</p>';
  }
}

function renderEssayDetail(essay) {
  const box = $("essay-detail");
  const score = essay.score_json || {};
  box.innerHTML =
    '<div class="detail-head">' +
      "<div>" +
        "<h3>" + esc(essay.title) + "</h3>" +
        '<p class="hint">' + esc(essay.genre) + " · " + esc(essay.word_count) + " 字 · " + esc(essay.created_at) + "</p>" +
      "</div>" +
      '<div class="detail-actions">' +
        '<button class="btn btn-ghost btn-sm" id="d-fav">' + (essay.favorite ? "★ 取消收藏" : "☆ 收藏") + "</button>" +
        '<button class="btn btn-ghost btn-sm" id="d-md">导出 Markdown</button>' +
        '<button class="btn btn-ghost btn-sm" id="d-docx">导出 DOCX</button>' +
        '<button class="btn btn-danger btn-sm" id="d-del">删除</button>' +
      "</div>" +
    "</div>" +
    (score && score.total != null ? '<div class="detail-block">' + scoreHTML(score) + "</div>" : "") +
    '<div class="detail-block"><h4>正文</h4><div class="txt content">' + esc(essay.content) + "</div></div>" +
    (essay.outline ? '<div class="detail-block"><h4>结构提纲</h4><div class="txt">' + esc(essay.outline) + "</div></div>" : "") +
    (essay.critique ? '<div class="detail-block"><h4>评分自检原文</h4><div class="txt">' + esc(essay.critique) + "</div></div>" : "");

  $("d-fav").addEventListener("click", async () => {
    const res = await request("/api/essays/" + essay.id + "/favorite", { method: "POST", body: { favorite: !essay.favorite } });
    if (res.ok) { toast(essay.favorite ? "已取消收藏" : "已收藏"); loadEssayDetail(essay.id); loadEssays(); }
  });
  $("d-md").addEventListener("click", () => exportEssay(essay.id, "md"));
  $("d-docx").addEventListener("click", () => exportEssay(essay.id, "docx"));
  $("d-del").addEventListener("click", async () => {
    if (!confirm("确定删除这篇作文？此操作不可恢复。")) return;
    const res = await request("/api/essays/" + essay.id, { method: "DELETE" });
    if (res.ok) {
      toast("已删除");
      state.selectedEssayId = null;
      state.currentEssay = null;
      $("essay-detail").innerHTML = "";
      $("essay-detail").classList.add("empty");
      loadEssays();
    }
  });
}

async function exportEssay(id, fmt) {
  try {
    const res = await fetch("/api/essays/" + id + "/export?fmt=" + fmt, {
      headers: { "Authorization": "Bearer " + state.token },
    });
    if (!res.ok) { toast("导出失败：" + res.status); return; }
    const blob = await res.blob();
    let filename = "essay-" + id + (fmt === "docx" ? ".docx" : ".md");
    const cd = res.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename\*=UTF-8''([^;]+)/);
    if (m) filename = decodeURIComponent(m[1]);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast("已导出 " + filename);
  } catch (err) {
    toast("导出失败");
  }
}

/* ---------------- 提示词页 ---------------- */

async function loadPrompts() {
  if (state.prompts) { renderPrompts(state.prompts); return; }
  try {
    const res = await fetch("/api/prompts");
    const data = await res.json();
    state.prompts = data;
    renderPrompts(data);
  } catch (err) {
    $("prompt-stages").innerHTML = '<p class="hint">提示词加载失败</p>';
  }
}

function renderPrompts(data) {
  const strategies = data.strategies || [];
  $("strategies").innerHTML = strategies.map((s) =>
    '<div class="strategy"><h4>' + esc(s.name) + "</h4><p>" + esc(s.desc) + "</p></div>"
  ).join("");

  const stages = data.stages || [];
  $("prompt-stages").innerHTML = stages.map((st) =>
    '<div class="prompt-stage">' +
      '<div class="prompt-stage-head">' +
        '<h4>' + esc(st.title) + '</h4><span class="prompt-key">' + esc(st.key) + '</span>' +
        '<span class="arrow">▸</span>' +
      "</div>" +
      '<div class="prompt-stage-body">' +
        '<div class="prompt-block"><h5>system</h5><pre>' + esc(st.system) + '</pre>' +
          '<button class="btn btn-ghost btn-sm copy-btn" data-copy="system:' + esc(st.key) + '">复制 system</button></div>' +
        '<div class="prompt-block"><h5>user_template</h5><pre>' + esc(st.user_template) + '</pre>' +
          '<button class="btn btn-ghost btn-sm copy-btn" data-copy="user:' + esc(st.key) + '">复制 user</button></div>' +
      "</div>" +
    "</div>"
  ).join("");

  $("prompt-stages").querySelectorAll(".prompt-stage-head").forEach((head) => {
    head.addEventListener("click", () => head.parentElement.classList.toggle("open"));
  });
  $("prompt-stages").querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const [which, key] = btn.dataset.copy.split(":");
      const stage = (data.stages || []).find((s) => s.key === key);
      if (!stage) return;
      const text = which === "system" ? stage.system : stage.user_template;
      navigator.clipboard.writeText(text).then(
        () => toast("已复制"),
        () => toast("复制失败，请手动选择复制")
      );
    });
  });
}

/* ---------------- 事件绑定 ---------------- */

function bindEvents() {
  // 顶栏视图切换
  document.querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => switchView(t.dataset.view));
  });
  $("auth-btn").addEventListener("click", () => (state.user ? logout() : showAuth("login")));
  $("refresh-essays").addEventListener("click", loadEssays);

  // 认证
  $("close-auth").addEventListener("click", hideAuth);
  $("tab-login").addEventListener("click", () => showAuth("login"));
  $("tab-register").addEventListener("click", () => showAuth("register"));
  $("auth-form").addEventListener("submit", submitAuth);

  // 工作台
  $("gen-form").addEventListener("submit", startGenerate);
  $("angle-count").addEventListener("input", () => { $("angle-val").textContent = $("angle-count").value; });
  $("search-corpus").addEventListener("click", openDrawer);
  $("close-drawer").addEventListener("click", closeDrawer);
  $("drawer-mask").addEventListener("click", closeDrawer);
  $("corpus-search-btn").addEventListener("click", searchCorpus);
  $("corpus-query").addEventListener("keydown", (e) => { if (e.key === "Enter") searchCorpus(); });
}

document.addEventListener("DOMContentLoaded", init);
