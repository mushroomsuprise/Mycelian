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
Alert Database Migration Module for Mycelian

This module provides functionality to import alerts from external JSON files,
such as those exported from other streaming tools like Streamlabs OBS, OBS WebSocket alerts, etc.

Supports common alert formats and provides preview and migration capabilities.
"""

import json
import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from pathlib import Path

from .alertutils import AlertObj
from .database_manager import database_manager

logger = logging.getLogger(__name__)

@dataclass
class MigrationResult:
    """Result of an alert migration operation"""
    total_alerts: int = 0
    successful_migrations: int = 0
    failed_migrations: int = 0
    skipped_alerts: int = 0
    alert_type_counts: Dict[str, int] = None
    errors: List[Dict[str, str]] = None
    
    def __post_init__(self):
        if self.alert_type_counts is None:
            self.alert_type_counts = {}
        if self.errors is None:
            self.errors = []

def preview_migration(file_path: str) -> tuple[int, Dict[str, int]]:
    """
    Preview what would be migrated from a file without actually migrating.
    
    Args:
        file_path: Path to the JSON file to preview
        
    Returns:
        tuple: (total_count, type_counts) where type_counts is a dict of alert_type: count
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        alerts = _parse_alert_data(data)
        total_count = len(alerts)
        
        # Count by type
        type_counts = {}
        for alert in alerts:
            alert_type = alert.get('type', 'unknown')
            type_counts[alert_type] = type_counts.get(alert_type, 0) + 1
        
        logger.info(f"Preview: Found {total_count} alerts in {file_path}")
        return total_count, type_counts
        
    except Exception as e:
        logger.error(f"Error previewing migration from {file_path}: {str(e)}")
        return 0, {}

def migrate_alerts_from_file(file_path: str, overwrite_existing: bool = False) -> MigrationResult:
    """
    Migrate alerts from an external JSON file.
    
    Args:
        file_path: Path to the JSON file containing alerts
        overwrite_existing: Whether to overwrite existing alerts with the same ID
        
    Returns:
        MigrationResult: Detailed results of the migration
    """
    result = MigrationResult()
    
    try:
        # Load and parse the file
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        alerts = _parse_alert_data(data)
        result.total_alerts = len(alerts)
        
        logger.info(f"Starting migration of {result.total_alerts} alerts from {file_path}")
        
        # Process each alert
        for alert_data in alerts:
            try:
                success = _migrate_single_alert(alert_data, overwrite_existing, result)
                if success:
                    result.successful_migrations += 1
                    alert_type = alert_data.get('type', 'unknown')
                    result.alert_type_counts[alert_type] = result.alert_type_counts.get(alert_type, 0) + 1
                else:
                    result.failed_migrations += 1
                    
            except Exception as e:
                result.failed_migrations += 1
                result.errors.append({
                    'type': alert_data.get('type', 'unknown'),
                    'id': alert_data.get('id', 'unknown'),
                    'error': str(e)
                })
                logger.error(f"Error migrating alert {alert_data.get('id', 'unknown')}: {str(e)}")
        
        logger.info(f"Migration completed: {result.successful_migrations} successful, {result.failed_migrations} failed")
        return result
        
    except Exception as e:
        logger.error(f"Error during migration from {file_path}: {str(e)}")
        result.errors.append({
            'type': 'file',
            'id': file_path,
            'error': str(e)
        })
        return result

def _parse_alert_data(data: Any) -> List[Dict[str, Any]]:
    """
    Parse alert data from various JSON formats.
    
    Supports multiple common formats from different streaming tools.
    """
    alerts = []
    
    try:
        # Handle different file formats
        if isinstance(data, list):
            # Direct list of alerts
            alerts = data
        elif isinstance(data, dict):
            # Check for common structure patterns
            if 'alerts' in data:
                alerts = data['alerts']
            elif 'notifications' in data:
                alerts = data['notifications']
            elif 'events' in data:
                alerts = data['events']
            elif 'sounds' in data:
                # Streamlabs OBS format
                alerts = _parse_streamlabs_format(data['sounds'])
            else:
                # Try to parse as individual alert
                alerts = [data]
        
        # Normalize alert format
        normalized_alerts = []
        for alert in alerts:
            normalized = _normalize_alert_format(alert)
            if normalized:
                normalized_alerts.append(normalized)
        
        return normalized_alerts
        
    except Exception as e:
        logger.error(f"Error parsing alert data: {str(e)}")
        return []

