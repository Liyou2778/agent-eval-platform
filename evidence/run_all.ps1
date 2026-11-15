$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root
$py = 'C:\Users\Liyou\Documents\deepseek\replicas\.venv\Scripts\python.exe'
$log = Join-Path $root 'data\_verify\evidence.txt'

Start-Transcript -Path $log -Force | Out-Null
try {
    $steps = @(
        @{ Name = 'wait for server'; Args = @('data\_verify\wait_ready.py') },
        @{ Name = 'a) seed 200 cases + 5 targets via REST API'; Args = @('seed.py', '--api', 'http://127.0.0.1:8205') },
        @{ Name = 'b) baseline run: 200 cases, concurrency=8'; Args = @('data\_verify\run_once.py', '--dataset-id', '1', '--target-id', '1', '--concurrency', '8', '--retries', '1', '--timeout-s', '30', '--label', 'baseline') },
        @{ Name = 'c) improved run: same dataset, second mock target'; Args = @('data\_verify\run_once.py', '--dataset-id', '1', '--target-id', '2', '--concurrency', '8', '--retries', '1', '--timeout-s', '30', '--label', 'improved') },
        @{ Name = 'c) /api/compare a=1 b=2 regression diff'; Args = @('data\_verify\compare_summary.py') },
        @{ Name = 'd) cancel propagation'; Args = @('data\_verify\cancel_test.py') },
        @{ Name = 'e) timeout classification + retry'; Args = @('data\_verify\timeout_test.py') },
        @{ Name = 'f) judge rules + llm mock unit checks'; Args = @('data\_verify\judge_unit.py') },
        @{ Name = 'f) target probes + http target run + llm judge run'; Args = @('data\_verify\judge_probe_test.py') },
        @{ Name = 'f) frontend 4 tabs + static assets'; Args = @('data\_verify\frontend_check.py') }
    )
    foreach ($step in $steps) {
        Write-Output ''
        Write-Output ('=' * 78)
        Write-Output ("STEP: " + $step.Name)
        Write-Output ('=' * 78)
        & $py @($step.Args)
        if ($LASTEXITCODE -ne 0) { throw ("step failed: " + $step.Name) }
    }
}
finally {
    Stop-Transcript | Out-Null
}
Write-Output ''
Write-Output "evidence saved to $log"
