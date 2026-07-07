"""
OpenRouter Setup — Ronsi95 AI OS
Wires OPENROUTER_API_KEY into Hermes config and tests connectivity.
Run from Claude-openclaw root: python setup_openrouter.py
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

# Best free models from Ronnie's guardrail (confirmed IDs):
PRIMARY_MODEL   = "qwen/qwen3-30b-a3b:free"          # Qwen3 Next 80B A3B Instruct
FALLBACK_MODEL  = "meta-llama/llama-3.3-70b-instruct:free"
BASE_URL        = "https://openrouter.ai/api/v1"

def load_env(base: Path) -> dict:
    env = {}
    for p in [base / ".env", base.parent / ".env"]:
        if p.exists():
            for line in p.read_text(errors="ignore").splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
            break
    return env

def append_env(path: Path, updates: dict):
    existing = {}
    if path.exists():
        for line in path.read_text(errors="ignore").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
    existing.update(updates)
    path.write_text("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n")

def test_openrouter(api_key: str) -> bool:
    try:
        req = urllib.request.Request(
            f"{BASE_URL}/models",
            headers={"Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            count = len(data.get("data", []))
            print(f"  ✅ OpenRouter connected — {count} models available")
            # Find free ones
            free = [m["id"] for m in data.get("data", []) if ":free" in m.get("id","")]
            if free:
                print(f"  Free models available: {len(free)}")
            return True
    except Exception as e:
        print(f"  ❌ Connection failed: {e}")
        return False

def update_hermes_config(api_key: str):
    """Point Hermes config at OpenRouter instead of Ollama."""
    import subprocess, sys, os
    localappdata = os.environ.get("LOCALAPPDATA", "")
    hermes_config = Path(localappdata) / "hermes" / "config.yaml"
    hermes_env    = Path(localappdata) / "hermes" / ".env"

    if hermes_config.exists():
        content = hermes_config.read_text(errors="ignore")
        # Check if already updated
        if "openrouter" in content.lower():
            print(f"  Hermes config already has OpenRouter — skipping")
        else:
            print(f"  Updating {hermes_config}")
            # Backup
            hermes_config.with_suffix(".yaml.bak").write_text(content)
            # Replace model/provider lines
            lines = []
            for line in content.splitlines():
                ll = line.lower()
                if "ollama" in ll or ("model:" in ll and "hermes" in ll):
                    lines.append(f"  # {line}  # replaced by OpenRouter")
                else:
                    lines.append(line)
            hermes_config.write_text("\n".join(lines) + "\n")
            print(f"  ✅ Hermes config updated (backup at .yaml.bak)")
    else:
        print(f"  ℹ️  Hermes config not found at {hermes_config} — skipping")

    # Write hermes .env
    if hermes_env.parent.exists():
        append_env(hermes_env, {
            "OPENROUTER_API_KEY": api_key,
            "OPENAI_API_KEY":     api_key,   # Some hermes builds use OPENAI_* keys
            "OPENAI_BASE_URL":    BASE_URL,
            "OPENAI_MODEL":       PRIMARY_MODEL,
        })
        print(f"  ✅ Hermes .env updated (key written)")
    else:
        print(f"  ℹ️  Hermes dir not found at {hermes_env.parent}")

def main():
    print("\n" + "="*55)
    print("  Ronsi95 AI OS — OpenRouter Setup")
    print("="*55 + "\n")

    base = Path(__file__).parent
    env  = load_env(base)

    # 1. Check key
    api_key = env.get("OPENROUTER_API_KEY", "").strip().strip('"').strip("'")
    if not api_key:
        print("  ❌ OPENROUTER_API_KEY not found in .env")
        print("  Open .env and add:  OPENROUTER_API_KEY=sk-or-...")
        input("  Press Enter to exit...")
        sys.exit(1)
    print(f"[1/4] OPENROUTER_API_KEY — present ✓")

    # 2. Test connectivity
    print(f"\n[2/4] Testing OpenRouter connectivity...")
    ok = test_openrouter(api_key)
    if not ok:
        print("  Check your key or internet connection.")

    # 3. Append vars to project .env
    print(f"\n[3/4] Updating project .env with OpenRouter settings...")
    project_env = base / ".env"
    append_env(project_env, {
        "OPENROUTER_BASE_URL":      BASE_URL,
        "OPENROUTER_MODEL":         PRIMARY_MODEL,
        "OPENROUTER_FALLBACK_MODEL": FALLBACK_MODEL,
        # OpenAI-compat vars so anything using OPENAI_* works too
        "OPENAI_BASE_URL":          BASE_URL,
        "OPENAI_API_KEY":           api_key,
        "OPENAI_MODEL":             PRIMARY_MODEL,
    })
    print(f"  ✅ Written: {project_env}")
    print(f"     PRIMARY:  {PRIMARY_MODEL}")
    print(f"     FALLBACK: {FALLBACK_MODEL}")

    # 4. Update Hermes config + hermes .env
    print(f"\n[4/4] Updating Hermes config...")
    update_hermes_config(api_key)

    print("\n" + "="*55)
    print("  Setup complete! ✅")
    print("  Hermes now routes through OpenRouter (free tier)")
    print("  Restart Hermes gateway to apply: hermes gateway restart")
    print("="*55 + "\n")
    input("Press Enter to close...")

if __name__ == "__main__":
    main()
