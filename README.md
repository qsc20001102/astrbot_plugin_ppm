# PPM 独立项目进度管理

PPM 现在是独立 Web 应用，不再需要 AstrBot。Python 服务同时提供网页和 JSON API，所有业务数据存放于外挂目录的 SQLite 数据库。

## 保留与移除

保留项目管理、团队成员、公司日历、成员考勤、项目状态和人员历史、任务生命周期、工作记录、跨项目进程表、AI 每日总结及人工修订。沿用原有页面和业务规则，设置拆分为“模型配置”和“系统配置”两个独立页面。

已删除所有消息指令、AstrBot 启动和通信依赖，以及推送调度。新增纯记录用途的待办页面，支持关联项目（也可不关联）、编辑、删除、已完成／未完成切换、按项目和状态筛选及内容搜索。项目下的任务仍保留，与待办记录相互独立。项目详情提供“项目待办”快捷入口。旧归档待办在升级时恢复到 `project_todos` 表，原 `legacy_todos` 保留备查，不恢复任何推送设置。

## 绿联云部署（Intel / AMD，linux/amd64）

发布包 `dist/ppm-ugreen-1.2.2-amd64.zip` 内包含：

- `ppm-standalone-1.2.2-amd64.tar`：可导入的本地镜像，标签 `ppm-standalone:1.2.2`。
- `compose.yaml`：仅使用本地镜像，不拉取，不需要现场构建。
- `部署说明.txt`、`SHA256SUMS.txt`。

1. 解压并上传到 NAS 上单独的目录（例如你建立的 `docker/ppm` 文件夹）。无需 `.env` 文件。
2. 在绿联云 Docker 的本地镜像导入入口选择 `.tar` 文件，不要展开 tar 内部文件。命令行等价操作为 `docker load -i ppm-standalone-1.2.2-amd64.tar`。
3. 直接编辑 `compose.yaml`：`ports` 的 `8080:8080` 左侧是访问端口；`volumes` 的 `./data:/data` 左侧是 NAS 数据目录，可改为实际绝对路径，例如 `/volume1/docker/ppm/data:/data`。右侧 `/data` 保持不变。登录密码直接填写在 `PPM_PASSWORD` 中。
4. 从该目录创建 Compose 项目并导入 `compose.yaml`，所有配置都在这个文件中。
5. 启动后访问 `http://NAS的局域网IP:8080`。用户名 `admin`，密码见 `compose.yaml` 的 `PPM_PASSWORD`。进入模型配置填写模型配置并保存，再点击测试连接。

使用 SSH 时，在部署目录执行：

```sh
docker load -i ppm-standalone-1.2.2-amd64.tar
docker compose up -d
docker compose ps
docker compose logs --tail=100 ppm
```

健康检查使用 `/healthz`，无需登录。其余页面和 API 由 Compose 中的管理密码保护。跨站修改请求被拒绝。默认面向内网单管理员使用；如需外网访问，请通过 NAS 的 HTTPS 反向代理接入。

## 外挂数据与迁移

容器中的 `/data` 对应 Compose 的 `volumes` 左侧宿主机目录，内容包括：

```text
data/
  ppm.sqlite3            项目、任务、成员、考勤、记录和日报
  settings.json          模型设置、密钥、界面偏好
  backups/               升级前的完整 SQLite 快照
```

不要把数据保存在容器可写层。更换镜像时继续挂载同一目录，数据不随容器删除。`settings.json` 包含密钥，应与数据库一起妥善备份；登录后的模型配置页按要求直接显示已保存密钥；删除密钥内容并保存即可清除。系统配置和模型配置分别保存，不会覆盖另一页的设置。

### 从 AstrBot 迁入

1. **先停止旧 AstrBot 插件的写入**，复制 `data/plugin_data/astrbot_plugin_ppm` 整个数据目录。若存在 `ppm.sqlite3-wal` 和 `ppm.sqlite3-shm`，一并复制，不能只复制尚未检查点合并的主文件。
2. 在新服务首次启动前，将这些文件放到外挂目录根部，使目录中有 `ppm.sqlite3`。不要覆盖已有新项目的数据。
3. 启动 PPM。服务先检查库的完整性和版本，对旧版本自动创建备份，再升级至 schema v5。项目 ID、任务、历史、记录、考勤及日报保持不变。
4. 也可将原有 `plugin_data/astrbot_plugin_ppm/ppm.sqlite3` 或 `data/plugin_data/astrbot_plugin_ppm/ppm.sqlite3` 目录结构复制到外挂目录中。根部没有数据库时，程序会识别、复制并迁移；有多个候选旧库时拒绝猜测。
5. 在模型配置重新填写模型基础地址、模型名称及 API 密钥。AstrBot 的 provider ID 不是模型 ID，不能直接转换成独立服务凭据。

没有提供实际旧库时，迁移能力通过旧结构测试库验证；正式迁移仍应保留原始备份，核对项目及日报数量。

