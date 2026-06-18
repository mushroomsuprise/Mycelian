#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Mycelian Template Config Merger

This script merges new JSON configuration template files with existing ones,
preserving user-defined values while updating structure from the new files.

The new config file is always the authoritative structure. Only keys and
categories present in the new file will appear in the output. User-customized
'value' fields are transferred from the existing config where matching
elements (by 'id') are found.

Replaces the PowerShell merge_template_configs.ps1 script for cross-platform
compatibility and easier compilation as an executable.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, List


def is_array(value: Any) -> bool:
    """Check if value is an array (list) but not a string or dict."""
    return isinstance(value, list) and not isinstance(value, (str, dict))


def is_object(value: Any) -> bool:
    """Check if value is an object (dict)."""
    return isinstance(value, dict)


def clone_value(value: Any) -> Any:
    """Deep clone a value via JSON roundtrip."""
    if value is None:
        return None
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return value


def merge_element_arrays(new_array: List, old_array: List) -> List:
    """
    Merge element arrays by 'id', transferring user values from old to new.

    Only elements present in new_array appear in the result. Old-only
    elements are considered removed and are dropped. For each new element
    with a matching 'id' in old_array, the 'value' field is copied from
    the old element. Nested 'elements' arrays are merged recursively.

    Elements without an 'id' (e.g. separators) are kept as-is from new.
    """
    if not is_array(new_array) or not is_array(old_array):
        return clone_value(new_array)

    # Check if arrays contain objects with id fields
    new_has_ids = any(is_object(el) and "id" in el for el in new_array)
    old_has_ids = any(is_object(el) and "id" in el for el in old_array)

    if not (new_has_ids and old_has_ids):
        # Arrays don't have IDs or mixed content, use new array as-is
        return clone_value(new_array)

    # Build lookup of old elements by id
    old_by_id: dict[str, dict] = {}
    for el in old_array:
        if is_object(el) and "id" in el:
            el_id = str(el["id"])
            if el_id not in old_by_id:
                old_by_id[el_id] = el

    # Iterate over new array only; old-only elements are dropped
    result = []
    for new_el in new_array:
        if is_object(new_el) and "id" in new_el:
            el_id = str(new_el["id"])
            merged_el = clone_value(new_el)

            if el_id in old_by_id:
                old_el = old_by_id[el_id]

                # Transfer user's value from old element
                if "value" in old_el and "value" in merged_el:
                    merged_el["value"] = clone_value(old_el["value"])

                # Recursively merge nested elements arrays
                if "elements" in merged_el and "elements" in old_el:
                    merged_el["elements"] = merge_element_arrays(
                        merged_el["elements"], old_el["elements"]
                    )

            result.append(merged_el)
        else:
            # Element without id (separator, etc.) - keep new version as-is
            result.append(clone_value(new_el))

    return result


def merge_config(new_obj: Any, old_obj: Any) -> Any:
    """
    Recursively merge configs using new_obj as the authoritative structure.

    The new config defines what keys and categories exist. User-customized
    'value' fields are transferred from old_obj where matching keys/elements
    are found. Keys present in old_obj but not in new_obj are dropped.

    Args:
        new_obj: The new config structure (authoritative).
        old_obj: The existing config with user values to preserve.

    Returns:
        A new config with the structure of new_obj and values from old_obj.
    """
    if not is_object(new_obj) or not is_object(old_obj):
        return clone_value(new_obj)

    result = clone_value(new_obj)

    for key in list(result.keys()):
        if key not in old_obj:
            # New key/category not in old config, keep new defaults
            continue

        new_val = result[key]
        old_val = old_obj[key]

        if key == "value":
            # Transfer user's customized value from old config
            result[key] = clone_value(old_val)
        elif is_array(new_val) and is_array(old_val):
            # Merge element arrays by id
            result[key] = merge_element_arrays(new_val, old_val)
        elif is_object(new_val) and is_object(old_val):
            # Recurse into nested objects (connector_actions, dynamic_controls, etc.)
            result[key] = merge_config(new_val, old_val)
        # Otherwise: keep new value (type, label, description, options, min, max, etc.)

    return result


def ensure_directory(path: Path) -> None:
    """Ensure directory exists, creating parents as needed."""
    path.mkdir(parents=True, exist_ok=True)


def backup_existing_configs(final_dir: Path, backup_dir: Path) -> bool:
    """
    Back up all existing JSON config files to the backup directory.

    Files are copied with their original names for easy reference.

    Args:
        final_dir: Directory containing existing config files.
        backup_dir: Directory to copy backups into.

    Returns:
        True if backup succeeded, False on error.
    """
    print("Backing up existing configuration files...")
    try:
        ensure_directory(backup_dir)
        for config_file in final_dir.glob("*.json"):
            shutil.copy2(config_file, backup_dir / config_file.name)
            print(f"  Backed up {config_file.name}")
        return True
    except Exception as e:
        print(f"Error backing up configuration files: {e}")
        return False


