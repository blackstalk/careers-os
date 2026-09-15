from enum import Enum


class SkillCategory(str, Enum):
    """Shared taxonomy used by both job-requirement extraction
    (domain/requirements.py) and career evidence (domain/evidence.py) — the
    same enum on both sides is what makes matching a direct comparison
    instead of a translation step. See career/data/skills_taxonomy.yaml.
    """

    PROGRAMMING_LANGUAGE = "programming_language"
    FRAMEWORK = "framework"
    CLOUD = "cloud"
    ARCHITECTURE = "architecture"
    DATA = "data"
    AI_ML = "ai_ml"
    CUSTOMER_FACING = "customer_facing"
    LEADERSHIP = "leadership"
    DOMAIN = "domain"
    EDUCATION = "education"
    RESPONSIBILITY = "responsibility"  # extracted phrase with no taxonomy hit
