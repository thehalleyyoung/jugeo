"""Integration with JuGeo's existing Site model."""
from __future__ import annotations
from dataclasses import dataclass, field
from .web_site import WebApplicationSite
from .models import (
    WebCoordinate, WebMorphism, WebCoveringFamily,
    DescentCondition, DescentViolation,
)
from .topology import WebTopology


def WebSiteToSite(web_site: WebApplicationSite) -> dict:
    """Convert WebApplicationSite to a dict compatible with jugeo geometry Site.parse()."""
    return {
        "coordinates": [c.to_dict() for c in web_site.coordinates],
        "morphisms": [m.to_dict() for m in web_site.morphisms],
        "covering_families": [f.to_dict() for f in web_site.covering_families],
        "name": web_site.name,
    }


def SiteToWebSite(site_dict: dict) -> WebApplicationSite:
    """Convert a Site dict back to WebApplicationSite."""
    return WebApplicationSite.parse(site_dict)


@dataclass
class WebSiteAnalyzer:
    """Facade combining site construction + descent checking + reporting."""
    site: WebApplicationSite = field(default_factory=WebApplicationSite)
    topology: WebTopology = field(default_factory=WebTopology)

    def add_coordinate(self, coord: WebCoordinate) -> WebSiteAnalyzer:
        self.site.add_coordinate(coord)
        return self

    def add_morphism(self, m: WebMorphism) -> WebSiteAnalyzer:
        self.site.add_morphism(m)
        return self

    def analyze(self) -> dict:
        """Run full analysis and return results dict."""
        violations = self.site.check_descent()
        conditions = self.site.descent_conditions()
        components = self.site.connected_components()
        covers = self.topology.generate_standard_covers(self.site)
        return {
            "coordinate_count": len(self.site.coordinates),
            "morphism_count": len(self.site.morphisms),
            "descent_condition_count": len(conditions),
            "violation_count": len(violations),
            "violations": [v.to_dict() for v in violations],
            "connected_component_count": len(components),
            "standard_cover_count": len(covers),
        }


def generate_report(site: WebApplicationSite) -> dict:
    """Generate a comprehensive analysis report for the site."""
    analyzer = WebSiteAnalyzer(site=site)
    base = analyzer.analyze()

    layers: dict[str, dict] = {}
    for layer in ["python", "template", "javascript", "css", "html", "database", "http", "auth"]:
        coords = site.coordinates_in_layer(layer)
        layers[layer] = {
            "coordinate_count": len(coords),
            "coordinates": [c.id for c in coords],
        }

    boundary_graph = site.language_boundary_graph()
    cross_morphisms = site.cross_language_morphisms()

    return {
        **base,
        "site_name": site.name,
        "language_layers": layers,
        "cross_language_morphism_count": len(cross_morphisms),
        "language_boundary_graph": boundary_graph,
        "descent_conditions": [c.to_dict() for c in site.descent_conditions()],
    }
