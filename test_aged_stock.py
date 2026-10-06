import pandas as pd
from aged_stock import compute, TIMS_COLS

RCOLS = ["Location Committed", "Location On Order", "Location Back Ordered", "Reorder Point", "Preferred Stock Level"]


def row(part, oh, desc="X", **kw):
    r = {"Part_Number": part, "Description": desc, "Part Group": "", "Inventory Location": "10 - PALM NTH PARTS DEP",
         "Location On Hand": str(oh), "Average Cost": "10", **{c: "" for c in RCOLS}}
    r.update(kw)
    return r


def tims(part, last_pos):  # last_pos = ith index (1-24) of last positive month, None = no movement
    r = {"ith_part": part, "ith_loc": "10", **{c: "0" for c in TIMS_COLS}}
    if last_pos:
        r[f"ith_{last_pos:02d}"] = "3"
    return r


reorder = pd.DataFrame([
    row("NS", 2), row("T24", 1), row("T18", 1), row("T10", 1), row("NONE", 5), row("NEW", 1),
    row("ZERO", 0), row("COMMIT", 1, **{"Location Committed": "1"}), row("NS", 2),  # duplicate supplier row
])
t = pd.DataFrame([tims("NS", 2), tims("T24", 24), tims("T18", 18), tims("T10", 10), tims("NONE", None),
                  tims("COMMIT", 10)])
ns = pd.DataFrame({"Location": ["10 - PALM NTH PARTS DEP", "DC - PALM NTH DC"], "Part Number": ["NS", "T10"],
                   "Last Sale Date": ["15/09/2026", "15/09/2026"]})  # DC sale must be ignored

df, has_cost = compute(reorder, t, ns, "2026-10-07", "2026-07-01")
b = df.set_index("Part_Number")["Bucket"]
assert has_cost
assert "ZERO" not in b and len(df) == 7, df
assert b["NS"] == "Active" and b["T24"] == "Active"     # sale Sep 26 / Jul 26
assert b["T18"] == "Review"                              # Jan 26 -> ~8 months
assert b["T10"] == "Clearance"                           # May 25, DC sale ignored
assert b["NONE"] == "Clearance" and b["NEW"] == "Check data"
c = df.set_index("Part_Number").loc["COMMIT"]
assert c["Conflict"] and "Committed" in c["Flags"] and c["Aged_Order_Override"] == "DO NOT ORDER"
assert df.set_index("Part_Number").at["NONE", "Stock_Value"] == 50
print("ok")
