import streamlit as st

from config import APP_TITLE, APP_CAPTION, WORKSHEET_TYPES
from ui.bunnings_view import render_bunnings_mode
from ui.inventory_view import render_inventory_mode


def main() -> None:
    """
    Main Streamlit entry point.

    Keeps the root app file very small so all business logic
    lives in dedicated modules.
    """
    st.set_page_config(page_title=APP_TITLE, layout="wide")

    st.title(APP_TITLE)
    st.caption(APP_CAPTION)

    worksheet_type = st.selectbox("Worksheet Type", WORKSHEET_TYPES)

    if worksheet_type == "Bunnings":
        render_bunnings_mode()
    else:
        render_inventory_mode(worksheet_type)


if __name__ == "__main__":
    main()