/* ============================================================
   JuGeo Documentation Site — Main JavaScript
   ============================================================ */

// --- Theme Toggle ---
function initTheme() {
  const saved = localStorage.getItem('jugeo-theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);

  document.querySelectorAll('.theme-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme');
      const next = current === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('jugeo-theme', next);
      btn.textContent = next === 'light' ? '\u263E' : '\u2600';
    });
  });
}

// --- Scroll Animations ---
function initScrollAnimations() {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
      }
    });
  }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });

  document.querySelectorAll('.animate-on-scroll, .stagger-children').forEach(el => {
    observer.observe(el);
  });
}

// --- Tab System ---
function initTabs() {
  document.querySelectorAll('.tabs').forEach(tabGroup => {
    const buttons = tabGroup.querySelectorAll('.tab-btn');
    const panelContainer = tabGroup.nextElementSibling || tabGroup.parentElement;

    buttons.forEach(btn => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.tab;

        buttons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        // Find panels - they might be siblings or in a wrapper
        const panels = panelContainer.querySelectorAll ?
          panelContainer.querySelectorAll('.tab-panel') :
          document.querySelectorAll(`.tab-panel[data-tab-group="${tabGroup.dataset.group}"]`);

        // Also check siblings
        let el = tabGroup.nextElementSibling;
        while (el && el.classList.contains('tab-panel')) {
          el.classList.remove('active');
          if (el.dataset.tab === target) el.classList.add('active');
          el = el.nextElementSibling;
        }

        // Check within container
        if (panels) {
          panels.forEach(p => {
            p.classList.remove('active');
            if (p.dataset.tab === target) p.classList.add('active');
          });
        }
      });
    });
  });
}

// --- Code Copy Buttons ---
function initCodeCopy() {
  document.querySelectorAll('.code-block .copy-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const code = btn.closest('.code-block').querySelector('code');
      navigator.clipboard.writeText(code.textContent).then(() => {
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = orig, 2000);
      });
    });
  });
}

// --- Mobile Navigation ---
function initMobileNav() {
  const toggle = document.querySelector('.nav-toggle');
  const sidebar = document.querySelector('.sidebar');

  if (toggle && sidebar) {
    toggle.addEventListener('click', () => {
      sidebar.classList.toggle('open');
    });
  }
}

// --- Active Sidebar Link ---
function initActiveSidebar() {
  const path = window.location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.sidebar-link').forEach(link => {
    const href = link.getAttribute('href');
    if (href === path || (path === 'index.html' && href === './')) {
      link.classList.add('active');
    }
  });
}

// --- Smooth Scroll for Anchor Links ---
function initSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', e => {
      const target = document.querySelector(a.getAttribute('href'));
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        history.pushState(null, '', a.getAttribute('href'));
      }
    });
  });
}

// --- TOC Highlighting ---
function initTOC() {
  const toc = document.querySelector('.toc');
  if (!toc) return;

  const links = toc.querySelectorAll('a');
  const sections = [];

  links.forEach(link => {
    const id = link.getAttribute('href').slice(1);
    const section = document.getElementById(id);
    if (section) sections.push({ link, section });
  });

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        links.forEach(l => l.classList.remove('active'));
        const match = sections.find(s => s.section === entry.target);
        if (match) match.link.classList.add('active');
      }
    });
  }, { threshold: 0.3, rootMargin: `-${64 + 20}px 0px -60% 0px` });

  sections.forEach(s => observer.observe(s.section));
}

