"""spaCy → HLF bridge: dependency parse → f-structure → EventTerm.

Uses spaCy for tokenization, POS tagging, morphological analysis,
dependency parsing (Universal Dependencies), and NER.

Then converts to HLF via:
  1. UD dep labels → LFG grammatical functions (nsubj→SUBJ, obj→OBJ, etc.)
  2. morphological features → FeatureBundle (via DMEngine.analyze)
  3. f-structure → EventTerm (via FStructure.to_event_term)
  4. Grade = spaCy confidence scores ⊗ f-structure wellformedness grades

All mappings are Grade-annotated:
  - High-confidence UD labels get Grade ~0.95
  - Morphological ambiguity reduces Grade
  - f-structure incompleteness/incoherence reduces Grade

When spaCy is unavailable (e.g. Python 3.14 compatibility issues),
falls back to a regex-based tokenizer with heuristic POS/dep analysis.
"""
from __future__ import annotations

__all__ = ["SpacyBridge"]

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from gofai_chat.core.grade import Grade
from gofai_chat.core.terms import (
    HLF,
    Const,
    EventTerm,
    TenseTerm,
    AspectTerm,
    NegTerm,
    Var,
)
from gofai_chat.grammar.features import (
    Feature,
    FeatureBundle,
    PhiFeatures,
    TenseFeature,
)
from gofai_chat.grammar.distributed_morphology import (
    DMEngine,
    ENGLISH_VIS,
    ENGLISH_READJUSTMENTS,
    ENGLISH_IMPOVERISHMENTS,
)
from gofai_chat.grammar.lfg import FStructure, GrammaticalFunction


# ══════════════════════════════════════════════════════════════════════════
# Universal Dependencies → LFG GF mapping (with Grade weights)
# ══════════════════════════════════════════════════════════════════════════

_UD_TO_GF: Dict[str, Tuple[str, Grade]] = {
    "nsubj": ("SUBJ", Grade.from_prob(0.97)),
    "nsubj:pass": ("SUBJ", Grade.from_prob(0.93)),
    "csubj": ("SUBJ", Grade.from_prob(0.85)),
    "csubj:pass": ("SUBJ", Grade.from_prob(0.82)),
    "obj": ("OBJ", Grade.from_prob(0.97)),
    "iobj": ("OBJ-TH", Grade.from_prob(0.92)),
    "ccomp": ("COMP", Grade.from_prob(0.90)),
    "xcomp": ("XCOMP", Grade.from_prob(0.88)),
    "nmod": ("ADJ", Grade.from_prob(0.80)),
    "nmod:poss": ("POSS", Grade.from_prob(0.90)),
    "advmod": ("ADJ", Grade.from_prob(0.82)),
    "amod": ("ADJ", Grade.from_prob(0.88)),
    "obl": ("OBL", Grade.from_prob(0.85)),
    "obl:agent": ("OBL", Grade.from_prob(0.90)),
    "obl:tmod": ("ADJ", Grade.from_prob(0.82)),
    "advcl": ("ADJ", Grade.from_prob(0.78)),
    "acl": ("ADJ", Grade.from_prob(0.80)),
    "acl:relcl": ("ADJ", Grade.from_prob(0.82)),
    "appos": ("APP", Grade.from_prob(0.85)),
    "conj": ("ADJ", Grade.from_prob(0.75)),
    "nummod": ("ADJ", Grade.from_prob(0.90)),
    "det": ("SPEC", Grade.from_prob(0.95)),
    "case": ("SPEC", Grade.from_prob(0.92)),
    "mark": ("SPEC", Grade.from_prob(0.88)),
    "compound": ("ADJ", Grade.from_prob(0.85)),
    "flat": ("ADJ", Grade.from_prob(0.85)),
    "flat:name": ("ADJ", Grade.from_prob(0.90)),
    "fixed": ("ADJ", Grade.from_prob(0.88)),
    "vocative": ("ADJ", Grade.from_prob(0.70)),
    "expl": ("SUBJ", Grade.from_prob(0.75)),
    "dislocated": ("TOPIC", Grade.from_prob(0.72)),
    "dep": ("ADJ", Grade.from_prob(0.50)),
    "punct": ("_PUNCT", Grade.from_prob(0.99)),
    "cc": ("_CC", Grade.from_prob(0.95)),
    "aux": ("_AUX", Grade.from_prob(0.95)),
    "aux:pass": ("_AUX", Grade.from_prob(0.93)),
    "cop": ("_COP", Grade.from_prob(0.92)),
    "parataxis": ("ADJ", Grade.from_prob(0.65)),
    "orphan": ("ADJ", Grade.from_prob(0.50)),
    "ROOT": ("_ROOT", Grade.from_prob(0.99)),
}


