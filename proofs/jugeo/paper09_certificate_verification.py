#!/usr/bin/env python3
"""Paper 09 — Proof-Carrying Python Certificates."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo_proof import (
    theorem, check, verify, carry_proof,
    ProofCertificate,
    reset, run_all,
)
from jugeo.geometry.site import (
    SiteBuilder, Coordinate, CoordinateKind,
    Morphism, MorphismKind, GrothendieckTopology,
)
from jugeo.geometry.descent import (
    DescentEngine, DescentConfiguration, DescentStrategy,
)
from jugeo.geometry.covers import Cover

reset()

# ─── Test programs ──────────────────────────────────────────

TRIVIAL = '''
def identity(x):
    return x
'''

MEDIUM = '''
def first(lst):
    return lst[0]

def last(lst):
    return lst[-1]

def middle(lst):
    n = len(lst)
    return lst[n // 2]
'''

COMPLEX = '''
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def distance(self):
        return (self.x ** 2 + self.y ** 2) ** 0.5

    def translate(self, dx, dy):
        return Point(self.x + dx, self.y + dy)

    def scale(self, factor):
        return Point(self.x * factor, self.y * factor)
'''

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("Trivial program produces valid certificate")
def cert_trivial():
    source, cert = carry_proof(TRIVIAL)
    assert cert.verdict == "verified"
    assert cert.reverify(source)
    assert cert.code_hash

@theorem("Medium program produces valid certificate")
def cert_medium():
    source, cert = carry_proof(MEDIUM)
    assert cert.verdict == "verified"
    assert cert.reverify(source)
    assert cert.propositions_ok > 0

@theorem("Complex program produces valid certificate")
def cert_complex():
    source, cert = carry_proof(COMPLEX)
    assert cert.verdict == "verified"
    assert cert.reverify(source)
    assert cert.n_coordinates > 0

@theorem("Certificate serialization roundtrips")
def cert_roundtrip():
    source, cert = carry_proof(MEDIUM)
    json_str = cert.to_json()
    cert2 = ProofCertificate.from_json(json_str)
    assert cert2.verdict == cert.verdict
    assert cert2.code_hash == cert.code_hash
    assert cert2.reverify(source)

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: descent certificate for trivial program")
def deep_cert_trivial():
    mod = Coordinate(('triv',), CoordinateKind.MODULE)
    fn = Coordinate(('triv', 'identity'), CoordinateKind.FUNCTION)
    site = (SiteBuilder('trivial')
        .add_coordinate(mod).add_coordinate(fn)
        .add_morphism(Morphism(fn, mod, MorphismKind.RESTRICTION))
        .set_topology(GrothendieckTopology.canonical())
        .build())

    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(fn,)),
        sections={'triv.identity': {'verified': True, 'trust': 1.0, 'props_ok': 1}})
    assert result.is_success
    cert = result.certificate
    assert cert.certificate_id
    assert len(cert.certificate_id) > 8

@theorem("Deep API: descent certificate for complex program")
def deep_cert_complex():
    mod = Coordinate(('point',), CoordinateKind.MODULE)
    init = Coordinate(('point', '__init__'), CoordinateKind.FUNCTION)
    dist = Coordinate(('point', 'distance'), CoordinateKind.FUNCTION)
    trans = Coordinate(('point', 'translate'), CoordinateKind.FUNCTION)
    scale = Coordinate(('point', 'scale'), CoordinateKind.FUNCTION)

    site = (SiteBuilder('point-class')
        .add_coordinate(mod).add_coordinate(init)
        .add_coordinate(dist).add_coordinate(trans).add_coordinate(scale)
        .add_morphism(Morphism(init, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(dist, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(trans, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(scale, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(dist, init, MorphismKind.TRANSPORT))
        .add_morphism(Morphism(trans, init, MorphismKind.TRANSPORT))
        .add_morphism(Morphism(scale, init, MorphismKind.TRANSPORT))
        .set_topology(GrothendieckTopology.canonical())
        .build())

    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(init, dist, trans, scale)),
        sections={
            'point.__init__':   {'verified': True, 'trust': 1.0, 'props_ok': 1},
            'point.distance':   {'verified': True, 'trust': 1.0, 'props_ok': 1},
            'point.translate':  {'verified': True, 'trust': 1.0, 'props_ok': 1},
            'point.scale':      {'verified': True, 'trust': 1.0, 'props_ok': 1},
        })
    assert result.is_success
    assert result.unwrap_section().constituent_count == 4

@theorem("Deep API: certificate IDs are unique across runs")
def deep_cert_unique():
    mod = Coordinate(('u',), CoordinateKind.MODULE)
    fn = Coordinate(('u', 'f'), CoordinateKind.FUNCTION)
    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))

    ids = set()
    for _ in range(3):
        result = engine.attempt_descent(
            cover=Cover(target=mod, patches=(fn,)),
            sections={'u.f': {'verified': True, 'trust': 1.0, 'props_ok': 1}})
        ids.add(result.certificate.certificate_id)
    assert len(ids) == 3, "Certificate IDs should be unique"

# ─── Checks ────────────────────────────────────────────────

@check("Certificate hash is deterministic")
def chk_deterministic():
    _, c1 = carry_proof(TRIVIAL)
    _, c2 = carry_proof(TRIVIAL)
    assert c1.code_hash == c2.code_hash

@check("Certificate hash matches verify result")
def chk_hash_match():
    r = verify(MEDIUM)
    _, cert = carry_proof(MEDIUM)
    assert cert.code_hash == r.certificate_hash

@check("Reverify rejects tampered source")
def chk_reverify_reject():
    source, cert = carry_proof(TRIVIAL)
    tampered = source + "\n# tampered\n"
    assert not cert.reverify(tampered)

@check("Certificate grows with program complexity")
def chk_cert_grows():
    _, ct = carry_proof(TRIVIAL)
    _, cm = carry_proof(MEDIUM)
    _, cc = carry_proof(COMPLEX)
    assert ct.n_coordinates <= cm.n_coordinates <= cc.n_coordinates

@check("Grothendieck ok in certificates")
def chk_grot_ok():
    for code in (TRIVIAL, MEDIUM, COMPLEX):
        _, cert = carry_proof(code)
        assert cert.grothendieck_ok

@check("All certificates have H1=0")
def chk_h1():
    for code in (TRIVIAL, MEDIUM, COMPLEX):
        _, cert = carry_proof(code)
        assert cert.H1 == "0"

if __name__ == "__main__":
    run_all("Paper 09 — Proof-Carrying Python Certificates")
