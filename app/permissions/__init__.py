"""
permissions — access control for the platform.

roles.py   — what each role IS (pure predicates, no side effects)
guards.py  — what each role CAN DO (raises exceptions when denied)

Usage in route handlers:
    from app.permissions.guards import (
        require_role,
        require_admin_level,
        require_kyc,
        require_subscription,
        require_verified,
    )

    @router.post("/properties")
    async def create_property(
        user = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_db),
    ):
        require_role(user, UserRole.AGENT)
        require_kyc(user)
        await require_subscription(user, db)
        ...
"""
