"""Reusable Jinja2 / HTML snippet templates."""

SNIPPETS = {
    "base_html": """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}{{ app_name }}{% endblock %}</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/base.css') }}">
    {% block styles %}{% endblock %}
</head>
<body>
    {% block navbar %}{% endblock %}
    <div class="flash-messages">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
    </div>
    <main class="container">
        {% block content %}{% endblock %}
    </main>
    <footer class="footer">{% block footer %}{% endblock %}</footer>
    <script src="{{ url_for('static', filename='js/base.js') }}"></script>
    {% block scripts %}{% endblock %}
</body>
</html>""",

    "navbar": """<nav class="navbar">
    <div class="navbar-brand"><a href="{{ url_for('index') }}">{{ brand }}</a></div>
    <button class="navbar-toggle">&#9776;</button>
    <ul class="navbar-nav">
        {% for item in nav_items %}
        <li><a href="{{ item.url }}">{{ item.label }}</a></li>
        {% endfor %}
    </ul>
</nav>""",

    "footer": """<footer class="footer">
    <div class="container">
        <p>&copy; {{ year }} {{ app_name }}. All rights reserved.</p>
    </div>
</footer>""",

    "flash_messages": """<div class="flash-messages">
    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="alert alert-{{ category }}">{{ message }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}
</div>""",

    "pagination": """<nav class="pagination">
    {% if page > 1 %}
        <a href="?page={{ page - 1 }}" class="page-link">Previous</a>
    {% endif %}
    {% for p in range(1, total_pages + 1) %}
        <a href="?page={{ p }}" class="page-link{% if p == page %} active{% endif %}">{{ p }}</a>
    {% endfor %}
    {% if page < total_pages %}
        <a href="?page={{ page + 1 }}" class="page-link">Next</a>
    {% endif %}
</nav>""",

    "form_field_text": """<div class="form-group">
    <label for="{{ field.name }}">{{ field.label }}</label>
    <input type="text" id="{{ field.name }}" name="{{ field.name }}"
           value="{{ field.value or '' }}" {% if field.required %}required{% endif %}>
</div>""",

    "form_field_select": """<div class="form-group">
    <label for="{{ field.name }}">{{ field.label }}</label>
    <select id="{{ field.name }}" name="{{ field.name }}" {% if field.required %}required{% endif %}>
        {% for choice in field.choices %}
        <option value="{{ choice.value }}" {% if choice.value == field.value %}selected{% endif %}>
            {{ choice.label }}
        </option>
        {% endfor %}
    </select>
</div>""",

    "form_field_textarea": """<div class="form-group">
    <label for="{{ field.name }}">{{ field.label }}</label>
    <textarea id="{{ field.name }}" name="{{ field.name }}"
              {% if field.required %}required{% endif %}>{{ field.value or '' }}</textarea>
</div>""",

    "form_field_checkbox": """<div class="form-group">
    <label>
        <input type="checkbox" name="{{ field.name }}"
               {% if field.value %}checked{% endif %}
               {% if field.required %}required{% endif %}>
        {{ field.label }}
    </label>
</div>""",

    "table_view": """<table class="table">
    <thead>
        <tr>
            {% for header in headers %}
            <th>{{ header }}</th>
            {% endfor %}
        </tr>
    </thead>
    <tbody>
        {% for row in rows %}
        <tr>
            {% for cell in row %}
            <td>{{ cell }}</td>
            {% endfor %}
        </tr>
        {% endfor %}
    </tbody>
</table>""",

    "card_view": """<div class="card">
    <div class="card-header"><h3>{{ title }}</h3></div>
    <div class="card-body">{{ body }}</div>
    {% if footer %}<div class="card-footer">{{ footer }}</div>{% endif %}
</div>""",

    "detail_view": """<div class="detail-view">
    {% for label, value in fields %}
    <p><strong>{{ label }}:</strong> {{ value }}</p>
    {% endfor %}
</div>""",

    "list_view": """<div class="list-view">
    {% for item in items %}
    <div class="list-item">
        <h3><a href="{{ item.url }}">{{ item.title }}</a></h3>
        {% if item.description %}<p>{{ item.description }}</p>{% endif %}
    </div>
    {% endfor %}
</div>""",

    "login_form": """<form action="{{ url_for('login') }}" method="POST" class="auth-form">
    <h2>Login</h2>
    <div class="form-group">
        <label for="username">Username</label>
        <input type="text" id="username" name="username" required>
    </div>
    <div class="form-group">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" required>
    </div>
    <button type="submit" class="btn btn-primary">Login</button>
</form>""",

    "register_form": """<form action="{{ url_for('register') }}" method="POST" class="auth-form">
    <h2>Register</h2>
    <div class="form-group">
        <label for="username">Username</label>
        <input type="text" id="username" name="username" required>
    </div>
    <div class="form-group">
        <label for="email">Email</label>
        <input type="email" id="email" name="email" required>
    </div>
    <div class="form-group">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" required>
    </div>
    <div class="form-group">
        <label for="confirm_password">Confirm Password</label>
        <input type="password" id="confirm_password" name="confirm_password" required>
    </div>
    <button type="submit" class="btn btn-primary">Register</button>
</form>""",

    "search_form": """<form action="{{ url_for('search') }}" method="GET" class="search-form">
    <div class="form-group">
        <input type="text" name="q" placeholder="Search..." value="{{ query or '' }}" required>
        <button type="submit" class="btn btn-primary">Search</button>
    </div>
</form>""",

    "modal": """<div class="modal" id="{{ modal_id }}">
    <div class="modal-overlay"></div>
    <div class="modal-content">
        <div class="modal-header">
            <h3>{{ modal_title }}</h3>
            <button class="modal-close" data-dismiss="{{ modal_id }}">&times;</button>
        </div>
        <div class="modal-body">
            {{ modal_body }}
        </div>
    </div>
</div>""",

    "alert": """<div class="alert alert-{{ category }}">
    {{ message }}
</div>""",

    "breadcrumb": """<nav class="breadcrumb">
    {% for crumb in breadcrumbs %}
        {% if not loop.last %}
            <a href="{{ crumb.url }}">{{ crumb.label }}</a> &raquo;
        {% else %}
            <span>{{ crumb.label }}</span>
        {% endif %}
    {% endfor %}
</nav>""",

    "tabs": """<div class="tabs">
    <ul class="tab-nav">
        {% for tab in tabs %}
        <li class="tab-item{% if tab.active %} active{% endif %}">
            <a href="#{{ tab.id }}">{{ tab.label }}</a>
        </li>
        {% endfor %}
    </ul>
    {% for tab in tabs %}
    <div class="tab-pane{% if tab.active %} active{% endif %}" id="{{ tab.id }}">
        {{ tab.content }}
    </div>
    {% endfor %}
</div>""",
}
