"""Write code from a description, and scaffold whole projects.

Two capabilities:

  * `generate` -- the model writes a single file (Python script, HTML page, SQL
    query, shell script...). Output is parsed from JSON so the filename, language
    and code are separated cleanly, then written to disk.
  * `scaffold` -- deterministic project templates ("layouts"). These are plain
    dictionaries of path -> content, so a small local model can't get the
    structure wrong; only the file *contents* it fills in can vary.

Everything is written under `beastt_workspace/` so generated code never mixes
with the assistant's own source.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .brain.base import Brain
from .paths import project_root

# Language -> file extension, for naming generated files sensibly.
EXTENSIONS = {
    "python": ".py", "py": ".py",
    "html": ".html", "css": ".css",
    "javascript": ".js", "js": ".js", "typescript": ".ts",
    "json": ".json", "yaml": ".yml", "yml": ".yml",
    "sql": ".sql", "bash": ".sh", "shell": ".sh", "powershell": ".ps1",
    "batch": ".bat", "java": ".java", "c": ".c", "cpp": ".cpp",
    "markdown": ".md", "text": ".txt",
}


def workspace() -> Path:
    path = project_root() / "beastt_workspace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_relpath(name: str, default: str) -> str:
    """Keep generated paths inside the workspace: no absolute or parent paths."""
    name = str(name or "").strip().replace("\\", "/")
    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
    parts = [re.sub(r"[^A-Za-z0-9._-]", "_", p) for p in parts]
    return "/".join(parts) if parts else default


# --- single-file generation -------------------------------------------------
_CODE_PROMPT = """Write code for this request: {request}

Return ONLY JSON in exactly this shape:
{{"filename": "suggested_name{ext}",
  "language": "{language}",
  "explanation": "one or two sentences on what it does and how to run it",
  "code": "the complete file contents"}}

Rules:
- Write the whole file, ready to run -- no placeholders or "..." omissions.
- Include brief comments for anything non-obvious.
- Use only the standard library unless the request needs otherwise; if third-party
  packages are required, name them in the explanation.
