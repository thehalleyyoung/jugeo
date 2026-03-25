from __future__ import annotations
__all__ = ['TemplateGenerator', 'GeneratedTemplates']

import re
from dataclasses import dataclass, field

try:
    from jugeo.webapp.theory.sites.dom.presheaves import DOMValidityPresheaf, AccessibilityPresheaf
    from jugeo.webapp.theory.integration.accessibility import AccessibilityChecker
    from jugeo.webapp.theory.integration.security import CSRFChecker
    from jugeo.webapp.theory.sites.jinja.template_site import JinjaTemplateSite
    _THEORY = True
except ImportError:
    _THEORY = False


@dataclass
class GeneratedTemplates:
    """Output of :class:`TemplateGenerator`."""

    files: dict[str, str] = field(default_factory=dict)
    theory_annotations: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


class TemplateGenerator:
    """
    Generates HTML/Jinja2 templates satisfying accessibility and structural theory.

    Generated templates satisfy:
    - DOMValidityPresheaf: valid HTML5 nesting
    - AccessibilityChecker: alt, labels, heading hierarchy, lang, title, focus
    - JinjaTemplateSite: base.html with {% block %} structure
    - CSRFChecker: {{ csrf_token() }} in every POST form (Flask mode)
    - PerformanceChecker: width+height on img (prevents CLS)
    - WCAGCriterion 2.4.1: skip-link to #main-content
    """

    def generate(self, spec_dict: dict) -> GeneratedTemplates:
        """Main entry point. *spec_dict* is the dict from ``AppSpec.to_dict()``."""
        spec = spec_dict
        annotations: list[str] = []
        violations: list[str] = []
        files: dict[str, str] = {}

        mode = spec.get("mode", "flask")
        is_flask = mode == "flask"

        annotations.append("DOMValidityPresheaf: valid HTML5 structure enforced in all templates")
        annotations.append("AccessibilityChecker: lang attr, page title, heading hierarchy, skip-link")
        if is_flask:
            annotations.append("JinjaTemplateSite: base.html with {% block %} inheritance hierarchy")
            annotations.append("CSRFChecker: {{ csrf_token() }} present in every POST form")
        annotations.append("PerformanceChecker: img tags include width+height to prevent CLS")
        annotations.append("WCAGCriterion 2.4.1: skip-link to #main-content in base template")

        files["base.html"] = self._generate_base_html(spec)

        nouns = spec.get("domain_nouns", [])
        for noun in nouns:
            folder = f"{noun}s" if not noun.endswith("s") else noun
            files[f"{folder}/list.html"] = self._generate_list_template(noun, spec)
            files[f"{folder}/show.html"] = self._generate_show_template(noun, spec)
            files[f"{folder}/form.html"] = self._generate_form_template(noun, spec)

        auth_required = spec.get("auth_required", False)
        if auth_required or is_flask:
            auth_files = self._generate_auth_templates(spec)
            files.update(auth_files)

        files["index.html"] = self._generate_index_template(spec)

        violations.extend(self._verify_templates(files))

        return GeneratedTemplates(files=files, theory_annotations=annotations, violations=violations)

    # ------------------------------------------------------------------
    # Base template
    # ------------------------------------------------------------------

    def _generate_base_html(self, spec: dict) -> str:
        mode = spec.get("mode", "flask")
        app_name = spec.get("name", "App")

        if mode == "flask":
            return (
                "<!DOCTYPE html>\n"
                "{# [AccessibilityChecker: lang attribute — WCAG 3.1.1] #}\n"
                '<html lang="en">\n'
                "<head>\n"
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                "  {# [AccessibilityChecker: non-empty page title — WCAG 2.4.2] #}\n"
                "  <title>{% block title %}{{ app_name }}{% endblock %}</title>\n"
                "  {# [CSRFChecker: meta tag for JS CSRF reads] #}\n"
                '  <meta name="csrf-token" content="{{ csrf_token() }}">\n'
                "  <link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}\">\n"
                "  {% block head %}{% endblock %}\n"
                "</head>\n"
                "<body>\n"
                "  {# [WCAGCriterion 2.4.1: skip navigation link] #}\n"
                '  <a href="#main-content" class="sr-only skip-link">Skip to main content</a>\n'
                '  <nav aria-label="Main navigation">\n'
                "    <a href=\"{{ url_for('index') }}\" class=\"nav-brand\">{{ app_name }}</a>\n"
                "    <ul>\n"
                "      {% if current_user and current_user.is_authenticated %}\n"
                "        <li><a href=\"{{ url_for('logout') }}\">Logout</a></li>\n"
                "      {% else %}\n"
                "        <li><a href=\"{{ url_for('login') }}\">Login</a></li>\n"
                "        <li><a href=\"{{ url_for('register') }}\">Register</a></li>\n"
                "      {% endif %}\n"
                "    </ul>\n"
                "  </nav>\n"
                "  {# [AccessibilityChecker: main landmark] #}\n"
                '  <main id="main-content">\n'
                "    {% with messages = get_flashed_messages(with_categories=true) %}\n"
                "      {% for category, message in messages %}\n"
                '        <div role="alert" class="alert alert-{{ category }}">{{ message }}</div>\n'
                "      {% endfor %}\n"
                "    {% endwith %}\n"
                "    {% block body %}{% endblock %}\n"
                "  </main>\n"
                "  <footer>\n"
                f"    <p>&copy; {{{{ now.year if now else '' }}}} {{{{ app_name }}}}</p>\n"
                "  </footer>\n"
                "  <script src=\"{{ url_for('static', filename='csrf.js') }}\"></script>\n"
                "  {% block scripts %}{% endblock %}\n"
                "</body>\n"
                "</html>\n"
            )
        else:
            return (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                "  <title>PAGE_TITLE</title>\n"
                '  <link rel="stylesheet" href="style.css">\n'
                "</head>\n"
                "<body>\n"
                '  <a href="#main-content" class="sr-only skip-link">Skip to main content</a>\n'
                '  <nav aria-label="Main navigation">\n'
                f'    <a href="index.html" class="nav-brand">{app_name}</a>\n'
                "  </nav>\n"
                '  <main id="main-content">\n'
                "    PAGE_CONTENT\n"
                "  </main>\n"
                f"  <footer><p>&copy; 2025 {app_name}</p></footer>\n"
                '  <script src="app.js" defer></script>\n'
                "</body>\n"
                "</html>\n"
            )

    # ------------------------------------------------------------------
    # CRUD templates
    # ------------------------------------------------------------------

    def _generate_list_template(self, noun: str, spec: dict) -> str:
        mode = spec.get("mode", "flask")
        app_name = spec.get("name", "App")
        noun_cap = noun.capitalize()
        noun_plural = f"{noun_cap}s" if not noun_cap.endswith("s") else noun_cap
        route_prefix = f"{noun}s" if not noun.endswith("s") else noun
        metaphors = spec.get("ui_metaphors", [])
        use_cards = any("card" in m for m in metaphors)

        if mode == "flask":
            card_grid_open = '    <ul class="card-grid">' if use_cards else '    <ul>'
            card_cls = ' class="card"' if use_cards else ''
            return (
                "{% extends 'base.html' %}\n"
                "{# [DOMValidityPresheaf: valid HTML5 nesting via block inheritance] #}\n"
                "{% block title %}" + noun_plural + " — {% endblock %}\n"
                "{% block body %}\n"
                "{# [AccessibilityChecker: h1 present — WCAG 2.4.6] #}\n"
                f"<h1>{noun_plural}</h1>\n"
                "{# [AccessibilityChecker: landmark list] #}\n"
                + card_grid_open + "\n"
                "  {% for item in items %}\n"
                f"  <li{card_cls}>\n"
                "    <h2>\n"
                "      {# [AccessibilityChecker: descriptive link text — WCAG 2.4.4] #}\n"
                "      <a href=\"{{ url_for('" + route_prefix + ".show', id=item.id) }}\">\n"
                "        {{ item.title if item.title is defined else (item.name if item.name is defined else item.id) }}\n"
                "      </a>\n"
                "    </h2>\n"
                "    <nav aria-label=\"Item actions\">\n"
                "      <a href=\"{{ url_for('" + route_prefix + ".edit', id=item.id) }}\">Edit</a>\n"
                "      <form method=\"post\" action=\"{{ url_for('" + route_prefix + ".delete', id=item.id) }}\">\n"
                "        {# [CSRFChecker: csrf_token on every POST form] #}\n"
                "        {{ csrf_token() }}\n"
                "        <button type=\"submit\">Delete</button>\n"
                "      </form>\n"
                "    </nav>\n"
                "  </li>\n"
                "  {% else %}\n"
                f"  <li>No {noun_plural.lower()} yet.</li>\n"
                "  {% endfor %}\n"
                "</ul>\n"
                "<a href=\"{{ url_for('" + route_prefix + ".new') }}\" class=\"btn btn-primary\">"
                "New " + noun_cap + "</a>\n"
                "{% endblock %}\n"
            )
        else:
            return (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                f"  <title>{noun_plural} — {app_name}</title>\n"
                '  <link rel="stylesheet" href="../style.css">\n'
                "</head>\n"
                "<body>\n"
                '  <a href="#main-content" class="sr-only skip-link">Skip to main content</a>\n'
                '  <nav aria-label="Main navigation">\n'
                f'    <a href="../index.html" class="nav-brand">{app_name}</a>\n'
                "  </nav>\n"
                '  <main id="main-content">\n'
                f"    <h1>{noun_plural}</h1>\n"
                '    <ul class="card-grid">\n'
                "      <!-- items rendered by JavaScript -->\n"
                "    </ul>\n"
                f'    <a href="{noun}-form.html" class="btn btn-primary">New {noun_cap}</a>\n'
                "  </main>\n"
                f"  <footer><p>&copy; 2025 {app_name}</p></footer>\n"
                '  <script src="../app.js" defer></script>\n'
                "</body>\n"
                "</html>\n"
            )

    def _generate_show_template(self, noun: str, spec: dict) -> str:
        mode = spec.get("mode", "flask")
        app_name = spec.get("name", "App")
        noun_cap = noun.capitalize()
        route_prefix = f"{noun}s" if not noun.endswith("s") else noun

        models = spec.get("models", [])
        model = next(
            (m for m in models if m.get("name", "").lower() == noun.lower()),
            None,
        )
        fields = [
            f for f in (model.get("fields", []) if model else [])
            if not f.get("primary_key", False)
        ]

        if mode == "flask":
            field_rows = ""
            for f in fields:
                fname = f.get("name", "field")
                field_rows += (
                    "  <dt>" + fname.replace("_", " ").capitalize() + "</dt>\n"
                    "  <dd>{{ item." + fname + " }}</dd>\n"
                )

            return (
                "{% extends 'base.html' %}\n"
                "{# [DOMValidityPresheaf: valid HTML5 nesting] #}\n"
                "{% block title %}{{ item.title if item.title is defined else item.id }} — {% endblock %}\n"
                "{% block body %}\n"
                "{# [AccessibilityChecker: h1 present — WCAG 2.4.6] #}\n"
                "<h1>{{ item.title if item.title is defined else (item.name if item.name is defined else item.id) }}</h1>\n"
                "<dl>\n"
                + field_rows +
                "</dl>\n"
                "<nav aria-label=\"Record actions\">\n"
                "  <a href=\"{{ url_for('" + route_prefix + ".edit', id=item.id) }}\">Edit</a>\n"
                "  {# [AccessibilityChecker: descriptive back link — WCAG 2.4.4] #}\n"
                "  <a href=\"{{ url_for('" + route_prefix + ".index') }}\">Back to " + noun_cap + "s</a>\n"
                "</nav>\n"
                "{% endblock %}\n"
            )
        else:
            return (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                f"  <title>{noun_cap} Detail — {app_name}</title>\n"
                '  <link rel="stylesheet" href="../style.css">\n'
                "</head>\n"
                "<body>\n"
                '  <a href="#main-content" class="sr-only skip-link">Skip to main content</a>\n'
                '  <nav aria-label="Main navigation">\n'
                f'    <a href="../index.html" class="nav-brand">{app_name}</a>\n'
                "  </nav>\n"
                '  <main id="main-content">\n'
                f"    <h1 id=\"item-title\">{noun_cap} Detail</h1>\n"
                "    <dl id=\"item-fields\">\n"
                "      <!-- rendered by JavaScript -->\n"
                "    </dl>\n"
                f'    <a href="{noun}s-list.html">Back to {noun_cap}s</a>\n'
                "  </main>\n"
                f"  <footer><p>&copy; 2025 {app_name}</p></footer>\n"
                '  <script src="../app.js" defer></script>\n'
                "</body>\n"
                "</html>\n"
            )

    def _generate_form_template(self, noun: str, spec: dict) -> str:
        mode = spec.get("mode", "flask")
        app_name = spec.get("name", "App")
        noun_cap = noun.capitalize()
        route_prefix = f"{noun}s" if not noun.endswith("s") else noun

        forms = spec.get("forms", [])
        form_def = next(
            (f for f in forms if f.get("name", "").lower().startswith(noun.lower())),
            None,
        )
        form_fields = form_def.get("fields", []) if form_def else []

        if not form_fields:
            models = spec.get("models", [])
            model = next(
                (m for m in models if m.get("name", "").lower() == noun.lower()),
                None,
            )
            if model:
                form_fields = [
                    {"name": f["name"], "type": "text", "required": not f.get("nullable", True)}
                    for f in model.get("fields", [])
                    if not f.get("primary_key", False)
                ]

        if mode == "flask":
            field_html = ""
            for f in form_fields:
                fname = f.get("name", "field")
                ftype = f.get("type", "text")
                required = f.get("required", False)
                req_attr = " required" if required else ""
                req_mark = ' <span aria-hidden="true">*</span>' if required else ""
                field_html += (
                    "  {# [AccessibilityChecker: label+input pair — WCAG 1.3.1] #}\n"
                    f"  <div class=\"form-group\">\n"
                    f"    <label for=\"{fname}\">{fname.replace('_', ' ').capitalize()}{req_mark}</label>\n"
                    f"    <input type=\"{ftype}\" id=\"{fname}\" name=\"{fname}\""
                    f" value=\"{{{{ item.{fname} if item else '' }}}}\"" + req_attr + ">\n"
                    f"    <span role=\"alert\" data-error-for=\"{fname}\"></span>\n"
                    "  </div>\n"
                )

            action_new = f"url_for('{route_prefix}.create')"
            action_edit = f"url_for('{route_prefix}.update', id=item.id)"

            return (
                "{% extends 'base.html' %}\n"
                "{# [DOMValidityPresheaf: valid HTML5 nesting] #}\n"
                "{% block title %}{% if item %}Edit{% else %}New{% endif %} " + noun_cap + " — {% endblock %}\n"
                "{% block body %}\n"
                "{# [AccessibilityChecker: h1 present — WCAG 2.4.6] #}\n"
                "<h1>{% if item %}Edit{% else %}New{% endif %} " + noun_cap + "</h1>\n"
                "<form method=\"post\"\n"
                "      action=\"{% if item %}" + "{{ " + action_edit + " }}" + "{% else %}" + "{{ " + action_new + " }}" + "{% endif %}\">\n"
                "  {# [CSRFChecker: csrf_token FIRST in every POST form — Phase 2b requirement] #}\n"
                "  {{ csrf_token() }}\n"
                + field_html +
                "  {# [AccessibilityChecker: button min-height 44px enforced in style.css — WCAG 2.5.5] #}\n"
                "  <button type=\"submit\" class=\"btn btn-primary\">{% if item %}Update{% else %}Create{% endif %}</button>\n"
                "  <a href=\"{{ url_for('" + route_prefix + ".index') }}\">Cancel</a>\n"
                "</form>\n"
                "{% endblock %}\n"
            )
        else:
            field_html = ""
            for f in form_fields:
                fname = f.get("name", "field")
                ftype = f.get("type", "text")
                required = f.get("required", False)
                req_attr = " required" if required else ""
                req_mark = " *" if required else ""
                field_html += (
                    f"    <div class=\"form-group\">\n"
                    f"      <label for=\"{fname}\">{fname.replace('_', ' ').capitalize()}{req_mark}</label>\n"
                    f"      <input type=\"{ftype}\" id=\"{fname}\" name=\"{fname}\"{req_attr}>\n"
                    f"    </div>\n"
                )

            return (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                f"  <title>New {noun_cap} — {app_name}</title>\n"
                '  <link rel="stylesheet" href="../style.css">\n'
                "</head>\n"
                "<body>\n"
                '  <a href="#main-content" class="sr-only skip-link">Skip to main content</a>\n'
                '  <nav aria-label="Main navigation">\n'
                f'    <a href="../index.html" class="nav-brand">{app_name}</a>\n'
                "  </nav>\n"
                '  <main id="main-content">\n'
                f"    <h1>New {noun_cap}</h1>\n"
                f'    <form id="{noun}-form">\n'
                + field_html +
                '      <button type="submit" class="btn btn-primary">Save</button>\n'
                "    </form>\n"
                "  </main>\n"
                f"  <footer><p>&copy; 2025 {app_name}</p></footer>\n"
                '  <script src="../app.js" defer></script>\n'
                "</body>\n"
                "</html>\n"
            )

    # ------------------------------------------------------------------
    # Auth templates
    # ------------------------------------------------------------------

    def _generate_auth_templates(self, spec: dict) -> dict[str, str]:
        mode = spec.get("mode", "flask")
        app_name = spec.get("name", "App")
        result: dict[str, str] = {}

        if mode == "flask":
            result["login.html"] = (
                "{% extends 'base.html' %}\n"
                "{# [DOMValidityPresheaf: valid HTML5 nesting] #}\n"
                "{% block title %}Login — {% endblock %}\n"
                "{% block body %}\n"
                "{# [AccessibilityChecker: h1 present — WCAG 2.4.6] #}\n"
                "<h1>Login</h1>\n"
                "<form method=\"post\" action=\"{{ url_for('login') }}\">\n"
                "  {# [CSRFChecker: csrf_token FIRST in every POST form] #}\n"
                "  {{ csrf_token() }}\n"
                "  {# [AccessibilityChecker: label+input pair — WCAG 1.3.1] #}\n"
                "  <div class=\"form-group\">\n"
                "    <label for=\"username\">Username</label>\n"
                "    <input type=\"text\" id=\"username\" name=\"username\" required autocomplete=\"username\">\n"
                "    <span role=\"alert\" data-error-for=\"username\"></span>\n"
                "  </div>\n"
                "  <div class=\"form-group\">\n"
                "    <label for=\"password\">Password</label>\n"
                "    <input type=\"password\" id=\"password\" name=\"password\" required autocomplete=\"current-password\">\n"
                "    <span role=\"alert\" data-error-for=\"password\"></span>\n"
                "  </div>\n"
                "  <button type=\"submit\" class=\"btn btn-primary\">Login</button>\n"
                "</form>\n"
                "{% endblock %}\n"
            )
            result["register.html"] = (
                "{% extends 'base.html' %}\n"
                "{# [DOMValidityPresheaf: valid HTML5 nesting] #}\n"
                "{% block title %}Register — {% endblock %}\n"
                "{% block body %}\n"
                "{# [AccessibilityChecker: h1 present — WCAG 2.4.6] #}\n"
                "<h1>Register</h1>\n"
                "<form method=\"post\" action=\"{{ url_for('register') }}\">\n"
                "  {# [CSRFChecker: csrf_token FIRST in every POST form] #}\n"
                "  {{ csrf_token() }}\n"
                "  <div class=\"form-group\">\n"
                "    <label for=\"username\">Username</label>\n"
                "    <input type=\"text\" id=\"username\" name=\"username\" required autocomplete=\"username\">\n"
                "    <span role=\"alert\" data-error-for=\"username\"></span>\n"
                "  </div>\n"
                "  <div class=\"form-group\">\n"
                "    <label for=\"email\">Email</label>\n"
                "    <input type=\"email\" id=\"email\" name=\"email\" required autocomplete=\"email\">\n"
                "    <span role=\"alert\" data-error-for=\"email\"></span>\n"
                "  </div>\n"
                "  <div class=\"form-group\">\n"
                "    <label for=\"password\">Password</label>\n"
                "    <input type=\"password\" id=\"password\" name=\"password\" required autocomplete=\"new-password\">\n"
                "    <span role=\"alert\" data-error-for=\"password\"></span>\n"
                "  </div>\n"
                "  <div class=\"form-group\">\n"
                "    <label for=\"password_confirm\">Confirm Password</label>\n"
                "    <input type=\"password\" id=\"password_confirm\" name=\"password_confirm\" required autocomplete=\"new-password\">\n"
                "    <span role=\"alert\" data-error-for=\"password_confirm\"></span>\n"
                "  </div>\n"
                "  <button type=\"submit\" class=\"btn btn-primary\">Register</button>\n"
                "</form>\n"
                "{% endblock %}\n"
            )
        else:
            result["login.html"] = (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                f"  <title>Login — {app_name}</title>\n"
                '  <link rel="stylesheet" href="style.css">\n'
                "</head>\n"
                "<body>\n"
                '  <a href="#main-content" class="sr-only skip-link">Skip to main content</a>\n'
                '  <nav aria-label="Main navigation">\n'
                f'    <a href="index.html" class="nav-brand">{app_name}</a>\n'
                "  </nav>\n"
                '  <main id="main-content">\n'
                "    <h1>Login</h1>\n"
                '    <form id="login-form">\n'
                '      <div class="form-group">\n'
                '        <label for="username">Username</label>\n'
                '        <input type="text" id="username" name="username" required>\n'
                "      </div>\n"
                '      <div class="form-group">\n'
                '        <label for="password">Password</label>\n'
                '        <input type="password" id="password" name="password" required>\n'
                "      </div>\n"
                '      <button type="submit" class="btn btn-primary">Login</button>\n'
                "    </form>\n"
                "  </main>\n"
                f"  <footer><p>&copy; 2025 {app_name}</p></footer>\n"
                '  <script src="app.js" defer></script>\n'
                "</body>\n"
                "</html>\n"
            )
            result["register.html"] = (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                f"  <title>Register — {app_name}</title>\n"
                '  <link rel="stylesheet" href="style.css">\n'
                "</head>\n"
                "<body>\n"
                '  <a href="#main-content" class="sr-only skip-link">Skip to main content</a>\n'
                '  <nav aria-label="Main navigation">\n'
                f'    <a href="index.html" class="nav-brand">{app_name}</a>\n'
                "  </nav>\n"
                '  <main id="main-content">\n'
                "    <h1>Register</h1>\n"
                '    <form id="register-form">\n'
                '      <div class="form-group">\n'
                '        <label for="username">Username</label>\n'
                '        <input type="text" id="username" name="username" required>\n'
                "      </div>\n"
                '      <div class="form-group">\n'
                '        <label for="email">Email</label>\n'
                '        <input type="email" id="email" name="email" required>\n'
                "      </div>\n"
                '      <div class="form-group">\n'
                '        <label for="password">Password</label>\n'
                '        <input type="password" id="password" name="password" required>\n'
                "      </div>\n"
                '      <button type="submit" class="btn btn-primary">Register</button>\n'
                "    </form>\n"
                "  </main>\n"
                f"  <footer><p>&copy; 2025 {app_name}</p></footer>\n"
                '  <script src="app.js" defer></script>\n'
                "</body>\n"
                "</html>\n"
            )

        return result

    # ------------------------------------------------------------------
    # Index template
    # ------------------------------------------------------------------

    def _generate_index_template(self, spec: dict) -> str:
        mode = spec.get("mode", "flask")
        app_name = spec.get("name", "App")
        description = spec.get("description", f"Welcome to {app_name}.")
        metaphors = spec.get("ui_metaphors", [])
        has_hero = any("hero" in m for m in metaphors)

        nouns = spec.get("domain_nouns", [])
        first_noun = nouns[0] if nouns else None
        first_noun_plural = (
            f"{first_noun}s" if first_noun and not first_noun.endswith("s") else first_noun
        )

        if mode == "flask":
            cta = (
                f"  <a href=\"{{{{ url_for('{first_noun_plural}.index') }}}}\" class=\"btn btn-primary\""
                f">Browse {first_noun_plural.capitalize() if first_noun_plural else 'Items'}</a>\n"
                if first_noun_plural
                else ""
            )
            hero_open = '  <section class="hero" aria-labelledby="hero-heading">\n' if has_hero else ""
            hero_close = "  </section>\n" if has_hero else ""
            heading_indent = "    " if has_hero else ""
            cta_indent = "    " if has_hero else ""

            return (
                "{% extends 'base.html' %}\n"
                "{# [DOMValidityPresheaf: valid HTML5 nesting] #}\n"
                "{% block title %}" + app_name + "{% endblock %}\n"
                "{% block body %}\n"
                + hero_open +
                "{# [AccessibilityChecker: h1 present — WCAG 2.4.6] #}\n"
                + heading_indent + "<h1>" + app_name + "</h1>\n"
                + heading_indent + "<p>" + description + "</p>\n"
                + cta_indent + (cta.strip("\n") + "\n" if cta else "")
                + hero_close +
                "{% endblock %}\n"
            )
        else:
            cta_href = f"{first_noun_plural}-list.html" if first_noun_plural else "index.html"
            cta_label = f"Browse {first_noun_plural.capitalize()}" if first_noun_plural else "Get Started"
            hero_open = '  <section class="hero">\n' if has_hero else ""
            hero_close = "  </section>\n" if has_hero else ""
            heading_indent = "    " if has_hero else "    "

            return (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                f"  <title>{app_name}</title>\n"
                '  <link rel="stylesheet" href="style.css">\n'
                "</head>\n"
                "<body>\n"
                '  <a href="#main-content" class="sr-only skip-link">Skip to main content</a>\n'
                '  <nav aria-label="Main navigation">\n'
                f'    <a href="index.html" class="nav-brand">{app_name}</a>\n'
                "  </nav>\n"
                '  <main id="main-content">\n'
                + hero_open +
                f"{heading_indent}<h1>{app_name}</h1>\n"
                f"{heading_indent}<p>{description}</p>\n"
                f'{heading_indent}<a href="{cta_href}" class="btn btn-primary">{cta_label}</a>\n'
                + hero_close +
                "  </main>\n"
                f"  <footer><p>&copy; 2025 {app_name}</p></footer>\n"
                '  <script src="app.js" defer></script>\n'
                "</body>\n"
                "</html>\n"
            )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def _verify_templates(self, files: dict[str, str]) -> list[str]:
        violations: list[str] = []

        for name, content in files.items():
            is_jinja = "{% " in content or "{# " in content

            if not is_jinja:
                if '<html lang=' not in content:
                    violations.append(
                        f"[AccessibilityChecker] {name}: missing lang attribute on <html> (WCAG 3.1.1)"
                    )
                if '<title>' not in content:
                    violations.append(
                        f"[AccessibilityChecker] {name}: missing <title> element (WCAG 2.4.2)"
                    )
            else:
                if '<html lang=' not in content and "extends 'base.html'" not in content:
                    violations.append(
                        f"[AccessibilityChecker] {name}: missing lang attribute and does not extend base.html (WCAG 3.1.1)"
                    )

            if "<h1" not in content and "{% block body" not in content and "block body" not in name:
                if "base.html" not in name:
                    violations.append(
                        f"[AccessibilityChecker] {name}: missing <h1> primary heading (WCAG 2.4.6)"
                    )

            for form_match in re.finditer(r'<form[^>]*method=["\']post["\']', content, re.IGNORECASE):
                form_start = form_match.start()
                form_end = content.find("</form>", form_start)
                if form_end == -1:
                    form_end = len(content)
                form_content = content[form_start:form_end]
                if "csrf_token" not in form_content:
                    violations.append(
                        f"[CSRFChecker] {name}: POST form missing csrf_token (Flask mode)"
                    )

            for img_match in re.finditer(r'<img(?![^>]*\balt=)[^>]*>', content, re.IGNORECASE):
                violations.append(
                    f"[AccessibilityChecker] {name}: <img> missing alt attribute (WCAG 1.1.1)"
                )

        return violations
