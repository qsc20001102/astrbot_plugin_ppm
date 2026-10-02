"""Bundle the importable image, Compose file and private deployment settings."""
import gzip
import hashlib
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
bundle = DIST / "ppm-ugreen-1.2.2-amd64"
bundle.mkdir(parents=True, exist_ok=True)
image = bundle / "ppm-standalone-1.2.2-amd64.tar"
with gzip.open(DIST / (image.name + ".gz"), "rb") as source, image.open("wb") as output:
    shutil.copyfileobj(source, output)
shutil.copyfile(ROOT / "compose.yaml", bundle / "compose.yaml")
shutil.copyfile(ROOT / "README.md", bundle / "README.md")
(bundle / "部署说明.txt").write_text("""PPM 1.2.2 — 绿联云 Intel/AMD 部署包

1. 解压整个包到 NAS 的独立目录。
2. 在 Docker 镜像管理中从本地导入 ppm-standalone-1.2.2-amd64.tar。
3. 编辑 compose.yaml 中 ports、volumes、PPM_PASSWORD；默认数据保存在同目录 data/。
4. 在此目录创建 Compose 项目，使用 compose.yaml 启动。
5. 访问 http://NAS-IP:8080，用户名 admin，密码见 compose.yaml 中的 PPM_PASSWORD。
6. 在“模型配置”配置大模型并保存，再测试连接。

SSH 命令：
docker load -i ppm-standalone-1.2.2-amd64.tar
docker compose up -d
docker compose ps

从 1.2.0 / 1.2.1 升级：导入新镜像，将现有 Compose 的 image 改为 ppm-standalone:1.2.2，
保留现有数据挂载目录、端口及密码，然后重新创建容器。数据库结构无需变更。
本次更新：网页名称改为“项目进度管理”，更新进度图标；优化全部功能页的手机布局、导航、卡片、表单及弹窗。
包含 1.2.1 的任务生命周期与考勤展示修复。

迁移旧数据：先停止旧插件，在第一次启动前将旧数据目录完整复制到 data/，
包括 ppm.sqlite3 及存在的 -wal、-shm 文件。启动自动备份并升级。
不要覆盖已有新库。项目任务保留；原待办记录自动恢复，不恢复推送。模型配置与系统配置独立，密钥明文显示。
大模型连接凭据需重新填写。详细迁移、备份、恢复办法见 README.md。

验证状态：71 项自动化测试通过；浏览器已验证手机、平板和桌面宽度及主要交互；镜像层摘要已校验。
手机布局使用浏览器模拟验证，未做手机真机测试。
打包电脑没有 Docker/WSL，因此尚未完成 Linux 容器及绿联 NAS 实机启动测试。
没有配置真实模型凭据，模型协议使用模拟服务验证。
""", encoding="utf-8")
checks = []
for path in sorted(bundle.iterdir()):
    if path.is_file() and path.name != "SHA256SUMS.txt":
        with path.open("rb") as file:
            checks.append(hashlib.file_digest(file, "sha256").hexdigest() + "  " + path.name)
(bundle / "SHA256SUMS.txt").write_text("\n".join(checks) + "\n", encoding="utf-8")
output = DIST / "ppm-ugreen-1.2.2-amd64.zip"
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(bundle.iterdir()):
        archive.write(path, arcname=bundle.name + "/" + path.name)
with zipfile.ZipFile(output) as archive:
    assert archive.testzip() is None
print(f"Release bundle verified: {output} ({output.stat().st_size} bytes)")
