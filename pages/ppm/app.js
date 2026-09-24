import { progressMeter, logTaskLabel, taskOverview } from "./tasks.js";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[c]);
const today = new Date();
const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const monthIso = d => iso(d).slice(0, 7);
const fmt = value => value ? new Date(value).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "—";
const statusClass = value => `status status-${esc(value)}`;

const state = {view:"projects", members:[], projects:[], todos:[], todoPushConfigured:false, attendance:null, summary:null, timeline:null, summaryTimeline:null, selectedProject:null, summaryPreview:null, projectLogRange:null, detailTab:"overview", taskFilter:"all", taskKeyword:"", expandedTasks:new Set()};
const bridge = window.AstrBotPluginPage;
const api = bridge ? {
  get: (endpoint, params) => bridge.apiGet(endpoint, params),
  post: (endpoint, body) => bridge.apiPost(endpoint, body),
} : createDemoApi();

const settings = {week_start:"monday",ui_color_theme:"forest"};
if (bridge) {
  await bridge.ready();
  Object.assign(settings, (await api.get("health")).settings || {});
}
document.documentElement.dataset.colorTheme = ["forest","ocean","violet","amber"].includes(settings.ui_color_theme) ? settings.ui_color_theme : "forest";
$("#today-label").textContent = today.toLocaleDateString("zh-CN", {year:"numeric",month:"long",day:"numeric",weekday:"short"});
$("#attendance-month").value = monthIso(today);
$("#timeline-end").value = iso(today);
const weekAgo = new Date(today); weekAgo.setDate(weekAgo.getDate() - 6);
$("#timeline-start").value = iso(weekAgo);

const viewMeta = {
  "project-detail":["项目详情","全部项目档案，包含已结束项目","新增工作记录"],
  members:["团队成员","成员档案、项目参与及出勤统计","新增成员"],
  attendance:["考勤管理","公司日历、成员考勤与周期汇总","登记考勤"],
  projects:["项目列表","每一步有迹可循，让进度清晰可见","新建项目"],
  "project-records":["项目记录","每日总结与项目工作进程","生成每日总结"],
  todos:["待办事项","关联项目，设置一次性或周期提醒","新增待办"],
};

let loadingCount = 0;
async function withLoading(operation) {
  loadingCount++;
  $("#loading").classList.remove("hidden");
  try { return await operation(); }
  catch (error) { toast(error.message || "操作失败", true); throw error; }
  finally { if (--loadingCount === 0) $("#loading").classList.add("hidden"); }
}

function toast(message, error = false) {
  const el = $("#toast"); el.textContent = message; el.className = `toast${error ? " error" : ""}`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.add("hidden"), 2600);
}

