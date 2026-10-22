<#
.SYNOPSIS
Build openhamshack-atv-<version>-setup.exe (and its .sha256) on Windows.

.DESCRIPTION
Run on a Windows 10/11 x64 machine, from a checkout (or a `git archive`) of
the repository:

    powershell -ExecutionPolicy Bypass -File tools\build-windows.ps1 [-Work C:\ohsatv-build\<v>] [-NoTests] [-NoSign]

Everything it downloads or builds goes under -Work: a private Python
(python.org's NuGet package, unpacked, not installed), a virtualenv, a
download cache, PyInstaller's work folders, and out\ with the installer.
Nothing is installed machine-wide and PATH is not touched, so a developer's
own PC is left as it was: delete the folder and it is gone. Inno Setup, if
not already installed, is unpacked into the same folder in its portable
mode. Seed a new work folder's cache\ from an earlier one (or from OpenHamShack
NB's or DATV's: most pins are theirs) to skip the downloads; every file is
SHA-checked again.

The version comes from ohsatv\VERSION, as the .deb's does. The test suite
runs under the build's own Python first (skip with -NoTests: over SSH it can
outlast one foreground session, so there build with -NoTests and run the
suite separately, in parts - HANDOVER.md, Windows). After freezing, the
console .exe is run headless on the simulator, and the frozen program is
made to load its own libiio and its own libhackrf and to run its own
ffmpeg, so a bundle missing a module or a DLL fails here rather than on
someone's PC.

What the installer carries, beside the program: libiio 0.26 and ADI's
PlutoSDR USB drivers (exactly as OpenHamShack NB and DATV carry them), FFmpeg
9.0.2 (ffmpeg.exe and ffprobe.exe from the static build DATV carries) for
the webcam and file sources, and libhackrf 2026.01.3 with the libusb and
winpthreads DLLs it imports, from conda-forge's packages, for a HackRF.
Their licences are in packaging\windows\THIRD-PARTY.txt and their source in
packaging\windows\sources.txt.

Everything of ours in the installer, and the installer and its uninstaller,
is Authenticode-signed as 360 Technology through Azure Artifact Signing,
exactly as NB and HAM are (packaging\windows\sign.ps1 says how, where the
credential lives and how to rotate it): the two .exes before Inno Setup
packs them, the uninstaller and setup.exe by Inno Setup's own SignTool, and
every signature is checked (signer, chain, timestamp) before the .sha256 is
written. Third-party binaries keep their own publishers' signatures, or
none. A build that cannot sign fails; -NoSign makes an unsigned build, and
says so loudly: for local testing, and for the OpenHamShack beta, whose
installers are unsigned (BETA-PLAN; the download page says what Windows
shows).

Copied in shape from OpenHamShack DATV's tools/build-windows.ps1 (the fetching,
ffmpeg, the smoke tests' shape) and OpenHamShack NB's (signing, the licence
check, the Qt PDF check), both GPLv3, whose Windows ports proved every step
shared with this one. New here: libhackrf.

Every download that ends up inside the installer is pinned by SHA-256: a
changed upstream file stops the build instead of shipping. Windows
PowerShell 5.1 compatible (no ?? and no -NoNewline): that is what a fresh
Windows has. Run it in the foreground over SSH: Windows OpenSSH kills a
background child when the session closes.
#>
param(
    [string]$Work = "C:\ohsatv-build\work",
    [switch]$NoTests,
    # An unsigned build: for trying something locally, or a beta installer
    # (BETA-PLAN: unsigned for the beta). Never for a signed release.
    [switch]$NoSign
)

$ErrorActionPreference = "Stop"
# Invoke-WebRequest in 5.1 is tens of times slower while drawing its progress bar.
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = Split-Path -Parent $PSScriptRoot
$Pkg = "openhamshack-atv"
$Version = (Get-Content (Join-Path $Repo "ohsatv\VERSION") -Raw).Trim()
$Cache = Join-Path $Work "cache"
$Vendor = Join-Path $Work "vendor"
$Out = Join-Path $Work "out"
New-Item -ItemType Directory -Force -Path $Work, $Cache, $Vendor, $Out | Out-Null
# Windows' own tar (bsdtar, libarchive) by full path: it reads the zstd
# inside conda packages, and a Git or MSYS tar earlier on PATH may not.
$Tar = Join-Path $env:SystemRoot "System32\tar.exe"

