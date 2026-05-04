"""
Auth module — FastAPI router.

Endpoints: register, login, get current user profile.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_email_service
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.auth.schemas import RegisterRequest, TokenResponse, UserResponse
from app.modules.auth.service import AuthService
from app.shared.email import EmailService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    data: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """Create a new student account."""
    svc = AuthService(db, email_service=email_service)
    try:
        user = await svc.register(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate with **email** (admission) or **UGR student ID**
    (post-enrollment portal) plus password. Returns a JWT plus a
    ``must_change_password`` flag — when True, the client must call
    ``POST /auth/change-password`` before any other endpoint will
    accept the token.

    Uses OAuth2 form: 'username' field = identifier, 'password' field
    = password.
    """
    svc = AuthService(db)
    try:
        token, must_change = await svc.authenticate(
            form_data.username, form_data.password,
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return TokenResponse(
        access_token=token, must_change_password=must_change,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the currently authenticated user's profile."""
    return current_user