### 显式离线迁移

导入服务必须停止，目标目录必须没有 `ppm.sqlite3`。使用全新目录，导入不会覆盖已有业务库。

```sh
python -m ppm.storage import /旧目录/ppm.sqlite3 --data-dir /新数据目录
python -m ppm.storage import-settings /旧插件配置.json --data-dir /新数据目录
```

第二条可迁移原来的色调、每周起始日和日报提示词，不迁移推送配置或 AstrBot 的模型凭据。新服务已经配置模型后也可执行，但运行中的服务需重启才能读取离线变更。

在 NAS 上可用镜像中的相同工具，例如将原库放在部署目录的 `legacy` 子目录：

```sh
docker compose stop ppm
docker compose run --rm -v ./legacy:/legacy:ro ppm python -m ppm.storage import /legacy/ppm.sqlite3 --data-dir /data
docker compose up -d
```

此导入要求外挂目录里还没有数据库；若首次启动已生成空库，请将 `compose.yaml` 的 `volumes` 左侧路径改为新的空目录再导入。迁移完成前不会把临时库放到正式路径。高于 v5 的数据库会拒绝启动，防止旧程序写入新结构。

### 备份与恢复

运行中可使用 SQLite backup API 获取一致快照：

```sh
docker compose exec ppm python -m ppm.storage backup /data/ppm.sqlite3
```

备份写入 `/data/backups/`。同时另存 `settings.json`。恢复时停止容器，选用一个新的空外挂目录，把目标快照命名为 `ppm.sqlite3`，放入配置文件，再修改 `compose.yaml` 的数据挂载路径指向新目录启动。保留旧目录便于回退；旧插件回退应使用升级前备份。

## 大模型配置

支持 OpenAI 兼容的 **Chat Completions** 服务，包括实现该协议的云端服务和本地模型网关。基础地址按服务商文档填写（通常含 `/v1`），程序自动追加 `/chat/completions`。不能填写完整的 `/chat/completions` URL，也不要填写网页聊天地址。容器内 `localhost` 是容器自身，NAS 上另一个模型服务应填写可从容器访问的 NAS IP 和端口。

填写 API 基础地址和密钥后，点击“获取模型列表”，选择对应模型并点击“添加并使用”，即可保存连接配置并使用该模型。可重复添加多个模型，在“已添加的模型”中一键切换或移除；模型列表对应当前连接地址。地址、密钥和已添加模型在刷新后仍保留，密钥直接明文显示。免密服务可将密钥留空。列表仅获取时不会保存，服务商不支持列表接口时仍可手动输入模型名称后保存。

当前模型用于生成日报。列表可能包含非聊天模型，添加后可测试连接。日报提示词、生成超时（10～600 秒）位于模型配置；模型列表请求最长等待 30 秒。测试连接使用已保存配置并实际发起一条简短请求。界面色调、每周起始日位于系统配置，保存后立即生效。

schema v5 自动恢复结构完整的旧归档待办：旧状态为“已完成”的仍标记为已完成，其他记录默认为未完成；已删除记录仍不显示。此迁移只执行一次，后续删除待办不会因重启再次恢复。升级前自动备份完整数据库。


## 本地运行

Python 3.13：

```sh
python -m pip install -r requirements.txt
python main.py
```

默认访问 `http://127.0.0.1:8080`，默认数据目录为当前目录的 `data`。可通过 `PPM_DATA_DIR`、`PPM_PORT` 和 `PPM_PASSWORD` 环境变量调整。直接本地运行未设置密码时不启用登录；交付 Compose 已直接填写管理密码。

法定节假日使用 `chinesecalendar`；超出支持年份回退到周一至周五，并允许人工覆盖。容器时区固定为 `Asia/Shanghai`，以此确定业务日期。

## 构建与验证

有 Docker 的环境：

```sh
docker build --platform linux/amd64 -t ppm-standalone:1.2.2 .
docker save -o ppm-standalone-1.2.2-amd64.tar ppm-standalone:1.2.2
```

Windows 可运行 `scripts/build-image.ps1`。没有 Docker 时，`python scripts/build_archive.py` 从固定摘要的官方 Python Linux 镜像构造 Docker-load 压缩归档，下载 Linux Python 3.13 wheels，并校验所有基础层与应用层 SHA-256。随后运行 `python scripts/package_release.py` 生成带有未压缩 `.tar` 镜像、Compose 及随机密码的 ZIP 部署包。该方式不会运行目标 Linux 二进制，不能替代 Docker 实机测试。

```sh
python -m unittest discover -s tests -v
python -m compileall -q main.py ppm scripts tests
node --check pages/ppm/app.js
node --check pages/ppm/tasks.js
```

