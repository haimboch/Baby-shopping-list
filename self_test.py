from tests.test_core import (
    test_classifier, test_dimensions, test_quantities, test_xml_price_and_store
)

for fn in (test_classifier, test_dimensions, test_quantities, test_xml_price_and_store):
    fn()
    print(f"✅ {fn.__name__}")
print("✅ All local parser/classifier self-tests passed.")
