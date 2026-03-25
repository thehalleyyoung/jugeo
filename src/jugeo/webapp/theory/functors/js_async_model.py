from __future__ import annotations

"""Semantic gap model between Python asyncio and JavaScript Promises/async-await."""

__all__ = [
    "AsyncModelKind",
    "AsyncSemanticGap",
    "ASYNC_GAPS",
    "AsyncPatternTranslation",
    "ASYNC_TRANSLATIONS",
    "AsyncCodeAnalyzer",
]

import re
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# 1. AsyncModelKind
# ---------------------------------------------------------------------------

class AsyncModelKind(str, Enum):
    PYTHON_ASYNCIO = "python_asyncio"
    JS_PROMISE = "js_promise"
    JS_GENERATOR_BASED = "js_generator_based"

    def runtime(self) -> str:
        match self:
            case AsyncModelKind.PYTHON_ASYNCIO:
                return "asyncio event loop"
            case AsyncModelKind.JS_PROMISE:
                return "JS engine event loop"
            case AsyncModelKind.JS_GENERATOR_BASED:
                return "generator protocol"


# ---------------------------------------------------------------------------
# 2. AsyncSemanticGap
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AsyncSemanticGap:
    gap_id: str
    name: str
    python_behavior: str
    js_behavior: str
    example_python: str
    example_js: str
    is_silent_failure: bool
    severity: int  # 1 (low) – 5 (critical)
    safe_pattern: str


# ---------------------------------------------------------------------------
# 3. ASYNC_GAPS
# ---------------------------------------------------------------------------

