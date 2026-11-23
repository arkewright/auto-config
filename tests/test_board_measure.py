import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "scripts/target/run"
BOOT_IDS = [f"12345678-1234-4abc-8123-{i:012x}" for i in range(6)]
TRACE = "KCFG-TRACE\t1\t" + "a" * 64 + "\n1\t1\t0\t0\nEND\t1\n"
PROFILE_START = ["readiness", "modprobe kcfg_collect", "start"]

FAKE_COMMANDS = r'''#!/bin/bash
set -euo pipefail
log() { printf '%s\n' "$*" >> "$CALLS"; }
case ${0##*/} in
    uname) [[ $* == '-r' ]]; printf '%s\n' "$RUNNING" ;;
    systemctl)
        [[ $* == 'show -p ActiveState -p ActiveEnterTimestampMonotonic multi-user.target' ]]
        log readiness
        printf 'ActiveEnterTimestampMonotonic=%s\nActiveState=%s\n' "$READY_US" "${ACTIVE_STATE:-active}"
        ;;
    sudo)
        case $1 in
            modprobe)
                [[ $# == 3 && $2 == -- ]]
                log "modprobe $3"
                mkdir -p -- "$KCFG_RUN_SYSTEM_ROOT/sys/module/${3//-/_}"
                ;;
            dd)
                [[ $* == 'dd of=/dev/kconfig-trace conv=nocreat,notrunc status=none' ]]
                IFS= read -r action
                log "$action"
                case $action in
                    start)
                        boot=$(<"$KCFG_RUN_SYSTEM_ROOT/proc/sys/kernel/random/boot_id")
                        mkdir -- "$KCFG_RUN_SYSTEM_ROOT/capture-$boot"
                        ;;
                    stop) [[ ${STOP_FAIL:-0} == 0 ]] ;;
                    *) exit 99 ;;
                esac
                ;;
            cat)
                [[ $* == 'cat -- /dev/kconfig-trace' ]]
                log snapshot
                printf '%s' "$TRACE"
                [[ ${READ_FAIL:-0} == 0 ]]
                ;;
            *) exit 99 ;;
        esac
        ;;
    workload)
        [[ $PWD == "$WORKING_DIR" && $EUID == "$WORKLOAD_UID" ]]
        log "workload $# $*"
        exit "${WORKLOAD_FAIL:-0}"
        ;;
    *) exit 99 ;;
esac
'''


class BoardRunnerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        commands = self.root / "bin"
        commands.mkdir()
        command = commands / "fake"
        command.write_text(FAKE_COMMANDS)
        command.chmod(0o755)
        for name in ("uname", "systemctl", "sudo", "workload"):
            (commands / name).symlink_to(command)
        self.calls = self.root / "calls"
        self.boot_file = self.root / "proc/sys/kernel/random/boot_id"
        self.boot_file.parent.mkdir(parents=True)
        self.boot_file.write_text(BOOT_IDS[0] + "\n")
        self.env = dict(os.environ, PATH=f"{commands}:{os.defpath}", CALLS=str(self.calls),
                        KCFG_RUN_SYSTEM_ROOT=str(self.root), TRACE=TRACE, READY_US="1234567",
                        WORKING_DIR=str(self.root), WORKLOAD_UID=str(os.geteuid()))

    def bundle(self, kind, modules=""):
        bundle = self.root / kind
        bundle.mkdir()
        shutil.copy2(RUN, bundle / "run")
        (bundle / "kind").write_text(f"{kind}\n")
        (bundle / "release").write_text(f"6.12-{kind}\n")
        (bundle / "modules-load.late").write_text(modules)
        return bundle

    def run_bundle(self, bundle, **env):
        return subprocess.run([bundle / "run", "workload", "two words"], cwd=self.root,
                              env={**self.env, "RUNNING": f"6.12-{bundle.name}", **env},
                              capture_output=True, text=True, check=False)

    def test_baseline_records_successes_once_per_boot_and_preserves_prior_rows(self):
        baseline = self.bundle("baseline")
        output = baseline / "boot.tsv"
        result = self.run_bundle(baseline)
        self.assertEqual(result.returncode, 0, result.stderr)
        first = f"baseline\t6.12-baseline\t{BOOT_IDS[0]}\t1234567\n"
        self.assertEqual(output.read_text(), first)
        result = self.run_bundle(baseline)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already recorded", result.stderr)
        self.assertEqual(self.calls.read_text().splitlines(), ["readiness", "workload 1 two words"])
        self.boot_file.write_text(BOOT_IDS[1] + "\n")
        result = self.run_bundle(baseline, WORKLOAD_FAIL="17")
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(output.read_text(), first)
        result = self.run_bundle(baseline, READY_US="2222222")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(), first + f"baseline\t6.12-baseline\t{BOOT_IDS[1]}\t2222222\n")
        self.assertFalse((baseline / "trace.tsv").exists())

    def test_mismatched_release_or_unready_system_never_runs_workload(self):
        baseline = self.bundle("baseline")
        for env, message in (({"RUNNING": "6.12-profile"}, "release mismatch"),
                             ({"ACTIVE_STATE": "inactive"}, "not active"),
                             ({"READY_US": "0"}, "invalid multi-user.target")):
            with self.subTest(env=env):
                result = self.run_bundle(baseline, **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        self.assertEqual(self.calls.read_text().splitlines(), ["readiness", "readiness"])
        self.assertFalse((baseline / "boot.tsv").exists())

    def test_candidate_checks_all_late_modules_then_loads_them_after_readiness(self):
        candidate = self.bundle("candidate", "first\nsecond-dev\n")
        preloaded = self.root / "sys/module/second_dev"
        preloaded.mkdir(parents=True)
        result = self.run_bundle(candidate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already loaded: second-dev", result.stderr)
        self.assertEqual(self.calls.read_text().splitlines(), ["readiness"])
        preloaded.rmdir()
        self.calls.unlink()
        result = self.run_bundle(candidate)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().splitlines(),
                         ["readiness", "modprobe first", "modprobe second-dev", "workload 1 two words"])
        self.assertEqual((candidate / "boot.tsv").read_text(), f"candidate\t6.12-candidate\t{BOOT_IDS[0]}\t1234567\n")
        self.assertFalse((candidate / "trace.tsv").exists())

    def test_profile_publishes_only_frozen_trace_and_rejects_same_boot(self):
        profile = self.bundle("profile")
        result = self.run_bundle(profile)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = profile / "trace.tsv"
        self.assertEqual(output.read_text(), TRACE)
        self.assertFalse((profile / "boot.tsv").exists())
        result = self.run_bundle(profile)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("snapshot already saved", result.stderr)
        self.assertEqual(output.read_text(), TRACE)
        output.rename(profile / "first.tsv")
        result = self.run_bundle(profile)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only one capture per boot", result.stderr)
        self.assertFalse(output.exists())
        self.assertEqual(self.calls.read_text().splitlines(),
                         PROFILE_START + ["workload 1 two words", "stop", "snapshot"] + PROFILE_START)

    def test_failed_profile_stops_and_publishes_no_partial_snapshot(self):
        profile = self.bundle("profile")
        output = profile / "trace.tsv"
        failures = (({"WORKLOAD_FAIL": "23"}, 23, False),
                    ({"STOP_FAIL": "1"}, 1, False), ({"READ_FAIL": "1"}, 1, True))
        for boot_id, (env, status, reads) in zip(BOOT_IDS, failures):
            with self.subTest(env=env):
                self.boot_file.write_text(boot_id + "\n")
                self.calls.write_text("")
                result = self.run_bundle(profile, **env)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertFalse(output.exists())
                self.assertEqual(list(profile.glob(".result.*")), [])
                expected = PROFILE_START + ["workload 1 two words", "stop"] + (["snapshot"] if reads else [])
                retry = self.run_bundle(profile)
                self.assertNotEqual(retry.returncode, 0)
                self.assertIn("only one capture per boot", retry.stderr)
                self.assertEqual(self.calls.read_text().splitlines(), expected + PROFILE_START)


class HostCompareTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        packages = self.work / "packages"
        packages.mkdir()
        for kind, size in (("baseline", 20), ("candidate", 25)):
            release = self.work / kind / "kernel/include/config/kernel.release"
            release.parent.mkdir(parents=True)
            release.write_text(f"6.12-{kind}\n")
            (packages / f"{kind}.tar.gz").write_bytes(b"x" * size)
        self.baseline = [self.row("baseline", 0, 5_000_000), self.row("baseline", 1, 2_000_000),
                         self.row("baseline", 2, 3_000_000)]
        self.candidate = [self.row("candidate", 3, 3_500_000), self.row("candidate", 4, 5_500_000)]

    @staticmethod
    def row(kind, boot, micros, release=None):
        return f"{kind}\t{release or '6.12-' + kind}\t{BOOT_IDS[boot]}\t{micros}\n"

    def compare(self, candidate):
        for kind, rows in (("baseline", self.baseline), ("candidate", candidate)):
            (self.work / f"{kind}.tsv").write_text("".join(rows))
        return subprocess.run([ROOT / "kcfg", "compare"], env=dict(os.environ, WORK=str(self.work), PYTHON=sys.executable),
                              capture_output=True, text=True, check=False)

    def test_reports_archive_bytes_signed_reductions_medians_and_counts(self):
        result = self.compare(self.candidate)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("package bytes: baseline=20 candidate=25 reduction=-25.000%", result.stdout)
        self.assertIn("baseline=3.0000000 (n=3) candidate=4.5000000 (n=2) reduction=-50.000%", result.stdout)

    def test_rejects_unsuccessful_missing_wrong_or_duplicate_samples(self):
        valid = self.candidate
        for rows, error in (
            ([], "no successful boot samples"),
            ([self.row("profile", 3, 1)], "expected candidate release"),
            ([self.row("candidate", 3, 1, "wrong")], "expected candidate release"),
            ([self.row("candidate", 3, 0)], "invalid ready_us"),
            (["candidate\t6.12-candidate\tbad-id\t1\n"], "invalid boot ID"),
            (valid + valid[:1], "duplicate boot ID"),
            ([self.row("candidate", 0, 1)], "duplicate boot ID"),
        ):
            with self.subTest(error=error, rows=rows):
                result = self.compare(rows)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
