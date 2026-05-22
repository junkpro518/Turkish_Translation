from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings


def require_admin(request: Request, settings: Settings = Depends(get_settings)) -> None:
    if request.session.get("admin_authenticated") is True:
        return
    raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