// --- Hero Background Animation (geometric mesh) ---
function initHeroCanvas() {
  const canvas = document.getElementById('hero-canvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  let width, height, points, frame = 0;

  function resize() {
    width = canvas.width = canvas.offsetWidth * (window.devicePixelRatio || 1);
    height = canvas.height = canvas.offsetHeight * (window.devicePixelRatio || 1);
    ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
    initPoints();
  }

  function initPoints() {
    points = [];
    const spacing = 80;
    const cols = Math.ceil(canvas.offsetWidth / spacing) + 2;
    const rows = Math.ceil(canvas.offsetHeight / spacing) + 2;

    for (let i = 0; i < cols; i++) {
      for (let j = 0; j < rows; j++) {
        points.push({
          x: i * spacing + (j % 2 ? spacing / 2 : 0),
          y: j * spacing,
          ox: i * spacing + (j % 2 ? spacing / 2 : 0),
          oy: j * spacing,
          vx: (Math.random() - 0.5) * 0.3,
          vy: (Math.random() - 0.5) * 0.3,
        });
      }
    }
  }

  function draw() {
    frame++;
    ctx.clearRect(0, 0, canvas.offsetWidth, canvas.offsetHeight);

    // Update positions
    points.forEach(p => {
      p.x = p.ox + Math.sin(frame * 0.008 + p.ox * 0.01) * 15 + Math.sin(frame * 0.005 + p.oy * 0.008) * 10;
      p.y = p.oy + Math.cos(frame * 0.006 + p.oy * 0.01) * 15 + Math.cos(frame * 0.004 + p.ox * 0.008) * 10;
    });

    // Draw connections
    const maxDist = 120;
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const dx = points[i].x - points[j].x;
        const dy = points[i].y - points[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < maxDist) {
          const alpha = (1 - dist / maxDist) * 0.12;
          ctx.strokeStyle = `rgba(99, 102, 241, ${alpha})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(points[i].x, points[i].y);
          ctx.lineTo(points[j].x, points[j].y);
          ctx.stroke();
        }
      }
    }

    // Draw points
    points.forEach(p => {
      const pulse = Math.sin(frame * 0.02 + p.ox * 0.05) * 0.3 + 0.7;
      ctx.fillStyle = `rgba(129, 140, 248, ${0.25 * pulse})`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 2, 0, Math.PI * 2);
      ctx.fill();
    });

    requestAnimationFrame(draw);
  }

  window.addEventListener('resize', () => {
    resize();
  });

  resize();
  draw();
}

// --- Terminal typing effect ---
function initTerminalDemo() {
  const demos = document.querySelectorAll('.demo-typing');
  demos.forEach(demo => {
    const lines = JSON.parse(demo.dataset.lines || '[]');
    const target = demo.querySelector('.demo-body');
    if (!target || !lines.length) return;

    let lineIdx = 0;
    let charIdx = 0;
    let html = '';

    function type() {
      if (lineIdx >= lines.length) return;

      const line = lines[lineIdx];
      if (line.type === 'command') {
        if (charIdx === 0) {
          html += `<span class="demo-prompt">$ </span><span class="demo-command">`;
        }
        if (charIdx < line.text.length) {
          html += line.text[charIdx];
          charIdx++;
          target.innerHTML = html + '<span class="cursor">|</span>';
          setTimeout(type, 30 + Math.random() * 40);
        } else {
          html += '</span>\n';
          charIdx = 0;
          lineIdx++;
          setTimeout(type, 400);
        }
      } else {
        html += `<span class="demo-output">${line.text}</span>\n`;
        lineIdx++;
        target.innerHTML = html;
        setTimeout(type, 100);
      }
    }

    // Start when visible
    const observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting) {
        observer.disconnect();
        setTimeout(type, 500);
      }
    });
    observer.observe(demo);
  });
}

// --- Search (basic client-side) ---
function initSearch() {
  const searchInput = document.querySelector('.nav-search input');
  if (!searchInput) return;

  // Ctrl+K / Cmd+K to focus search
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      searchInput.focus();
    }
    if (e.key === 'Escape') {
      searchInput.blur();
    }
  });
}

// --- Syntax Highlighting (token-safe) ---
function highlightPython(code) {
  // Escape HTML
  code = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // Tokenize into segments that should NOT be further processed (strings, comments)
  // and segments that should be highlighted (everything else).
  const tokens = [];
  let remaining = code;

  // Match comments and strings as protected tokens first
  const protectedRe = /"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|#.*$/gm;
  let lastIdx = 0;
  let m;

  // Reset regex
  protectedRe.lastIndex = 0;
  while ((m = protectedRe.exec(code)) !== null) {
    // Push the gap before this match as "code" to highlight
    if (m.index > lastIdx) {
      tokens.push({ type: 'code', text: code.slice(lastIdx, m.index) });
    }
    // Determine if comment or string
    const cls = m[0].startsWith('#') ? 'token-comment' : 'token-string';
    tokens.push({ type: 'protected', text: m[0], cls });
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < code.length) {
    tokens.push({ type: 'code', text: code.slice(lastIdx) });
  }

  // Now highlight only the "code" tokens using a placeholder strategy
  // to prevent cascading regex matches inside inserted <span> tags.
  function highlightCode(text) {
    // Phase 1: Replace decorators with placeholders
    var placeholders = [];
    text = text.replace(/(@\w+)/g, function(match) {
      var idx = placeholders.length;
      placeholders.push('<span class="token-decorator">' + match + '</span>');
      return '\x00PH' + idx + '\x00';
    });

    // Phase 2: Keywords, builtins, numbers — safe because decorators are placeholders
    text = text.replace(/\b(def|class|import|from|return|if|elif|else|for|while|in|not|and|or|is|with|as|try|except|finally|raise|yield|async|await|True|False|None|assert|lambda|pass|break|continue|global|nonlocal)\b/g,
      '<span class="token-keyword">$1</span>');
    text = text.replace(/\b(print|len|range|int|str|list|dict|tuple|set|type|isinstance|enumerate|zip|map|filter|sorted|sum|min|max|abs|any|all|open|super|property|staticmethod|classmethod|dataclass|frozen)\b/g,
      '<span class="token-builtin">$1</span>');
    text = text.replace(/\b(\d+\.?\d*)\b/g,
      '<span class="token-number">$1</span>');

    // Phase 3: Restore decorator placeholders
    text = text.replace(/\x00PH(\d+)\x00/g, function(_, idx) {
      return placeholders[parseInt(idx)];
    });
    return text;
  }

  return tokens.map(t => {
    if (t.type === 'protected') {
      return `<span class="${t.cls}">${t.text}</span>`;
    }
    return highlightCode(t.text);
  }).join('');
}

function initSyntaxHighlight() {
  document.querySelectorAll('pre code.language-python').forEach(block => {
    block.innerHTML = highlightPython(block.textContent);
  });
}

// --- Number Counter Animation ---
function initCounters() {
  const counters = document.querySelectorAll('[data-count]');
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const el = entry.target;
        const target = parseInt(el.dataset.count);
        const suffix = el.dataset.suffix || '';
        const prefix = el.dataset.prefix || '';
        const duration = 1500;
        const startTime = performance.now();

        function update(currentTime) {
          const elapsed = currentTime - startTime;
          const progress = Math.min(elapsed / duration, 1);
          // Ease out cubic
          const eased = 1 - Math.pow(1 - progress, 3);
          const current = Math.round(eased * target);
          el.textContent = prefix + current.toLocaleString() + suffix;
          if (progress < 1) requestAnimationFrame(update);
        }

        requestAnimationFrame(update);
        observer.unobserve(el);
      }
    });
  }, { threshold: 0.5 });

  counters.forEach(c => observer.observe(c));
}

// --- Navbar scroll effect ---
function initNavScroll() {
  const nav = document.querySelector('.nav');
  if (!nav) return;

  let lastScroll = 0;
  window.addEventListener('scroll', () => {
    const scroll = window.scrollY;
    if (scroll > 100) {
      nav.style.boxShadow = 'var(--shadow-md)';
    } else {
      nav.style.boxShadow = 'none';
    }
    lastScroll = scroll;
  }, { passive: true });
}

// --- Initialize everything ---
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initScrollAnimations();
  initTabs();
  initCodeCopy();
  initMobileNav();
  initActiveSidebar();
  initSmoothScroll();
  initTOC();
  initHeroCanvas();
  initTerminalDemo();
  initSearch();
  initSyntaxHighlight();
  initCounters();
  initNavScroll();
});
