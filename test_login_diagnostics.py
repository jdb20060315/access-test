"""脱敏阶段识别和仅登录检测；全部使用本地假响应，不发送账号请求。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from neko_account_check import check, login_diagnostics
from neko_single_run_check import single_run
import personal_service as service


class DiagnosticsTest(unittest.TestCase):
    def test_http_errors_are_attributed_only_when_labeled(self):
        output = (
            "[gee] checkGeeUse 失败（HTTP 567: secret-response），按需验证码处理\n"
            "[login] 开始 GT4 滑块验证…\n"
            "× [gt4] 第 1 次失败: HTTP 567: secret-response\n"
            "× [gt4] 第 2 次失败: HTTP 567: secret-response\n"
            "GT4 连续 3 次失败: HTTP 567: secret-response\n"
        )
        result = login_diagnostics(output)
        self.assertEqual(result["progress"], ["check_failed", "captcha_started"])
        self.assertEqual(result["http_failures"], [
            {"stage": "gee_check", "status": 567}, {"stage": "captcha", "status": 567},
        ])
        self.assertNotIn("secret-response", json.dumps(result))

    def test_captcha_keywords_do_not_classify_an_unlabeled_failure(self):
        result = login_diagnostics("[gee] checkGeeUse data=true（跳过 GT4）\nHTTP 567: captcha")
        self.assertEqual(result, {"progress": [], "http_failures": [{"stage": "unknown", "status": 567}]})

    def test_fixed_progress_and_output_limit_exclude_raw_data(self):
        output = (
            "[login] GT4 通过 lot_number=private-value\n"
            "[geevalidate] 验证通过\n"
            "[login] 免验证码路径：直接登录\n"
        ) + "\n".join(f"HTTP {code}: private-token https://private.invalid/" for code in range(500, 520))
        result = login_diagnostics(output)
        self.assertEqual(result["progress"], ["captcha_passed", "validation_passed", "direct_login"])
        self.assertEqual(len(result["http_failures"]), 10)
        self.assertNotIn("private", json.dumps(result))

    def test_account_check_and_single_run_forward_safe_diagnostics(self):
        for operation in (check, single_run):
            with self.subTest(operation=operation.__name__), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "nekosportsworldtool").touch()
                calls = []

                def runner(args, **kwargs):
                    calls.append(args[1])
                    return subprocess.CompletedProcess(
                        args, 1, "", "× [gt4] 第 1 次失败: HTTP 567: private-password"
                    )

                report = operation(root, "private-user", "private-password", runner=runner)
                self.assertEqual(report["login_diagnostics"]["http_failures"], [{"stage": "captcha", "status": 567}])
                self.assertNotIn("run", calls)
                for secret in ("private-user", "private-password"):
                    self.assertNotIn(secret, json.dumps(report))

    def test_diagnostic_worker_never_uploads_and_verifies_logout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "nekosportsworldtool").touch()
            calls = []
            logged_out = False

            def runner(args, **kwargs):
                nonlocal logged_out
                command = args[1]
                calls.append(command)
                current = Path(kwargs["cwd"])
                if command == "login":
                    (current / "session.json").write_text('{"token":"private-token"}')
                elif command == "logout":
                    logged_out = True
                    (current / "session.json").unlink(missing_ok=True)
                elif command == "semester" and logged_out:
                    return subprocess.CompletedProcess(args, 1, "", "HTTP 401")
                elif command != "semester":
                    self.assertEqual(command, "login")
                return subprocess.CompletedProcess(args, 0, "", "")

            cipher = Fernet(Fernet.generate_key())
            with patch.object(service, "ROOT", root), \
                 patch.object(service, "settings", return_value=("test-key", None, None, cipher)), \
                 patch.object(service.subprocess, "run", side_effect=runner), \
                 patch.object(service.store, "checkpoint"), \
                 patch.object(service.store, "load", return_value={"session_ciphertext": None}), \
                 patch.object(service.store, "finish") as finish, \
                 patch.object(service, "single_run") as upload:
                service.execute("browser-test", "account-test", "private-user", "private-password",
                                {}, 2, 390, True)
            upload.assert_not_called()
            self.assertEqual(calls, ["login", "semester", "logout", "semester"])
            report = finish.call_args.args[1]
            self.assertEqual(report["operation"], "login_diagnosis")
            self.assertFalse(report["run_attempted"])
            self.assertFalse(report["record_uploaded"])
            self.assertTrue(report["remote_logout"])
            self.assertIsNone(finish.call_args.args[2])

    def test_diagnostic_exception_cannot_fall_back_to_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "nekosportsworldtool").touch()
            with patch.object(service, "ROOT", root), \
                 patch.object(service, "settings", return_value=("test-key", None, None, None)), \
                 patch.object(service, "check_account", side_effect=RuntimeError("private-error")), \
                 patch.object(service.store, "load", return_value=None), \
                 patch.object(service.store, "finish") as finish, \
                 patch.object(service, "single_run") as upload:
                service.execute("browser-test", "account-test", "private-user", "private-password",
                                {}, 2, 390, True)
            upload.assert_not_called()
            report = finish.call_args.args[1]
            self.assertEqual(report["failure"], "result_unknown_no_retry")
            self.assertNotIn("private-error", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
