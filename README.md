# minecraft-auth

English | [日本語](README-jp.md)

A Linux authentication helper for Minecraft Java Edition. Sign in with a Microsoft
device code, store the account in GNOME Keyring, and pass credentials to Java
through a sealed anonymous file descriptor. The game launcher supplies the
instance, classpath, and game arguments.

## Run locally

Sign-in requires a desktop session with a password-protected, persistent
default GNOME keyring. From a checkout on x86_64 or aarch64 Linux, Nix supplies
the Python and GUI dependencies:

```sh
nix run . -- --help
nix run . -- login
```

For a Python installation, use Python 3.10 or later. The optional GUI uses
Zenity and xdg-open.

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/minecraft-auth --help
.venv/bin/minecraft-auth login
.venv/bin/minecraft-auth status
```

Each Linux user can register one account. Use `minecraft-auth --gui` to
manage it from the desktop or `minecraft-auth logout` to forget it locally.
For integration, invoke `minecraft-auth launch -- /absolute/path/to/java ...`
in the game directory with the launcher's normal Java and game arguments.
The helper supplies the username, UUID and access token.

## Tests

Run `nix flake check` to build the package and run the tests, linters, type
checks, and workflow security audits. Use `nix fmt` to format Nix files or
`nix develop` for a shell with the same Python and check tools. If your shell
has the direnv hook enabled, run `direnv allow` in the repository root to load
that environment automatically whenever you enter the checkout.

A consuming flake can build it with its own package set using `lib.mkAuth { inherit pkgs; }`.
The optional `checkJdk` argument selects the Java used in tests and defaults
to `pkgs.jdk25`. The launcher supplies Java when starting a game.

The **CI** workflow runs static checks (`ruff`, `pyright`, `nixfmt`, `statix`,
`deadnix`, `actionlint`, `zizmor`), Nix package checks, and Ubuntu tests with
synthetic accounts exercising Microsoft and Minecraft response handling, a real
Java process, Zenity under Xvfb, and a temporary keyring on a private D-Bus
session. [See the CI workflow](.github/workflows/ci.yml).

## Origin

This helper was derived from
[Nixcraft's client-auth.py at fd933a2](https://github.com/loystonpais/nixcraft/blob/fd933a2806fbc8c1627018a80489964d618b0c2c/nix/client-auth/client-auth.py)
with the downstream keyring and protected-launch changes. Review upstream
Microsoft, Xbox and Minecraft authentication changes when maintaining the fork.
The code is distributed under [GPL-3.0-only](LICENSE).
