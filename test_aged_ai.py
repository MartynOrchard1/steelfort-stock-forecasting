"""Checks the aged-stock AI plumbing with a fake Claude client - no API calls, no cost."""
import io
import typing
from types import SimpleNamespace

import pandas as pd

import services.aged_ai as ai


class FakeClient:
    """Answers messages.parse with one canned item per CSV row it was sent."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def parse(self, *, output_format, messages, **_):
        self.calls += 1
        rows = pd.read_csv(io.StringIO(messages[0]["content"].split("\n\n")[-1]), dtype=str)
        item = output_format.model_fields["lines"].annotation.__args__[0]

        def value(name, ann):
            if name == "part_number":
                return None
            return typing.get_args(ann)[0] if typing.get_origin(ann) is typing.Literal else f"{name}!"

        lines = [item(part_number=p, **{n: value(n, f.annotation) for n, f in item.model_fields.items()
                                        if n != "part_number"})
                 for p in rows["Part_Number"]]
        return SimpleNamespace(stop_reason="end_turn", parsed_output=output_format(lines=lines))


fake = FakeClient()
ai.get_client = lambda: fake

portal = pd.DataFrame({"Part_Number": ["A1", "B2"], "Description": ["WASHER ZP", "BLADE"], "Bucket": ["Review", "Clearance"]})
promo = ai.write_promo_copy(portal)
assert list(promo["Promo_Title"]) == ["title!", "title!"] and len(promo) == 2, promo

aged = pd.DataFrame({
    "Part_Number": [f"P{i}" for i in range(120)] + ["ACTIVE"],
    "Description": "X", "Part Group": "G", "Qty_On_Hand": 1.0, "Unit_Cost": 2.0,
    "Stock_Value": [float(i) for i in range(120)] + [999.0], "Months_Since_Move": 13.0, "Flags": "",
    "Bucket": ["Clearance"] * 120 + ["Active"],
})
reorder = pd.DataFrame({"Part_Number": ["P119 ", "P119"], "Supplier": ["SUP1", "SUP2"]})  # dup supplier rows
lines = ai.clearance_lines(aged, reorder, 110)
assert len(lines) == 110 and lines.iloc[0]["Part_Number"] == "P119" and lines.iloc[0]["Supplier"] == "SUP1"
fake.calls = 0
triage = ai.triage_clearance(lines)
assert fake.calls == 3 and len(triage) == 110 and triage["Suggested_Action"].eq("Discount / portal special").all()

print("ok")
