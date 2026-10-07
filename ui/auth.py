import hmac
import time

import streamlit as st

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
FAIL_DELAY_SECONDS = 1  # slows guessing from a single session


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

    # Lockout is per browser session: Streamlit can't reliably tell clients apart by IP (on
    # Streamlit Cloud they may all share a proxy address, so an IP lockout could lock out every
    # user at once). It stops casual guessing; a long random password is the real protection.
    now = time.time()
    fails = [t for t in st.session_state.get("login_failures", []) if now - t < LOCKOUT_SECONDS]
    st.session_state["login_failures"] = fails
    if len(fails) >= MAX_ATTEMPTS:
        minutes = int((LOCKOUT_SECONDS - (now - fails[0])) // 60) + 1
        st.error(f"Too many incorrect passwords. Try again in {minutes} minute(s).")
        st.stop()

    with st.form("login_form"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        # compare_digest: the check takes the same time however much of the password matches
        if hmac.compare_digest(password.encode(), str(correct_password).encode()):
            st.session_state["authenticated"] = True
            st.session_state.pop("login_failures", None)
            st.rerun()
        else:
            time.sleep(FAIL_DELAY_SECONDS)
            st.session_state["login_failures"] = fails + [now]
            st.error("Incorrect password.")

    st.stop()


def render_logout_button() -> None:
    """Small logout control - call from the sidebar once logged in."""
    if st.sidebar.button("Log out"):
        st.session_state["authenticated"] = False
        st.rerun()
