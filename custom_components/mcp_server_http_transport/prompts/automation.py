"""Automation-related prompts."""

import json
from typing import Any

from homeassistant.core import HomeAssistant

from . import register_prompt


@register_prompt(
    name="automation_review",
    description=(
        "Review an automation's configuration for issues, " "improvements, and best practices"
    ),
    arguments=[
        {
            "name": "automation_id",
            "description": "The automation ID to review",
            "required": True,
        }
    ],
)
async def automation_review(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate an automation review prompt."""
    from ..config_manager import read_list_entry

    automation_id = arguments.get("automation_id", "")
    try:
        config = await read_list_entry(hass, "automations.yaml", automation_id)
        config_text = json.dumps(config, indent=2)
    except Exception:
        config_text = f"Automation with id '{automation_id}' not found in automations.yaml"

    return {
        "description": f"Review automation {automation_id}",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Please review the following Home Assistant automation "
                        f"configuration and provide feedback on:\n"
                        f"1. Trigger correctness and completeness\n"
                        f"2. Condition logic and edge cases\n"
                        f"3. Action reliability and error handling\n"
                        f"4. Mode setting appropriateness "
                        f"(single, restart, queued, parallel)\n"
                        f"5. Potential improvements or simplifications\n"
                        f"6. Security considerations\n\n"
                        f"Automation configuration:\n"
                        f"```json\n{config_text}\n```"
                    ),
                },
            }
        ],
    }
