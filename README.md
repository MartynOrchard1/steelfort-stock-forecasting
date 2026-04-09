# 📦 Steelfort Stock Forecasting

A Streamlit application for analysing inventory data, integrating demand forecasting, and generating intelligent ordering recommendations — built for Steelfort's real-world inventory workflows.

**Version:** 7.0.0 — Modular refactor with Bunnings as a dedicated worksheet type

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
  - [Local Setup](#local-setup)
  - [Docker](#docker)
- [Workflow](#workflow)
- [Worksheet Types](#worksheet-types)
- [File Inputs](#file-inputs)
- [Calculations](#calculations)
- [Demand Basis Options](#demand-basis-options)
- [Priority System](#priority-system)
- [OpenClaw Integration](#openclaw-integration)
- [Architecture](#architecture)
- [Dependencies](#dependencies)
- [Roadmap](#roadmap)
- [Author](#author)

---

## Overview

Steelfort Stock Forecasting helps procurement and operations teams cut through the noise of legacy inventory exports. Upload a stock file, optionally attach a forecast, and get clear, prioritised order recommendations in seconds — no spreadsheet wrangling required.

The application is designed around Steelfort's real inventory workflows and supports multiple export formats from legacy ERP systems. It intelligently auto-detects headers, normalises part numbers, and merges forecast data where available — falling back gracefully to worksheet averages when it isn't.

---

## Features

- **Multi-format file ingestion** — Accepts `.csv`, `.xlsx`, and `.xls` inventory and forecast files
- **Automatic header detection** — Handles messy exports from legacy systems with varying column naming conventions
- **Four worksheet modes** — Power Parts, All Parts, MTD, and a dedicated Bunnings supplier mode
- **Demand forecasting integration** — Merges 24-month forecast history and computes 3m, 6m, 12m, and recency-weighted averages per part
- **Intelligent order recommendations** — Calculates suggested order quantities based on configurable demand basis and months target
- **Priority triage** — Flags URGENT, REPLENISH, and OK stock levels at a glance
- **EOQ rounding** — Optional Economic Order Quantity rounding via the sidebar
- **Bunnings-specific mode** — Dedicated cleaning, display, and ordering logic for Bunnings supplier stock
- **Export to CSV** — Download your order list directly from the interface
- **Docker support** — Fully containerised for consistent deployment

---

## Project Structure

```
steelfort-stock-forecasting/
├── app.py                        # Streamlit entry point — minimal, routes to view modules
├── config.py                     # App-wide constants (title, worksheet types, header markers)
├── requirements.txt
├── Dockerfile
│
├── services/
│   ├── __init__.py
│   ├── file_loader.py            # Multi-format file ingestion with auto header detection
│   ├── inventory_service.py      # Core inventory cleaning, normalisation, and calculations
│   ├── forecast_service.py       # Forecast loading, aggregation, and merging logic
│   └── bunnings_service.py       # Bunnings-specific data cleaning and column mapping
│
├── ui/
│   ├── __init__.py
│   ├── inventory_view.py         # Main inventory table, sidebar controls, export
│   ├── bunnings_view.py          # Bunnings-specific UI and ordering table
│   ├── demand_trend_preview.py   # Demand trend chart preview component
│   └── dialogs.py                # Part detail popup dialogs
│
├── utils/
│   ├── __init__.py
│   └── helpers.py                # Part number normalisation and shared utilities
│
└── openclaw/
    ├── api.py                    # FastAPI backend for OpenClaw agent integration
    ├── inventory_engine.py       # Inventory processing engine exposed to OpenClaw
    └── instructions for claw.md  # Setup instructions for running the OpenClaw agent
```

---

## Quick Start

### Local Setup

**1. Clone the repository:**
```bash
git clone <repo-url>
cd steelfort-stock-forecasting
```

**2. Install dependencies:**
```bash
pip install -r requirements.txt
```

**3. Run the app:**
```bash
streamlit run app.py
```

Then open [http://localhost:8501](http://localhost:8501) in your browser.

### Docker

**Build the image:**
```bash
docker build -t steelfort-forecasting .
```

**Run the container:**
```bash
docker run -p 8501:8501 steelfort-forecasting
```

Then open [http://localhost:8501](http://localhost:8501) in your browser.

---

## Workflow

1. Select a **Worksheet Type** from the dropdown — Power Parts, All Parts, MTD, or Bunnings
2. **Upload** your inventory file (CSV, XLSX, or XLS)
3. Optionally **upload a forecast file** (24-month demand history)
4. Configure **demand basis** and **months target** in the sidebar
5. Optionally enable **EOQ rounding**
6. **Filter and review** recommended orders by priority
7. **Export** a CSV of your order list

---

## Worksheet Types

| Type | Description |
|---|---|
| Power Parts | Filtered view for high-velocity, fast-moving parts |
| All Parts | Full inventory scope across all parts and suppliers |
| MTD | Month-to-date demand view for current period analysis |
| Bunnings | Dedicated mode for Bunnings supplier stock with its own column schema |

---

## File Inputs

### Inventory File *(required)*

Accepted formats: `.csv`, `.xlsx`, `.xls`

The file loader auto-detects header rows using known marker columns and normalises column naming — so exports from most legacy ERP systems (including POREF and ITMAS schemas) work out of the box.

Expected fields include:

| Field | Description |
|---|---|
| Part Number | Unique part identifier (various naming conventions supported) |
| Description | Part name or description |
| Supplier | Supplier code |
| Type | Part type/category |
| Qty on Hand | Current stock on hand |
| Qty Allocated | Quantity committed to existing orders |
| Qty on Order | Quantity on outstanding purchase orders |
| Min / Max | Minimum and maximum stock levels |
| 6mAvg / 12mAvg | Six and twelve month usage averages |
| EOQ | Economic Order Quantity |
| Loc | Stock location |
| Status | Part status |

### Forecast File *(optional)*

Accepted formats: `.csv`, `.xlsx`, `.xls`

Expected structure:

| Column | Description |
|---|---|
| `ith_part` | Part number (or equivalent — `part_number`, `part` are also recognised) |
| `ith_01` → `ith_24` | Monthly demand columns, where `ith_24` = most recent month |

The system automatically computes 3-month, 6-month, and 12-month totals and averages, plus a recency-weighted 6-month demand figure. Parts not present in the forecast file fall back to worksheet averages from the inventory file.

---

## Calculations

| Field | Formula |
|---|---|
| Available | Qty on Hand − Qty Allocated |
| Net After POs | Qty on Hand + Qty on Order − Qty Allocated |
| Target Stock | Demand per Month × Months Target |
| Recommended Order | Target Stock − Available |

Where no minimum stock level is defined for a part, a fallback minimum of **5** is applied.

Optional **EOQ rounding** can be enabled via the sidebar — recommended order quantities are rounded up to the nearest EOQ multiple when set.

---

## Demand Basis Options

The demand basis controls which figure is used as the "demand per month" input for all calculations. Options:

| Basis | Description |
|---|---|
| Custom forecast average | Average over a user-selected 1–24 month window from the forecast file |
| Weighted 6-month forecast | Recency-weighted average over the 6 most recent forecast months |
| 6-month average (6mAvg) | 6-month usage average from the inventory worksheet |
| 12-month average (12mAvg) | 12-month usage average from the inventory worksheet |

---

## Priority System

| Priority | Meaning |
|---|---|
| ✅ OK | Stock levels are sufficient — no action required |
| 🟡 REPLENISH | Stock is below the minimum level — order recommended |
| 🔴 URGENT | Net stock is negative even after accounting for incoming orders — order immediately |

---

## OpenClaw Integration

The `openclaw/` directory contains a **FastAPI backend** that exposes inventory processing capabilities to an OpenClaw AI agent. This allows conversational querying and automation of inventory workflows via an AI interface.

### Running OpenClaw

In separate terminals:

```bash
# Terminal 1 — expose the local server via ngrok
ngrok http http://localhost:8000

# Terminal 2 — start the FastAPI backend
python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Copy the ngrok forwarding URL and provide it to the OpenClaw agent as its base URL.

The Streamlit app runs independently:

```bash
streamlit run app.py
```

### OpenClaw API

The `api.py` module exposes REST endpoints that the OpenClaw agent calls to interact with inventory data programmatically. The `inventory_engine.py` module contains the underlying processing logic consumed by those endpoints.

---

## Architecture

```
                    ┌──────────────────────┐
                    │      app.py           │
                    │  (Streamlit entry)    │
                    └────────┬─────────────┘
                             │
              ┌──────────────┼──────────────┐
              │                             │
   ┌──────────▼──────────┐     ┌────────────▼────────────┐
   │   inventory_view.py  │     │    bunnings_view.py      │
   │  (main UI + export)  │     │  (Bunnings-specific UI)  │
   └──────────┬──────────┘     └────────────┬────────────┘
              │                             │
   ┌──────────▼──────────┐     ┌────────────▼────────────┐
   │  inventory_service   │     │   bunnings_service       │
   │  forecast_service    │     │   file_loader            │
   └──────────┬──────────┘     └────────────┬────────────┘
              │                             │
              └──────────────┬──────────────┘
                             │
                    ┌────────▼────────┐
                    │   utils/helpers  │
                    │  (normalisation) │
                    └─────────────────┘
```

---

## Dependencies

```
streamlit
pandas
openpyxl
numpy
```

Install all with:

```bash
pip install -r requirements.txt
```

The OpenClaw API additionally requires `fastapi` and `uvicorn` if running that component.

---

## Roadmap

- ERP integration (e.g. NetSuite)
- Automated supplier order generation
- Historical trend visualisation
- AI-driven demand forecasting
- Stock anomaly detection

---

## Author

**Martyn James Orchard** — Bachelor of ICT, Software Engineering Major
