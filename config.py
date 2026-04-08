"""
Application-wide config and constants.

Keeping constants here avoids magic strings being repeated
all over the project.
"""

APP_TITLE = "Parts Order Forecasting"
APP_CAPTION = "Version 7.0.0 - Modular refactor with Bunnings as its own worksheet type"

WORKSHEET_TYPES = ["Power Parts", "All Parts", "MTD", "Bunnings"]

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