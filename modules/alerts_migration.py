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

"""
Alerts Migration Module for Mycelian

This module handles migrating alerts from external JSON files (like alerts.json from other tools)
into the Mycelian alert system using the standardized AlertObj format and AlertStateManager.

Author: AI Assistant
Created for: Mycelian - Twitch Integration and Alert System
"""

import logging
import time
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from .alerts_parser import AlertsParser, AlertData
from .alertutils import AlertObj, alert_state_manager, initialize_alert_state
from .legacy_migration import normalize_path
from . import database_manager

logger = logging.getLogger(__name__)


class AlertMigrationResult:
    """Results of an alert migration operation"""
    
    def __init__(self):
        self.total_alerts = 0
        self.successful_migrations = 0
        self.failed_migrations = 0
        self.skipped_alerts = 0
        self.errors = []
        self.migrated_alerts = []
        self.alert_type_counts = {}
    
    def add_success(self, alert_type: str, alert_id: str, alert_name: str = ""):
        """Record a successful migration"""
        self.successful_migrations += 1
        self.migrated_alerts.append({
            'type': alert_type,
            'id': alert_id,
            'name': alert_name
        })
        
        if alert_type not in self.alert_type_counts:
            self.alert_type_counts[alert_type] = 0
        self.alert_type_counts[alert_type] += 1
    
    def add_error(self, alert_type: str, alert_id: str, error: str):
        """Record a migration error"""
        self.failed_migrations += 1
        self.errors.append({
            'type': alert_type,
            'id': alert_id,
            'error': error
        })
    
    def add_skip(self, alert_type: str, alert_id: str, reason: str):
        """Record a skipped alert"""
        self.skipped_alerts += 1
        self.errors.append({
            'type': alert_type,
            'id': alert_id,
            'error': f"Skipped: {reason}"
        })
    
    def get_summary(self) -> str:
        """Get a summary of the migration results"""
        summary = f"Migration Complete:\n"
        summary += f"Total alerts processed: {self.total_alerts}\n"
        summary += f"Successfully migrated: {self.successful_migrations}\n"
        summary += f"Failed: {self.failed_migrations}\n"
        summary += f"Skipped: {self.skipped_alerts}\n\n"
        
        if self.alert_type_counts:
            summary += "Alerts migrated by type:\n"
            for alert_type, count in self.alert_type_counts.items():
                summary += f"  {alert_type}: {count}\n"
        
        if self.errors:
            summary += f"\nFirst few errors/skips:\n"
            for error in self.errors[:5]:  # Show first 5 errors
                summary += f"  {error['type']} - {error['id']}: {error['error']}\n"
            
            if len(self.errors) > 5:
                summary += f"  ... and {len(self.errors) - 5} more\n"
        
        return summary


