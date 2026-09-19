const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  me: null,
  counts: null,
  privateStars: [],
  sharedStars: [],
  requests: { incoming: [], outgoing: [] },
  offers: { incoming: [], outgoing: [] },
  session: null,
  notifications: [],
  specialDates: [],
  sessions: [],
  selectedSharedId: null,
  selectedShared: null,
  unreadCount: 0,
};

const titles = {
  observatory: ["三只瓶子", "只看得到该看见的，剩下的交给未来。"],
  write: ["写一颗", "把这一刻留下，不需要把它写成一封信。"],
  flows: ["递与拆", "请求、递出、接住和一起拆，都在这里发生。"],
  shared: ["我们的星轨", "已经被两个人知道的星，才会留在这条轨迹上。"],
};

// F01：进入页面时先记住详情面板的初始结构，退出/换号时恢复成它
const inspectorInitialHtml = $("#starInspector").innerHTML;

// F01：同一会话内的所有请求挂在同一个 AbortController 上，
// 退出/换号时统一取消，旧响应不允许再回填新身份的界面。
let apiAbort = new AbortController();

// F10：上次因网络失败没能确认结果的写操作。用户重试同一动作时
// 复用同一个 Idempotency-Key，服务端直接返回已保存的那条结果。
let retryCandidate = null;