发布环境未安装 Docker/WSL，交付镜像已做归档和摘要校验，未在绿联云或 Linux Docker 引擎启动实测。源程序已通过独立 HTTP、数据库、迁移、模型模拟及浏览器交互测试。浏览器验证使用 1440×1000 桌面与 390×844 手机视口。

接口和镜像格式依据：[aiohttp 服务端文档](https://docs.aiohttp.org/en/v3.12.0/web.html)、[Docker 镜像导入文档](https://docs.docker.com/reference/cli/docker/image/load/)。

模型列表协议依据：[服务商模型列表接口](https://api-docs.deepseek.com/api/list-models/)，使用基础地址下的 `GET /models` 和 `data[].id`。

升级至 1.2.2：导入新镜像，将现有 Compose 的 image 改为 `ppm-standalone:1.2.2` 后重新创建容器。保留你当前的数据目录、端口和密码配置，无需手动迁移或清空数据库，数据库继续使用 schema v5；从旧版本迁入时自动备份并升级。

## 模型调用排查

1.2.0 保留服务商返回的结构化错误码、错误说明、请求 ID 和 Retry-After，并对密钥脱敏。HTTP 429 的 RPM（每分钟请求次数）和 TPM（每分钟 Token 吞吐量）限流分别提示；不会一概提示密钥或余额错误，也不会自动连续重试以放大限流。

能获取模型列表不等于当前推理请求可被接收。遇到 `RateLimitExceeded.EndpointRPMExceeded` 或 `RateLimitExceeded.EndpointTPMExceeded` 时，减少同密钥的并发调用、等待限流窗口恢复，或携带请求 ID 联系服务商核对限额。错误码不能单独确定限流是账户、模型入口还是共享资源池级别；切换流式或增加重试不保证解决。

错误码解释可参考[接口错误码文档](https://docs.volcengine.com/docs/ark/error-codes?lang=en)；实际限制以接入服务商的响应与控制台为准。


### 1.2.0 汇报核对与模型诊断

- 模型配置提供简短日报、详细日报、周报写作模板；填入后可修改并保存。原有自定义提示词保持不变。
- 生成窗口先选择项目、范围和模板，再点击“核对生成资料”。可查看模型、地址、写作要求、固定事实约束、完整工作资料及实际请求 JSON。核对阶段不调用模型，也不保存总结。
- 写作要求与事实约束通过 system 消息发送，工作资料单独通过 user 消息发送；不使用历史日报作为写作指令。自定义要求控制语气、结构和篇幅，事实约束防止虚构。
- 确认后生成；配置或资料变化会要求重新核对。首次日报生成后保存；已有日报默认只生成预览，明确选择覆盖才修改原日报。
- 周报范围为截止日期及前六天，按真实每日记录汇总，无需提前生成日报。当前提供预览和复制，不存入日报表，也不覆盖日报。可在任一日期的总结入口选择周报。
- “诊断模型列表”和“诊断文本生成”独立调用已保存的配置，展示耗时、HTTP 状态、错误码、请求 ID、Retry-After 和脱敏请求参数，可复制诊断。文本诊断不应用汇报提示词；诊断不会自动重试。
- 本版本提供诊断工具，未宣称已修复特定服务商的 HTTP 429。模型协议与异常分支以本地模拟服务验证，真实模型输出仍受模型能力影响。

### 1.2.1 任务生命周期与考勤展示

- 任务生命周期按每天实际生效的项目状态计算：进行期间每天计入，维护期间仅计入有当前任务工作记录的日期，暂停期间不计入。同一天多条记录仅算一天；准备、结束状态沿用原有规则。
- 甘特条按计入日期分段，任务详情与清单显示一致的累计天数。修改状态历史或任务关联记录后自动重新计算。
- 考勤提示以人工登记状态优先：休息日改为加班后显示当天参与项目，无项目则显示正常出勤；调休统一显示“休息”。
- 移除考勤说明输入框及悬停、明细中的说明展示。
- 从 1.2.0 升级无需变更数据库结构。保留现有数据挂载、端口及密码，仅替换镜像为 `ppm-standalone:1.2.1` 并重新创建容器。

### 1.2.2 网页图标与手机端适配

- 网页名称统一为“项目进度管理”，浏览器图标及导航标识更新为进度条与完成勾选组成的 SVG 图标。
- 手机导航改为侧边菜单，项目、成员和任务清单使用卡片展示；考勤及日期进程表保留局部横向滑动。
- 全部八个功能页面优化手机布局、按钮尺寸、筛选、长文本与表单；弹窗改为底部面板，减少固定高度与多层滚动。
- 71 项自动化测试通过；浏览器验证覆盖 320、390、640、768、1440 像素宽度及主要交互，未进行手机真机测试。
- 从 1.2.1 升级无需变更数据库结构。导入新镜像，将现有 Compose 的 image 改为 `ppm-standalone:1.2.2` 后重新创建容器，保留原数据目录、端口和密码。更新后刷新网页加载新界面。
