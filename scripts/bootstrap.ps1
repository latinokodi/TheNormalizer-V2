<#
    TheNormalizer - everything the application needs, gathered by itself.

    The promise this file keeps is narrow and absolute: a Windows PC with nothing installed on it,
    and a person who double-clicks `start.bat`, ends up looking at the window. Not "install Python
    first", not "install Node first", not "ffmpeg was not found on PATH" -- those are the
    application failing to do its own job, and they were what the first version of it did.

    So this file finds or installs four things:

      Python 3.10+    the engine
      Node 18+        the window's runtime and the interface's build
      ffmpeg/ffprobe  the encodes and the probes; the engine shells out to both
      the packages    aiohttp into the project's venv, npm's trees, Electron's binary

    It writes nothing outside this folder and %LOCALAPPDATA%, so it needs no administrator, and it
    never assumes a PATH entry it cannot see: a running process does not observe a PATH change made
    while it runs, so anything installed here is located by path afterwards rather than by name.

    Everything downloaded is cached under `.tools\downloads`, so a second run is seconds rather
    than a hundred megabytes, and every step is skipped when it is already done.

    The failures this file has actually had are worth naming, because they shaped it. A package
    install that a slow connection did not finish was reported as "could not be installed", retried
    as the identical command, and then declared dead -- and the message showed the *last* lines of
    the output, which for pip was twenty copies of a cache warning and for npm was a deprecation
    notice about a package this application does not depend on. So a failure now prints the lines
    that carry a diagnosis, says which of the two failures it was, and retries as a *different*
    attempt rather than the same one. `scripts\bootstrap.ps1 -Doctor` reports what is missing
    without changing anything, which is the answer to "it died somewhere" that the console could
    not previously give.

    Written for Windows PowerShell 5.1 -- the one every supported Windows already has. No
    PowerShell 7 syntax is used, because requiring it would be the same failure in a new costume.
#>

[CmdletBinding()]
param(
    # Prove the whole thing without opening the window. Used by the checks that run this file.
    [switch] $NoLaunch,
    # Provision the project's own artefacts again -- the venv, the npm trees, the bundle -- while
    # still using whatever Python, Node and ffmpeg are already on the machine.
    [switch] $Force,
    # Put the downloaded tools somewhere else. This is how the download-and-unpack path is tested
    # without disturbing a working `.tools` folder.
    [string] $ToolsRoot,
    # Report the state of every prerequisite and change nothing. Exit 0 when everything is present
    # and non-zero when it is not, naming exactly what is missing. This is the switch that turns
    # "it died somewhere" into "here is the one thing that is wrong", and it is written to answer
    # even when the rest of this file's assumptions are unmet -- no Python, no venv, no tree.
    [switch] $Doctor
)

$ErrorActionPreference = 'Stop'
# Latest, so a typo'd variable name or a property that is not there is an error rather than an
# empty string that quietly becomes an argument somewhere further down.
Set-StrictMode -Version Latest
# Without this, downloads fail with a bare "could not create SSL/TLS secure channel": Windows
# PowerShell still negotiates TLS 1.0 by default on some installs, and python.org, nodejs.org and
# gyan.dev all refuse it.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ToolsDir = if ($ToolsRoot) { $ToolsRoot } else { Join-Path $ProjectRoot '.tools' }
$DownloadsDir = Join-Path $ToolsDir 'downloads'

# The versions the download URLs were checked against. 3.12 is what the engine was developed and
# tested on; the others are there for the day that one is withdrawn. Node is not pinned -- it is
# asked for from nodejs.org's own index, so this file does not grow stale.
$PythonFallbacks = @('3.12.10', '3.13.7', '3.11.9')
$PythonMinimum = [version] '3.10'
# The range every requirement in `backend\requirements.txt` publishes a wheel for, and the range
# the engine is tested on. It exists because of a measurement rather than a preference: left to
# itself this file chose Python 3.14.5 on a machine that also had 3.12, 3.13 and 3.11 installed,
# and it worked only because aiohttp happened to ship a `cp314` wheel. That
# is luck, and the next lagging package fails on it with "No matching distribution" -- a dead end
# that a different interpreter on the same machine would not have hit. So an interpreter in this
# range wins, and one outside it is used only as a last resort and says so.
$PythonKnownGood = '3.11-3.13'
$PythonKnownGoodMin = [version] '3.11'
$PythonKnownGoodMax = [version] '3.13'
$NodeMinimum = [version] '18.0'

$script:Provisioned = New-Object System.Collections.ArrayList

# ------------------------------------------------------------------------------------------------
#  Talking to the person watching the console
# ------------------------------------------------------------------------------------------------

#  Every stage prints one aligned line -- "  python     using Python 3.12.10 at ..." -- and any
#  further lines sit under the detail column. The console is the only diagnostic surface a
#  double-click has, so it is worth the alignment.
$LabelWidth = 10

function Write-Head {
    Write-Host ''
    Write-Host '  TheNormalizer - peak level normalization, without re-encoding the picture' -ForegroundColor White
    Write-Host ('  ' + ('-' * 66)) -ForegroundColor DarkGray
}

function Write-Stage {
    param([string] $Label, [string] $Detail)
    Write-Host ''
    Write-Host ("  {0,-$LabelWidth} " -f $Label) -ForegroundColor Cyan -NoNewline
    Write-Host $Detail -ForegroundColor Gray
}

function Write-Note {
    param([string] $Message)
    Write-Host ((' ' * (2 + $LabelWidth + 1)) + $Message) -ForegroundColor DarkGray
}

function Write-Good {
    param([string] $Message)
    Write-Host "  $Message" -ForegroundColor Green
}

function Write-Problem {
    param([string] $Message)
    Write-Host "  $Message" -ForegroundColor Red
}

function Wait-ForReader {
    Write-Host ''
    Write-Host '  Press any key to close.' -ForegroundColor DarkGray
    try { $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown') } catch { Start-Sleep -Seconds 20 }
}

function Write-Warn {
    param([string] $Message)
    Write-Host "  $Message" -ForegroundColor Yellow
}

# A failure a person can act on. The console is about to close, so the advice matters as much as
# the message, and the wait keeps both on screen.
#
# `Evidence` sits between the two: the lines of the failed command's own output that carry the
# diagnosis. It is passed in rather than appended to the message because the message is a sentence
# and the evidence is a quotation, and because the first version of this file printed the tail of a
# captured command's output as if it were the reason -- twenty copies of a cache warning under the
# words "could not be installed", on a run whose real cause had scrolled past.
function Stop-With {
    param([string] $Message, [string] $Advice, [string[]] $Evidence)
    Write-Host ''
    Write-Problem $Message
    if ($Evidence -and $Evidence.Count -gt 0) {
        Write-Host ''
        foreach ($line in $Evidence) { Write-Note $line }
    }
    if ($Advice) {
        Write-Host ''
        foreach ($line in ($Advice -split "`r?`n")) { Write-Note $line.TrimEnd() }
    }
    Wait-ForReader
    exit 1
}

function Ensure-Directory {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        $null = New-Item -ItemType Directory -Path $Path -Force
    }
    return $Path
}

# ------------------------------------------------------------------------------------------------
#  Running other programs
# ------------------------------------------------------------------------------------------------

# Runs a program and reports whether it worked and what it said, so a caller does not have to know
# that a native command's failure and a PowerShell exception arrive by different routes.
function Invoke-Native {
    param([string] $File, [string[]] $Arguments, [string] $WorkDir)

    $pushed = $false
    if ($WorkDir) { Push-Location $WorkDir; $pushed = $true }
    try {
        $output = & $File @Arguments 2>&1 | Out-String
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        return [pscustomobject]@{ Ok = ($code -eq 0); Code = $code; Text = $output.Trim() }
    } catch {
        return [pscustomobject]@{ Ok = $false; Code = -1; Text = $_.Exception.Message }
    } finally {
        if ($pushed) { Pop-Location }
    }
}

# ------------------------------------------------------------------------------------------------
#  Reading a failure
# ------------------------------------------------------------------------------------------------

#  What a failed install actually says, and where. A fixed-size tail is not a diagnosis: measured on
#  this machine, `pip install -r backend\requirements.txt` printed 20 copies of `WARNING: Cache entry
#  deserialization failed, entry ignored` and nothing else, so any tail showed the cache warning and
#  no cause; and npm's first `npm warn ...` line landed in the tail of a failed `npm install` while
#  the `npm ERR!` lines that named the cause were above it. So the diagnostic lines are searched for
#  by name and the tail is only what is left when none is found.
$script:DiagnosticPatterns = @(
    'npm ERR!', 'npm error', 'ERROR:', 'error:', 'No matching distribution',
    'Failed building wheel', 'Could not find a version', 'ETIMEDOUT', 'ENOTFOUND',
    'ECONNRESET', 'EAI_AGAIN', 'SELF_SIGNED_CERT')

