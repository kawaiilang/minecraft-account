{
  lib,
  stdenvNoCC,
  python3,
  makeWrapper,
  zenity,
  xdg-utils,
  dbus,
  gnome-keyring,
  xvfb-run,
  checkJdk,
}:
let
  python = python3.withPackages (packages: [ packages.secretstorage ]);
in
stdenvNoCC.mkDerivation {
  pname = "minecraft-auth";
  version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./minecraft_auth.py
      ./LICENSE
      ./tests
    ];
  };
  nativeBuildInputs = [ makeWrapper ];

  postPatch = ''
    substituteInPlace minecraft_auth.py \
      --replace-fail 'ZENITY = "zenity"' 'ZENITY = "${lib.getExe zenity}"' \
      --replace-fail 'XDG_OPEN = "xdg-open"' 'XDG_OPEN = "${lib.getExe' xdg-utils "xdg-open"}"'
  '';

  doCheck = true;
  nativeCheckInputs = [
    python
    dbus
    gnome-keyring
    checkJdk
    xvfb-run
  ];
  checkPhase = ''
    runHook preCheck
    export PYTHONDONTWRITEBYTECODE=1
    export AUTH_MODULE="$PWD/minecraft_auth.py"
    export AUTH_JAVA='${lib.getExe checkJdk}'
    export AUTH_JAVA_PROBE="$PWD/tests/ArgumentProbe.java"
    ${lib.getExe python} tests/test_auth.py
    xvfb-run -a ${lib.getExe python} tests/gui.py
    dbus-run-session --config-file=tests/session.conf -- ${lib.getExe python} tests/keyring.py
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm444 minecraft_auth.py "$out/lib/minecraft_auth.py"
    install -Dm444 LICENSE "$out/share/licenses/minecraft-auth/LICENSE"
    makeWrapper ${lib.getExe python} "$out/bin/minecraft-auth" \
      --add-flags "$out/lib/minecraft_auth.py"
    runHook postInstall
  '';

  meta = {
    description = "Microsoft authentication for Minecraft Java using GNOME Keyring";
    mainProgram = "minecraft-auth";
    platforms = lib.platforms.linux;
    license = lib.licenses.gpl3Only;
  };
}
