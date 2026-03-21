"""Bluetooth API endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bridge_core.core import SourceRegistry

router = APIRouter(prefix="/v1/bluetooth", tags=["bluetooth"])


class PairingWindowRequest(BaseModel):
    timeout_seconds: int = 90
    candidate_mac: str | None = None


class TrustRequest(BaseModel):
    alias: str | None = None


class PreferredDeviceRequest(BaseModel):
    mac: str | None


def _get_bluetooth_adapter(registry: SourceRegistry) -> Any:
    adapter_info = registry.get_adapter("linux-bluetooth-adapter")
    if not adapter_info or not adapter_info.adapter:
        raise HTTPException(
            status_code=503,
            detail={"code": "BLUETOOTH_ADAPTER_NOT_FOUND", "message": "Bluetooth adapter not found or not active"},
        )
    return adapter_info.adapter


@router.get("/status")
async def get_bluetooth_status(request: Request) -> dict[str, Any]:
    """Get status of the Bluetooth subsystem."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    return await adapter.get_adapter_status()


@router.post("/pairing/open")
async def open_pairing_window(request: Request, body: PairingWindowRequest) -> dict[str, Any]:
    """Open the Bluetooth pairing window."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    result = adapter.start_pairing(body.timeout_seconds, candidate_mac=body.candidate_mac)
    if not result.success:
        raise HTTPException(
            status_code=400,
            detail={"code": "PAIRING_START_FAILED", "message": result.message or "Failed to start pairing"},
        )
    return {"success": True, "message": result.message}


@router.post("/pairing/close")
async def close_pairing_window(request: Request) -> dict[str, Any]:
    """Close the Bluetooth pairing window."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    result = adapter.stop_pairing()
    if not result.success:
        raise HTTPException(
            status_code=400,
            detail={"code": "PAIRING_STOP_FAILED", "message": result.message or "Failed to stop pairing"},
        )
    return {"success": True, "message": result.message}


@router.get("/pairing/status")
async def get_pairing_status(request: Request) -> dict[str, Any]:
    """Get the current status of the pairing window."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    status = await adapter.get_adapter_status()
    return status.get("pairing_window", {})


@router.get("/devices")
async def list_bluetooth_devices(request: Request) -> list[dict[str, Any]]:
    """List all discovered/known Bluetooth devices."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    return await adapter.list_devices()


@router.get("/devices/preferred")
async def get_preferred_device(request: Request) -> dict[str, Any]:
    """Get the current preferred Bluetooth device."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    return {"mac": adapter.get_preferred_device()}


@router.post("/devices/preferred")
async def set_preferred_device(request: Request, body: PreferredDeviceRequest) -> dict[str, Any]:
    """Set the preferred Bluetooth device."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    adapter.set_preferred_device(body.mac)
    return {"success": True, "mac": body.mac}


@router.post("/devices/{mac}/trust")
async def trust_device(request: Request, mac: str, body: TrustRequest) -> dict[str, Any]:
    """Trust a Bluetooth device."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    adapter.trust_device(mac, alias=body.alias)
    return {"success": True}


@router.post("/devices/{mac}/untrust")
async def untrust_device(request: Request, mac: str) -> dict[str, Any]:
    """Untrust a Bluetooth device."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    adapter.forget_device(mac)
    return {"success": True}


@router.delete("/devices/{mac}")
async def remove_device(request: Request, mac: str) -> dict[str, Any]:
    """Remove (unpair) and forget a Bluetooth device."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    success = await adapter.remove_device(mac)
    return {"success": success}


@router.post("/devices/{mac}/connect")
async def connect_device(request: Request, mac: str) -> dict[str, Any]:
    """Connect to a Bluetooth device."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    success = await adapter.connect_device(mac)
    if not success:
        raise HTTPException(
            status_code=400,
            detail={"code": "CONNECTION_FAILED", "message": f"Failed to connect to device {mac}"},
        )
    return {"success": True}


@router.post("/devices/{mac}/disconnect")
async def disconnect_device(request: Request, mac: str) -> dict[str, Any]:
    """Disconnect from a Bluetooth device."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    success = await adapter.disconnect_device(mac)
    if not success:
        raise HTTPException(
            status_code=400,
            detail={"code": "DISCONNECTION_FAILED", "message": f"Failed to disconnect from device {mac}"},
        )
    return {"success": True}


@router.get("/source")
async def get_bluetooth_source_info(request: Request) -> dict[str, Any]:
    """Get info about the active Bluetooth source session."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    sources = adapter.list_sources()
    if not adapter._running or not sources:
        return {"active": False}

    # In practice, LinuxBluetoothAdapter only supports one active capture session
    return {"active": True, "session_id": adapter._session_id, "source": sources[0].model_dump()}


@router.get("/backend/status")
async def get_backend_status(request: Request) -> dict[str, Any]:
    """Get status of the Bluetooth backend."""
    registry: SourceRegistry = request.app.state.source_registry
    adapter = _get_bluetooth_adapter(registry)
    status = await adapter.get_adapter_status()
    return status.get("backend", {})
