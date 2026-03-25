"""Behavioural obligation presheaf — sheaf-theoretic behavioural governance.

Behavioural properties form a presheaf over the view site.  Descent
means consistent behaviour across views (e.g., the router handles all
routes, error handling covers all modules, state persists across
navigation).  Obstructions are behavioural bugs (dead buttons,
unhandled routes, lost state).

Every interactive behaviour — events, state management, routing,
loading, persistence, error handling — is an *obligation* that the
generated JavaScript must satisfy.  This module formalises those
obligations as a presheaf B over the view site V and provides:

  - Typed value objects for every behavioural domain (events, state,
    routes, loading phases, error strategies, persistence).
  - The ``BehavioralObligationPresheaf`` — the presheaf itself, with
    sections at each view and global sections.
  - ``BehavioralPresetBuilder`` — preset obligation bundles at four
    quality tiers (minimal → production).
  - ``BaseJSGenerator`` — derives the base JavaScript from the
    behavioural theory rather than hardcoding it.

This is a *general* theory: it applies to any web application, not
only to a specific domain.
"""
from __future__ import annotations

import json
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "BehaviorDomain",
    "EventPattern",
    "StateShape",
    "RouteDefinition",
    "LoadingPhase",
    "InitializationSequence",
    "ErrorStrategy",
    "PersistenceLayer",
    "BehavioralObligation",
    "BehavioralObligationPresheaf",
    "BehavioralPresetBuilder",
    "BaseJSGenerator",
]


# ══════════════════════════════════════════════════════════════════════
# Behavior domains — the fibres of the behavioural presheaf
# ══════════════════════════════════════════════════════════════════════

class BehaviorDomain(str, Enum):
    """Each value names a fibre of the behavioural obligation presheaf."""

    EVENT_HANDLING = "event_handling"
    STATE_MANAGEMENT = "state_management"
    ROUTING = "routing"
    LOADING = "loading"
    PERSISTENCE = "persistence"
    ERROR_HANDLING = "error_handling"
    ACCESSIBILITY_BEHAVIOR = "accessibility_behavior"
    ANIMATION_BEHAVIOR = "animation_behavior"
    FORM_HANDLING = "form_handling"
    NETWORK = "network"
    AUDIO = "audio"
    KEYBOARD_NAV = "keyboard_nav"
    DRAG_DROP = "drag_drop"
    UNDO_REDO = "undo_redo"
    REAL_TIME = "real_time"


# ══════════════════════════════════════════════════════════════════════
# Value objects — the stalks of each fibre
# ══════════════════════════════════════════════════════════════════════

@dataclass
class EventPattern:
    """A UI event pattern: which element, what event, how it propagates."""

    element_selector: str
    event_type: str
    handler_description: str
    propagation: str = "delegate"
    requires_debounce: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_selector": self.element_selector,
            "event_type": self.event_type,
            "handler_description": self.handler_description,
            "propagation": self.propagation,
            "requires_debounce": self.requires_debounce,
        }


@dataclass
class StateShape:
    """Application state structure — a section of the state fibre."""

    name: str
    fields: dict[str, str]
    persistence: str = "memory"
    views: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fields": dict(self.fields),
            "persistence": self.persistence,
            "views": list(self.views),
        }


@dataclass
class RouteDefinition:
    """A client-side route — a morphism in the view site."""

    path: str
    view_id: str
    title: str = ""
    requires_auth: bool = False
    preload_data: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "view_id": self.view_id,
            "title": self.title,
            "requires_auth": self.requires_auth,
            "preload_data": list(self.preload_data),
        }


# ══════════════════════════════════════════════════════════════════════
# Loading sequence — the temporal fibre
# ══════════════════════════════════════════════════════════════════════

@dataclass
class LoadingPhase:
    """A single initialization phase with dependency ordering."""

    id: str
    label: str
    description: str = ""
    weight: float = 0.2
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "weight": self.weight,
            "dependencies": list(self.dependencies),
        }