ASYNC_GAPS: list[AsyncSemanticGap] = [
    AsyncSemanticGap(
        gap_id="promise_is_eager",
        name="Promise executor runs immediately; coroutine is lazy",
        python_behavior=(
            "Calling an async def function returns a coroutine object. No code inside "
            "the function runs until the coroutine is awaited (or scheduled)."
        ),
        js_behavior=(
            "new Promise(executor) calls executor synchronously on construction. "
            "The async function body starts running immediately up to the first await."
        ),
        example_python=(
            "async def fetch(): print('running')\n"
            "coro = fetch()   # nothing printed yet\n"
            "await coro       # 'running' printed now"
        ),
        example_js=(
            "const p = new Promise(resolve => { console.log('running'); resolve(); });\n"
            "// 'running' printed immediately, before any await"
        ),
        is_silent_failure=False,
        severity=4,
        safe_pattern=(
            "In Python, always await or schedule coroutines. "
            "In JS, be aware that async functions start eagerly—don't rely on deferred execution."
        ),
    ),
    AsyncSemanticGap(
        gap_id="unhandled_rejection",
        name="Unhandled Promise rejection is silent by default",
        python_behavior=(
            "An exception raised inside an unawaited or garbage-collected coroutine "
            "emits a RuntimeWarning and the traceback. Unhandled task exceptions are "
            "logged by the event loop."
        ),
        js_behavior=(
            "A rejected Promise that has no .catch() or try/catch around its await "
            "silently disappears in the browser. Node.js emits an 'unhandledRejection' "
            "event but does not crash by default (until Node 15+)."
        ),
        example_python=(
            "async def boom(): raise ValueError('oops')\n"
            "asyncio.run(boom())  # raises ValueError visibly"
        ),
        example_js=(
            "async function boom() { throw new Error('oops'); }\n"
            "boom();  // rejection silently ignored in old environments"
        ),
        is_silent_failure=True,
        severity=5,
        safe_pattern=(
            "Always attach .catch() to every Promise chain or wrap top-level async "
            "calls in try/catch. Register process.on('unhandledRejection', ...) in Node."
        ),
    ),
    AsyncSemanticGap(
        gap_id="cancellation",
        name="Task cancellation mechanism differs fundamentally",
        python_behavior=(
            "asyncio.Task.cancel() injects CancelledError into the coroutine at the "
            "next await point. The coroutine can catch it for cleanup and must re-raise "
            "or the cancellation is suppressed."
        ),
        js_behavior=(
            "Promises have no built-in cancellation. AbortController/AbortSignal can "
            "signal cancellation to fetch() and some Web APIs, but arbitrary async "
            "functions must check the signal manually."
        ),
        example_python=(
            "task = asyncio.create_task(long_op())\n"
            "task.cancel()\n"
            "try:\n"
            "    await task\n"
            "except asyncio.CancelledError:\n"
            "    print('cancelled')"
        ),
        example_js=(
            "const controller = new AbortController();\n"
            "fetch('/api', { signal: controller.signal });\n"
            "controller.abort();  // cancels only if API supports signal"
        ),
        is_silent_failure=False,
        severity=4,
        safe_pattern=(
            "Propagate AbortSignal through your JS async call stack explicitly. "
            "Check signal.aborted at each await point inside long-running functions."
        ),
    ),
    AsyncSemanticGap(
        gap_id="parallel_await",
        name="Sequential awaits vs concurrent execution",
        python_behavior=(
            "await a(); await b() runs a then b sequentially. "
            "asyncio.gather(a(), b()) schedules both concurrently on the event loop."
        ),
        js_behavior=(
            "await a(); await b() is also sequential in JS. "
            "Promise.all([a(), b()]) is idiomatic for concurrent execution, "
            "but because Promises are eager, a() and b() have already started "
            "running before Promise.all is called."
        ),
        example_python=(
            "# Sequential\n"
            "r1 = await fetch_a()\n"
            "r2 = await fetch_b()\n\n"
            "# Concurrent\n"
            "r1, r2 = await asyncio.gather(fetch_a(), fetch_b())"
        ),
        example_js=(
            "// Sequential\n"
            "const r1 = await fetchA();\n"
            "const r2 = await fetchB();\n\n"
            "// Concurrent\n"
            "const [r1, r2] = await Promise.all([fetchA(), fetchB()]);"
        ),
        is_silent_failure=False,
        severity=3,
        safe_pattern=(
            "Use Promise.all / asyncio.gather for independent concurrent operations. "
            "Avoid sequential awaits when tasks don't depend on each other."
        ),
    ),
    AsyncSemanticGap(
        gap_id="async_return_type",
        name="Calling an async function returns different types",
        python_behavior=(
            "Calling an async def returns a coroutine object (not the result). "
            "Awaiting or scheduling the coroutine eventually produces the return value."
        ),
        js_behavior=(
            "Calling an async function immediately returns a Promise (and starts "
            "executing the body). Awaiting the Promise yields the resolved value."
        ),
        example_python=(
            "async def answer(): return 42\n"
            "obj = answer()   # <coroutine object>\n"
            "val = await answer()  # 42"
        ),
        example_js=(
            "async function answer() { return 42; }\n"
            "const p = answer();   // Promise<42> — already running\n"
            "const val = await answer();  // 42"
        ),
        is_silent_failure=False,
        severity=3,
        safe_pattern=(
            "In Python, never forget to await or schedule coroutines. "
            "In JS, store the Promise if you want to await it later, but know it has started."
        ),
    ),
    AsyncSemanticGap(
        gap_id="microtask_macrotask",
        name="Microtask vs macrotask queue ordering",
        python_behavior=(
            "asyncio has a single event loop queue. Callbacks scheduled with "
            "call_soon are processed in FIFO order in the next iteration. "
            "There is no distinct microtask/macrotask split."
        ),
        js_behavior=(
            "JS distinguishes microtasks (Promise.then callbacks, queueMicrotask) "
            "and macrotasks (setTimeout, setInterval, I/O). The microtask queue "
            "drains completely after every task before the next macrotask runs."
        ),
        example_python=(
            "loop.call_soon(lambda: print('A'))\n"
            "loop.call_soon(lambda: print('B'))\n"
            "# Prints A then B deterministically"
        ),
        example_js=(
            "setTimeout(() => console.log('macro'), 0);\n"
            "Promise.resolve().then(() => console.log('micro'));\n"
            "// Prints: micro, then macro"
        ),
        is_silent_failure=False,
        severity=3,
        safe_pattern=(
            "Rely on queueMicrotask for high-priority deferred work in JS. "
            "Don't assume setTimeout(fn, 0) runs before Promise callbacks."
        ),
    ),
    AsyncSemanticGap(
        gap_id="then_vs_await",
        name=".then() chaining has no Python equivalent",
        python_behavior=(
            "Python coroutines are awaited linearly. There is no .then() method on "
            "coroutines or awaitables. Chaining is expressed with sequential awaits "
            "or by composing coroutines."
        ),
        js_behavior=(
            "Every Promise has a .then(onFulfilled, onRejected) method that registers "
            "a microtask callback and returns a new Promise. Chains can be arbitrarily "
            "long: p.then(f1).then(f2).catch(e)."
        ),
        example_python=(
            "result = await step1()\n"
            "result = await step2(result)\n"
            "result = await step3(result)"
        ),
        example_js=(
            "step1()\n"
            "  .then(step2)\n"
            "  .then(step3)\n"
            "  .catch(handleError);"
        ),
        is_silent_failure=False,
        severity=2,
        safe_pattern=(
            "Prefer async/await syntax over .then() chains in JS for readability "
            "and to keep error handling consistent with Python-style try/except."
        ),
    ),
    AsyncSemanticGap(
        gap_id="exception_from_promise",
        name="Forgotten await makes rejection silent",
        python_behavior=(
            "Awaiting a coroutine that raises re-raises the exception at the await "
            "site. A coroutine that is never awaited emits RuntimeWarning."
        ),
        js_behavior=(
            "await rejected_promise throws at the await site. But if you forget the "
            "await, the Promise rejection disappears silently (or fires unhandledRejection)."
        ),
        example_python=(
            "result = await might_fail()  # raises if coroutine raises"
        ),
        example_js=(
            "const result = await mightFail();  // throws if rejected\n"
            "const result = mightFail();         // rejection silently lost!"
        ),
        is_silent_failure=True,
        severity=5,
        safe_pattern=(
            "Use a linter (eslint no-floating-promises) to detect missing awaits. "
            "TypeScript's strict mode helps catch unawaited Promises."
        ),
    ),
    AsyncSemanticGap(
        gap_id="async_generator",
        name="Async iterables and generators differ in protocol",
        python_behavior=(
            "async for uses __aiter__ / __anext__ dunder methods. "
            "async def f(): yield x creates an async generator implementing that protocol."
        ),
        js_behavior=(
            "for await...of uses the async iterable protocol: Symbol.asyncIterator. "
            "async function* f() { yield x; } creates an async generator. "
            "The protocol is prototype-based, not dunder-based."
        ),
        example_python=(
            "async def gen():\n"
            "    for i in range(3):\n"
            "        await asyncio.sleep(0)\n"
            "        yield i\n\n"
            "async for val in gen():\n"
            "    print(val)"
        ),
        example_js=(
            "async function* gen() {\n"
            "    for (let i = 0; i < 3; i++) {\n"
            "        await delay(0);\n"
            "        yield i;\n"
            "    }\n"
            "}\n"
            "for await (const val of gen()) console.log(val);"
        ),
        is_silent_failure=False,
        severity=2,
        safe_pattern=(
            "The patterns are structurally similar; the main risk is forgetting that "
            "JS generators return {value, done} objects internally—transparent via for-await."
        ),
    ),
    AsyncSemanticGap(
        gap_id="event_loop_required",
        name="Python requires explicit event loop setup; JS does not",
        python_behavior=(
            "Python code must call asyncio.run(main()) (or manage a loop manually) "
            "to enter async context. Outside an event loop, await is a SyntaxError."
        ),
        js_behavior=(
            "The JS runtime provides the event loop automatically. Top-level await is "
            "supported in ES modules. No setup is required."
        ),
        example_python=(
            "import asyncio\n\n"
            "async def main():\n"
            "    await do_stuff()\n\n"
            "asyncio.run(main())  # required"
        ),
        example_js=(
            "// In an ES module (type=module)\n"
            "const result = await doStuff();  // top-level await, works directly"
        ),
        is_silent_failure=False,
        severity=2,
        safe_pattern=(
            "Always wrap Python entry points with asyncio.run(). "
            "In JS, use ES modules for top-level await; CommonJS requires an IIFE."
        ),
    ),
    AsyncSemanticGap(
        gap_id="concurrent_execution",
        name="True parallelism: asyncio is cooperative; JS has Web Workers",
        python_behavior=(
            "asyncio is single-threaded and cooperative. CPU-bound work blocks the "
            "event loop. Use multiprocessing or concurrent.futures for true parallelism."
        ),
        js_behavior=(
            "The main thread is single-threaded. Web Workers (browser) or worker_threads "
            "(Node) provide true parallelism with message-passing. SharedArrayBuffer "
            "enables shared memory between workers."
        ),
        example_python=(
            "# Blocks event loop — bad\n"
            "result = await asyncio.get_event_loop().run_in_executor(None, cpu_task)"
        ),
        example_js=(
            "// In browser\n"
            "const worker = new Worker('worker.js');\n"
            "worker.postMessage({ data });\n"
            "worker.onmessage = e => console.log(e.data);"
        ),
        is_silent_failure=False,
        severity=3,
        safe_pattern=(
            "Offload CPU-bound work to thread/process pools in Python or Workers in JS. "
            "Never run blocking synchronous code in the main event loop of either runtime."
        ),
    ),
    AsyncSemanticGap(
        gap_id="promise_chaining_pitfall",
        name="Silent swallowing of errors in .then() chains without .catch()",
        python_behavior=(
            "An exception in a sequential await chain propagates immediately to the "
            "caller via normal exception propagation. No special handler needed."
        ),
        js_behavior=(
            "In p.then(fn1).then(fn2), if fn1 throws, fn2 is skipped and the rejection "
            "propagates along the chain. Without a terminal .catch(), the rejection is "
            "silently swallowed (or fires unhandledRejection)."
        ),
        example_python=(
            "r1 = await step1()  # if raises, propagates immediately\n"
            "r2 = await step2(r1)"
        ),
        example_js=(
            "step1()\n"
            "  .then(r1 => step2(r1))  // if step1 rejects, step2 never called\n"
            "  .then(r2 => step3(r2))  // also skipped\n"
            "  // No .catch() — rejection disappears!"
        ),
        is_silent_failure=True,
        severity=5,
        safe_pattern=(
            "Always append .catch(handler) to every .then() chain. "
            "Prefer async/await + try/catch over raw .then() chains to avoid this trap."
        ),
    ),
]