# Each pin: file (the name in cache\), sha (SHA-256 of that file) and urls,
# tried in order.
$Pins = @{
    # Python 3.13, the full interpreter as a zip (tools\ inside): venv and pip
    # work, nothing is registered with Windows. NB's and DATV's pin.
    python   = @{ urls = @("https://api.nuget.org/v3-flatcontainer/python/3.13.16/python.3.13.16.nupkg")
                  file = "python.3.13.16.zip"
                  sha  = "95fad176338f1d6a799e45946488063f4aa61764e6cff2f132ddeffbf7a04c34" }
    # ADI's libiio 0.26 Windows binaries: the pure-Python iio.py, and
    # libiio.dll with the DLLs it links against, all shipped unmodified.
    # 0.26, not 1.0: the program is written against the 0.2x interface.
    libiio   = @{ urls = @("https://github.com/analogdevicesinc/libiio/releases/download/v0.26/Windows.zip")
                  file = "libiio-0.26-Windows.zip"
                  sha  = "4ad4a8c6b3f7145922c122dcfc51d693f0d3f9a0054fb2228814ce58c538a6ca" }
    # ADI's PlutoSDR/M2k USB driver package, carried unmodified and run by
    # our installer only when the drivers are not already in the driver
    # store (packaging\windows\pluto-drivers.iss). NB's pin.
    plutodrivers = @{ urls = @("https://github.com/analogdevicesinc/plutosdr-m2k-drivers-win/releases/download/v0.9/PlutoSDR-M2k-USB-Drivers.exe")
                  file = "PlutoSDR-M2k-USB-Drivers-0.9.exe"
                  sha  = "c53dac79be6af8e268b2b5460856d46501f3a61c7b820cbfc3fe24288960550d" }
    # The licence of each of those, from the exact tag the binaries were
    # built from (NB's pins; THIRD-PARTY.txt says which is which).
    libiio_copying = @{ urls = @("https://raw.githubusercontent.com/analogdevicesinc/libiio/v0.26/COPYING.txt")
                  file = "libiio-COPYING.txt"
                  sha  = "102900208eef27b766380135906d431dba87edaa7ec6aa72e6ebd3dd67f3a97b" }
    libusb_copying = @{ urls = @("https://raw.githubusercontent.com/libusb/libusb/v1.0.24/COPYING")
                  file = "libusb-COPYING"
                  sha  = "5df07007198989c622f5d41de8d703e7bef3d0e79d62e24332ee739a452af62a" }
    libserialport_copying = @{ urls = @("https://raw.githubusercontent.com/sigrokproject/libserialport/libserialport-0.1.1/COPYING")
                  file = "libserialport-COPYING"
                  sha  = "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768" }
    libxml2_copyright = @{ urls = @("https://raw.githubusercontent.com/GNOME/libxml2/v2.9.12/Copyright")
                  file = "libxml2-Copyright"
                  sha  = "c5c63674f8a83c4d2e385d96d1c670a03cb871ba2927755467017317878574bd" }
    plutodrivers_license = @{ urls = @("https://raw.githubusercontent.com/analogdevicesinc/plutosdr-m2k-drivers-win/v0.9/LICENSE")
                  file = "plutosdr-m2k-drivers-LICENSE"
                  sha  = "a914058ac8f4eb858e93c0050d29f1a95bb7a7abe6e3f4d4d1189b46daa7d888" }
    # FFmpeg 9.0.2, Gyan Doshi's "essentials" static build: DATV's pin, so
    # the two programs carry the same binary and one set of sources on the
    # source site covers both. It has everything the sources ask of it
    # (dshow, rawvideo, f32le, tcp, the scale/pad/fps filters) - checked
    # below. Static: no DLLs beside the .exes.
    ffmpeg   = @{ urls = @("https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.7z")
                  file = "ffmpeg-9.0.2-essentials_build.7z"
                  sha  = "4705843ccaaf54257c16ad90f3e952ece33c17df964ecf7bfdbb0f49c7171077" }
    # Its source, fetched (and so hash-checked) on every build, not shipped:
    # the pointer in THIRD-PARTY.txt is then known to be live and exact.
    ffmpeg_src = @{ urls = @("https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz")
                  file = "ffmpeg-9.0.2.tar.xz"
                  sha  = "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e" }
    # libhackrf for Windows. Great Scott Gadgets publish only source for
    # the host tools, and the build they point Windows users to is
    # radioconda's - which is conda-forge's package, taken here directly:
    # libhackrf0 (hackrf-0.dll), and the two DLLs it imports beyond Windows
    # and the C runtime, each from the package its recipe names (read from
    # the DLL's import table and the package's info/index.json, 08/10/2026):
    # libusb 1.0.29 and mingw-w64's winpthreads. Its other import,
    # VCRUNTIME140.dll, is Python's own and already in the process.
    libhackrf = @{ urls = @("https://api.anaconda.org/download/conda-forge/libhackrf0/2026.01.3/win-64/libhackrf0-2026.01.3-hfd05255_0.conda")
                  file = "libhackrf0-2026.01.3-hfd05255_0.conda"
                  sha  = "fd9a6a718cad7ff61f830e6c3789e26363d7f9e12399e25d9d5a68c06251f8f7" }
    conda_libusb = @{ urls = @("https://api.anaconda.org/download/conda-forge/libusb/1.0.29/win-64/libusb-1.0.29-h1839187_0.conda")
                  file = "libusb-1.0.29-h1839187_0.conda"
                  sha  = "9837f8e8de20b6c9c033561cd33b4554cd551b217e3b8d2862b353ed2c23d8b8" }
    conda_winpthread = @{ urls = @("https://api.anaconda.org/download/conda-forge/libwinpthread/12.0.0.r4.gg4f2fc60ca/win-64/libwinpthread-12.0.0.r4.gg4f2fc60ca-h57928b3_10.conda")
                  file = "libwinpthread-12.0.0.r4.gg4f2fc60ca-h57928b3_10.conda"
                  sha  = "0fccf2d17026255b6e10ace1f191d0a2a18f2d65088fd02430be17c701f8ffe0" }
    # libhackrf's source release, the one conda-forge's recipe builds
    # (with one patch that only renames the DLL); fetched as ffmpeg's is.
    hackrf_src = @{ urls = @("https://github.com/greatscottgadgets/hackrf/releases/download/v2026.01.3/hackrf-2026.01.3.tar.xz")
                  file = "hackrf-2026.01.3.tar.xz"
                  sha  = "d2b76a1115d9b4df648c29efb2f3c8e80009b7cf9a8adf37abbfdba72101b086" }
    # 7-Zip, unpacked with msiexec /a (an administrative image: files only,
    # nothing installed), to open the .7z archive FFmpeg comes in.
    sevenzip = @{ urls = @("https://www.7-zip.org/a/7z2301-x64.msi")
                  file = "7z2301-x64.msi"
                  sha  = "0ba639b6dacdf573d847c911bd147c6384381a54dac082b1e8c77bc73d58958b" }
    innosetup = @{ urls = @("https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe")
                  file = "innosetup-6.7.3.exe"
                  sha  = "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732" }
    # Signing tools (packaging\windows\sign.ps1), used on this machine and
    # never shipped: NB's pins, which NB's sign.ps1 notes proved on this VM.
    signtool = @{ urls = @("https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.buildtools/10.0.28000.2705/microsoft.windows.sdk.buildtools.10.0.28000.2705.nupkg")
                  file = "microsoft.windows.sdk.buildtools.10.0.28000.2705.zip"
                  sha  = "8bfdfb6ca2633f531cf80b5fa22512ba61a394d7988f0970db83baadc67929ed" }
    signclient = @{ urls = @("https://api.nuget.org/v3-flatcontainer/microsoft.artifactsigning.client/1.0.128/microsoft.artifactsigning.client.1.0.128.nupkg")
                  file = "microsoft.artifactsigning.client.1.0.128.zip"
                  sha  = "74bd7d27e6ce1051409c38d9b46bc8df0400ecd643d51ffbf2ac00869061e40b" }
    dotnet8 = @{ urls = @("https://builds.dotnet.microsoft.com/dotnet/Runtime/8.0.31/dotnet-runtime-8.0.31-win-x64.zip")
                  file = "dotnet-runtime-8.0.31-win-x64.zip"
                  sha  = "7deabbefdc83e378b343c551fe447b86002680eea91355b1f6f81d0c9ab0dcaa" }
}