#  A transfer that did not finish. Everything here can be fixed by running the thing again, because
#  what arrived is on disk and in a cache.
$script:TransferSignatures = @(
    'ETIMEDOUT', 'ENOTFOUND', 'ECONNRESET', 'EAI_AGAIN', 'SELF_SIGNED_CERT',
    'Could not fetch URL', 'Read timed out', 'read timeout', 'Connection aborted',
    'Connection reset', 'ConnectionResetError', 'Temporary failure in name resolution',
    'Name or service not known', 'remote end closed connection', 'IncompleteRead',
    'certificate verify failed', 'timed out', 'Retrying')

#  No build exists for this interpreter and no compiler is configured to make one. These signatures
#  are conclusive on their own: a wheel that failed to build, or a compiler that is not there, can
#  only be reported *after* the download succeeded, so they outrank any network wording beside them.
#  The last two are node-gyp's wording for the same thing, which npm prints instead of pip's.
$script:NoBuildSignatures = @(
    'Failed building wheel', 'Microsoft Visual C++', 'requires a compiler',
    'You need to install the latest version of Visual Studio', 'msvs_version not set')

#  The ambiguous half of the same failure. `No matching distribution` is what pip prints both when
#  no wheel exists for this interpreter *and* when the index could not be reached at all -- so it is
#  only read as "no build" once no transfer signature is present, because the two need opposite
#  advice and reading this one wrongly sends a person looking for a Python they do not need.
$script:NoBuildSignaturesIfQuiet = @('No matching distribution', 'Could not find a version')

#  Lines that are noise. pip prints one of these per damaged cache entry and they crowd out anything
#  sized to a fixed number of lines, which is exactly how the cache warning became the whole report.
$script:NoisePatterns = @('Cache entry deserialization failed')

function Test-Signature {
    param([string] $Text, [string[]] $Signatures)

    if (-not $Text) { return $false }
    foreach ($signature in $Signatures) {
        if ($Text.IndexOf($signature, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    return $false
}

# Which of the two failures this was. There is deliberately no third answer: a native command that
# exits non-zero and says nothing a person can act on is, empirically, a transfer that did not
# finish -- and the one thing that is true of it is that running it again is worth trying.
function Get-FailureKind {
    param([string] $Text)

    if (Test-Signature -Text $Text -Signatures $script:NoBuildSignatures) { return 'nobuild' }
    if (Test-Signature -Text $Text -Signatures $script:TransferSignatures) { return 'transfer' }
    if (Test-Signature -Text $Text -Signatures $script:NoBuildSignaturesIfQuiet) { return 'nobuild' }
    return 'transfer'
}

# The lines worth showing for a failed command: every diagnostic line (bounded), or else the last
# 15 lines of what it said with the fact that no diagnostic line was found stated plainly.
function Get-DiagnosticEvidence {
    param([string] $Text, [int] $Limit = 25, [int] $Fallback = 15)

    $all = New-Object System.Collections.ArrayList
    if ($Text) {
        foreach ($line in ($Text -split "`r?`n")) {
            $trimmed = $line.TrimEnd()
            if (-not $trimmed.Trim()) { continue }
            $noise = $false
            foreach ($pattern in $script:NoisePatterns) {
                if ($trimmed.IndexOf($pattern, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                    $noise = $true
                    break
                }
            }
            if ($noise) { continue }
            # Runs of one repeated line are collapsed: the count is information (a retry loop ran
            # five times), the four hundred copies of it are not.
            if ($all.Count -gt 0 -and $all[$all.Count - 1] -eq $trimmed) { continue }
            $null = $all.Add($trimmed)
        }
    }

    $matched = New-Object System.Collections.ArrayList
    foreach ($line in $all) {
        foreach ($pattern in $script:DiagnosticPatterns) {
            if ($line.IndexOf($pattern, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $null = $matched.Add($line)
                break
            }
        }
    }

    $evidence = New-Object System.Collections.ArrayList
    if ($matched.Count -gt 0) {
        $start = 0
        if ($matched.Count -gt $Limit) { $start = $matched.Count - $Limit }
        for ($index = $start; $index -lt $matched.Count; $index++) {
            $null = $evidence.Add($matched[$index])
        }
        return $evidence.ToArray()
    }

    if ($all.Count -eq 0) {
        $null = $evidence.Add('the command printed nothing at all')
        return $evidence.ToArray()
    }
    $start = 0
    if ($all.Count -gt $Fallback) { $start = $all.Count - $Fallback }
    for ($index = $start; $index -lt $all.Count; $index++) {
        $null = $evidence.Add($all[$index])
    }
    $null = $evidence.Add('no diagnostic line was found in this output; the last lines of it are shown instead')
    return $evidence.ToArray()
}

# The one place a package failure becomes a sentence. The two kinds get different sentences because
# they need opposite things from the person reading them, and the difference is the whole point:
# one is fixed by waiting and running this again, the other can never be.
function Stop-WithPackageFailure {
    param([string] $What, $Result, [string] $RunsOn, [string] $NoBuildAdvice)

    $evidence = Get-DiagnosticEvidence -Text $Result.Text
    if ($Result.Kind -eq 'nobuild') {
        Stop-With "$What has no build for $RunsOn." $NoBuildAdvice $evidence
    }

    # Not a proxy. The previous version of this said "set HTTPS_PROXY and run start.bat again",
    # which sent the operator looking for a proxy they do not have -- the failure it was printed
    # for was a 40 MB download that a slow connection had not finished.
    Stop-With "$What did not finish downloading." @"
This is almost always a slow or interrupted download rather than anything wrong with the packages
themselves: this machine could not finish fetching them in one go. Everything that did arrive is
already cached, so the next attempt continues from it instead of starting over.
Run start.bat again. It is expected to succeed.
"@ $evidence
}

function Save-Download {
    param([string] $Url, [string] $Destination, [string] $What)

    Ensure-Directory (Split-Path -Parent $Destination) | Out-Null
    $partial = "$Destination.part"
    if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }

    # Three attempts. The first run of this file on a fresh machine is exactly when a dropped
    # connection costs the most, and exactly when nobody is in a position to retry it. Each
    # attempt starts the file over, so a half-written download is never kept.
    $attempt = 0
    while ($attempt -lt 3) {
        $attempt++
        try {
            $client = New-Object System.Net.WebClient
            $client.Headers.Add('User-Agent', 'TheNormalizer')
            try { $client.DownloadFile($Url, $partial) } finally { $client.Dispose() }

            if (-not (Test-Path -LiteralPath $partial)) { throw 'nothing was written' }
            $size = (Get-Item -LiteralPath $partial).Length
            if ($size -lt 1024) { throw "only $size bytes arrived" }
            Move-Item -LiteralPath $partial -Destination $Destination -Force
            return $true
        } catch {
            if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
            if ($attempt -ge 3) {
                Write-Note "could not download $What"
                Write-Note "  $Url"
                Write-Note "  $($_.Exception.Message)"
                return $false
            }
            Write-Note "$What did not arrive; trying again ($attempt of 3)"
        }
    }
    return $false
}

function Expand-Zip {
    param([string] $Zip, [string] $Destination)

    Ensure-Directory $Destination | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
    try {
        [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Destination)
        return $true
    } catch {
        # Expand-Archive is slower but is present even where the compression assembly is not.
        try {
            Expand-Archive -LiteralPath $Zip -DestinationPath $Destination -Force
            return $true
        } catch {
            Write-Note "could not unpack $Zip"
            Write-Note "  $($_.Exception.Message)"
            return $false
        }
    }
}

# ------------------------------------------------------------------------------------------------
#  Python
# ------------------------------------------------------------------------------------------------

# A Python is usable only if it is new enough *and* able to build the project's environment: the
# `venv` module and `ensurepip` are both required, and the embeddable distribution ships neither.
function Test-Python {
    param([string] $Exe)

    if (-not $Exe) { return $null }
    # The Microsoft Store publishes a `python.exe` that is a stub opening the Store when run.
    # Testing it would open a shop window at somebody who only wanted to start an application.
    if ($Exe -like '*\WindowsApps\*') { return $null }
    if (-not (Test-Path -LiteralPath $Exe)) { return $null }

    # Single quotes inside the Python, not double: Windows PowerShell strips embedded double quotes
    # when it hands an argument to a native program, so `print("{0}")` reaches python as
    # `print({0})` and dies of a syntax error -- which this file would then read as "no Python
    # here" and answer by downloading another one.
    $version = Invoke-Native -File $Exe -Arguments @(
        '-c', "import sys;print('%d.%d.%d' % sys.version_info[:3])")
    if (-not $version.Ok) { return $null }
    try { $number = [version] $version.Text.Trim() } catch { return $null }
    if ($number -lt $PythonMinimum) { return $null }

    $modules = Invoke-Native -File $Exe -Arguments @('-c', 'import venv, ensurepip')
    if (-not $modules.Ok) { return $null }

    return [pscustomobject]@{ Exe = $Exe; Version = $number }
}

# An interpreter this application is known good on, judged by version alone. See $PythonKnownGood
# for the measurement behind the range.
#
# The comparison is made on major.minor, not on the whole version: `[version] '3.13'` is 3.13.0, so
# comparing a whole version against it would call Python 3.13.11 -- the very interpreter this range
# exists to prefer -- "outside" it. That mistake was made here and caught by the doctor, which
# reported a 3.13.11 on this machine as out of range while choosing a 3.12 over it.
function Test-PythonKnownGood {
    param($Version)

    if (-not $Version) { return $false }
    try { $number = [version] $Version } catch { return $false }
    $majorMinor = [version] "$($number.Major).$($number.Minor)"
    return ($majorMinor -ge $PythonKnownGoodMin -and $majorMinor -le $PythonKnownGoodMax)
}

# Every usable interpreter on this machine, best first. The sort is by known-good range and is
# otherwise stable, which is deliberate on two counts: the venv's own interpreter still wins among
# equals, so a second run of this file is still seconds rather than a rebuild; and an out-of-range
# interpreter -- 3.14 today -- loses to any 3.11-3.13 that is also installed.
function Get-Pythons {
    $candidates = New-Object System.Collections.ArrayList

    # One provisioned by an earlier run wins: it is the interpreter the venv belongs to.
    $null = $candidates.Add((Join-Path $ProjectRoot 'venv\Scripts\python.exe'))

    # The launcher, which is the reliable name on a machine carrying several Pythons. `-0p` lists
    # every interpreter it knows about with the path to each, which is the whole list wanted here;
    # `-3` answers with the one the launcher considers current, and on this machine that was 3.14.5
    # while 3.12, 3.13 and 3.11 were installed -- the exact choice this ordering exists to avoid.
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($arguments in @(@('-0p'), @('-3', '-c', 'import sys;print(sys.executable)'))) {
            $resolved = Invoke-Native -File $launcher.Source -Arguments $arguments
            if (-not $resolved.Ok -or -not $resolved.Text) { continue }
            foreach ($line in ($resolved.Text -split "`r?`n")) {
                # A path to an interpreter, wherever on the line the launcher put it: the listing
                # pads the version to a column and marks the default with a `*`.
                if ($line -match '(?i)([A-Z]:\\[^\r\n]*?python[w]?\.exe)') {
                    $null = $candidates.Add($matches[1].Trim())
                }
            }
        }
    }

    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $null = $candidates.Add($command.Source) }
    }

    # Straight from the official installer, in the order it lays them down.
    foreach ($pattern in @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe'),
            (Join-Path $env:ProgramFiles 'Python3*\python.exe'),
            'C:\Python3*\python.exe')) {
        foreach ($found in @(Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue)) {
            $null = $candidates.Add($found.FullName)
        }
    }

    $usable = New-Object System.Collections.ArrayList
    foreach ($candidate in $candidates) {
        $python = Test-Python -Exe $candidate
        if (-not $python) { continue }
        $seen = $false
        foreach ($already in $usable) {
            if ($already.Exe -eq $python.Exe) { $seen = $true; break }
        }
        if (-not $seen) { $null = $usable.Add($python) }
    }

    $ordered = New-Object System.Collections.ArrayList
    foreach ($knownGood in @($true, $false)) {
        foreach ($python in $usable) {
            if ((Test-PythonKnownGood -Version $python.Version) -eq $knownGood) {
                $null = $ordered.Add($python)
            }
        }
    }
    return $ordered.ToArray()
}

