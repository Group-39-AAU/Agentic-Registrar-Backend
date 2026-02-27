"""
Auth module — API routes.

TODO: Implement the following endpoints:
    - POST /register  -> Register a new user (returns UserResponse)
    - POST /login     -> Authenticate and return JWT (returns TokenResponse)
    - GET  /me        -> Get current user profile (returns UserResponse, requires auth)
"""

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Define routes here
