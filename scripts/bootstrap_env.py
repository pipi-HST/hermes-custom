"""Bootstrap local .env and config.yaml for one-command Hermes Knowledge startup.

Reads `<repo>/.env`, ensures `HERMES_API_TOKEN` has a strong local random value,
and syncs `<repo>/config.yaml` without ever printing secret values to stdout.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
CONFIG_PATH = ROOT / "config.yaml"
CONFIG_EXAMPLE_PATH = ROOT / "config.example.yaml"


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _set_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    replaced = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k == key:
                new_lines.append(f"{key}={value}")
                replaced = True
                continue
        new_lines.append(line)
    if not replaced:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _is_placeholder(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return True
    upper = v.upper()
    return (
        upper.startswith("REPLACE_WITH")
        or upper.startswith("YOUR_")
        or "把这里换成" in v
        or "换成你" in v
    )


def main() -> int:
    if not ENV_PATH.exists():
        if ENV_EXAMPLE_PATH.exists():
            ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            print("[Hermes] 已为你自动创建 .env 配置文件。")
            print("[Hermes] 请先用编辑器打开 .env，填入 DEEPSEEK_API_KEY 后重新运行启动脚本。")
            return 2
        print("[Hermes] 找不到 .env 或 .env.example，请检查仓库文件是否完整。")
        return 2

    env = _parse_env(ENV_PATH)

    api_key = env.get("DEEPSEEK_API_KEY") or env.get("OPENAI_API_KEY") or ""
    if _is_placeholder(api_key):
        print("[Hermes] 检测到 .env 中尚未填写 DEEPSEEK_API_KEY。")
        print("[Hermes] 请打开根目录下的 .env，填入你的 DeepSeek API Key 后重新运行启动脚本。")
        return 2

    token = env.get("HERMES_API_TOKEN", "").strip()
    if _is_placeholder(token):
        token = secrets.token_urlsafe(32)
        _set_env_value(ENV_PATH, "HERMES_API_TOKEN", token)
        print("[Hermes] 已自动生成本地访问口令 HERMES_API_TOKEN 并写入 .env。")

    dashscope_key = env.get("DASHSCOPE_API_KEY", "").strip()
    if _is_placeholder(dashscope_key):
        dashscope_key = ""

    model = env.get("LLM_MODEL", "").strip() or "deepseek-v4-pro"
    base_url = env.get("LLM_BASE_URL", "").strip() or "https://api.deepseek.com"
    vault_path = env.get("VAULT_PATH", "").strip() or "./data/vault"

    base_cfg: dict = {}
    if CONFIG_PATH.exists():
        base_cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    elif CONFIG_EXAMPLE_PATH.exists():
        base_cfg = yaml.safe_load(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8")) or {}

    base_cfg.setdefault("llm", {})
    base_cfg["llm"]["provider"] = base_cfg["llm"].get("provider") or "openai"
    base_cfg["llm"]["model"] = model
    base_cfg["llm"]["base_url"] = base_url
    base_cfg["llm"]["api_key"] = api_key

    base_cfg.setdefault("qwen", {})
    base_cfg["qwen"]["api_key"] = dashscope_key

    base_cfg.setdefault("asr", {})
    base_cfg["asr"]["provider"] = base_cfg["asr"].get("provider") or "dashscope"
    base_cfg["asr"]["api_key"] = dashscope_key

    base_cfg.setdefault("database", {})
    base_cfg["database"]["path"] = base_cfg["database"].get("path") or "./data/captures.db"

    base_cfg.setdefault("vault", {})
    base_cfg["vault"]["path"] = vault_path

    CONFIG_PATH.write_text(
        yaml.safe_dump(base_cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print("[Hermes] 本地配置已就绪（.env -> config.yaml 已同步）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
