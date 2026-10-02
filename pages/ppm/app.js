import { setupTodos } from "./todos.js";
import { progressMeter, logTaskLabel, taskOverview } from "./tasks.js";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[c]);
const today = new Date();
const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const monthIso = d => iso(d).slice(0, 7);
const fmt = value => value ? new Date(value).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "—";
const statusClass = value => `status status-${esc(value)}`;

const state = {view:"projects", members:[], projects:[],  attendance:null, summary:null, timeline:null, summaryTimeline:null, selectedProject:null, summaryPreview:null, projectLogRange:null, detailTab:"overview", taskFilter:"all", taskKeyword:"", expandedTasks:new Set()};
async function requestApi(endpoint, options = {}) {
  const response = await fetch(`/api/${endpoint}`, options);
  const result = await response.json().catch(() => ({error:"服务返回异常，请刷新页面或检查服务日志"}));
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result.data;
}
const api = {
  get: (endpoint, params) => requestApi(endpoint + (params ? `?${new URLSearchParams(params)}` : "")),
  post: (endpoint, body) => requestApi(endpoint, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}),
};
const settings = {week_start:"monday",ui_color_theme:"forest"};
try { Object.assign(settings, (await api.get("health")).settings || {}); }
catch (error) { document.body.textContent = "连接服务失败：" + error.message; throw error; }
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
  models:["模型配置","连接模型服务，管理日报使用的模型","保存模型配置"],
  settings:["系统配置","界面色调与日历偏好","保存系统配置"],
  todos:["待办记录","随手记录，按项目整理，完成后勾选","新增待办"],
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

const mobileNavigationQuery = window.matchMedia("(max-width: 640px)");
function setMobileNavigation(open) {
  const mobile = mobileNavigationQuery.matches;
  const expanded = mobile && open;
  document.body.classList.toggle("navigation-open", expanded);
  $("#menu-toggle").setAttribute("aria-expanded", String(expanded));
  $("#navigation-backdrop").hidden = !expanded;
  $("main").inert = expanded;
  $("#app-navigation").inert = mobile && !expanded;
  if (expanded) $("#close-navigation").focus();
  else if (mobile && $("#app-navigation").contains(document.activeElement)) $("#menu-toggle").focus();
}
$("#menu-toggle").addEventListener("click", () => setMobileNavigation(true));
$("#close-navigation").addEventListener("click", () => setMobileNavigation(false));
$("#navigation-backdrop").addEventListener("click", () => setMobileNavigation(false));
mobileNavigationQuery.addEventListener("change", () => setMobileNavigation(false));
setMobileNavigation(false);

let navigationVersion = 0;
async function switchView(view) {
  navigationVersion++;
  setMobileNavigation(false);
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
    if (view === "settings" || view === "models") { await loadSettings(view); }
    if (view === "todos") { await todoUI.load(); }
  });
}