function Step([string]$what) { Write-Host ""; Write-Host "== $what" }

function Fetch([string]$name) {
    $p = $Pins[$name]
    $dest = Join-Path $Cache $p.file
    if (Test-Path $dest) {
        if ((Get-FileHash $dest -Algorithm SHA256).Hash -eq $p.sha.ToUpper()) { return $dest }
        Remove-Item $dest
    }
    $why = @()
    foreach ($url in $p.urls) {
        Write-Host "   fetching $url"
        try {
            # A plain tool's user agent: SourceForge answers PowerShell's
            # default one with an HTML page instead of the file (NB's lesson).
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -UserAgent "Wget/1.21"
        } catch {
            $why += "$url : $($_.Exception.Message)"
            continue
        }
        $got = (Get-FileHash $dest -Algorithm SHA256).Hash
        if ($got -eq $p.sha.ToUpper()) { return $dest }
        Remove-Item $dest
        $why += "$url : SHA-256 $got, expected $($p.sha)"
    }
    throw "$($p.file) could not be fetched as pinned (upstream changed or moved it; check it and update the pin):`n$($why -join "`n")"
}

function Run([string]$exe) {
    # Native commands do not throw in 5.1; their exit code is the only signal.
    # stderr is not an error here (DATV's lesson: with the output redirected,
    # 5.1 turns each stderr line into an error record, and under "Stop" the
    # first of PyInstaller's INFO lines ended the build).
    $eap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $exe @args 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $eap }
    if ($LASTEXITCODE -ne 0) { throw "$exe $args exited $LASTEXITCODE" }
}

function SevenZip {
    $SevenDir = Join-Path $Work "7zip"
    $seven = Join-Path $SevenDir "Files\7-Zip\7z.exe"
    if (-not (Test-Path $seven)) {
        $msi = Fetch "sevenzip"
        $p = Start-Process msiexec.exe -ArgumentList "/a `"$msi`" /qn TARGETDIR=`"$SevenDir`"" -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "msiexec /a 7-Zip exited $($p.ExitCode)" }
    }
    return $seven
}

