from types import SimpleNamespace

from careers_os.domain.opportunity_decision import PursueRecommendation
from careers_os.ingestion.discovery import DiscoveryOpportunity
from careers_os.notifications.clustering import (
    cluster_opportunities,
    rank_clusters,
    title_family,
)


def _opp(
    company: str,
    title: str,
    location: str | None = None,
    pursue: PursueRecommendation = PursueRecommendation.STRONG_PURSUE,
    rank_score: float = 0.5,
    job_id: int = 1,
) -> DiscoveryOpportunity:
    job = SimpleNamespace(id=job_id, title=title, company=company, location=location, source="test")
    decision = SimpleNamespace(pursue=SimpleNamespace(recommendation=pursue))
    return DiscoveryOpportunity(
        job=job, fit=None, bridge=None, immediate=None, direction=None,
        matched_profiles=[], rank_score=rank_score, decision=decision,
    )


class TestTitleFamily:
    def test_plain_title_has_no_suffix_to_strip(self):
        assert title_family("Applied AI Architect") == "applied ai architect"

    def test_strips_generic_segment_noise(self):
        assert title_family("Applied AI Engineer, Enterprise") == "applied ai engineer"
        assert title_family("Solutions Architect, Startups") == "solutions architect"

    def test_strips_location_derived_suffix_using_structured_location(self):
        assert title_family("Applied AI Architect - Tokyo", "Tokyo, Japan") == "applied ai architect"

    def test_last_word_fallback_handles_multi_word_qualifiers(self):
        assert title_family("Applied AI Architect, Large Enterprise") == "applied ai architect"
        a = title_family("Forward Deployed Infrastructure Engineer, New Grad - UK Government", "London, UK")
        b = title_family("Forward Deployed Infrastructure Engineer, New Grad - US Government", "New York, NY")
        assert a == b == "forward deployed infrastructure engineer"

    def test_does_not_blindly_collapse_a_comma_in_a_real_title(self):
        # "Forward Deployed Engineering" is neither a generic qualifier nor
        # a location — must not be stripped, or two unrelated roles would
        # incorrectly merge.
        family = title_family("Physical Design Engineer, Forward Deployed Engineering", "San Francisco")
        assert family == "physical design engineer forward deployed engineering"

    def test_genuinely_different_specializations_stay_distinct(self):
        backend = title_family("Software Engineer, Backend", "San Francisco")
        frontend = title_family("Software Engineer, Frontend", "San Francisco")
        assert backend != frontend

    def test_unrelated_team_code_name_in_parens_is_not_stripped(self):
        # "Codex" isn't a generic qualifier or a location — conservative
        # under-clustering is preferred over guessing at internal jargon.
        family = title_family("Manager, Applied AI Engineering (Codex)")
        assert "codex" in family


class TestClustering:
    def test_same_company_same_family_clusters_together(self):
        opps = [
            _opp("openai", "Applied AI Engineer, Enterprise", "San Francisco", job_id=1),
            _opp("openai", "Applied AI Engineer, Enterprise", "New York, NY", job_id=2),
        ]
        clusters = cluster_opportunities(opps)
        assert len(clusters) == 1
        assert clusters[0].additional_variant_count == 1

    def test_same_title_different_companies_does_not_cluster(self):
        opps = [
            _opp("openai", "Solutions Architect", job_id=1),
            _opp("ramp", "Solutions Architect", job_id=2),
        ]
        clusters = cluster_opportunities(opps)
        assert len(clusters) == 2

    def test_sufficiently_different_roles_same_company_do_not_cluster(self):
        opps = [
            _opp("openai", "Applied AI Engineer", job_id=1),
            _opp("openai", "Partner Applied AI Engineer", job_id=2),
        ]
        clusters = cluster_opportunities(opps)
        assert len(clusters) == 2

    def test_representative_is_the_highest_ranked_variant(self):
        weak = _opp("openai", "Applied AI Engineer, Enterprise", rank_score=0.4, job_id=1)
        strong = _opp("openai", "Applied AI Engineer, Enterprise", rank_score=0.9, job_id=2)
        clusters = cluster_opportunities([weak, strong])
        assert len(clusters) == 1
        assert clusters[0].representative.job.id == 2

    def test_representative_prefers_higher_pursue_tier_over_raw_rank_score(self):
        # A lower rank_score but STRONG_PURSUE must still win over a
        # higher rank_score at a lower pursue tier — pursue tier is the
        # existing, authoritative signal; rank_score only tie-breaks.
        pursue_tier = _opp(
            "openai", "Applied AI Engineer, Enterprise",
            pursue=PursueRecommendation.PURSUE, rank_score=0.95, job_id=1,
        )
        strong_pursue_tier = _opp(
            "openai", "Applied AI Engineer, Enterprise",
            pursue=PursueRecommendation.STRONG_PURSUE, rank_score=0.5, job_id=2,
        )
        clusters = cluster_opportunities([pursue_tier, strong_pursue_tier])
        assert clusters[0].representative.job.id == 2

    def test_provider_identity_never_affects_clustering_key(self):
        # Greenhouse/Ashby/Lever opportunities with the same company and
        # title family must cluster identically regardless of source.
        opps = [
            _opp("openai", "Applied AI Engineer, Enterprise", job_id=1),
        ]
        opps[0].job.source = "ashby"
        opps.append(_opp("openai", "Applied AI Engineer, Enterprise", job_id=2))
        opps[1].job.source = "greenhouse"
        clusters = cluster_opportunities(opps)
        assert len(clusters) == 1


class TestRankClusters:
    def test_orders_by_pursue_tier_then_rank_score(self):
        low = cluster_opportunities([_opp("a", "Role One", rank_score=0.3, job_id=1)])[0]
        high = cluster_opportunities([_opp("b", "Role Two", rank_score=0.9, job_id=2)])[0]
        ranked = rank_clusters([low, high])
        assert ranked == [high, low]

    def test_deterministic_for_equal_ranks(self):
        a = cluster_opportunities([_opp("a", "Role One", rank_score=0.5, job_id=1)])[0]
        b = cluster_opportunities([_opp("b", "Role Two", rank_score=0.5, job_id=2)])[0]
        assert rank_clusters([a, b]) == rank_clusters([a, b])

    def test_no_provider_bonus_influences_order(self):
        # Same rank_score/pursue tier, different providers via job.source
        # — order must be stable/input-preserving, never provider-biased.
        a = cluster_opportunities([_opp("openai", "Role One", rank_score=0.5, job_id=1)])[0]
        a.representative.job.source = "ashby"
        b = cluster_opportunities([_opp("palantir", "Role Two", rank_score=0.5, job_id=2)])[0]
        b.representative.job.source = "lever"
        assert rank_clusters([a, b]) == [a, b]
        assert rank_clusters([b, a]) == [b, a]
