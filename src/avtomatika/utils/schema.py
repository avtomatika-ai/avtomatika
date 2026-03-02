# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from typing import Any, cast

try:
    from pydantic import BaseModel

    _PYDANTIC_INSTALLED = True
except ImportError:
    _PYDANTIC_INSTALLED = False


def orchestrator_extract_json_schema(schema_type: Any) -> dict[str, Any] | None:
    """Orchestrator-side Pydantic schema extractor."""
    if _PYDANTIC_INSTALLED and isinstance(schema_type, type) and issubclass(schema_type, BaseModel):
        return cast(dict[str, Any], cast(Any, schema_type).model_json_schema())
    return None