// F12：轻量轮询未读数，另一台设备写星后这里也能看到变化
let unreadPollTimer = null;

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function icon(name) {
  return `<svg aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}

function formatDate(value, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", withTime
    ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }
    : { year: "numeric", month: "2-digit", day: "2-digit" }
  ).format(date);
}

function sourceName(origin) {
  return {
    visible_from_start: "一开始就给我们",
    revealed_by_request: "对方请求后拆开",
    revealed_by_offer: "主动递出",
    revealed_by_anniversary: "纪念日拆开",
    revealed_by_special_day: "特殊日拆开",
  }[origin] || "已共享";
}

function stateName(star) {
  if (star.being_offered) return "递送中";
  if (star.waiting_random_delivery) return "已被请求锁定";
  if (star.in_active_session) return "本轮拆星中";
  return "封存中";
}

function requestStatusName(status) {
  return { pending: "等对方决定", approved: "已经给你一颗", rejected: "这次先不给", cancelled: "已取消" }[status] || status;
}

function sessionLabel(sess) {
  if (sess.special_date_name) {
    return `${sess.type === "anniversary" ? "纪念日" : "特殊日"} · ${sess.special_date_name}`;
  }
  return sess.type === "anniversary" ? "纪念日批次" : "特殊日批次";
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2600);
}

function celebrateReveal() {
  const app = $("#appView");
  app.classList.remove("reveal-pulse");
  void app.offsetWidth;
  app.classList.add("reveal-pulse");
  setTimeout(() => app.classList.remove("reveal-pulse"), 1000);
}

function newOperationKey() {
  return crypto.randomUUID ? crypto.randomUUID() : `op-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const mutating = method !== "GET" && method !== "HEAD";
  const bodyKey = options.body === undefined ? "" : JSON.stringify(options.body);
  const init = { credentials: "same-origin", ...options, signal: apiAbort.signal };
  if (mutating) {
    let opKey;
    if (retryCandidate && retryCandidate.path === path && retryCandidate.method === method
        && retryCandidate.bodyKey === bodyKey) {
      opKey = retryCandidate.opKey;
    } else {
      opKey = newOperationKey();
    }
    init.headers = { ...(init.headers || {}), "Idempotency-Key": opKey };
  }
  if (init.body && typeof init.body !== "string") {
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
    init.body = JSON.stringify(init.body);
  }
  let response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch (networkError) {
    if (networkError.name === "AbortError") {
      // 会话已被重置：这个请求属于上一个身份，永远不要把结果交给调用方
      return new Promise(() => {});
    }
    if (mutating) retryCandidate = { path, method, bodyKey, opKey: init.headers["Idempotency-Key"] };
    throw networkError;
  }
  retryCandidate = null;
  let data = null;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/")) showLogin();
    const message = data?.message || data?.detail || `请求失败（${response.status}）`;
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

async function busy(button, task) {
  if (!button) return task();
  const wasDisabled = button.disabled;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try { return await task(); }
  finally { button.disabled = wasDisabled; button.removeAttribute("aria-busy"); }
}

function resetForNewIdentity() {
  // F01：退出 / 401 / 换号时清空全部业务状态与界面残留——
  // 草稿、正在编辑的隐藏星、详情、列表、筛选、未读角标，一个不留。
  apiAbort.abort();
  apiAbort = new AbortController();
  retryCandidate = null;
  stopUnreadPolling();
  Object.assign(state, {
    me: null,
    counts: null,
    privateStars: [],
    sharedStars: [],
    requests: { incoming: [], outgoing: [] },
    offers: { incoming: [], outgoing: [] },
    session: null,
    notifications: [],
    specialDates: [],
    sessions: [],
    selectedSharedId: null,
    selectedShared: null,
    unreadCount: 0,
  });
  $("#writeForm").reset();
  $("#editingStarId").value = "";
  $("#contentCount").textContent = "0";
  $("#writeHeading").textContent = "写一颗星";
  $("#saveStarButton span").textContent = "放进瓶子";
  $("#visibilityChoice").hidden = false;
  $("#cancelEditButton").hidden = true;
  $("#writeError").textContent = "";
  $("#starInspector").innerHTML = inspectorInitialHtml;
  $("#notificationPanel").hidden = true;
  $("#notificationButton").setAttribute("aria-expanded", "false");
  $("#sharedFilters").reset();
  $("#requestHint").textContent = "";
  $("#notificationBadge").hidden = true;
  $("#flowBadge").hidden = true;
  ["#privateStars", "#sharedRecent", "#sharedList", "#requestList", "#offerList",
   "#sessionContent", "#notificationList"].forEach((selector) => { $(selector).innerHTML = ""; });
  // 被中断的 busy() 永远走不到 finally，手动恢复按钮可用
  $$("#appView button").forEach((button) => {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    delete button.dataset.confirm;
    button.style.color = "";
  });
  switchView("observatory", { reload: false });
}

function showLogin() {
  resetForNewIdentity();
  $("#appView").hidden = true;
  $("#loginView").hidden = false;
}

function showApp() {
  $("#loginView").hidden = true;
  $("#appView").hidden = false;
}

function switchView(view, { reload = true } = {}) {
  $$(`[data-view]`).forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $$(`[data-view-panel]`).forEach((panel) => {
    const active = panel.dataset.viewPanel === view;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  const [title, subtitle] = titles[view];
  $("#viewTitle").textContent = title;
  $("#viewSubtitle").textContent = subtitle;
  if (reload) {
    if (view === "shared") loadShared();
    if (view === "flows") refreshFlows();
    refreshUnreadBadge();
  }
  window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

function renderIdentity() {
  if (!state.me) return;
  $("#identityName").textContent = state.me.name;
  $("#partnerName").textContent = state.me.partner_name;
  $("#identityInitial").textContent = state.me.name.slice(0, 1);
  $("#partnerBottleHeading").textContent = `${state.me.partner_name}的瓶子`;
  $("#filterAuthor").innerHTML = `<option value="">全部</option><option value="${escapeHtml(state.me.id)}">${escapeHtml(state.me.name)}</option><option value="${escapeHtml(state.me.partner_id)}">${escapeHtml(state.me.partner_name)}</option>`;
}

function renderBottleStars(count) {
  const holder = $("#myBottleStars");
  holder.innerHTML = "";
  const visible = Math.min(count, 18);
  for (let i = 0; i < visible; i += 1) {
    const dot = document.createElement("i");
    const x = 8 + ((i * 37) % 84);
    const y = 8 + ((i * 29) % 78);
    dot.style.left = `${x}%`;
    dot.style.top = `${y}%`;
    holder.append(dot);
  }
}

function renderCounts() {
  const c = state.counts;
  if (!c) return;
  $("#myPrivateCount").textContent = c.my_private_count;
  $("#partnerPrivateCount").textContent = c.partner_private_count;
  $("#sharedCount").textContent = c.shared_count;
  $("#requestStarButton").disabled = c.partner_private_count === 0;
  $("#requestHint").textContent = c.partner_private_count === 0 ? "对方的瓶子现在是空的。" : "随机由服务端完成，拿到后不能换。";
  renderBottleStars(c.my_private_count);
}

function renderPrivateStars() {
  const root = $("#privateStars");
  // F07：不再截断最近六颗——这里渲染作者全部可管理的私人星，容器内滚动。
  // 产品规则：星星可以改，不能删——所以只有查看、编辑和递出，没有删除。
  // 列表本身只是摘要，不产生查看足迹；足迹只在显式点开"详情"时累计。
  if (!state.privateStars.length) {
    root.innerHTML = `<p class="empty-line">你的私人瓶子现在是空的。写一颗藏起来吧。</p>`;
    return;
  }
  root.innerHTML = state.privateStars.map((star) => `
    <article class="private-item" data-private-id="${escapeHtml(star.id)}">
      <div>
        <p>${escapeHtml(star.content)}</p>
        <div class="private-meta"><span class="state-dot ${star.state === "SEALED" ? "" : "busy"}"></span><span>${escapeHtml(stateName(star))}</span><span>${formatDate(star.written_at)}</span></div>
        <div class="private-detail" hidden></div>
      </div>
      <div class="private-actions">
        <button data-action="view-private" title="查看详情（留足迹）">${icon("open")}</button>
        <button data-action="edit-private" title="编辑" ${star.state !== "SEALED" ? "disabled" : ""}>${icon("edit")}</button>
        <button data-action="offer-private" title="递给对方（可捎一句话）" ${star.state !== "SEALED" ? "disabled" : ""}>${icon("send")}</button>
      </div>
    </article>`).join("");
}

function renderSharedRecent() {
  const root = $("#sharedRecent");
  const items = state.sharedStars.slice(0, 6);
  if (!items.length) {
    root.innerHTML = `<p class="empty-line">还没有共享星。第一颗可见星会从这里开始。</p>`;
    return;
  }
  root.innerHTML = items.map((star) => `
    <article class="track-item" data-shared-id="${escapeHtml(star.id)}" tabindex="0" role="button">
      <time>${formatDate(star.shared_at || star.created_at)}</time>
      <p>${escapeHtml(star.content)}</p>
      <small>${escapeHtml(star.author_name || star.author_id)} · ${escapeHtml(sourceName(star.shared_origin))}</small>
    </article>`).join("");
}

function renderRequests() {
  const root = $("#requestList");
  const rows = [];
  for (const req of state.requests.incoming) {
    // 用户规则：给的时候可以指定给哪一颗，不选就随机
    const sealed = state.privateStars.filter((s) => s.state === "SEALED");
    const picker = req.status === "pending" && sealed.length
      ? `<select class="give-picker" data-give-picker aria-label="给哪一颗"><option value="">随机给一颗</option>${sealed.map((s) => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.content.slice(0, 18))}${s.content.length > 18 ? "…" : ""}</option>`).join("")}</select>`
      : "";
    rows.push(`
      <article class="action-item" data-request-id="${escapeHtml(req.id)}">
        <p>${escapeHtml(state.me.partner_name)} 想要一颗</p>
        <small>${formatDate(req.requested_at, true)} · ${escapeHtml(requestStatusName(req.status))}</small>
        ${req.status === "pending" ? `<div class="action-row">${picker}<button class="mini-button primary" data-action="request-give">给一颗</button><button class="mini-button" data-action="request-decline">现在先不给</button></div>` : ""}
      </article>`);
  }
  for (const req of state.requests.outgoing) {
    let actions = "";
    if (req.status === "approved" && !req.opened_at) actions = `<button class="mini-button primary" data-action="request-open">接住并打开</button>`;
    if (req.opened_star_id) actions = `<button class="mini-button" data-action="request-view" data-star-id="${escapeHtml(req.opened_star_id)}">看这颗星</button>`;
    rows.push(`
      <article class="action-item" data-request-id="${escapeHtml(req.id)}">
        <p>我向 ${escapeHtml(state.me.partner_name)} 请求一颗</p>
        <small>${formatDate(req.requested_at, true)} · ${escapeHtml(requestStatusName(req.status))}</small>
        ${actions ? `<div class="action-row">${actions}</div>` : ""}
      </article>`);
  }
  root.innerHTML = rows.join("") || `<p class="empty-line">还没有请求记录。</p>`;
}

function renderOffers() {
  const root = $("#offerList");
  const rows = [];
  for (const offer of state.offers.incoming) {
    rows.push(`
      <article class="action-item" data-offer-id="${escapeHtml(offer.id)}">
        <p>${escapeHtml(state.me.partner_name)} 递给你一颗星</p>
        ${offer.message ? `<p class="offer-message">“${escapeHtml(offer.message)}”</p>` : ""}
        <small>${formatDate(offer.offered_at, true)} · ${offer.status === "offered" ? "等你接住" : "已经接住"}</small>
        ${offer.status === "offered" ? `<div class="action-row"><button class="mini-button primary" data-action="offer-accept">接住并打开</button></div>` : ""}
      </article>`);
  }
  for (const offer of state.offers.outgoing) {
    rows.push(`
      <article class="action-item" data-offer-id="${escapeHtml(offer.id)}">
        <p>我递出了一颗星</p>
        ${offer.message ? `<p class="offer-message">捎的话：“${escapeHtml(offer.message)}”</p>` : ""}
        <small>${formatDate(offer.offered_at, true)} · ${offer.status === "offered" ? "等对方接住" : "对方已经接住"}</small>
      </article>`);
  }
  root.innerHTML = rows.join("") || `<p class="empty-line">还没有递星记录。</p>`;
}

function specialDateLabel(d) {
  return `${String(d.month).padStart(2, "0")}-${String(d.day).padStart(2, "0")}${d.year ? `-${d.year}` : ""} · ${d.type === "anniversary" ? "纪念日" : "自定义"}`;
}

// 发起拆星必须绑定具体日期；纪念日（anniversary 类型）与自定义特殊日
// （custom 类型）分别呈现，session 类型由服务器按日期数据派生。
function sessionDateOptions() {
  const anniversaries = state.specialDates.filter((d) => d.type === "anniversary");
  const customs = state.specialDates.filter((d) => d.type !== "anniversary");
  const group = (label, items) => items.length
    ? `<optgroup label="${label}">${items.map((d) => `<option value="${escapeHtml(d.id)}">${escapeHtml(d.name)}（${escapeHtml(specialDateLabel(d))}）</option>`).join("")}</optgroup>`
    : "";
  return group("纪念日", anniversaries) + group("自定义特殊日", customs);
}

function renderSession() {
  const root = $("#sessionContent");
  const badge = $("#sessionStatus");
  const wrapper = state.session;
  const session = wrapper?.session;
  const dateNameOf = (id) => {
    const d = state.specialDates.find((x) => x.id === id);
    return d ? d.name : null;
  };
  if (!session) {
    badge.textContent = "没有进行中";
    const options = sessionDateOptions();
    const anniversaries = state.specialDates.filter((d) => d.type === "anniversary");
    const customs = state.specialDates.filter((d) => d.type !== "anniversary");
    const listHtml = (items) => items.map((d) => `<li><b>${escapeHtml(d.name)}</b><span>${escapeHtml(specialDateLabel(d))}</span></li>`).join("") || `<li class="empty-line">还没有。</li>`;
    root.innerHTML = `<div class="session-body">
      <div class="session-constellation" aria-hidden="true"><i></i></div>
      <p>发起后要等对方确认。选择一个具体日期：纪念日的交换额度按当天 0 点时瓶里的星数冻结（0 点后新写的星这一轮不算）；自定义特殊日则按确认那一刻的数量。都只能在自己瓶里有星时看对方的星。</p>
      <div class="session-actions">
        <label class="session-date-pick">选日期<select id="sessionDateSelect">${options || `<option value="">先在下面添加一个</option>`}</select></label>
        <button class="button secondary" data-action="session-start" ${options ? "" : "disabled"}>发起一起拆星</button>
      </div>
      <section class="special-dates-box" aria-labelledby="specialDatesHeading">
        <h3 id="specialDatesHeading">纪念日与特殊日</h3>
        <p class="box-sub">纪念日</p>
        <ul class="special-date-list">${listHtml(anniversaries)}</ul>
        <p class="box-sub">自定义特殊日</p>
        <ul class="special-date-list">${listHtml(customs)}</ul>
        <form id="specialDateForm" class="special-date-form">
          <input name="name" maxlength="100" required placeholder="名字，如：第一次看海" aria-label="特殊日名字">
          <input name="month" type="number" min="1" max="12" required placeholder="月" aria-label="月">
          <input name="day" type="number" min="1" max="31" required placeholder="日" aria-label="日">
          <input name="year" type="number" min="1" max="9999" placeholder="年（可空）" aria-label="年（可空）">
          <select name="type" aria-label="类型"><option value="custom">自定义</option><option value="anniversary">纪念日</option></select>
          <button class="mini-button primary" type="submit">添加</button>
        </form>
        <p class="form-error" id="specialDateError" role="alert"></p>
      </section>
    </div>`;
    return;
  }
  if (session.status === "waiting_confirmation") {
    badge.textContent = "等待确认";
    const invited = session.initiated_by !== state.me.id;
    const bound = dateNameOf(session.special_date_id);
    const kindLabel = session.type === "anniversary" ? "纪念日" : "特殊日";
    root.innerHTML = `<div class="session-body"><div class="session-constellation" aria-hidden="true"><i></i></div><p>${invited
      ? `${escapeHtml(state.me.partner_name)} 邀请你一起看星星${bound ? `（${kindLabel} · ${escapeHtml(bound)}）` : ""}。你同意后就可以互相看对方的瓶子。`
      : `邀请已经发给 ${escapeHtml(state.me.partner_name)}${bound ? `（${kindLabel} · ${escapeHtml(bound)}）` : ""}，对方确认前谁都不能看；也可以现在取消。`}</p><div class="session-actions">${invited
      ? `<button class="button primary" data-action="session-confirm">同意，一起看</button><button class="button quiet" data-action="session-decline">这次先不看</button>`
      : `<button class="button quiet" data-action="session-cancel">取消邀请</button>`}</div></div>`;
    return;
  }
  badge.textContent = `进行中 · 已看 ${wrapper.my_viewed ?? 0}/${wrapper.my_quota ?? 0}`;
  const boundActive = dateNameOf(session.special_date_id);
  const remainingQuota = Math.max(0, (wrapper.my_quota ?? 0) - (wrapper.my_viewed ?? 0));
  const isAnniversary = session.type === "anniversary";
  const quotaText = isAnniversary
    ? `本轮额度按${boundActive ? `「${escapeHtml(boundActive)}」` : "纪念日"}当天 0 点冻结`
    : "额度按确认那一刻瓶里的星数固定";
  root.innerHTML = `<div class="session-body"><div class="session-constellation" aria-hidden="true"><i></i></div><p>你的额度还剩 <b>${remainingQuota}</b> 颗（${quotaText}，已看 ${wrapper.my_viewed ?? 0} 颗；对方本轮还有 ${wrapper.partner_remaining ?? 0} 颗可看）。每看一颗都会直接进入我们的瓶子，记得给每一颗留句话。</p><div class="session-actions"><button class="button primary" data-action="session-take">看对方一颗</button><button class="button quiet" data-action="session-finish">结束本轮</button></div></div>`;
}

function renderSessionFilter() {
  const select = $("#filterSession");
  if (!select) return;
  const current = select.value;
  select.innerHTML = `<option value="">全部批次</option>` + state.sessions
    .map((s) => `<option value="${escapeHtml(s.id)}">${escapeHtml(sessionLabel(s))}${s.started_at ? ` · ${formatDate(s.started_at)}` : ""}</option>`)
    .join("");
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function renderFlowBadge() {
  const incomingRequests = state.requests.incoming.filter((r) => r.status === "pending").length;
  const incomingOffers = state.offers.incoming.filter((o) => o.status === "offered").length;
  const invited = state.session?.session?.status === "waiting_confirmation" && state.session.session.initiated_by !== state.me.id ? 1 : 0;
  const total = incomingRequests + incomingOffers + invited;
  const badge = $("#flowBadge");
  badge.textContent = total;
  badge.hidden = total === 0;
}

function renderNotifications() {
  const root = $("#notificationList");
  root.innerHTML = state.notifications.map((note) => `
    <article class="notification-item ${note.is_read ? "" : "unread"}"${note.id ? ` data-notification-id="${escapeHtml(note.id)}" tabindex="0" role="button"` : ""}>
      <div class="notification-title">${escapeHtml(note.title)}</div>
      ${note.body ? `<p>${escapeHtml(note.body)}</p>` : ""}
      <time>${formatDate(note.created_at, true)}</time>
    </article>`).join("") || `<p class="empty-line">暂时没有新的提醒。</p>`;
  // F12：未读数一律采用服务端口径，不按本页可见条数重算
  const unread = state.unreadCount || 0;
  const badge = $("#notificationBadge");
  badge.textContent = unread;
  badge.hidden = unread === 0;
}

function renderSharedList() {
  const root = $("#sharedList");
  if (!state.sharedStars.length) {
    root.innerHTML = `<p class="empty-line">这段筛选里还没有星。</p>`;
    return;
  }
  root.innerHTML = state.sharedStars.map((star) => `
    <article class="timeline-item ${star.id === state.selectedSharedId ? "selected" : ""}" data-shared-id="${escapeHtml(star.id)}" tabindex="0" role="button">
      <div class="timeline-meta"><time>${formatDate(star.shared_at || star.created_at)}</time><span>${escapeHtml(star.author_name || star.author_id)}</span><span>${escapeHtml(sourceName(star.shared_origin))}</span></div>
      <h3>${escapeHtml(star.mood_type || "一颗星")}</h3>
      <p>${escapeHtml(star.content)}</p>
    </article>`).join("");
}

function renderInspector(star) {
  const root = $("#starInspector");
  const responses = (star.responses || []).map((response) => `
    <article class="response-item"><strong>${escapeHtml(response.responder_name || response.responder_id)}</strong><p>${response.type === "text" ? escapeHtml(response.text || "") : "一段语音回应"}</p></article>`).join("");
  // 查看足迹：首次查看日期只记录第一次；之后按人累计看了多少次
  const firstView = star.first_view_at
    ? `${formatDate(star.first_view_at, true)}${star.first_view_by_name ? ` · ${escapeHtml(star.first_view_by_name)} 第一眼看到` : ""}`
    : null;
  const views = (star.views || [])
    .map((v) => `${escapeHtml(v.viewer_name || v.viewer_id)} 看了 ${v.count} 次`)
    .join(" · ");
  // F11：session 批次来源直接可见
  const batch = star.cycle_id
    ? (() => {
        const sess = state.sessions.find((s) => s.id === star.cycle_id);
        return `<div><dt>所属批次</dt><dd>${escapeHtml(sess ? sessionLabel(sess) : "一起看开的一轮")}</dd></div>`;
      })()
    : "";
  // 作者本人随时可编辑自己的星（封存中/公共池均可）；星星不提供删除
  const isMine = star.author_id === state.me?.id;
  const editButton = isMine
    ? `<button class="mini-button" data-action="edit-shared">编辑这颗星</button>`
    : "";
  root.innerHTML = `
    <div class="inspector-head"><span>${escapeHtml(star.author_name || star.author_id)}</span><span>${formatDate(star.shared_at || star.created_at)}</span></div>
    <div class="inspector-content">${escapeHtml(star.content)}</div>
    <dl class="inspector-facts">
      <div><dt>写下</dt><dd>${formatDate(star.written_at, true)}</dd></div>
      <div><dt>进入我们的瓶子</dt><dd>${formatDate(star.shared_at, true)}</dd></div>
      <div><dt>来源</dt><dd>${escapeHtml(sourceName(star.shared_origin))}</dd></div>
      <div><dt>心情</dt><dd>${escapeHtml(star.mood_text || star.mood_type || "没有写")}</dd></div>
      ${star.note ? `<div><dt>注释</dt><dd>${escapeHtml(star.note)}</dd></div>` : ""}
      ${batch}
      ${firstView ? `<div><dt>首次查看</dt><dd>${firstView}</dd></div>` : ""}
      <div><dt>足迹</dt><dd>${views ? escapeHtml(views) : "还没有人看过"}</dd></div>
    </dl>
    ${editButton ? `<div class="action-row inspector-edit">${editButton}</div>` : ""}
    <section class="responses"><h3>回应</h3>${responses || `<p class="empty-line">还没有回应。</p>`}<form class="response-form" id="responseForm"><input id="responseText" maxlength="1000" required placeholder="告诉 TA：我看到了。"><button class="button secondary" type="submit">回应</button></form></section>`;
}

function renderToday() {
  $("#todayDate").textContent = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "long" }).format(new Date());
}

async function loadShared() {
  if (!state.me) return;
  const params = new URLSearchParams();
  const pairs = [
    ["author_id", $("#filterAuthor").value],
    ["shared_origin", $("#filterOrigin").value],
    ["written_date", $("#filterWrittenDate").value],
    ["opened_date", $("#filterOpenedDate").value],
    ["session_id", $("#filterSession")?.value || ""],
  ];
  pairs.forEach(([key, value]) => value && params.set(key, value));
  try {
    const data = await api(`/stars/shared${params.size ? `?${params}` : ""}`);
    state.sharedStars = data.items;
    renderSharedList();
    renderSharedRecent();
  } catch (error) { toast(error.message); }
}

async function openSharedStar(id, { navigate = true } = {}) {
  try {
    const star = await api(`/stars/shared/${encodeURIComponent(id)}`);
    state.selectedSharedId = id;
    state.selectedShared = star;   // 供"编辑这颗星"等详情内操作使用
    if (navigate) switchView("shared", { reload: false });
    renderSharedList();
    renderInspector(star);
  } catch (error) { toast(error.message); }
}

// 揭晓接口（接住请求星 / 接住递星 / 拆星 take-next）的返回值已经是完整详情，
// 且服务端已为这次揭晓计过 1 次查看足迹——直接用它渲染，
// 不能再 GET /stars/shared/{id}，否则同一次操作会记两次。
function showRevealedStar(star) {
  state.selectedSharedId = star.id;
  state.selectedShared = star;
  switchView("shared", { reload: false });
  renderSharedList();
  renderInspector(star);
}

async function refreshFlows() {
  if (!state.me) return;
  try {
    const [requests, offers, session] = await Promise.all([
      api("/requests"), api("/offers"), api("/sessions/current"),
    ]);
    state.requests = requests;
    state.offers = offers;
    state.session = session;
    renderRequests();
    renderOffers();
    renderSession();
    renderFlowBadge();
  } catch (error) { toast(error.message); }
}

async function loadNotifications() {
  if (!state.me) return;
  const data = await api("/notifications?limit=200");
  state.notifications = data.items;
  state.unreadCount = data.unread_count;
  renderNotifications();
}

async function refreshUnreadBadge() {
  if (!state.me) return;
  try {
    const data = await api("/notifications?unread_only=true&limit=1");
    state.unreadCount = data.unread_count;
    renderNotifications();
  } catch { /* 轮询失败不打扰；打开面板或刷新时会重试 */ }
}

function startUnreadPolling() {
  stopUnreadPolling();
  unreadPollTimer = setInterval(refreshUnreadBadge, 60000);
}

function stopUnreadPolling() {
  if (unreadPollTimer) {
    clearInterval(unreadPollTimer);
    unreadPollTimer = null;
  }
}

async function refreshAll() {
  const [counts, privateData, sharedData, requests, offers, session, notes, dates, sessions] = await Promise.all([
    api("/bottles/counts"),
    api("/stars/hidden/mine"),
    api("/stars/shared"),
    api("/requests"),
    api("/offers"),
    api("/sessions/current"),
    api("/notifications?limit=200"),
    api("/special-dates"),
    api("/sessions"),
  ]);
  state.counts = counts;
  state.privateStars = privateData.items;
  state.sharedStars = sharedData.items;
  state.requests = requests;
  state.offers = offers;
  state.session = session;
  state.notifications = notes.items;
  state.unreadCount = notes.unread_count;
  state.specialDates = dates.items;
  state.sessions = sessions.items;
  renderCounts();
  renderPrivateStars();
  renderSharedRecent();
  renderSharedList();
  renderRequests();
  renderOffers();
  renderSession();
  renderSessionFilter();
  renderFlowBadge();
  renderNotifications();
}

async function onLogin(event) {
  event.preventDefault();
  const button = event.submitter;
  const tokenInput = $("#tokenInput");
  $("#loginError").textContent = "";
  try {
    const identity = await busy(button, () => api("/auth/login", { method: "POST", body: { token: tokenInput.value } }));
    tokenInput.value = "";
    // F01：同页换号也必须从干净状态开始（showLogin 已重置，这里再兜底一次）
    if (state.me && state.me.id !== identity.id) resetForNewIdentity();
    state.me = identity;
    showApp();
    renderIdentity();
    renderToday();
    startUnreadPolling();
    await refreshAll();
  } catch (error) {
    $("#loginError").textContent = error.message;
  }
}

function startEdit(star) {
  switchView("write");
  $("#editingStarId").value = star.id;
  $("#starContent").value = star.content;
  $("#moodType").value = star.mood_type || "";
  $("#moodText").value = star.mood_text || "";
  $("#starNote").value = star.note || "";
  $("#contentCount").textContent = star.content.length;
  $("#writeHeading").textContent = "改这颗星";
  $("#saveStarButton span").textContent = "保存修改";
  $("#visibilityChoice").hidden = true;
  $("#cancelEditButton").hidden = false;
  $("#starContent").focus();
}

function cancelEdit() {
  $("#writeForm").reset();
  $("#editingStarId").value = "";
  $("#contentCount").textContent = "0";
  $("#writeHeading").textContent = "写一颗星";
  $("#saveStarButton span").textContent = "放进瓶子";
  $("#visibilityChoice").hidden = false;
  $("#cancelEditButton").hidden = true;
}

async function onWrite(event) {
  event.preventDefault();
  const id = $("#editingStarId").value;
  const content = $("#starContent").value;
  // F16：心情/注释字段原样传空字符串——服务端把"空串"解释为清空，
  // 只有不传字段才表示"不修改"，清空从此真正生效。
  const mood_type = $("#moodType").value;
  const mood_text = $("#moodText").value;
  const note = $("#starNote").value;
  $("#writeError").textContent = "";
  try {
    const saved = await busy(event.submitter, () => id
      ? api(`/stars/${encodeURIComponent(id)}`, { method: "PATCH", body: { content, mood_type, mood_text, note } })
      : api("/stars", { method: "POST", body: { content, visibility: $("input[name=visibility]:checked").value, mood_type, mood_text, note } })
    );
    toast(id ? "这颗星已经改好。" : "星星已经放进瓶子。 ");
    const editingId = id;
    cancelEdit();
    await refreshAll();
    if (editingId) {
      // 用服务端返回的状态分流：公共池的星直接用 PATCH 返回的字段更新当前
      // 详情（保存动作不算一次新的查看）；私人星（SEALED 等）留在自己的
      // 瓶子区域——两种情况都不该再按 shared 详情去请求。
      if (saved && saved.state === "SHARED") {
        if (state.selectedShared && state.selectedShared.id === editingId) {
          state.selectedShared = { ...state.selectedShared, ...saved };
          renderInspector(state.selectedShared);
        } else {
          await openSharedStar(editingId, { navigate: true }).catch(() => {});
        }
      } else {
        switchView("observatory", { reload: false });
      }
    } else {
      switchView("observatory");
    }
  } catch (error) { $("#writeError").textContent = error.message; }
}

async function handlePrivateAction(button, item) {
  const id = item.dataset.privateId;
  const star = state.privateStars.find((s) => s.id === id);
  if (!star) return;
  const action = button.dataset.action;
  if (action === "view-private") {
    // 显式打开私人星详情：这次才算一次查看足迹（列表刷新不计）。
    // 再次点击收起；展开/收起本身不额外发请求以外的写入。
    const panel = item.querySelector(".private-detail");
    if (!panel.hidden) { panel.hidden = true; return; }
    try {
      const detail = await busy(button, () => api(`/stars/hidden/mine/${encodeURIComponent(id)}`));
      const views = (detail.views || [])
        .map((v) => `${escapeHtml(v.viewer_name || v.viewer_id)} 看了 ${v.count} 次`)
        .join(" · ");
      panel.innerHTML = `
        <p class="private-detail-content">${escapeHtml(detail.content)}</p>
        ${detail.mood_text || detail.mood_type ? `<p class="private-detail-meta">心情：${escapeHtml(detail.mood_text || detail.mood_type)}</p>` : ""}
        ${detail.note ? `<p class="private-detail-meta">注释：${escapeHtml(detail.note)}</p>` : ""}
        <p class="private-detail-meta">首次查看：${detail.first_view_at ? `${formatDate(detail.first_view_at, true)}${detail.first_view_by_name ? ` · ${escapeHtml(detail.first_view_by_name)}` : ""}` : "还没有"}${views ? ` · 足迹：${views}` : ""}</p>`;
      panel.hidden = false;
    } catch (error) { toast(error.message); }
    return;
  }
  if (action === "edit-private") return startEdit(star);
  if (action === "offer-private") {
    // 用户规则：递的时候可以捎一句话（可不填）。
    // Cancel（prompt 返回 null）必须立即返回：不发请求、不改任何状态。
    const raw = window.prompt(`要给 ${state.me.partner_name} 捎一句话吗？（可不填）`, "");
    if (raw === null) return;
    const message = raw.trim() || null;
    try {
      await busy(button, () => api("/offers", { method: "POST", body: { star_id: id, message } }));
      toast(`已经把这颗星递给 ${state.me.partner_name}。`);
      await refreshAll();
    } catch (error) { toast(error.message); }
  }
}

async function handleFlowAction(button, host) {
  const action = button.dataset.action;
  try {
    if (action === "request-give" || action === "request-decline") {
      const decision = action === "request-give" ? "give" : "not_now";
      // 用户规则：给的时候可以从选择器里指定给哪一颗；不选就随机
      const picker = host.querySelector("[data-give-picker]");
      const body = picker && picker.value
        ? { decision, star_id: picker.value }
        : { decision };
      const result = await busy(button, () => api(`/requests/${host.dataset.requestId}/respond`, { method: "POST", body }));
      // F19：提示跟随服务端真实结果——候选为空时后端会带 note 说明
      toast(decision === "give"
        ? (result?.note || "已经锁定一颗（随机或你指定的），等对方打开。")
        : "这次先没有给。 ");
    } else if (action === "request-open") {
      const star = await busy(button, () => api(`/requests/${host.dataset.requestId}/open`, { method: "POST" }));
      celebrateReveal();
      toast("接住了。这颗星现在属于你们两个人。 ");
      await refreshAll();
      showRevealedStar(star);
      return;
    } else if (action === "request-view") {
      return openSharedStar(button.dataset.starId);
    } else if (action === "offer-accept") {
      const star = await busy(button, () => api(`/offers/${host.dataset.offerId}/accept`, { method: "POST" }));
      celebrateReveal();
      toast("你接住了这颗星。 ");
      await refreshAll();
      showRevealedStar(star);
      return;
    }
    await refreshAll();
  } catch (error) { toast(error.message); }
}

async function handleSessionAction(button) {
  const action = button.dataset.action;
  const session = state.session?.session;
  try {
    if (action === "session-start") {
      // 必须绑定具体日期；纪念日/自定义由服务器按该日期的类型派生 session 类型，
      // 前端只提交 special_date_id（带上派生 type 以便服务端一致性校验）。
      const select = $("#sessionDateSelect");
      if (!select?.value) {
        toast("先选择一个纪念日或特殊日。");
        return;
      }
      const picked = state.specialDates.find((d) => d.id === select.value);
      const body = {
        special_date_id: select.value,
        type: picked ? (picked.type === "anniversary" ? "anniversary" : "special_day") : null,
      };
      await busy(button, () => api("/sessions", { method: "POST", body }));
      toast(picked
        ? `已经按「${picked.name}」邀请 ${state.me.partner_name} 一起拆星。`
        : `已经邀请 ${state.me.partner_name} 一起拆星。`);
    } else if (action === "session-confirm") {
      await busy(button, () => api(`/sessions/${session.id}/confirm`, { method: "POST" }));
      toast("本轮范围已经确定，可以开始拆了。 ");
    } else if (action === "session-decline") {
      // F05：被邀请的一方可以拒绝，不必先同意再结束
      await busy(button, () => api(`/sessions/${session.id}/decline`, { method: "POST" }));
      toast("已经告诉对方这次先不拆，谁的瓶子都没有变化。 ");
    } else if (action === "session-cancel") {
      // F05：发起的一方可以在确认前撤回邀请
      await busy(button, () => api(`/sessions/${session.id}/cancel`, { method: "POST" }));
      toast("邀请已撤回，谁的瓶子都没有变化。 ");
    } else if (action === "session-take") {
      const star = await busy(button, () => api(`/sessions/${session.id}/take-next`, { method: "POST" }));
      celebrateReveal();
      toast(`看到了 ${state.me.partner_name} 写的一颗星，记得留句话。`);
      await refreshAll();
      showRevealedStar(star);
      return;
    } else if (action === "session-finish") {
      await busy(button, () => api(`/sessions/${session.id}/finish`, { method: "POST" }));
      toast("这一轮结束了。 ");
    }
    await refreshAll();
  } catch (error) { toast(error.message); }
}

async function addSpecialDate(event) {
  event.preventDefault();
  const form = event.target;
  const data = new FormData(form);
  const yearRaw = String(data.get("year") || "").trim();
  const body = {
    name: String(data.get("name") || "").trim(),
    month: Number(data.get("month")),
    day: Number(data.get("day")),
    year: yearRaw ? Number(yearRaw) : null,
    type: String(data.get("type") || "custom"),
  };
  const errorEl = $("#specialDateError");
  if (errorEl) errorEl.textContent = "";
  try {
    await api("/special-dates", { method: "POST", body });
    toast("特殊日已经加上了。");
    const dates = await api("/special-dates");
    state.specialDates = dates.items;
    renderSession();
  } catch (error) {
    if (errorEl) errorEl.textContent = error.message;
    else toast(error.message);
  }
}

async function requestAStar(button) {
  try {
    await busy(button, () => api("/requests", { method: "POST" }));
    toast(`已经问 ${state.me.partner_name} 要一颗星。`);
    await refreshAll();
    switchView("flows");
  } catch (error) {
    $("#requestHint").textContent = error.message;
    toast(error.message);
  }
}

async function markNotification(item) {
  if (!item.dataset.notificationId) return;
  if (!item.classList.contains("unread")) return;
  try {
    await api(`/notifications/${encodeURIComponent(item.dataset.notificationId)}/read`, { method: "POST" });
    item.classList.remove("unread");
    const note = state.notifications.find((n) => n.id === item.dataset.notificationId);
    if (note) note.is_read = 1;
    if (note?.type === "HIDDEN_STAR_ADDED") {
      // F02：合并的数量提示读掉后整条都算已读
      state.notifications.forEach((n) => { if (n.type === "HIDDEN_STAR_ADDED") n.is_read = 1; });
    }
    state.unreadCount = Math.max(0, state.unreadCount - 1);
    renderNotifications();
  } catch (error) { toast(error.message); }
}

async function submitResponse(event) {
  event.preventDefault();
  if (!state.selectedSharedId) return;
  const input = $("#responseText");
  const text = input.value.trim();
  if (!text) return;
  try {
    const response = await busy(event.submitter, () => api(`/stars/${encodeURIComponent(state.selectedSharedId)}/responses`, { method: "POST", body: { type: "text", text } }));
    input.value = "";
    toast("回应已经留下。 ");
    // 回应后把 POST 返回的回应直接 append 到当前详情，不重新 GET 详情——
    // 用户没有重新点开这颗星，不该多计一次查看足迹。
    if (state.selectedShared) {
      state.selectedShared.responses = [
        ...(state.selectedShared.responses || []),
        { ...response, responder_name: state.me.name },
      ];
      renderInspector(state.selectedShared);
    }
    await refreshFlows();
  } catch (error) { toast(error.message); }
}

async function onLogout() {
  // F08：服务端撤销成功才算退出；失败时如实提示并保留当前会话供重试
  try {
    await api("/auth/logout", { method: "POST" });
  } catch (error) {
    toast(`退出没有成功：${error.message}。请再试一次。`);
    return;
  }
  showLogin();
}

function activateFromKeyboard(event) {
  // F18：星轨/详情/通知条目现在可被 Tab 聚焦，Enter/空格等效点击
  if (event.key !== "Enter" && event.key !== " ") return;
  const target = event.target.closest?.("[data-shared-id], [data-notification-id]");
  if (!target || target.matches("button, a, input, select, textarea")) return;
  event.preventDefault();
  if (target.dataset.sharedId) return openSharedStar(target.dataset.sharedId);
  if (target.dataset.notificationId) return markNotification(target);
}

function bindEvents() {
  $("#loginForm").addEventListener("submit", onLogin);
  $("#writeForm").addEventListener("submit", onWrite);
  $("#cancelEditButton").addEventListener("click", cancelEdit);
  $("#starContent").addEventListener("input", (event) => { $("#contentCount").textContent = event.target.value.length; });
  $$(".logout-button").forEach((button) => button.addEventListener("click", onLogout));
  $("#requestStarButton").addEventListener("click", (event) => requestAStar(event.currentTarget));
  $("#notificationButton").addEventListener("click", () => {
    const panel = $("#notificationPanel");
    panel.hidden = !panel.hidden;
    $("#notificationButton").setAttribute("aria-expanded", String(!panel.hidden));
    // F12：面板每次展开都重新拉取，另一台设备的动作能出现在这里
    if (!panel.hidden) loadNotifications().catch((error) => toast(error.message));
  });
  $("#closeNotifications").addEventListener("click", () => { $("#notificationPanel").hidden = true; $("#notificationButton").setAttribute("aria-expanded", "false"); });
  $("#filterToggle").addEventListener("click", () => $("#sharedFilters").classList.toggle("open"));
  $("#clearFilters").addEventListener("click", () => { $("#sharedFilters").reset(); loadShared(); });
  ["#filterAuthor", "#filterOrigin", "#filterWrittenDate", "#filterOpenedDate", "#filterSession"].forEach((selector) => $(selector)?.addEventListener("change", loadShared));

  document.addEventListener("click", (event) => {
    const nav = event.target.closest("[data-view]");
    if (nav) return switchView(nav.dataset.view);
    const goto = event.target.closest("[data-goto]");
    if (goto) return switchView(goto.dataset.goto);
    const privateAction = event.target.closest("[data-action^=\"view-private\"], [data-action^=\"edit-private\"], [data-action^=\"offer-private\"]");
    if (privateAction) return handlePrivateAction(privateAction, privateAction.closest("[data-private-id]"));
    const sharedEdit = event.target.closest("[data-action=\"edit-shared\"]");
    if (sharedEdit) return state.selectedShared ? startEdit(state.selectedShared) : toast("先点开这颗星再编辑。");
    const flowAction = event.target.closest("[data-action^=\"request-\"], [data-action=\"offer-accept\"]");
    if (flowAction) return handleFlowAction(flowAction, flowAction.closest("[data-request-id], [data-offer-id]"));
    const sessionAction = event.target.closest("[data-action^=\"session-\"]");
    if (sessionAction) return handleSessionAction(sessionAction);
    const sharedItem = event.target.closest("[data-shared-id]");
    if (sharedItem) return openSharedStar(sharedItem.dataset.sharedId);
    const notification = event.target.closest("[data-notification-id]");
    if (notification) return markNotification(notification);
  });

  document.addEventListener("keydown", activateFromKeyboard);

  document.addEventListener("submit", (event) => {
    if (event.target.id === "responseForm") submitResponse(event);
    if (event.target.id === "specialDateForm") addSpecialDate(event);
  });

  $$('input[name="visibility"]').forEach((input) => input.addEventListener("change", () => {
    const hidden = $("input[name=visibility]:checked").value === "hidden";
    const rules = $$("#writeRulePreview li");
    rules[1].textContent = hidden ? "封存" : "立刻共享";
    rules[2].textContent = hidden ? "等未来的打开方式" : "两个人现在都能看见";
  }));
}

async function boot() {
  bindEvents();
  renderToday();
  try {
    state.me = await api("/auth/me");
    showApp();
    renderIdentity();
    startUnreadPolling();
    await refreshAll();
  } catch (error) {
    if (error.status !== 401) $("#loginError").textContent = error.message;
    showLogin();
  }
}

boot();
