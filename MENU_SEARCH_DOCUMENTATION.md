# Menu Search System Documentation

## Overview
This document provides a comprehensive analysis of the menu item search functionality in the Servio voice ordering system, specifically for the 39Miles POS integration.

---

## Search Flow Architecture

### Entry Point: `handle_check_menu_item_english`
**Location**: `app/handlers/english_tool_logic.py`

When a user requests a menu item, the flow is:
1. AI calls `check_menu_item_english` tool with `dish_name_en` parameter
2. Handler calls `find_dish_by_english_name(portal_id, dish_name_en)`
3. Results are filtered for valid English names
4. Item is routed to appropriate order manager based on type

---

## Core Search Logic: `_find_dish()`
**Location**: `app/utils/thirty_nine_miles.py`

### Search Algorithm (Multi-Phase Approach)

#### Phase 0: Query Normalization (English only)
**Function**: `_normalize_combo_query(query: str) -> str`

Normalizes spoken variations into standard format:
- Input: "combo number two", "lunch special one", "COMBO two"
- Output: "COMBO #2", "LUNCH SPECIAL #1"
- Handles both number words ("one", "two") and digits ("1", "2")

**Example Transformations**:
```python
"combo number two" → "COMBO #2"
"family combo"     → "FAMILY COMBO" (unchanged, no number)
"COMBO 1"          → "COMBO #1"
```

#### Phase 1: Exact Match (Case-Insensitive)
**Priority**: Highest
**Threshold**: 100% (exact string match)

- Normalizes whitespace (handles double spaces, trailing spaces)
- Case-insensitive for English (`dish_name.lower()`)
- Case-sensitive for Chinese (exact Unicode match)
- Returns immediately on first match

**Code**:
```python
normalized_dish_name = " ".join(dish_name.lower().split())
normalized_search_term = " ".join(search_term_lower.split())

if normalized_dish_name == normalized_search_term:
    return [dish]  # Single result
```

#### Phase 2: General Fuzzy Match (English only)
**Priority**: High
**Threshold**: 80 (configurable)
**Library**: `thefuzz.process.extractOne()`

- Uses original query (not normalized) for better matching
- Applies to all menu items
- Returns single best match if score > 80

**Code**:
```python
best_match_name, score = process.extractOne(original_query, all_dish_names)
if score > 80:
    return [best_match_dish]
```

**Example Matches**:
- Query: "famly combo" → Match: "FAMILY COMBO" (score: ~85)
- Query: "orange chiken" → Match: "Orange Chicken" (score: ~88)

#### Phase 3: Combo-Specific Fuzzy Match
**Priority**: Medium
**Threshold**: 80
**Scope**: Items with `is_combo` flag

- Only triggers if "combo" is in search term
- Searches within combo items only
- Provides more focused matching for combo menu

**Code**:
```python
if "combo" in search_term_lower:
    combo_dishes = [dish for dish in all_dishes if dish.get("is_combo")]
    best_match_name, score = process.extractOne(original_query, combo_names)
    if score > 80:
        return [best_match_dish]
```

#### Phase 4: Contains Match
**Priority**: Lowest
**Threshold**: Substring present

- Case-insensitive substring search
- Returns up to 4 matches (configurable via `[:4]`)
- Used as final fallback

**Code**:
```python
contains_matches = [
    dish for dish in all_dishes
    if search_term_lower in dish.get("dish_name_en", "").lower()
]
return contains_matches[:4]
```

---

## Data Preprocessing: `_preprocess_menu_data()`

### Purpose
Corrects known API data issues and adds helpful metadata before caching.

### Key Operations

#### 1. Missing English Name Correction
**Problem**: Some items have English text in the Chinese field
**Solution**: Automatically copies to English field if detected

```python
if not name_en and name_zh and _is_primarily_english(name_zh):
    logger.warning(f"Correcting missing English name for item '{item_id}'")
    name_obj["en"] = name_zh
```

**Detection Logic** (`_is_primarily_english()`):
- Regex: `^[a-zA-Z0-9\s!\"#$%&'()*+,-./:;<=>?@[\\\]^_`{|}~]*$`
- Returns `True` if text contains only Latin characters, digits, and common punctuation
- Rejects Chinese characters, Japanese, Korean, Arabic, etc.

#### 2. Combo Flagging
**Purpose**: Adds `is_combo` flag for routing decisions

```python
if "combo" in item_name_en_lower:
    item["is_combo"] = True
