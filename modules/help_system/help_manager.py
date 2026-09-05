# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Help Manager

Central manager for the help system providing search, indexing, and topic management.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from .help_content import HelpCategory, HelpTopic

logger = logging.getLogger(__name__)


class HelpManager:
    """Central manager for help system"""

    _instance = None

    def __init__(self):
        from .help_content import HELP_TOPICS

        self.topics: Dict[str, HelpTopic] = HELP_TOPICS
        self._search_index: Dict[str, List[str]] = {}
        self._build_search_index()
        logger.info(f"HelpManager initialized with {len(self.topics)} topics")

    @classmethod
    def get_instance(cls) -> "HelpManager":
        if cls._instance is None:
            cls._instance = HelpManager()
        return cls._instance

    def _build_search_index(self):
        """Build keyword search index"""
        self._search_index.clear()

        for topic_id, topic in self.topics.items():
            # Index by keywords
            for keyword in topic.keywords:
                keyword_lower = keyword.lower()
                if keyword_lower not in self._search_index:
                    self._search_index[keyword_lower] = []
                if topic_id not in self._search_index[keyword_lower]:
                    self._search_index[keyword_lower].append(topic_id)

            # Index by title words (3+ characters)
            for word in topic.title.lower().split():
                if len(word) > 2:  # Skip very short words
                    if word not in self._search_index:
                        self._search_index[word] = []
                    if topic_id not in self._search_index[word]:
                        self._search_index[word].append(topic_id)

            # Index by category name
            category_name = topic.category.value.replace("_", " ")
            if category_name not in self._search_index:
                self._search_index[category_name] = []
            if topic_id not in self._search_index[category_name]:
                self._search_index[category_name].append(topic_id)

        logger.debug(f"Built search index with {len(self._search_index)} keywords")

    def get_topic(self, topic_id: str) -> Optional[HelpTopic]:
        """Get a help topic by ID"""
        return self.topics.get(topic_id)

    def get_topics_by_category(self, category: HelpCategory) -> List[HelpTopic]:
        """Get all topics in a category"""
        return [t for t in self.topics.values() if t.category == category]

    def get_all_categories(self) -> List[HelpCategory]:
        """Get all categories that have topics"""
        categories = set()
        for topic in self.topics.values():
            categories.add(topic.category)
        return sorted(list(categories), key=lambda x: x.value)

    def search(self, query: str, limit: int = 10) -> List[HelpTopic]:
        """Search help topics"""
        if not query or len(query.strip()) == 0:
            return []

        query_lower = query.lower().strip()
        results: Dict[str, float] = {}  # topic_id -> relevance score

        # Split query into words for multi-term search
        query_words = query_lower.split()

        # Search keywords and title words
        for word in query_words:
            if len(word) < 2:  # Skip very short words
                continue

            if word in self._search_index:
                for topic_id in self._search_index[word]:
                    # Base score for keyword match
                    score = 2.0 if word in [k.lower() for k in self.topics[topic_id].keywords] else 1.0
                    results[topic_id] = results.get(topic_id, 0) + score

            # Partial matches for longer queries
            if len(word) > 3:
                for keyword, topic_ids in self._search_index.items():
                    if word in keyword or keyword in word:
                        for topic_id in topic_ids:
                            results[topic_id] = results.get(topic_id, 0) + 0.5

        # Search in content and summaries
        for topic_id, topic in self.topics.items():
            content_score = 0

            # Search in content (lower weight)
            if query_lower in topic.content.lower():
                content_score += 0.5

            # Search in summary (higher weight)
            if query_lower in topic.summary.lower():
                content_score += 1.0

            # Bonus for exact phrase matches
            if query_lower in topic.title.lower():
                content_score += 3.0

            if content_score > 0:
                results[topic_id] = results.get(topic_id, 0) + content_score

        # Sort by relevance score (descending)
        sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)

        # Return topics, limited by count
        result_topics = []
        for topic_id, score in sorted_results[:limit]:
            topic = self.topics.get(topic_id)
            if topic:
                result_topics.append(topic)

        logger.debug(f"Search for '{query}' returned {len(result_topics)} results")
        return result_topics

    def get_topic_for_context(self, ui_context: str) -> Optional[HelpTopic]:
        """Get help topic for a specific UI context"""
        for topic in self.topics.values():
            if topic.ui_context == ui_context:
                return topic
        return None

    def get_related_topics(self, topic_id: str) -> List[HelpTopic]:
        """Get related topics"""
        topic = self.get_topic(topic_id)
        if not topic:
            return []

        related = []
        for related_id in topic.related_topics:
            related_topic = self.topics.get(related_id)
            if related_topic:
                related.append(related_topic)

        return related

    def get_adjacent_topics(self, topic_id: str) -> tuple:
        """Get previous and next topics within the same category for sequential reading.

        Returns:
            Tuple of (prev_topic, next_topic), either may be None
        """
        topic = self.get_topic(topic_id)
        if not topic:
            return None, None

        category_topics = self.get_topics_by_category(topic.category)
        current_index = None
        for i, t in enumerate(category_topics):
            if t.id == topic_id:
                current_index = i
                break

        if current_index is None:
            return None, None

        prev_topic = category_topics[current_index - 1] if current_index > 0 else None
        next_topic = category_topics[current_index + 1] if current_index < len(category_topics) - 1 else None

        return prev_topic, next_topic

    def get_popular_topics(self, limit: int = 5) -> List[HelpTopic]:
        """Get popular/recommended topics for quick access"""
        # Return some key topics that users commonly need
        popular_ids = [
            "getting_started_intro",
            "twitch_setup",
            "alerts_overview",
            "integrations_spotify",
            "integrations_psn",
            "obs_setup",
            "troubleshooting_alerts",
            "templates_intro",
        ]

        popular = []
        for topic_id in popular_ids:
            topic = self.get_topic(topic_id)
            if topic:
                popular.append(topic)
            if len(popular) >= limit:
                break

        return popular

    def get_topic_count(self) -> int:
        """Get total number of help topics"""
        return len(self.topics)

    def get_category_count(self, category: HelpCategory) -> int:
        """Get number of topics in a category"""
        return len(self.get_topics_by_category(category))

    def validate_topics(self) -> List[str]:
        """Validate help topics for consistency and completeness"""
        errors = []

        for topic_id, topic in self.topics.items():
            # Check required fields
            if not topic.title or not topic.title.strip():
                errors.append(f"Topic {topic_id}: Missing title")

            if not topic.content or not topic.content.strip():
                errors.append(f"Topic {topic_id}: Missing content")

            if not topic.summary or not topic.summary.strip():
                errors.append(f"Topic {topic_id}: Missing summary")

            # Check related topics exist
            for related_id in topic.related_topics:
                if related_id not in self.topics:
                    errors.append(f"Topic {topic_id}: Related topic '{related_id}' not found")

            # Check keywords are reasonable
            if len(topic.keywords) == 0:
                errors.append(f"Topic {topic_id}: No keywords defined")

        return errors


def get_help_manager() -> HelpManager:
    """Get the singleton help manager instance"""
    return HelpManager.get_instance()