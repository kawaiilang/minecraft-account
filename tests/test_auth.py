"""Tests for minecraft-auth using synthetic credentials and network responses."""

import contextlib
import copy
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("auth", os.environ["AUTH_MODULE"])
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)
TOKEN = "SYNTHETIC_ACCESS_TOKEN_DO_NOT_LOG"
REFRESH = "SYNTHETIC_REFRESH_TOKEN_DO_NOT_LOG"
UUID = "1234567890abcdef1234567890abcdef"


def record(expired=False):
    return {
        "schema": 1,
        "refresh_token": REFRESH,
        "mc_token": TOKEN,
        "expires_at": 0 if expired else int(time.time()) + 3600,
        "username": "TestPlayer",
        "uuid": UUID,
    }


class Store:
    def __init__(self, value=None):
        self.value = copy.deepcopy(value)
        self.writes = []

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = copy.deepcopy(value)
        self.writes.append(copy.deepcopy(value))


class Interface:
    def __init__(self):
        self.shown = []
        self.waits = []
        self.finished = False

    def device(self, uri, code):
        self.shown.append((uri, code))

    def wait(self, seconds):
        self.waits.append(seconds)

    def finish(self):
        self.finished = True

    def check_cancelled(self):
        pass


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(
            auth, "_http_request", side_effect=AssertionError("Unexpected network call")
        )
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_first_login_stores_only_after_profile_validation(self):
        store, ui = Store(), Interface()
        with (
            patch.object(
                auth, "_device_code_login", return_value=("ms-access", REFRESH)
            ),
            patch.object(
                auth,
                "_exchange_for_minecraft_chain",
                return_value=(TOKEN, 3600, "TestPlayer", UUID),
            ),
        ):
            self.assertEqual(auth.credentials(store, ui)["uuid"], UUID)
        self.assertEqual(len(store.writes), 1)
        self.assertTrue(ui.finished)

    def test_login_cancel_and_mismatched_reauthentication_preserve_account(self):
        store, ui = Store(record()), Interface()
        with patch.object(auth, "_device_code_login", side_effect=auth.Cancelled):
            with self.assertRaises(auth.Cancelled):
                auth.login(store, ui)
        self.assertFalse(store.writes)
        with (
            patch.object(auth, "_device_code_login", return_value=("ms", "new")),
            patch.object(
                auth,
                "_exchange_for_minecraft_chain",
                return_value=(TOKEN, 3600, "OtherPlayer", "a" * 32),
            ),
        ):
            with self.assertRaises(auth.AuthError):
                auth.login(store, ui, expected_uuid=UUID)
        self.assertEqual(store.load(), record())

    def test_rotation_is_saved_before_downstream_failure(self):
        store = Store(record(expired=True))

        def downstream(_):
            self.assertEqual(store.value["refresh_token"], "rotated")
            self.assertEqual(store.value["mc_token"], "")
            raise auth.AuthError("Xbox temporarily unavailable")

        with (
            patch.object(
                auth, "_refresh_microsoft_token", return_value=("ms", "rotated")
            ),
            patch.object(auth, "_exchange_for_minecraft_chain", side_effect=downstream),
        ):
            with self.assertRaises(auth.AuthError):
                auth.credentials(store, Interface())
        self.assertEqual(store.value["refresh_token"], "rotated")

    def test_secret_write_failure_stops_downstream_and_launch(self):
        store = Store(record(expired=True))
        with (
            patch.object(
                auth, "_refresh_microsoft_token", return_value=("ms", "rotated")
            ),
            patch.object(store, "save", side_effect=OSError(TOKEN)),
            patch.object(auth, "_exchange_for_minecraft_chain") as exchange,
        ):
            with self.assertRaises(OSError):
                auth.credentials(store, Interface())
            exchange.assert_not_called()

    def test_refresh_and_valid_cached_session(self):
        store = Store(record(expired=True))
        with (
            patch.object(
                auth, "_refresh_microsoft_token", return_value=("ms", "rotated")
            ),
            patch.object(
                auth,
                "_exchange_for_minecraft_chain",
                return_value=(TOKEN, 3600, "TestPlayer", UUID),
            ),
        ):
            self.assertEqual(auth.credentials(store, Interface())["mc_token"], TOKEN)
        with patch.object(
            auth,
            "_http_request",
            return_value=(200, {"id": UUID, "name": "Renamed"}, b"", {}),
        ):
            self.assertEqual(
                auth.credentials(store, Interface())["username"], "Renamed"
            )

    def test_transient_failure_preserves_credentials_without_relogin(self):
        for failure in [auth.AuthError("network"), OSError("network")]:
            store = Store(record(expired=True))
            with (
                patch.object(auth, "_refresh_microsoft_token", side_effect=failure),
                patch.object(auth, "_device_code_login") as login,
            ):
                with self.assertRaises(type(failure)):
                    auth.credentials(store, Interface())
                login.assert_not_called()
            self.assertFalse(store.writes)

    def test_revoked_refresh_requires_same_account_reauthentication(self):
        store = Store(record(expired=True))
        with (
            patch.object(
                auth, "_refresh_microsoft_token", side_effect=auth.LoginRequired
            ),
            patch.object(auth, "login", return_value=record()) as login,
        ):
            auth.credentials(store, Interface())
            self.assertEqual(login.call_args.kwargs, {"expected_uuid": UUID})

    def test_http_errors_never_echo_response_secrets(self):
        calls = [
            lambda: auth._device_code_login(Interface()),
            lambda: auth._refresh_microsoft_token(REFRESH),
            lambda: auth._exchange_for_minecraft_chain(TOKEN),
        ]
        for call in calls:
            with patch.object(
                auth,
                "_http_request",
                return_value=(
                    503,
                    {"error": TOKEN, "error_description": REFRESH},
                    b"",
                    {},
                ),
            ):
                with self.assertRaises(auth.AuthError) as caught:
                    call()
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertNotIn(REFRESH, str(caught.exception))

    def test_device_pending_slowdown_success_and_expiry(self):
        initiation = (
            200,
            {
                "device_code": "private-device",
                "user_code": "ABCD-1234",
                "verification_uri": "https://www.microsoft.com/link",
                "interval": 1,
            },
            b"",
            {},
        )
        ui = Interface()
        replies = [
            initiation,
            (400, {"error": "authorization_pending"}, b"", {}),
            (400, {"error": "slow_down"}, b"", {}),
            (200, {"access_token": TOKEN, "refresh_token": REFRESH}, b"", {}),
        ]
        with patch.object(auth, "_http_request", side_effect=replies):
            self.assertEqual(auth._device_code_login(ui), (TOKEN, REFRESH))
        self.assertEqual(ui.waits, [1, 1, 6])
        for error in ["expired_token", "authorization_declined"]:
            with patch.object(
                auth,
                "_http_request",
                side_effect=[initiation, (400, {"error": error}, b"", {})],
            ):
                with self.assertRaises(auth.AuthError):
                    auth._device_code_login(Interface())
        ui = Interface()
        ui.wait = lambda _: (_ for _ in ()).throw(auth.Cancelled())
        with patch.object(auth, "_http_request", return_value=initiation):
            with self.assertRaises(auth.Cancelled):
                auth._device_code_login(ui)

    def test_main_sanitizes_unexpected_errors_and_status_does_not_login(self):
        for failure in [
            OSError(TOKEN),
            ValueError(REFRESH),
            auth.AuthError("Safe local error"),
        ]:
            output = io.StringIO()
            with (
                patch.object(sys, "argv", ["minecraft-auth", "status"]),
                patch.object(auth, "run_action", side_effect=failure),
                contextlib.redirect_stderr(output),
            ):
                self.assertEqual(auth.main(), 1)
            self.assertNotIn(TOKEN, output.getvalue())
            self.assertNotIn(REFRESH, output.getvalue())

    def test_invalid_stored_json_reports_recovery_without_exposing_contents(self):
        store = object.__new__(auth.AccountStore)
        for payload in [TOKEN.encode(), b"\xff" + REFRESH.encode()]:
            with self.subTest(
                payload_type="non-json" if payload.isascii() else "invalid-utf8"
            ):
                item = Mock()
                item.get_secret.return_value = payload
                with patch.object(store, "_items", return_value=[item]):
                    with self.assertRaises(auth.AuthError) as caught:
                        store.load()
                message = str(caught.exception)
                self.assertIn("Forget this account on this computer", message)
                self.assertNotIn(TOKEN, message)
                self.assertNotIn(REFRESH, message)
                item.delete.assert_not_called()
                item.set_secret.assert_not_called()

    def test_lock_prevents_parallel_mutations_and_releases_after_error(self):
        with (
            tempfile.TemporaryDirectory() as runtime,
            patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}),
        ):
            with auth.account_lock():
                pid = os.fork()
                if pid == 0:
                    try:
                        with auth.account_lock():
                            os._exit(4)
                    except auth.AuthError:
                        os._exit(0)
                self.assertEqual(os.waitpid(pid, 0)[1], 0)
            with self.assertRaises(ValueError):
                with auth.account_lock():
                    raise ValueError("fixture")
            with auth.account_lock():
                pass
            lock = Path(runtime) / "minecraft-personal-auth.lock"
            self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
            lock.unlink()
            lock.symlink_to(Path(runtime) / "other")
            with self.assertRaises(OSError):
                with auth.account_lock():
                    pass

    def test_java_argfile_handoff_and_exit_code(self):
        # The child execs the actual packaged Java; no token is a process argument.
        args = [
            os.environ["AUTH_JAVA"],
            os.environ["AUTH_JAVA_PROBE"],
            "with spaces",
            'double"quote',
            "back\\slash",
            "new\nline",
            "#literal",
            "@literal",
        ]
        pid = os.fork()
        if pid == 0:
            auth.launch_java(args, record())
            os._exit(9)
        self.assertEqual(os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]), 23)

    def test_authentication_args_cannot_be_injected(self):
        for argument in ["--accessToken", "--username=Other", "--uuid=anything"]:
            with self.assertRaises(auth.AuthError):
                auth.launch_java(["/not-run/java", argument], record())


if __name__ == "__main__":
    unittest.main()
