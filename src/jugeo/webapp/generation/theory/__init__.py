"""Grand theory of web application generation in judgment-geometry terms.

This package provides the sheaf-theoretic foundation for generating
web applications.  Every generation decision — what HTML to emit, what
CSS properties to set, what JS behaviours to wire — is grounded in a
formal obligation presheaf over a view site, with descent verification
ensuring cross-fibre coherence.

Submodules
----------
view_site
    The site of *views* (routes/pages) with navigation morphisms.
visual_obligations
    Presheaf of visual obligations: layout, colour, typography, spacing,
    hierarchy, responsive descent.
behavioral_obligations
    Presheaf of behavioural obligations: events, state, routing, loading,
    persistence, error handling.
structural_obligations
    Presheaf of structural (HTML) obligations: document structure,
    accessibility, semantic markup, form integrity.
css_theory
    Theory of CSS as a presheaf on the DOM selector site, with cascade
    descent, specificity algebra, responsive gluing, and animation.
js_theory
    Theory of JavaScript as a presheaf on the event/state site, with
    module encapsulation, event delegation, state coherence.
html_theory
    Theory of HTML as the structural fibre of the web application sheaf.
flask_theory
    Theory of Flask as the server-side fibre: routes, models, templates,
    security, CRUD descent.
component_presheaf
    Components as a presheaf over the view site with fibres in HTML/CSS/JS.
generation_descent
    Descent engine for verifying generated code satisfies all obligations.
"""