# ══════════════════════════════════════════════════════════════════════════
# UD morphological features → FeatureBundle
# ══════════════════════════════════════════════════════════════════════════

_UD_MORPH_MAP: Dict[str, Tuple[str, str, float, bool]] = {
    # (name, value, confidence, interpretable)
    "Person=1": ("Person", "1", 0.99, True),
    "Person=2": ("Person", "2", 0.99, True),
    "Person=3": ("Person", "3", 0.99, True),
    "Number=Sing": ("Number", "sg", 0.99, True),
    "Number=Plur": ("Number", "pl", 0.99, True),
    "Tense=Past": ("Tense", "past", 0.98, True),
    "Tense=Pres": ("Tense", "present", 0.98, True),
    "Case=Nom": ("Case", "nominative", 0.95, False),
    "Case=Acc": ("Case", "accusative", 0.95, False),
    "Case=Dat": ("Case", "dative", 0.92, False),
    "Case=Gen": ("Case", "genitive", 0.92, False),
    "VerbForm=Inf": ("VerbForm", "inf", 0.97, False),
    "VerbForm=Part": ("VerbForm", "part", 0.97, False),
    "VerbForm=Ger": ("VerbForm", "ger", 0.97, False),
    "VerbForm=Fin": ("VerbForm", "fin", 0.98, False),
    "Mood=Ind": ("Mood", "indicative", 0.95, True),
    "Mood=Sub": ("Mood", "subjunctive", 0.92, True),
    "Mood=Imp": ("Mood", "imperative", 0.93, True),
    "Mood=Cnd": ("Mood", "conditional", 0.90, True),
    "Aspect=Prog": ("Aspect", "progressive", 0.97, True),
    "Aspect=Perf": ("Aspect", "perfect", 0.97, True),
    "Voice=Pass": ("Voice", "passive", 0.95, False),
    "Voice=Act": ("Voice", "active", 0.98, False),
    "Degree=Pos": ("Degree", "positive", 0.95, True),
    "Degree=Cmp": ("Degree", "comparative", 0.95, True),
    "Degree=Sup": ("Degree", "superlative", 0.95, True),
    "Definite=Def": ("Definiteness", "definite", 0.95, True),
    "Definite=Ind": ("Definiteness", "indefinite", 0.95, True),
    "PronType=Prs": ("PronType", "personal", 0.95, True),
    "PronType=Dem": ("PronType", "demonstrative", 0.95, True),
    "PronType=Rel": ("PronType", "relative", 0.95, True),
    "PronType=Int": ("PronType", "interrogative", 0.95, True),
    "Gender=Masc": ("Gender", "m", 0.95, True),
    "Gender=Fem": ("Gender", "f", 0.95, True),
    "Gender=Neut": ("Gender", "n", 0.95, True),
    "Polarity=Neg": ("Polarity", "negative", 0.98, True),
    "Polarity=Pos": ("Polarity", "positive", 0.98, True),
    "NumType=Card": ("NumType", "cardinal", 0.95, True),
    "NumType=Ord": ("NumType", "ordinal", 0.95, True),
    "Poss=Yes": ("Poss", "yes", 0.95, True),
    "Reflex=Yes": ("Reflex", "yes", 0.95, True),
    "Foreign=Yes": ("Foreign", "yes", 0.80, True),
    "Abbr=Yes": ("Abbr", "yes", 0.90, True),
    "Typo=Yes": ("Typo", "yes", 0.70, True),
}

