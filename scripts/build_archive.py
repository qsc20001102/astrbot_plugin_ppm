"""Create a Docker-load archive without a daemon, using verified registry layers.

Windows fallback: cross-install Linux wheels; never execute target binaries.
The resulting archive still requires runtime verification on a Linux Docker host.
"""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
CACHE = DIST / "image-build"
BASE = "sha256:3e2de9c40ca4e3d73240059f9d48baff27908f10293e985a2f382a0378e6df4a"
TAG = "ppm-standalone:1.2.2"

def digest(path):
    with open(path, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()

def add_bytes(archive, name, content, mode=0o644):
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = mode
    archive.addfile(info, io.BytesIO(content))

def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    token = json.load(urllib.request.urlopen("https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull", timeout=30))["token"]
    def fetch(kind, sha):
        path = CACHE / sha.split(":")[1]
        if not path.exists() or digest(path) != sha.split(":")[1]:
            request = urllib.request.Request(f"https://registry-1.docker.io/v2/library/python/{kind}/{sha}", headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"})
            with urllib.request.urlopen(request, timeout=120) as response, open(path, "wb") as out:
                shutil.copyfileobj(response, out)
        if digest(path) != sha.split(":")[1]:
            raise ValueError("Registry digest mismatch")
        return path
    manifest = json.loads(fetch("manifests", BASE).read_bytes())
    config = json.loads(fetch("blobs", manifest["config"]["digest"]).read_bytes())
    layers = []
    for number, layer in enumerate(manifest["layers"]):
        print(f"Download and verify base layer {number + 1}", flush=True)
        compressed = fetch("blobs", layer["digest"])
        target = CACHE / f"base-{number}.tar"
        if not target.exists():
            with gzip.open(compressed, "rb") as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
        if "sha256:" + digest(target) != config["rootfs"]["diff_ids"][number]:
            raise ValueError("Uncompressed layer digest mismatch")
        layers.append(target)
    deps = CACHE / "deps"
    subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(deps), "--upgrade", "--no-compile",
        "--platform", "manylinux2014_x86_64", "--python-version", "3.13", "--implementation", "cp", "--abi", "cp313",
        "--only-binary=:all:", "-r", str(ROOT / "requirements.txt")], check=True)
    app_layer = CACHE / "app.tar"
    with tarfile.open(app_layer, "w", format=tarfile.PAX_FORMAT) as archive:
        for folder, prefix in [(ROOT / "ppm", "app/ppm"), (ROOT / "pages", "app/pages"), (deps, "opt/ppm-deps")]:
            for path in sorted(folder.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    add_bytes(archive, prefix + "/" + path.relative_to(folder).as_posix(), path.read_bytes())
        for name in ("main.py", "requirements.txt"):
            add_bytes(archive, "app/" + name, (ROOT / name).read_bytes())
    layers.append(app_layer)
    config["rootfs"]["diff_ids"].append("sha256:" + digest(app_layer))
    config.setdefault("history", []).append({"created_by": "PPM standalone verified archive assembly"})
    config["config"].update({"WorkingDir": "/app", "Cmd": ["python", "main.py"], "Entrypoint": None,
        "ExposedPorts": {"8080/tcp": {}}, "Volumes": {"/data": {}},
        "Healthcheck": {"Test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3)"], "Interval": 30000000000, "Timeout": 5000000000, "StartPeriod": 20000000000, "Retries": 3}})
    config["config"]["Env"] += ["PYTHONPATH=/opt/ppm-deps", "PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1", "PPM_DATA_DIR=/data", "TZ=Asia/Shanghai"]
    raw_config = json.dumps(config, separators=(",", ":")).encode()
    config_name = hashlib.sha256(raw_config).hexdigest() + ".json"
    layer_names = [digest(p) + "/layer.tar" for p in layers]
    output = DIST / "ppm-standalone-1.2.2-amd64.tar.gz"
    with tarfile.open(output, "w:gz") as archive:
        add_bytes(archive, config_name, raw_config)
        add_bytes(archive, "manifest.json", json.dumps([{"Config": config_name, "RepoTags": [TAG], "Layers": layer_names}]).encode())
        for path, name in zip(layers, layer_names):
            archive.add(path, arcname=name)
    with tarfile.open(output, "r:gz") as archive:
        saved = json.load(archive.extractfile("manifest.json"))[0]
        saved_config = json.load(archive.extractfile(saved["Config"]))
        assert saved_config["architecture"] == "amd64" and saved_config["os"] == "linux"
        for name, expected in zip(saved["Layers"], saved_config["rootfs"]["diff_ids"]):
            assert "sha256:" + hashlib.file_digest(archive.extractfile(name), "sha256").hexdigest() == expected
    (DIST / "SHA256SUMS.txt").write_text(digest(output) + "  " + output.name + "\n", encoding="utf-8")
    print(f"Verified Docker archive: {output} ({output.stat().st_size} bytes)", flush=True)

if __name__ == "__main__":
    main()
