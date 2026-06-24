"""Tests for agents.source_registry — YAML-backed domain registry."""
import pytest
from pathlib import Path

from agents.source_registry import SourceRegistry, get_registry, reset_registry

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "source_preferences.yaml"


@pytest.fixture(autouse=True)
def _reset():
    """Reset the singleton between tests."""
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def registry():
    return SourceRegistry(CONFIG_PATH)


# ---- loading ----

def test_loads_without_error(registry):
    assert registry is not None


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        SourceRegistry("/nonexistent/path.yaml")


# ---- trusted_score (composite merge order) ----

class TestTrustedScore:
    def test_finance_domain(self, registry):
        assert registry.trusted_score("companiesmarketcap.com") == 9

    def test_official_domain(self, registry):
        assert registry.trusted_score("sec.gov") == 10

    def test_subdomain_match(self, registry):
        # subdomain of a trusted domain should match
        assert registry.trusted_score("en.wikipedia.org") == 7

    def test_normalizes_domain_case_and_www_prefix(self, registry):
        assert registry.trusted_score("WWW.XUEQIU.COM") == 4
        assert registry.official_score("www.SEC.gov") == 10
        assert registry.is_encyclopedia("WWW.WIKIPEDIA.ORG") is True

    def test_unknown_domain_returns_zero(self, registry):
        assert registry.trusted_score("random-site.xyz") == 0

    def test_merge_order_xueqiu(self, registry):
        # xueqiu.com appears in finance(3), china_market(4), news(3)
        # merge order: finance → official → news → profile → glossary → china_market
        # china_market is last among those, so china_market score (4) wins
        assert registry.trusted_score("xueqiu.com") == 4

    def test_merge_order_stcn(self, registry):
        # stcn.com in finance(4), china_market(8), news(5) → china_market(8) wins
        assert registry.trusted_score("stcn.com") == 8


# ---- category-specific scores ----

class TestCategoryScores:
    def test_official_score(self, registry):
        assert registry.official_score("federalreserve.gov") == 12
        assert registry.official_score("sec.gov") == 10

    def test_low_quality_score(self, registry):
        assert registry.low_quality_score("douyin.com") == -8
        assert registry.low_quality_score("zhidao.baidu.com") == -6

    def test_weak_secondary_score(self, registry):
        assert registry.weak_secondary_score("csdn.net") == -4
        assert registry.weak_secondary_score("zhihu.com") == -3

    def test_profile_score(self, registry):
        assert registry.profile_score("britannica.com") == 8

    def test_glossary_score(self, registry):
        assert registry.glossary_score("investopedia.com") == 8

    def test_news_score(self, registry):
        assert registry.news_score("reuters.com") == 8

    def test_finance_score(self, registry):
        assert registry.finance_score("finance.yahoo.com") == 8

    def test_china_market_score(self, registry):
        assert registry.china_market_score("eastmoney.com") == 8


# ---- non-score lookups ----

class TestNonScoreLookups:
    def test_preferred_kind_domains(self, registry):
        domains = registry.preferred_kind_domains("finance_glossary")
        assert "investopedia.com" in domains
        assert "corporatefinanceinstitute.com" in domains

    def test_preferred_kind_domains_unknown(self, registry):
        assert registry.preferred_kind_domains("nonexistent_kind") == []

    def test_identity_alias_domains(self, registry):
        assert registry.identity_alias_domains("investopedia") == ("investopedia.com",)
        assert registry.identity_alias_domains("cfi") == ("corporatefinanceinstitute.com",)

    def test_identity_alias_unknown(self, registry):
        assert registry.identity_alias_domains("unknown_alias") == ()

    def test_identity_aliases_property(self, registry):
        aliases = registry.identity_aliases
        assert "investopedia" in aliases

    def test_is_language_dictionary(self, registry):
        assert registry.is_language_dictionary("dictionary.cambridge.org") is True
        assert registry.is_language_dictionary("www.dictionary.com") is True
        assert registry.is_language_dictionary("google.com") is False

    def test_is_encyclopedia(self, registry):
        assert registry.is_encyclopedia("wikipedia.org") is True
        assert registry.is_encyclopedia("en.wikipedia.org") is True
        assert registry.is_encyclopedia("baike.baidu.com") is True
        assert registry.is_encyclopedia("google.com") is False

    def test_encyclopedia_domains_property(self, registry):
        domains = registry.encyclopedia_domains
        assert "wikipedia.org" in domains
        assert len(domains) == 3

    def test_domain_roles_reports_all_matching_categories(self, registry):
        roles = registry.domain_roles("xueqiu.com")
        assert "finance" in roles
        assert "china_market" in roles
        assert "news" in roles
        assert "low_quality" in roles

    def test_domain_scores_reports_score_metadata(self, registry):
        scores = registry.domain_scores("sec.gov")
        assert scores["trusted"] == 10
        assert scores["official"] == 10
        assert scores["low_quality"] == 0

    def test_language_dictionary_domains_property(self, registry):
        domains = registry.language_dictionary_domains
        assert "dictionary.com" in domains
        assert len(domains) == 5

    def test_multi_source_preferred_kinds(self, registry):
        kinds = registry.multi_source_preferred_kinds
        assert "glossary" in kinds
        assert "encyclopedia" in kinds
        assert len(kinds) == 8

    def test_is_multi_source_preferred_kind(self, registry):
        assert registry.is_multi_source_preferred_kind("glossary") is True
        assert registry.is_multi_source_preferred_kind("random_kind") is False

    def test_greek_letter_name(self, registry):
        assert registry.greek_letter_name("α") == "alpha"
        assert registry.greek_letter_name("Ω") == "omega"  # case-insensitive
        assert registry.greek_letter_name("z") is None  # not a Greek letter


# ---- raw access properties ----

class TestRawAccess:
    def test_trusted_domains(self, registry):
        d = registry.trusted_domains
        assert isinstance(d, dict)
        assert "sec.gov" in d

    def test_official_domains(self, registry):
        d = registry.official_domains
        assert "federalreserve.gov" in d

    def test_low_quality_domains(self, registry):
        d = registry.low_quality_domains
        assert "douyin.com" in d

    def test_weak_secondary_domains(self, registry):
        d = registry.weak_secondary_domains
        assert "csdn.net" in d

    def test_raw_returns_copies(self, registry):
        """Mutating returned dicts must not affect the registry."""
        d = registry.trusted_domains
        d["evil.com"] = 999
        assert registry.trusted_score("evil.com") == 0


# ---- module-level singleton ----

class TestSingleton:
    def test_get_registry_returns_same_instance(self):
        r1 = get_registry(CONFIG_PATH)
        r2 = get_registry()
        assert r1 is r2

    def test_reset_clears_instance(self):
        r1 = get_registry(CONFIG_PATH)
        reset_registry()
        r2 = get_registry(CONFIG_PATH)
        assert r1 is not r2