def process_config_file(new_file: Path, existing_file: Path) -> bool:
    """
    Merge user values from existing config into the new config file in-place.

    The new file's structure is authoritative. User 'value' fields from the
    existing file are transferred to matching elements. The merged result is
    written back to new_file (in the temp directory).

    If the existing file does not exist, the new file is left as-is.

    Args:
        new_file: Path to the new config file (in temp dir, modified in-place).
        existing_file: Path to the existing config file (in final dir).

    Returns:
        True if successful, False if an error occurred.
    """
    print(f"Processing {new_file.name}...")

    if not existing_file.exists():
        # New template file with no existing counterpart, use as-is
        print(f"  New template: {new_file.name} (no existing file to merge)")
        return True

    try:
        # Load both JSON files
        with open(new_file, "r", encoding="utf-8") as f:
            new_obj = json.loads(f.read())
        with open(existing_file, "r", encoding="utf-8") as f:
            existing_obj = json.loads(f.read())

        if new_obj is None or existing_obj is None:
            print(f"  Warning: Could not parse JSON for {new_file.name}, skipping merge")
            return False

        # Merge: new structure + old user values
        merged_obj = merge_config(new_obj, existing_obj)

        # Write merged result back to the new file in temp dir
        with open(new_file, "w", encoding="utf-8") as f:
            json.dump(merged_obj, f, indent=2, ensure_ascii=False)

        print(f"  Successfully merged {new_file.name}")
        return True

    except Exception as e:
        print(f"  Error merging {new_file.name}: {e}")
        return False


def cleanup_backup(
    backup_dir: Path, error_files: List[str]
) -> None:
    """
    Clean up the backup directory, retaining backups for files with errors.

    If there were no errors, the entire backup directory is removed.
    If there were errors, only the backup copies of errored files are kept
    and all other backups are deleted.

    Args:
        backup_dir: Path to the backup directory.
        error_files: List of filenames that had merge errors.
    """
    if not backup_dir.exists():
        return

    if not error_files:
        # No errors, remove entire backup
        print("All files merged successfully. Removing backup directory...")
        try:
            shutil.rmtree(backup_dir)
            print("Backup directory removed.")
        except Exception as e:
            print(f"Warning: Could not remove backup directory: {e}")
    else:
        # Keep only backups for files that had errors
        print(f"Keeping backups for files with errors: {', '.join(error_files)}")
        error_set = set(error_files)
        try:
            for backup_file in list(backup_dir.glob("*.json")):
                if backup_file.name not in error_set:
                    backup_file.unlink()
            # Check if any files remain; if not, remove the directory
            remaining = list(backup_dir.glob("*"))
            if not remaining:
                backup_dir.rmdir()
        except Exception as e:
            print(f"Warning: Error during backup cleanup: {e}")


def cleanup_temp_directory(temp_dir: Path) -> None:
    """Remove the temporary templates directory."""
    print("Cleaning up temporary directory...")
    try:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            print("Temporary directory removed successfully.")
    except Exception as e:
        print(f"Warning: Could not remove temporary directory: {e}")


def main() -> int:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Merge new template configuration files with existing ones"
    )
    parser.add_argument(
        "--temp-dir", required=True, help="Path to temporary templates directory"
    )
    parser.add_argument(
        "--final-dir", required=True, help="Path to final templates directory"
    )

    args = parser.parse_args()

    temp_dir = Path(args.temp_dir)
    final_dir = Path(args.final_dir)

    print("Starting template configuration migration...")

    # Validate temp directory exists (contains new files from installer)
    if not temp_dir.exists():
        print(f"Error: Temporary templates directory not found: {temp_dir}")
        return 1

    # If final directory doesn't exist, this is a fresh install - just copy all
    if not final_dir.exists():
        print("Final templates directory does not exist. Copying all new files...")
        ensure_directory(final_dir)
        try:
            for item in temp_dir.glob("*.json"):
                shutil.copy2(item, final_dir / item.name)
                print(f"  Copied {item.name}")
        except Exception as e:
            print(f"Error copying files: {e}")
            return 1

        cleanup_temp_directory(temp_dir)
        print("Template configuration migration complete.")
        return 0

    # --- Existing install: backup, merge, move, cleanup ---

    print("Final templates directory exists. Merging changes...")

    # Step 1: Back up all existing configs to a backup directory
    backup_dir = final_dir.parent.parent / "template_configs_backup"
    if not backup_existing_configs(final_dir, backup_dir):
        print("Error: Failed to back up existing configs. Aborting migration.")
        return 1

    # Step 2: For each new file in temp_dir, merge old values into new structure
    error_files: List[str] = []
    success_count = 0
    total_count = 0

    for new_file in sorted(temp_dir.glob("*.json")):
        existing_file = final_dir / new_file.name
        total_count += 1

        if process_config_file(new_file, existing_file):
            success_count += 1
        else:
            error_files.append(new_file.name)

    print(f"Processed {success_count}/{total_count} files successfully.")

    if success_count == 0 and total_count > 0:
        print("No files were processed successfully. Aborting migration.")
        print(f"Backups preserved in: {backup_dir}")
        return 1

    # Step 3: Move merged files from temp_dir to final_dir
    print("Moving merged files to final directory...")
    try:
        for new_file in temp_dir.glob("*.json"):
            dest = final_dir / new_file.name
            shutil.copy2(new_file, dest)
            print(f"  Installed {new_file.name}")
    except Exception as e:
        print(f"Error moving files to final directory: {e}")
        print(f"Backups preserved in: {backup_dir}")
        return 1

    # Step 4: Clean up backup (keep only files that had errors)
    cleanup_backup(backup_dir, error_files)

    # Step 5: Remove temp directory
    cleanup_temp_directory(temp_dir)

    if error_files:
        print(
            f"Migration complete with errors on {len(error_files)} file(s). "
            f"Backups preserved in: {backup_dir}"
        )
    else:
        print("Template configuration migration complete.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
