"""
Auth module — business logic layer.

TODO: Implement AuthService with methods:
    - register(data: UserRegisterRequest) -> User
        - Check for duplicate email
        - Hash password
        - Create user via repository
        - Emit audit log
    - authenticate(email, password) -> str | None
        - Verify credentials
        - Check is_active
        - Create and return JWT token
        - Emit audit log
"""
