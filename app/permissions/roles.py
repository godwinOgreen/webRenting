"""
permissions/roles.py

Role definitions and pure Python permission logic — no FastAPI imports,
no database access, no HTTP exceptions. This module only knows what
each role IS and what it means at a structural level.

The enforcing layer is permissions/guards.py, which imports from here
and turns these definitions into FastAPI Depends() callables. Keeping
the two files separate means permission logic is testable without
spinning up a FastAPI app or a database session.

Role model (see app/domains/users/models.py):

    users.role       → UserRole:  renter | agent | admin
    users.admin_role → AdminRole: super_admin | admin | moderator
                        (only set when role=admin)

There is NO UserRole.MODERATOR. Moderation is an AdminRole sub-role,
not a top-level role. A moderator is always a user with role=admin and
admin_role=moderator.

Admin hierarchy (highest to lowest):
    super_admin  — full platform access, can manage other admins
    admin        — standard admin operations (approve listings, suspend users)
    moderator    — content moderation (review flags, resolve reports)

Role predicate hierarchy:
    is_moderator()    — exactly AdminRole.MODERATOR
    is_super_admin()  — exactly AdminRole.SUPER_ADMIN
    can_moderate()    — any admin (admin, super_admin, moderator)
    can_manage_users() — admin + super_admin (not moderator)
    can_manage_platform() — super_admin only
"""

from __future__ import annotations

from app.domains.users.models import AdminRole, User, UserRole

# ── Role predicates ───────────────────────────────────────────────────────────
# Pure functions — take a User, return bool.
# Testable with just a User instance: no DB, no HTTP, no FastAPI.


def is_renter(user: User) -> bool:
    """True if the user's top-level role is renter."""
    return user.role == UserRole.RENTER


def is_agent(user: User) -> bool:
    """True if the user's top-level role is agent (includes landlords)."""
    return user.role == UserRole.AGENT


def is_admin(user: User) -> bool:
    """
    True for any user with role=admin, regardless of admin_role.
    Covers super_admin, admin, and moderator.
    """
    return user.role == UserRole.ADMIN


def is_moderator(user: User) -> bool:
    """
    True ONLY for admin users whose admin_role is moderator.

    There is no separate UserRole.MODERATOR — moderation is a sub-role
    on admin users. A user with admin_role=moderator still has
    role=admin in users.role.
    """
    return user.role == UserRole.ADMIN and user.admin_role == AdminRole.MODERATOR


def is_standard_admin(user: User) -> bool:
    """
    True for admin users whose admin_role is exactly admin
    (not super_admin, not moderator).
    """
    return user.role == UserRole.ADMIN and user.admin_role == AdminRole.ADMIN


def is_super_admin(user: User) -> bool:
    """True for admin users whose admin_role is super_admin."""
    return user.role == UserRole.ADMIN and user.admin_role == AdminRole.SUPER_ADMIN


# ── Capability predicates ─────────────────────────────────────────────────────
# These answer "CAN this user do X?" rather than "IS this user a Y?".
# Guards.py uses these for access control decisions.


def can_moderate(user: User) -> bool:
    """
    Can this user approve listings, resolve reports, review content?

    All admin sub-roles can moderate: super_admin, admin, and moderator
    all have content oversight responsibilities.
    """
    return user.role == UserRole.ADMIN


def can_manage_users(user: User) -> bool:
    """
    Can this user manage other users (view all, update roles, view
    audit log)?

    Admin + super_admin only. Moderators can suspend users through
    the moderation workflow, but cannot promote, demote, or view the
    full user management interface.
    """
    return user.role == UserRole.ADMIN and user.admin_role in (
        AdminRole.ADMIN,
        AdminRole.SUPER_ADMIN,
    )


def can_manage_platform(user: User) -> bool:
    """
    Can this user manage platform configuration, admin roles, and
    system settings?

    Super_admin only. This is the highest-privilege check.
    """
    return user.role == UserRole.ADMIN and user.admin_role == AdminRole.SUPER_ADMIN


# ── Admin hierarchy ──────────────────────────────────────────────────────────
# Higher number = more privilege. Guards use this to compare levels.
# Referenced by guards.py require_admin_level().

_ADMIN_HIERARCHY: dict[str, int] = {
    AdminRole.MODERATOR.value: 0,
    AdminRole.ADMIN.value: 1,
    AdminRole.SUPER_ADMIN.value: 2,
}


# ── Role display names ────────────────────────────────────────────────────────
# Used in notification messages, emails, admin UI — keeps display
# strings out of templates and model files.


ROLE_DISPLAY_NAMES: dict[UserRole, str] = {
    UserRole.RENTER: "Renter",
    UserRole.AGENT: "Agent",
    UserRole.ADMIN: "Admin",
}

ADMIN_ROLE_DISPLAY_NAMES: dict[AdminRole, str] = {
    AdminRole.SUPER_ADMIN: "Super Admin",
    AdminRole.ADMIN: "Admin",
    AdminRole.MODERATOR: "Moderator",
}


def display_role(user: User) -> str:
    """
    Human-readable label for a user's role. Returns the most specific
    applicable label — a super_admin shows as "Super Admin", not "Admin".

    Examples:
        renter, admin_role=None           → "Renter"
        agent, admin_role=None            → "Agent"
        admin, admin_role=super_admin     → "Super Admin"
        admin, admin_role=admin           → "Admin"
        admin, admin_role=moderator       → "Moderator"
    """
    if user.role == UserRole.ADMIN and user.admin_role:
        return ADMIN_ROLE_DISPLAY_NAMES.get(user.admin_role, "Admin")
    return ROLE_DISPLAY_NAMES.get(user.role, user.role.value)
