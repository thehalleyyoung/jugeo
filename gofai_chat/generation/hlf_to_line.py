"""HLF → poetry-line bridge.

Extracts semantic content from HLF terms and converts it into the
vocabulary needed by LineTemplateBank to fill {NOUN}/{VERB}/{ADJ}/{PLACE}
slots with actual HLF-grounded words rather than generic defaults.
"""
from __future__ import annotations

from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Frame → verb mapping (used when VerbDecomposer has no entry)
# ---------------------------------------------------------------------------

FRAME_TO_VERB: Dict[str, List[str]] = {
    'Flying':     ['hover', 'soar', 'drift', 'glide', 'wing'],
    'Moving':     ['flow', 'wander', 'drift', 'pass', 'turn'],
    'Producing':  ['yield', 'make', 'forge', 'weave', 'spill'],
    'Existing':   ['remain', 'endure', 'persist', 'dwell', 'stand'],
    'Describing': ['hold', 'keep', 'bear', 'know', 'show'],
    'Dying':      ['fall', 'fade', 'sink', 'fail', 'cease'],
    'Creating':   ['build', 'shape', 'form', 'make', 'weave'],
    'Feeling':    ['ache', 'yearn', 'burn', 'grieve', 'linger'],
    'Knowing':    ['seek', 'find', 'know', 'learn', 'trace'],
    'Giving':     ['give', 'pour', 'send', 'lend', 'grant'],
    'Changing':   ['turn', 'shift', 'break', 'melt', 'dissolve'],
    'Working':    ['toil', 'labor', 'strive', 'press', 'forge'],
    'Taking':     ['take', 'grasp', 'hold', 'seize', 'reach'],
    'Causing':    ['drive', 'press', 'push', 'force', 'move'],
    'Becoming':   ['grow', 'turn', 'rise', 'bloom', 'emerge'],
    'Having':     ['hold', 'bear', 'keep', 'carry', 'own'],
    'Receiving':  ['take', 'gain', 'catch', 'find', 'draw'],
    'Destroying': ['break', 'end', 'crush', 'tear', 'burn'],
}

# ---------------------------------------------------------------------------
# Topic → HarmonicLexicon imagery domain
# ---------------------------------------------------------------------------

_TOPIC_TO_IMAGERY: Dict[str, str] = {
    'autumn': 'nature', 'spring': 'nature', 'summer': 'nature', 'winter': 'nature',
    'tree': 'nature', 'forest': 'nature', 'leaf': 'nature', 'flower': 'nature',
    'bird': 'nature', 'bee': 'nature', 'insect': 'nature', 'grass': 'nature',
    'river': 'water', 'ocean': 'water', 'rain': 'water', 'sea': 'water',
    'lake': 'water', 'stream': 'water', 'tide': 'water', 'wave': 'water',
    'love': 'love', 'heart': 'love', 'desire': 'love', 'longing': 'love',
    'death': 'death', 'grief': 'death', 'loss': 'death', 'mourning': 'death',
    'star': 'celestial', 'moon': 'celestial', 'sun': 'celestial', 'sky': 'celestial',
    'night': 'celestial', 'dawn': 'celestial', 'dusk': 'celestial', 'cosmos': 'celestial',
    'fire': 'fire', 'flame': 'fire', 'burning': 'fire', 'ash': 'fire',
    'time': 'time', 'eternity': 'time', 'moment': 'time', 'age': 'time',
    'light': 'light', 'shadow': 'light', 'darkness': 'light', 'gleam': 'light',
    'joy': 'emotional', 'sorrow': 'emotional', 'fear': 'emotional',
    'anger': 'emotional', 'hope': 'emotional', 'wonder': 'emotional',
    'water': 'water', 'wind': 'nature', 'earth': 'nature', 'stone': 'nature',
}

# ---------------------------------------------------------------------------
# Mood → default adjectives
# ---------------------------------------------------------------------------

_MOOD_TO_ADJS: Dict[str, List[str]] = {
    'melancholic':   ['pale', 'dark', 'still', 'hollow', 'cold', 'grey', 'bare'],
    'joyful':        ['bright', 'warm', 'golden', 'light', 'clear', 'sweet', 'glad'],
    'yearning':      ['distant', 'quiet', 'soft', 'faint', 'deep', 'lone', 'aching'],
    'contemplative': ['deep', 'silent', 'old', 'slow', 'bare', 'still', 'vast'],
    'elegiac':       ['lost', 'gone', 'old', 'faded', 'still', 'grey', 'fallen'],
    'urgent':        ['fierce', 'sharp', 'swift', 'bright', 'burning', 'raw', 'hard'],
    'neutral':       ['quiet', 'still', 'deep', 'slow', 'soft', 'long', 'clear'],
    'resolved':      ['clear', 'calm', 'still', 'whole', 'true', 'sure', 'bright'],
    'tense':         ['dark', 'sharp', 'cold', 'hard', 'close', 'taut', 'fierce'],
    'wistful':       ['soft', 'dim', 'pale', 'quiet', 'lost', 'faint', 'fading'],
}