- The "code" value must be a single JSON string with real newlines escaped.
"""

_LANGUAGE_WORDS = {
    "python": "python", "py": "python", "script": "python",
    "html": "html", "webpage": "html", "web page": "html", "website": "html",
    "css": "css", "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "sql": "sql", "query": "sql",
    "bash": "bash", "shell script": "bash", "powershell": "powershell",
    "batch": "batch", "java": "java", "c++": "cpp", "cpp": "cpp",
    "markdown": "markdown", "json": "json", "yaml": "yaml",
}


def detect_language(text: str) -> str:
    lowered = text.lower()
    # Longest match first, so "shell script" beats "script".
    for word in sorted(_LANGUAGE_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return _LANGUAGE_WORDS[word]
    return "python"


def generate(brain: Brain, request: str, language: str = "") -> Optional[Dict]:
    """Ask the model for one file. Returns {path, language, explanation}."""
    from .docgen import _extract_json, ask_json

    language = language or detect_language(request)
    ext = EXTENSIONS.get(language, ".txt")
    prompt = _CODE_PROMPT.format(request=request.strip(), language=language, ext=ext)

    for attempt in range(2):
        try:
            raw = ask_json(brain, prompt, json_mode=(attempt == 0))
        except Exception as exc:
            print(f"[code] Model call failed: {exc.__class__.__name__}: {exc}")
            return None
        data = _extract_json(raw)
        code = (data or {}).get("code") if isinstance(data, dict) else None
        if isinstance(code, list):          # some models return lines
            code = "\n".join(str(line) for line in code)
        if code and str(code).strip():
            filename = _safe_relpath(data.get("filename") or "", f"generated{ext}")
            if not Path(filename).suffix:
                filename += ext
            target = workspace() / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(code), encoding="utf-8")
            return {
                "path": target,
                "language": language,
                "explanation": str(data.get("explanation") or "").strip()[:400],
                "lines": len(str(code).splitlines()),
            }
        print(f"[code] Attempt {attempt + 1}: no usable code in the reply.")
        prompt += "\n\nReturn ONLY the JSON object, with the full file in \"code\"."
    return None


# --- project scaffolds ("layouts") -----------------------------------------
def _py_cli(name: str) -> Dict[str, str]:
    return {
        f"{name}/__init__.py": '"""A small command-line application."""\n\n__version__ = "0.1.0"\n',
        f"{name}/cli.py": (
            '"""Command-line entry point."""\n\n'
            "import argparse\n\n\n"
            "def main(argv=None) -> int:\n"
            '    parser = argparse.ArgumentParser(description="%s")\n'
            '    parser.add_argument("name", nargs="?", default="world")\n'
            "    args = parser.parse_args(argv)\n"
            '    print(f"Hello, {args.name}!")\n'
            "    return 0\n\n\n"
            'if __name__ == "__main__":\n'
            "    raise SystemExit(main())\n" % name
        ),
        "main.py": f"from {name}.cli import main\n\nif __name__ == \"__main__\":\n    raise SystemExit(main())\n",
        "requirements.txt": "# add dependencies here\n",
        "README.md": f"# {name}\n\nA command-line application.\n\n```bash\npython main.py\n```\n",
        ".gitignore": "__pycache__/\n*.pyc\n.venv/\n",
    }


def _py_package(name: str) -> Dict[str, str]:
    return {
        f"{name}/__init__.py": f'"""{name} package."""\n\n__version__ = "0.1.0"\n',
        f"{name}/core.py": (
            '"""Core logic."""\n\n\n'
            "def add(a: float, b: float) -> float:\n"
            '    """Return the sum of two numbers."""\n'
            "    return a + b\n"
        ),
        f"tests/test_core.py": (
            f"from {name}.core import add\n\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        ),
        "pyproject.toml": (
            "[project]\n"
            f'name = "{name}"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.9"\n\n'
            "[build-system]\n"
            'requires = ["setuptools>=61"]\n'
            'build-backend = "setuptools.build_meta"\n'
        ),
        "README.md": f"# {name}\n\nA Python package.\n\n```bash\npip install -e .\npytest\n```\n",
        ".gitignore": "__pycache__/\n*.pyc\n.venv/\ndist/\nbuild/\n*.egg-info/\n",
    }


def _flask_app(name: str) -> Dict[str, str]:
    return {
        "app.py": (
            '"""A minimal Flask web application."""\n\n'
            "from flask import Flask, jsonify, render_template\n\n"
            "app = Flask(__name__)\n\n\n"
            '@app.route("/")\n'
            "def home():\n"
            '    return render_template("index.html", title="%s")\n\n\n'
            '@app.route("/api/health")\n'
            "def health():\n"
            '    return jsonify(status="ok")\n\n\n'
            'if __name__ == "__main__":\n'
            "    app.run(debug=True, port=5000)\n" % name
        ),
        "templates/index.html": (
            "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
            "  <meta charset=\"utf-8\">\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "  <title>{{ title }}</title>\n"
            "  <link rel=\"stylesheet\" href=\"/static/style.css\">\n"
            "</head>\n<body>\n  <main>\n    <h1>{{ title }}</h1>\n"
            "    <p>Your Flask app is running.</p>\n  </main>\n</body>\n</html>\n"
        ),
        "static/style.css": (
            ":root { --ink: #0b2545; --accent: #3da5d9; }\n"
            "body { font-family: system-ui, sans-serif; margin: 0; color: var(--ink); }\n"
            "main { max-width: 46rem; margin: 4rem auto; padding: 0 1.5rem; }\n"
            "h1 { border-bottom: 4px solid var(--accent); padding-bottom: .5rem; }\n"
        ),
        "requirements.txt": "flask>=3.0\n",
        "README.md": f"# {name}\n\nFlask app.\n\n```bash\npip install -r requirements.txt\npython app.py\n```\n",
        ".gitignore": "__pycache__/\n*.pyc\n.venv/\ninstance/\n",
    }


def _fastapi_app(name: str) -> Dict[str, str]:
    return {
        "main.py": (
            '"""A minimal FastAPI service."""\n\n'
            "from fastapi import FastAPI\n"
            "from pydantic import BaseModel\n\n"
            f'app = FastAPI(title="{name}")\n\n\n'
            "class Item(BaseModel):\n"
            "    name: str\n"
            "    quantity: int = 1\n\n\n"
            '@app.get("/health")\n'
            "def health():\n"
            '    return {"status": "ok"}\n\n\n'
            '@app.post("/items")\n'
            "def create_item(item: Item):\n"
            '    return {"created": item}\n'
        ),
        "requirements.txt": "fastapi>=0.110\nuvicorn>=0.29\n",
        "README.md": f"# {name}\n\nFastAPI service.\n\n```bash\npip install -r requirements.txt\nuvicorn main:app --reload\n```\n",
        ".gitignore": "__pycache__/\n*.pyc\n.venv/\n",
    }


def _static_site(name: str) -> Dict[str, str]:
    return {
        "index.html": (
            "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
            "  <meta charset=\"utf-8\">\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"  <title>{name}</title>\n"
            "  <link rel=\"stylesheet\" href=\"style.css\">\n"
            "</head>\n<body>\n"
            "  <header><h1>%s</h1><p class=\"tagline\">Built with a little help.</p></header>\n"
            "  <main>\n    <section>\n      <h2>About</h2>\n"
            "      <p>Replace this with your content.</p>\n    </section>\n  </main>\n"
            "  <footer><p>&copy; <span id=\"year\"></span></p></footer>\n"
            "  <script src=\"script.js\"></script>\n</body>\n</html>\n" % name
        ),
        "style.css": (
            ":root { --ink: #0b2545; --accent: #3da5d9; --paper: #ffffff; }\n"
            "* { box-sizing: border-box; }\n"
            "body { margin: 0; font-family: system-ui, -apple-system, sans-serif;\n"
            "       color: var(--ink); background: var(--paper); line-height: 1.6; }\n"
            "header { background: var(--ink); color: #fff; padding: 4rem 1.5rem; }\n"
            "header h1 { margin: 0 0 .25rem; font-size: 2.5rem; }\n"
            ".tagline { margin: 0; color: var(--accent); }\n"
            "main { max-width: 46rem; margin: 3rem auto; padding: 0 1.5rem; }\n"
            "h2 { border-bottom: 3px solid var(--accent); display: inline-block; }\n"
            "footer { border-top: 1px solid #e5e7eb; padding: 2rem 1.5rem; text-align: center; }\n"
        ),
        "script.js": (
            "// Fill in the current year in the footer.\n"
            "document.getElementById('year').textContent = new Date().getFullYear();\n"
        ),
        "README.md": f"# {name}\n\nA static site. Open `index.html`, or serve it:\n\n```bash\npython -m http.server\n```\n",
    }


SCAFFOLDS = {
    "python-cli": ("A command-line Python app (argparse, entry point)", _py_cli),
    "python-package": ("An installable Python package with a test", _py_package),
    "flask": ("A Flask web app with a template and stylesheet", _flask_app),
    "fastapi": ("A FastAPI service with a typed model", _fastapi_app),
    "static-site": ("A static website: HTML, CSS and JavaScript", _static_site),
}


def list_scaffolds() -> str:
    return "\n".join(f"  - {key}: {desc}" for key, (desc, _) in SCAFFOLDS.items())


def scaffold(kind: str, project_name: str) -> Tuple[Optional[Path], List[str]]:
    """Create a project from a template. Returns (directory, files written)."""
    entry = SCAFFOLDS.get(kind)
    if entry is None:
        return None, []
    _desc, builder = entry

    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", project_name.strip()) or "project"
    module_name = re.sub(r"[^a-z0-9_]", "_", safe_name.lower()).strip("_") or "app"

    root = workspace() / safe_name
    written: List[str] = []
    for relative, content in builder(module_name).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(relative)

    # Packages need their directories importable.
    for path in root.rglob("*"):
        if path.is_dir() and not (path / "__init__.py").exists():
            if path.name in ("tests",):
                (path / "__init__.py").write_text("", encoding="utf-8")
    return root, sorted(written)
