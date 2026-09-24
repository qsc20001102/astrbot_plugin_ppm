const esc = (value = "") => String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[c]);
const dayNumber = value => Date.parse(`${value}T00:00:00Z`) / 86400000;
const localToday = () => { const d = new Date(); return new Date(d - d.getTimezoneOffset()*60000).toISOString().slice(0,10); };
const taskEndLabel = task => task.completed_date || (task.deleted_at ? task.deleted_at.slice(0,10) : task.start_date > localToday() ? "尚未开始" : "至今");

export function progressMeter(project, large = false) {
  const total = Number(project.task_count || 0), done = Number(project.completed_task_count || 0);
  const percent = total ? Math.round(done / total * 100) : 0;
  return `<div class="project-progress ${large ? "large" : ""}"><strong>${percent}<span>%</span></strong><div><small>${total ? `已完成 ${done} / ${total} 项任务` : "尚未设置任务"}</small><progress max="100" value="${percent}" aria-label="任务完成进度 ${percent}%"></progress></div></div>`;
}

export function logTaskLabel(log) {
  return `<span class="task-label">${esc(log.task_name || "项目级记录")}${log.task_deleted_at ? " · 任务已删除" : ""}</span>`;
}

function taskJournal(task, logs) {
  return `<div class="task-journal"><div class="task-journal-head"><strong>${esc(task.start_date)} — ${esc(taskEndLabel(task))}</strong><span>${task.duration_days} 天</span></div>
    ${task.description ? `<p>${esc(task.description)}</p>` : ""}
    <div class="task-records">${logs.map(log => `<article><time>${esc(log.work_date)}</time><p>${esc(log.content)}</p><button class="text-action" data-edit-project-log="${log.id}">编辑</button></article>`).join("") || `<p class="muted-note">这项任务还没有关联记录。</p>`}</div>
    <details class="task-history"><summary>变更历史 · ${(task.history || []).length} 次</summary>${(task.history || []).slice().reverse().map(h => `<div><time>${esc(h.changed_at.replace("T"," ").slice(0,16))}</time><strong>${esc(h.event)}</strong><span>${esc(h.snapshot.name)} · ${esc(h.snapshot.status)} · ${esc(h.snapshot.start_date)} — ${esc(h.snapshot.completed_date || "未完成")}</span></div>`).join("")}</details></div>`;
}

export function taskOverview(project, filter = "all", keyword = "", expanded = new Set()) {
  const tasks = project.tasks || [], logs = project.work_logs || [], now = localToday();
  const bounds = [project.start_date || project.created_at?.slice(0,10), ...tasks.flatMap(t=>[t.start_date,t.completed_date,t.deleted_at?.slice(0,10)]), ...logs.map(l=>l.work_date)].filter(Boolean).sort();
  const start = bounds[0] || now, end = [bounds.at(-1) || now, ...(tasks.some(t=>!t.deleted_at && t.status === "未完成") ? [now] : [])].sort().at(-1);
  const span = Math.max(1,dayNumber(end)-dayNumber(start)+1), position = date => Math.max(0, Math.min(100, (dayNumber(date)-dayNumber(start))/span*100));
  const visible = tasks.filter(t => (filter === "deleted" ? t.deleted_at : !t.deleted_at && (filter === "all" || t.status === filter)) && `${t.name} ${t.description}`.toLowerCase().includes(keyword.toLowerCase()));
  const ticks = [...new Set(Array.from({length:5},(_,i)=> new Date((dayNumber(start)+Math.floor((span-1)*i/4))*86400000).toISOString().slice(0,10)))];
  const rows = visible.map(task => {
    const records = logs.filter(l=>l.task_id===task.id);
    const taskEnd = task.completed_date || task.deleted_at?.slice(0,10) || now;
    const left = position(task.start_date), width = Math.max(.6, (Math.max(0, dayNumber(taskEnd)-dayNumber(task.start_date))+1)/span*100);
    return `<details class="gantt-task" data-task-detail="${task.id}" ${expanded.has(task.id) ? "open" : ""}><summary><span class="gantt-name"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg><strong>${esc(task.name)}</strong><span class="task-state ${task.status==="完成" ? "done" : ""}">${task.deleted_at ? "已删除" : task.status}</span></span><span class="gantt-track"><span class="gantt-bar ${task.status==="完成" ? "done" : ""}" style="left:${left}%;width:${Math.min(width,100-left)}%"></span>${records.map(l=>`<i class="record-dot" style="left:${position(l.work_date)}%" title="${esc(l.work_date+" · "+l.content)}"></i>`).join("")}</span></summary>${taskJournal(task,records)}</details>`;
  }).join("");
  return `<section class="surface lifecycle-panel"><div class="section-head"><div><h2>任务生命周期</h2><p>展开任务，查看时间范围、工作记录与每次变更</p></div><button class="primary" data-add-task>新增任务</button></div>
    <div class="task-filter"><input id="task-search" aria-label="搜索任务" placeholder="搜索任务名称或描述" value="${esc(keyword)}"><select id="task-filter" aria-label="筛选任务状态">${[["all","全部任务"],["未完成","未完成"],["完成","完成"],["deleted","已删除任务"]].map(([value,label])=>`<option value="${value}" ${filter===value?"selected":""}>${label}</option>`).join("")}</select><small>${esc(start)} — ${esc(end)} · 圆点代表工作记录</small></div>
    <div class="gantt-scroll"><div class="gantt"><div class="gantt-axis"><span>任务 / 状态</span><div>${ticks.map(d=>`<time>${d.slice(5)}</time>`).join("")}</div></div>${rows || `<div class="empty">${tasks.length ? "没有符合条件的任务" : "把项目拆成任务，进度就有了依据。点击「新增任务」开始。"}</div>`}</div></div></section>
    <section class="surface task-table-panel"><div class="section-head"><h2>任务清单 <small>${visible.length}</small></h2><span class="muted-note">持续天数按自然日计算，含首尾日期</span></div><div class="task-table-scroll"><table><thead><tr><th>任务</th><th>时间范围</th><th>记录</th><th>状态</th><th>操作</th></tr></thead><tbody>${visible.map(t=>`<tr><td><strong>${esc(t.name)}</strong><small class="task-description">${esc(t.description)}</small></td><td>${esc(t.start_date)} — ${esc(taskEndLabel(t))}<small class="task-description">${t.duration_days} 天</small></td><td>${logs.filter(l=>l.task_id===t.id).length}</td><td><span class="task-state ${t.status==="完成"?"done":""}">${t.deleted_at?"已删除":t.status}</span></td><td><div class="actions">${t.deleted_at ? "历史保留" : `<button class="text-action" data-add-task-log="${t.id}">记一笔</button><button class="text-action" data-toggle-task="${t.id}">${t.status==="完成"?"重开":"完成"}</button><button class="text-action" data-edit-task="${t.id}">编辑</button><button class="text-action danger" data-delete-task="${t.id}">删除</button>`}</div></td></tr>`).join("")}</tbody></table></div>${!visible.length?`<div class="empty compact-empty">暂无任务</div>`:""}</section>
    <div class="project-level-note">${logs.filter(l=>!l.task_id).length} 条项目级记录未关联任务，可在「工作记录」中查看或补充关联。</div>`;
}
