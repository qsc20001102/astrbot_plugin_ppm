from contextlib import closing
"""Offline migration and consistent SQLite backups."""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .database import Database
from .settings import Settings

SCHEMA_VERSION = 5

def inspect_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as conn, conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("数据库完整性检查失败")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"projects", "members", "work_logs"} <= tables:
            raise ValueError("不是支持的 PPM 数据库")
        if version > SCHEMA_VERSION:
            raise ValueError("数据库版本高于程序版本，请升级程序，禁止降级写入")
        return version

def snapshot(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)) as src, src:
        with closing(sqlite3.connect(destination)) as dst, dst:
            src.backup(dst)
    return destination

def backup(path):
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return snapshot(path, path.parent / "backups" / f"ppm-{stamp}.sqlite3")

def import_database(source, data_dir):
    """Only into an empty destination. Stop all writers before calling."""
    source, data_dir = Path(source), Path(data_dir)
    inspect_database(source)
    target = data_dir / "ppm.sqlite3"
    if target.exists():
        raise ValueError("目标已有数据库，请使用新的空数据目录，避免覆盖")
    data_dir.mkdir(parents=True, exist_ok=True)
    staging = data_dir / "importing.sqlite3"
    if staging.exists():
        raise ValueError("存在未完成的迁移文件 importing.sqlite3，请先检查")
    snapshot(source, staging)
    try:
        backup(staging)
        Database(staging).initialize()
        inspect_database(staging)
        with closing(sqlite3.connect(staging)) as conn, conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.replace(staging, target)
    except Exception:
        if staging.exists():
            staging.unlink()
        raise
    return target

def prepare_data(data_dir):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "ppm.sqlite3"
    if not path.exists():
        candidates = [data_dir / "plugin_data/astrbot_plugin_ppm/ppm.sqlite3",
                      data_dir / "data/plugin_data/astrbot_plugin_ppm/ppm.sqlite3"]
        found = [p for p in candidates if p.exists()]
        if len(found) > 1:
            raise ValueError("发现多个旧库，请使用迁移命令指定来源")
        if found:
            import_database(found[0], data_dir)
    if path.exists() and inspect_database(path) < SCHEMA_VERSION:
        backup(path)
    database = Database(path)
    database.initialize()
    return database

def main():
    parser = argparse.ArgumentParser(description="PPM 离线迁移/备份；导入前请停止 PPM")
    parser.add_argument("action", choices=["import", "backup", "import-settings"])
    parser.add_argument("source", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("PPM_DATA_DIR", "data")))
    args = parser.parse_args()
    if args.action == "import":
        print(import_database(args.source, args.data_dir))
    elif args.action == "backup":
        inspect_database(args.source)
        print(backup(args.source))
    else:
        settings = Settings(args.data_dir / "settings.json")
        legacy = json.loads(args.source.read_text(encoding="utf-8"))
        settings.save({k: legacy[k] for k in ("ui_color_theme", "week_start", "ai_summary_prompt") if k in legacy})
        print("显示设置及提示词已迁移；请在网页重新配置模型地址、名称和密钥")

if __name__ == "__main__":
    main()
