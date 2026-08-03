import csv
import io
import pandas as pd
import streamlit as st

from config import HEADER_MARKERS


@st.cache_data(show_spinner=False)
def load_file_from_bytes(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Load a CSV or Excel file from raw bytes.

    The loader scans for a valid header row so the app can handle
    messy exports where headers are not always on row 1.
    """
    file_name = file_name.lower()

    if file_name.endswith(".csv"):
        text = file_bytes.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))

        header_index = None
        for i, row in enumerate(rows):
            cleaned = [str(cell).replace("\n", " ").strip() for cell in row]
            if any(cell in HEADER_MARKERS for cell in cleaned):
                header_index = i
                break

        if header_index is None:
            raise ValueError("Could not find a valid header row in the CSV.")

        header = [str(x).replace("\n", " ").strip() for x in rows[header_index]]
        data_rows = rows[header_index + 1:]

        fixed_rows = []
        header_len = len(header)

        for row in data_rows:
            if not row or all(str(cell).strip() == "" for cell in row):
                continue

            if len(row) < header_len:
                row = row + [""] * (header_len - len(row))
            elif len(row) > header_len:
                row = row[:header_len]

            fixed_rows.append(row)

        return pd.DataFrame(fixed_rows, columns=header)

    excel_buffer = io.BytesIO(file_bytes)
    df = pd.read_excel(excel_buffer, header=None)

    header_index = None
    max_scan = min(30, len(df))

    for i in range(max_scan):
        row_values = [str(x).replace("\n", " ").strip() for x in df.iloc[i].tolist()]
        if any(val in HEADER_MARKERS for val in row_values):
            header_index = i
            break

    if header_index is None:
        raise ValueError("Could not find a valid header row in the Excel file.")

    header = [str(x).replace("\n", " ").strip() for x in df.iloc[header_index].tolist()]
    df = df.iloc[header_index + 1:].copy()
    df.columns = header
    df = df.reset_index(drop=True)

    return df