# POS → default role name when building frames
_POS_TO_DEFAULT_ROLE: Dict[str, str] = {
    "VERB": "predicate",
    "NOUN": "entity",
    "PROPN": "entity",
    "ADJ": "attribute",
    "ADV": "manner",
    "ADP": "relation",
    "DET": "determiner",
    "PRON": "entity",
    "NUM": "quantity",
}


# ══════════════════════════════════════════════════════════════════════════
# Fallback tokenizer (no spaCy dependency)
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class FallbackToken:
    """Minimal token object mirroring spaCy Token interface."""
    text: str
    lemma_: str = ""
    pos_: str = ""
    dep_: str = ""
    head_idx: int = 0
    morph_str: str = ""
    i: int = 0
    ent_type_: str = ""
    is_sent_start: Optional[bool] = None

    def __post_init__(self):
        if not self.lemma_:
            self.lemma_ = self.text.lower()


@dataclass
class FallbackSent:
    """Minimal sentence span."""
    tokens: List[FallbackToken] = field(default_factory=list)
    root_idx: int = 0

    @property
    def root(self) -> FallbackToken:
        if self.tokens:
            return self.tokens[self.root_idx]
        return FallbackToken(text="")

    def __iter__(self):
        return iter(self.tokens)


class FallbackTokenizer:
    """Regex-based tokenizer with heuristic POS/dep when spaCy unavailable."""

    _PRONOUNS = frozenset({
        "i", "me", "my", "mine", "myself",
        "you", "your", "yours", "yourself",
        "he", "him", "his", "himself",
        "she", "her", "hers", "herself",
        "it", "its", "itself",
        "we", "us", "our", "ours", "ourselves",
        "they", "them", "their", "theirs", "themselves",
    })
    _DETERMINERS = frozenset({
        "the", "a", "an", "this", "that", "these", "those",
        "my", "your", "his", "her", "its", "our", "their",
        "some", "any", "no", "every", "each", "all", "both",
    })
    _PREPOSITIONS = frozenset({
        "in", "on", "at", "to", "from", "by", "with", "for",
        "of", "about", "into", "through", "during", "before",
        "after", "above", "below", "between", "under", "over",
        "near", "beside", "among", "across", "along", "around",
        "behind", "beyond", "against", "within", "without",
        "upon", "toward", "towards", "until", "since",
    })
    _AUXILIARIES = frozenset({
        "is", "am", "are", "was", "were", "be", "been", "being",
        "has", "have", "had", "having",
        "do", "does", "did",
        "will", "would", "shall", "should",
        "can", "could", "may", "might", "must",
    })
    _CONJUNCTIONS = frozenset({
        "and", "or", "but", "nor", "yet", "so", "for",
        "because", "although", "while", "if", "when", "unless",
    })
    _ADVERBS = frozenset({
        "not", "never", "always", "often", "sometimes",
        "also", "too", "very", "quite", "rather",
        "here", "there", "now", "then", "today", "yesterday",
    })
    _IRREGULAR_VERBS = frozenset({
        "went", "gone", "saw", "seen", "said", "made", "took", "taken",
        "came", "come", "knew", "known", "got", "gotten", "gave", "given",
        "found", "thought", "told", "became", "left", "felt", "put",
        "brought", "began", "begun", "kept", "held", "wrote", "written",
        "stood", "heard", "let", "meant", "set", "met", "ran", "paid",
        "sat", "spoke", "spoken", "lay", "lain", "led", "read", "grew",
        "grown", "lost", "fell", "fallen", "sent", "built", "understood",
        "drew", "drawn", "broke", "broken", "spent", "cut", "rose",
        "risen", "drove", "driven", "bought", "wore", "worn", "chose",
        "chosen", "sang", "sung", "won", "caught", "taught", "fought",
        "threw", "thrown", "slept", "sold", "swam", "swum", "flew",
        "flown", "drank", "drunk", "forgot", "forgotten", "hid", "hidden",
        "rang", "rung", "shook", "shaken", "bit", "bitten", "ate",
        "eaten", "tore", "torn", "hung", "sought", "bound", "froze",
        "frozen", "shone", "dug", "woke", "woken", "blew", "blown",
        "struck", "stole", "stolen", "swore", "sworn", "bore", "borne",
        "wove", "woven", "clung", "spun", "sprang", "sprung", "strode",
        "stank", "stunk", "slew", "slain", "forbade", "forbidden",
        "forsook", "forsaken",
    })

    def tokenize(self, text: str) -> List[FallbackSent]:
        """Tokenize text into sentences of tokens with heuristic POS/dep."""
        import re
        # Split into sentences
        raw_sents = re.split(r'(?<=[.!?])\s+', text.strip())
        if not raw_sents:
            raw_sents = [text]

        results: List[FallbackSent] = []
        for raw in raw_sents:
            if not raw.strip():
                continue
            # Tokenize
            words = re.findall(r"\w+(?:'\w+)?|[^\w\s]", raw)
            tokens: List[FallbackToken] = []
            root_idx = 0
            found_verb = False

            for idx, w in enumerate(words):
                tok = FallbackToken(text=w, i=idx)
                low = w.lower()

                # POS heuristics
                if low in self._DETERMINERS:
                    tok.pos_ = "DET"
                    tok.dep_ = "det"
                elif low in self._PRONOUNS:
                    tok.pos_ = "PRON"
                    tok.dep_ = "nsubj" if not found_verb else "obj"
                elif low in self._PREPOSITIONS:
                    tok.pos_ = "ADP"
                    tok.dep_ = "case"
                elif low in self._AUXILIARIES:
                    tok.pos_ = "AUX"
                    tok.dep_ = "aux"
                elif low in self._CONJUNCTIONS:
                    tok.pos_ = "CCONJ" if low in ("and", "or", "but", "nor") else "SCONJ"
                    tok.dep_ = "cc" if tok.pos_ == "CCONJ" else "mark"
                elif low in self._ADVERBS:
                    tok.pos_ = "ADV"
                    tok.dep_ = "advmod"
                elif not w.isalpha():
                    tok.pos_ = "PUNCT"
                    tok.dep_ = "punct"
                elif low in self._IRREGULAR_VERBS and not found_verb:
                    tok.pos_ = "VERB"
                    tok.dep_ = "ROOT"
                    root_idx = idx
                    found_verb = True
                elif w[0].isupper() and idx > 0:
                    tok.pos_ = "PROPN"
                    tok.dep_ = "nsubj" if not found_verb else "obl"
                elif w[0].isupper() and idx == 0 and len(words) > 1 and words[1][0:1].isupper() and words[1].isalpha():
                    # Sentence-initial word followed by another capitalised word → proper noun
                    tok.pos_ = "PROPN"
                    tok.dep_ = "nsubj"
                elif low.endswith(("ed", "ing", "es", "s")) and not found_verb:
                    tok.pos_ = "VERB"
                    tok.dep_ = "ROOT"
                    root_idx = idx
                    found_verb = True
                elif low.endswith(("ly",)):
                    tok.pos_ = "ADV"
                    tok.dep_ = "advmod"
                elif low.endswith(("tion", "ness", "ment", "ity")):
                    tok.pos_ = "NOUN"
                    tok.dep_ = "obj" if found_verb else "nsubj"
                else:
                    # Default: before verb → noun (subj), after verb → noun (obj)
                    if not found_verb:
                        tok.pos_ = "NOUN"
                        tok.dep_ = "nsubj"
                    else:
                        tok.pos_ = "NOUN"
                        tok.dep_ = "obj"

                # Lemmatise carefully: only strip inflectional suffixes for verbs
                if tok.pos_ == "VERB":
                    if low.endswith("ied"):
                        tok.lemma_ = low[:-3] + "y"
                    elif low.endswith("ed") and len(low) > 3:
                        tok.lemma_ = low[:-2] if not low.endswith("eed") else low[:-1]
                        if len(tok.lemma_) >= 3 and tok.lemma_[-1] == tok.lemma_[-2]:
                            # Doubled consonant: stopp → stop
                            tok.lemma_ = tok.lemma_[:-1]
                    elif low.endswith("ing") and len(low) > 4:
                        tok.lemma_ = low[:-3]
                        if tok.lemma_.endswith("e") and not tok.lemma_.endswith("ee"):
                            pass
                        elif len(tok.lemma_) >= 2 and tok.lemma_[-1] == tok.lemma_[-2]:
                            tok.lemma_ = tok.lemma_[:-1]
                        else:
                            tok.lemma_ = tok.lemma_ + "e" if not tok.lemma_.endswith("e") else tok.lemma_
                    elif low.endswith("es") and len(low) > 3:
                        tok.lemma_ = low[:-2] if low.endswith(("shes", "ches", "xes", "zes", "ses")) else low[:-1]
                    elif low.endswith("s") and not low.endswith("ss") and len(low) > 2:
                        tok.lemma_ = low[:-1]
                    else:
                        tok.lemma_ = low
                elif tok.pos_ in ("PROPN",):
                    tok.lemma_ = w  # keep original case for proper nouns
                else:
                    tok.lemma_ = low

                # Override with known irregulars
                _LEMMA_OVERRIDES = {
                    "was": "be", "were": "be", "is": "be", "am": "be",
                    "are": "be", "been": "be", "being": "be",
                    "had": "have", "has": "have", "having": "have",
                    "did": "do", "does": "do",
                    "went": "go", "gone": "go", "goes": "go",
                    "saw": "see", "seen": "see",
                    "said": "say", "told": "tell",
                    "made": "make", "took": "take", "taken": "take",
                    "came": "come", "knew": "know", "known": "know",
                    "gave": "give", "given": "give",
                    "found": "find", "thought": "think",
                    "got": "get", "gotten": "get",
                    "wrote": "write", "written": "write",
                    "ran": "run", "sat": "sit",
                    "spoke": "speak", "spoken": "speak",
                    "broke": "break", "broken": "break",
                    "fell": "fall", "fallen": "fall",
                    "sang": "sing", "sung": "sing",
                    "swam": "swim", "swum": "swim",
                    "flew": "fly", "flown": "fly",
                    "drew": "draw", "drawn": "draw",
                    "rose": "rise", "risen": "rise",
                    "drove": "drive", "driven": "drive",
                    "chose": "choose", "chosen": "choose",
                }
                if low in _LEMMA_OVERRIDES:
                    tok.lemma_ = _LEMMA_OVERRIDES[low]

                tokens.append(tok)

            # If no verb found, pick the first non-det/non-punct as root
            if not found_verb and tokens:
                for t in tokens:
                    if t.pos_ not in ("DET", "PUNCT", "ADP", "CCONJ", "SCONJ"):
                        t.dep_ = "ROOT"
                        t.pos_ = "VERB"
                        root_idx = t.i
                        break

            results.append(FallbackSent(tokens=tokens, root_idx=root_idx))

        return results


