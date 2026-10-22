; Inno Setup script for openhamshack-atv-<version>-setup.exe. Run by
; tools/build-windows.ps1, which passes:
;   /DAppVersion=<ohsatv/VERSION>  /DDistDir=<PyInstaller's dist\openhamshack-atv>
;   /DIconFile=<openhamshack-atv.ico>  /DRepo=<repository root>  /DOutDir=<out\>
;   /DPlutoDrivers=<ADI's PlutoSDR-M2k-USB-Drivers.exe> (pluto-drivers.iss)
;   and, unless -NoSign, /DSign=1 with the "ohssign" tool (sign.ps1)
; Copied in shape from OpenHamShack NB's packaging/windows/openhamshack-nb.iss (the
; signing) and OpenHamShack DATV's openhamshack-datv.iss (the radio-lock folder).
;
; Installs per user by default (PrivilegesRequired=lowest): no UAC prompt,
; into %LOCALAPPDATA%\Programs\OpenHamShack ATV, which is enough for a program
; that writes nothing outside the operator's own profile and the radio
; lock's shared folder. The wizard (or /ALLUSERS on the command line) still
; offers Program Files for a shared machine. Two machine-wide things ask
; for administrator rights for themselves alone, each only when it is not
; already done: ADI's USB drivers for a Pluto (pluto-drivers.iss) and the
; radio lock's %ProgramData%\openhamshack-radio with its ACL
; (radio-lock-folder.iss), which every OpenHamShack program on the PC shares and
; no uninstaller removes. libiio 0.26, ffmpeg and libhackrf are inside the
; bundle, so nothing else needs installing. A HackRF needs no driver step:
; its firmware carries Microsoft's WCID descriptors, so Windows binds WinUSB
; to it by itself (HANDOVER.md, Windows: not yet seen with a real HackRF).
;
; Side by side with OpenHamShack NB, HAM and DATV: its own AppId, folder,
; Start-menu group, uninstaller entry and %APPDATA%\openhamshack-atv. Nothing here
; names or touches theirs; the PlutoSDR drivers and the radio-lock folder
; are the things they share, and each is found already done by whichever
; installer runs second.
;
; Uninstalling removes the program and its shortcuts but leaves
; %APPDATA%\openhamshack-atv - the operator's settings, recordings and transmit
; log - as the .deb leaves ~/.config and ~/.local/share: reinstalling must
; not cost them their station.
;
; No self-update step (NB's self-update.iss): OpenHamShack ATV has no updater yet
; (ohscore/update.py's design, docs/update-feed.md in NB, is still to be
; followed here). When it has, #include NB's file as NB does.

#ifndef AppVersion
  #error Pass /DAppVersion=<version> (tools/build-windows.ps1 does)
#endif

[Setup]
; Never change AppId: it is how a newer installer finds the old install to
; upgrade in place, and how Windows matches the uninstaller. Generated for
; OpenHamShack ATV on 08/10/2026; never NB's {5B0E6A3C-...}, HAM's {6CB153AC-...}
; or DATV's {FFAB800B-...}, or one would upgrade over the other.
AppId={{B92D9921-A099-4C9E-8A69-C2CDF6D681DB}
AppName=OpenHamShack ATV
AppVersion={#AppVersion}
AppVerName=OpenHamShack ATV {#AppVersion}
AppPublisher=360 Technology
AppPublisherURL=https://openhamshackatv.brian-henderson.co.uk/
AppComments=Analogue amateur-TV transceiver
DefaultDirName={autopf}\OpenHamShack ATV
DefaultGroupName=OpenHamShack ATV
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=openhamshack-atv-{#AppVersion}-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\openhamshack-atv.exe
UninstallDisplayName=OpenHamShack ATV {#AppVersion}
LicenseFile={#Repo}\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Close a running copy before replacing its files, rather than leaving a
; half-updated folder behind a locked DLL.
CloseApplications=yes
#ifdef Sign
; Signed as 360 Technology, as NB and HAM are: build-windows.ps1 defines
; Sign and the "ohssign" tool (packaging\windows\sign.ps1) unless run with
; -NoSign. Inno Setup then signs the uninstaller before packing it - so
; Windows names a publisher when it is run from Apps - and setup.exe itself
; once it is written. The program's own .exes arrive in DistDir already
; signed.
SignTool=ohssign
SignedUninstaller=yes
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An upgrade replaces the whole bundle: a DLL a newer build no longer ships
; must not linger and be picked up instead of the new one.
Type: filesandordirs; Name: "{app}\_internal"
; The shortcuts as named before the OpenHamShack rename (09/10/2026). An
; upgrade keeps the old Start-menu group (UsePreviousGroup) and adds the
; renamed shortcuts to it, so without these the menu would show both
; names. The install folder keeps its old name on purpose (DefaultDirName).
Type: files; Name: "{group}\OpenHamShack ATV.lnk"
Type: files; Name: "{group}\OpenHamShack ATV (console).lnk"
Type: files; Name: "{group}\Uninstall OpenHamShack ATV.lnk"
Type: files; Name: "{autodesktop}\OpenHamShack ATV.lnk"

[Icons]
Name: "{group}\OpenHamShack ATV"; Filename: "{app}\openhamshack-atv.exe"; Comment: "Analogue amateur-TV transceiver"
Name: "{group}\OpenHamShack ATV (console)"; Filename: "{app}\openhamshack-atv-console.exe"; Comment: "The same program with a console, for diagnosing start-up problems"
Name: "{group}\Uninstall OpenHamShack ATV"; Filename: "{uninstallexe}"
Name: "{autodesktop}\OpenHamShack ATV"; Filename: "{app}\openhamshack-atv.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\openhamshack-atv.exe"; Description: "{cm:LaunchProgram,OpenHamShack ATV}"; Flags: nowait postinstall skipifsilent

; The PlutoSDR USB drivers option: [Tasks], [Files] and [Code] of its own.
; Read it for how it elevates and how it knows the drivers are already there.
; The radio lock's shared folder and its ACL likewise (radio-lock-folder.iss).
; No comment line may come between the includes: after the first, a ; line
; would be inside its [Code], where ";" is Pascal, not a comment.
#include "pluto-drivers.iss"
#define LockCheckExe "openhamshack-atv-console.exe"
#include "radio-lock-folder.iss"
