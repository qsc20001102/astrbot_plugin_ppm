const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[c]);
const today = new Date();
const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const monthIso = d => iso(d).slice(0, 7);
const fmt = value => value ? new Date(value).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "—";
const statusClass = value => `status status-${esc(value)}`;
const ADVANCING_STATUSES = new Set(["准备","进行","维护"]);

const state = {view:"dashboard", members:[], projects:[], attendance:null, summary:null, timeline:null, summaryTimeline:null, dashboard:null, selectedProject:null, summaryPreview:null};
const bridge = window.AstrBotPluginPage;
const api = bridge ? {
  get: (endpoint, params) => bridge.apiGet(endpoint, params),
  post: (endpoint, body) => bridge.apiPost(endpoint, body),
} : createDemoApi();

if (bridge) await bridge.ready();
$("#today-label").textContent = today.toLocaleDateString("zh-CN", {year:"numeric",month:"long",day:"numeric",weekday:"short"});
$("#attendance-month").value = monthIso(today);
$("#timeline-end").value = iso(today);
const weekAgo = new Date(today); weekAgo.setDate(weekAgo.getDate() - 6);
$("#timeline-start").value = iso(weekAgo);

const viewMeta = {
  dashboard:["工作台","团队项目与考勤概览","新建项目"],
  members:["团队成员","成员档案、项目参与及出勤统计","新增成员"],
  attendance:["考勤管理","公司日历、成员考勤与周期汇总","登记考勤"],
  projects:["项目管理","状态、成员变动、每日进展与 AI 日报","新建项目"],
};

async function withLoading(operation) {
  $("#loading").classList.remove("hidden");
  try { return await operation(); }
  catch (error) { toast(error.message || "操作失败", true); throw error; }
  finally { $("#loading").classList.add("hidden"); }
}

function toast(message, error = false) {
  const el = $("#toast"); el.textContent = message; el.className = `toast${error ? " error" : ""}`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.add("hidden"), 2600);
}

