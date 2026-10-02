export function setupTodos({api, esc, withLoading, toast, openModal, closeModal, confirmAction}) {
  const $ = selector => document.querySelector(selector);
  let items = [], projects = [], generation = 0;
  async function load(projectId) {
    const version = ++generation;
    const [rows, available] = await Promise.all([api.get("todos"), api.get("projects")]);
    if (version !== generation) return;
    items = rows; projects = available;
    if (projectId !== undefined) {$("#todo-search").value="";$("#todo-status-filter").value="";}
    const filter = $("#todo-project-filter"), selected = projectId === undefined ? filter.value : String(projectId);
    const options = new Map(projects.map(p => [p.id, p.name]));
    for (const todo of items) if (todo.project_id && !options.has(todo.project_id)) options.set(todo.project_id, `${todo.project_name}（已删除）`);
    filter.replaceChildren(new Option("全部项目", ""), new Option("未关联项目", "none"), ...[...options].map(([id,name]) => new Option(name,id)));
    filter.value = [...filter.options].some(o => o.value === selected) ? selected : "";
    render();
  }
  function render() {
    const search = $("#todo-search").value.trim().toLowerCase(), project = $("#todo-project-filter").value, status = $("#todo-status-filter").value;
    const visible = items.filter(t => (!status || t.status === status) && (!project || (project === "none" ? !t.project_id : String(t.project_id) === project)) && `${t.content} ${t.project_name || ""}`.toLowerCase().includes(search));
    $("#todo-count").textContent = `共 ${items.length} 项 · 未完成 ${items.filter(t => t.status === "未完成").length} 项 · 当前显示 ${visible.length} 项`;
    $("#todo-list").innerHTML = visible.map(t => `<article class="surface todo-card ${t.status === "已完成" ? "todo-completed" : ""}">
      <button class="todo-check" data-todo-toggle="${t.id}" aria-label="${t.status === "已完成" ? "标记未完成" : "标记已完成"}" aria-pressed="${t.status === "已完成"}">${t.status === "已完成" ? "✓" : "○"}</button>
      <div class="todo-main"><div class="todo-meta"><strong>${esc(t.status)}</strong><span>${esc(t.project_name || "未关联项目")}${t.project_deleted_at ? "（已删除）" : ""}</span></div><p>${esc(t.content)}</p><small>更新于 ${esc(new Date(t.updated_at).toLocaleString("zh-CN"))}</small></div>
      <div class="actions"><button class="text-action" data-todo-edit="${t.id}">编辑</button><button class="text-action danger" data-todo-delete="${t.id}">删除</button></div></article>`).join("") || '<div class="surface empty">暂无符合条件的待办，可重置筛选或点击右上角新增。</div>';
  }
  async function open(todo = {}) {
    const choices = await api.get("projects");
    if (todo.project_id && !choices.some(p => p.id === todo.project_id)) choices.push({id:todo.project_id,name:`${todo.project_name}（已删除）`});
    const preferred = todo.project_id ?? (Number($("#todo-project-filter").value) || "");
    openModal({title:todo.id ? "编辑待办" : "新增待办",description:"记录需要处理的事项，可关联项目并手动标记完成。",
      fields:`<label class="field full"><span>待办内容</span><textarea name="content" required maxlength="3000" rows="5">${esc(todo.content || "")}</textarea></label><label class="field"><span>关联项目</span><select name="project_id"><option value="">未关联项目</option>${choices.map(p => `<option value="${p.id}" ${p.id === preferred ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label><label class="field"><span>状态</span><select name="status"><option value="未完成">未完成</option><option value="已完成" ${todo.status === "已完成" ? "selected" : ""}>已完成</option></select></label>`,
      onSubmit:async form => {
        await withLoading(() => api.post("todos/save", {id:todo.id,content:form.get("content"),project_id:form.get("project_id") ? Number(form.get("project_id")) : null,status:form.get("status")}));
        closeModal(); toast("待办已保存"); await load();
      }});
  }
  $("#todo-search").addEventListener("input",render);
  for (const selector of ["#todo-project-filter", "#todo-status-filter"]) $(selector).addEventListener("change",render);
  $("#todo-reset").addEventListener("click",()=>{for(const id of ["#todo-search","#todo-project-filter","#todo-status-filter"]) $(id).value="";render();});
  $("#todo-list").addEventListener("click",async event => {
    const button = event.target.closest("button"); if (!button || button.disabled) return;
    const id = Number(button.dataset.todoToggle || button.dataset.todoEdit || button.dataset.todoDelete), todo = items.find(t => t.id === id);
    if (!todo) return;
    button.disabled = true;
    try {
      if (button.dataset.todoEdit) await open(todo);
      else if (button.dataset.todoToggle) {
        await withLoading(()=>api.post("todos/save",{id,status:todo.status === "已完成" ? "未完成" : "已完成"})); await load(); toast("待办状态已更新");
      } else if (await confirmAction("确认删除这条待办记录？")) {
        await withLoading(()=>api.post("todos/delete",{id})); await load(); toast("待办已删除");
      }
    } catch (error) { toast(error.message,true); }
    finally { button.disabled = false; }
  });
  return {load, open};
}