def _parse_streamlabs_format(sounds_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Streamlabs OBS sound format"""
    alerts = []
    
    # Streamlabs format maps event types to sound configurations
    event_type_map = {
        'donation': 'donations',
        'follow': 'follows',
        'subscription': 'subs',
        'bits': 'bits',
        'raid': 'raids',
        'giftsub': 'giftsubs'
    }
    
    for event_type, mycelian_type in event_type_map.items():
        if event_type in sounds_data:
            event_data = sounds_data[event_type]
            
            # Handle both single alerts and arrays
            if isinstance(event_data, list):
                for i, item in enumerate(event_data):
                    alert = _convert_streamlabs_alert(item, mycelian_type, f"{event_type}_{i}")
                    if alert:
                        alerts.append(alert)
            else:
                alert = _convert_streamlabs_alert(event_data, mycelian_type, event_type)
                if alert:
                    alerts.append(alert)
    
    return alerts

def _convert_streamlabs_alert(streamlabs_alert: Dict[str, Any], alert_type: str, alert_id: str) -> Optional[Dict[str, Any]]:
    """Convert a Streamlabs alert to Mycelian format"""
    try:
        # Extract common fields
        alert = {
            'id': alert_id,
            'type': alert_type,
            'enabled': streamlabs_alert.get('enabled', True),
            'sound_file': streamlabs_alert.get('file', ''),
            'volume': streamlabs_alert.get('volume', 50) / 100.0,  # Convert to 0-1 range
            'delay': streamlabs_alert.get('delay', 0),
            'message': streamlabs_alert.get('message', ''),
            'duration': streamlabs_alert.get('duration', 5000),
            'conditions': {}
        }
        
        # Handle amount-based conditions
        if 'amount' in streamlabs_alert:
            amount = streamlabs_alert['amount']
            if isinstance(amount, dict):
                min_amount = amount.get('min', 0)
                max_amount = amount.get('max', 999999)
                if min_amount == max_amount:
                    alert['id'] = f"{alert_type}{min_amount}"
                else:
                    alert['id'] = f"{alert_type}{min_amount}-{max_amount}"
                    alert['conditions']['amount_range'] = [min_amount, max_amount]
            elif isinstance(amount, (int, float)):
                alert['id'] = f"{alert_type}{amount}"
                alert['conditions']['amount'] = amount
        
        return alert
        
    except Exception as e:
        logger.error(f"Error converting Streamlabs alert: {str(e)}")
        return None

def _normalize_alert_format(alert_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize an alert to Mycelian's expected format"""
    try:
        # Required fields
        if 'type' not in alert_data and 'event_type' not in alert_data:
            return None
        
        alert_type = alert_data.get('type') or alert_data.get('event_type')
        alert_id = alert_data.get('id') or alert_data.get('name') or f"{alert_type}_import"
        
        # Normalize type names
        type_map = {
            'donation': 'donations',
            'follow': 'follows',
            'subscription': 'subs',
            'subscriber': 'subs',
            'bit': 'bits',
            'cheer': 'bits',
            'raid': 'raids',
            'host': 'raids',  # Treat hosts as raids
            'giftsub': 'giftsubs',
            'gift_subscription': 'giftsubs'
        }
        
        normalized_type = type_map.get(alert_type.lower(), alert_type.lower())
        
        # Build normalized alert
        normalized = {
            'id': alert_id,
            'type': normalized_type,
            'enabled': alert_data.get('enabled', True),
            'sound_file': alert_data.get('sound_file') or alert_data.get('audio_file') or alert_data.get('file', ''),
            'volume': float(alert_data.get('volume', 0.5)),
            'delay': int(alert_data.get('delay', 0)),
            'message': alert_data.get('message') or alert_data.get('text', ''),
            'duration': int(alert_data.get('duration', 5000)),
            'conditions': alert_data.get('conditions', {})
        }
        
        # Handle amount/value conditions
        if 'amount' in alert_data:
            normalized['conditions']['amount'] = alert_data['amount']
        if 'min_amount' in alert_data and 'max_amount' in alert_data:
            normalized['conditions']['amount_range'] = [alert_data['min_amount'], alert_data['max_amount']]
        
        return normalized
        
    except Exception as e:
        logger.error(f"Error normalizing alert format: {str(e)}")
        return None

def _migrate_single_alert(alert_data: Dict[str, Any], overwrite_existing: bool, result: MigrationResult) -> bool:
    """Migrate a single alert to the database"""
    try:
        alert_type = alert_data['type']
        alert_id = alert_data['id']
        
        # Determine the database path
        path_map = {
            'bits': 'Alerts/BitAlerts',
            'subs': 'Alerts/SubAlerts', 
            'donations': 'Alerts/DonationAlerts',
            'follows': 'Alerts/FollowAlerts',
            'raids': 'Alerts/RaidAlerts',
            'giftsubs': 'Alerts/GiftsubAlerts',
            'points': 'Alerts/PointAlerts'
        }
        
        # Handle range alerts (if amount_range is specified)
        if 'amount_range' in alert_data.get('conditions', {}):
            range_path_map = {
                'bits': 'Alerts/BitRangeAlerts',
                'donations': 'Alerts/DonationRangeAlerts',
                'raids': 'Alerts/RaidRangeAlerts',
                'giftsubs': 'Alerts/GiftsubRangeAlerts'
            }
            db_path = range_path_map.get(alert_type)
            if not db_path:
                logger.warning(f"Range alerts not supported for type: {alert_type}")
                return False
        else:
            db_path = path_map.get(alert_type)
            if not db_path:
                logger.warning(f"Unknown alert type: {alert_type}")
                return False
        
        # Check if alert already exists
        full_path = f"{db_path}/{alert_id}"
        existing_alert = database_manager.get_data(full_path)
        
        if existing_alert and not overwrite_existing:
            result.skipped_alerts += 1
            logger.info(f"Skipped existing alert: {alert_id}")
            return False
        
        # Create AlertObj
        alert_obj = AlertObj(
            alert_id=alert_id,
            enabled=alert_data.get('enabled', True),
            sound_file=alert_data.get('sound_file', ''),
            volume=alert_data.get('volume', 0.5),
            delay=alert_data.get('delay', 0),
            message=alert_data.get('message', ''),
            duration=alert_data.get('duration', 5000)
        )
        
        # Save to database
        alert_dict = {
            'alert_id': alert_obj.alert_id,
            'enabled': alert_obj.enabled,
            'sound_file': alert_obj.sound_file,
            'volume': alert_obj.volume,
            'delay': alert_obj.delay,
            'message': alert_obj.message,
            'duration': alert_obj.duration
        }
        
        success = database_manager.set_data(full_path, alert_dict)
        
        if success:
            logger.info(f"Successfully migrated alert: {alert_id} to {db_path}")
        else:
            logger.error(f"Failed to save alert: {alert_id}")
        
        return success
        
    except Exception as e:
        logger.error(f"Error migrating single alert: {str(e)}")
        return False 