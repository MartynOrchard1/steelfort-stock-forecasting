"""
Application-wide config and constants.

Keeping constants here avoids magic strings being repeated
all over the project.
"""

APP_TITLE = "Steelfort Stock Forecasting"
APP_CAPTION = "Version 8.0.0 - Rebuilt around NetSuite as the single inventory source"

# NetSuite gives one combined saved search covering all parts, so there's no
# more need to pick a worksheet "shape" up front (Power Parts / All Parts /
# MTD were all just different TIMS export formats). Bunnings stays separate
# because it's a genuinely different data source (from Bunnings, not NetSuite).
APP_MODES = ["Inventory", "Bunnings"]

HEADER_MARKERS = {
    "POREF_PART",
    "Part_Number",
    "ITMAS_PART",
    "Part",
    "PART",
    "Type",
    "TYPE",
    "ith_part",
    "part_number",
    "Part #",
    "Steelfort Sku",
    "SKU",
    "Bunnings Item Number",
    "Item Description",
}

# Some parts don't carry a supplier in the NetSuite export, but their part
# number prefix reliably identifies the supplier. Used as a fallback only -
# it fills in a supplier when one is missing, it never overrides a supplier
# that's already present in the source data.
PART_PREFIX_SUPPLIER_MAP = {
    "PV": "ROY040",
    "MT": "MTD021",
    "HU": "MTD021",
}