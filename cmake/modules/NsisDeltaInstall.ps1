param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$PluginsDir
)

$ErrorActionPreference = 'Stop'

function Read-Manifest([string]$Path) {
    $map = @{}
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $bar = $line.LastIndexOf('|')
        if ($bar -gt 0) {
            $map[$line.Substring(0, $bar)] = $line.Substring($bar + 1).ToLowerInvariant()
        }
    }
    return $map
}

function Remove-EmptyParents([string]$Start, [string]$Root) {
    $dir = $Start
    while ($dir -and $dir.Length -gt $Root.Length -and $dir.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
            $dir = Split-Path -Parent $dir
            continue
        }
        if (Get-ChildItem -LiteralPath $dir -Force | Select-Object -First 1) {
            return
        }
        Remove-Item -LiteralPath $dir -Force
        $dir = Split-Path -Parent $dir
    }
}

try {
    $root = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    $new = Read-Manifest (Join-Path $PluginsDir 'new_manifest.txt')

    $skipDir = Join-Path $PluginsDir 'skip'
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $index = 0
    $skipped = 0
    $total = 0
    foreach ($rel in [System.IO.File]::ReadAllLines((Join-Path $PluginsDir 'files.txt'))) {
        if ($rel) {
            $total++
            $target = Join-Path $root $rel
            if ($new.ContainsKey($rel) -and [System.IO.File]::Exists($target)) {
                $stream = [System.IO.File]::OpenRead($target)
                try {
                    $hash = [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
                } finally {
                    $stream.Dispose()
                }
                if ($hash -eq $new[$rel]) {
                    [System.IO.File]::WriteAllBytes((Join-Path $skipDir "$index"), [byte[]]@())
                    $skipped++
                }
            }
        }
        $index++
    }
    Write-Output "Unchanged files skipped: $skipped of $total"

    $oldPath = Join-Path $root 'install_manifest.txt'
    if (Test-Path -LiteralPath $oldPath -PathType Leaf) {
        $old = Read-Manifest $oldPath
        foreach ($rel in $old.Keys) {
            if ($new.ContainsKey($rel)) {
                continue
            }
            $target = Join-Path $root $rel
            if (Test-Path -LiteralPath $target -PathType Leaf) {
                Remove-Item -LiteralPath $target -Force
                Write-Output "Removed: $rel"
            }
            Remove-EmptyParents (Split-Path -Parent $target) $root
        }
    }
} catch {
    Write-Output "Delta check skipped: $($_.Exception.Message)"
}

exit 0