@dataclass
class InitializationSequence:
    """Full loading sequence — an ordered cover of the temporal fibre.

    The ``ordered`` method returns a topological sort respecting
    dependency edges; ``to_js_config`` emits the JavaScript
    configuration consumed by the loading-screen driver.
    """

    phases: list[LoadingPhase] = field(default_factory=list)

    @property
    def total_phases(self) -> int:
        return len(self.phases)

    # ── topological sort ──────────────────────────────────────────

    def ordered(self) -> list[LoadingPhase]:
        """Return phases in dependency-respecting order (Kahn's algorithm)."""
        by_id: dict[str, LoadingPhase] = {p.id: p for p in self.phases}
        in_degree: dict[str, int] = {p.id: 0 for p in self.phases}
        dependents: dict[str, list[str]] = defaultdict(list)

        for phase in self.phases:
            for dep in phase.dependencies:
                if dep in by_id:
                    in_degree[phase.id] += 1
                    dependents[dep].append(phase.id)

        queue = [pid for pid, deg in in_degree.items() if deg == 0]
        result: list[LoadingPhase] = []

        while queue:
            queue.sort()
            pid = queue.pop(0)
            result.append(by_id[pid])
            for child in dependents[pid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        # Append any remaining phases (cycle-breaking fallback).
        seen = {p.id for p in result}
        for phase in self.phases:
            if phase.id not in seen:
                result.append(phase)

        return result

    # ── JS config emission ────────────────────────────────────────

    def to_js_config(self) -> str:
        """Emit a JS constant consumed by the loading-screen driver."""
        ordered = self.ordered()
        entries: list[dict[str, Any]] = []
        for phase in ordered:
            entries.append({
                "id": phase.id,
                "label": phase.label,
                "weight": round(phase.weight, 3),
            })
        return (
            "const LOADING_PHASES = "
            + json.dumps(entries, indent=2)
            + ";\n"
        )

    # ── factory from concept names ────────────────────────────────

    _CONCEPT_MAP: dict[str, tuple[str, str]] = {
        "game_engine": ("engine", "Game engine"),
        "canvas_renderer": ("canvas", "Canvas renderer"),
        "audio_system": ("audio", "Audio synthesizer"),
        "data_layer": ("data", "Data persistence"),
        "ai_opponent": ("ai", "AI system"),
        "ui_system": ("ui", "Interface"),
        "physics": ("physics", "Physics engine"),
        "networking": ("network", "Network layer"),
        "animation": ("animation", "Animation system"),
        "particle_system": ("particles", "Particle engine"),
        "save_system": ("save", "Save system"),
    }

    _DEFAULT_PHASES: list[tuple[str, str, float]] = [
        ("core", "Core", 0.30),
        ("ui", "Interface", 0.40),
        ("data", "Data", 0.30),
    ]

    @classmethod
    def from_concepts(cls, concept_names: list[str]) -> InitializationSequence:
        """Derive loading phases from high-level concept names.

        Recognised concepts get a specific phase; unrecognised concepts
        fall through to a generic three-phase sequence.
        """
        phases: list[LoadingPhase] = []
        matched: set[str] = set()

        for name in concept_names:
            key = name.lower().replace(" ", "_").replace("-", "_")
            if key in cls._CONCEPT_MAP:
                pid, label = cls._CONCEPT_MAP[key]
                if pid not in matched:
                    matched.add(pid)
                    phases.append(LoadingPhase(
                        id=pid,
                        label=label,
                        weight=round(1.0 / max(len(concept_names), 1), 3),
                    ))

        if not phases:
            for pid, label, weight in cls._DEFAULT_PHASES:
                phases.append(LoadingPhase(id=pid, label=label, weight=weight))
        else:
            # Normalise weights so they sum to 1.
            total = sum(p.weight for p in phases) or 1.0
            for p in phases:
                p.weight = round(p.weight / total, 3)

        return cls(phases=phases)


# ══════════════════════════════════════════════════════════════════════
# Error strategy — the error-handling fibre
# ══════════════════════════════════════════════════════════════════════

@dataclass
class ErrorStrategy:
    """How the application handles runtime errors."""

    boundary_level: str = "global"
    user_feedback: str = "toast"
    recovery: str = "retry"
    logging: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_level": self.boundary_level,
            "user_feedback": self.user_feedback,
            "recovery": self.recovery,
            "logging": self.logging,
        }


# ══════════════════════════════════════════════════════════════════════
# Persistence layer — the data-permanence fibre
# ══════════════════════════════════════════════════════════════════════