# The best interpreter available, which is what every caller other than the doctor wants.
function Find-Python {
    $pythons = @(Get-Pythons)
    if ($pythons.Count -eq 0) { return $null }
    return $pythons[0]
}

function Install-Python {
    # Winget first where it exists: it is already on the machine and it installs a version the
    # person can uninstall the ordinary way. It is not everywhere -- Windows Server and the LTSC
    # images ship without the App Installer -- so the official installer is the real path and this
    # is the shortcut.
    $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Note 'asking winget for Python 3.12 (it may print its own progress)'
        $null = Invoke-Native -File $winget.Source -Arguments @(
            'install', '--exact', '--id', 'Python.Python.3.12', '--scope', 'user',
            '--silent', '--accept-package-agreements', '--accept-source-agreements',
            '--disable-interactivity')
        $found = Find-Python
        if ($found) { return $found }
        Write-Note 'winget left no usable Python behind; using the official installer instead'
    }

    Ensure-Directory $DownloadsDir | Out-Null
    foreach ($version in $PythonFallbacks) {
        $installer = Join-Path $DownloadsDir "python-$version-amd64.exe"
        if (-not (Test-Path -LiteralPath $installer)) {
            Write-Note "downloading Python $version (about 26 MB)"
            $url = "https://www.python.org/ftp/python/$version/python-$version-amd64.exe"
            if (-not (Save-Download -Url $url -Destination $installer -What "Python $version")) {
                continue
            }
        }

        Write-Note "installing Python $version for this user, so no administrator is needed"
        # `InstallAllUsers=0` is what keeps this inside %LOCALAPPDATA% and out of a UAC prompt.
        # `PrependPath=1` is for the person's own later use of `python`; this file does not rely on
        # it, because a running process never sees a PATH change made while it runs.
        $result = Invoke-Native -File $installer -Arguments @(
            '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'Include_pip=1',
            'Include_test=0', 'Include_doc=0', 'Include_tcltk=0', 'SimpleInstall=1')
        if (-not $result.Ok) { Write-Note "the installer exited $($result.Code)" }

        $found = Find-Python
        if ($found) { return $found }
    }
    return $null
}

# ------------------------------------------------------------------------------------------------
#  Node
# ------------------------------------------------------------------------------------------------

function Test-Node {
    param([string] $Exe)

    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) { return $null }
    $version = Invoke-Native -File $Exe -Arguments @('--version')
    if (-not $version.Ok) { return $null }
    try { $number = [version] $version.Text.Trim().TrimStart('v') } catch { return $null }
    if ($number -lt $NodeMinimum) { return $null }
    return [pscustomobject]@{ Exe = $Exe; Dir = (Split-Path -Parent $Exe); Version = $number }
}

function Find-Node {
    $candidates = New-Object System.Collections.ArrayList

    $null = $candidates.Add((Join-Path $ToolsDir 'node\node.exe'))
    $command = Get-Command 'node.exe' -ErrorAction SilentlyContinue
    if ($command) { $null = $candidates.Add($command.Source) }
    $null = $candidates.Add((Join-Path $env:ProgramFiles 'nodejs\node.exe'))

    foreach ($candidate in $candidates) {
        $usable = Test-Node -Exe $candidate
        # npm has to be beside it: node alone cannot install anything.
        if ($usable -and (Test-Path -LiteralPath (Join-Path $usable.Dir 'npm.cmd'))) {
            return $usable
        }
    }
    return $null
}

# The newest release nodejs.org calls LTS, asked for rather than pinned, so this file does not go
# stale the way a hard-coded version does. The pinned fallback is for a machine whose access to
# nodejs.org is filtered but which can still reach the dist mirror.
function Get-NodeArchiveUrl {
    try {
        $index = Invoke-RestMethod -Uri 'https://nodejs.org/dist/index.json' -TimeoutSec 30
        $lts = $index | Where-Object { $_.lts } | Select-Object -First 1
        if ($lts -and $lts.version) {
            return "https://nodejs.org/dist/$($lts.version)/node-$($lts.version)-win-x64.zip"
        }
    } catch {
        Write-Note 'could not read the Node release index; falling back to a known version'
    }
    return 'https://nodejs.org/dist/v22.14.0/node-v22.14.0-win-x64.zip'
}

