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

# Receipts: stock received after it last moved is aged from the receipt.
receipts = pd.Series(pd.to_datetime(["2026-06-01", "2025-01-15", "2026-09-01"]), index=["NONE", "NEW", "T24"])
r = compute(reorder, t, ns, "2026-10-07", "2026-07-01", receipts=receipts)[0].set_index("Part_Number")
assert r.at["NONE", "Bucket"] == "Active" and r.at["NONE", "Age_Basis"].startswith("Received")  # was Clearance
assert r.at["NEW", "Bucket"] == "Clearance"            # was Check data: received 21 months ago, never sold
assert r.at["T24", "Bucket"] == "Active" and r.at["T10", "Bucket"] == "Clearance"  # untouched
assert r.at["NONE", "Months_Since_Move"] == df.set_index("Part_Number").at["NONE", "Months_Since_Move"]
# A receipt from before the TIMS window can't age a no-movement part past the window (it may have sold in between).
old = compute(reorder, t, ns, "2026-10-07", "2026-07-01", receipts=pd.Series([pd.Timestamp("2016-03-01")], index=["NONE"]))[0]
o = old.set_index("Part_Number").loc["NONE"]
assert o["Age_Months"] == o["Months_Since_Move"] == 26.2 and o["Age_Basis"].startswith("No movement"), o

# Receipt files: NetSuite-style (Item / Date / Location) and TIMS-style (ith_part / Last Received Date), loc 10 only.
from aged_stock import last_receipts
lr = last_receipts([
    pd.DataFrame({"Item": ["NONE", "NONE", "T24"], "Date": ["01/06/2026", "15/09/2026", "02/09/2026"],
                  "Location": ["10 - PALM NTH PARTS DEP", "DC - PALM NTH DC", "10 - PALM NTH PARTS DEP"]}),
    pd.DataFrame({"ith_part": ["NONE "], "Last Received Date": ["2026-03-01"], "ith_loc": ["10"], "Qty Allocated": ["0"]}),
    # The TIMS export as IT supplied it (SQL headers, ISO dates read from Excel as text).
    pd.DataFrame({"# ITM_Part": ["T18", "T18"], "ITM_Loc": ["10", "DC"],
                  "MAX(STR_TO_DATE(ITM_Date, '%d/%m/%Y'))": ["2026-02-03 00:00:00", "2026-09-30 00:00:00"]}),
])
assert lr["NONE"] == pd.Timestamp("2026-06-01") and lr["T24"] == pd.Timestamp("2026-09-02")  # DC row ignored
assert lr["T18"] == pd.Timestamp("2026-02-03")  # 3 Feb - ISO date not flipped to 2 Mar by dayfirst
try:
    last_receipts([pd.DataFrame({"Item": ["X"], "Qty": ["1"]})])
    raise AssertionError("missing date column should raise")
except ValueError:
    pass
# Filters
from aged_stock import filter_view
f = pd.DataFrame({"Part_Number": ["A1", "B2", "C3"], "Description": ["Blade", "WASHER zp", None],
                  "Part Group": ["G1", "G2", "G1"], "Part Type": ["T1", "T1", "T2"], "Supplier": ["S1", "S2", "S1"],
                  "Flags": ["Back ordered; Obsolete part group", "", "Obsolete part group"],
                  "Age_Basis": ["NetSuite sale", "No movement", "No movement"],
                  "Age_Months": [3.0, 26.2, 14.0], "Stock_Value": [10.0, 500.0, float("nan")]})
ids = lambda **kw: list(filter_view(f, **kw)["Part_Number"])
assert ids() == ["A1", "B2", "C3"]
assert ids(groups=["G1"], types=["T2"]) == ["C3"] and ids(suppliers=["S2"]) == ["B2"]
assert ids(flags=["Obsolete part group"]) == ["A1", "C3"] and ids(bases=["No movement"]) == ["B2", "C3"]
assert ids(age=(12, 20)) == ["C3"] and ids(min_value=100) == ["B2"]
assert ids(search="washer") == ["B2"] and ids(search="c3") == ["C3"]
print("ok")
