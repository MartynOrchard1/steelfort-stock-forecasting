import streamlit as st


def require_login() -> None:
    """
    Simple shared-password gate for the whole app.

    Call this as the very first thing in app.py (after st.set_page_config).
    Blocks rendering of everything else until the correct password is
    entered. The password itself lives in Streamlit Secrets (APP_PASSWORD),
    never in source code, so it's never committed to the repo.
    """
    if st.session_state.get("authenticated"):
        return

    st.title("Steelfort Stock Forecasting")
    st.subheader("Login required")

    correct_password = st.secrets.get("APP_PASSWORD")

    if not correct_password:
        st.error(
            "No password has been set up for this app yet. "
            "Add APP_PASSWORD in the app's Secrets (Streamlit Cloud: "
            "Settings > Secrets, or locally in .streamlit/secrets.toml)."
        )
        st.stop()

    with st.form("login_form"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        if password == correct_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    st.stop()


def render_logout_button() -> None:
    """Small logout control - call from the sidebar once logged in."""
    if st.sidebar.button("Log out"):
        st.session_state["authenticated"] = False
        st.rerun()
