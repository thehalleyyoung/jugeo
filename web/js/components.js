/* ============================================================
   JuGeo — Shared Components (Nav, Footer, Sidebar)
   Injected into every page via JS for DRY multi-page site.
   ============================================================ */

function renderNav(activePage) {
  return `
  <nav class="nav" role="navigation">
    <div class="nav-inner">
      <a href="index.html" class="nav-logo">
        <div class="logo-icon">J</div>
        <span>JuGeo</span>
      </a>
      <ul class="nav-links">
        <li><a href="index.html" class="${activePage === 'home' ? 'active' : ''}">Home</a></li>
        <li><a href="pages/quickstart.html" class="${activePage === 'quickstart' ? 'active' : ''}">Quickstart</a></li>
        <li><a href="pages/concepts.html" class="${activePage === 'concepts' ? 'active' : ''}">Concepts</a></li>
        <li><a href="pages/api.html" class="${activePage === 'api' ? 'active' : ''}">API</a></li>
        <li><a href="pages/tutorial.html" class="${activePage === 'tutorial' ? 'active' : ''}">Tutorial</a></li>

        <li><a href="pages/proofs.html" class="${activePage === 'proofs' ? 'active' : ''}">Proofs</a></li>
        <li><a href="pages/comparison.html" class="${activePage === 'comparison' ? 'active' : ''}">Comparison</a></li>
      </ul>
      <div class="nav-right">
        <div class="nav-search">
          <span style="opacity:0.5">&#x1F50D;</span>
          <input type="text" placeholder="Search docs..." />
          <kbd>&#x2318;K</kbd>
        </div>
        <button class="theme-toggle" title="Toggle theme">&#x2600;</button>
        <a href="https://github.com/thehalleyyoung/jugeo" class="btn btn-ghost btn-sm" target="_blank">GitHub</a>
      </div>
      <button class="nav-toggle" aria-label="Menu">&#x2630;</button>
    </div>
  </nav>`;
}

function renderFooter() {
  return `
  <footer class="footer">
    <div class="container">
      <div class="footer-grid">
        <div class="footer-brand">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
            <div class="logo-icon" style="width:28px;height:28px;background:linear-gradient(135deg,var(--color-primary),var(--color-accent));border-radius:7px;display:flex;align-items:center;justify-content:center;color:white;font-weight:800;font-size:0.75rem;">J</div>
            <strong style="font-size:1.1rem;">JuGeo</strong>
          </div>
          <p>Judgment Geometry &mdash; A sheaf-theoretic foundation for proof-carrying Python verification with multi-channel evidence and trust algebra.</p>
          <p style="margin-top:12px;font-size:0.8rem;">Python &ge; 3.10 &middot; MIT License</p>
        </div>
        <div class="footer-section">
          <h4>Documentation</h4>
          <a href="pages/quickstart.html">Quickstart</a>
          <a href="pages/concepts.html">Core Concepts</a>
          <a href="pages/api.html">API Reference</a>
          <a href="pages/tutorial.html">Tutorial</a>
          <a href="pages/cli.html">CLI Reference</a>
        </div>
        <div class="footer-section">
          <h4>Theory</h4>

          <a href="pages/proofs.html">Lean Proofs</a>
          <a href="pages/math.html">Math Background</a>
          <a href="pages/comparison.html">vs. Lean/Coq/F*</a>
          <a href="pages/trust.html">Trust Algebra</a>
        </div>
        <div class="footer-section">
          <h4>Community</h4>
          <a href="https://github.com/thehalleyyoung/jugeo" target="_blank">GitHub</a>
          <a href="pages/contributing.html">Contributing</a>
          <a href="pages/faq.html">FAQ</a>
          <a href="pages/changelog.html">Changelog</a>
        </div>
      </div>
      <div class="footer-bottom">
        <span>&copy; 2025&ndash;2026 JuGeo Contributors</span>
        <span>Built with sheaf-theoretic precision</span>
      </div>
    </div>
  </footer>`;
}