# ---------------------------------------------------------------------------
# Imagery domain → place words
# ---------------------------------------------------------------------------

_DOMAIN_TO_PLACES: Dict[str, List[str]] = {
    'nature':    ['meadow', 'grove', 'field', 'hillside', 'shore', 'wood', 'vale'],
    'water':     ['shore', 'deep', 'tide', 'current', 'pool', 'bay', 'creek'],
    'celestial': ['sky', 'night', 'horizon', 'void', 'heaven', 'ether', 'dome'],
    'death':     ['grave', 'dust', 'shadow', 'silence', 'abyss', 'dark', 'ruin'],
    'love':      ['garden', 'chamber', 'threshold', 'hearth', 'bower', 'grove'],
    'fire':      ['hearth', 'ash', 'ember', 'pyre', 'forge', 'grate', 'kiln'],
    'time':      ['moment', 'dawn', 'dusk', 'hour', 'threshold', 'eon', 'age'],
    'light':     ['dawn', 'threshold', 'shadow', 'veil', 'clearing', 'glow'],
    'emotional': ['heart', 'silence', 'depth', 'shore', 'threshold', 'hollow'],
    'default':   ['silence', 'threshold', 'depth', 'hollow', 'shore', 'dark'],
}


# ---------------------------------------------------------------------------
# HLFContentExtractor
# ---------------------------------------------------------------------------

class HLFContentExtractor:
    """Walk an HLF term tree and collect surface-realizable slot values."""

    def extract(self, hlf) -> dict:
        """Return {'nouns': [...], 'verbs': [...], 'adjs': [...], 'places': [...], 'modals': [...]}"""
        result: dict = {
            'nouns': [], 'verbs': [], 'adjs': [], 'places': [], 'modals': [],
        }
        try:
            self._walk(hlf, result)
        except Exception:
            pass
        # Deduplicate while preserving insertion order
        for key in result:
            seen: set = set()
            deduped: List[str] = []
            for item in result[key]:
                if item and item not in seen:
                    seen.add(item)
                    deduped.append(item)
            result[key] = deduped
        return result

    def _walk(self, node, result: dict) -> None:  # noqa: C901
        if node is None:
            return
        try:
            from gofai_chat.core.terms import (
                EventTerm, TenseTerm, AspectTerm, ModalTerm,
                NegTerm, ImplTerm, ConjTerm, PolyTerm,
                Const, CoerceTerm, QuoteTerm,
            )
        except ImportError:
            return

        if isinstance(node, EventTerm):
            frame = node.frame_type_name
            verbs = FRAME_TO_VERB.get(frame, [])
            result['verbs'].extend(verbs[:3])
            # Role fillers → noun / place candidates
            for role_name, filler in node.roles.items():
                if isinstance(filler, Const):
                    word = filler.name.lower().replace('_', ' ').strip()
                    if not word:
                        continue
                    rn = role_name.lower()
                    if rn in ('place', 'location', 'path', 'goal', 'source'):
                        result['places'].append(word)
                    else:
                        result['nouns'].append(word)
                else:
                    try:
                        self._walk(filler, result)
                    except Exception:
                        pass

        elif isinstance(node, TenseTerm):
            self._walk(node.body, result)

        elif isinstance(node, AspectTerm):
            self._walk(node.body, result)

        elif isinstance(node, ModalTerm):
            modal_map = {
                'epistemic':   'perhaps',
                'deontic':     'must',
                'bouletic':    'longs',
                'dynamic':     'can',
                'teleological': 'seeks',
            }
            adv = modal_map.get(node.modal_kind)
            if adv:
                result['modals'].append(adv)
            self._walk(node.body, result)

        elif isinstance(node, NegTerm):
            result['modals'].extend(['not', 'never', 'no'])
            self._walk(node.body, result)

        elif isinstance(node, ImplTerm):
            result['modals'].append('if')
            self._walk(node.antecedent, result)
            self._walk(node.consequent, result)

        elif isinstance(node, ConjTerm):
            for c in getattr(node, 'conjuncts', []):
                self._walk(c, result)

        elif isinstance(node, PolyTerm):
            for v in getattr(node, 'voices', []):
                self._walk(v, result)

        elif isinstance(node, CoerceTerm):
            coercion = node.coercion_name.lower().replace('_', ' ').strip()
            if coercion:
                result['nouns'].append(coercion)
            self._walk(node.body, result)

        elif isinstance(node, QuoteTerm):
            if node.source:
                result['nouns'].append(node.source.lower().strip())
            self._walk(node.body, result)