function Install-Node {
    $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Note 'asking winget for Node.js LTS (it may print its own progress)'
        $null = Invoke-Native -File $winget.Source -Arguments @(
            'install', '--exact', '--id', 'OpenJS.NodeJS.LTS', '--silent',
            '--accept-package-agreements', '--accept-source-agreements',
            '--disable-interactivity')
        $found = Find-Node
        if ($found) { return $found }
        Write-Note 'winget left no usable Node behind; unpacking the official archive instead'
    }

    # The archive rather than the installer: it needs no administrator, it cannot half-install, and
    # it lands in a folder this project owns and can delete. It is a complete Node -- node.exe, npm
    # and npx are all inside it.
    Ensure-Directory $DownloadsDir | Out-Null
    $archive = Join-Path $DownloadsDir 'node-win-x64.zip'
    if (-not (Test-Path -LiteralPath $archive)) {
        Write-Note 'downloading Node.js (about 36 MB)'
        if (-not (Save-Download -Url (Get-NodeArchiveUrl) -Destination $archive -What 'Node.js')) {
            return $null
        }
    }

    $target = Ensure-Directory (Join-Path $ToolsDir 'node')
    $staging = Join-Path $ToolsDir 'node-unpack'
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    if (-not (Expand-Zip -Zip $archive -Destination $staging)) { return $null }

    # The folder is replaced rather than merged into. Moving the archive's contents over an
    # existing install fails the moment it meets a name that is already there -- `node_modules` is
    # a directory in both -- and `Move-Item -Force` cannot overwrite a directory, so the second
    # run of this file, or any run with -Force, died on it. Renaming the unpacked folder into
    # place cannot collide with anything.
    $inner = @(Get-ChildItem -Path $staging -Directory | Select-Object -First 1)
    if ($inner.Count -eq 0) {
        Write-Note 'the Node archive did not contain a folder to unpack'
        return $null
    }
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    Move-Item -LiteralPath $inner[0].FullName -Destination $target -Force
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue

    # Report the interpreter that was just unpacked rather than asking the locator about it. The
    # locator answers "where is Node?", and it is also the function a caller turns to when it has
    # found none -- so an installer reporting through it can answer "nothing" about a job it
    # completed perfectly. That is not hypothetical: it made this file fetch a second archive for
    # a tool it had already installed, and made the check that runs it report two false failures.
    $unpacked = Test-Node -Exe (Join-Path $target 'node.exe')
    if ($unpacked) { return $unpacked }
    return (Find-Node)
}

# ------------------------------------------------------------------------------------------------
#  ffmpeg
# ------------------------------------------------------------------------------------------------

function Test-Ffmpeg {
    param([string] $Exe)

    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) { return $null }
    $version = Invoke-Native -File $Exe -Arguments @('-version')
    if (-not $version.Ok) { return $null }
    return [pscustomobject]@{ Exe = $Exe; Dir = (Split-Path -Parent $Exe) }
}