async function switchView(view) {
  state.view = view;
  $$(".view").forEach(el => el.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  $$("#nav button").forEach(el => el.classList.toggle("active", el.dataset.view === view));
  const [title, subtitle, action] = viewMeta[view];
  $("#page-title").textContent = title; $("#page-subtitle").textContent = subtitle; $("#primary-action").textContent = action;
  await loadView(view);
}

async function loadView(view) {
  return withLoading(async () => {
    if (view === "dashboard") { state.dashboard = await api.get("dashboard"); renderDashboard(); }
    if (view === "members") { state.members = await api.get("members"); renderMembers(); }
    if (view === "attendance") { await loadAttendance(); }
    if (view === "projects") { await Promise.all([loadProjects(), loadTimeline(), loadSummaries()]); }
  });
}

function metric(label, value, note = "") { return `<div class="metric"><label>${esc(label)}</label><strong>${Number(value || 0)}</strong><small>${esc(note)}</small></div>`; }

function renderDashboard() {
  const data = state.dashboard, metrics = data.metrics || {};
  $("#dashboard-metrics").innerHTML = metric("全部项目", metrics.total, "持续跟踪") + metric("进行中", metrics.active, "当前执行") + metric("已完成", metrics.finished, "累计完成") + metric("已暂停", metrics.paused, "需要关注");
  $("#recent-projects").innerHTML = (data.recent_projects || []).map(p => `<div class="list-row" data-project="${p.id}"><strong>${esc(p.name)}</strong><span class="${statusClass(p.status)}">${esc(p.status)}</span><small class="latest-record-date">${esc(p.latest_record_date || "暂无记录")}</small><small>${fmt(p.updated_at)}</small></div>`).join("") || `<div class="empty">暂无项目数据</div>`;
  const att = data.attendance || {}, total = Number(att.normal||0)+Number(att.overtime||0)+Number(att.time_off||0) || 1;
  $("#attendance-overview").innerHTML = `<span class="big-number">${Number(att.normal||0)}</span><span> 人日正常出勤（截至今日）</span><div class="overview-bars">${overviewBar("正常",att.normal,total,"#0da66e")}${overviewBar("加班",att.overtime,total,"#d88912")}${overviewBar("调休",att.time_off,total,"#e5484d")}</div>`;
}
function overviewBar(label, value, total, color) { return `<div><div class="bar-label"><span>${label}</span><strong>${Number(value||0)}</strong></div><div class="bar"><i style="width:${Math.min(100,Number(value||0)/total*100)}%;background:${color}"></i></div></div>`; }

function renderMembers() {
  const keyword = $("#member-search").value.trim().toLowerCase();
  const items = state.members.filter(m => [m.name,m.role].join(" ").toLowerCase().includes(keyword));
  $("#members-body").innerHTML = items.map(m => `<tr data-member="${m.id}"><td><div class="member-cell"><span class="member-avatar">${esc(m.name.slice(0,1))}</span><div><strong>${esc(m.name)}</strong><small>${esc(m.role || "未设置岗位")}</small></div></div></td><td>${Number(m.ready_projects||0)}</td><td>${Number(m.active_projects||0)}</td><td>${Number(m.maintenance_projects||0)}</td><td>${Number(m.paused_projects||0)}</td><td>${Number(m.completed_projects||0)}</td><td><div class="actions"><button class="text-action" data-edit-member="${m.id}">编辑</button><button class="text-action danger" data-delete-member="${m.id}">删除</button></div></td></tr>`).join("");
  $("#members-empty").classList.toggle("hidden", items.length > 0);
}

async function loadProjects() { state.projects = await api.get("projects"); renderProjects(); }
function renderProjects() {
  const counts = Object.fromEntries(["准备","进行","维护","暂停","结束"].map(s => [s,state.projects.filter(p=>p.status===s).length]));
  $("#project-metrics").innerHTML = ["准备","进行","维护","暂停","结束"].map(s=>metric(s,counts[s],s==="进行"?"正在执行":"项目状态")).join("");
  const keyword = $("#project-search").value.trim().toLowerCase();
  const statuses = new Set($$("#project-status-filter input:checked").map(input => input.value));
  const items = state.projects.filter(p => statuses.has(p.status) && [p.id,p.name,p.description,p.latest_record_date].join(" ").toLowerCase().includes(keyword));
  $("#projects-body").innerHTML = items.map(p => `<tr data-project="${p.id}"><td><div class="project-cell"><strong>${esc(p.name)}</strong><small>项目ID：${esc(p.id)}</small></div></td><td><span class="${statusClass(p.status)}">${esc(p.status)}</span></td><td>${memberStack(p.members)}</td><td>${esc(p.latest_record_date || "暂无记录")}</td><td>${fmt(p.updated_at)}</td><td><div class="actions"><button class="text-action" data-view-project="${p.id}">查看</button><button class="text-action" data-edit-project="${p.id}">编辑</button><button class="text-action danger" data-delete-project="${p.id}">删除</button></div></td></tr>`).join("");
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
  const statuses = new Set($$("#project-status-filter input:checked").map(input => input.value));
  const projects = (t?.projects||[]).filter(project=>statuses.has(project.status));
  if (!t || !projects.length) { $("#timeline").innerHTML = `<div class="empty">当前筛选条件下暂无项目进程</div>`; return; }
  const head = t.dates.map(d=>`<th>${esc(d.slice(5))}<small style="display:block;color:#8b95a6">${"日一二三四五六"[new Date(d+"T00:00:00").getDay()]}</small></th>`).join("");
  const rows = projects.map(p=>`<tr><td><strong>${esc(p.name)}</strong></td>${t.dates.map(d=>{const logs=t.logs[`${p.id}:${d}`]||[];return `<td><button class="timeline-cell ${logs.length?"has-log":""}" data-log-project="${p.id}" data-log-date="${d}" title="${esc(logs.map(l=>l.content).join("\n"))}">${logs.length?`${logs.length} 条记录`:"—"}</button></td>`}).join("")}</tr>`).join("");
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
    const offset = (anchor.getDay() + 6) % 7, start = new Date(anchor); start.setDate(anchor.getDate() - offset);
    const end = new Date(start); end.setDate(start.getDate() + 6);
    return {start:iso(start),end:iso(end)};
  }
  return {start:`${month}-01`,end:lastDay(month)};
}
function renderAttendance() {
  const {calendar,members,records,assignments,today:todayValue} = state.attendance;
  $("#workdays-count").textContent = `${calendar.workdays} 个工作日`;
  $("#calendar-grid").innerHTML = ["一","二","三","四","五","六","日"].map(d=>`<div class="calendar-week">${d}</div>`).join("") + `<div style="grid-column:span ${calendar.days[0].weekday}"></div>` + calendar.days.map(d=>`<button class="calendar-day ${d.is_workday?"workday":"restday"} ${d.source==="人工调整"?"manual":""}" data-calendar-date="${d.date}" data-workday="${d.is_workday}" title="${esc(d.source)}"><strong>${Number(d.date.slice(-2))}</strong><small>${d.is_workday?"上班":"休班"}</small></button>`).join("");
  $("#attendance-head").innerHTML = `<tr><th>成员</th>${calendar.days.map(d=>`<th>${Number(d.date.slice(-2))}<small style="display:block">${"一二三四五六日"[d.weekday]}</small></th>`).join("")}</tr>`;
  $("#attendance-body").innerHTML = members.map(m=>`<tr><td><strong>${esc(m.name)}</strong></td>${calendar.days.map(d=>{if(d.date>todayValue)return `<td><button class="attendance-cell future" disabled aria-label="未来日期">—</button></td>`;const key=`${m.id}:${d.date}`,r=records[key],projects=assignments[key]||[];const cls=r?.status==="加班"?"overtime":r?.status==="调休"?"timeoff":d.is_workday?(projects.length?"assigned":"normal"):"rest";const label=r?(r.status==="加班"?"加":r.status==="调休"?"休":projects.length?"项":"勤"):(d.is_workday?(projects.length?"项":"勤"):"—");const defaultTitle=d.is_workday?(projects.length?`参与项目：${projects.join("、")}`:"默认正常出勤；未参与项目"):"休息日";return `<td><button class="attendance-cell ${cls}" data-member-id="${m.id}" data-att-date="${d.date}" data-status="${r?.status||""}" title="${esc([defaultTitle,r?.note].filter(Boolean).join("；"))}">${label}</button></td>`}).join("")}</tr>`).join("");
  const periodLabel={week:"周",month:"月",year:"年"}[$("#summary-period").value];
  $("#summary-title").textContent=`${periodLabel}度汇总`;
  $("#summary-range").textContent=`${state.summary.start} 至 ${state.summary.end} · 正常、加班、调休天数及异常明细`;
  $("#attendance-summary").innerHTML = `<div class="summary-row header"><span>成员</span><span>正常</span><span>加班</span><span>调休</span></div>` + state.summary.summary.map(r=>`<div class="summary-row"><strong>${esc(r.name)}</strong><span>${Number(r.normal_days||0)}</span><span>${Number(r.overtime_days||0)}</span><span>${Number(r.time_off_days||0)}</span></div>`).join("");
  $("#attendance-details").innerHTML = `<h3 style="margin-top:0">加班 / 调休明细</h3>` + (state.summary.details.map(r=>`<div class="detail-line"><time>${esc(r.work_date.slice(5))}</time><strong>${esc(r.name)}</strong><span class="${r.status==="加班"?"status status-维护":"status status-暂停"}">${esc(r.status)} ${Number(r.hours||0)}h</span><span>${esc(r.note||"")}</span></div>`).join("") || `<p style="color:var(--muted)">本月暂无异常记录</p>`);
}

