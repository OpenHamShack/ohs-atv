@echo off
rem Gives the radio lock's folder, %ProgramData%\openhamshack-radio, the ACL that
rem makes it behave like Linux's /run/lock (root's, mode 1777, sticky): any
rem user may create a marker in it, only the marker's maker (or an
rem administrator) may change or delete it. docs/radio-lock.md, "The folder
rem must be the system's". Run elevated: the installer does (one UAC
rem prompt, only when the program's own check says the folder is wrong),
rem and the program's refusal names this file for an administrator to run.
rem
rem Why: ProgramData's inherited ACL gives a subfolder's creator Full
rem Control through CREATOR OWNER. OpenHamShack Desktop showed on VM 130
rem (6 October 2026) that a standard user who created the folder could then
rem delete an administrator's marker. Made here instead, the result is
rem   O:BA  D:P (A;OICI;FA;;;SY) (A;OICI;FA;;;BA)
rem           (A;;0x1200ab;;;BU)    Users, the folder only: list, traverse,
rem                                 read attributes and EAs, create files
rem           (A;OIIO;FR;;;BU)      Users, files only: read anyone's marker
rem           (A;OIIO;FA;;;CO)      the maker of each file: Full Control of it
rem with no delete-child, delete, change-permissions or take-ownership for
rem Users on the folder. Deleting one's own marker needs DELETE on the file,
rem which CREATOR OWNER gives, and not delete-child on the folder.
rem
rem Shared: every OpenHamShack installer (DATV, NB, HAM, Desktop, ATV) carries the
rem same lines, and running them again changes nothing. No installer's
rem uninstaller removes the folder - another OpenHamShack program may be using it.
rem
rem Owner first, so that between these lines nobody but an administrator
rem can change the ACL. /reset then drops any explicit entries a previous
rem ACL left (a user who owned the folder could have added some) before the
rem protected list is written. Never /T: the markers inside keep their own
rem owners and entries. A folder that is a junction is removed (rmdir takes
rem the link, not its target) and made again, so an elevated icacls is never
rem pointed at a folder somebody else chose; /L as well, for the same reason.
setlocal
set "D=%ProgramData%\openhamshack-radio"
dir /b /al "%ProgramData%" 2>nul | findstr /x /i "openhamshack-radio" >nul && rmdir "%D%"
if not exist "%D%\" mkdir "%D%" || exit /b 1
icacls "%D%" /L /setowner *S-1-5-32-544 || exit /b 1
icacls "%D%" /L /reset || exit /b 1
icacls "%D%" /L /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F || exit /b 1
icacls "%D%" /L /grant *S-1-5-32-545:(RD,WD,X,RA,REA,RC,S) *S-1-5-32-545:(OI)(IO)R *S-1-3-0:(OI)(IO)F || exit /b 1
exit /b 0
