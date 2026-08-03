In seperate terminals run these:
- `ngrok http http://localhost:8000`
- `python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload`

Copy the ngrok FORWARDING link and give it to openclaw

just run the streamlit app.
- `streamlit run all_parts_v6.py`