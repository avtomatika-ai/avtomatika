# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from src.avtomatika.blueprint import StateMachineBlueprint

error_flow_bp = StateMachineBlueprint(name="error_flow", api_endpoint="/jobs/error_flow", api_version="v1")


@error_flow_bp.handler_for("start", is_start=True)
async def start(context, actions):
    actions.dispatch_task(
        task_type="error_task",
        params={"error_type": context.initial_data.get("error_type", "SUCCESS")},
        transitions={"success": "finished", "failure": "failed"},
    )


@error_flow_bp.handler_for("finished", is_end=True)
async def finished(context, actions):
    pass


@error_flow_bp.handler_for("failed", is_end=True)
async def failed(context, actions):
    pass
