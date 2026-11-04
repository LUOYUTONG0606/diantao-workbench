"""Build a checked ZIP from committed public history, never the working directory."""
from __future__ import annotations

import argparse
import io
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_DIRS = {
    ".git", ".workbuddy", ".runtime", "__pycache__", ".pytest_cache",
    "screenshots", "logs", "ocr_debug", ".cb_vision_temp", "dist", "tests",
}


def distributable(name):
    path = PurePosixPath(name)
    if any(part in BLOCKED_DIRS or part.startswith(".venv") for part in path.parts):
        return False
    if path.name.startswith(".env"):
        return path.name == ".env.example"
    if path.suffix.lower() in {".log", ".pyc"} or path.name in {
        "task_log.json", "bailian_config.json", "AGENTS.md", ".clinerules",
        "diantao_docs_vector_utf8.txt",
    }:
        return False
    return len(path.parts) != 1 or path.suffix.lower() != ".md" or path.name == "README.md"


def private_values(paths):
    values = set()
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            value = value.strip().strip("'\"")
            if not separator or key.lstrip().startswith("#") or len(value) < 8:
                continue
            if "<" in value or "127.0.0.1" in value or "localhost" in value:
                continue
            values.add(value.encode("utf-8"))
            host = urlsplit(value).hostname if "://" in value else None
            if host and len(host) >= 8:
                values.add(host.encode("utf-8"))
    return values


def check_content(name, data, known_private=()):
    """Report only file/type; never echo sensitive content."""
    if any(value in data for value in known_private):
        raise ValueError(f"{name}: 包含本机私有配置值")
    text = data.decode("utf-8", errors="replace")
    patterns = {
        "疑似密钥": r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKID[A-Za-z0-9]{16,})",
        "私钥": r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
        "个人用户目录": r"[A-Za-z]:[\\/]+Users[\\/]+(?!<|Public\b|Default\b)[^\\/\s\"'<>]+",
        "URL 中的凭证": r"https?://[^/\s:@]+:[^/\s@]+@",
    }
    for label, pattern in patterns.items():
        if re.search(pattern, text):
            raise ValueError(f"{name}: {label}")
    for match in re.finditer(r"https?://(\d{1,3}(?:\.\d{1,3}){3})(?=[:/\s\"']|$)", text):
        address = ipaddress.ip_address(match[1])
        if address.is_private and not address.is_loopback:
            raise ValueError(f"{name}: 私有网络服务地址")


def build_release(root, version, output, known_private=()):
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("版本格式应为 v1.0.0 或 v1.0.0-rc.1")
    metadata_path = root / "VERSION.json"
    try:
        declared = json.loads(metadata_path.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("根 VERSION.json 缺失或无效") from error
    if declared != version:
        raise ValueError(f"发布版本 {version} 与 VERSION.json {declared} 不一致")
    def git(*args):
        return subprocess.check_output(["rtk", "proxy", "git", *args], cwd=root)
    # Ignored runtime files may exist; modifications to tracked publication files may not.
    if git("status", "--porcelain", "--untracked-files=normal").strip():
        raise ValueError("发布工作区须无未提交修改；请在公开发布分支提交后打包")
    commit = git("rev-parse", "HEAD").decode().strip()
    snapshot = git("archive", "--format=zip", "HEAD")
    files = {}
    with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not distributable(entry.filename):
                continue
            data = archive.read(entry)
            check_content(entry.filename, data, known_private)
            files[entry.filename] = data
    for required in ("启动大管家.cmd", "启动工作台.cmd", "启动旧版大管家.cmd",
                     "配置本机.cmd", "local_config.py", ".env.example", "VERSION.json",
                     "tools/launch_update_preview.py", "workbench_update_preview/app.py",
                     "workbench/app.py", "workbench/pyproject.toml", "workbench/uv.lock",
                     "养优惠.py", "tools/taobao_energy.py"):
        if required not in files:
            raise ValueError(f"发布包缺少必需文件：{required}")
    files["VERSION.json"] = json.dumps(
        {"version": version, "commit": commit}, ensure_ascii=False, indent=2,
    ).encode("utf-8")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"diantao-workbench-{version}.zip"
    # Check every file before creating the artifact; publish an atomic completed ZIP.
    temporary = destination.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(f"diantao-workbench-{version}/{name}", data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="例如 v0.1.0")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--private-config", action="append", type=Path, default=[],
                        help="额外检查的本机配置路径，配置内容不会输出或打包")
    args = parser.parse_args()
    import os
    config = Path(os.environ.get("DIANTAO_CONFIG_FILE") or
                  Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config") / "DianTao" / "config.env")
    try:
        secrets = private_values([ROOT / ".env", config, *args.private_config])
        print(build_release(ROOT, args.version, args.output, secrets))
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"发布检查失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
