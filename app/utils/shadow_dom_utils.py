"""
Shadow DOM-aware XPath extraction utilities for Playwright.

This module provides utilities to extract data from web components that use Shadow DOM,
which are not accessible via standard XPath selectors. It handles custom elements
(like pt-main-specs, pt-text, etc.) by detecting shadow boundaries and traversing
into shadow roots to locate and extract data.

Author: Auto-generated from working test implementation
"""

import asyncio
import re
from typing import Optional
from playwright.async_api import Page, ElementHandle


# ============================================================
# Shadow-aware XPath extraction utilities
# ============================================================

_TAG_STEP_RE = re.compile(r"""
    ^\s*
    (?P<tag>[\w-]+)                           # tag name (allows custom elements: has '-')
    (?:\[(?P<predicate>.+?)\])?               # optional [predicate] ... kept simple
    \s*$
""", re.VERBOSE)


def _is_custom_tag(tag: str) -> bool:
    """
    Check if a tag name is a custom element (heuristic: contains a dash).
    
    :param tag: Tag name to check
    :return: True if tag appears to be a custom element
    """
    return "-" in tag


def _split_xpath_steps(xpath: str) -> list[tuple[str, bool]]:
    """
    Split an XPath like //a[@x="y"]/div/b[1] into steps.
    Returns list of (step, is_descendant) where is_descendant=True if it came from '//' (vs '/').
    
    :param xpath: XPath expression to split
    :return: List of (step, is_descendant) tuples
    """
    xp = xpath.strip()
    parts: list[tuple[str, bool]] = []
    i = 0
    while i < len(xp):
        if xp[i:i+2] == "//":
            i += 2
            j = i
            buf = []
            while j < len(xp) and xp[j] != '/':
                buf.append(xp[j])
                j += 1
            step = ''.join(buf).strip()
            if step:
                parts.append((step, True))
            i = j
        elif xp[i] == "/":
            i += 1
            j = i
            buf = []
            while j < len(xp) and xp[j] != '/':
                buf.append(xp[j])
                j += 1
            step = ''.join(buf).strip()
            if step:
                parts.append((step, False))
            i = j
        else:
            j = i
            buf = []
            while j < len(xp) and xp[j] != '/':
                buf.append(xp[j])
                j += 1
            step = ''.join(buf).strip()
            if step:
                parts.append((step, False))
            i = j
    return parts


def _parse_step(step: str) -> tuple[str, dict[str, str], Optional[int]]:
    """
    Parse a step like:
      'pt-main-specs[@element-id="mainSpecs"]'
      'div'
      'pt-text[1]'
    Returns (tag, attrs, nth) where nth is 1-based index for :nth-of-type
    
    :param step: XPath step to parse
    :return: Tuple of (tag, attributes dict, nth index or None)
    """
    m = _TAG_STEP_RE.match(step)
    if not m:
        return step, {}, None
    tag = m.group("tag")
    pred = m.group("predicate")
    attrs: dict[str, str] = {}
    nth: Optional[int] = None
    if pred:
        # handle only simple forms: @attr="val" and numeric index [N]
        for piece in re.split(r'\s+and\s+', pred):
            piece = piece.strip()
            if re.fullmatch(r"\d+", piece):
                nth = int(piece)
                continue
            m2 = re.match(r'@([\w:-]+)\s*=\s*"(.*?)"', piece)
            if m2:
                attrs[m2.group(1)] = m2.group(2)
    return tag, attrs, nth


def _to_css_from_step(tag: str, attrs: dict[str, str], nth: Optional[int]) -> str:
    """
    Convert parsed step components to CSS selector.
    
    :param tag: Tag name
    :param attrs: Attributes dictionary
    :param nth: Nth-of-type index (1-based) or None
    :return: CSS selector string
    """
    sel = [tag]
    if attrs:
        for k, v in attrs.items():
            sel.append(f'[{k}="{v}"]')
    if nth is not None:
        sel.append(f":nth-of-type({nth})")
    return "".join(sel)


def _clean_xpath_and_mode(xpath: str) -> tuple[str, Optional[str], bool]:
    """
    Return (clean_xpath, attr_name, text_mode).
    If attr_name is not None, extract that attribute; else if text_mode is True, extract text.
    Supports '/text()' and '/@attr'.
    
    :param xpath: The XPath expression to clean
    :return: Tuple of (cleaned XPath, attribute name or None, text_mode flag)
    """
    xp = xpath.strip()
    if "/@" in xp:
        base, attr = xp.rsplit("/@", 1)
        attr_name = attr.split("/")[0]
        return base, attr_name, False
    if xp.endswith("/text()"):
        return xp[:-7], None, True
    return xp, None, True


