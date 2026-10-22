"""Whether the radio lock trusts this computer's lock folder
(ohsatv/lockacl.py, docs/radio-lock.md "The folder must be the system's").

The Windows descriptors are the ones VM 130 really produced on 6 October
2026 (read back with Get-Acl after each step), not ones written to fit the
code: the folder made by packaging/windows/radio-lock-folder.cmd, a folder a
standard user made under ProgramData's inherited ACL (OpenHamShack Desktop's
finding), and one an elevated administrator made under the same inherited
ACL. The judging is pure, so it runs on Linux; reading a real descriptor is
proven on VM 130 and by the Windows run of this suite.

Copied from openhamshack-datv d3c62d6 (tests/test_lockacl.py) with the module
(ohsatv/lockacl.py): the same rules, so the same cases. Changed: the
package, the program's name in the repair sentence, and the temporary
directory taken from helpers.tmp_dir (tests/test_tmp_hygiene.py)."""
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from helpers import tmp_dir
from ohsatv import host, lockacl

USER_SID = "S-1-5-21-161507095-2074863075-2215977629-1002"

# radio-lock-folder.cmd's result (O:BA, protected, Users create-only).
INSTALLER = ("O:BAG:S-1-5-21-161507095-2074863075-2215977629-513D:PAI(A;OIIO;FA;;;CO)(A;OICI;FA;;;SY)"
             "(A;OICI;FA;;;BA)(A;OIIO;FR;;;BU)(A;;0x1200ab;;;BU)")
# A standard user's os.mkdir under C:\ProgramData: owner the user, and
# CREATOR OWNER's inherited Full Control turned into the user's own.
USER_MADE = (f"O:{USER_SID}D:AI(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)(A;ID;FA;;;{USER_SID})"
             "(A;OICIIOID;GA;;;CO)(A;OICIID;0x1200a9;;;BU)(A;CIID;DCLCRPCR;;;BU)")
# An elevated administrator's mkdir under C:\ProgramData: owner
# Administrators, so CREATOR OWNER became nothing on the folder itself.
ADMIN_MADE_INHERITED = ("O:BAG:S-1-5-21-161507095-2074863075-2215977629-513D:AI(A;OICIID;FA;;;SY)"
                        "(A;OICIID;FA;;;BA)(A;OICIIOID;GA;;;CO)(A;OICIID;0x1200a9;;;BU)(A;CIID;DCLCRPCR;;;BU)")


class Windows(unittest.TestCase):
    def test_the_installers_folder_is_trusted(self):
        self.assertEqual(lockacl.judge_sddl(INSTALLER), [])

    def test_a_folder_a_standard_user_made_is_refused_naming_the_owner_and_what_they_can_do(self):
        why = lockacl.judge_sddl(USER_MADE, lookup=lambda sid: "LAB\\lbatvlk" if sid == USER_SID else "")
        self.assertEqual(why[0], "it is owned by LAB\\lbatvlk, not Administrators or SYSTEM, so that account "
                                 "can rewrite its permissions")
        self.assertEqual(why[1], "LAB\\lbatvlk may delete anyone's file in it, delete or rename the folder, "
                                 "change its permissions and take ownership")
        self.assertEqual(len(why), 2)

    def test_an_administrators_folder_under_the_inherited_defaults_is_trusted(self):
        # Desktop's test: when an administrator made the folder, the
        # standard user was refused. The inherited ACL then holds, and the
        # check reads the effect, not who ran which installer.
        self.assertEqual(lockacl.judge_sddl(ADMIN_MADE_INHERITED), [])

    def test_everyone_full_control_is_refused_on_the_folder_and_on_the_markers(self):
        why = lockacl.judge_sddl("O:BAD:PAI(A;OICI;FA;;;WD)(A;OICI;FA;;;BA)")
        self.assertEqual(why, [
            "Everyone may delete anyone's file in it, delete or rename the folder, change its permissions "
            "and take ownership",
            "Everyone may delete, change the permissions of, take ownership of, rewrite and touch other "
            "accounts' markers"])

    def test_each_dangerous_right_alone_is_refused(self):
        for mask, said in (("0x40", "delete anyone's file in it"), ("SD", "delete or rename the folder"),
                           ("WD", "change its permissions"), ("WO", "take ownership"),
                           ("GA", "delete anyone's file in it")):
            with self.subTest(mask=mask):
                why = lockacl.judge_sddl(f"O:BAD:PAI(A;OICI;FA;;;BA)(A;;{mask};;;BU)")
                self.assertEqual(len(why), 1, why)
                self.assertIn(f"Users may {said}", why[0])

    def test_users_given_delete_on_the_files_is_refused(self):
        why = lockacl.judge_sddl("O:BAD:PAI(A;OICI;FA;;;BA)(A;;0x1200ab;;;BU)(A;OIIO;0x130089;;;AU)")
        self.assertEqual(why, ["Authenticated Users may delete other accounts' markers"])

    def test_creator_owner_and_owner_rights_may_have_everything_on_the_files(self):
        self.assertEqual(lockacl.judge_sddl("O:SYD:PAI(A;OICI;FA;;;SY)(A;OIIO;FA;;;CO)(A;OIIO;FA;;;OW)"
                                            "(A;;0x1200ab;;;BU)"), [])

    def test_owner_must_be_administrators_or_system(self):
        self.assertEqual(lockacl.judge_sddl("O:SYD:PAI(A;OICI;FA;;;SY)"), [])
        why = lockacl.judge_sddl("O:BUD:PAI(A;OICI;FA;;;SY)")
        self.assertEqual(why, ["it is owned by Users, not Administrators or SYSTEM, so that account can "
                               "rewrite its permissions"])

    def test_no_dacl_at_all_is_refused(self):
        self.assertIn("it has no access list at all, so everyone may do anything in it",
                      lockacl.judge_sddl("O:BAD:NO_ACCESS_CONTROL"))
        self.assertIn("it has no access list at all, so everyone may do anything in it",
                      lockacl.judge_sddl("O:BAG:SY"))

    def test_deny_entries_neither_help_nor_hurt(self):
        # A deny cannot make a bad folder good in this check (it is not
        # weighed), and a deny alone is not a grant.
        self.assertEqual(lockacl.judge_sddl("O:BAD:PAI(D;;FA;;;WD)(A;OICI;FA;;;BA)"), [])
        self.assertNotEqual(lockacl.judge_sddl("O:BAD:PAI(D;;0x40;;;BU)(A;;FA;;;BU)"), [])

    def test_trustedinstaller_and_system_are_trusted(self):
        self.assertEqual(lockacl.judge_sddl(f"O:BAD:PAI(A;OICI;FA;;;{lockacl.TRUSTED_INSTALLER})"
                                            "(A;OICI;FA;;;SY)"), [])

    def test_malformed_sddl_is_an_error_not_a_pass(self):
        with self.assertRaises(ValueError):
            lockacl.judge_sddl("O:BAD:PAI(A;OICI;QQ;;;BU)")


