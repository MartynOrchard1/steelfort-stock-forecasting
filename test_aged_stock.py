import pandas as pd
from aged_stock import compute, tims_last_usage

RCOLS = ["Location Committed", "Location On Order", "Location Back Ordered", "Reorder Point", "Preferred Stock Level"]


def row(part, oh, desc="X", **kw):
    r = {"Part_Number": part, "Description": desc, "Part Group": "", "Inventory Location": "10 - PALM NTH PARTS DEP",
         "Location On Hand": str(oh), "Average Cost": "10", **{c: "" for c in RCOLS}}
    r.update(kw)
    return r


def usage(part, sale=None, rec=None, trf=None, adj=None, loc="10"):  # the TIMS last-usage export, as IT supplied it
    iso = lambda d: f"{d} 00:00:00" if d else None
    return {"# ITM_Part": part, "xp_type": "PP", "ITM_Loc": loc, "Last_Rec": iso(rec), "Last_Adj": iso(adj),
            "Last_Trf": iso(trf), "Last_Sale": iso(sale)}


reorder = pd.DataFrame([
    row("NS", 2), row("T24", 1), row("T18", 1), row("T10", 1), row("NONE", 5), row("TRF", 1), row("RECV", 1),
    row("NEW", 1), row("ZERO", 0), row("COMMIT", 1, **{"Location Committed": "1"}), row("NS", 2),  # dup supplier row
])
u = tims_last_usage(pd.DataFrame([
    usage("NS", sale="2024-03-01"), usage("T24", sale="2026-07-15"), usage("T18", sale="2026-02-01"),
    usage("T18", sale="2026-07-30", loc="DC"),                        # other location ignored
    usage("T10", sale="2025-05-15"), usage("NONE", adj="2026-05-01"),  # adjustment only: doesn't count
    usage("TRF", trf="2026-06-10"),                                    # transfer only: doesn't count
    usage("RECV", sale="2023-01-01", rec="2026-06-01"), usage("COMMIT", sale="2024-11-01"),
    usage("OLDEST", rec="2015-01-06"),                                 # earliest date = TIMS records start
]))
ns = pd.DataFrame({"Location": ["10 - PALM NTH PARTS DEP", "DC - PALM NTH DC"], "Part Number": ["NS", "T10"],
                   "Last Sale Date": ["15/09/2026", "15/09/2026"]})  # DC sale must be ignored

df, has_cost = compute(reorder, ns, u, "2026-10-07")
d = df.set_index("Part_Number")
b = d["Bucket"]
assert has_cost
assert "ZERO" not in b and len(df) == 9, df
assert b["NS"] == "Active" and d.at["NS", "Age_Basis"] == "Last sale"   # NetSuite sale beats the older TIMS one
assert b["T24"] == "Active" and b["T18"] == "Review"                   # Jul 26 / Feb 26 (~8 months); DC row ignored
assert b["T10"] == "Clearance"                                          # May 25, DC NetSuite sale ignored
assert b["NONE"] == "Clearance" and b["TRF"] == "Clearance"             # adjustments / transfers don't age stock
assert d.at["NONE", "Age_Basis"].startswith("No sale or receipt since TIMS records began (2015)")
assert d.at["NONE", "Age_Months"] == 141.0 and pd.isna(d.at["NONE", "Months_Since_Sale"])  # minimum: since Jan 2015
assert d.at["TRF", "Last_Transfer"] == pd.Timestamp("2026-06-10")       # shown for context
assert b["RECV"] == "Active" and d.at["RECV", "Age_Basis"].startswith("Received after last sale")
assert d.at["RECV", "Months_Since_Sale"] > 40                           # still shows how long since it sold
assert b["NEW"] == "Check data"                                         # not in TIMS, nothing in NetSuite
c = d.loc["COMMIT"]
assert c["Bucket"] == "Clearance" and c["Conflict"] and "Committed" in c["Flags"] and c["Aged_Order_Override"] == "DO NOT ORDER"
assert d.at["NONE", "Stock_Value"] == 50

# NetSuite receipts (since go-live) age stock from the receipt too.
receipts = pd.Series(pd.to_datetime(["2025-01-15", "2026-09-01"]), index=["NEW", "T10"])
r = compute(reorder, ns, u, "2026-10-07", receipts=receipts)[0].set_index("Part_Number")
assert r.at["NEW", "Bucket"] == "Clearance" and r.at["NEW", "Age_Basis"].startswith("Received")  # was Check data
assert r.at["T10", "Bucket"] == "Active" and r.at["T18", "Bucket"] == "Review"  # T18 untouched
try:
    tims_last_usage(pd.DataFrame({"# ITM_Part": ["X"], "Last_Rec": ["2020-01-01"]}))
    raise AssertionError("missing Last_Sale should raise")
except ValueError:
    pass

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
# Exports: text that Excel would run as a formula comes out as text.
import io as _io
import openpyxl
from aged_stock import portal_list, to_excel
from utils.helpers import csv_for_download, spreadsheet_safe
bad = pd.DataFrame({"Part_Number": ["P1", "P2"], "Description": ['=HYPERLINK("http://x","y")', "- STRAP ONLY"],
                    "Qty": [-3, 4], "Note": [None, "@SUM(A1)"]})
safe = spreadsheet_safe(bad)
assert list(safe["Description"]) == ["'" + bad.at[0, "Description"], "'- STRAP ONLY"] and safe.at[1, "Note"] == "'@SUM(A1)"
assert list(safe["Qty"]) == [-3, 4] and safe.at[0, "Part_Number"] == "P1" and pd.isna(safe.at[0, "Note"])  # numbers / plain text untouched
assert csv_for_download(bad)().decode().count("'=HYPERLINK") == 1
x = df.copy(); x.loc[x.index[0], "Description"] = "=1+1"
wb = openpyxl.load_workbook(_io.BytesIO(to_excel(x, portal_list(x, 5, 15, 30, True), True)))
cells = [c for ws in wb for row in ws.iter_rows() for c in row if isinstance(c.value, str) and "1+1" in c.value]
assert cells and all(c.data_type == "s" for c in cells), [(c.value, c.data_type) for c in cells]  # text, not formula

print("ok")
