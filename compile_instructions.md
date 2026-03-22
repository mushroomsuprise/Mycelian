# PyInstaller Compilation Instructions

To compile the `merge_template_configs.py` script into a standalone executable:

## Prerequisites
```bash
pip install pyinstaller
```

## Basic Compilation
```bash
pyinstaller --onefile merge_template_configs.py
```

## Recommended Options
```bash
pyinstaller --onefile --noconsole --name "merge_template_configs" merge_template_configs.py
```

## Detailed Options
```bash
pyinstaller \
  --onefile \
  --noconsole \
  --name "merge_template_configs" \
  --distpath "dist" \
  --workpath "build" \
  --clean \
  merge_template_configs.py
```

## After Compilation
- The executable will be in the `dist` folder
- Update the InnoSetup script to use the new executable instead of PowerShell
- The executable accepts the same arguments as the PowerShell script:
  - `--temp-dir`: Path to temporary templates directory
  - `--final-dir`: Path to final templates directory

## Example Usage
```bash
merge_template_configs.exe --temp-dir "C:\Mycelian\templates\template_configs_temp" --final-dir "C:\Mycelian\templates\template_configs"
```

## InnoSetup Integration
Replace the PowerShell execution line in Mycelian.iss with:
```
Filename: "{app}\merge_template_configs.exe"; Parameters: "--temp-dir ""{app}\templates\template_configs_temp"" --final-dir ""{app}\templates\template_configs"""; StatusMsg: "Merging template configuration files..."; Flags: waituntilterminated
```

## Notes
- The script runs silently with no console window when compiled with `--noconsole`
- All output goes to print statements for logging/debugging
- The script uses only Python standard library, no external dependencies
- Returns exit code 0 on success, 1 on error