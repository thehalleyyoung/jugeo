"""Pre-built blueprint patterns for common app structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from ..models import (
    RouteSpec, ModelSpec, TemplateSpec, ResponseType,
    ColumnSpec, ColumnType,
)


@dataclass
class BlueprintPattern:
    name: str
    description: str = ""
    routes: list = field(default_factory=list)
    models: list = field(default_factory=list)
    templates: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "routes": [r.to_dict() if hasattr(r, "to_dict") else r for r in self.routes],
            "models": [m.to_dict() if hasattr(m, "to_dict") else m for m in self.models],
            "templates": [t.to_dict() if hasattr(t, "to_dict") else t for t in self.templates],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BlueprintPattern":
        d = dict(d)
        if "routes" in d:
            d["routes"] = [RouteSpec.from_dict(r) if isinstance(r, dict) else r for r in d["routes"]]
        if "models" in d:
            d["models"] = [ModelSpec.from_dict(m) if isinstance(m, dict) else m for m in d["models"]]
        if "templates" in d:
            d["templates"] = [TemplateSpec.from_dict(t) if isinstance(t, dict) else t for t in d["templates"]]
        return cls(**d)


# ---------------------------------------------------------------------------
# Pattern factory functions
# ---------------------------------------------------------------------------

def auth_pattern() -> BlueprintPattern:
    """Login / logout / register / profile with session management."""
    return BlueprintPattern(
        name="auth",
        description="Authentication blueprint with login, logout, register, profile",
        routes=[
            RouteSpec(url="/login", handler_name="login",
                      template="auth/login.html",
                      methods=["GET", "POST"], response_type=ResponseType.FORM),
            RouteSpec(url="/logout", handler_name="logout",
                      methods=["GET"], response_type=ResponseType.REDIRECT),
            RouteSpec(url="/register", handler_name="register",
                      template="auth/register.html",
                      methods=["GET", "POST"], response_type=ResponseType.FORM),
            RouteSpec(url="/profile", handler_name="profile",
                      template="auth/profile.html",
                      auth_required=True),
        ],
        models=[
            ModelSpec(name="User", columns=[
                ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
                ColumnSpec(name="username", type=ColumnType.STRING, nullable=False, unique=True),
                ColumnSpec(name="email", type=ColumnType.STRING, nullable=False, unique=True),
                ColumnSpec(name="password_hash", type=ColumnType.STRING, nullable=False),
            ]),
        ],
        templates=[
            TemplateSpec(name="auth/login.html"),
            TemplateSpec(name="auth/register.html"),
            TemplateSpec(name="auth/profile.html"),
        ],
    )


def crud_pattern(resource_name: str) -> BlueprintPattern:
    """List / detail / create / edit / delete routes for a resource."""
    lower = resource_name.lower()
    return BlueprintPattern(
        name=lower,
        description=f"CRUD operations for {resource_name}",
        routes=[
            RouteSpec(url=f"/{lower}s", handler_name=f"{lower}_list",
                      template=f"{lower}/list.html"),
            RouteSpec(url=f"/{lower}s/<int:id>", handler_name=f"{lower}_detail",
                      template=f"{lower}/detail.html",
                      params=[{"name": "id", "type": "int"}]),
            RouteSpec(url=f"/{lower}s/create", handler_name=f"{lower}_create",
                      template=f"{lower}/form.html",
                      methods=["GET", "POST"], response_type=ResponseType.FORM),
            RouteSpec(url=f"/{lower}s/<int:id>/edit", handler_name=f"{lower}_edit",
                      template=f"{lower}/form.html",
                      methods=["GET", "POST"], response_type=ResponseType.FORM,
                      params=[{"name": "id", "type": "int"}]),
            RouteSpec(url=f"/{lower}s/<int:id>/delete", handler_name=f"{lower}_delete",
                      methods=["POST"], response_type=ResponseType.REDIRECT,
                      params=[{"name": "id", "type": "int"}]),
        ],
        models=[
            ModelSpec(name=resource_name.capitalize(), columns=[
                ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
                ColumnSpec(name="name", type=ColumnType.STRING, nullable=False),
                ColumnSpec(name="created_at", type=ColumnType.DATETIME),
            ]),
        ],
        templates=[
            TemplateSpec(name=f"{lower}/list.html"),
            TemplateSpec(name=f"{lower}/detail.html"),
            TemplateSpec(name=f"{lower}/form.html"),
        ],
    )


def api_pattern(resource_name: str) -> BlueprintPattern:
    """REST API: GET list, GET detail, POST, PUT, DELETE."""
    lower = resource_name.lower()
    return BlueprintPattern(
        name=f"{lower}_api",
        description=f"REST API for {resource_name}",
        routes=[
            RouteSpec(url=f"/api/{lower}s", handler_name=f"api_{lower}_list",
                      methods=["GET"], response_type=ResponseType.JSON),
            RouteSpec(url=f"/api/{lower}s", handler_name=f"api_{lower}_create",
                      methods=["POST"], response_type=ResponseType.JSON),
            RouteSpec(url=f"/api/{lower}s/<int:id>", handler_name=f"api_{lower}_detail",
                      methods=["GET"], response_type=ResponseType.JSON,
                      params=[{"name": "id", "type": "int"}]),
            RouteSpec(url=f"/api/{lower}s/<int:id>", handler_name=f"api_{lower}_update",
                      methods=["PUT"], response_type=ResponseType.JSON,
                      params=[{"name": "id", "type": "int"}]),
            RouteSpec(url=f"/api/{lower}s/<int:id>", handler_name=f"api_{lower}_delete",
                      methods=["DELETE"], response_type=ResponseType.JSON,
                      params=[{"name": "id", "type": "int"}]),
        ],
    )


def dashboard_pattern() -> BlueprintPattern:
    """Main dashboard with widgets."""
    return BlueprintPattern(
        name="dashboard",
        description="Dashboard with summary widgets",
        routes=[
            RouteSpec(url="/dashboard", handler_name="dashboard",
                      template="dashboard/index.html"),
            RouteSpec(url="/api/dashboard/stats", handler_name="dashboard_stats",
                      response_type=ResponseType.JSON),
        ],
        templates=[
            TemplateSpec(name="dashboard/index.html"),
        ],
    )


def search_pattern() -> BlueprintPattern:
    """Search with pagination."""
    return BlueprintPattern(
        name="search",
        description="Search functionality with pagination",
        routes=[
            RouteSpec(url="/search", handler_name="search",
                      template="search/results.html",
                      methods=["GET"]),
        ],
        templates=[
            TemplateSpec(name="search/results.html"),
        ],
    )


def file_upload_pattern() -> BlueprintPattern:
    """File upload, process, download."""
    return BlueprintPattern(
        name="files",
        description="File upload and download",
        routes=[
            RouteSpec(url="/upload", handler_name="upload",
                      template="files/upload.html",
                      methods=["GET", "POST"], response_type=ResponseType.FORM),
            RouteSpec(url="/files/<int:id>/download", handler_name="download",
                      methods=["GET"],
                      params=[{"name": "id", "type": "int"}]),
        ],
        templates=[
            TemplateSpec(name="files/upload.html"),
        ],
    )


def admin_pattern() -> BlueprintPattern:
    """Admin panel with user management."""
    return BlueprintPattern(
        name="admin",
        description="Admin panel",
        routes=[
            RouteSpec(url="/admin", handler_name="admin_index",
                      template="admin/index.html", auth_required=True),
            RouteSpec(url="/admin/users", handler_name="admin_users",
                      template="admin/users.html", auth_required=True),
        ],
        templates=[
            TemplateSpec(name="admin/index.html"),
            TemplateSpec(name="admin/users.html"),
        ],
    )


PATTERNS = {
    "auth": auth_pattern,
    "crud": crud_pattern,
    "api": api_pattern,
    "dashboard": dashboard_pattern,
    "search": search_pattern,
    "file_upload": file_upload_pattern,
    "admin": admin_pattern,
}
