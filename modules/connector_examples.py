#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import logging
import uuid

from . import connector_actions, connector_manager, connector_triggers
from .connector_core import (
    ActionType,
    ComparisonOperator,
    Connector,
    TriggerCondition,
    TriggerType,
)

logger = logging.getLogger(__name__)


def create_example_connectors():
    """Create example connectors to demonstrate the system"""
    try:
        manager = connector_manager.get_manager()

        # Only create examples if no connectors exist
        existing_connectors = manager.get_all_connectors()
        if existing_connectors:
            logger.info("Connectors already exist, skipping example creation")
            return

        examples = [
            create_high_bits_counter_example(),
            create_new_follower_wheel_example(),
            create_chat_command_example(),
            create_donation_log_example(),
        ]

        created_count = 0
        for example in examples:
            if example and manager.add_connector(example):
                created_count += 1
                logger.info(f"Created example connector: {example.name}")
            else:
                logger.warning(
                    f"Failed to create example connector: {example.name if example else 'Unknown'}"
                )

        logger.info(f"Created {created_count} example connectors")

    except Exception as e:
        logger.error(f"Error creating example connectors: {e}", exc_info=True)


def create_high_bits_counter_example() -> Connector:
    """Create an example connector that increments counter for high bit amounts"""
    try:
        # Create trigger for bits >= 100
        trigger = connector_triggers.create_trigger(
            trigger_type=TriggerType.TWITCH_BITS,
            trigger_id=str(uuid.uuid4()),
            name="High Bits Trigger",
            description="Triggers when someone cheers 100+ bits",
            conditions=[
                TriggerCondition(
                    field="amount",
                    operator=ComparisonOperator.GREATER_THAN_OR_EQUAL,
                    value=100,
                )
            ],
        )

        # Create action to increment counter
        action = connector_actions.create_action(
            action_type=ActionType.TEMPLATE_CONTROL,
            action_id=str(uuid.uuid4()),
            name="Increment Counter",
            description="Increment the counter template",
            template_name="counter",
            control_action="counter_increment",
            control_data={},
        )

        # Create connector
        connector = Connector(
            connector_id=str(uuid.uuid4()),
            name="High Bits Counter",
            description="Increments counter when someone cheers 100+ bits",
            trigger=trigger,
            actions=[action],
            enabled=False,  # Start disabled so users can review first
        )

        return connector

    except Exception as e:
        logger.error(f"Error creating high bits counter example: {e}", exc_info=True)
        return None


def create_new_follower_wheel_example() -> Connector:
    """Create an example connector that spins roulette for new followers"""
    try:
        # Create trigger for follows
        trigger = connector_triggers.create_trigger(
            trigger_type=TriggerType.TWITCH_FOLLOW,
            trigger_id=str(uuid.uuid4()),
            name="New Follower Trigger",
            description="Triggers when someone follows the channel",
        )

        # Create action to spin roulette
        action = connector_actions.create_action(
            action_type=ActionType.TEMPLATE_CONTROL,
            action_id=str(uuid.uuid4()),
            name="Spin Roulette",
            description="Spin the roulette wheel for new followers",
            template_name="roulette",
            control_action="spin",
            control_data={},
        )

        # Create connector
        connector = Connector(
            connector_id=str(uuid.uuid4()),
            name="New Follower Celebration",
            description="Spins the roulette wheel when someone follows",
            trigger=trigger,
            actions=[action],
            enabled=False,
        )

        return connector

    except Exception as e:
        logger.error(f"Error creating new follower wheel example: {e}", exc_info=True)
        return None


