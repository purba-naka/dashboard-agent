# Diagnosis proses yang memegang port suite e2e (8001, 3001). Pakai -Kill untuk mematikan pohonnya.
param([int[]]$Ports = @(8001, 3001), [switch]$Kill)
foreach ($port in $Ports) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)"
    "{0} pid={1} parent={2} {3}" -f $port, $p.ProcessId, $p.ParentProcessId, $p.CommandLine
    if ($Kill) { taskkill /PID $p.ProcessId /T /F | Out-Null }
  }
}
