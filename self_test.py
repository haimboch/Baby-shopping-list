from tests.test_core import (
    test_classifier, test_dimensions, test_quantities, test_xml_price_and_store,
    test_multi_buy_promotion_terms, test_ksp_official_product_parser,
    test_ksp_print_page_fallback_parser,
    test_ksp_json_api_barcode_search_parser,
    test_ksp_json_api_detail_finds_barcode_in_specification,
    test_superpharm_promo_filename_and_merge,
    test_superpharm_online_product_and_promo_parser,
    test_superpharm_online_multi_buy_parser,
    test_superpharm_category_product_url_extraction,
    test_strict_source_outcome_is_not_false_success,
    test_cheapersal_price_fallback_normalizes_matching_chain,
)

for fn in (
    test_classifier, test_dimensions, test_quantities, test_xml_price_and_store,
    test_multi_buy_promotion_terms, test_ksp_official_product_parser,
    test_ksp_print_page_fallback_parser,
    test_ksp_json_api_barcode_search_parser,
    test_ksp_json_api_detail_finds_barcode_in_specification,
    test_superpharm_promo_filename_and_merge,
    test_superpharm_online_product_and_promo_parser,
    test_superpharm_online_multi_buy_parser,
    test_superpharm_category_product_url_extraction,
    test_strict_source_outcome_is_not_false_success,
    test_cheapersal_price_fallback_normalizes_matching_chain,
):
    fn()
    print(f"✅ {fn.__name__}")
print("✅ All local parser/classifier self-tests passed.")
