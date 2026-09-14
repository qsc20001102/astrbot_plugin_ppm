# astrbot-plugin-ppm

PPM 是一个运行在 AstrBot WebUI 内的项目进度管理插件，覆盖团队成员、公司考勤、项目状态与人员变动追溯、每日工作记录、跨项目进程表和 AI 日报。

## 功能

- 团队成员：新增、编辑、软删除成员，按准备、进行、维护、暂停、结束展示参与项目数量；可在居中窗口查看项目明细。
- 公司考勤：按中国法定工作日生成月历，依赖不可用或年份超出支持范围时退化为周一至周五；管理员可逐日覆盖上班/休班。
- 成员考勤：法定工作日默认正常出勤，只需登记加班、调休或特殊正常出勤；提供月度汇总和异常明细。
- 项目管理：支持 `准备 / 进行 / 维护 / 暂停 / 结束`，完整记录状态变化和成员加入/退出历史。
- 每日进展：按项目、日期记录工作内容，可在跨项目日期矩阵中查看。
- AI 日报：使用 AstrBot 已接入的聊天模型，默认合并全部项目，也可自选多个项目生成团队日报并保存结果。

## 安装与配置

1. 将仓库目录放到 AstrBot 的 `data/plugins/astrbot_plugin_ppm`。
2. 安装插件依赖（AstrBot WebUI 安装插件时会读取 `requirements.txt`）。
3. 在插件配置中选择“日报总结模型”，该选项来自 AstrBot 已配置的模型提供商。
4. 重载插件，在插件详情页打开 `ppm` Page。

数据库自动创建在：

```text
data/plugin_data/astrbot_plugin_ppm/ppm.sqlite3
```

更新或重装插件不会覆盖此目录。建议将该 SQLite 文件纳入日常备份。

## 目录结构

```text
main.py                    # AstrBot 生命周期和依赖装配
ppm/
  database.py              # SQLite schema、仓储和业务规则
  calendar_service.py      # 法定日历与工作日回退逻辑
  validation.py            # Web 输入验证
  web_api.py               # AstrBot Page API 适配层
pages/ppm/
  index.html               # 页面语义结构
  style.css                # 设计系统和响应式布局
  app.js                   # bridge、状态与交互
tests/test_database.py     # 核心业务回归测试
```

页面和业务服务之间只通过 JSON API 通信，后续 QQ/微信等平台消息处理可以复用 `Database` 服务，不需要依赖 WebUI 代码。

## 数据模型

核心表包括 `members`、`projects`、`project_status_history`、`project_memberships`、`work_logs`、`calendar_days`、`attendance_records` 与 `team_daily_summaries`。实体删除采用软删除；状态和成员变动历史支持人工修正，历史表不级联删除。

考勤约定：一个成员每天只有一个主状态。工作日无记录时为“正常”，参与项目时显示项目标识并可悬停查看项目名称；登记“加班”或“调休”后，该日按对应状态汇总。未来日期不显示成员状态，也不参与统计。

## 开发验证

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```

直接通过静态服务器打开 `pages/ppm/` 时，页面会使用仅存在于浏览器内存的演示数据，便于视觉开发；在 AstrBot iframe 中会自动切换到 Page bridge 和真实 SQLite 数据。

## 当前边界

- 本版本未注册聊天平台指令，但服务层已与 Page 解耦，后续可以直接添加 QQ 等消息入口。
- 法定节假日取决于 `chinesecalendar` 已收录年份；超出范围时按周末规则回退，并可在 WebUI 人工修正。
- 当前提供月度考勤 UI；后端汇总接口接受任意起止日期，可直接扩展为周汇总和年汇总视图。
