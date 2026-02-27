"""
Security utilities: JWT token creation/verification and password hashing.

TODO: Implement:
    - hash_password(password: str) -> str
    - verify_password(plain_password, hashed_password) -> bool
    - create_access_token(data: dict, expires_delta: timedelta | None) -> str
    - decode_access_token(token: str) -> dict | None

Dependencies:
    - python-jose for JWT
    - passlib[bcrypt] for password hashing
    - Settings from core/config.py for SECRET_KEY, ALGORITHM, expiry
"""
