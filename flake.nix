{
  description = "Synapse native Notes intake client";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
    in {
      packages = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in rec {
          notes-intake = pkgs.writeShellApplication {
            name = "notes-intake";
            text = ''
              export PYTHONPATH=${./src}
              exec ${pkgs.python313}/bin/python3 -m notes_intake.main "$@"
            '';
          };
          default = notes-intake;
        });
      darwinModules.notes-intake = import ./nix/notes-intake.nix self;
      checks = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          cli = pkgs.runCommand "notes-intake-cli-check" {} ''
            ${self.packages.${system}.notes-intake}/bin/notes-intake --help
            touch $out
          '';
        });
    };
}