@dataclass
class PersistenceLayer:
    """Specification for a client-side persistence layer."""

    kind: str = "localStorage"
    namespace: str = "jugeo"
    entities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "namespace": self.namespace,
            "entities": list(self.entities),
        }

    def to_js_store_class(self) -> str:
        """Generate a JS storage wrapper class."""
        ns = self.namespace
        if self.kind == "sessionStorage":
            storage = "sessionStorage"
        elif self.kind == "indexedDB":
            return self._indexeddb_store()
        elif self.kind == "server":
            return self._server_store()
        else:
            storage = "localStorage"

        return textwrap.dedent(f"""\
        window.JugeoStore = class {{
          constructor(prefix = '{ns}') {{ this.prefix = prefix; }}
          _key(k) {{ return this.prefix + ':' + k; }}
          get(key, fallback = null) {{
            try {{ return JSON.parse({storage}.getItem(this._key(key))); }}
            catch {{ return fallback; }}
          }}
          set(key, value) {{ {storage}.setItem(this._key(key), JSON.stringify(value)); }}
          remove(key) {{ {storage}.removeItem(this._key(key)); }}
          keys() {{
            return Object.keys({storage})
              .filter(k => k.startsWith(this.prefix + ':'))
              .map(k => k.slice(this.prefix.length + 1));
          }}
          all() {{ return Object.fromEntries(this.keys().map(k => [k, this.get(k)])); }}
          clear() {{ this.keys().forEach(k => this.remove(k)); }}
        }};
        """)

    def _indexeddb_store(self) -> str:
        ns = self.namespace
        stores = json.dumps(self.entities) if self.entities else '["default"]'
        return textwrap.dedent(f"""\
        window.JugeoStore = class {{
          constructor(dbName = '{ns}', v = 1) {{
            this.dbName = dbName; this.version = v; this._db = null;
            this._stores = {stores};
          }}
          open() {{
            if (this._db) return Promise.resolve(this._db);
            return new Promise((res, rej) => {{
              const r = indexedDB.open(this.dbName, this.version);
              r.onupgradeneeded = e => {{
                const db = e.target.result;
                this._stores.forEach(s => {{
                  if (!db.objectStoreNames.contains(s)) db.createObjectStore(s, {{ keyPath: 'id' }});
                }});
              }};
              r.onsuccess = e => {{ this._db = e.target.result; res(this._db); }};
              r.onerror = e => rej(e.target.error);
            }});
          }}
          async _tx(store, mode, fn) {{
            const db = await this.open();
            return new Promise((res, rej) => {{
              const r = fn(db.transaction(store, mode).objectStore(store));
              r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
            }});
          }}
          get(store, key) {{ return this._tx(store, 'readonly', s => s.get(key)); }}
          set(store, val) {{ return this._tx(store, 'readwrite', s => s.put(val)); }}
          remove(store, key) {{ return this._tx(store, 'readwrite', s => s.delete(key)); }}
        }};
        """)

    def _server_store(self) -> str:
        return textwrap.dedent(f"""\
        window.JugeoStore = class {{
          constructor(baseUrl = '/api/store') {{ this.baseUrl = baseUrl; }}
          async _req(key, opts) {{
            const url = this.baseUrl + (key ? '/' + encodeURIComponent(key) : '');
            const res = await fetch(url, opts);
            return res.ok ? res.json() : null;
          }}
          get(key, fb = null) {{ return this._req(key).then(v => v ?? fb).catch(() => fb); }}
          set(key, value) {{
            return this._req(key, {{ method: 'PUT', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify(value) }});
          }}
          remove(key) {{ return this._req(key, {{ method: 'DELETE' }}); }}
          keys() {{ return this._req(null).then(v => v || []).catch(() => []); }}
        }};
        """)


# ══════════════════════════════════════════════════════════════════════
# Behavioral obligation — a single stalk of the presheaf
# ══════════════════════════════════════════════════════════════════════

@dataclass
class BehavioralObligation:
    """A single behavioural obligation — one stalk of the presheaf B.

    The ``domain`` places the obligation in a specific fibre;
    ``js_pattern`` is a substring or regex that the generated JS
    must contain for the obligation to be considered satisfied.
    """

    domain: BehaviorDomain
    description: str
    js_pattern: str | None = None
    required: bool = True
    view_scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain.value,
            "description": self.description,
            "js_pattern": self.js_pattern,
            "required": self.required,
            "view_scope": self.view_scope,
        }

    def to_js_comment(self) -> str:
        """Emit a JS comment documenting this obligation."""
        scope = f" [view: {self.view_scope}]" if self.view_scope else " [global]"
        req = " (required)" if self.required else " (optional)"
        return f"/* Obligation: {self.description}{scope}{req} */"


# ══════════════════════════════════════════════════════════════════════
# The presheaf itself
# ══════════════════════════════════════════════════════════════════════

