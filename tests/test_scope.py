from careers_os.career.scope import classify_scope
from careers_os.domain.opportunity_decision import ScopeDimension


class TestScopeClassification:
    def test_execution_and_maintenance_role_detected(self):
        result = classify_scope(
            "Senior Web Developer",
            "Execute front-end and back-end website updates based on approved wireframes. "
            "Support best practices for maintainability and ongoing support.",
        )
        assert ScopeDimension.EXECUTION in result.dimensions_present
        assert ScopeDimension.ARCHITECTURE not in result.dimensions_present

    def test_architecture_and_ownership_role_detected(self):
        result = classify_scope(
            "Platform Architect",
            "Own the platform architecture and API design, working with AWS infrastructure "
            "and distributed systems.",
        )
        assert ScopeDimension.ARCHITECTURE in result.dimensions_present
        assert ScopeDimension.OWNERSHIP in result.dimensions_present
        assert result.primary_dimension == ScopeDimension.ARCHITECTURE

    def test_no_signal_yields_empty_result(self):
        result = classify_scope("Job", "A short generic description.")
        assert result.dimensions_present == []
        assert result.primary_dimension is None

    def test_customer_facing_and_leadership_detected(self):
        result = classify_scope(
            "Technical Solutions Engineer",
            "Communicate with external developers in a customer-facing role, providing "
            "technical leadership and technical consulting to the team.",
        )
        assert ScopeDimension.CUSTOMER_FACING in result.dimensions_present
        assert ScopeDimension.TECHNICAL_LEADERSHIP in result.dimensions_present
