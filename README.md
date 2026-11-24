# minecraft-account

[日本語](README-jp.md)

A Linux account helper for Minecraft Java Edition. Sign in with a Microsoft
device code, store the account in GNOME Keyring, and pass credentials to Java
through a sealed anonymous file descriptor. The game launcher supplies the
instance, classpath, and game arguments.

## Run locally

Use Python 3.10 or later and a desktop session with a password-protected,
persistent default GNOME keyring. The optional GUI uses Zenity and xdg-open.

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/minecraft-account --help
.venv/bin/minecraft-account login
.venv/bin/minecraft-account status
```

Each Linux user can register one account. Use `minecraft-account --gui` to
manage it from the desktop or `minecraft-account logout` to forget it locally.
For integration, invoke `minecraft-account launch -- /absolute/path/to/java ...`
in the game directory with the launcher's normal Java and game arguments.
The helper supplies the username, UUID and access token.

## Tests

The **Account tests** workflow runs on Ubuntu with synthetic accounts. It
exercises Microsoft and Minecraft response handling, a real Java process,
Zenity under Xvfb, and a temporary keyring on a private D-Bus session.
[See the test commands](.github/workflows/test.yml).

## Origin

This helper was derived from
[Nixcraft's client-auth.py at fd933a2](https://github.com/loystonpais/nixcraft/blob/fd933a2806fbc8c1627018a80489964d618b0c2c/nix/client-auth/client-auth.py)
with the downstream keyring and protected-launch changes. Review upstream
Microsoft, Xbox and Minecraft authentication changes when maintaining the fork.
The code is distributed under [GPL-3.0-only](LICENSE).
