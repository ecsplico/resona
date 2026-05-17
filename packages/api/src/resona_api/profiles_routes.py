"""REST CRUD for postprocessing profile files."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from resona_postprocess.profile import ProfileError

from . import profiles_store as store
from .auth import verify_api_key

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/profiles", summary="List profiles", tags=["Config"])
def list_profiles_route(api_key: str = Depends(verify_api_key)):
    """List every stored profile (name + description)."""
    return {"profiles": store.list_all()}


@router.get("/profiles/{name}", summary="Get a profile", tags=["Config"])
def get_profile_route(name: str, api_key: str = Depends(verify_api_key)):
    """Return one profile's JSON."""
    try:
        data = store.read(name)
    except store.ProfileNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data is None:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    return data


@router.put("/profiles/{name}", summary="Create or replace a profile", tags=["Config"])
def put_profile_route(name: str, body: dict, api_key: str = Depends(verify_api_key)):
    """Validate and store a profile file."""
    try:
        return store.write(name, body)
    except store.ProfileNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProfileError as e:
        raise HTTPException(status_code=400, detail=f"Invalid profile: {e}")


@router.delete("/profiles/{name}", summary="Delete a profile", tags=["Config"])
def delete_profile_route(name: str, api_key: str = Depends(verify_api_key)):
    """Delete a profile file by name."""
    try:
        deleted = store.delete(name)
    except store.ProfileNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    return {"ok": True}