class Posix(unittest.TestCase):
    def test_run_lock_as_debian_makes_it(self):
        self.assertEqual(lockacl.judge_posix(0, stat.S_IFDIR | 0o1777), [])
        self.assertEqual(lockacl.judge_posix(0, stat.S_IFDIR | 0o755), [])

    def test_world_writable_without_the_sticky_bit_is_refused(self):
        self.assertEqual(lockacl.judge_posix(0, stat.S_IFDIR | 0o777),
                         ["it is writable by others without the sticky bit (mode 0777)"])

    def test_a_users_directory_is_refused(self):
        self.assertEqual(lockacl.judge_posix(1000, stat.S_IFDIR | 0o1777), ["it is owned by uid 1000, not root"])

    @unittest.skipIf(sys.platform == "win32", "POSIX ownership")
    def test_a_real_directory_of_ours_is_refused_and_a_missing_one_said(self):
        d = tmp_dir(self, "ohsatv-acl-")
        os.chmod(d, 0o1777)
        why = lockacl.folder_problems(d)
        if os.geteuid() != 0:
            self.assertEqual(why, [f"it is owned by uid {os.geteuid()}, not root"])
        self.assertEqual(lockacl.folder_problems(d / "nope"), ["it does not exist"])
        link = d / "link"
        link.symlink_to(d)
        self.assertEqual(lockacl.folder_problems(link), ["it is a link to another folder, not a folder"])


class Sentence(unittest.TestCase):
    def test_trusted_says_nothing(self):
        self.assertEqual(host.lock_dir_problem(Path("/x"), problems=lambda p: []), "")

    def test_linux_sentence_names_the_folder_the_reasons_the_consequence_and_the_fix(self):
        with mock.patch.object(host, "WINDOWS", False):
            s = host.lock_dir_problem(Path("/run/lock"), problems=lambda p: ["a", "b"])
        self.assertTrue(s.startswith(f"The radio lock's folder {Path('/run/lock')} cannot be trusted: a; b. "), s)
        self.assertIn("takes no shared radio", s)
        self.assertIn("sudo chown root:root /run/lock && sudo chmod 1777 /run/lock", s)

    def test_windows_sentence_names_the_installer_and_the_script(self):
        with mock.patch.object(host, "WINDOWS", True):
            s = host.lock_dir_problem(Path(r"C:\ProgramData\openhamshack-radio"), problems=lambda p: ["it does not exist"])
        script = host.lock_repair_script()
        self.assertIsNotNone(script)       # the repository's copy, running from source
        self.assertEqual(script.name, "radio-lock-folder.cmd")
        self.assertIn("Repair it by running the OpenHamShack ATV installer again, or from an administrator command "
                      f'prompt: "{script}"', s)


if __name__ == "__main__":
    unittest.main()
