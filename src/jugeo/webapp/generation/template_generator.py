"""Generate Jinja2 templates and HTML components — stdlib only."""
from __future__ import annotations

import textwrap
from .models import TemplateSpec, FormSpec, FormFieldSpec, FormFieldType


class TemplateCodeGenerator:
    """Produces Jinja2 template strings for Flask apps."""

    def generate_template(self, spec: TemplateSpec) -> str:
        lines: list[str] = []
        if spec.extends:
            lines.append(f"{{% extends '{spec.extends}' %}}")
            lines.append("")
        for block_name, block_content in spec.blocks.items():
            lines.append(f"{{% block {block_name} %}}")
            lines.append(block_content)
            lines.append(f"{{% endblock %}}")
            lines.append("")
        if not spec.blocks:
            lines.append("{% block content %}")
            lines.append(f"<h1>{spec.name}</h1>")
            lines.append("{% endblock %}")
        return "\n".join(lines)

    def generate_base_template(self, app_name: str, nav_items: list | None = None) -> str:
        nav_items = nav_items or []
        nav_html = ""
        if nav_items:
            items = "\n        ".join(
                f'<li><a href="{item.get("url", "/")}">{item.get("label", "Link")}</a></li>'
                for item in nav_items
            )
            nav_html = textwrap.dedent(f"""\
    <nav class="navbar">
        <div class="navbar-brand"><a href="{{{{ url_for('index') }}}}">{app_name}</a></div>
        <ul class="navbar-nav">
        {items}
        </ul>
    </nav>""")
        return textwrap.dedent(f"""\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{% block title %}}{app_name}{{% endblock %}}</title>
    <link rel="stylesheet" href="{{{{ url_for('static', filename='css/base.css') }}}}">
    {{% block styles %}}{{% endblock %}}
</head>
<body>
    {{% block navbar %}}
    {nav_html}
    {{% endblock %}}
    <div class="flash-messages">
        {{% with messages = get_flashed_messages(with_categories=true) %}}
            {{% if messages %}}
                {{% for category, message in messages %}}
                    <div class="alert alert-{{{{ category }}}}">{{{{ message }}}}</div>
                {{% endfor %}}
            {{% endif %}}
        {{% endwith %}}
    </div>
    <main class="container">
        {{% block content %}}{{% endblock %}}
    </main>
    <footer class="footer">{{% block footer %}}{{% endblock %}}</footer>
    <script src="{{{{ url_for('static', filename='js/base.js') }}}}"></script>
    {{% block scripts %}}{{% endblock %}}
</body>
</html>""")

    def generate_list_template(self, model_name: str, columns: list) -> str:
        headers = "\n            ".join(f"<th>{c}</th>" for c in columns)
        cells = "\n                ".join(f"<td>{{{{ item.{c} }}}}</td>" for c in columns)
        return textwrap.dedent(f"""\
{{% extends 'base.html' %}}
{{% block content %}}
<h1>{model_name} List</h1>
<table class="table">
    <thead>
        <tr>
            {headers}
        </tr>
    </thead>
    <tbody>
        {{% for item in items %}}
        <tr>
                {cells}
        </tr>
        {{% endfor %}}
    </tbody>
</table>
{{% endblock %}}""")

    def generate_detail_template(self, model_name: str, fields: list) -> str:
        rows = "\n    ".join(
            f"<p><strong>{f}:</strong> {{{{ item.{f} }}}}</p>" for f in fields
        )
        return textwrap.dedent(f"""\
{{% extends 'base.html' %}}
{{% block content %}}
<h1>{model_name} Detail</h1>
<div class="detail-view">
    {rows}
</div>
{{% endblock %}}""")

    def generate_form_template(self, form: FormSpec) -> str:
        action = form.action_url or "#"
        method = form.method or "POST"
        fields_html = "\n        ".join(self._render_form_field(f) for f in form.fields)
        return textwrap.dedent(f"""\
{{% extends 'base.html' %}}
{{% block content %}}
<h1>{form.name}</h1>
<form action="{action}" method="{method}">
    <div class="form-group">
        {fields_html}
    </div>
    <button type="submit" class="btn btn-primary">Submit</button>
</form>
{{% endblock %}}""")

    def generate_dashboard_template(self, widgets: list | None = None) -> str:
        widgets = widgets or []
        cards = "\n    ".join(
            f'<div class="card"><h3>{w.get("title", "Widget")}</h3>'
            f'<p class="value">{w.get("value", "")}</p></div>'
            for w in widgets
        ) or "<p>No dashboard widgets configured.</p>"
        return textwrap.dedent(f"""\
{{% extends 'base.html' %}}
{{% block content %}}
<h1>Dashboard</h1>
<div class="dashboard-grid">
    {cards}
</div>
{{% endblock %}}""")

    def generate_error_template(self, code: int) -> str:
        messages = {
            400: "Bad Request",
            403: "Forbidden",
            404: "Page Not Found",
            500: "Internal Server Error",
        }
        msg = messages.get(code, "Error")
        return textwrap.dedent(f"""\
{{% extends 'base.html' %}}
{{% block title %}}{code} - {msg}{{% endblock %}}
{{% block content %}}
<div class="error-page">
    <h1>{code}</h1>
    <p>{msg}</p>
    <a href="{{{{ url_for('index') }}}}" class="btn">Go Home</a>
</div>
{{% endblock %}}""")

    def generate_login_template(self) -> str:
        return textwrap.dedent("""\
{% extends 'base.html' %}
{% block content %}
<h1>Login</h1>
<form action="{{ url_for('login') }}" method="POST">
    <div class="form-group">
        <label for="username">Username</label>
        <input type="text" id="username" name="username" required>
    </div>
    <div class="form-group">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" required>
    </div>
    <button type="submit" class="btn btn-primary">Login</button>
</form>
{% endblock %}""")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _render_form_field(self, field: FormFieldSpec) -> str:
        name = field.name if isinstance(field, FormFieldSpec) else str(field)
        label = field.label if isinstance(field, FormFieldSpec) else name.title()
        ftype = field.field_type if isinstance(field, FormFieldSpec) else FormFieldType.TEXT
        required = " required" if (isinstance(field, FormFieldSpec) and field.required) else ""

        if ftype == FormFieldType.TEXTAREA:
            return (
                f'<label for="{name}">{label}</label>\n'
                f'        <textarea id="{name}" name="{name}"{required}></textarea>'
            )
        elif ftype == FormFieldType.SELECT:
            choices = getattr(field, "choices", [])
            opts = "\n".join(f'<option value="{c}">{c}</option>' for c in choices)
            return (
                f'<label for="{name}">{label}</label>\n'
                f'        <select id="{name}" name="{name}"{required}>{opts}</select>'
            )
        elif ftype == FormFieldType.CHECKBOX:
            return (
                f'<label><input type="checkbox" name="{name}"{required}> {label}</label>'
            )
        else:
            itype = ftype.value if isinstance(ftype, FormFieldType) else "text"
            return (
                f'<label for="{name}">{label}</label>\n'
                f'        <input type="{itype}" id="{name}" name="{name}"{required}>'
            )


