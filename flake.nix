{
  description = "Ski scraper development environment";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.mkShell {
            packages = with pkgs; [
              python3
              poetry
              ruff
              pyright
            ];

            # Keep the Poetry venv in ./.venv so the hook below can activate it
            POETRY_VIRTUALENVS_IN_PROJECT = "true";

            shellHook = ''
              if [ -d .venv ]; then
                source .venv/bin/activate
              fi
            '';
          };
        }
      );
    };
}
