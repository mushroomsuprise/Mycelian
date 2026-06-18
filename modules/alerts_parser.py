#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

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

import json
import logging
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AlertData:
    """Data class to hold parsed alert information"""
    alerttype: str
    audioname: str
    audiodirectory: str
    duration: Union[str, int]
    volume: Union[str, int]
    randomized: str
    randomizedchance: Optional[str]
    randomizeddirectory: Optional[str]
    randomizedextrachance: Optional[str]
    randomizedextradirectory: Optional[str]
    twitchrewardid: Optional[str]
    gifname: Optional[str]
    gifdirectory: Optional[str]
    
    # Additional useful fields for context
    alert_id: str = ""
    alert_category: str = ""
    alertname: Optional[str] = None
    disabled: bool = False


class AlertsParser:
    """
    Parser for alerts.json files used in Twitch alert systems.
    
    This class provides methods to parse and extract specific alert configuration
    data from JSON files, making it easy to work with alert settings programmatically.
    """
    
    def __init__(self, file_path: Optional[str] = None):
        """
        Initialize the AlertsParser
        
        Args:
            file_path (str, optional): Path to the alerts.json file. 
                                     If None, defaults to 'alerts.json' in current directory.
        """
        self.file_path = file_path or "alerts.json"
        self.logger = logging.getLogger(__name__)
        self._raw_data: Dict[str, Any] = {}
        self._alerts_data: List[AlertData] = []
    
    def load_file(self, file_path: Optional[str] = None) -> bool:
        """
        Load and parse the alerts JSON file
        
        Args:
            file_path (str, optional): Path to the JSON file. If None, uses instance file_path.
            
        Returns:
            bool: True if successful, False otherwise
        """
        if file_path:
            self.file_path = file_path
            
        try:
            file_path_obj = Path(self.file_path)
            if not file_path_obj.exists():
                self.logger.error(f"Alerts file not found: {self.file_path}")
                return False
                
            with open(self.file_path, 'r', encoding='utf-8') as file:
                self._raw_data = json.load(file)
                
            self.logger.info(f"Successfully loaded alerts file: {self.file_path}")
            return True
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in alerts file: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Error loading alerts file: {e}")
            return False
    
    def parse_alerts(self) -> List[AlertData]:
        """
        Parse all alerts from the loaded JSON data
        
        Returns:
            List[AlertData]: List of parsed alert data objects
        """
        if not self._raw_data:
            self.logger.warning("No data loaded. Call load_file() first.")
            return []
        
        self._alerts_data = []
        
        # Get the ToolData.alerts section
        try:
            alerts_section = self._raw_data.get("ToolData", {}).get("alerts", {})
        except (KeyError, AttributeError):
            self.logger.error("Invalid alerts file structure - missing ToolData.alerts")
            return []
        
        # Parse each alert category
        alert_categories = [
            "bit_alerts",
            "donation_alerts", 
            "follow_alerts",
            "point_alerts",
            "raid_alerts",
            "sub_alerts"
        ]
        
        for category in alert_categories:
            if category in alerts_section:
                self._parse_alert_category(alerts_section[category], category)
        
        self.logger.info(f"Parsed {len(self._alerts_data)} alerts from {len(alert_categories)} categories")
        return self._alerts_data
    
    def _parse_alert_category(self, category_data: Dict[str, Any], category_name: str) -> None:
        """
        Parse alerts from a specific category
        
        Args:
            category_data (dict): The alert data for this category
            category_name (str): Name of the alert category
        """
        for alert_id, alert_config in category_data.items():
            try:
                alert_data = self._extract_alert_variables(alert_config, alert_id, category_name)
                if alert_data:
                    self._alerts_data.append(alert_data)
            except Exception as e:
                self.logger.warning(f"Error parsing alert {alert_id} in {category_name}: {e}")
                continue
    
    def _extract_alert_variables(self, alert_config: Dict[str, Any], alert_id: str, category: str) -> Optional[AlertData]:
        """
        Extract the specific variables from an alert configuration
        
        Args:
            alert_config (dict): Single alert configuration
            alert_id (str): ID/key of the alert
            category (str): Alert category name
            
        Returns:
            AlertData: Parsed alert data or None if invalid
        """
        try:
            # Extract required variables with safe defaults
            alerttype = alert_config.get("alerttype", "")
            audioname = alert_config.get("audioname", "")
            audiodirectory = alert_config.get("audiodirectory", "")
            duration = alert_config.get("duration", "0")
            volume = alert_config.get("volume", 0)
            randomized = alert_config.get("randomized", "False")
            
            # Extract optional randomization variables
            randomizedchance = alert_config.get("randomizedchance")
            randomizeddirectory = alert_config.get("randomizeddirectory")
            randomizedextrachance = alert_config.get("randomizedextrachance")
            randomizedextradirectory = alert_config.get("randomizedextradirectory")
            
            # Extract Twitch reward ID
            twitchrewardid = alert_config.get("twitchrewardid")
            
            # Extract GIF variables
            gifname = alert_config.get("gifname")
            gifdirectory = alert_config.get("gifdirectory")
            
            # Additional context fields
            alertname = alert_config.get("alertname")
            disabled = alert_config.get("disabled", False)
            
            # Handle "None" strings (convert to actual None)
            def clean_none_value(value):
                if value == "None" or value == "N/A":
                    return None
                return value
            
            return AlertData(
                alerttype=alerttype,
                audioname=clean_none_value(audioname),
                audiodirectory=clean_none_value(audiodirectory),
                duration=duration,
                volume=volume,
                randomized=randomized,
                randomizedchance=clean_none_value(randomizedchance),
                randomizeddirectory=clean_none_value(randomizeddirectory),
                randomizedextrachance=clean_none_value(randomizedextrachance),
                randomizedextradirectory=clean_none_value(randomizedextradirectory),
                twitchrewardid=clean_none_value(twitchrewardid),
                gifname=clean_none_value(gifname),
                gifdirectory=clean_none_value(gifdirectory),
                alert_id=alert_id,
                alert_category=category,
                alertname=clean_none_value(alertname),
                disabled=disabled
            )
            
        except Exception as e:
            self.logger.error(f"Error extracting variables from alert config: {e}")
            return None
    
    def get_alerts_by_type(self, alerttype: str) -> List[AlertData]:
        """
        Get all alerts of a specific type
        
        Args:
            alerttype (str): Type of alert (e.g., "bits", "points", "sub", etc.)
            
        Returns:
            List[AlertData]: Filtered list of alerts
        """
        return [alert for alert in self._alerts_data if alert.alerttype == alerttype]
    
    def get_alerts_by_category(self, category: str) -> List[AlertData]:
        """
        Get all alerts from a specific category
        
        Args:
            category (str): Category name (e.g., "bit_alerts", "point_alerts")
            
        Returns:
            List[AlertData]: Filtered list of alerts
        """
        return [alert for alert in self._alerts_data if alert.alert_category == category]
    
    def get_alert_by_id(self, alert_id: str) -> Optional[AlertData]:
        """
        Get a specific alert by its ID
        
        Args:
            alert_id (str): The alert ID to search for
            
        Returns:
            AlertData: The alert data or None if not found
        """
        for alert in self._alerts_data:
            if alert.alert_id == alert_id:
                return alert
        return None
    
    def get_alerts_with_audio(self) -> List[AlertData]:
        """
        Get all alerts that have audio files configured
        
        Returns:
            List[AlertData]: Alerts with audio configuration
        """
        return [alert for alert in self._alerts_data 
                if alert.audioname and alert.audioname != "None"]
    
    def get_randomized_alerts(self) -> List[AlertData]:
        """
        Get all alerts that have randomization enabled
        
        Returns:
            List[AlertData]: Alerts with randomization enabled
        """
        return [alert for alert in self._alerts_data 
                if alert.randomized and alert.randomized.lower() == "true"]
    
    def get_twitch_reward_alerts(self) -> List[AlertData]:
        """
        Get all alerts associated with Twitch channel point rewards
        
        Returns:
            List[AlertData]: Alerts with Twitch reward IDs
        """
        return [alert for alert in self._alerts_data 
                if alert.twitchrewardid and alert.twitchrewardid != "None"]
    
    def get_alerts_with_gifs(self) -> List[AlertData]:
        """
        Get all alerts that have GIF files configured
        
        Returns:
            List[AlertData]: Alerts with GIF configuration
        """
        return [alert for alert in self._alerts_data 
                if alert.gifname and alert.gifname != "None"]
    
    def export_filtered_data(self, alerts: List[AlertData], format: str = "dict") -> Union[List[Dict], List[AlertData]]:
        """
        Export alert data in different formats
        
        Args:
            alerts (List[AlertData]): List of alerts to export
            format (str): Export format ("dict" or "dataclass")
            
        Returns:
            Exported data in requested format
        """
        if format == "dict":
            return [
                {
                    "alert_id": alert.alert_id,
                    "alert_category": alert.alert_category,
                    "alerttype": alert.alerttype,
                    "alertname": alert.alertname,
                    "audioname": alert.audioname,
                    "audiodirectory": alert.audiodirectory,
                    "duration": alert.duration,
                    "volume": alert.volume,
                    "randomized": alert.randomized,
                    "randomizedchance": alert.randomizedchance,
                    "randomizeddirectory": alert.randomizeddirectory,
                    "randomizedextrachance": alert.randomizedextrachance,
                    "randomizedextradirectory": alert.randomizedextradirectory,
                    "twitchrewardid": alert.twitchrewardid,
                    "gifname": alert.gifname,
                    "gifdirectory": alert.gifdirectory,
                    "disabled": alert.disabled
                }
                for alert in alerts
            ]
        else:
            return alerts
    
    def get_summary_stats(self) -> Dict[str, Any]:
        """
        Get summary statistics about the parsed alerts
        
        Returns:
            Dict: Summary statistics
        """
        total_alerts = len(self._alerts_data)
        alerts_by_type = {}
        alerts_with_audio = 0
        alerts_with_gifs = 0
        randomized_alerts = 0
        twitch_reward_alerts = 0
        disabled_alerts = 0
        
        for alert in self._alerts_data:
            # Count by type
            if alert.alerttype in alerts_by_type:
                alerts_by_type[alert.alerttype] += 1
            else:
                alerts_by_type[alert.alerttype] = 1
            
            # Count features
            if alert.audioname and alert.audioname != "None":
                alerts_with_audio += 1
            if alert.gifname and alert.gifname != "None":
                alerts_with_gifs += 1
            if alert.randomized and alert.randomized.lower() == "true":
                randomized_alerts += 1
            if alert.twitchrewardid and alert.twitchrewardid != "None":
                twitch_reward_alerts += 1
            if alert.disabled:
                disabled_alerts += 1
        
        return {
            "total_alerts": total_alerts,
            "alerts_by_type": alerts_by_type,
            "alerts_with_audio": alerts_with_audio,
            "alerts_with_gifs": alerts_with_gifs,
            "randomized_alerts": randomized_alerts,
            "twitch_reward_alerts": twitch_reward_alerts,
            "disabled_alerts": disabled_alerts
        }