class AlertsMigrator:
    """
    Migrates alerts from external JSON files to Mycelian's alert system
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Mapping from parser alert types to Mycelian alert types
        self.alert_type_mapping = {
            'bits': 'bits',
            'donation': 'donations', 
            'follow': 'follows',
            'points': 'points',
            'raid': 'raids',
            'sub': 'subs',
            'giftsub': 'giftsubs',
            'resub': 'subs'  # resubs are stored as subs in Mycelian
        }
    
    def migrate_from_file(self, file_path: str, overwrite_existing: bool = False) -> AlertMigrationResult:
        """
        Migrate alerts from a JSON file

        Args:
            file_path (str): Path to the alerts JSON file
            overwrite_existing (bool): Whether to overwrite existing alerts

        Returns:
            AlertMigrationResult: Results of the migration
        """
        result = AlertMigrationResult()

        try:
            # Initialize alert state if needed
            initialize_alert_state()

            # Parse the alerts file
            parser = AlertsParser(file_path)
            if not parser.load_file():
                result.add_error("file", file_path, "Failed to load alerts file")
                return result

            # Get all parsed alerts
            parsed_alerts = parser.parse_alerts()
            result.total_alerts = len(parsed_alerts)

            self.logger.info(f"Starting migration of {result.total_alerts} alerts from {file_path}")

            # Track alerts saved for each type to update collections at the end
            alerts_saved_by_type = {}

            # Process each alert
            for alert_data in parsed_alerts:
                try:
                    success, alert_type, alert_id = self._migrate_single_alert(alert_data, overwrite_existing, result, batch_mode=True)
                    if success and alert_type:
                        if alert_type not in alerts_saved_by_type:
                            alerts_saved_by_type[alert_type] = []
                        alerts_saved_by_type[alert_type].append(alert_id)
                except Exception as e:
                    self.logger.error(f"Error migrating alert {alert_data.alert_id}: {str(e)}", exc_info=True)
                    result.add_error(alert_data.alerttype, alert_data.alert_id, str(e))

            # Update collections for all alert types that had alerts saved
            for alert_type in alerts_saved_by_type:
                try:
                    alert_state_manager.update_alert_collections(alert_type)
                except Exception as e:
                    self.logger.error(f"Error updating collections for {alert_type}: {e}", exc_info=True)

            self.logger.info(f"Migration completed: {result.successful_migrations} successful, {result.failed_migrations} failed, {result.skipped_alerts} skipped")

        except Exception as e:
            self.logger.error(f"Critical error during migration: {str(e)}", exc_info=True)
            result.add_error("migration", "critical", f"Critical migration error: {str(e)}")

        return result
    
    def _migrate_single_alert(self, alert_data: AlertData, overwrite_existing: bool, result: AlertMigrationResult, batch_mode: bool = False):
        """
        Migrate a single alert

        Args:
            alert_data (AlertData): The parsed alert data
            overwrite_existing (bool): Whether to overwrite existing alerts
            result (AlertMigrationResult): Result tracker
            batch_mode (bool): Whether to use batch saving (skip collection updates)

        Returns:
            tuple: (success, alert_type, alert_id) for batch mode
        """
        try:
            # Map the alert type
            mycelian_alert_type = self.alert_type_mapping.get(alert_data.alerttype)
            if not mycelian_alert_type:
                result.add_skip(alert_data.alerttype, alert_data.alert_id, f"Unsupported alert type: {alert_data.alerttype}")
                return False, None, None

            # Generate proper alert ID for Mycelian system
            mycelian_alert_id = self._generate_mycelian_alert_id(alert_data)
            if not mycelian_alert_id:
                result.add_skip(alert_data.alerttype, alert_data.alert_id, "Could not generate valid Mycelian alert ID")
                return False, None, None

            # Check if alert already exists
            if not overwrite_existing:
                existing_alert = alert_state_manager.get_alert_by_id(mycelian_alert_type, mycelian_alert_id)
                if existing_alert:
                    result.add_skip(alert_data.alerttype, alert_data.alert_id, f"Alert {mycelian_alert_id} already exists")
                    return False, None, None

            # Convert to AlertObj format
            alert_obj_data = self._convert_to_alert_obj(alert_data, mycelian_alert_type, mycelian_alert_id)

            # Save using AlertStateManager (with batch mode option)
            success = alert_state_manager.save_alert(mycelian_alert_type, mycelian_alert_id, alert_obj_data, update_collection=not batch_mode)

            if success:
                display_name = alert_data.alertname or mycelian_alert_id
                result.add_success(mycelian_alert_type, mycelian_alert_id, display_name)
                self.logger.debug(f"Successfully migrated {alert_data.alerttype} alert {alert_data.alert_id} -> {mycelian_alert_type} {mycelian_alert_id}")
                return True, mycelian_alert_type, mycelian_alert_id
            else:
                result.add_error(alert_data.alerttype, alert_data.alert_id, "Failed to save alert to AlertStateManager")
                return False, None, None

        except Exception as e:
            self.logger.error(f"Error in _migrate_single_alert: {str(e)}", exc_info=True)
            result.add_error(alert_data.alerttype, alert_data.alert_id, str(e))
            return False, None, None
    
    def _generate_mycelian_alert_id(self, alert_data: AlertData) -> Optional[str]:
        """
        Generate a proper Mycelian alert ID from the parsed alert data
        
        Args:
            alert_data (AlertData): The parsed alert data
            
        Returns:
            str: The Mycelian-compatible alert ID, or None if invalid
        """
        try:
            # Extract the ID from the original alert_id
            original_id = alert_data.alert_id
            original_alert_type = alert_data.alerttype
            
            # Map to Mycelian alert type
            mycelian_alert_type = self.alert_type_mapping.get(original_alert_type)
            if not mycelian_alert_type:
                self.logger.warning(f"No mapping for alert type: {original_alert_type}")
                return None
            
            # Handle different alert types based on the original type
            if original_alert_type in ['sub', 'resub']:
                # Both sub and resub map to 'subs' in Mycelian
                if original_id.startswith(original_alert_type):
                    numeric_part = original_id[len(original_alert_type):]
                    return f"subs{numeric_part}"
                elif any(char.isdigit() for char in original_id):
                    # Extract numeric part if ID doesn't start with type
                    import re
                    numbers = re.findall(r'\d+(?:-\d+)?', original_id)
                    if numbers:
                        return f"subs{numbers[0]}"
                        
            elif original_alert_type == 'giftsub':
                # giftsub maps to 'giftsubs' in Mycelian
                if original_id.startswith('giftsub'):
                    numeric_part = original_id[7:]  # Remove 'giftsub'
                    return f"giftsubs{numeric_part}"
                elif any(char.isdigit() for char in original_id):
                    # Extract numeric part if ID doesn't start with type
                    import re
                    numbers = re.findall(r'\d+(?:-\d+)?', original_id)
                    if numbers:
                        return f"giftsubs{numbers[0]}"
                        
            elif original_alert_type in ['bits', 'donations', 'raids']:
                # These types map directly (bits->bits, donation->donations, raid->raids)
                if original_id.startswith(original_alert_type):
                    numeric_part = original_id[len(original_alert_type):]
                    return f"{mycelian_alert_type}{numeric_part}"
                elif any(char.isdigit() for char in original_id):
                    # Extract numeric part if ID doesn't start with type
                    import re
                    numbers = re.findall(r'\d+(?:-\d+)?', original_id)
                    if numbers:
                        return f"{mycelian_alert_type}{numbers[0]}"
                        
            elif original_alert_type == 'follow':
                # follow maps to 'follows' in Mycelian
                return "follows1"
                
            elif original_alert_type == 'points':
                # Point alerts use the Twitch reward ID if available
                if alert_data.twitchrewardid:
                    return alert_data.twitchrewardid
                else:
                    # If no reward ID, skip this alert
                    return None
            
            # Default fallback - try to extract numbers from the original ID
            if any(char.isdigit() for char in original_id):
                import re
                numbers = re.findall(r'\d+(?:-\d+)?', original_id)
                if numbers:
                    return f"{mycelian_alert_type}{numbers[0]}"
            
            # Final fallback
            return f"{mycelian_alert_type}1"
            
        except Exception as e:
            self.logger.error(f"Error generating Mycelian alert ID: {str(e)}")
            return None
    
    def _convert_to_alert_obj(self, alert_data: AlertData, mycelian_alert_type: str, mycelian_alert_id: str) -> dict:
        """
        Convert AlertData to AlertObj-compatible dictionary
        
        Args:
            alert_data (AlertData): The parsed alert data
            mycelian_alert_type (str): The Mycelian alert type
            mycelian_alert_id (str): The Mycelian alert ID
            
        Returns:
            dict: AlertObj-compatible data
        """
        # Base alert data mapping
        alert_obj_data = {
            # Basic alert properties
            'alert_type': mycelian_alert_type,
            'alert_id': mycelian_alert_id,
            'alert_name': alert_data.alertname or self._generate_display_name(alert_data, mycelian_alert_id),
            'duration': float(alert_data.duration) if alert_data.duration else 3.0,
            'volume': int(alert_data.volume) if alert_data.volume else 100,
            'stackable': False,  # Default to False, not specified in source
            'timestamp': time.time(),
            'deleted': False,
            'played': False,
            'skip_alert': False,
            
            # Audio settings - map the variable names
            'single_audio_dir': normalize_path(alert_data.audiodirectory) if alert_data.audiodirectory else "",
            'single_audio_name': alert_data.audioname or "",
            'fade_in': 0,  # Not in source data
            'fade_out': 0,  # Not in source data
            'audio_only': False,  # Default to False for migrated alerts

            # Randomization settings - map the variable names
            'randomized': self._convert_bool(alert_data.randomized),
            'randomized_dir': normalize_path(alert_data.randomizeddirectory) if alert_data.randomizeddirectory else "",
            'randomized_chance': int(alert_data.randomizedchance) if alert_data.randomizedchance else 0,
            'randomized_extra': False,  # Determine from extra fields
            'randomized_extra_dir': normalize_path(alert_data.randomizedextradirectory) if alert_data.randomizedextradirectory else "",
            'randomized_extra_chance': int(alert_data.randomizedextrachance) if alert_data.randomizedextrachance else 0,

            # Visual settings - map from parsed GIF data
            'gif_dir': normalize_path(alert_data.gifdirectory) if alert_data.gifdirectory else "",
            'gif_name': alert_data.gifname or "",
            
            # Initialize Twitch data fields
            'username': "",
            'anonymous': False,
            'message': None,
            'emotes': None,
            'title': "",
            
            # Initialize type-specific fields
            'tier': 0,
            'gift_qty': 0,
            'resub_month': 0,
            'months_prepaid': 0,
            'amt_cheered': 0,
            'twitch_reward_id': alert_data.twitchrewardid or "",
            'point_cost': 0,
            'raider_count': 0,
            'donation_amount': 0.0,
            'currency': "USD",
            'hype_train_level': 0,
            'hype_train_in_progress': False
        }
        
        # Set randomized_extra based on whether we have extra randomization
        if alert_data.randomizedextrachance and int(alert_data.randomizedextrachance) > 0:
            alert_obj_data['randomized_extra'] = True

        # If GIF directory is not set but audio directory is, use the same directory for GIF
        # Check if the normalized gif_dir is empty (meaning original was None/"None"/empty)
        if not alert_obj_data.get('gif_dir') and alert_obj_data.get('single_audio_dir'):
            alert_obj_data['gif_dir'] = alert_obj_data['single_audio_dir']

        # Extract type-specific data from the alert ID
        self._extract_type_specific_data(alert_data, mycelian_alert_id, alert_obj_data)

        return alert_obj_data
    
    def _convert_bool(self, value) -> bool:
        """Convert string boolean values to actual booleans"""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ['true', '1', 'yes', 'on']
        return bool(value)
    
    def _generate_display_name(self, alert_data: AlertData, alert_id: str) -> str:
        """Generate a display name for the alert"""
        if alert_data.alertname:
            return alert_data.alertname

        # Generate based on alert type and ID - match the format from alertutils.get_display_name
        alert_type = alert_data.alerttype

        if alert_type == 'bits':
            if '-' in alert_id:
                # Extract numeric part: bits1-99 -> 1-99
                numeric_part = alert_id.replace('bits', '')
                return f"{numeric_part} Bits"
            else:
                amount = alert_id.replace('bits', '')
                return f"{amount} Bits"
        elif alert_type == 'subs':
            if '-' in alert_id:
                # Extract numeric part: subs1-12 -> 1-12
                numeric_part = alert_id.replace('subs', '')
                return f"{numeric_part} Months"
            else:
                month = alert_id.replace('subs', '')
                return f"{month} Month" if month == '1' else f"{month} Months"
        elif alert_type == 'giftsubs':
            numeric_part = alert_id.replace('giftsubs', '')
            return f"{numeric_part} Gift Sub" if numeric_part == '1' else f"{numeric_part} Gift Subs"
        elif alert_type == 'donations':
            numeric_part = alert_id.replace('donations', '')
            return f"${numeric_part} Donation"
        elif alert_type == 'raids':
            numeric_part = alert_id.replace('raids', '')
            return f"{numeric_part} Raider" if numeric_part == '1' else f"{numeric_part} Raiders"
        elif alert_type == 'follow':
            return "Follow"
        elif alert_type == 'points':
            return f"Point Reward {alert_id}"

        return f"{alert_type.title()} {alert_id}"
    
    def _extract_type_specific_data(self, alert_data: AlertData, alert_id: str, alert_obj_data: dict):
        """Extract type-specific data from the alert ID and set appropriate fields"""
        try:
            # Use the original alert type to determine the mapped Mycelian type
            original_alert_type = alert_data.alerttype
            
            # Map to Mycelian alert type for proper processing
            mycelian_alert_type = self.alert_type_mapping.get(original_alert_type)
            if not mycelian_alert_type:
                self.logger.warning(f"Unknown alert type for extraction: {original_alert_type}")
                return
            
            if mycelian_alert_type == 'bits':
                # Extract bit amount from ID
                amount_str = alert_id.replace('bits', '')
                if '-' in amount_str:
                    # Range format - use the minimum value
                    min_amount = int(amount_str.split('-')[0])
                    alert_obj_data['amt_cheered'] = min_amount
                else:
                    alert_obj_data['amt_cheered'] = int(amount_str)
                    
            elif mycelian_alert_type == 'subs':
                # Handle both sub and resub types (both map to 'subs' in Mycelian)
                # Extract month from ID
                month_str = alert_id.replace('subs', '')
                if '-' in month_str:
                    # Range format - use the minimum value
                    min_month = int(month_str.split('-')[0])
                    alert_obj_data['resub_month'] = min_month
                else:
                    alert_obj_data['resub_month'] = int(month_str)
                
                # Set default tier to 1 (Tier 1 subscription) since most external tools don't specify tiers
                alert_obj_data['tier'] = 1
                    
            elif mycelian_alert_type == 'giftsubs':
                # Extract gift quantity from ID
                qty_str = alert_id.replace('giftsubs', '')
                if '-' in qty_str:
                    # Range format - use the minimum value
                    min_qty = int(qty_str.split('-')[0])
                    alert_obj_data['gift_qty'] = min_qty
                else:
                    alert_obj_data['gift_qty'] = int(qty_str)
                    
            elif mycelian_alert_type == 'donations':
                # Extract donation amount from ID
                amount_str = alert_id.replace('donations', '')
                if '-' in amount_str:
                    # Range format - use the minimum value
                    min_amount = float(amount_str.split('-')[0])
                    alert_obj_data['donation_amount'] = min_amount
                else:
                    alert_obj_data['donation_amount'] = float(amount_str)
                    
            elif mycelian_alert_type == 'raids':
                # Extract raider count from ID
                count_str = alert_id.replace('raids', '')
                if '-' in count_str:
                    # Range format - use the minimum value
                    min_count = int(count_str.split('-')[0])
                    alert_obj_data['raider_count'] = min_count
                else:
                    alert_obj_data['raider_count'] = int(count_str)
                    
            elif mycelian_alert_type == 'points':
                # For points, the alert_id is the Twitch reward ID
                alert_obj_data['twitch_reward_id'] = alert_id
                # Point cost would need to be retrieved from Twitch API
                
        except (ValueError, IndexError) as e:
            self.logger.warning(f"Could not extract type-specific data from {alert_id}: {str(e)}")
            # Continue with default values


def migrate_alerts_from_file(file_path: str, overwrite_existing: bool = False) -> AlertMigrationResult:
    """
    Convenience function to migrate alerts from a file
    
    Args:
        file_path (str): Path to the alerts JSON file
        overwrite_existing (bool): Whether to overwrite existing alerts
        
    Returns:
        AlertMigrationResult: Results of the migration
    """
    migrator = AlertsMigrator()
    return migrator.migrate_from_file(file_path, overwrite_existing)


def preview_migration(file_path: str) -> Tuple[int, dict]:
    """
    Preview what would be migrated from a file without actually migrating
    
    Args:
        file_path (str): Path to the alerts JSON file
        
    Returns:
        Tuple[int, dict]: Total count and breakdown by alert type
    """
    try:
        parser = AlertsParser(file_path)
        if not parser.load_file():
            return 0, {}
        
        alerts = parser.parse_alerts()
        
        # Count by alert type
        type_counts = {}
        for alert in alerts:
            alert_type = alert.alerttype
            if alert_type not in type_counts:
                type_counts[alert_type] = 0
            type_counts[alert_type] += 1
        
        return len(alerts), type_counts
        
    except Exception as e:
        logger.error(f"Error previewing migration: {str(e)}", exc_info=True)
        return 0, {} 