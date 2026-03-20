"""Configuration management endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bridge_core.api.models import ErrorResponse
from bridge_core.core import ConfigStore

router = APIRouter(prefix="/v1/config", tags=["config"])


class ConfigValue(BaseModel):
    value: Any


class ConfigResponse(BaseModel):
    config: dict[str, Any]


@router.get("", response_model=ConfigResponse)
async def list_config(request: Request) -> ConfigResponse:
    """List all configuration values."""
    store: ConfigStore = request.app.state.config_store
    return ConfigResponse(config=store.list_all())


@router.get("/{key}", response_model=ConfigValue, responses={404: {"model": ErrorResponse}})
async def get_config(request: Request, key: str) -> ConfigValue:
    """Get a configuration value."""
    store: ConfigStore = request.app.state.config_store
    value = store.get(key)
    if value is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONFIG_NOT_FOUND", "message": f"Config key {key} not found"},
        )
    return ConfigValue(value=value)


@router.put("/{key}", response_model=ConfigValue)
async def set_config(request: Request, key: str, body: ConfigValue) -> ConfigValue:
    """Set a configuration value."""
    store: ConfigStore = request.app.state.config_store
    store.set(key, body.value)
    return ConfigValue(value=body.value)


@router.delete("/{key}", responses={404: {"model": ErrorResponse}})
async def delete_config(request: Request, key: str) -> dict[str, bool]:
    """Delete a configuration value."""
    store: ConfigStore = request.app.state.config_store
    store.delete(key)
    return {"success": True}