const todoUI = setupTodos({api,esc,withLoading,toast,openModal,closeModal,confirmAction});
let promptTemplates = {}, lastDiagnostics = null;
let modelListVersion = 0, savedModels = [], settingsBusy = false;
function resetModels(message = "连接配置已更改，请重新获取模型列表") {
  modelListVersion++;
  $("#available-models").replaceChildren(new Option("获取后选择模型", ""));
  $("#available-models").disabled = true; $("#add-model").disabled = true;
  $("#models-status").textContent = message;
}
function renderSavedModels() {
  const current = $("#model-form").elements.ai_model.value;
  $("#saved-models").innerHTML = savedModels.map(id => `<div class="saved-model"><button type="button" class="${id === current ? "primary" : "secondary"}" data-use-model="${esc(id)}">${esc(id)}${id === current ? " · 当前使用" : ""}</button><button type="button" class="text-action danger" data-remove-model="${esc(id)}" aria-label="移除 ${esc(id)}">移除</button></div>`).join("") || '<small>暂无模型，可从服务商列表添加或手动填写模型名称后保存。</small>';
}
async function loadSettings(view = state.view) {
  const config = await api.get("settings"), form = $(view === "models" ? "#model-form" : "#settings-form");
  for (const [key,value] of Object.entries(config)) if (form.elements[key]) form.elements[key].value = value;
  if (view === "models") {
    promptTemplates = await api.get("ai/templates");
    $("#prompt-template").replaceChildren(new Option("选择模板", ""), ...Object.entries(promptTemplates).map(([id,t])=>new Option(t.name,id)));
    resetModels("填写地址和密钥后获取模型列表，选择后点击“添加并使用”。");
    savedModels = [...new Set([...(config.ai_models || []), ...(config.ai_model ? [config.ai_model] : [])])];
    $("#key-status").textContent = "已保存的密钥直接显示；删除内容并保存即可清除。本地免密服务可留空。";
    renderSavedModels();
  }
}
async function saveSettings(view = state.view) {
  const model = view === "models", form = $(model ? "#model-form" : "#settings-form");
  if (settingsBusy || !form.reportValidity()) return false;
  settingsBusy = true;
  const buttons = [$("#primary-action"), ...$$("button[type=submit]",form)]; buttons.forEach(b=>b.disabled=true);
  try {
    const values = Object.fromEntries(new FormData(form));
    if (model) { values.ai_timeout = Number(values.ai_timeout); values.ai_models = savedModels; }
    const config = await withLoading(() => api.post("settings/save", values));
    if (model) { savedModels = config.ai_models || []; renderSavedModels(); }
    else { Object.assign(settings, values); document.documentElement.dataset.colorTheme = values.ui_color_theme; }
    toast(model ? "模型配置已保存" : "系统配置已保存"); return true;
  } finally { settingsBusy=false; buttons.forEach(b=>b.disabled=false); }
}
for (const [selector,view] of [["#settings-form","settings"],["#model-form","models"]]) $(selector).addEventListener("submit",event=>{event.preventDefault(); saveSettings(view).catch(()=>{});});
for (const name of ["ai_base_url", "ai_api_key", "ai_timeout"]) $("#model-form").elements[name].addEventListener("input",()=>resetModels());
$("#model-form").elements.ai_model.addEventListener("input",renderSavedModels);
$("#available-models").addEventListener("change",event=>{$("#add-model").disabled=!event.target.value;});
$("#add-model").addEventListener("click",async()=>{
  const id=$("#available-models").value; if(!id || settingsBusy)return;
  const form=$("#model-form"), before=form.elements.ai_model.value;
  form.elements.ai_model.value=id;
  try { if(!await saveSettings("models")) form.elements.ai_model.value=before; else $("#models-status").textContent=`已添加并使用 ${id}`; }
  catch(_) { form.elements.ai_model.value=before; }
  renderSavedModels();
});
$("#saved-models").addEventListener("click",async event=>{
  const button=event.target.closest("[data-use-model],[data-remove-model]");if(!button || settingsBusy)return;
  const form=$("#model-form"), previous=form.elements.ai_model.value, previousModels=[...savedModels];
  if(button.dataset.useModel) form.elements.ai_model.value=button.dataset.useModel;
  else { savedModels=savedModels.filter(id=>id!==button.dataset.removeModel); if(previous===button.dataset.removeModel)form.elements.ai_model.value=savedModels[0]||""; }
  try { if(!await saveSettings("models")) {savedModels=previousModels;form.elements.ai_model.value=previous;} }
  catch(_) {savedModels=previousModels;form.elements.ai_model.value=previous;}
  renderSavedModels();
});
$("#fetch-models").addEventListener("click", async event => {
  const form = $("#model-form"), button = event.currentTarget;
  if (!form.elements.ai_base_url.reportValidity() || !form.elements.ai_timeout.reportValidity()) return;
  resetModels("正在获取模型列表…"); const version = modelListVersion;
  button.disabled = true; button.textContent = "正在获取…";
  try {
    const result = await api.post("settings/models", {ai_base_url:form.elements.ai_base_url.value, ai_api_key:form.elements.ai_api_key.value, ai_timeout:Number(form.elements.ai_timeout.value)});
    if (version !== modelListVersion) return;
    const select = $("#available-models");
    select.replaceChildren(new Option("请选择模型", ""), ...result.models.map(id => new Option(id, id)));
    select.disabled = !result.models.length;
    if (result.models.includes(form.elements.ai_model.value)) {select.value=form.elements.ai_model.value;$("#add-model").disabled=false;}
    $("#models-status").textContent = result.models.length ? `已获取 ${result.models.length} 个模型，选择后可直接添加并使用。` : "服务商返回了空列表，可以手动填写模型名称。";
  } catch (error) { if (version === modelListVersion) $("#models-status").textContent = error.message; }
  finally { button.disabled = false; button.textContent = "获取模型列表"; }
});
$("#apply-prompt-template").addEventListener("click", async()=>{
  const template=promptTemplates[$("#prompt-template").value];
  if(!template) {toast("请先选择模板",true);return;}
  const input=$("#model-form").elements.ai_summary_prompt;
  if(input.value.trim() && !await confirmAction("用所选模板替换当前提示词？保存前仍可修改。"))return;
  input.value=template.prompt; toast("已填入模板，请保存模型配置");
});
async function copyText(text) {
  try { if(navigator.clipboard && window.isSecureContext) {await navigator.clipboard.writeText(text); return;} } catch(_) {}
  const input=document.createElement("textarea");input.value=text;input.style.position="fixed";input.style.opacity="0";document.body.append(input);input.select();
  const ok=document.execCommand("copy");input.remove();if(!ok)throw new Error("自动复制不可用，请选中文本手动复制");
}
async function diagnose(kind) {
  const buttons=[$("#test-model"),$("#diagnose-models")];buttons.forEach(b=>b.disabled=true);
  lastDiagnostics=null;$("#copy-diagnostics").disabled=true;
  $("#model-test-result").textContent="正在诊断已保存的配置…";
  try {
    const result=await api.post("settings/diagnose",{kind});lastDiagnostics=result;$("#copy-diagnostics").disabled=false;
    const info=result.ok ? (kind==="models" ? `发现 ${result.model_count} 个模型；当前模型${result.selected_model_listed?"在":"不在"}列表中` : result.reply) : result.error;
    $("#model-test-result").innerHTML=`<p><strong>${kind==="models"?"模型列表":"文本生成"}：${result.ok?"成功":"失败"}</strong> · ${result.elapsed_ms} ms · HTTP ${result.http_status??"未收到响应"}</p><p>${esc(info)}</p><details><summary>查看请求参数和诊断详情（已脱敏）</summary><pre class="diagnostic-json">${esc(JSON.stringify(result,null,2))}</pre></details>`;
  }catch(error){$("#model-test-result").textContent=error.message;}
  finally{buttons.forEach(b=>b.disabled=false);}
}
$("#test-model").addEventListener("click",()=>diagnose("generation"));
$("#diagnose-models").addEventListener("click",()=>diagnose("models"));
$("#copy-diagnostics").addEventListener("click",()=>copyText(JSON.stringify(lastDiagnostics,null,2)).then(()=>toast("脱敏诊断已复制")).catch(e=>toast(e.message,true)));

