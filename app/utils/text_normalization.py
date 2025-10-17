"""
Text normalization utilities for robust fuzzy matching in voice ordering systems.

This module provides industry-standard text preprocessing to handle:
- Spelling variations and typos in menu data
- Case sensitivity issues
- Punctuation and whitespace inconsistencies
- Voice transcription errors
"""

import re
from typing import List, Tuple


def normalize_for_matching(text: str) -> str:
    """
    Normalizes text for more robust fuzzy matching.
    
    This is a general-purpose normalization function that makes fuzzy matching
    more tolerant of common issues without hardcoding specific fixes.
    
    Args:
        text: The text to normalize
        
    Returns:
        Normalized text suitable for fuzzy matching
        
    Examples:
        >>> normalize_for_matching("Sriracha!")
        'sriracha'
        >>> normalize_for_matching("  BBQ  Sauce  ")
        'bbq sauce'
    """
    if not text:
        return ""
    
    # Convert to lowercase for case-insensitive matching
    text = text.lower()
    
    # Remove punctuation (keeps letters, numbers, and spaces)
    text = re.sub(r'[^\w\s]', '', text)
    
    # Normalize whitespace (multiple spaces → single space)
    text = ' '.join(text.split())
    
    return text.strip()


def normalize_options_for_matching(options: List[str]) -> Tuple[List[str], dict]:
    """
    Normalizes a list of options and creates a mapping back to originals.
    
    Args:
        options: List of original option names
        
    Returns:
        Tuple of (normalized_options, mapping_dict)
        where mapping_dict maps normalized → original
        
    Example:
        >>> options = ["BBQ Sauce", "Ranch!"]
        >>> normalized, mapping = normalize_options_for_matching(options)
        >>> normalized
        ['bbq sauce', 'ranch']
        >>> mapping['bbq sauce']
        'BBQ Sauce'
    """
    normalized = [normalize_for_matching(opt) for opt in options]
    mapping = {norm: orig for norm, orig in zip(normalized, options) if norm}
    return normalized, mapping
