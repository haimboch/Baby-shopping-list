from baby_worker.classifier import classify_need, parse_dimension, parse_package_quantity
from baby_worker.xmlfeeds import parse_price_rows, parse_stores

def test_classifier():
    assert classify_need("האגיס אקסטרה קר חיתולים מידה 3 40 יחידות") == "diapers"
    assert classify_need("מגבונים לחים האגיס 4x56") == "wipes"
    assert classify_need("מטרנה חלבי שלב 2 700 גרם") == "formula"
    assert classify_need("שקיות לחיתולים מלוכלכים") is None

def test_dimensions():
    assert parse_dimension("פמפרס מידה 4+", "diapers") == ("size", "4+")
    assert parse_dimension("Huggies NB newborn", "diapers") == ("size", "NB")
    assert parse_dimension("סימילאק גולד שלב 1", "formula") == ("stage", "1")

def test_quantities():
    assert parse_package_quantity("מגבונים 4x56", "wipes")[0] == 224
    assert parse_package_quantity("תמ״ל 700 גרם", "formula")[0] == 700

def test_xml_price_and_store():
    price_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Root>
      <StoreId>123</StoreId><SubChainId>005</SubChainId>
      <Items>
        <Item>
          <ItemCode>7290000000001</ItemCode>
          <ItemName>האגיס חיתולים מידה 3 40 יחידות</ItemName>
          <ManufacturerName>Huggies</ManufacturerName>
          <QtyInPackage>40</QtyInPackage>
          <ItemPrice>39.90</ItemPrice>
        </Item>
        <Item>
          <ItemCode>7290000000002</ItemCode>
          <ItemName>קולה 1.5 ליטר</ItemName>
          <ItemPrice>7.90</ItemPrice>
        </Item>
      </Items>
    </Root>""".encode("utf-8")
    rows = parse_price_rows(price_xml, "SHUFERSAL", "PriceFull7290027600007-005-123-202608160800.xml")
    assert len(rows) == 1
    assert rows[0]["branch_code"] == "123"
    assert rows[0]["subchain_id"] == "005"
    assert rows[0]["need_key"] == "diapers"
    assert rows[0]["dimension_value"] == "3"
    assert rows[0]["package_quantity"] == 40

    stores_xml = """<Root><Stores><Store>
      <StoreId>123</StoreId><SubChainId>005</SubChainId><StoreName>BE שדרות</StoreName>
      <Address>רחוב לדוגמה 1</Address><City>שדרות</City>
    </Store></Stores></Root>""".encode("utf-8")
    stores = parse_stores(stores_xml, "SHUFERSAL", "Stores7290027600007-20260816.xml")
    assert stores[0]["branch_code"] == "123"
    assert stores[0]["city"] == "שדרות"