# ---------------------------------------------------------------------------
# ConceptualWordBank
# ---------------------------------------------------------------------------

class ConceptualWordBank:
    """Build HLF-grounded vocabulary for a topic using GOFAI modules."""

    def build(self, topic: str, mood: str = 'neutral') -> dict:
        """Return {'nouns': [...], 'verbs': [...], 'adjs': [...], 'places': [...]}

        Sources in priority order:
        1. CoreDefaultReasoner.most_typical_properties(topic)
        2. WikiKnowledgeReasoner.reason_about(topic)
        3. WordNet (NLTK): hyponyms → nouns, synsets → verbs
        4. HarmonicLexicon.by_imagery(imagery_domain)
        5. MetaphorEngine entailments
        6. MorphologyEngine inflection for verb forms
        """
        nouns: List[str] = [topic]
        verbs: List[str] = []
        adjs: List[str] = list(_MOOD_TO_ADJS.get(mood, _MOOD_TO_ADJS['neutral']))
        places: List[str] = []

        imagery_domain = self._topic_to_imagery_domain(topic)

        # 1. CoreDefaultReasoner
        try:
            from gofai_chat.inference.defaults import CoreDefaultReasoner
            props = CoreDefaultReasoner().most_typical_properties(topic, top_k=10)
            for prop, _score in props:
                parts = prop.split('_')
                if len(parts) == 1 and not parts[0].startswith(('is', 'has')):
                    verbs.append(parts[0])
                elif len(parts) == 2 and parts[0] in ('has', 'can', 'is'):
                    # has_sting → 'sting' usable as noun and verb
                    nouns.append(parts[1])
                    verbs.append(parts[1])
                elif len(parts) >= 2:
                    nouns.append(parts[-1])
        except Exception:
            pass

        # 2. WikiKnowledgeReasoner
        try:
            from gofai_chat.knowledge.wiki_reasoner import WikiKnowledgeReasoner
            wiki_props = WikiKnowledgeReasoner().reason_about(topic)
            for prop, grade in wiki_props[:8]:
                g_val = grade.to_prob() if hasattr(grade, 'to_prob') else float(grade)
                if g_val < 0.4:
                    continue
                parts = prop.split('_')
                if len(parts) == 1:
                    verbs.append(parts[0])
                else:
                    nouns.append(parts[-1])
        except Exception:
            pass

        # 3. WordNet via NLTK
        try:
            from nltk.corpus import wordnet as wn
            for syn in wn.synsets(topic, pos=wn.NOUN)[:3]:
                for lemma in syn.lemmas()[:5]:
                    word = lemma.name().replace('_', ' ')
                    if len(word) > 2 and word != topic:
                        nouns.append(word)
                for hypo in syn.hyponyms()[:3]:
                    for lemma in hypo.lemmas()[:2]:
                        word = lemma.name().replace('_', ' ')
                        if len(word) > 2:
                            nouns.append(word)
            for syn in wn.synsets(topic, pos=wn.VERB)[:3]:
                for lemma in syn.lemmas()[:5]:
                    word = lemma.name().replace('_', ' ')
                    if len(word) > 2:
                        verbs.append(word)
            for syn in wn.synsets(mood, pos=wn.ADJ)[:2]:
                for lemma in syn.lemmas()[:3]:
                    word = lemma.name().replace('_', ' ')
                    if len(word) > 2:
                        adjs.append(word)
        except Exception:
            pass

        # 4. HarmonicLexicon by imagery domain
        try:
            from gofai_chat.lexicon.harmonic_lexicon import HarmonicLexicon
            hl = HarmonicLexicon()
            for entry in hl.by_imagery(imagery_domain)[:20]:
                pos = entry.pos.lower()
                if pos in ('n', 'noun'):
                    nouns.append(entry.word)
                elif pos in ('v', 'verb'):
                    verbs.append(entry.word)
                elif pos in ('adj', 'j'):
                    adjs.append(entry.word)
            # Pull place nouns from nature/water domains as fallback
            for domain in ['nature', 'water']:
                if domain != imagery_domain:
                    for entry in hl.by_imagery(domain)[:8]:
                        if entry.pos.lower() in ('n', 'noun'):
                            places.append(entry.word)
                    break
        except Exception:
            pass

        # 5. MetaphorEngine entailments
        try:
            from gofai_chat.coercion.metaphor_engine import MetaphorEngine
            for ent in MetaphorEngine().entailments_for_text(topic)[:5]:
                words = [w for w in ent.lower().split() if len(w) > 3 and w.isalpha()]
                nouns.extend(words[:2])
        except Exception:
            pass

        # 6. Morphologically inflect top verb candidates
        try:
            from gofai_chat.generation.morphology import MorphologyEngine, MorphFeatures
            me = MorphologyEngine()
            feats = MorphFeatures(tense='present', number='singular', person='third')
            inflected: List[str] = []
            for v in verbs[:6]:
                try:
                    inf = me.inflect(v, 'verb', feats)
                    inflected.append(inf)
                except Exception:
                    inflected.append(v)
            verbs = inflected + verbs[6:]
        except Exception:
            pass

        # Domain-specific places
        places.extend(_DOMAIN_TO_PLACES.get(imagery_domain, _DOMAIN_TO_PLACES['default']))

        def _dedup(lst: List[str]) -> List[str]:
            seen: set = set()
            out: List[str] = []
            for item in lst:
                item = item.strip()
                if item and len(item) > 1 and item not in seen:
                    seen.add(item)
                    out.append(item)
            return out

        return {
            'nouns':  _dedup(nouns)[:20],
            'verbs':  _dedup(verbs)[:20],
            'adjs':   _dedup(adjs)[:15],
            'places': _dedup(places)[:10],
        }

    def _topic_to_imagery_domain(self, topic: str) -> str:
        """Map a topic word to an imagery domain for HarmonicLexicon."""
        t = topic.lower().strip()
        if t in _TOPIC_TO_IMAGERY:
            return _TOPIC_TO_IMAGERY[t]
        for key, domain in _TOPIC_TO_IMAGERY.items():
            if key in t or t in key:
                return domain
        return 'nature'


