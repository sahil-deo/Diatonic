"""Shared Google integration: any number of accounts, used by both the Calendar and Gmail apps."""
from .accounts import GoogleAccounts, GoogleAuthError, has_calendar, gmail_level

__all__ = ["GoogleAccounts", "GoogleAuthError", "has_calendar", "gmail_level"]
