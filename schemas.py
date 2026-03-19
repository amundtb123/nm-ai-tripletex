"""Pydantic models for HTTP request bodies."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TripletexCredentialsIn(BaseModel):
    """Tripletex session + API base URL (Basic auth user is fixed to \"0\" in the client)."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(..., description="Tripletex API base URL, e.g. https://api.tripletex.io/v2")
    session_token: str = Field(..., min_length=1, description="Session token used as Basic Auth password")


class IncomingFileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., min_length=1, description="Original file name")
    content_base64: str = Field(..., min_length=1, description="File bytes as standard base64")


class SolveRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [
            {
                "prompt": "Søk etter kunde Acme AS",
                "files": [],
                "tripletex_credentials": {
                    "base_url": "https://api.tripletex.io/v2",
                    "session_token": "<session-token>",
                },
            }
        ]
    })

    prompt: str = Field(..., description="User task in natural language")
    files: list[IncomingFileIn] = Field(default_factory=list, description="Optional base64-encoded attachments")
    tripletex_credentials: TripletexCredentialsIn
