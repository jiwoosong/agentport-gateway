"""Small deterministic service for end-to-end tests; no network or filesystem access."""

import csv
import io

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI()
invocations = 0


class Input(BaseModel):
    text: str = Field(max_length=500000)


@app.get("/health")
def health():
    return {"ok": True, "invocations": invocations}


@app.post("/convert")
def convert(value: Input):
    global invocations
    invocations += 1
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["line", "text"])
    lines = value.text.splitlines()
    for number, text in enumerate(lines, start=1):
        writer.writerow([number, text.upper()])
    return {"filename": "converted.csv", "text": output.getvalue(), "lines": len(lines)}
