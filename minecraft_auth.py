#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Derived from loystonpais/nixcraft's nix/client-auth/client-auth.py.
# Forked 2026-11-24 with Secret Service storage and protected Java launch.
"""Authenticate Minecraft Java accounts and launch with GNOME Keyring credentials."""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple
import fcntl
import re
import stat
import subprocess

import secretstorage

ZENITY = "zenity"
XDG_OPEN = "xdg-open"

CLIENT_ID = "000000004c12ae6f"
SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
EXPIRY_BUFFER_SECONDS = 300
_SSL_CONTEXT = ssl.create_default_context()


def normalize_uuid(uuid_str: str) -> str:
    """Strip hyphens and lowercase UUID string."""
    return uuid_str.replace("-", "").strip().lower()


def _http_request(
    url: str,
    method: str = "GET",
    data: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 15.0,
) -> Tuple[int, Dict[str, Any], bytes, Dict[str, str]]:
    """Execute an HTTP request and parse JSON response if present."""
    req_headers = {
        "User-Agent": "MinecraftLauncher/2.2.10675",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)

    body_bytes = None
    if json_data is not None:
        req_headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(json_data).encode("utf-8")
    elif data is not None:
        req_headers["Content-Type"] = "application/x-www-form-urlencoded"
        body_bytes = urllib.parse.urlencode(data).encode("utf-8")

    req = urllib.request.Request(
        url, data=body_bytes, headers=req_headers, method=method
    )

    try:
        with urllib.request.urlopen(
            req, timeout=timeout, context=_SSL_CONTEXT
        ) as response:
            res_code = response.status
            res_headers = dict(response.headers)
            raw_data = response.read()
            try:
                parsed = json.loads(raw_data.decode("utf-8")) if raw_data else {}
            except Exception:
                parsed = {}
            return res_code, parsed, raw_data, res_headers
    except urllib.error.HTTPError as e:
        res_headers = dict(e.headers) if hasattr(e, "headers") else {}
        raw_data = e.read()
        try:
            parsed = json.loads(raw_data.decode("utf-8")) if raw_data else {}
        except Exception:
            parsed = {}
        return e.code, parsed, raw_data, res_headers
    except Exception:
        raise AuthError(
            "The authentication service could not be reached. Check the connection and retry."
        ) from None


