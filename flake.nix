{
  description = "Microsoft authentication for Minecraft Java using GNOME Keyring";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [
        "x86_64-linux"
        "aarch64-linux"
      ];
      mkAuth =
        {
          pkgs,
          checkJdk ? pkgs.jdk25,
        }:
        pkgs.callPackage ./package.nix { inherit checkJdk; };
      lintTools =
        pkgs:
        let
          python = pkgs.python3.withPackages (packages: [ packages.secretstorage ]);
        in
        {
          inherit python;
          packages = [
            python
            pkgs.ruff
            pkgs.pyright
            pkgs.nixfmt
            pkgs.statix
            pkgs.deadnix
            pkgs.actionlint
            pkgs.shellcheck
            pkgs.zizmor
          ];
        };
    in
    {
      lib = { inherit mkAuth; };
      packages = forAllSystems (system: {
        default = mkAuth { pkgs = nixpkgs.legacyPackages.${system}; };
      });
      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-tree);
      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          tools = lintTools pkgs;
        in
        {
          default = pkgs.mkShellNoCC {
            inherit (tools) packages;
          };
        }
      );
      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          tools = lintTools pkgs;
          lintSrc = pkgs.lib.fileset.toSource {
            root = ./.;
            fileset = pkgs.lib.fileset.unions [
              ./.github
              ./flake.nix
              ./minecraft_auth.py
              ./package.nix
              ./pyproject.toml
              ./tests
            ];
          };
        in
        {
          inherit (self.packages.${system}) default;
          lint = pkgs.runCommand "minecraft-auth-lint" { nativeBuildInputs = tools.packages; } ''
            export HOME="$TMPDIR"
            export RUFF_CACHE_DIR="$TMPDIR/ruff-cache"
            cd ${lintSrc}
            ruff check .
            ruff format --check .
            pyright --pythonpath ${pkgs.lib.getExe tools.python}
            nixfmt --check flake.nix package.nix
            statix check .
            deadnix --fail flake.nix package.nix
            actionlint .github/workflows/*.yml
            zizmor --offline .github
            touch "$out"
          '';
        }
      );
    };
}
