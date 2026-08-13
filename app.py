import streamlit as st

from config import APP_TITLE, APP_CAPTION, APP_MODES
from ui.auth import require_login, render_logout_button
from ui.bunnings_view import render_bunnings_mode
from ui.inventory_view import render_inventory_mode
from ui.poc_check_view import render_poc_check_mode
from ui.units_view import render_units_mode


def main() -> None:
    """
    Main Streamlit entry point.

    NetSuite now supplies one combined item export covering all parts, so
    the app runs as a single "Spare Parts Ordering" flow rather than
    switching between TIMS-shaped worksheet types (Power Parts / All Parts /
    MTD). "Units Ordering" and "POC Check" are separate modes scoped to
    whole units (mowers, chippers, etc.) and Cutting Edge / POC parts
    respectively - same NetSuite saved-search shapes, but their own reorder
    logic. Bunnings stays as its own mode since it's a separate data
    source, not something NetSuite provides.
    """
    st.set_page_config(page_title=APP_TITLE, layout="wide")

    require_login()

    st.title(APP_TITLE)
    st.caption(APP_CAPTION)

    mode = st.sidebar.radio("Mode", APP_MODES)
    render_logout_button()

    if mode == "Bunnings":
        render_bunnings_mode()
    elif mode == "Units Ordering":
        render_units_mode()
    elif mode == "POC Check":
        render_poc_check_mode()
    else:
        render_inventory_mode()


if __name__ == "__main__":
    main()