```

#### 3. Customized Combo Enforcement
**Purpose**: Fixes ordering and required flags for specific combo

- Filters out `None` entries from option groups
- Enforces specific order: "choose one" → "half a pound" → "pick your flavor" → "pick your spicy level" → "add on"
- Sets all groups to `isRequired = True`

---

## Post-Search Processing

### 1. English Name Validation
**Function**: `is_primarily_english(text: str) -> bool`
**Location**: `app/handlers/english_tool_logic.py`

Filters search results to ensure valid English content:
```python
valid_matches = []
for dish_data in found_dishes_from_pos:
    name_to_check = _get_english_name(dish_details.get("name"))
    if name_to_check and is_primarily_english(name_to_check):
        valid_matches.append(dish_data)
```

### 2. Ambiguity Handling
If multiple valid matches found:
```json
{
  "is_ambiguous": true,
  "ambiguous_matches_en": ["COMBO #1", "COMBO #2", "COMBO #3"],
  "message_for_agent": "Found multiple items... Please clarify."
}
```

### 3. Routing Decision
Based on item characteristics:

- **No required options** → Add directly to cart
- **Customized/Family/Numbered Combos** → Route to `combo_order_manager`
- **Standard items with options** → Route to `order_manager`

---

## Performance Considerations

### Caching Strategy
**File**: `app/utils/extracted_dishes_cache_v2_{portal_id}.json`
**Duration**: 24 hours (configurable)

Cache Structure:
```json
{
  "timestamp": "2025-01-12T18:30:00",
  "dishes": [
    {
      "dish_id": "...",
      "dish_name_en": "FAMILY COMBO",
      "dish_name_zh": "家庭套餐",
      "price": 99.99,
      "options": [...],
      "is_combo": true,
      "dish_details": {...}
    }
  ]
}
```

### Search Performance
- **Exact match**: O(n) - single pass through menu
- **Fuzzy match**: O(n×m) - compares query against all items
- **Contains match**: O(n) - single pass with substring check

**Optimization**: Fuzzy matching only occurs if exact match fails

---

## Known Issues & Edge Cases

### 1. **CRITICAL: Fuzzy Match Returns Only Single Best Match**
**Issue**: Phase 2 uses `process.extractOne()` which returns only the best match, not all matches above threshold
**Impact**: User says "oyster" → System auto-selects "Raw Oyster (score 90)" and ignores "Fried Oyster"

**Example from Real Logs**:
```
User: "Can I have oyster?"
Phase 1 (Exact): No match for "oyster"
Phase 2 (Fuzzy): Finds "Raw Oyster (6 PCs)" with score 90 → Returns ONLY this item
Phase 3 & 4: Never reached!
Result: System adds "Raw Oyster" without asking about "Fried Oyster"
```

**Current Code Problem**:
```python
# Phase 2 in _find_dish()
best_match_name, score = process.extractOne(original_query, all_dish_names)  # Only ONE result!
if score > 80:
    return [best_match_dish]  # Returns immediately, skips other phases
```

**Recommended Fix**:
```python
# Use extract() to get ALL matches above threshold
matches = process.extract(original_query, all_dish_names, limit=None)
good_matches = [(name, score) for name, score in matches if score > 80]

if good_matches:
    # Return ALL items that match well
    matched_dishes = [dish for dish in all_dishes 
                     if dish.get(name_key) in [m[0] for m in good_matches]]
    return matched_dishes  # Return all matches for ambiguity handling