# ---------------------------------------------------------------- signing tools
# First, before anything slow: a build that is going to fail for want of a
# credential should say so in seconds, not after the test suite. NB's step.
$SignCfg = $null
$SignTool = $null
if ($NoSign) {
    Step "Signing: OFF (-NoSign). This build is UNSIGNED and must not be released."
} else {
    Step "Signing tools: signtool, the Artifact Signing dlib, .NET 8"
    foreach ($n in @("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "OPENHAMSHACK_SIGNING_ACCOUNT")) {
        # Present or not, never the value.
        if (-not [Environment]::GetEnvironmentVariable($n, "User")) {
            throw "$n is not set for this account, so nothing can be signed (packaging\windows\sign.ps1 says what it is). " +
                  "Use -NoSign for an unsigned local test build."
        }
    }
    $SignDir = Join-Path $Work "signing"
    foreach ($t in @(@("signtool", "sdk-buildtools"), @("signclient", "artifactsigning-client"), @("dotnet8", "dotnet"))) {
        $dest = Join-Path $SignDir $t[1]
        $stamp = Join-Path $dest ".from-$($Pins[$t[0]].file)"
        if (-not (Test-Path $stamp)) {
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
            Expand-Archive -Path (Fetch $t[0]) -DestinationPath $dest -Force
            Set-Content -Path $stamp -Value $Pins[$t[0]].sha
        }
    }
    $SignTool = Get-ChildItem (Join-Path $SignDir "sdk-buildtools\bin\*\x64\signtool.exe") | Select-Object -First 1 -ExpandProperty FullName
    $Dlib = Join-Path $SignDir "artifactsigning-client\bin\x64\Azure.CodeSigning.Dlib.dll"
    if (-not $SignTool -or -not (Test-Path $Dlib)) { throw "signtool.exe or Azure.CodeSigning.Dlib.dll is not where the packages put them" }
    # Only the environment's client secret may sign: every other credential
    # DefaultAzureCredential would try is off (NB's sign.ps1 says why).
    $meta = [ordered]@{
        Endpoint = "https://weu.codesigning.azure.net/"
        CodeSigningAccountName = [Environment]::GetEnvironmentVariable("OPENHAMSHACK_SIGNING_ACCOUNT", "User")
        CertificateProfileName = "Default"
        CorrelationId = "$Pkg-$Version"
        ExcludeCredentials = @("ManagedIdentityCredential", "WorkloadIdentityCredential", "SharedTokenCacheCredential",
                               "VisualStudioCredential", "VisualStudioCodeCredential", "AzureCliCredential",
                               "AzurePowerShellCredential", "AzureDeveloperCliCredential", "InteractiveBrowserCredential")
    }
    $Metadata = Join-Path $SignDir "metadata.json"
    [IO.File]::WriteAllText($Metadata, ($meta | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    $SignCfg = Join-Path $SignDir "signing.json"
    $cfg = [ordered]@{ SignTool = $SignTool; Dlib = $Dlib; Metadata = $Metadata
                       DotnetRoot = (Join-Path $SignDir "dotnet"); TimestampUrl = "http://timestamp.acs.microsoft.com" }
    [IO.File]::WriteAllText($SignCfg, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    Write-Host "   $SignTool"
    Write-Host "   $Dlib"
}
$SignPs1 = Join-Path $Repo "packaging\windows\sign.ps1"

function Sign([string[]]$paths) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SignPs1 -Config $SignCfg @paths
    if ($LASTEXITCODE -ne 0) { throw "signing $($paths -join ', ') failed; not building an unsigned installer" }
}

function AssertSigned([string]$path) {
    # Signed by 360 Technology, chaining to a root Windows trusts, and
    # timestamped (the certificate lives three days). NB's check.
    $out = (cmd /c "`"$SignTool`" verify /pa /v `"$path`" 2>&1") | Out-String
    $code = $LASTEXITCODE
    $s = Get-AuthenticodeSignature -LiteralPath $path
    $name = Split-Path -Leaf $path
    if ($code -ne 0 -or $s.Status -ne "Valid") {
        throw "$name does not verify (signtool verify exit $code, Authenticode $($s.Status)):`n$out"
    }
    if ($s.SignerCertificate.Subject -notmatch '(^|, )O=360 Technology(,|$)' -or $out -notmatch "Issued to: 360 Technology") {
        throw "$name is signed, but not by 360 Technology ($($s.SignerCertificate.Subject))"
    }
    if (-not $s.TimeStamperCertificate -or $out -notmatch "The signature is timestamped: (.+)") {
        throw "$name is signed without a timestamp; it would stop verifying when the 3-day certificate expires"
    }
    $when = $Matches[1].Trim()
    Write-Host "   $name : $($s.Status), signer $($s.SignerCertificate.Subject)"
    Write-Host "      timestamped $when by $($s.TimeStamperCertificate.Subject)"
}

# ---------------------------------------------------------------- Python
Step "Python"
$PyHome = Join-Path $Work "python"
$Py = Join-Path $PyHome "tools\python.exe"
if (-not (Test-Path $Py)) {
    $zip = Fetch "python"
    Expand-Archive -Path $zip -DestinationPath $PyHome -Force
}
$Venv = Join-Path $Work "venv"
$VPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VPy)) { Run $Py -m venv $Venv }
Run $VPy -m pip install --disable-pip-version-check --quiet -r (Join-Path $Repo "packaging\windows\requirements.txt")
Run $VPy -c "import sys, numpy, PyQt6.QtCore, pyaudio, paramiko, PIL; print('   python', sys.version.split()[0], 'numpy', numpy.__version__, 'Qt', PyQt6.QtCore.QT_VERSION_STR, 'paramiko', paramiko.__version__, 'Pillow', PIL.__version__)"
Write-Host "   pip freeze: $((& $VPy -m pip freeze --disable-pip-version-check) -join ', ')"

# The pinned shared OpenHamShack libraries, hash-checked against
# tools\vendor\requirements.lock and unpacked as plain packages into
# vendor\shack, the spec's pathex (packaging\shack_vendor.py says why
# unpacked rather than installed). Afresh every build: a new pin must never
# sit beside an old one.
$ShackDir = Join-Path $Vendor "shack"
if (Test-Path $ShackDir) { Remove-Item -Recurse -Force $ShackDir }
Run $VPy (Join-Path $Repo "packaging\shack_vendor.py") $ShackDir

$Lic = Join-Path $Vendor "licences"
if (Test-Path $Lic) { Remove-Item -Recurse -Force $Lic }
New-Item -ItemType Directory -Force -Path $Lic | Out-Null

# ---------------------------------------------------------------- libiio
Step "libiio 0.26: Python bindings and DLLs"
# Exactly as NB's and DATV's builds do (read NB's for why each step is so):
# iio.py onto the spec's pathex, and libiio.dll with exactly what it imports
# beyond Windows (libxml2, libusb-1.0, libserialport) in a folder of their
# own, _internal\libiio, which ohsatv.host.bundled_libiio() names.
$PyDir = Join-Path $Vendor "py"
$IioDir = Join-Path $Vendor "libiio"
$IioDlls = @("libiio.dll", "libxml2.dll", "libusb-1.0.dll", "libserialport-0.dll")
if (-not (Test-Path (Join-Path $PyDir "iio.py")) -or -not (Test-Path (Join-Path $IioDir "libiio.dll"))) {
    $zip = Fetch "libiio"
    $tmp = Join-Path $Work "tmp-libiio"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $vs = Join-Path $tmp "Windows-VS-2022-x64"
    Run $Tar -xzf (Join-Path $vs "libiio-py39-amd64.tar.gz") -C $tmp
    New-Item -ItemType Directory -Force -Path $PyDir, $IioDir | Out-Null
    Copy-Item (Join-Path $tmp "pylibiio-0.26\iio.py") $PyDir
    foreach ($dll in $IioDlls) { Copy-Item (Join-Path $vs $dll) $IioDir }
    Remove-Item -Recurse -Force $tmp
}
$oldPath = $env:PATH
$env:PATH = "$IioDir;$env:PATH"
try {
    Run $VPy -c "import sys; sys.path.insert(0, sys.argv[1]); import iio, os; d = os.path.dirname(iio._lib._name); print('   libiio', '.'.join(map(str, iio.version[:2])), iio.version[2], 'from', d); assert iio.version[:2] == (0, 26), iio.version; assert os.path.samefile(d, sys.argv[2]), d" $PyDir $IioDir
} finally { $env:PATH = $oldPath }
foreach ($l in @("libiio_copying", "libusb_copying", "libserialport_copying", "libxml2_copyright", "plutodrivers_license")) {
    Copy-Item (Fetch $l) (Join-Path $Lic $Pins[$l].file)
}

Step "PlutoSDR USB drivers (ADI v0.9, run by the installer)"
$PlutoDrivers = Fetch "plutodrivers"
Write-Host "   $PlutoDrivers"

# ---------------------------------------------------------------- ffmpeg
Step "FFmpeg 9.0.2 (gyan.dev essentials build, DATV's pin)"
# ffmpeg.exe and ffprobe.exe into vendor\ffmpeg -> _internal\ffmpeg, where
# ohsatv.host.find_tool() looks before PATH. Its LICENSE (GPL-3.0) and README
# (the build's configuration and every library's version, which
# THIRD-PARTY.txt points to) go into licences\.
$Ff = Join-Path $Vendor "ffmpeg"
$FfFiles = @("ffmpeg.exe", "ffprobe.exe")
$FfTop = "ffmpeg-9.0.2-essentials_build"
if (-not (Test-Path (Join-Path $Ff "ffprobe.exe")) -or -not (Test-Path (Join-Path $Vendor "ffmpeg-README.txt"))) {
    $Seven = SevenZip
    $arc = Fetch "ffmpeg"
    if (Test-Path $Ff) { Remove-Item -Recurse -Force $Ff }
    New-Item -ItemType Directory -Force -Path $Ff | Out-Null
    Run $Seven e -y "-o$Ff" $arc ($FfFiles | ForEach-Object { "$FfTop\bin\$_" })
    Run $Seven e -y "-o$Vendor" $arc "$FfTop\LICENSE" "$FfTop\README.txt"
    Move-Item -Force (Join-Path $Vendor "LICENSE") (Join-Path $Vendor "ffmpeg-LICENSE.txt")
    Move-Item -Force (Join-Path $Vendor "README.txt") (Join-Path $Vendor "ffmpeg-README.txt")
}
foreach ($f in $FfFiles) { if (-not (Test-Path (Join-Path $Ff $f))) { throw "the FFmpeg build had no bin\$f" } }
Copy-Item (Join-Path $Vendor "ffmpeg-LICENSE.txt") (Join-Path $Lic "ffmpeg-LICENSE.txt")
Copy-Item (Join-Path $Vendor "ffmpeg-README.txt") (Join-Path $Lic "ffmpeg-build-README.txt")
Write-Host "   source for THIRD-PARTY.txt's pointer: $(Fetch "ffmpeg_src")"
# What ohsatv/sources.py asks of it. A build that lacks one fails here, not
# when an operator first picks a webcam.
$FfExe = Join-Path $Ff "ffmpeg.exe"
$ver = (& $FfExe -hide_banner -version | Select-Object -First 1)
Write-Host "   $ver"
if ($ver -notmatch "ffmpeg version 9\.0\.2") { throw "bundled ffmpeg is not 9.0.2: $ver" }
$fmt = (& $FfExe -hide_banner -formats) -join "`n"
$filt = (& $FfExe -hide_banner -filters) -join "`n"
$dev = (& $FfExe -hide_banner -devices) -join "`n"
$proto = (& $FfExe -hide_banner -protocols) -join "`n"
foreach ($want in @(@($dev, "dshow"), @($fmt, "rawvideo"), @($fmt, "f32le"), @($proto, "tcp"), @($proto, "pipe"),
                    @($filt, " scale "), @($filt, " pad "), @($filt, " fps "), @($filt, " setsar "))) {
    if ($want[0] -notmatch [regex]::Escape($want[1])) { throw "bundled ffmpeg lacks $($want[1].Trim())" }
}
Write-Host "   has dshow, rawvideo, f32le, tcp, pipe, scale, pad, fps, setsar"

# ---------------------------------------------------------------- libhackrf
Step "libhackrf 2026.01.3 (conda-forge), with libusb 1.0.29 and winpthreads"
# Each .conda is a zip holding pkg-*.tar.zst (the files) and info-*.tar.zst
# (the licence texts and the recipe); Expand-Archive wants a .zip name, and
# Windows' tar reads the zstd. hackrf-0.dll, libusb-1.0.dll and
# libwinpthread-1.dll go into vendor\hackrf -> _internal\hackrf, side by
# side, where Windows finds hackrf-0.dll's imports first.
$Hk = Join-Path $Vendor "hackrf"
if (Test-Path $Hk) { Remove-Item -Recurse -Force $Hk }
New-Item -ItemType Directory -Force -Path $Hk | Out-Null
$CondaTmp = Join-Path $Work "tmp-conda"
function CondaPackage([string]$pin, [string]$dll, [hashtable]$licences) {
    $dir = Join-Path $CondaTmp $pin
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $zip = Join-Path $dir "pkg.zip"
    Copy-Item (Fetch $pin) $zip
    Expand-Archive -Path $zip -DestinationPath $dir -Force
    foreach ($t in Get-ChildItem $dir -Filter "*.tar.zst") { Run $Tar -xf $t.FullName -C $dir }
    $src = Join-Path $dir "Library\bin\$dll"
    if (-not (Test-Path $src)) { throw "$($Pins[$pin].file) has no Library\bin\$dll" }
    Copy-Item $src $Hk
    foreach ($k in $licences.Keys) {
        $l = Join-Path $dir "info\licenses\$k"
        if (-not (Test-Path $l)) { throw "$($Pins[$pin].file) has no info\licenses\$k" }
        Copy-Item $l (Join-Path $Lic $licences[$k])
    }
    Write-Host "   $dll  $((Get-FileHash $src -Algorithm SHA256).Hash.ToLower())"
}
CondaPackage "libhackrf" "hackrf-0.dll" @{ "COPYING" = "hackrf-COPYING" }
CondaPackage "conda_libusb" "libusb-1.0.dll" @{ "COPYING" = "libusb-1.0.29-COPYING" }
CondaPackage "conda_winpthread" "libwinpthread-1.dll" @{ "mingw-w64-libraries\winpthreads\COPYING" = "winpthreads-COPYING";
                                                          "COPYING.MinGW-w64\COPYING.MinGW-w64.txt" = "mingw-w64-COPYING.txt" }
Remove-Item -Recurse -Force $CondaTmp
Write-Host "   source for THIRD-PARTY.txt's pointer: $(Fetch "hackrf_src")"
# It must load from that folder alone, by full path as the program loads
# it, and initialise libusb; and it reports how many HackRFs this machine
# has (0 is the normal answer on a build machine). Nothing is opened.
Run $VPy -c "import ctypes, sys; lib = ctypes.CDLL(sys.argv[1]); assert lib.hackrf_init() == 0, 'hackrf_init failed'; lib.hackrf_device_list.restype = ctypes.POINTER(ctypes.c_int * 8); l = lib.hackrf_device_list(); print('   hackrf-0.dll loads from the bundle folder; HackRFs attached here:', l.contents[6] if l else '?'); lib.hackrf_exit()" (Join-Path $Hk "hackrf-0.dll")

# ---------------------------------------------------------------- Inno Setup
Step "Inno Setup"
$Iscc = $null
foreach ($c in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                 "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", (Join-Path $Work "innosetup\ISCC.exe"))) {
    if ($c -and (Test-Path $c)) { $Iscc = $c; break }
}
if (-not $Iscc) {
    # /PORTABLE=1: files only - no uninstaller, no registry, no Start menu.
    $is = Fetch "innosetup"
    $dir = Join-Path $Work "innosetup"
    $p = Start-Process $is -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /PORTABLE=1 /DIR=`"$dir`"" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Inno Setup unpack exited $($p.ExitCode)" }
    $Iscc = Join-Path $dir "ISCC.exe"
}
Write-Host "   $Iscc"

# ---------------------------------------------------------------- icon
Step "Icon"
$Ico = Join-Path $Vendor "$Pkg.ico"
Run $VPy (Join-Path $Repo "tools\make-ico.py") $Ico

# ---------------------------------------------------------------- tests
if (-not $NoTests) {
    Step "Test suite under Windows Python (tools\test-windows.ps1, module by module)"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "test-windows.ps1") -Work $Work
    if ($LASTEXITCODE -ne 0) { throw "$LASTEXITCODE test module(s) failed" }
}

