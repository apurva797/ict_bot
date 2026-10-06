"""User-facing disclosures shared by every analytical module."""

EDUCATIONAL_DISCLAIMER = (
    "Educational Disclaimer: This tool provides educational and analytical "
    "information only. It is not investment advice, a recommendation, research "
    "report, or a solicitation to buy, sell, or hold any security. Historical "
    "performance does not guarantee future results. Please conduct your own "
    "research and consult a SEBI-registered investment adviser where appropriate."
)


def disclaimer_markdown() -> str:
    """Return a consistent, subtle Markdown disclosure."""
    return f"> {EDUCATIONAL_DISCLAIMER}"