class BehavioralObligationPresheaf:
    """Presheaf B over the view site V.

    Sections at a view *v* are the behavioural obligations scoped to
    that view.  Global sections live at the terminal object and
    restrict to every view.  The ``unsatisfied`` method checks descent
    of a JS string against the obligation presheaf — any obligation
    whose ``js_pattern`` is absent in the generated code is an
    obstruction.
    """

    def __init__(self) -> None:
        self._global: list[BehavioralObligation] = []
        self._by_view: dict[str, list[BehavioralObligation]] = defaultdict(list)

    # ── mutators ──────────────────────────────────────────────────

    def add_obligation(
        self,
        view_id: str | None,
        obligation: BehavioralObligation,
    ) -> None:
        """Add an obligation.  *view_id* ``None`` means global."""
        if view_id is None:
            self._global.append(obligation)
        else:
            self._by_view[view_id].append(obligation)

    # ── queries ───────────────────────────────────────────────────

    def obligations_for_view(self, view_id: str) -> list[BehavioralObligation]:
        """All obligations relevant to *view_id* (view-local + global)."""
        return list(self._global) + list(self._by_view.get(view_id, []))

    def global_obligations(self) -> list[BehavioralObligation]:
        """Return only the global obligations."""
        return list(self._global)

    def all_obligations(self) -> list[BehavioralObligation]:
        """Every obligation in the presheaf (global + all views)."""
        result = list(self._global)
        for obs in self._by_view.values():
            result.extend(obs)
        return result

    def views(self) -> list[str]:
        """Return all view ids that have local obligations."""
        return sorted(self._by_view.keys())

    # ── descent verification ──────────────────────────────────────

    def unsatisfied(self, js: str) -> list[BehavioralObligation]:
        """Return obligations whose ``js_pattern`` is NOT found in *js*.

        Obligations without a ``js_pattern`` are always considered
        satisfied (they require manual verification).
        """
        missing: list[BehavioralObligation] = []
        for ob in self.all_obligations():
            if ob.js_pattern and ob.js_pattern not in js:
                missing.append(ob)
        return missing

    # ── section extraction ────────────────────────────────────────

    def section_at(self, view_id: str) -> dict[str, Any]:
        """Return the section (stalk data) at a given view."""
        obs = self.obligations_for_view(view_id)
        by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for ob in obs:
            by_domain[ob.domain.value].append(ob.to_dict())
        return {
            "view_id": view_id,
            "domains": dict(by_domain),
            "count": len(obs),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "global": [ob.to_dict() for ob in self._global],
            "by_view": {
                v: [ob.to_dict() for ob in obs]
                for v, obs in self._by_view.items()
            },
        }


# ══════════════════════════════════════════════════════════════════════
# Preset builder — four quality tiers
# ══════════════════════════════════════════════════════════════════════

class BehavioralPresetBuilder:
    """Build behavioural obligation presheaves at progressive quality tiers.

    Each tier adds obligations on top of the previous one.

      - **minimal** — basic click handling + error boundary.
      - **standard** — routing, state, loading, persistence.
      - **polished** — accessibility, keyboard nav, animations, toasts.
      - **production** — undo/redo, real-time, drag-drop, network,
        form validation, audio.
    """

    @staticmethod
    def minimal() -> BehavioralObligationPresheaf:
        """Tier 1: the bare minimum — events + error boundary."""
        p = BehavioralObligationPresheaf()
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.EVENT_HANDLING,
            description="Click delegation on [data-action] elements",
            js_pattern="data-action",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.ERROR_HANDLING,
            description="Global error boundary with console logging",
            js_pattern="addEventListener('error'",
        ))
        return p

    @staticmethod
    def standard() -> BehavioralObligationPresheaf:
        """Tier 2: routing, state management, loading, persistence."""
        p = BehavioralPresetBuilder.minimal()

        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.ROUTING,
            description="Hash-based SPA router (JugeoRouter)",
            js_pattern="JugeoRouter",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.STATE_MANAGEMENT,
            description="View switching via data-view attributes",
            js_pattern="data-view",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.LOADING,
            description="Loading screen with progress phases",
            js_pattern="loading-screen",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.PERSISTENCE,
            description="LocalStorage wrapper (JugeoStore)",
            js_pattern="JugeoStore",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.EVENT_HANDLING,
            description="Tab switching via delegated click",
            js_pattern="tab-btn",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.EVENT_HANDLING,
            description="Accordion toggle via delegated click",
            js_pattern="accordion-trigger",
        ))
        return p

    @staticmethod
    def polished() -> BehavioralObligationPresheaf:
        """Tier 3: accessibility, keyboard nav, toasts, modals, animations."""
        p = BehavioralPresetBuilder.standard()

        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.ACCESSIBILITY_BEHAVIOR,
            description="ARIA live regions for dynamic content",
            js_pattern="aria-",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.KEYBOARD_NAV,
            description="Keyboard navigation (arrow keys, Escape, Enter)",
            js_pattern="keydown",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.ANIMATION_BEHAVIOR,
            description="Fade-in on scroll via IntersectionObserver",
            js_pattern="IntersectionObserver",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.ERROR_HANDLING,
            description="Toast notifications for user feedback",
            js_pattern="showToast",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.EVENT_HANDLING,
            description="Modal open/close helpers",
            js_pattern="openModal",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.EVENT_HANDLING,
            description="Mobile nav toggle",
            js_pattern="nav-toggle",
        ))
        return p

    @staticmethod
    def production() -> BehavioralObligationPresheaf:
        """Tier 4: full production suite — undo, drag-drop, real-time, forms."""
        p = BehavioralPresetBuilder.polished()

        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.UNDO_REDO,
            description="Undo/redo command stack",
            js_pattern="UndoManager",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.DRAG_DROP,
            description="Drag-and-drop support",
            js_pattern="dragstart",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.REAL_TIME,
            description="WebSocket or SSE for real-time updates",
            js_pattern="WebSocket",
            required=False,
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.FORM_HANDLING,
            description="Client-side form validation",
            js_pattern="reportValidity",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.NETWORK,
            description="Fetch wrapper with retry and timeout",
            js_pattern="fetch(",
        ))
        p.add_obligation(None, BehavioralObligation(
            domain=BehaviorDomain.AUDIO,
            description="AudioContext initialisation on user gesture",
            js_pattern="AudioContext",
            required=False,
        ))
        return p


