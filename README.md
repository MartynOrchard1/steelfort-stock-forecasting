# 📦 Steelfort Stock Forecasting

A Streamlit application for analysing inventory data, integrating demand forecasting, and generating intelligent ordering recommendations — built for Steelfort's real-world inventory workflows.

**Version:** 7.0.0 — Modular refactor with Bunnings as a dedicated worksheet type

---

## Overview

This tool helps procurement and operations teams cut through the noise of legacy inventory exports. Upload a stock file, optionally attach a forecast, and get clear, prioritised order recommendations in seconds — no spreadsheet wrangling required.

---

## Quick Start

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Run the app:**
```bash
streamlit run app.py
```

**Or run with Docker:**
```bash
docker build -t steelfort-forecasting .
docker run -p 8501:8501 steelfort-forecasting
```

Then open `http://localhost:8501` in your browser.

---

## Workflow

1. Select a **Worksheet Type** — Power Parts, All Parts, MTD, or Bunnings
2. **Upload** your inventory file (CSV, XLSX, or XLS)
3. Optionally **upload a forecast file** (monthly demand history)
4. Configure **demand basis** and **months target** in the sidebar
5. **Filter and review** recommended orders
6. **Export** a CSV of your order list

---

## Worksheet Types

| Type | Description |
|---|---|
| Power Parts | Filtered view for high-velocity parts |
| All Parts | Full inventory scope |
| MTD | Month-to-date demand view |
| Bunnings | Dedicated mode for Bunnings supplier stock |

---

## File Inputs

### Inventory File *(required)*
Accepted: `.csv`, `.xlsx`, `.xls`

The loader auto-detects header rows and normalises column naming — so exports from most legacy systems should work out of the box. Expected fields include Part Number, Description, Supplier, Qty on Hand, Qty Allocated, Qty on Order, Min/Max levels, and usage averages.

### Forecast File *(optional)*
Expected structure:
- `ith_part` — Part number
- `ith_01` → `ith_24` — Monthly demand columns (ith_24 = most recent month)

The system automatically aggregates 3-month, 6-month, and 12-month totals/averages, plus a recency-weighted 6-month demand figure. If a part has no forecast match, it falls back to worksheet averages.

---

## Calculations

| Field | Formula |
|---|---|
| Available | Qty on Hand − Qty Allocated |
| Net After POs | Qty on Hand + Qty on Order − Qty Allocated |
| Target Stock | Demand per Month × Months Target |
| Recommended Order | Target Stock − Available |

Optional EOQ rounding can be applied via the sidebar.

---

## Demand Basis Options

- Custom forecast average (1–24 months)
- Weighted 6-month forecast (recency-weighted)
- 6-month average (6mAvg)
- 12-month average (12mAvg)

---

## Priority System

| Priority | Meaning |
|---|---|
| ✅ OK | Stock levels are sufficient |
| 🟡 REPLENISH | Below minimum stock level |
| 🔴 URGENT | Negative net stock (even after incoming orders) |

Where no minimum stock level is defined, a fallback minimum of 5 is applied.

---

## Project Structure

```
steelfort-stock-forecasting/
├── app.py                    # Streamlit entry point
├── config.py                 # App-wide constants
├── requirements.txt
├── Dockerfile
├── services/
│   ├── bunnings_service.py   # Bunnings-specific data cleaning
│   ├── forecast_service.py   # Forecast loading and merging
│   ├── inventory_service.py  # Core inventory calculations
│   └── file_loader.py        # Multi-format file ingestion
├── ui/
│   ├── inventory_view.py     # Main inventory table + controls
│   ├── bunnings_view.py      # Bunnings-specific UI
│   └── dialogs.py            # Part detail popups
└── utils/
    └── helpers.py            # Part number normalisation, shared utilities
```

---

## Dependencies

```
streamlit
pandas
openpyxl
numpy
```

---

## Roadmap

- ERP integration (e.g. NetSuite)
- Automated supplier order generation
- Historical trend visualisation
- AI-driven demand forecasting
- Stock anomaly detection

---

## Author

Martyn James Orchard — Bachelor of ICT, Software Engineering Major