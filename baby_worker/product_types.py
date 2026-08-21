"""Shared baby-product taxonomy used by collection and catalog enrichment."""

PRODUCT_TYPES = {
    "diapers": {"category": "החתלה", "need_name": "טיטולים"},
    "wipes": {"category": "החתלה", "need_name": "מגבונים"},
    "diaper_cream": {"category": "החתלה", "need_name": "משחת החתלה"},
    "changing_pads": {"category": "החתלה", "need_name": "משטחי החתלה"},
    "diaper_bags": {"category": "החתלה", "need_name": "שקיות לחיתולים"},
    "formula": {"category": "האכלה", "need_name": "תמ״ל"},
    "baby_wash": {"category": "רחצה וטיפוח", "need_name": "סבון רחצה"},
    "bath_oil": {"category": "רחצה וטיפוח", "need_name": "שמן אמבטיה"},
    "body_cream": {"category": "רחצה וטיפוח", "need_name": "קרם גוף"},
    "baby_laundry": {"category": "כביסה", "need_name": "חומרי כביסה"},
}

SUPPORTED_PRODUCT_TYPES = frozenset(PRODUCT_TYPES)
LIQUID_PRODUCT_TYPES = frozenset({"baby_wash", "bath_oil", "baby_laundry"})
CREAM_PRODUCT_TYPES = frozenset({"diaper_cream", "body_cream"})
