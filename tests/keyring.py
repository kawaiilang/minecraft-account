"""Isolated Secret Service integration for minecraft-account."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

import secretstorage

spec = importlib.util.spec_from_file_location("auth", os.environ["AUTH_MODULE"])
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)

with tempfile.TemporaryDirectory() as work:
    root = Path(work)
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    env = {
        **os.environ,
        "HOME": work,
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_RUNTIME_DIR": str(runtime),
    }
    # This password and token are disposable fixtures, never a user's keyring.
    daemon = subprocess.Popen(
        ["gnome-keyring-daemon", "--foreground", "--components=secrets", "--unlock"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    daemon.stdin.write(b"synthetic-keyring-password\n")
    daemon.stdin.close()
    connection = None
    try:
        for _ in range(100):
            try:
                connection = secretstorage.dbus_init()
                collection = secretstorage.get_collection_by_alias(
                    connection, "default"
                )
                break
            except secretstorage.exceptions.SecretServiceNotAvailableException:
                if connection is not None:
                    connection.close()
                time.sleep(0.1)
            except secretstorage.exceptions.ItemNotFoundException:
                if connection is not None:
                    connection.close()
                time.sleep(0.1)
        else:
            raise AssertionError("Private keyring did not become ready")
        with patch.dict(os.environ, env):
            store = auth.AccountStore()
            try:
                assert store.load() is None
                value = {
                    "schema": 1,
                    "refresh_token": "PRIVATE_KEYRING_REFRESH_FIXTURE",
                    "mc_token": "PRIVATE_KEYRING_ACCESS_FIXTURE",
                    "expires_at": 123,
                    "username": "Fixture",
                    "uuid": "a" * 32,
                }
                store.save(value)
                assert store.load() == value
                value["refresh_token"] = "PRIVATE_KEYRING_ROTATED_FIXTURE"
                store.save(value)
                assert store.load() == value
                items = list(collection.search_items(store.attributes))
                assert len(items) == 1
                attributes = items[0].get_attributes()
                # GNOME Keyring may add its generic schema to the stored attributes.
                schema = attributes.pop("xdg:schema", None)
                assert schema in (None, "org.freedesktop.Secret.Generic"), schema
                assert attributes == store.attributes, (attributes, store.attributes)
                assert items[0].get_label() == "Minecraft Personal"
                keyring_files = list((root / "data" / "keyrings").glob("*.keyring"))
                assert keyring_files, "Persistent encrypted keyring must exist"
                for file in keyring_files:
                    contents = file.read_bytes()
                    assert b"PRIVATE_KEYRING" not in contents
                    assert b"Fixture" not in contents
                store.delete()
                assert store.load() is None
                # A locked collection must not become an empty account or file fallback.
                collection.lock()
                with patch.object(
                    auth.secretstorage.Collection, "unlock", return_value=True
                ):
                    try:
                        auth.AccountStore()
                    except auth.Cancelled:
                        pass
                    else:
                        raise AssertionError("Dismissed unlock must cancel")
                locked = auth.AccountStore(unlock=False)
                try:
                    try:
                        locked.load()
                    except auth.AuthError:
                        pass
                    else:
                        raise AssertionError("Status must report locked keyring")
                finally:
                    locked.close()
            finally:
                store.close()
            assert not (root / ".local/share/nixcraft/client/auth").exists()
        print("Private encrypted keyring create/read/rotate/delete/lock tests passed")
    finally:
        if connection is not None:
            connection.close()
        daemon.terminate()
        daemon.wait(timeout=10)
