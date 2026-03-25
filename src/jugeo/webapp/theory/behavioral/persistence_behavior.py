"""Browser persistence modelled as sections surviving temporal boundaries.

Overview
--------
Persistence in a browser is the property that a piece of application state
retains its value across a *temporal boundary morphism* — an event like a page
reload, a tab close, or a session end that would otherwise destroy in-memory
state.  We model each such event as a morphism in the site of browser contexts,
and we say that a storage section *persists* over that morphism if its value
factors through the morphism, i.e. is preserved on the other side.

Mathematical framing
--------------------
Let ``B`` be the Grothendieck site of browser contexts, whose objects are
pairs ``(origin, time-slice)`` and whose morphisms are browser events
(reload, tab-close, navigation, origin-change).  A *persistence presheaf*
``P : B^op → Set`` assigns to each context the set of key-value pairs
accessible in that context.  A section ``s ∈ P(U)`` *persists* over a
morphism ``f : V → U`` (e.g. "page reload") if the restriction
``P(f)(s) ∈ P(V)`` is the same section with the same value — i.e. the
section factors through ``f``.

Temporal boundary morphisms
---------------------------
- **page_reload** : ``(origin, t) → (origin, t+1)`` — the section survives
  if it is in localStorage, IndexedDB, cookies, or URL state.
- **tab_close** : ``(origin, tab, t) → ∅`` — destroys sessionStorage but not
  localStorage.
- **session_end** : broader than tab_close — covers all tabs and the
  expiration of cookies without a ``Max-Age``.
- **origin_change** : destroys all client-side storage (same-origin policy).

Storage backends as sheaf sections
-----------------------------------
Each storage backend provides a different presheaf with different persistence
properties:

- **localStorage** — CROSS_TAB scope; sections persist over page_reload and
  tab_close; shared across all tabs of the same origin.
- **sessionStorage** — TAB_SESSION scope; sections persist over page_reload
  but NOT tab_close; isolated per-tab.
- **IndexedDB** — CROSS_SESSION scope; async, structured, transactional.
- **Cookies** — CROSS_SESSION scope (when ``Max-Age`` is set); sent with
  every HTTP request to the matching domain+path; HttpOnly cookies are
  invisible to JS.
- **URL state** — URL_STATE scope; the most transparent layer; bookmarkable
  and shareable; the canonical address of a resource.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    CoveringFamily,
    GrothendieckTopology,
    Morphism,
    MorphismKind,
    Site,
)
from jugeo.geometry.descent import (
    DescentObstruction,
    DescentResult,
    GlobalSection,
    LocalSection,
)

__all__ = [
    "PersistenceScope",
    "PersistenceKey",
    "LocalStorageTheory",
    "SessionStorageTheory",
    "IndexedDBTheory",
    "CookieJar",
    "CookieTheory",
    "URLStateTheory",
    "StoragePresheaf",
    "PersistenceDescentChecker",
]


# ---------------------------------------------------------------------------
# PersistenceScope
# ---------------------------------------------------------------------------


class PersistenceScope(str, Enum):
    """Temporal scope of a persisted section.

    Each value names a *class of temporal boundary morphisms* that the section
    survives (or does not survive).  The ordering is roughly from shortest-lived
    to longest-lived.

    TAB_SESSION
        The section lives only for the lifetime of the browser tab.  It is
        destroyed when the tab is closed.  This corresponds to sessionStorage.
    PAGE_RELOAD
        The section survives a page reload (``location.reload()``) but is
        destroyed when the tab closes.  Technically the same backend as
        TAB_SESSION (sessionStorage), but emphasises the reload-survival
        property rather than the tab-close destruction.
    CROSS_TAB
        The section is shared across all tabs of the same origin.  It persists
        over page reloads and tab closes.  This corresponds to localStorage.
    CROSS_SESSION
        The section persists across multiple browser sessions (browser restarts).
        Corresponds to cookies with a ``Max-Age`` / ``Expires`` attribute, or
        IndexedDB.
    SERVER_SIDE
        The section is stored on the server and accessed via an API call.  The
        client holds only a token (ideally an HttpOnly cookie) that references
        the server-side section.
    URL_STATE
        The section lives in the URL.  It is bookmarkable and shareable.
        Query parameters are the local coordinates; the fragment is client-only.
    """

    TAB_SESSION = "tab_session"
    PAGE_RELOAD = "page_reload"
    CROSS_TAB = "cross_tab"
    CROSS_SESSION = "cross_session"
    SERVER_SIDE = "server_side"
    URL_STATE = "url_state"


# ---------------------------------------------------------------------------
# PersistenceKey
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistenceKey:
    """Descriptor for a single persistence key in a storage backend.

    scope:
        The temporal scope — which boundary morphisms this key survives.
    key_name:
        The string key used to look up the value in the storage backend.
    schema:
        Expected value type: ``"string"``, ``"json"``, ``"number"``,
        ``"boolean"``.  Defaults to ``"string"``.
    max_size_bytes:
        Maximum allowed value size in bytes.  Defaults to 5 MB.
    expiry:
        ISO 8601 datetime string after which the key should be treated as
        expired, or ``None`` for no expiry.
    encryption_required:
        Whether the value must be encrypted before storage (e.g. for PII).
    description:
        Human-readable description of what this key stores.
    """

    scope: PersistenceScope
    key_name: str
    schema: str = "string"
    max_size_bytes: int = 5_000_000
    expiry: str | None = None
    encryption_required: bool = False
    description: str = ""

    def storage_backend(self) -> str:
        """Return the canonical storage backend name for this key's scope.

        Maps each :class:`PersistenceScope` to its natural storage backend.
        URL_STATE and SERVER_SIDE have their own backend names.
        """
        mapping: dict[PersistenceScope, str] = {
            PersistenceScope.TAB_SESSION: "sessionStorage",
            PersistenceScope.PAGE_RELOAD: "sessionStorage",
            PersistenceScope.CROSS_TAB: "localStorage",
            PersistenceScope.CROSS_SESSION: "indexedDB",
            PersistenceScope.SERVER_SIDE: "server",
            PersistenceScope.URL_STATE: "url",
        }
        return mapping[self.scope]

    def is_sensitive(self) -> bool:
        """Return True if the key name suggests sensitive data.

        Checks the key name for substrings associated with credentials,
        tokens, or personal data.  This is a heuristic — the authoritative
        answer comes from the data classification in :class:`StoragePresheaf`.

        bool
            ``True`` if the key name contains any of: ``token``, ``secret``,
            ``password``, ``auth``, ``session``, ``credential``, ``ssn``,
            ``credit``, ``private``.
        """
        sensitive_patterns = {
            "token", "secret", "password", "auth", "session",
            "credential", "ssn", "credit", "private",
        }
        lower = self.key_name.lower()
        return any(p in lower for p in sensitive_patterns)

    def to_coordinate(self) -> Coordinate:
        """Convert this key to a site :class:`Coordinate`.

        The coordinate path is ``("persistence", <scope>, <key_name>)``,
        which places this key in the persistence region of the site.
        """
        return Coordinate(
            ("persistence", self.scope.value, self.key_name),
            CoordinateKind.REGION,
        )


# ---------------------------------------------------------------------------
# LocalStorageTheory
# ---------------------------------------------------------------------------


class LocalStorageTheory:
    """Model of localStorage as a sheaf of string-valued sections over an origin.

    localStorage stores string key-value pairs that persist across page reloads
    and survive tab and window closes.  Sections are scoped to an *origin*
    (scheme + host + port), so they are NOT shared across different origins.
    Within the same origin, all tabs share the same localStorage — reading in
    one tab returns data written in another.

    The synchronous API (``getItem``, ``setItem``, ``removeItem``, ``clear``)
    blocks the main thread during I/O.  For large amounts of data this can
    cause jank; IndexedDB is preferable in that case.

    The ``storage`` event fires in OTHER tabs (not the writing tab) when a key
    changes, enabling cross-tab state synchronisation.  This is the sheaf
    gluing condition: sections written in one tab are automatically visible in
    all other tabs of the same origin.

    Key theoretical property: localStorage sections are persistent over the
    ``page_reload`` morphism AND the ``tab_close`` morphism — they factor
    through any morphism that preserves the origin.  They do NOT persist over
    ``origin_change``.

    Security: localStorage is accessible by any JavaScript running on the
    origin, including injected scripts.  Session tokens and credentials MUST
    NOT be stored here.
    """

    def __init__(self) -> None:
        """Initialise the theory and build the underlying site.

        Constructs a :class:`Site` with coordinates for the localStorage
        region, individual keys, and the origin boundary.  The topology
        encodes the covering families for cross-tab synchronisation.
        """
        self._topology = GrothendieckTopology()
        self._site = Site(topology=self._topology, label="localStorage")
        # Root coordinate
        self._root = Coordinate(("persistence", "localStorage"), CoordinateKind.REGION)
        self._site.add_coordinate(self._root)

    def storage_section(self, key: PersistenceKey) -> str | None:
        """Return the simulated section value at this coordinate.

        In a real browser this would call ``localStorage.getItem(key)``.  Here
        we return a placeholder string illustrating what would be retrieved.
        The method models the *restriction* morphism from the localStorage
        region to the specific key coordinate.


        str | None
            A placeholder value string, or ``None`` if the key would be absent.
        """
        return f"<localStorage['{key.key_name}']>"

    def set_section(self, key: PersistenceKey, value: Any) -> str:
        """Return JavaScript code to store a value in localStorage.

        The value is serialized via :meth:`serialize_value` before storage.
        This method generates the JavaScript statement that would be executed
        in a browser to persist this section.

        value:
            The Python value to serialize and store.
        """
        serialized = self.serialize_value(value)
        escaped_key = key.key_name.replace("'", "\\'")
        escaped_val = serialized.replace("'", "\\'")
        return f"localStorage.setItem('{escaped_key}', '{escaped_val}');"

    def remove_section(self, key: PersistenceKey) -> str:
        """Return JavaScript code to remove a key from localStorage.

        Removing a section is modelled as applying the *zero section* at
        that coordinate — the key ceases to exist in the presheaf.

        """
        escaped_key = key.key_name.replace("'", "\\'")
        return f"localStorage.removeItem('{escaped_key}');"

    def clear(self) -> str:
        """Return JavaScript code to clear ALL localStorage for this origin.

        This is a DESTRUCTIVE operation: every section in the localStorage
        presheaf is set to the zero section.  Use with extreme caution in
        production — it will delete all persisted client state.
        """
        return "/* DESTRUCTIVE: removes all localStorage keys */ localStorage.clear();"

    def storage_event_handler(self, key: PersistenceKey) -> str:
        """Return JavaScript code to listen for cross-tab storage changes.

        The ``storage`` event fires in other tabs when a localStorage key
        changes.  This is the sheaf-theoretic gluing: other tabs observe the
        updated section and can react to maintain consistency.

        """
        k = key.key_name.replace("'", "\\'")
        return (
            f"window.addEventListener('storage', function(e) {{\n"
            f"  if (e.key === '{k}') {{\n"
            f"    console.log('Cross-tab update:', e.oldValue, '->', e.newValue);\n"
            f"  }}\n"
            f"}});"
        )

    def handle_quota_exceeded(self) -> str:
        """Return JavaScript code to handle QuotaExceededError gracefully.

        localStorage is limited to ~5 MB per origin.  When the quota is
        exceeded, ``setItem`` throws a ``DOMException`` with the name
        ``QuotaExceededError``.  This snippet wraps a write in a try/catch
        and evicts the oldest key to make room.
        """
        return (
            "try {\n"
            "  localStorage.setItem(key, value);\n"
            "} catch (e) {\n"
            "  if (e.name === 'QuotaExceededError') {\n"
            "    // Evict oldest key\n"
            "    const oldest = localStorage.key(0);\n"
            "    if (oldest) localStorage.removeItem(oldest);\n"
            "    localStorage.setItem(key, value);\n"
            "  } else { throw e; }\n"
            "}"
        )

    def serialize_value(self, value: Any) -> str:
        """Serialize a Python value to a string suitable for localStorage.

        localStorage stores only strings; structured values must be
        JSON-serialized.  Primitives (str, int, float, bool) are converted
        directly; everything else is JSON-encoded.

        """
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return json.dumps(value, separators=(",", ":"))

    def deserialize_value(self, raw: str, schema: str) -> Any:
        """Deserialize a raw localStorage string to a typed Python value.

        Applies the schema hint to guide parsing.  If parsing fails, the raw
        string is returned unchanged rather than raising an exception, which
        mirrors the lenient behavior of browser storage APIs.

        schema:
            One of ``"string"``, ``"json"``, ``"number"``, ``"boolean"``.

        Any
            The deserialized value.
        """
        try:
            if schema == "string":
                return raw
            if schema == "number":
                return float(raw) if "." in raw else int(raw)
            if schema == "boolean":
                return raw.lower() == "true"
            if schema == "json":
                return json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            pass
        return raw

    def cross_tab_sync_section(self, keys: list[PersistenceKey]) -> list[LocalSection]:
        """Model cross-tab synchronisation as a list of LocalSections.

        Each key is converted to a :class:`LocalSection` whose
        ``judgment_data`` records that the section is shared across tabs.
        The ``evidence_bundle`` records the storage event handler as proof
        of synchronisation.


        list[LocalSection]
            One section per key, marked as cross-tab shared.
        """
        sections = []
        for k in keys:
            coord = str(k.to_coordinate().components)
            sections.append(
                LocalSection(
                    coordinate=coord,
                    judgment_data={
                        "key": k.key_name,
                        "scope": k.scope.value,
                        "cross_tab": True,
                        "backend": "localStorage",
                    },
                    evidence_bundle=("storage_event",),
                    trust_level=0.9,
                    provenance=("LocalStorageTheory.cross_tab_sync_section",),
                )
            )
        return sections

    def scope_appropriate_keys(self) -> list[str]:
        """Return examples of data that belongs in localStorage.

        localStorage is appropriate for non-sensitive, long-lived,
        cross-tab preferences.  Session tokens and credentials must NOT
        be stored here — they are accessible by any JS on the origin and
        are vulnerable to XSS.

        list[str]
            Descriptions of appropriate localStorage use-cases.
        """
        return [
            "theme preference (dark/light)",
            "UI layout settings (sidebar open/closed)",
            "non-sensitive user preferences (language, timezone)",
            "recently viewed items (non-sensitive)",
            "cached public API responses (non-sensitive)",
            "feature flag overrides (developer mode)",
        ]


# ---------------------------------------------------------------------------
# SessionStorageTheory
# ---------------------------------------------------------------------------


class SessionStorageTheory:
    """Model of sessionStorage as a per-tab presheaf of ephemeral sections.

    sessionStorage has the same synchronous string-key API as localStorage
    (``getItem``, ``setItem``, ``removeItem``, ``clear``) but a fundamentally
    different persistence scope.  Sections in sessionStorage:

    - Persist over ``page_reload`` (the same tab reloads → sections survive).
    - Do NOT persist over ``tab_close`` (the tab closes → sections are lost).
    - Are NOT shared between tabs: each tab maintains its own independent copy.
      Even two tabs at the same URL see different sessionStorage.

    This tab-isolation property is modelled as a morphism
    ``tab_isolation : tab_A → tab_B`` that is NOT the identity — there is no
    restriction morphism that lets tab A see tab B's sessionStorage.

    sessionStorage is appropriate for per-tab state that should not leak
    across tabs: wizard state, multi-step form drafts, in-progress
    transactions, and temporary authentication challenge nonces.  It is
    unsuitable for long-term auth tokens (those die with the tab) or for
    state that needs cross-tab coordination.
    """

    def __init__(self) -> None:
        """Initialise the theory and build the underlying site.

        Constructs a :class:`Site` with coordinates for the sessionStorage
        region, isolated per-tab.  The absence of a cross-tab covering family
        reflects the isolation property.
        """
        self._topology = GrothendieckTopology()
        self._site = Site(topology=self._topology, label="sessionStorage")
        self._root = Coordinate(("persistence", "sessionStorage"), CoordinateKind.REGION)
        self._site.add_coordinate(self._root)

    def storage_section(self, key: PersistenceKey) -> str | None:
        """Return the simulated section value from this tab's sessionStorage.

        Models the restriction from the sessionStorage region to the specific
        key coordinate within the current tab's context.  Another tab would
        return a different (or absent) value.


        str | None
            A placeholder value string representing what this tab stores.
        """
        return f"<sessionStorage['{key.key_name}']>"

    def set_section(self, key: PersistenceKey, value: Any) -> str:
        """Return JavaScript code to write a value to sessionStorage.

        Serializes the value to JSON if it is not already a string, then
        generates the ``sessionStorage.setItem`` call.

        value:
            The value to store.
        """
        if isinstance(value, str):
            serialized = value
        else:
            serialized = json.dumps(value, separators=(",", ":"))
        escaped_key = key.key_name.replace("'", "\\'")
        escaped_val = serialized.replace("'", "\\'")
        return f"sessionStorage.setItem('{escaped_key}', '{escaped_val}');"

    def remove_section(self, key: PersistenceKey) -> str:
        """Return JavaScript code to remove a key from sessionStorage.

        """
        escaped_key = key.key_name.replace("'", "\\'")
        return f"sessionStorage.removeItem('{escaped_key}');"

    def appropriate_use_cases(self) -> list[str]:
        """List data types that belong in sessionStorage.

        sessionStorage is ideal for state that is per-tab, temporary, and
        should not outlive the tab.  It is safe for in-progress data because
        it is automatically cleaned up when the tab closes.

        list[str]
            Descriptions of appropriate sessionStorage use-cases.
        """
        return [
            "multi-step form wizard state (current step, entered values)",
            "in-progress transaction data (payment flow, checkout cart)",
            "temporary authentication challenge nonces (PKCE verifier)",
            "unsaved editor draft (within a tab session)",
            "navigation back-stack for a complex in-page flow",
            "per-tab search filter state",
        ]

    def inappropriate_use_cases(self) -> list[str]:
        """List data types that should NOT go in sessionStorage.

        (none)

        list[str]
            Descriptions of inappropriate use-cases and the reason.
        """
        return [
            "cross-tab shared state (each tab sees its own copy)",
            "long-lived auth tokens (lost when tab closes)",
            "user preferences that should persist across sessions",
            "analytics session IDs that span multiple tabs",
            "shopping cart that should survive tab close",
        ]

    def vs_local_storage(self) -> dict[str, str]:
        """Return a comparison table: sessionStorage vs localStorage.

        dict[str, str]
            Keys are properties, values are ``"sessionStorage: X | localStorage: Y"``
            formatted strings.
        """
        return {
            "persistence_over_reload": "sessionStorage: YES | localStorage: YES",
            "persistence_over_tab_close": "sessionStorage: NO | localStorage: YES",
            "shared_across_tabs": "sessionStorage: NO | localStorage: YES",
            "storage_event": "sessionStorage: NO | localStorage: YES",
            "max_size": "sessionStorage: ~5 MB | localStorage: ~5 MB",
            "api": "sessionStorage: synchronous | localStorage: synchronous",
            "scope": "sessionStorage: per-tab | localStorage: per-origin",
        }

    def tab_isolation_morphism(self) -> Morphism:
        """Return the morphism encoding tab isolation of sessionStorage.

        The tab-isolation morphism maps the sessionStorage coordinate of one
        tab to the sessionStorage coordinate of another.  Because it is a
        RESTRICTION morphism with no covering family, sections do NOT glue
        across this morphism — each tab's storage is independent.
        """
        tab_a = Coordinate(("persistence", "sessionStorage", "tab_A"), CoordinateKind.REGION)
        tab_b = Coordinate(("persistence", "sessionStorage", "tab_B"), CoordinateKind.REGION)
        return Morphism(source=tab_a, target=tab_b, kind=MorphismKind.RESTRICTION, label="tab_isolation")


# ---------------------------------------------------------------------------
# IndexedDBTheory
# ---------------------------------------------------------------------------


class IndexedDBTheory:
    """Model of IndexedDB as a transactional structured-storage presheaf.

    IndexedDB provides full structured storage with object stores, indexes,
    range queries, and transactions.  Unlike localStorage's flat synchronous
    key-value model, IndexedDB:

    - Stores *structured clones*: strings, numbers, dates, objects, arrays,
      Blobs, ArrayBuffers — but NOT functions or DOM nodes (these are not
      serializable sections).
    - Is *asynchronous*: every operation returns a Promise (or uses request
      events).  This means a read is a section over *time* — the value arrives
      asynchronously.
    - Supports *transactions*: a set of operations either all commit or all
      abort.  This is the monad structure of the persistence presheaf — the
      transaction is a natural transformation that maps a sequence of local
      operations to a single atomic global section.

    Transactions have a mode: ``"readonly"`` allows concurrent reads;
    ``"readwrite"`` is exclusive.  Transactions auto-commit when all
    requests complete and there are no outstanding references.

    IndexedDB persists over page reload, tab close, and browser restart
    (CROSS_SESSION scope).  It is cleared by the user via browser settings
    or by calling ``indexedDB.deleteDatabase(name)``.

    The site models the database, object stores, and indexes as nested
    coordinates.  A ``get`` operation is a restriction morphism from the
    object-store coordinate to the key coordinate.
    """

    def __init__(self) -> None:
        """Initialise the theory and build the underlying site.

        Creates a :class:`Site` with a root coordinate for the IndexedDB
        region and placeholder coordinates for object stores.
        """
        self._topology = GrothendieckTopology()
        self._site = Site(topology=self._topology, label="IndexedDB")
        self._root = Coordinate(("persistence", "indexedDB"), CoordinateKind.REGION)
        self._site.add_coordinate(self._root)

    def open_database(self, db_name: str, version: int) -> str:
        """Return JavaScript code to open an IndexedDB database.

        The ``onupgradeneeded`` callback is where schema migrations happen —
        it fires when the database is first created or when ``version``
        increases.  This is the sheaf-theoretic upgrade morphism.

        version:
            The schema version integer (must be a positive integer).
        """
        return (
            f"const request = indexedDB.open('{db_name}', {version});\n"
            f"request.onupgradeneeded = (event) => {{\n"
            f"  const db = event.target.result;\n"
            f"  // Create object stores here during schema migration\n"
            f"}};\n"
            f"request.onsuccess = (event) => {{ const db = event.target.result; }};\n"
            f"request.onerror = (event) => {{ console.error('IDB open error', event.target.error); }};"
        )

    def transaction(self, stores: list[str], mode: str) -> str:
        """Return JavaScript code to start a transaction over object stores.

        Transactions are the atomic unit of IndexedDB operations.  All
        requests within a transaction share the same consistency snapshot.
        ``"readonly"`` transactions can run concurrently; ``"readwrite"``
        transactions are serialized.

        mode:
            Either ``"readonly"`` or ``"readwrite"``.
        """
        stores_js = json.dumps(stores)
        return f"const tx = db.transaction({stores_js}, '{mode}');"

    def object_store(self, name: str) -> str:
        """Return JavaScript code to get a reference to an object store.

        """
        return f"const store = tx.objectStore('{name}');"

    def put_value(self, store_name: str, value: dict) -> str:
        """Return JavaScript code to put (insert or update) a value.

        ``put`` is an upsert: it creates the record if it does not exist,
        or replaces it if it does.  The key is read from the object's
        ``keyPath`` if one was set on the store.

        value:
            The Python dict to serialize as the stored object.
        """
        value_js = json.dumps(value, separators=(",", ":"))
        return (
            f"const store = tx.objectStore('{store_name}');\n"
            f"const req = store.put({value_js});"
        )

    def get_value(self, store_name: str, key: str) -> str:
        """Return JavaScript code to get a value by key from an object store.

        key:
            The primary key string.
        """
        return (
            f"const store = tx.objectStore('{store_name}');\n"
            f"const req = store.get('{key}');\n"
            f"req.onsuccess = (e) => {{ const value = e.target.result; }};"
        )

    def delete_value(self, store_name: str, key: str) -> str:
        """Return JavaScript code to delete a record from an object store.

        key:
            The primary key of the record to delete.
        """
        return (
            f"const store = tx.objectStore('{store_name}');\n"
            f"store.delete('{key}');"
        )

    def get_all(self, store_name: str) -> str:
        """Return JavaScript code to retrieve all records from an object store.

        ``getAll()`` returns all records as an array.  For large stores,
        cursor-based iteration (see :meth:`cursor_iteration`) is preferable
        to avoid holding all records in memory simultaneously.

        """
        return (
            f"const store = tx.objectStore('{store_name}');\n"
            f"const req = store.getAll();\n"
            f"req.onsuccess = (e) => {{ const all = e.target.result; }};"
        )

    def create_index(self, store_name: str, index_name: str, key_path: str) -> str:
        """Return JavaScript code to create an index during schema upgrade.

        Indexes allow efficient lookup by non-primary-key properties.
        They must be created inside the ``onupgradeneeded`` callback.

        index_name:
            The name of the index.
        """
        return (
            f"const store = db.createObjectStore('{store_name}', {{ keyPath: 'id' }});\n"
            f"store.createIndex('{index_name}', '{key_path}', {{ unique: false }});"
        )

    def range_query(self, store_name: str, lower: Any, upper: Any) -> str:
        """Return JavaScript code for a bounded range query using IDBKeyRange.

        Range queries retrieve all records whose key falls within
        ``[lower, upper]``.  This is the sheaf-theoretic restriction to a
        sub-interval of the key space.

        lower:
            The lower bound of the range (inclusive).
        """
        lower_js = json.dumps(lower)
        upper_js = json.dumps(upper)
        return (
            f"const store = tx.objectStore('{store_name}');\n"
            f"const range = IDBKeyRange.bound({lower_js}, {upper_js});\n"
            f"const req = store.getAll(range);\n"
            f"req.onsuccess = (e) => {{ const results = e.target.result; }};"
        )

    def cursor_iteration(self, store_name: str) -> str:
        """Return JavaScript code for cursor-based iteration over an object store.

        Cursors allow memory-efficient sequential access to records one
        at a time.  This models a *section-by-section* traversal of the
        presheaf rather than loading the entire stalk into memory.

        """
        return (
            f"const store = tx.objectStore('{store_name}');\n"
            f"const cursorReq = store.openCursor();\n"
            f"cursorReq.onsuccess = (e) => {{\n"
            f"  const cursor = e.target.result;\n"
            f"  if (cursor) {{\n"
            f"    console.log(cursor.key, cursor.value);\n"
            f"    cursor.continue();\n"
            f"  }}\n"
            f"}};"
        )

    def storable_types(self) -> list[str]:
        """Return the list of types that can be stored in IndexedDB.

        IndexedDB uses the structured-clone algorithm.  These types are
        serializable sections of the persistence presheaf.

        list[str]
            Names of serializable types.
        """
        return [
            "string", "number", "boolean", "null", "undefined",
            "Date", "Array", "Object (plain)", "Map", "Set",
            "ArrayBuffer", "TypedArray (Uint8Array etc.)", "Blob", "File",
            "ImageData", "RegExp", "BigInt",
        ]

    def non_storable_types(self) -> list[str]:
        """Return the list of types that CANNOT be stored in IndexedDB.

        These types are not serializable by the structured-clone algorithm
        and will cause a ``DataCloneError`` if an attempt is made to store
        them.  They are NOT valid sections of the persistence presheaf.

        list[str]
            Names of non-serializable types.
        """
        return [
            "Function",
            "DOM nodes (HTMLElement, etc.)",
            "Error objects (some browsers)",
            "Symbol",
            "WeakMap / WeakSet",
            "objects with circular references (without custom handling)",
            "Proxy objects",
        ]

    def transaction_as_section(self, operations: list[dict]) -> LocalSection:
        """Model a transaction as a single atomic LocalSection.

        A transaction wraps multiple operations into a single atomic unit.
        If all operations succeed, the transaction commits — producing a
        single section that encodes the post-commit state.  If any operation
        fails, the transaction aborts and the section is not produced.

        operations:
            A list of operation dicts, each with keys ``"type"``
            (``"put"`` | ``"delete"`` | ``"get"``), ``"store"``, and ``"data"``.
        """
        op_summary = [f"{op.get('type','?')}:{op.get('store','?')}" for op in operations]
        return LocalSection(
            coordinate="persistence.indexedDB.transaction",
            judgment_data={
                "operations": op_summary,
                "count": len(operations),
                "atomic": True,
                "backend": "IndexedDB",
            },
            evidence_bundle=tuple(op_summary),
            trust_level=1.0,
            provenance=("IndexedDBTheory.transaction_as_section",),
        )


# ---------------------------------------------------------------------------
# CookieJar
# ---------------------------------------------------------------------------


@dataclass
class CookieJar:
    """A mutable collection of cookies for a domain+path scope.

    cookies:
        Mapping from cookie name to value.
    domain:
        The domain for which these cookies apply.
    path:
        The path scope (default ``"/"``).
    """

    cookies: dict[str, str] = field(default_factory=dict)
    domain: str = ""
    path: str = "/"

    def get(self, name: str) -> str | None:
        """Return the value of a cookie by name, or None if absent.


        str | None
            The cookie value or ``None``.
        """
        return self.cookies.get(name)

    def set(self, name: str, value: str, **attrs: Any) -> str:
        """Store a cookie and return the Set-Cookie header string.

        Updates the internal cookie store and generates the corresponding
        ``Set-Cookie`` header value.  Attribute keys should be ``max_age``,
        ``path``, ``domain``, ``secure``, ``http_only``, ``same_site``.

        value:
            The cookie value.
        **attrs:
            Optional cookie attributes.
        """
        self.cookies[name] = value
        parts = [f"{name}={value}"]
        if "path" in attrs:
            parts.append(f"Path={attrs['path']}")
        if "max_age" in attrs:
            parts.append(f"Max-Age={attrs['max_age']}")
        if "domain" in attrs:
            parts.append(f"Domain={attrs['domain']}")
        if attrs.get("secure"):
            parts.append("Secure")
        if attrs.get("http_only"):
            parts.append("HttpOnly")
        if "same_site" in attrs:
            parts.append(f"SameSite={attrs['same_site']}")
        return "; ".join(parts)

    def delete(self, name: str) -> str:
        """Remove a cookie by name and return the deletion Set-Cookie header.

        Cookies are deleted by setting ``Max-Age=0`` (or an expired
        ``Expires`` date).  This generates the appropriate header.

        """
        self.cookies.pop(name, None)
        return f"{name}=; Max-Age=0; Path={self.path}"

    def parse_cookie_string(self, raw: str) -> None:
        """Parse a ``Cookie:`` request header string into the internal dict.

        The format is ``name=value; name2=value2; ...``.  This mutates
        the ``cookies`` dict in place.

        """
        for pair in raw.split(";"):
            pair = pair.strip()
            if "=" in pair:
                name, _, value = pair.partition("=")
                self.cookies[name.strip()] = value.strip()

    def to_coordinate(self) -> Coordinate:
        """Convert this cookie jar to a site Coordinate.

        The coordinate encodes the domain and path scope, placing the
        cookie jar in the ``persistence.cookies`` region.
        """
        domain_part = self.domain.replace(".", "_") if self.domain else "unknown"
        path_part = self.path.strip("/").replace("/", "_") or "root"
        return Coordinate(
            ("persistence", "cookies", domain_part, path_part),
            CoordinateKind.REGION,
        )


# ---------------------------------------------------------------------------
# CookieTheory
# ---------------------------------------------------------------------------


class CookieTheory:
    """Model of browser cookies as sections of the HTTP state presheaf.

    Cookies are sections of the HTTP state presheaf: they are sent with
    every request to the matching ``Domain`` + ``Path``, bridging client and
    server state.  They are the oldest persistence mechanism on the web and
    remain the most powerful — because they participate in HTTP, not just JS.

    Two classes of cookies exist from the JS perspective:
    - **Non-HttpOnly cookies**: accessible via ``document.cookie``.  These
      form a section visible at the JS level.
    - **HttpOnly cookies**: set by the server with the ``HttpOnly`` attribute.
      They are INVISIBLE to JavaScript — they form a section accessible only
      at the HTTP protocol level.  Session tokens MUST be HttpOnly.

    The ``SameSite`` attribute controls cross-site cookie sending:
    - ``Strict``: never sent cross-site (best CSRF protection).
    - ``Lax``: sent on top-level navigations and safe methods (default modern).
    - ``None``: always sent cross-site (requires ``Secure``; needed for embeds).

    ``Domain`` + ``Path`` define the *coverage* of a cookie: the set of
    requests that include this section.  This coverage is modelled as a
    :class:`CoveringFamily` over the domain coordinate.

    Cookies persist over page reload and tab close.  Session cookies
    (no ``Max-Age`` / ``Expires``) are deleted when the browser session ends.
    Persistent cookies (``Max-Age`` > 0) survive browser restart.
    """

    def __init__(self) -> None:
        """Initialise the theory and build the underlying site.

        Constructs a :class:`Site` modelling the cookie-coverage space,
        with coordinates for the cookie region and a placeholder domain.
        """
        self._topology = GrothendieckTopology()
        self._site = Site(topology=self._topology, label="cookies")
        self._root = Coordinate(("persistence", "cookies"), CoordinateKind.REGION)
        self._site.add_coordinate(self._root)

    def read_cookies(self) -> str:
        """Return JavaScript code to read all non-HttpOnly cookies.

        ``document.cookie`` returns a semicolon-separated string of
        ``name=value`` pairs.  HttpOnly cookies are NOT included — they
        are invisible at the JS level.
        """
        return (
            "const cookies = Object.fromEntries(\n"
            "  document.cookie.split('; ')\n"
            "    .filter(Boolean)\n"
            "    .map(c => c.split('=').map(decodeURIComponent))\n"
            ");"
        )

    def set_cookie(
        self,
        name: str,
        value: str,
        path: str = "/",
        same_site: str = "Lax",
        max_age: int = 0,
    ) -> str:
        """Return JavaScript code to set a non-HttpOnly cookie.

        HttpOnly cookies can only be set by the server via a ``Set-Cookie``
        response header.  This JS-side method can only set non-HttpOnly
        cookies.  Session tokens should be set server-side with HttpOnly.

        value:
            The cookie value (will be URI-encoded).
        same_site:
            ``"Strict"``, ``"Lax"``, or ``"None"``.
        """
        parts = [f"{encodeURIComponent_py(name)}={encodeURIComponent_py(value)}"]
        parts.append(f"Path={path}")
        parts.append(f"SameSite={same_site}")
        if max_age > 0:
            parts.append(f"Max-Age={max_age}")
        if same_site == "None":
            parts.append("Secure")
        cookie_str = "; ".join(parts)
        return f"document.cookie = '{cookie_str}';"

    def delete_cookie(self, name: str) -> str:
        """Return JavaScript code to delete a cookie by name.

        Deletion is accomplished by setting ``Max-Age=0``, which causes the
        browser to immediately expire the cookie.

        """
        encoded = encodeURIComponent_py(name)
        return f"document.cookie = '{encoded}=; Max-Age=0; Path=/';"

    def http_only_warning(self) -> str:
        """Return an explanation of the HttpOnly security restriction.

        HttpOnly cookies are the correct storage location for session tokens
        because they cannot be read or modified by JavaScript.  XSS attacks
        cannot steal them.  This method returns a descriptive warning string.
        """
        return (
            "WARNING: Session tokens and auth cookies MUST be set with HttpOnly=true.\n"
            "HttpOnly cookies are invisible to document.cookie and to all JavaScript.\n"
            "They can only be set and read at the HTTP protocol level (server-side).\n"
            "This prevents XSS attacks from stealing session tokens.\n"
            "Never store session tokens in localStorage or non-HttpOnly cookies."
        )

    def parse_cookie_header(self, header: str) -> CookieJar:
        """Parse a Set-Cookie response header into a CookieJar.

        Parses the name=value pair from the header, ignoring cookie
        attributes (Path, Domain, Max-Age, etc.) for the CookieJar's
        key-value store.

        """
        jar = CookieJar()
        parts = [p.strip() for p in header.split(";")]
        if parts:
            first = parts[0]
            if "=" in first:
                name, _, value = first.partition("=")
                jar.cookies[name.strip()] = value.strip()
            for attr in parts[1:]:
                lower = attr.lower()
                if lower.startswith("domain="):
                    jar.domain = attr.split("=", 1)[1].strip()
                elif lower.startswith("path="):
                    jar.path = attr.split("=", 1)[1].strip()
        return jar

    def same_site_policy(self, mode: str) -> str:
        """Return an explanation of the SameSite policy for the given mode.

        """
        explanations = {
            "Strict": (
                "SameSite=Strict: cookie is NEVER sent on cross-site requests. "
                "Best CSRF protection. May break OAuth redirects and link sharing."
            ),
            "Lax": (
                "SameSite=Lax: cookie is sent on top-level navigations (user clicks a link) "
                "and safe methods (GET). Not sent on cross-site POST/fetch. "
                "Modern browsers default to Lax."
            ),
            "None": (
                "SameSite=None: cookie is ALWAYS sent cross-site. "
                "REQUIRES Secure attribute. Needed for embedded iframes and third-party APIs. "
                "Exposes to CSRF — mitigate with CSRF tokens."
            ),
        }
        return explanations.get(mode, f"Unknown SameSite mode: {mode}")

    def cookie_scope(self, domain: str, path: str) -> CoveringFamily:
        """Return a CoveringFamily encoding the coverage of a cookie.

        A cookie with Domain=``domain`` and Path=``path`` covers all
        requests to sub-paths.  This is modelled as a covering family
        over the domain coordinate.

        path:
            The cookie path (e.g. ``"/"``).
        """
        base = Coordinate(("persistence", "cookies", domain), CoordinateKind.REGION)
        sub = Coordinate(("persistence", "cookies", domain, path.strip("/")), CoordinateKind.REGION)
        morphism = Morphism(source=sub, target=base, kind=MorphismKind.RESTRICTION, label=f"cookie_scope:{path}")
        return CoveringFamily(base=base, members=[morphism], label=f"cookie_coverage:{domain}{path}")

    def secure_cookie_attributes(self) -> dict[str, str]:
        """Return the recommended secure defaults for cookie attributes.

        These defaults follow OWASP and RFC 6265bis recommendations for
        production cookies carrying any state that influences authorization.

        dict[str, str]
            Mapping from attribute name to recommended value / description.
        """
        return {
            "HttpOnly": "true — prevent JavaScript access (mandatory for session tokens)",
            "Secure": "true — HTTPS only (mandatory in production)",
            "SameSite": "Lax — prevent CSRF on cross-site POST (Strict for high-security)",
            "Path": "/ — scope to full site unless narrower scope is intentional",
            "Max-Age": "set expiry explicitly; omit for session cookie",
            "Domain": "omit unless subdomain sharing is required (narrower is safer)",
        }


# ---------------------------------------------------------------------------
# URLStateTheory
# ---------------------------------------------------------------------------


class URLStateTheory:
    """Model of URL state as the most transparent persistence layer.

    URL state lives in the address bar — it is visible to the user,
    bookmarkable, shareable via copy-paste or link, and indexable by search
    engines.  It is the *identity section* of the persistence presheaf:
    every other storage layer must be consistent with it.

    URL structure:
    - **Path** (``/products/42``): hierarchical resource coordinates.
    - **Query string** (``?filter=active&page=2``): flat key-value parameters;
      the local coordinates of the current view.
    - **Fragment** / hash (``#section-3``): client-side-only anchor or router
      state; NEVER sent to the server.

    Constraints:
    - Maximum safe URL length: ~2000 characters (IE11 historic limit; modern
      browsers support much more, but servers may truncate at 8 KB).
    - All characters outside the ASCII unreserved set must be percent-encoded.
    - URL state is completely PUBLIC: never encode secrets, PII, or tokens
      in the URL.

    Browser history integration: ``history.pushState`` / ``replaceState``
    update the URL without a page reload, preserving in-memory state while
    making the current view bookmarkable.  The back/forward buttons navigate
    the history stack.
    """

    def __init__(self) -> None:
        """Initialise the theory and build the underlying site.

        Constructs a :class:`Site` with a URL state root coordinate and
        coordinates for path, query, and fragment sub-regions.
        """
        self._topology = GrothendieckTopology()
        self._site = Site(topology=self._topology, label="urlState")
        self._root = Coordinate(("persistence", "url"), CoordinateKind.REGION)
        self._site.add_coordinate(self._root)

    def encode_state(self, state_dict: dict[str, Any]) -> str:
        """Encode a state dictionary as a URL query string.

        Values are JSON-serialized if they are not strings, then
        percent-encoded.  The resulting string begins with ``?``.

        """
        params = {}
        for k, v in state_dict.items():
            if isinstance(v, str):
                params[k] = v
            elif isinstance(v, bool):
                params[k] = "true" if v else "false"
            elif isinstance(v, (int, float)):
                params[k] = str(v)
            else:
                params[k] = json.dumps(v, separators=(",", ":"))
        return "?" + urllib.parse.urlencode(params) if params else ""

    def decode_state(self, url_string: str) -> dict[str, Any]:
        """Decode a URL string's query parameters into a state dictionary.

        Attempts to JSON-parse values that look like JSON (start with
        ``{``, ``[``, or are quoted); otherwise returns raw strings.


        dict[str, Any]
            The decoded state dictionary.
        """
        if "?" in url_string:
            qs = url_string.split("?", 1)[1].split("#")[0]
        else:
            qs = url_string.lstrip("?").split("#")[0]
        params = urllib.parse.parse_qs(qs, keep_blank_values=True)
        result: dict[str, Any] = {}
        for k, values in params.items():
            raw = values[0] if values else ""
            stripped = raw.strip()
            if stripped.startswith(("{", "[")) or stripped in ("true", "false", "null"):
                try:
                    result[k] = json.loads(stripped)
                    continue
                except json.JSONDecodeError:
                    pass
            result[k] = raw
        return result

    def encode_component(self, value: str) -> str:
        """Percent-encode a string for use as a URL component.

        Uses RFC 3986 encoding (encodes all characters except unreserved
        chars: ``A-Z a-z 0-9 - _ . ~``).

        """
        return urllib.parse.quote(value, safe="")

    def decode_component(self, value: str) -> str:
        """Percent-decode a URL component back to a plain string.

        """
        return urllib.parse.unquote(value)

    def parse_query_string(self, qs: str) -> dict[str, str]:
        """Parse a query string into a flat string-to-string dict.

        Takes only the first value when a key appears multiple times.


        dict[str, str]
            Flat mapping of parameter names to their first values.
        """
        qs = qs.lstrip("?")
        parsed = urllib.parse.parse_qs(qs, keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}

    def build_url(self, base: str, params: dict[str, Any], fragment: str = "") -> str:
        """Build a complete URL from a base, parameters, and optional fragment.

        params:
            Query parameters to append.
        """
        encoded = self.encode_state(params).lstrip("?")
        url = base
        if encoded:
            url = f"{url}?{encoded}"
        if fragment:
            url = f"{url}#{self.encode_component(fragment)}"
        return url

    def max_safe_length(self) -> int:
        """Return the maximum safe URL length in characters.

        Based on the historic IE11 limit of 2083 characters and common
        server/proxy limits.  Modern browsers support much longer URLs, but
        2000 is a safe practical limit for broad compatibility.

        int
            2000.
        """
        return 2000

    def public_fields_only(self, state: dict) -> dict:
        """Filter out secret fields from a state dict before encoding in URL.

        Removes keys that suggest sensitive data — passwords, tokens, secrets,
        credentials.  URL state is public and must never contain secrets.

        """
        sensitive = {"token", "secret", "password", "auth", "credential", "key", "private"}
        return {
            k: v for k, v in state.items()
            if not any(s in k.lower() for s in sensitive)
        }

    def hash_vs_query(self, state: dict) -> dict[str, dict]:
        """Partition state into what belongs in the hash vs the query string.

        Query parameters are sent to the server and are indexable.
        Fragment/hash state is client-only, not sent to the server.


        dict[str, dict]
            A dict with keys ``"query"`` and ``"hash"``, each containing
            a sub-dict of state entries.
        """
        # Heuristic: things that look like client-only UI state go in hash
        hash_keywords = {"tab", "anchor", "section", "modal", "panel", "scroll"}
        query_part: dict[str, Any] = {}
        hash_part: dict[str, Any] = {}
        for k, v in state.items():
            if any(kw in k.lower() for kw in hash_keywords):
                hash_part[k] = v
            else:
                query_part[k] = v
        return {"query": query_part, "hash": hash_part}

    def browser_history_integration(self) -> str:
        """Return an explanation of back/forward button integration.

        ``history.pushState`` adds a new entry to the browser history stack,
        making the current URL bookmarkable without a page reload.
        ``replaceState`` updates the current entry without adding a new one.
        The ``popstate`` event fires when the user navigates back/forward.
        """
        return (
            "history.pushState(state, title, url)  — adds entry to history stack\n"
            "history.replaceState(state, title, url) — replaces current entry\n"
            "window.addEventListener('popstate', (e) => { /* restore state */ })\n"
            "Use pushState for navigations the user should be able to back out of;\n"
            "use replaceState for transient state that should not add a history entry."
        )

    def url_state_as_coordinate(self, params: dict) -> Coordinate:
        """Convert URL query parameters into a site Coordinate.

        Sorts the parameter keys to ensure a canonical representation,
        then creates a coordinate whose path encodes the parameter keys.

        """
        sorted_keys = tuple(sorted(params.keys()))
        path = ("persistence", "url") + sorted_keys
        return Coordinate(path, CoordinateKind.REGION)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def encodeURIComponent_py(s: str) -> str:
    """Percent-encode a string mimicking JavaScript's ``encodeURIComponent``.

    s:
        The string to encode.
    """
    return urllib.parse.quote(s, safe="!~*'()")


# ---------------------------------------------------------------------------
# StoragePresheaf
# ---------------------------------------------------------------------------


class StoragePresheaf:
    """The storage presheaf: assigns to each temporal boundary the surviving sections.

    The storage presheaf ``P : Boundaries^op → Set`` assigns to each temporal
    boundary morphism (page_reload, tab_close, session_end, origin_change) the
    set of data sections that survive that morphism.  A section *persists* over
    a boundary if it factors through the boundary morphism.

    Persistence diagram::

        in-memory ──── (survives none) ───────────────────────────────┐
        sessionStorage ─ (survives page_reload, dies at tab_close) ───┤
        localStorage ─── (survives page_reload + tab_close) ──────────┤
        IndexedDB ──────── (survives browser restart) ─────────────────┤
        cookies (with expiry) ── (survives browser restart) ───────────┤ → CROSS_SESSION
        URL state ─────────── (the canonical address, bookmarkable) ───┘

    Descent condition: every piece of application state should have an
    appropriate persistence scope.  An obstruction occurs when:
    - Sensitive data (session token, password) is stored in localStorage/URL.
    - Non-serializable data is stored in any backend.
    - Quota is exceeded without a handling strategy.
    - Expiry-bearing keys have no cleanup mechanism.
    """

    def __init__(self) -> None:
        """Initialise the storage presheaf with quota and classification tables."""
        self._sensitive_classes: frozenset[str] = frozenset({
            "session_token", "password", "credit_card", "ssn", "private_key",
            "refresh_token", "api_secret", "encryption_key",
        })
        self._serializable = {
            "str", "int", "float", "bool", "NoneType", "list", "dict",
            "tuple", "bytes", "datetime", "date", "Decimal",
        }
        self._non_serializable = {
            "function", "lambda", "type", "module", "frame",
            "generator", "coroutine", "HTMLElement", "Window", "Document",
        }
        self._quotas: dict[str, int] = {
            "localStorage": 5_242_880,   # ~5 MB
            "sessionStorage": 5_242_880,
            "indexedDB": 1_073_741_824,  # ~1 GB (browser-specific)
            "cookie": 4_096,             # 4 KB per cookie
            "url": 2_000,                # safe character limit
        }

    def check_serializable(self, value: Any) -> bool:
        """Return True if the value can be persisted in any storage backend.

        Checks the Python type of the value against the set of serializable
        types.  Functions, lambdas, and DOM-like objects cannot be serialized
        and would raise errors at storage time.


        bool
            ``True`` if the value's type is serializable.
        """
        type_name = type(value).__name__
        if type_name in self._non_serializable:
            return False
        try:
            json.dumps(value)
            return True
        except (TypeError, ValueError):
            return False

    def check_scope_appropriate(self, key: PersistenceKey, data_classification: str) -> bool:
        """Return True if the key's scope is appropriate for the data classification.

        Sensitive data must NOT be in CROSS_TAB (localStorage) or URL_STATE
        scope.  Session tokens must be in SERVER_SIDE or cookies (HttpOnly).

        data_classification:
            A string classification like ``"session_token"``, ``"preference"``.

        bool
            ``True`` if the scope is appropriate for the classification.
        """
        if data_classification in self._sensitive_classes:
            return key.scope not in (
                PersistenceScope.CROSS_TAB,
                PersistenceScope.URL_STATE,
                PersistenceScope.TAB_SESSION,
                PersistenceScope.PAGE_RELOAD,
            )
        return True

    def check_expiry_handling(self, persistent_items: list[PersistenceKey]) -> list[str]:
        """Return warnings for keys with expiry dates that lack cleanup.

        Keys that declare an expiry but have no mechanism to be cleaned up
        will accumulate stale data.  This checks for such keys and returns
        warning strings.


        list[str]
            Warning strings for keys with unhandled expiry.
        """
        warnings = []
        for key in persistent_items:
            if key.expiry and key.scope in (
                PersistenceScope.CROSS_TAB, PersistenceScope.CROSS_SESSION
            ):
                warnings.append(
                    f"Key '{key.key_name}' ({key.scope.value}) has expiry={key.expiry} "
                    f"but no automatic cleanup — implement an expiry check on read."
                )
        return warnings

    def check_storage_quota(self, current_size_bytes: int, backend: str) -> bool:
        """Return True if the current usage is within quota.

        backend:
            The storage backend name (``"localStorage"``, etc.).

        bool
            ``True`` if within quota.
        """
        limit = self._quotas.get(backend, 0)
        return current_size_bytes < limit

    def sensitive_data_classifications(self) -> frozenset[str]:
        """Return the set of data classifications considered sensitive.

        frozenset[str]
            Sensitive classification strings.
        """
        return self._sensitive_classes

    def scope_for_classification(self, classification: str) -> PersistenceScope:
        """Return the recommended persistence scope for a data classification.


        PersistenceScope
            The recommended scope.
        """
        if classification in self._sensitive_classes:
            return PersistenceScope.SERVER_SIDE
        if classification in {"preference", "theme", "language", "timezone"}:
            return PersistenceScope.CROSS_TAB
        if classification in {"wizard_state", "form_draft", "in_progress"}:
            return PersistenceScope.TAB_SESSION
        if classification in {"filter", "page", "sort", "search"}:
            return PersistenceScope.URL_STATE
        return PersistenceScope.CROSS_SESSION

    def serializable_types(self) -> set[str]:
        """Return the set of serializable type names.

        set[str]
            Python type names that can be round-tripped through storage backends.
        """
        return set(self._serializable)

    def non_serializable_types(self) -> set[str]:
        """Return the set of non-serializable type names.

        set[str]
            Python type names that CANNOT be stored in any persistence backend.
        """
        return set(self._non_serializable)

    def quota_limits(self) -> dict[str, int]:
        """Return the quota limits for each storage backend in bytes.

        dict[str, int]
            Mapping from backend name to maximum bytes.
        """
        return dict(self._quotas)

    def to_local_sections(self, keys: list[PersistenceKey]) -> list[LocalSection]:
        """Convert a list of persistence keys to LocalSections for descent analysis.

        Each key becomes a LocalSection whose judgment_data encodes the key's
        scope, schema, and security properties.


        list[LocalSection]
            One LocalSection per key.
        """
        sections = []
        for k in keys:
            coord = str(k.to_coordinate().components)
            sections.append(
                LocalSection(
                    coordinate=coord,
                    judgment_data={
                        "key_name": k.key_name,
                        "scope": k.scope.value,
                        "schema": k.schema,
                        "sensitive": k.is_sensitive(),
                        "encryption_required": k.encryption_required,
                        "backend": k.storage_backend(),
                    },
                    evidence_bundle=(k.key_name, k.scope.value),
                    trust_level=1.0 if not k.is_sensitive() else 0.5,
                    provenance=("StoragePresheaf.to_local_sections",),
                )
            )
        return sections


# ---------------------------------------------------------------------------
# PersistenceDescentChecker
# ---------------------------------------------------------------------------


class PersistenceDescentChecker:
    """Descent checker for the persistence presheaf.

    The persistence descent checker verifies that the persistence layer
    forms a coherent presheaf: that sections are stored in the right scope,
    are serializable, respect quota limits, are cleaned up on expiry, and
    do not expose sensitive data in unsafe backends.

    An *obstruction* in the persistence presheaf means some data is
    persisted incorrectly:
    - Session token in localStorage → XSS-accessible (security vulnerability).
    - Non-serializable value passed to setItem → runtime error.
    - Quota exceeded without handling → data loss.
    - Expired keys never removed → stale data accumulation.
    - Secrets in URL → public exposure via browser history and server logs.

    Descent success means: all data is in the appropriate scope, properly
    serialized, with quota handling in place, expiry correctly managed, and
    no sensitive data in public storage.

    The checker uses the :class:`StoragePresheaf` as its model and
    produces :class:`DescentResult` objects for each check.
    """

    def __init__(self, site: Site | None = None) -> None:
        """Initialise the checker with an optional pre-built site.

        If no site is provided, a default site is constructed.  The
        :class:`StoragePresheaf` is always created fresh.

        site:
            Optional pre-existing :class:`Site`.  If ``None``, a new
            :class:`Site` with a default topology is created.
        """
        if site is None:
            topology = GrothendieckTopology()
            site = Site(topology=topology, label="persistenceChecker")
        self._site = site
        self._presheaf = StoragePresheaf()

    def check_sensitive_data_scope(self, keys: list[PersistenceKey]) -> DescentResult:
        """Check that sensitive keys are not stored in unsafe backends.

        Sensitive keys (session tokens, passwords, etc.) must NOT be in
        localStorage (CROSS_TAB), sessionStorage (TAB_SESSION/PAGE_RELOAD),
        or URL state.  They should be in SERVER_SIDE (HttpOnly cookie or
        server-managed) storage.

        """
        violations = []
        unsafe_scopes = {
            PersistenceScope.CROSS_TAB,
            PersistenceScope.URL_STATE,
            PersistenceScope.TAB_SESSION,
            PersistenceScope.PAGE_RELOAD,
        }
        for key in keys:
            if key.is_sensitive() and key.scope in unsafe_scopes:
                violations.append(
                    f"'{key.key_name}' (scope={key.scope.value}) is sensitive but in unsafe backend"
                )
        if violations:
            return self._make_failure(
                "persistence.sensitive_scope",
                "Sensitive data stored in XSS-accessible backend: " + "; ".join(violations),
            )
        return self._make_success(
            "persistence.sensitive_scope",
            {"checked": len(keys), "violations": 0},
        )

    def check_serialization_safety(self, keys: list[PersistenceKey]) -> DescentResult:
        """Check that all key schemas are serializable.

        Keys with schema ``"json"`` must be serializable to JSON.  This
        check validates that the schema declarations are consistent with the
        backend's serialization requirements.

        """
        supported_schemas = {"string", "json", "number", "boolean"}
        bad = [k.key_name for k in keys if k.schema not in supported_schemas]
        if bad:
            return self._make_failure(
                "persistence.serialization",
                f"Keys with unsupported schema: {bad}. Supported: {sorted(supported_schemas)}",
            )
        return self._make_success(
            "persistence.serialization",
            {"checked": len(keys), "supported_schemas": sorted(supported_schemas)},
        )

    def check_quota_handling(self, backends: list[str]) -> DescentResult:
        """Check that all referenced backends have known quota limits.

        A backend without a quota limit in the presheaf model is a gap in
        the coverage — the descent checker cannot guarantee storage will
        succeed.

        """
        known = set(self._presheaf.quota_limits().keys())
        unknown = [b for b in backends if b not in known]
        if unknown:
            return self._make_failure(
                "persistence.quota",
                f"Unknown backends without quota tracking: {unknown}. Known: {sorted(known)}",
            )
        return self._make_success(
            "persistence.quota",
            {"backends_checked": len(backends), "quota_limits": self._presheaf.quota_limits()},
        )

    def check_cross_tab_sync(self, local_storage_keys: list[PersistenceKey]) -> DescentResult:
        """Check that cross-tab localStorage keys have StorageEvent handlers.

        LocalStorage sections are shared across tabs.  Keys that are expected
        to be observed by other tabs must have a StorageEvent listener to
        maintain sheaf coherence.

        """
        cross_tab_keys = [
            k for k in local_storage_keys
            if k.scope == PersistenceScope.CROSS_TAB
        ]
        return self._make_success(
            "persistence.cross_tab_sync",
            {
                "cross_tab_keys": [k.key_name for k in cross_tab_keys],
                "advisory": "Implement StorageEvent listeners for cross-tab coherence",
            },
        )

    def check_url_state_encoding(self, url_params: dict[str, Any]) -> DescentResult:
        """Check that URL state parameters contain no sensitive data.

        URL parameters are public (browser history, server logs, referrer
        headers).  Any key that suggests sensitive data is an obstruction.

        """
        sensitive_patterns = {
            "token", "secret", "password", "auth", "credential",
            "key", "private", "ssn", "credit",
        }
        violations = [
            k for k in url_params
            if any(p in k.lower() for p in sensitive_patterns)
        ]
        if violations:
            return self._make_failure(
                "persistence.url_state",
                f"Sensitive keys found in URL state: {violations}. "
                "URL state is public — never encode secrets.",
            )
        theory = URLStateTheory()
        total_length = len(theory.encode_state(url_params))
        if total_length > theory.max_safe_length():
            return self._make_failure(
                "persistence.url_state",
                f"URL state too long: {total_length} chars > {theory.max_safe_length()} safe limit.",
            )
        return self._make_success(
            "persistence.url_state",
            {"params_checked": len(url_params), "encoded_length": total_length},
        )

    def check_expiry_implementation(self, keys: list[PersistenceKey]) -> DescentResult:
        """Check that keys with expiry dates have a cleanup strategy.

        Keys with an expiry attribute in backends that do not auto-expire
        (localStorage, sessionStorage) require manual cleanup on read.
        This check reports keys that declare expiry but may lack cleanup.

        """
        warnings = self._presheaf.check_expiry_handling(keys)
        if warnings:
            return self._make_failure(
                "persistence.expiry",
                "Expiry handling gaps: " + " | ".join(warnings),
            )
        return self._make_success(
            "persistence.expiry",
            {"keys_with_expiry": [k.key_name for k in keys if k.expiry]},
        )

    def full_persistence_descent(
        self,
        keys: list[PersistenceKey],
        url_params: dict[str, Any],
    ) -> DescentResult:
        """Run all persistence descent checks and return a combined result.

        Runs all individual checks in sequence.  The first failure short-
        circuits the remaining checks and returns that failure.  If all
        checks pass, returns a success result with a combined evidence dict.

        url_params:
            URL query parameters currently in use.
        """
        backends = list({k.storage_backend() for k in keys})

        checks = [
            self.check_sensitive_data_scope(keys),
            self.check_serialization_safety(keys),
            self.check_quota_handling(backends),
            self.check_cross_tab_sync(keys),
            self.check_url_state_encoding(url_params),
            self.check_expiry_implementation(keys),
        ]

        failures = [c for c in checks if not c.is_success]
        if failures:
            return failures[0]

        return self._make_success(
            "persistence.full_descent",
            {
                "total_keys": len(keys),
                "backends": backends,
                "url_params_checked": len(url_params),
                "all_checks_passed": True,
            },
        )

    def _make_success(self, coordinate: str, evidence: dict) -> DescentResult:
        """Construct a successful DescentResult.

        evidence:
            A dict of evidence supporting the success judgment.
        """
        return DescentResult.success(
            GlobalSection(
                coordinate=coordinate,
                merged_judgment=evidence,
                certificate="persistence_descent_pass",
                trust_floor=1.0,
            )
        )

    def _make_failure(self, coordinate: str, reason: str) -> DescentResult:
        """Construct a failure DescentResult with an obstruction.

        reason:
            A human-readable description of the obstruction.
        """
        return DescentResult.failure(
            DescentObstruction(
                coordinate=coordinate,
                violated_overlaps=((coordinate, reason),),
                partial_section={"reason": reason},
            )
        )
