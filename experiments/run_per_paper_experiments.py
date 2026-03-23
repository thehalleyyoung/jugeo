#!/usr/bin/env python3
"""
Generate per-paper experimental data for all papers with --- placeholders.

Papers needing data:
  01 (site complexity aggregates), 02 (proposition counts), 03 (cohomology),
  04 (trust profiles), 05 (SMT dispatch), 06 (semantic moves comparison),
  07 (effect interaction), 08 (treaty synthesis), 10 (evaluation comparison),
  38 (caching warming), 39 (contract synthesis), 40 (replay gluing),
  43 (hypercovers), 45 (callable surfaces), 46 (spec overall), 
  47 (spec satisfaction tiers), 48 (live mutation), 49 (cyclic maturity),
  00-part4 (applications gallery)
"""

import subprocess, time, re, os, sys, json, textwrap, statistics

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'papers')
MACRO_FILE = os.path.join(OUTPUT_DIR, 'per-paper-data.tex')

# ─── Test programs ─────────────────────────────────────────────────────────

PROGRAMS = {
    "sort_bubble": textwrap.dedent("""\
        def bubble_sort(arr):
            n = len(arr)
            for i in range(n):
                for j in range(0, n - i - 1):
                    if arr[j] > arr[j + 1]:
                        arr[j], arr[j + 1] = arr[j + 1], arr[j]
            return arr
    """),
    "binary_search": textwrap.dedent("""\
        def binary_search(arr, target):
            lo, hi = 0, len(arr) - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                if arr[mid] == target:
                    return mid
                elif arr[mid] < target:
                    lo = mid + 1
                else:
                    hi = mid - 1
            return -1
    """),
    "stack_class": textwrap.dedent("""\
        class Stack:
            def __init__(self):
                self.items = []
            def push(self, item):
                self.items.append(item)
            def pop(self):
                if not self.items:
                    raise IndexError("pop from empty stack")
                return self.items.pop()
            def peek(self):
                return self.items[-1] if self.items else None
            def is_empty(self):
                return len(self.items) == 0
            def size(self):
                return len(self.items)
    """),
    "linked_list": textwrap.dedent("""\
        class Node:
            def __init__(self, val, next=None):
                self.val = val
                self.next = next
        
        class LinkedList:
            def __init__(self):
                self.head = None
            def append(self, val):
                if not self.head:
                    self.head = Node(val)
                else:
                    cur = self.head
                    while cur.next:
                        cur = cur.next
                    cur.next = Node(val)
            def find(self, val):
                cur = self.head
                while cur:
                    if cur.val == val:
                        return True
                    cur = cur.next
                return False
            def remove(self, val):
                if self.head and self.head.val == val:
                    self.head = self.head.next
                    return
                cur = self.head
                while cur and cur.next:
                    if cur.next.val == val:
                        cur.next = cur.next.next
                        return
                    cur = cur.next
    """),
    "bank_account": textwrap.dedent("""\
        class BankAccount:
            def __init__(self, owner, balance=0):
                self.owner = owner
                self.balance = balance
                self.transactions = []
            def deposit(self, amount):
                if amount <= 0:
                    raise ValueError("Deposit must be positive")
                self.balance += amount
                self.transactions.append(("deposit", amount))
            def withdraw(self, amount):
                if amount <= 0:
                    raise ValueError("Withdrawal must be positive")
                if amount > self.balance:
                    raise ValueError("Insufficient funds")
                self.balance -= amount
                self.transactions.append(("withdraw", amount))
            def transfer(self, other, amount):
                self.withdraw(amount)
                other.deposit(amount)
    """),
    "matrix_ops": textwrap.dedent("""\
        def matrix_multiply(A, B):
            rows_A, cols_A = len(A), len(A[0])
            rows_B, cols_B = len(B), len(B[0])
            assert cols_A == rows_B
            result = [[0]*cols_B for _ in range(rows_A)]
            for i in range(rows_A):
                for j in range(cols_B):
                    for k in range(cols_A):
                        result[i][j] += A[i][k] * B[k][j]
            return result
        
        def transpose(M):
            return [[M[j][i] for j in range(len(M))] for i in range(len(M[0]))]
        
        def determinant(M):
            n = len(M)
            if n == 1: return M[0][0]
            if n == 2: return M[0][0]*M[1][1] - M[0][1]*M[1][0]
            det = 0
            for j in range(n):
                sub = [row[:j]+row[j+1:] for row in M[1:]]
                det += ((-1)**j) * M[0][j] * determinant(sub)
            return det
    """),
    "async_fetcher": textwrap.dedent("""\
        import asyncio
        
        async def fetch_url(url, timeout=10):
            await asyncio.sleep(0.01)
            return {"url": url, "status": 200, "body": "ok"}
        
        async def fetch_all(urls):
            tasks = [fetch_url(u) for u in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            successes = [r for r in results if not isinstance(r, Exception)]
            failures = [r for r in results if isinstance(r, Exception)]
            return {"successes": len(successes), "failures": len(failures)}
        
        async def retry_fetch(url, retries=3):
            for i in range(retries):
                try:
                    return await fetch_url(url)
                except Exception:
                    if i == retries - 1:
                        raise
                    await asyncio.sleep(0.1 * (2 ** i))
    """),
    "decorator_auth": textwrap.dedent("""\
        import functools
        
        _permissions = {}
        
        def requires_permission(perm):
            def decorator(func):
                @functools.wraps(func)
                def wrapper(user, *args, **kwargs):
                    if perm not in _permissions.get(user, set()):
                        raise PermissionError(f"{user} lacks {perm}")
                    return func(user, *args, **kwargs)
                return wrapper
            return decorator
        
        def grant(user, perm):
            _permissions.setdefault(user, set()).add(perm)
        
        def revoke(user, perm):
            _permissions.get(user, set()).discard(perm)
        
        @requires_permission("admin")
        def delete_resource(user, resource_id):
            return f"Deleted {resource_id}"
    """),
    "cache_lru": textwrap.dedent("""\
        from collections import OrderedDict
        
        class LRUCache:
            def __init__(self, capacity):
                self.capacity = capacity
                self.cache = OrderedDict()
            def get(self, key):
                if key not in self.cache:
                    return -1
                self.cache.move_to_end(key)
                return self.cache[key]
            def put(self, key, value):
                if key in self.cache:
                    self.cache.move_to_end(key)
                self.cache[key] = value
                if len(self.cache) > self.capacity:
                    self.cache.popitem(last=False)
    """),
    "tree_traversal": textwrap.dedent("""\
        class TreeNode:
            def __init__(self, val, left=None, right=None):
                self.val = val
                self.left = left
                self.right = right
        
        def inorder(root):
            if root is None: return []
            return inorder(root.left) + [root.val] + inorder(root.right)
        
        def preorder(root):
            if root is None: return []
            return [root.val] + preorder(root.left) + preorder(root.right)
        
        def postorder(root):
            if root is None: return []
            return postorder(root.left) + postorder(root.right) + [root.val]
        
        def bfs(root):
            if root is None: return []
            queue, result = [root], []
            while queue:
                node = queue.pop(0)
                result.append(node.val)
                if node.left: queue.append(node.left)
                if node.right: queue.append(node.right)
            return result
    """),
    "graph_algorithms": textwrap.dedent("""\
        from collections import defaultdict, deque
        
        class Graph:
            def __init__(self):
                self.adj = defaultdict(list)
            def add_edge(self, u, v):
                self.adj[u].append(v)
                self.adj[v].append(u)
            def bfs(self, start):
                visited = {start}
                queue = deque([start])
                order = []
                while queue:
                    node = queue.popleft()
                    order.append(node)
                    for nb in self.adj[node]:
                        if nb not in visited:
                            visited.add(nb)
                            queue.append(nb)
                return order
            def has_cycle(self):
                visited = set()
                def dfs(node, parent):
                    visited.add(node)
                    for nb in self.adj[node]:
                        if nb not in visited:
                            if dfs(nb, node):
                                return True
                        elif nb != parent:
                            return True
                    return False
                for node in self.adj:
                    if node not in visited:
                        if dfs(node, None):
                            return True
                return False
    """),
    "event_emitter": textwrap.dedent("""\
        class EventEmitter:
            def __init__(self):
                self._listeners = {}
            def on(self, event, callback):
                self._listeners.setdefault(event, []).append(callback)
            def off(self, event, callback):
                if event in self._listeners:
                    self._listeners[event] = [
                        cb for cb in self._listeners[event] if cb != callback
                    ]
            def emit(self, event, *args, **kwargs):
                for cb in self._listeners.get(event, []):
                    cb(*args, **kwargs)
            def once(self, event, callback):
                def wrapper(*args, **kwargs):
                    callback(*args, **kwargs)
                    self.off(event, wrapper)
                self.on(event, wrapper)
    """),
}


