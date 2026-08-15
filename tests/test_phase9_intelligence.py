from intelligence_service import rank_products


def test_rank_products_prefers_name_match_over_description():
    products = [
        {"id": 1, "name": "Alpha Notebook", "short_description": "", "category": "Tools", "views": 10, "quantity": 3},
        {"id": 2, "name": "Premium Kit", "short_description": "alpha notebook accessory", "category": "Tools", "views": 999, "quantity": 3},
    ]
    ranked = rank_products(products, "alpha notebook")
    assert ranked[0]["id"] == 1


def test_rank_products_handles_empty_query():
    products = [
        {"id": 1, "name": "Low", "short_description": "", "category": "", "views": 10, "quantity": 1},
        {"id": 2, "name": "High", "short_description": "", "category": "", "views": 100, "quantity": 1},
    ]
    assert [p["id"] for p in rank_products(products, "")] == [2, 1]