function renderDocsSidebar(activePage) {
  const sections = [
    {
      title: 'Getting Started',
      links: [
        { href: 'quickstart.html', label: 'Quickstart', id: 'quickstart' },
        { href: 'installation.html', label: 'Installation', id: 'installation' },
        { href: 'tutorial.html', label: 'Beginner Tutorial', id: 'tutorial' },
      ]
    },
    {
      title: 'Core Concepts',
      links: [
        { href: 'concepts.html', label: 'Overview', id: 'concepts' },
        { href: 'judgments.html', label: 'Judgments', id: 'judgments' },
        { href: 'sites.html', label: 'Semantic Sites', id: 'sites' },
        { href: 'descent.html', label: 'Descent & Gluing', id: 'descent' },
        { href: 'trust.html', label: 'Trust Algebra', id: 'trust' },
        { href: 'evidence.html', label: 'Evidence Channels', id: 'evidence' },
        { href: 'obstructions.html', label: 'Obstructions', id: 'obstructions' },
        { href: 'effects.html', label: 'Python Effects', id: 'effects' },
        { href: 'concept-ideation.html', label: 'Geometry of Ideation', id: 'concept-ideation' },
        { href: 'concept-orchestration.html', label: 'Geometry of Orchestration', id: 'concept-orchestration' },
      ]
    },
    {
      title: 'Using JuGeo',
      links: [
        { href: 'cli.html', label: 'CLI Reference', id: 'cli' },
        { href: 'api.html', label: 'API Reference', id: 'api' },
        { href: 'modes.html', label: 'Proof Modes', id: 'modes' },
        { href: 'solver.html', label: 'Solver & Routing', id: 'solver' },
        { href: 'tactics.html', label: 'Semantic Moves', id: 'tactics' },
        { href: 'generation.html', label: 'Code Generation', id: 'generation' },
        { href: 'ideation.html', label: 'Ideating Math Papers', id: 'ideation' },
        { href: 'research-orchestration.html', label: 'Research Orchestration', id: 'research-orchestration' },
      ]
    },
    {
      title: 'jugeo-agents',
      links: [
        { href: 'agents-overview.html', label: 'Overview', id: 'agents-overview' },
        { href: 'agents-coding-cli.html', label: 'Coding-Agent CLIs', id: 'agents-coding-cli' },
        { href: 'agents-concepts.html', label: 'Sheaf Theory for Agents', id: 'agents-concepts' },
        { href: 'agents-tutorial.html', label: 'Tutorial', id: 'agents-tutorial' },
        { href: 'agents-api.html', label: 'API Reference', id: 'agents-api' },
        { href: 'agents-examples.html', label: 'Examples Gallery', id: 'agents-examples' },
        { href: 'agents-fusion.html', label: 'Knowledge Fusion', id: 'agents-fusion' },
        { href: 'agents-bundle.html', label: 'Judgment Fiber Bundles', id: 'agents-bundle' },
      ]
    },
    {
      title: 'Theory & Research',
      links: [
        { href: 'math.html', label: 'Math Background', id: 'math' },

        { href: 'proofs.html', label: 'Lean Proofs', id: 'proofs' },
        { href: 'comparison.html', label: 'vs. Lean/Coq/F*', id: 'comparison' },
      ]
    },
    {
      title: 'Advanced',
      links: [
        { href: 'architecture.html', label: 'Architecture', id: 'architecture' },
        { href: 'encodings.html', label: 'Encodings', id: 'encodings' },
        { href: 'orchestration.html', label: 'Orchestration', id: 'orchestration' },
        { href: 'copilot.html', label: 'Copilot Integration', id: 'copilot' },
        { href: 'ideation.html', label: 'Theorem Discovery', id: 'ideation' },
      ]
    },
  ];

  let html = '<aside class="sidebar">';
  for (const section of sections) {
    html += `<div class="sidebar-section">`;
    html += `<div class="sidebar-section-title">${section.title}</div>`;
    for (const link of section.links) {
      const cls = activePage === link.id ? 'sidebar-link active' : 'sidebar-link';
      html += `<a href="${link.href}" class="${cls}">${link.label}</a>`;
    }
    html += '</div>';
  }
  html += '</aside>';
  return html;
}

// Inject nav + footer on page load
function initPage(activePage, isSubpage) {
  const prefix = isSubpage ? '../' : '';

  // Fix nav links for subpages
  let navHtml = renderNav(activePage);
  if (isSubpage) {
    navHtml = navHtml
      .replace(/href="index\.html"/g, 'href="../index.html"')
      .replace(/href="pages\//g, 'href="');
  }

  const navTarget = document.getElementById('nav-mount');
  if (navTarget) navTarget.innerHTML = navHtml;

  let footerHtml = renderFooter();
  if (isSubpage) {
    footerHtml = footerHtml
      .replace(/href="pages\//g, 'href="');
  }

  const footerTarget = document.getElementById('footer-mount');
  if (footerTarget) footerTarget.innerHTML = footerHtml;

  // Docs sidebar
  const sidebarTarget = document.getElementById('sidebar-mount');
  if (sidebarTarget) {
    sidebarTarget.innerHTML = renderDocsSidebar(activePage);
  }
}
