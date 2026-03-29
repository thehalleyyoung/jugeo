"""WikiKnowledgeReasoner: merges static DEFAULT_RULES with wiki-sourced rules
and runs defeasible reasoning to produce graded derived facts.

Usage::

    reasoner = WikiKnowledgeReasoner()
    facts = reasoner.reason_about("bee")
    # facts: [("produces_honey", Grade(0.9)), ("is_insect", Grade(0.85)), ...]
    answer = reasoner.answer_question("What do bees do?")
    words  = reasoner.imagery_for("bee")
"""
from __future__ import annotations
from typing import List, Tuple, Optional

from gofai_chat.core.grade import Grade


def _priority_resolution(rules: list) -> List[Tuple[str, Grade]]:
    """Apply priority-based defeasible resolution to a list of DefaultRules.

    Higher-priority rules win when two rules produce contradicting conclusions.
    A contradiction is detected when one rule's conclusion is the negation of
    another's (prefixed with ``not_``).

    Returns a deduplicated list of ``(conclusion, Grade)`` sorted by strength.
    """
    # Sort highest priority first so winners are processed before losers.
    sorted_rules = sorted(rules, key=lambda r: (-r.priority, -r.strength.to_prob()))

    accepted: dict = {}   # conclusion → (Grade, priority)
    blocked: set = set()  # conclusions blocked by higher-priority negations

    for rule in sorted_rules:
        concl = rule.conclusion
        if concl in blocked:
            continue

        # Check if a higher-priority rule negates this conclusion.
        neg = ('not_' + concl) if not concl.startswith('not_') else concl[4:]
        if neg in accepted and accepted[neg][1] >= rule.priority:
            blocked.add(concl)
            continue

        if concl not in accepted:
            accepted[concl] = (rule.strength, rule.priority)
        else:
            # Keep the higher-strength version of a duplicate.
            if rule.strength.to_prob() > accepted[concl][0].to_prob():
                accepted[concl] = (rule.strength, rule.priority)

    results = [(c, g) for c, (g, _) in accepted.items()]
    results.sort(key=lambda x: x[1].to_prob(), reverse=True)
    return results


class WikiKnowledgeReasoner:
    """Combines static defaults + wiki-learned rules for defeasible reasoning."""

    def __init__(self):
        from gofai_chat.inference.defaults import DEFAULT_RULES
        from gofai_chat.knowledge.knowledge_db import KnowledgeDB

        self._static_rules = list(DEFAULT_RULES)
        self._db = KnowledgeDB()

    def reason_about(self, entity_type: str) -> List[Tuple[str, Grade]]:
        """Return all derived properties for entity_type, merging static + wiki rules."""
        et = entity_type.lower().strip()
        wiki_rules = self._db.retrieve_rules(entity_type)

        # Filter static rules to those matching the entity type.
        static_matching = [
            r for r in self._static_rules
            if r.condition.lower() == et
        ]
        all_rules = static_matching + wiki_rules
        if not all_rules:
            return []

        return _priority_resolution(all_rules)

    def answer_question(self, question: str, topic: Optional[str] = None) -> Optional[str]:
        """Generate a factual answer by reasoning about the topic.

        Returns None if no relevant facts are found (caller should fall through).
        """
        if topic is None:
            topic = self._extract_topic(question)
        if not topic:
            return None

        facts = self.reason_about(topic)
        if not facts:
            return None

        fact_strings = []
        for prop, grade in facts[:5]:
            if grade.to_prob() < 0.4:
                continue
            fact_strings.append(prop.replace('_', ' '))

        if not fact_strings:
            return None

        lines = [f"Based on what I know about **{topic}**:"]
        for fact in fact_strings:
            lines.append(f"  \u2022 {topic.title()} {fact}.")

        typical = self.most_typical_properties(topic, top_k=3)
        if typical:
            props = ", ".join(p.replace('_', ' ') for p, v in typical if v > 0.5)
            if props:
                lines.append(f"\nTypically: {props}.")

        return "\n".join(lines)

    def most_typical_properties(self, entity_type: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """Return the top-k most typical properties for an entity type."""
        facts = self.reason_about(entity_type)
        results = [(prop, grade.to_prob()) for prop, grade in facts if grade.to_prob() > 0.5]
        return results[:top_k]

    def imagery_for(self, topic: str) -> List[str]:
        """Return imagery words for a topic: stored wiki words + derived properties."""
        wiki_images = self._db.retrieve_imagery(topic)
        derived = []
        for prop, grade in self.reason_about(topic)[:8]:
            if grade.to_prob() > 0.6:
                # "produces_honey" → "honey" is the concrete imagery word
                parts = prop.split('_')
                if len(parts) > 1:
                    derived.append(parts[-1])
        return list(dict.fromkeys(wiki_images + derived))[:30]

    def fetch_and_store(self, query: str) -> Tuple[Optional[object], List[Tuple[str, Grade]]]:
        """Fetch a Wikipedia article, convert to rules, store in DB, return (article, facts)."""
        from gofai_chat.knowledge.wikipedia_source import WikipediaKnowledgeSource
        from gofai_chat.knowledge.wiki_to_lf import WikiArticleToLF

        wks = WikipediaKnowledgeSource()
        converter = WikiArticleToLF()

        article = wks.search_and_fetch(query)
        if not article:
            return None, []

        rules = converter.convert(article)
        article_id = self._db.store_article(article)
        if article_id > 0:
            self._db.store_rules(article_id, rules)
            for rule in rules:
                prob = rule.strength.to_prob()
                self._db.store_typicality(rule.condition, rule.conclusion, prob)

        facts = self.reason_about(article.title.lower().split()[0])
        return article, facts

    def _extract_topic(self, question: str) -> str:
        """Extract the main topic noun from a question string."""
        stop = {
            'what', 'where', 'when', 'who', 'how', 'why',
            'is', 'are', 'was', 'were', 'does', 'do', 'did',
            'tell', 'me', 'about', 'the', 'a', 'an', 'please',
        }
        words = [w.strip('?.,!').lower() for w in question.split()]
        candidates = [w for w in words if w not in stop and len(w) > 2]
        return candidates[-1] if candidates else ''

