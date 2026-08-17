$s = New-Object System.IO.FileStream('E:\Glassless 3d\overlay.log', [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
$r = New-Object System.IO.StreamReader($s)
$all = $r.ReadToEnd()
$r.Close(); $s.Close()
$lines = $all -split "`n"
Write-Host "=== HEAD (first 22 lines) ==="
$lines | Select-Object -First 22
Write-Host ""
Write-Host "=== TAIL (last 25 lines) ==="
$lines | Select-Object -Last 25