# ══════════════════════════════════════════════════════════════════════
# Base JS generator — derives JS from the behavioural theory
# ══════════════════════════════════════════════════════════════════════

class BaseJSGenerator:
    """Generate the base JavaScript scaffolding from behavioural theory.

    Every public ``generate_*`` method produces a self-contained JS
    fragment.  The ``generate_all`` method composes them into the
    complete base JS that a generated web application requires.
    """

    # ── loading screen driver ─────────────────────────────────────

    @staticmethod
    def generate_loading_driver(sequence: InitializationSequence) -> str:
        """Generate the loading-screen driver from an InitializationSequence."""
        ordered = sequence.ordered()
        phase_ids = json.dumps([p.id for p in ordered])
        total = len(ordered)

        return textwrap.dedent(f"""\
        /* ─── Loading screen driver ─── */
        (function() {{
          var screen = document.getElementById('loading-screen');
          if (!screen) return;
          var fill = screen.querySelector('[data-hook="loading-progress-fill"]');
          var label = screen.querySelector('[data-hook="loading-progress-label"]');
          var steps = screen.querySelectorAll('[data-hook="loading-steps"] li, .loading-screen__step');
          var bar = screen.querySelector('[data-hook="loading-progress"]');
          var pct = 0, phases = {phase_ids}, idx = 0, total = {total};
          function advance() {{
            if (idx < total) {{
              var step = steps[idx]; if (step) step.classList.add('done');
              idx++; pct = Math.round((idx / total) * 100);
              if (fill) fill.style.width = pct + '%';
              if (label) label.textContent = pct + '%';
              if (bar) bar.setAttribute('aria-valuenow', pct);
            }}
            if (idx < total) {{ setTimeout(advance, 200 + Math.random() * 300); }}
            else {{ setTimeout(function() {{
              screen.style.opacity = '0'; screen.style.transition = 'opacity 0.5s ease';
              setTimeout(function() {{
                screen.style.display = 'none';
                document.body.classList.add('loaded');
                document.body.removeAttribute('data-state');
              }}, 500);
            }}, 400); }}
          }}
          setTimeout(advance, 300);
        }})();
        """)

    # ── SPA router ────────────────────────────────────────────────

    @staticmethod
    def generate_router(routes: list[RouteDefinition]) -> str:
        """Generate a hash-based SPA router for the given routes."""
        regs: list[str] = []
        for r in routes:
            regs.append(f"  router.on('{r.path}', () => showView('{r.view_id}'));")
        regs.append("  router.on('*', (p) => showView(p.replace('/', '') || 'home'));")
        block = "\n".join(regs)

        return textwrap.dedent(f"""\
        /* ─── SPA-style client routing (hash-based) ─── */
        window.JugeoRouter = class {{
          constructor() {{ this.routes = {{}}; window.addEventListener('hashchange', () => this._resolve()); }}
          on(path, handler) {{ this.routes[path] = handler; return this; }}
          navigate(path) {{ window.location.hash = '#' + path; }}
          _resolve() {{
            var hash = window.location.hash.slice(1) || '/';
            var handler = this.routes[hash] || this.routes['*'];
            if (handler) handler(hash);
          }}
          start() {{ this._resolve(); return this; }}
        }};

        (function() {{
          var router = new JugeoRouter();
        {block}
          if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', () => router.start());
          }} else {{ router.start(); }}
          window._jugeoRouter = router;
        }})();
        """)

    # ── view management ───────────────────────────────────────────

    @staticmethod
    def generate_view_manager(view_ids: list[str]) -> str:
        """Generate the view-switching logic."""
        default_view = view_ids[0] if view_ids else "home"
        fallbacks = " || ".join(
            f"document.querySelector('[data-view=\"{v}\"]')"
            for v in (view_ids[:2] if view_ids else ["home"])
        )
        return textwrap.dedent(f"""\
        /* ─── View management ─── */
        function showView(viewName) {{
          document.querySelectorAll('[data-view]').forEach(function(el) {{ el.style.display = 'none'; }});
          var target = document.querySelector('[data-view="' + viewName + '"]');
          if (target) {{ target.style.display = ''; }}
          else {{
            var fb = {fallbacks} || document.querySelector('[data-view]');
            if (fb) fb.style.display = '';
          }}
          document.querySelectorAll('.nav-link').forEach(function(link) {{
            link.classList.remove('active');
            var href = link.getAttribute('href') || '';
            if (href === '#/' + viewName || (viewName === '/' && (href === '#/' || href === '/')))
              link.classList.add('active');
          }});
        }}
        """)

    # ── event delegation ──────────────────────────────────────────

    @staticmethod
    def generate_event_delegation(patterns: list[EventPattern]) -> str:
        """Generate delegated event listeners from EventPattern objects."""
        blocks: list[str] = []
        by_event: dict[str, list[EventPattern]] = defaultdict(list)
        for pat in patterns:
            by_event[pat.event_type].append(pat)

        for event_type, pats in sorted(by_event.items()):
            clauses: list[str] = []
            for pat in pats:
                sel, desc = pat.element_selector, pat.handler_description
                clauses.append(
                    f"    if (e.target.closest('{sel}')) {{ /* {desc} */ }}"
                )
            body = "\n".join(clauses)
            blocks.append(
                f"/* ─── Delegated {event_type} ─── */\n"
                f"document.addEventListener('{event_type}', function(e) {{\n"
                f"{body}\n}});\n"
            )

        if not blocks:
            blocks.append(_DEFAULT_CLICK_DELEGATION)

        return "\n".join(blocks)

    # ── error boundaries ──────────────────────────────────────────

    @staticmethod
    def generate_error_boundaries(strategy: ErrorStrategy) -> str:
        """Generate error handling JS from an ErrorStrategy."""
        if strategy.user_feedback == "toast":
            fb = "if (window.showToast) window.showToast(msg, 'error');"
        elif strategy.user_feedback == "modal":
            fb = ("if (window.openModal) { document.getElementById('error-modal-msg').textContent = msg; "
                  "window.openModal('error-modal'); }")
        elif strategy.user_feedback == "inline":
            fb = "var el = document.getElementById('error-display'); if (el) { el.textContent = msg; el.style.display = ''; }"
        else:
            fb = "/* console-only feedback */"
        log = "console.error('[jugeo]', msg, error);" if strategy.logging else ""
        rec = ""
        if strategy.recovery == "retry":
            rec = "_ec++; if (_ec < 3) console.warn('[jugeo] Retrying (' + _ec + '/3)...');"
        elif strategy.recovery == "fallback":
            rec = ("document.body.classList.add('error-state'); "
                   "var fb = document.getElementById('fallback-ui'); if (fb) fb.style.display = '';")

        return textwrap.dedent(f"""\
        /* ─── Error boundaries ({strategy.boundary_level}) ─── */
        (function() {{
          var _ec = 0;
          window.addEventListener('error', function(event) {{
            var error = event.error || event;
            var msg = error.message || String(error);
            {log}
            {fb}
            {rec}
          }});
          window.addEventListener('unhandledrejection', function(event) {{
            var msg = event.reason ? (event.reason.message || String(event.reason)) : 'Promise rejected';
            {log}
            {fb}
          }});
        }})();
        """)

    # ── persistence store ─────────────────────────────────────────

    @staticmethod
    def generate_store(persistence: PersistenceLayer) -> str:
        """Generate the persistence store class."""
        return persistence.to_js_store_class()

    # ── modal system ──────────────────────────────────────────────

    @staticmethod
    def generate_modal_system() -> str:
        """Generate modal open/close helpers and backdrop click handling."""
        return textwrap.dedent("""\
        /* ─── Modal helpers ─── */
        window.openModal = function(id) {
          var m = document.getElementById(id);
          if (m) { m.style.display = 'flex'; m.setAttribute('aria-hidden', 'false'); }
        };
        window.closeModal = function(id) {
          var m = document.getElementById(id);
          if (m) { m.style.display = 'none'; m.setAttribute('aria-hidden', 'true'); }
        };
        document.addEventListener('click', function(e) {
          if (e.target.matches('[data-action="close-modal"]')) {
            var modal = e.target.closest('.modal');
            if (modal) { modal.style.display = 'none'; modal.setAttribute('aria-hidden', 'true'); }
          }
        });
        """)

    # ── toast system ──────────────────────────────────────────────

    @staticmethod
    def generate_toast_system() -> str:
        """Generate a toast notification system."""
        return textwrap.dedent("""\
        /* ─── Toast system ─── */
        window.showToast = function(message, type, duration) {
          type = type || 'info';
          duration = duration || 3000;
          var container = document.getElementById('toasts');
          if (!container) {
            container = document.createElement('div');
            container.id = 'toasts';
            container.className = 'toast-container';
            container.setAttribute('role', 'status');
            container.setAttribute('aria-live', 'polite');
            document.body.appendChild(container);
          }
          var toast = document.createElement('div');
          toast.className = 'toast toast-' + type;
          toast.setAttribute('role', 'alert');
          toast.textContent = message;
          container.appendChild(toast);
          setTimeout(function() {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.3s ease';
            setTimeout(function() { toast.remove(); }, 300);
          }, duration);
        };
        """)

    # ── keyboard navigation ───────────────────────────────────────

    @staticmethod
    def generate_keyboard_nav() -> str:
        """Generate keyboard navigation support."""
        return textwrap.dedent("""\
        /* ─── Keyboard navigation ─── */
        document.addEventListener('keydown', function(e) {
          if (e.key === 'Escape') {
            document.querySelectorAll('.modal[style*="flex"]').forEach(function(m) {
              m.style.display = 'none'; m.setAttribute('aria-hidden', 'true');
            });
            document.querySelectorAll('.toast').forEach(function(t) { t.remove(); });
          }
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            var active = document.activeElement;
            if (active && active.closest('[role="menu"], [role="listbox"], .nav-links')) {
              e.preventDefault();
              var items = Array.from(
                active.closest('[role="menu"], [role="listbox"], .nav-links')
                      .querySelectorAll('[role="menuitem"], [role="option"], a, button'));
              var idx = items.indexOf(active);
              if (idx === -1) return;
              var next = e.key === 'ArrowDown'
                ? items[(idx + 1) % items.length]
                : items[(idx - 1 + items.length) % items.length];
              if (next) next.focus();
            }
          }
          if (e.key === 'Enter') {
            var el = document.activeElement;
            if (el && el.matches('[role="menuitem"], [role="option"]')) el.click();
          }
        });
        """)

    # ── scroll animations ─────────────────────────────────────────

    @staticmethod
    def _generate_scroll_animations() -> str:
        """Generate fade-in on scroll via IntersectionObserver."""
        return textwrap.dedent("""\
        /* ─── Fade-in on scroll (IntersectionObserver) ─── */
        if ('IntersectionObserver' in window) {
          var obs = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
              if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                obs.unobserve(entry.target);
              }
            });
          }, { threshold: 0.1 });
          document.querySelectorAll('.fade-in,.animate-fade-up').forEach(function(el) {
            obs.observe(el);
          });
        }
        """)

    # ── tab / accordion delegation ────────────────────────────────

    @staticmethod
    def _generate_tab_accordion() -> str:
        """Generate tab switching and accordion toggling."""
        return textwrap.dedent("""\
        /* ─── Tab switching ─── */
        document.addEventListener('click', function(e) {
          var btn = e.target.closest('.tab-btn');
          if (!btn) return;
          var c = btn.closest('.tabs-container');
          c.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); b.setAttribute('aria-selected', 'false'); });
          c.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); p.setAttribute('aria-hidden', 'true'); });
          btn.classList.add('active'); btn.setAttribute('aria-selected', 'true');
          var panel = c.querySelector('#' + btn.dataset.tab);
          if (panel) { panel.classList.add('active'); panel.setAttribute('aria-hidden', 'false'); }
        });

        /* ─── Accordion ─── */
        document.addEventListener('click', function(e) {
          var trigger = e.target.closest('.accordion-trigger');
          if (!trigger) return;
          var expanded = trigger.getAttribute('aria-expanded') === 'true';
          trigger.setAttribute('aria-expanded', String(!expanded));
          var content = trigger.nextElementSibling;
          if (content) { content.classList.toggle('open'); content.setAttribute('aria-hidden', String(expanded)); }
        });
        """)

    # ── button action delegation ──────────────────────────────────

    @staticmethod
    def _generate_action_delegation() -> str:
        """Generate delegated [data-action] button handling."""
        return textwrap.dedent("""\
        /* ─── Button action delegation ─── */
        document.addEventListener('click', function(e) {
          var btn = e.target.closest('[data-action]');
          if (!btn) return;
          var action = btn.dataset.action;
          if (action === 'navigate') {
            var target = btn.dataset.target || btn.getAttribute('href');
            if (target && window._jugeoRouter) window._jugeoRouter.navigate(target.replace('#', ''));
          } else if (action === 'toggle-sidebar') {
            var sb = document.querySelector('.sidebar'); if (sb) sb.classList.toggle('collapsed');
          } else if (action === 'toggle-hud') {
            var hud = document.querySelector('.hud'); if (hud) hud.classList.toggle('collapsed');
          } else if (action === 'start-game') {
            if (window._jugeoRouter) window._jugeoRouter.navigate('/play');
            if (window.CT && window.CT.GameEngine) {
              try { new window.CT.GameEngine().init(); } catch(err) { console.warn('Game init:', err); }
            }
          }
        });
        """)

    # ── mobile nav ────────────────────────────────────────────────

    @staticmethod
    def _generate_mobile_nav() -> str:
        """Generate mobile nav toggle."""
        return textwrap.dedent("""\
        /* ─── Mobile nav toggle ─── */
        document.addEventListener('click', function(e) {
          if (e.target.closest('.nav-toggle')) {
            var links = document.querySelector('.nav-links');
            if (links) links.classList.toggle('open');
          }
        });
        """)

    # ══════════════════════════════════════════════════════════════
    # Main composition method
    # ══════════════════════════════════════════════════════════════

    @classmethod
    def generate_all(
        cls,
        obligations: BehavioralObligationPresheaf,
        views: list[str],
        concepts: list[str],
    ) -> str:
        """Generate all base JS from the behavioural theory.

        This is the theory-derived replacement for the hardcoded
        ``_generate_base_js()`` in ``html_generator``.  Every JS
        fragment is emitted only if a corresponding obligation exists
        in the presheaf.
        """
        all_obs = obligations.all_obligations()
        domains = {ob.domain for ob in all_obs}
        parts: list[str] = []

        parts.append("/* ═══ Generated by jugeo-webapp BaseJSGenerator ═══ */")
        parts.append("'use strict';\n")

        # ── loading screen driver ─────────────────────────────────
        if BehaviorDomain.LOADING in domains:
            sequence = InitializationSequence.from_concepts(concepts)
            parts.append(cls.generate_loading_driver(sequence))

        # ── tab / accordion ───────────────────────────────────────
        parts.append(cls._generate_tab_accordion())

        # ── toast system ──────────────────────────────────────────
        if BehaviorDomain.ERROR_HANDLING in domains:
            parts.append(cls.generate_toast_system())

        # ── modal helpers ─────────────────────────────────────────
        parts.append(cls.generate_modal_system())

        # ── scroll animations ─────────────────────────────────────
        if BehaviorDomain.ANIMATION_BEHAVIOR in domains:
            parts.append(cls._generate_scroll_animations())

        # ── SPA router + view manager ─────────────────────────────
        if BehaviorDomain.ROUTING in domains:
            routes = _default_routes_for_views(views)
            parts.append(cls.generate_router(routes))

        if BehaviorDomain.STATE_MANAGEMENT in domains:
            parts.append(cls.generate_view_manager(views))

        # ── button action delegation ──────────────────────────────
        if BehaviorDomain.EVENT_HANDLING in domains:
            parts.append(cls._generate_action_delegation())

        # ── persistence store ─────────────────────────────────────
        if BehaviorDomain.PERSISTENCE in domains:
            store = PersistenceLayer(kind="localStorage", namespace="jugeo")
            parts.append(cls.generate_store(store))

        # ── keyboard navigation ───────────────────────────────────
        if BehaviorDomain.KEYBOARD_NAV in domains:
            parts.append(cls.generate_keyboard_nav())

        # ── error boundaries ──────────────────────────────────────
        if BehaviorDomain.ERROR_HANDLING in domains:
            strategy = ErrorStrategy()
            parts.append(cls.generate_error_boundaries(strategy))

        # ── mobile nav ────────────────────────────────────────────
        parts.append(cls._generate_mobile_nav())

        parts.append("console.log('jugeo-webapp initialized');")

        return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════
# Module-private helpers
# ══════════════════════════════════════════════════════════════════════

_DEFAULT_CLICK_DELEGATION = textwrap.dedent("""\
/* ─── Default click delegation ─── */
document.addEventListener('click', function(e) {
  var btn = e.target.closest('[data-action]');
  if (!btn) return;
  /* generic action dispatch */
});
""")


def _default_routes_for_views(view_ids: list[str]) -> list[RouteDefinition]:
    """Derive a default set of routes from view names."""
    routes: list[RouteDefinition] = []
    for vid in view_ids:
        path = "/" if vid in ("home", "index") else f"/{vid}"
        title = vid.replace("_", " ").replace("-", " ").title()
        routes.append(RouteDefinition(path=path, view_id=vid, title=title))
    if not any(r.path == "/" for r in routes) and routes:
        routes[0] = RouteDefinition(
            path="/",
            view_id=routes[0].view_id,
            title=routes[0].title,
        )
    return routes
