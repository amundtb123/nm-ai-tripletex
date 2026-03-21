"""Pydantic models for HTTP request bodies."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def _files_null_to_empty(v: Any) -> Any:
    """NM / proxies sometimes send ``\"files\": null`` instead of []."""
    if v is None:
        return []
    return v


class TripletexCredentialsIn(BaseModel):
    """Tripletex session + API base URL (Basic auth user is fixed to \"0\" in the client)."""

    # Ignore unknown keys so strict clients (extra metadata) do not 422 before /solve runs.
    model_config = ConfigDict(extra="ignore")

    base_url: str = Field(
        ...,
        description="Tripletex API base URL, e.g. https://api.tripletex.io/v2",
        strip_whitespace=True,
    )
    session_token: str = Field(
        ...,
        min_length=1,
        description="Session token used as Basic Auth password",
        strip_whitespace=True,
    )


class IncomingFileIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    filename: str = Field(..., min_length=1, description="Original file name", strip_whitespace=True)
    content_base64: str = Field(..., min_length=1, description="File bytes as standard base64")


class SolveRequestBody(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
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
        },
    )

    prompt: str = Field(..., description="User task in natural language", strip_whitespace=True)
    files: Annotated[list[IncomingFileIn], BeforeValidator(_files_null_to_empty)] = Field(
        default_factory=list,
        description="Optional base64-encoded attachments",
    )
    tripletex_credentials: TripletexCredentialsIn

    @field_validator("prompt", mode="before")
    @classmethod
    def _coerce_prompt_primitive_to_str(cls, v: Any) -> Any:
        """Accept JSON numbers/bools as prompt (some clients mis-encode)."""
        if isinstance(v, (int, float, bool)):
            return str(v)
        return v
