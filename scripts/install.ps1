# Install the native pb CLI from GitHub releases.
#
#   irm https://github.com/VibeEngineeringAI/pb-rs/releases/latest/download/install.ps1 | iex
#
# Environment: PB_VERSION (e.g. v0.1.0-rc.1), PB_INSTALL_DIR,
# PB_NO_MODIFY_PATH=1 (legacy VERSION and INSTALL_DIR are honoured).
# The archive checksum is always verified before installation.

# A script block keeps preferences and variables out of the caller's session
# when the script is piped into Invoke-Expression.
& {
    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"
    Set-StrictMode -Version 2

    $Repo = "VibeEngineeringAI/pb-rs"
    $BinaryName = "pb"
    # Release builds rewrite this line so a pinned installer installs its own tag.
    $DefaultVersion = "latest"

    function Get-Setting([string[]]$Names, [string]$Default) {
        foreach ($Name in $Names) {
            $Value = [Environment]::GetEnvironmentVariable($Name)
            if ($Value) { return $Value }
        }
        return $Default
    }

    $GitHubUrl = (Get-Setting @("PB_INSTALL_GITHUB_URL") "https://github.com").TrimEnd("/")
    $Version = Get-Setting @("PB_VERSION", "VERSION") $DefaultVersion
    $DefaultInstallDir = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "Programs\pb\bin" } else { Join-Path $HOME ".local/bin" }
    $InstallDir = Get-Setting @("PB_INSTALL_DIR", "INSTALL_DIR") $DefaultInstallDir
    $ModifyPath = -not (Get-Setting @("PB_NO_MODIFY_PATH") "")

    if ($PSVersionTable.PSVersion.Major -lt 6) {
        # Windows PowerShell 5.1 may default to TLS 1.0, which GitHub rejects.
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    }

    function Get-Architecture {
        $Arch = $env:PROCESSOR_ARCHITECTURE
        if ($env:PROCESSOR_ARCHITEW6432) { $Arch = $env:PROCESSOR_ARCHITEW6432 }
        try { $Arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() } catch { }
        switch ($Arch.ToLowerInvariant()) {
            { $_ -in "x64", "amd64" } { return "amd64" }
            { $_ -in "arm64", "aarch64" } { return "arm64" }
            default { throw "Unsupported architecture: $Arch" }
        }
    }

    # Follow the /releases/latest redirect without the rate-limited API.
    # HttpWebRequest behaves identically on Windows PowerShell 5.1 and PowerShell 7.
    function Resolve-LatestVersion {
        $Url = "$GitHubUrl/$Repo/releases/latest"
        $Request = [System.Net.HttpWebRequest]::Create($Url)
        $Request.AllowAutoRedirect = $false
        $Request.Method = "HEAD"
        try {
            $Response = $Request.GetResponse()
        }
        catch [System.Net.WebException] {
            $Response = $_.Exception.Response
            if (-not $Response) { throw "Could not resolve the latest release from ${Url}: $($_.Exception.Message)" }
        }
        try { $Location = $Response.Headers["Location"] } finally { $Response.Close() }
        $Tag = if ($Location) { ($Location.TrimEnd("/") -split "/")[-1] } else { "" }
        if ($Tag -notmatch '^v\d') {
            throw "No stable release found at $Url; pin one with `$env:PB_VERSION = 'v0.1.0-rc.1'"
        }
        return $Tag
    }

    function Get-File([string]$Url, [string]$OutFile) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $OutFile
        }
        catch {
            throw "Download failed: $Url ($($_.Exception.Message))"
        }
    }

    function Test-UrlExists([string]$Url) {
        try {
            Invoke-WebRequest -UseBasicParsing -Method Head -Uri $Url | Out-Null
            return $true
        }
        catch {
            return $false
        }
    }

    function Add-UserPath([string]$Dir) {
        # Read the raw registry value so %VARIABLE% entries are not expanded away.
        # CreateSubKey opens the key writable, creating it on profiles without one.
        $Key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
        try {
            $Raw = [string]$Key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $Entries = @($Raw -split ";" | Where-Object { $_ })
            $Normalized = $Dir.TrimEnd("\")
            foreach ($Entry in $Entries) {
                if ([Environment]::ExpandEnvironmentVariables($Entry).TrimEnd("\") -ieq $Normalized) { return $false }
            }
            $Key.SetValue("Path", (($Entries + $Dir) -join ";"), [Microsoft.Win32.RegistryValueKind]::ExpandString)
        }
        finally {
            $Key.Close()
        }
        # Broadcast WM_SETTINGCHANGE so new terminals see the updated PATH.
        $Marker = "PB_INSTALL_PATH_REFRESH"
        [Environment]::SetEnvironmentVariable($Marker, "1", "User")
        [Environment]::SetEnvironmentVariable($Marker, $null, "User")
        return $true
    }

    if ($Version -eq "latest") {
        $Version = Resolve-LatestVersion
    }
    $Version = $Version.TrimStart("v")
    # Strict semver shape: the version becomes part of URLs and file names.
    if ($Version -notmatch '\A[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?\z') {
        throw "Invalid version: $Version (expected vMAJOR.MINOR.PATCH[-PRERELEASE])"
    }

    $BaseUrl = "$GitHubUrl/$Repo/releases/download/v$Version"
    $Arch = Get-Architecture
    $Asset = "${BinaryName}_${Version}_windows_${Arch}.zip"
    if ($Arch -eq "arm64" -and -not (Test-UrlExists "$BaseUrl/$Asset")) {
        # x64 binaries run under emulation on Windows on Arm.
        Write-Host "No native arm64 build for v$Version; installing the x64 build."
        $Arch = "amd64"
        $Asset = "${BinaryName}_${Version}_windows_${Arch}.zip"
    }
    $Checksums = "${BinaryName}_${Version}_checksums.txt"

    $TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("pb-install-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
    try {
        $AssetPath = Join-Path $TempDir $Asset
        $ChecksumsPath = Join-Path $TempDir $Checksums

        Write-Host "Downloading $BinaryName v$Version for windows/$Arch..."
        Get-File "$BaseUrl/$Checksums" $ChecksumsPath
        Get-File "$BaseUrl/$Asset" $AssetPath

        $Expected = $null
        foreach ($Line in Get-Content -Path $ChecksumsPath) {
            $Fields = $Line.Trim() -split "\s+"
            if ($Fields.Count -ge 2 -and $Fields[1].TrimStart("*") -eq $Asset) {
                $Expected = $Fields[0].ToLowerInvariant()
                break
            }
        }
        if (-not $Expected) {
            throw "Checksum for $Asset not found in $Checksums"
        }
        $Actual = (Get-FileHash -Algorithm SHA256 -Path $AssetPath).Hash.ToLowerInvariant()
        if ($Actual -ne $Expected) {
            throw "Checksum verification failed for $Asset (expected $Expected, got $Actual)"
        }
        Write-Host "Verified SHA-256 checksum."

        $ExtractDir = Join-Path $TempDir "extract"
        Expand-Archive -Path $AssetPath -DestinationPath $ExtractDir -Force
        $Source = Join-Path $ExtractDir "$BinaryName.exe"
        if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
            throw "$Asset does not contain $BinaryName.exe"
        }

        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        $Destination = Join-Path $InstallDir "$BinaryName.exe"
        try {
            Move-Item -Force -Path $Source -Destination $Destination
        }
        catch {
            throw "Could not replace $Destination; close any running $BinaryName and retry. ($($_.Exception.Message))"
        }
        Write-Host "$BinaryName v$Version installed to $Destination"

        $OnWindows = [System.IO.Path]::DirectorySeparatorChar -eq "\"
        $InSessionPath = @($env:PATH -split [System.IO.Path]::PathSeparator | Where-Object { $_.TrimEnd("\", "/") -ieq $InstallDir.TrimEnd("\", "/") }).Count -gt 0
        if ($ModifyPath -and $OnWindows) {
            if (Add-UserPath $InstallDir) {
                Write-Host "Added $InstallDir to your user PATH. Open a new terminal to use $BinaryName from any directory."
            }
            if (-not $InSessionPath) { $env:PATH = "$env:PATH;$InstallDir" }
        }
        elseif (-not $InSessionPath) {
            Write-Host "$InstallDir is not in your PATH. Add it to run $BinaryName from any directory."
        }
    }
    finally {
        Remove-Item -Recurse -Force -LiteralPath $TempDir -ErrorAction SilentlyContinue
    }
}