# ══════════════════════════════════════════════════════════════════════════
# SpacyBridge — main class
# ══════════════════════════════════════════════════════════════════════════


class SpacyBridge:
    """Convert spaCy Doc → FStructure → EventTerm, with full Grade annotation.

    When spaCy is unavailable, falls back to FallbackTokenizer for basic
    analysis with reduced confidence grades.
    """

    def __init__(self, model: str = "en_core_web_sm"):
        self._nlp = None
        self._use_spacy = False
        self._fallback = FallbackTokenizer()
        self._dm = DMEngine(ENGLISH_VIS, ENGLISH_READJUSTMENTS, ENGLISH_IMPOVERISHMENTS)

        try:
            import spacy
            self._nlp = spacy.load(model)
            self._use_spacy = True
        except Exception:
            pass

    def analyze(self, text: str) -> List[Tuple[HLF, Grade]]:
        """Full analytic pipeline: text → [EventTerm], each with confidence Grade.

        Steps:
          1. Tokenize & parse (spaCy or fallback)
          2. For each sentence: build FStructure from dependencies
          3. Convert FStructure → EventTerm
          4. Grade = parse_confidence ⊗ f-structure_wellformedness
        """
        if self._use_spacy:
            return self._analyze_spacy(text)
        return self._analyze_fallback(text)

    def get_fstructures(self, text: str) -> List[Tuple[FStructure, Grade]]:
        """Get f-structures only (without converting to EventTerm)."""
        if self._use_spacy:
            doc = self._nlp(text)
            results = []
            for sent in doc.sents:
                fs, g = self._sent_to_fstruct_spacy(sent)
                results.append((fs, g))
            return results
        return self._fstructs_fallback(text)

    # ── spaCy pipeline ───────────────────────────────────────────────────

    def _analyze_spacy(self, text: str) -> List[Tuple[HLF, Grade]]:
        doc = self._nlp(text)
        results: List[Tuple[HLF, Grade]] = []
        for sent in doc.sents:
            fs, grade = self._sent_to_fstruct_spacy(sent)
            et = fs.to_event_term()
            results.append((et, grade))
        return results

    def _sent_to_fstruct_spacy(self, sent: Any) -> Tuple[FStructure, Grade]:
        """spaCy sentence span → LFG f-structure."""
        root = sent.root
        overall_grade = Grade.from_prob(0.95)  # base spaCy confidence

        # Build root f-structure
        fs = FStructure(
            pred=root.lemma_,
            features=self._morph_to_bundle(root),
            grade=overall_grade,
        )

        # Process dependents
        for child in root.children:
            dep = child.dep_
            gf_info = _UD_TO_GF.get(dep, ("ADJ", Grade.from_prob(0.6)))
            gf, dep_grade = gf_info

            child_fs = self._token_to_fstruct(child)
            overall_grade = overall_grade * dep_grade

            if gf == "SUBJ":
                fs.subj = child_fs
                fs._role_mapping["SUBJ"] = self._guess_role_name(child, "SUBJ")
            elif gf == "OBJ":
                fs.obj = child_fs
                fs._role_mapping["OBJ"] = self._guess_role_name(child, "OBJ")
            elif gf == "OBJ-TH":
                fs.obj_th = child_fs
                fs._role_mapping["OBJ-TH"] = "recipient"
            elif gf == "OBL":
                role = self._obl_role_name(child)
                fs.obliques[role] = child_fs
            elif gf == "COMP":
                fs.comp = child_fs
            elif gf == "XCOMP":
                fs.xcomp = child_fs
            elif gf == "ADJ":
                child_fs._role_mapping["role"] = self._adj_role_name(child)
                fs.adjuncts.append(child_fs)
            elif gf.startswith("_"):
                # Functional elements (aux, det, etc.) → features
                self._incorporate_functional(fs, child, gf)

        fs.grade = overall_grade
        return fs, overall_grade

    def _token_to_fstruct(self, token: Any) -> FStructure:
        """Build sub-f-structure from a spaCy token and its subtree."""
        # Collect the full span text for multi-word expressions
        subtree_tokens = list(token.subtree)
        # Filter out functional elements
        content_tokens = [
            t for t in subtree_tokens
            if t.dep_ not in ("det", "case", "cc", "punct", "mark")
        ]

        if token.pos_ in ("NOUN", "PROPN", "PRON"):
            name = token.text
            # Check for compound/flat names
            for t in content_tokens:
                if t != token and t.dep_ in ("compound", "flat", "flat:name"):
                    if t.i < token.i:
                        name = t.text + " " + name
                    else:
                        name = name + " " + t.text

            fs = FStructure(
                referent=name,
                features=self._morph_to_bundle(token),
                grade=Grade.from_prob(0.95),
            )
            # Definiteness from determiner
            for child in token.children:
                if child.dep_ == "det":
                    if child.text.lower() in ("the", "this", "that", "these", "those"):
                        fs.definiteness = "definite"
                    else:
                        fs.definiteness = "indefinite"
            return fs

        if token.pos_ in ("VERB", "AUX"):
            # Embedded clause
            child_fs = FStructure(
                pred=token.lemma_,
                features=self._morph_to_bundle(token),
                grade=Grade.from_prob(0.85),
            )
            for child in token.children:
                dep = child.dep_
                gf_info = _UD_TO_GF.get(dep, ("ADJ", Grade.from_prob(0.6)))
                gf, _ = gf_info
                sub_fs = self._token_to_fstruct(child)
                if gf == "SUBJ":
                    child_fs.subj = sub_fs
                elif gf == "OBJ":
                    child_fs.obj = sub_fs
            return child_fs

        # Default: treat as simple referent
        return FStructure(
            referent=token.text,
            features=self._morph_to_bundle(token),
            grade=Grade.from_prob(0.80),
        )

    def _morph_to_bundle(self, token: Any) -> FeatureBundle:
        """Convert spaCy token morphology to FeatureBundle."""
        bundle = FeatureBundle()
        morph_str = str(token.morph) if hasattr(token, 'morph') else ""
        for morph_feat in morph_str.split("|"):
            morph_feat = morph_feat.strip()
            if morph_feat in _UD_MORPH_MAP:
                name, val, conf, interp = _UD_MORPH_MAP[morph_feat]
                bundle.set(name, val, Grade.from_prob(conf), interp)
        return bundle

    def _guess_role_name(self, token: Any, gf: str) -> str:
        """Guess a semantic role name from the token and its GF."""
        if gf == "SUBJ":
            head = token.head
            if head.lemma_ in (
                "see", "hear", "feel", "notice", "watch", "observe",
                "perceive", "detect", "smell", "taste",
            ):
                return "experiencer"
            if head.lemma_ in (
                "know", "think", "believe", "understand", "remember",
                "forget", "realize", "recognize", "consider", "imagine",
            ):
                return "cognizer"
            if head.lemma_ in (
                "fall", "arrive", "come", "go", "appear", "disappear",
                "exist", "happen", "occur", "emerge",
            ):
                return "theme"
            return "agent"
        if gf == "OBJ":
            return "theme"
        return "entity"

    def _obl_role_name(self, token: Any) -> str:
        """Determine oblique role name from case marker."""
        for child in token.children:
            if child.dep_ == "case":
                prep = child.text.lower()
                return {
                    "in": "location", "at": "location", "on": "location",
                    "to": "goal", "into": "goal", "toward": "goal",
                    "from": "source", "out": "source",
                    "with": "instrument", "by": "agent",
                    "for": "purpose", "about": "topic",
                    "during": "time", "before": "time", "after": "time",
                    "through": "path", "along": "path", "across": "path",
                    "between": "location", "among": "location",
                    "under": "location", "over": "location",
                    "near": "location", "beside": "location",
                    "behind": "location", "beyond": "location",
                    "against": "opposition",
                }.get(prep, "oblique")
        return "oblique"

    def _adj_role_name(self, token: Any) -> str:
        """Determine adjunct role name."""
        if token.pos_ == "ADV":
            low = token.text.lower()
            if low in ("here", "there", "everywhere", "somewhere", "nowhere"):
                return "location"
            if low in ("now", "then", "today", "yesterday", "tomorrow"):
                return "time"
            return "manner"
        if token.dep_ in ("nmod", "obl"):
            return self._obl_role_name(token)
        return "modifier"

    def _incorporate_functional(
        self, fs: FStructure, token: Any, gf: str
    ) -> None:
        """Incorporate functional elements (aux, cop, det) into f-structure features."""
        if gf == "_AUX":
            lemma = token.lemma_
            if lemma in ("will", "shall"):
                fs.features.set("Tense", "future", Grade.from_prob(0.95))
            elif lemma in ("would", "should"):
                fs.features.set("Mood", "conditional", Grade.from_prob(0.90))
            elif lemma in ("can", "could"):
                fs.features.set("Mood", "potential", Grade.from_prob(0.88))
            elif lemma in ("may", "might"):
                fs.features.set("Mood", "epistemic", Grade.from_prob(0.85))
            elif lemma == "must":
                fs.features.set("Mood", "deontic", Grade.from_prob(0.90))
            elif lemma == "have":
                fs.features.set("Aspect", "perfect", Grade.from_prob(0.95))
            elif lemma == "be":
                morph = str(token.morph)
                if "Tense=Past" in morph:
                    fs.features.set("Tense", "past", Grade.from_prob(0.95))
                if token.head.dep_ == "ROOT" and "VerbForm=Part" in str(token.head.morph):
                    fs.features.set("Aspect", "progressive", Grade.from_prob(0.93))
        elif gf == "_COP":
            morph = str(token.morph)
            if "Tense=Past" in morph:
                fs.features.set("Tense", "past", Grade.from_prob(0.93))

    # ── Fallback pipeline ────────────────────────────────────────────────

    def _analyze_fallback(self, text: str) -> List[Tuple[HLF, Grade]]:
        """Fallback analysis without spaCy."""
        sents = self._fallback.tokenize(text)
        results: List[Tuple[HLF, Grade]] = []

        for sent in sents:
            fs, grade = self._sent_to_fstruct_fallback(sent)
            et = fs.to_event_term()
            # Reduce grade for fallback mode
            fallback_penalty = Grade.from_prob(0.75)
            results.append((et, grade * fallback_penalty))

        return results

    def _fstructs_fallback(self, text: str) -> List[Tuple[FStructure, Grade]]:
        sents = self._fallback.tokenize(text)
        results: List[Tuple[FStructure, Grade]] = []
        for sent in sents:
            fs, g = self._sent_to_fstruct_fallback(sent)
            results.append((fs, g))
        return results

    def _sent_to_fstruct_fallback(
        self, sent: FallbackSent
    ) -> Tuple[FStructure, Grade]:
        """Build f-structure from fallback tokenization."""
        overall = Grade.from_prob(0.70)
        root = sent.root

        fs = FStructure(
            pred=root.lemma_,
            features=FeatureBundle(),
            grade=overall,
        )

        # Collect adjacent proper nouns as compound names
        i = 0
        tokens = sent.tokens
        while i < len(tokens):
            tok = tokens[i]
            if tok.i == sent.root_idx:
                i += 1
                continue

            # Multi-word proper noun handling
            if tok.pos_ == "PROPN":
                name_parts = [tok.text]
                j = i + 1
                while j < len(tokens) and tokens[j].pos_ == "PROPN":
                    name_parts.append(tokens[j].text)
                    j += 1
                compound_name = " ".join(name_parts)
                child_fs = FStructure(
                    referent=compound_name,
                    features=FeatureBundle(),
                    grade=Grade.from_prob(0.75),
                )
                dep = tok.dep_
                gf_info = _UD_TO_GF.get(dep, ("ADJ", Grade.from_prob(0.5)))
                gf, dep_grade = gf_info
                if gf == "SUBJ" and not fs.subj:
                    fs.subj = child_fs
                    fs._role_mapping["SUBJ"] = "agent"
                elif gf in ("OBJ",) and not fs.obj:
                    fs.obj = child_fs
                    fs._role_mapping["OBJ"] = "theme"
                elif gf == "OBL":
                    fs.obliques[compound_name.lower()] = child_fs
                else:
                    child_fs._role_mapping["role"] = compound_name.lower()
                    fs.adjuncts.append(child_fs)
                i = j
                continue

            dep = tok.dep_
            gf_info = _UD_TO_GF.get(dep, ("ADJ", Grade.from_prob(0.5)))
            gf, dep_grade = gf_info

            if gf.startswith("_"):
                # Skip functional tokens (punct, det, case, etc.)
                i += 1
                continue

            child_fs = FStructure(
                referent=tok.text,
                features=FeatureBundle(),
                grade=dep_grade,
            )

            if gf == "SUBJ" and not fs.subj:
                fs.subj = child_fs
                fs._role_mapping["SUBJ"] = "agent"
            elif gf == "OBJ" and not fs.obj:
                fs.obj = child_fs
                fs._role_mapping["OBJ"] = "theme"
            elif gf == "OBL":
                role_name = tok.lemma_ if tok.pos_ != "ADP" else "oblique"
                fs.obliques[role_name] = child_fs
            elif gf == "ADJ":
                child_fs._role_mapping["role"] = tok.lemma_
                fs.adjuncts.append(child_fs)

            i += 1

        fs.grade = overall
        return fs, overall
