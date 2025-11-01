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


def clean_option_group_name(group_name: str) -> str:
    """
    Cleans option group names for natural, conversational speech output.
    
    Removes common prefixes like "Pick your", "Choose your", etc. and maps
    generic names to their semantic meaning to create more natural-sounding
    phrases for voice agents.
    
    Args:
        group_name: The original option group name from menu data
        
    Returns:
        Cleaned name suitable for natural speech
        
    Examples:
        >>> clean_option_group_name("Pick your flavor")
        'flavor'
        >>> clean_option_group_name("Pick your spicy level")
        'spicy level'
        >>> clean_option_group_name("Choose one")
        'flavor'
        >>> clean_option_group_name("Pick one")
        'flavor'
        >>> clean_option_group_name("Sides")
        'Sides'
    """
    if not group_name:
        return group_name
    
    cleaned = group_name.strip()
    cleaned_lower = cleaned.lower()
    
    # Semantic mappings for generic option group names
    # These represent what the option actually means in context
    semantic_mappings = {
        "one": "flavor",
        "choose one": "flavor",
        "select one": "flavor",
        "pick one": "flavor"
    }
    
    # Check for semantic mappings first (before prefix removal)
    if cleaned_lower in semantic_mappings:
        return semantic_mappings[cleaned_lower]
    
    # Common prefixes to remove (case-insensitive)
    prefixes_to_remove = [
        "pick your ",
        "choose your ",
        "select your ",
        "select ",
        "choose ",
        "pick "
    ]
    
    # Check each prefix and remove if found
    for prefix in prefixes_to_remove:
        if cleaned_lower.startswith(prefix):
            # Remove prefix but preserve original casing of remaining text
            cleaned = cleaned[len(prefix):]
            # After removing prefix, check if result maps to something semantic
            remaining_lower = cleaned.lower().strip()
            if remaining_lower in semantic_mappings:
                return semantic_mappings[remaining_lower]
            break
    
    return cleaned.strip()
