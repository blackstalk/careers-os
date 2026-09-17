"""Work-style fit / role-shape classification (Phase 4.2).

Answers "what does a normal day in this role consist of?" from the job's
own description — hands-on building vs. customer/meeting time vs.
coordination/communication — so a technically relevant role whose real
work product is meetings, account management, or liaison work can be
told apart from one where customer contact serves hands-on building.

Deterministic and explainable, same discipline as career/bridge_role.py:
counts *distinct* activity phrases (never raw frequency), reports every
phrase it matched, and never looks at the title or company. See
docs/pursue-recommendation.md#work-style-fit.

Phrases describe what the *person* does. Bare "customer-facing" is
deliberately absent: postings routinely use it for the *product* being
built ("ship customer-facing AI products"), which says nothing about the
engineer's calendar.
"""

import re

from careers_os.domain.opportunity_decision import WorkStyle, WorkStyleResult

BUILD_PHRASES = (
    "write code", "writing code", "production code", "write production", "code review",
    "you'll build", "you will build", "build and ship", "build and deploy",
    "design and ship", "design and build", "design, build", "ship production",
    "from idea to production", "prototype", "build internal tools", "build tools",
    "build integrations", "build services", "build agents", "build pipelines",
    "build data pipelines", "own services", "owning services", "end-to-end ownership",
    "own the implementation", "own implementation", "implement solutions",
    "implement integrations", "hands-on", "deploy", "debug", "troubleshoot",
    "observab", "on-call", "production systems", "backend services", "api development",
    "automations",
)

CUSTOMER_PHRASES = (
    "on the frontlines", "liaison", "work with customers",
    "working with customers", "work directly with customers", "meet with customers",
    "customer meetings", "client meetings", "discovery calls", "technical discovery",
    "customer success", "account management", "account executive", "account expansion",
    "expansion opportunities", "relationship", "partnerships with",
    "technical advisor to customers", "trusted advisor", "externally",
    "educate customers", "educate on", "demos", "demonstrations", "pre-sales",
    "presales", "post-sales", "sales cycle", "executive stakeholders",
    "executive relationships", "workshops", "guide customers", "customer requirements",
    "success criteria", "customer engagements", "embed with", "partner with customer",
    "prospects", "technical sales", "sales process", "sales team", "go-to-market",
    "executive briefings", "exec sponsors", "executive sponsors", "trade shows",
    "conferences", "consult with", "voice of the customer", "drive adoption",
    "travel between", "travel up to",
)

COORDINATION_PHRASES = (
    "coordinate", "facilitate", "stakeholder management", "stakeholder alignment",
    "align stakeholders", "cross-functional", "project management", "program management",
    "manage timelines", "presentations", "present to", "roadmap", "influence",
    "advocate for", "status updates", "follow up with", "follow-up communication",
    "support technical workstreams", "channeling", "technical project management",
    "track progress", "manage dependencies", "change management", "delivery plan",
    "milestones", "report to", "tracking and driving", "provide training",
    "training sessions", "enablement",
)

# Explicit statements that the role does not involve writing code. These
# override build signals: a role can mention integrations and
# troubleshooting and still say outright that the work is advisory.
# Kept narrow on purpose: "... without writing code" alone often describes
# what a *product* lets its users do, not the job.
NO_CODE_PHRASES = (
    "without writing code directly", "not a coding role", "non-coding role",
    "no coding required", "not a hands-on coding",
)

# Collaboration phrases that appear in plenty of hands-on engineering
# postings ("work cross-functionally", "influence the roadmap"). They are
# reported as evidence but count half toward customer/coordination weight,
# so on their own they can't make a build role look meeting-heavy.
WEAK_PEOPLE_PHRASES = frozenset({
    "cross-functional", "roadmap", "influence", "advocate for", "relationship",
    "go-to-market", "drive adoption", "conferences", "consult with", "milestones",
    "report to",
})

# The classifier reads the role section only: company "About us"
# marketing ("everyone is a builder") and benefits/EEO boilerplate
# otherwise leak build or customer signals that say nothing about the job.
_ROLE_START_MARKERS = (
    "about the role", "about this role", "the role", "what you'll do", "what you will do",
    "what you’ll do", "responsibilities", "in this role", "your role", "the opportunity",
)
_ROLE_END_MARKERS = (
    "benefits", "perks", "equal opportunity", "we are an equal",
)

_MIN_SIGNALS = 3
_MIN_BUILD = 3
_MIN_STRONG_PEOPLE = 2


def role_section(description: str) -> str:
    text = description.lower()
    starts = [i for m in _ROLE_START_MARKERS if (i := text.find(m)) != -1]
    start = min(starts) if starts else 0
    ends = [i for m in _ROLE_END_MARKERS if (i := text.find(m, start + 1)) != -1]
    end = min(ends) if ends else len(text)
    return text[start:end]


def _matches(text: str, phrases: tuple[str, ...]) -> list[str]:
    return [p for p in phrases if p in text]


def classify_work_style(description: str | None) -> WorkStyleResult:
    if not description or len(description.strip()) < 200:
        return WorkStyleResult(
            style=WorkStyle.UNKNOWN,
            reason="Not enough description text to judge the day-to-day shape of the role.",
        )

    text = re.sub(r"\s+", " ", role_section(description))
    no_code = _matches(text, NO_CODE_PHRASES)
    # "without writing code" must not also count as a "writing code" build signal.
    build_text = text
    for phrase in no_code:
        build_text = build_text.replace(phrase, " ")
    build = _matches(build_text, BUILD_PHRASES)
    customer = _matches(text, CUSTOMER_PHRASES)
    coordination = _matches(text, COORDINATION_PHRASES)

    b, c, k = len(build), len(customer), len(coordination)
    weak = sum(1 for p in customer + coordination if p in WEAK_PEOPLE_PHRASES)
    strong = c + k - weak
    people = strong + weak / 2
    result = dict(build_signals=build, customer_signals=customer, coordination_signals=coordination, no_code_signals=no_code)
    counts = f"{b} build, {c} customer, {k} coordination signal(s)"

    if b + c + k < _MIN_SIGNALS:
        return WorkStyleResult(
            style=WorkStyle.UNKNOWN,
            reason=f"Too few role-activity signals to judge work style ({counts}).",
            **result,
        )

    if not no_code and b >= _MIN_BUILD and b >= people:
        style, reason = WorkStyle.BUILD_HEAVY, f"Hands-on building dominates the described work ({counts})."
    elif not no_code and b >= _MIN_BUILD and b * 2 >= people:
        style, reason = (
            WorkStyle.BALANCED,
            f"Substantial hands-on building alongside real customer/coordination work ({counts}).",
        )
    elif no_code or (strong >= _MIN_STRONG_PEOPLE and people > b):
        strong_c = sum(1 for p in customer if p not in WEAK_PEOPLE_PHRASES)
        strong_k = strong - strong_c
        style = WorkStyle.CUSTOMER_HEAVY if strong_c >= strong_k else WorkStyle.COORDINATION_HEAVY
        label = "customer/meeting" if style == WorkStyle.CUSTOMER_HEAVY else "coordination/communication"
        reason = f"Described work is mostly {label} rather than hands-on building ({counts})."
        if no_code:
            reason += f" The posting explicitly limits coding: {', '.join(repr(p) for p in no_code)}."
    else:
        style, reason = (
            WorkStyle.UNKNOWN,
            f"Neither hands-on building nor customer/coordination work clearly dominates ({counts}).",
        )

    return WorkStyleResult(style=style, reason=reason, **result)
