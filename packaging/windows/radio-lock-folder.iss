#ifndef LockCheckExe
  #define LockCheckExe "openhamshack-datv-console.exe"
#endif
[Files]
; LockCheckExe: the including installer #defines its own console exe
; before the #include (Desktop, NB, HAM, ATV); DATV's is the default, so
; this file is shared word for word and never conflicts on a merge.
; (The section header comes first: this file follows pluto-drivers.iss's
; [Code], where a ; line is not a comment.)
;
; The radio lock's folder step, #included by openhamshack-datv.iss after its own
; sections (beside pluto-drivers.iss, and shaped like it).
;
; Every OpenHamShack program on a PC shares %ProgramData%\openhamshack-radio for its
; radio-lock markers (docs/radio-lock.md). It must carry an explicit ACL -
; any user may create a marker, only its maker or an administrator may
; change or delete it, the Windows form of /run/lock's sticky bit - or one
; user can take a radio another is transmitting with. Left to ProgramData's
; inherited ACL, whoever makes the folder first owns it with Full Control:
; OpenHamShack Desktop's two-account test on VM 130 (6 October 2026) had a
; standard user who made it delete an administrator's marker. The program
; refuses to take any shared radio from a folder without the ACL, so this
; step is what makes the radio lock usable at all.
;
; The ACL itself is radio-lock-folder.cmd, installed beside the program so
; that the program's refusal can name it for an administrator to run, and
; so that this file and that one cannot disagree: this step runs that file
; and nothing else. NB, HAM, Desktop and ATV carry the same .cmd; it is
; idempotent, so whichever installer runs, in whatever order, the folder
; ends the same.
;
; Elevation, as for the Pluto drivers: our installer is per user (no UAC
; prompt to put a program in %LOCALAPPDATA%), and setting an owner of
; Administrators needs an administrator. So first the installed program
; itself is asked, unelevated and hidden, whether the folder is right
; (`{#LockCheckExe} --radio-lock-folder`, exit 0 = trusted - the
; very check the radio lock makes, rather than a second opinion written in
; Pascal). Only if it is not is the .cmd run with "runas": one UAC prompt,
; on the first OpenHamShack install on a PC, and never again once it is right.
; An all-users install that is already elevated runs it directly.
;
; Uninstalling never removes the folder: nothing here is in [Dirs] or
; [UninstallDelete], so Inno Setup does not know it exists. Another OpenHamShack
; program may be using it, and a marker left in it is somebody's live lock.
; The .cmd in {app} goes with the program, as everything in {app} does.

Source: "{#Repo}\packaging\windows\radio-lock-folder.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Code]
function RadioLockFolderTrusted(): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{app}\{#LockCheckExe}'), '--radio-lock-folder', '',
                 SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  Log('Radio lock folder: the program''s check exited ' + IntToStr(Code));
end;

procedure SetUpRadioLockFolder();
var
  Params, Why: String;
  Code: Integer;
  Ran: Boolean;
begin
  if RadioLockFolderTrusted() then
  begin
    Log('Radio lock folder: already has its ACL; nothing to do');
    exit;
  end;
  { cmd's /c "" ... "" form: the outer quotes are stripped, the inner ones
    keep a path with spaces in one piece. }
  Params := '/c ""' + ExpandConstant('{app}\radio-lock-folder.cmd') + '""';
  if not WizardSilent() then
    WizardForm.StatusLabel.Caption := 'Setting up the shared radio-lock folder...';
  Why := '';
  if IsAdmin() then
    Ran := Exec(ExpandConstant('{cmd}'), Params, '', SW_HIDE, ewWaitUntilTerminated, Code)
  else
    Ran := ShellExec('runas', ExpandConstant('{cmd}'), Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
  if Ran then
    Log('Radio lock folder: radio-lock-folder.cmd exited ' + IntToStr(Code))
  else
  begin
    { 1223 is ERROR_CANCELLED: the UAC prompt was answered No. }
    if Code = 1223 then
      Why := ' (administrator permission was not given)'
    else
      Why := ' (' + SysErrorMessage(Code) + ')';
    Log('Radio lock folder: radio-lock-folder.cmd did not start' + Why);
  end;
  if RadioLockFolderTrusted() then
    Log('Radio lock folder: set up')
  else
    SuppressibleMsgBox('The shared radio-lock folder was not set up' + Why + '.' + #13#10#13#10 +
      '{#SetupSetting("AppName")} is installed and runs on the simulator, but it will not take a radio or a ' +
      'PTT relay until the folder is set up, so that another account on this PC cannot take a radio away ' +
      'from it. Run this installer again and allow the administrator prompt, or have an administrator run:' + #13#10#13#10 +
      ExpandConstant('{app}\radio-lock-folder.cmd'),
      mbInformation, MB_OK, IDOK);
end;

<event('CurStepChanged')>
procedure RadioLockFolderCurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SetUpRadioLockFolder();
end;
