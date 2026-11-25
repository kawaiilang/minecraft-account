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
      mkAccount =
        {
          pkgs,
          checkJdk ? pkgs.jdk25,
        }:
        pkgs.callPackage ./package.nix { inherit checkJdk; };
    in
    {
      lib = { inherit mkAccount; };
      packages = forAllSystems (system: {
        default = mkAccount { pkgs = nixpkgs.legacyPackages.${system}; };
      });
      checks = forAllSystems (system: {
        inherit (self.packages.${system}) default;
      });
    };
}
