param(
    [Parameter(Mandatory = $true)]
    [string] $TempTemplatesDir,

    [Parameter(Mandatory = $true)]
    [string] $FinalTemplatesDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Is-Array {
    param([Parameter(Mandatory = $true)] $Value)
    return ($Value -is [System.Collections.IEnumerable]) -and -not ($Value -is [string]) -and -not ($Value -is [hashtable]) -and -not ($Value -is [pscustomobject])
}

function Is-Object {
    param([Parameter(Mandatory = $true)] $Value)
    return ($Value -is [pscustomobject]) -or ($Value -is [hashtable])
}

function Clone-Value {
    param([Parameter(Mandatory = $true)] $Value)
    if ($null -eq $Value) { return $null }
    try {
        # Deep clone via JSON roundtrip
        return ($Value | ConvertTo-Json -Depth 100 | ConvertFrom-Json -Depth 100)
    } catch {
        return $Value
    }
}

function Deep-Merge {
    param(
        [Parameter(Mandatory = $true)] $Target,
        [Parameter(Mandatory = $true)] $Source
    )

    if (Is-Object -Value $Source) {
        if (-not (Is-Object -Value $Target)) {
            # Target is not an object, replace it entirely with source
            return (Clone-Value -Value $Source)
        }

        # Both are objects, merge recursively
        foreach ($srcProp in $Source.PSObject.Properties) {
            $name = $srcProp.Name
            $srcVal = $srcProp.Value

            if ($Target.PSObject.Properties[$name]) {
                # Property exists in target
                if ($name -eq 'value') {
                    # Preserve existing value - do not overwrite
                    continue
                } elseif (Is-Object -Value $srcVal) {
                    # Recursively merge objects
                    $Target.$name = Deep-Merge -Target $Target.$name -Source $srcVal
                } elseif (Is-Array -Value $srcVal) {
                    # For arrays, replace entirely (but preserve individual element values if they have ids)
                    if (Is-Array -Value $Target.$name) {
                        $Target.$name = Merge-Array-Preserve-Values -ExistingArray $Target.$name -NewArray $srcVal
                    } else {
                        $Target.$name = Clone-Value -Value $srcVal
                    }
                } else {
                    # Simple value, overwrite
                    $Target.$name = Clone-Value -Value $srcVal
                }
            } else {
                # Property doesn't exist in target, add it
                Add-Member -InputObject $Target -NotePropertyName $name -NotePropertyValue (Clone-Value -Value $srcVal)
            }
        }
        return $Target
    } elseif (Is-Array -Value $Source) {
        if (Is-Array -Value $Target) {
            return Merge-Array-Preserve-Values -ExistingArray $Target -NewArray $Source
        } else {
            return Clone-Value -Value $Source
        }
    } else {
        # Simple value, overwrite
        return Clone-Value -Value $Source
    }
}

function Merge-Array-Preserve-Values {
    param(
        [Parameter(Mandatory = $true)] $ExistingArray,
        [Parameter(Mandatory = $true)] $NewArray
    )

    if (-not (Is-Array -Value $ExistingArray) -or -not (Is-Array -Value $NewArray)) {
        return Clone-Value -Value $NewArray
    }

    # Check if arrays contain objects with id fields
    $existingHasIds = $ExistingArray.Count -gt 0 -and ($ExistingArray | Where-Object { Is-Object -Value $_ -and $_.PSObject.Properties['id'] }).Count -gt 0
    $newHasIds = $NewArray.Count -gt 0 -and ($NewArray | Where-Object { Is-Object -Value $_ -and $_.PSObject.Properties['id'] }).Count -gt 0

    if ($existingHasIds -and $newHasIds) {
        # Merge by ID, preserving values
        $existingById = @{}
        $existingOrder = @()
        $existingNoId = @()

        foreach ($el in $ExistingArray) {
            if (Is-Object -Value $el -and $el.PSObject.Properties['id']) {
                $id = [string]$el.id
                if (-not $existingById.ContainsKey($id)) {
                    $existingById[$id] = $el
                    $existingOrder += $id
                }
            } else {
                $existingNoId += $el
            }
        }

        $mergedElements = @()
        $usedIds = New-Object 'System.Collections.Generic.HashSet[string]'

        foreach ($newEl in $NewArray) {
            if (Is-Object -Value $newEl -and $newEl.PSObject.Properties['id']) {
                $id = [string]$newEl.id
                if ($existingById.ContainsKey($id)) {
                    # Merge the element but preserve the value
                    $mergedEl = Deep-Merge -Target (Clone-Value -Value $newEl) -Source $existingById[$id]
                    $mergedElements += $mergedEl
                    [void]$usedIds.Add($id)
                } else {
                    $mergedElements += Clone-Value -Value $newEl
                }
            } else {
                $mergedElements += Clone-Value -Value $newEl
            }
        }

        # Add any existing elements not in the new array
        foreach ($id in $existingOrder) {
            if (-not $usedIds.Contains($id)) {
                $mergedElements += Clone-Value -Value $existingById[$id]
            }
        }
        foreach ($el in $existingNoId) {
            $mergedElements += Clone-Value -Value $el
        }

        return $mergedElements
    } else {
        # Arrays don't have IDs or mixed content, replace with new array
        return Clone-Value -Value $NewArray
    }
}

function Ensure-Directory {
    param([Parameter(Mandatory = $true)] [string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

Write-Host "Starting template configuration migration..."

if (-not (Test-Path -LiteralPath $TempTemplatesDir)) {
    throw "Temporary templates directory not found: $TempTemplatesDir"
}

# Check if final directory exists
$finalDirExists = Test-Path -LiteralPath $FinalTemplatesDir

if (-not $finalDirExists) {
    Write-Host "Final templates directory does not exist. Copying all files..."
    Ensure-Directory -Path $FinalTemplatesDir
    Copy-Item -LiteralPath "$TempTemplatesDir\*" -Destination $FinalTemplatesDir -Recurse -Force
} else {
    Write-Host "Final templates directory exists. Merging changes..."

    Get-ChildItem -LiteralPath $TempTemplatesDir -Filter *.json -File | ForEach-Object {
        $tempFile = $_.FullName
        $finalFile = Join-Path -Path $FinalTemplatesDir -ChildPath $_.Name

        if (-not (Test-Path -LiteralPath $finalFile)) {
            Write-Host "New template file: $($_.Name) - copying..."
            Copy-Item -LiteralPath $tempFile -Destination $finalFile -Force
            return
        }

        Write-Host "Merging template file: $($_.Name)"

        try {
            $existingJson = Get-Content -LiteralPath $finalFile -Raw -ErrorAction Stop
            $newJson = Get-Content -LiteralPath $tempFile -Raw -ErrorAction Stop

            $existingObj = $existingJson | ConvertFrom-Json -Depth 100
            $newObj = $newJson | ConvertFrom-Json -Depth 100

            if ($null -eq $existingObj -or $null -eq $newObj) {
                Write-Host "Warning: Could not parse JSON for $($_.Name), skipping..."
                return
            }

            # Perform deep merge, preserving all existing 'value' fields
            $mergedObj = Deep-Merge -Target (Clone-Value -Value $existingObj) -Source $newObj

            # Save the merged result
            ($mergedObj | ConvertTo-Json -Depth 100) | Set-Content -LiteralPath $finalFile -Encoding UTF8

        } catch {
            Write-Host "Error processing $($_.Name): $($_.Exception.Message)"
            # On error, copy the new file as fallback
            Copy-Item -LiteralPath $tempFile -Destination $finalFile -Force
        }
    }
}

# Cleanup: delete the temporary templates directory
Write-Host "Cleaning up temporary directory..."
try {
    if (Test-Path -LiteralPath $TempTemplatesDir) {
        Remove-Item -LiteralPath $TempTemplatesDir -Recurse -Force -ErrorAction Stop
    }
} catch {
    Write-Host "Warning: Could not remove temporary directory: $($_.Exception.Message)"
}

Write-Host "Template configuration migration complete."


