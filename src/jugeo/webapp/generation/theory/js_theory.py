"""JavaScript as a presheaf on the event/state site.

Modules are local sections covering different behavioral domains (routing,
state, events, loading).  The IIFE + namespace pattern is the gluing
mechanism — modules compose without interference.  Event delegation is
descent from global listener to local handler.  State management is the
transport of data along navigation morphisms.  Obstructions are JS bugs:
dead event handlers, lost state, unhandled errors, namespace collisions.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "JSModuleKind",
    "JSModuleSpec",
    "JSEncapsulationObligation",
    "JSEventDelegationObligation",
    "JSStateObligation",
    "JSModuleGenerator",
    "JSTheoryGenerator",
    "JSDescentChecker",
    "AgentPromptDeriver",
]


# ---------------------------------------------------------------------------
# 1. JSModuleKind — kinds of JS modules
# ---------------------------------------------------------------------------

class JSModuleKind(str, Enum):
    """Behavioural domain a JS module covers."""

    ROUTER = "router"
    STATE_STORE = "state_store"
    EVENT_DELEGATOR = "event_delegator"
    LOADING_DRIVER = "loading_driver"
    MODAL_SYSTEM = "modal_system"
    TOAST_SYSTEM = "toast_system"
    TAB_CONTROLLER = "tab_controller"
    ACCORDION_CONTROLLER = "accordion_controller"
    FORM_HANDLER = "form_handler"
    KEYBOARD_NAV = "keyboard_nav"
    SCROLL_EFFECTS = "scroll_effects"
    THEME_TOGGLE = "theme_toggle"
    ANIMATION_CONTROLLER = "animation_controller"
    CANVAS_MANAGER = "canvas_manager"
    AUDIO_MANAGER = "audio_manager"
    DATA_PERSISTENCE = "data_persistence"
    ERROR_BOUNDARY = "error_boundary"
    ACCESSIBILITY_HELPERS = "accessibility_helpers"
    DRAG_DROP = "drag_drop"
    UNDO_REDO = "undo_redo"
    SEARCH = "search"
    NOTIFICATION = "notification"
    LAZY_LOADER = "lazy_loader"
    DEBOUNCE_THROTTLE = "debounce_throttle"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# 2. JSModuleSpec — specification for a JS module
# ---------------------------------------------------------------------------

@dataclass
class JSModuleSpec:
    """Specification tying a JS module to a behavioural domain."""

    kind: JSModuleKind
    namespace_key: str
    description: str
    exports: list[str] = field(default_factory=list)
    depends_on: list[JSModuleKind] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    is_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value if isinstance(self.kind, JSModuleKind) else self.kind,
            "namespace_key": self.namespace_key,
            "description": self.description,
            "exports": list(self.exports),
            "depends_on": [
                k.value if isinstance(k, JSModuleKind) else k for k in self.depends_on
            ],
            "views": list(self.views),
            "is_required": self.is_required,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JSModuleSpec:
        return cls(
            kind=JSModuleKind(d["kind"]),
            namespace_key=d["namespace_key"],
            description=d.get("description", ""),
            exports=d.get("exports", []),
            depends_on=[JSModuleKind(v) for v in d.get("depends_on", [])],
            views=d.get("views", []),
            is_required=d.get("is_required", False),
        )


# ---------------------------------------------------------------------------
# 3. JSEncapsulationObligation
# ---------------------------------------------------------------------------

@dataclass
class JSEncapsulationObligation:
    """Module-pattern obligations enforcing namespace hygiene.

    Every module must be IIFE-wrapped, must not pollute the global scope
    beyond ``window.CT``, must create DOM elements programmatically, and
    must communicate with other modules solely through the CT namespace.
    """

    iife_wrapped: bool = True
    no_global_pollution: bool = True
    programmatic_dom: bool = True
    ct_namespace_only: bool = True

    def describe(self) -> list[str]:
        clauses: list[str] = []
        if self.iife_wrapped:
            clauses.append("All modules must be IIFE-wrapped")
        if self.no_global_pollution:
            clauses.append(
                "No global namespace pollution except window.CT"
            )
        if self.programmatic_dom:
            clauses.append(
                "All DOM creation must be programmatic — do not querySelector "
                "for elements you did not create"
            )
        if self.ct_namespace_only:
            clauses.append(
                "All inter-module communication via the CT namespace"
            )
        return clauses


# ---------------------------------------------------------------------------
# 4. JSEventDelegationObligation
# ---------------------------------------------------------------------------

@dataclass
class JSEventDelegationObligation:
    """Event-handling obligations enforcing delegation descent.

    Global listeners delegate to local handlers via ``data-action``
    attributes.  Scroll and resize handlers are debounced.  Every handler
    is wrapped in an error boundary.
    """

    use_event_delegation: bool = True
    data_action_attributes: bool = True
    debounce_scroll_resize: bool = True
    error_boundaries: bool = True

    def describe(self) -> list[str]:
        clauses: list[str] = []
        if self.use_event_delegation:
            clauses.append(
                "Use event delegation (document.addEventListener) "
                "not per-element listeners"
            )
        if self.data_action_attributes:
            clauses.append("Use data-action attributes for buttons")
        if self.debounce_scroll_resize:
            clauses.append("Debounce scroll/resize handlers")
        if self.error_boundaries:
            clauses.append("Error boundaries around all handlers")
        return clauses


# ---------------------------------------------------------------------------
# 5. JSStateObligation
# ---------------------------------------------------------------------------

@dataclass
class JSStateObligation:
    """State-management obligations enforcing transport coherence.

    State lives in a centralised store, changes are observable, state
    persists across navigation, and is serialisable to localStorage.
    """

    centralized_store: bool = True
    observable_changes: bool = True
    persist_across_navigation: bool = True
    serializable_to_local_storage: bool = True

    def describe(self) -> list[str]:
        clauses: list[str] = []
        if self.centralized_store:
            clauses.append("Centralized state store")
        if self.observable_changes:
            clauses.append("State changes observable")
        if self.persist_across_navigation:
            clauses.append("State persists across navigation")
        if self.serializable_to_local_storage:
            clauses.append("State serializable to localStorage")
        return clauses


# ---------------------------------------------------------------------------
# 6. JSModuleGenerator — generate JS modules from theory
# ---------------------------------------------------------------------------

class JSModuleGenerator:
    """Generate real, working JavaScript modules from abstract specs."""

    # -- loading driver -----------------------------------------------------

    @staticmethod
    def generate_loading_driver(phases: list[dict[str, Any]]) -> str:
        """Generate a loading-screen driver.

        *phases* is a list of ``{id, label, weight}`` dicts — NOT
        hard-coded to any specific application.
        """
        phase_ids = ", ".join(f"'{p['id']}'" for p in phases)
        phase_labels = ", ".join(f"'{p['label']}'" for p in phases)
        phase_weights = ", ".join(str(p.get("weight", 1)) for p in phases)
        return textwrap.dedent(f"""\
            (function() {{
              'use strict';
              window.CT = window.CT || {{}};

              var phaseIds = [{phase_ids}];
              var phaseLabels = [{phase_labels}];
              var phaseWeights = [{phase_weights}];
              var totalWeight = phaseWeights.reduce(function(a, b) {{ return a + b; }}, 0);

              var screen = document.getElementById('loading-screen');
              if (!screen) return;
              var fill = screen.querySelector('[data-hook="loading-progress-fill"]');
              var label = screen.querySelector('[data-hook="loading-progress-label"]');
              var steps = screen.querySelectorAll('[data-hook="loading-steps"] li, .loading-screen__step');
              var bar = screen.querySelector('[data-hook="loading-progress"]');
              var completed = 0;
              var idx = 0;

              function advance() {{
                try {{
                  if (idx < phaseIds.length) {{
                    if (steps[idx]) steps[idx].classList.add('done');
                    completed += phaseWeights[idx];
                    idx++;
                    var pct = Math.round((completed / totalWeight) * 100);
                    if (fill) fill.style.width = pct + '%';
                    if (label) label.textContent = pct + '%';
                    if (bar) bar.setAttribute('aria-valuenow', pct);
                  }}
                  if (idx < phaseIds.length) {{
                    setTimeout(advance, 200 + Math.random() * 300);
                  }} else {{
                    setTimeout(function() {{
                      screen.style.opacity = '0';
                      screen.style.transition = 'opacity 0.5s ease';
                      setTimeout(function() {{
                        screen.style.display = 'none';
                        document.body.classList.add('loaded');
                        document.body.removeAttribute('data-state');
                      }}, 500);
                    }}, 400);
                  }}
                }} catch (err) {{
                  console.error('[LoadingDriver] advance error:', err);
                }}
              }}

              window.CT.LoadingDriver = {{
                start: function() {{ setTimeout(advance, 300); }},
                phases: phaseIds,
                labels: phaseLabels
              }};
              window.CT.LoadingDriver.start();
            }})();
        """)

    # -- router -------------------------------------------------------------

    @staticmethod
    def generate_router(routes: list[dict[str, str]]) -> str:
        """Generate a hash-based SPA router.

        *routes* is a list of ``{path, view_id}`` dicts.
        """
        route_map_entries = ",\n      ".join(
            f"'{r['path']}': '{r['view_id']}'" for r in routes
        )
        default_path = routes[0]["path"] if routes else "/"
        return textwrap.dedent(f"""\
            (function() {{
              'use strict';
              window.CT = window.CT || {{}};

              var routes = {{
                  {route_map_entries}
              }};
              var currentPath = null;

              function resolve(hash) {{
                var path = (hash || '#/').replace('#', '') || '/';
                return routes[path] ? path : '{default_path}';
              }}

              function navigate(path) {{
                try {{
                  if (path === currentPath) return;
                  currentPath = path;
                  window.location.hash = '#' + path;
                  var viewId = routes[path];
                  if (viewId && window.CT.ViewManager) {{
                    window.CT.ViewManager.show(viewId);
                  }}
                  window.dispatchEvent(new CustomEvent('ct:route-change', {{
                    detail: {{ path: path, viewId: viewId }}
                  }}));
                }} catch (err) {{
                  console.error('[Router] navigation error:', err);
                }}
              }}

              window.addEventListener('hashchange', function() {{
                navigate(resolve(window.location.hash));
              }});

              window.CT.Router = {{
                navigate: navigate,
                resolve: resolve,
                routes: routes,
                current: function() {{ return currentPath; }}
              }};

              navigate(resolve(window.location.hash));
            }})();
        """)

    # -- view manager -------------------------------------------------------

    @staticmethod
    def generate_view_manager(view_ids: list[str]) -> str:
        """Generate view show/hide manager."""
        ids_literal = ", ".join(f"'{v}'" for v in view_ids)
        return textwrap.dedent(f"""\
            (function() {{
              'use strict';
              window.CT = window.CT || {{}};

              var viewIds = [{ids_literal}];

              function show(targetId) {{
                try {{
                  viewIds.forEach(function(id) {{
                    var el = document.getElementById(id);
                    if (el) {{
                      el.style.display = (id === targetId) ? '' : 'none';
                      el.setAttribute('aria-hidden', id !== targetId ? 'true' : 'false');
                    }}
                  }});
                  window.dispatchEvent(new CustomEvent('ct:view-change', {{
                    detail: {{ viewId: targetId }}
                  }}));
                }} catch (err) {{
                  console.error('[ViewManager] show error:', err);
                }}
              }}

              window.CT.ViewManager = {{
                show: show,
                views: viewIds,
                current: function() {{
                  for (var i = 0; i < viewIds.length; i++) {{
                    var el = document.getElementById(viewIds[i]);
                    if (el && el.style.display !== 'none') return viewIds[i];
                  }}
                  return null;
                }}
              }};
            }})();
        """)

    # -- event delegator ----------------------------------------------------

    @staticmethod
    def generate_event_delegator(actions: list[dict[str, str]]) -> str:
        """Generate event delegation system.

        *actions* is a list of ``{action, handler}`` dicts where *handler*
        is a JS expression referencing the CT namespace.
        """
        cases = "\n        ".join(
            f"case '{a['action']}': {a['handler']}; break;"
            for a in actions
        )
        return textwrap.dedent(f"""\
            (function() {{
              'use strict';
              window.CT = window.CT || {{}};
              var handlers = {{}};

              function register(action, fn) {{
                handlers[action] = fn;
              }}

              document.addEventListener('click', function(e) {{
                try {{
                  var btn = e.target.closest('[data-action]');
                  if (!btn) return;
                  var action = btn.getAttribute('data-action');
                  if (handlers[action]) {{
                    handlers[action](e, btn);
                    return;
                  }}
                  switch (action) {{
                    {cases}
                    default:
                      console.warn('[EventDelegator] unhandled action:', action);
                  }}
                }} catch (err) {{
                  console.error('[EventDelegator] handler error:', err);
                }}
              }});

              window.CT.EventDelegator = {{
                register: register,
                handlers: handlers
              }};
            }})();
        """)

    # -- store --------------------------------------------------------------

    @staticmethod
    def generate_store(namespace: str, entities: list[str]) -> str:
        """Generate a localStorage-backed state store."""
        entity_defaults = ", ".join(f"'{e}': []" for e in entities)
        return textwrap.dedent(f"""\
            (function() {{
              'use strict';
              window.CT = window.CT || {{}};

              var STORAGE_KEY = '{namespace}_store';
              var listeners = {{}};
              var state = load();

              function load() {{
                try {{
                  var raw = localStorage.getItem(STORAGE_KEY);
                  if (raw) return JSON.parse(raw);
                }} catch (err) {{
                  console.warn('[Store] load error:', err);
                }}
                return {{ {entity_defaults} }};
              }}

              function save() {{
                try {{
                  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
                }} catch (err) {{
                  console.warn('[Store] save error:', err);
                }}
              }}

              function get(entity) {{
                return state[entity] !== undefined ? state[entity] : null;
              }}

              function set(entity, value) {{
                var prev = state[entity];
                state[entity] = value;
                save();
                notify(entity, value, prev);
              }}

              function subscribe(entity, fn) {{
                if (!listeners[entity]) listeners[entity] = [];
                listeners[entity].push(fn);
                return function unsubscribe() {{
                  listeners[entity] = listeners[entity].filter(function(f) {{ return f !== fn; }});
                }};
              }}

              function notify(entity, value, prev) {{
                (listeners[entity] || []).forEach(function(fn) {{
                  try {{ fn(value, prev); }} catch (err) {{
                    console.error('[Store] listener error:', err);
                  }}
                }});
                (listeners['*'] || []).forEach(function(fn) {{
                  try {{ fn(entity, value, prev); }} catch (err) {{
                    console.error('[Store] wildcard listener error:', err);
                  }}
                }});
              }}

              window.CT.Store = {{
                get: get,
                set: set,
                subscribe: subscribe,
                getAll: function() {{ return JSON.parse(JSON.stringify(state)); }},
                clear: function() {{
                  state = {{ {entity_defaults} }};
                  save();
                }}
              }};
            }})();
        """)

    # -- modal system -------------------------------------------------------

    @staticmethod
    def generate_modal_system() -> str:
        return textwrap.dedent("""\
            (function() {
              'use strict';
              window.CT = window.CT || {};

              function openModal(id) {
                try {
                  var el = document.getElementById(id);
                  if (!el) { console.warn('[Modal] not found:', id); return; }
                  el.style.display = 'flex';
                  el.setAttribute('aria-hidden', 'false');
                  el.classList.add('modal--open');
                  document.body.style.overflow = 'hidden';
                  var close = el.querySelector('[data-action="close-modal"]');
                  if (close) close.focus();
                } catch (err) {
                  console.error('[Modal] open error:', err);
                }
              }

              function closeModal(id) {
                try {
                  var el = document.getElementById(id);
                  if (!el) return;
                  el.style.display = 'none';
                  el.setAttribute('aria-hidden', 'true');
                  el.classList.remove('modal--open');
                  document.body.style.overflow = '';
                } catch (err) {
                  console.error('[Modal] close error:', err);
                }
              }

              document.addEventListener('click', function(e) {
                if (e.target.classList.contains('modal--open')) {
                  closeModal(e.target.id);
                }
              });

              document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape') {
                  var open = document.querySelector('.modal--open');
                  if (open) closeModal(open.id);
                }
              });

              window.CT.Modal = { open: openModal, close: closeModal };
            })();
        """)

    # -- toast system -------------------------------------------------------

    @staticmethod
    def generate_toast_system() -> str:
        return textwrap.dedent("""\
            (function() {
              'use strict';
              window.CT = window.CT || {};

              var container = null;

              function ensureContainer() {
                if (container) return container;
                container = document.createElement('div');
                container.className = 'toast-container';
                container.setAttribute('aria-live', 'polite');
                container.style.cssText =
                  'position:fixed;top:1rem;right:1rem;z-index:10000;display:flex;' +
                  'flex-direction:column;gap:0.5rem;pointer-events:none;';
                document.body.appendChild(container);
                return container;
              }

              function show(message, type, duration) {
                type = type || 'info';
                duration = duration || 3000;
                try {
                  var c = ensureContainer();
                  var toast = document.createElement('div');
                  toast.className = 'toast toast--' + type;
                  toast.setAttribute('role', 'status');
                  toast.style.cssText =
                    'padding:0.75rem 1.25rem;border-radius:0.5rem;color:#fff;' +
                    'pointer-events:auto;opacity:0;transform:translateX(100%);' +
                    'transition:all 0.3s ease;font-size:0.9rem;max-width:24rem;';
                  var bg = { info:'#3b82f6', success:'#10b981', warning:'#f59e0b', error:'#ef4444' };
                  toast.style.background = bg[type] || bg.info;
                  toast.textContent = message;
                  c.appendChild(toast);
                  requestAnimationFrame(function() {
                    toast.style.opacity = '1';
                    toast.style.transform = 'translateX(0)';
                  });
                  setTimeout(function() {
                    toast.style.opacity = '0';
                    toast.style.transform = 'translateX(100%)';
                    setTimeout(function() { toast.remove(); }, 300);
                  }, duration);
                } catch (err) {
                  console.error('[Toast] show error:', err);
                }
              }

              window.CT.Toast = { show: show };
            })();
        """)

    # -- tab controller -----------------------------------------------------

    @staticmethod
    def generate_tab_controller() -> str:
        return textwrap.dedent("""\
            (function() {
              'use strict';
              window.CT = window.CT || {};

              document.addEventListener('click', function(e) {
                try {
                  var btn = e.target.closest('[data-tab]');
                  if (!btn) return;
                  var group = btn.closest('[data-tab-group]');
                  if (!group) return;
                  var target = btn.getAttribute('data-tab');
                  group.querySelectorAll('[data-tab]').forEach(function(b) {
                    b.classList.toggle('active', b === btn);
                    b.setAttribute('aria-selected', b === btn ? 'true' : 'false');
                  });
                  group.querySelectorAll('[data-tab-panel]').forEach(function(p) {
                    var match = p.getAttribute('data-tab-panel') === target;
                    p.style.display = match ? '' : 'none';
                    p.setAttribute('aria-hidden', match ? 'false' : 'true');
                  });
                } catch (err) {
                  console.error('[TabController] error:', err);
                }
              });

              window.CT.Tabs = {
                activate: function(groupSel, tabId) {
                  var group = document.querySelector(groupSel);
                  if (!group) return;
                  var btn = group.querySelector('[data-tab="' + tabId + '"]');
                  if (btn) btn.click();
                }
              };
            })();
        """)

    # -- accordion controller -----------------------------------------------

    @staticmethod
    def generate_accordion_controller() -> str:
        return textwrap.dedent("""\
            (function() {
              'use strict';
              window.CT = window.CT || {};

              document.addEventListener('click', function(e) {
                try {
                  var trigger = e.target.closest('[data-accordion-trigger]');
                  if (!trigger) return;
                  var item = trigger.closest('[data-accordion-item]');
                  if (!item) return;
                  var content = item.querySelector('[data-accordion-content]');
                  if (!content) return;
                  var isOpen = item.classList.contains('open');
                  var group = item.closest('[data-accordion]');
                  if (group && !group.hasAttribute('data-multi')) {
                    group.querySelectorAll('[data-accordion-item].open').forEach(function(it) {
                      it.classList.remove('open');
                      var c = it.querySelector('[data-accordion-content]');
                      if (c) { c.style.maxHeight = '0'; c.setAttribute('aria-hidden', 'true'); }
                    });
                  }
                  if (!isOpen) {
                    item.classList.add('open');
                    content.style.maxHeight = content.scrollHeight + 'px';
                    content.setAttribute('aria-hidden', 'false');
                  } else {
                    item.classList.remove('open');
                    content.style.maxHeight = '0';
                    content.setAttribute('aria-hidden', 'true');
                  }
                } catch (err) {
                  console.error('[Accordion] error:', err);
                }
              });

              window.CT.Accordion = { init: function() {} };
            })();
        """)

    # -- scroll effects -----------------------------------------------------

    @staticmethod
    def generate_scroll_effects() -> str:
        return textwrap.dedent("""\
            (function() {
              'use strict';
              window.CT = window.CT || {};

              var debounceTimer = null;
              var scrollCallbacks = [];

              function onScroll() {
                if (debounceTimer) return;
                debounceTimer = setTimeout(function() {
                  debounceTimer = null;
                  var scrollY = window.pageYOffset || document.documentElement.scrollTop;
                  try {
                    scrollCallbacks.forEach(function(cb) { cb(scrollY); });
                    var reveals = document.querySelectorAll('[data-reveal]');
                    var wh = window.innerHeight;
                    reveals.forEach(function(el) {
                      var rect = el.getBoundingClientRect();
                      if (rect.top < wh * 0.85) {
                        el.classList.add('revealed');
                      }
                    });
                  } catch (err) {
                    console.error('[ScrollEffects] error:', err);
                  }
                }, 16);
              }

              window.addEventListener('scroll', onScroll, { passive: true });

              window.CT.ScrollEffects = {
                onScroll: function(fn) { scrollCallbacks.push(fn); },
                scrollTo: function(sel) {
                  var el = document.querySelector(sel);
                  if (el) el.scrollIntoView({ behavior: 'smooth' });
                }
              };
            })();
        """)

    # -- keyboard nav -------------------------------------------------------

    @staticmethod
    def generate_keyboard_nav() -> str:
        return textwrap.dedent("""\
            (function() {
              'use strict';
              window.CT = window.CT || {};

              var shortcuts = {};

              document.addEventListener('keydown', function(e) {
                try {
                  var key = [];
                  if (e.ctrlKey || e.metaKey) key.push('mod');
                  if (e.shiftKey) key.push('shift');
                  if (e.altKey) key.push('alt');
                  key.push(e.key.toLowerCase());
                  var combo = key.join('+');
                  if (shortcuts[combo]) {
                    e.preventDefault();
                    shortcuts[combo](e);
                  }
                  if (e.key === 'Tab') {
                    document.body.classList.add('keyboard-nav');
                  }
                } catch (err) {
                  console.error('[KeyboardNav] error:', err);
                }
              });

              document.addEventListener('mousedown', function() {
                document.body.classList.remove('keyboard-nav');
              });

              window.CT.KeyboardNav = {
                register: function(combo, fn) { shortcuts[combo] = fn; },
                unregister: function(combo) { delete shortcuts[combo]; }
              };
            })();
        """)

    # -- theme toggle -------------------------------------------------------

    @staticmethod
    def generate_theme_toggle() -> str:
        return textwrap.dedent("""\
            (function() {
              'use strict';
              window.CT = window.CT || {};

              var STORAGE_KEY = 'ct_theme';
              var current = localStorage.getItem(STORAGE_KEY) || 'dark';

              function apply(theme) {
                try {
                  current = theme;
                  document.documentElement.setAttribute('data-theme', theme);
                  localStorage.setItem(STORAGE_KEY, theme);
                  window.dispatchEvent(new CustomEvent('ct:theme-change', {
                    detail: { theme: theme }
                  }));
                } catch (err) {
                  console.error('[ThemeToggle] error:', err);
                }
              }

              function toggle() {
                apply(current === 'dark' ? 'light' : 'dark');
              }

              apply(current);

              window.CT.Theme = {
                toggle: toggle,
                set: apply,
                current: function() { return current; }
              };
            })();
        """)

    # -- error boundaries ---------------------------------------------------

    @staticmethod
    def generate_error_boundaries() -> str:
        return textwrap.dedent("""\
            (function() {
              'use strict';
              window.CT = window.CT || {};

              var errorLog = [];

              window.addEventListener('error', function(e) {
                var entry = {
                  message: e.message,
                  source: e.filename,
                  line: e.lineno,
                  col: e.colno,
                  time: Date.now()
                };
                errorLog.push(entry);
                console.error('[ErrorBoundary] uncaught:', entry);
                if (window.CT.Toast) {
                  window.CT.Toast.show('An error occurred. See console.', 'error', 5000);
                }
              });

              window.addEventListener('unhandledrejection', function(e) {
                var entry = {
                  message: String(e.reason),
                  source: 'promise',
                  time: Date.now()
                };
                errorLog.push(entry);
                console.error('[ErrorBoundary] unhandled rejection:', entry);
              });

              function safeCall(fn, context) {
                context = context || 'unknown';
                return function() {
                  try {
                    return fn.apply(this, arguments);
                  } catch (err) {
                    console.error('[ErrorBoundary][' + context + ']', err);
                    errorLog.push({
                      message: err.message,
                      source: context,
                      time: Date.now()
                    });
                    return undefined;
                  }
                };
              }

              window.CT.ErrorBoundary = {
                safeCall: safeCall,
                errors: function() { return errorLog.slice(); },
                clear: function() { errorLog = []; }
              };
            })();
        """)

    # -- form handler -------------------------------------------------------

    @staticmethod
    def generate_form_handler() -> str:
        return textwrap.dedent("""\
            (function() {
              'use strict';
              window.CT = window.CT || {};

              document.addEventListener('submit', function(e) {
                try {
                  var form = e.target;
                  if (!form.hasAttribute('data-ct-form')) return;
                  e.preventDefault();
                  var data = {};
                  var elements = form.elements;
                  for (var i = 0; i < elements.length; i++) {
                    var el = elements[i];
                    if (!el.name) continue;
                    if (el.type === 'checkbox') {
                      data[el.name] = el.checked;
                    } else if (el.type === 'radio') {
                      if (el.checked) data[el.name] = el.value;
                    } else {
                      data[el.name] = el.value;
                    }
                  }
                  var handler = form.getAttribute('data-ct-form');
                  window.dispatchEvent(new CustomEvent('ct:form-submit', {
                    detail: { formId: handler, data: data }
                  }));
                  if (window.CT.Store) {
                    window.CT.Store.set('form_' + handler, data);
                  }
                } catch (err) {
                  console.error('[FormHandler] error:', err);
                  if (window.CT.Toast) {
                    window.CT.Toast.show('Form submission error', 'error');
                  }
                }
              });

              window.CT.Form = {
                getData: function(formSel) {
                  var form = document.querySelector(formSel);
                  if (!form) return null;
                  var data = {};
                  var elements = form.elements;
                  for (var i = 0; i < elements.length; i++) {
                    var el = elements[i];
                    if (el.name) data[el.name] = el.value;
                  }
                  return data;
                },
                reset: function(formSel) {
                  var form = document.querySelector(formSel);
                  if (form) form.reset();
                }
              };
            })();
        """)

    # -- mobile nav ---------------------------------------------------------

    @staticmethod
    def generate_mobile_nav() -> str:
        return textwrap.dedent("""\
            (function() {
              'use strict';
              window.CT = window.CT || {};

              var isOpen = false;

              function toggle() {
                try {
                  isOpen = !isOpen;
                  var nav = document.querySelector('[data-mobile-nav]');
                  var overlay = document.querySelector('[data-nav-overlay]');
                  if (nav) {
                    nav.classList.toggle('open', isOpen);
                    nav.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
                  }
                  if (overlay) {
                    overlay.style.display = isOpen ? 'block' : 'none';
                  }
                  document.body.style.overflow = isOpen ? 'hidden' : '';
                } catch (err) {
                  console.error('[MobileNav] error:', err);
                }
              }

              document.addEventListener('click', function(e) {
                if (e.target.closest('[data-action="toggle-nav"]')) toggle();
                if (e.target.closest('[data-nav-overlay]') && isOpen) toggle();
                if (e.target.closest('[data-mobile-nav] a') && isOpen) toggle();
              });

              window.CT.MobileNav = {
                toggle: toggle,
                isOpen: function() { return isOpen; }
              };
            })();
        """)


# ---------------------------------------------------------------------------
# 7. JSTheoryGenerator — main entry point
# ---------------------------------------------------------------------------

# Maps concept keywords to module kinds that should be loaded.
_CONCEPT_MODULE_MAP: dict[str, list[JSModuleKind]] = {
    "game": [
        JSModuleKind.CANVAS_MANAGER,
        JSModuleKind.ANIMATION_CONTROLLER,
        JSModuleKind.KEYBOARD_NAV,
        JSModuleKind.AUDIO_MANAGER,
    ],
    "art": [
        JSModuleKind.CANVAS_MANAGER,
        JSModuleKind.ANIMATION_CONTROLLER,
        JSModuleKind.SCROLL_EFFECTS,
    ],
    "music": [JSModuleKind.AUDIO_MANAGER, JSModuleKind.KEYBOARD_NAV],
    "form": [JSModuleKind.FORM_HANDLER, JSModuleKind.DATA_PERSISTENCE],
    "dashboard": [
        JSModuleKind.TAB_CONTROLLER,
        JSModuleKind.SCROLL_EFFECTS,
        JSModuleKind.DATA_PERSISTENCE,
    ],
    "editor": [
        JSModuleKind.UNDO_REDO,
        JSModuleKind.KEYBOARD_NAV,
        JSModuleKind.DRAG_DROP,
    ],
    "search": [JSModuleKind.SEARCH, JSModuleKind.DEBOUNCE_THROTTLE],
    "notification": [JSModuleKind.NOTIFICATION, JSModuleKind.TOAST_SYSTEM],
    "settings": [JSModuleKind.THEME_TOGGLE, JSModuleKind.FORM_HANDLER],
    "gallery": [
        JSModuleKind.LAZY_LOADER,
        JSModuleKind.MODAL_SYSTEM,
        JSModuleKind.SCROLL_EFFECTS,
    ],
    "tutorial": [
        JSModuleKind.ACCORDION_CONTROLLER,
        JSModuleKind.TAB_CONTROLLER,
    ],
}

# Canonical specs for every module kind.
_MODULE_SPECS: dict[JSModuleKind, JSModuleSpec] = {
    JSModuleKind.ERROR_BOUNDARY: JSModuleSpec(
        kind=JSModuleKind.ERROR_BOUNDARY,
        namespace_key="ErrorBoundary",
        description="Global error capture and safe-call wrapper",
        exports=["ErrorBoundary"],
        depends_on=[],
        is_required=True,
    ),
    JSModuleKind.LOADING_DRIVER: JSModuleSpec(
        kind=JSModuleKind.LOADING_DRIVER,
        namespace_key="LoadingDriver",
        description="Animated loading-screen sequencer",
        exports=["LoadingDriver"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
        is_required=True,
    ),
    JSModuleKind.STATE_STORE: JSModuleSpec(
        kind=JSModuleKind.STATE_STORE,
        namespace_key="Store",
        description="Centralized observable state with localStorage persistence",
        exports=["Store"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
        is_required=True,
    ),
    JSModuleKind.EVENT_DELEGATOR: JSModuleSpec(
        kind=JSModuleKind.EVENT_DELEGATOR,
        namespace_key="EventDelegator",
        description="Global-to-local event delegation via data-action",
        exports=["EventDelegator"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
        is_required=True,
    ),
    JSModuleKind.ROUTER: JSModuleSpec(
        kind=JSModuleKind.ROUTER,
        namespace_key="Router",
        description="Hash-based SPA router",
        exports=["Router"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY, JSModuleKind.EVENT_DELEGATOR],
        is_required=True,
    ),
    JSModuleKind.TOAST_SYSTEM: JSModuleSpec(
        kind=JSModuleKind.TOAST_SYSTEM,
        namespace_key="Toast",
        description="Non-blocking toast notifications",
        exports=["Toast"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.MODAL_SYSTEM: JSModuleSpec(
        kind=JSModuleKind.MODAL_SYSTEM,
        namespace_key="Modal",
        description="Accessible modal dialogs",
        exports=["Modal"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.TAB_CONTROLLER: JSModuleSpec(
        kind=JSModuleKind.TAB_CONTROLLER,
        namespace_key="Tabs",
        description="Tab group switching",
        exports=["Tabs"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.ACCORDION_CONTROLLER: JSModuleSpec(
        kind=JSModuleKind.ACCORDION_CONTROLLER,
        namespace_key="Accordion",
        description="Collapsible accordion panels",
        exports=["Accordion"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.FORM_HANDLER: JSModuleSpec(
        kind=JSModuleKind.FORM_HANDLER,
        namespace_key="Form",
        description="Form serialisation, validation, and submission",
        exports=["Form"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY, JSModuleKind.STATE_STORE],
    ),
    JSModuleKind.KEYBOARD_NAV: JSModuleSpec(
        kind=JSModuleKind.KEYBOARD_NAV,
        namespace_key="KeyboardNav",
        description="Keyboard shortcut registration and focus management",
        exports=["KeyboardNav"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.SCROLL_EFFECTS: JSModuleSpec(
        kind=JSModuleKind.SCROLL_EFFECTS,
        namespace_key="ScrollEffects",
        description="Debounced scroll listeners and reveal animations",
        exports=["ScrollEffects"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.THEME_TOGGLE: JSModuleSpec(
        kind=JSModuleKind.THEME_TOGGLE,
        namespace_key="Theme",
        description="Dark / light theme switching with persistence",
        exports=["Theme"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.ANIMATION_CONTROLLER: JSModuleSpec(
        kind=JSModuleKind.ANIMATION_CONTROLLER,
        namespace_key="Animation",
        description="requestAnimationFrame loop manager",
        exports=["Animation"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.CANVAS_MANAGER: JSModuleSpec(
        kind=JSModuleKind.CANVAS_MANAGER,
        namespace_key="Canvas",
        description="Canvas creation and resize handling",
        exports=["Canvas"],
        depends_on=[
            JSModuleKind.ERROR_BOUNDARY,
            JSModuleKind.ANIMATION_CONTROLLER,
        ],
    ),
    JSModuleKind.AUDIO_MANAGER: JSModuleSpec(
        kind=JSModuleKind.AUDIO_MANAGER,
        namespace_key="Audio",
        description="Web Audio API manager with sound pooling",
        exports=["Audio"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.DATA_PERSISTENCE: JSModuleSpec(
        kind=JSModuleKind.DATA_PERSISTENCE,
        namespace_key="Persistence",
        description="IndexedDB / localStorage abstraction layer",
        exports=["Persistence"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY, JSModuleKind.STATE_STORE],
    ),
    JSModuleKind.ACCESSIBILITY_HELPERS: JSModuleSpec(
        kind=JSModuleKind.ACCESSIBILITY_HELPERS,
        namespace_key="A11y",
        description="ARIA live-region, focus-trap, skip-link helpers",
        exports=["A11y"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.DRAG_DROP: JSModuleSpec(
        kind=JSModuleKind.DRAG_DROP,
        namespace_key="DragDrop",
        description="Drag-and-drop interaction",
        exports=["DragDrop"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.UNDO_REDO: JSModuleSpec(
        kind=JSModuleKind.UNDO_REDO,
        namespace_key="UndoRedo",
        description="Command-pattern undo/redo stack",
        exports=["UndoRedo"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY, JSModuleKind.STATE_STORE],
    ),
    JSModuleKind.SEARCH: JSModuleSpec(
        kind=JSModuleKind.SEARCH,
        namespace_key="Search",
        description="Client-side fuzzy search",
        exports=["Search"],
        depends_on=[
            JSModuleKind.ERROR_BOUNDARY,
            JSModuleKind.DEBOUNCE_THROTTLE,
        ],
    ),
    JSModuleKind.NOTIFICATION: JSModuleSpec(
        kind=JSModuleKind.NOTIFICATION,
        namespace_key="Notification",
        description="In-app notification centre",
        exports=["Notification"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY, JSModuleKind.TOAST_SYSTEM],
    ),
    JSModuleKind.LAZY_LOADER: JSModuleSpec(
        kind=JSModuleKind.LAZY_LOADER,
        namespace_key="LazyLoader",
        description="IntersectionObserver-based lazy loading",
        exports=["LazyLoader"],
        depends_on=[JSModuleKind.ERROR_BOUNDARY],
    ),
    JSModuleKind.DEBOUNCE_THROTTLE: JSModuleSpec(
        kind=JSModuleKind.DEBOUNCE_THROTTLE,
        namespace_key="Timing",
        description="Debounce and throttle utilities",
        exports=["Timing"],
        depends_on=[],
    ),
}


class JSTheoryGenerator:
    """Derive and generate all base JS from the theory of the application.

    Given the app name, its views, and the high-level concept keywords,
    the generator determines which modules are needed, derives loading
    phases, routes, and event actions, then emits each module in
    dependency order.
    """

    def __init__(
        self, app_name: str, views: list[str], concepts: list[str]
    ) -> None:
        self.app_name = app_name
        self.views = views
        self.concepts = [c.lower() for c in concepts]
        self._gen = JSModuleGenerator()

    # -- module resolution --------------------------------------------------

    def required_modules(self) -> list[JSModuleSpec]:
        """Determine which modules are needed from views and concepts."""
        needed: set[JSModuleKind] = set()

        # Required core modules.
        for kind, spec in _MODULE_SPECS.items():
            if spec.is_required:
                needed.add(kind)

        # Concept-driven modules.
        for concept in self.concepts:
            for keyword, kinds in _CONCEPT_MODULE_MAP.items():
                if keyword in concept:
                    needed.update(kinds)

        # Toast is cheap and universally useful.
        needed.add(JSModuleKind.TOAST_SYSTEM)
        needed.add(JSModuleKind.THEME_TOGGLE)
        needed.add(JSModuleKind.SCROLL_EFFECTS)

        # Resolve transitive dependencies.
        resolved: set[JSModuleKind] = set()
        queue = list(needed)
        while queue:
            kind = queue.pop()
            if kind in resolved:
                continue
            resolved.add(kind)
            spec = _MODULE_SPECS.get(kind)
            if spec:
                for dep in spec.depends_on:
                    if dep not in resolved:
                        queue.append(dep)

        # Topological sort by dependency depth.
        return self._topo_sort(resolved)

    def _topo_sort(self, kinds: set[JSModuleKind]) -> list[JSModuleSpec]:
        """Sort module kinds so dependencies come first."""
        order: list[JSModuleKind] = []
        visited: set[JSModuleKind] = set()

        def visit(k: JSModuleKind) -> None:
            if k in visited:
                return
            visited.add(k)
            spec = _MODULE_SPECS.get(k)
            if spec:
                for dep in spec.depends_on:
                    if dep in kinds:
                        visit(dep)
            order.append(k)

        for k in kinds:
            visit(k)

        return [_MODULE_SPECS[k] for k in order if k in _MODULE_SPECS]

    # -- derivation helpers -------------------------------------------------

    def _derive_loading_phases(
        self, modules: list[JSModuleSpec]
    ) -> list[dict[str, Any]]:
        """Derive loading phases from the module list."""
        phases: list[dict[str, Any]] = []
        for spec in modules:
            if spec.kind in (
                JSModuleKind.ERROR_BOUNDARY,
                JSModuleKind.DEBOUNCE_THROTTLE,
            ):
                continue
            phases.append(
                {
                    "id": spec.kind.value,
                    "label": spec.description.split("—")[0]
                    .split(",")[0]
                    .strip()[:40],
                    "weight": 2 if spec.is_required else 1,
                }
            )
        return phases

    def _derive_routes(self) -> list[dict[str, str]]:
        """Derive routes from view identifiers."""
        routes: list[dict[str, str]] = []
        for view in self.views:
            slug = view.lower().replace(" ", "-").replace("_", "-")
            path = "/" if slug in ("home", "landing", "index", "main") else f"/{slug}"
            view_id = f"view-{slug}"
            routes.append({"path": path, "view_id": view_id})
        # Ensure we always have a root route.
        if not any(r["path"] == "/" for r in routes):
            routes.insert(0, {"path": "/", "view_id": "view-home"})
        return routes

    def _derive_actions(self) -> list[dict[str, str]]:
        """Derive event actions from views and concepts."""
        actions: list[dict[str, str]] = [
            {"action": "navigate", "handler": "window.CT.Router.navigate(btn.getAttribute('data-target'))"},
            {"action": "close-modal", "handler": "window.CT.Modal.close(btn.closest('.modal--open').id)"},
            {"action": "toggle-theme", "handler": "window.CT.Theme.toggle()"},
            {"action": "toggle-nav", "handler": "window.CT.MobileNav.toggle()"},
        ]
        return actions

    def _derive_view_ids(self) -> list[str]:
        routes = self._derive_routes()
        return [r["view_id"] for r in routes]

    def _derive_entities(self) -> list[str]:
        """Derive store entity names from concepts."""
        entities = ["settings", "ui_state"]
        for concept in self.concepts:
            safe = re.sub(r"[^a-z0-9_]", "_", concept.lower()).strip("_")
            if safe and safe not in entities:
                entities.append(safe)
        return entities

    # -- generation ---------------------------------------------------------

    def generate_base_js(self) -> str:
        """Generate ALL base JS from theory.

        Steps:
        1. Determine required modules from concepts.
        2. Derive loading phases from modules.
        3. Derive routes from views.
        4. Derive event actions from view types.
        5. Generate each module in dependency order.
        6. Wrap in a document-ready handler.
        """
        modules = self.required_modules()
        phases = self._derive_loading_phases(modules)
        routes = self._derive_routes()
        actions = self._derive_actions()
        view_ids = self._derive_view_ids()
        entities = self._derive_entities()
        ns = re.sub(r"[^a-z0-9]", "_", self.app_name.lower()).strip("_")

        parts: list[str] = [
            f"/* ═══ Generated by jugeo-webapp JSTheoryGenerator for {self.app_name} ═══ */",
        ]

        gen_map: dict[JSModuleKind, str] = {}
        for spec in modules:
            js = self._generate_module(spec, phases, routes, actions, view_ids, ns, entities)
            if js:
                gen_map[spec.kind] = js

        for spec in modules:
            js = gen_map.get(spec.kind)
            if js:
                parts.append(f"\n/* ─── {spec.description} ─── */")
                parts.append(js)

        # Final initialisation inside DOMContentLoaded.
        parts.append(textwrap.dedent(f"""\
            document.addEventListener('DOMContentLoaded', function() {{
              'use strict';
              try {{
                if (window.CT && window.CT.LoadingDriver) {{
                  /* loading driver auto-starts */
                }}
                console.log('{self.app_name} initialized via JSTheoryGenerator');
              }} catch (err) {{
                console.error('[Init] error:', err);
              }}
            }});
        """))

        return "\n".join(parts)

    def _generate_module(
        self,
        spec: JSModuleSpec,
        phases: list[dict[str, Any]],
        routes: list[dict[str, str]],
        actions: list[dict[str, str]],
        view_ids: list[str],
        ns: str,
        entities: list[str],
    ) -> str | None:
        """Dispatch generation to the appropriate JSModuleGenerator method."""
        dispatch: dict[JSModuleKind, Any] = {
            JSModuleKind.ERROR_BOUNDARY: lambda: self._gen.generate_error_boundaries(),
            JSModuleKind.LOADING_DRIVER: lambda: self._gen.generate_loading_driver(phases),
            JSModuleKind.STATE_STORE: lambda: self._gen.generate_store(ns, entities),
            JSModuleKind.EVENT_DELEGATOR: lambda: self._gen.generate_event_delegator(actions),
            JSModuleKind.ROUTER: lambda: self._gen.generate_router(routes),
            JSModuleKind.TOAST_SYSTEM: lambda: self._gen.generate_toast_system(),
            JSModuleKind.MODAL_SYSTEM: lambda: self._gen.generate_modal_system(),
            JSModuleKind.TAB_CONTROLLER: lambda: self._gen.generate_tab_controller(),
            JSModuleKind.ACCORDION_CONTROLLER: lambda: self._gen.generate_accordion_controller(),
            JSModuleKind.FORM_HANDLER: lambda: self._gen.generate_form_handler(),
            JSModuleKind.KEYBOARD_NAV: lambda: self._gen.generate_keyboard_nav(),
            JSModuleKind.SCROLL_EFFECTS: lambda: self._gen.generate_scroll_effects(),
            JSModuleKind.THEME_TOGGLE: lambda: self._gen.generate_theme_toggle(),
        }
        factory = dispatch.get(spec.kind)
        return factory() if factory else None


# ---------------------------------------------------------------------------
# 8. JSDescentChecker — verify JS satisfies obligations
# ---------------------------------------------------------------------------

class JSDescentChecker:
    """Verify generated JS satisfies encapsulation, delegation, state,
    and error-handling obligations."""

    def check(
        self,
        js: str,
        obligations: list[
            JSEncapsulationObligation
            | JSEventDelegationObligation
            | JSStateObligation
        ]
        | None = None,
    ) -> list[str]:
        """Return a list of violation descriptions (empty ⇒ pass)."""
        if obligations is None:
            obligations = [
                JSEncapsulationObligation(),
                JSEventDelegationObligation(),
                JSStateObligation(),
            ]
        violations: list[str] = []
        for ob in obligations:
            if isinstance(ob, JSEncapsulationObligation):
                violations.extend(self._check_encapsulation(js, ob))
            elif isinstance(ob, JSEventDelegationObligation):
                violations.extend(self._check_delegation(js, ob))
            elif isinstance(ob, JSStateObligation):
                violations.extend(self._check_state(js, ob))
        return violations

    # -- encapsulation ------------------------------------------------------

    @staticmethod
    def _check_encapsulation(
        js: str, ob: JSEncapsulationObligation
    ) -> list[str]:
        violations: list[str] = []
        if ob.iife_wrapped:
            iife_count = len(re.findall(r"\(function\s*\(\)\s*\{", js))
            if iife_count == 0:
                violations.append("No IIFE wrappers found")
        if ob.no_global_pollution:
            # Anything assigned to window.* that isn't window.CT
            bad_globals = re.findall(
                r"window\.(?!CT\b|addEventListener|dispatchEvent|"
                r"location|pageYOffset|innerHeight|innerWidth|"
                r"requestAnimationFrame|setTimeout|setInterval|"
                r"getComputedStyle|matchMedia|scrollTo|"
                r"AudioContext|webkitAudioContext)(\w+)\s*=",
                js,
            )
            if bad_globals:
                violations.append(
                    f"Global pollution: window.{', window.'.join(set(bad_globals))}"
                )
        if ob.ct_namespace_only:
            if "window.CT" not in js:
                violations.append("CT namespace not initialised")
        return violations

    # -- delegation ---------------------------------------------------------

    @staticmethod
    def _check_delegation(
        js: str, ob: JSEventDelegationObligation
    ) -> list[str]:
        violations: list[str] = []
        if ob.use_event_delegation:
            if "document.addEventListener" not in js:
                violations.append(
                    "No document-level event delegation found"
                )
        if ob.data_action_attributes:
            if "data-action" not in js:
                violations.append("No data-action attribute usage found")
        if ob.error_boundaries:
            if "try" not in js or "catch" not in js:
                violations.append("No error boundaries (try/catch) found")
        return violations

    # -- state --------------------------------------------------------------

    @staticmethod
    def _check_state(js: str, ob: JSStateObligation) -> list[str]:
        violations: list[str] = []
        if ob.centralized_store:
            if "CT.Store" not in js and "window.CT.Store" not in js:
                violations.append("No centralized store (CT.Store) found")
        if ob.serializable_to_local_storage:
            if "localStorage" not in js:
                violations.append("No localStorage persistence found")
        return violations


# ---------------------------------------------------------------------------
# 9. AgentPromptDeriver — derive agent prompts from theory
# ---------------------------------------------------------------------------

class AgentPromptDeriver:
    """Derive structured prompts for AI code-generation agents.

    Replaces hard-coded prompt strings with theory-grounded derivations
    that carry encapsulation, DOM-interaction, and error-handling
    obligations into the prompt.
    """

    _ENCAP = JSEncapsulationObligation()
    _EVENT = JSEventDelegationObligation()

    def js_prompt_for_concept(
        self,
        concept_name: str,
        concept_description: str,
        app_context: str,
        target_lines: int = 300,
    ) -> str:
        """Prompt for generating a concept's JS via an AI agent."""
        encap_rules = "\n".join(f"- {c}" for c in self._ENCAP.describe())
        event_rules = "\n".join(f"- {c}" for c in self._EVENT.describe())
        return textwrap.dedent(f"""\
            Generate a COMPLETE, self-contained JavaScript module for a web application.
            Output the code directly as text — do NOT create or write any files.

            APPLICATION CONTEXT: {app_context}

            CONCEPT: {concept_name}
            WHAT TO BUILD: {concept_description}

            ENCAPSULATION REQUIREMENTS:
            {encap_rules}
            - Wrap everything in an IIFE: (function() {{ 'use strict'; ... }})();
            - Export classes/functions to window.CT namespace: window.CT = window.CT || {{}};

            DOM INTERACTION REQUIREMENTS:
            - All DOM interaction must create elements programmatically
            - Do NOT use getElementById/querySelector for elements you have not created yourself
            - Use the CT namespace for inter-module communication, not DOM queries
            - If you need a canvas, create it: const canvas = document.createElement('canvas');

            EVENT HANDLING REQUIREMENTS:
            {event_rules}

            ERROR HANDLING REQUIREMENTS:
            - Wrap handlers in try/catch
            - Log errors to console with module prefix
            - Never let an error crash the entire application

            GENERAL REQUIREMENTS:
            - Output {target_lines}+ lines of real, working JavaScript
            - NO external dependencies — pure vanilla JS
            - NO placeholders, NO "TODO", NO stubs — every function must have a real implementation
            - Include detailed JSDoc comments on all classes and public methods
            - Code must be immediately runnable in a browser

            Output ONLY the JavaScript code. No explanations, no markdown fences, no file operations.
        """)

    def css_prompt_for_concept(
        self,
        concept_name: str,
        concept_description: str,
        target_lines: int = 200,
    ) -> str:
        """Prompt for generating a concept's CSS via an AI agent."""
        return textwrap.dedent(f"""\
            Generate COMPLETE CSS for a web application component.
            Output the code directly as text — do NOT create or write any files.

            CONCEPT: {concept_name}
            WHAT TO STYLE: {concept_description}

            REQUIREMENTS:
            - Output {target_lines}+ lines of real, working CSS
            - Use CSS custom properties (variables) where appropriate
            - Mobile-first responsive design with appropriate breakpoints
            - Support dark and light themes via [data-theme="dark"] / [data-theme="light"]
            - Smooth transitions and animations where appropriate
            - Accessible focus styles for keyboard navigation
            - NO external dependencies — pure CSS
            - NO placeholders, NO "TODO" — every rule must be real
            - Use BEM-like class naming conventions
            - Ensure sufficient colour contrast for accessibility

            Output ONLY the CSS code. No explanations, no markdown fences.
        """)

    def html_prompt_for_shell(
        self,
        app_title: str,
        tagline: str,
        view_specs: list[dict[str, str]],
        loading_phases: list[str],
        target_lines: int = 300,
    ) -> str:
        """Prompt for generating the HTML shell via an AI agent.

        *view_specs* is a list of ``{id, label, description}`` dicts.
        The prompt is NOT hard-coded to any specific application.
        """
        view_bullets = "\n".join(
            f"  - id=\"{v['id']}\": {v.get('label', v['id'])} — {v.get('description', '')}"
            for v in view_specs
        )
        phase_bullets = "\n".join(f"  - {p}" for p in loading_phases)
        return textwrap.dedent(f"""\
            Generate a COMPLETE single-page HTML shell for a web application.
            Output the code directly as text — do NOT create or write any files.

            APPLICATION: {app_title}
            TAGLINE: {tagline}

            VIEWS (each is a <section> that gets shown/hidden by the router):
            {view_bullets}

            LOADING PHASES (displayed on the loading screen):
            {phase_bullets}

            REQUIREMENTS:
            - Output {target_lines}+ lines of real, working HTML
            - Include a full loading screen with progress bar and phase steps
            - Use semantic HTML5 elements (header, nav, main, section, footer)
            - Include ARIA attributes for accessibility
            - All interactive elements must have data-action attributes
            - Navigation links use data-action="navigate" data-target="/path"
            - Include meta viewport, charset, and theme-color tags
            - Link to style.css and script.js (no inline styles or scripts)
            - Include a mobile navigation toggle
            - Each view section starts hidden except the first
            - Include a theme toggle button with data-action="toggle-theme"

            Output ONLY the HTML code. No explanations, no markdown fences.
        """)
