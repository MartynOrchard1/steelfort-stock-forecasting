"""Aged stock analysis for location 10 (spare parts).

Combines NetSuite last-sale dates with TIMS monthly movement history to work out
how long each on-hand part has gone without moving, then buckets it:
    Active (<6 mths) / Review (6-12, portal special or flyer) / Clearance (12+)

Use inside the app:           import aged_stock; aged_stock.render(reorder_df)
Or standalone:                streamlit run aged_stock.py
"""
import io
import re
from datetime import date

import pandas as pd

LOC = "10"
TIMS_COLS = [f"ith_{i:02d}" for i in range(1, 25)]  # ith_24 = most recent month
SUPERSEDED = re.compile(r"^REF |NO LONGER AVAILABLE|\bTRY\b|SUPERSEDED", re.I)


def _num(s):
    return pd.to_numeric(s, errors="coerce").fillna(0)


def _cost_col(df):
    # Reorder Rpt has no cost yet; picks it up once Average Cost is added to the saved search.
    for c in df.columns:
        if re.search(r"average cost|avg cost|unit cost", c, re.I):
            return c
    return None


def tims_last_move(tims, latest_month):
    """Month-end date of the last month with positive movement (ith_24 = latest_month)."""
    tims = tims[tims["ith_loc"].astype(str).str.strip() == LOC]
    moved = tims[TIMS_COLS[::-1]].apply(pd.to_numeric, errors="coerce").fillna(0).gt(0).to_numpy()
    back = moved.argmax(axis=1)  # months back from latest
    base = pd.Period(latest_month, "M")
    dates = [(base - int(b)).end_time.normalize() if m else pd.NaT for b, m in zip(back, moved.any(axis=1))]
    return pd.Series(dates, index=tims["ith_part"].astype(str).str.strip(), name="Last_Move_TIMS")


def ns_last_sale(ns):
    ns = ns[ns["Location"].astype(str).str.startswith(LOC + " ")]
    d = pd.to_datetime(ns["Last Sale Date"], dayfirst=True, errors="coerce")
    return pd.Series(d.values, index=ns["Part Number"].astype(str).str.strip(), name="Last_Sale_NS").groupby(level=0).max()


def compute(reorder, tims, ns, as_at, tims_latest_month, review_m=6, clear_m=12):
    inv = reorder[reorder["Inventory Location"].astype(str).str.startswith(LOC + " ")]
    inv = inv.drop_duplicates("Part_Number").copy()  # one row per supplier in the export
    inv["Part_Number"] = inv["Part_Number"].astype(str).str.strip()
    inv["Qty_On_Hand"] = _num(inv["Location On Hand"])
    inv = inv[inv["Qty_On_Hand"] > 0]

    out = inv[["Part_Number", "Description", "Part Group"]].copy()
    out["Qty_On_Hand"] = inv["Qty_On_Hand"]
    for src, dst in [("Location Committed", "Committed"), ("Location On Order", "On_Order"),
                     ("Location Back Ordered", "Back_Ordered"), ("Reorder Point", "Reorder_Point"),
                     ("Preferred Stock Level", "Preferred_Level")]:
        out[dst] = _num(inv[src])
    cost = _cost_col(inv)
    out["Unit_Cost"] = _num(inv[cost]) if cost else float("nan")
    out["Stock_Value"] = out["Qty_On_Hand"] * out["Unit_Cost"]

    t = tims_last_move(tims, tims_latest_month)
    out["In_TIMS"] = out["Part_Number"].isin(t.index)
    out["Last_Move_TIMS"] = out["Part_Number"].map(t)
    out["Last_Sale_NS"] = out["Part_Number"].map(ns_last_sale(ns))
    out["Last_Move"] = out[["Last_Sale_NS", "Last_Move_TIMS"]].max(axis=1)

    as_at = pd.Timestamp(as_at)
    tims_start = (pd.Period(tims_latest_month, "M") - 23).start_time
    no_move = out["Last_Move"].isna()
    # No movement anywhere: age is at least the start of TIMS history (a lower bound).
    out["Months_Since_Move"] = ((as_at - out["Last_Move"].fillna(tims_start)).dt.days / 30.4375).round(1)

    out["Age_Basis"] = "TIMS movement (may include transfers)"
    out.loc[out["Last_Sale_NS"].notna(), "Age_Basis"] = "NetSuite sale"
    out.loc[no_move, "Age_Basis"] = "No movement in TIMS 24 mths or NetSuite (age is a minimum)"
    out.loc[no_move & ~out["In_TIMS"], "Age_Basis"] = "Not in TIMS, no NetSuite sale (new item?)"

    m = out["Months_Since_Move"]
    out["Bucket"] = "Active"
    out.loc[m >= review_m, "Bucket"] = "Review"
    out.loc[m >= clear_m, "Bucket"] = "Clearance"
    out.loc[no_move & ~out["In_TIMS"], "Bucket"] = "Check data"
    aged = out["Bucket"].isin(["Review", "Clearance"])

    flags = [
        (out["Committed"] > 0, "Committed to open orders"),
        (out["Back_Ordered"] > 0, "Back ordered"),
        (aged & (out["On_Order"] > 0), "On order while aged"),
        (aged & ((out["Reorder_Point"] > 0) | (out["Preferred_Level"] > 0)), "Has reorder point - will auto-reorder"),
        (out["Description"].fillna("").str.contains(SUPERSEDED), "Possibly superseded - check replacement's sales"),
        (out["Part Group"].fillna("").str.contains("OBSOLETE", case=False), "Obsolete part group"),
    ]
    out["Flags"] = ""
    for mask, text in flags:
        out.loc[mask, "Flags"] += text + "; "
    out["Flags"] = out["Flags"].str.rstrip("; ")
    out["Conflict"] = aged & ((out["Committed"] > 0) | (out["Back_Ordered"] > 0) | (out["On_Order"] > 0))

    out["Action"] = out["Bucket"].map({
        "Active": "", "Review": "Portal special / flyer candidate",
        "Clearance": "Clearance - do not reorder", "Check data": "Confirm part history before acting"})
    out["Aged_Order_Override"] = (out["Bucket"] == "Clearance").map({True: "DO NOT ORDER", False: ""})
    out["Qty_Available"] = (out["Qty_On_Hand"] - out["Committed"]).clip(lower=0)

    sort = "Stock_Value" if cost else "Qty_On_Hand"
    return out.sort_values(sort, ascending=False).reset_index(drop=True), bool(cost)


