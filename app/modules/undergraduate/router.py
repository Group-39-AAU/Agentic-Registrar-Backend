"""
Undergraduate Admission module — API routes.

TODO: Implement the following endpoints:
    - POST   /applications        -> Submit application (student, requires auth)
    - GET    /applications        -> List all applications (registrar/admin only)
    - GET    /applications/me     -> List my applications (student, requires auth)
    - GET    /applications/{id}   -> Get specific application (requires auth)
    - PATCH  /applications/{id}   -> Update/review application (registrar/admin only)
"""

from fastapi import APIRouter

router = APIRouter(prefix="/undergraduate", tags=["Undergraduate Admission"])

# Define routes here
