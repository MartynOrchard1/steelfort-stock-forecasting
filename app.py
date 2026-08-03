import streamlit as st

from config import APP_TITLE, APP_CAPTION, APP_MODES
from ui.bunnings_view import render_bunnings_mode
from ui.inventory_view import render_inventory_mode


def main() -> None:
    """
    Main Streamlit entry point.

    NetSuite now supplies one combined item export covering all parts, so
    the app runs as a single Inventory flow rather than switching between
    TIMS-shaped worksheet types (Power Parts / All Parts / MTD). Bunnings
    stays as its own mode since it's a separate data source, not something
    NetSuite provides.
    """
    st.set_page_config(page_title=APP_TITLE, layout="wide")

    st.title(APP_TITLE)
    st.caption(APP_CAPTION)

    mode = st.sidebar.radio("Mode", APP_MODES)

    if mode == "Bunnings":
        render_bunnings_mode()
    else:
        render_inventory_mode()


if __name__ == "__main__":
    main()
