"""
Report Validation Helpers

Post-generation checks for language consistency, image references, and
section completeness.  All validators are non-blocking — they return warning
lists rather than raising.
"""

from __future__ import annotations

import os
import re
import logging
from typing import List

logger = logging.getLogger(__name__)


def validate_report_language(content: str, expected_lang: str) -> List[str]:
    """Check for unintended language mixing.

    Args:
        content: The full report Markdown text.
        expected_lang: ``'en'`` or ``'zh'``.

    Returns:
        List of warning strings (empty if OK).
    """
    warnings: List[str] = []
    if not content:
        return warnings

    if expected_lang == 'en':
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', content))
        total_chars = len(content)
        if total_chars > 0 and chinese_chars / total_chars > 0.05:
            warnings.append(
                f"Report language is English but {chinese_chars} Chinese characters "
                f"found ({chinese_chars / total_chars:.1%})"
            )
    elif expected_lang == 'zh':
        ascii_words = len(re.findall(r'[a-zA-Z]{4,}', content))
        if ascii_words > 500:
            warnings.append(
                f"Report language is Chinese but found {ascii_words} English words "
                "— may contain untranslated sections"
            )
    return warnings


def validate_image_references(content: str, working_dir: str) -> List[str]:
    """Check that all Markdown image references point to existing files.

    Args:
        content: The full report Markdown text.
        working_dir: Base directory for resolving relative paths.

    Returns:
        List of warning strings for missing images.
    """
    warnings: List[str] = []
    img_refs = re.findall(r'!\[.*?\]\((.*?)\)', content)
    for img_path in img_refs:
        resolved = img_path
        if not os.path.isabs(img_path):
            resolved = os.path.join(working_dir, img_path)
        if not os.path.exists(resolved):
            warnings.append(f"Referenced image not found: {img_path}")
    return warnings


def validate_section_completeness(content: str) -> List[str]:
    """Flag suspiciously short sections in the report.

    Args:
        content: The full report Markdown text.

    Returns:
        List of warning strings for near-empty sections.
    """
    warnings: List[str] = []
    sections = re.split(r'^#{1,3}\s', content, flags=re.MULTILINE)
    for i, section in enumerate(sections[1:], 1):
        if len(section.strip()) < 20:
            warnings.append(f"Section {i} appears empty or too short")
    return warnings


def validate_report(content: str, expected_lang: str, working_dir: str) -> List[str]:
    """Run all validators.

    Args:
        content: The full report Markdown text.
        expected_lang: ``'en'`` or ``'zh'``.
        working_dir: Base directory for resolving image paths.

    Returns:
        Combined list of warning strings.
    """
    warnings: List[str] = []
    warnings.extend(validate_report_language(content, expected_lang))
    warnings.extend(validate_image_references(content, working_dir))
    warnings.extend(validate_section_completeness(content))
    return warnings
