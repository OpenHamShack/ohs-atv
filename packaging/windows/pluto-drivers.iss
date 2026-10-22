; The PlutoSDR USB drivers step, #included by openhamshack-atv.iss after its own
; sections. tools/build-windows.ps1 passes
; /DPlutoDrivers=<cache>\PlutoSDR-M2k-USB-Drivers-0.9.exe: ADI's own driver
; installer, carried unmodified and run as it is.
;
; Copied from OpenHamShack DATV (~/openhamshack-datv/packaging/windows/pluto-drivers.iss,
; d3c62d6), itself OpenHamShack NB's (GPLv3), where it was proven on the test VM;
; the only change is this paragraph. Its message names the program through
; SetupSetting("AppName"), so it needed nothing else. Keep the copies in
; step: a fix found in one belongs in the others. The detection matters more
; than usual here - NB, HAM, DATV and ATV may all be installed on one PC, and
; only the first of them should ever run ADI's installer.
;
; Why run ADI's installer rather than unpack its INFs and call pnputil: the
; package is ADI's, signed by ADI, with DPInst and its co-installers; their
; installer is the thing ADI tests and documents, it registers its own
; uninstaller under Apps, and a Pluto user who meets it elsewhere sees the
; same thing. It is itself an Inno Setup installer (6.0), so it takes
; /VERYSILENT /SUPPRESSMSGBOXES /NORESTART, and its one [Run] entry is
;   {app}\dpinst.exe /PATH "{app}" {param:dpinstflags|/F}
; (read from its setup data), so /dpinstflags="/F /SW /SE" replaces ADI's
; DPInst wizard with nothing: /F force (ADI's own default), /SW no wizard
; pages, /SE no EULA page. Not /S, DPInst's fully quiet mode: that also
; suppresses Windows' own trust question below, and with it unanswered
; every package fails with 0xE0000242 (publisher not trusted) - tried on
; the test VM, 05/10/2026, where the drivers then simply did not install.
;
; Elevation. Our installer stays per user (PrivilegesRequired=lowest: no
; UAC prompt to put a program in %LOCALAPPDATA%), but drivers are
; machine-wide and need an administrator. So the driver step alone is
; elevated, by ShellExec with the "runas" verb: Windows shows its UAC prompt
; for ADI's installer, and nothing else in the install changes. Making the
; whole installer admin-only for this was rejected - it would put every
; install in Program Files and prompt even on machines that already have
; the drivers. If Setup is already elevated (an all-users install), "runas"
; starts it without a second prompt.
;
; Windows then asks "Would you like to install this device software?"
; (publisher Analog Devices International UC), unless ADI is already a
; trusted publisher on the machine: the catalogs are signed by ADI with an
; ordinary code-signing certificate, not through Microsoft's hardware
; programme, and the question is Windows' own. It must be answered
; Install; the user guide must say so. (Adding ADI's
; certificate to the machine's TrustedPublisher store first would make the
; step silent, but that is a trust decision for the operator, not for an
; installer to take on their behalf.) In a session with no desktop - an
; unattended install over SSH - the question cannot be shown and the
; drivers fail the same way as under /S; the check afterwards reports it.
;
; Whether the drivers are already there. The answer comes from the driver
; store, which is what Windows matches a newly plugged-in Pluto against:
; each staged package lives in
;   %SystemRoot%\System32\DriverStore\FileRepository\<inf>_<arch>_<hash>\
; a layout unchanged since Vista and readable without administrator rights,
; so the check runs before any elevation. Both of the INFs a Pluto uses
; must be there: pluto-rndis.inf (the USB network adapter that puts it at
; ip:192.168.2.1) and pluto-usbd.inf (WinUSB, for libiio's usb: URIs).
; Rejected: ADI's uninstall registry key - it outlives drivers deleted in
; Device Manager, and is absent when the drivers came some other way;
; `pnputil /enum-drivers` - its output is translated into the Windows
; display language. Note the names are matched in full: a bare "pluto*"
; also matches Microsoft's own Pluton security processor drivers
; (pluton-heci.inf), which is on many Windows 11 PCs - seen on the test VM.
;
; When present, the option is not offered at all, and the setup log
; (/LOG) says why. Afterwards the store is checked again, so the operator
; is told whether the drivers really arrived rather than trusting an exit
; code: ADI's installer does not pass DPInst's result on.

#ifndef PlutoDrivers
  #error Pass /DPlutoDrivers=<path to PlutoSDR-M2k-USB-Drivers.exe> (tools/build-windows.ps1 does)
#endif

[Tasks]
Name: "plutodrivers"; Description: "Install the PlutoSDR USB drivers (Analog Devices). Windows asks for administrator permission, then whether to install the device software: answer Install."; GroupDescription: "ADALM-Pluto and Pluto family:"; Check: not PlutoDriversInstalled

[Files]
; Into Setup's own temporary folder, removed when Setup ends, and only when
; the option is ticked: nothing of ADI's stays in the program's folder.
Source: "{#PlutoDrivers}"; DestDir: "{tmp}"; DestName: "PlutoSDR-M2k-USB-Drivers.exe"; Tasks: plutodrivers

[UninstallDelete]
; ADI's installer writes its log here (below); the drivers themselves have
; their own uninstaller under Apps and stay when OpenHamShack is removed - other
; Pluto software may be using them.
Type: files; Name: "{app}\plutosdr-drivers-setup.log"

[Code]
var
  PlutoChecked, PlutoPresent: Boolean;

function PlutoInDriverStore(): Boolean;
var
  Store: String;
  F: TFindRec;
  Rndis, Usbd: Boolean;
begin
  Store := ExpandConstant('{sys}\DriverStore\FileRepository\');
  Rndis := FindFirst(Store + 'pluto-rndis.inf_*', F);
  if Rndis then
  begin
    Log('PlutoSDR USB drivers: found ' + F.Name);
    FindClose(F);
  end;
  Usbd := FindFirst(Store + 'pluto-usbd.inf_*', F);
  if Usbd then
  begin
    Log('PlutoSDR USB drivers: found ' + F.Name);
    FindClose(F);
  end;
  Result := Rndis and Usbd;
end;

{ The [Tasks] Check: asked more than once while the wizard runs, so the
  store is read once and the answer kept. }
function PlutoDriversInstalled(): Boolean;
begin
  if not PlutoChecked then
  begin
    PlutoPresent := PlutoInDriverStore();
    PlutoChecked := True;
    if PlutoPresent then
      Log('PlutoSDR USB drivers: already installed; the option to install them is not offered')
    else
      Log('PlutoSDR USB drivers: not installed; offering to install them');
  end;
  Result := PlutoPresent;
end;

procedure InstallPlutoDrivers();
var
  Exe, Params, Why: String;
  Code: Integer;
begin
  Exe := ExpandConstant('{tmp}\PlutoSDR-M2k-USB-Drivers.exe');
  Params := '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG="' +
            ExpandConstant('{app}\plutosdr-drivers-setup.log') + '" /dpinstflags="/F /SW /SE"';
  if not WizardSilent() then
    WizardForm.StatusLabel.Caption := 'Installing the PlutoSDR USB drivers (Analog Devices)...';
  Log('PlutoSDR USB drivers: running ' + Exe + ' ' + Params);
  Why := '';
  if ShellExec('runas', Exe, Params, '', SW_SHOWNORMAL, ewWaitUntilTerminated, Code) then
    Log('PlutoSDR USB drivers: ADI''s installer exited ' + IntToStr(Code))
  else
  begin
    { 1223 is ERROR_CANCELLED: the UAC prompt was answered No. }
    if Code = 1223 then
      Why := ' (administrator permission was not given)'
    else
      Why := ' (' + SysErrorMessage(Code) + ')';
    Log('PlutoSDR USB drivers: ADI''s installer did not start' + Why);
  end;
  if PlutoInDriverStore() then
    Log('PlutoSDR USB drivers: installed')
  else
  begin
    Log('PlutoSDR USB drivers: still not in the driver store');
    { With Why empty ADI's installer ran: the usual reason is Windows'
      device-software question answered Don't install (or never shown). }
    if Why = '' then
      Why := ' (Windows needs "Install" answered when it asks about device software from Analog Devices)';
    SuppressibleMsgBox('The PlutoSDR USB drivers were not installed' + Why + '.' + #13#10#13#10 +
      '{#SetupSetting("AppName")} itself is installed and runs on the simulator. A Pluto on USB needs ' +
      'the drivers: run this installer again with "Install the PlutoSDR USB drivers" ticked, or ' +
      'install Analog Devices'' PlutoSDR-M2k-USB-Drivers.exe yourself.',
      mbInformation, MB_OK, IDOK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('plutodrivers') then
    InstallPlutoDrivers();
end;
