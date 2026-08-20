from tests.test_core import (
    test_classifier, test_dimensions, test_quantities, test_xml_price_and_store,
    test_multi_buy_promotion_terms, test_ksp_official_product_parser,
    test_superpharm_promo_filename_and_merge,
)

for fn in (
    test_classifier, test_dimensions, test_quantities, test_xml_price_and_store,
    test_multi_buy_promotion_terms, test_ksp_official_product_parser,
    test_superpharm_promo_filename_and_merge,
):
    fn()
    print(f"✅ {fn.__name__}")
print("✅ All local parser/classifier self-tests passed.")
