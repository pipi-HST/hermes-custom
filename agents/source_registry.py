"""Load and query the source-preference registry from YAML config.

Replaces the hardcoded domain dictionaries that were previously defined
at the top of ``research_bridge.py``.  Each domain may appear in multiple
trusted sections; the loader merges them in a defined order so the last
section wins for overlapping domains (see ``_TRUSTED_MERGE_ORDER``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "source_preferences.yaml"

# Merge order: later sections overwrite earlier ones for shared domains.
# china_market is last so it wins for Chinese market-specific sources
# (stcn.com, 21jingji.com, xueqiu.com, sse.com.cn, szse.cn).
_TRUSTED_MERGE_ORDER = ("finance", "official", "news", "profile", "glossary", "china_market")


class SourceRegistry:
    """Singleton-ish registry.  Call :meth:`load` once at startup (or let
    :func:`get_registry` handle it lazily)."""

    def __init__(self, config_path: str | Path) -> None:
        self._config_path = Path(config_path)
        self._raw: dict[str, Any] = {}
        self._trusted: dict[str, int] = {}
        self._official: dict[str, int] = {}
        self._low_quality: dict[str, int] = {}
        self._weak_secondary: dict[str, int] = {}
        self._profile: dict[str, int] = {}
        self._glossary: dict[str, int] = {}
        self._news: dict[str, int] = {}
        self._finance: dict[str, int] = {}
        self._china_market: dict[str, int] = {}
        self._source_kind_query_domains: dict[str, list[str]] = {}
        self._source_identity_aliases: dict[str, tuple[str, ...]] = {}
        self._language_dictionary_domains: set[str] = set()
        self._multi_source_preferred_kinds: set[str] = set()
        self._greek_letter_names: dict[str, str] = {}
        self._encyclopedia_domains: set[str] = set()
        self._reload()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        with open(self._config_path, encoding="utf-8") as fh:
            self._raw = yaml.safe_load(fh) or {}

        trusted_sections = self._raw.get("trusted") or {}
        self._finance = dict(trusted_sections.get("finance") or {})
        self._official = dict(trusted_sections.get("official") or {})
        self._china_market = dict(trusted_sections.get("china_market") or {})
        news = dict(trusted_sections.get("news") or {})
        profile = dict(trusted_sections.get("profile") or {})
        glossary = dict(trusted_sections.get("glossary") or {})

        # Build composite trusted dict with same merge order as original.
        merged: dict[str, int] = {}
        for section_name in _TRUSTED_MERGE_ORDER:
            merged.update(trusted_sections.get(section_name) or {})
        self._trusted = merged
        self._news = news
        self._profile = profile
        self._glossary = glossary

        self._low_quality = dict(self._raw.get("low_quality") or {})
        self._weak_secondary = dict(self._raw.get("weak_secondary") or {})

        self._source_kind_query_domains = {
            str(k): list(v)
            for k, v in (self._raw.get("source_kind_query_domains") or {}).items()
        }
        self._source_identity_aliases = {
            str(k): tuple(v)
            for k, v in (self._raw.get("source_identity_aliases") or {}).items()
        }
        self._language_dictionary_domains = set(self._raw.get("language_dictionary_domains") or [])
        self._multi_source_preferred_kinds = set(self._raw.get("multi_source_preferred_kinds") or [])
        self._greek_letter_names = dict(self._raw.get("greek_letter_names") or {})
        self._encyclopedia_domains = set(self._raw.get("encyclopedia_domains") or [])

    # ------------------------------------------------------------------
    # Domain scoring (replaces _domain_score(domain, DICT))
    # ------------------------------------------------------------------

    @staticmethod
    def _match(domain: str, scores: dict[str, int]) -> int:
        normalized_domain = str(domain or "").lower().removeprefix("www.")
        for candidate, score in scores.items():
            normalized_candidate = str(candidate or "").lower().removeprefix("www.")
            if normalized_domain == normalized_candidate or normalized_domain.endswith(f".{normalized_candidate}"):
                return score
        return 0

    def trusted_score(self, domain: str) -> int:
        return self._match(domain, self._trusted)

    def official_score(self, domain: str) -> int:
        return self._match(domain, self._official)

    def low_quality_score(self, domain: str) -> int:
        return self._match(domain, self._low_quality)

    def weak_secondary_score(self, domain: str) -> int:
        return self._match(domain, self._weak_secondary)

    def profile_score(self, domain: str) -> int:
        return self._match(domain, self._profile)

    def glossary_score(self, domain: str) -> int:
        return self._match(domain, self._glossary)

    def news_score(self, domain: str) -> int:
        return self._match(domain, self._news)

    def finance_score(self, domain: str) -> int:
        return self._match(domain, self._finance)

    def china_market_score(self, domain: str) -> int:
        return self._match(domain, self._china_market)

    # ------------------------------------------------------------------
    # Non-score lookups
    # ------------------------------------------------------------------

    def preferred_kind_domains(self, kind: str) -> list[str]:
        return list(self._source_kind_query_domains.get(kind.strip().lower(), []))

    def identity_alias_domains(self, alias: str) -> tuple[str, ...]:
        return self._source_identity_aliases.get(alias, ())

    @property
    def identity_aliases(self) -> dict[str, tuple[str, ...]]:
        return self._source_identity_aliases

    def is_language_dictionary(self, domain: str) -> bool:
        clean = domain.lower().removeprefix("www.")
        return any(
            clean == item or clean.endswith(f".{item}")
            for item in self._language_dictionary_domains
        )

    def is_encyclopedia(self, domain: str) -> bool:
        clean = str(domain or "").lower().removeprefix("www.")
        return any(
            clean == item or clean.endswith(f".{item}")
            for item in self._encyclopedia_domains
        )

    def domain_roles(self, domain: str) -> list[str]:
        """Return all configured roles that match this domain."""
        roles: list[str] = []
        category_checks = [
            ("finance", self.finance_score),
            ("official", self.official_score),
            ("china_market", self.china_market_score),
            ("news", self.news_score),
            ("profile", self.profile_score),
            ("glossary", self.glossary_score),
            ("low_quality", self.low_quality_score),
            ("weak_secondary", self.weak_secondary_score),
        ]
        for role, checker in category_checks:
            if checker(domain):
                roles.append(role)
        if self.is_encyclopedia(domain):
            roles.append("encyclopedia")
        if self.is_language_dictionary(domain):
            roles.append("language_dictionary")
        return roles

    def domain_scores(self, domain: str) -> dict[str, int]:
        """Return score metadata for ranking hints and critic prompts."""
        return {
            "trusted": self.trusted_score(domain),
            "official": self.official_score(domain),
            "finance": self.finance_score(domain),
            "china_market": self.china_market_score(domain),
            "news": self.news_score(domain),
            "profile": self.profile_score(domain),
            "glossary": self.glossary_score(domain),
            "low_quality": self.low_quality_score(domain),
            "weak_secondary": self.weak_secondary_score(domain),
        }

    def is_multi_source_preferred_kind(self, kind: str) -> bool:
        return kind.strip().lower() in self._multi_source_preferred_kinds

    @property
    def multi_source_preferred_kinds(self) -> set[str]:
        return set(self._multi_source_preferred_kinds)

    def greek_letter_name(self, char: str) -> str | None:
        return self._greek_letter_names.get(char.lower())

    @property
    def language_dictionary_domains(self) -> set[str]:
        return set(self._language_dictionary_domains)

    @property
    def encyclopedia_domains(self) -> set[str]:
        return set(self._encyclopedia_domains)

    # ------------------------------------------------------------------
    # Raw access (for backward-compat if needed during migration)
    # ------------------------------------------------------------------

    @property
    def trusted_domains(self) -> dict[str, int]:
        return dict(self._trusted)

    @property
    def official_domains(self) -> dict[str, int]:
        return dict(self._official)

    @property
    def low_quality_domains(self) -> dict[str, int]:
        return dict(self._low_quality)

    @property
    def weak_secondary_domains(self) -> dict[str, int]:
        return dict(self._weak_secondary)


# ------------------------------------------------------------------
# Module-level convenience
# ------------------------------------------------------------------

_instance: SourceRegistry | None = None


def get_registry(config_path: str | Path | None = None) -> SourceRegistry:
    """Return the shared registry instance, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = SourceRegistry(config_path or _DEFAULT_CONFIG_PATH)
    return _instance


def reset_registry() -> None:
    """Drop the cached instance (for tests)."""
    global _instance
    _instance = None
