#!/usr/bin/env python3
"""构建门禁：镜像构建前的静态校验（纯标准库，可在 python:3.12-slim 容器内直接运行）。

校验项：
  1. app/ 与 main.py 下所有 .py 语法可解析，提前拦截语法错误；
  2. uv.lock 存在，且 pyproject.toml 声明的直接依赖均出现在锁文件中
     （镜像按 uv.lock 精确还原，两处不一致时镜像内会以 --frozen 构建失败）；
  3. PYTHON_IMAGE 与 .python-version 的主次版本一致（传入了该变量时才校验）；
  4. 前端工程完整：frontend/package.json 可解析、含 build 脚本与 vue/vite 依赖，
     frontend/index.html 与 vite.config.js 存在（镜像内 node 阶段据此构建）；
  5. static/ 目录存在（镜像运行期以 /home/static 托管前端）；
  6. env/.env.<env> 的数据库配置自洽：DATABASE_TYPE 合法，postgres 下 VECTOR_DIM 为正整数
     （向量列类型在导入期定型，配置写错只会在运行期报错）。

任一校验失败即退出码 1，由 devops/scripts/build.sh 终止发布。
"""

import ast
import json
import os
import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "app"
MAIN_FILE = PROJECT_ROOT / "main.py"
LOCK_FILE = PROJECT_ROOT / "uv.lock"
PYPROJECT_FILE = PROJECT_ROOT / "pyproject.toml"
PY_VERSION_FILE = PROJECT_ROOT / ".python-version"

FRONTEND_DIR = PROJECT_ROOT / "frontend"
PACKAGE_JSON = FRONTEND_DIR / "package.json"
INDEX_HTML = FRONTEND_DIR / "index.html"
VITE_CONFIG = FRONTEND_DIR / "vite.config.js"
STATIC_DIR = PROJECT_ROOT / "static"
ENV_DIR = PROJECT_ROOT / "env"

# 与 app/common/enums.py 的 DatabaseTypeEnum 保持一致
_DATABASE_TYPES = {"mysql", "postgres", "sqlite"}