function metric(label, value, note = "") { return `<div class="metric"><label>${esc(label)}</label><strong>${Number(value || 0)}</strong><small>${esc(note)}</small></div>`; }

function labelTableCells(body) {
  const labels = $$("thead th", body.closest("table")).map(th => th.textContent);
  $$("tr", body).forEach(row => [...row.cells].forEach((cell, index) => cell.dataset.label = labels[index] || ""));
}

function renderMembers() {
  const keyword = $("#member-search").value.trim().toLowerCase();
  const items = state.members.filter(m => [m.name,m.role].join(" ").toLowerCase().includes(keyword));
  $("#members-body").innerHTML = items.map(m => `<tr data-member="${m.id}"><td><div class="member-cell"><span class="member-avatar">${esc(m.name.slice(0,1))}</span><div><strong>${esc(m.name)}</strong><small>${esc(m.role || "未设置岗位")}</small></div></div></td><td>${Number(m.ready_projects||0)}</td><td>${Number(m.active_projects||0)}</td><td>${Number(m.maintenance_projects||0)}</td><td>${Number(m.paused_projects||0)}</td><td>${Number(m.completed_projects||0)}</td><td><div class="actions"><button class="text-action" data-edit-member="${m.id}">编辑</button><button class="text-action danger" data-delete-member="${m.id}">删除</button></div></td></tr>`).join("");
  labelTableCells($("#members-body"));
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
  labelTableCells($("#projects-body"));
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
  $("#attendance-body").innerHTML = members.map(m=>`<tr><td><strong>${esc(m.name)}</strong></td>${calendar.days.map(d=>{
    if(d.date>todayValue)return `<td><button class="attendance-cell future" disabled aria-label="未来日期">—</button></td>`;
    const key=`${m.id}:${d.date}`,r=records[key],projects=assignments[key]||[];
    const isWorking = r ? r.status !== "调休" : d.is_workday;
    const cls=r?.status==="加班"?"overtime":r?.status==="调休"?"timeoff":isWorking?(projects.length?"assigned":"normal"):"rest";
    const label=r?.status==="加班"?"加":r?.status==="调休"?"休":isWorking?(projects.length?"项":"勤"):"—";
    const title=isWorking?(projects.length?`参与项目：${projects.join("、")}`:"正常出勤；未参与项目"):"休息";
    return `<td><button class="attendance-cell ${cls}" data-member-id="${m.id}" data-att-date="${d.date}" data-status="${r?.status||""}" title="${esc(title)}">${label}</button></td>`;
  }).join("")}</tr>`).join("");
  const periodLabel={week:"周",month:"月",year:"年"}[$("#summary-period").value];
  $("#summary-title").textContent=`${periodLabel}度汇总`;
  $("#summary-range").textContent=`${state.summary.start} 至 ${state.summary.end} · 正常、加班、调休天数及异常明细`;
  $("#attendance-summary").innerHTML = `<div class="summary-row header"><span>成员</span><span>正常</span><span>加班</span><span>调休</span></div>` + state.summary.summary.map(r=>`<div class="summary-row"><strong>${esc(r.name)}</strong><span>${Number(r.normal_days||0)}</span><span>${Number(r.overtime_days||0)}</span><span>${Number(r.time_off_days||0)}</span></div>`).join("");
  $("#attendance-details").innerHTML = `<h3 style="margin-top:0">加班 / 调休明细</h3>` + (state.summary.details.map(r=>`<div class="detail-line"><time>${esc(r.work_date.slice(5))}</time><strong>${esc(r.name)}</strong><span class="${r.status==="加班"?"status status-维护":"status status-暂停"}">${esc(r.status)} ${Number(r.hours||0)}h</span></div>`).join("") || `<p style="color:var(--muted)">当前周期暂无异常记录</p>`);
}