function openModal({title,description="",fields,onSubmit}) {
  $("#modal-title").textContent=title; $("#modal-description").textContent=description; $("#modal-fields").innerHTML=fields;
  $(".modal-actions").classList.remove("hidden");
  $("#modal-form button[type=submit]").textContent="保存";
  $("#modal").classList.remove("hidden"); $("#modal-backdrop").classList.remove("hidden");
  $("#modal-form").onsubmit = async event => {
    event.preventDefault();
    try { await onSubmit(new FormData(event.currentTarget),event.currentTarget); }
    catch (_) { /* withLoading 已向用户显示统一错误，避免重复抛送。 */ }
  };
}
function closeModal() { $("#modal").classList.add("hidden"); $("#modal-backdrop").classList.add("hidden"); }
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
    await withLoading(()=>api.post("projects/save",payload)); closeModal(); toast("项目已保存"); await loadView("projects");
  }});
}

function workLogModal(projectId, workDate=iso(today), record={}) {
  openModal({title:record.id?"编辑当天记录":"新增当天记录",description:"只记录日期和实际工作内容。",fields:field("日期","work_date",record.work_date||workDate,"date",false,true)+textarea("工作内容","content",record.content||"",true,"work-log-content"),onSubmit:async form=>{
    const date=form.get("work_date");
    await withLoading(()=>api.post("worklogs/save",{id:record.id,project_id:projectId,work_date:date,content:form.get("content")})); closeModal(); toast(record.id?"记录已更新":"记录已新增"); await Promise.all([loadProjects(),loadTimeline()]); await openDayLogs(projectId,date);
  }});
}