```

This would allow the existing ambiguity handling in `english_tool_logic.py` to ask:
> "I found multiple items: raw oyster and fried oyster. Which one would you like?"

### 2. Fuzzy Match Sensitivity
**Issue**: Threshold of 80 may miss valid variations
**Example**: "shrimp basket" vs "SHRIMP head on" might match incorrectly

**Recommendation**: 
- Consider two-tier threshold (85 for auto-accept, 70-85 for confirmation)
- Add synonym mapping for common variations

### 2. Double Space Handling
**Current**: Whitespace normalization handles this
**Example**: "FAMILY  COMBO" (double space) → "FAMILY COMBO"

### 3. Missing Categories in Search
**Status**: Categories not currently used in matching
**Potential Enhancement**: Weight matches by category relevance

### 4. Special Characters
**Current**: Basic punctuation handling
**Gap**: May not handle emoji, special Unicode, or Chinese punctuation

---

## Recommendations for Next Agent

### High Priority

1. **Implement Match Confidence Logging**
   ```python
   logger.info(f"Match: '{query}' → '{result}' (score: {score}, method: {method})")
   ```
   This helps debug false positives/negatives.

2. **Add Synonym Mapping**
   ```python
   SYNONYM_MAP = {
       "chicken tenders": ["chicken strips", "chicken fingers", "tenders"],
       "wings": ["chicken wings", "hot wings", "buffalo wings"],
       "fries": ["french fries", "chips"]
   }
   ```

3. **Consider Category Context**
   If user previously browsed "Appetizers", weight appetizer matches higher.

### Medium Priority

4. **Implement Two-Tier Threshold**
   - 85+: Auto-accept
   - 70-84: Ask for confirmation
   - <70: Suggest alternatives

5. **Add Popularity Weighting**
   Track frequently ordered items and boost their match scores.

6. **Enhanced Normalization**
   - Handle plural/singular: "wing" vs "wings"
   - Handle articles: "the combo" vs "combo"

### Low Priority

7. **A/B Testing Framework**
   Test different threshold values with real call data.

8. **Search Analytics**
   Track which queries fail to match and manually add mappings.

---

## Testing Recommendations

### Test Cases to Add

```python
# Exact matches
assert search("FAMILY COMBO") == ["FAMILY COMBO"]
assert search("combo #1") == ["COMBO #1"]

# Fuzzy matches (typos)
assert search("famly combo") == ["FAMILY COMBO"]
assert search("comob 1") == ["COMBO #1"]

# Normalization
assert search("combo number two") == ["COMBO #2"]
assert search("COMBO  2") == ["COMBO #2"]  # Double space

# Ambiguity
results = search("combo")
assert len(results) > 1  # Should return multiple combos
assert all("combo" in r.lower() for r in results)

# Edge cases
assert search("") == []
assert search("xxxnonexistentxxx") == []
```

---

## Configuration Reference

### Tunable Parameters

| Parameter | Location | Current Value | Recommended Range |
|-----------|----------|---------------|-------------------|
| Fuzzy threshold (general) | `_find_dish()` | 80 | 75-90 |
| Fuzzy threshold (combo) | `_find_dish()` | 80 | 75-90 |
| Max contains results | `_find_dish()` | 4 | 3-10 |
| Cache duration | `CACHE_DURATION_HOURS` | 24 | 12-48 |

### English Detection Regex
```python
r"^[a-zA-Z0-9\s!\"#$%&'()*+,-./:;<=>?@[\\\]^_`{|}~]*$"
```

This allows:
- Latin alphabet (a-z, A-Z)
- Numbers (0-9)
- Spaces and common punctuation
- Excludes: Chinese, Japanese, Korean, Arabic, etc.

---

## Integration Points

### Upstream Callers
- `handle_check_menu_item_english()` - Main entry point
- Direct API calls (future: REST endpoint)

### Downstream Dependencies
- `combo_order_manager` - Handles combo configuration
- `order_manager` - Handles standard item configuration
- `get_extracted_dishes()` - Provides cached menu data

### External APIs
- **39Miles POS**: Source of truth for menu data
- **Deepgram**: Receives search results for AI processing

---

## Summary

The current menu search system uses a sophisticated multi-phase matching strategy:
1. **Exact match** for precision
2. **Fuzzy match** for typo tolerance
3. **Contains match** for partial queries

Key strengths:
- Robust normalization (whitespace, case, combo queries)
- Automatic data correction (missing English names)
- Efficient caching (24-hour TTL)
- Ambiguity handling

Areas for improvement:
- Add synonym mapping
- Implement confidence logging
- Consider category context
- Add popularity weighting

The system balances precision (exact matches) with flexibility (fuzzy matching) while maintaining good performance through caching.
