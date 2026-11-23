"""
Utility modules for web scraping operations.
"""

from .shadow_dom_utils import (
    extract_shadow_aware,
    wait_shadow_aware,
)

__all__ = [
    "wait_shadow_aware",
    "extract_shadow_aware",
]