# ---------------------------------------------------------------------------
# HLFToPoetryContext
# ---------------------------------------------------------------------------

class HLFToPoetryContext:
    """Combine HLFContentExtractor + ConceptualWordBank → LineTemplateBank context."""

    def __init__(self) -> None:
        self._extractor = HLFContentExtractor()
        self._word_bank = ConceptualWordBank()

    def build_context(
        self,
        hlf_unit: dict,
        topic: str,
        mood: str,
        tense: str = 'present',
    ) -> dict:
        """Return context dict for LineTemplateBank.random_filled():

        {
          'topic':       topic,
          'mood':        mood,
          'topic_label': topic,
          'noun':        best_noun,
          'verb':        best_verb (morphologically inflected),
          'adj':         best_adj,
          'place':       best_place,
        }
        """
        bank = self._word_bank.build(topic, mood)

        # Extract HLF-grounded words from an attached HLF object
        hlf_words: dict = {
            'nouns': [], 'verbs': [], 'adjs': [], 'places': [], 'modals': [],
        }
        hlf_obj = hlf_unit.get('_hlf_obj')
        if hlf_obj is not None:
            try:
                hlf_words = self._extractor.extract(hlf_obj)
            except Exception:
                pass

        # Also harvest roles stored as plain strings in the unit dict
        for role_name, filler in hlf_unit.get('roles', {}).items():
            if not isinstance(filler, str) or not filler:
                continue
            rn = role_name.lower()
            if rn in ('place', 'location', 'path', 'goal', 'source'):
                hlf_words['places'].insert(0, filler.lower())
            else:
                hlf_words['nouns'].insert(0, filler.lower())

        # Merge: HLF-grounded words first, then bank words
        all_nouns  = hlf_words['nouns']  + bank['nouns']
        all_verbs  = hlf_words['verbs']  + bank['verbs']
        all_adjs   = hlf_words['adjs']   + bank['adjs']
        all_places = hlf_words['places'] + bank['places']

        def _pick(lst: List[str], fallback: str = '') -> str:
            for item in lst:
                item = item.strip()
                if item and len(item) > 1:
                    return item
            return fallback

        best_verb = self._inflect_verb(_pick(all_verbs, 'remain'), tense)

        return {
            'topic':       topic,
            'mood':        mood,
            'topic_label': topic,
            'noun':        _pick(all_nouns, topic),
            'verb':        best_verb,
            'adj':         _pick(all_adjs, 'quiet'),
            'place':       _pick(all_places, 'silence'),
        }

    def _inflect_verb(self, lemma: str, tense: str) -> str:
        """Inflect a verb lemma to 3rd-person singular of *tense*."""
        try:
            from gofai_chat.generation.morphology import MorphologyEngine, MorphFeatures
            feats = MorphFeatures(tense=tense, number='singular', person='third')
            return MorphologyEngine().inflect(lemma, 'verb', feats)
        except Exception:
            return lemma