async def _query_all_shadow_chain_handles(page: Page, xpath_clean: str) -> list[ElementHandle]:
    """
    Shadow-aware resolver returning ElementHandle list:
      1) Try normal XPath (Playwright).
      2) If nothing AND a custom element exists in the path, traverse via the first shadow host.
    
    :param page: Playwright page object
    :param xpath_clean: Cleaned XPath expression
    :return: List of ElementHandle objects
    """
    # 1) Try plain XPath first
    elements = await page.query_selector_all(f"xpath={xpath_clean}")
    if elements:
        return elements

    # 2) Shadow-aware attempt (one shadow boundary)
    steps = _split_xpath_steps(xpath_clean)
    if not steps:
        return []

    parsed: list[tuple[str, dict[str, str], Optional[int], bool]] = []
    first_custom_idx: Optional[int] = None
    for idx, (step, is_desc) in enumerate(steps):
        tag, attrs, nth = _parse_step(step)
        parsed.append((tag, attrs, nth, is_desc))
        if first_custom_idx is None and _is_custom_tag(tag):
            first_custom_idx = idx

    if first_custom_idx is None:
        return []

    # Build CSS to the first custom host in the document
    css = []
    for i, (tag, attrs, nth, is_desc) in enumerate(parsed[:first_custom_idx+1]):
        frag = _to_css_from_step(tag, attrs, nth)
        if i == 0:
            css.append(frag)
        else:
            combinator = " " if is_desc else " > "
            css.append(combinator + frag)
    host_css = "".join(css)

    host_el = await page.query_selector(f"css={host_css}")
    if not host_el:
        return []

    # Enter shadow root
    shadow = await host_el.evaluate_handle("el => el.shadowRoot")
    remain = parsed[first_custom_idx+1:]
    if not remain:
        # host itself targeted
        return [host_el]

    # Build CSS for the remaining chain inside shadow root
    remain_css_parts: list[str] = []
    for i, (tag, attrs, nth, is_desc) in enumerate(remain):
        frag = _to_css_from_step(tag, attrs, nth)
        remain_css_parts.append(("" if i == 0 else (" " if is_desc else " > ")) + frag)
    remain_css = "".join(remain_css_parts)

    # Query inside the shadow root
    handles_js = await shadow.evaluate_handle(
        "(root, sel) => Array.from(root.querySelectorAll(sel))",
        remain_css
    )
    count = await handles_js.evaluate("arr => arr.length")
    out: list[ElementHandle] = []
    for i in range(count):
        el = await handles_js.evaluate_handle("(arr, i) => arr[i]", i)
        out.append(el)
    return out


async def _extract_from_handles(
    handles: list[ElementHandle], 
    attr_name: Optional[str], 
    text_mode: bool
) -> list[str]:
    """
    Extract values from a list of element handles.
    
    :param handles: List of ElementHandle objects
    :param attr_name: Attribute name to extract, or None
    :param text_mode: Whether to extract text content
    :return: List of extracted string values
    """
    out: list[str] = []
    for el in handles:
        if attr_name:
            val = await el.get_attribute(attr_name)
        elif text_mode:
            # inner_text() waits for layout; text_content() is faster but may include hidden text
            val = await el.inner_text()
        else:
            # default to text if neither explicitly set
            val = await el.inner_text()
        if val is not None:
            s = val.strip()
            if s:
                out.append(s)
    return out


async def wait_shadow_aware(page: Page, xpath: str, timeout_ms: int = 10000) -> None:
    """
    Waits until either the plain XPath resolves or the shadow-aware path yields an element.
    
    :param page: Playwright page object
    :param xpath: XPath expression to wait for
    :param timeout_ms: Timeout in milliseconds
    """
    clean_xpath, _, _ = _clean_xpath_and_mode(xpath)
    try:
        await page.wait_for_selector(f"xpath={clean_xpath}", timeout=timeout_ms, state="attached")
        return
    except Exception:
        pass

    # Fallback to shadow-aware polling
    import time
    end = time.time() + (timeout_ms / 1000.0)
    while time.time() < end:
        handles = await _query_all_shadow_chain_handles(page, clean_xpath)
        if handles:
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for selector (shadow-aware): {xpath}")


async def extract_shadow_aware(page: Page, xpath: str) -> list[str]:
    """
    Returns list of strings for the XPath, piercing one shadow boundary if needed.
    Respects '/text()' and '/@attr' suffixes.
    
    :param page: Playwright page object
    :param xpath: XPath expression to extract
    :return: List of extracted string values
    """
    clean_xpath, attr_name, text_mode = _clean_xpath_and_mode(xpath)
    handles = await _query_all_shadow_chain_handles(page, clean_xpath)
    return await _extract_from_handles(handles, attr_name, text_mode)


# Export internal functions for advanced use cases
__all__ = [
    'wait_shadow_aware',
    'extract_shadow_aware',
    '_query_all_shadow_chain_handles',
    '_clean_xpath_and_mode',
]

