"""Bundled food nutritional database.

A simple in-memory lookup of common food items.  Values are per 100 g.
When an item is not found, returns None so the caller can prompt for custom
values.
"""

from __future__ import annotations

from typing import Any

# Nutritional data per 100 g: {calories, protein_g, carbs_g, fat_g}
_FOOD_DB: dict[str, dict[str, float]] = {
    "chicken breast": {"calories": 165, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
    "rice": {"calories": 130, "protein_g": 2.7, "carbs_g": 28.0, "fat_g": 0.3},
    "brown rice": {"calories": 123, "protein_g": 2.7, "carbs_g": 26.0, "fat_g": 1.0},
    "broccoli": {"calories": 34, "protein_g": 2.8, "carbs_g": 7.0, "fat_g": 0.4},
    "egg": {"calories": 155, "protein_g": 13.0, "carbs_g": 1.1, "fat_g": 11.0},
    "banana": {"calories": 89, "protein_g": 1.1, "carbs_g": 23.0, "fat_g": 0.3},
    "apple": {"calories": 52, "protein_g": 0.3, "carbs_g": 14.0, "fat_g": 0.2},
    "salmon": {"calories": 208, "protein_g": 20.0, "carbs_g": 0.0, "fat_g": 13.0},
    "oatmeal": {"calories": 68, "protein_g": 2.4, "carbs_g": 12.0, "fat_g": 1.4},
    "pasta": {"calories": 131, "protein_g": 5.0, "carbs_g": 25.0, "fat_g": 1.1},
    "bread": {"calories": 265, "protein_g": 9.0, "carbs_g": 49.0, "fat_g": 3.2},
    "milk": {"calories": 42, "protein_g": 3.4, "carbs_g": 5.0, "fat_g": 1.0},
    "yogurt": {"calories": 59, "protein_g": 10.0, "carbs_g": 3.6, "fat_g": 0.4},
    "cheese": {"calories": 402, "protein_g": 25.0, "carbs_g": 1.3, "fat_g": 33.0},
    "beef": {"calories": 250, "protein_g": 26.0, "carbs_g": 0.0, "fat_g": 15.0},
    "pork": {"calories": 242, "protein_g": 27.0, "carbs_g": 0.0, "fat_g": 14.0},
    "tofu": {"calories": 76, "protein_g": 8.0, "carbs_g": 1.9, "fat_g": 4.8},
    "sweet potato": {"calories": 86, "protein_g": 1.6, "carbs_g": 20.0, "fat_g": 0.1},
    "potato": {"calories": 77, "protein_g": 2.0, "carbs_g": 17.0, "fat_g": 0.1},
    "spinach": {"calories": 23, "protein_g": 2.9, "carbs_g": 3.6, "fat_g": 0.4},
    "avocado": {"calories": 160, "protein_g": 2.0, "carbs_g": 9.0, "fat_g": 15.0},
    "almonds": {"calories": 579, "protein_g": 21.0, "carbs_g": 22.0, "fat_g": 49.0},
    "peanut butter": {"calories": 588, "protein_g": 25.0, "carbs_g": 20.0, "fat_g": 50.0},
    "tuna": {"calories": 132, "protein_g": 28.0, "carbs_g": 0.0, "fat_g": 1.0},
    "shrimp": {"calories": 99, "protein_g": 24.0, "carbs_g": 0.2, "fat_g": 0.3},
    "lentils": {"calories": 116, "protein_g": 9.0, "carbs_g": 20.0, "fat_g": 0.4},
    "chickpeas": {"calories": 164, "protein_g": 8.9, "carbs_g": 27.0, "fat_g": 2.6},
    "olive oil": {"calories": 884, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 100.0},
    "honey": {"calories": 304, "protein_g": 0.3, "carbs_g": 82.0, "fat_g": 0.0},
    "whey protein": {"calories": 400, "protein_g": 80.0, "carbs_g": 10.0, "fat_g": 5.0},
}


import json
import os
import urllib.request
import urllib.error

def fetch_nutrition_from_web(name: str) -> dict[str, float] | None:
    """Fetch nutritional data for a generic food item using OpenRouter API via HTTP.
    Returns per 100g data: {calories, protein_g, carbs_g, fat_g}.
    """
    # The API key in .env for OpenAI is actually an openrouter key format (sk-or-v1-...)
    # Or just use GROQ/OPENROUTER_API_KEY directly.
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    prompt = (
        f"Provide the nutritional values (calories, protein_g, carbs_g, fat_g) per 100g of raw/standard '{name}'. "
        "Return ONLY a valid JSON object matching this schema: "
        '{"calories": <float>, "protein_g": <float>, "carbs_g": <float>, "fat_g": <float>}. '
        "Do not include any other text."
    )

    data = json.dumps({
        "model": os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=10.0) as response:
            result = json.loads(response.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"].strip()
            # In case the LLM wraps it in markdown code blocks
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            parsed = json.loads(content.strip())
            
            # Validate output types
            return {
                "calories": float(parsed.get("calories", 0)),
                "protein_g": float(parsed.get("protein_g", 0)),
                "carbs_g": float(parsed.get("carbs_g", 0)),
                "fat_g": float(parsed.get("fat_g", 0)),
            }
    except Exception as e:
        print("API Error:", e)
        # Even print the traceback if it's not a generic urllib error
        import traceback
        traceback.print_exc()
        return None


def lookup(name: str) -> dict[str, float] | None:
    """Return nutritional data per 100 g for *name*, or None if not found.

    Lookup is case-insensitive. If not in local DB, queries web/LLM, caches, and returns.
    """
    key = name.lower().strip()
    if key in _FOOD_DB:
        return _FOOD_DB[key]
    
    # Not found! Go to the web/LLM and fetch it
    fetched = fetch_nutrition_from_web(key)
    if fetched is not None:
        _FOOD_DB[key] = fetched  # cache it locally so we don't look it up again
        return fetched

    return None


def list_all() -> list[str]:
    """Return all known food item names."""
    return list(_FOOD_DB.keys())
