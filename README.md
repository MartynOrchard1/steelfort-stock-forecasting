# Supplier Order Review V6

A high-performance Streamlit application designed to analyse inventory data, integrate forecasting, and generate intelligent ordering recommendations.

This tool is built to support real-world inventory workflows, particularly where legacy systems and inconsistent data structures make decision-making difficult.

## Overview

Supplier Order Review V6 allows users to:

- Upload inventory exports (CSV or Excel)
- Optionally upload forecasting datasets
- Automatically clean and standardise messy data
- Calculate stock health and demand-driven ordering
- Identify urgent and replenishment items
- Filter, sort, and analyse large datasets instantly
- Export recommended order lists

The application is optimised for large datasets using caching and efficient data processing.

## Key Features
### 1. Intelligent Data Cleaning

- Detects header rows automatically
- Normalises part numbers across datasets
- Handles inconsistent column naming
- Converts numeric fields safely
- Flags NLA (No Longer Available) parts

### 2. Inventory Calculations

- Available = Qty on Hand − Qty Allocated  
- Net After POs = Qty on Hand + Qty on Order − Qty Allocated  
- Target Stock = Demand per Month × Months Target  
- Recommended Order = Target Stock − Available  

Optional EOQ rounding is supported.

### 3. Forecast Integration

Supports multiple demand calculation methods:

- Custom forecast average (1–24 months)
- Weighted 6-month forecast
- 6-month average (6mAvg)
- 12-month average (12mAvg)

Automatically falls back to worksheet averages if forecast data is missing.

### 4. Priority System (V2)

Items are categorised into:

- OK → Stock levels are sufficient  
- REPLENISH → Below minimum stock level  
- URGENT → Negative stock after considering incoming orders  

A fallback minimum of 5 is applied where no minimum is defined.

### 5. Dual Table Views

#### Simple View

Core operational fields:
- Part Number
- Description
- Supplier
- Qty on Hand
- Qty Allocated
- Qty on Order
- Available
- Net After POs
- Recommended Order
- Priority

#### Detailed View

Full dataset including:
- Forecast data
- Demand calculations
- Min / Max levels
- EOQ
- Usage metrics

### 6. Filtering & Search

- Filter by Supplier or Type
- Filter by Priority
- Only show items needing order
- Only allocated items
- Only below minimum stock
- Free-text search (Part Number / Description)

### 7. Performance Optimisation

- Uses caching for fast reloads
- Processes large forecasting datasets efficiently
- Minimises recomputation when filters change

### 8. Interactive UI

- Editable table with row selection
- Popup modal for part details
- Sidebar configuration panel

Real-time metrics:
- Rows shown
- Allocated units
- Units to order
- Urgent items
- Forecast match count

## File Inputs

### Inventory File (Required)

Accepted formats:
- CSV
- XLSX
- XLS

Expected fields (flexible naming supported):
- Part Number
- Description
- Supplier
- Qty on Hand
- Qty Allocated
- Qty on Order
- Min / Max
- Usage averages

### Forecast File (Optional)

Expected structure:
- ith_part (Part Number)
- Monthly columns: ith_01 → ith_24

The system aggregates:
- 3-month, 6-month, 12-month totals and averages
- Weighted 6-month demand

## How It Works

1. Upload inventory file  
2. (Optional) Upload forecast file  
3. Select:
   - Worksheet type
   - Demand basis
   - Months target  
4. Apply filters  
5. Review recommended orders  
6. Export results  

## Output

- Interactive table view
- CSV download of filtered order list
- Detailed part-level breakdown via popup

## Installation

pip install -r requirements.txt
streamlit run app.py

## Performance Notes

- Designed for large datasets
- Forecast file processing is cached
- Significant speed improvements over previous versions

## Version

V6

Includes:
- Forecast integration improvements
- Dynamic demand basis
- Performance optimisations
- Table view modes (Simple / Detailed)

## Source Code

Main application file:
See attached script: :contentReference[oaicite:0]{index=0}

## Future Improvements

- Integration with ERP systems (e.g., NetSuite)
- Automated supplier ordering
- Historical trend visualisation
- AI-driven demand forecasting
- Stock anomaly detection

## Author

Martyn James Orchard | Bachelor Of ICT Student | Major Software Engineering