def create_chat_command_example() -> Connector:
    """Create an example connector that responds to a chat command"""
    try:
        # Create trigger for !hello command (using chat message with conditions)
        trigger = connector_triggers.create_trigger(
            trigger_type=TriggerType.TWITCH_CHAT_MESSAGE,
            trigger_id=str(uuid.uuid4()),
            name="Hello Command Trigger",
            description="Triggers when someone types !hello",
            conditions=[
                TriggerCondition(
                    field="message",
                    operator=ComparisonOperator.STARTS_WITH,
                    value="!hello",
                )
            ],
        )

        # Create action to send chat message
        action = connector_actions.create_action(
            action_type=ActionType.SEND_CHAT_MESSAGE,
            action_id=str(uuid.uuid4()),
            name="Hello Response",
            description="Respond with a greeting",
            message="Hello {{username}}! Welcome to the stream! 👋",
        )

        # Create connector
        connector = Connector(
            connector_id=str(uuid.uuid4()),
            name="Hello Command Response",
            description="Responds when someone types !hello in chat",
            trigger=trigger,
            actions=[action],
            enabled=False,
        )

        return connector

    except Exception as e:
        logger.error(f"Error creating chat command example: {e}", exc_info=True)
        return None


def create_donation_log_example() -> Connector:
    """Create an example connector that logs donations to a file"""
    try:
        # Create trigger for donations >= $5
        trigger = connector_triggers.create_trigger(
            trigger_type=TriggerType.DONATION,
            trigger_id=str(uuid.uuid4()),
            name="Donation Logger Trigger",
            description="Triggers when someone donates $5 or more",
            conditions=[
                TriggerCondition(
                    field="amount",
                    operator=ComparisonOperator.GREATER_THAN_OR_EQUAL,
                    value=5.0,
                )
            ],
        )

        # Create action to log to file
        action = connector_actions.create_action(
            action_type=ActionType.WRITE_FILE,
            action_id=str(uuid.uuid4()),
            name="Log Donation",
            description="Log donation details to file",
            file_path="logs/donations.log",
            content="[{{timestamp}}] {{username}} donated ${{amount}} {{currency}} - {{message}}\n",
            append=True,
        )

        # Create connector
        connector = Connector(
            connector_id=str(uuid.uuid4()),
            name="Donation Logger",
            description="Logs donations of $5+ to a file",
            trigger=trigger,
            actions=[action],
            enabled=False,
        )

        return connector

    except Exception as e:
        logger.error(f"Error creating donation log example: {e}", exc_info=True)
        return None


def create_raid_celebration_example() -> Connector:
    """Create an example connector for raid celebrations"""
    try:
        # Create trigger for raids with 10+ viewers
        trigger = connector_triggers.create_trigger(
            trigger_type=TriggerType.TWITCH_RAID,
            trigger_id=str(uuid.uuid4()),
            name="Raid Celebration Trigger",
            description="Triggers when raided with 10+ viewers",
            conditions=[
                TriggerCondition(
                    field="viewer_count",
                    operator=ComparisonOperator.GREATER_THAN_OR_EQUAL,
                    value=10,
                )
            ],
        )

        # Create multiple actions for celebration
        actions = [
            # Spin the wheel
            connector_actions.create_action(
                action_type=ActionType.TEMPLATE_CONTROL,
                action_id=str(uuid.uuid4()),
                name="Celebration Spin",
                description="Spin roulette for raid celebration",
                template_name="roulette",
                control_action="spin",
                control_data={},
            ),
            # Increment counter
            connector_actions.create_action(
                action_type=ActionType.TEMPLATE_CONTROL,
                action_id=str(uuid.uuid4()),
                name="Raid Counter",
                description="Increment raid counter",
                template_name="counter",
                control_action="counter_increment",
                control_data={},
            ),
            # Send chat message
            connector_actions.create_action(
                action_type=ActionType.SEND_CHAT_MESSAGE,
                action_id=str(uuid.uuid4()),
                name="Raid Thanks",
                description="Thank the raider in chat",
                message="🎉 RAID! Thank you {{username}} for bringing {{viewer_count}} viewers! Welcome everyone! 🎉",
            ),
        ]

        # Create connector
        connector = Connector(
            connector_id=str(uuid.uuid4()),
            name="Raid Celebration Multi-Action",
            description="Multiple celebration actions for raids with 10+ viewers",
            trigger=trigger,
            actions=actions,
            enabled=False,
        )

        return connector

    except Exception as e:
        logger.error(f"Error creating raid celebration example: {e}", exc_info=True)
        return None