# ---------------------------------------------------------------- freeze
Step "PyInstaller"
$env:OHSATV_VENDOR = $Vendor
$env:OHSATV_ICO = $Ico
$Dist = Join-Path $Work "dist"
Run $VPy -m PyInstaller --noconfirm --log-level WARN --distpath $Dist --workpath (Join-Path $Work "pyi-work") (Join-Path $Repo "packaging\windows\$Pkg.spec")
$Bundle = Join-Path $Dist $Pkg
$Internal = Join-Path $Bundle "_internal"
foreach ($f in @("ffmpeg\ffmpeg.exe", "ffmpeg\ffprobe.exe", "libiio\libiio.dll", "hackrf\hackrf-0.dll",
                 "hackrf\libusb-1.0.dll", "hackrf\libwinpthread-1.dll", "ohsatv\VERSION",
                 "packaging\openhamshack-atv.svg", "LICENSE", "THIRD-PARTY.txt",
                 "PIL\_imaging*.pyd", "licences\pillow-LICENSE", "shack_bandplan")) {
    if (-not (Test-Path (Join-Path $Internal $f))) { throw "the bundle has no _internal\$f" }
}

Step "No Qt PDF in the bundle"
# NB's check: bundle_prune.py takes Qt6Pdf.dll and the qpdf plugin out; an
# upgrade that brings them back by another route would oblige us to host
# Qt WebEngine's source, so look at what was actually collected.
$pdf = @(Get-ChildItem -Path $Bundle -Recurse -File | Where-Object { $_.Name -like "Qt6Pdf*.dll" -or $_.Name -ieq "qpdf.dll" })
if ($pdf.Count -gt 0) { throw "Qt PDF is in the bundle ($(($pdf | ForEach-Object { $_.FullName }) -join ', '))" }
Write-Host "   neither Qt6Pdf*.dll nor qpdf.dll in $Bundle"

