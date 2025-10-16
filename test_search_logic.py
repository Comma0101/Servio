import pytest
import asyncio
from unittest.mock import patch, AsyncMock

# Make sure the function we want to test is importable
# This might require adjusting the python path depending on the project structure,
# but for now, we assume it can be imported directly.
from app.utils.thirty_nine_miles import find_dish_by_english_name

# --- Mock Data ---
# A sample menu that simulates the data structure returned by get_extracted_dishes
# This includes the tricky cases we want to test.
MOCK_MENU_DATA = {
    "success": True,
    "data": [
        {
            "dish_id": "101",
            "dish_name_en": "Fried Oyster",
            "dish_name_zh": "炸蚝",
            "dish_details": {"name": {"en": "Fried Oyster"}}
        },
        {
            "dish_id": "102",
            "dish_name_en": "Steamed Oyster",
            "dish_name_zh": "蒸蚝",
            "dish_details": {"name": {"en": "Steamed Oyster"}}
        },
        {
            "dish_id": "103",
            "dish_name_en": "Fried Calamari",
            "dish_name_zh": "炸鱿鱼",
            "dish_details": {"name": {"en": "Fried Calamari"}}
        },
        {
            "dish_id": "104",
            "dish_name_en": "Garlic Shrimp",
            "dish_name_zh": "蒜蓉虾",
            "dish_details": {"name": {"en": "Garlic Shrimp"}}
        },
        {
            "dish_id": "105",
            "dish_name_en": "Combo #1",
            "dish_name_zh": "一号套餐",
            "dish_details": {"name": {"en": "Combo #1"}}
        },
        {
            "dish_id": "106",
            "dish_name_en": "Raw Oyster (6 PCs)",
            "dish_name_zh": "生蚝（6个）",
            "dish_details": {"name": {"en": "Raw Oyster (6 PCs)"}}
        }
    ]
}

# --- Pytest Fixture and Test Cases ---

@pytest.mark.asyncio
@patch('app.utils.thirty_nine_miles.get_extracted_dishes', new_callable=AsyncMock)
async def test_find_dish_logic(mock_get_dishes):
    """
    A comprehensive test suite for the improved find_dish_by_english_name function.
    """
    # Configure the mock to return our fake menu data whenever it's called
    mock_get_dishes.return_value = MOCK_MENU_DATA

    # --- Test Case 1: Exact Match ---
    # Should return only the exact match.
    results = await find_dish_by_english_name("test_portal", "Fried Oyster")
    assert len(results) == 1
    assert results[0]['dish_name_en'] == "Fried Oyster"
    print("Test Case 1 (Exact Match) PASSED")

    # --- Test Case 2: "All Words" Match ---
    # A multi-word query that isn't an exact match but where all words are present.
    # This is the core fix for the "fried oyster" vs "fried calamari" problem.
    results = await find_dish_by_english_name("test_portal", "oyster fried")
    assert len(results) == 1
    assert results[0]['dish_name_en'] == "Fried Oyster"
    print("Test Case 2 ('All Words' Match) PASSED")

    # --- Test Case 3: Phrase "Contains" Match ---
    # A single-word query that is part of multiple dish names.
    # Should return all relevant dishes for ambiguity resolution.
    results = await find_dish_by_english_name("test_portal", "oyster")
    assert len(results) == 3
    dish_names = {dish['dish_name_en'] for dish in results}
    assert "Fried Oyster" in dish_names
    assert "Steamed Oyster" in dish_names
    assert "Raw Oyster (6 PCs)" in dish_names
    print("Test Case 3 (Phrase 'Contains' Match) PASSED")

    # --- Test Case 4: Fallback Fuzzy Match ---
    # A misspelled query that should still be caught by the fuzzy search.
    results = await find_dish_by_english_name("test_portal", "fried oister")
    assert len(results) >= 1
    assert results[0]['dish_name_en'] == "Fried Oyster"
    print("Test Case 4 (Fallback Fuzzy Match) PASSED")

    # --- Test Case 5: Irrelevant Match Prevention ---
    # A query that previously caused issues. Should NOT return "Fried Calamari".
    results = await find_dish_by_english_name("test_portal", "fried oyster")
    assert len(results) == 1 # Should not find Fried Calamari
    assert results[0]['dish_name_en'] == "Fried Oyster"
    print("Test Case 5 (Irrelevant Match Prevention) PASSED")

    # --- Test Case 6: No Match ---
    # A query for a dish that doesn't exist.
    results = await find_dish_by_english_name("test_portal", "lobster bisque")
    assert len(results) == 0
    print("Test Case 6 (No Match) PASSED")

    # --- Test Case 7: Combo Normalization ---
    # Test if "combo number one" is correctly normalized and found.
    results = await find_dish_by_english_name("test_portal", "combo number one")
    assert len(results) == 1
    assert results[0]['dish_name_en'] == "Combo #1"
    print("Test Case 7 (Combo Normalization) PASSED")

    # --- Test Case 8: Advanced Normalization (Number word and unit) ---
    # This is the key test for the "six piece raw oyster" issue.
    results = await find_dish_by_english_name("test_portal", "six piece raw oyster")
    assert len(results) == 1
    assert results[0]['dish_name_en'] == "Raw Oyster (6 PCs)"
    print("Test Case 8 (Advanced Normalization) PASSED")

# To run this test:
# 1. Make sure you have pytest and pytest-asyncio installed:
#    pip install pytest pytest-asyncio
# 2. Run pytest from your terminal in the project root directory:
#    pytest test_search_logic.py
