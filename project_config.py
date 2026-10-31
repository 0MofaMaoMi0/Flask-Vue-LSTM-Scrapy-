"""Project-local environment loading shared by Flask and Scrapy.

取值优先级（高 → 低）：

1. 进程环境变量——部署或临时运行 `set` 的真实变量，优先级最高，永不被文件覆盖；
2. ``..\\敏感信息调用\\*.env``——口令、Token、Cookie、Secret 的唯一存放处；
3. 项目内 ``.env``——只放主机、端口、库名等非敏感默认值。

该目录与 ``项目源码`` 平级，天然位于仓库之外，Git 既不会跟踪也无法误提交。
本模块**从不打印配置值**；``python project_config.py --check`` 只列变量名与来源文件。
"""

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"

# 敏感信息统一目录（仓库外）
SENSITIVE_DIR = PROJECT_ROOT.parent / "敏感信息调用"
SENSITIVE_PATTERN = "*.env"
# 模板文件即使放进该目录也不参与加载，避免占位值顶替真实值
SENSITIVE_EXCLUDE = ("example", "sample", "template")

_WRITTEN = {}  # 本模块写过的 name -> value，用于识别"值是否已被外部改动"
_SOURCES = {}  # name -> 来源文件，供 --check 展示
_SHADOWED = set()  # 文件里有、但被进程环境变量压住的变量名


def _parse_env_file(path):
    """逐行解析 KEY=VALUE，跳过空行与 # 注释；仅支持简单值写法。"""
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name.lower().startswith("export "):
            name = name[7:].strip()
        value = value.strip()
        if not name:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        yield name, value


def sensitive_files():
    """返回 ``敏感信息调用\\`` 下参与加载的 ``*.env`` 文件，按文件名排序。"""
    if not SENSITIVE_DIR.is_dir():
        return []
    files = []
    for path in sorted(SENSITIVE_DIR.glob(SENSITIVE_PATTERN)):
        if not path.is_file():
            continue
        if any(tag in path.name.lower() for tag in SENSITIVE_EXCLUDE):
            continue
        files.append(path)
    return files


def _apply(path, allow_override):
    """把文件里的键值并入 os.environ。

    allow_override=True 时允许覆盖"本模块自己写进去的值"（即让敏感目录压过 .env），
    但绝不覆盖进程环境变量，也不覆盖运行期被外部改过的值。
    """
    for name, value in _parse_env_file(path):
        current = os.environ.get(name)
        if current is not None and not (allow_override and _WRITTEN.get(name) == current):
            if name not in _WRITTEN:
                _SHADOWED.add(name)
            continue
        os.environ[name] = value
        _WRITTEN[name] = value
        _SOURCES[name] = str(path)


def load_project_env():
    """先加载 .env 打底，再用 敏感信息调用\\*.env 覆盖其中的同名项。"""
    if ENV_FILE.is_file():
        _apply(ENV_FILE, allow_override=False)
    for path in sensitive_files():
        _apply(path, allow_override=True)


def required(name):
    """返回必填配置；缺失时只报变量名，绝不回显值。"""
    load_project_env()
    value = os.getenv(name)
    if not value:
        raise RuntimeError("Missing required configuration: %s" % name)
    return value


def optional(name, default=None):
    """返回可选配置，缺失时回落到 default。"""
    load_project_env()
    return os.getenv(name, default)


def _rel(path):
    try:
        return os.path.relpath(str(path), str(PROJECT_ROOT))
    except ValueError:  # 跨盘符时无法相对化
        return str(path)


def check():
    """打印配置来源概览：只有变量名、来源文件与空/非空，没有值。"""
    load_project_env()

    files = sensitive_files()
    if SENSITIVE_DIR.is_dir():
        print("敏感信息目录: %s  [存在，加载 %d 个 *.env]" % (_rel(SENSITIVE_DIR), len(files)))
    else:
        print("敏感信息目录: %s  [不存在]" % _rel(SENSITIVE_DIR))
    print("项目 .env  : %s" % ("存在" if ENV_FILE.is_file() else "不存在"))

    if not _SOURCES:
        print("\n未从文件加载任何配置项。")
        return

    names = sorted(_SOURCES)
    width = max(len(name) for name in names)
    empty = []

    print("\n已加载 %d 项（仅列变量名与来源，不显示值）:" % len(names))
    for name in names:
        if os.environ.get(name):
            state = "已设置"
        else:
            state = "空值"
            empty.append(name)
        print("  %-*s  %-4s <- %s" % (width, name, state, _rel(_SOURCES[name])))

    print("\n合计 %d 项：%d 项有值，%d 项为空" % (len(names), len(names) - len(empty), len(empty)))
    if empty:
        print("空值项: %s" % ", ".join(empty))
    if _SHADOWED:
        print("另有 %d 项由进程环境变量提供，文件中的值未生效: %s"
              % (len(_SHADOWED), ", ".join(sorted(_SHADOWED))))


def main(argv):
    args = [a for a in argv[1:] if a.strip()]
    if not args or args == ["--check"]:
        check()
        return 0
    print("用法: python project_config.py [--check]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))