if (-not $NoSign) {
    Step "Sign our own binaries: $Pkg.exe, $Pkg-console.exe"
    # Before the smoke tests, so the program they run is byte for byte the
    # one that ships. Only these two: the rest of the bundle is Python's,
    # Qt's, libiio's, FFmpeg's or libhackrf's, and keeps whatever signature
    # its publisher gave it.
    $ours = @((Join-Path $Bundle "$Pkg.exe"), (Join-Path $Bundle "$Pkg-console.exe"))
    Sign $ours
    foreach ($f in $ours) { AssertSigned $f }
}

# A console program's output, merged and as text. cmd merges stderr so
# PowerShell 5.1 does not turn each stderr line into an error record.
function Said([string]$exe, [string]$argline) {
    return ((cmd /c "`"$exe`" $argline 2>&1") | Out-String)
}
function Indent([string]$text) { return ($text.Trim() -replace '(?m)^', '   ') }

# Start the console .exe, and while it runs see which file a named DLL was
# loaded from (DATV's way: a DLL of that name elsewhere on the machine -
# System32, a radioconda - would otherwise pass the test for the wrong
# reason). Returns @(output, path loaded from).
function LoadedFrom([string]$exe, [string]$argline, [string]$dll) {
    $o = Join-Path $smoke "out.txt"
    $e = Join-Path $smoke "err.txt"
    $p = Start-Process $exe -ArgumentList $argline -PassThru -NoNewWindow -RedirectStandardOutput $o -RedirectStandardError $e
    $loaded = $null
    while (-not $p.HasExited -and -not $loaded) {
        try {
            $p.Refresh()            # Modules is cached from the first read without it
            $m = $p.Modules | Where-Object { $_.ModuleName -eq $dll } | Select-Object -First 1
            if ($m) { $loaded = $m.FileName }
        } catch { }
        Start-Sleep -Milliseconds 50
    }
    $p.WaitForExit()
    return @(((Get-Content $o -Raw) + (Get-Content $e -Raw)), $loaded)
}

Step "Smoke test: the frozen console build, headless, on the simulator"
# Its own config and data folders, so a developer's real settings are never
# read or written by the build.
$smoke = Join-Path $Work "smoke"
if (Test-Path $smoke) { Remove-Item -Recurse -Force $smoke }
New-Item -ItemType Directory -Force -Path $smoke | Out-Null
$env:OHSATV_CONF = Join-Path $smoke "config.json"
$env:OHSATV_DATA = Join-Path $smoke "data"
$Cli = Join-Path $Bundle "$Pkg-console.exe"
try {
    $said = Said $Cli "--headless --device sim --no-audio --seconds 5"
    Write-Host (Indent $said)
    $lines = @($said -split "`n" | Where-Object { $_ -match " MHz @" })
    if ($lines.Count -lt 3 -or $said -notmatch "(?m)^stopped") {
        throw "the frozen build did not run the simulator for 5 s (status lines: $($lines.Count))"
    }

    Step "Smoke test: the radio-lock folder check the installer runs"
    $said = Said $Cli "--radio-lock-folder"
    Write-Host (Indent $said)
    if ($said -notmatch "radio-lock folder: .* can be trusted|cannot be trusted") { throw "--radio-lock-folder said nothing useful" }

    Step "Smoke test: the bundled ffmpeg lists this machine's cameras"
    $said = Said (Join-Path $Internal "ffmpeg\ffmpeg.exe") "-hide_banner -list_devices true -f dshow -i dummy"
    Write-Host (Indent $said)

    Step "Smoke test: the frozen build loads its own libiio"
    # A Pluto at 192.0.2.1 (TEST-NET-1: never routed). "No Pluto at" can
    # only be said after iio.py loaded a libiio.dll and it failed to
    # connect; the module list says which libiio.dll it was.
    [IO.File]::WriteAllText($env:OHSATV_CONF, '{"device": "pluto", "plutoUri": "ip:192.0.2.1"}', [Text.Encoding]::ASCII)
    $r = LoadedFrom $Cli "--headless --no-audio --seconds 2" "libiio.dll"
    Write-Host (Indent ($r[0] -split "`n" | Select-Object -First 3 | Out-String))
    Write-Host "   libiio.dll loaded from: $($r[1])"
    if ($r[0] -notmatch "No Pluto at ip:192\.0\.2\.1" -or $r[0] -match "libiio could not|reinstall") {
        throw "the frozen build did not reach libiio:`n$($r[0])"
    }
    if (-not $r[1] -or -not $r[1].StartsWith((Join-Path $Internal "libiio"), [StringComparison]::OrdinalIgnoreCase)) {
        throw "the frozen build loaded libiio.dll from '$($r[1])', not from its bundle's _internal\libiio"
    }

    Step "Smoke test: the frozen build loads its own libhackrf"
    # The HackRF as the transmitter beside the simulator: it is opened at
    # start (never keyed - nothing here calls ptt). With no HackRF attached
    # the start says "No HackRF ... found", which needs hackrf-0.dll loaded
    # and initialised; "needs libhackrf" means it was not found. With one
    # attached it opens quiet (no gain, no amplifier, nothing streaming)
    # and the line is absent; the module list proves the DLL either way.
    [IO.File]::WriteAllText($env:OHSATV_CONF, '{"txDevice": "hackrf"}', [Text.Encoding]::ASCII)
    $r = LoadedFrom $Cli "--headless --device sim --no-audio --seconds 3" "hackrf-0.dll"
    Write-Host (Indent (($r[0] -split "`n" | Where-Object { $_ -match "HackRF|hackrf" }) | Out-String))
    Write-Host "   hackrf-0.dll loaded from: $($r[1])"
    if ($r[0] -match "needs libhackrf") { throw "the frozen build did not find its libhackrf:`n$($r[0])" }
    if (-not $r[1] -or -not $r[1].StartsWith((Join-Path $Internal "hackrf"), [StringComparison]::OrdinalIgnoreCase)) {
        throw "the frozen build loaded hackrf-0.dll from '$($r[1])', not from its bundle's _internal\hackrf"
    }
} finally {
    Remove-Item Env:OHSATV_CONF, Env:OHSATV_DATA
}

# ---------------------------------------------------------------- installer
Step "Installer"
$isccArgs = @("/Q", "/DAppVersion=$Version", "/DDistDir=$Bundle", "/DIconFile=$Ico", "/DRepo=$Repo", "/DOutDir=$Out", "/DPlutoDrivers=$PlutoDrivers")
if (-not $NoSign) {
    # Inno Setup signs the uninstaller it generates and then setup.exe by
    # running the "ohssign" tool defined here (NB's way; $q is Inno's quote
    # and $f the quoted file name, so no quotes pass through 5.1's argument
    # handling, which mangles them).
    $isccArgs += @("/DSign=1", "/Slbsign=powershell.exe -NoProfile -ExecutionPolicy Bypass -File `$q$SignPs1`$q -Config `$q$SignCfg`$q `$f")
}
$isccArgs += @(Join-Path $Repo "packaging\windows\$Pkg.iss")
Run $Iscc @isccArgs
$Setup = Join-Path $Out "$Pkg-$Version-setup.exe"
if (-not $NoSign) {
    Step "Installer signature"
    AssertSigned $Setup
}
$hash = (Get-FileHash $Setup -Algorithm SHA256).Hash.ToLower()
# Only now, with every signature in place: signing changes the bytes. The
# same "<hash>  <name>" line sha256sum writes for the .deb, ASCII, no BOM.
[IO.File]::WriteAllText("$Setup.sha256", "$hash  $Pkg-$Version-setup.exe`n", [Text.Encoding]::ASCII)
Write-Host ""
Write-Host "built $Setup ($([math]::Round((Get-Item $Setup).Length / 1MB, 1)) MB)"
Write-Host "sha256 $hash"
if ($NoSign) { Write-Host "UNSIGNED (-NoSign): for local testing only" }
else { Write-Host "signed by 360 Technology and timestamped" }
exit 0
