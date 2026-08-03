from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from inventory_engine import (
    explain_part_decision,
    get_part_details,
    load_and_run,
)

app = FastAPI(
    title="Steelfort Inventory API",
    version="1.0.0",
    description="API wrapper around the Streamlit inventory ordering logic."
)


# =========================================================
# REQUEST MODELS
# =========================================================

class ReorderCandidatesRequest(BaseModel):
    inventory_path: str = Field(..., description="Path to the inventory CSV/XLSX file")
    forecast_path: str | None = Field(None, description="Optional path to the forecast CSV/XLSX file")
    worksheet_type: str = "All Parts"
    months_target: int = 6
    demand_basis: str = "6mAvg"
    custom_forecast_months: int = 3
    only_need_order: bool = True
    use_eoq_rounding: bool = False
    exclude_nla: bool = True
    selected_main_filters: list[str] = Field(default_factory=list)
    selected_priorities: list[str] | None = None
    only_below_min: bool = False
    only_allocated: bool = False
    text_search: str = ""
    sort_col: str = "Recommended Order"
    sort_desc: bool = True
    limit: int = 250


class PartDetailsRequest(BaseModel):
    inventory_path: str
    forecast_path: str | None = None
    part_number: str
    worksheet_type: str = "All Parts"
    months_target: int = 6
    demand_basis: str = "6mAvg"
    custom_forecast_months: int = 3
    use_eoq_rounding: bool = False
    exclude_nla: bool = False


class ExplainPartRequest(BaseModel):
    inventory_path: str
    forecast_path: str | None = None
    part_number: str
    worksheet_type: str = "All Parts"
    months_target: int = 6
    demand_basis: str = "6mAvg"
    custom_forecast_months: int = 3
    use_eoq_rounding: bool = False
    exclude_nla: bool = False


# =========================================================
# HELPERS
# =========================================================

def clean_json_value(value: Any) -> Any:
    """
    Convert pandas/numpy values into JSON-safe Python values.
    """
    if value is None:
        return None

    if isinstance(value, (np.integer,)):
        return int(value)

    if isinstance(value, (np.floating, float)):
        if np.isnan(value):
            return None
        return float(value)

    if isinstance(value, (np.bool_,)):
        return bool(value)

    return value


def dataframe_to_records(df, limit: int | None = None) -> list[dict[str, Any]]:
    if limit is not None:
        df = df.head(limit)

    records = df.replace({np.nan: None}).to_dict(orient="records")
    cleaned_records = []

    for record in records:
        cleaned_records.append(
            {key: clean_json_value(value) for key, value in record.items()}
        )

    return cleaned_records


# =========================================================
# ROUTES
# =========================================================

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "steelfort-inventory-api",
        "version": "1.0.0",
    }


@app.post("/reorder-candidates")
def reorder_candidates(payload: ReorderCandidatesRequest) -> dict[str, Any]:
    try:
        df, meta = load_and_run(
            inventory_path=payload.inventory_path,
            forecast_path=payload.forecast_path,
            worksheet_type=payload.worksheet_type,
            months_target=payload.months_target,
            demand_basis=payload.demand_basis,
            custom_forecast_months=payload.custom_forecast_months,
            only_need_order=payload.only_need_order,
            use_eoq_rounding=payload.use_eoq_rounding,
            exclude_nla=payload.exclude_nla,
            selected_main_filters=payload.selected_main_filters,
            selected_priorities=payload.selected_priorities,
            only_below_min=payload.only_below_min,
            only_allocated=payload.only_allocated,
            text_search=payload.text_search,
            sort_col=payload.sort_col,
            sort_desc=payload.sort_desc,
        )

        records = dataframe_to_records(df, limit=payload.limit)

        return {
            "meta": meta,
            "count": len(records),
            "items": records,
        }

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}") from exc


@app.post("/part-details")
def part_details(payload: PartDetailsRequest) -> dict[str, Any]:
    try:
        details = get_part_details(
            inventory_path=payload.inventory_path,
            forecast_path=payload.forecast_path,
            part_number=payload.part_number,
            worksheet_type=payload.worksheet_type,
            months_target=payload.months_target,
            demand_basis=payload.demand_basis,
            custom_forecast_months=payload.custom_forecast_months,
            use_eoq_rounding=payload.use_eoq_rounding,
            exclude_nla=payload.exclude_nla,
        )

        if details is None:
            raise HTTPException(
                status_code=404,
                detail=f"Part not found: {payload.part_number}"
            )

        cleaned = {key: clean_json_value(value) for key, value in details.items()}
        return cleaned

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}") from exc


@app.post("/explain-part")
def explain_part(payload: ExplainPartRequest) -> dict[str, Any]:
    try:
        explanation = explain_part_decision(
            inventory_path=payload.inventory_path,
            forecast_path=payload.forecast_path,
            part_number=payload.part_number,
            worksheet_type=payload.worksheet_type,
            months_target=payload.months_target,
            demand_basis=payload.demand_basis,
            custom_forecast_months=payload.custom_forecast_months,
            use_eoq_rounding=payload.use_eoq_rounding,
            exclude_nla=payload.exclude_nla,
        )

        if explanation is None:
            raise HTTPException(
                status_code=404,
                detail=f"Part not found: {payload.part_number}"
            )

        cleaned = {key: clean_json_value(value) for key, value in explanation.items()}
        return cleaned

        

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}") from exc
    
import os
from fastapi import Query
from fastapi.responses import JSONResponse
import pandas as pd

@app.get("/read-csv")
def read_csv(
    path: str = Query(..., description="Path to the CSV file"),
    limit: int = Query(50, description="Max rows to return"),
    offset: int = Query(0, description="Row offset"),
) -> dict[str, Any]:
    try:
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail=f"File not found: {path}")

        df = pd.read_csv(path, encoding="latin-1")
        total_rows = len(df)
        df_slice = df.iloc[offset:offset + limit]
        records = dataframe_to_records(df_slice)

        return {
            "file": path,
            "total_rows": total_rows,
            "columns": list(df.columns),
            "offset": offset,
            "limit": limit,
            "count": len(records),
            "rows": records,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading file: {exc}") from exc