def summary(df):
    s = df.groupby("Bucket").agg(Lines=("Part_Number", "size"), Qty=("Qty_On_Hand", "sum"),
                                 Value=("Stock_Value", "sum"))
    return s.reindex(["Active", "Review", "Clearance", "Check data"]).fillna(0)


def portal_list(df, top_n, disc_review, disc_clear, has_cost):
    p = df[df["Bucket"].isin(["Review", "Clearance"]) & ~df["Conflict"] & (df["Qty_Available"] > 0)]
    p = p.head(top_n)[["Part_Number", "Description", "Qty_Available", "Bucket", "Months_Since_Move", "Unit_Cost"]].copy()
    p["Suggested_Discount_%"] = p["Bucket"].map({"Review": disc_review, "Clearance": disc_clear})
    return p if has_cost else p.drop(columns="Unit_Cost")


def to_excel(df, portal, has_cost):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary(df).to_excel(xw, sheet_name="Summary")
        df[df["Bucket"] == "Review"].to_excel(xw, sheet_name="Review 6-12", index=False)
        df[df["Bucket"] == "Clearance"].to_excel(xw, sheet_name="Clearance 12+", index=False)
        df[(df["Bucket"] == "Check data") | df["Conflict"]].to_excel(xw, sheet_name="Data Checks", index=False)
        portal.to_excel(xw, sheet_name="Portal Specials", index=False)
    return buf.getvalue()


def render(reorder=None, tims_file=None, ns_file=None):
    """tims_file / ns_file: CSV file-likes already uploaded elsewhere in the app (skips our uploaders)."""
    import streamlit as st

    st.subheader("Aged Stock - Location 10")
    with st.expander("Files and settings", expanded=reorder is None):
        if reorder is None:
            f = st.file_uploader("NetSuite Part Reorder Rpt (CSV)", type="csv", key="aged_reorder")
            reorder = pd.read_csv(f, dtype=str) if f else None
        ft = tims_file or st.file_uploader("TIMS movement history (Dataset for forecasting 10)", type="csv", key="aged_tims")
        fn = ns_file or st.file_uploader("NetSuite sales history", type="csv", key="aged_ns")
        c1, c2, c3 = st.columns(3)
        as_at = c1.date_input("As-at date", date.today(), key="aged_asat")
        tims_latest = c2.date_input("TIMS latest month (ith_24)", date(2026, 7, 1), key="aged_tl",
                                    help="Last full month before NetSuite go-live.")
        review_m = c3.number_input("Review at (months)", 1, 60, 6, key="aged_rv")
        clear_m = c3.number_input("Clearance at (months)", 1, 120, 12, key="aged_cl")
        top_n = c1.number_input("Portal list: top lines", 5, 500, 30, key="aged_top")
        disc_r = c2.number_input("Review discount %", 0, 90, 15, key="aged_dr")
        disc_c = c2.number_input("Clearance discount %", 0, 90, 30, key="aged_dc")

    if reorder is None or ft is None or fn is None:
        st.info("Upload the reorder report, TIMS history and NetSuite sales history to run.")
        st.session_state.pop("aged_df", None)  # don't leave a stale override on the purchasing tab
        return

    df, has_cost = st.cache_data(show_spinner="Ageing stock...")(compute)(reorder, pd.read_csv(ft, dtype=str), pd.read_csv(fn, dtype=str),
                           as_at, tims_latest, review_m, clear_m)
    st.session_state["aged_df"] = df  # read by the purchasing tab for the DO NOT ORDER override
    if not has_cost:
        st.warning("No cost column found (e.g. 'Average Cost') - showing quantities only, sorted by qty. "
                   "Add Average Cost to the Part Reorder Rpt saved search to get stock values.")

    s = summary(df)
    cols = st.columns(4)
    for col, b in zip(cols, s.index):
        val = f"${s.at[b, 'Value']:,.0f}" if has_cost else f"{s.at[b, 'Qty']:,.0f} units"
        col.metric(b, val, f"{int(s.at[b, 'Lines']):,} lines", delta_color="off")
    st.bar_chart(s["Value" if has_cost else "Lines"])

    show = st.multiselect("Show buckets", list(s.index), ["Review", "Clearance"], key="aged_show")
    only_conf = st.checkbox("Only lines with conflicts (committed / on order / back ordered)", key="aged_conf")
    view = df[df["Bucket"].isin(show)]
    if only_conf:
        view = view[view["Conflict"]]
    st.dataframe(view, width="stretch", hide_index=True)

    portal = portal_list(df, top_n, disc_r, disc_c, has_cost)
    st.markdown("**Portal specials entry list** (for the New Promotion form)")
    st.dataframe(portal, width="stretch", hide_index=True)
    st.download_button("Download aged stock report (Excel)", to_excel(df, portal, has_cost),
                       f"aged_stock_loc10_{as_at:%Y%m%d}.xlsx", key="aged_dl")


if __name__ == "__main__":
    render()
