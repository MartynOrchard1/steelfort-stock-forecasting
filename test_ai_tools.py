"""Checks the chat tools against a fake dataset - no API calls. Run: python test_ai_tools.py"""
import pandas as pd

from services.ai_tools import extract_sku_tokens, find_parts, order_timing, summarise

codes = ["HA27318A", "HA27373A", "HA27390A", "HA27391A", "HA27394A", "HA27397A", "HA27398A", "HA27404A"]
df = pd.DataFrame({
    "Part_Number": ["PV" + c for c in codes[:6]] + ["XYZ1"],  # last two codes absent on purpose
    "Description": [f"OIL {c} VALVOLINE 1L" for c in codes[:6]] + ["OTHER"],
    "POREF_SUPP": "ROY040",
    "Qty on hand": 100, "Qty Allocated": 10, "Qty on Order": 0,
    "Demand_Per_Month_Used": [30, 300, 0, 10, 10, 10, 1], "Recommended Order": 50,
})

pasted = "3370344 HA27390A OIL 4 STROKE VALVOLINE 1L SAE30 HA27390A Live 9310009018038 5316 3216 0 " + " ".join(codes)
toks = extract_sku_tokens(pasted)
assert all(c in toks for c in codes), toks

res = find_parts(df, skus=codes)
assert res["matched_count"] == 6, res
assert [s for s, h in res["sku_match"].items() if h == "NOT FOUND"] == codes[6:], res["sku_match"]

t = order_timing(df, codes, lead_days=30, safety_days=7)
p = {x["Part_Number"]: x for x in t["parts"]}
assert p["PVHA27373A"]["fast_seller"] and "OVERDUE" in p["PVHA27373A"]["latest_order_date"], p["PVHA27373A"]
assert "note" in p["PVHA27390A"]  # zero velocity -> no date, no guess
assert "warning" in order_timing(df, codes)  # lead time missing is reported
assert summarise(df, supplier="roy")["parts"] == 7
print("ok")
