"""Convert WikiArticle.key_facts (SPO dicts) → DefaultRule objects.

Each fact dict like {"subject": "bee", "predicate": "produces", "object": "honey"}
becomes:
  DefaultRule(name="wiki_bee_produces_honey", condition="bee",
              conclusion="produces_honey", strength=Grade.from_prob(0.9), priority=50)

These are stored in KnowledgeDB and retrieved for reasoning.
"""
from __future__ import annotations
from typing import List, Optional

from gofai_chat.core.grade import Grade


# Predicate → confidence mapping: how strongly to trust Wikipedia SPO facts.
_PREDICATE_STRENGTH: dict = {
    'is_a': 0.95, 'is': 0.95, 'are': 0.95,
    'produces': 0.9, 'has': 0.85, 'have': 0.85,
    'consists_of': 0.85, 'contains': 0.85, 'made_of': 0.9,
    'lives_in': 0.8, 'found_in': 0.8, 'inhabits': 0.8,
    'eats': 0.8, 'feeds_on': 0.8, 'consumes': 0.8,
    'creates': 0.85, 'builds': 0.85, 'makes': 0.85,
    'causes': 0.75, 'leads_to': 0.7,
    'characterized_by': 0.8, 'known_for': 0.85,
}
_DEFAULT_STRENGTH = 0.7


class WikiArticleToLF:
    """Convert a WikiArticle to DefaultRule objects."""

    def convert(self, article) -> List:
        """Return a list of DefaultRule objects from article.key_facts + frame_names."""
        from gofai_chat.inference.defaults import DefaultRule

        rules = []
        seen_names: set = set()

        for fact in getattr(article, 'key_facts', []):
            rule = self._fact_to_rule(fact, seen_names)
            if rule is not None:
                rules.append(rule)

        # Derive frame-evocation rules from frame_names.
        title_key = article.title.lower().replace(' ', '_')
        for frame in getattr(article, 'frame_names', [])[:5]:
            name = f"wiki_{title_key}_frame_{frame.lower()}"[:80]
            if name in seen_names:
                continue
            seen_names.add(name)
            # Condition: first word of title (e.g., "bee" from "Bee")
            condition = article.title.lower().split()[0] if article.title else title_key
            rules.append(DefaultRule(
                name=name,
                condition=condition,
                conclusion=f"evokes_{frame.lower().replace(' ', '_')}",
                strength=Grade.from_prob(0.65),
                priority=40,
                exceptions=[],
                description=f"Frame from Wikipedia: {article.title}",
            ))
        return rules

    def _fact_to_rule(self, fact, seen_names: set) -> Optional[object]:
        """Convert one SPO fact (dict or tuple/list) to a DefaultRule, or None."""
        from gofai_chat.inference.defaults import DefaultRule
        try:
            if isinstance(fact, dict):
                subj = str(fact.get('subject', '') or fact.get('s', ''))
                pred = str(fact.get('predicate', '') or fact.get('p', ''))
                obj = str(fact.get('object', '') or fact.get('o', ''))
            elif isinstance(fact, (list, tuple)) and len(fact) >= 3:
                subj, pred, obj = str(fact[0]), str(fact[1]), str(fact[2])
            elif isinstance(fact, str):
                parts = fact.split()
                if len(parts) < 3:
                    return None
                subj, pred, obj = parts[0], parts[1], ' '.join(parts[2:])
            else:
                return None

            subj = subj.lower().strip().replace(' ', '_')
            pred = pred.lower().strip().replace(' ', '_')
            obj = obj.lower().strip().replace(' ', '_')[:40]

            if not subj or not pred or not obj:
                return None

            strength = _PREDICATE_STRENGTH.get(pred, _DEFAULT_STRENGTH)
            conclusion = f"{pred}_{obj}".replace('-', '_')
            name = f"wiki_{subj}_{conclusion}"[:80]

            if name in seen_names:
                return None
            seen_names.add(name)

            return DefaultRule(
                name=name,
                condition=subj,
                conclusion=conclusion,
                strength=Grade.from_prob(strength),
                priority=50,
                exceptions=[],
                description=f"From Wikipedia: {subj} {pred} {obj}",
            )
        except Exception:
            return None

    def to_hlf(self, rule) -> Optional[object]:
        """Convert a DefaultRule to an HLF EventTerm (best-effort)."""
        try:
            from gofai_chat.core.terms import EventTerm
            parts = rule.conclusion.split('_', 1)
            pred = parts[0] if parts else rule.conclusion
            obj = parts[1] if len(parts) > 1 else ''
            roles = {'theme': obj} if obj else {}
            return EventTerm(
                grade=rule.strength,
                frame_type_name=pred.capitalize() + '_event',
                event_var='e',
                roles=roles,
            )
        except Exception:
            return None