let modalReturnFocus = null;
function openModal({title,description="",fields,onSubmit}) {
  if($("#modal").classList.contains("hidden")) modalReturnFocus=document.activeElement;
  $("#modal-title").textContent=title; $("#modal-description").textContent=description; $("#modal-fields").innerHTML=fields;
  $(".modal-actions").classList.remove("hidden");
  $("#modal-form").onchange=null;
  $("#modal-form button[type=submit]").disabled=false;
  delete $("#modal-form button[type=submit]").dataset.needsReview;
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
    finally { button.disabled = button.dataset.needsReview === "true"; }
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
  const button=$("button[type=submit]",form), label=button.textContent, marker=$("#modal-fields").firstElementChild;
  const controls=$$("input,select,textarea,button",form).map(el=>[el,el.disabled]);
  controls.forEach(([el])=>el.disabled=true);button.textContent="生成中…";
  try { return await operation(); }
  finally { if($("#modal-fields").firstElementChild===marker){controls.forEach(([el,disabled])=>el.disabled=disabled);button.textContent=label;} }
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
  setMobileNavigation(false);
  const version = ++navigationVersion;
  const detail = await withLoading(()=>api.get("projects/detail",{id}));
  if (version !== navigationVersion) return;
  const changingProject = state.view !== "project-detail" || state.selectedProject?.id !== detail.id;
  if (changingProject) { window.scrollTo(0, 0); state.projectLogRange = {start:"",end:""}; state.detailTab="overview"; state.taskFilter="all"; state.taskKeyword=""; state.expandedTasks=new Set(); }
  state.selectedProject=detail;
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
    <div class="detail-command"><button class="text-action" data-go="projects">项目列表 /</button><span class="${statusClass(detail.status)}">${esc(detail.status)}</span><span class="command-spacer"></span><button class="secondary" data-project-todos="${detail.id}">项目待办</button><button class="secondary" data-edit-project="${detail.id}">编辑项目</button></div>
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
    `;
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
  const [datedProjects,templates]=await withLoading(()=>Promise.all([api.get("projects",{summary_date:summaryDate}),api.get("ai/templates")]));
  const savedSummary=summaryByDate(summaryDate);
  const selected=new Set(datedProjects.filter(p=>p.has_records||p.has_task_changes).map(p=>p.id));
  const checks=`<div class="field full"><label>选择项目</label><div class="checkbox-grid">${datedProjects.map(p=>`<label class="check-item"><input type="checkbox" name="project_ids" value="${p.id}" ${selected.has(p.id)?"checked":""}/><span>${esc(p.name)}</span></label>`).join("")||"暂无项目"}</div></div>`;
  let reviewed=null, reviewing=false;
  const options=`<label class="field"><span>汇报范围</span><select name="period"><option value="daily">当天日报</option><option value="weekly">截至该日的七天周报（预览 / 复制）</option></select></label><label class="field"><span>本次写作模板</span><select name="template"><option value="custom">已保存的自定义提示词</option>${Object.entries(templates).map(([id,t])=>`<option value="${id}">${esc(t.name)}</option>`).join("")}</select></label>`;
  openModal({title:"生成工作汇报",description:"先核对模型、写作要求和工作资料，再生成。周报不会覆盖每日总结。",fields:`<div class="field full"><span>汇报截止日期</span><strong>${esc(summaryDate)}</strong></div>`+options+field("附带前几天日报（仅作背景）","history_days",1,"number",false,true)+checks+(savedSummary?`<label class="check-item field full"><input type="checkbox" name="overwrite"/><span>覆盖该日已保存的日报；不勾选时仅生成预览</span></label>`:"")+`<div class="field full"><button type="button" class="secondary" id="review-summary">核对生成资料</button></div><div id="summary-input-preview" class="field full"></div><div id="team-summary-result" class="field full"></div>`,onSubmit:async(form,formElement)=>{
    if(!reviewed){toast("请先核对生成资料",true);return;}
    const data=readInputs();
    if(JSON.stringify(data)!==reviewed.inputs){invalidate();toast("选项已变化，请重新核对资料",true);return;}
    let result;
    try {result=await withGenerating(formElement,()=>withLoading(()=>api.post("ai/summary",{...data,fingerprint:reviewed.fingerprint,overwrite:form.has("overwrite")})));}
    catch(error){invalidate();throw error;}
    if($("#review-summary")!==review)return;
    if(result.persisted){await loadSummaries();closeModal();toast(`${summaryDate} 日报已保存`);return;}
    state.summaryPreview=result;
    $("#team-summary-result").innerHTML=`<label>${result.period==="weekly"?"周报预览（未保存，不覆盖日报）":"日报预览（尚未覆盖原总结）"}</label><textarea readonly class="summary-content-editor" aria-label="生成结果">${esc(result.content)}</textarea><button type="button" class="secondary" id="copy-summary-result">复制汇报</button>${result.period==="daily"?'<button type="button" class="primary preview-save" data-save-summary-preview>使用此结果覆盖已保存总结</button>':""}`;
    $("#copy-summary-result").addEventListener("click",()=>copyText(result.content).then(()=>toast("汇报已复制")).catch(e=>toast(e.message,true)));
    toast("已生成预览，尚未保存");
  }});
  const form=$("#modal-form"),submit=$("button[type=submit]",form),review=$("#review-summary");
  submit.disabled=true;submit.dataset.needsReview="true";submit.textContent="确认资料并生成";
  const history=form.elements.history_days;history.min="0";history.max="30";history.step="1";
  function readInputs(){return {date:summaryDate,project_ids:new FormData(form).getAll("project_ids").map(Number),history_days:form.elements.period.value==="weekly"?0:Number(history.value),period:form.elements.period.value,template:form.elements.template.value};}
  function invalidate(){reviewed=null;submit.disabled=true;submit.dataset.needsReview="true";state.summaryPreview=null;$("#summary-input-preview").textContent="请核对最新生成资料。";$("#team-summary-result").replaceChildren();}
  form.onchange=event=>{
    if(event.target.name==="overwrite")return;
    if(event.target.name==="template" && event.target.value==="weekly")form.elements.period.value="weekly";
    if(event.target.name==="period" || (event.target.name==="template" && event.target.value==="weekly")){
      const weekly=form.elements.period.value==="weekly";history.disabled=weekly;history.closest(".field").classList.toggle("hidden",weekly);
      if(weekly){form.elements.template.value="weekly";$$('[name="project_ids"]',form).forEach(i=>i.checked=true);}
      else if(form.elements.template.value==="weekly")form.elements.template.value="custom";
      if(form.elements.overwrite){form.elements.overwrite.disabled=weekly;form.elements.overwrite.checked=false;form.elements.overwrite.closest("label").classList.toggle("hidden",weekly);}
    }
    invalidate();
  };
  history.addEventListener("input",invalidate);
  review.addEventListener("click",async()=>{
    if(reviewing || !form.reportValidity())return;
    const data=readInputs();if(!data.project_ids.length){toast("请至少选择一个项目",true);return;}
    reviewing=true;review.disabled=true;submit.disabled=true;
    try{
      const result=await withLoading(()=>api.post("ai/preview",data));
      if($("#review-summary")!==review || JSON.stringify(readInputs())!==JSON.stringify(data))return;
      reviewed={fingerprint:result.fingerprint,inputs:JSON.stringify(data)};
      $("#summary-input-preview").innerHTML=`<p><strong>已核对 · ${esc(result.model)}</strong><br>${esc(result.start)} 至 ${esc(result.date)} · ${result.project_ids.length} 个项目 · 历史背景 ${result.history_days} 天</p><details open><summary>本次写作要求</summary><pre class="diagnostic-json">${esc(result.writing_prompt||"未填写自定义要求")}</pre></details><details><summary>固定事实约束</summary><p>${esc(result.rules)}</p></details><details><summary>查看完整工作资料</summary><pre class="diagnostic-json">${esc(result.material)}</pre></details><details><summary>模型地址与实际请求参数</summary><pre class="diagnostic-json">${esc(result.endpoint)}\n${esc(JSON.stringify(result.request,null,2))}</pre></details>`;
      submit.disabled=false;submit.dataset.needsReview="false";
    }catch(_){reviewed=null;}finally{reviewing=false;review.disabled=false;}
  });
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
  openModal({title:"登记考勤",description:"正常、加班、调休默认均为 8 小时。",fields:`<label class="field"><span>成员</span><select name="member_id" required>${memberOptions}</select></label>`+field("日期","date",dateValue,"date",false,true)+selectField("类型","status",["正常","加班","调休"],status)+field("时长（小时）","hours",8,"number"),onSubmit:async form=>{
    await withLoading(()=>api.post("attendance/save",{member_id:Number(form.get("member_id")),date:form.get("date"),status:form.get("status"),hours:Number(form.get("hours"))}));closeModal();toast("考勤已保存");await loadAttendance();
  }});
}

document.addEventListener("click", async event => {
  const projectTodos=event.target.closest("[data-project-todos]");if(projectTodos){await switchView("todos");await withLoading(()=>todoUI.load(Number(projectTodos.dataset.projectTodos)));return;}
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
$("#primary-action").addEventListener("click",async()=>{if(state.view==="models"||state.view==="settings"){await saveSettings().catch(()=>{});return;}if(state.view==="todos"){await withLoading(()=>todoUI.open()).catch(()=>{});return;}if(state.view==="project-records"){await generateSummaryModal($("#timeline-end").value);return;}if(state.view==="project-detail"){if(state.selectedProject)workLogModal(state.selectedProject.id);return;}if(state.view==="members")memberModal();else if(state.view==="attendance")attendanceModal();else{if(!state.members.length)state.members=await api.get("members");await projectModal();}});
$("#modal-backdrop").addEventListener("click",closeModal);
$("#confirm-cancel").addEventListener("click",()=>resolveConfirmation(false));
$("#confirm-submit").addEventListener("click",()=>resolveConfirmation(true));
$("#confirm-backdrop").addEventListener("click",()=>resolveConfirmation(false));
document.addEventListener("keydown",event=>{
  const modal=confirmResolver?$("#confirm-modal"):!$("#modal").classList.contains("hidden")?$("#modal"):document.body.classList.contains("navigation-open")?$("#app-navigation"):null;
  if(event.key==="Escape"){if(confirmResolver)resolveConfirmation(false);else if(document.body.classList.contains("navigation-open"))setMobileNavigation(false);else if(modal)closeModal();else closeDrawer();}
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
