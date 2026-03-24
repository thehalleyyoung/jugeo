"""App runner and syntax checking — stdlib only."""
from __future__ import annotations

import ast
import os
import py_compile
import textwrap


class AppRunner:
    """Validates generated Flask project files."""

    def validate_syntax(self, output_dir: str) -> list:
        errors: list[str] = []
        for root, _dirs, files in os.walk(output_dir):
            for fname in files:
                if fname.endswith(".py"):
                    path = os.path.join(root, fname)
                    try:
                        with open(path) as f:
                            source = f.read()
                        ast.parse(source, filename=path)
                    except SyntaxError as e:
                        errors.append(f"{path}: {e}")
        return errors

    def check_imports(self, output_dir: str) -> list:
        warnings: list[str] = []
        stdlib_safe = {
            "os", "sys", "json", "sqlite3", "datetime", "re", "hashlib",
            "secrets", "functools", "collections", "typing", "pathlib",
            "io", "csv", "math", "random", "time", "uuid",
        }
        third_party_ok = {"flask", "flask_sqlalchemy", "jinja2", "werkzeug"}
        for root, _dirs, files in os.walk(output_dir):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(root, fname)
                with open(path) as f:
                    for lineno, line in enumerate(f, 1):
                        stripped = line.strip()
                        if stripped.startswith("import ") or stripped.startswith("from "):
                            module = self._extract_module(stripped)
                            if module and module not in stdlib_safe and module not in third_party_ok:
                                warnings.append(f"{path}:{lineno}: unknown import '{module}'")
        return warnings

    def check_template_syntax(self, output_dir: str) -> list:
        errors: list[str] = []
        templates_dir = os.path.join(output_dir, "templates")
        if not os.path.isdir(templates_dir):
            return errors
        for fname in os.listdir(templates_dir):
            if not fname.endswith(".html"):
                continue
            path = os.path.join(templates_dir, fname)
            with open(path) as f:
                content = f.read()
            # Check balanced Jinja2 delimiters
            for open_d, close_d in [("{%", "%}"), ("{{", "}}"), ("{#", "#}")]:
                opens = content.count(open_d)
                closes = content.count(close_d)
                if opens != closes:
                    errors.append(
                        f"{path}: unbalanced {open_d}...{close_d} "
                        f"(open={opens}, close={closes})"
                    )
        return errors

    def generate_launch_script(self, output_dir: str, port: int) -> str:
        return textwrap.dedent(f"""\
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "Starting Flask app on port {port}..."
python main.py
""")

    @staticmethod
    def _extract_module(line: str) -> str:
        line = line.strip()
        if line.startswith("from "):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1].split(".")[0]
        elif line.startswith("import "):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1].split(".")[0].rstrip(",")
        return ""


class SyntaxChecker:
    """Lightweight syntax checkers for multiple languages."""

    def check_python(self, source: str) -> list:
        try:
            ast.parse(source)
            return []
        except SyntaxError as e:
            return [str(e)]

    def check_html(self, source: str) -> list:
        errors: list[str] = []
        # Basic tag balance check (very simplified)
        import re
        open_tags = re.findall(r"<(\w+)[\s>]", source)
        close_tags = re.findall(r"</(\w+)>", source)
        void_elements = {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }
        open_non_void = [t.lower() for t in open_tags if t.lower() not in void_elements]
        close_lower = [t.lower() for t in close_tags]
        # Simple count check per tag
        from collections import Counter
        open_counts = Counter(open_non_void)
        close_counts = Counter(close_lower)
        for tag in set(open_counts) | set(close_counts):
            diff = open_counts.get(tag, 0) - close_counts.get(tag, 0)
            if diff > 0:
                errors.append(f"Unclosed <{tag}> tag(s): {diff} unclosed")
            elif diff < 0:
                errors.append(f"Extra </{tag}> tag(s): {-diff} extra")
        return errors

    def check_css(self, source: str) -> list:
        errors: list[str] = []
        opens = source.count("{")
        closes = source.count("}")
        if opens != closes:
            errors.append(f"Unbalanced braces: {{ = {opens}, }} = {closes}")
        return errors

    def check_js(self, source: str) -> list:
        errors: list[str] = []
        for open_c, close_c, name in [("{", "}", "braces"), ("(", ")", "parens"), ("[", "]", "brackets")]:
            opens = source.count(open_c)
            closes = source.count(close_c)
            if opens != closes:
                errors.append(f"Unbalanced {name}: {open_c} = {opens}, {close_c} = {closes}")
        return errors