class ComponentLibrary:
    """Reusable HTML component generators."""

    def navbar(self, brand: str, items: list) -> str:
        nav_items = "\n    ".join(
            f'<li><a href="{i.get("url", "#")}">{i.get("label", "Link")}</a></li>'
            for i in items
        )
        return textwrap.dedent(f"""\
<nav class="navbar">
    <div class="navbar-brand">{brand}</div>
    <ul class="navbar-nav">
    {nav_items}
    </ul>
</nav>""")

    def card(self, title: str, body: str, footer: str = "") -> str:
        footer_html = f'\n    <div class="card-footer">{footer}</div>' if footer else ""
        return textwrap.dedent(f"""\
<div class="card">
    <div class="card-header"><h3>{title}</h3></div>
    <div class="card-body">{body}</div>{footer_html}
</div>""")

    def table(self, headers: list, rows: list) -> str:
        head = "".join(f"<th>{h}</th>" for h in headers)
        body_rows = "\n    ".join(
            "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
            for row in rows
        )
        return textwrap.dedent(f"""\
<table class="table">
    <thead><tr>{head}</tr></thead>
    <tbody>
    {body_rows}
    </tbody>
</table>""")

    def form_field(self, field: FormFieldSpec) -> str:
        name = field.name
        label = field.label
        ftype = field.field_type.value if isinstance(field.field_type, FormFieldType) else str(field.field_type)
        required = " required" if field.required else ""
        if ftype == "textarea":
            return (
                f'<div class="form-group">\n'
                f'  <label for="{name}">{label}</label>\n'
                f'  <textarea id="{name}" name="{name}"{required}></textarea>\n'
                f'</div>'
            )
        elif ftype == "select":
            opts = "\n".join(f'    <option value="{c}">{c}</option>' for c in field.choices)
            return (
                f'<div class="form-group">\n'
                f'  <label for="{name}">{label}</label>\n'
                f'  <select id="{name}" name="{name}"{required}>\n{opts}\n  </select>\n'
                f'</div>'
            )
        elif ftype == "checkbox":
            return (
                f'<div class="form-group">\n'
                f'  <label><input type="checkbox" id="{name}" name="{name}"{required}> {label}</label>\n'
                f'</div>'
            )
        else:
            return (
                f'<div class="form-group">\n'
                f'  <label for="{name}">{label}</label>\n'
                f'  <input type="{ftype}" id="{name}" name="{name}"{required}>\n'
                f'</div>'
            )

    def pagination(self, page: int, total_pages: int) -> str:
        links: list[str] = []
        if page > 1:
            links.append(f'<a href="?page={page - 1}" class="page-link">Previous</a>')
        for p in range(1, total_pages + 1):
            active = ' class="active"' if p == page else ""
            links.append(f'<a href="?page={p}"{active}>{p}</a>')
        if page < total_pages:
            links.append(f'<a href="?page={page + 1}" class="page-link">Next</a>')
        inner = "\n    ".join(links)
        return f'<nav class="pagination">\n    {inner}\n</nav>'

    def alert(self, message: str, kind: str = "info") -> str:
        return f'<div class="alert alert-{kind}">{message}</div>'

    def modal(self, id: str, title: str, body: str) -> str:
        return textwrap.dedent(f"""\
<div class="modal" id="{id}">
    <div class="modal-overlay"></div>
    <div class="modal-content">
        <div class="modal-header">
            <h3>{title}</h3>
            <button class="modal-close" data-dismiss="{id}">&times;</button>
        </div>
        <div class="modal-body">{body}</div>
    </div>
</div>""")