# ---------------------------------------------------------------------------
# 4. AsyncPatternTranslation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AsyncPatternTranslation:
    python_pattern: str
    js_safe_pattern: str
    notes: str


# ---------------------------------------------------------------------------
# 5. ASYNC_TRANSLATIONS
# ---------------------------------------------------------------------------

ASYNC_TRANSLATIONS: list[AsyncPatternTranslation] = [
    AsyncPatternTranslation(
        python_pattern="await asyncio.gather(a(), b())",
        js_safe_pattern="await Promise.all([a(), b()])",
        notes=(
            "Both run tasks concurrently and wait for all to complete. "
            "In JS, a() and b() start executing immediately when called, before "
            "Promise.all sees them—unlike Python where asyncio.gather schedules them."
        ),
    ),
    AsyncPatternTranslation(
        python_pattern="await asyncio.sleep(1)",
        js_safe_pattern="await new Promise(r => setTimeout(r, 1000))",
        notes=(
            "asyncio.sleep takes seconds; setTimeout takes milliseconds. "
            "Many JS projects wrap this as a utility: const delay = ms => new Promise(r => setTimeout(r, ms))."
        ),
    ),
    AsyncPatternTranslation(
        python_pattern="await asyncio.wait_for(coro, timeout=5)",
        js_safe_pattern=(
            "const controller = new AbortController();\n"
            "const timer = setTimeout(() => controller.abort(), 5000);\n"
            "try { await fetch(url, { signal: controller.signal }); }\n"
            "finally { clearTimeout(timer); }"
        ),
        notes=(
            "Python has first-class timeout support via wait_for. "
            "JS has no universal timeout primitive; AbortController works only with "
            "APIs that accept a signal. For arbitrary async functions, use Promise.race."
        ),
    ),
    AsyncPatternTranslation(
        python_pattern="asyncio.run(main())",
        js_safe_pattern="main();  // or top-level await main() in ES module",
        notes=(
            "Python requires an explicit event loop entry point. "
            "JS runs in an event loop by default. In CommonJS modules use an IIFE: "
            "(async () => { await main(); })();"
        ),
    ),
    AsyncPatternTranslation(
        python_pattern=(
            "try:\n"
            "    await task\n"
            "except asyncio.CancelledError:\n"
            "    cleanup()\n"
            "    raise"
        ),
        js_safe_pattern=(
            "const controller = new AbortController();\n"
            "try {\n"
            "    await longRunning(controller.signal);\n"
            "} catch (e) {\n"
            "    if (e.name === 'AbortError') { cleanup(); }\n"
            "    else throw e;\n"
            "}"
        ),
        notes=(
            "Python CancelledError is injected by the task scheduler. "
            "JS has no equivalent—AbortController must be threaded through manually, "
            "and each async function must check signal.aborted at suspension points."
        ),
    ),
    AsyncPatternTranslation(
        python_pattern="async for item in aiter:",
        js_safe_pattern="for await (const item of asyncIter) {",
        notes=(
            "Syntactically very similar. Python uses __aiter__/__anext__; "
            "JS uses Symbol.asyncIterator. Both support break/continue/return. "
            "Ensure the JS iterable properly closes on early exit (return() method)."
        ),
    ),
    AsyncPatternTranslation(
        python_pattern=(
            "async with contextmanager() as resource:\n"
            "    await use(resource)"
        ),
        js_safe_pattern=(
            "const resource = await acquire();\n"
            "try {\n"
            "    await use(resource);\n"
            "} finally {\n"
            "    await resource.release();\n"
            "}"
        ),
        notes=(
            "Python async context managers (__aenter__/__aexit__) have no JS equivalent. "
            "The try/finally pattern is the idiomatic JS replacement. "
            "TC39 has a 'using' proposal (explicit resource management) as a future option."
        ),
    ),
    AsyncPatternTranslation(
        python_pattern="loop.call_soon(fn)",
        js_safe_pattern="queueMicrotask(fn)  // microtask; or setTimeout(fn, 0) for macrotask",
        notes=(
            "call_soon schedules fn in the next loop iteration (similar to macrotask). "
            "queueMicrotask runs before the next macrotask. "
            "Choose based on desired ordering relative to other queued work."
        ),
    ),
    AsyncPatternTranslation(
        python_pattern="asyncio.create_task(coro())",
        js_safe_pattern="coro();  // Promise starts immediately; store reference if needed",
        notes=(
            "asyncio.create_task schedules the coroutine and returns a Task handle "
            "for cancellation/awaiting. In JS, calling an async function returns a "
            "Promise that has already started—no explicit scheduling needed. "
            "Store the Promise if you want to await it later or catch rejections."
        ),
    ),
    AsyncPatternTranslation(
        python_pattern="await asyncio.shield(coro())",
        js_safe_pattern="// No direct equivalent — manually track completion with a stored Promise",
        notes=(
            "asyncio.shield protects a coroutine from cancellation while still allowing "
            "the outer task to be cancelled. JS has no such construct. "
            "The closest approximation is holding a reference to the Promise outside "
            "any AbortController scope and awaiting it separately."
        ),
    ),
    AsyncPatternTranslation(
        python_pattern=(
            "results, _ = await asyncio.wait(\n"
            "    tasks, return_when=asyncio.FIRST_COMPLETED\n"
            ")"
        ),
        js_safe_pattern="const result = await Promise.race([a(), b(), c()]);",
        notes=(
            "asyncio.wait(FIRST_COMPLETED) returns sets of done/pending tasks giving "
            "fine-grained control. Promise.race resolves/rejects with the first settled "
            "Promise—remaining Promises keep running but their results are discarded."
        ),
    ),
    AsyncPatternTranslation(
        python_pattern=(
            "async def producer():\n"
            "    for item in source:\n"
            "        yield await fetch(item)"
        ),
        js_safe_pattern=(
            "async function* producer() {\n"
            "    for (const item of source) {\n"
            "        yield await fetch(item);\n"
            "    }\n"
            "}"
        ),
        notes=(
            "Python async generators and JS async generators are structurally identical. "
            "Both pause at each yield, resuming on next iteration. "
            "The consumer should use 'async for' / 'for await...of' respectively."
        ),
    ),
]


