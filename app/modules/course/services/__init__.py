"""
Course Management — service layer.

Holds module-specific services that wrap external integrations or
encapsulate cross-cutting policy. Distinct from the agents in
app/modules/course/agents/ which carry autonomous decision logic.
"""

from app.modules.course.services.pay_mock import PayMock, pay_mock

__all__ = ["PayMock", "pay_mock"]