_NAME_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _normalize(name: str) -> str:
    """按 PEP 503 归一化包名，使 yao_api / yao-api / yao.api 视为同一个包。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(spec: str) -> str | None:
    match = _NAME_PATTERN.match(spec.split(";")[0].strip())
    return _normalize(match.group(1)) if match else None


def check_syntax() -> tuple[list[str], int]:
    errors: list[str] = []
    targets = sorted(APP_DIR.glob("**/*.py"))
    targets.append(MAIN_FILE)
    checked = 0
    for path in targets:
        if not path.is_file():
            errors.append(f"{_rel(path)} 不存在")
            continue
        checked += 1
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{_rel(path)}:{exc.lineno} 语法错误：{exc.msg}")
        except (UnicodeDecodeError, ValueError) as exc:
            errors.append(f"{_rel(path)} 无法解析：{exc}")
    return errors, checked


def read_locked_names() -> tuple[set[str], list[str]]:
    """解析 uv.lock（TOML），取全部 [[package]] 名归一化后的集合。"""
    if not LOCK_FILE.is_file():
        return set(), [
            f"{_rel(LOCK_FILE)} 不存在（依赖锁文件必须入库：本地执行 uv lock 后提交）"
        ]

    try:
        data = tomllib.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, ValueError) as exc:
        return set(), [f"{_rel(LOCK_FILE)} 无法解析：{exc}"]

    names: set[str] = set()
    for package in data.get("package", []):
        name = package.get("name")
        if isinstance(name, str):
            names.add(_normalize(name))
    if not names:
        return names, [f"{_rel(LOCK_FILE)} 未解析到任何包"]
    return names, []


def read_declared_names() -> tuple[set[str], list[str]]:
    project = tomllib.loads(PYPROJECT_FILE.read_text(encoding="utf-8")).get("project", {})
    specs = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        specs.extend(group)

    names: set[str] = set()
    errors: list[str] = []
    for spec in specs:
        name = _requirement_name(spec)
        if name is None:
            errors.append(f"{_rel(PYPROJECT_FILE)} 无法解析依赖项：{spec}")
            continue
        names.add(name)
    return names, errors


def check_python_image() -> list[str]:
    image = os.environ.get("PYTHON_IMAGE", "").strip()
    if not image:
        return []
    wanted = PY_VERSION_FILE.read_text(encoding="utf-8").strip()
    tag = image.split(":", 1)[1] if ":" in image else image
    # astral/uv 组合镜像的 tag 以 "python" 开头（如 python3.12-bookworm-slim），剥掉再比对
    tag = tag.removeprefix("python")
    if not re.match(rf"^{re.escape(wanted)}([.\-@]|$)", tag):
        return [f"PYTHON_IMAGE={image} 与 {_rel(PY_VERSION_FILE)}={wanted} 的版本不一致"]
    return []


def check_frontend() -> list[str]:
    """前端工程完整性：镜像内 node 阶段按 package.json 的 build 脚本构建。"""
    errors: list[str] = []
    for path in (PACKAGE_JSON, INDEX_HTML, VITE_CONFIG):
        if not path.is_file():
            errors.append(f"{_rel(path)} 不存在（前端无法在镜像内构建）")
    if not STATIC_DIR.is_dir():
        errors.append(f"{_rel(STATIC_DIR)} 不存在（镜像运行期以 /home/static 托管前端）")

    if not PACKAGE_JSON.is_file():
        return errors

    try:
        package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append(f"{_rel(PACKAGE_JSON)} 无法解析：{exc}")
        return errors

    if "build" not in package.get("scripts", {}):
        errors.append(f"{_rel(PACKAGE_JSON)} 缺少 scripts.build（镜像内执行 npm run build）")

    deps = package.get("dependencies", {})
    dev_deps = package.get("devDependencies", {})
    if "vue" not in deps:
        errors.append(f"{_rel(PACKAGE_JSON)} dependencies 缺少 vue")
    if "vite" not in dev_deps:
        errors.append(f"{_rel(PACKAGE_JSON)} devDependencies 缺少 vite")

    for section, entries in (("dependencies", deps), ("devDependencies", dev_deps)):
        for name, spec in entries.items():
            # file:/link: 形式依赖在镜像内无对应路径，构建必失败，提前拦截
            if isinstance(spec, str) and spec.startswith(("file:", "link:")):
                errors.append(
                    f"{_rel(PACKAGE_JSON)} {section}.{name} 使用本地路径依赖（{spec}），"
                    "镜像内无法解析"
                )
    return errors


def _read_env_file(path: Path) -> dict[str, str]:
    """读取 KEY=value 形式的 env 文件（忽略注释与空行，键统一大写）。"""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip().strip('"').strip("'")
    return values


def check_env_files() -> list[str]:
    """多环境文件的数据库/向量配置自检（env/ 需入库，部署机也会原样挂载）。"""
    errors: list[str] = []
    env_files = sorted(ENV_DIR.glob(".env.*"))
    if not env_files:
        return [f"{_rel(ENV_DIR)} 下没有 .env.<env> 环境文件（至少需要 .env.dev）"]

    for path in env_files:
        values = _read_env_file(path)
        db_type = values.get("DATABASE_TYPE", "").lower()
        if db_type not in _DATABASE_TYPES:
            errors.append(
                f"{_rel(path)} DATABASE_TYPE={values.get('DATABASE_TYPE')!r} 非法"
                f"（可选：{'/'.join(sorted(_DATABASE_TYPES))}）"
            )
            continue
        if db_type != "postgres":
            errors.append(
                f"{_rel(path)} DATABASE_TYPE={db_type}：本工程向量存储依赖 PostgreSQL + pgvector"
            )
            continue
        dim = values.get("VECTOR_DIM", "")
        if not dim.isdigit() or int(dim) <= 0:
            errors.append(f"{_rel(path)} VECTOR_DIM={dim!r} 非法（postgres 下须为正整数，如 1024）")
    return errors


def main() -> int:
    errors: list[str] = []

    syntax_errors, checked = check_syntax()
    errors.extend(syntax_errors)

    locked, locked_errors = read_locked_names()
    declared, declared_errors = read_declared_names()
    errors.extend(locked_errors + declared_errors)

    missing = sorted(declared - locked)
    if missing:
        errors.append("uv.lock 缺少 pyproject.toml 声明的直接依赖：" + "、".join(missing))

    errors.extend(check_python_image())
    errors.extend(check_frontend())
    errors.extend(check_env_files())

    print(f"[门禁] 语法校验：{checked} 个 .py 文件")
    print(f"[门禁] 依赖校验：pyproject.toml 声明 {len(declared)} 项，uv.lock 锁定 {len(locked)} 项")
    print("[门禁] 前端校验：package.json / index.html / vite.config.js / static/")
    print("[门禁] 环境校验：env/.env.* 的 DATABASE_TYPE 与 VECTOR_DIM")

    if errors:
        print("\n校验未通过：", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("[门禁] 校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