let navigationVersion = 0;
async function switchView(view) {
  navigationVersion++;
  closeDrawer();
  state.view = view;
  window.scrollTo(0, 0);
  $("#primary-action").disabled = false;
  if (window.matchMedia("(max-width: 640px)").matches) setProjectMenuExpanded(false);
  if (view !== "project-detail") { const url = new URL(location.href); url.searchParams.set("view", view); url.searchParams.delete("project"); history.replaceState(null, "", url); }
  $$(".view").forEach(el => el.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  $$("#nav button").forEach(el => el.classList.toggle("active", el.dataset.view === view));
  const [title, subtitle, action] = viewMeta[view];
  $("#page-title").textContent = title; $("#page-subtitle").textContent = subtitle; $("#primary-action").textContent = action;
  await loadView(view);
}

async function loadView(view) {
  const version = navigationVersion;
  return withLoading(async () => {
    if (view === "project-detail") {
      await loadProjects();
      if (version !== navigationVersion) return;
      const id = state.projects.find(p => p.id === state.selectedProject?.id)?.id || state.projects[0]?.id;
      if (id && state.projects.some(p => p.id === id)) await openProject(id);
      else { state.selectedProject = null; $("#project-detail-content").innerHTML = `<div class="surface empty">暂无项目，请先在项目列表中创建项目</div>`; $("#primary-action").disabled = true; }
    }
    if (view === "members") { state.members = await api.get("members"); renderMembers(); }
    if (view === "attendance") { await loadAttendance(); }
    if (view === "projects") { await loadProjects(); }
    if (view === "project-records") { await Promise.all([loadTimeline(), loadSummaries()]); }
    if (view === "todos") { await loadTodos(); }
  });
}

const todoWeekLabels = ["周一","周二","周三","周四","周五","周六","周日"];
function todoZone(offset) { return `UTC${offset >= 0 ? "+" : "-"}${String(Math.floor(Math.abs(offset)/60)).padStart(2,"0")}:${String(Math.abs(offset)%60).padStart(2,"0")}`; }
function todoScheduleLabel(todo) {
  if (todo.push_mode !== "weekly") return `一次性 · ${todoTime(todo.push_at)}`;
  const days = todo.push_weekdays || [];
  return `${days.length === 7 ? "每天" : days.map(day=>todoWeekLabels[day]).join("、")} ${todo.push_time}（${todoZone(todo.push_utc_offset)}）`;
}
const todoTime = value => value ? new Date(value).toLocaleString("zh-CN", {year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "未设置时间";
let todoLoadVersion = 0;
async function loadTodos() {
  const version = ++todoLoadVersion, data = await api.get("todos");
  if (version !== todoLoadVersion) return;
  state.todos = data.todos;
  state.todoPushConfigured = data.push_configured;
  const filter = $("#todo-project-filter"), selected = filter.value;
  const projects = [...new Map(state.todos.map(todo => [todo.project_id, todo.project_name])).entries()];
  filter.innerHTML = `<option value="">全部项目</option>` + projects.map(([id,name]) => `<option value="${id}">${esc(name)}</option>`).join("");
  if (projects.some(([id]) => String(id) === selected)) filter.value = selected;
  renderTodos();
}
function todoPushLabel(todo) {
  if (!todo.push_enabled) return "未开启";
  if (todo.sent_at && todo.push_mode !== "weekly") return "已推送";
  if (todo.project_deleted_at) return "项目已删除，停止推送";
  if (!state.todoPushConfigured) return "待配置推送会话";
  if (todo.claimed_until * 1000 > Date.now()) return "推送中";
  if (todo.push_error) return "推送失败，等待重试";
  return todo.push_due * 1000 <= Date.now() ? "已到期，等待推送" : "等待推送";
}
function renderTodos() {
  const notice = $("#todo-push-notice");
  notice.classList.toggle("needs-config", !state.todoPushConfigured);
  notice.textContent = state.todoPushConfigured
    ? "推送会话已配置。支持一次性和周期推送；周期推送按选定星期的固定时间提醒，全选即每天。"
    : "尚未配置推送会话：请在插件配置中填写「待办推送会话 ID（UMO）」并重载插件。待办仍可保存，提醒将在配置后发送。";
  const keyword = $("#todo-search").value.trim().toLowerCase(), projectId = $("#todo-project-filter").value;
  const items = state.todos.filter(todo => (!projectId || String(todo.project_id) === projectId) && `${todo.content} ${todo.project_name}`.toLowerCase().includes(keyword));
  $("#todos-body").innerHTML = items.map(todo => `<tr data-todo="${todo.id}">
    <td><div class="todo-content">${esc(todo.content)}</div><small>#${todo.id} · 更新于 ${esc(todoTime(todo.updated_at))}</small></td>
    <td>${esc(todo.project_name)}<small>项目 ID：${todo.project_id}${todo.project_deleted_at ? " · 已删除" : ""}</small></td>
    <td><strong class="${todo.push_error ? "push-failed" : ""}">${esc(todoPushLabel(todo))}</strong>
      ${todo.push_enabled ? `<small>${esc(todoScheduleLabel(todo))}</small>${todo.push_mode === "weekly" ? `<small>下次：${esc(todoTime(new Date(todo.push_due * 1000).toISOString()))}</small>` : ""}` : ""}
      ${todo.sent_at ? `<small>上次发送：${esc(todoTime(todo.sent_at))}</small>` : ""}
      ${todo.push_enabled && todo.push_error ? `<small class="push-failed">${esc(todo.push_error)}</small>` : ""}</td>
    <td><div class="actions"><button class="text-action" data-edit-todo="${todo.id}">编辑</button><button class="text-action danger" data-delete-todo="${todo.id}">删除</button></div></td></tr>`).join("");
  $("#todos-empty").textContent = state.todos.length ? "没有符合筛选条件的待办" : "暂无待办，点击右上角新增";
  $("#todos-empty").classList.toggle("hidden", items.length > 0);
}
async function todoModal(todo = {}) {
  const projects = await withLoading(() => api.get("projects"));
  if (!projects.length && !todo.id) { toast("请先在项目列表中创建项目", true); return; }
  if (todo.project_deleted_at) projects.push({id:todo.project_id,name:`${todo.project_name}（已删除）`});
  const projectField = `<label class="field full"><span>所属项目</span><select name="project_id" required><option value="">请选择项目</option>${projects.map(project=>`<option value="${project.id}" ${project.id===todo.project_id ? "selected" : ""}>${esc(project.name)} · #${project.id}</option>`).join("")}</select></label>`;
  const offset = todo.push_mode === "weekly" ? todo.push_utc_offset : -new Date().getTimezoneOffset();
  const days = todo.push_weekdays || [];
  openModal({title:todo.id ? "编辑待办" : "新增待办",description:"像闹钟一样设置提醒：一次性推送，或每周指定几天的固定时间推送。",
    fields:projectField + textarea("待办内容","content",todo.content||"") +
      `<label class="field"><span>定时推送</span><span class="todo-push-control"><input type="checkbox" name="push_enabled" ${todo.push_enabled ? "checked" : ""} />开启推送</span></label>` +
      `<label class="field"><span>推送方式</span><select name="push_mode"><option value="once" ${todo.push_mode !== "weekly" ? "selected" : ""}>一次性推送</option><option value="weekly" ${todo.push_mode === "weekly" ? "selected" : ""}>周期推送</option></select></label>` +
      `<div class="field full" id="todo-once-fields">${field("推送日期时间（当前浏览器时区）","push_at",todo.push_at ? localDateTime(todo.push_at) : "","datetime-local",true)}</div>` +
      `<div class="field full" id="todo-weekly-fields"><div class="todo-week-heading"><span>重复星期</span><button type="button" class="text-action" id="todo-select-all">全选（每天）</button></div><div class="todo-weekdays">${todoWeekLabels.map((label,index)=>`<label><input type="checkbox" name="push_weekdays" value="${index}" ${days.includes(index) ? "checked" : ""} />${label}</label>`).join("")}</div>${field("每天固定推送时间","push_time",todo.push_time||"09:00","time",true)}<small class="muted-note">按固定 ${todoZone(offset)} 时区执行。</small></div>` +
      `<p class="field full muted-note">${state.todoPushConfigured ? "关闭推送可暂停提醒。周期提醒不会补发停机期间往日的历史提醒。" : "尚未配置推送会话，请在插件配置中填写完整 UMO。"}</p>`,
    onSubmit:async form => {
      const enabled = form.get("push_enabled") === "on", mode = form.get("push_mode"), at = form.get("push_at");
      const weekdays = form.getAll("push_weekdays").map(Number);
      if (mode === "weekly" && !weekdays.length) { toast("请至少选择一个重复星期",true); return; }
      await withLoading(() => api.post("todos/save", {id:todo.id,project_id:Number(form.get("project_id")),content:form.get("content"),push_enabled:enabled,push_mode:mode,push_at:mode === "once" && at ? new Date(at).toISOString() : null,push_weekdays:weekdays,push_time:form.get("push_time"),push_utc_offset:offset}));
      closeModal(); toast("待办已保存"); await withLoading(loadTodos); if(state.view==="project-detail")await openProject(state.selectedProject.id);
    }});
  const content = $('#modal-form [name="content"]'); content.required = true; content.maxLength = 3000;
  const toggle = $('#modal-form [name="push_enabled"]'), mode = $('#modal-form [name="push_mode"]');
  const dateInput = $('#modal-form [name="push_at"]'), timeInput = $('#modal-form [name="push_time"]');
  const updatePushInput = () => {
    const weekly = mode.value === "weekly";
    $("#todo-once-fields").classList.toggle("hidden",weekly); $("#todo-weekly-fields").classList.toggle("hidden",!weekly);
    dateInput.disabled = weekly; dateInput.required = toggle.checked && !weekly;
    timeInput.disabled = !weekly; timeInput.required = weekly;
  };
  $("#todo-select-all").onclick = () => { $$('[name="push_weekdays"]',$("#modal-form")).forEach(input=>{input.checked=true;}); };
  toggle.addEventListener("change", updatePushInput); mode.addEventListener("change",updatePushInput); updatePushInput();
}

function metric(label, value, note = "") { return `<div class="metric"><label>${esc(label)}</label><strong>${Number(value || 0)}</strong><small>${esc(note)}</small></div>`; }

function renderMembers() {
  const keyword = $("#member-search").value.trim().toLowerCase();
  const items = state.members.filter(m => [m.name,m.role].join(" ").toLowerCase().includes(keyword));
  $("#members-body").innerHTML = items.map(m => `<tr data-member="${m.id}"><td><div class="member-cell"><span class="member-avatar">${esc(m.name.slice(0,1))}</span><div><strong>${esc(m.name)}</strong><small>${esc(m.role || "未设置岗位")}</small></div></div></td><td>${Number(m.ready_projects||0)}</td><td>${Number(m.active_projects||0)}</td><td>${Number(m.maintenance_projects||0)}</td><td>${Number(m.paused_projects||0)}</td><td>${Number(m.completed_projects||0)}</td><td><div class="actions"><button class="text-action" data-edit-member="${m.id}">编辑</button><button class="text-action danger" data-delete-member="${m.id}">删除</button></div></td></tr>`).join("");
  $("#members-empty").classList.toggle("hidden", items.length > 0);
}

async function loadProjects() { state.projects = await api.get("projects"); renderProjects(); renderProjectMenu(); }
function setProjectMenuExpanded(expanded) {
  $("#project-submenu").classList.toggle("expanded", expanded);
  $('#nav [data-view="project-detail"]').setAttribute("aria-expanded", String(expanded));
}
window.matchMedia("(max-width: 640px)").addEventListener("change", event => {
  if (event.matches) setProjectMenuExpanded(false);
});
function renderProjectMenu() {
  $("#project-submenu").innerHTML = state.projects.map(p => `<button data-view-project="${p.id}" class="${state.view === "project-detail" && state.selectedProject?.id === p.id ? "active" : ""}" title="${esc(p.name)} · ${esc(p.status)}"><span class="project-nav-name">${esc(p.name)}</span><small>${esc(p.status)}</small></button>`).join("") || `<p class="muted-note">暂无项目</p>`;
}
function renderProjects() {
  const tasks = state.projects.reduce((n,p)=>n+(p.task_count||0),0), completed = state.projects.reduce((n,p)=>n+(p.completed_task_count||0),0);
  $("#project-metrics").innerHTML = metric("项目总数",state.projects.length,"全部项目档案")+metric("进行中的项目",state.projects.filter(p=>p.status==="进行").length,"持续推进")+metric("未完成任务",tasks-completed,"下一步行动")+metric("已完成任务",completed,`共 ${tasks} 项任务`);
  const keyword = $("#project-search").value.trim().toLowerCase();
  const statuses = new Set($$("#project-status-filter input:checked").map(input => input.value));
  const items = state.projects.filter(p => statuses.has(p.status) && [p.id,p.name,p.description,p.latest_record_date].join(" ").toLowerCase().includes(keyword));
  $("#projects-body").innerHTML = items.map(p => `<tr data-project="${p.id}"><td><div class="project-cell"><strong>${esc(p.name)}</strong><small>项目ID：${esc(p.id)}</small></div></td><td><span class="${statusClass(p.status)}">${esc(p.status)}</span></td><td>${progressMeter(p)}</td><td>${memberStack(p.members)}</td><td>${esc(p.latest_record_date || "暂无记录")}</td><td>${fmt(p.updated_at)}</td><td><div class="actions"><button class="text-action" data-view-project="${p.id}">详情</button><button class="text-action" data-add-log-for-project="${p.id}">记一笔</button><button class="text-action danger" data-delete-project="${p.id}">删除</button></div></td></tr>`).join("");
  $("#projects-empty").classList.toggle("hidden", items.length > 0);
}
function memberStack(members = []) { return `<div class="member-stack">${members.slice(0,4).map(m=>`<span class="mini-avatar" title="${esc(m.name)}">${esc(m.name.slice(0,1))}</span>`).join("")}${members.length>4?`<span class="mini-avatar">+${members.length-4}</span>`:""}${!members.length?"—":""}</div>`; }

async function loadSummaries() {
  state.summaryTimeline = await api.get("summaries", {start:$("#timeline-start").value,end:$("#timeline-end").value});
  renderSummaryTimeline();
}
function summaryByDate(summaryDate) {
  return (state.summaryTimeline?.summaries||[]).find(item=>item.summary_date===summaryDate);
}
function renderSummaryTimeline() {
  const data=state.summaryTimeline;
  if (!data?.dates?.length) { $("#summary-timeline").innerHTML=`<div class="empty">当前时间范围内没有日期</div>`; return; }
  const head=data.dates.map(date=>`<th>${esc(date.slice(5))}<small style="display:block;color:#8b95a6">${"日一二三四五六"[new Date(date+"T00:00:00").getDay()]}</small></th>`).join("");
  const cells=data.dates.map(date=>{const summary=summaryByDate(date);return `<td><button class="timeline-cell summary-cell ${summary?"has-log generated":"missing"}" data-summary-date="${date}" title="${summary?"点击查看和修改":"点击生成该日总结"}">${summary?"已生成":"未生成"}</button></td>`}).join("");
  $("#summary-timeline").innerHTML=`<table class="timeline-table summary-timeline-table"><thead><tr><th>日期</th>${head}</tr></thead><tbody><tr><td><strong>生成状态</strong></td>${cells}</tr></tbody></table>`;
}

async function loadTimeline() {
  state.timeline = await api.get("timeline", {start:$("#timeline-start").value,end:$("#timeline-end").value}); renderTimeline();
}
function renderTimeline() {
  const t = state.timeline;
  const statuses = new Set($$("#record-status-filter input:checked").map(input => input.value));
  const keyword = $("#record-search").value.trim().toLowerCase();
  const projects = (t?.projects||[]).filter(project=>statuses.has(project.status) && [project.id,project.name,project.description,project.latest_record_date].join(" ").toLowerCase().includes(keyword));
  if (!t || !projects.length) { $("#timeline").innerHTML = `<div class="empty">当前筛选条件下暂无项目进程</div>`; return; }
  const head = t.dates.map(d=>`<th>${esc(d.slice(5))}<small style="display:block;color:#8b95a6">${"日一二三四五六"[new Date(d+"T00:00:00").getDay()]}</small></th>`).join("");
  const rows = projects.map(p=>`<tr><td><strong>${esc(p.name)}</strong></td>${t.dates.map(d=>{const logs=t.logs[`${p.id}:${d}`]||[];return `<td><button class="timeline-cell ${logs.length?"has-log":""}" data-log-project="${p.id}" data-log-date="${d}" title="${esc(logs.map(l=>`${l.task_name||"项目级记录"}：${l.content}`).join("\n"))}">${logs.length?`${logs.length} 条记录`:"—"}</button></td>`}).join("")}</tr>`).join("");
  $("#timeline").innerHTML = `<table class="timeline-table"><thead><tr><th>项目名称</th>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}

async function loadAttendance() {
  const month = $("#attendance-month").value;
  const range = summaryRange($("#summary-period").value, month);
  [state.attendance,state.summary] = await Promise.all([api.get("attendance",{month}),api.get("attendance/summary",range)]);
  state.members = state.attendance.members; renderAttendance();
}
function lastDay(month) { const [y,m]=month.split("-").map(Number); return iso(new Date(y,m,0)); }
function summaryRange(period, month) {
  const [year, number] = month.split("-").map(Number);
  if (period === "year") return {start:`${year}-01-01`,end:`${year}-12-31`};
  if (period === "week") {
    const anchor = month === monthIso(today) ? new Date(today) : new Date(year, number - 1, 1);
    const offset = settings.week_start === "sunday" ? anchor.getDay() : (anchor.getDay() + 6) % 7, start = new Date(anchor); start.setDate(anchor.getDate() - offset);
    const end = new Date(start); end.setDate(start.getDate() + 6);
    return {start:iso(start),end:iso(end)};
  }
  return {start:`${month}-01`,end:lastDay(month)};
}
function renderAttendance() {
  const {calendar,members,records,assignments,today:todayValue} = state.attendance;
  $("#workdays-count").textContent = `${calendar.workdays} 个工作日`;
  const weekLabels = settings.week_start === "sunday" ? ["日","一","二","三","四","五","六"] : ["一","二","三","四","五","六","日"];
  const offset = (calendar.days[0].weekday + (settings.week_start === "sunday" ? 1 : 0)) % 7;
  $("#calendar-grid").innerHTML = weekLabels.map(d=>`<div class="calendar-week">${d}</div>`).join("") + (offset ? `<div style="grid-column:span ${offset}"></div>` : "") + calendar.days.map(d=>`<button class="calendar-day ${d.is_workday?"workday":"restday"} ${d.source==="人工调整"?"manual":""}" data-calendar-date="${d.date}" data-workday="${d.is_workday}" title="${esc(d.source)}"><strong>${Number(d.date.slice(-2))}</strong><small>${d.is_workday?"上班":"休班"}</small></button>`).join("");
  $("#attendance-head").innerHTML = `<tr><th>成员</th>${calendar.days.map(d=>`<th>${Number(d.date.slice(-2))}<small style="display:block">${"一二三四五六日"[d.weekday]}</small></th>`).join("")}</tr>`;
  $("#attendance-body").innerHTML = members.map(m=>`<tr><td><strong>${esc(m.name)}</strong></td>${calendar.days.map(d=>{if(d.date>todayValue)return `<td><button class="attendance-cell future" disabled aria-label="未来日期">—</button></td>`;const key=`${m.id}:${d.date}`,r=records[key],projects=assignments[key]||[];const cls=r?.status==="加班"?"overtime":r?.status==="调休"?"timeoff":d.is_workday?(projects.length?"assigned":"normal"):"rest";const label=r?(r.status==="加班"?"加":r.status==="调休"?"休":projects.length?"项":"勤"):(d.is_workday?(projects.length?"项":"勤"):"—");const defaultTitle=d.is_workday?(projects.length?`参与项目：${projects.join("、")}`:"默认正常出勤；未参与项目"):"休息日";return `<td><button class="attendance-cell ${cls}" data-member-id="${m.id}" data-att-date="${d.date}" data-status="${r?.status||""}" title="${esc([defaultTitle,r?.note].filter(Boolean).join("；"))}">${label}</button></td>`}).join("")}</tr>`).join("");
  const periodLabel={week:"周",month:"月",year:"年"}[$("#summary-period").value];
  $("#summary-title").textContent=`${periodLabel}度汇总`;
  $("#summary-range").textContent=`${state.summary.start} 至 ${state.summary.end} · 正常、加班、调休天数及异常明细`;
  $("#attendance-summary").innerHTML = `<div class="summary-row header"><span>成员</span><span>正常</span><span>加班</span><span>调休</span></div>` + state.summary.summary.map(r=>`<div class="summary-row"><strong>${esc(r.name)}</strong><span>${Number(r.normal_days||0)}</span><span>${Number(r.overtime_days||0)}</span><span>${Number(r.time_off_days||0)}</span></div>`).join("");
  $("#attendance-details").innerHTML = `<h3 style="margin-top:0">加班 / 调休明细</h3>` + (state.summary.details.map(r=>`<div class="detail-line"><time>${esc(r.work_date.slice(5))}</time><strong>${esc(r.name)}</strong><span class="${r.status==="加班"?"status status-维护":"status status-暂停"}">${esc(r.status)} ${Number(r.hours||0)}h</span><span>${esc(r.note||"")}</span></div>`).join("") || `<p style="color:var(--muted)">当前周期暂无异常记录</p>`);
}

let modalReturnFocus = null;
function openModal({title,description="",fields,onSubmit}) {
  if($("#modal").classList.contains("hidden")) modalReturnFocus=document.activeElement;
  $("#modal-title").textContent=title; $("#modal-description").textContent=description; $("#modal-fields").innerHTML=fields;
  $(".modal-actions").classList.remove("hidden");
  $("#modal-form button[type=submit]").textContent="保存";
  $("#modal").classList.remove("hidden"); $("#modal-backdrop").classList.remove("hidden");
  requestAnimationFrame(()=>($("#modal-fields input:not([disabled]), #modal-fields select:not([disabled]), #modal-fields textarea:not([disabled])") || $("#modal [data-close-modal]"))?.focus({preventScroll:true}));
  $("#modal-form").onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget, button = $("button[type=submit]", form);
    if (button.disabled) return;
    button.disabled = true;
    try { await onSubmit(new FormData(form),form); }
    catch (_) { /* withLoading 已向用户显示统一错误，避免重复抛送。 */ }
    finally { button.disabled = false; }
  };
}
function closeModal() { $("#modal").classList.add("hidden"); $("#modal-backdrop").classList.add("hidden"); if(modalReturnFocus?.isConnected)modalReturnFocus.focus({preventScroll:true}); }
let confirmResolver = null;
function confirmAction(message) {
  if (confirmResolver) confirmResolver(false);
  $("#confirm-message").textContent=message;
  $("#confirm-modal").classList.remove("hidden");
  $("#confirm-backdrop").classList.remove("hidden");
  $("#confirm-submit").focus();
  return new Promise(resolve=>{confirmResolver=resolve;});
}
function resolveConfirmation(confirmed) {
  $("#confirm-modal").classList.add("hidden");
  $("#confirm-backdrop").classList.add("hidden");
  const resolve=confirmResolver; confirmResolver=null;
  if (resolve) resolve(confirmed);
}
function field(label,name,value="",type="text",full=false,required=false) { return `<label class="field ${full?"full":""}"><span>${esc(label)}</span><input type="${type}" name="${name}" value="${esc(value??"")}" ${required?"required":""}/></label>`; }
function textarea(label,name,value="",full=true,className="") { return `<label class="field ${full?"full":""}"><span>${esc(label)}</span><textarea${className?` class="${esc(className)}"`:""} name="${name}">${esc(value??"")}</textarea></label>`; }
function selectField(label,name,options,value="",full=false) { return `<label class="field ${full?"full":""}"><span>${esc(label)}</span><select name="${name}">${options.map(o=>`<option ${o===value?"selected":""}>${esc(o)}</option>`).join("")}</select></label>`; }
async function withGenerating(form, operation) {
  const button=$("button[type=submit]",form), label=button.textContent;
  button.disabled=true; button.textContent="生成中…";
  try { return await operation(); }
  finally { button.disabled=false; button.textContent=label; }
}

function memberModal(member = {}) {
  openModal({title:member.id?"编辑成员":"新增成员",description:"维护成员姓名、岗位与备注。",fields:field("姓名","name",member.name,"text",false,true)+field("岗位","role",member.role)+textarea("备注","notes",member.notes),onSubmit:async form=>{
    await withLoading(()=>api.post("members/save",{id:member.id,name:form.get("name"),role:form.get("role"),notes:form.get("notes")})); closeModal(); toast("成员已保存"); await loadView("members");
  }});
}

function openMember(id) {
  const member = state.members.find(item => Number(item.id) === Number(id));
  if (!member) return;
  const projects = member.projects || [];
  $("#drawer-content").innerHTML = `<div class="drawer-head"><div><h2>${esc(member.name)}的项目</h2><span>${projects.length} 条项目参与记录</span></div><button class="icon-button" data-close-drawer>×</button></div><div class="drawer-body">
    <div class="detail-section"><h3>项目明细</h3>${projects.map(p=>`<div class="list-row" style="grid-template-columns:1fr 90px 150px;padding-left:0;padding-right:0" data-project="${p.id}"><strong>${esc(p.name)}</strong><span class="${statusClass(p.status)}">${esc(p.status)}</span><small>${esc(p.joined_at)} 加入${p.left_at?` · ${esc(p.left_at)} 退出`:""}</small></div>`).join("")||"<p style='color:var(--muted)'>暂未参与项目</p>"}</div>
  </div>`;
  $("#detail-drawer").classList.remove("hidden"); $("#drawer-backdrop").classList.remove("hidden");
}

async function projectModal(project = {}) {
  if (!state.members.length) state.members = await api.get("members");
  const selected = new Set((project.members||[]).map(m=>Number(m.id)));
  const memberChecks = `<div class="field full"><label>参与成员</label><div class="checkbox-grid">${state.members.map(m=>`<label class="check-item"><input type="checkbox" name="member_ids" value="${m.id}" ${selected.has(Number(m.id))?"checked":""}/><span>${esc(m.name)}</span></label>`).join("")||"<span>请先创建成员</span>"}</div></div>`;
  openModal({title:project.id?"编辑项目":"新建项目",description:"状态和参与成员的每一次变化都会被记录。",fields:field("项目名称","name",project.name,"text",false,true)+selectField("当前状态","status",["准备","进行","维护","暂停","结束"],project.status||"准备")+field("开始日期","start_date",project.start_date,"date")+field("计划结束","end_date",project.end_date,"date")+textarea("项目描述","description",project.description)+memberChecks+textarea("状态变更说明","status_note",""),onSubmit:async form=>{
    const payload={id:project.id,name:form.get("name"),status:form.get("status"),start_date:form.get("start_date"),end_date:form.get("end_date"),description:form.get("description"),status_note:form.get("status_note"),member_ids:form.getAll("member_ids").map(Number)};
    await withLoading(()=>api.post("projects/save",payload)); closeModal(); toast("项目已保存"); await loadProjects(); if (state.view === "project-detail" && project.id) await openProject(project.id); else await loadView("projects");
  }});
}

async function workLogModal(projectId, workDate=iso(today), record={}) {
  const tasks = await withLoading(()=>api.get("tasks",{project_id:projectId}));
  const choices = tasks.filter(t=>!t.deleted_at || t.id===record.task_id);
  openModal({title:record.id?"编辑工作记录":"新增工作记录",description:"记录可以关联一项任务。完成操作与本次记录一起保存；删除或修改记录不会自动撤销任务状态。",
    fields:field("记录日期","work_date",record.work_date||workDate,"date",false,true)+
    `<label class="field"><span>关联任务</span><select name="task_id"><option value="">项目级记录（不关联任务）</option>${choices.map(t=>`<option value="${t.id}" ${t.id===record.task_id?"selected":""}>${esc(t.name)} · ${t.deleted_at?"已删除":t.status}</option>`).join("")}</select></label>`+
    `<label class="field full"><span>保存时的任务状态</span><select name="task_action"><option value="keep">保持当前状态</option><option value="complete">标记为完成（完成日期使用记录日期）</option><option value="reopen">重新打开（保留之前的完成历史）</option></select></label>`+
    textarea("工作内容","content",record.content||"",true,"work-log-content"),onSubmit:async form=>{
      const date=form.get("work_date");
      await withLoading(()=>api.post("worklogs/save",{id:record.id,project_id:projectId,work_date:date,content:form.get("content"),task_id:form.get("task_id")?Number(form.get("task_id")):null,task_action:form.get("task_action")||"keep"}));
      closeModal(); toast(record.id?"记录已更新":"记录已新增"); await Promise.all([loadProjects(),loadTimeline()]); if (state.view === "project-detail") await openProject(projectId); else await openDayLogs(projectId,date);
    }});
  const select = $('[name="task_id"]',$("#modal-form")), action = $('[name="task_action"]',$("#modal-form"));
  const update = ()=>{action.disabled=!select.value || Boolean(tasks.find(t=>t.id===Number(select.value))?.deleted_at); if(action.disabled) action.value="keep";};
  select.onchange=update; update();
  $('[name="content"]',$("#modal-form")).required=true;
  $('[name="content"]',$("#modal-form")).maxLength=4000;
}

function taskModal(task = {}) {
  openModal({title:task.id?"编辑任务":"新增任务",description:"完成进度由任务数量计算。每次完成、重新打开和日期修改都会保留历史。",
    fields:field("任务名称","name",task.name,"text",true,true)+field("开始日期","start_date",task.start_date||iso(today),"date",false,true)+selectField("任务状态","status",["未完成","完成"],task.status||"未完成")+field("完成日期","completed_date",task.completed_date||iso(today),"date")+textarea("任务说明","description",task.description),
    onSubmit:async form=>{
      await withLoading(()=>api.post("tasks/save",{id:task.id,project_id:state.selectedProject.id,name:form.get("name"),description:form.get("description"),start_date:form.get("start_date"),status:form.get("status"),completed_date:form.get("status")==="完成"?form.get("completed_date"):null}));
      closeModal(); toast("任务已保存"); await loadProjects(); await openProject(state.selectedProject.id);
    }});
  const status=$('[name="status"]',$("#modal-form")), completed=$('[name="completed_date"]',$("#modal-form"));
  completed.max=iso(new Date());
  const update=()=>{completed.disabled=status.value!=="完成";completed.required=!completed.disabled;completed.closest(".field").classList.toggle("hidden",completed.disabled);};
  status.onchange=update;update();
  $('[name="name"]',$("#modal-form")).maxLength=120;
}

async function openDayLogs(projectId, workDate) {
  const detail=await withLoading(()=>api.get("worklogs",{project_id:projectId,date:workDate}));
  const rows=(detail.logs||[]).map(log=>`<div class="day-log-item"><label class="field full"><span>${logTaskLabel(log)}</span><textarea class="work-log-content day-log-content" readonly>${esc(log.content)}</textarea></label><div class="day-log-footer"><small>记录日期：${esc(log.work_date)}</small><div class="actions"><button type="button" class="text-action" data-edit-day-log="${log.id}">编辑</button><button type="button" class="text-action danger" data-delete-day-log="${log.id}">删除</button></div></div></div>`).join("")||`<div class="empty">当天暂无记录</div>`;
  openModal({title:`${detail.project.name} · ${workDate}`,description:"查看并维护该项目当天的全部工作记录。",fields:`<div class="field full"><div class="section-head compact-head"><h3>当天记录</h3><button type="button" class="primary" data-add-day-log>新增记录</button></div><div id="day-log-list">${rows}</div></div>`,onSubmit:async()=>{}});
  state.dayLogs={projectId:Number(projectId),workDate,logs:detail.logs||[]};
  $(".modal-actions").classList.add("hidden");
}

async function openProject(id) {
  const version = ++navigationVersion;
  const [detail, todoData] = await withLoading(()=>Promise.all([api.get("projects/detail",{id}),api.get("todos")]));
  if (version !== navigationVersion) return;
  const changingProject = state.view !== "project-detail" || state.selectedProject?.id !== detail.id;
  if (changingProject) { window.scrollTo(0, 0); state.projectLogRange = {start:"",end:""}; state.detailTab="overview"; state.taskFilter="all"; state.taskKeyword=""; state.expandedTasks=new Set(); }
  state.selectedProject=detail; state.todos=todoData.todos; state.todoPushConfigured=todoData.push_configured;
  closeDrawer();
  state.view = "project-detail";
  $$(".view").forEach(el => el.classList.toggle("hidden", el.id !== "view-project-detail"));
  $$("#nav > button").forEach(el => el.classList.toggle("active", el.dataset.view === "project-detail"));
  if (changingProject || window.matchMedia("(max-width: 640px)").matches) setProjectMenuExpanded(!window.matchMedia("(max-width: 640px)").matches);
  $("#page-title").textContent = detail.name;
  $("#page-subtitle").textContent = "任务、记录与项目全生命周期";
  $("#primary-action").textContent = "新增工作记录";
  $("#primary-action").disabled = false;
  const url = new URL(location.href); url.searchParams.set("view", "project-detail"); url.searchParams.set("project", id); history.replaceState(null, "", url);
  renderProjectMenu();
  renderProjectDetail();
}
function renderProjectDetail() {
  const detail = state.selectedProject;
  $("#page-subtitle").textContent = detail.description || "任务、记录与项目全生命周期";
  $("#project-detail-content").innerHTML = `
    <div class="detail-command"><button class="text-action" data-go="projects">项目列表 /</button><span class="${statusClass(detail.status)}">${esc(detail.status)}</span><span class="command-spacer"></span><button class="secondary" data-edit-project="${detail.id}">编辑项目</button></div>
    <section class="project-summary-band">${progressMeter(detail,true)}<div class="summary-fact"><small>开始日期</small><strong>${esc(detail.start_date||"未设置")}</strong></div><div class="summary-fact"><small>工作记录</small><strong>${detail.work_logs.length}</strong></div><div class="summary-fact"><small>项目成员</small><strong>${esc(detail.membership_history.filter(m=>!m.left_at).map(m=>m.name).join("、")||"暂无成员")}</strong></div></section>
    <nav class="detail-tabs" aria-label="项目详情视图">${[["overview","总览与任务"],["logs","工作记录"],["archive","项目档案"]].map(([id,label])=>`<button data-detail-tab="${id}" class="${state.detailTab===id?"active":""}" aria-pressed="${state.detailTab===id}">${label}</button>`).join("")}</nav>
    <div id="detail-tab-content"></div>`;
  renderDetailTab();
}
function renderDetailTab() {
  const detail=state.selectedProject, target=$("#detail-tab-content");
  $$("[data-detail-tab]").forEach(b=>{b.classList.toggle("active",b.dataset.detailTab===state.detailTab);b.setAttribute("aria-pressed",String(b.dataset.detailTab===state.detailTab));});
  if(state.detailTab==="overview") { target.innerHTML=taskOverview(detail,state.taskFilter,state.taskKeyword,state.expandedTasks); return; }
  if(state.detailTab==="logs") {
    target.innerHTML=`<section class="surface journal-panel"><div class="section-head"><div><h2 id="project-log-heading">工作记录</h2><p>按任务筛选，回看项目每一步</p></div><button class="primary" data-add-project-log>新增记录</button></div><div class="project-log-range"><label>开始日期<input type="date" id="project-log-start" value="${state.projectLogRange.start}" /></label><label>结束日期<input type="date" id="project-log-end" value="${state.projectLogRange.end}" /></label><label>关联任务<select id="project-log-task"><option value="">全部任务与记录</option><option value="none">项目级记录</option>${(detail.tasks||[]).map(t=>`<option value="${t.id}">${esc(t.name)}${t.deleted_at?"（已删除）":""}</option>`).join("")}</select></label><button class="secondary" data-all-project-logs>全部时间</button><button class="secondary" data-recent-project-logs>最近一周</button></div><div id="project-log-list"></div></section>`;
    renderProjectLogs(); return;
  }
  target.innerHTML=`<section class="surface"><div class="section-head"><h2>项目档案</h2></div><dl class="project-info-grid"><div><dt>项目 ID</dt><dd>#${detail.id}</dd></div><div><dt>计划周期</dt><dd>${esc(detail.start_date||"未设置")} — ${esc(detail.end_date||"未设置")}</dd></div><div><dt>创建时间</dt><dd>${fmt(detail.created_at)}</dd></div><div><dt>最近更新</dt><dd>${fmt(detail.updated_at)}</dd></div><div class="project-description"><dt>项目描述</dt><dd>${esc(detail.description||"暂无描述")}</dd></div></dl></section><div class="archive-columns">
    <section class="surface"><div class="section-head"><h2>状态变更</h2><button class="secondary" data-add-status-history>新增状态记录</button></div><div class="archive-content">${detail.status_history.map(h=>`<div class="history-item"><div><strong>${esc(h.from_status?`${h.from_status} → ${h.to_status}`:`创建为 ${h.to_status}`)}</strong><small>${fmt(h.changed_at)} · ${esc(h.note)}</small></div><div class="actions"><button class="text-action" data-edit-status-history="${h.id}">编辑</button><button class="text-action danger" data-delete-status-history="${h.id}">删除</button></div></div>`).join("")}</div></section>
    <section class="surface"><div class="section-head"><h2>成员变更</h2><button class="secondary" data-add-membership-history>新增成员记录</button></div><div class="archive-content">${detail.membership_history.map(h=>`<div class="history-item"><div><strong>${esc(h.name)} · ${h.left_at?"已退出":"参与中"}</strong><small>${esc(h.joined_at)} 加入${h.left_at?`，${esc(h.left_at)} 退出`:""}</small><small>${esc(h.join_reason||"—")} · ${esc(h.leave_reason||"—")}</small></div><div class="actions"><button class="text-action" data-edit-membership-history="${h.id}">编辑</button><button class="text-action danger" data-delete-membership-history="${h.id}">删除</button></div></div>`).join("")||`<div class="empty">暂无成员记录</div>`}</div></section></div>
    <section class="surface"><div class="section-head"><h2>关联待办</h2><button class="secondary" data-add-project-todo>新增待办</button></div><div class="archive-content">${state.todos.filter(t=>t.project_id===detail.id).map(t=>`<article class="log-card"><p>${esc(t.content)}</p><small>${esc(todoPushLabel(t))} · ${esc(todoScheduleLabel(t))}</small><div class="actions"><button class="text-action" data-edit-todo="${t.id}">编辑</button><button class="text-action danger" data-delete-todo="${t.id}">删除</button></div></article>`).join("")||`<div class="empty">暂无关联待办</div>`}</div></section>`;
}

function recentProjectLogRange() {
  const end = new Date(), start = new Date(end); start.setDate(start.getDate() - 6);
  return {start:iso(start), end:iso(end)};
}
function renderProjectLogs() {
  const {start,end} = state.projectLogRange;
  const task = $("#project-log-task")?.value || "";
  const logs = (state.selectedProject.work_logs||[]).filter(log=>(!start || log.work_date>=start) && (!end || log.work_date<=end) && (!task || (task==="none" ? !log.task_id : log.task_id===Number(task))));
  $("#project-log-heading").textContent = `每日记录 · ${logs.length} 条`;
  $("#project-log-list").innerHTML = logs.map(log=>`<article class="log-card"><div class="section-head compact-head"><strong>${esc(log.work_date)} ${logTaskLabel(log)}</strong><div class="actions"><button class="text-action" data-edit-project-log="${log.id}">编辑</button><button class="text-action danger" data-delete-project-log="${log.id}">删除</button></div></div><p>${esc(log.content)}</p><small>更新于 ${fmt(log.updated_at||log.created_at)}</small></article>`).join("") || `<div class="empty">所选时间区间暂无记录</div>`;
}
document.addEventListener("change", event => {
  if (!event.target.matches("#project-log-start, #project-log-end")) return;
  const start = $("#project-log-start").value, end = $("#project-log-end").value;
  if (start && end && start > end) {
    toast("开始日期不能晚于结束日期", true);
    $("#project-log-start").value = state.projectLogRange.start;
    $("#project-log-end").value = state.projectLogRange.end;
    return;
  }
  state.projectLogRange = {start,end}; renderProjectLogs();
});

function closeDrawer(){ $("#detail-drawer").classList.add("hidden"); $("#drawer-backdrop").classList.add("hidden"); }

function localDateTime(value) {
  if (!value) return "";
  const date = new Date(value), offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function statusHistoryModal(history={}) {
  const fromStatus=`<label class="field"><span>原状态</span><select name="from_status"><option value="" ${history.from_status?"":"selected"}>无（项目创建）</option>${["准备","进行","维护","暂停","结束"].map(status=>`<option ${status===history.from_status?"selected":""}>${status}</option>`).join("")}</select></label>`;
  openModal({title:history.id?"编辑状态变更历史":"新增状态变更历史",description:"保存后，项目当前状态会按最新一条历史自动重算。",fields:fromStatus+selectField("目标状态","to_status",["准备","进行","维护","暂停","结束"],history.to_status||state.selectedProject.status)+field("变更时间","changed_at",localDateTime(history.changed_at||new Date().toISOString()),"datetime-local",true,true)+textarea("变更说明","note",history.note),onSubmit:async form=>{
    const endpoint=history.id?"projects/status-history/save":"projects/status-history/add";
    await withLoading(()=>api.post(endpoint,{id:history.id,project_id:state.selectedProject.id,from_status:form.get("from_status"),to_status:form.get("to_status"),changed_at:form.get("changed_at"),note:form.get("note")}));closeModal();toast(history.id?"状态历史已更新":"状态历史已新增");await openProject(state.selectedProject.id);await Promise.all([loadProjects(),loadTimeline()]);
  }});
}

async function membershipHistoryModal(history={}) {
  if (!state.members.length) state.members=await api.get("members");
  const memberField=history.id?"":`<label class="field"><span>成员</span><select name="member_id" required>${state.members.map(member=>`<option value="${member.id}">${esc(member.name)}</option>`).join("")}</select></label>`;
  openModal({title:history.id?`编辑 ${history.name} 的成员变动`:"新增成员变动历史",description:"维护成员加入、退出日期及变动原因。",fields:memberField+field("加入日期","joined_at",history.joined_at||iso(today),"date",false,true)+field("退出日期","left_at",history.left_at,"date")+textarea("加入原因","join_reason",history.join_reason)+textarea("退出原因","leave_reason",history.leave_reason),onSubmit:async form=>{
    const endpoint=history.id?"projects/membership-history/save":"projects/membership-history/add";
    await withLoading(()=>api.post(endpoint,{id:history.id,project_id:state.selectedProject.id,member_id:Number(form.get("member_id")),joined_at:form.get("joined_at"),left_at:form.get("left_at"),join_reason:form.get("join_reason"),leave_reason:form.get("leave_reason")}));closeModal();toast(history.id?"成员历史已更新":"成员历史已新增");await openProject(state.selectedProject.id);await loadProjects();
  }});
}

async function generateSummaryModal(summaryDate) {
  const datedProjects=await withLoading(()=>api.get("projects",{summary_date:summaryDate}));
  const savedSummary=summaryByDate(summaryDate);
  const selectableProjects=datedProjects.filter(project=>project.status!=="结束"||project.has_records||project.has_task_changes);
  const selected = new Set(selectableProjects.filter(project=>project.has_records||project.has_task_changes).map(project=>project.id));
  const checks = `<div class="field full"><label>选择项目（默认勾选当天有记录或任务变更的项目）</label><div class="checkbox-grid">${selectableProjects.map(project=>`<label class="check-item"><input type="checkbox" name="project_ids" value="${project.id}" ${selected.has(project.id)?"checked":""} /><span>${esc(project.name)} · ${esc(project.status)}</span></label>`).join("")||`<span class="muted-note">没有可选项目</span>`}</div></div>`;
  const overwrite = savedSummary ? `<label class="check-item field full overwrite-option"><input type="checkbox" name="overwrite" /><span>覆盖该日已保存的总结；不勾选时仅生成预览</span></label>` : "";
  openModal({title:savedSummary?"重新生成每日总结":"生成每日总结",description:"按任务归纳当天工作，包含项目级记录、当日任务变更与截至当日的进度。默认勾选当天有记录或任务变更的项目，可手动调整。",fields:`<div class="field"><span>总结日期</span><strong class="readonly-value">${esc(summaryDate)}</strong></div>`+field("附带前几天总结","history_days",1,"number",false,true)+checks+overwrite+`<div id="team-summary-result" class="field full"></div>`,onSubmit:async (form,formElement)=>{
    const projectIds=form.getAll("project_ids").map(Number);
    if (!projectIds.length) { toast("请至少选择一个项目",true); return; }
    const historyDays=Number(form.get("history_days"));
    if (!Number.isInteger(historyDays)||historyDays<0||historyDays>30) { toast("附带天数必须是 0 到 30 的整数",true); return; }
    const result=await withGenerating(formElement,()=>withLoading(()=>api.post("ai/summary",{date:summaryDate,project_ids:projectIds,history_days:historyDays,overwrite:form.has("overwrite")})));
    if (result.persisted) {
      await loadSummaries(); closeModal(); toast(`${summaryDate} 每日总结已保存`); return;
    }
    state.summaryPreview=result;
    $("#team-summary-result").innerHTML=`<label>新生成的预览（尚未覆盖）</label><div class="ai-box summary-result">${esc(result.content)}</div><button type="button" class="primary preview-save" data-save-summary-preview>使用此结果覆盖已保存总结</button>`;
    toast("已生成预览，原总结未被覆盖");
  }});
  const historyInput=$("#modal-form [name=history_days]"); historyInput.min="0"; historyInput.max="30"; historyInput.step="1";
  $("#modal-form button[type=submit]").textContent=savedSummary?"重新生成":"生成并保存";
}

function openSummaryDetail(summaryDate) {
  const summary=summaryByDate(summaryDate);
  if (!summary) { generateSummaryModal(summaryDate); return; }
  openModal({title:`${summaryDate} 每日总结`,description:`${(summary.project_names||[]).join("、")||"全部项目"} · ${fmt(summary.created_at)} 更新`,fields:`<label class="field full"><span>总结内容</span><textarea class="summary-content-editor" name="content">${esc(summary.content)}</textarea></label><div class="field full summary-detail-actions"><button type="button" class="secondary" data-regenerate-summary="${summaryDate}">重新生成</button><button type="button" class="danger-button" data-delete-summary="${summaryDate}">删除总结</button></div>`,onSubmit:async form=>{
    const content=form.get("content").trim();
    if (!content) { toast("总结内容不能为空",true); return; }
    await withLoading(()=>api.post("summaries/save",{date:summaryDate,content}));
    await loadSummaries(); closeModal(); toast("总结内容已保存");
  }});
  $("#modal-form button[type=submit]").textContent="保存修改";
}

function attendanceModal(memberId="",dateValue=iso(today),status="加班") {
  const memberOptions=state.members.map(m=>`<option value="${m.id}" ${Number(memberId)===Number(m.id)?"selected":""}>${esc(m.name)}</option>`).join("");
  openModal({title:"登记考勤",description:"正常、加班、调休默认均为 8 小时。",fields:`<label class="field"><span>成员</span><select name="member_id" required>${memberOptions}</select></label>`+field("日期","date",dateValue,"date",false,true)+selectField("类型","status",["正常","加班","调休"],status)+field("时长（小时）","hours",8,"number")+textarea("说明","note",""),onSubmit:async form=>{
    await withLoading(()=>api.post("attendance/save",{member_id:Number(form.get("member_id")),date:form.get("date"),status:form.get("status"),hours:Number(form.get("hours")),note:form.get("note")}));closeModal();toast("考勤已保存");await loadAttendance();
  }});
}

document.addEventListener("click", async event => {
  const tab=event.target.closest("[data-detail-tab]"); if(tab){state.detailTab=tab.dataset.detailTab;renderDetailTab();return;}
  if(event.target.closest("[data-add-task]")){taskModal();return;}
  const taskControl=event.target.closest("[data-edit-task],[data-toggle-task],[data-delete-task],[data-add-task-log]");
  if(taskControl){
    const id=Number(taskControl.dataset.editTask||taskControl.dataset.toggleTask||taskControl.dataset.deleteTask||taskControl.dataset.addTaskLog), task=state.selectedProject.tasks.find(t=>t.id===id);
    if(taskControl.hasAttribute("data-edit-task")){taskModal(task);return;}
    if(taskControl.hasAttribute("data-add-task-log")){await workLogModal(state.selectedProject.id,iso(today),{task_id:id});return;}
    try {
      if(taskControl.hasAttribute("data-delete-task")){
        if(!await confirmAction("删除任务后将不计入进度，任务历史与关联记录仍会保留。确认删除？"))return;
        await withLoading(()=>api.post("tasks/delete",{id,project_id:state.selectedProject.id}));
      } else {
        await withLoading(()=>api.post("tasks/save",{id,project_id:state.selectedProject.id,status:task.status==="完成"?"未完成":"完成",completed_date:task.status==="完成"?null:iso(new Date())}));
      }
      await loadProjects(); await openProject(state.selectedProject.id); toast("任务已更新");
    }catch(_){} return;
  }
  const addLog=event.target.closest("[data-add-log-for-project]");if(addLog){await workLogModal(Number(addLog.dataset.addLogForProject));return;}
  if(event.target.closest("[data-all-project-logs]")){state.projectLogRange={start:"",end:""};renderDetailTab();return;}

  if (event.target.closest("[data-recent-project-logs]")) {
    state.projectLogRange = recentProjectLogRange();
    $("#project-log-start").value = state.projectLogRange.start;
    $("#project-log-end").value = state.projectLogRange.end;
    renderProjectLogs(); return;
  }
  const editTodo = event.target.closest("[data-edit-todo]");
  const deleteTodo = event.target.closest("[data-delete-todo]");
  if (editTodo || deleteTodo) {
    try {
      if (editTodo) await todoModal(state.todos.find(todo=>todo.id===Number(editTodo.dataset.editTodo)));
      else if (await confirmAction("确认删除这条待办？未发送的提醒将停止。")) {
        await withLoading(()=>api.post("todos/delete",{id:Number(deleteTodo.dataset.deleteTodo)}));
        toast("待办已删除"); await withLoading(loadTodos); if(state.view==="project-detail")await openProject(state.selectedProject.id);
      }
    } catch (_) { /* withLoading already displays the error. */ }
    return;
  }
  if(event.target.closest("[data-add-project-todo]")){await todoModal({project_id:state.selectedProject.id});return;}
  if(event.target.closest("[data-add-project-log]")){workLogModal(state.selectedProject.id);return;}
  const editLog=event.target.closest("[data-edit-project-log]");
  if(editLog){const log=state.selectedProject.work_logs.find(item=>item.id==editLog.dataset.editProjectLog);workLogModal(state.selectedProject.id,log.work_date,log);return;}
  const deleteLog=event.target.closest("[data-delete-project-log]");
  if(deleteLog){if(await confirmAction("确认删除这条工作记录？")){await withLoading(()=>api.post("worklogs/delete",{id:Number(deleteLog.dataset.deleteProjectLog)}));await openProject(state.selectedProject.id);await loadProjects();toast("工作记录已删除");}return;}
  const nav=event.target.closest("[data-view]"); if(nav){
    if(nav.dataset.view==="project-detail"){setProjectMenuExpanded(nav.getAttribute("aria-expanded")!=="true");return;}
    await switchView(nav.dataset.view);return;
  }
  const go=event.target.closest("[data-go]"); if(go){await switchView(go.dataset.go);return;}
  if(event.target.closest("[data-close-modal]")){closeModal();return;}
  if(event.target.closest("[data-close-drawer]")||event.target===$("#drawer-backdrop")){closeDrawer();return;}
  const deleteSummary=event.target.closest("[data-delete-summary]"); if(deleteSummary){if(await confirmAction(`确认删除 ${deleteSummary.dataset.deleteSummary} 的每日总结？`)){await withLoading(()=>api.post("summaries/delete",{date:deleteSummary.dataset.deleteSummary}));await loadSummaries();closeModal();toast("每日总结已删除");}return;}
  const regenerateSummary=event.target.closest("[data-regenerate-summary]"); if(regenerateSummary){await generateSummaryModal(regenerateSummary.dataset.regenerateSummary);return;}
  if(event.target.closest("[data-save-summary-preview]")){
    if (!state.summaryPreview) return;
    await withLoading(()=>api.post("summaries/save",{date:state.summaryPreview.date,content:state.summaryPreview.content,project_ids:state.summaryPreview.project_ids}));
    state.summaryPreview=null; await loadSummaries(); closeModal(); toast("新结果已覆盖原总结"); return;
  }
  const editMember=event.target.closest("[data-edit-member]"); if(editMember){memberModal(state.members.find(m=>m.id==editMember.dataset.editMember));return;}
  const deleteMember=event.target.closest("[data-delete-member]"); if(deleteMember){event.stopPropagation();if(await confirmAction("删除成员后，其历史项目与考勤记录仍会保留。确认删除？")){await withLoading(()=>api.post("members/delete",{id:Number(deleteMember.dataset.deleteMember)}));toast("成员已删除");await loadView("members");}return;}
  const member=event.target.closest("[data-member]"); if(member){openMember(Number(member.dataset.member));return;}
  if(event.target.closest("[data-add-status-history]")){statusHistoryModal();return;}
  const statusHistory=event.target.closest("[data-edit-status-history]"); if(statusHistory){const item=state.selectedProject.status_history.find(history=>history.id==statusHistory.dataset.editStatusHistory);statusHistoryModal(item);return;}
  const deleteStatusHistory=event.target.closest("[data-delete-status-history]"); if(deleteStatusHistory){if(await confirmAction("确认删除这条状态变更历史？项目当前状态将自动重算。")){const projectId=state.selectedProject.id;await withLoading(()=>api.post("projects/status-history/delete",{id:Number(deleteStatusHistory.dataset.deleteStatusHistory),project_id:projectId}));toast("状态历史已删除");await openProject(projectId);await Promise.all([loadProjects(),loadTimeline()]);}return;}
  if(event.target.closest("[data-add-membership-history]")){await membershipHistoryModal();return;}
  const membershipHistory=event.target.closest("[data-edit-membership-history]"); if(membershipHistory){const item=state.selectedProject.membership_history.find(history=>history.id==membershipHistory.dataset.editMembershipHistory);membershipHistoryModal(item);return;}
  const deleteMembershipHistory=event.target.closest("[data-delete-membership-history]"); if(deleteMembershipHistory){if(await confirmAction("确认删除这条成员变更历史？")){const projectId=state.selectedProject.id;await withLoading(()=>api.post("projects/membership-history/delete",{id:Number(deleteMembershipHistory.dataset.deleteMembershipHistory),project_id:projectId}));toast("成员历史已删除");await openProject(projectId);await loadProjects();}return;}
  const editDayLog=event.target.closest("[data-edit-day-log]"); if(editDayLog){const record=state.dayLogs.logs.find(log=>log.id==editDayLog.dataset.editDayLog);workLogModal(state.dayLogs.projectId,state.dayLogs.workDate,record);return;}
  const deleteDayLog=event.target.closest("[data-delete-day-log]"); if(deleteDayLog){if(await confirmAction("确认删除这条当天记录？")){const {projectId,workDate}=state.dayLogs;await withLoading(()=>api.post("worklogs/delete",{id:Number(deleteDayLog.dataset.deleteDayLog)}));toast("当天记录已删除");await Promise.all([loadProjects(),loadTimeline()]);await openDayLogs(projectId,workDate);}return;}
  if(event.target.closest("[data-add-day-log]")){workLogModal(state.dayLogs.projectId,state.dayLogs.workDate);return;}
  const editProject=event.target.closest("[data-edit-project]"); if(editProject){event.stopPropagation();await projectModal(state.projects.find(p=>p.id==editProject.dataset.editProject));return;}
  const deleteProject=event.target.closest("[data-delete-project]"); if(deleteProject){event.stopPropagation();if(await confirmAction("删除项目后历史记录仍会保留。确认删除？")){await withLoading(()=>api.post("projects/delete",{id:Number(deleteProject.dataset.deleteProject)}));toast("项目已删除");await loadView("projects");}return;}
  const timelineCell=event.target.closest("[data-log-project][data-log-date]"); if(timelineCell){await openDayLogs(Number(timelineCell.dataset.logProject),timelineCell.dataset.logDate);return;}
  const summaryCell=event.target.closest("[data-summary-date]"); if(summaryCell){openSummaryDetail(summaryCell.dataset.summaryDate);return;}
  const project=event.target.closest("[data-view-project],[data-project]"); if(project){await openProject(Number(project.dataset.viewProject||project.dataset.project));return;}
  const day=event.target.closest("[data-calendar-date]"); if(day){await withLoading(()=>api.post("calendar/save",{date:day.dataset.calendarDate,is_workday:day.dataset.workday!=="true",note:"WebUI 人工调整"}));await loadAttendance();return;}
  const att=event.target.closest("[data-att-date]"); if(att){attendanceModal(att.dataset.memberId,att.dataset.attDate,att.dataset.status||"加班");return;}
});


document.addEventListener("toggle",event=>{const id=event.target.dataset?.taskDetail;if(id){if(event.target.open)state.expandedTasks.add(Number(id));else state.expandedTasks.delete(Number(id));}},true);
document.addEventListener("change",event=>{if(event.target.id==="task-filter"){state.taskFilter=event.target.value;renderDetailTab();}if(event.target.id==="project-log-task")renderProjectLogs();});
function filterTaskSearch(event) {
  if(event.target.id!=="task-search" || event.isComposing)return;
  state.taskKeyword=event.target.value;
  const pos=event.target.selectionStart;
  renderDetailTab();
  $("#task-search").focus({preventScroll:true});
  $("#task-search").setSelectionRange(pos,pos);
}
document.addEventListener("input",filterTaskSearch);
document.addEventListener("compositionend",filterTaskSearch);

$("#member-search").addEventListener("input",renderMembers);
$("#todo-search").addEventListener("input",renderTodos);
$("#todo-project-filter").addEventListener("change",renderTodos);
$("#project-search").addEventListener("input",renderProjects);
$("#project-status-filter").addEventListener("change",renderProjects);
$("#record-status-filter").addEventListener("change",renderTimeline);
$("#record-search").addEventListener("input",renderTimeline);
$("#attendance-month").addEventListener("change",()=>withLoading(loadAttendance));
$("#summary-period").addEventListener("change",()=>withLoading(loadAttendance));
$("#timeline-start").addEventListener("change",()=>withLoading(()=>Promise.all([loadTimeline(),loadSummaries()])));
$("#timeline-end").addEventListener("change",()=>withLoading(()=>Promise.all([loadTimeline(),loadSummaries()])));
$("#refresh").addEventListener("click",()=>loadView(state.view));
$("#month-prev").addEventListener("click",()=>moveMonth(-1));
$("#month-next").addEventListener("click",()=>moveMonth(1));
function moveMonth(delta){const [y,m]=$("#attendance-month").value.split("-").map(Number),d=new Date(y,m-1+delta,1);$("#attendance-month").value=monthIso(d);withLoading(loadAttendance)}
$("#primary-action").addEventListener("click",async()=>{if(state.view==="project-records"){await generateSummaryModal($("#timeline-end").value);return;}if(state.view==="project-detail"){if(state.selectedProject)workLogModal(state.selectedProject.id);return;}if(state.view==="todos"){try {await todoModal();}catch(_){} return;}if(state.view==="members")memberModal();else if(state.view==="attendance")attendanceModal();else{if(!state.members.length)state.members=await api.get("members");await projectModal();}});
$("#modal-backdrop").addEventListener("click",closeModal);
$("#confirm-cancel").addEventListener("click",()=>resolveConfirmation(false));
$("#confirm-submit").addEventListener("click",()=>resolveConfirmation(true));
$("#confirm-backdrop").addEventListener("click",()=>resolveConfirmation(false));
document.addEventListener("keydown",event=>{
  const modal=confirmResolver?$("#confirm-modal"):!$("#modal").classList.contains("hidden")?$("#modal"):null;
  if(event.key==="Escape"){if(confirmResolver)resolveConfirmation(false);else if(modal)closeModal();else closeDrawer();}
  if(event.key==="Tab"&&modal){
    const controls=$$('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]',modal).filter(el=>el.getClientRects().length);
    const first=controls[0],last=controls.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  }
});

const initialView = new URLSearchParams(location.search).get("view");
await withLoading(loadProjects);
const initialProject = Number(new URLSearchParams(location.search).get("project"));
if (initialView === "project-detail" && state.projects.some(p => p.id === initialProject)) await openProject(initialProject);
else await switchView(viewMeta[initialView] ? initialView : "projects");
setInterval(() => {
  if (state.view === "todos" && !document.hidden && $("#modal").classList.contains("hidden") && !confirmResolver && !loadingCount) {
    loadTodos().catch(() => { $("#todo-push-notice").textContent = "待办状态刷新失败，请点击右上角刷新重试。"; });
  }
}, 15000);

function createDemoApi(){
  const now=new Date().toISOString(), dates=[-4,-3,-2,-1,0].map(n=>{const d=new Date();d.setDate(d.getDate()+n);return iso(d)});
  const store={members:[{id:1,name:"张三",role:"产品经理",notes:"",ready_projects:1,active_projects:1,maintenance_projects:0,paused_projects:0,completed_projects:1},{id:2,name:"陈小明",role:"前端工程师",notes:"",ready_projects:0,active_projects:1,maintenance_projects:1,paused_projects:0,completed_projects:1},{id:3,name:"刘芳",role:"后端工程师",notes:"",ready_projects:0,active_projects:1,maintenance_projects:1,paused_projects:0,completed_projects:0}],projects:[],logs:[],summaries:{},todos:[]};
  store.projects=[{id:1,name:"客户管理系统",description:"建设统一客户信息与合同管理平台",status:"进行",start_date:dates[0],end_date:null,created_at:now,updated_at:now,latest_record_date:dates[4],members:store.members},{id:2,name:"移动端 App",description:"员工移动办公应用",status:"维护",start_date:dates[0],end_date:null,created_at:now,updated_at:now,latest_record_date:dates[4],members:store.members.slice(1)},{id:3,name:"官网重构",description:"品牌官网视觉与内容升级",status:"结束",start_date:dates[0],end_date:dates[4],created_at:now,updated_at:now,latest_record_date:dates[4],members:store.members.slice(0,2)},{id:4,name:"数据分析平台",description:"经营数据指标与看板",status:"准备",start_date:null,end_date:null,created_at:now,updated_at:now,latest_record_date:null,members:[store.members[0]]}];
  store.projects.push({id:5,name:"内部工具升级",description:"等待资源后继续推进",status:"暂停",start_date:dates[0],end_date:null,created_at:now,updated_at:now,latest_record_date:null,members:[store.members[0]]});
  store.logs=[{id:1,project_id:1,work_date:dates[4],content:"完成需求评审，开始数据库设计"},{id:2,project_id:1,work_date:dates[4],content:"整理客户字段与权限矩阵"},{id:3,project_id:2,work_date:dates[4],content:"修复登录模块问题"},{id:4,project_id:3,work_date:dates[4],content:"完成上线验收"}];
  store.members.forEach(member => { member.projects = store.projects.filter(project => project.members.some(item => item.id === member.id)).map(project=>({...project,joined_at:dates[0],left_at:null})); });
  store.tasks = store.projects.slice(0,3).flatMap((p,i)=>[0,1,2].map((n)=>({id:i*3+n+1,project_id:p.id,name:["需求梳理","核心功能开发","联调与验收"][n],description:["确认范围与验收标准","交付核心流程","验证端到端工作流"][n],status:n<2?"完成":"未完成",start_date:dates[Math.min(n,2)],completed_date:n<2?dates[n+1]:null,created_at:now,updated_at:now,deleted_at:null,history:[]})));
  store.logs.forEach((log,i)=>{log.task_id=log.project_id===1?3:log.project_id===2?6:9;});
  const taskData=projectId=>store.tasks.filter(t=>t.project_id==projectId).map(t=>({...t,duration_days:Math.max(0,Math.round((Date.parse(t.completed_date||t.deleted_at?.slice(0,10)||iso(new Date()))-Date.parse(t.start_date))/86400000)+1)}));
  const progressData=p=>{const ts=taskData(p.id).filter(t=>!t.deleted_at),done=ts.filter(t=>t.status==="完成").length;return {...p,task_count:ts.length,completed_task_count:done,progress:ts.length?Math.round(done/ts.length*100):0};};
  const decoratedLog=log=>{const t=store.tasks.find(t=>t.id===log.task_id);return {...log,task_name:t?.name,task_deleted_at:t?.deleted_at};};
  const auditTask=(task,event)=>task.history.push({id:Date.now(),event,changed_at:new Date().toISOString(),snapshot:{name:task.name,status:task.status,start_date:task.start_date,completed_date:task.completed_date}});
  store.tasks.forEach(t=>auditTask(t,"创建"));
  const calendar=month=>{const end=Number(lastDay(month).slice(-2));return {month,workdays:0,days:Array.from({length:end},(_,i)=>{const date=`${month}-${String(i+1).padStart(2,"0")}`,weekday=new Date(date+"T00:00:00").getDay();const is_workday=weekday!==0&&weekday!==6;return {date,weekday:(weekday+6)%7,is_workday,source:"法定日历"}})}};
  const rangeDates=(start,end)=>{const result=[];for(let day=new Date(start+"T00:00:00");day<=new Date(end+"T00:00:00");day.setDate(day.getDate()+1))result.push(iso(day));return result};
  const todayValue=iso(today);
  const refreshLatestRecord=projectId=>{const project=store.projects.find(item=>item.id==projectId);if(project)project.latest_record_date=store.logs.filter(log=>log.project_id==projectId).reduce((latest,log)=>!latest||log.work_date>latest?log.work_date:latest,null)};
  const attendanceSummary=(start,end)=>{const boundedEnd=end>todayValue?todayValue:end,workdays=rangeDates(start,boundedEnd).filter(day=>{const weekday=new Date(day+"T00:00:00").getDay();return weekday!==0&&weekday!==6});return store.members.map(member=>({id:member.id,name:member.name,normal_days:workdays.length,overtime_days:0,time_off_days:0}))};
  store.todos = [{id:1,project_id:1,project_name:store.projects[0].name,content:"整理客户字段与权限清单，安排接口联调",push_mode:"once",push_enabled:0,push_at:null,created_at:now,updated_at:now,sent_at:null,push_error:""}];
  return {
    async get(endpoint,params={}){
      await new Promise(resolve=>setTimeout(resolve,80));
      if(endpoint==="tasks")return taskData(params.project_id);
      if(endpoint==="todos")return {todos:store.todos,push_configured:false};
      if(endpoint==="members")return store.members;
      if(endpoint==="projects")return params.summary_date?store.projects.map(project=>({...project,has_records:store.logs.some(log=>log.project_id===project.id&&log.work_date===params.summary_date),has_task_changes:store.tasks.some(task=>task.project_id===project.id&&task.history.some(h=>h.changed_at.slice(0,10)===params.summary_date))})):store.projects.map(progressData);
      if(endpoint==="summaries"){const range=rangeDates(params.start,params.end);return {dates:range,summaries:Object.values(store.summaries).filter(item=>range.includes(item.summary_date))}}
      if(endpoint==="timeline"){const range=rangeDates(params.start,params.end),logs={};store.logs.filter(log=>range.includes(log.work_date)).forEach(log=>(logs[`${log.project_id}:${log.work_date}`]??=[]).push(log));return {dates:range,projects:store.projects,logs}}
      if(endpoint==="worklogs"){const project=store.projects.find(item=>item.id==params.project_id);return {project,date:params.date,logs:store.logs.filter(log=>log.project_id==params.project_id&&log.work_date===params.date).map(decoratedLog)}}
      if(endpoint==="projects/detail"){const project=store.projects.find(item=>item.id==params.id);return {...progressData(project),tasks:taskData(project.id),work_logs:store.logs.filter(log=>log.project_id==params.id).map(decoratedLog).sort((a,b)=>b.work_date.localeCompare(a.work_date)||b.id-a.id),status_history:[{id:1,from_status:"准备",to_status:project.status,changed_at:now,note:"进入当前阶段"},{id:2,from_status:null,to_status:"准备",changed_at:dates[0],note:"项目创建"}],membership_history:project.members.map((member,index)=>({id:index+1,name:member.name,joined_at:dates[0],left_at:null,join_reason:"项目创建",leave_reason:""}))}}
      if(endpoint==="attendance"){const result=calendar(params.month);result.workdays=result.days.filter(day=>day.is_workday).length;const assignments={};result.days.filter(day=>day.date<=todayValue).forEach(day=>store.projects.filter(project=>(project.status==="进行"||(project.status==="维护"&&store.logs.some(log=>log.project_id===project.id&&log.work_date===day.date)))).forEach(project=>project.members.forEach(member=>(assignments[`${member.id}:${day.date}`]??=[]).push(project.name))));return {calendar:result,members:store.members,records:{},assignments,today:todayValue}}
      if(endpoint==="attendance/summary"){const start=params.start||`${monthIso(today)}-01`,requestedEnd=params.end||todayValue,end=requestedEnd>todayValue?todayValue:requestedEnd,summary=attendanceSummary(start,end);return {start,end,summary,details:[]}};
      return {};
    },
    async post(endpoint,body){
      if(endpoint==="tasks/save"){
        let task=store.tasks.find(t=>t.id===body.id), before=task?.status;
        const values={name:"",description:"",status:"未完成",start_date:iso(new Date()),completed_date:null,...task,...body};
        if(!values.name.trim())throw new Error("任务名称不能为空");
        if(values.status==="完成"&&(values.completed_date<values.start_date||values.completed_date>iso(new Date())))throw new Error("完成日期不能早于开始日期或晚于今天");
        if(!task){task={id:Date.now(),created_at:new Date().toISOString(),history:[]};store.tasks.push(task);}
        Object.assign(task,values,{id:task.id,updated_at:new Date().toISOString()});
        if(task.status==="未完成")task.completed_date=null;
        auditTask(task,!before?"创建":before!==task.status?(task.status==="完成"?"完成":"重新打开"):"修改");return task;
      }
      if(endpoint==="tasks/delete"){const task=store.tasks.find(t=>t.id===body.id);task.deleted_at=new Date().toISOString();auditTask(task,"删除");return {deleted:true};}
      if(endpoint==="todos/save"){
        const project=store.projects.find(item=>item.id===body.project_id);
        if(!project)throw new Error("请选择项目");
        const saved={...body,project_name:project.name,updated_at:new Date().toISOString()};
        if(body.push_mode==="weekly"){
          const now=Date.now(),local=new Date(now+body.push_utc_offset*60000),[hour,minute]=body.push_time.split(":").map(Number);
          for(let delta=0;delta<8;delta++){const next=new Date(Date.UTC(local.getUTCFullYear(),local.getUTCMonth(),local.getUTCDate()+delta,hour,minute));const due=next.getTime()-body.push_utc_offset*60000;if(body.push_weekdays.includes((next.getUTCDay()+6)%7)&&due>now){saved.push_due=due/1000;break;}}
        }else saved.push_due=body.push_at?new Date(body.push_at).getTime()/1000:null;
        if(body.id)Object.assign(store.todos.find(item=>item.id===body.id),saved);
        else store.todos.unshift({...saved,id:Date.now(),created_at:saved.updated_at,sent_at:null,push_error:""});
        return saved;
      }
      if(endpoint==="todos/delete"){store.todos=store.todos.filter(item=>item.id!==body.id);return {deleted:true};}
      await new Promise(resolve=>setTimeout(resolve,endpoint==="ai/summary"?450:120));
      if(endpoint==="ai/summary"){const content=store.projects.filter(p=>body.project_ids.includes(p.id)).map(p=>{const logs=store.logs.filter(l=>l.project_id===p.id&&l.work_date===body.date);return `${p.name}：${logs.length?logs.map(l=>`${store.tasks.find(t=>t.id===l.task_id)?.name||"项目级记录"}：${l.content}`).join("；"):"当天无工作记录"}。`}).join("\n"),persisted=!store.summaries[body.date]||body.overwrite;if(persisted)store.summaries[body.date]={id:Date.now(),summary_date:body.date,project_ids:body.project_ids,project_names:store.projects.filter(project=>body.project_ids.includes(project.id)).map(project=>project.name),provider_id:"demo",content,created_at:new Date().toISOString()};return {content,date:body.date,project_ids:body.project_ids,persisted}}
      if(endpoint==="summaries/save"){const saved=store.summaries[body.date];store.summaries[body.date]={...saved,content:body.content,project_ids:body.project_ids||saved.project_ids,created_at:new Date().toISOString()};store.summaries[body.date].project_names=store.projects.filter(project=>store.summaries[body.date].project_ids.includes(project.id)).map(project=>project.name);return store.summaries[body.date]}
      if(endpoint==="summaries/delete"){delete store.summaries[body.date];return {deleted:true}}
      if(endpoint==="worklogs/save"){
        if(!body.content?.trim())throw new Error("工作内容不能为空");
        const task=store.tasks.find(t=>t.id===body.task_id);
        if(task&&body.task_action&&body.task_action!=="keep"){
          if(body.task_action==="complete"&&(body.work_date<task.start_date||body.work_date>iso(new Date())))throw new Error("完成日期不能早于开始日期或晚于今天");
          const status=body.task_action==="complete"?"完成":"未完成";
          if(status!==task.status){task.status=status;task.completed_date=status==="完成"?body.work_date:null;auditTask(task,status==="完成"?"完成":"重新打开");}
        }
        if(body.id)Object.assign(store.logs.find(l=>l.id===body.id),body,{updated_at:new Date().toISOString()});else store.logs.push({...body,id:Date.now(),created_at:new Date().toISOString()});refreshLatestRecord(body.project_id);return body;
      }
      if(endpoint==="worklogs/delete"){const log=store.logs.find(item=>item.id==body.id);store.logs=store.logs.filter(item=>item.id!=body.id);if(log)refreshLatestRecord(log.project_id);return {deleted:true}}
      if(endpoint==="members/save"){if(body.id)Object.assign(store.members.find(member=>member.id===body.id),body);else store.members.push({...body,id:Date.now(),ready_projects:0,active_projects:0,maintenance_projects:0,paused_projects:0,completed_projects:0});return body}
      if(endpoint==="projects/save"){const members=store.members.filter(member=>body.member_ids.includes(member.id));if(body.id)Object.assign(store.projects.find(project=>project.id===body.id),body,{members,updated_at:now});else store.projects.unshift({...body,id:Date.now(),members,created_at:now,updated_at:now,latest_record_date:null});return body}
      return {saved:true};
    }
  };
}