def _device_code_login(ui) -> Tuple[str, str]:
    """Initiate Microsoft Live OAuth Device Flow and poll until authorization succeeds."""
    code_status, code_res, _, headers = _http_request(
        "https://login.live.com/oauth20_connect.srf",
        method="POST",
        data={
            "client_id": CLIENT_ID,
            "scope": SCOPE,
            "response_type": "device_code",
        },
    )

    if code_status != 200 or "device_code" not in code_res:
        raise AuthError(
            "Microsoft device login could not be started. Please retry later."
        )

    user_code = code_res["user_code"]
    verification_uri = code_res.get(
        "verification_uri", "https://www.microsoft.com/link"
    )
    device_code = code_res["device_code"]
    interval = min(30, max(1, int(code_res.get("interval", 5))))
    expires_in = min(900, max(1, int(code_res.get("expires_in", 900))))
    if verification_uri not in (
        "https://www.microsoft.com/link",
        "https://microsoft.com/link",
        "https://www.microsoft.com/devicelogin",
        "https://microsoft.com/devicelogin",
    ):
        raise AuthError("Microsoft returned an unexpected verification address.")
    if not re.fullmatch(r"[A-Z0-9-]{4,32}", user_code):
        raise AuthError("Microsoft returned an invalid device code.")

    set_cookie = headers.get("Set-Cookie", "")
    poll_headers = {"Cookie": set_cookie} if set_cookie else {}

    ui.device(verification_uri, user_code)

    start_time = time.monotonic()
    while time.monotonic() - start_time < expires_in:
        ui.wait(interval)
        token_status, token_res, _, _ = _http_request(
            f"https://login.live.com/oauth20_token.srf?client_id={CLIENT_ID}",
            method="POST",
            headers=poll_headers,
            data={
                "client_id": CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )

        if token_status == 200 and "access_token" in token_res:
            return token_res["access_token"], token_res["refresh_token"]

        err = token_res.get("error")
        if err == "authorization_pending":
            continue
        elif err == "slow_down":
            interval = min(60, interval + 5)
            continue
        elif err in ("expired_token", "authorization_declined"):
            raise AuthError(
                "Microsoft sign-in expired or was declined. Sign in again to retry."
            )
        elif token_status >= 400:
            raise AuthError("Microsoft sign-in failed. Please retry later.")

    raise AuthError("Microsoft sign-in timed out. Sign in again to retry.")


def _refresh_microsoft_token(refresh_token: str) -> Tuple[str, str]:
    """Exchange a Microsoft refresh token for new access and refresh tokens."""
    status, res, _, _ = _http_request(
        "https://login.live.com/oauth20_token.srf",
        method="POST",
        data={
            "client_id": CLIENT_ID,
            "scope": SCOPE,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )

    if status != 200 or "access_token" not in res:
        if res.get("error") == "invalid_grant":
            raise LoginRequired()
        raise AuthError("Microsoft could not refresh the session. Please retry later.")

    return res["access_token"], res["refresh_token"]


def _exchange_for_minecraft_chain(ms_access_token: str) -> Tuple[str, int, str, str]:
    """
    Exchange Microsoft access token through Xbox Live (XAU), XSTS, and Minecraft services.
    Returns (mc_access_token, mc_expires_in, username, normalized_uuid).
    """
    rps_ticket = (
        ms_access_token
        if (ms_access_token.startswith("d=") or ms_access_token.startswith("t="))
        else f"t={ms_access_token}"
    )

    # 1. Xbox Live User Authentication (XAU)
    xbl_status, xbl_res, _, _ = _http_request(
        "https://user.auth.xboxlive.com/user/authenticate",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-xbl-contract-version": "2",
        },
        json_data={
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": rps_ticket,
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        },
    )

    if xbl_status != 200 or "Token" not in xbl_res:
        raise AuthError(
            "Xbox Live authentication failed. Check your Xbox profile and retry."
        )

    xbl_token = xbl_res["Token"]
    try:
        user_hash = xbl_res["DisplayClaims"]["xui"][0]["uhs"]
    except (KeyError, IndexError):
        raise AuthError("Xbox Live returned an incomplete profile.") from None

    # 2. Xbox Secure Token Service (XSTS)
    xsts_status, xsts_res, _, _ = _http_request(
        "https://xsts.auth.xboxlive.com/xsts/authorize",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-xbl-contract-version": "1",
        },
        json_data={
            "Properties": {
                "SandboxId": "RETAIL",
                "UserTokens": [xbl_token],
            },
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        },
    )

    if xsts_status != 200 or "Token" not in xsts_res:
        xerr = xsts_res.get("XErr")
        if xerr == 2148916233:
            raise AuthError(
                "Create an Xbox profile for this Microsoft account, then retry."
            )
        elif xerr == 2148916238:
            raise AuthError(
                "This Xbox account requires Microsoft Family setup and multiplayer permission."
            )
        raise AuthError(
            "Xbox authorization failed. Check your account and multiplayer permissions."
        )

    xsts_token = xsts_res["Token"]

    # 3. Minecraft Services Login
    mc_status, mc_res, _, _ = _http_request(
        "https://api.minecraftservices.com/authentication/login_with_xbox",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json_data={"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"},
    )

    if mc_status != 200 or "access_token" not in mc_res:
        raise AuthError("Minecraft authentication failed. Please retry later.")

    mc_access_token = mc_res["access_token"]
    mc_expires_in = mc_res.get("expires_in", 86400)

    # 4. Fetch Minecraft Profile
    prof_status, prof_res, _, _ = _http_request(
        "https://api.minecraftservices.com/minecraft/profile",
        method="GET",
        headers={"Authorization": f"Bearer {mc_access_token}"},
    )

    if prof_status != 200 or "id" not in prof_res or "name" not in prof_res:
        raise AuthError(
            "Minecraft Java profile not found. If this is a new purchase, "
            "please visit https://www.minecraft.net/profile to set your in-game username first."
        )

    norm_uuid = normalize_uuid(prof_res["id"])
    return mc_access_token, mc_expires_in, prof_res["name"], norm_uuid


