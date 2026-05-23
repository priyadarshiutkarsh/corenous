"""Pre-compiled regex patterns for structured PII detection."""
import re

SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Luhn check not performed; any 13-16 digit cluster with optional separators
CREDIT_CARD_RE = re.compile(r"\b(?:\d[ \-]?){13,16}\d\b")

# US phone numbers (various formats)
PHONE_RE = re.compile(
    r"\b(?:\+1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b"
)

EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Common API key patterns
OPENAI_KEY_RE   = re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")
ANTHROPIC_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9\-]{32,}\b")
GITHUB_TOKEN_RE = re.compile(r"\bghp_[A-Za-z0-9]{36}\b")
AWS_KEY_RE      = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
GENERIC_KEY_RE  = re.compile(
    r"(?:api[_\-]?key|auth[_\-]?token|secret[_\-]?key|access[_\-]?token)"
    r"[\s\"':=]+[A-Za-z0-9\-_]{16,}",
    re.IGNORECASE,
)

# Passwords typed near common labels
PASSWORD_RE = re.compile(
    r"(?:password|passwd|pwd|passphrase|secret)\s*[:=]\s*\S+",
    re.IGNORECASE,
)

# Private keys (PEM blocks)
PEM_RE = re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----")

ALL_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("SSN",             SSN_RE),
    ("credit_card",     CREDIT_CARD_RE),
    ("phone",           PHONE_RE),
    ("email",           EMAIL_RE),
    ("openai_key",      OPENAI_KEY_RE),
    ("anthropic_key",   ANTHROPIC_KEY_RE),
    ("github_token",    GITHUB_TOKEN_RE),
    ("aws_key",         AWS_KEY_RE),
    ("generic_api_key", GENERIC_KEY_RE),
    ("password_field",  PASSWORD_RE),
    ("pem_private_key", PEM_RE),
]

# ── Health & medical keywords ─────────────────────────────────────────────────
HEALTH_KEYWORDS: frozenset[str] = frozenset([
    "diagnosis", "diagnoses", "prescription", "prescribed", "medication",
    "dosage", "hiv", "aids", "cancer", "diabetes", "insulin", "chemotherapy",
    "antidepressant", "antipsychotic", "psychotherapy", "psychiatrist",
    "medical record", "patient id", "icd-10", "icd-11", "ehr", "hipaa",
    "therapy session", "mental health", "substance abuse", "rehab",
    "blood pressure", "cholesterol", "genetic test",
])

# ── Financial keywords ────────────────────────────────────────────────────────
FINANCE_KEYWORDS: frozenset[str] = frozenset([
    "account number", "routing number", "swift code", "iban", "sort code",
    "annual salary", "annual income", "net worth", "tax return", "w-2",
    "1099", "social security number", "ein", "wire transfer", "bank statement",
    "credit score", "loan application", "mortgage", "ssn",
    "national insurance", "ni number", "hmrc", "dvla", "bank details",
    "account details", "online banking", "card number", "cvv", "pin number",
    "gdpr subject access", "uk passport", "passport number",
])

PRIVACY_COMPLIANCE_KEYWORDS: frozenset[str] = frozenset([
    "hipaa compliance", "protected health information", "protected health", "patient consent",
    "data protection act", "ico registration", "subject access request",
    "right to erasure", "right to be forgotten", "dsar",
])
