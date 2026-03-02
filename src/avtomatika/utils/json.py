# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from typing import Any

from aiohttp import web
from rxon.utils import json_dumps


def json_response(data: Any, **kwargs: Any) -> web.Response:
    """Standardized JSON response for Avtomatika API."""
    return web.json_response(data, dumps=json_dumps, **kwargs)