# Both binaries from one folder, because a mismatched ffmpeg and ffprobe is a quiet way to be wrong
# about a file rather than a loud way to fail.
function Find-Ffmpeg {
    $candidates = New-Object System.Collections.ArrayList
    $null = $candidates.Add((Join-Path $ToolsDir 'ffmpeg\bin\ffmpeg.exe'))
    if ($env:THE_NORMALIZER_FFMPEG) { $null = $candidates.Add($env:THE_NORMALIZER_FFMPEG) }
    $command = Get-Command 'ffmpeg.exe' -ErrorAction SilentlyContinue
    if ($command) { $null = $candidates.Add($command.Source) }
    # Where winget puts its shims, for a machine where ffmpeg was installed earlier.
    $null = $candidates.Add((Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\ffmpeg.exe'))

    foreach ($candidate in $candidates) {
        $ffmpeg = Test-Ffmpeg -Exe $candidate
        if (-not $ffmpeg) { continue }
        $ffprobe = Join-Path $ffmpeg.Dir 'ffprobe.exe'
        if (-not (Test-Ffmpeg -Exe $ffprobe)) { continue }
        return [pscustomobject]@{ Ffmpeg = $ffmpeg.Exe; Ffprobe = $ffprobe; Dir = $ffmpeg.Dir }
    }
    return $null
}

function Install-Ffmpeg {
    # gyan.dev publishes this exact URL for the current release, so it does not go stale. The
    # build is the "essentials" one, which carries libx264 and libx265; prores_ks and dnxhd are
    # ffmpeg's own encoders and are present in every build. The GitHub build is the fallback.
    $sources = @(
        [pscustomobject]@{
            Url  = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
            Name = 'ffmpeg-release-essentials.zip'
        },
        [pscustomobject]@{
            Url  = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
            Name = 'ffmpeg-master-latest-win64-gpl.zip'
        })

    Ensure-Directory $DownloadsDir | Out-Null
    foreach ($source in $sources) {
        $archive = Join-Path $DownloadsDir $source.Name
        if (-not (Test-Path -LiteralPath $archive)) {
            Write-Note 'downloading ffmpeg (about 110 MB, once)'
            if (-not (Save-Download -Url $source.Url -Destination $archive -What 'ffmpeg')) {
                continue
            }
        }

        Write-Note 'unpacking ffmpeg'
        $staging = Join-Path $ToolsDir 'ffmpeg-unpack'
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
        if (-not (Expand-Zip -Zip $archive -Destination $staging)) { continue }

        # The build sits one folder down and its name changes with the release, so the folder
        # holding `bin\ffmpeg.exe` is found rather than guessed.
        $binary = Get-ChildItem -Path $staging -Recurse -Filter 'ffmpeg.exe' -File -ErrorAction SilentlyContinue |
                  Select-Object -First 1
        if (-not $binary) {
            Write-Note 'the ffmpeg archive did not contain ffmpeg.exe'
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
            continue
        }

        $target = Ensure-Directory (Join-Path $ToolsDir 'ffmpeg\bin')
        foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
            $from = Join-Path $binary.DirectoryName $name
            if (Test-Path -LiteralPath $from) {
                Copy-Item -LiteralPath $from -Destination (Join-Path $target $name) -Force
            }
        }
        # The licences travel with the binaries. They are the terms of redistributing them, so
        # leaving them behind would be the one thing in this file that is not just plumbing.
        #
        # They are looked for beside the binary *and* one folder up, because where a build puts
        # them is not standardised: this was written expecting them in `bin`, where the build it
        # was tested against keeps none, so a working install reported a missing licence and the
        # real fault was the search. The build root is where they are.
        $buildRoot = Split-Path -Parent $binary.DirectoryName
        foreach ($folder in @($binary.DirectoryName, $buildRoot)) {
            foreach ($licence in @(Get-ChildItem -Path $folder -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match '(?i)^(licen[cs]e|copying|readme)' })) {
                Copy-Item -LiteralPath $licence.FullName -Destination (Join-Path $target $licence.Name) `
                    -Force -ErrorAction SilentlyContinue
            }
        }
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue

        # Report the pair that was just unpacked rather than asking the locator: see the note in
        # Install-Node. An installer that reports through the locator can report nothing about a
        # job it finished, and then fetch a second 190 MB archive for no reason.
        $unpacked = Test-Ffmpeg -Exe (Join-Path $target 'ffmpeg.exe')
        if ($unpacked -and (Test-Ffmpeg -Exe (Join-Path $target 'ffprobe.exe'))) {
            return [pscustomobject]@{
                Ffmpeg  = $unpacked.Exe
                Ffprobe = (Join-Path $target 'ffprobe.exe')
                Dir     = $target
            }
        }
    }
    return $null
}

# ------------------------------------------------------------------------------------------------
#  The project's own environment and dependencies
# ------------------------------------------------------------------------------------------------

# The pip cache, thrown away rather than reasoned about. This machine's cache answers every command
# with "Cache entry deserialization failed, entry ignored" -- twenty copies of it in one install,
# measured -- and a retry that resumes from it walks straight back into the same damaged entries.
# Clearing it is what makes the second attempt a different attempt rather than the same command
# again; the wheels are then fetched again, which is the price of the retry being worth running.
function Reset-PipCache {
    param([string] $PythonExe)

    $result = Invoke-Native -File $PythonExe -Arguments @(
        '-m', 'pip', 'cache', 'purge', '--disable-pip-version-check')
    if ($result.Ok) {
        Write-Note 'the pip cache was cleared first, so the retry cannot reuse its damaged entries'
    } else {
        Write-Note "the pip cache could not be cleared (pip exited $($result.Code)); retrying anyway"
    }
}

# pip, with one retry that is not the same command again. The timeouts are raised because the
# failure this was written for is a slow 40 MB download and not a broken one: pip's default read
# timeout abandons a wheel that is still arriving, and reports it as if the package were at fault.
function Install-PythonPackages {
    param([string] $PythonExe, [string] $Requirements)

    $arguments = @(
        '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
        '--timeout', '60', '--retries', '5', '-r', $Requirements)

    $result = Invoke-Native -File $PythonExe -Arguments $arguments
    if ($result.Ok) { return [pscustomobject]@{ Ok = $true; Kind = 'ok'; Result = $result } }

    $kind = Get-FailureKind -Text $result.Text
    if ($kind -eq 'transfer') {
        Write-Note 'that did not finish; clearing the cache and trying once more'
        Reset-PipCache -PythonExe $PythonExe
        $result = Invoke-Native -File $PythonExe -Arguments $arguments
        if ($result.Ok) { return [pscustomobject]@{ Ok = $true; Kind = 'ok'; Result = $result } }
        $kind = Get-FailureKind -Text $result.Text
    }

    return [pscustomobject]@{ Ok = $false; Kind = $kind; Result = $result }
}

# pytest and pytest-bdd: the application does not need them and `backend\tests` does. A clone that
# can run the application but not its own tests is how a "262 tests pass" claim gets made without
# being checkable, so this is asked for rather than assumed -- including when the environment
# already exists, because "the venv is there" is not the same question as "the venv is complete".
function Test-DevPackages {
    param([string] $PythonExe)

    $probe = Invoke-Native -File $PythonExe -Arguments @('-c', 'import pytest, pytest_bdd')
    return $probe.Ok
}

function Install-DevPackages {
    param([string] $PythonExe, [string] $Requirements)

    $installed = Install-PythonPackages -PythonExe $PythonExe -Requirements $Requirements
    if ($installed.Ok) {
        $null = $script:Provisioned.Add('the engine''s test packages')
        return
    }

    # A warning, not a refusal. The window opens and works without pytest; the only thing that
    # cannot be done without it is running the engine's tests, so stopping here would trade a
    # working application for a missing convenience.
    Write-Host ''
    foreach ($line in (Get-DiagnosticEvidence -Text $installed.Result.Text)) { Write-Note $line }
    Write-Warn 'the engine''s test packages (pytest, pytest-bdd) are NOT installed.'
    Write-Note 'the window can still be opened and used. Only running the engine''s own tests needs'
    Write-Note 'them, and start.bat tries again on the next run.'
}

function Sync-Venv {
    param($Python, [object[]] $Candidates)

    $venvRoot = Join-Path $ProjectRoot 'venv'
    $venvPython = Join-Path $venvRoot 'Scripts\python.exe'
    $requirements = Join-Path $ProjectRoot 'backend\requirements.txt'
    $requirementsDev = Join-Path $ProjectRoot 'backend\requirements-dev.txt'

    $existing = Test-Python -Exe $venvPython
    if ($existing -and -not $Force) {
        # Reused, unless it stands on an interpreter this application is not known good on *and* a
        # known-good one is on the machine. An environment on 3.14 that resolves today is the one
        # the next lagging wheel fails on; rebuilding it is how the luck is spent on purpose.
        $keep = (Test-PythonKnownGood -Version $existing.Version) -or
                (-not (Test-PythonKnownGood -Version $Python.Version))
        if ($keep) {
            Write-Stage 'engine' "the environment is ready (Python $($existing.Version))"
            if (-not (Test-DevPackages -PythonExe $venvPython)) {
                Write-Note 'the engine''s test packages are missing from it; installing them'
                Install-DevPackages -PythonExe $venvPython -Requirements $requirementsDev
            }
            return $existing
        }
        Write-Stage 'engine' "rebuilding it on Python $($Python.Version); it held Python $($existing.Version), which is outside $PythonKnownGood"
    } else {
        Write-Stage 'engine' "creating the environment with Python $($Python.Version)"
    }

    # The chosen interpreter first, then every other usable one. A package that publishes no build
    # for the first is a dead end for that interpreter and not for the machine -- and another
    # interpreter is the one thing that turns it into a working install without a download.
    $queue = New-Object System.Collections.ArrayList
    $null = $queue.Add($Python)
    if ($Candidates) {
        foreach ($candidate in $Candidates) {
            if ($candidate.Exe -ne $Python.Exe) { $null = $queue.Add($candidate) }
        }
    }

    $selected = $Python
    for ($index = 0; $index -lt $queue.Count; $index++) {
        $attempt = $queue[$index]
        if ($index -gt 0) {
            Write-Note "trying Python $($attempt.Version) at $($attempt.Exe) instead"
        }

        if (Test-Path -LiteralPath $venvRoot) { Remove-Item -LiteralPath $venvRoot -Recurse -Force }
        $created = Invoke-Native -File $attempt.Exe -Arguments @('-m', 'venv', $venvRoot)
        if (-not $created.Ok) {
            $detail = Get-DiagnosticEvidence -Text $created.Text
            if (($index + 1) -lt $queue.Count) {
                Write-Note "Python $($attempt.Version) could not create the environment; trying the next one"
                continue
            }
            Stop-With 'The engine''s environment could not be created.' @'
Nothing was installed, so nothing is half-done. Install Python by hand from
https://www.python.org/downloads/ -- ticking "Add python.exe to PATH" -- and run start.bat again.
'@ $detail
        }

        if ($index -eq 0) {
            Write-Note "installing the engine's packages (aiohttp, about 2 MB)"
        }
        # pip is brought up to date first, so an environment whose bundled pip predates the wheels
        # on the index is not what the next line fails on. Its own failure is not fatal.
        $null = Invoke-Native -File $venvPython -Arguments @(
            '-m', 'pip', 'install', '--quiet', '--upgrade', 'pip', '--disable-pip-version-check')

        $installed = Install-PythonPackages -PythonExe $venvPython -Requirements $requirements
        if ($installed.Ok) { $selected = $attempt; break }

        if ($installed.Kind -eq 'nobuild' -and (($index + 1) -lt $queue.Count)) {
            Write-Note "Python $($attempt.Version) has no build for one of the requirements"
            continue
        }

        # The advice is chosen from the interpreter that actually failed, because the two halves are
        # opposites. "Install a Python in the range this application is known good on" is the right
        # answer for a 3.14 that has just failed and a nonsense one for a 3.12, which *is* that
        # range: the operator would be told to install what they are already running. Naming the
        # wrong cause is the defect the proxy advice was, so the sentence is picked rather than
        # written once.
        $runsOn = "Python $($attempt.Version) at $($attempt.Exe)"
        $advice = @"
This is a dead end for that interpreter: the package publishes no build for it, so pip would have
to compile it here from source, and no compiler is configured for it. Nothing the network can do
will change this.
Install the Microsoft C++ Build Tools, from
https://visualstudio.microsoft.com/visual-cpp-build-tools/ , and run start.bat again so pip can
build the package for the interpreter it is already using.
"@
        if (-not (Test-PythonKnownGood -Version $attempt.Version)) {
            $advice = @"
This is a dead end for that interpreter: the package publishes no build for it, so pip would have
to compile it here from source, and no compiler is configured for it. Nothing the network can do
will change this.
Install a Python in the range this application is known good on ($PythonKnownGood) -- 3.12 is what
it is developed on -- and run start.bat again.
"@
        }
        Stop-WithPackageFailure -What "The engine's packages" -Result $installed `
            -RunsOn $runsOn -NoBuildAdvice $advice
    }

    $null = $script:Provisioned.Add("the engine's packages (Python $($selected.Version))")

    # After requirements.txt and never instead of it: this is the file that makes the engine's own
    # tests runnable, and its failure is a warning rather than a refusal.
    Install-DevPackages -PythonExe $venvPython -Requirements $requirementsDev

    return (Test-Python -Exe $venvPython)
}

# npm, with one retry that is deliberately *not* preceded by deleting anything. The tree a slow
# first attempt left behind is the point: npm resumes into it, and the packages already written to
# disk and to the npm cache are never fetched twice. Deleting `node_modules` to "start clean" would
# turn one 90 MB download into two, which is the opposite of what a retry is for.
function Install-NodePackages {
    param([string] $Npm, [string] $Tree, [string] $What)

    $arguments = @(
        'install', '--no-audit', '--no-fund',
        '--fetch-retries', '5', '--fetch-retry-maxtimeout', '120000')

    $result = Invoke-Native -File $Npm -Arguments $arguments -WorkDir $Tree
    if ($result.Ok) { return [pscustomobject]@{ Ok = $true; Kind = 'ok'; Result = $result } }

    # Retried only when the failure reads as a transfer that did not finish, which is the same
    # question -- and the same function -- the pip path asks. npm failing for a reason that is
    # conclusive, node-gyp with no compiler being the one this file has seen, is not fixed by
    # running it again: announcing "did not finish; trying again" over it would name a cause that
    # is not the one and then spend a second full install proving it. A failure that cannot be read
    # as conclusive is a transfer, and lands on the side that retries.
    $kind = Get-FailureKind -Text $result.Text
    if ($kind -eq 'transfer') {
        Write-Note "$What did not finish; trying again, resuming into the tree npm has already filled"
        $result = Invoke-Native -File $Npm -Arguments $arguments -WorkDir $Tree
        if ($result.Ok) { return [pscustomobject]@{ Ok = $true; Kind = 'ok'; Result = $result } }
        $kind = Get-FailureKind -Text $result.Text
    }

    return [pscustomobject]@{ Ok = $false; Kind = $kind; Result = $result }
}

function Sync-NodePackages {
    param($Node)

    # For this process and everything it starts: npm, and the build tools npm runs.
    $env:PATH = "$($Node.Dir);$env:PATH"
    $npm = Join-Path $Node.Dir 'npm.cmd'

    if (-not $Force -and (Test-Path -LiteralPath (Join-Path $ProjectRoot 'node_modules'))) {
        Write-Stage 'window' 'the packages are already installed'
    } else {
        Write-Stage 'window' "installing the window's packages with npm (about 90 MB)"
        $result = Install-NodePackages -Npm $npm -Tree $ProjectRoot -What "The window's packages"
        if (-not $result.Ok) {
            # Node was located or installed before this stage ran and is at least 18 by then, so
            # "install Node.js" is advice this file has already disproved. What a failed build of an
            # npm package actually needs is a compiler, which is the thing that is missing here.
            $advice = @"
One of the packages has to be built here from source and no compiler is configured for it, which is
what npm reports as a failed build. Nothing the network can do will change that.
Install the Microsoft C++ Build Tools, from
https://visualstudio.microsoft.com/visual-cpp-build-tools/ , and run start.bat again. The Node.js
LTS build from https://nodejs.org/en/download is the fallback if a package publishes a prebuilt
binary for some Node versions and not the one already installed.
"@
            Stop-WithPackageFailure -What "The window's packages" -Result $result `
                -RunsOn "Node $($Node.Version)" -NoBuildAdvice $advice
        }
        $null = $script:Provisioned.Add('the window''s packages')
    }

    # `npm install` fetches the Electron package; its postinstall step is what downloads the
    # binary, and any `ignore-scripts=true` in the person's npm configuration skips it in silence.
    # The result is "Electron failed to install correctly, please delete node_modules/electron" on
    # the first launch, so the download is checked for and run explicitly rather than trusted to a
    # hook.
    $electron = Join-Path $ProjectRoot 'node_modules\electron\dist\electron.exe'
    if (-not (Test-Path -LiteralPath $electron)) {
        $installer = Join-Path $ProjectRoot 'node_modules\electron\install.js'

        # The `npm install` above is skipped whenever `node_modules` exists, and that is the right
        # question only for a tree that is whole. A killed install leaves the folder *without* the
        # packages it had not fetched yet, and then no later run ever fetches them -- so this stage
        # would report the same unfinished-download cause on every run forever, about a package npm
        # was never asked for. `install.js` is the file that exists if and only if the Electron
        # package is in the tree, so it is what is tested, and npm is asked to finish the tree
        # rather than the operator being told to delete a tree that is mostly good.
        if (-not (Test-Path -LiteralPath $installer)) {
            Write-Note 'the Electron package is not in the tree; asking npm to finish it'
            $repair = Invoke-Native -File $Npm -Arguments @(
                'install', '--no-audit', '--no-fund',
                '--fetch-retries', '5', '--fetch-retry-maxtimeout', '120000') -WorkDir $ProjectRoot
            if (-not (Test-Path -LiteralPath $installer)) {
                # Not a download that ran out of time: npm was asked for this package and it is
                # still not there, so there is nothing for the next run to resume from either, and
                # saying otherwise would send the operator round the same loop a fourth time.
                Stop-With 'The Electron package could not be installed into node_modules.' @'
This is npm's own answer about the package rather than a transfer that ran out of time, so running
start.bat again is not expected to change it. The lines above are what npm said.
'@ (Get-DiagnosticEvidence -Text $repair.Text)
            }
        }

        Write-Note 'fetching the Electron runtime (about 100 MB)'
        $result = $null
        # Twice, for the same reason npm is tried twice: this is a 100 MB download and a slow
        # connection is the known way it does not finish.
        for ($attempt = 1; $attempt -le 2; $attempt++) {
            if ($attempt -gt 1) { Write-Note 'that did not finish; trying once more' }
            $result = Invoke-Native -File $Node.Exe -Arguments @($installer) -WorkDir $ProjectRoot
            if (Test-Path -LiteralPath $electron) { break }
        }
        if (-not (Test-Path -LiteralPath $electron)) {
            # The advice here used to be "delete node_modules\electron and run start.bat again",
            # which throws away the packages that did arrive and is the opposite of a resume.
            $evidence = $null
            if ($result) { $evidence = Get-DiagnosticEvidence -Text $result.Text }
            Stop-With 'The Electron runtime did not finish downloading.' @'
The window cannot open without it, but nothing about it is broken: Electron's own downloader keeps
the bytes it has already fetched, so running start.bat again continues from them rather than
starting over. It is a 100 MB download and it is expected to succeed on a second run.
'@ $evidence
        }
        $null = $script:Provisioned.Add('the Electron runtime')
    }
    return $npm
}

# Whether the built interface is older than anything it was built from. Factored out because two
# callers need the same answer -- the stage that rebuilds it and the doctor that only reports it --
# and because a second copy of this comparison is a second place for it to drift.
#
# The comparison is against the bundle itself and not against a fixed timestamp: comparing to a
# fixed timestamp rebuilds for the wrong reason and misses the right one the moment the bundle
# happens to be newer than it.
function Test-FrontendStale {
    param([string] $Frontend)

    $bundle = Join-Path $Frontend 'dist\index.html'
    if (-not (Test-Path -LiteralPath $bundle)) { return $true }
    if ($Force) { return $true }

    $built = (Get-Item -LiteralPath $bundle).LastWriteTimeUtc
    $changed = @(Get-ChildItem -Path (Join-Path $Frontend 'src') -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @('.ts', '.tsx', '.css', '.html') -and $_.LastWriteTimeUtc -gt $built })
    if ($changed.Count -gt 0) { return $true }

    foreach ($name in @('index.html', 'vite.config.ts', 'vite.config.js', 'package.json', 'tsconfig.json')) {
        $path = Join-Path $Frontend $name
        if ((Test-Path -LiteralPath $path) -and (Get-Item -LiteralPath $path).LastWriteTimeUtc -gt $built) {
            return $true
        }
    }
    return $false
}

# Whether the interface's tree holds the two programs its build script runs. `frontend\node_modules`
# being there is a different question: the script is `tsc --noEmit && vite build`, and a tree a killed
# install left half-written has the folder without them. Asked by the stage that installs the tree and
# by the doctor that reports it, so it is spelled once here and not in two places that can drift.
#
# It matters because the folder is what the skip is keyed on. Reporting "the packages are already
# installed" about a tree that is about to fail the build is a claim that gets repeated on every
# later run, and "The interface did not build" would then be the whole of the explanation.
function Test-FrontendTree {
    param([string] $Frontend)

    foreach ($package in @('typescript', 'vite')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Frontend "node_modules\$package\package.json"))) {
            return $false
        }
    }
    return $true
}

function Sync-Frontend {
    param([string] $Npm, $Node)

    $frontend = Join-Path $ProjectRoot 'frontend'
    if (-not $Force -and (Test-FrontendTree -Frontend $frontend)) {
        Write-Stage 'interface' 'the packages are already installed'
    } else {
        Write-Stage 'interface' "installing the interface's packages (React, Vite and TypeScript)"
        $result = Install-NodePackages -Npm $Npm -Tree $frontend -What "The interface's packages"
        if (-not $result.Ok) {
            # Node was located or installed before this stage ran and is at least 18 by then, so
            # "install Node.js" is advice this file has already disproved. What a failed build of an
            # npm package actually needs is a compiler, which is the thing that is missing here.
            $advice = @"
One of the packages has to be built here from source and no compiler is configured for it, which is
what npm reports as a failed build. Nothing the network can do will change that.
Install the Microsoft C++ Build Tools, from
https://visualstudio.microsoft.com/visual-cpp-build-tools/ , and run start.bat again. The Node.js
LTS build from https://nodejs.org/en/download is the fallback if a package publishes a prebuilt
binary for some Node versions and not the one already installed.
"@
            Stop-WithPackageFailure -What "The interface's packages" -Result $result `
                -RunsOn "Node $($Node.Version)" -NoBuildAdvice $advice
        }
    }

    if (Test-FrontendStale -Frontend $frontend) {
        Write-Note 'building the interface'
        $result = Invoke-Native -File $Npm -Arguments @('run', 'build') -WorkDir $frontend
        if (-not $result.Ok) {
            Stop-With -Message 'The interface did not build.' `
                -Evidence (Get-DiagnosticEvidence -Text $result.Text)
        }
        if (-not (Test-Path -LiteralPath (Join-Path $frontend 'dist\index.html'))) {
            Stop-With 'The interface build finished but produced no page.' $null
        }
        $null = $script:Provisioned.Add('the interface bundle')
    } else {
        Write-Note 'the interface is already built'
    }
}

function Invoke-EngineChecks {
    param($Python)

    if ($env:THE_NORMALIZER_SKIP_TESTS -eq '1') { return }
    $pytest = Invoke-Native -File $Python.Exe -Arguments @('-c', 'import pytest')
    if (-not $pytest.Ok) { return }

    Write-Stage 'checks' "the engine's own tests; they never launch an encoder"
    $result = Invoke-Native -File $Python.Exe -Arguments @('-m', 'pytest', 'backend\tests', '-q') -WorkDir $ProjectRoot
    if ($result.Ok) {
        Write-Note 'the engine''s checks passed'
    } else {
        # The output is printed, not summarised. The first version of this said "the failure is
        # above" while `Invoke-Native` had captured the output and dropped it, so the message
        # pointed at nothing -- a diagnostic thrown away is worse than no diagnostic, because it
        # reads like one was given.
        Write-Host ''
        foreach ($line in ($result.Text -split "`r?`n")) {
            if ($line.Trim()) { Write-Host "  $line" -ForegroundColor DarkGray }
        }
        Write-Host ''
        # A warning rather than a refusal: the application does not need these tests in order to
        # run, and a person who wants to trim something should not be stopped by a red test.
        Write-Note 'the engine''s checks did NOT pass. The window can still be opened; the output'
        Write-Note 'above is the failure. Nothing the dependencies can do will change it.'
    }
}

# ------------------------------------------------------------------------------------------------
#  The doctor: what is here, changing nothing
# ------------------------------------------------------------------------------------------------

#  One line per prerequisite, in the shape of the stages above, with a state where the stage's
#  detail would be. The state is a value and not a colour, because the exit code is built from it.
$DoctorStateWidth = 8

function Write-Doctor {
    param([string] $Label, [string] $State, [string] $Detail)

    $colour = 'Gray'
    if ($State -eq 'ok') { $colour = 'Green' }
    elseif ($State -eq 'warn') { $colour = 'Yellow' }
    elseif ($State -eq 'missing' -or $State -eq 'busy') { $colour = 'Red' }

    Write-Host ''
    Write-Host ("  {0,-$LabelWidth} " -f $Label) -ForegroundColor Cyan -NoNewline
    Write-Host ("{0,-$DoctorStateWidth} " -f $State) -ForegroundColor $colour -NoNewline
    Write-Host $Detail -ForegroundColor Gray
}

# Whether anything is already listening on the port the engine binds. `electron/main.cjs` reads the
# same number out of PORT, so a second copy of the application -- or the sibling cutting tool, which
# this project deliberately does not share a port with -- is worth knowing about before the window
# opens rather than after it fails to reach its own engine.
function Test-PortFree {
    param([int] $Port)

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $wait = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        $arrived = $wait.AsyncWaitHandle.WaitOne(500, $false)
        # A refused connection also completes the wait; only an accepted one sets Connected.
        return (-not ($arrived -and $client.Connected))
    } catch {
        return $true
    } finally {
        $client.Close()
    }
}

# The name of whatever holds the port, when Windows will say. Only used to make the doctor's line
# more useful, so every way of not knowing is silent rather than fatal.
function Get-PortOwner {
    param([int] $Port)

    try {
        foreach ($connection in @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)) {
            $owner = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
            if ($owner) { return $owner.ProcessName }
        }
    } catch {
        return $null
    }
    return $null
}

# The first line of `ffmpeg -version`, cut before its copyright notice: enough to name the build,
# which is what matters when a build missing libx264 is the reason a cut fails later.
function Get-FfmpegBanner {
    param([string] $Exe)

    $version = Invoke-Native -File $Exe -Arguments @('-version')
    if (-not $version.Ok -or -not $version.Text) { return $null }
    $first = ($version.Text -split "`r?`n")[0].Trim()
    return ($first -replace '\s+Copyright.*$', '')
}

function Invoke-Doctor {
    Write-Head
    Write-Stage 'doctor' 'reporting only; nothing is changed by this'

    $missing = New-Object System.Collections.ArrayList
    $warnings = New-Object System.Collections.ArrayList
    $chosenPython = $null

    # --- Every interpreter on the machine, and the one start.bat would actually use.
    $pythons = @(Get-Pythons)
    if ($pythons.Count -eq 0) {
        Write-Doctor 'python' 'missing' 'no usable Python (3.10 or newer, with venv and ensurepip) was found'
        $null = $missing.Add('the Python interpreter: none usable was found; start.bat would install 3.12')
    } else {
        $chosenPython = $pythons[0]
        $detail = "Python $($chosenPython.Version) at $($chosenPython.Exe)  (would be chosen)"
        if (Test-PythonKnownGood -Version $chosenPython.Version) {
            Write-Doctor 'python' 'ok' $detail
        } else {
            Write-Doctor 'python' 'warn' $detail
            $null = $warnings.Add("Python $($chosenPython.Version) is outside $PythonKnownGood, the range every requirement publishes a wheel for")
            Write-Note 'start.bat would use it only because nothing in that range is installed here'
        }
        foreach ($other in @($pythons | Select-Object -Skip 1)) {
            $note = "also usable: Python $($other.Version) at $($other.Exe)"
            if (-not (Test-PythonKnownGood -Version $other.Version)) { $note += " (outside $PythonKnownGood)" }
            Write-Note $note
        }
    }

    # --- Node, with the npm that has to be beside it.
    $node = Find-Node
    if ($node) {
        Write-Doctor 'node' 'ok' "Node $($node.Version) at $($node.Dir), with npm.cmd beside it"
    } else {
        Write-Doctor 'node' 'missing' 'no Node 18 or newer with npm.cmd beside it'
        $null = $missing.Add('Node.js: no usable node.exe with npm.cmd beside it; start.bat would install one')
    }

    # --- ffmpeg and ffprobe, which the engine shells out to for every probe and every encode.
    $ffmpeg = Find-Ffmpeg
    if ($ffmpeg) {
        Write-Doctor 'ffmpeg' 'ok' $ffmpeg.Ffmpeg
        Write-Note "ffprobe:  $($ffmpeg.Ffprobe)"
        $banner = Get-FfmpegBanner -Exe $ffmpeg.Ffmpeg
        if ($banner) { Write-Note $banner }
    } else {
        Write-Doctor 'ffmpeg' 'missing' 'ffmpeg and ffprobe were not found together'
        $null = $missing.Add('ffmpeg and ffprobe: not found as a pair; start.bat would install them')
    }

    # --- The engine's environment, and what actually imports inside it.
    $venvPython = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
    $venv = Test-Python -Exe $venvPython
    if (-not $venv) {
        Write-Doctor 'engine' 'missing' 'venv\Scripts\python.exe is not there'
        $null = $missing.Add('the engine''s environment: venv is not there; start.bat would create it')
    } else {
        $imported = New-Object System.Collections.ArrayList
        $absent = New-Object System.Collections.ArrayList
        foreach ($module in @('aiohttp', 'pytest', 'pytest_bdd')) {
            if ((Invoke-Native -File $venvPython -Arguments @('-c', "import $module")).Ok) {
                $null = $imported.Add("$module ok")
            } else {
                $null = $imported.Add("$module MISSING")
                $null = $absent.Add($module)
            }
        }
        $needed = @($absent | Where-Object { $_ -notin @('pytest', 'pytest_bdd') })
        $state = 'ok'
        if ($needed.Count -gt 0) { $state = 'missing' }
        elseif ($absent.Count -gt 0) { $state = 'warn' }
        Write-Doctor 'engine' $state "venv is Python $($venv.Version); $($imported -join ', ')"

        foreach ($module in $needed) {
            $null = $missing.Add("the engine's packages: $module does not import from the venv")
        }
        foreach ($module in $absent) {
            if ($module -notin $needed) {
                $null = $warnings.Add("$module does not import from the venv, so the engine's own tests cannot run")
            }
        }
        if (-not (Test-PythonKnownGood -Version $venv.Version) -and $chosenPython -and
            (Test-PythonKnownGood -Version $chosenPython.Version)) {
            Write-Note "start.bat would rebuild it on Python $($chosenPython.Version), which is in $PythonKnownGood"
        }
    }

    # --- The window's tree, and the Electron binary the tree alone does not put there.
    $electron = Join-Path $ProjectRoot 'node_modules\electron\dist\electron.exe'
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'node_modules'))) {
        Write-Doctor 'window' 'missing' 'node_modules is not there'
        $null = $missing.Add('the window''s packages: node_modules is not there')
    } elseif (-not (Test-Path -LiteralPath $electron)) {
        Write-Doctor 'window' 'missing' 'node_modules is there, but node_modules\electron\dist\electron.exe is not'
        $null = $missing.Add('the Electron runtime: node_modules\electron\dist\electron.exe is not there')
    } else {
        Write-Doctor 'window' 'ok' 'node_modules is there, with the Electron runtime in it'
    }

    # --- The interface's tree and the page it is built into.
    $frontend = Join-Path $ProjectRoot 'frontend'
    if (-not (Test-Path -LiteralPath (Join-Path $frontend 'node_modules'))) {
        Write-Doctor 'interface' 'missing' 'frontend\node_modules is not there'
        $null = $missing.Add('the interface''s packages: frontend\node_modules is not there')
    } elseif (-not (Test-FrontendTree -Frontend $frontend)) {
        # The folder without the two programs the build script runs is what a killed npm install
        # leaves, and reporting it "ok" would be the doctor claiming a tree that cannot build is
        # complete -- the one question the doctor exists to answer.
        Write-Doctor 'interface' 'missing' 'frontend\node_modules is there, but typescript and vite are not in it'
        $null = $missing.Add('the interface''s packages: frontend\node_modules has no typescript or vite to build with')
    } else {
        Write-Doctor 'interface' 'ok' 'frontend\node_modules is there'
    }

    $bundle = Join-Path $frontend 'dist\index.html'
    if (-not (Test-Path -LiteralPath $bundle)) {
        Write-Doctor 'bundle' 'missing' 'frontend\dist\index.html is not there'
        $null = $missing.Add('the interface bundle: frontend\dist\index.html is not there')
    } elseif (Test-FrontendStale -Frontend $frontend) {
        Write-Doctor 'bundle' 'warn' 'frontend\dist\index.html is older than a source it was built from'
        Write-Note 'start.bat would rebuild it; the page that is there still opens'
        $null = $warnings.Add('the interface bundle is older than its sources and would be rebuilt')
    } else {
        Write-Doctor 'bundle' 'ok' 'frontend\dist\index.html is newer than every source it is built from'
    }

    # --- The port the engine binds.
    $port = 8767
    if ($env:PORT) {
        try { $port = [int] $env:PORT } catch { $port = 8767 }
    }
    if (Test-PortFree -Port $port) {
        Write-Doctor 'port' 'ok' "127.0.0.1:$port is free"
    } else {
        $owner = Get-PortOwner -Port $port
        $detail = "127.0.0.1:$port is already in use"
        if ($owner) { $detail += " by $owner" }
        Write-Doctor 'port' 'busy' $detail
        Write-Note 'the engine has to bind this port; something else answering there is the one thing'
        Write-Note 'a second copy of this application cannot share'
        $null = $missing.Add("the backend port: 127.0.0.1:$port is already in use")
    }

    Write-Host ''
    Write-Host ('  ' + ('-' * 66)) -ForegroundColor DarkGray
    if ($missing.Count -eq 0) {
        Write-Good 'nothing is missing; everything the application needs is present'
        foreach ($warning in $warnings) { Write-Note $warning }
        Write-Host ''
        return 0
    }

    $what = 'things are'
    if ($missing.Count -eq 1) { $what = 'thing is' }
    Write-Problem "$($missing.Count) $what missing:"
    foreach ($item in $missing) { Write-Note $item }
    foreach ($warning in $warnings) { Write-Warn $warning }
    Write-Host ''
    return 1
}

# ------------------------------------------------------------------------------------------------
#  Main
# ------------------------------------------------------------------------------------------------

function Start-Stitcher {
    Write-Head
    if ($ToolsRoot) { Write-Note "tools root: $ToolsDir" }

    # 1. Python. Found first whatever else was asked for: `-Force` means "provision this project
    #    again", not "download a second Python over a perfectly good one".
    #
    #    Every usable interpreter is kept, not just the best one, because a package that publishes
    #    no wheel for the best one is a dead end for that interpreter and not for the machine --
    #    and the machine has usually already got the answer installed.
    $pythons = @(Get-Pythons)
    $python = $null
    if ($pythons.Count -gt 0) { $python = $pythons[0] }
    if ($python) {
        Write-Stage 'python' "using $($python.Version) at $($python.Exe)"
        if (-not (Test-PythonKnownGood -Version $python.Version)) {
            # Said out loud rather than left to be discovered by the next failed install: it is
            # used because it is all that is here, and the wheels are only known good in the range.
            Write-Note "warning: $($python.Version) is outside $PythonKnownGood, the range every requirement"
            Write-Note "publishes a wheel for. It is used because nothing in that range is installed."
        }
    } else {
        Write-Stage 'python' 'none found on this machine; installing one'
        $python = Install-Python
        if (-not $python) {
            Stop-With 'Python could not be found or installed.' @'
Install it by hand from https://www.python.org/downloads/ -- ticking "Add python.exe to PATH"
-- and run start.bat again.
'@
        }
        $null = $script:Provisioned.Add("Python $($python.Version)")
        $pythons = @($python)
    }

    # 2. Node.
    $node = Find-Node
    if ($node) {
        Write-Stage 'node' "using $($node.Version) at $($node.Dir)"
    } else {
        Write-Stage 'node' 'none found on this machine; installing one'
        $node = Install-Node
        if (-not $node) {
            Stop-With 'Node.js could not be found or installed.' @'
Install the LTS build from https://nodejs.org/en/download and run start.bat again.
'@
        }
        $null = $script:Provisioned.Add("Node $($node.Version)")
    }

    # 3. ffmpeg. The engine shells out to ffmpeg and ffprobe for every probe and every encode, so
    #    this is not optional the way a build tool would be.
    $ffmpeg = Find-Ffmpeg
    if ($ffmpeg) {
        Write-Stage 'ffmpeg' "using $($ffmpeg.Ffmpeg)"
    } else {
        Write-Stage 'ffmpeg' 'none found on this machine; installing one'
        $ffmpeg = Install-Ffmpeg
        if (-not $ffmpeg) {
            Stop-With 'ffmpeg could not be found or installed.' @'
Install it with "winget install Gyan.FFmpeg", or download a build from
https://www.gyan.dev/ffmpeg/builds/ and put ffmpeg.exe and ffprobe.exe on PATH.
'@
        }
        $null = $script:Provisioned.Add('ffmpeg')
    }

    # The engine reads these two names before it looks on PATH, so pointing them at the binaries
    # just verified is what makes the application use exactly these and not another copy that
    # happens to come first in PATH. Electron inherits them, and so does the engine it starts.
    $env:THE_NORMALIZER_FFMPEG = $ffmpeg.Ffmpeg
    $env:THE_NORMALIZER_FFPROBE = $ffmpeg.Ffprobe

    # 4. The engine's environment and packages. The interpreters are handed over so that a package
    #    publishing no build for the chosen one can be answered with another one on this machine.
    $venvPython = Sync-Venv -Python $python -Candidates $pythons

    # 5. The window's packages, and Electron's own binary.
    $npm = Sync-NodePackages -Node $node

    # 6. The interface.
    Sync-Frontend -Npm $npm -Node $node

    # 7. The engine's own tests.
    Invoke-EngineChecks -Python $venvPython

    Write-Host ''
    Write-Host ('  ' + ('-' * 66)) -ForegroundColor DarkGray
    if ($script:Provisioned.Count -gt 0) {
        Write-Good ('provisioned: ' + ($script:Provisioned -join ', '))
    }
    Write-Good 'everything the application needs is present'

    if ($NoLaunch) {
        Write-Note 'not opening the window (--NoLaunch was given)'
        Write-Host ''
        return 0
    }

    Write-Note 'opening the window...'
    Write-Host ''
    # Not through Invoke-Native: that captures output, and Electron is a program a person watches
    # and closes, not one whose console is read after it exits.
    Push-Location $ProjectRoot
    # `$code` is initialised before the call rather than read straight from `$LASTEXITCODE`, which is
    # only ever set by a *native* command and is `$null` otherwise. `$null -ne 0` is true, so a window
    # that closed cleanly down a path that ran no native command was reported as "closed with an
    # error" -- a false alarm on the one line whose entire job is telling a real failure from a normal
    # close, which is how a person learns to ignore it.
    $code = 0
    try {
        & $npm 'start'
        if ($null -ne $LASTEXITCODE) {
            $code = $LASTEXITCODE
        }
    } finally {
        Pop-Location
    }
    if ($code -ne 0) {
        Write-Host ''
        Write-Problem 'The window closed with an error.'
        Wait-ForReader
        return 1
    }
    return 0
}

# Runs when this file is executed. It is dot-sourceable as well -- `. .\bootstrap.ps1` -- which is
# how the checks in `scripts/check-bootstrap.ps1` reach the locators without a machine to test on.
# A dot-sourced file has an InvocationName of '.', and an executed one has the path.
if ($MyInvocation.InvocationName -ne '.') {
    # `-Doctor` answers the question the rest of this file cannot: what is missing. It shares the
    # locators and nothing else -- it creates no environment, installs nothing and builds nothing --
    # so it is safe on a checkout in any state, including one this file has never touched.
    if ($Doctor) {
        exit (Invoke-Doctor)
    }
    exit (Start-Stitcher)
}