# ---------------------------------------------------------------------------
# 6. AsyncCodeAnalyzer
# ---------------------------------------------------------------------------

# JS async function names commonly seen in production code that require await.
_COMMON_ASYNC_CALLS = re.compile(
    r"""(?<![.\w])  # not preceded by . or word char (avoid method chains starting mid-word)
    (fetch|axios(?:\.\w+)?|fs\.promises\.\w+|readFile|writeFile|
     readdir|stat|open|close|connect|query|findOne|find|save|
     create|update|delete|remove|request|get|post|put|patch|
     setTimeout|setImmediate|nextTick|sleep|delay|wait|
     \w+Async|\w+Promise)
    \s*\(""",
    re.VERBOSE,
)

_AWAIT_PREFIX = re.compile(r"""(?:^|[\s(,=!&|?:])await\s+\S""")


class AsyncCodeAnalyzer:
    """Heuristic analyzer for common async mistakes in JavaScript code."""

    # ------------------------------------------------------------------
    # detect_forgotten_await
    # ------------------------------------------------------------------

    def detect_forgotten_await(self, js_code: str) -> list[tuple[int, str]]:
        """Return (line_number, snippet) for async calls missing an ``await``."""
        findings: list[tuple[int, str]] = []
        lines = js_code.splitlines()

        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Skip comment lines and lines that already have await.
            if stripped.startswith("//") or stripped.startswith("*"):
                continue

            for match in _COMMON_ASYNC_CALLS.finditer(line):
                call_start = match.start()

                # Look backwards in the same line for an 'await' keyword.
                prefix = line[:call_start]
                if re.search(r"\bawait\b\s*$", prefix):
                    continue

                # Skip if it's inside a .then() / .catch() / .finally() callback
                # (the function call is the callback body, not a bare call).
                if re.search(r"\.\s*(?:then|catch|finally)\s*\(", prefix):
                    continue

                # Skip declarations: const fn = async function... / function fetch...
                if re.search(r"\b(?:async\s+function|function)\s+$", prefix):
                    continue

                snippet = line.strip()
                findings.append((lineno, snippet))

        return findings

    # ------------------------------------------------------------------
    # detect_unhandled_rejection
    # ------------------------------------------------------------------

    def detect_unhandled_rejection(self, js_code: str) -> list[tuple[int, str]]:
        """Return (line_number, snippet) for .then() chains lacking .catch()."""
        findings: list[tuple[int, str]] = []
        lines = js_code.splitlines()

        for i, line in enumerate(lines):
            if ".then(" not in line:
                continue
            if ".catch(" in line:
                continue

            # Check the next 3 lines for a .catch().
            lookahead = lines[i + 1 : i + 4]
            if any(".catch(" in la for la in lookahead):
                continue

            # Also skip if there's a surrounding try/catch block (rough check).
            context_start = max(0, i - 3)
            context = "\n".join(lines[context_start : i + 1])
            if re.search(r"\btry\s*\{", context):
                continue

            findings.append((i + 1, line.strip()))

        return findings

    # ------------------------------------------------------------------
    # detect_sequential_awaits
    # ------------------------------------------------------------------

    def detect_sequential_awaits(self, js_code: str) -> list[tuple[int, str]]:
        """Return (line_number, snippet) for consecutive awaits that could use Promise.all."""
        findings: list[tuple[int, str]] = []
        lines = js_code.splitlines()

        _await_stmt = re.compile(
            r"^\s*(?:const|let|var)\s+\w+\s*=\s*await\s+\S"
        )

        i = 0
        while i < len(lines) - 1:
            current = lines[i]
            nxt = lines[i + 1]

            if _await_stmt.match(current) and _await_stmt.match(nxt):
                # Check that the second await doesn't reference the first variable
                # (dependency check: extract first variable name).
                m = re.match(r"\s*(?:const|let|var)\s+(\w+)\s*=", current)
                first_var = m.group(1) if m else None
                if first_var and re.search(r"\b" + re.escape(first_var) + r"\b", nxt):
                    i += 1
                    continue

                snippet = f"{current.strip()}  |  {nxt.strip()}"
                findings.append((i + 1, snippet))
                i += 2
                continue

            i += 1

        return findings