class AuthError(Exception):
    """Only these fixed, locally authored messages may reach the UI."""


class Cancelled(Exception):
    pass


class LoginRequired(Exception):
    pass


class Interface:
    def __init__(self, gui=False):
        self.gui = gui
        self.progress = None

    def message(self, text, error=False):
        if self.gui:
            subprocess.run(
                [
                    ZENITY,
                    "--text-info",
                    "--title=Minecraft Auth",
                    "--width=540",
                    "--height=240",
                    "--no-markup",
                ],
                input=text,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            print(text, file=sys.stderr if error else sys.stdout)

    def device(self, uri, code):
        # Display the short-lived code locally, never put it in process arguments.
        text = f"Open {uri}\nEnter code: {code}\nWaiting for Microsoft authorization…"
        if self.gui:
            self.progress = subprocess.Popen(
                [
                    ZENITY,
                    "--progress",
                    "--pulsate",
                    "--auto-close",
                    "--no-markup",
                    "--title=Minecraft Auth — Microsoft sign-in",
                    "--width=540",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self.progress.stdin.write("# " + text.replace("\n", "  ") + "\n")
            self.progress.stdin.flush()
            subprocess.run(
                [XDG_OPEN, uri],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            print(text, file=sys.stderr)

    def wait(self, seconds):
        self.check_cancelled()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_cancelled()
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    def check_cancelled(self):
        if self.progress is not None and self.progress.poll() is not None:
            raise Cancelled()

    def finish(self):
        if self.progress is not None:
            if self.progress.poll() is None:
                self.progress.terminate()
            self.progress.wait()
            self.progress.stdin.close()
            self.progress = None


@contextmanager
def account_lock():
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if not os.path.isabs(runtime):
        raise AuthError(
            "A private desktop runtime directory is required. Sign in to the desktop first."
        )
    info = os.stat(runtime, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise AuthError(
            "The desktop runtime directory must belong to you with mode 0700."
        )
    # Older releases use this lock when updating the same keyring entry.
    fd = os.open(
        os.path.join(runtime, "minecraft-personal-auth.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise AuthError(
                "The authentication lock has unsafe ownership or permissions."
            )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AuthError(
                "Another account operation is in progress. Please retry after it finishes."
            ) from None
        yield
    finally:
        os.close(fd)


def validate_record(record):
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise AuthError(
            "The saved Minecraft account is invalid. Log out and sign in again."
        )
    if not isinstance(record.get("refresh_token"), str) or not record["refresh_token"]:
        raise AuthError(
            "The saved Minecraft account has no refresh credential. Sign in again."
        )
    if not re.fullmatch(r"[0-9a-f]{32}", record.get("uuid", "")) or not re.fullmatch(
        r"[A-Za-z0-9_]{1,16}", record.get("username", "")
    ):
        raise AuthError(
            "The saved Minecraft profile is invalid. Log out and sign in again."
        )
    if not isinstance(record.get("mc_token"), str) or not isinstance(
        record.get("expires_at"), int
    ):
        raise AuthError(
            "The saved Minecraft session is invalid. Log out and sign in again."
        )
    return record


class AccountStore:
    # These attributes identify saved accounts across application renames.
    attributes = {"application": "minecraft-personal", "slot": "default"}

    def __init__(self, unlock=True):
        self.connection = secretstorage.dbus_init()
        # Do not create a collection or silently choose the session collection.
        try:
            self.collection = secretstorage.get_collection_by_alias(
                self.connection, "default"
            )
        except secretstorage.exceptions.ItemNotFoundException:
            raise AuthError(
                "Set up a password-protected default GNOME keyring in Passwords and Keys, then retry."
            ) from None
        if (
            self.collection.collection_path
            == "/org/freedesktop/secrets/collection/session"
        ):
            raise AuthError(
                "The default keyring must be persistent and password-protected, not session-only."
            )
        if self.collection.is_locked() and unlock:
            if self.collection.unlock() or self.collection.is_locked():
                raise Cancelled()

    def close(self):
        self.connection.close()

    def _items(self):
        if self.collection.is_locked():
            raise AuthError("The GNOME keyring is locked.")
        items = list(self.collection.search_items(self.attributes))
        if len(items) > 1:
            raise AuthError(
                "Multiple Minecraft credentials were found. Remove the duplicates in Passwords and Keys."
            )
        return items

    def load(self):
        items = self._items()
        if not items:
            return None
        try:
            record = json.loads(items[0].get_secret())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AuthError(
                "The saved Minecraft account cannot be read. In Minecraft Auth, choose 'Forget this account on this computer', then sign in again."
            ) from None
        return validate_record(record)

    def save(self, record):
        validate_record(record)
        data = json.dumps(record).encode()
        items = self._items()
        if items:
            item = items[0]
            item.set_secret(data, content_type="application/json")
        else:
            item = self.collection.create_item(
                "Minecraft Auth",
                self.attributes,
                data,
                replace=True,
                content_type="application/json",
            )
        if item.get_secret() != data:
            raise AuthError(
                "The keyring did not confirm the credential update. Launch has been stopped."
            )

    def delete(self):
        for item in self._items():
            item.delete()


def profile_record(refresh_token, result):
    token, expires_in, username, uuid = result
    return validate_record(
        {
            "schema": 1,
            "refresh_token": refresh_token,
            "mc_token": token,
            "expires_at": int(time.time()) + int(expires_in),
            "username": username,
            "uuid": uuid,
        }
    )


def login(store, ui, expected_uuid=None):
    try:
        access, refresh = _device_code_login(ui)
        record = profile_record(refresh, _exchange_for_minecraft_chain(access))
        ui.check_cancelled()
        if expected_uuid is not None and record["uuid"] != expected_uuid:
            raise AuthError(
                "This is a different Minecraft account. Log out before changing accounts."
            )
        store.save(record)
        return record
    finally:
        ui.finish()


def credentials(store, ui):
    record = store.load()
    if record is None:
        return login(store, ui)
    if (
        record["mc_token"]
        and time.time() < record["expires_at"] - EXPIRY_BUFFER_SECONDS
    ):
        status, profile, _, _ = _http_request(
            "https://api.minecraftservices.com/minecraft/profile",
            headers={"Authorization": f"Bearer {record['mc_token']}"},
        )
        if status == 200:
            if normalize_uuid(profile.get("id", "")) != record["uuid"]:
                raise AuthError(
                    "The Minecraft session belongs to a different account. Log out and sign in again."
                )
            record = validate_record({**record, "username": profile.get("name", "")})
            store.save(record)
            return record
        if status != 401:
            raise AuthError(
                "Minecraft could not verify the session. Check the connection and account, then retry."
            )
    try:
        access, refresh = _refresh_microsoft_token(record["refresh_token"])
    except LoginRequired:
        return login(store, ui, expected_uuid=record["uuid"])
    # Refresh rotation is committed before any downstream request can fail.
    record = {**record, "refresh_token": refresh, "mc_token": "", "expires_at": 0}
    store.save(record)
    updated = profile_record(refresh, _exchange_for_minecraft_chain(access))
    if updated["uuid"] != record["uuid"]:
        raise AuthError(
            "The refreshed Minecraft profile changed account. Log out and sign in again."
        )
    store.save(updated)
    return updated


def java_argument(value):
    if "\0" in value:
        raise AuthError("A launch argument contains a NUL character.")
    # Java launcher argfile quoting, not shell quoting.
    return (
        '"'
        + value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        + '"'
    )


def launch_java(command, record):
    if not command or not os.path.isabs(command[0]):
        raise AuthError("The launcher must supply an absolute Java executable.")
    if any(
        arg.split("=", 1)[0] in ("--accessToken", "--uuid", "--username")
        for arg in command[1:]
    ):
        raise AuthError(
            "Authentication arguments cannot be overridden on the command line."
        )
    args = command[1:] + [
        "--username",
        record["username"],
        "--uuid",
        record["uuid"],
        "--accessToken",
        record["mc_token"],
    ]
    fd = os.memfd_create(
        "minecraft-auth-arguments", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
    )
    try:
        with os.fdopen(os.dup(fd), "w") as handle:
            handle.write("\n".join(java_argument(arg) for arg in args) + "\n")
        os.lseek(fd, 0, os.SEEK_SET)
        os.fchmod(fd, 0o400)
        fcntl.fcntl(
            fd,
            fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_WRITE
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_SEAL,
        )
        os.set_inheritable(fd, True)
        os.execv(command[0], [command[0], f"@/proc/self/fd/{fd}"])
    finally:
        os.close(fd)


def choose_action():
    result = subprocess.run(
        [
            ZENITY,
            "--list",
            "--title=Minecraft Auth",
            "--column=Action",
            "--column=Description",
            "--hide-column=1",
            "--print-column=1",
            "status",
            "Show saved account",
            "login",
            "Sign in with Microsoft",
            "logout",
            "Forget this account on this computer",
            "--width=540",
            "--height=260",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise Cancelled()
    if result.stdout.strip() not in ("status", "login", "logout"):
        raise Cancelled()
    return result.stdout.strip()


def run_action(action, ui, command):
    with account_lock():
        store = AccountStore(unlock=action != "status")
        try:
            if action == "logout":
                store.delete()
                ui.message(
                    "Minecraft account removed from this computer. Already running games and Microsoft sessions are unchanged."
                )
            elif action == "status":
                record = store.load()
                ui.message(
                    f"Minecraft account: {record['username']}\nStored in GNOME Keyring."
                    if record
                    else "No Minecraft account is registered."
                )
            elif action == "login":
                if store.load() is not None:
                    raise AuthError(
                        "A Minecraft account is already registered. Log out first to replace it."
                    )
                record = login(store, ui)
                ui.message(
                    f"Signed in as {record['username']}. You can now launch Minecraft Java Edition."
                )
            else:
                record = credentials(store, ui)
        finally:
            store.close()
    # The lock and D-Bus connection must not survive into the game.
    if action == "launch":
        ui.finish()
        launch_java(command, record)


def main():
    parser = argparse.ArgumentParser(
        prog="minecraft-auth",
        description="Microsoft authentication for Minecraft Java Edition using GNOME Keyring",
    )
    parser.add_argument(
        "--gui", action="store_true", help="Show dialogs in the desktop session"
    )
    parser.add_argument(
        "action", choices=["login", "status", "logout", "launch"], nargs="?"
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    ui = Interface(args.gui)
    try:
        action = args.action or (choose_action() if args.gui else "status")
        command = args.command
        if command[:1] == ["--"]:
            command = command[1:]
        if action != "launch" and command:
            raise AuthError("This account action does not accept additional arguments.")
        if action == "launch" and not command:
            raise AuthError("The launcher must supply a Java command after '--'.")
        run_action(action, ui, command)
        return 0
    except (Cancelled, KeyboardInterrupt):
        return 130
    except AuthError as error:
        ui.finish()
        ui.message(str(error), error=True)
        return 1
    except Exception:
        ui.finish()
        ui.message(
            "Minecraft authentication failed. Check your connection and password-protected GNOME keyring, then retry. No plaintext fallback was used.",
            error=True,
        )
        return 1
    finally:
        ui.finish()


if __name__ == "__main__":
    sys.exit(main())