def main():
    """
    Example usage of the AlertsParser
    """
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Create parser instance
    parser = AlertsParser("alerts.json")
    
    # Load and parse the file
    if parser.load_file():
        alerts = parser.parse_alerts()
        
        print(f"Successfully parsed {len(alerts)} alerts")
        
        # Get summary statistics
        stats = parser.get_summary_stats()
        print("\nSummary Statistics:")
        print(f"Total alerts: {stats['total_alerts']}")
        print(f"Alerts with audio: {stats['alerts_with_audio']}")
        print(f"Alerts with GIFs: {stats['alerts_with_gifs']}")
        print(f"Randomized alerts: {stats['randomized_alerts']}")
        print(f"Twitch reward alerts: {stats['twitch_reward_alerts']}")
        print(f"Disabled alerts: {stats['disabled_alerts']}")
        
        print("\nAlerts by type:")
        for alert_type, count in stats['alerts_by_type'].items():
            print(f"  {alert_type}: {count}")
        
        # Example: Get all point alerts
        point_alerts = parser.get_alerts_by_type("points")
        print(f"\nFound {len(point_alerts)} point alerts")
        
        # Example: Get alerts with Twitch rewards
        reward_alerts = parser.get_twitch_reward_alerts()
        print(f"Found {len(reward_alerts)} Twitch reward alerts")
        
        # Example: Get alerts with GIFs
        gif_alerts = parser.get_alerts_with_gifs()
        print(f"Found {len(gif_alerts)} alerts with GIFs")
        
        # Example: Show first few alerts
        print("\nFirst 3 alerts:")
        for i, alert in enumerate(alerts[:3]):
            print(f"{i+1}. {alert.alerttype} - {alert.alertname or alert.alert_id}")
            print(f"   Audio: {alert.audioname}")
            print(f"   Volume: {alert.volume}")
            print(f"   Duration: {alert.duration}")
            if alert.gifname:
                print(f"   GIF: {alert.gifname}")
            if alert.twitchrewardid:
                print(f"   Twitch Reward ID: {alert.twitchrewardid}")
            print()


if __name__ == "__main__":
    main() 