async function openDayLogs(projectId, workDate) {
  const detail=await withLoading(()=>api.get("worklogs",{project_id:projectId,date:workDate}));
  const rows=(detail.logs||[]).map(log=>`<div class="day-log-item"><label class="field full"><span>工作内容</span><textarea class="work-log-content day-log-content" readonly>${esc(log.content)}</textarea></label><div class="day-log-footer"><small>记录日期：${esc(log.work_date)}</small><div class="actions"><button type="button" class="text-action" data-edit-day-log="${log.id}">编辑</button><button type="button" class="text-action danger" data-delete-day-log="${log.id}">删除</button></div></div></div>`).join("")||`<div class="empty">当天暂无记录</div>`;
  openModal({title:`${detail.project.name} · ${workDate}`,description:"查看并维护该项目当天的全部工作记录。",fields:`<div class="field full"><div class="section-head compact-head"><h3>当天记录</h3><button type="button" class="primary" data-add-day-log>新增记录</button></div><div id="day-log-list">${rows}</div></div>`,onSubmit:async()=>{}});
  state.dayLogs={projectId:Number(projectId),workDate,logs:detail.logs||[]};
  $(".modal-actions").classList.add("hidden");
}

async function openProject(id) {
  const detail = await withLoading(()=>api.get("projects/detail",{id})); state.selectedProject=detail;
  $("#drawer-content").innerHTML = `<div class="drawer-head"><div><h2>${esc(detail.name)}</h2><span class="${statusClass(detail.status)}">${esc(detail.status)}</span></div><button class="icon-button" data-close-drawer>×</button></div><div class="drawer-body project-detail-body">
    <div class="detail-section"><h3>项目信息</h3><dl class="definition-list"><dt>项目ID</dt><dd>${esc(detail.id)}</dd><dt>项目描述</dt><dd>${esc(detail.description||"—")}</dd><dt>计划周期</dt><dd>${esc(detail.start_date||"—")} 至 ${esc(detail.end_date||"—")}</dd><dt>创建时间</dt><dd>${fmt(detail.created_at)}</dd></dl></div>
    <div class="detail-section"><div class="section-head compact-head"><h3>状态变更历史</h3><button class="primary" data-add-status-history>新增状态记录</button></div>${detail.status_history.map(h=>`<div class="history-item"><div><strong>${esc(h.from_status?`${h.from_status} → ${h.to_status}`:`创建为 ${h.to_status}`)}</strong><small>${fmt(h.changed_at)}${h.note?` · ${esc(h.note)}`:""}</small></div><div class="actions"><button class="text-action" data-edit-status-history="${h.id}">编辑</button><button class="text-action danger" data-delete-status-history="${h.id}">删除</button></div></div>`).join("")||"<p style='color:var(--muted)'>暂无状态记录</p>"}</div>
    <div class="detail-section"><div class="section-head compact-head"><h3>成员变更历史</h3><button class="primary" data-add-membership-history>新增成员记录</button></div>${detail.membership_history.map(h=>`<div class="history-item"><div><strong>${esc(h.name)} · ${h.left_at?"已退出":"参与中"}</strong><small>${esc(h.joined_at)} 加入${h.left_at?`，${esc(h.left_at)} 退出`:""}</small></div><div class="actions"><button class="text-action" data-edit-membership-history="${h.id}">编辑</button><button class="text-action danger" data-delete-membership-history="${h.id}">删除</button></div></div>`).join("")||"<p style='color:var(--muted)'>暂无成员记录</p>"}</div>
  </div>`;
  $("#detail-drawer").classList.remove("hidden"); $("#drawer-backdrop").classList.remove("hidden");
}
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
  if (!state.projects.length) await loadProjects();
  const savedSummary=summaryByDate(summaryDate);
  const selectableProjects=state.projects.filter(project=>project.status!=="结束");
  const selected = new Set(savedSummary?.project_ids || selectableProjects.filter(project=>ADVANCING_STATUSES.has(project.status)).map(project=>project.id));
  const checks = `<div class="field full"><label>选择项目（暂停项目默认不选）</label><div class="checkbox-grid">${selectableProjects.map(project=>`<label class="check-item"><input type="checkbox" name="project_ids" value="${project.id}" ${selected.has(project.id)?"checked":""} /><span>${esc(project.name)} · ${esc(project.status)}</span></label>`).join("")||`<span class="muted-note">没有推进中或暂停的项目</span>`}</div></div>`;
  const overwrite = savedSummary ? `<label class="check-item field full overwrite-option"><input type="checkbox" name="overwrite" /><span>覆盖该日已保存的总结；不勾选时仅生成预览</span></label>` : "";
  openModal({title:savedSummary?"重新生成项目总结":"生成项目总结",description:"只显示推进中和暂停的项目；可附带前几天已保存的总结作为上下文。",fields:`<div class="field"><span>总结日期</span><strong class="readonly-value">${esc(summaryDate)}</strong></div>`+field("附带前几天总结","history_days",3,"number",false,true)+checks+overwrite+`<div id="team-summary-result" class="field full"></div>`,onSubmit:async (form,formElement)=>{
    const projectIds=form.getAll("project_ids").map(Number);
    if (!projectIds.length) { toast("请至少选择一个项目",true); return; }
    const historyDays=Number(form.get("history_days"));
    if (!Number.isInteger(historyDays)||historyDays<0||historyDays>30) { toast("附带天数必须是 0 到 30 的整数",true); return; }
    const result=await withGenerating(formElement,()=>withLoading(()=>api.post("ai/summary",{date:summaryDate,project_ids:projectIds,history_days:historyDays,overwrite:form.has("overwrite")})));
    if (result.persisted) {
      await loadSummaries(); closeModal(); toast(`${summaryDate} 项目总结已保存`); return;
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
  openModal({title:`${summaryDate} 项目总结`,description:`${(summary.project_names||[]).join("、")||"全部项目"} · ${fmt(summary.created_at)} 更新`,fields:`<label class="field full"><span>总结内容</span><textarea class="summary-content-editor" name="content">${esc(summary.content)}</textarea></label><div class="field full summary-detail-actions"><button type="button" class="secondary" data-regenerate-summary="${summaryDate}">重新生成</button><button type="button" class="danger-button" data-delete-summary="${summaryDate}">删除总结</button></div>`,onSubmit:async form=>{
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
  const nav=event.target.closest("[data-view]"); if(nav){await switchView(nav.dataset.view);return;}
  const go=event.target.closest("[data-go]"); if(go){await switchView(go.dataset.go);return;}
  if(event.target.closest("[data-close-modal]")){closeModal();return;}
  if(event.target.closest("[data-close-drawer]")||event.target===$("#drawer-backdrop")){closeDrawer();return;}
  const deleteSummary=event.target.closest("[data-delete-summary]"); if(deleteSummary){if(await confirmAction(`确认删除 ${deleteSummary.dataset.deleteSummary} 的项目总结？`)){await withLoading(()=>api.post("summaries/delete",{date:deleteSummary.dataset.deleteSummary}));await loadSummaries();closeModal();toast("项目总结已删除");}return;}
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

$("#nav").addEventListener("keydown",e=>{if(e.key==="Enter")e.target.click()});
$("#member-search").addEventListener("input",renderMembers);
$("#project-search").addEventListener("input",renderProjects);
$("#project-status-filter").addEventListener("change",()=>{renderProjects();renderTimeline();});
$("#attendance-month").addEventListener("change",()=>withLoading(loadAttendance));
$("#summary-period").addEventListener("change",()=>withLoading(loadAttendance));
$("#timeline-start").addEventListener("change",()=>withLoading(()=>Promise.all([loadTimeline(),loadSummaries()])));
$("#timeline-end").addEventListener("change",()=>withLoading(()=>Promise.all([loadTimeline(),loadSummaries()])));
$("#refresh").addEventListener("click",()=>loadView(state.view));
$("#month-prev").addEventListener("click",()=>moveMonth(-1));
$("#month-next").addEventListener("click",()=>moveMonth(1));
function moveMonth(delta){const [y,m]=$("#attendance-month").value.split("-").map(Number),d=new Date(y,m-1+delta,1);$("#attendance-month").value=monthIso(d);withLoading(loadAttendance)}
$("#primary-action").addEventListener("click",async()=>{if(state.view==="members")memberModal();else if(state.view==="attendance")attendanceModal();else{if(!state.members.length)state.members=await api.get("members");await projectModal();}});
$("#modal-backdrop").addEventListener("click",closeModal);
$("#confirm-cancel").addEventListener("click",()=>resolveConfirmation(false));
$("#confirm-submit").addEventListener("click",()=>resolveConfirmation(true));
$("#confirm-backdrop").addEventListener("click",()=>resolveConfirmation(false));
document.addEventListener("keydown",event=>{if(event.key==="Escape"&&confirmResolver)resolveConfirmation(false);});

const initialView = new URLSearchParams(location.search).get("view");
await switchView(viewMeta[initialView] ? initialView : "dashboard");

function createDemoApi(){
  const now=new Date().toISOString(), dates=[-4,-3,-2,-1,0].map(n=>{const d=new Date();d.setDate(d.getDate()+n);return iso(d)});
  const store={members:[{id:1,name:"张三",role:"产品经理",notes:"",ready_projects:1,active_projects:1,maintenance_projects:0,paused_projects:0,completed_projects:1},{id:2,name:"陈小明",role:"前端工程师",notes:"",ready_projects:0,active_projects:1,maintenance_projects:1,paused_projects:0,completed_projects:1},{id:3,name:"刘芳",role:"后端工程师",notes:"",ready_projects:0,active_projects:1,maintenance_projects:1,paused_projects:0,completed_projects:0}],projects:[],logs:[],summaries:{}};
  store.projects=[{id:1,name:"客户管理系统",description:"建设统一客户信息与合同管理平台",status:"进行",start_date:dates[0],end_date:null,created_at:now,updated_at:now,latest_record_date:dates[4],members:store.members},{id:2,name:"移动端 App",description:"员工移动办公应用",status:"维护",start_date:dates[0],end_date:null,created_at:now,updated_at:now,latest_record_date:dates[4],members:store.members.slice(1)},{id:3,name:"官网重构",description:"品牌官网视觉与内容升级",status:"结束",start_date:dates[0],end_date:dates[4],created_at:now,updated_at:now,latest_record_date:dates[4],members:store.members.slice(0,2)},{id:4,name:"数据分析平台",description:"经营数据指标与看板",status:"准备",start_date:null,end_date:null,created_at:now,updated_at:now,latest_record_date:null,members:[store.members[0]]}];
  store.projects.push({id:5,name:"内部工具升级",description:"等待资源后继续推进",status:"暂停",start_date:dates[0],end_date:null,created_at:now,updated_at:now,latest_record_date:null,members:[store.members[0]]});
  store.logs=[{id:1,project_id:1,work_date:dates[4],content:"完成需求评审，开始数据库设计"},{id:2,project_id:1,work_date:dates[4],content:"整理客户字段与权限矩阵"},{id:3,project_id:2,work_date:dates[4],content:"修复登录模块问题"},{id:4,project_id:3,work_date:dates[4],content:"完成上线验收"}];
  store.members.forEach(member => { member.projects = store.projects.filter(project => project.members.some(item => item.id === member.id)).map(project=>({...project,joined_at:dates[0],left_at:null})); });
  const calendar=month=>{const end=Number(lastDay(month).slice(-2));return {month,workdays:0,days:Array.from({length:end},(_,i)=>{const date=`${month}-${String(i+1).padStart(2,"0")}`,weekday=new Date(date+"T00:00:00").getDay();const is_workday=weekday!==0&&weekday!==6;return {date,weekday:(weekday+6)%7,is_workday,source:"法定日历"}})}};
  const rangeDates=(start,end)=>{const result=[];for(let day=new Date(start+"T00:00:00");day<=new Date(end+"T00:00:00");day.setDate(day.getDate()+1))result.push(iso(day));return result};
  const todayValue=iso(today);
  const refreshLatestRecord=projectId=>{const project=store.projects.find(item=>item.id==projectId);if(project)project.latest_record_date=store.logs.filter(log=>log.project_id==projectId).reduce((latest,log)=>!latest||log.work_date>latest?log.work_date:latest,null)};
  const attendanceSummary=(start,end)=>{const boundedEnd=end>todayValue?todayValue:end,workdays=rangeDates(start,boundedEnd).filter(day=>{const weekday=new Date(day+"T00:00:00").getDay();return weekday!==0&&weekday!==6});return store.members.map(member=>({id:member.id,name:member.name,normal_days:workdays.length,overtime_days:0,time_off_days:0}))};
  const attendanceTotals=rows=>rows.reduce((totals,row)=>({normal:totals.normal+row.normal_days,overtime:totals.overtime+row.overtime_days,time_off:totals.time_off+row.time_off_days}),{normal:0,overtime:0,time_off:0});
  return {
    async get(endpoint,params={}){
      await new Promise(resolve=>setTimeout(resolve,80));
      if(endpoint==="dashboard"){const rows=attendanceSummary(`${monthIso(today)}-01`,todayValue);return {metrics:{total:5,active:1,finished:1,paused:1},recent_projects:store.projects,attendance:attendanceTotals(rows)}};
      if(endpoint==="members")return store.members;
      if(endpoint==="projects")return store.projects;
      if(endpoint==="summaries"){const range=rangeDates(params.start,params.end);return {dates:range,summaries:Object.values(store.summaries).filter(item=>range.includes(item.summary_date))}}
      if(endpoint==="timeline"){const range=rangeDates(params.start,params.end),logs={};store.logs.filter(log=>range.includes(log.work_date)).forEach(log=>(logs[`${log.project_id}:${log.work_date}`]??=[]).push(log));return {dates:range,projects:store.projects,logs}}
      if(endpoint==="worklogs"){const project=store.projects.find(item=>item.id==params.project_id);return {project,date:params.date,logs:store.logs.filter(log=>log.project_id==params.project_id&&log.work_date===params.date)}}
      if(endpoint==="projects/detail"){const project=store.projects.find(item=>item.id==params.id);return {...project,status_history:[{id:1,from_status:"准备",to_status:project.status,changed_at:now,note:"进入当前阶段"},{id:2,from_status:null,to_status:"准备",changed_at:dates[0],note:"项目创建"}],membership_history:project.members.map((member,index)=>({id:index+1,name:member.name,joined_at:dates[0],left_at:null,join_reason:"项目创建",leave_reason:""}))}}
      if(endpoint==="attendance"){const result=calendar(params.month);result.workdays=result.days.filter(day=>day.is_workday).length;const assignments={};result.days.filter(day=>day.date<=todayValue).forEach(day=>store.projects.filter(project=>ADVANCING_STATUSES.has(project.status)).forEach(project=>project.members.forEach(member=>(assignments[`${member.id}:${day.date}`]??=[]).push(project.name))));return {calendar:result,members:store.members,records:{},assignments,today:todayValue}}
      if(endpoint==="attendance/summary"){const start=params.start||`${monthIso(today)}-01`,requestedEnd=params.end||todayValue,end=requestedEnd>todayValue?todayValue:requestedEnd,summary=attendanceSummary(start,end);return {start,end,summary,details:[]}};
      return {};
    },
    async post(endpoint,body){
      await new Promise(resolve=>setTimeout(resolve,endpoint==="ai/summary"?450:120));
      if(endpoint==="ai/summary"){const content="今日完成：所选项目均按计划推进。\n风险与阻塞：暂无明确阻塞。\n明日建议：继续推进接口联调与验收。",persisted=!store.summaries[body.date]||body.overwrite;if(persisted)store.summaries[body.date]={id:Date.now(),summary_date:body.date,project_ids:body.project_ids,project_names:store.projects.filter(project=>body.project_ids.includes(project.id)).map(project=>project.name),provider_id:"demo",content,created_at:new Date().toISOString()};return {content,date:body.date,project_ids:body.project_ids,persisted}}
      if(endpoint==="summaries/save"){const saved=store.summaries[body.date];store.summaries[body.date]={...saved,content:body.content,project_ids:body.project_ids||saved.project_ids,created_at:new Date().toISOString()};store.summaries[body.date].project_names=store.projects.filter(project=>store.summaries[body.date].project_ids.includes(project.id)).map(project=>project.name);return store.summaries[body.date]}
      if(endpoint==="summaries/delete"){delete store.summaries[body.date];return {deleted:true}}
      if(endpoint==="worklogs/save"){if(body.id){const log=store.logs.find(item=>item.id==body.id);Object.assign(log,body);refreshLatestRecord(log.project_id)}else{store.logs.push({...body,id:Date.now()});refreshLatestRecord(body.project_id)}return body}
      if(endpoint==="worklogs/delete"){const log=store.logs.find(item=>item.id==body.id);store.logs=store.logs.filter(item=>item.id!=body.id);if(log)refreshLatestRecord(log.project_id);return {deleted:true}}
      if(endpoint==="members/save"){if(body.id)Object.assign(store.members.find(member=>member.id===body.id),body);else store.members.push({...body,id:Date.now(),ready_projects:0,active_projects:0,maintenance_projects:0,paused_projects:0,completed_projects:0});return body}
      if(endpoint==="projects/save"){const members=store.members.filter(member=>body.member_ids.includes(member.id));if(body.id)Object.assign(store.projects.find(project=>project.id===body.id),body,{members,updated_at:now});else store.projects.unshift({...body,id:Date.now(),members,created_at:now,updated_at:now,latest_record_date:null});return body}
      return {saved:true};
    }
  };
}