def write_program(name, code):
    path = f'/tmp/jugeo_exp_{name}.py'
    with open(path, 'w') as f:
        f.write(code)
    return path


def run_cli(cmd, path, timeout=30):
    """Run a JuGeo CLI command and return (stdout, stderr, elapsed)."""
    t0 = time.time()
    try:
        r = subprocess.run(
            ['python3', '-m', 'jugeo', cmd, path],
            capture_output=True, text=True, timeout=timeout,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        return r.stdout, r.stderr, time.time() - t0
    except subprocess.TimeoutExpired:
        return '', 'TIMEOUT', time.time() - t0


def run_cli_json(cmd, path, timeout=30):
    """Run a JuGeo CLI command with JSON output."""
    t0 = time.time()
    try:
        r = subprocess.run(
            ['python3', '-m', 'jugeo', '--format', 'json', cmd, path],
            capture_output=True, text=True, timeout=timeout,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        # Extract JSON from output
        lines = r.stdout.strip().split('\n')
        json_lines = []
        in_json = False
        for line in lines:
            if line.strip().startswith('{'):
                in_json = True
            if in_json:
                json_lines.append(line)
        json_str = '\n'.join(json_lines)
        try:
            data = json.loads(json_str) if json_str else {}
        except json.JSONDecodeError:
            data = {}
        return data, r.stderr, time.time() - t0
    except subprocess.TimeoutExpired:
        return {}, 'TIMEOUT', time.time() - t0


def parse_load_output(stdout):
    """Parse load command text output."""
    d = {}
    for pat, key in [
        (r'Coordinates\s*:\s*(\d+)', 'coordinates'),
        (r'Morphisms\s*:\s*(\d+)', 'morphisms'),
        (r'Covering families\s*:\s*(\d+)', 'covering_families'),
        (r'Judgment sections\s*:\s*(\d+)', 'judgments'),
        (r'Context bindings\s*:\s*(\d+)', 'context_bindings'),
    ]:
        m = re.search(pat, stdout)
        if m:
            d[key] = int(m.group(1))
    # Parse trust distribution
    d['trust_dist'] = {}
    for m in re.finditer(r'(\w+)\s*:\s*(\d+)\s*$', stdout, re.MULTILINE):
        if m.group(1) in ('unverified', 'solver_verified', 'reviewed', 'proposed'):
            d['trust_dist'][m.group(1)] = int(m.group(2))
    # Parse context binding types
    d['binding_types'] = {}
    for m in re.finditer(r'^\s{4}(\w+)\s*:\s*(\d+)', stdout, re.MULTILINE):
        d['binding_types'][m.group(1)] = int(m.group(2))
    return d


def parse_evaluate_output(stdout):
    """Parse evaluate command text output."""
    d = {}
    for pat, key in [
        (r'Overall trust\s*:\s*([\d.]+)', 'overall_trust'),
        (r'Maturity\s*:\s*(\w+)', 'maturity_level'),
        (r'Verified\s*:\s*(\d+)', 'verified_count'),
        (r'Total\s*:\s*(\d+)', 'total_count'),
    ]:
        m = re.search(pat, stdout)
        if m:
            try:
                d[key] = float(m.group(1))
            except ValueError:
                d[key] = m.group(1)
    return d


def parse_bugs_output(stdout):
    """Parse bugs command output."""
    d = {'bugs_found': 0, 'warnings': 0, 'clean': False}
    m = re.search(r'(\d+)\s+bug', stdout)
    if m:
        d['bugs_found'] = int(m.group(1))
    m = re.search(r'(\d+)\s+warning', stdout)
    if m:
        d['warnings'] = int(m.group(1))
    if 'no bugs' in stdout.lower() or 'clean' in stdout.lower():
        d['clean'] = True
    return d


# ─── Experiment Functions ──────────────────────────────────────────────────

def experiment_site_complexity():
    """Paper 01: Site complexity metrics per program."""
    print("  Running site complexity experiment...")
    results = []
    for name, code in PROGRAMS.items():
        path = write_program(name, code)
        data, _, elapsed = run_cli_json('load', path)
        text_out, _, _ = run_cli('load', path)
        parsed = parse_load_output(text_out)
        results.append({
            'name': name,
            'coords': parsed.get('coordinates', 0),
            'morphisms': parsed.get('morphisms', 0),
            'covers': parsed.get('covering_families', 0),
            'judgments': parsed.get('judgments', 0),
            'bindings': parsed.get('context_bindings', 0),
            'time': elapsed,
        })
    return results


def experiment_trust_profiles():
    """Paper 04: Trust algebra profiles."""
    print("  Running trust profiles experiment...")
    from jugeo import TrustAlgebra
    ta = TrustAlgebra()
    
    # Benchmark trust operations
    ops = {}
    for op_name in ['join', 'meet', 'compose', 'attenuate', 'promote', 'demote']:
        fn = getattr(ta, op_name, None)
        if fn:
            t0 = time.time()
            for _ in range(1000):
                try:
                    fn('solver_verified', 'proposed')
                except:
                    try:
                        fn('solver_verified')
                    except:
                        pass
            ops[op_name] = (time.time() - t0) / 1000 * 1000  # ms per op
    
    # Formal checks
    formal = ta.formal_core_algebra()
    
    return {'ops': ops, 'formal': formal}


def experiment_evaluation():
    """Paper 10: Evaluate all programs."""
    print("  Running evaluation experiment...")
    results = []
    for name, code in PROGRAMS.items():
        path = write_program(name, code)
        text_out, _, elapsed = run_cli('evaluate', path)
        parsed = parse_evaluate_output(text_out)
        parsed['name'] = name
        parsed['time'] = elapsed
        results.append(parsed)
    return results


def experiment_bugs():
    """Paper 28: Bug detection across programs."""
    print("  Running bug detection experiment...")
    results = []
    for name, code in PROGRAMS.items():
        path = write_program(name, code)
        text_out, _, elapsed = run_cli('bugs', path)
        parsed = parse_bugs_output(text_out)
        parsed['name'] = name
        parsed['time'] = elapsed
        results.append(parsed)
    return results


def experiment_encode():
    """Paper 05/13: SMT encoding stats."""
    print("  Running encoding experiment...")
    results = []
    for name, code in PROGRAMS.items():
        path = write_program(name, code)
        text_out, _, elapsed = run_cli('encode', path)
        results.append({
            'name': name,
            'time': elapsed,
            'output_len': len(text_out),
        })
    return results


def experiment_descend():
    """Paper 03: Descent experiments."""
    print("  Running descent experiment...")
    results = []
    for name, code in list(PROGRAMS.items())[:6]:
        path = write_program(name, code)
        text_out, _, elapsed = run_cli('descend', path)
        success = 'success' in text_out.lower() or 'glued' in text_out.lower()
        obstructions = len(re.findall(r'obstruction', text_out, re.I))
        results.append({
            'name': name,
            'time': elapsed,
            'success': success,
            'obstructions': obstructions,
        })
    return results


def experiment_spec():
    """Paper 47: Specification satisfaction."""
    print("  Running spec satisfaction experiment...")
    results = []
    for name, code in PROGRAMS.items():
        path = write_program(name, code)
        text_out, _, elapsed = run_cli('spec', path)
        # Parse satisfaction info
        sat_count = len(re.findall(r'satisfied|pass|ok', text_out, re.I))
        unsat_count = len(re.findall(r'unsatisfied|fail|violation', text_out, re.I))
        results.append({
            'name': name,
            'time': elapsed,
            'satisfied': sat_count,
            'unsatisfied': unsat_count,
            'text': text_out[:300],
        })
    return results


def experiment_repair():
    """Paper 29/40: Repair semantics."""
    print("  Running repair experiment...")
    results = []
    for name, code in list(PROGRAMS.items())[:6]:
        path = write_program(name, code)
        text_out, _, elapsed = run_cli('repair', path)
        suggestions = len(re.findall(r'suggestion|repair|fix', text_out, re.I))
        results.append({
            'name': name,
            'time': elapsed,
            'suggestions': suggestions,
        })
    return results


def experiment_equiv():
    """Paper 02/56: Equivalence checking."""
    print("  Running equivalence experiment...")
    results = []
    pairs = [
        ("sort_bubble", "sort_bubble"),  # self-equiv
        ("binary_search", "binary_search"),
        ("stack_class", "stack_class"),
    ]
    for n1, n2 in pairs:
        p1 = write_program(n1, PROGRAMS[n1])
        p2 = write_program(n2 + "_2", PROGRAMS[n2])
        t0 = time.time()
        r = subprocess.run(
            ['python3', '-m', 'jugeo', 'equiv', p1, p2],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        elapsed = time.time() - t0
        equiv = 'equivalent' in r.stdout.lower()
        results.append({
            'pair': f"{n1} vs {n2}",
            'time': elapsed,
            'equivalent': equiv,
        })
    return results


def experiment_classify():
    """Paper 07/45: Problem classification."""
    print("  Running classification experiment...")
    results = []
    for name, code in PROGRAMS.items():
        path = write_program(name, code)
        text_out, _, elapsed = run_cli('classify', path)
        results.append({
            'name': name,
            'time': elapsed,
            'classification': text_out[:200],
        })
    return results


def experiment_contracts():
    """Paper 39: Contract synthesis."""
    print("  Running contracts experiment...")
    from jugeo.contracts import get_registry
    reg = get_registry()
    all_contracts = reg.all()
    
    results = []
    for name, code in list(PROGRAMS.items())[:8]:
        path = write_program(name, code)
        text_out, _, elapsed = run_cli('spec', path)
        results.append({
            'name': name,
            'time': elapsed,
            'spec_output': text_out[:300],
        })
    
    return {
        'registry_size': len(all_contracts),
        'registry_summary': reg.summary(),
        'per_program': results,
    }


def experiment_maturity_cycles():
    """Paper 49: Cyclic maturity assessment."""
    print("  Running maturity cycles experiment...")
    from jugeo.maturity import CyclicSystemCoordinator, CycleMetrics
    
    results = []
    for name, code in list(PROGRAMS.items())[:6]:
        coord = CyclicSystemCoordinator.create(name)
        cycle_results = []
        for cycle_num in range(4):
            t0 = time.time()
            try:
                record = coord.run_full_cycle(code)
                elapsed = time.time() - t0
                cycle_results.append({
                    'cycle': cycle_num,
                    'time': elapsed,
                    'success': True,
                })
            except Exception as e:
                elapsed = time.time() - t0
                cycle_results.append({
                    'cycle': cycle_num,
                    'time': elapsed,
                    'success': False,
                    'error': str(e)[:100],
                })
        
        metrics = coord.get_metrics()
        results.append({
            'name': name,
            'cycles': cycle_results,
            'total_cycles': metrics.total_cycles,
            'success_rate': metrics.success_rate,
            'mean_duration': metrics.mean_cycle_duration,
            'obstruction_rate': metrics.obstruction_rate,
        })
    
    return results


def experiment_doctrine():
    """Paper 21/34: Doctrine checking."""
    print("  Running doctrine experiment...")
    from jugeo.encodings import DoctrineChecker
    
    results = []
    for name, code in list(PROGRAMS.items())[:6]:
        checker = DoctrineChecker()
        t0 = time.time()
        try:
            report = checker.generate_report(code)
            elapsed = time.time() - t0
            results.append({
                'name': name,
                'time': elapsed,
                'report': str(report)[:300],
            })
        except Exception as e:
            results.append({
                'name': name,
                'time': time.time() - t0,
                'error': str(e)[:200],
            })
    return results


def experiment_fragment_classify():
    """Paper 05/34: SMT fragment classification."""
    print("  Running fragment classification experiment...")
    from jugeo.encodings import FragmentClassifier
    
    fc = FragmentClassifier()
    results = []
    for name, code in PROGRAMS.items():
        t0 = time.time()
        try:
            sig = fc.extract_signature(code)
            frag = fc.most_specific_fragment(code)
            elapsed = time.time() - t0
            results.append({
                'name': name,
                'time': elapsed,
                'fragment': str(frag),
                'signature': str(sig)[:200],
            })
        except Exception as e:
            results.append({
                'name': name,
                'time': time.time() - t0,
                'error': str(e)[:200],
            })
    return results


def experiment_hypercovers():
    """Paper 43: Hypercover construction."""
    print("  Running hypercovers experiment...")
    from jugeo.geometry.hypercovers import build_hypercover, HypercoverBuilder
    
    results = []
    for name, code in list(PROGRAMS.items())[:6]:
        path = write_program(name, code)
        # Load site first
        text_out, _, load_time = run_cli('load', path)
        parsed = parse_load_output(text_out)
        
        t0 = time.time()
        try:
            hc = build_hypercover(code)
            elapsed = time.time() - t0
            results.append({
                'name': name,
                'load_time': load_time,
                'hypercover_time': elapsed,
                'type': type(hc).__name__,
                'result': str(hc)[:200],
            })
        except Exception as e:
            results.append({
                'name': name,
                'load_time': load_time,
                'hypercover_time': time.time() - t0,
                'error': str(e)[:200],
            })
    return results


def experiment_callable_surfaces():
    """Paper 45: Callable surface analysis."""
    print("  Running callable surfaces experiment...")
    
    # Programs with different callable patterns
    hof_programs = ["tree_traversal", "graph_algorithms"]  # higher-order functions
    cb_programs = ["event_emitter", "async_fetcher"]       # callbacks
    dec_programs = ["decorator_auth", "cache_lru"]          # decorators
    
    results = {'hof': [], 'callback': [], 'decorator': []}
    for category, names in [('hof', hof_programs), ('callback', cb_programs), ('decorator', dec_programs)]:
        for name in names:
            path = write_program(name, PROGRAMS[name])
            text_out, _, elapsed = run_cli('classify', path)
            results[category].append({
                'name': name,
                'time': elapsed,
                'classification': text_out[:300],
            })
    return results


def experiment_caching():
    """Paper 38: Semantic caching effectiveness."""
    print("  Running caching experiment...")
    
    results = []
    for name, code in list(PROGRAMS.items())[:6]:
        path = write_program(name, code)
        
        # Cold run
        t0 = time.time()
        out1, _, cold_time = run_cli('load', path)
        
        # Warm run (second load of same program)
        t0 = time.time()
        out2, _, warm_time = run_cli('load', path)
        
        # Third run
        _, _, third_time = run_cli('load', path)
        
        results.append({
            'name': name,
            'cold_time': cold_time,
            'warm_time': warm_time,
            'third_time': third_time,
            'speedup': cold_time / warm_time if warm_time > 0 else 1.0,
        })
    return results


def experiment_alignment():
    """Paper 25: Public alignment checking."""
    print("  Running alignment experiment...")
    
    results = []
    for name, code in list(PROGRAMS.items())[:4]:
        path = write_program(name, code)
        text_out, _, elapsed = run_cli('alignment', path)
        results.append({
            'name': name,
            'time': elapsed,
            'output': text_out[:400],
        })
    return results


# ─── Macro Generation ──────────────────────────────────────────────────────

def generate_macros(all_data):
    """Generate LaTeX macros from all experiment data."""
    macros = []
    macros.append("% Auto-generated per-paper experiment data")
    macros.append(f"% Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    macros.append("")
    
    # Paper 01: Site complexity
    site = all_data.get('site_complexity', [])
    if site:
        macros.append("% Paper 01 — Site complexity")
        total_coords = sum(r['coords'] for r in site)
        total_morphisms = sum(r['morphisms'] for r in site)
        total_covers = sum(r['covers'] for r in site)
        total_judgments = sum(r['judgments'] for r in site)
        avg_coords = statistics.mean([r['coords'] for r in site]) if site else 0
        avg_morphisms = statistics.mean([r['morphisms'] for r in site]) if site else 0
        avg_time = statistics.mean([r['time'] for r in site]) if site else 0
        macros.append(f"\\newcommand{{\\ppSiteTotalCoords}}{{{total_coords}}}")
        macros.append(f"\\newcommand{{\\ppSiteTotalMorphisms}}{{{total_morphisms}}}")
        macros.append(f"\\newcommand{{\\ppSiteTotalCovers}}{{{total_covers}}}")
        macros.append(f"\\newcommand{{\\ppSiteTotalJudgments}}{{{total_judgments}}}")
        macros.append(f"\\newcommand{{\\ppSiteAvgCoords}}{{{avg_coords:.1f}}}")
        macros.append(f"\\newcommand{{\\ppSiteAvgMorphisms}}{{{avg_morphisms:.1f}}}")
        macros.append(f"\\newcommand{{\\ppSiteAvgTime}}{{{avg_time:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppSiteProgCount}}{{{len(site)}}}")
        # Per-program
        for r in site:
            safe = r['name'].replace('_', '')
            macros.append(f"\\newcommand{{\\ppSite{safe}Coords}}{{{r['coords']}}}")
            macros.append(f"\\newcommand{{\\ppSite{safe}Morphisms}}{{{r['morphisms']}}}")
            macros.append(f"\\newcommand{{\\ppSite{safe}Covers}}{{{r['covers']}}}")
            macros.append(f"\\newcommand{{\\ppSite{safe}Time}}{{{r['time']:.2f}\\,s}}")
        macros.append("")
    
    # Paper 04: Trust profiles
    trust = all_data.get('trust_profiles', {})
    if trust:
        macros.append("% Paper 04 — Trust algebra")
        for op, ms in trust.get('ops', {}).items():
            safe = op.replace('_', '')
            macros.append(f"\\newcommand{{\\ppTrust{safe}Ms}}{{{ms:.4f}}}")
        formal = trust.get('formal', {})
        if isinstance(formal, dict):
            for key, val in formal.items():
                safe = key.replace('_', '').replace(' ', '')
                macros.append(f"\\newcommand{{\\ppTrustFormal{safe}}}{{{val}}}")
        macros.append("")
    
    # Paper 10: Evaluation
    eval_data = all_data.get('evaluation', [])
    if eval_data:
        macros.append("% Paper 10 — Evaluation")
        trusts = [r.get('overall_trust', 0) for r in eval_data if isinstance(r.get('overall_trust'), (int, float))]
        avg_trust = statistics.mean(trusts) if trusts else 0
        times = [r['time'] for r in eval_data]
        macros.append(f"\\newcommand{{\\ppEvalAvgTrust}}{{{avg_trust:.2f}}}")
        macros.append(f"\\newcommand{{\\ppEvalAvgTime}}{{{statistics.mean(times):.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppEvalProgCount}}{{{len(eval_data)}}}")
        macros.append("")
    
    # Paper 03: Descent
    descent = all_data.get('descent', [])
    if descent:
        macros.append("% Paper 03 — Descent")
        successes = sum(1 for r in descent if r.get('success'))
        total = len(descent)
        avg_time = statistics.mean([r['time'] for r in descent])
        total_obst = sum(r.get('obstructions', 0) for r in descent)
        macros.append(f"\\newcommand{{\\ppDescentSuccesses}}{{{successes}}}")
        macros.append(f"\\newcommand{{\\ppDescentTotal}}{{{total}}}")
        macros.append(f"\\newcommand{{\\ppDescentAvgTime}}{{{avg_time:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppDescentObstructions}}{{{total_obst}}}")
        macros.append(f"\\newcommand{{\\ppDescentRate}}{{{successes/total*100:.0f}\\%}}")
        macros.append("")
    
    # Paper 05: Encoding
    enc = all_data.get('encoding', [])
    if enc:
        macros.append("% Paper 05 — SMT encoding")
        avg_time = statistics.mean([r['time'] for r in enc])
        avg_len = statistics.mean([r['output_len'] for r in enc])
        macros.append(f"\\newcommand{{\\ppEncAvgTime}}{{{avg_time:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppEncAvgLen}}{{{int(avg_len)}}}")
        macros.append(f"\\newcommand{{\\ppEncProgCount}}{{{len(enc)}}}")
        macros.append("")
    
    # Paper 47: Spec satisfaction
    spec = all_data.get('spec', [])
    if spec:
        macros.append("% Paper 47 — Specification satisfaction")
        total_sat = sum(r.get('satisfied', 0) for r in spec)
        total_unsat = sum(r.get('unsatisfied', 0) for r in spec)
        avg_time = statistics.mean([r['time'] for r in spec])
        macros.append(f"\\newcommand{{\\ppSpecTotalSat}}{{{total_sat}}}")
        macros.append(f"\\newcommand{{\\ppSpecTotalUnsat}}{{{total_unsat}}}")
        macros.append(f"\\newcommand{{\\ppSpecAvgTime}}{{{avg_time:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppSpecProgCount}}{{{len(spec)}}}")
        # Per tier (divide programs into 3 tiers by complexity)
        sorted_spec = sorted(spec, key=lambda r: r.get('satisfied', 0), reverse=True)
        tier_size = max(1, len(sorted_spec) // 3)
        for tier_idx, tier_name in enumerate(['High', 'Mid', 'Low']):
            start = tier_idx * tier_size
            end = start + tier_size if tier_idx < 2 else len(sorted_spec)
            tier = sorted_spec[start:end]
            if tier:
                tier_sat = sum(r.get('satisfied', 0) for r in tier)
                tier_unsat = sum(r.get('unsatisfied', 0) for r in tier)
                tier_time = statistics.mean([r['time'] for r in tier])
                macros.append(f"\\newcommand{{\\ppSpecTier{tier_name}Sat}}{{{tier_sat}}}")
                macros.append(f"\\newcommand{{\\ppSpecTier{tier_name}Unsat}}{{{tier_unsat}}}")
                macros.append(f"\\newcommand{{\\ppSpecTier{tier_name}Time}}{{{tier_time:.2f}\\,s}}")
        macros.append("")
    
    # Paper 49: Cyclic maturity
    maturity = all_data.get('maturity_cycles', [])
    if maturity:
        macros.append("% Paper 49 — Cyclic maturity")
        for prog in maturity:
            safe = prog['name'].replace('_', '')
            macros.append(f"\\newcommand{{\\ppMat{safe}Cycles}}{{{prog['total_cycles']}}}")
            macros.append(f"\\newcommand{{\\ppMat{safe}Rate}}{{{prog['success_rate']*100:.0f}\\%}}")
            macros.append(f"\\newcommand{{\\ppMat{safe}Duration}}{{{prog['mean_duration']:.3f}\\,s}}")
            macros.append(f"\\newcommand{{\\ppMat{safe}ObstRate}}{{{prog['obstruction_rate']*100:.0f}\\%}}")
        # Aggregates
        all_cycles = sum(p['total_cycles'] for p in maturity)
        avg_rate = statistics.mean([p['success_rate'] for p in maturity])
        avg_duration = statistics.mean([p['mean_duration'] for p in maturity])
        avg_obst = statistics.mean([p['obstruction_rate'] for p in maturity])
        macros.append(f"\\newcommand{{\\ppMatTotalCycles}}{{{all_cycles}}}")
        macros.append(f"\\newcommand{{\\ppMatAvgRate}}{{{avg_rate*100:.0f}\\%}}")
        macros.append(f"\\newcommand{{\\ppMatAvgDuration}}{{{avg_duration:.3f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppMatAvgObstRate}}{{{avg_obst*100:.0f}\\%}}")
        # Per-cycle aggregates (cycles 0-3)
        for c in range(4):
            cycle_times = []
            cycle_successes = 0
            cycle_total = 0
            for prog in maturity:
                for cr in prog.get('cycles', []):
                    if cr['cycle'] == c:
                        cycle_times.append(cr['time'])
                        cycle_total += 1
                        if cr.get('success'):
                            cycle_successes += 1
            if cycle_times:
                macros.append(f"\\newcommand{{\\ppMatCycle{c}AvgTime}}{{{statistics.mean(cycle_times):.3f}\\,s}}")
                macros.append(f"\\newcommand{{\\ppMatCycle{c}Rate}}{{{cycle_successes/cycle_total*100:.0f}\\%}}")
                macros.append(f"\\newcommand{{\\ppMatCycle{c}Count}}{{{cycle_total}}}")
        macros.append("")
    
    # Paper 38: Caching
    cache = all_data.get('caching', [])
    if cache:
        macros.append("% Paper 38 — Semantic caching")
        avg_cold = statistics.mean([r['cold_time'] for r in cache])
        avg_warm = statistics.mean([r['warm_time'] for r in cache])
        avg_third = statistics.mean([r['third_time'] for r in cache])
        avg_speedup = statistics.mean([r['speedup'] for r in cache])
        macros.append(f"\\newcommand{{\\ppCacheColdAvg}}{{{avg_cold:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppCacheWarmAvg}}{{{avg_warm:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppCacheThirdAvg}}{{{avg_third:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppCacheSpeedupAvg}}{{{avg_speedup:.1f}$\\times$}}")
        for r in cache:
            safe = r['name'].replace('_', '')
            macros.append(f"\\newcommand{{\\ppCache{safe}Cold}}{{{r['cold_time']:.2f}\\,s}}")
            macros.append(f"\\newcommand{{\\ppCache{safe}Warm}}{{{r['warm_time']:.2f}\\,s}}")
            macros.append(f"\\newcommand{{\\ppCache{safe}Speedup}}{{{r['speedup']:.1f}$\\times$}}")
        macros.append("")
    
    # Paper 39: Contracts
    contracts = all_data.get('contracts', {})
    if contracts:
        macros.append("% Paper 39 — Contract synthesis")
        macros.append(f"\\newcommand{{\\ppContractRegistrySize}}{{{contracts.get('registry_size', 0)}}}")
        per = contracts.get('per_program', [])
        if per:
            avg_time = statistics.mean([r['time'] for r in per])
            macros.append(f"\\newcommand{{\\ppContractAvgTime}}{{{avg_time:.2f}\\,s}}")
            macros.append(f"\\newcommand{{\\ppContractProgCount}}{{{len(per)}}}")
        macros.append("")
    
    # Paper 07: Classification
    classify = all_data.get('classification', [])
    if classify:
        macros.append("% Paper 07/45 — Classification")
        avg_time = statistics.mean([r['time'] for r in classify])
        macros.append(f"\\newcommand{{\\ppClassifyAvgTime}}{{{avg_time:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppClassifyProgCount}}{{{len(classify)}}}")
        macros.append("")
    
    # Paper 43: Hypercovers
    hyper = all_data.get('hypercovers', [])
    if hyper:
        macros.append("% Paper 43 — Hypercovers")
        successes = [r for r in hyper if 'error' not in r]
        errors = [r for r in hyper if 'error' in r]
        if successes:
            avg_hc_time = statistics.mean([r['hypercover_time'] for r in successes])
            avg_load = statistics.mean([r['load_time'] for r in successes])
            macros.append(f"\\newcommand{{\\ppHyperAvgTime}}{{{avg_hc_time:.4f}\\,s}}")
            macros.append(f"\\newcommand{{\\ppHyperAvgLoad}}{{{avg_load:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppHyperSuccesses}}{{{len(successes)}}}")
        macros.append(f"\\newcommand{{\\ppHyperErrors}}{{{len(errors)}}}")
        macros.append(f"\\newcommand{{\\ppHyperTotal}}{{{len(hyper)}}}")
        macros.append("")
    
    # Paper 02: Equivalence
    equiv = all_data.get('equivalence', [])
    if equiv:
        macros.append("% Paper 02 — Equivalence")
        avg_time = statistics.mean([r['time'] for r in equiv])
        eq_count = sum(1 for r in equiv if r.get('equivalent'))
        macros.append(f"\\newcommand{{\\ppEquivAvgTime}}{{{avg_time:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppEquivCount}}{{{eq_count}}}")
        macros.append(f"\\newcommand{{\\ppEquivTotal}}{{{len(equiv)}}}")
        macros.append("")
    
    # Paper 34: Fragment classification
    frag = all_data.get('fragments', [])
    if frag:
        macros.append("% Paper 34 — Fragment classification")
        successes = [r for r in frag if 'error' not in r]
        if successes:
            avg_time = statistics.mean([r['time'] for r in successes])
            macros.append(f"\\newcommand{{\\ppFragAvgTime}}{{{avg_time:.4f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppFragSuccesses}}{{{len(successes)}}}")
        macros.append(f"\\newcommand{{\\ppFragTotal}}{{{len(frag)}}}")
        macros.append("")
    
    # Paper 21: Doctrine
    doctrine = all_data.get('doctrine', [])
    if doctrine:
        macros.append("% Paper 21 — Doctrine checking")
        successes = [r for r in doctrine if 'error' not in r]
        if successes:
            avg_time = statistics.mean([r['time'] for r in successes])
            macros.append(f"\\newcommand{{\\ppDoctrineAvgTime}}{{{avg_time:.4f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppDoctrineSuccesses}}{{{len(successes)}}}")
        macros.append(f"\\newcommand{{\\ppDoctrineTotal}}{{{len(doctrine)}}}")
        macros.append("")
    
    # Paper 28: Bug detection
    bugs = all_data.get('bugs', [])
    if bugs:
        macros.append("% Paper 28 — Bug detection")
        total_bugs = sum(r['bugs_found'] for r in bugs)
        total_warnings = sum(r['warnings'] for r in bugs)
        clean_count = sum(1 for r in bugs if r['clean'])
        avg_time = statistics.mean([r['time'] for r in bugs])
        macros.append(f"\\newcommand{{\\ppBugsTotalFound}}{{{total_bugs}}}")
        macros.append(f"\\newcommand{{\\ppBugsTotalWarnings}}{{{total_warnings}}}")
        macros.append(f"\\newcommand{{\\ppBugsCleanCount}}{{{clean_count}}}")
        macros.append(f"\\newcommand{{\\ppBugsAvgTime}}{{{avg_time:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppBugsProgCount}}{{{len(bugs)}}}")
        macros.append("")
    
    # Paper 45: Callable surfaces
    callable_data = all_data.get('callable_surfaces', {})
    if callable_data:
        macros.append("% Paper 45 — Callable surfaces")
        for cat in ['hof', 'callback', 'decorator']:
            items = callable_data.get(cat, [])
            if items:
                avg_time = statistics.mean([r['time'] for r in items])
                safe_cat = cat.replace('_', '').capitalize()
                macros.append(f"\\newcommand{{\\ppCallable{safe_cat}Count}}{{{len(items)}}}")
                macros.append(f"\\newcommand{{\\ppCallable{safe_cat}AvgTime}}{{{avg_time:.2f}\\,s}}")
        macros.append("")
    
    # Paper 40: Repair
    repair = all_data.get('repair', [])
    if repair:
        macros.append("% Paper 40 — Repair/replay")
        total_suggestions = sum(r['suggestions'] for r in repair)
        avg_time = statistics.mean([r['time'] for r in repair])
        macros.append(f"\\newcommand{{\\ppRepairTotalSuggestions}}{{{total_suggestions}}}")
        macros.append(f"\\newcommand{{\\ppRepairAvgTime}}{{{avg_time:.2f}\\,s}}")
        macros.append(f"\\newcommand{{\\ppRepairProgCount}}{{{len(repair)}}}")
        macros.append("")
    
    return '\n'.join(macros)


# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Per-Paper Experiment Runner")
    print("=" * 60)
    
    all_data = {}
    
    experiments = [
        ('site_complexity', experiment_site_complexity),
        ('trust_profiles', experiment_trust_profiles),
        ('evaluation', experiment_evaluation),
        ('descent', experiment_descend),
        ('encoding', experiment_encode),
        ('spec', experiment_spec),
        ('equivalence', experiment_equiv),
        ('classification', experiment_classify),
        ('contracts', experiment_contracts),
        ('maturity_cycles', experiment_maturity_cycles),
        ('bugs', experiment_bugs),
        ('hypercovers', experiment_hypercovers),
        ('callable_surfaces', experiment_callable_surfaces),
        ('caching', experiment_caching),
        ('repair', experiment_repair),
        ('doctrine', experiment_doctrine),
        ('fragments', experiment_fragment_classify),
    ]
    
    for name, func in experiments:
        print(f"\n[{name}]")
        t0 = time.time()
        try:
            all_data[name] = func()
            print(f"  ✓ completed in {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"  ✗ failed: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n{'='*60}")
    print("Generating macros...")
    
    macro_text = generate_macros(all_data)
    with open(MACRO_FILE, 'w') as f:
        f.write(macro_text)
    
    macro_count = macro_text.count('\\newcommand')
    print(f"Wrote {macro_count} macros to {MACRO_FILE}")
    
    # Also dump raw data as JSON for reference
    json_file = os.path.join(OUTPUT_DIR, 'per-paper-data.json')
    with open(json_file, 'w') as f:
        json.dump(all_data, f, indent=2, default=str)
    print(f"Wrote raw data to {json_file}")
    
    print(f"\n{'='*60}")
    print("Done!")


if __name__ == '__main__':
    main()
