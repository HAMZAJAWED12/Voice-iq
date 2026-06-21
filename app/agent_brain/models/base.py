"""Shared Pydantic base for Agent Brain models.

The Agent Brain API is the Java-facing contract, so its JSON uses
camelCase (sessionId, agentType, ...). Internally we keep snake_case
Python fields; the alias generator bridges the two. `populate_by_name`
lets us construct models with snake_case kwargs in Python while still
parsing/serialising camelCase JSON at the boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
